from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, or_
from ..database import get_db
from ..models import (
    Tabela, TabelaEkipa, Utakmica, TabelaSortPravilo,
    Uzrast, Sezona, Takmicenje, PrijavaKluba, Klub,
)
from .auth import get_current_user
from typing import Optional
import datetime

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

KRITERIJI = [
    ("bodovi",           "Bodovi"),
    ("gol_razlika",      "Gol-razlika"),
    ("dati_golovi",      "Dati golovi"),
    ("primljeni_golovi", "Primljeni golovi"),
    ("pobjede",          "Pobjede"),
    ("porazi",           "Porazi"),
    ("medjusobno_bodovi","Međusobno — bodovi"),
    ("medjusobno_gr",    "Međusobno — gol-razlika"),
]


# ─── Berger schedule generator ────────────────────────────────────────────────

def _berger_schedule(n_real: int, dvokruzni: bool = True) -> list[list[tuple]]:
    """Generira raspored po standardnim Bergerovim tablicama.

    Algoritam (npr. Wikipedia hr - Bergerove tablice):
      • Tim N je fiksirani (ili BYE ako je N neparan)
      • Ring = [1, 2, ..., N-1] rotira se za N//2 mjesta ulijevo po kolu
      • Parno kolo  → N je domaćin vs ring[0]
      • Neparno kolo → ring[0] je domaćin vs N
      • Ostali parovi: ring[i] (dom) vs ring[N-1-i] (gost)

    Vraća listu kola; svako kolo je lista (home_seed, away_seed | None, is_bye).
    BYE: is_bye=True, away_seed=None — ta ekipa je slobodna to kolo.
    """
    n = n_real
    has_bye = (n % 2 == 1)
    if has_bye:
        n += 1          # n je sada paran; BYE slot = n (fiksirani "protivnik")

    # ring: ekipe 1..n-1 (ekipa n je fiksirani ili BYE)
    ring: list[int] = list(range(1, n))   # [1, 2, ..., n-1]
    half = n // 2
    rounds: list[list[tuple]] = []

    for r in range(1, n):       # n-1 kola
        pairs: list[tuple] = []

        # 1. par: ring[0] vs fiksirani n
        if has_bye:
            pairs.append((ring[0], None, True))     # ring[0] slobodan (BYE)
        elif r % 2 == 1:
            pairs.append((ring[0], n, False))       # neparno: ring[0] domaćin
        else:
            pairs.append((n, ring[0], False))       # parno:   n domaćin

        # Ostali parovi: ring[i] (domaćin) vs ring[n-1-i] (gost)
        for i in range(1, half):
            pairs.append((ring[i], ring[n - 1 - i], False))

        rounds.append(pairs)

        # Rotacija CCW za half mjesta: ring = ring[half:] + ring[:half]
        ring = ring[half:] + ring[:half]

    if dvokruzni:
        # Drugi krug = isti raspored, zamijenjen domaćin/gost
        second: list[list[tuple]] = []
        for rnd in rounds:
            swapped = [
                (a, h, False) if not bye else (h, None, True)
                for (h, a, bye) in rnd
            ]
            second.append(swapped)
        rounds = rounds + second

    return rounds


# ─── helper: izračun tabele ───────────────────────────────────────────────────

