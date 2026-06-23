"""Zapisnik utakmice — admin upravljanje i unos od strane klubova."""
import datetime
from typing import List, Optional

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, or_

from ..templates_config import templates
from ..database import get_db
from ..models import (ZapisnikUtakmica, ZapisnikIgrac, Klub, Utakmica, Tabela, Uzrast, Takmicenje,
                      PrijavaKluba, Igrac, Registracija, SluzbenoLice, RegistracijaSL)
from .auth import get_current_user

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Sarajevo")
except Exception:
    _TZ = datetime.timezone(datetime.timedelta(hours=2))


def _now() -> datetime.datetime:
    return datetime.datetime.now(tz=_TZ)


router = APIRouter()

STATUSI = ["Zakazana", "U toku", "Odigrana", "Odgođena", "Napuštena", "W.O."]

POZICIJE_IGRACI   = ["GK", "LK", "DK", "LL", "DL", "LS", "DS", "PB", "LB", "DB"]
POZICIJE_SL       = ["Trener", "Asistent trenera", "Doktor/Fizioterapeut", "Delegat", "Ostalo"]


# ─── ADMIN ─────────────────────────────────────────────────────────────────

@router.get("/admin/zapisnici", response_class=HTMLResponse)
async def admin_zapisnici_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)

    q        = request.query_params.get("q",       "").strip()
    fstat    = request.query_params.get("status",  "").strip()
    fuzrast  = request.query_params.get("uzrast",  "").strip()
    fliga    = request.query_params.get("liga",    "").strip()
    fkolo    = request.query_params.get("kolo",    "").strip()
    fdat_od  = request.query_params.get("dat_od",  "").strip()
    fdat_do  = request.query_params.get("dat_do",  "").strip()
    fsezona  = request.query_params.get("sezona",  "").strip()

    qry = select(ZapisnikUtakmica).order_by(
        ZapisnikUtakmica.datum.desc().nulls_last(), ZapisnikUtakmica.kreiran_datum.desc()
    )
    if q:
        qry = qry.where(or_(
            ZapisnikUtakmica.ekipa_a.ilike(f"%{q}%"),
            ZapisnikUtakmica.ekipa_b.ilike(f"%{q}%"),
            ZapisnikUtakmica.liga.ilike(f"%{q}%"),
            ZapisnikUtakmica.uzrast.ilike(f"%{q}%"),
            ZapisnikUtakmica.br_utakmice.ilike(f"%{q}%"),
        ))
    if fstat:
        qry = qry.where(ZapisnikUtakmica.status_utakmice == fstat)
    if fuzrast:
        qry = qry.where(ZapisnikUtakmica.uzrast == fuzrast)
    if fliga:
        qry = qry.where(ZapisnikUtakmica.liga.ilike(f"%{fliga}%"))
    if fkolo:
        try:
            qry = qry.where(ZapisnikUtakmica.kolo == int(fkolo))
        except ValueError:
            pass
    if fdat_od:
        try:
            import datetime as _dt
            qry = qry.where(ZapisnikUtakmica.datum >= _dt.date.fromisoformat(fdat_od))
        except ValueError:
            pass
    if fdat_do:
        try:
            import datetime as _dt
            qry = qry.where(ZapisnikUtakmica.datum <= _dt.date.fromisoformat(fdat_do))
        except ValueError:
            pass
    if fsezona:
        qry = qry.where(ZapisnikUtakmica.sezona == fsezona)

    zapisnici = (await db.execute(qry)).scalars().all()

    # Distinct values for filter dropdowns
    dist_uzrast = (await db.execute(
        select(ZapisnikUtakmica.uzrast).where(ZapisnikUtakmica.uzrast.isnot(None))
        .distinct().order_by(ZapisnikUtakmica.uzrast)
    )).scalars().all()
    dist_liga = (await db.execute(
        select(ZapisnikUtakmica.liga).where(ZapisnikUtakmica.liga.isnot(None))
        .distinct().order_by(ZapisnikUtakmica.liga)
    )).scalars().all()
    dist_sezona = (await db.execute(
        select(ZapisnikUtakmica.sezona).where(ZapisnikUtakmica.sezona.isnot(None))
        .distinct().order_by(ZapisnikUtakmica.sezona.desc())
    )).scalars().all()

    result_k = await db.execute(
        select(Klub).where(Klub.aktivan == True).order_by(Klub.naziv_kluba)
    )
    klubovi = result_k.scalars().all()
    return templates.TemplateResponse("admin_zapisnici.html", {
        "request": request, "user": user,
        "zapisnici": zapisnici, "klubovi": klubovi, "statusi": STATUSI,
        "q": q, "fstat": fstat,
        "fuzrast": fuzrast, "fliga": fliga, "fkolo": fkolo,
        "fdat_od": fdat_od, "fdat_do": fdat_do, "fsezona": fsezona,
        "dist_uzrast": dist_uzrast, "dist_liga": dist_liga, "dist_sezona": dist_sezona,
    })


