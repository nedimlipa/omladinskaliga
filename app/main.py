from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from contextlib import asynccontextmanager
from .database import engine, Base
from .routers import auth, dashboard


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Kreiraj tabele ako ne postoje
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="ORL Sjever", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)


@app.get("/")
async def root():
    return RedirectResponse("/login", status_code=302)
