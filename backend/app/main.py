"""Main entry point for the SmartSort FastAPI backend."""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.health import router as health_router
from app.api.folder import router as folder_router
from app.api.scan import router as scan_router
from app.api.duplicates import router as duplicates_router
from app.api.extract import router as extract_router
from app.api.analyze import router as analyze_router
from app.api.search import router as search_router
from app.api.organization import router as organization_router, alt_router as organization_alt_router
from app.api.agent import router as agent_router
from app.api.tools import router as tools_router
from app.api.approval import router as approval_router
from app.api.execute import router as execute_router
from app.api.verify import router as verify_router
from app.database.schema import init_db
from app.tools.definitions import build_registry

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Initialize database schema on startup
    init_db()
    # Build and attach the Phase 10 Tool Registry
    app.state.tool_registry = build_registry()
    yield

app = FastAPI(
    title="SmartSort Backend",
    description="Local-first AI-assisted file and folder organization backend.",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure CORS for local development origins
configured_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in configured_origins if origin.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(folder_router)
app.include_router(scan_router)
app.include_router(duplicates_router)
app.include_router(extract_router)
app.include_router(analyze_router)
app.include_router(search_router)
app.include_router(organization_router)
app.include_router(organization_alt_router)
app.include_router(agent_router)
app.include_router(tools_router)
app.include_router(approval_router)
app.include_router(execute_router)
app.include_router(verify_router)

