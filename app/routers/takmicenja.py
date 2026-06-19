from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Takmicenje, Sezona, Uzrast, PrijavaKluba
from .auth import get_current_user
import datetime

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ═══════════════════════════════════════════════════════════════
#  ADMIN — Pregled takmičenja / sezona / uzrasti
# ═══════════════════════════════════════════════════════════════

@router.get("/admin/takmicenja", response_class=HTMLResponse)
async def admin_takmicenja_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)

    takmicenja = (await db.execute(select(Takmicenje).order_by(Takmicenje.naziv))).scalars().all()
    sezone     = (await db.execute(select(Sezona).order_by(Sezona.naziv))).scalars().all()
    uzrasti    = (await db.execute(select(Uzrast).order_by(Uzrast.naziv))).scalars().all()
    prijave    = (await db.execute(select(PrijavaKluba))).scalars().all()

    return templates.TemplateResponse("admin_takmicenja.html", {
        "request":    request,
        "user":       user,
        "takmicenja": takmicenja,
        "sezone":     sezone,
        "uzrasti":    uzrasti,
        "prijave":    prijave,
        "ok":         request.query_params.get("ok"),
    })


# ── Dodaj takmičenje ──────────────────────────────────────────
@router.post("/admin/takmicenje/dodaj")
async def dodaj_takmicenje(
    request: Request,
    naziv: str = Form(...),
    opis:  str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    db.add(Takmicenje(naziv=naziv.strip(), opis=opis.strip() or None))
    await db.commit()
    return RedirectResponse("/admin/takmicenja?ok=1", status_code=302)


# ── Toggle takmičenje ─────────────────────────────────────────
@router.post("/admin/takmicenje/{tak_id}/toggle")
async def toggle_takmicenje(tak_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    t = await db.get(Takmicenje, tak_id)
    if t:
        t.aktivan = not t.aktivan
        await db.commit()
    return RedirectResponse("/admin/takmicenja", status_code=302)


# ── Dodaj sezonu ──────────────────────────────────────────────
@router.post("/admin/sezona/dodaj")
async def dodaj_sezonu(
    request: Request,
    naziv:         str = Form(...),
    takmicenje_id: int = Form(...),
    datum_od:      str = Form(""),
    datum_do:      str = Form(""),
    db: AsyncSession   = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    db.add(Sezona(
        naziv=naziv.strip(),
        takmicenje_id=takmicenje_id,
        datum_od=datetime.date.fromisoformat(datum_od) if datum_od else None,
        datum_do=datetime.date.fromisoformat(datum_do) if datum_do else None,
    ))
    await db.commit()
    return RedirectResponse("/admin/takmicenja?ok=1", status_code=302)


# ── Toggle sezona ─────────────────────────────────────────────
@router.post("/admin/sezona/{sezona_id}/toggle")
async def toggle_sezona(sezona_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    s = await db.get(Sezona, sezona_id)
    if s:
        s.aktivna = not s.aktivna
        await db.commit()
    return RedirectResponse("/admin/takmicenja", status_code=302)


# ── Dodaj uzrast ──────────────────────────────────────────────
@router.post("/admin/uzrast/dodaj")
async def dodaj_uzrast(
    request: Request,
    naziv:         str = Form(...),
    sezona_id:     int = Form(...),
    takmicenje_id: int = Form(...),
    db: AsyncSession   = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    db.add(Uzrast(naziv=naziv.strip(), sezona_id=sezona_id, takmicenje_id=takmicenje_id))
    await db.commit()
    return RedirectResponse("/admin/takmicenja?ok=1", status_code=302)


# ── Toggle uzrast ─────────────────────────────────────────────
@router.post("/admin/uzrast/{uzrast_id}/toggle")
async def toggle_uzrast(uzrast_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") not in ("admin", "moderator"):
        return RedirectResponse("/login", status_code=302)
    u = await db.get(Uzrast, uzrast_id)
    if u:
        u.aktivan = not u.aktivan
        await db.commit()
    return RedirectResponse("/admin/takmicenja", status_code=302)


# ═══════════════════════════════════════════════════════════════
#  KLUB — Prijava / Otkaz prijave
# ═══════════════════════════════════════════════════════════════

@router.post("/klub/prijava/dodaj")
async def dodaj_prijavu(
    request:   Request,
    uzrast_id: int = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)
    klub_id = int(user["sub"])
    existing = (await db.execute(
        select(PrijavaKluba).where(
            PrijavaKluba.klub_id  == klub_id,
            PrijavaKluba.uzrast_id == uzrast_id,
        )
    )).scalar_one_or_none()
    if not existing:
        db.add(PrijavaKluba(klub_id=klub_id, uzrast_id=uzrast_id))
        await db.commit()
    return RedirectResponse("/klub/dashboard?ok=1", status_code=302)


@router.post("/klub/prijava/{prijava_id}/otkazi")
async def otkazi_prijavu(prijava_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    user = get_current_user(request)
    if not user or user.get("tip") != "klub":
        return RedirectResponse("/login", status_code=302)
    p = await db.get(PrijavaKluba, prijava_id)
    if p and p.klub_id == int(user["sub"]) and p.status == "prijavljen":
        await db.delete(p)
        await db.commit()
    return RedirectResponse("/klub/dashboard", status_code=302)
