import datetime
from fastapi import APIRouter, Request, Depends, Form
from ..templates_config import templates, local_dt_str
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from ..database import get_db
from ..models import SluzbenoLice, RegistracijaSL, PozicijaSL, Klub, Sezona
from .auth import get_current_user

router    = APIRouter()


# ── Helper: generiraj broj registracije SL ───────────────────
async def _gen_br_sl(db: AsyncSession, klub_id: int, sezona_id: int) -> str:
    seq = (await db.execute(
        select(func.count(RegistracijaSL.id)).where(
            RegistracijaSL.klub_id   == klub_id,
            RegistracijaSL.sezona_id == sezona_id,
            RegistracijaSL.br_registracije.isnot(None),
        )
    )).scalar() or 0
    return f"SL-{klub_id}-{sezona_id}-{seq + 1}"


# ── Helper: ažuriraj trenutni/prethodni klub SL ───────────────
async def _azuriraj_klub_sl(db: AsyncSession, sl_id: int, novi_klub_id: int):
    sl = await db.get(SluzbenoLice, sl_id)
    if sl:
        if sl.trenutni_klub_id and sl.trenutni_klub_id != novi_klub_id:
            sl.prethodni_klub_id = sl.trenutni_klub_id
        sl.trenutni_klub_id = novi_klub_id


# ═══════════════════════════════════════════════════════════════
#  KLUB — Stranica za upravljanje službenim licima
# ═══════════════════════════════════════════════════════════════

@router.get("/klub/sluzbena-lica", response_class=HTMLResponse)
async def klub_sl_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)

    klub_id = int(user["sub"])
    klub    = await db.get(Klub, klub_id)
    if not klub or not klub.aktivan:
        return RedirectResponse("/klub/dashboard", status_code=302)

    rows = (await db.execute(
        select(RegistracijaSL, SluzbenoLice, Sezona, PozicijaSL)
        .join(SluzbenoLice, RegistracijaSL.sluzbeno_lice_id == SluzbenoLice.id)
        .join(Sezona,       RegistracijaSL.sezona_id        == Sezona.id)
        .outerjoin(PozicijaSL, SluzbenoLice.pozicija_id     == PozicijaSL.id)
        .where(RegistracijaSL.klub_id == klub_id)
        .order_by(RegistracijaSL.kreiran_datum.desc())
    )).all()

    sezone   = (await db.execute(
        select(Sezona).where(Sezona.aktivna == True).order_by(Sezona.naziv)
    )).scalars().all()

    pozicije = (await db.execute(
        select(PozicijaSL).where(PozicijaSL.aktivan == True).order_by(PozicijaSL.naziv)
    )).scalars().all()

    registracije = [
        {
            "id":              reg.id,
            "br":              reg.br_registracije or "—",
            "status":          reg.status,
            "sezona":          sez.naziv,
            "ime":             sl.ime,
            "prezime":         sl.prezime,
            "datum_rodjenja":  sl.datum_rodjenja.strftime("%d.%m.%Y") if sl.datum_rodjenja else "—",
            "mjesto":          sl.mjesto or "—",
            "pozicija":        poz.naziv if poz else "—",
            "sl_status":       sl.status,
            "kreiran":         reg.kreiran_datum.strftime("%d.%m.%Y") if reg.kreiran_datum else "—",
        }
        for reg, sl, sez, poz in rows
    ]

    return templates.TemplateResponse("klub_sl.html", {
        "request":      request,
        "user":         user,
        "klub":         klub,
        "registracije": registracije,
        "sezone":       sezone,
        "pozicije":     pozicije,
        "ok":           request.query_params.get("ok"),
        "error":        request.query_params.get("error"),
    })


