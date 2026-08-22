"""Health and readiness probes.

Conceptual separation:

- ``GET /health`` — liveness. Is the process up and able to serve requests?
  Always cheap, always dependency-free.
- ``GET /readiness`` — readiness. Is the service configured well enough to
  receive traffic? Reports per-dependency *configuration* status (never secret
  values) and fails closed with HTTP 503 on misconfiguration. Live dependency
  connectivity checks are deferred to Phase 2 with the database layer.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ConfigStatus(BaseModel):
    database_configured: bool
    auth_configured: bool
    llm_configured: bool
    razorpay_configured: bool


class ReadinessResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    environment: str
    config: ConfigStatus
    issues: list[str] = Field(default_factory=list)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness probe",
)
def read_health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/readiness",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "Misconfigured"}},
    summary="Readiness probe",
)
def read_readiness(settings: Settings = Depends(get_settings)) -> ReadinessResponse:
    issues = settings.configuration_issues()
    payload = ReadinessResponse(
        status="unavailable" if issues else "ready",
        environment=settings.environment,
        config=ConfigStatus(
            database_configured=settings.database_url is not None,
            auth_configured=settings.jwt_secret is not None,
            llm_configured=settings.llm_api_key is not None,
            razorpay_configured=(
                settings.razorpay_key_id is not None
                and settings.razorpay_key_secret is not None
            ),
        ),
        issues=issues,
    )
    if issues:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content=payload.model_dump())
    return payload