@router.post("/admin/zapisnici/iz-utakmice/{uid}")
async def admin_zapisnik_iz_utakmice(
    uid: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    """Create or open a zapisnik linked to an existing Utakmica record."""
    if not user or user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)

    # Return existing if already created
    existing = (await db.execute(
        select(ZapisnikUtakmica).where(ZapisnikUtakmica.utakmica_id == uid)
    )).scalar_one_or_none()
    if existing:
        return RedirectResponse(f"/admin/zapisnik/{existing.id}", status_code=303)

    # Load utakmica with all related data
    row = (await db.execute(
        select(Utakmica, Tabela, Uzrast, Takmicenje)
        .join(Tabela, Utakmica.tabela_id == Tabela.id)
        .join(Uzrast, Tabela.uzrast_id == Uzrast.id)
        .join(Takmicenje, Uzrast.takmicenje_id == Takmicenje.id)
        .where(Utakmica.id == uid)
    )).first()
    if not row:
        raise HTTPException(404)
    u, tabela, uzrast_obj, takm = row

    # Resolve club names via prijave_klubova
    pk_dom = (await db.execute(
        select(PrijavaKluba, Klub)
        .join(Klub, PrijavaKluba.klub_id == Klub.id)
        .where(PrijavaKluba.id == u.domacin_id)
    )).first()
    pk_gost = None
    if u.gost_id:
        pk_gost = (await db.execute(
            select(PrijavaKluba, Klub)
            .join(Klub, PrijavaKluba.klub_id == Klub.id)
            .where(PrijavaKluba.id == u.gost_id)
        )).first()

    dom_naziv   = pk_dom[1].naziv_kluba   if pk_dom  else None
    dom_klub_id = pk_dom[1].id            if pk_dom  else None
    gost_naziv  = pk_gost[1].naziv_kluba  if pk_gost else None
    gost_klub_id = pk_gost[1].id          if pk_gost else None

    # Extract date/time from utakmica (aware → Sarajevo local)
    datum_val = None
    vrijeme_val = None
    if u.datum_utakmice:
        local_dt = u.datum_utakmice.astimezone(_TZ) if u.datum_utakmice.tzinfo else u.datum_utakmice
        datum_val  = local_dt.date()
        vrijeme_val = local_dt.strftime("%H:%M")

    # Auto-generate br_utakmice
    ustr = uzrast_obj.naziv or ""
    prefix = ustr.replace(" ", "").replace("-", "")
    cnt = (await db.execute(
        select(func.count(ZapisnikUtakmica.id))
        .where(ZapisnikUtakmica.uzrast == ustr)
    )).scalar() or 0
    br_utakmice = f"{prefix}-{cnt + 1}" if ustr else None

    liga_val = takm.naziv
    if tabela.naziv:
        liga_val = f"{takm.naziv} — {tabela.naziv}"

    z = ZapisnikUtakmica(
        utakmica_id=uid,
        br_utakmice=br_utakmice,
        datum=datum_val,
        vrijeme=vrijeme_val,
        kolo=u.kolo,
        uzrast=ustr or None,
        liga=liga_val,
        ekipa_a=dom_naziv,
        ekipa_b=gost_naziv,
        ekipa_a_id=dom_klub_id,
        ekipa_b_id=gost_klub_id,
        status_utakmice="Zakazana",
        zadnje_spasio=user["ime"],
        zadnje_izmijenjeno=_now(),
    )
    db.add(z)
    await db.commit()
    await db.refresh(z)
    return RedirectResponse(f"/admin/zapisnik/{z.id}", status_code=303)