@router.post("/klub/sl/zahtjev")
async def klub_sl_zahtjev(
    request:         Request,
    ime:             str = Form(...),
    prezime:         str = Form(...),
    datum_rodjenja:  str = Form(""),
    mjesto:          str = Form(""),
    pozicija_id:     int = Form(...),
    sezona_id:       int = Form(...),
    br_registracije: str = Form(""),
    db: AsyncSession    = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)

    klub_id = int(user["sub"])

    dob = None
    if datum_rodjenja.strip():
        try:
            dob = datetime.date.fromisoformat(datum_rodjenja.strip())
        except ValueError:
            return RedirectResponse("/klub/sluzbena-lica?error=datum", status_code=302)

    # Provjera duplikata
    existing = (await db.execute(
        select(SluzbenoLice).where(
            func.lower(SluzbenoLice.ime)     == ime.strip().lower(),
            func.lower(SluzbenoLice.prezime) == prezime.strip().lower(),
        )
    )).scalar_one_or_none()

    if existing:
        ex_reg = (await db.execute(
            select(RegistracijaSL).where(
                RegistracijaSL.sluzbeno_lice_id == existing.id,
                RegistracijaSL.klub_id           == klub_id,
                RegistracijaSL.sezona_id         == sezona_id,
                RegistracijaSL.status.in_(["na_cekanju", "aktivna"]),
            )
        )).scalar_one_or_none()
        if ex_reg:
            return RedirectResponse("/klub/sluzbena-lica?error=duplikat", status_code=302)
        sl = existing
    else:
        sl = SluzbenoLice(
            ime=ime.strip(),
            prezime=prezime.strip(),
            datum_rodjenja=dob,
            mjesto=mjesto.strip() or None,
            pozicija_id=pozicija_id,
        )
        db.add(sl)
        await db.flush()

    db.add(RegistracijaSL(
        sluzbeno_lice_id=sl.id,
        klub_id=klub_id,
        sezona_id=sezona_id,
        br_registracije=br_registracije.strip() or None,
        status="na_cekanju",
    ))
    await db.commit()
    return RedirectResponse("/klub/sluzbena-lica?ok=1", status_code=302)


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Upravljanje službenim licima
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/sluzbena-lica", response_class=HTMLResponse)
async def admin_sl_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    pending_rows = (await db.execute(
        select(RegistracijaSL, SluzbenoLice, Klub, Sezona, PozicijaSL)
        .join(SluzbenoLice, RegistracijaSL.sluzbeno_lice_id == SluzbenoLice.id)
        .join(Klub,         RegistracijaSL.klub_id          == Klub.id)
        .join(Sezona,       RegistracijaSL.sezona_id        == Sezona.id)
        .outerjoin(PozicijaSL, SluzbenoLice.pozicija_id     == PozicijaSL.id)
        .where(RegistracijaSL.status == "na_cekanju")
        .order_by(RegistracijaSL.kreiran_datum.desc())
    )).all()

    sl_rows = (await db.execute(
        select(SluzbenoLice, Klub, PozicijaSL)
        .outerjoin(Klub,      SluzbenoLice.trenutni_klub_id == Klub.id)
        .outerjoin(PozicijaSL, SluzbenoLice.pozicija_id     == PozicijaSL.id)
        .order_by(SluzbenoLice.prezime, SluzbenoLice.ime)
    )).all()

    reg_rows = (await db.execute(
        select(RegistracijaSL, SluzbenoLice, Klub, Sezona, PozicijaSL)
        .join(SluzbenoLice, RegistracijaSL.sluzbeno_lice_id == SluzbenoLice.id)
        .join(Klub,         RegistracijaSL.klub_id          == Klub.id)
        .join(Sezona,       RegistracijaSL.sezona_id        == Sezona.id)
        .outerjoin(PozicijaSL, SluzbenoLice.pozicija_id     == PozicijaSL.id)
        .where(RegistracijaSL.status.in_(["aktivna", "nevazeca", "odbijena"]))
        .order_by(RegistracijaSL.kreiran_datum.desc())
    )).all()

    klubovi  = (await db.execute(select(Klub).where(Klub.aktivan == True).order_by(Klub.naziv_kluba))).scalars().all()
    sezone   = (await db.execute(select(Sezona).order_by(Sezona.naziv))).scalars().all()
    pozicije = (await db.execute(select(PozicijaSL).order_by(PozicijaSL.naziv))).scalars().all()

    pending = [
        {
            "id":            r.id,
            "sl_id":         sl.id,
            "ime":           sl.ime,
            "prezime":       sl.prezime,
            "datum":         sl.datum_rodjenja.strftime("%d.%m.%Y") if sl.datum_rodjenja else "—",
            "mjesto":        sl.mjesto or "—",
            "pozicija":      poz.naziv if poz else "—",
            "pozicija_id":   sl.pozicija_id,
            "br":            r.br_registracije or "",
            "klub":          k.naziv_kluba,
            "klub_id":       k.id,
            "sezona":        s.naziv,
            "sezona_id":     r.sezona_id,
            "kreiran":       local_dt_str(r.kreiran_datum),
        }
        for r, sl, k, s, poz in pending_rows
    ]

    sluzbena = [
        {
            "id":        sl.id,
            "ime":       sl.ime,
            "prezime":   sl.prezime,
            "datum":     sl.datum_rodjenja.strftime("%d.%m.%Y") if sl.datum_rodjenja else "—",
            "mjesto":    sl.mjesto or "—",
            "pozicija":  poz.naziv if poz else "—",
            "status":    sl.status,
            "klub":      k.naziv_kluba if k else "—",
        }
        for sl, k, poz in sl_rows
    ]

    registracije = [
        {
            "id":       r.id,
            "br":       r.br_registracije or "—",
            "status":   r.status,
            "ime":      sl.ime,
            "prezime":  sl.prezime,
            "sl_id":    sl.id,
            "pozicija": poz.naziv if poz else "—",
            "klub":     k.naziv_kluba,
            "klub_id":  k.id,
            "sezona":   s.naziv,
            "datum":    r.kreiran_datum.strftime("%d.%m.%Y") if r.kreiran_datum else "—",
            "nevazeca": r.nevazeca_datum.strftime("%d.%m.%Y") if r.nevazeca_datum else None,
        }
        for r, sl, k, s, poz in reg_rows
    ]

    return templates.TemplateResponse("admin_sl.html", {
        "request":      request,
        "user":         user,
        "pending":      pending,
        "sluzbena":     sluzbena,
        "registracije": registracije,
        "klubovi":      klubovi,
        "sezone":       sezone,
        "pozicije":     pozicije,
        "ok":           request.query_params.get("ok"),
        "error":        request.query_params.get("error"),
        "tab":          request.query_params.get("tab", "zahtjevi"),
    })


