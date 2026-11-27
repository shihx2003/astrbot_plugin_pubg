"""Pure domain layer for SquadLens (AstrBot-independent, testable via pytest)."""

from .maps import (
    MAP_CATALOG,
    MapInfo,
    get_map_info,
    is_standard_br_match,
    pubg_cm_to_norm,
    resolve_damage_type,
    resolve_game_mode,
    resolve_weapon_name,
)
from .models import (
    MatchHighlights,
    MatchSummary,
    PlayerMatchStats,
    SquadMember,
)
from .pubg_client import AsyncRateLimiter, PubgApiClient, PubgApiError
from .service import SquadLensService
from .storage import SquadStorage
from .telemetry import build_replay_sync, parse_match_response, parse_telemetry_to_replay_gzip

__all__ = [
    "AsyncRateLimiter",
    "MAP_CATALOG",
    "MapInfo",
    "MatchHighlights",
    "MatchSummary",
    "PlayerMatchStats",
    "PubgApiClient",
    "PubgApiError",
    "SquadLensService",
    "SquadMember",
    "SquadStorage",
    "build_replay_sync",
    "get_map_info",
    "is_standard_br_match",
    "parse_match_response",
    "parse_telemetry_to_replay_gzip",
    "pubg_cm_to_norm",
    "resolve_damage_type",
    "resolve_game_mode",
    "resolve_weapon_name",
]
