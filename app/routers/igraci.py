import datetime
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from ..database import get_db
from ..models import Igrac, Registracija, Klub, Sezona, SluzbenoLice, RegistracijaSL, PozicijaSL
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
    return f"{klub_id}-{sezona_id}-{seq + 1}"


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
    request:          Request,
    ime:              str = Form(...),
    prezime:          str = Form(...),
    datum_rodjenja:   str = Form(...),
    drzavljanstvo:    str = Form(...),
    sezona_id:        int = Form(...),
    br_registracije:  str = Form(""),
    db: AsyncSession     = Depends(get_db),
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
        br_registracije=br_registracije.strip() or None,
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
            "br":            r.br_registracije or "",
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
        if not reg.br_registracije:
            reg.br_registracije = await _gen_br(db, reg.klub_id, reg.sezona_id)
        reg.status        = "aktivna"
        reg.odobren_datum = datetime.datetime.now(datetime.timezone.utc)
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


# ── Admin: Bulk odobravanje zahtjeva ─────────────────────────
@router.post("/admin/registracije/odobri-bulk")
async def admin_odobri_bulk(
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
        return RedirectResponse("/admin/igraci?tab=zahtjevi&error=ids", status_code=302)
    if ids:
        regs = (await db.execute(
            select(Registracija).where(
                Registracija.id.in_(ids),
                Registracija.status == "na_cekanju",
            )
        )).scalars().all()
        now = datetime.datetime.now(datetime.timezone.utc)
        for reg in regs:
            if not reg.br_registracije:
                reg.br_registracije = await _gen_br(db, reg.klub_id, reg.sezona_id)
            reg.status        = "aktivna"
            reg.odobren_datum = now
            await _azuriraj_klub(db, reg.igrac_id, reg.klub_id)
        await db.commit()
    return RedirectResponse("/admin/igraci?tab=zahtjevi&ok=1", status_code=302)


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


# ═══════════════════════════════════════════════════════════════
#  PRINT — A4 spisak igrača kluba (admin ili vlastiti klub)
# ═══════════════════════════════════════════════════════════════
#  ADMIN — Detaljna stranica kluba (igrači + službena lica)
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/klub/{klub_id}", response_class=HTMLResponse)
async def admin_klub_detalji(klub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    klub = await db.get(Klub, klub_id)
    if not klub:
        return RedirectResponse("/admin/dashboard", status_code=302)

    # ── Sve registracije igrača (sve statuse) ──
    igrac_rows = (await db.execute(
        select(Registracija, Igrac, Sezona)
        .join(Igrac,  Registracija.igrac_id  == Igrac.id)
        .join(Sezona, Registracija.sezona_id == Sezona.id)
        .where(Registracija.klub_id == klub_id)
        .order_by(Registracija.status, Igrac.prezime, Igrac.ime)
    )).all()

    igraci = [
        {
            "ime":           igr.ime,
            "prezime":       igr.prezime,
            "datum":         igr.datum_rodjenja.strftime("%d.%m.%Y") if igr.datum_rodjenja else "—",
            "drzavljanstvo": igr.drzavljanstvo or "—",
            "br":            reg.br_registracije or "—",
            "sezona":        sez.naziv,
            "reg_status":    reg.status,
            "igrac_status":  igr.status,
        }
        for reg, igr, sez in igrac_rows
    ]

    # ── Sve registracije SL (sve statuse) ──
    sl_rows = (await db.execute(
        select(RegistracijaSL, SluzbenoLice, Sezona, PozicijaSL)
        .join(SluzbenoLice, RegistracijaSL.sluzbeno_lice_id == SluzbenoLice.id)
        .join(Sezona,       RegistracijaSL.sezona_id        == Sezona.id)
        .outerjoin(PozicijaSL, SluzbenoLice.pozicija_id     == PozicijaSL.id)
        .where(RegistracijaSL.klub_id == klub_id)
        .order_by(RegistracijaSL.status, SluzbenoLice.prezime, SluzbenoLice.ime)
    )).all()

    sluzbena = [
        {
            "ime":        sl.ime,
            "prezime":    sl.prezime,
            "datum":      sl.datum_rodjenja.strftime("%d.%m.%Y") if sl.datum_rodjenja else "—",
            "mjesto":     sl.mjesto or "—",
            "pozicija":   poz.naziv if poz else "—",
            "br":         reg.br_registracije or "—",
            "sezona":     sez.naziv,
            "reg_status": reg.status,
            "sl_status":  sl.status,
        }
        for reg, sl, sez, poz in sl_rows
    ]

    return templates.TemplateResponse("admin_klub.html", {
        "request":  request,
        "user":     user,
        "klub":     klub,
        "igraci":   igraci,
        "sluzbena": sluzbena,
    })


# ═══════════════════════════════════════════════════════════════
#  PRINT — A4 spisak igrača kluba (admin ili vlastiti klub)
# ═══════════════════════════════════════════════════════════════
async def print_igraci_kluba(klub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    tip = user.get("tip")
    # Klub može štampati samo vlastite igrače; admin može bilo koji klub
    if tip == "klub" and int(user["sub"]) != klub_id:
        return RedirectResponse("/klub/igraci", status_code=302)
    if tip not in ("admin", "moderator", "klub"):
        return RedirectResponse("/login", status_code=302)

    klub = await db.get(Klub, klub_id)
    if not klub:
        back = "/admin/igraci" if tip in ("admin", "moderator") else "/klub/igraci"
        return RedirectResponse(back, status_code=302)

    rows = (await db.execute(
        select(Registracija, Igrac, Sezona)
        .join(Igrac,  Registracija.igrac_id  == Igrac.id)
        .join(Sezona, Registracija.sezona_id == Sezona.id)
        .where(
            Registracija.klub_id == klub_id,
            Registracija.status  == "aktivna",
        )
        .order_by(Igrac.prezime, Igrac.ime)
    )).all()

    igraci = [
        {
            "ime":           igr.ime,
            "prezime":       igr.prezime,
            "datum":         igr.datum_rodjenja.strftime("%d.%m.%Y") if igr.datum_rodjenja else "—",
            "drzavljanstvo": igr.drzavljanstvo or "—",
            "br":            reg.br_registracije or "—",
            "sezona":        sez.naziv,
            "status":        igr.status,
        }
        for reg, igr, sez in rows
    ]

    return templates.TemplateResponse("print_igraci.html", {
        "request":      request,
        "klub":         klub,
        "igraci":       igraci,
        "sezona_naziv": None,
    })



# ═══════════════════════════════════════════════════════════════
#  PRINT — Kombinirani pregled kluba: igrači + službena lica
# ═══════════════════════════════════════════════════════════════

@router.get("/print/klub/{klub_id}", response_class=HTMLResponse)
async def print_klub(klub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    tip = user.get("tip")
    if tip == "klub" and int(user["sub"]) != klub_id:
        return RedirectResponse("/klub/igraci", status_code=302)
    if tip not in ("admin", "moderator", "klub"):
        return RedirectResponse("/login", status_code=302)

    klub = await db.get(Klub, klub_id)
    if not klub:
        back = "/admin/dashboard" if tip in ("admin", "moderator") else "/klub/dashboard"
        return RedirectResponse(back, status_code=302)

    # ── Igrači ──
    igrac_rows = (await db.execute(
        select(Registracija, Igrac, Sezona)
        .join(Igrac,  Registracija.igrac_id  == Igrac.id)
        .join(Sezona, Registracija.sezona_id == Sezona.id)
        .where(
            Registracija.klub_id == klub_id,
            Registracija.status  == "aktivna",
        )
        .order_by(Igrac.prezime, Igrac.ime)
    )).all()

    igraci = [
        {
            "ime":           igr.ime,
            "prezime":       igr.prezime,
            "datum":         igr.datum_rodjenja.strftime("%d.%m.%Y") if igr.datum_rodjenja else "—",
            "drzavljanstvo": igr.drzavljanstvo or "—",
            "br":            reg.br_registracije or "—",
            "sezona":        sez.naziv,
            "status":        igr.status,
        }
        for reg, igr, sez in igrac_rows
    ]

    # ── Službena lica ──
    sl_rows = (await db.execute(
        select(RegistracijaSL, SluzbenoLice, Sezona, PozicijaSL)
        .join(SluzbenoLice, RegistracijaSL.sluzbeno_lice_id == SluzbenoLice.id)
        .join(Sezona,       RegistracijaSL.sezona_id        == Sezona.id)
        .outerjoin(PozicijaSL, SluzbenoLice.pozicija_id     == PozicijaSL.id)
        .where(
            RegistracijaSL.klub_id == klub_id,
            RegistracijaSL.status  == "aktivna",
        )
        .order_by(SluzbenoLice.prezime, SluzbenoLice.ime)
    )).all()

    sluzbena = [
        {
            "ime":           sl.ime,
            "prezime":       sl.prezime,
            "datum":         sl.datum_rodjenja.strftime("%d.%m.%Y") if sl.datum_rodjenja else "—",
            "mjesto":        sl.mjesto or "—",
            "pozicija":      poz.naziv if poz else "—",
            "br":            reg.br_registracije or "—",
            "sezona":        sez.naziv,
            "sl_status":     sl.status,
        }
        for reg, sl, sez, poz in sl_rows
    ]

    return templates.TemplateResponse("print_klub.html", {
        "request":  request,
        "klub":     klub,
        "igraci":   igraci,
        "sluzbena": sluzbena,
    })


# ═══════════════════════════════════════════════════════════════
#  PDF HELPER
# ═══════════════════════════════════════════════════════════════

def _generate_pdf(naziv: str, grad: str | None, igraci_list: list, now: datetime.datetime) -> bytes:
    """Generiraj A4 PDF sa spiskom igrača kluba (fpdf2)."""
    try:
        from fpdf import FPDF
    except ImportError:
        raise RuntimeError("fpdf2 nije instaliran. Pokrenite: pip install fpdf2")

    pdf = FPDF('P', 'mm', 'A4')
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, 20)

    F = 'Helvetica'
    try:
        pdf.add_font('DV', '',  '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        pdf.add_font('DV', 'B', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
        F = 'DV'
    except Exception:
        pass

    pdf.add_page()

    # ── Crvena traka gore ──────────────────────────────────────
    pdf.set_fill_color(220, 38, 38)
    pdf.rect(0, 0, 210, 2.5, 'F')

    # ── Header ───────────────────────────────────────────
    pdf.set_xy(15, 8)
    pdf.set_font(F, '', 7)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(120, 4, 'OMLADINSKA RUKOMETNA LIGA SJEVER', ln=0)

    pdf.set_xy(135, 8)
    pdf.set_font(F, 'B', 11)
    pdf.set_text_color(220, 38, 38)
    pdf.cell(60, 4.5, 'SPISAK IGRACA', align='R', ln=0)

    pdf.set_xy(15, 13)
    pdf.set_font(F, 'B', 18)
    pdf.set_text_color(17, 17, 17)
    pdf.cell(120, 9, naziv[:28] if len(naziv) > 28 else naziv, ln=0)

    pdf.set_xy(135, 13)
    pdf.set_font(F, '', 8.5)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(60, 4.5, 'Datum: ' + now.strftime('%d.%m.%Y'), align='R', ln=0)
    pdf.set_xy(135, 18)
    pdf.cell(60, 4.5, 'Vrijeme: ' + now.strftime('%H:%M:%S'), align='R', ln=0)

    y_sep = 24
    if grad:
        pdf.set_xy(15, 23)
        pdf.set_font(F, '', 9)
        pdf.set_text_color(107, 114, 128)
        pdf.cell(120, 4, grad, ln=0)
        y_sep = 28

    # ── Separator ───────────────────────────────────────
    pdf.set_draw_color(220, 38, 38)
    pdf.set_line_width(0.5)
    pdf.line(15, y_sep, 195, y_sep)

    # ── Statistike ─────────────────────────────────────
    y_s   = y_sep + 3
    n_a   = sum(1 for i in igraci_list if i['status'] == 'aktivan')
    n_s   = sum(1 for i in igraci_list if i['status'] == 'suspendovan')

    pdf.set_fill_color(249, 250, 251)
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.3)
    pdf.rect(15, y_s, 180, 13, 'FD')

    for x, lbl, val, col in [
        (20,  'UKUPNO IGRACA', str(len(igraci_list)), (17,  17,  17)),
        (65,  'AKTIVNIH',      str(n_a),              (21, 128,  61)),
        (110, 'SUSPENDOVANIH', str(n_s),              (220, 38,  38)),
    ]:
        pdf.set_xy(x, y_s + 1.5)
        pdf.set_font(F, '', 6.5)
        pdf.set_text_color(130, 130, 130)
        pdf.cell(40, 3.5, lbl, ln=0)
        pdf.set_xy(x, y_s + 6)
        pdf.set_font(F, 'B', 12)
        pdf.set_text_color(*col)
        pdf.cell(40, 5, val, ln=0)

    pdf.set_xy(145, y_s + 5.5)
    pdf.set_font(F, '', 6.5)
    pdf.set_text_color(160, 160, 160)
    pdf.cell(45, 4, 'Samo aktivne registracije', align='R', ln=0)

    # ── Tabela ─────────────────────────────────────────
    CW  = [7, 52, 26, 24, 31, 22, 18]   # suma = 180
    HDR = ['#', 'Prezime i ime', 'Datum rodjenja', 'Drzavljanstvo',
           'Br. registracije', 'Sezona', 'Status']

    pdf.set_y(y_s + 17)
    pdf.set_x(15)
    pdf.set_fill_color(220, 38, 38)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font(F, 'B', 7.5)
    for i, (h, w) in enumerate(zip(HDR, CW)):
        pdf.cell(w, 7, h, fill=True, ln=1 if i == len(HDR) - 1 else 0)

    for idx, igr in enumerate(igraci_list):
        if idx % 2 == 1:
            pdf.set_fill_color(249, 250, 251)
        else:
            pdf.set_fill_color(255, 255, 255)

        st     = igr['status']
        st_col = (21,128,61) if st == 'aktivan' else (220,38,38) if st == 'suspendovan' else (107,114,128)

        cells = [
            (str(idx + 1),                        F, '',  7.5, (150,150,150)),
            (igr['prezime'] + ' ' + igr['ime'],   F, 'B', 8.0, (17, 17, 17)),
            (igr['datum'],                         F, '',  7.5, (100,100,100)),
            (igr['drzavljanstvo'],                 F, '',  7.5, (100,100,100)),
            (igr['br'],                            F, 'B', 7.5, (55, 65, 81)),
            (igr['sezona'],                        F, '',  7.5, (100,100,100)),
            (st.capitalize(),                      F, 'B', 7.5, st_col),
        ]

        pdf.set_x(15)
        for i, (txt, fam, sty, sz, col) in enumerate(cells):
            pdf.set_font(fam, sty, sz)
            pdf.set_text_color(*col)
            pdf.cell(CW[i], 6.5, txt, fill=True, ln=1 if i == len(cells) - 1 else 0)

    # ── Footer ──────────────────────────────────────────
    pdf.set_y(-14)
    pdf.set_draw_color(229, 231, 235)
    pdf.set_line_width(0.3)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.set_y(pdf.get_y() + 1.5)
    pdf.set_font(F, '', 7.5)
    pdf.set_text_color(156, 163, 175)
    pdf.cell(90, 4, 'ORL Sjever - Automatski generisan dokument', ln=0)
    pdf.set_text_color(107, 114, 128)
    pdf.cell(90, 4, 'Odstampano: ' + now.strftime('%d.%m.%Y') + ' u ' + now.strftime('%H:%M:%S'), align='R', ln=0)

    return bytes(pdf.output())


# ═══════════════════════════════════════════════════════════════
#  DOWNLOAD — Direktno preuzimanje PDF-a igrača kluba
# ═══════════════════════════════════════════════════════════════

@router.get("/download/klub/{klub_id}/igraci.pdf")
async def download_igraci_pdf(klub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)

    tip = user.get("tip")
    if tip == "klub" and int(user["sub"]) != klub_id:
        return RedirectResponse("/klub/igraci", status_code=302)
    if tip not in ("admin", "moderator", "klub"):
        return RedirectResponse("/login", status_code=302)

    klub = await db.get(Klub, klub_id)
    if not klub:
        back = "/admin/igraci" if tip in ("admin", "moderator") else "/klub/igraci"
        return RedirectResponse(back, status_code=302)

    rows = (await db.execute(
        select(Registracija, Igrac, Sezona)
        .join(Igrac,  Registracija.igrac_id  == Igrac.id)
        .join(Sezona, Registracija.sezona_id == Sezona.id)
        .where(Registracija.klub_id == klub_id, Registracija.status == "aktivna")
        .order_by(Igrac.prezime, Igrac.ime)
    )).all()

    igraci = [
        {
            "ime":           igr.ime,
            "prezime":       igr.prezime,
            "datum":         igr.datum_rodjenja.strftime("%d.%m.%Y") if igr.datum_rodjenja else "—",
            "drzavljanstvo": igr.drzavljanstvo or "—",
            "br":            reg.br_registracije or "—",
            "sezona":        sez.naziv,
            "status":        igr.status,
        }
        for reg, igr, sez in rows
    ]

    now = datetime.datetime.now()
    try:
        pdf_bytes = _generate_pdf(klub.naziv_kluba, klub.grad, igraci, now)
    except RuntimeError:
        return RedirectResponse(f"/print/klub/{klub_id}/igraci", status_code=302)

    safe     = "".join(c if c.isalnum() or c in "-_" else "_" for c in klub.naziv_kluba).strip("_")
    filename = f"igraci_{safe}_{now.strftime('%Y%m%d')}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
