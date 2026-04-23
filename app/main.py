"""
Setu Payment Reconciliation Service
====================================
FastAPI application entrypoint.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import Base, engine
from app.routers import events, reconciliation, transactions

logging.basicConfig(level=logging.DEBUG if settings.DEBUG else logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup (idempotent — won't drop existing tables)
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables initialised.")
    yield
    logger.info("Shutting down.")


app = FastAPI(
    title="Setu Payment Reconciliation Service",
    description=(
        "A backend service for ingesting payment lifecycle events, "
        "maintaining transaction state, and identifying reconciliation discrepancies."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# Register routers
app.include_router(events.router)
app.include_router(transactions.router)
app.include_router(reconciliation.router)


@app.get("/health", tags=["Meta"])
def health_check():
    return {"status": "ok", "version": settings.APP_VERSION}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )
