from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import (
    Tabela, TabelaEkipa, Utakmica, TabelaSortPravilo,
    Uzrast, Takmicenje, PrijavaKluba, Klub,
)
from .tabele import _izracunaj, _enrich_tabela
import datetime

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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