@router.post("/admin/zapisnici/novi")
async def admin_zapisnik_novi_post(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    datum: str = Form(None),
    vrijeme: str = Form(None),
    kolo: Optional[int] = Form(None),
    uzrast: str = Form(None),
    liga: str = Form(None),
    ekipa_a: str = Form(None),
    ekipa_b: str = Form(None),
    ekipa_a_id: Optional[int] = Form(None),
    ekipa_b_id: Optional[int] = Form(None),
    sudija_a: str = Form(None),
    sudija_b: str = Form(None),
    delegat: str = Form(None),
    zapisnicar: str = Form(None),
    mjerilac_vremena: str = Form(None),
    glavni_sluzbeni: str = Form(None),
    ljekar: str = Form(None),
    status_utakmice: str = Form("Zakazana"),
):
    if not user or user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)

    # Auto-generate br_utakmice
    br_utakmice = None
    if uzrast and uzrast.strip():
        ustr = uzrast.strip()
        prefix = ustr.replace(" ", "").replace("-", "")
        cnt_res = await db.execute(
            select(func.count(ZapisnikUtakmica.id))
            .where(ZapisnikUtakmica.uzrast == ustr)
        )
        cnt = cnt_res.scalar() or 0
        br_utakmice = f"{prefix}-{cnt + 1}"

    z = ZapisnikUtakmica(
        br_utakmice=br_utakmice,
        datum=datetime.date.fromisoformat(datum) if datum else None,
        vrijeme=vrijeme or None,
        kolo=kolo,
        uzrast=uzrast.strip() if uzrast else None,
        liga=liga or None,
        ekipa_a=ekipa_a or None,
        ekipa_b=ekipa_b or None,
        ekipa_a_id=ekipa_a_id or None,
        ekipa_b_id=ekipa_b_id or None,
        sudija_a=sudija_a or None,
        sudija_b=sudija_b or None,
        delegat=delegat or None,
        zapisnicar=zapisnicar or None,
        mjerilac_vremena=mjerilac_vremena or None,
        glavni_sluzbeni=glavni_sluzbeni or None,
        ljekar=ljekar or None,
        status_utakmice=status_utakmice or "Zakazana",
        zadnje_spasio=user["ime"],
        zadnje_izmijenjeno=_now(),
    )
    db.add(z)
    await db.commit()
    await db.refresh(z)
    return RedirectResponse(f"/admin/zapisnik/{z.id}", status_code=303)


@router.get("/admin/zapisnik/{zid}", response_class=HTMLResponse)
async def admin_zapisnik_detalji(
    zid: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)
    z = await db.get(ZapisnikUtakmica, zid)
    if not z:
        raise HTTPException(404)

    res_a = await db.execute(
        select(ZapisnikIgrac)
        .where(ZapisnikIgrac.zapisnik_id == zid, ZapisnikIgrac.tim == "A")
        .order_by(ZapisnikIgrac.tip.desc(), ZapisnikIgrac.br_dresa)
    )
    igraci_a = res_a.scalars().all()

    res_b = await db.execute(
        select(ZapisnikIgrac)
        .where(ZapisnikIgrac.zapisnik_id == zid, ZapisnikIgrac.tim == "B")
        .order_by(ZapisnikIgrac.tip.desc(), ZapisnikIgrac.br_dresa)
    )
    igraci_b = res_b.scalars().all()

    res_k = await db.execute(
        select(Klub).where(Klub.aktivan == True).order_by(Klub.naziv_kluba)
    )
    klubovi = res_k.scalars().all()

    return templates.TemplateResponse("admin_zapisnik_detalji.html", {
        "request": request, "user": user, "z": z,
        "igraci_a": igraci_a, "igraci_b": igraci_b,
        "klubovi": klubovi, "statusi": STATUSI,
    })


