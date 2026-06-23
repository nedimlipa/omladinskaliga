from fastapi import APIRouter, Request, Depends
from ..templates_config import templates
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import (
    Tabela, TabelaEkipa, Utakmica, TabelaSortPravilo,
    Uzrast, Takmicenje, PrijavaKluba, Klub,
)
from .tabele import _izracunaj, _enrich_tabela
from collections import OrderedDict
import datetime

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def public_home(request: Request, db: AsyncSession = Depends(get_db)):
    now = datetime.datetime.now(datetime.timezone.utc)

    # Pre-fetch prijava → klub map
    pk_rows = (await db.execute(
        select(PrijavaKluba, Klub).join(Klub, PrijavaKluba.klub_id == Klub.id)
    )).all()
    prijava_map = {pk.id: {"naziv": k.naziv_kluba, "logo": k.logo, "id": k.id} for pk, k in pk_rows}

    # Sve aktivne tabele enriched
    tabele = (await db.execute(
        select(Tabela).where(Tabela.aktivan == True).order_by(Tabela.id)
    )).scalars().all()

    ligas = []
    for tabela in tabele:
        uzrast, sezona, takm = await _enrich_tabela(tabela, db)
        if not uzrast or not takm:
            continue

        # Sve utakmice u tabeli
        utakmice_rows = (await db.execute(
            select(Utakmica)
            .where(Utakmica.tabela_id == tabela.id)
            .order_by(Utakmica.kolo, Utakmica.je_bye.asc(), Utakmica.datum_utakmice)
        )).scalars().all()

        # Ekipe za standings
        ekipe_rows = (await db.execute(
            select(TabelaEkipa, PrijavaKluba, Klub)
            .join(PrijavaKluba, TabelaEkipa.prijava_id == PrijavaKluba.id)
            .join(Klub, PrijavaKluba.klub_id == Klub.id)
            .where(TabelaEkipa.tabela_id == tabela.id, TabelaEkipa.aktivan == True)
        )).all()

        sort_pravila = (await db.execute(
            select(TabelaSortPravilo)
            .where(TabelaSortPravilo.tabela_id == tabela.id)
            .order_by(TabelaSortPravilo.prioritet)
        )).scalars().all()

        klub_map = {r[0].prijava_id: {"naziv": r[2].naziv_kluba, "logo": r[2].logo, "id": r[2].id}
                    for r in ekipe_rows}
        te_list = [r[0] for r in ekipe_rows]
        standings = _izracunaj(tabela, te_list, utakmice_rows, sort_pravila, klub_map)

        # Odredi koji kolo prikazati
        upcoming_kolos = [
            u.kolo for u in utakmice_rows
            if not u.je_bye and not u.odigrana and u.datum_utakmice and u.kolo
            and (u.datum_utakmice if u.datum_utakmice.tzinfo
                 else u.datum_utakmice.replace(tzinfo=datetime.timezone.utc)) >= now
        ]
        next_kolo = min(upcoming_kolos, default=None)

        if next_kolo is None:
            # Nema nadolazećih — prikaži zadnje odigrano kolo
            played = [u.kolo for u in utakmice_rows if u.odigrana and u.kolo]
            display_kolo = max(played, default=None)
            show_results = True
        else:
            display_kolo = next_kolo
            show_results = False

        kolo_utakmice = []
        if display_kolo is not None:
            for u in utakmice_rows:
                if u.kolo != display_kolo:
                    continue
                dom = prijava_map.get(u.domacin_id)
                gost = prijava_map.get(u.gost_id) if u.gost_id else None
                kolo_utakmice.append({"u": u, "dom": dom, "gost": gost})

        ligas.append({
            "tabela":       tabela,
            "uzrast":       uzrast,
            "takm":         takm,
            "standings":    standings,
            "kolo_utakmice": kolo_utakmice,
            "display_kolo": display_kolo,
            "show_results": show_results,
            "next_kolo":    next_kolo,
        })

    return templates.TemplateResponse("public_home.html", {
        "request": request,
        "ligas":   ligas,
        "now":     now,
    })


# ═══════════════════════════════════════════════════════════════
#  PUBLIC — Raspored (pregled svih kola po uzrastu)
# ═══════════════════════════════════════════════════════════════

