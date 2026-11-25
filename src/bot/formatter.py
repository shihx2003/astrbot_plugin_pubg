"""QQ chat message formatters for PUBG match summaries, highlights, and replay links.

Note: Avoids sentence-ending punctuation (。？！?!) in plain text so AstrBot's
optional reply-segmentation feature never fragments a single战报 into multiple bubbles.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..core.models import MatchSummary, SquadMember


def _format_duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    mins, secs = divmod(total, 60)
    return f"{mins}分{secs:02d}秒"


def _format_beijing_time(iso_str: str) -> str:
    if not iso_str:
        return "未知时间"
    text = iso_str.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text).astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%m-%d %H:%M")
    except ValueError:
        return iso_str


def build_replay_url(web_public_url: str, match_id: str) -> str:
    """Build clickable 2D replay deep link WITHOUT '?' so reply splitters won't cut it."""
    base = (web_public_url or "").strip().rstrip("/")
    if not base or not match_id:
        return ""
    return f"{base}/match/{match_id}"


def format_match_detail(
    summary: MatchSummary,
    *,
    web_public_url: str = "",
    enable_web: bool = False,
    title_prefix: str = "【PUBG 对局战报】",
) -> str:
    """Format a rich QQ group match report with squad stats, highlights, and replay link."""
    badge = "🍗 大吉大利，今晚吃鸡" if summary.is_chicken_dinner else f"🏁 最终排名 #{summary.squad_win_place or '-'}"
    lines = [
        f"{title_prefix} {badge}",
        f"🗺️ 地图：{summary.map_display_name} | 模式：{summary.game_mode_display}",
        f"🕒 开局：{_format_beijing_time(summary.created_at)} | 时长：{_format_duration(summary.duration)}",
        f"🔥 小队合计：{summary.squad_total_kills} 击杀 | {summary.squad_total_damage:.0f} 伤害",
        "────────────────────",
    ]

    for p in summary.squad_players:
        slot_prefix = f"[{p.squad_slot}号位] " if p.squad_slot else ""
        status_tag = "存活" if p.death_type == "alive" else f"第{p.death_order}个成盒"
        tk_note = ""
        if p.team_kills > 0 or p.team_damage > 0:
            tk_note = f" | ⚠️友伤:{p.team_damage:.0f}(TK:{p.team_kills})"
        lines.append(
            f"• {slot_prefix}{p.display_name} ({status_tag})\n"
            f"  击杀:{p.kills} 助攻:{p.assists} 击倒:{p.dbnos} 伤害:{p.damage_dealt:.0f} "
            f"爆头:{p.headshots} 救人:{p.revives} 存活:{_format_duration(p.time_survived)}{tk_note}"
        )

    hl = summary.highlights
    hl_parts: list[str] = []
    if hl.top_damage_player and hl.top_damage > 0:
        hl_parts.append(f"💥 全场伤害王：{hl.top_damage_player} ({hl.top_damage:.0f})")
    if hl.fastest_death_player and not summary.is_chicken_dinner:
        hl_parts.append(f"📦 成盒最快奖：{hl.fastest_death_player} ({_format_duration(hl.fastest_death_sec)})")
    if hl.team_damage_events:
        hl_parts.append(f"💣 友伤事件：{len(hl.team_damage_events)} 次（友伤警告）")

    if hl_parts:
        lines.append("────────────────────")
        lines.extend(hl_parts)

    replay_url = build_replay_url(web_public_url, summary.match_id) if enable_web else ""
    if replay_url:
        lines.append("────────────────────")
        lines.append(f"🗺️ 点击查看本局 2D 战术回放：\n{replay_url}")

    return "\n".join(lines)


def format_recent_matches(
    matches: list[MatchSummary],
    *,
    header: str = "📋 小队近期对局记录",
    web_public_url: str = "",
    enable_web: bool = False,
) -> str:
    """Format a compact list of recent matches."""
    if not matches:
        return "暂未查询到近期 PUBG 对局记录，请确认 API Key 与游戏 ID 配置正确"

    lines = [header, "────────────────────"]
    for idx, m in enumerate(matches, start=1):
        chicken = "🍗吃鸡" if m.is_chicken_dinner else f"#{m.squad_win_place or '-'}"
        lines.append(
            f"{idx}. [{chicken}] {m.map_display_name} ({m.game_mode_display})\n"
            f"   {_format_beijing_time(m.created_at)} | 击杀:{m.squad_total_kills} | 伤害:{m.squad_total_damage:.0f} | 时长:{_format_duration(m.duration)}"
        )

    if enable_web and (web_public_url or "").strip():
        lines.append("────────────────────")
        lines.append(f"🌐 网页看板与 2D 回放：{(web_public_url or '').strip().rstrip('/')}")

    lines.append("💡 发送 /pubg last 查看最新一局详细战报")
    return "\n".join(lines)


def format_squad_roster(members: list[SquadMember]) -> str:
    if not members:
        return "⚠️ 尚未配置固定小队成员，请在 AstrBot WebUI 插件配置中的 squad_members 添加好友游戏 ID"
    lines = ["👥 当前绑定 PUBG 固定小队（局内按游戏实际号位配色）：", "────────────────────"]
    for idx, m in enumerate(members, start=1):
        aid_status = "已缓存ID" if m.account_id else "待同步"
        color_note = m.color if m.color else "跟随游戏内号位色"
        lines.append(f"{idx}. {m.display_name} (IGN: {m.ign} | {m.platform} | {color_note} | {aid_status})")
    return "\n".join(lines)


def format_web_status(enable_web: bool, host: str, port: int, public_url: str, access_code: str) -> str:
    if not enable_web:
        return "🌐 内嵌 Web 战绩看板当前已关闭（enable_web=false），可在 AstrBot 插件配置中开启后重载"
    lines = [
        "🌐 SquadLens 内嵌 Web 服务运行中",
        f"• 本机监听：http://{host}:{port}",
        f"• 公网入口：{public_url.rstrip('/') if public_url else '未配置 web_public_url'}",
        f"• 访问口令：{'已启用保护' if access_code else '未设置（开放访问）'}",
    ]
    return "\n".join(lines)


def format_help_message() -> str:
    return (
        "🎮 SquadLens PUBG 战绩助手指令说明\n"
        "────────────────────\n"
        "• /pubg last — 查看小队最新一局详细战报与高光\n"
        "• /pubg recent [数量] — 查看小队近期对局列表\n"
        "• /pubg player <游戏ID> [平台] — 临时查询任意玩家近期战绩\n"
        "• /pubg squad — 查看当前绑定的固定小队名单\n"
        "• /pubg bind — 绑定当前群聊接收对局结束自动播报\n"
        "• /pubg unbind — 取消当前群聊的自动播报绑定\n"
        "• /pubg web — 查看内嵌 Web 战绩看板与 2D 回放状态\n"
        "• 直接 @机器人 提问近期吃鸡记录或队员伤害对比即可触发 AI 复盘点评"
    )
