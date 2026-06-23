"""Zapisnik utakmice — admin upravljanje i unos od strane klubova."""
import datetime
from typing import List, Optional

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from ..templates_config import templates
from ..database import get_db
from ..models import ZapisnikUtakmica, ZapisnikIgrac, Klub
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
    result = await db.execute(
        select(ZapisnikUtakmica).order_by(ZapisnikUtakmica.kreiran_datum.desc())
    )
    zapisnici = result.scalars().all()
    result_k = await db.execute(
        select(Klub).where(Klub.aktivan == True).order_by(Klub.naziv_kluba)
    )
    klubovi = result_k.scalars().all()
    return templates.TemplateResponse("admin_zapisnici.html", {
        "request": request, "user": user,
        "zapisnici": zapisnici, "klubovi": klubovi, "statusi": STATUSI,
    })


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
    iskljucenje: int = Form(0),
    iskljucenje_1: int = Form(0),
    iskljucenje_2: int = Form(0),
    crveni_karton: str = Form("ne"),
    plavi_karton: str = Form("ne"),
    time_out_1: int = Form(0),
    time_out_2: int = Form(0),
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
        iskljucenje=iskljucenje or 0,
        iskljucenje_1=iskljucenje_1 or 0,
        iskljucenje_2=iskljucenje_2 or 0,
        crveni_karton=(crveni_karton == "da"),
        plavi_karton=(plavi_karton == "da"),
        time_out_1=time_out_1 or 0,
        time_out_2=time_out_2 or 0,
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
    iskljucenje: int = Form(0),
    iskljucenje_1: int = Form(0),
    iskljucenje_2: int = Form(0),
    crveni_karton: str = Form("ne"),
    plavi_karton: str = Form("ne"),
    time_out_1: int = Form(0),
    time_out_2: int = Form(0),
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
    ig.iskljucenje = iskljucenje or 0
    ig.iskljucenje_1 = iskljucenje_1 or 0
    ig.iskljucenje_2 = iskljucenje_2 or 0
    ig.crveni_karton = (crveni_karton == "da")
    ig.plavi_karton = (plavi_karton == "da")
    ig.time_out_1 = time_out_1 or 0
    ig.time_out_2 = time_out_2 or 0
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

    return templates.TemplateResponse("klub_zapisnik.html", {
        "request": request, "user": user, "z": z,
        "moj_tim": moj_tim, "igraci": igraci,
    })


class _IgracInput(BaseModel):
    br_dresa: Optional[int] = None
    ime_prezime: str
    br_registracije: Optional[str] = None
    pozicija: Optional[str] = None
    tip: str = "igrac"
    golovi: int = 0
    opomene: int = 0
    iskljucenje: int = 0
    iskljucenje_1: int = 0
    iskljucenje_2: int = 0
    crveni_karton: bool = False
    plavi_karton: bool = False
    time_out_1: int = 0
    time_out_2: int = 0


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
            iskljucenje=ig_data.iskljucenje,
            iskljucenje_1=ig_data.iskljucenje_1,
            iskljucenje_2=ig_data.iskljucenje_2,
            crveni_karton=ig_data.crveni_karton,
            plavi_karton=ig_data.plavi_karton,
            time_out_1=ig_data.time_out_1,
            time_out_2=ig_data.time_out_2,
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
