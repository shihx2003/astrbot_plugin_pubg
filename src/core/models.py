"""Core data models for SquadLens."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# PUBG official in-game 1-4 squad slot colors: 1=Yellow, 2=Orange, 3=Blue, 4=Green
PUBG_SLOT_COLORS: dict[int, str] = {
    1: "#F5C518",
    2: "#FF8C1A",
    3: "#2EA8FF",
    4: "#3CD070",
}

DEFAULT_SQUAD_COLORS: tuple[str, ...] = (
    "#F5C518",
    "#FF8C1A",
    "#2EA8FF",
    "#3CD070",
    "#C084FC",
    "#2DD4BF",
)


def slot_color(slot: int, fallback_idx: int = 0) -> str:
    """Return PUBG official in-game slot color for slot 1..4."""
    if slot in PUBG_SLOT_COLORS:
        return PUBG_SLOT_COLORS[slot]
    return DEFAULT_SQUAD_COLORS[fallback_idx % len(DEFAULT_SQUAD_COLORS)]


@dataclass(slots=True)
class SquadMember:
    """Configured fixed squad member (slot number is NOT hardcoded; resolved per match)."""

    ign: str
    platform: str = "steam"
    alias: str = ""
    color: str = ""  # Empty = auto-assign PUBG in-game slot color per match
    account_id: str = ""

    @property
    def display_name(self) -> str:
        return self.alias.strip() or self.ign

    def to_dict(self) -> dict[str, Any]:
        return {
            "ign": self.ign,
            "platform": self.platform,
            "alias": self.alias,
            "display_name": self.display_name,
            "color": self.color,
            "account_id": self.account_id,
        }

    @classmethod
    def from_config_list(
        cls,
        raw_items: list[dict[str, Any]] | None,
        default_platform: str = "steam",
    ) -> list[SquadMember]:
        """Parse AstrBot template_list config entries, ignoring empty IGN slots."""
        members: list[SquadMember] = []
        if not raw_items:
            return members
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            ign = str(item.get("ign") or "").strip()
            if not ign:
                continue
            platform = str(item.get("platform") or default_platform).strip().lower() or default_platform
            alias = str(item.get("alias") or "").strip()
            color = str(item.get("color") or "").strip()
            account_id = str(item.get("account_id") or "").strip()
            members.append(
                cls(
                    ign=ign,
                    platform=platform,
                    alias=alias,
                    color=color,
                    account_id=account_id,
                )
            )
        return members


@dataclass(slots=True)
class PlayerMatchStats:
    """Per-player match statistics."""

    account_id: str
    name: str
    team_id: int = 0
    squad_slot: int = 0  # 1..4 derived from in-game roster/LogMatchStart order
    kills: int = 0
    assists: int = 0
    dbnos: int = 0
    damage_dealt: float = 0.0
    headshots: int = 0
    revives: int = 0
    time_survived: float = 0.0
    win_place: int = 0
    kill_place: int = 0
    death_type: str = "alive"
    longest_kill: float = 0.0
    walk_distance: float = 0.0
    ride_distance: float = 0.0
    swim_distance: float = 0.0
    team_kills: int = 0
    team_damage: float = 0.0
    death_order: int = 0
    alias: str = ""
    color: str = ""
    is_squad_member: bool = False

    @property
    def display_name(self) -> str:
        return self.alias.strip() or self.name

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["display_name"] = self.display_name
        data["damage_dealt"] = round(float(self.damage_dealt), 1)
        data["time_survived"] = round(float(self.time_survived), 1)
        data["longest_kill"] = round(float(self.longest_kill), 1)
        data["team_damage"] = round(float(self.team_damage), 1)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerMatchStats:
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)


@dataclass(slots=True)
class MatchHighlights:
    """Computed notable highlights for a squad match."""

    fastest_death_player: str = ""
    fastest_death_sec: float = 0.0
    top_damage_player: str = ""
    top_damage: float = 0.0
    top_kills_player: str = ""
    top_kills: int = 0
    team_damage_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MatchHighlights:
        if not data:
            return cls()
        return cls(
            fastest_death_player=str(data.get("fastest_death_player", "")),
            fastest_death_sec=float(data.get("fastest_death_sec", 0.0)),
            top_damage_player=str(data.get("top_damage_player", "")),
            top_damage=float(data.get("top_damage", 0.0)),
            top_kills_player=str(data.get("top_kills_player", "")),
            top_kills=int(data.get("top_kills", 0)),
            team_damage_events=list(data.get("team_damage_events", [])),
        )


@dataclass(slots=True)
class MatchSummary:
    """Normalized summary for a completed PUBG match."""

    match_id: str
    platform: str
    map_name: str
    map_display_name: str
    map_image: str
    game_mode: str
    game_mode_display: str
    created_at: str
    duration: int
    telemetry_url: str
    terrain_image: str = ""
    squad_win_place: int = 0
    squad_total_kills: int = 0
    squad_total_damage: float = 0.0
    is_chicken_dinner: bool = False
    all_players_count: int = 0
    squad_players: list[PlayerMatchStats] = field(default_factory=list)
    highlights: MatchHighlights = field(default_factory=MatchHighlights)

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "platform": self.platform,
            "map_name": self.map_name,
            "map_display_name": self.map_display_name,
            "map_image": self.map_image,
            "terrain_image": self.terrain_image,
            "game_mode": self.game_mode,
            "game_mode_display": self.game_mode_display,
            "created_at": self.created_at,
            "duration": self.duration,
            "telemetry_url": self.telemetry_url,
            "squad_win_place": self.squad_win_place,
            "squad_total_kills": self.squad_total_kills,
            "squad_total_damage": round(float(self.squad_total_damage), 1),
            "is_chicken_dinner": self.is_chicken_dinner,
            "all_players_count": self.all_players_count,
            "squad_players": [p.to_dict() for p in self.squad_players],
            "highlights": self.highlights.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchSummary:
        return cls(
            match_id=str(data["match_id"]),
            platform=str(data.get("platform", "steam")),
            map_name=str(data.get("map_name", "")),
            map_display_name=str(data.get("map_display_name", "")),
            map_image=str(data.get("map_image", "static/maps/erangel.webp")),
            terrain_image=str(data.get("terrain_image", "")),
            game_mode=str(data.get("game_mode", "")),
            game_mode_display=str(data.get("game_mode_display", "")),
            created_at=str(data.get("created_at", "")),
            duration=int(data.get("duration", 0)),
            telemetry_url=str(data.get("telemetry_url", "")),
            squad_win_place=int(data.get("squad_win_place", 0)),
            squad_total_kills=int(data.get("squad_total_kills", 0)),
            squad_total_damage=float(data.get("squad_total_damage", 0.0)),
            is_chicken_dinner=bool(data.get("is_chicken_dinner", False)),
            all_players_count=int(data.get("all_players_count", 0)),
            squad_players=[
                PlayerMatchStats.from_dict(p) for p in data.get("squad_players", [])
            ],
            highlights=MatchHighlights.from_dict(data.get("highlights")),
        )
