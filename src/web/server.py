"""Optional embedded aiohttp.web server lifecycle with proxy logging and auth middleware."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from aiohttp import web
from astrbot.api import logger

from ..core.service import SquadLensService
from .routes import get_client_ip, is_authorized, register_routes

PUBLIC_API_PATHS = frozenset({"/api/health", "/api/auth/status", "/api/auth/verify"})


def create_web_app(
    service: SquadLensService,
    *,
    access_code: str = "",
    enable_3d_replay: bool = True,
) -> web.Application:
    """Create configured aiohttp.web.Application (directly testable via TestClient)."""
    clean_code = (access_code or "").strip()

    @web.middleware
    async def access_and_log_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        start = time.monotonic()
        client_ip = get_client_ip(request)

        if clean_code and request.path.startswith("/api/") and request.path not in PUBLIC_API_PATHS:
            if not is_authorized(request, clean_code):
                return web.json_response(
                    {"error": "需要提供有效的访问口令", "auth_required": True},
                    status=401,
                )

        try:
            response = await handler(request)
            return response
        finally:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            if request.path.startswith("/api/"):
                logger.info(
                    "Web %s %s from %s (%.1fms)",
                    request.method,
                    request.path,
                    client_ip,
                    elapsed_ms,
                )

    app = web.Application(middlewares=[access_and_log_middleware])
    register_routes(app, service, access_code=clean_code, enable_3d_replay=enable_3d_replay)
    return app


class SquadWebServer:
    """Manages startup and hot-reload-safe port cleanup for the embedded Web service."""

    def __init__(
        self,
        service: SquadLensService,
        *,
        host: str = "127.0.0.1",
        port: int = 8001,
        access_code: str = "",
        enable_3d_replay: bool = True,
    ) -> None:
        self.service = service
        self.host = (host or "127.0.0.1").strip()
        self.port = int(port or 8001)
        self.access_code = (access_code or "").strip()
        self.enable_3d_replay = bool(enable_3d_replay)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def is_running(self) -> bool:
        return self._runner is not None

    async def start(self) -> None:
        if self._runner is not None:
            return
        app = create_web_app(
            self.service,
            access_code=self.access_code,
            enable_3d_replay=self.enable_3d_replay,
        )
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, host=self.host, port=self.port, reuse_address=True)
            await site.start()
        except Exception:
            await runner.cleanup()
            raise
        self._runner = runner
        self._site = site
        logger.info("SquadLens embedded Web server listening on http://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Stop aiohttp runner and release the TCP port cleanly on plugin reload/disable."""
        if self._runner is None:
            return
        runner = self._runner
        self._runner = None
        self._site = None
        await runner.cleanup()
        logger.info("SquadLens embedded Web server stopped (port %d released)", self.port)
