"""Unified browser console and metadata API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from .console_ui import catalog, render_console


def create_console_app() -> FastAPI:
    """Create the dependency-free unified console application."""
    app = FastAPI(title="LLM Budget Gateway Console", version="7.1.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/console", response_class=HTMLResponse)
    async def console() -> str:
        return render_console()

    @app.get("/v1/console/catalog")
    async def console_catalog() -> dict[str, object]:
        centers = catalog()
        return {
            "version": "7.1.0",
            "centers": centers,
            "center_count": len(centers),
            "capability_count": sum(len(center["capabilities"]) for center in centers),
        }

    return app
