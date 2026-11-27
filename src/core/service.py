"""High-level domain service orchestrating PUBG API, SQLite cache, and Telemetry replays."""

from __future__ import annotations

import asyncio
import gzip
import json
from collections import defaultdict
from typing import Any

from astrbot.api import logger

from .maps import is_standard_br_match
from .models import MatchSummary, SquadMember, slot_color
from .pubg_client import PubgApiClient, PubgApiError
from .storage import SquadStorage
from .telemetry import REPLAY_SCHEMA_VERSION, parse_match_response, parse_telemetry_to_replay_gzip


class SquadLensService:
    """Core business service shared by AstrBot handlers/tools and the optional Web API."""

    def __init__(
        self,
        client: PubgApiClient,
        storage: SquadStorage,
        squad_members: list[SquadMember],
        *,
        default_platform: str = "steam",
        sample_interval_sec: float = 1.5,
        recent_matches_limit: int = 10,
        exclude_non_br_matches: bool = True,
    ) -> None:
        self.client = client
        self.storage = storage
        self.squad_members = squad_members
        self.default_platform = default_platform.lower() or "steam"
        self.sample_interval_sec = max(0.5, float(sample_interval_sec))
        self.recent_matches_limit = max(1, int(recent_matches_limit))
        self.exclude_non_br_matches = bool(exclude_non_br_matches)
        self._replay_locks: dict[str, asyncio.Lock] = {}

    def _get_replay_lock(self, match_id: str) -> asyncio.Lock:
        lock = self._replay_locks.get(match_id)
        if lock is None:
            lock = asyncio.Lock()
            self._replay_locks[match_id] = lock
        return lock

    async def sync_squad_accounts(self) -> list[SquadMember]:
        """Resolve and cache PUBG account_ids for all configured squad members."""
        missing_by_platform: dict[str, list[SquadMember]] = defaultdict(list)

        for member in self.squad_members:
            if member.account_id:
                continue
            cached = await self.storage.get_player_account_id(member.platform, member.ign)
            if cached:
                member.account_id = cached
            else:
                missing_by_platform[member.platform].append(member)

        for platform, missing_members in missing_by_platform.items():
            names = [m.ign for m in missing_members]
            try:
                resolved = await self.client.fetch_players_by_names(platform, names)
            except PubgApiError as exc:
                logger.warning("Failed to resolve squad players on %s: %s", platform, exc)
                continue
            by_lower = {p["name"].lower(): p for p in resolved if p.get("name")}
            for member in missing_members:
                info = by_lower.get(member.ign.lower())
                if info and info.get("account_id"):
                    member.account_id = str(info["account_id"])
                    await self.storage.upsert_player(platform, member.ign, member.account_id)

        return self.squad_members

    async def _get_or_fetch_match(self, platform: str, match_id: str) -> MatchSummary | None:
        cached = await self.storage.get_match(match_id)
        if cached is not None:
            # Refresh alias/color metadata in case user edited squad config
            return self._apply_squad_labels(cached)
        try:
            raw = await self.client.fetch_match(platform, match_id)
        except PubgApiError as exc:
            logger.warning("Failed to fetch match %s (%s): %s", match_id, platform, exc)
            return None
        summary, all_aids = parse_match_response(raw, platform, self.squad_members)
        await self.storage.save_match(summary, all_aids)
        return summary

    def _apply_squad_labels(self, summary: MatchSummary) -> MatchSummary:
        by_aid = {m.account_id: m for m in self.squad_members if m.account_id}
        by_ign = {m.ign.lower(): m for m in self.squad_members if m.ign}
        for idx, sp in enumerate(summary.squad_players):
            member = by_aid.get(sp.account_id) or by_ign.get(sp.name.lower())
            if member:
                sp.alias = member.alias
                sp.color = member.color.strip() or slot_color(sp.squad_slot, idx)
                sp.is_squad_member = True
        return summary

    async def get_squad_recent_matches(
        self,
        limit: int | None = None,
        *,
        force_refresh: bool = True,
    ) -> list[MatchSummary]:
        """Fetch recent matches played by the configured fixed squad."""
        target_limit = max(1, int(limit or self.recent_matches_limit))
        await self.sync_squad_accounts()
        active_members = [m for m in self.squad_members if m.account_id]
        if not active_members:
            return []

        if force_refresh:
            by_platform: dict[str, list[str]] = defaultdict(list)
            for m in active_members:
                by_platform[m.platform].append(m.account_id)

            candidate_matches: list[tuple[str, str]] = []
            seen: set[str] = set()
            for platform, aids in by_platform.items():
                try:
                    players_data = await self.client.fetch_players_by_ids(platform, aids)
                except PubgApiError as exc:
                    logger.warning("Failed to refresh squad player matches on %s: %s", platform, exc)
                    continue
                for p in players_data:
                    for mid in p.get("match_ids", [])[: target_limit]:
                        if mid not in seen:
                            seen.add(mid)
                            candidate_matches.append((platform, mid))

            # Fetch uncached match details (capped to avoid excessive burst)
            for platform, mid in candidate_matches[: target_limit * 2]:
                await self._get_or_fetch_match(platform, mid)

        account_ids = [m.account_id for m in active_members]
        fetch_limit = max(target_limit * 3, target_limit + 20) if self.exclude_non_br_matches else target_limit
        matches = await self.storage.get_recent_matches_for_accounts(account_ids, limit=fetch_limit)
        if self.exclude_non_br_matches:
            matches = [m for m in matches if is_standard_br_match(m.map_name, m.game_mode)]
        return [self._apply_squad_labels(m) for m in matches[:target_limit]]

    async def get_player_recent_matches(
        self,
        ign: str,
        platform: str | None = None,
        limit: int = 5,
    ) -> tuple[dict[str, Any] | None, list[MatchSummary]]:
        """Dynamically query recent matches for ANY PUBG player IGN."""
        plat = (platform or self.default_platform).lower()
        clean_ign = ign.strip()
        if not clean_ign:
            return None, []

        resolved = await self.client.fetch_players_by_names(plat, [clean_ign])
        if not resolved:
            return None, []

        player_info = resolved[0]
        aid = str(player_info.get("account_id") or "")
        name = str(player_info.get("name") or clean_ign)
        if aid:
            await self.storage.upsert_player(plat, name, aid)

        temp_member = SquadMember(ign=name, platform=plat, alias="", color="#1890FF", account_id=aid)
        merged_members = list(self.squad_members)
        if not any(m.ign.lower() == name.lower() for m in merged_members):
            merged_members.append(temp_member)

        target_limit = max(1, int(limit))
        scan_cap = min(20, max(target_limit * 2, target_limit + 5)) if self.exclude_non_br_matches else target_limit
        summaries: list[MatchSummary] = []
        for mid in player_info.get("match_ids", [])[:scan_cap]:
            cached = await self.storage.get_match(mid)
            if cached is not None and any(p.account_id == aid for p in cached.squad_players):
                if not self.exclude_non_br_matches or is_standard_br_match(cached.map_name, cached.game_mode):
                    summaries.append(cached)
                    if len(summaries) >= target_limit:
                        break
                continue
            try:
                raw = await self.client.fetch_match(plat, mid)
            except PubgApiError as exc:
                logger.warning("Skipping match %s for player %s: %s", mid, name, exc)
                continue
            summary, all_aids = parse_match_response(raw, plat, merged_members)
            await self.storage.save_match(summary, all_aids)
            if not self.exclude_non_br_matches or is_standard_br_match(summary.map_name, summary.game_mode):
                summaries.append(summary)
                if len(summaries) >= target_limit:
                    break

        summaries.sort(key=lambda m: m.created_at, reverse=True)
        return {"account_id": aid, "name": name, "platform": plat}, summaries[:target_limit]

    async def get_match_detail(self, match_id: str, platform: str | None = None) -> MatchSummary | None:
        """Retrieve a single match summary from SQLite cache or PUBG API."""
        cached = await self.storage.get_match(match_id)
        if cached is not None:
            return self._apply_squad_labels(cached)
        return await self._get_or_fetch_match(platform or self.default_platform, match_id)

    @staticmethod
    def _is_replay_current_version(gz_bytes: bytes) -> bool:
        try:
            data = json.loads(gzip.decompress(gz_bytes).decode("utf-8"))
            return int(data.get("version") or 0) >= REPLAY_SCHEMA_VERSION
        except Exception:
            return False

    async def get_or_build_replay_gzip(self, match_id: str, platform: str | None = None) -> bytes:
        """Return gzip-compressed compact 2D replay bytes (auto-upgrades v1 cache to v2)."""
        cached_gz = await self.storage.get_replay_gzip(match_id)
        if cached_gz is not None and self._is_replay_current_version(cached_gz):
            return cached_gz

        lock = self._get_replay_lock(match_id)
        async with lock:
            cached_gz = await self.storage.get_replay_gzip(match_id)
            if cached_gz is not None and self._is_replay_current_version(cached_gz):
                return cached_gz

            summary = await self.get_match_detail(match_id, platform=platform)
            if summary is None:
                if cached_gz is not None:
                    return cached_gz
                raise PubgApiError(f"找不到对局 {match_id}", 404)
            if not summary.telemetry_url:
                if cached_gz is not None:
                    return cached_gz
                raise PubgApiError(f"对局 {match_id} 不包含官方 Telemetry 回放数据。", 404)

            try:
                raw_bytes = await self.client.download_telemetry_bytes(summary.telemetry_url)
                replay_dict, gz_bytes = await parse_telemetry_to_replay_gzip(
                    raw_bytes,
                    summary,
                    self.squad_members,
                    sample_interval_sec=self.sample_interval_sec,
                )
                del raw_bytes
            except Exception as exc:
                if cached_gz is not None:
                    logger.warning(
                        "Failed to upgrade cached replay %s to v%d (%s); serving existing cached archive.",
                        match_id,
                        REPLAY_SCHEMA_VERSION,
                        exc,
                    )
                    return cached_gz
                raise
            await self.storage.save_replay_gzip(match_id, gz_bytes)

            # Enrich match summary with telemetry team-damage metrics if present
            team_dmg_events = [e for e in replay_dict.get("events", []) if e.get("type") == "team_damage"]
            if team_dmg_events:
                players_map = replay_dict.get("players", {})
                for sp in summary.squad_players:
                    p_entry = players_map.get(sp.account_id)
                    if p_entry:
                        sp.team_damage = float(p_entry.get("team_damage") or 0.0)
                summary.highlights.team_damage_events = team_dmg_events[:10]
                await self.storage.save_match(summary)

            return gz_bytes

    async def get_or_build_replay_dict(self, match_id: str, platform: str | None = None) -> dict[str, Any]:
        """Return decoded replay dictionary (useful for LLM tools or tests)."""
        gz_bytes = await self.get_or_build_replay_gzip(match_id, platform=platform)
        return json.loads(gzip.decompress(gz_bytes).decode("utf-8"))

    async def check_new_squad_matches(self) -> list[MatchSummary]:
        """Poll for all newly finished standard-BR squad matches since the last stored watermark.

        Returns matches ordered oldest-to-newest so no chicken dinner is missed when
        multiple matches finish within a single polling interval. On first execution,
        records the latest match ID as watermark and returns [] to avoid spamming old games.
        """
        matches = await self.get_squad_recent_matches(limit=6, force_refresh=True)
        matches = [m for m in matches if is_standard_br_match(m.map_name, m.game_mode)]
        if not matches:
            return []
        latest = matches[0]
        last_seen = await self.storage.get_state("last_broadcast_match_id")
        if not last_seen:
            await self.storage.set_state("last_broadcast_match_id", latest.match_id)
            logger.info("Initialized auto-broadcast watermark to match %s", latest.match_id)
            return []
        if latest.match_id == last_seen:
            return []

        new_matches: list[MatchSummary] = []
        for m in matches:
            if m.match_id == last_seen:
                break
            new_matches.append(m)
        await self.storage.set_state("last_broadcast_match_id", latest.match_id)
        new_matches.reverse()
        return new_matches

    async def check_new_squad_match(self) -> MatchSummary | None:
        """Backward-compatible helper returning the latest newly finished squad match."""
        new_matches = await self.check_new_squad_matches()
        return new_matches[-1] if new_matches else None
