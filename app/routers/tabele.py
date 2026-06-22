from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
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
    prijava_klub: dict[int, str] = {r[0].id: r[1].naziv_kluba for r in ekipe_rows}

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

    return templates.TemplateResponse("admin_tabela_detalji.html", {
        "request":      request,
        "user":         user,
        "tabela":       tabela,
        "uzrast":       uzrast,
        "sezona":       sezona,
        "takm":         takm,
        "ekipe":        ekipe,
        "slobodne":     slobodne,
        "utakmice":     utakmice_rows,
        "prijava_klub": prijava_klub,
        "sort_pravila": sort_pravila,
        "standings":    standings,
        "kriteriji":    KRITERIJI,
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
    gol_domacin:   Optional[int] = Form(None),
    gol_gost:      Optional[int] = Form(None),
    napomena:      Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    odigrana = gol_domacin is not None and gol_gost is not None
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
        gol_domacin=gol_domacin,
        gol_gost=gol_gost,
        odigrana=odigrana,
        napomena=napomena.strip() if napomena else None,
    ))
    await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


@router.post("/admin/tabela/{tabela_id}/utakmica/{uid}/uredi")
async def admin_utakmica_uredi(
    tabela_id:     int,
    uid:           int,
    request:       Request,
    domacin_id:    int           = Form(...),
    gost_id:       int           = Form(...),
    kolo:          Optional[int] = Form(None),
    datum_utakmice: Optional[str] = Form(None),
    gol_domacin:   Optional[int] = Form(None),
    gol_gost:      Optional[int] = Form(None),
    napomena:      Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    u = (await db.execute(select(Utakmica).where(Utakmica.id == uid, Utakmica.tabela_id == tabela_id))).scalar_one_or_none()
    if u:
        u.domacin_id  = domacin_id
        u.gost_id     = gost_id
        u.kolo        = kolo
        u.gol_domacin = gol_domacin
        u.gol_gost    = gol_gost
        u.odigrana    = gol_domacin is not None and gol_gost is not None
        u.napomena    = napomena.strip() if napomena else None
        if datum_utakmice and datum_utakmice.strip():
            try:
                u.datum_utakmice = datetime.datetime.fromisoformat(datum_utakmice.strip())
            except ValueError:
                pass
        await db.commit()
    return RedirectResponse(f"/admin/tabela/{tabela_id}", status_code=303)


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