@router.get("/raspored", response_class=HTMLResponse)
async def public_raspored(
    request:   Request,
    db:        AsyncSession = Depends(get_db),
    tabela_id: str = None,
    kolo:      int = None,
    sve:       str = None,
):
    now = datetime.datetime.now(datetime.timezone.utc)
    tabela_id_int = int(tabela_id) if tabela_id else None

    # Pre-fetch prijava → klub map
    pk_rows = (await db.execute(
        select(PrijavaKluba, Klub).join(Klub, PrijavaKluba.klub_id == Klub.id)
    )).all()
    prijava_map = {pk.id: {"naziv": k.naziv_kluba, "logo": k.logo, "id": k.id} for pk, k in pk_rows}

    # Sve aktivne tabele enriched (za pills navigaciju)
    tabele_all = (await db.execute(
        select(Tabela, Uzrast, Takmicenje)
        .join(Uzrast,     Tabela.uzrast_id      == Uzrast.id)
        .join(Takmicenje, Uzrast.takmicenje_id  == Takmicenje.id)
        .where(Tabela.aktivan == True)
        .order_by(Takmicenje.naziv, Uzrast.naziv)
    )).all()

    nav_tabele = [{"tabela": t, "uzrast": u, "takm": tk} for t, u, tk in tabele_all]

    # Odabrana tabela — default: prva
    if not nav_tabele:
        return templates.TemplateResponse("public_raspored.html", {
            "request": request, "nav_tabele": [], "sel_tabela": None,
            "kolos": {}, "active_kolo": None, "next_kolo": None,
            "prev_kolo": None, "next_kolo_nav": None, "all_kolos": [],
            "sve": False, "total": 0,
        })

    sel = next((x for x in nav_tabele if x["tabela"].id == tabela_id_int), nav_tabele[0])
    sel_tabela_id = sel["tabela"].id

    # Sve utakmice za odabranu tabelu
    rows = (await db.execute(
        select(Utakmica)
        .where(Utakmica.tabela_id == sel_tabela_id)
        .order_by(Utakmica.kolo.nullslast(), Utakmica.je_bye.asc(), Utakmica.datum_utakmice.nullslast())
    )).scalars().all()

    items = []
    for u in rows:
        dom  = prijava_map.get(u.domacin_id)
        gost = prijava_map.get(u.gost_id) if u.gost_id else None
        items.append({"u": u, "dom": dom, "gost": gost})

    all_kolos = sorted({item["u"].kolo for item in items if item["u"].kolo})

    # Auto next kolo
    upcoming = [
        item["u"].kolo for item in items
        if not item["u"].je_bye and not item["u"].odigrana
        and item["u"].datum_utakmice and item["u"].kolo
        and (item["u"].datum_utakmice if item["u"].datum_utakmice.tzinfo
             else item["u"].datum_utakmice.replace(tzinfo=datetime.timezone.utc)) >= now
    ]
    next_kolo = min(upcoming, default=None)

    if sve:
        active_kolo = None
    elif kolo:
        active_kolo = kolo
    else:
        active_kolo = next_kolo or (all_kolos[0] if all_kolos else None)

    filtered = items if active_kolo is None else [i for i in items if i["u"].kolo == active_kolo]

    # Grupiraj po kolu za "sve" prikaz
    kolos: OrderedDict = OrderedDict()
    for item in filtered:
        k = item["u"].kolo or 0
        if k not in kolos:
            kolos[k] = []
        kolos[k].append(item)

    prev_kolo = (all_kolos[all_kolos.index(active_kolo) - 1]
                 if active_kolo and active_kolo in all_kolos and all_kolos.index(active_kolo) > 0
                 else None)
    next_kolo_nav = (all_kolos[all_kolos.index(active_kolo) + 1]
                     if active_kolo and active_kolo in all_kolos
                     and all_kolos.index(active_kolo) < len(all_kolos) - 1
                     else None)

    return templates.TemplateResponse("public_raspored.html", {
        "request":      request,
        "nav_tabele":   nav_tabele,
        "sel":          sel,
        "kolos":        kolos,
        "active_kolo":  active_kolo,
        "next_kolo":    next_kolo,
        "prev_kolo":    prev_kolo,
        "next_kolo_nav": next_kolo_nav,
        "all_kolos":    all_kolos,
        "sve":          bool(sve),
        "total":        len(filtered),
        "now":          now,
    })


# ═══════════════════════════════════════════════════════════════
#  PUBLIC — Tabele (javni prikaz standings)
# ═══════════════════════════════════════════════════════════════

