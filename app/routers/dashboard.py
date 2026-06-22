from fastapi import APIRouter, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Klub, Admin, Uzrast, Sezona, Takmicenje, PrijavaKluba, Igrac, Registracija, SluzbenoLice, RegistracijaSL, PozicijaSL, Tabela, TabelaEkipa, Utakmica, TabelaSortPravilo
from .tabele import _izracunaj
from ..security import hash_password
from .auth import get_current_user
import os, io, time
from PIL import Image

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def require_admin(request: Request):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def require_klub(request: Request):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


# ── ADMIN DASHBOARD ─────────────────────────────────────────
@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    klubovi = (await db.execute(select(Klub).order_by(Klub.naziv_kluba))).scalars().all()
    admini  = (await db.execute(select(Admin).order_by(Admin.prezime))).scalars().all()

    return templates.TemplateResponse("dashboard_admin.html", {
        "request": request,
        "user": user,
        "klubovi": klubovi,
        "admini": admini,
    })


# ── TOGGLE STATUS KLUBA ──────────────────────────────────────
@router.post("/admin/klub/{klub_id}/toggle")
async def toggle_klub_status(klub_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    klub = await db.get(Klub, klub_id)
    if not klub:
        raise HTTPException(status_code=404, detail="Klub nije pronađen")

    klub.aktivan = not klub.aktivan
    await db.commit()
    return RedirectResponse("/admin/dashboard", status_code=302)


# ── DODAJ KLUB ───────────────────────────────────────────────
@router.post("/admin/klub/dodaj")
async def dodaj_klub(
    request: Request,
    naziv_kluba: str   = Form(...),
    username:    str   = Form(...),
    password:    str   = Form(...),
    email:       str   = Form(...),
    kontakt_osoba: str = Form(""),
    mobitel:     str   = Form(""),
    grad:        str   = Form(""),
    db: AsyncSession   = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    novi = Klub(
        naziv_kluba=naziv_kluba,
        username=username,
        password_hash=hash_password(password),
        email=email,
        kontakt_osoba=kontakt_osoba or None,
        mobitel=mobitel or None,
        grad=grad or None,
    )
    db.add(novi)
    await db.commit()
    return RedirectResponse("/admin/dashboard", status_code=302)


# ── KLUB DASHBOARD ───────────────────────────────────────────
@router.get("/klub/dashboard", response_class=HTMLResponse)
async def klub_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)

    klub_id = int(user["sub"])
    klub    = await db.get(Klub, klub_id)
    ok      = request.query_params.get("ok")
    error   = request.query_params.get("error")

    # Dostupni uzrasti za prijavu (aktivni)
    uzrasti_rows = (await db.execute(
        select(Uzrast, Sezona, Takmicenje)
        .join(Sezona, Uzrast.sezona_id == Sezona.id)
        .join(Takmicenje, Sezona.takmicenje_id == Takmicenje.id)
        .where(Uzrast.aktivan == True, Sezona.aktivna == True, Takmicenje.aktivan == True)
        .order_by(Takmicenje.naziv, Sezona.naziv, Uzrast.naziv)
    )).all()

    # Postojeće prijave kluba
    prijave_rows = (await db.execute(
        select(PrijavaKluba, Uzrast, Sezona, Takmicenje)
        .join(Uzrast,      PrijavaKluba.uzrast_id    == Uzrast.id)
        .join(Sezona,      Uzrast.sezona_id           == Sezona.id)
        .join(Takmicenje,  Uzrast.takmicenje_id       == Takmicenje.id)
        .where(PrijavaKluba.klub_id == klub_id)
        .order_by(PrijavaKluba.prijavljen_datum.desc())
    )).all()

    registered_ids = {row[0].uzrast_id for row in prijave_rows}

    available = [
        {"id": uzrast.id, "label": f"{tak.naziv}  ·  {sez.naziv}  ·  {uzrast.naziv}"}
        for uzrast, sez, tak in uzrasti_rows
        if uzrast.id not in registered_ids
    ]

    moje_prijave = [
        {
            "id":          prijava.id,
            "takmicenje":  tak.naziv,
            "sezona":      sez.naziv,
            "uzrast":      uzrast.naziv,
            "status":      prijava.status,
            "datum":       prijava.prijavljen_datum.strftime("%d.%m.%Y") if prijava.prijavljen_datum else "—",
        }
        for prijava, uzrast, sez, tak in prijave_rows
    ]

    # Aktivni igrači kluba (potvrđene registracije)
    igrac_rows = (await db.execute(
        select(Registracija, Igrac)
        .join(Igrac, Registracija.igrac_id == Igrac.id)
        .where(Registracija.klub_id == klub_id, Registracija.status == "aktivna")
        .order_by(Igrac.prezime, Igrac.ime)
    )).all()

    igraci_kluba = [
        {
            "br":      reg.br_registracije or "—",
            "ime":     igr.ime,
            "prezime": igr.prezime,
            "datum":   igr.datum_rodjenja.strftime("%d.%m.%Y") if igr.datum_rodjenja else "—",
            "status":  igr.status,
        }
        for reg, igr in igrac_rows
    ]

    # Aktivna službena lica kluba
    sl_rows = (await db.execute(
        select(RegistracijaSL, SluzbenoLice, PozicijaSL)
        .join(SluzbenoLice, RegistracijaSL.sluzbeno_lice_id == SluzbenoLice.id)
        .outerjoin(PozicijaSL, SluzbenoLice.pozicija_id == PozicijaSL.id)
        .where(RegistracijaSL.klub_id == klub_id, RegistracijaSL.status == "aktivna")
        .order_by(SluzbenoLice.prezime, SluzbenoLice.ime)
    )).all()

    sluzbena_kluba = [
        {
            "br":      reg.br_registracije or "—",
            "ime":     sl.ime,
            "prezime": sl.prezime,
            "pozicija": poz.naziv if poz else "—",
            "status":  sl.status,
        }
        for reg, sl, poz in sl_rows
    ]

    # ── Tabele u kojima je ovaj klub ─────────────────────────
    moje_tabele = []
    tabela_member_rows = (await db.execute(
        select(Tabela, TabelaEkipa)
        .join(TabelaEkipa, Tabela.id == TabelaEkipa.tabela_id)
        .join(PrijavaKluba, TabelaEkipa.prijava_id == PrijavaKluba.id)
        .where(
            PrijavaKluba.klub_id  == klub_id,
            Tabela.aktivan        == True,
            TabelaEkipa.aktivan   == True,
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
        odigrane = sum(1 for u in utakmice_t if u.odigrana)

        moje_tabele.append({
            "tabela":    t_tabela,
            "uzrast":    uzrast_t,
            "sezona":    sezona_t,
            "takm":      takm_t,
            "standings": standings_t,
            "moj_rank":  moj_rank,
            "moja_prijava_id": moja_te.prijava_id,
            "odigrane":  odigrane,
        })

    return templates.TemplateResponse("dashboard_klub.html", {
        "request":        request,
        "user":           user,
        "klub":           klub,
        "ok":             ok,
        "error":          error,
        "available":      available,
        "moje_prijave":   moje_prijave,
        "igraci_kluba":   igraci_kluba,
        "sluzbena_kluba": sluzbena_kluba,
        "moje_tabele":    moje_tabele,
    })


# ── UREDI PROFIL KLUBA ────────────────────────────────────────
@router.post("/klub/profil/uredi")
async def uredi_profil_kluba(
    request: Request,
    naziv_kluba:   str = Form(...),
    email:         str = Form(...),
    kontakt_osoba: str = Form(""),
    mobitel:       str = Form(""),
    grad:          str = Form(""),
    nova_lozinka:  str = Form(""),
    db: AsyncSession   = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)

    klub = await db.get(Klub, int(user["sub"]))
    if not klub:
        return RedirectResponse("/login", status_code=302)

    klub.naziv_kluba   = naziv_kluba.strip()
    klub.email         = email.strip()
    klub.kontakt_osoba = kontakt_osoba.strip() or None
    klub.mobitel       = mobitel.strip() or None
    klub.grad          = grad.strip() or None

    if nova_lozinka.strip():
        klub.password_hash = hash_password(nova_lozinka.strip())

    await db.commit()
    return RedirectResponse("/klub/dashboard?ok=1", status_code=302)


# ── UPLOAD LOGO KLUBA ─────────────────────────────────────────
LOGO_DIR = "static/logos"
ALLOWED  = {"image/jpeg", "image/jpg", "image/png"}
MAX_KB   = 100


def _process_logo(data: bytes, content_type: str) -> bytes:
    """Resize to max 512x512, convert to JPEG, compress to ≤100 KB."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((512, 512), Image.LANCZOS)
    for quality in range(85, 20, -5):
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        if buf.tell() <= MAX_KB * 1024:
            return buf.getvalue()
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=20, optimize=True)
    return buf.getvalue()


@router.post("/klub/logo/upload")
async def upload_logo(
    request: Request,
    logo: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)

    if logo.content_type not in ALLOWED:
        return RedirectResponse("/klub/dashboard?error=format", status_code=302)

    raw = await logo.read()
    if not raw:
        return RedirectResponse("/klub/dashboard?error=empty", status_code=302)

    processed = _process_logo(raw, logo.content_type)

    os.makedirs(LOGO_DIR, exist_ok=True)
    klub_id  = int(user["sub"])
    filename = f"klub_{klub_id}_{int(time.time())}.jpg"
    filepath = os.path.join(LOGO_DIR, filename)

    with open(filepath, "wb") as f:
        f.write(processed)

    klub = await db.get(Klub, klub_id)
    if klub:
        # Obriši stari logo fajl ako postoji
        if klub.logo and klub.logo != filename:
            old_path = os.path.join(LOGO_DIR, klub.logo)
            if os.path.isfile(old_path):
                os.remove(old_path)
        klub.logo = filename
        await db.commit()

    return RedirectResponse("/klub/dashboard?ok=1", status_code=302)