@router.post("/admin/zapisnik/{zid}/uredi")
async def admin_zapisnik_uredi(
    zid: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    datum: str = Form(None),
    vrijeme: str = Form(None),
    kolo: Optional[int] = Form(None),
    uzrast: str = Form(None),
    liga: str = Form(None),
    ekipa_a: str = Form(None),
    ekipa_b: str = Form(None),
    ekipa_a_id: Optional[int] = Form(None),
    ekipa_b_id: Optional[int] = Form(None),
    sudija_a: str = Form(None),
    sudija_b: str = Form(None),
    delegat: str = Form(None),
    zapisnicar: str = Form(None),
    mjerilac_vremena: str = Form(None),
    glavni_sluzbeni: str = Form(None),
    ljekar: str = Form(None),
    dvorana: str = Form(None),
    mjesto: str = Form(None),
    sezona: str = Form(None),
    jmbg_ljekar: str = Form(None),
    jmbg_glavni_dezurni: str = Form(None),
    rezultat_a: Optional[int] = Form(None),
    rezultat_b: Optional[int] = Form(None),
    poluvrijeme_a: Optional[int] = Form(None),
    poluvrijeme_b: Optional[int] = Form(None),
    to_a_1: str = Form(None),
    to_a_2: str = Form(None),
    to_a_3: str = Form(None),
    to_b_1: str = Form(None),
    to_b_2: str = Form(None),
    to_b_3: str = Form(None),
    sedam_m_a_dato: Optional[int] = Form(None),
    sedam_m_a_promj: Optional[int] = Form(None),
    sedam_m_b_dato: Optional[int] = Form(None),
    sedam_m_b_promj: Optional[int] = Form(None),
    status_utakmice: str = Form("Zakazana"),
):
    if not user or user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)
    z = await db.get(ZapisnikUtakmica, zid)
    if not z:
        raise HTTPException(404)

    z.datum = datetime.date.fromisoformat(datum) if datum else None
    z.vrijeme = vrijeme or None
    z.kolo = kolo
    z.uzrast = uzrast or None
    z.liga = liga or None
    z.ekipa_a = ekipa_a or None
    z.ekipa_b = ekipa_b or None
    z.ekipa_a_id = ekipa_a_id or None
    z.ekipa_b_id = ekipa_b_id or None
    z.sudija_a = sudija_a or None
    z.sudija_b = sudija_b or None
    z.delegat = delegat or None
    z.zapisnicar = zapisnicar or None
    z.mjerilac_vremena = mjerilac_vremena or None
    z.glavni_sluzbeni = glavni_sluzbeni or None
    z.ljekar = ljekar or None
    z.dvorana = dvorana or None
    z.mjesto = mjesto or None
    z.sezona = sezona or None
    z.jmbg_ljekar = jmbg_ljekar or None
    z.jmbg_glavni_dezurni = jmbg_glavni_dezurni or None
    z.rezultat_a = rezultat_a
    z.rezultat_b = rezultat_b
    z.poluvrijeme_a = poluvrijeme_a
    z.poluvrijeme_b = poluvrijeme_b
    z.to_a_1 = to_a_1 or None
    z.to_a_2 = to_a_2 or None
    z.to_a_3 = to_a_3 or None
    z.to_b_1 = to_b_1 or None
    z.to_b_2 = to_b_2 or None
    z.to_b_3 = to_b_3 or None
    z.sedam_m_a_dato = sedam_m_a_dato
    z.sedam_m_a_promj = sedam_m_a_promj
    z.sedam_m_b_dato = sedam_m_b_dato
    z.sedam_m_b_promj = sedam_m_b_promj
    z.status_utakmice = status_utakmice or "Zakazana"
    z.zadnje_spasio = user["ime"]
    z.zadnje_izmijenjeno = _now()
    await db.commit()
    return RedirectResponse(f"/admin/zapisnik/{zid}", status_code=303)


@router.post("/admin/zapisnik/{zid}/igrac/dodaj")
async def admin_igrac_dodaj(
    zid: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    tim: str = Form(...),
    tip: str = Form("igrac"),
    pozicija: str = Form(None),
    br_dresa: Optional[int] = Form(None),
    ime_prezime: str = Form(...),
    br_registracije: str = Form(None),
    golovi: int = Form(0),
    opomene: int = Form(0),
    iskljucenje: str = Form(None),
    iskljucenje_1: str = Form(None),
    iskljucenje_2: str = Form(None),
    crveni_karton: str = Form("ne"),
    plavi_karton: str = Form("ne"),
    time_out_1: int = Form(0),
    time_out_2: int = Form(0),
    sedam_m_dato: int = Form(0),
    sedam_m_promj: int = Form(0),
):
    if not user or user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)
    z = await db.get(ZapisnikUtakmica, zid)
    if not z:
        raise HTTPException(404)

    ig = ZapisnikIgrac(
        zapisnik_id=zid,
        tim=tim,
        tip=tip,
        pozicija=pozicija or None,
        br_dresa=br_dresa,
        ime_prezime=ime_prezime.strip(),
        br_registracije=br_registracije or None,
        golovi=golovi or 0,
        opomene=opomene or 0,
        iskljucenje=iskljucenje or None,
        iskljucenje_1=iskljucenje_1 or None,
        iskljucenje_2=iskljucenje_2 or None,
        crveni_karton=(crveni_karton == "da"),
        plavi_karton=(plavi_karton == "da"),
        time_out_1=time_out_1 or 0,
        time_out_2=time_out_2 or 0,
        sedam_m_dato=sedam_m_dato or 0,
        sedam_m_promj=sedam_m_promj or 0,
        zadnje_spasio=user["ime"],
        zadnje_izmijenjeno=_now(),
    )
    db.add(ig)
    z.zadnje_spasio = user["ime"]
    z.zadnje_izmijenjeno = _now()
    await db.commit()
    return RedirectResponse(f"/admin/zapisnik/{zid}", status_code=303)