def _izracunaj(tabela: Tabela, ekipe, utakmice, sort_pravila, klub_map: dict) -> list:
    """Vraća sortirani list dict-ova sa statistikama po ekipi."""
    stats: dict[int, dict] = {}
    for te in ekipe:
        if not te.aktivan:
            continue
        stats[te.prijava_id] = {
            "te":       te,
            "klub":     klub_map.get(te.prijava_id, {}),
            "P": 0, "N": 0, "I": 0,
            "DG": 0, "PG": 0,
            "BOD": te.bonus_bodovi - te.kazneni_bodovi,
        }

    # Proći sve odigrane utakmice
    for u in utakmice:
        if not u.odigrana or u.gol_domacin is None or u.gol_gost is None:
            continue
        d = stats.get(u.domacin_id)
        g = stats.get(u.gost_id)
        gd, gg = u.gol_domacin, u.gol_gost

        if d:
            d["DG"] += gd
            d["PG"] += gg
        if g:
            g["DG"] += gg
            g["PG"] += gd

        if gd > gg:          # domacin pobijedio
            if d: d["P"] += 1; d["BOD"] += tabela.bodovi_pobjeda
            if g: g["I"] += 1; g["BOD"] += tabela.bodovi_poraz
        elif gd < gg:        # gost pobijedio
            if g: g["P"] += 1; g["BOD"] += tabela.bodovi_pobjeda
            if d: d["I"] += 1; d["BOD"] += tabela.bodovi_poraz
        else:                # neriješeno
            if d: d["N"] += 1; d["BOD"] += tabela.bodovi_nerjeseno
            if g: g["N"] += 1; g["BOD"] += tabela.bodovi_nerjeseno

    for s in stats.values():
        s["GR"] = s["DG"] - s["PG"]
        s["UT"] = s["P"] + s["N"] + s["I"]

    rows = list(stats.values())
    active = sorted([p for p in sort_pravila if p.aktivan], key=lambda p: p.prioritet)

    kriterij_fn = {
        "bodovi":           lambda r: r["BOD"],
        "gol_razlika":      lambda r: r["GR"],
        "dati_golovi":      lambda r: r["DG"],
        "primljeni_golovi": lambda r: -r["PG"],
        "pobjede":          lambda r: r["P"],
        "porazi":           lambda r: -r["I"],
    }

    def sort_key(row):
        key = []
        for p in active:
            fn = kriterij_fn.get(p.kriterij)
            if fn:
                val = fn(row)
                key.append(-val if p.smjer == "DESC" else val)
        return key

    if active:
        rows.sort(key=sort_key)

    return rows


