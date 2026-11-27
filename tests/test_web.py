"""Unit tests for optional Web API, access code auth, gzip replay, and hot-reload port release."""

from __future__ import annotations

import gzip
import io
import json
import socket
import zipfile
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.core.models import MatchSummary, SquadMember
from src.core.pubg_client import PubgApiClient
from src.core.service import SquadLensService
from src.core.storage import SquadStorage
from src.core.telemetry import REPLAY_SCHEMA_VERSION
from src.web.server import SquadWebServer, create_web_app


async def _build_service(tmp_path: Path) -> SquadLensService:
    storage = SquadStorage(data_dir=tmp_path, max_cached_replays=10)
    await storage.initialize()
    squad = [
        SquadMember(ign="Alpha", alias="队长", platform="steam", color="#FF4D4F", account_id="acc.1"),
    ]
    summary = MatchSummary(
        match_id="match-web-1",
        platform="steam",
        map_name="Baltic_Main",
        map_display_name="艾伦格 (Erangel)",
        map_image="static/maps/erangel.png",
        game_mode="squad-fpp",
        game_mode_display="四排 FPP",
        created_at="2026-11-25T10:00:00Z",
        duration=1200,
        telemetry_url="https://secret-cdn.example/telemetry.json",
        squad_win_place=1,
        squad_total_kills=8,
        squad_total_damage=950.0,
        is_chicken_dinner=True,
    )
    await storage.save_match(summary, ["acc.1"])
    replay_payload = {
        "version": REPLAY_SCHEMA_VERSION,
        "match_id": "match-web-1",
        "map_image": "static/maps/erangel.webp",
        "terrain_image": "static/maps/terrain/erangel.webp",
        "heightmap_image": "static/maps/height/erangel.png",
        "duration": 1200,
        "teams": {"7": {"team_id": 7, "color": "#06B6D4", "is_squad": True, "members": ["acc.1"]}},
        "players": {"acc.1": {"account_id": "acc.1", "name": "Alpha", "is_squad": True, "squad_slot": 1, "color": "#F5C518"}},
        "tracks": {"acc.1": [[10.0, 400000.0, 400000.0, 100, 0, 1500.0]]},
        "zones": [],
        "events": [],
    }
    gz = gzip.compress(json.dumps(replay_payload).encode("utf-8"))
    await storage.save_replay_gzip("match-web-1", gz)

    client = PubgApiClient(api_key="SUPER_SECRET_PUBG_API_KEY")
    return SquadLensService(client=client, storage=storage, squad_members=squad)


@pytest.mark.asyncio
async def test_web_auth_isolation_and_gzip_replay(tmp_path: Path) -> None:
    service = await _build_service(tmp_path)
    try:
        app = create_web_app(service, access_code="my-secret-code")
        async with TestClient(TestServer(app)) as tc:
            # Public health & auth status endpoints work without token
            r_health = await tc.get("/api/health")
            assert r_health.status == 200
            r_status = await tc.get("/api/auth/status")
            status_data = await r_status.json()
            assert status_data == {"auth_required": True, "authenticated": False}

            # Protected endpoints reject missing/wrong code
            r_unauth = await tc.get("/api/squad")
            assert r_unauth.status == 401

            r_bad = await tc.post("/api/auth/verify", json={"code": "wrong"})
            assert r_bad.status == 401

            # Verify valid code sets cookie and unlocks endpoints
            r_ok = await tc.post(
                "/api/auth/verify",
                json={"code": "my-secret-code"},
                headers={"X-Real-IP": "203.0.113.10"},
            )
            assert r_ok.status == 200

            r_squad = await tc.get("/api/squad")
            assert r_squad.status == 200
            squad_text = await r_squad.text()
            # Iron rule: NEVER leak pubg_api_key or access_code
            assert "SUPER_SECRET_PUBG_API_KEY" not in squad_text
            assert "my-secret-code" not in squad_text

            r_matches = await tc.get("/api/matches?refresh=0")
            assert r_matches.status == 200
            matches_data = await r_matches.json()
            assert len(matches_data["matches"]) == 1
            assert "telemetry_url" not in matches_data["matches"][0]
            assert matches_data["matches"][0]["has_telemetry"] is True

            # Replay endpoint returns valid JSON (and supports gzip Accept-Encoding)
            r_replay = await tc.get(
                "/api/matches/match-web-1/replay",
                headers={"Accept-Encoding": "gzip"},
            )
            assert r_replay.status == 200
            assert r_replay.headers.get("Content-Encoding") == "gzip"
            replay_json = await r_replay.json()
            assert replay_json["match_id"] == "match-web-1"

            # Clean /match/{id} redirect route without '?' for QQ segmentation safety
            r_redir = await tc.get("/match/match-web-1", allow_redirects=False)
            assert r_redir.status == 302
            assert r_redir.headers.get("Location") == "../?match=match-web-1"

            # Static HTML uses strictly relative paths and dynamically injects PLUGIN_VERSION from metadata.yaml
            from src.web.routes import PLUGIN_VERSION

            r_health_data = await r_health.json()
            assert r_health_data["version"] == PLUGIN_VERSION

            r_index = await tc.get("/")
            assert r_index.status == 200
            html = await r_index.text()
            assert f"./static/css/style.css?v={PLUGIN_VERSION}" in html
            assert f"./static/js/ui-mobile.js?v={PLUGIN_VERSION}" in html
            assert "__SQUADLENS_VER__" not in html
            assert "127.0.0.1" not in html
    finally:
        await service.client.close()
        await service.storage.close()