@router.get("/tabele", response_class=HTMLResponse)
async def public_tabele_view(
    request:   Request,
    db:        AsyncSession = Depends(get_db),
    tabela_id: str = None,
):
    tabela_id_int = int(tabela_id) if tabela_id else None

    # Sve aktivne tabele za pills
    tabele_all = (await db.execute(
        select(Tabela, Uzrast, Takmicenje)
        .join(Uzrast,     Tabela.uzrast_id      == Uzrast.id)
        .join(Takmicenje, Uzrast.takmicenje_id  == Takmicenje.id)
        .where(Tabela.aktivan == True)
        .order_by(Takmicenje.naziv, Uzrast.naziv)
    )).all()
    nav_tabele = [{"tabela": t, "uzrast": u, "takm": tk} for t, u, tk in tabele_all]

    if not nav_tabele:
        return templates.TemplateResponse("public_tabele.html", {
            "request": request, "nav_tabele": [], "sel": None, "standings": [],
        })

    sel = next((x for x in nav_tabele if x["tabela"].id == tabela_id_int), nav_tabele[0])

    # Ekipe + standings za odabranu tabelu
    ekipe_rows = (await db.execute(
        select(TabelaEkipa, PrijavaKluba, Klub)
        .join(PrijavaKluba, TabelaEkipa.prijava_id == PrijavaKluba.id)
        .join(Klub,         PrijavaKluba.klub_id   == Klub.id)
        .where(TabelaEkipa.tabela_id == sel["tabela"].id, TabelaEkipa.aktivan == True)
    )).all()

    utakmice_rows = (await db.execute(
        select(Utakmica).where(Utakmica.tabela_id == sel["tabela"].id)
    )).scalars().all()

    sort_pravila = (await db.execute(
        select(TabelaSortPravilo)
        .where(TabelaSortPravilo.tabela_id == sel["tabela"].id)
        .order_by(TabelaSortPravilo.prioritet)
    )).scalars().all()

    klub_map = {r[0].prijava_id: {"naziv": r[2].naziv_kluba, "logo": r[2].logo, "id": r[2].id}
                for r in ekipe_rows}
    standings = _izracunaj(sel["tabela"], [r[0] for r in ekipe_rows], utakmice_rows, sort_pravila, klub_map)

    return templates.TemplateResponse("public_tabele.html", {
        "request":    request,
        "nav_tabele": nav_tabele,
        "sel":        sel,
        "standings":  standings,
    })


# ═══════════════════════════════════════════════════════════════
#  PUBLIC — Profil kluba
# ═══════════════════════════════════════════════════════════════

@router.get("/klub/{klub_id}", response_class=HTMLResponse)
async def public_klub_profil(
    request: Request,
    klub_id: int,
    db:      AsyncSession = Depends(get_db),
):
    from fastapi import HTTPException
