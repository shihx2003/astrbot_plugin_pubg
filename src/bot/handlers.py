"""Auto-broadcast coordinator and persistent broadcast target session store."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from astrbot.api import logger

from ..core.maps import is_standard_br_match
from ..core.models import MatchSummary
from ..core.service import SquadLensService
from ..core.storage import SquadStorage
from .formatter import format_match_detail

BOUND_SESSIONS_KEY = "bound_broadcast_sessions"


class BroadcastTargetStore:
    """Manages combined configured + dynamically bound QQ broadcast sessions."""

    def __init__(self, storage: SquadStorage, configured_targets: list[str] | None = None) -> None:
        self._storage = storage
        self._configured = [str(t).strip() for t in (configured_targets or []) if str(t).strip()]

    async def get_dynamic_targets(self) -> list[str]:
        raw = await self._storage.get_state(BOUND_SESSIONS_KEY)
        if not raw:
            return []
        try:
            data = json.loads(raw)
            return [str(x).strip() for x in data if str(x).strip()] if isinstance(data, list) else []
        except ValueError:
            return []

    async def get_all_targets(self) -> list[str]:
        dynamic = await self.get_dynamic_targets()
        merged: list[str] = []
        seen: set[str] = set()
        for item in [*self._configured, *dynamic]:
            if item not in seen:
                seen.add(item)
                merged.append(item)
        return merged

    async def bind_session(self, umo: str) -> bool:
        target = (umo or "").strip()
        if not target:
            return False
        current = await self.get_dynamic_targets()
        if target in current:
            return False
        current.append(target)
        await self._storage.set_state(BOUND_SESSIONS_KEY, json.dumps(current, ensure_ascii=False))
        return True

    async def unbind_session(self, umo: str) -> bool:
        target = (umo or "").strip()
        current = await self.get_dynamic_targets()
        if target not in current:
            return False
        current = [x for x in current if x != target]
        await self._storage.set_state(BOUND_SESSIONS_KEY, json.dumps(current, ensure_ascii=False))
        return True


class AutoBroadcastCoordinator:
    """Background async poller that broadcasts newly finished squad matches to QQ groups."""

    def __init__(
        self,
        service: SquadLensService,
        target_store: BroadcastTargetStore,
        send_text_fn: Callable[[str, str], Awaitable[None]],
        *,
        poll_interval_seconds: int = 180,
        enable_web: bool = False,
        web_public_url: str = "",
        broadcast_only_chicken: bool = True,
    ) -> None:
        self._service = service
        self._target_store = target_store
        self._send_text = send_text_fn
        self._interval = max(60, int(poll_interval_seconds))
        self._enable_web = enable_web
        self._web_public_url = web_public_url
        self._broadcast_only_chicken = bool(broadcast_only_chicken)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._poll_loop(), name="squadlens_auto_broadcast")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _poll_loop(self) -> None:
        # Initial slight delay so AstrBot finishes loading all platform adapters
        await asyncio.sleep(15)
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("SquadLens auto-broadcast poll error: %s", exc)
            await asyncio.sleep(self._interval)

    async def poll_once(self) -> MatchSummary | None:
        targets = await self._target_store.get_all_targets()
        if not targets:
            return None
        new_matches = await self._service.check_new_squad_matches()
        if not new_matches:
            return None

        last_sent: MatchSummary | None = None
        for match in new_matches:
            if not is_standard_br_match(match.map_name, match.game_mode):
                logger.info(
                    "Skipping non-BR match %s (%s / %s) from auto-broadcast.",
                    match.match_id,
                    match.map_name,
                    match.game_mode,
                )
                continue
            if self._broadcast_only_chicken and not match.is_chicken_dinner:
                logger.info(
                    "Skipping non-chicken match %s (rank=#%d) due to broadcast_only_chicken=True.",
                    match.match_id,
                    match.squad_win_place,
                )
                continue

            prefix = "【PUBG 吃鸡喜报】" if match.is_chicken_dinner else "【PUBG 刚打完一局】"
            text = format_match_detail(
                match,
                web_public_url=self._web_public_url,
                enable_web=self._enable_web,
                title_prefix=prefix,
            )
            for umo in targets:
                try:
                    await self._send_text(umo, text)
                except Exception as exc:
                    logger.warning("Failed to broadcast match %s to %s: %s", match.match_id, umo, exc)
            last_sent = match
        return last_sent
