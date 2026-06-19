import datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from ..database import get_db
from ..models import Igrac, Registracija, Klub, Sezona
from .auth import get_current_user

router    = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ── Helper: generiraj broj registracije ──────────────────────
async def _gen_br(db: AsyncSession, klub_id: int, sezona_id: int) -> str:
    seq = (await db.execute(
        select(func.count(Registracija.id)).where(
            Registracija.klub_id == klub_id,
            Registracija.sezona_id == sezona_id,
            Registracija.br_registracije.isnot(None),
        )
    )).scalar() or 0
    return f"{klub_id}-{sezona_id}-{seq + 1:04d}"


# ── Helper: ažuriraj trenutni/prethodni klub igrača ──────────
async def _azuriraj_klub(db: AsyncSession, igrac_id: int, novi_klub_id: int):
    igrac = await db.get(Igrac, igrac_id)
    if igrac:
        if igrac.trenutni_klub_id and igrac.trenutni_klub_id != novi_klub_id:
            igrac.prethodni_klub_id = igrac.trenutni_klub_id
        igrac.trenutni_klub_id = novi_klub_id


# ═══════════════════════════════════════════════════════════════
#  KLUB — Stranica za upravljanje igračima
# ═══════════════════════════════════════════════════════════════

@router.get("/klub/igraci", response_class=HTMLResponse)
async def klub_igraci_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)

    klub_id = int(user["sub"])
    klub    = await db.get(Klub, klub_id)
    if not klub or not klub.aktivan:
        return RedirectResponse("/klub/dashboard", status_code=302)

    rows = (await db.execute(
        select(Registracija, Igrac, Sezona)
        .join(Igrac,  Registracija.igrac_id  == Igrac.id)
        .join(Sezona, Registracija.sezona_id == Sezona.id)
        .where(Registracija.klub_id == klub_id)
        .order_by(Registracija.kreiran_datum.desc())
    )).all()

    sezone = (await db.execute(
        select(Sezona).where(Sezona.aktivna == True).order_by(Sezona.naziv)
    )).scalars().all()

    registracije = [
        {
            "id":            reg.id,
            "br":            reg.br_registracije or "—",
            "status":        reg.status,
            "sezona":        sez.naziv,
            "ime":           igr.ime,
            "prezime":       igr.prezime,
            "datum_rodjenja": igr.datum_rodjenja.strftime("%d.%m.%Y") if igr.datum_rodjenja else "—",
            "drzavljanstvo": igr.drzavljanstvo,
            "igrac_status":  igr.status,
            "kreiran":       reg.kreiran_datum.strftime("%d.%m.%Y") if reg.kreiran_datum else "—",
        }
        for reg, igr, sez in rows
    ]

    return templates.TemplateResponse("klub_igraci.html", {
        "request":      request,
        "user":         user,
        "klub":         klub,
        "registracije": registracije,
        "sezone":       sezone,
        "ok":           request.query_params.get("ok"),
        "error":        request.query_params.get("error"),
    })