from ..templates_config import templates
    from sqlalchemy import or_

    now = datetime.datetime.now(datetime.timezone.utc)

    klub = await db.get(Klub, klub_id)
    if not klub:
        raise HTTPException(status_code=404, detail="Klub nije pronađen")

    # Sve prijave ovog kluba
    pk_rows = (await db.execute(
        select(PrijavaKluba).where(PrijavaKluba.klub_id == klub_id)
    )).scalars().all()
    moje_pk_ids = {pk.id for pk in pk_rows}

    # Prijava → klub map (za prikaz protivnika)
    all_pk_rows = (await db.execute(
        select(PrijavaKluba, Klub).join(Klub, PrijavaKluba.klub_id == Klub.id)
    )).all()
    prijava_map = {pk.id: {"naziv": k.naziv_kluba, "logo": k.logo, "id": k.id} for pk, k in all_pk_rows}

    # Ligas u kojima nastupa + standings pozicija
    ligas_info = []
    for pk in pk_rows:
        te = (await db.execute(
            select(TabelaEkipa).where(TabelaEkipa.prijava_id == pk.id, TabelaEkipa.aktivan == True)
        )).scalar_one_or_none()
        if not te:
            continue
        tabela = await db.get(Tabela, te.tabela_id)
        if not tabela or not tabela.aktivan:
            continue
        uzrast, _, takm = await _enrich_tabela(tabela, db)

        ekipe_rows2 = (await db.execute(
            select(TabelaEkipa, PrijavaKluba, Klub)
            .join(PrijavaKluba, TabelaEkipa.prijava_id == PrijavaKluba.id)
            .join(Klub, PrijavaKluba.klub_id == Klub.id)
            .where(TabelaEkipa.tabela_id == tabela.id, TabelaEkipa.aktivan == True)
        )).all()
        utakmice_t = (await db.execute(
            select(Utakmica).where(Utakmica.tabela_id == tabela.id)
        )).scalars().all()
        sort_pravila_t = (await db.execute(
            select(TabelaSortPravilo)
            .where(TabelaSortPravilo.tabela_id == tabela.id)
            .order_by(TabelaSortPravilo.prioritet)
        )).scalars().all()
        km = {r[0].prijava_id: {"naziv": r[2].naziv_kluba, "logo": r[2].logo, "id": r[2].id} for r in ekipe_rows2}
        standings = _izracunaj(tabela, [r[0] for r in ekipe_rows2], utakmice_t, sort_pravila_t, km)
        rank = next((i + 1 for i, s in enumerate(standings) if s["klub"]["id"] == klub_id), None)
        my_stats = next((s for s in standings if s["klub"]["id"] == klub_id), None)
        ligas_info.append({
            "tabela": tabela, "uzrast": uzrast, "takm": takm,
            "standings": standings, "rank": rank, "my_stats": my_stats,
        })

    # Zadnji rezultati (5 odigranih)
    zadnji = []
    if moje_pk_ids:
        rows_z = (await db.execute(
            select(Utakmica, Tabela, Uzrast, Takmicenje)
            .join(Tabela,     Utakmica.tabela_id    == Tabela.id)
            .join(Uzrast,     Tabela.uzrast_id      == Uzrast.id)
            .join(Takmicenje, Uzrast.takmicenje_id  == Takmicenje.id)
            .where(
                Utakmica.odigrana == True,
                Utakmica.je_bye == False,
                or_(Utakmica.domacin_id.in_(moje_pk_ids), Utakmica.gost_id.in_(moje_pk_ids)),
            )
            .order_by(Utakmica.datum_utakmice.desc().nullslast())
            .limit(5)
        )).all()
        for u, tab, uzr, tk in rows_z:
            dom  = prijava_map.get(u.domacin_id)
            gost = prijava_map.get(u.gost_id) if u.gost_id else None
            je_dom = u.domacin_id in moje_pk_ids
            if je_dom:
                pobijedio = u.gol_domacin > u.gol_gost if u.gol_domacin is not None else None
                nerjeseno = u.gol_domacin == u.gol_gost if u.gol_domacin is not None else None
            else:
                pobijedio = u.gol_gost > u.gol_domacin if u.gol_gost is not None else None
                nerjeseno = u.gol_domacin == u.gol_gost if u.gol_domacin is not None else None
            zadnji.append({"u": u, "dom": dom, "gost": gost, "uzrast": uzr, "takm": tk,
                           "je_dom": je_dom, "pobijedio": pobijedio, "nerjeseno": nerjeseno})

    # Nadolazeće (3)
    nadolazece = []
    if moje_pk_ids:
        rows_n = (await db.execute(
            select(Utakmica, Tabela, Uzrast, Takmicenje)
            .join(Tabela,     Utakmica.tabela_id    == Tabela.id)
            .join(Uzrast,     Tabela.uzrast_id      == Uzrast.id)
            .join(Takmicenje, Uzrast.takmicenje_id  == Takmicenje.id)
            .where(
                Utakmica.odigrana == False,
                Utakmica.je_bye == False,
                or_(Utakmica.domacin_id.in_(moje_pk_ids), Utakmica.gost_id.in_(moje_pk_ids)),
            )
            .order_by(Utakmica.datum_utakmice.asc().nullslast())
            .limit(3)
        )).all()
        for u, tab, uzr, tk in rows_n:
            dom  = prijava_map.get(u.domacin_id)
            gost = prijava_map.get(u.gost_id) if u.gost_id else None
            nadolazece.append({"u": u, "dom": dom, "gost": gost, "uzrast": uzr, "takm": tk})

    return templates.TemplateResponse("public_klub.html", {
        "request":    request,
        "klub":       klub,
        "ligas_info": ligas_info,
        "zadnji":     zadnji,
        "nadolazece": nadolazece,
    })