@pytest.mark.asyncio
async def test_web_server_hot_reload_releases_port_cleanly(tmp_path: Path) -> None:
    service = await _build_service(tmp_path)
    # Grab a free ephemeral port on 127.0.0.1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    try:
        server1 = SquadWebServer(service, host="127.0.0.1", port=port)
        await server1.start()
        assert server1.is_running
        await server1.stop()
        assert not server1.is_running

        # Immediately re-binding the exact same port simulates AstrBot hot reload
        server2 = SquadWebServer(service, host="127.0.0.1", port=port)
        await server2.start()
        assert server2.is_running
        await server2.stop()
    finally:
        await service.client.close()
        await service.storage.close()


@pytest.mark.asyncio
async def test_3d_replay_console_toggle_and_buildings_glb_detection(tmp_path: Path) -> None:
    service = await _build_service(tmp_path)
    try:
        # 1. When enable_3d_replay=False, 3D config reports disabled and /assets_3d returns 404
        app_off = create_web_app(service, enable_3d_replay=False)
        async with TestClient(TestServer(app_off)) as tc:
            r_cfg = await tc.get("/api/3d/config")
            assert r_cfg.status == 200
            assert await r_cfg.json() == {"enabled": False}
            r_asset = await tc.get("/assets_3d/erangel/buildings.glb")
            assert r_asset.status == 404

        # 2. When enable_3d_replay=True, serves built-in 1024px heightmap + built-in buildings GLB,
        #    and allows overriding with custom <data_dir>/assets_3d/erangel/buildings.glb
        app_on = create_web_app(service, enable_3d_replay=True)
        async with TestClient(TestServer(app_on)) as tc:
            r_cfg_default = await tc.get("/api/3d/config")
            cfg_default = await r_cfg_default.json()
            assert cfg_default["enabled"] is True
            assert cfg_default["erangel"]["has_buildings"] is True
            assert cfg_default["erangel"]["buildings_url"] == "./static/maps/3d/erangel_buildings.glb"
            assert cfg_default["erangel"]["heightmap_url"] == "./static/maps/height/erangel.png"

            r_builtin_glb = await tc.get("/static/maps/3d/erangel_buildings.glb")
            assert r_builtin_glb.status == 200
            assert (await r_builtin_glb.read())[:4] == b"glTF"

            # Placing a custom override takes precedence
            glb_file = tmp_path / "assets_3d" / "erangel" / "buildings.glb"
            glb_file.write_bytes(b"glTF-dummy-bytes")
            r_cfg = await tc.get("/api/3d/config")
            cfg = await r_cfg.json()
            assert cfg["enabled"] is True
            assert cfg["erangel"]["has_buildings"] is True
            assert cfg["erangel"]["buildings_url"] == "./assets_3d/erangel/buildings.glb"
            assert cfg["erangel"]["heightmap_url"] == "./static/maps/height/erangel.png"

            r_glb = await tc.get("/assets_3d/erangel/buildings.glb")
            assert r_glb.status == 200
            assert await r_glb.read() == b"glTF-dummy-bytes"
    finally:
        await service.client.close()
        await service.storage.close()