@router.post("/klub/igrac/zahtjev")
async def klub_igrac_zahtjev(
    request:        Request,
    ime:            str = Form(...),
    prezime:        str = Form(...),
    datum_rodjenja: str = Form(...),
    drzavljanstvo:  str = Form(...),
    sezona_id:      int = Form(...),
    db: AsyncSession   = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)

    klub_id = int(user["sub"])

    try:
        dob = datetime.date.fromisoformat(datum_rodjenja)
    except ValueError:
        return RedirectResponse("/klub/igraci?error=datum", status_code=302)

    # Provjera duplikata igrača (ime + prezime + DOB, case-insensitive)
    existing = (await db.execute(
        select(Igrac).where(
            func.lower(Igrac.ime)     == ime.strip().lower(),
            func.lower(Igrac.prezime) == prezime.strip().lower(),
            Igrac.datum_rodjenja      == dob,
        )
    )).scalar_one_or_none()

    if existing:
        # Provjera duplikata registracije za ovaj klub + sezona
        ex_reg = (await db.execute(
            select(Registracija).where(
                Registracija.igrac_id  == existing.id,
                Registracija.klub_id   == klub_id,
                Registracija.sezona_id == sezona_id,
                Registracija.status.in_(["na_cekanju", "aktivna"]),
            )
        )).scalar_one_or_none()
        if ex_reg:
            return RedirectResponse("/klub/igraci?error=duplikat", status_code=302)
        igrac = existing
    else:
        igrac = Igrac(
            ime=ime.strip(),
            prezime=prezime.strip(),
            datum_rodjenja=dob,
            drzavljanstvo=drzavljanstvo.strip(),
        )
        db.add(igrac)
        await db.flush()

    db.add(Registracija(
        igrac_id=igrac.id,
        klub_id=klub_id,
        sezona_id=sezona_id,
        status="na_cekanju",
    ))
    await db.commit()
    return RedirectResponse("/klub/igraci?ok=1", status_code=302)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Upravljanje igračima i registracijama
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/igraci", response_class=HTMLResponse)
async def admin_igraci_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    # Zahtjevi (na_cekanju)
    pending_rows = (await db.execute(
        select(Registracija, Igrac, Klub, Sezona)
        .join(Igrac,  Registracija.igrac_id  == Igrac.id)
        .join(Klub,   Registracija.klub_id   == Klub.id)
        .join(Sezona, Registracija.sezona_id == Sezona.id)
        .where(Registracija.status == "na_cekanju")
        .order_by(Registracija.kreiran_datum.desc())
    )).all()

    # Svi igrači
    igraci_rows = (await db.execute(
        select(Igrac, Klub)
        .outerjoin(Klub, Igrac.trenutni_klub_id == Klub.id)
        .order_by(Igrac.prezime, Igrac.ime)
    )).all()

    # Sve registracije (osim pending)
    reg_rows = (await db.execute(
        select(Registracija, Igrac, Klub, Sezona)
        .join(Igrac,  Registracija.igrac_id  == Igrac.id)
        .join(Klub,   Registracija.klub_id   == Klub.id)
        .join(Sezona, Registracija.sezona_id == Sezona.id)
        .where(Registracija.status.in_(["aktivna", "nevazeca", "odbijena"]))
        .order_by(Registracija.kreiran_datum.desc())
    )).all()

    # Klubovi i sezone za formu "Dodaj igrača"
    klubovi = (await db.execute(
        select(Klub).where(Klub.aktivan == True).order_by(Klub.naziv_kluba)
    )).scalars().all()
    sezone  = (await db.execute(select(Sezona).order_by(Sezona.naziv))).scalars().all()

    pending = [
        {
            "id":            r.id,
            "igrac_id":      i.id,
            "ime":           i.ime,
            "prezime":       i.prezime,
            "datum":         i.datum_rodjenja.strftime("%d.%m.%Y") if i.datum_rodjenja else "—",
            "drzavljanstvo": i.drzavljanstvo,
            "klub":          k.naziv_kluba,
            "klub_id":       k.id,
            "sezona":        s.naziv,
            "sezona_id":     r.sezona_id,
            "kreiran":       r.kreiran_datum.strftime("%d.%m.%Y %H:%M") if r.kreiran_datum else "—",
        }
        for r, i, k, s in pending_rows
    ]

    igraci = [
        {
            "id":          i.id,
            "ime":         i.ime,
            "prezime":     i.prezime,
            "datum":       i.datum_rodjenja.strftime("%d.%m.%Y") if i.datum_rodjenja else "—",
            "drzavljanstvo": i.drzavljanstvo,
            "status":      i.status,
            "klub":        k.naziv_kluba if k else "—",
        }
        for i, k in igraci_rows
    ]

    registracije = [
        {
            "id":       r.id,
            "br":       r.br_registracije or "—",
            "status":   r.status,
            "ime":      i.ime,
            "prezime":  i.prezime,
            "igrac_id": i.id,
            "klub":     k.naziv_kluba,
            "klub_id":  k.id,
            "sezona":   s.naziv,
            "datum":    r.kreiran_datum.strftime("%d.%m.%Y") if r.kreiran_datum else "—",
            "nevazeca": r.nevazeca_datum.strftime("%d.%m.%Y") if r.nevazeca_datum else None,
        }
        for r, i, k, s in reg_rows
    ]

    return templates.TemplateResponse("admin_igraci.html", {
        "request":      request,
        "user":         user,
        "pending":      pending,
        "igraci":       igraci,
        "registracije": registracije,
        "klubovi":      klubovi,
        "sezone":       sezone,
        "ok":           request.query_params.get("ok"),
        "error":        request.query_params.get("error"),
        "tab":          request.query_params.get("tab", "zahtjevi"),
    })


