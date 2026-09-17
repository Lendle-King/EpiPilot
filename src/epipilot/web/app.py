"""Read-only, same-origin HTTP surface for the local workbench."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from epipilot.web.models import ProjectView
from epipilot.web.service import BackendName, SearchResponse, TaskProposal, WorkbenchService

_STATIC = Path(__file__).parent / "static"
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


def create_app(service: WorkbenchService) -> FastAPI:
    """Create a local reader, never an executor or a canonical-state write API."""
    app = FastAPI(title="EpiPilot Workbench", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def boundaries(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        origin = request.headers.get("origin")
        same_origin = str(request.base_url).rstrip("/")
        if origin is not None and origin != same_origin:
            return JSONResponse({"detail": "Cross-origin access is not allowed"}, status_code=403)
        if request.headers.get("sec-fetch-site") not in {None, "same-origin", "none"}:
            return JSONResponse({"detail": "Cross-site access is not allowed"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail": "This workbench is read-only"}, status_code=405)
        try:
            response = await call_next(request)
        except (ValueError, OSError, sqlite3.Error):
            response = JSONResponse(
                {"detail": "数据源不可用或未通过校验。请检查仓库、项目 ID 与事件库；旧视图不代表最新状态。"},
                status_code=503,
            )
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.mount("/assets", StaticFiles(directory=str(_STATIC)), name="assets")

    @app.get("/", response_class=FileResponse)
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/project")
    def project() -> ProjectView:
        return service.snapshot()

    @app.get("/api/search")
    def search(q: Annotated[str, Query(min_length=1, max_length=200)]) -> SearchResponse:
        if not q.strip():
            raise HTTPException(status_code=422, detail="Search query must not be blank")
        return service.search(q)

    @app.get("/api/proposals/{card_id}")
    def proposal(card_id: str, backend: BackendName = "codex") -> TaskProposal:
        try:
            return service.proposal(card_id, backend)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="Proposal source not found") from error

    @app.get("/api/report")
    def report() -> Response:
        return Response(service.report(), media_type="text/markdown", headers={
            "Content-Disposition": 'attachment; filename="epipilot-project-report.md"',
        })

    return app