@pytest.mark.asyncio
async def test_web_download_export_and_cache_clear(tmp_path: Path) -> None:
    service = await _build_service(tmp_path)
    try:
        await service.storage.upsert_player("steam", "Alpha", "acc.1")
        await service.storage.set_state("last_broadcast_match_id", "match-web-1")

        app = create_web_app(service, access_code="cache-secret")
        async with TestClient(TestServer(app)) as tc:
            # All cache/download endpoints require auth when access_code is set
            assert (await tc.post("/api/cache/clear", json={"scope": "replays"})).status == 401
            assert (await tc.get("/api/cache/export")).status == 401
            assert (await tc.get("/api/matches/match-web-1/download")).status == 401

            # Authenticate
            assert (await tc.post("/api/auth/verify", json={"code": "cache-secret"})).status == 200

            # /api/matches reports is_replay_cached=True
            r_m = await tc.get("/api/matches?refresh=0")
            assert r_m.status == 200
            m_data = await r_m.json()
            assert m_data["matches"][0]["is_replay_cached"] is True

            # Single replay download (.json and .gz)
            r_dl = await tc.get("/api/matches/match-web-1/download")
            assert r_dl.status == 200
            assert "attachment;" in (r_dl.headers.get("Content-Disposition") or "")
            assert ".replay.json" in (r_dl.headers.get("Content-Disposition") or "")
            dl_json = await r_dl.json()
            assert dl_json["match_id"] == "match-web-1"

            r_dl_gz = await tc.get("/api/matches/match-web-1/download?format=gz")
            assert r_dl_gz.status == 200
            assert ".replay.json.gz" in (r_dl_gz.headers.get("Content-Disposition") or "")
            assert json.loads(gzip.decompress(await r_dl_gz.read()))["match_id"] == "match-web-1"

            # Bulk ZIP export includes summary + replay and strips telemetry_url / secrets
            r_zip = await tc.get("/api/cache/export")
            assert r_zip.status == 200
            assert r_zip.content_type == "application/zip"
            zip_bytes = await r_zip.read()
            with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
                names = zf.namelist()
                assert "matches_summary.json" in names
                assert "README.txt" in names
                assert "replays/match-web-1.replay.json.gz" in names
                summary_txt = zf.read("matches_summary.json").decode("utf-8")
                assert "SUPER_SECRET_PUBG_API_KEY" not in summary_txt
                assert "secret-cdn.example" not in summary_txt

            # Clear replays only: preserves matches, players, and bot watermark
            r_clr_r = await tc.post("/api/cache/clear", json={"scope": "replays"})
            assert r_clr_r.status == 200
            clr_r = await r_clr_r.json()
            assert clr_r["ok"] is True
            assert clr_r["cleared"]["removed_replays"] == 1
            assert clr_r["cache"]["cached_replays"] == 0
            assert clr_r["cache"]["cached_matches"] == 1
            assert await service.storage.get_state("last_broadcast_match_id") == "match-web-1"
            assert await service.storage.get_player_account_id("steam", "Alpha") == "acc.1"

            # Invalid scope returns 400
            assert (await tc.post("/api/cache/clear", json={"scope": "invalid"})).status == 400

            # Clear matches & replays: empties matches too, STILL preserves players & watermark
            r_clr_all = await tc.post("/api/cache/clear", json={"scope": "matches"})
            assert r_clr_all.status == 200
            clr_all = await r_clr_all.json()
            assert clr_all["cleared"]["removed_matches"] == 1
            assert clr_all["cache"]["cached_matches"] == 0
            assert await service.storage.get_state("last_broadcast_match_id") == "match-web-1"
            assert await service.storage.get_player_account_id("steam", "Alpha") == "acc.1"

            # Export returns 404 when cache is empty
            assert (await tc.get("/api/cache/export")).status == 404

            # Static asset network optimizations: AVIF content negotiation, ETag 304, and Gzip GLB
            r_avif = await tc.get(
                "/static/maps/erangel.webp",
                headers={"Accept": "image/avif,image/webp,*/*"},
            )
            assert r_avif.status == 200
            assert r_avif.content_type == "image/avif"
            assert "Accept" in (r_avif.headers.get("Vary") or "")
            etag = r_avif.headers.get("ETag")
            assert etag

            r_304 = await tc.get(
                "/static/maps/erangel.webp",
                headers={"Accept": "image/avif,image/webp,*/*", "If-None-Match": etag},
            )
            assert r_304.status == 304

            r_webp = await tc.get(
                "/static/maps/erangel.webp",
                headers={"Accept": "image/webp,*/*"},
            )
            assert r_webp.status == 200
            assert r_webp.content_type == "image/webp"

            r_glb_gz = await tc.get(
                "/static/maps/3d/erangel_buildings.glb",
                headers={"Accept-Encoding": "gzip"},
            )
            assert r_glb_gz.status == 200
            assert r_glb_gz.headers.get("Content-Encoding") == "gzip"
            glb_bytes = await r_glb_gz.read()
            assert glb_bytes[:4] == b"glTF"
    finally:
        await service.client.close()
        await service.storage.close()