async def _enrich_tabela(tabela: Tabela, db: AsyncSession):
    """Dohvata uzrast/sezona/takmicenje za jednu tabelu."""
    uzrast = (await db.execute(select(Uzrast).where(Uzrast.id == tabela.uzrast_id))).scalar_one_or_none()
    sezona = (await db.execute(select(Sezona).where(Sezona.id == uzrast.sezona_id))).scalar_one_or_none() if uzrast else None
    takm   = (await db.execute(select(Takmicenje).where(Takmicenje.id == uzrast.takmicenje_id))).scalar_one_or_none() if uzrast else None
    return uzrast, sezona, takm


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Pregled svih utakmica
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/utakmice", response_class=HTMLResponse)
async def admin_utakmice_pregled(
    request:   Request,
    db:        AsyncSession = Depends(get_db),
    uzrast_id: Optional[str] = None,
    kolo:      Optional[int] = None,   # specific kolo; None = auto next
    sve:       Optional[str] = None,   # "1" = show all kolos
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    # Parse uzrast_id (form submits empty string when none selected)
    uzrast_id = int(uzrast_id) if uzrast_id else None

    # ── Filter dropdowns ─────────────────────────────────────
    uzrasti_rows = (await db.execute(
        select(Uzrast, Takmicenje)
        .join(Takmicenje, Uzrast.takmicenje_id == Takmicenje.id)
        .order_by(Takmicenje.naziv, Uzrast.naziv)
    )).all()

    # ── Pre-fetch prijava → klub map ──────────────────────────
    pk_rows = (await db.execute(
        select(PrijavaKluba, Klub).join(Klub, PrijavaKluba.klub_id == Klub.id)
    )).all()
    prijava_map = {pk.id: {"naziv": k.naziv_kluba, "logo": k.logo, "id": k.id} for pk, k in pk_rows}

    # ── Sve utakmice ──────────────────────────────────────────
    q = (
        select(Utakmica, Tabela, Uzrast, Takmicenje)
        .join(Tabela,     Utakmica.tabela_id    == Tabela.id)
        .join(Uzrast,     Tabela.uzrast_id      == Uzrast.id)
        .join(Takmicenje, Uzrast.takmicenje_id  == Takmicenje.id)
    )
    if uzrast_id:
        q = q.where(Uzrast.id == uzrast_id)
    q = q.order_by(Takmicenje.naziv, Uzrast.naziv, Utakmica.kolo.nullslast(), Utakmica.datum_utakmice.nullslast())
    rows = (await db.execute(q)).all()

    all_items = []
    for u, tabela, uzrast, takm in rows:
        dom  = prijava_map.get(u.domacin_id)
        gost = prijava_map.get(u.gost_id) if u.gost_id else None
        all_items.append({"u": u, "tabela": tabela, "uzrast": uzrast, "takm": takm, "dom": dom, "gost": gost})

    # ── Odredi sljedeće kolo (min kolo s budućim neodigranim) ─
    now = datetime.datetime.now(datetime.timezone.utc)
    upcoming = [
        item["u"].kolo for item in all_items
        if not item["u"].je_bye
        and not item["u"].odigrana
        and item["u"].datum_utakmice
        and (item["u"].datum_utakmice if item["u"].datum_utakmice.tzinfo else item["u"].datum_utakmice.replace(tzinfo=datetime.timezone.utc)) >= now
        and item["u"].kolo
    ]
    next_kolo = min(upcoming, default=None)

    # ── Sva dostupna kola (za navigaciju) ─────────────────────
    all_kolos = sorted({item["u"].kolo for item in all_items if item["u"].kolo})

    # ── Aktivni kolo ──────────────────────────────────────────
    if sve:
        active_kolo = None      # prikaži sve
    elif kolo:
        active_kolo = kolo
    else:
        active_kolo = next_kolo  # default = sljedeće

    # ── Filtriraj po kolu ────────────────────────────────────
    filtered = all_items if active_kolo is None else [
        item for item in all_items if item["u"].kolo == active_kolo
    ]

    # ── Grupiraj po tabeli → po kolu ─────────────────────────
    from collections import OrderedDict as _OD
    tabela_map: dict = _OD()
    for item in filtered:
        tid = item["tabela"].id
        if tid not in tabela_map:
            tabela_map[tid] = {"tabela": item["tabela"], "uzrast": item["uzrast"],
                               "takm": item["takm"], "kolos": _OD()}
        k = item["u"].kolo or 0
        if k not in tabela_map[tid]["kolos"]:
            tabela_map[tid]["kolos"][k] = []
        tabela_map[tid]["kolos"][k].append(item)

    filter_uzrasti = [{"id": u.id, "naziv": u.naziv, "takm": t.naziv} for u, t in uzrasti_rows]
    prev_kolo = (all_kolos[all_kolos.index(active_kolo) - 1]
                 if active_kolo and active_kolo in all_kolos and all_kolos.index(active_kolo) > 0
                 else None)
    next_kolo_nav = (all_kolos[all_kolos.index(active_kolo) + 1]
                     if active_kolo and active_kolo in all_kolos and all_kolos.index(active_kolo) < len(all_kolos) - 1
                     else None)

    return templates.TemplateResponse("admin_utakmice.html", {
        "request":       request,
        "user":          user,
        "tabela_groups": list(tabela_map.values()),
        "filter_uzrasti": filter_uzrasti,
        "sel_uzrast_id": uzrast_id,
        "active_kolo":   active_kolo,
        "next_kolo":     next_kolo,
        "prev_kolo":     prev_kolo,
        "next_kolo_nav": next_kolo_nav,
        "all_kolos":     all_kolos,
        "sve":           bool(sve),
        "total":         len(filtered),
    })


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Lista tabela
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/tabele", response_class=HTMLResponse)
async def admin_tabele(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    tabele = (await db.execute(select(Tabela).order_by(Tabela.kreiran_datum.desc()))).scalars().all()

    # Enrich svaku tabelu
    enriched = []
    for t in tabele:
        uzrast, sezona, takm = await _enrich_tabela(t, db)
        n_ekipa = (await db.execute(
            select(TabelaEkipa).where(TabelaEkipa.tabela_id == t.id, TabelaEkipa.aktivan == True)
        )).scalars().all()
        n_utakmica = (await db.execute(
            select(Utakmica).where(Utakmica.tabela_id == t.id)
        )).scalars().all()
        enriched.append({
            "tabela":    t,
            "uzrast":    uzrast,
            "sezona":    sezona,
            "takm":      takm,
            "n_ekipa":   len(n_ekipa),
            "n_utakmica":len(n_utakmica),
        })

    # Za formu "Dodaj tabelu"
    uzrasti = (await db.execute(select(Uzrast).where(Uzrast.aktivan == True).order_by(Uzrast.naziv))).scalars().all()
    sezone  = (await db.execute(select(Sezona).order_by(Sezona.naziv))).scalars().all()
    takm_all = (await db.execute(select(Takmicenje).order_by(Takmicenje.naziv))).scalars().all()

    # Enrich uzrasti za select
    uzrasti_info = []
    for u in uzrasti:
        s = next((x for x in sezone  if x.id == u.sezona_id),     None)
        t = next((x for x in takm_all if x.id == u.takmicenje_id), None)
        uzrasti_info.append({"uzrast": u, "sezona": s, "takm": t})

    return templates.TemplateResponse("admin_tabele.html", {
        "request":       request,
        "user":          user,
        "enriched":      enriched,
        "uzrasti_info":  uzrasti_info,
    })


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Kreiraj tabelu
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/tabela/dodaj")
async def admin_tabela_dodaj(
    request: Request,
    uzrast_id:        int = Form(...),
    naziv:            str = Form(...),
    grupa:            Optional[str] = Form(None),
    bodovi_pobjeda:   int = Form(2),
    bodovi_nerjeseno: int = Form(1),
    bodovi_poraz:     int = Form(0),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    grupa_val = grupa.strip() if grupa and grupa.strip() else None

    tabela = Tabela(
        naziv=naziv.strip(),
        uzrast_id=uzrast_id,
        grupa=grupa_val,
        bodovi_pobjeda=bodovi_pobjeda,
        bodovi_nerjeseno=bodovi_nerjeseno,
        bodovi_poraz=bodovi_poraz,
    )
    db.add(tabela)
    await db.commit()
    await db.refresh(tabela)

    # Dodaj default pravila sortiranja
    defaults = [
        ("bodovi",      1, "DESC"),
        ("gol_razlika", 2, "DESC"),
        ("dati_golovi", 3, "DESC"),
    ]
    for krit, prio, smjer in defaults:
        db.add(TabelaSortPravilo(tabela_id=tabela.id, kriterij=krit, prioritet=prio, smjer=smjer))
    await db.commit()

    return RedirectResponse(f"/admin/tabela/{tabela.id}", status_code=303)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Detalji tabele (ekipe, utakmice, sort pravila)
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/tabela/{tabela_id}", response_class=HTMLResponse)
async def admin_tabela_detalji(tabela_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    tabela = (await db.execute(select(Tabela).where(Tabela.id == tabela_id))).scalar_one_or_none()
    if not tabela:
        raise HTTPException(status_code=404, detail="Tabela nije pronađena")

    uzrast, sezona, takm = await _enrich_tabela(tabela, db)

    # Ekipe u tabeli
    ekipe_rows = (await db.execute(
        select(TabelaEkipa, PrijavaKluba, Klub)
        .join(PrijavaKluba, TabelaEkipa.prijava_id == PrijavaKluba.id)
        .join(Klub,         PrijavaKluba.klub_id   == Klub.id)
        .where(TabelaEkipa.tabela_id == tabela_id)
        .order_by(Klub.naziv_kluba)
    )).all()
    ekipe = [{"te": r[0], "prijava": r[1], "klub": r[2]} for r in ekipe_rows]

    # Odobrene prijave za ovaj uzrast koje NISU u tabeli
    vec_u_tabeli = {r[0].prijava_id for r in ekipe_rows}
    slobodne_rows = (await db.execute(
        select(PrijavaKluba, Klub)
        .join(Klub, PrijavaKluba.klub_id == Klub.id)
        .where(
            PrijavaKluba.uzrast_id == tabela.uzrast_id,
            PrijavaKluba.status    == "odobren",
        )
        .order_by(Klub.naziv_kluba)
    )).all()
    slobodne = [{"prijava": r[0], "klub": r[1]} for r in slobodne_rows if r[0].id not in vec_u_tabeli]

    # Utakmice
    utakmice_rows = (await db.execute(
        select(Utakmica)
        .where(Utakmica.tabela_id == tabela_id)
        .order_by(Utakmica.kolo, Utakmica.datum_utakmice)
    )).scalars().all()

    # Mapa prijava_id → Klub za prikaz u utakmicama
    # ekipe_rows tuple: (TabelaEkipa[0], PrijavaKluba[1], Klub[2])
    prijava_klub: dict[int, str] = {r[0].prijava_id: r[2].naziv_kluba for r in ekipe_rows}

    # Sort pravila
    sort_pravila = (await db.execute(
        select(TabelaSortPravilo)
        .where(TabelaSortPravilo.tabela_id == tabela_id)
        .order_by(TabelaSortPravilo.prioritet)
    )).scalars().all()

    # Izračun tabele
    klub_map = {r[0].prijava_id: {"naziv": r[2].naziv_kluba, "logo": r[2].logo, "id": r[2].id}
                for r in ekipe_rows}
    te_list  = [r[0] for r in ekipe_rows]
    standings = _izracunaj(tabela, te_list, utakmice_rows, sort_pravila, klub_map)

    # ── Seed info za Raspored tab ────────────────────────────────────────────
    active_ekipe  = [e for e in ekipe if e["te"].aktivan]
    n_active      = len(active_ekipe)
    seeds_set_list = [e["te"].seed_broj for e in active_ekipe if e["te"].seed_broj is not None]
    seeds_valid = (
        len(seeds_set_list) == n_active and
        n_active > 0 and
        sorted(seeds_set_list) == list(range(1, n_active + 1))
    )
    seed_to_naziv: dict[int, str] = {
        e["te"].seed_broj: e["klub"].naziv_kluba
        for e in active_ekipe if e["te"].seed_broj is not None
    }

    berger_preview: list | None = None
    if seeds_valid:
        raw = _berger_schedule(n_active, dvokruzni=True)
        berger_preview = []
        for rnd in raw:
            round_display = []
            for (h, a, bye) in rnd:
                round_display.append({
                    "home":       seed_to_naziv.get(h, f"Seed {h}"),
                    "away":       seed_to_naziv.get(a, "?") if a else "Slobodna ekipa",
                    "bye":        bye,
                    "home_seed":  h,
                    "away_seed":  a,
                })
            berger_preview.append(round_display)

    return templates.TemplateResponse("admin_tabela_detalji.html", {
        "request":        request,
        "user":           user,
        "tabela":         tabela,
        "uzrast":         uzrast,
        "sezona":         sezona,
        "takm":           takm,
        "ekipe":          ekipe,
        "slobodne":       slobodne,
        "utakmice":       utakmice_rows,
        "prijava_klub":   prijava_klub,
        "sort_pravila":   sort_pravila,
        "standings":      standings,
        "kriteriji":      KRITERIJI,
        # Raspored tab
        "seeds_valid":    seeds_valid,
        "n_active":       n_active,
        "n_seeds_set":    len(seeds_set_list),
        "berger_preview": berger_preview,
        "n_utakmica":     len(utakmice_rows),
        "n_kola_preview": len(berger_preview) if berger_preview else 0,
    })


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Toggle aktivan
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/tabela/{tabela_id}/toggle")
async def admin_tabela_toggle(tabela_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    tabela = (await db.execute(select(Tabela).where(Tabela.id == tabela_id))).scalar_one_or_none()
    if tabela:
        tabela.aktivan = not tabela.aktivan
        await db.commit()
    return RedirectResponse("/admin/tabele", status_code=303)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Dodaj/ukloni ekipu
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/tabela/{tabela_id}/ekipa/dodaj")
async def admin_tabela_ekipa_dodaj(
    tabela_id: int,
    request:   Request,
    prijava_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    db.add(TabelaEkipa(tabela_id=tabela_id, prijava_id=prijava_id))
    await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


@router.post("/admin/tabela/{tabela_id}/ekipa/{te_id}/ukloni")
async def admin_tabela_ekipa_ukloni(
    tabela_id: int, te_id: int,
    request: Request, db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    te = (await db.execute(select(TabelaEkipa).where(TabelaEkipa.id == te_id, TabelaEkipa.tabela_id == tabela_id))).scalar_one_or_none()
    if te:
        await db.delete(te)
        await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Utakmice (dodaj / uredi / ukloni)
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/tabela/{tabela_id}/utakmica/dodaj")
async def admin_utakmica_dodaj(
    tabela_id:     int,
    request:       Request,
    domacin_id:    int           = Form(...),
    gost_id:       int           = Form(...),
    kolo:          Optional[int] = Form(None),
    datum_utakmice: Optional[str] = Form(None),
    gol_domacin:   Optional[str] = Form(None),
    gol_gost:      Optional[str] = Form(None),
    napomena:      Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    gd = int(gol_domacin) if gol_domacin and gol_domacin.strip() else None
    gg = int(gol_gost)    if gol_gost    and gol_gost.strip()    else None
    odigrana = gd is not None and gg is not None
    datum    = None
    if datum_utakmice and datum_utakmice.strip():
        try:
            datum = datetime.datetime.fromisoformat(datum_utakmice.strip())
        except ValueError:
            datum = None

    db.add(Utakmica(
        tabela_id=tabela_id,
        domacin_id=domacin_id,
        gost_id=gost_id,
        kolo=kolo,
        datum_utakmice=datum,
        gol_domacin=gd,
        gol_gost=gg,
        odigrana=odigrana,
        napomena=napomena.strip() if napomena else None,
    ))
    await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


@router.post("/admin/tabela/{tabela_id}/utakmica/{uid}/uredi")
async def admin_utakmica_uredi(
    tabela_id:            int,
    uid:                  int,
    request:              Request,
    domacin_id:           int           = Form(...),
    gost_id:              int           = Form(...),
    kolo:                 Optional[int] = Form(None),
    datum_utakmice_date:  Optional[str] = Form(None),
    datum_utakmice_time:  Optional[str] = Form(None),
    gol_domacin:          Optional[str] = Form(None),
    gol_gost:             Optional[str] = Form(None),
    napomena:             Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    u = (await db.execute(select(Utakmica).where(Utakmica.id == uid, Utakmica.tabela_id == tabela_id))).scalar_one_or_none()
    if u:
        gd = int(gol_domacin) if gol_domacin and gol_domacin.strip() else None
        gg = int(gol_gost)    if gol_gost    and gol_gost.strip()    else None
        u.domacin_id  = domacin_id
        u.gost_id     = gost_id
        u.kolo        = kolo
        u.gol_domacin = gd
        u.gol_gost    = gg
        u.odigrana    = gd is not None and gg is not None
        u.napomena    = napomena.strip() if napomena else None
        if datum_utakmice_date and datum_utakmice_date.strip():
            time_str = datum_utakmice_time.strip() if datum_utakmice_time and datum_utakmice_time.strip() else "00:00"
            try:
                u.datum_utakmice = datetime.datetime.strptime(
                    f"{datum_utakmice_date.strip()} {time_str}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                u.datum_utakmice = None
        else:
            u.datum_utakmice = None
        await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}#utakmice", status_code=303)


@router.post("/admin/tabela/{tabela_id}/utakmica/{uid}/ukloni")
async def admin_utakmica_ukloni(
    tabela_id: int, uid: int,
    request: Request, db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    u = (await db.execute(select(Utakmica).where(Utakmica.id == uid, Utakmica.tabela_id == tabela_id))).scalar_one_or_none()
    if u:
        await db.delete(u)
        await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Sort pravila
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/tabela/{tabela_id}/sort/dodaj")
async def admin_sort_dodaj(
    tabela_id: int,
    request:   Request,
    kriterij:  str = Form(...),
    smjer:     str = Form("DESC"),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    # Sljedeći prioritet
    existing = (await db.execute(
        select(TabelaSortPravilo).where(TabelaSortPravilo.tabela_id == tabela_id)
        .order_by(TabelaSortPravilo.prioritet.desc())
    )).scalars().all()
    next_prio = (existing[0].prioritet + 1) if existing else 1

    db.add(TabelaSortPravilo(tabela_id=tabela_id, kriterij=kriterij, prioritet=next_prio, smjer=smjer))
    await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


@router.post("/admin/tabela/{tabela_id}/sort/{sid}/ukloni")
async def admin_sort_ukloni(
    tabela_id: int, sid: int,
    request: Request, db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    p = (await db.execute(select(TabelaSortPravilo).where(TabelaSortPravilo.id == sid, TabelaSortPravilo.tabela_id == tabela_id))).scalar_one_or_none()
    if p:
        await db.delete(p)
        await db.commit()
        # Renumber priorities
        remaining = (await db.execute(
            select(TabelaSortPravilo)
            .where(TabelaSortPravilo.tabela_id == tabela_id)
            .order_by(TabelaSortPravilo.prioritet)
        )).scalars().all()
        for i, r in enumerate(remaining, 1):
            r.prioritet = i
        await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


@router.post("/admin/tabela/{tabela_id}/sort/{sid}/gore")
async def admin_sort_gore(
    tabela_id: int, sid: int,
    request: Request, db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    pravila = (await db.execute(
        select(TabelaSortPravilo)
        .where(TabelaSortPravilo.tabela_id == tabela_id)
        .order_by(TabelaSortPravilo.prioritet)
    )).scalars().all()
    for i, p in enumerate(pravila):
        if p.id == sid and i > 0:
            pravila[i].prioritet, pravila[i-1].prioritet = pravila[i-1].prioritet, pravila[i].prioritet
            await db.commit()
            break
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


@router.post("/admin/tabela/{tabela_id}/sort/{sid}/dole")
async def admin_sort_dole(
    tabela_id: int, sid: int,
    request: Request, db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    pravila = (await db.execute(
        select(TabelaSortPravilo)
        .where(TabelaSortPravilo.tabela_id == tabela_id)
        .order_by(TabelaSortPravilo.prioritet)
    )).scalars().all()
    for i, p in enumerate(pravila):
        if p.id == sid and i < len(pravila) - 1:
            pravila[i].prioritet, pravila[i+1].prioritet = pravila[i+1].prioritet, pravila[i].prioritet
            await db.commit()
            break
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Seed (žrijeb broj za ekipu u tabeli)
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/tabela/{tabela_id}/ekipa/{te_id}/seed")
async def admin_tabela_seed(
    tabela_id: int,
    te_id:     int,
    request:   Request,
    seed_broj: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    te = (await db.execute(
        select(TabelaEkipa).where(TabelaEkipa.id == te_id, TabelaEkipa.tabela_id == tabela_id)
    )).scalar_one_or_none()
    if te:
        te.seed_broj = seed_broj if seed_broj and seed_broj > 0 else None
        await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}#ekipe", status_code=303)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Generiraj Bergerov raspored
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/tabela/{tabela_id}/raspored/generiraj")
async def admin_raspored_generiraj(
    tabela_id:   int,
    request:     Request,
    format:      str            = Form("dvokruzni"),
    start_date:  Optional[str]  = Form(None),   # YYYY-MM-DD
    start_time:  Optional[str]  = Form(None),   # HH:MM
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    tabela = (await db.execute(select(Tabela).where(Tabela.id == tabela_id))).scalar_one_or_none()
    if not tabela:
        raise HTTPException(status_code=404, detail="Tabela nije pronađena")

    # Dohvati aktivne ekipe sa seed brojevima
    te_list = (await db.execute(
        select(TabelaEkipa)
        .where(TabelaEkipa.tabela_id == tabela_id, TabelaEkipa.aktivan == True)
    )).scalars().all()

    n = len(te_list)
    seeds = [te.seed_broj for te in te_list if te.seed_broj is not None]

    # Validacija: svi moraju imati seed i mora biti 1..N bez duplikata
    if len(seeds) != n or sorted(seeds) != list(range(1, n + 1)):
        return RedirectResponse(f"/admin/tabela/{tabela_id}#raspored", status_code=303)

    seed_map: dict[int, int] = {te.seed_broj: te.prijava_id for te in te_list}

    # Parsiraj početni datum/vrijeme
    base_dt: Optional[datetime.datetime] = None
    if start_date and start_date.strip():
        try:
            time_part = start_time.strip() if start_time and start_time.strip() else "12:00"
            base_dt = datetime.datetime.strptime(f"{start_date.strip()} {time_part}", "%Y-%m-%d %H:%M")
        except ValueError:
            base_dt = None

    # Obriši postojeće neodigrane utakmice
    existing = (await db.execute(
        select(Utakmica)
        .where(Utakmica.tabela_id == tabela_id, Utakmica.odigrana == False)
    )).scalars().all()
    for u in existing:
        await db.delete(u)
    await db.commit()

    # Generiraj raspored
    dvokruzni = (format == "dvokruzni")
    schedule = _berger_schedule(n, dvokruzni=dvokruzni)

    for kolo_idx, rnd in enumerate(schedule, 1):
        # Datum za ovo kolo = baza + (kolo-1) * 7 dana
        kolo_dt = base_dt + datetime.timedelta(weeks=kolo_idx - 1) if base_dt else None
        for (h_seed, a_seed, bye) in rnd:
            dom_prijava  = seed_map[h_seed]
            gost_prijava = seed_map[a_seed] if a_seed is not None else None
            db.add(Utakmica(
                tabela_id      = tabela_id,
                domacin_id     = dom_prijava,
                gost_id        = gost_prijava,
                je_bye         = bye,
                kolo           = kolo_idx,
                datum_utakmice = kolo_dt,
                odigrana       = False,
            ))
    await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}#utakmice", status_code=303)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Obriši raspored (sve neodigrane utakmice)
# ═══════════════════════════════════════════════════════════════

@router.post("/admin/tabela/{tabela_id}/raspored/obrisi")
async def admin_raspored_obrisi(
    tabela_id: int,
    request:   Request,
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    existing = (await db.execute(
        select(Utakmica)
        .where(Utakmica.tabela_id == tabela_id, Utakmica.odigrana == False)
    )).scalars().all()
    for u in existing:
        await db.delete(u)
    await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}#raspored", status_code=303)


# ═══════════════════════════════════════════════════════════════
#  KLUB — Pregled tabela
# ═══════════════════════════════════════════════════════════════

async def _get_klub_tabele(klub_id: int, db: AsyncSession) -> list:
    """Zajednička logika: dohvat svih tabela u kojima je klub, sa standings."""
    moje_tabele = []
    tabela_member_rows = (await db.execute(
        select(Tabela, TabelaEkipa)
        .join(TabelaEkipa, Tabela.id == TabelaEkipa.tabela_id)
        .join(PrijavaKluba, TabelaEkipa.prijava_id == PrijavaKluba.id)
        .where(
            PrijavaKluba.klub_id == klub_id,
            Tabela.aktivan == True,
            TabelaEkipa.aktivan == True,
        )
        .order_by(Tabela.kreiran_datum.desc())
    )).all()

    for t_tabela, moja_te in tabela_member_rows:
        uzrast_t = (await db.execute(select(Uzrast).where(Uzrast.id == t_tabela.uzrast_id))).scalar_one_or_none()
        sezona_t = (await db.execute(select(Sezona).where(Sezona.id == uzrast_t.sezona_id))).scalar_one_or_none() if uzrast_t else None
        takm_t   = (await db.execute(select(Takmicenje).where(Takmicenje.id == uzrast_t.takmicenje_id))).scalar_one_or_none() if uzrast_t else None

        ekipe_rows_t = (await db.execute(
            select(TabelaEkipa, PrijavaKluba, Klub)
            .join(PrijavaKluba, TabelaEkipa.prijava_id == PrijavaKluba.id)
            .join(Klub, PrijavaKluba.klub_id == Klub.id)
            .where(TabelaEkipa.tabela_id == t_tabela.id, TabelaEkipa.aktivan == True)
            .order_by(Klub.naziv_kluba)
        )).all()

        utakmice_t = (await db.execute(
            select(Utakmica)
            .where(Utakmica.tabela_id == t_tabela.id)
            .order_by(Utakmica.kolo, Utakmica.datum_utakmice)
        )).scalars().all()

        sort_pravila_t = (await db.execute(
            select(TabelaSortPravilo)
            .where(TabelaSortPravilo.tabela_id == t_tabela.id)
            .order_by(TabelaSortPravilo.prioritet)
        )).scalars().all()

        klub_map_t = {
            r[0].prijava_id: {"naziv": r[2].naziv_kluba, "logo": r[2].logo, "id": r[2].id}
            for r in ekipe_rows_t
        }
        standings_t = _izracunaj(t_tabela, [r[0] for r in ekipe_rows_t], utakmice_t, sort_pravila_t, klub_map_t)

        moj_rank = next((i + 1 for i, row in enumerate(standings_t) if row["klub"]["id"] == klub_id), None)
        prijava_klub_t = {r[0].prijava_id: r[2].naziv_kluba for r in ekipe_rows_t}
        odigrane = sum(1 for u in utakmice_t if u.odigrana)

        # Utakmice kluba (odigrane)
        moje_utakmice = [
            u for u in utakmice_t
            if u.odigrana and (u.domacin_id == moja_te.prijava_id or u.gost_id == moja_te.prijava_id)
        ]

        moje_tabele.append({
            "tabela":      t_tabela,
            "uzrast":      uzrast_t,
            "sezona":      sezona_t,
            "takm":        takm_t,
            "standings":   standings_t,
            "moj_rank":    moj_rank,
            "n_ekipa":     len(standings_t),
            "moja_prijava_id": moja_te.prijava_id,
            "odigrane":    odigrane,
            "moje_utakmice": moje_utakmice,
            "prijava_klub": prijava_klub_t,
        })

    return moje_tabele


@router.get("/klub/tabele", response_class=HTMLResponse)
async def klub_tabele_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)

    klub_id = int(user["sub"])
    klub = (await db.execute(select(Klub).where(Klub.id == klub_id))).scalar_one_or_none()
    moje_tabele = await _get_klub_tabele(klub_id, db)

    return templates.TemplateResponse("klub_tabele.html", {
        "request":     request,
        "user":        user,
        "klub":        klub,
        "moje_tabele": moje_tabele,
    })
