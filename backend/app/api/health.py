"""Health check endpoint definition."""

from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api", tags=["system"])


class HealthResponse(BaseModel):
    status: str = Field(default="ok", description="Operational status of the backend service")
    app: str = Field(default="SmartSort", description="Application name")
    version: str = Field(default="0.1.0", description="Current application version")


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Return health and status information for the SmartSort backend."""
    return HealthResponse(
        status="ok",
        app="SmartSort",
        version="0.1.0",
    )