@router.post("/admin/zapisnik/{zid}/igrac/{iid}/uredi")
async def admin_igrac_uredi(
    zid: int,
    iid: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    tip: str = Form("igrac"),
    pozicija: str = Form(None),
    br_dresa: Optional[int] = Form(None),
    ime_prezime: str = Form(...),
    br_registracije: str = Form(None),
    golovi: int = Form(0),
    opomene: int = Form(0),
    iskljucenje: str = Form(None),
    iskljucenje_1: str = Form(None),
    iskljucenje_2: str = Form(None),
    crveni_karton: str = Form("ne"),
    plavi_karton: str = Form("ne"),
    time_out_1: int = Form(0),
    time_out_2: int = Form(0),
    sedam_m_dato: int = Form(0),
    sedam_m_promj: int = Form(0),
):
    if not user or user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)
    ig = await db.get(ZapisnikIgrac, iid)
    if not ig or ig.zapisnik_id != zid:
        raise HTTPException(404)

    ig.tip = tip
    ig.pozicija = pozicija or None
    ig.br_dresa = br_dresa
    ig.ime_prezime = ime_prezime.strip()
    ig.br_registracije = br_registracije or None
    ig.golovi = golovi or 0
    ig.opomene = opomene or 0
    ig.iskljucenje = iskljucenje or None
    ig.iskljucenje_1 = iskljucenje_1 or None
    ig.iskljucenje_2 = iskljucenje_2 or None
    ig.crveni_karton = (crveni_karton == "da")
    ig.plavi_karton = (plavi_karton == "da")
    ig.time_out_1 = time_out_1 or 0
    ig.time_out_2 = time_out_2 or 0
    ig.sedam_m_dato = sedam_m_dato or 0
    ig.sedam_m_promj = sedam_m_promj or 0
    ig.zadnje_spasio = user["ime"]
    ig.zadnje_izmijenjeno = _now()

    z = await db.get(ZapisnikUtakmica, zid)
    if z:
        z.zadnje_spasio = user["ime"]
        z.zadnje_izmijenjeno = _now()
    await db.commit()
    return RedirectResponse(f"/admin/zapisnik/{zid}", status_code=303)


@router.post("/admin/zapisnik/{zid}/igrac/{iid}/obrisi")
async def admin_igrac_obrisi(
    zid: int,
    iid: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user or user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)
    ig = await db.get(ZapisnikIgrac, iid)
    if ig and ig.zapisnik_id == zid:
        await db.delete(ig)
        z = await db.get(ZapisnikUtakmica, zid)
        if z:
            z.zadnje_spasio = user["ime"]
            z.zadnje_izmijenjeno = _now()
        await db.commit()
    return RedirectResponse(f"/admin/zapisnik/{zid}", status_code=303)


@router.post("/admin/zapisnik/{zid}/verifikacija")
async def admin_zapisnik_verifikacija(
    zid: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
    verifikacija_sudija_a: str = Form("ne"),
    verifikacija_sudija_b: str = Form("ne"),
    verifikacija_delegat: str = Form("ne"),
):
    if not user or user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)
    z = await db.get(ZapisnikUtakmica, zid)
    if not z:
        raise HTTPException(404)
    z.verifikacija_sudija_a = (verifikacija_sudija_a == "da")
    z.verifikacija_sudija_b = (verifikacija_sudija_b == "da")
    z.verifikacija_delegat  = (verifikacija_delegat == "da")
    z.zadnje_spasio = user["ime"]
    z.zadnje_izmijenjeno = _now()
    await db.commit()
    return RedirectResponse(f"/admin/zapisnik/{zid}?tab=verifikacija", status_code=303)


