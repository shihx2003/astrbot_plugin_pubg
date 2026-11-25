"""Read-only REST JSON API and static asset routes for SquadLens Web UI."""

from __future__ import annotations

import asyncio
import datetime
import gzip
import hmac
import io
import json
import mimetypes
import zipfile
from pathlib import Path
from typing import Any

from aiohttp import web
from astrbot.api import logger

from ..core.pubg_client import PubgApiError
from ..core.service import SquadLensService

STATIC_DIR = Path(__file__).resolve().parent / "static"
ACCESS_COOKIE_NAME = "squadlens_access_code"


def get_client_ip(request: web.Request) -> str:
    """Resolve real visitor IP behind FRP / Nginx reverse proxy."""
    real_ip = request.headers.get("X-Real-IP", "").strip()
    if real_ip:
        return real_ip
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return request.remote or "unknown"


def _extract_request_code(request: web.Request) -> str:
    header_code = request.headers.get("X-Access-Code", "").strip()
    if header_code:
        return header_code
    cookie_code = request.cookies.get(ACCESS_COOKIE_NAME, "").strip()
    if cookie_code:
        return cookie_code
    return request.query.get("code", "").strip()


def is_authorized(request: web.Request, expected_code: str) -> bool:
    if not expected_code:
        return True
    provided = _extract_request_code(request)
    if not provided:
        return False
    return hmac.compare_digest(provided, expected_code)


def _gzip_json_response(
    request: web.Request,
    payload: Any,
    *,
    status: int = 200,
    cache_control: str = "no-store",
) -> web.Response:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {"Cache-Control": cache_control}
    accept_enc = request.headers.get("Accept-Encoding", "")
    if len(raw) >= 1024 and "gzip" in accept_enc.lower():
        compressed = gzip.compress(raw, compresslevel=5)
        headers["Content-Encoding"] = "gzip"
        headers["Vary"] = "Accept-Encoding"
        return web.Response(
            body=compressed,
            status=status,
            content_type="application/json",
            charset="utf-8",
            headers=headers,
        )
    return web.Response(
        body=raw,
        status=status,
        content_type="application/json",
        charset="utf-8",
        headers=headers,
    )


def _sanitize_summary(summary_dict: dict[str, Any], *, is_replay_cached: bool | None = None) -> dict[str, Any]:
    clean = dict(summary_dict)
    has_telemetry = bool(clean.pop("telemetry_url", ""))
    clean["has_telemetry"] = has_telemetry
    if is_replay_cached is not None:
        clean["is_replay_cached"] = bool(is_replay_cached)
    return clean


def _safe_slug(text: str, fallback: str = "match") -> str:
    slug = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in (text or ""))
    slug = slug.strip("_")
    return slug[:32] or fallback