# ── Admin: Dodaj igrača direktno (sa registracijom) ──────────
@router.post("/admin/igrac/dodaj")
async def admin_dodaj_igraca(
    request:        Request,
    ime:            str = Form(...),
    prezime:        str = Form(...),
    datum_rodjenja: str = Form(...),
    drzavljanstvo:  str = Form(...),
    klub_id:        int = Form(...),
    sezona_id:      int = Form(...),
    db: AsyncSession   = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    try:
        dob = datetime.date.fromisoformat(datum_rodjenja)
    except ValueError:
        return RedirectResponse("/admin/igraci?tab=zahtjevi&error=datum", status_code=302)

    existing = (await db.execute(
        select(Igrac).where(
            func.lower(Igrac.ime)     == ime.strip().lower(),
            func.lower(Igrac.prezime) == prezime.strip().lower(),
            Igrac.datum_rodjenja      == dob,
        )
    )).scalar_one_or_none()

    if existing:
        ex_reg = (await db.execute(
            select(Registracija).where(
                Registracija.igrac_id  == existing.id,
                Registracija.klub_id   == klub_id,
                Registracija.sezona_id == sezona_id,
                Registracija.status.in_(["na_cekanju", "aktivna"]),
            )
        )).scalar_one_or_none()
        if ex_reg:
            return RedirectResponse("/admin/igraci?tab=igraci&error=duplikat", status_code=302)
        igrac = existing
    else:
        igrac = Igrac(
            ime=ime.strip(),
            prezime=prezime.strip(),
            datum_rodjenja=dob,
            drzavljanstvo=drzavljanstvo.strip(),
        )
        db.add(igrac)
        await db.flush()

    br  = await _gen_br(db, klub_id, sezona_id)
    now = datetime.datetime.now(datetime.timezone.utc)
    db.add(Registracija(
        igrac_id=igrac.id,
        klub_id=klub_id,
        sezona_id=sezona_id,
        br_registracije=br,
        status="aktivna",
        odobren_datum=now,
    ))
    await _azuriraj_klub(db, igrac.id, klub_id)
    await db.commit()
    return RedirectResponse("/admin/igraci?tab=igraci&ok=1", status_code=302)


# ── Admin: Promjena statusa igrača ────────────────────────────
@router.post("/admin/igrac/{igrac_id}/status")
async def admin_igrac_status(
    igrac_id: int,
    request:  Request,
    status:   str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    if status not in ("aktivan", "suspendovan", "neaktivan"):
        return RedirectResponse("/admin/igraci?tab=igraci&error=status", status_code=302)

    igrac = await db.get(Igrac, igrac_id)
    if igrac:
        igrac.status = status
        await db.commit()
    return RedirectResponse("/admin/igraci?tab=igraci&ok=1", status_code=302)


# ── Admin: Odobri zahtjev ─────────────────────────────────────
@router.post("/admin/registracija/{reg_id}/odobri")
async def admin_odobri(reg_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    reg = await db.get(Registracija, reg_id)
    if reg and reg.status == "na_cekanju":
        br = await _gen_br(db, reg.klub_id, reg.sezona_id)
        reg.br_registracije = br
        reg.status          = "aktivna"
        reg.odobren_datum   = datetime.datetime.now(datetime.timezone.utc)
        await _azuriraj_klub(db, reg.igrac_id, reg.klub_id)
        await db.commit()
    return RedirectResponse("/admin/igraci?tab=zahtjevi&ok=1", status_code=302)


# ── Admin: Odbij zahtjev ──────────────────────────────────────
@router.post("/admin/registracija/{reg_id}/odbij")
async def admin_odbij(reg_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    reg = await db.get(Registracija, reg_id)
    if reg and reg.status == "na_cekanju":
        reg.status = "odbijena"
        await db.commit()
    return RedirectResponse("/admin/igraci?tab=zahtjevi&ok=1", status_code=302)


# ── Admin: Proglasi nevažećom (NEPOVRATNO) ───────────────────
@router.post("/admin/registracija/{reg_id}/nevazeca")
async def admin_nevazeca(reg_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    reg = await db.get(Registracija, reg_id)
    if reg and reg.status == "aktivna":
        reg.status         = "nevazeca"
        reg.nevazeca_datum = datetime.datetime.now(datetime.timezone.utc)
        await db.commit()
    return RedirectResponse("/admin/igraci?tab=registracije&ok=1", status_code=302)


# ── Admin: Bulk poništavanje (NEPOVRATNO) ─────────────────────
@router.post("/admin/registracije/nevazece-bulk")
async def admin_nevazece_bulk(
    request:  Request,
    reg_ids:  str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    try:
        ids = [int(x.strip()) for x in reg_ids.split(",") if x.strip()]
    except ValueError:
        return RedirectResponse("/admin/igraci?tab=registracije&error=ids", status_code=302)

    if ids:
        regs = (await db.execute(
            select(Registracija).where(
                Registracija.id.in_(ids),
                Registracija.status == "aktivna",
            )
        )).scalars().all()
        now = datetime.datetime.now(datetime.timezone.utc)
        for reg in regs:
            reg.status         = "nevazeca"
            reg.nevazeca_datum = now
        await db.commit()
    return RedirectResponse("/admin/igraci?tab=registracije&ok=1", status_code=302)