# ─── KLUB ──────────────────────────────────────────────────────────────────

@router.get("/klub/zapisnik/{zid}", response_class=HTMLResponse)
async def klub_zapisnik_view(
    zid: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user:
        return RedirectResponse("/login", status_code=302)
    if user["tip"] != "klub":
        raise HTTPException(403)

    klub_id = int(user["sub"])
    z = await db.get(ZapisnikUtakmica, zid)
    if not z:
        raise HTTPException(404)

    if z.ekipa_a_id == klub_id:
        moj_tim = "A"
    elif z.ekipa_b_id == klub_id:
        moj_tim = "B"
    else:
        raise HTTPException(403, "Vaš klub nije dio ove utakmice.")

    res = await db.execute(
        select(ZapisnikIgrac)
        .where(ZapisnikIgrac.zapisnik_id == zid, ZapisnikIgrac.tim == moj_tim)
        .order_by(ZapisnikIgrac.tip.desc(), ZapisnikIgrac.br_dresa)
    )
    igraci = res.scalars().all()

    # Determine season via: zapisnik → utakmica → tabela → uzrast → sezona
    sezona_id = None
    if z.utakmica_id:
        u = await db.get(Utakmica, z.utakmica_id)
        if u:
            t = await db.get(Tabela, u.tabela_id)
            if t:
                uz = await db.get(Uzrast, t.uzrast_id)
                if uz:
                    sezona_id = uz.sezona_id

    reg_igraci: list = []
    reg_sl: list = []

    if sezona_id:
        rows_ig = (await db.execute(
            select(Igrac.ime, Igrac.prezime, Registracija.br_registracije)
            .join(Registracija, Registracija.igrac_id == Igrac.id)
            .where(
                Registracija.klub_id == klub_id,
                Registracija.sezona_id == sezona_id,
                Registracija.status == "odobren",
            )
            .order_by(Igrac.prezime, Igrac.ime)
        )).all()
        reg_igraci = [
            {"ime_prezime": f"{r.ime} {r.prezime}", "br_reg": r.br_registracije or ""}
            for r in rows_ig
        ]

        rows_sl = (await db.execute(
            select(SluzbenoLice.ime, SluzbenoLice.prezime, RegistracijaSL.br_registracije)
            .join(RegistracijaSL, RegistracijaSL.sluzbeno_lice_id == SluzbenoLice.id)
            .where(
                RegistracijaSL.klub_id == klub_id,
                RegistracijaSL.sezona_id == sezona_id,
                RegistracijaSL.status == "odobren",
            )
            .order_by(SluzbenoLice.prezime, SluzbenoLice.ime)
        )).all()
        reg_sl = [
            {"ime_prezime": f"{r.ime} {r.prezime}", "br_reg": r.br_registracije or ""}
            for r in rows_sl
        ]

    return templates.TemplateResponse("klub_zapisnik.html", {
        "request": request, "user": user, "z": z,
        "moj_tim": moj_tim, "igraci": igraci,
        "reg_igraci": reg_igraci, "reg_sl": reg_sl,
    })


class _IgracInput(BaseModel):
    br_dresa: Optional[int] = None
    ime_prezime: str
    br_registracije: Optional[str] = None
    pozicija: Optional[str] = None
    tip: str = "igrac"
    golovi: int = 0
    opomene: int = 0
    iskljucenje: Optional[str] = None    # MM:SS of 3rd 2-min exclusion
    iskljucenje_1: Optional[str] = None  # MM:SS of 1st 2-min exclusion
    iskljucenje_2: Optional[str] = None  # MM:SS of 2nd 2-min exclusion
    crveni_karton: bool = False
    plavi_karton: bool = False
    time_out_1: int = 0
    time_out_2: int = 0
    sedam_m_dato: int = 0
    sedam_m_promj: int = 0


class _KlubSaveRequest(BaseModel):
    tim: str
    igraci: List[_IgracInput]


@router.post("/klub/zapisnik/{zid}/spasi")
async def klub_zapisnik_spasi(
    zid: int,
    data: _KlubSaveRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user or user["tip"] != "klub":
        raise HTTPException(403)
    if data.tim not in ("A", "B"):
        raise HTTPException(400)

    klub_id = int(user["sub"])
    z = await db.get(ZapisnikUtakmica, zid)
    if not z:
        raise HTTPException(404)

    if data.tim == "A" and z.ekipa_a_id != klub_id:
        raise HTTPException(403)
    if data.tim == "B" and z.ekipa_b_id != klub_id:
        raise HTTPException(403)

    # Replace all rows for this team
    await db.execute(
        delete(ZapisnikIgrac).where(
            ZapisnikIgrac.zapisnik_id == zid,
            ZapisnikIgrac.tim == data.tim,
        )
    )
    now = _now()
    for ig_data in data.igraci:
        if not ig_data.ime_prezime.strip():
            continue
        db.add(ZapisnikIgrac(
            zapisnik_id=zid,
            tim=data.tim,
            tip=ig_data.tip,
            pozicija=ig_data.pozicija or None,
            br_dresa=ig_data.br_dresa,
            ime_prezime=ig_data.ime_prezime.strip(),
            br_registracije=ig_data.br_registracije or None,
            golovi=ig_data.golovi,
            opomene=ig_data.opomene,
            iskljucenje=ig_data.iskljucenje or None,
            iskljucenje_1=ig_data.iskljucenje_1 or None,
            iskljucenje_2=ig_data.iskljucenje_2 or None,
            crveni_karton=ig_data.crveni_karton,
            plavi_karton=ig_data.plavi_karton,
            time_out_1=ig_data.time_out_1,
            time_out_2=ig_data.time_out_2,
            sedam_m_dato=ig_data.sedam_m_dato,
            sedam_m_promj=ig_data.sedam_m_promj,
            zadnje_spasio=user["ime"],
            zadnje_izmijenjeno=now,
        ))

    z.zadnje_spasio = user["ime"]
    z.zadnje_izmijenjeno = now
    await db.commit()
    return JSONResponse({
        "ok": True,
        "poruka": "Podaci su uspješno sačuvani.",
        "vrijeme": now.strftime("%d.%m.%Y %H:%M"),
    })


# ─── PRINT VIEW ────────────────────────────────────────────────────────────

async def _get_zapisnik_print(zid: int, db: AsyncSession, user: dict, club_access: bool):
    """Shared logic for print view (admin + club)."""
    z = await db.get(ZapisnikUtakmica, zid)
    if not z:
        raise HTTPException(404)

    if club_access:
        klub_id = int(user["sub"])
        if z.ekipa_a_id != klub_id and z.ekipa_b_id != klub_id:
            raise HTTPException(403)

    igraci_a = (await db.execute(
        select(ZapisnikIgrac)
        .where(ZapisnikIgrac.zapisnik_id == zid, ZapisnikIgrac.tim == "A")
        .order_by(ZapisnikIgrac.tip.desc(), ZapisnikIgrac.br_dresa.nullslast(), ZapisnikIgrac.id)
    )).scalars().all()
    igraci_b = (await db.execute(
        select(ZapisnikIgrac)
        .where(ZapisnikIgrac.zapisnik_id == zid, ZapisnikIgrac.tim == "B")
        .order_by(ZapisnikIgrac.tip.desc(), ZapisnikIgrac.br_dresa.nullslast(), ZapisnikIgrac.id)
    )).scalars().all()
    return z, igraci_a, igraci_b


@router.get("/admin/zapisnik/{zid}/print", response_class=HTMLResponse)
async def admin_zapisnik_print(
    zid: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user or user["tip"] not in ("admin", "moderator"):
        raise HTTPException(403)
    z, igraci_a, igraci_b = await _get_zapisnik_print(zid, db, user, False)
    return templates.TemplateResponse("zapisnik_print.html", {
        "request": request, "user": user,
        "z": z, "igraci_a": igraci_a, "igraci_b": igraci_b,
        "back_url": f"/admin/zapisnik/{zid}",
    })


@router.get("/klub/zapisnik/{zid}/print", response_class=HTMLResponse)
async def klub_zapisnik_print(
    zid: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),
):
    if not user or user["tip"] != "klub":
        raise HTTPException(403)
    z, igraci_a, igraci_b = await _get_zapisnik_print(zid, db, user, True)
    return templates.TemplateResponse("zapisnik_print.html", {
        "request": request, "user": user,
        "z": z, "igraci_a": igraci_a, "igraci_b": igraci_b,
        "back_url": f"/klub/zapisnik/{zid}",
    })