# ── Admin: Dodaj SL direktno ──────────────────────────────────
@router.post("/admin/sl/dodaj")
async def admin_dodaj_sl(
    request:        Request,
    ime:            str = Form(...),
    prezime:        str = Form(...),
    datum_rodjenja: str = Form(""),
    mjesto:         str = Form(""),
    pozicija_id:    int = Form(...),
    klub_id:        int = Form(...),
    sezona_id:      int = Form(...),
    db: AsyncSession   = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    dob = None
    if datum_rodjenja.strip():
        try:
            dob = datetime.date.fromisoformat(datum_rodjenja.strip())
        except ValueError:
            return RedirectResponse("/admin/sluzbena-lica?tab=zahtjevi&error=datum", status_code=302)

    existing = (await db.execute(
        select(SluzbenoLice).where(
            func.lower(SluzbenoLice.ime)     == ime.strip().lower(),
            func.lower(SluzbenoLice.prezime) == prezime.strip().lower(),
        )
    )).scalar_one_or_none()

    if existing:
        ex_reg = (await db.execute(
            select(RegistracijaSL).where(
                RegistracijaSL.sluzbeno_lice_id == existing.id,
                RegistracijaSL.klub_id           == klub_id,
                RegistracijaSL.sezona_id         == sezona_id,
                RegistracijaSL.status.in_(["na_cekanju", "aktivna"]),
            )
        )).scalar_one_or_none()
        if ex_reg:
            return RedirectResponse("/admin/sluzbena-lica?tab=sluzbena&error=duplikat", status_code=302)
        sl = existing
    else:
        sl = SluzbenoLice(
            ime=ime.strip(),
            prezime=prezime.strip(),
            datum_rodjenja=dob,
            mjesto=mjesto.strip() or None,
            pozicija_id=pozicija_id,
        )
        db.add(sl)
        await db.flush()

    br  = await _gen_br_sl(db, klub_id, sezona_id)
    now = datetime.datetime.now(datetime.timezone.utc)
    db.add(RegistracijaSL(
        sluzbeno_lice_id=sl.id,
        klub_id=klub_id,
        sezona_id=sezona_id,
        br_registracije=br,
        status="aktivna",
        odobren_datum=now,
    ))
    await _azuriraj_klub_sl(db, sl.id, klub_id)
    await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=sluzbena&ok=1", status_code=302)


# ── Admin: Promjena statusa SL ────────────────────────────────
@router.post("/admin/sl/{sl_id}/status")
async def admin_sl_status(
    sl_id:   int,
    request: Request,
    status:  str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    if status not in ("aktivan", "suspendovan", "neaktivan"):
        return RedirectResponse("/admin/sluzbena-lica?tab=sluzbena&error=status", status_code=302)

    sl = await db.get(SluzbenoLice, sl_id)
    if sl:
        sl.status = status
        await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=sluzbena&ok=1", status_code=302)


# ── Admin: Promjena pozicije SL ───────────────────────────────
@router.post("/admin/sl/{sl_id}/pozicija")
async def admin_sl_pozicija(
    sl_id:      int,
    request:    Request,
    pozicija_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    sl = await db.get(SluzbenoLice, sl_id)
    if sl:
        sl.pozicija_id = pozicija_id
        await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=sluzbena&ok=1", status_code=302)


# ── Admin: Promjena kluba SL (sa arhivom) ────────────────────
@router.post("/admin/sl/{sl_id}/klub")
async def admin_sl_promjena_kluba(
    sl_id:    int,
    request:  Request,
    klub_id:  int = Form(...),
    sezona_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    sl = await db.get(SluzbenoLice, sl_id)
    if not sl:
        return RedirectResponse("/admin/sluzbena-lica?tab=sluzbena", status_code=302)

    # Nevažeća prethodna aktivna reg za ovaj SL (ako postoji)
    stara = (await db.execute(
        select(RegistracijaSL).where(
            RegistracijaSL.sluzbeno_lice_id == sl_id,
            RegistracijaSL.status == "aktivna",
        )
    )).scalar_one_or_none()

    now = datetime.datetime.now(datetime.timezone.utc)
    if stara:
        stara.status         = "nevazeca"
        stara.nevazeca_datum = now

    br = await _gen_br_sl(db, klub_id, sezona_id)
    db.add(RegistracijaSL(
        sluzbeno_lice_id=sl.id,
        klub_id=klub_id,
        sezona_id=sezona_id,
        br_registracije=br,
        status="aktivna",
        odobren_datum=now,
    ))
    await _azuriraj_klub_sl(db, sl_id, klub_id)
    await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=sluzbena&ok=1", status_code=302)


# ── Admin: Odobri zahtjev SL ──────────────────────────────────
@router.post("/admin/reg-sl/{reg_id}/odobri")
async def admin_sl_odobri(reg_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    reg = await db.get(RegistracijaSL, reg_id)
    if reg and reg.status == "na_cekanju":
        if not reg.br_registracije:
            reg.br_registracije = await _gen_br_sl(db, reg.klub_id, reg.sezona_id)
        reg.status        = "aktivna"
        reg.odobren_datum = datetime.datetime.now(datetime.timezone.utc)
        sl = await db.get(SluzbenoLice, reg.sluzbeno_lice_id)
        if sl and sl.status == "neaktivan":
            sl.status = "aktivan"
        await _azuriraj_klub_sl(db, reg.sluzbeno_lice_id, reg.klub_id)
        await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=zahtjevi&ok=1", status_code=302)


# ── Admin: Odbij zahtjev SL ───────────────────────────────────
@router.post("/admin/reg-sl/{reg_id}/odbij")
async def admin_sl_odbij(reg_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    reg = await db.get(RegistracijaSL, reg_id)
    if reg and reg.status == "na_cekanju":
        reg.status = "odbijena"
        await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=zahtjevi&ok=1", status_code=302)

# ── Admin: Bulk odobravanje zahtjeva SL ────────────────────
@router.post("/admin/reg-sl/odobri-bulk")
async def admin_sl_odobri_bulk(
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
        return RedirectResponse("/admin/sluzbena-lica?tab=zahtjevi&error=ids", status_code=302)
    if ids:
        regs = (await db.execute(
            select(RegistracijaSL).where(
                RegistracijaSL.id.in_(ids),
                RegistracijaSL.status == "na_cekanju",
            )
        )).scalars().all()
        now = datetime.datetime.now(datetime.timezone.utc)
        for reg in regs:
            if not reg.br_registracije:
                reg.br_registracije = await _gen_br_sl(db, reg.klub_id, reg.sezona_id)
            reg.status        = "aktivna"
            reg.odobren_datum = now
            sl = await db.get(SluzbenoLice, reg.sluzbeno_lice_id)
            if sl and sl.status == "neaktivan":
                sl.status = "aktivan"
            await _azuriraj_klub_sl(db, reg.sluzbeno_lice_id, reg.klub_id)
        await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=zahtjevi&ok=1", status_code=302)

# ── Admin: Nevažeća registracija SL (NEPOVRATNO) ─────────────
@router.post("/admin/reg-sl/{reg_id}/nevazeca")
async def admin_sl_nevazeca(reg_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    reg = await db.get(RegistracijaSL, reg_id)
    if reg and reg.status == "aktivna":
        reg.status         = "nevazeca"
        reg.nevazeca_datum = datetime.datetime.now(datetime.timezone.utc)
        await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=registracije&ok=1", status_code=302)


# ── Admin: Šifarnik pozicija — dodaj ─────────────────────────
@router.post("/admin/pozicija-sl/dodaj")
async def admin_dodaj_poziciju(
    request: Request,
    naziv:   str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    naziv = naziv.strip()
    if naziv:
        existing = (await db.execute(
            select(PozicijaSL).where(func.lower(PozicijaSL.naziv) == naziv.lower())
        )).scalar_one_or_none()
        if not existing:
            db.add(PozicijaSL(naziv=naziv))
            await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=sifarnik&ok=1", status_code=302)


# ── Admin: Šifarnik pozicija — toggle aktivan ─────────────────
@router.post("/admin/pozicija-sl/{poz_id}/toggle")
async def admin_toggle_poziciju(poz_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    poz = await db.get(PozicijaSL, poz_id)
    if poz:
        poz.aktivan = not poz.aktivan
        await db.commit()
    return RedirectResponse("/admin/sluzbena-lica?tab=sifarnik&ok=1", status_code=302)
