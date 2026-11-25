"""Structured JSON/text data helpers powering AstrBot LLM Tool Calling."""

from __future__ import annotations

import json

from ..core.service import SquadLensService
from .formatter import build_replay_url


class PubgLlmToolkit:
    """Formats real squad & player PUBG stats into concise LLM-friendly context."""

    def __init__(
        self,
        service: SquadLensService,
        *,
        enable_web: bool = False,
        web_public_url: str = "",
    ) -> None:
        self._service = service
        self._enable_web = enable_web
        self._web_public_url = web_public_url

    async def query_recent_matches_for_llm(self, limit: int = 5, ign: str = "") -> str:
        safe_limit = max(1, min(15, int(limit or 5)))
        if ign and ign.strip():
            player, matches = await self._service.get_player_recent_matches(ign.strip(), limit=safe_limit)
            if not player:
                return f"未找到游戏 ID 为 {ign} 的 PUBG 玩家。"
        else:
            matches = await self._service.get_squad_recent_matches(limit=safe_limit, force_refresh=True)

        if not matches:
            return "未查询到近期 PUBG 对局数据（可能未配置 API Key 或尚未绑定小队成员）。"

        payload = []
        for m in matches:
            entry = m.to_dict()
            entry.pop("telemetry_url", None)
            if self._enable_web and self._web_public_url:
                entry["replay_url"] = build_replay_url(self._web_public_url, m.match_id)
            payload.append(entry)
        return json.dumps(payload, ensure_ascii=False)

    async def query_match_detail_for_llm(self, match_id: str = "") -> str:
        target_id = (match_id or "").strip()
        if not target_id:
            recent = await self._service.get_squad_recent_matches(limit=1, force_refresh=True)
            if not recent:
                return "未查询到任何小队对局记录。"
            summary = recent[0]
        else:
            summary = await self._service.get_match_detail(target_id)
            if summary is None:
                return f"未找到对局 {target_id}。"

        data = summary.to_dict()
        data.pop("telemetry_url", None)
        if self._enable_web and self._web_public_url:
            data["replay_url"] = build_replay_url(self._web_public_url, summary.match_id)

        # Include compact kill/knock/team-damage highlights if replay already cached
        if await self._service.storage.has_replay(summary.match_id):
            replay = await self._service.get_or_build_replay_dict(summary.match_id)
            squad_ids = {p.account_id for p in summary.squad_players if p.account_id}
            squad_events = [
                ev
                for ev in replay.get("events", [])
                if ev.get("attacker_id") in squad_ids or ev.get("victim_id") in squad_ids or ev.get("type") == "chicken"
            ]
            data["key_combat_events"] = squad_events[:30]

        return json.dumps(data, ensure_ascii=False)
