"""Unit tests for SquadStorage SQLite cache and LRU replay eviction."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.core.models import MatchSummary
from src.core.storage import SquadStorage


def _dummy_summary(match_id: str, created_at: str) -> MatchSummary:
    return MatchSummary(
        match_id=match_id,
        platform="steam",
        map_name="Baltic_Main",
        map_display_name="艾伦格 (Erangel)",
        map_image="static/maps/erangel.png",
        game_mode="squad-fpp",
        game_mode_display="四排 FPP",
        created_at=created_at,
        duration=1200,
        telemetry_url="https://cdn.example/t.json",
        squad_win_place=1,
        squad_total_kills=10,
        squad_total_damage=1200.0,
        is_chicken_dinner=True,
    )


@pytest.mark.asyncio
async def test_storage_caches_players_matches_and_evicts_lru_replays(tmp_path: Path) -> None:
    storage = SquadStorage(data_dir=tmp_path, max_cached_replays=2)
    await storage.initialize()
    try:
        # Player IGN -> account_id cache
        await storage.upsert_player("steam", "AlphaIGN", "acc.1")
        assert await storage.get_player_account_id("steam", "alphaign") == "acc.1"

        # Match summary cache
        m1 = _dummy_summary("m-1", "2026-11-25T08:00:00Z")
        m2 = _dummy_summary("m-2", "2026-11-25T09:00:00Z")
        await storage.save_match(m1, ["acc.1"])
        await storage.save_match(m2, ["acc.1"])

        recent = await storage.get_recent_matches_for_accounts(["acc.1"], limit=5)
        assert [m.match_id for m in recent] == ["m-2", "m-1"]

        # Save 2 replays, access m-1 so m-2 becomes oldest LRU, then save m-3 -> m-2 must be evicted
        await storage.save_replay_gzip("m-1", b"gz-1")
        await asyncio.sleep(0.02)
        await storage.save_replay_gzip("m-2", b"gz-2")
        await asyncio.sleep(0.02)
        assert await storage.get_replay_gzip("m-1") == b"gz-1"
        await asyncio.sleep(0.02)
        await storage.save_replay_gzip("m-3", b"gz-3")

        assert await storage.has_replay("m-1") is True
        assert await storage.has_replay("m-3") is True
        assert await storage.has_replay("m-2") is False
        assert not (tmp_path / "replays" / "m-2.replay.json.gz").exists()

        stats = await storage.get_cache_stats()
        assert stats["cached_replays"] == 2
        assert stats["max_cached_replays"] == 2

        # KV watermark state
        await storage.set_state("last_broadcast_match_id", "m-2")
        assert await storage.get_state("last_broadcast_match_id") == "m-2"
    finally:
        await storage.close()
