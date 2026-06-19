from fastapi import APIRouter, Request, Form, Depends, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from ..database import get_db
from ..models import Klub, Admin
from ..security import verify_password, create_token, decode_token

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def get_current_user(request: Request) -> dict | None:
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(
            "/admin/dashboard" if user.get("tip") in ("admin", "moderator") else "/klub/dashboard",
            status_code=302
        )
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # Provjeri admin/moderator tabelu
    result = await db.execute(select(Admin).where(Admin.username == username))
    admin = result.scalar_one_or_none()
    if admin and admin.aktivan and verify_password(password, admin.password_hash):
        token = create_token({"sub": str(admin.id), "tip": admin.uloga, "ime": f"{admin.ime} {admin.prezime}"})
        resp = RedirectResponse("/admin/dashboard", status_code=302)
        resp.set_cookie("access_token", token, httponly=True, samesite="lax")
        return resp

    # Provjeri klubove
    result = await db.execute(select(Klub).where(Klub.username == username))
    klub = result.scalar_one_or_none()
    if klub and klub.aktivan and verify_password(password, klub.password_hash):
        token = create_token({"sub": str(klub.id), "tip": "klub", "ime": klub.naziv_kluba})
        resp = RedirectResponse("/klub/dashboard", status_code=302)
        resp.set_cookie("access_token", token, httponly=True, samesite="lax")
        return resp

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Pogrešno korisničko ime ili lozinka."},
        status_code=401,
    )


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("access_token")
    return resp