def _build_cache_export_zip(
    summaries: list[dict[str, Any]],
    replays: list[dict[str, Any]],
) -> bytes:
    """Build a self-contained ZIP archive of cached match summaries and replay files."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(
            "matches_summary.json",
            json.dumps(
                {
                    "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "matches_count": len(summaries),
                    "replays_count": len(replays),
                    "matches": summaries,
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
        zf.writestr(
            "README.txt",
            (
                "SquadLens 本地战绩与战术回放备份包\n"
                "====================================\n"
                "- matches_summary.json : 已缓存对局的战绩摘要数据\n"
                "- replays/*.replay.json.gz : 已缓存的 Gzip 压缩 2D/3D 战术回放数据包（解压后为标准 JSON）\n"
            ).encode("utf-8"),
        )
        for item in replays:
            fpath: Path = item["file_path"]
            if fpath.is_file():
                zf.write(
                    fpath,
                    arcname=f"replays/{item['file_name']}",
                    compress_type=zipfile.ZIP_STORED,
                )
    return buf.getvalue()


def register_routes(
    app: web.Application,
    service: SquadLensService,
    *,
    access_code: str = "",
    enable_3d_replay: bool = True,
) -> None:
    """Mount all static, 3D asset, and REST JSON routes onto the aiohttp Application."""
    assets_3d_dir = (service.storage.data_dir / "assets_3d").resolve()
    if enable_3d_replay:
        (assets_3d_dir / "erangel").mkdir(parents=True, exist_ok=True)

    async def index_handler(request: web.Request) -> web.StreamResponse:
        index_file = STATIC_DIR / "index.html"
        if not index_file.is_file():
            return web.Response(text="SquadLens static frontend missing.", status=404)
        return web.FileResponse(index_file, headers={"Cache-Control": "no-store, must-revalidate"})

    # In-memory gzip cache for compressible static assets (.glb, .js, .css) keyed by (path, mtime_ns, size)
    static_gzip_cache: dict[tuple[str, int, int], bytes] = {}
    compressible_exts = {".glb", ".js", ".css", ".json", ".svg"}
    long_cache_exts = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg", ".glb"}

    async def _serve_optimized_static(request: web.Request, target: Path) -> web.StreamResponse:
        # 1. Transparent AVIF content negotiation for .webp map images when client supports image/avif
        actual = target
        vary_headers: list[str] = []
        if target.suffix.lower() == ".webp":
            avif_candidate = target.with_suffix(".avif")
            if avif_candidate.is_file():
                vary_headers.append("Accept")
                accept_hdr = request.headers.get("Accept", "").lower()
                if "image/avif" in accept_hdr:
                    actual = avif_candidate

        st = actual.stat()
        etag = f'W/"{st.st_size:x}-{st.st_mtime_ns:x}"'

        ext = actual.suffix.lower()
        is_vendor = "vendor" in actual.parts
        if ext in long_cache_exts or is_vendor:
            cache_hdr = "public, max-age=2592000"
        else:
            cache_hdr = "no-store, must-revalidate"

        # 2. Conditional ETag 304 Not Modified (zero bytes over FRP on repeat visits!)
        inm = request.headers.get("If-None-Match", "")
        if etag in inm:
            hdrs = {"ETag": etag, "Cache-Control": cache_hdr}
            if vary_headers:
                hdrs["Vary"] = ", ".join(vary_headers)
            return web.Response(status=304, headers=hdrs)

        # 3. Gzip compression for compressible binary/text assets (.glb, .js, .css, .svg)
        accept_enc = request.headers.get("Accept-Encoding", "").lower()
        if ext in compressible_exts and st.st_size >= 1024 and "gzip" in accept_enc:
            vary_headers.append("Accept-Encoding")
            cache_key = (str(actual), st.st_mtime_ns, st.st_size)
            gz_body = static_gzip_cache.get(cache_key)
            if gz_body is None:
                raw_bytes = await asyncio.to_thread(actual.read_bytes)
                gz_body = await asyncio.to_thread(gzip.compress, raw_bytes, 6)
                if len(static_gzip_cache) > 48:
                    static_gzip_cache.clear()
                static_gzip_cache[cache_key] = gz_body
            ctype, _ = mimetypes.guess_type(str(actual))
            if ext == ".glb":
                ctype = "model/gltf-binary"
            elif ext == ".js":
                ctype = "application/javascript"
            elif ext == ".css":
                ctype = "text/css"
            elif ext == ".svg":
                ctype = "image/svg+xml"
            hdrs = {
                "Cache-Control": cache_hdr,
                "ETag": etag,
                "Content-Encoding": "gzip",
                "Vary": ", ".join(dict.fromkeys(vary_headers)),
            }
            return web.Response(body=gz_body, status=200, content_type=ctype or "application/octet-stream", headers=hdrs)

        # 4. Direct zero-copy FileResponse for pre-compressed image formats (AVIF/WebP/PNG)
        hdrs = {"Cache-Control": cache_hdr, "ETag": etag}
        if vary_headers:
            hdrs["Vary"] = ", ".join(dict.fromkeys(vary_headers))
        resp = web.FileResponse(actual, headers=hdrs)
        if ext == ".avif":
            resp.content_type = "image/avif"
        elif ext == ".webp":
            resp.content_type = "image/webp"
        elif ext == ".glb":
            resp.content_type = "model/gltf-binary"
        return resp

    async def static_handler(request: web.Request) -> web.StreamResponse:
        rel_path = request.match_info.get("path", "").lstrip("/")
        target = (STATIC_DIR / rel_path).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return web.Response(text="Forbidden", status=403)
        if not target.is_file():
            return web.Response(text="Not Found", status=404)
        return await _serve_optimized_static(request, target)

    async def health_handler(request: web.Request) -> web.Response:
        return _gzip_json_response(
            request,
            {"ok": True, "service": "SquadLens", "version": "0.5.7"},
        )

    async def auth_status_handler(request: web.Request) -> web.Response:
        return _gzip_json_response(
            request,
            {
                "auth_required": bool(access_code),
                "authenticated": is_authorized(request, access_code),
            },
        )

    async def auth_verify_handler(request: web.Request) -> web.Response:
        if not access_code:
            return _gzip_json_response(request, {"ok": True, "authenticated": True})
        try:
            body = await request.json()
        except Exception:
            body = {}
        code = str(body.get("code") or "").strip() if isinstance(body, dict) else ""
        if not hmac.compare_digest(code, access_code):
            logger.warning("Failed web access code attempt from %s", get_client_ip(request))
            return _gzip_json_response(
                request,
                {"ok": False, "error": "访问口令错误"},
                status=401,
            )
        resp = _gzip_json_response(request, {"ok": True, "authenticated": True})
        resp.set_cookie(
            ACCESS_COOKIE_NAME,
            access_code,
            max_age=86400 * 30,
            httponly=True,
            samesite="Lax",
        )
        return resp

    async def assets_3d_handler(request: web.Request) -> web.StreamResponse:
        if not enable_3d_replay:
            return web.Response(text="3D Replay Disabled", status=404)
        rel_path = request.match_info.get("path", "").lstrip("/")
        target = (assets_3d_dir / rel_path).resolve()
        try:
            target.relative_to(assets_3d_dir)
        except ValueError:
            return web.Response(text="Forbidden", status=403)
        if not target.is_file():
            return web.Response(text="Not Found", status=404)
        return web.FileResponse(target, headers={"Cache-Control": "public, max-age=604800"})

    async def config_3d_handler(request: web.Request) -> web.Response:
        if not enable_3d_replay:
            return _gzip_json_response(request, {"enabled": False})
        erangel_dir = assets_3d_dir / "erangel"
        custom_hm = erangel_dir / "heightmap.png"
        custom_glb = erangel_dir / "buildings.glb"
        static_hm = STATIC_DIR / "maps" / "height" / "erangel.png"
        static_glb = STATIC_DIR / "maps" / "3d" / "erangel_buildings.glb"
        heightmap_url = (
            "./assets_3d/erangel/heightmap.png"
            if custom_hm.is_file()
            else ("./static/maps/height/erangel.png" if static_hm.is_file() else "")
        )
        buildings_url = (
            "./assets_3d/erangel/buildings.glb"
            if custom_glb.is_file()
            else ("./static/maps/3d/erangel_buildings.glb" if static_glb.is_file() else "")
        )
        return _gzip_json_response(
            request,
            {
                "enabled": True,
                "supported_maps": ["Baltic_Main", "Erangel_Main"],
                "erangel": {
                    "heightmap_url": heightmap_url,
                    "buildings_url": buildings_url,
                    "has_buildings": bool(buildings_url),
                },
            },
        )

    async def squad_handler(request: web.Request) -> web.Response:
        cache_stats = await service.storage.get_cache_stats()
        return _gzip_json_response(
            request,
            {
                "default_platform": service.default_platform,
                "enable_3d_replay": enable_3d_replay,
                "squad_members": [m.to_dict() for m in service.squad_members],
                "cache": cache_stats,
            },
        )

    async def matches_handler(request: web.Request) -> web.Response:
        try:
            limit = max(1, min(30, int(request.query.get("limit", str(service.recent_matches_limit)))))
        except ValueError:
            limit = service.recent_matches_limit
        refresh = request.query.get("refresh", "1").lower() not in ("0", "false", "no")
        try:
            matches = await service.get_squad_recent_matches(limit=limit, force_refresh=refresh)
        except PubgApiError as exc:
            return _gzip_json_response(request, {"error": str(exc)}, status=exc.status_code or 502)
        cached_ids = await service.storage.get_cached_replay_ids([m.match_id for m in matches])
        return _gzip_json_response(
            request,
            {
                "matches": [
                    _sanitize_summary(m.to_dict(), is_replay_cached=(m.match_id in cached_ids))
                    for m in matches
                ]
            },
        )

    async def match_detail_handler(request: web.Request) -> web.Response:
        match_id = request.match_info["match_id"].strip()
        platform = request.query.get("platform") or None
        try:
            summary = await service.get_match_detail(match_id, platform=platform)
        except PubgApiError as exc:
            return _gzip_json_response(request, {"error": str(exc)}, status=exc.status_code or 502)
        if summary is None:
            return _gzip_json_response(request, {"error": "未找到该对局记录"}, status=404)
        is_cached = await service.storage.has_replay(match_id)
        return _gzip_json_response(
            request,
            {"match": _sanitize_summary(summary.to_dict(), is_replay_cached=is_cached)},
            cache_control="public, max-age=300",
        )

    async def match_replay_handler(request: web.Request) -> web.Response:
        match_id = request.match_info["match_id"].strip()
        platform = request.query.get("platform") or None
        try:
            gz_bytes = await service.get_or_build_replay_gzip(match_id, platform=platform)
        except PubgApiError as exc:
            return _gzip_json_response(request, {"error": str(exc)}, status=exc.status_code or 502)

        accept_enc = request.headers.get("Accept-Encoding", "").lower()
        if "gzip" in accept_enc:
            return web.Response(
                body=gz_bytes,
                status=200,
                content_type="application/json",
                charset="utf-8",
                headers={
                    "Content-Encoding": "gzip",
                    "Vary": "Accept-Encoding",
                    "Cache-Control": "public, max-age=3600",
                },
            )
        plain_bytes = await asyncio.to_thread(gzip.decompress, gz_bytes)
        return web.Response(
            body=plain_bytes,
            status=200,
            content_type="application/json",
            charset="utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    async def match_download_handler(request: web.Request) -> web.Response:
        match_id = request.match_info["match_id"].strip()
        platform = request.query.get("platform") or None
        fmt = request.query.get("format", "json").strip().lower()
        try:
            gz_bytes = await service.get_or_build_replay_gzip(match_id, platform=platform)
        except PubgApiError as exc:
            return _gzip_json_response(request, {"error": str(exc)}, status=exc.status_code or 502)

        summary = await service.storage.get_match(match_id)
        map_slug = _safe_slug(summary.map_name if summary else "pubg", "pubg")
        short_id = _safe_slug(match_id[:8], "replay")

        if fmt in ("gz", "gzip"):
            fname = f"squadlens_{map_slug}_{short_id}.replay.json.gz"
            return web.Response(
                body=gz_bytes,
                status=200,
                content_type="application/gzip",
                headers={
                    "Content-Disposition": f'attachment; filename="{fname}"',
                    "Cache-Control": "no-store",
                },
            )

        plain_bytes = await asyncio.to_thread(gzip.decompress, gz_bytes)
        fname = f"squadlens_{map_slug}_{short_id}.replay.json"
        return web.Response(
            body=plain_bytes,
            status=200,
            content_type="application/json",
            charset="utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "Cache-Control": "no-store",
            },
        )

    async def cache_clear_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            body = {}
        scope = str(body.get("scope") or "replays").strip().lower() if isinstance(body, dict) else "replays"
        if scope not in ("replays", "matches"):
            return _gzip_json_response(
                request,
                {"error": "无效的清理范围，仅支持 replays 或 matches"},
                status=400,
            )

        if scope == "matches":
            cleared = await service.storage.clear_matches_and_replays()
        else:
            cleared = await service.storage.clear_replays()

        logger.info("Web cache clear (%s) triggered by %s: %s", scope, get_client_ip(request), cleared)
        cache_stats = await service.storage.get_cache_stats()
        return _gzip_json_response(
            request,
            {
                "ok": True,
                "scope": scope,
                "cleared": cleared,
                "cache": cache_stats,
            },
        )

    async def cache_export_handler(request: web.Request) -> web.Response:
        cached_matches = await service.storage.list_all_cached_matches(limit=300)
        cached_replays = await service.storage.list_cached_replays()
        if not cached_matches and not cached_replays:
            return _gzip_json_response(
                request,
                {"error": "当前暂无已缓存的对局或回放数据可供打包下载"},
                status=404,
            )
        replay_ids = {r["match_id"] for r in cached_replays}
        summaries = [
            _sanitize_summary(m.to_dict(), is_replay_cached=(m.match_id in replay_ids))
            for m in cached_matches
        ]
        zip_bytes = await asyncio.to_thread(_build_cache_export_zip, summaries, cached_replays)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        fname = f"squadlens_backup_{stamp}.zip"
        return web.Response(
            body=zip_bytes,
            status=200,
            content_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "Cache-Control": "no-store",
            },
        )

    async def player_search_handler(request: web.Request) -> web.Response:
        ign = request.query.get("ign", "").strip()
        if not ign:
            return _gzip_json_response(request, {"error": "请提供玩家游戏 ID (ign)"}, status=400)
        platform = request.query.get("platform", "").strip() or service.default_platform
        try:
            limit = max(1, min(15, int(request.query.get("limit", "5"))))
        except ValueError:
            limit = 5
        try:
            player, matches = await service.get_player_recent_matches(ign, platform=platform, limit=limit)
        except PubgApiError as exc:
            return _gzip_json_response(request, {"error": str(exc)}, status=exc.status_code or 502)
        if not player:
            return _gzip_json_response(request, {"error": f"未找到玩家 {ign} ({platform})"}, status=404)
        cached_ids = await service.storage.get_cached_replay_ids([m.match_id for m in matches])
        return _gzip_json_response(
            request,
            {
                "player": player,
                "matches": [
                    _sanitize_summary(m.to_dict(), is_replay_cached=(m.match_id in cached_ids))
                    for m in matches
                ],
            },
        )

    async def match_redirect_handler(request: web.Request) -> web.StreamResponse:
        match_id = request.match_info["match_id"].strip()
        raise web.HTTPFound(location=f"../?match={match_id}")

    app.router.add_get("/", index_handler)
    app.router.add_get("/match/{match_id}", match_redirect_handler)
    app.router.add_get("/static/{path:.+}", static_handler)
    app.router.add_get("/assets_3d/{path:.+}", assets_3d_handler)
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/api/auth/status", auth_status_handler)
    app.router.add_post("/api/auth/verify", auth_verify_handler)
    app.router.add_get("/api/3d/config", config_3d_handler)
    app.router.add_get("/api/squad", squad_handler)
    app.router.add_get("/api/matches", matches_handler)
    app.router.add_get("/api/matches/{match_id}", match_detail_handler)
    app.router.add_get("/api/matches/{match_id}/replay", match_replay_handler)
    app.router.add_get("/api/matches/{match_id}/download", match_download_handler)
    app.router.add_post("/api/cache/clear", cache_clear_handler)
    app.router.add_get("/api/cache/export", cache_export_handler)
    app.router.add_get("/api/players/search", player_search_handler)
