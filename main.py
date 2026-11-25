"""SquadLens (`astrbot_plugin_pubg`) AstrBot plugin entry point."""

from __future__ import annotations

from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, StarTools

from .src.bot import (
    AutoBroadcastCoordinator,
    BroadcastTargetStore,
    PubgLlmToolkit,
    format_help_message,
    format_match_detail,
    format_recent_matches,
    format_squad_roster,
    format_web_status,
)
from .src.core import (
    PubgApiClient,
    PubgApiError,
    SquadLensService,
    SquadMember,
    SquadStorage,
)
from .src.web import SquadWebServer


class SquadLensPlugin(Star):
    """PUBG squad tracker, auto-broadcaster, AI review tool, and optional 2D replay Web server."""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self._storage: SquadStorage | None = None
        self._client: PubgApiClient | None = None
        self._service: SquadLensService | None = None
        self._target_store: BroadcastTargetStore | None = None
        self._broadcaster: AutoBroadcastCoordinator | None = None
        self._llm_toolkit: PubgLlmToolkit | None = None
        self._web_server: SquadWebServer | None = None

    def _cfg(self, key: str, default: Any = None) -> Any:
        try:
            val = self.config.get(key, default)
            return default if val is None else val
        except Exception:
            return default

    async def initialize(self) -> None:
        """Async lifecycle hook called by AstrBot when plugin is activated."""
        data_dir = StarTools.get_data_dir()
        api_key = str(self._cfg("pubg_api_key", "")).strip()
        default_platform = str(self._cfg("default_platform", "steam")).strip().lower() or "steam"
        raw_members = self._cfg("squad_members", [])
        squad_members = SquadMember.from_config_list(
            raw_members if isinstance(raw_members, list) else [],
            default_platform=default_platform,
        )
        max_cached_replays = int(self._cfg("max_cached_replays", 200))
        sample_interval_sec = float(self._cfg("telemetry_sample_interval_sec", 1.5))
        recent_matches_limit = int(self._cfg("recent_matches_limit", 10))

        self._storage = SquadStorage(data_dir=data_dir, max_cached_replays=max_cached_replays)
        await self._storage.initialize()

        self._client = PubgApiClient(api_key=api_key)
        self._service = SquadLensService(
            client=self._client,
            storage=self._storage,
            squad_members=squad_members,
            default_platform=default_platform,
            sample_interval_sec=sample_interval_sec,
            recent_matches_limit=recent_matches_limit,
        )

        enable_web = bool(self._cfg("enable_web", False))
        enable_3d_replay = bool(self._cfg("enable_3d_replay", True))
        web_host = str(self._cfg("web_host", "127.0.0.1")).strip() or "127.0.0.1"
        web_port = int(self._cfg("web_port", 8001))
        web_public_url = str(self._cfg("web_public_url", "")).strip()
        web_access_code = str(self._cfg("web_access_code", "")).strip()

        self._llm_toolkit = PubgLlmToolkit(
            self._service,
            enable_web=enable_web,
            web_public_url=web_public_url,
        )

        configured_targets = self._cfg("broadcast_targets", [])
        self._target_store = BroadcastTargetStore(
            self._storage,
            configured_targets=configured_targets if isinstance(configured_targets, list) else [],
        )

        if bool(self._cfg("enable_auto_broadcast", False)):
            poll_interval = int(self._cfg("poll_interval_seconds", 180))
            self._broadcaster = AutoBroadcastCoordinator(
                service=self._service,
                target_store=self._target_store,
                send_text_fn=self._send_proactive_text,
                poll_interval_seconds=poll_interval,
                enable_web=enable_web,
                web_public_url=web_public_url,
            )
            self._broadcaster.start()
            logger.info("SquadLens auto-broadcast task started (interval=%ds).", poll_interval)

        if enable_web:
            self._web_server = SquadWebServer(
                self._service,
                host=web_host,
                port=web_port,
                access_code=web_access_code,
                enable_3d_replay=enable_3d_replay,
            )
            try:
                await self._web_server.start()
                logger.info(
                    "SquadLens embedded Web server started on http://%s:%d (public=%s).",
                    web_host,
                    web_port,
                    web_public_url or "unset",
                )
            except OSError as exc:
                logger.error(
                    "SquadLens failed to start Web server on %s:%d: %s",
                    web_host,
                    web_port,
                    exc,
                )
                self._web_server = None

        logger.info(
            "SquadLens initialized (squad=%d, enable_web=%s, data_dir=%s).",
            len(squad_members),
            enable_web,
            data_dir,
        )

    async def terminate(self) -> None:
        """Hot-reload-safe cleanup: cancel background tasks, close sessions, and release Web port."""
        if self._broadcaster is not None:
            await self._broadcaster.stop()
            self._broadcaster = None

        if self._web_server is not None:
            await self._web_server.stop()
            self._web_server = None

        if self._client is not None:
            await self._client.close()
            self._client = None

        if self._storage is not None:
            await self._storage.close()
            self._storage = None

        logger.info("SquadLens terminated cleanly.")

    async def _send_proactive_text(self, session_umo: str, text: str) -> None:
        chain = MessageChain().message(text)
        await self.context.send_message(session_umo, chain)

    def _require_service(self) -> SquadLensService:
        if self._service is None:
            raise RuntimeError("SquadLens 尚未完成初始化，请稍后重试。")
        return self._service

    # ── QQ Group Commands ──────────────────────────────────────────────────────

    @filter.command_group("pubg")
    def pubg(self):
        """SquadLens PUBG 小队战绩与 2D 回放指令组"""

    @pubg.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """查看 SquadLens PUBG 插件帮助说明"""
        yield event.plain_result(format_help_message())

    @pubg.command("last")
    async def cmd_last(self, event: AstrMessageEvent):
        """查看绑定四人小队最新一局详细战报"""
        service = self._require_service()
        try:
            matches = await service.get_squad_recent_matches(limit=1, force_refresh=True)
        except PubgApiError as exc:
            yield event.plain_result(f"❌ 查询失败：{exc}")
            return

        if not matches:
            yield event.plain_result("⚠️ 暂未查到小队近期对局，请检查 API Key 与 squad_members 配置")
            return

        report = format_match_detail(
            matches[0],
            web_public_url=str(self._cfg("web_public_url", "")),
            enable_web=bool(self._cfg("enable_web", False)),
        )
        yield event.plain_result(report)

    @pubg.command("recent")
    async def cmd_recent(self, event: AstrMessageEvent, limit: int = 5):
        """查看小队近期对局记录列表"""
        service = self._require_service()
        safe_limit = max(1, min(15, int(limit or 5)))
        try:
            matches = await service.get_squad_recent_matches(limit=safe_limit, force_refresh=True)
        except PubgApiError as exc:
            yield event.plain_result(f"❌ 查询失败：{exc}")
            return

        text = format_recent_matches(
            matches,
            header=f"📋 小队最近 {len(matches)} 场对局记录",
            web_public_url=str(self._cfg("web_public_url", "")),
            enable_web=bool(self._cfg("enable_web", False)),
        )
        yield event.plain_result(text)

    @pubg.command("player")
    async def cmd_player(self, event: AstrMessageEvent, ign: str = "", platform: str = ""):
        """临时查询任意 PUBG 玩家的近期对局"""
        if not ign.strip():
            yield event.plain_result("用法：/pubg player <游戏ID> [平台，如 steam/kakao]")
            return
        service = self._require_service()
        try:
            player, matches = await service.get_player_recent_matches(
                ign.strip(),
                platform=platform.strip() or None,
                limit=5,
            )
        except PubgApiError as exc:
            yield event.plain_result(f"❌ 查询失败：{exc}")
            return

        if not player:
            yield event.plain_result(f"未找到玩家 {ign}")
            return

        text = format_recent_matches(
            matches,
            header=f"🔎 玩家 {player['name']} ({player['platform']}) 近期对局",
            web_public_url=str(self._cfg("web_public_url", "")),
            enable_web=bool(self._cfg("enable_web", False)),
        )
        yield event.plain_result(text)

    @pubg.command("squad")
    async def cmd_squad(self, event: AstrMessageEvent):
        """查看当前绑定的固定四人小队名单"""
        service = self._require_service()
        await service.sync_squad_accounts()
        yield event.plain_result(format_squad_roster(service.squad_members))

    @pubg.command("bind")
    async def cmd_bind(self, event: AstrMessageEvent):
        """绑定当前会话接收 PUBG 对局结束自动播报"""
        if self._target_store is None:
            yield event.plain_result("⚠️ 插件尚未初始化完成")
            return
        umo = event.unified_msg_origin
        added = await self._target_store.bind_session(umo)
        auto_on = bool(self._cfg("enable_auto_broadcast", False))
        tip = "" if auto_on else "\n💡 提示：当前 enable_auto_broadcast 尚未开启，请在插件配置中开启后生效"
        if added:
            yield event.plain_result(f"✅ 已绑定当前会话接收 PUBG 自动播报：\n{umo}{tip}")
        else:
            yield event.plain_result(f"ℹ️ 当前会话已在播报列表中：\n{umo}{tip}")

    @pubg.command("unbind")
    async def cmd_unbind(self, event: AstrMessageEvent):
        """取消当前会话的 PUBG 自动播报绑定"""
        if self._target_store is None:
            yield event.plain_result("⚠️ 插件尚未初始化完成")
            return
        removed = await self._target_store.unbind_session(event.unified_msg_origin)
        if removed:
            yield event.plain_result("✅ 已取消当前会话的 PUBG 自动播报绑定")
        else:
            yield event.plain_result("ℹ️ 当前会话未在动态绑定列表中（若在配置文件 broadcast_targets 中手动填写，请于 WebUI 移除）")

    @pubg.command("web")
    async def cmd_web(self, event: AstrMessageEvent):
        """查看内嵌 Web 战绩看板与 2D 回放服务状态"""
        yield event.plain_result(
            format_web_status(
                enable_web=bool(self._cfg("enable_web", False)),
                host=str(self._cfg("web_host", "127.0.0.1")),
                port=int(self._cfg("web_port", 8001)),
                public_url=str(self._cfg("web_public_url", "")),
                access_code=str(self._cfg("web_access_code", "")),
            )
        )

    # ── LLM Tools (AI Review & Banter) ─────────────────────────────────────────

    @filter.llm_tool(name="pubg_get_recent_matches")
    async def tool_get_recent_matches(
        self,
        event: AstrMessageEvent,
        limit: int = 5,
        ign: str = "",
    ) -> str:
        """获取固定开黑小队或指定玩家的 PUBG 近期对局战绩列表，用于回答吃鸡次数、排名、击杀、伤害等复盘问题。

        Args:
            limit(number): 需要返回的近期对局场次数量，默认 5，范围 1 到 15。
            ign(string): 可选的 PUBG 游戏 ID；留空时查询已绑定的默认四人小队战绩。
        """
        if self._llm_toolkit is None:
            return "SquadLens 尚未初始化。"
        try:
            return await self._llm_toolkit.query_recent_matches_for_llm(limit=limit, ign=ign)
        except PubgApiError as exc:
            return f"查询 PUBG 战绩失败：{exc}"

    @filter.llm_tool(name="pubg_get_match_detail")
    async def tool_get_match_detail(
        self,
        event: AstrMessageEvent,
        match_id: str = "",
    ) -> str:
        """获取单场 PUBG 对局的详细战报数据，包括四名队员的击杀、助攻、击倒、伤害、爆头、救援、成盒顺序、友伤高光与关键交火事件。

        Args:
            match_id(string): 可选的对局 UUID；留空时默认查询小队最近刚打完的那一局。
        """
        if self._llm_toolkit is None:
            return "SquadLens 尚未初始化。"
        try:
            return await self._llm_toolkit.query_match_detail_for_llm(match_id=match_id)
        except PubgApiError as exc:
            return f"查询单局详情失败：{exc}"
