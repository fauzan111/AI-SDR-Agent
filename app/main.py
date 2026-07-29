"""FastAPI application entry point for the SDR agent."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import health, leads, webhooks
from app.auth.tenant import TenantContext, get_tenant_context, issue_token
from app.config import get_settings
from app.db.base import Base, engine, get_db
from app.db.models import Tenant
from app.observability.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger("startup")
    Base.metadata.create_all(bind=engine)  # dev convenience; prod uses Alembic
    log.info("app.started", app=get_settings().app_name, env=get_settings().environment)
    yield


app = FastAPI(title=get_settings().app_name, version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(leads.router)


class SignupIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    # ICP hard filters (deterministic qualification).
    icp_min_company_size: int = Field(default=10, ge=0)
    icp_industries: str = Field(default="", description="comma-separated; empty = any")
    qualification_threshold: int = Field(default=60, ge=0, le=100)


class SignupOut(BaseModel):
    tenant_id: str
    name: str
    access_token: str  # dev convenience token


@app.post("/tenants/signup", response_model=SignupOut, tags=["tenants"])
def signup(body: SignupIn, db: Session = Depends(get_db)) -> SignupOut:
    tenant = Tenant(
        name=body.name,
        icp_min_company_size=body.icp_min_company_size,
        icp_industries=body.icp_industries,
        qualification_threshold=body.qualification_threshold,
    )
    db.add(tenant)
    db.commit()
    token = issue_token(tenant_id=tenant.id, user_id="owner")
    return SignupOut(tenant_id=tenant.id, name=tenant.name, access_token=token)


@app.get("/me", tags=["tenants"])
def whoami(ctx: TenantContext = Depends(get_tenant_context)) -> dict:
    return {"tenant_id": ctx.tenant_id, "user_id": ctx.user_id}


# --- Frontend: single-page console served by the same app (no separate deploy) ---
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html")
