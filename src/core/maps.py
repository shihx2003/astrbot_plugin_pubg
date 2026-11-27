"""PUBG official map dimensions, coordinate normalization, team colors, and item/weapon dictionaries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# Curated 32-color high-contrast PUBG esports team badge palette
TEAM_COLOR_PALETTE: tuple[str, ...] = (
    "#06B6D4", "#3B82F6", "#8B5CF6", "#EC4899", "#F43F5E", "#10B981",
    "#14B8A6", "#6366F1", "#D946EF", "#F97316", "#84CC16", "#0EA5E9",
    "#E11D48", "#22C55E", "#A855F7", "#EAB308", "#FB7185", "#38BDF8",
    "#4ADE80", "#C084FC", "#FDBA74", "#2DD4BF", "#818CF8", "#F472B6",
    "#34D399", "#60A5FA", "#FACC15", "#FB923C", "#A3E635", "#67E8F9",
    "#E879F9", "#FCA5A5",
)


def get_team_color(team_id: int) -> str:
    """Deterministic distinct badge color for any PUBG team_id."""
    tid = abs(int(team_id or 0))
    return TEAM_COLOR_PALETTE[tid % len(TEAM_COLOR_PALETTE)]


@dataclass(frozen=True, slots=True)
class MapInfo:
    """Metadata and telemetry dimensions for a PUBG map (default + terrain + 3D heightmap)."""

    map_id: str
    en_name: str
    zh_name: str
    size_cm: float
    image_file: str  # Relative static path for FRP / Nginx subpath safety
    terrain_file: str = ""  # Optional HD topographic terrain layer
    heightmap_file: str = ""  # Optional 3D elevation heightmap

    @property
    def display_name(self) -> str:
        return f"{self.zh_name} ({self.en_name})"


# Official PUBG Telemetry coordinate space sizes in centimeters:
# 8x8 km = 816000 cm, 4x4 km = 408000 cm, 3x3 km = 306000 cm, 2x2 km = 204000 cm, 1x1 km = 102000 cm
MAP_CATALOG: dict[str, MapInfo] = {
    "Baltic_Main": MapInfo("Baltic_Main", "Erangel", "艾伦格", 816000.0, "static/maps/erangel.webp", "static/maps/terrain/erangel.webp", "static/maps/height/erangel.png"),
    "Erangel_Main": MapInfo("Erangel_Main", "Erangel", "艾伦格", 816000.0, "static/maps/erangel.webp", "static/maps/terrain/erangel.webp", "static/maps/height/erangel.png"),
    "Desert_Main": MapInfo("Desert_Main", "Miramar", "米拉玛", 816000.0, "static/maps/miramar.webp", "static/maps/terrain/miramar.webp"),
    "Savage_Main": MapInfo("Savage_Main", "Sanhok", "萨诺", 408000.0, "static/maps/sanhok.webp", "static/maps/terrain/sanhok.webp"),
    "DihorOtok_Main": MapInfo("DihorOtok_Main", "Vikendi", "维寒迪", 816000.0, "static/maps/vikendi.webp", "static/maps/terrain/vikendi.webp"),
    "Tiger_Main": MapInfo("Tiger_Main", "Taego", "泰戈", 816000.0, "static/maps/taego.webp", "static/maps/terrain/taego.webp"),
    "Kiki_Main": MapInfo("Kiki_Main", "Deston", "帝斯顿", 816000.0, "static/maps/deston.webp", "static/maps/terrain/deston.webp"),
    "Neon_Main": MapInfo("Neon_Main", "Rondo", "荣都", 816000.0, "static/maps/rondo.webp", "static/maps/terrain/rondo.webp"),
    "Chimera_Main": MapInfo("Chimera_Main", "Paramo", "帕拉莫", 306000.0, "static/maps/paramo.webp", "static/maps/terrain/paramo.webp"),
    "Summerland_Main": MapInfo("Summerland_Main", "Karakin", "卡拉金", 204000.0, "static/maps/karakin.webp", "static/maps/terrain/karakin.webp"),
    "Heaven_Main": MapInfo("Heaven_Main", "Haven", "褐湾", 102000.0, "static/maps/haven.png", ""),
    "Range_Main": MapInfo("Range_Main", "Camp Jackal", "训练场", 204000.0, "static/maps/camp_jackal.png", ""),
}

_GAME_MODE_ZH: dict[str, str] = {
    "squad": "四排 TPP",
    "squad-fpp": "四排 FPP",
    "duo": "双排 TPP",
    "duo-fpp": "双排 FPP",
    "solo": "单排 TPP",
    "solo-fpp": "单排 FPP",
    "normal-squad": "普通四排 TPP",
    "normal-squad-fpp": "普通四排 FPP",
    "normal-duo": "普通双排 TPP",
    "normal-duo-fpp": "普通双排 FPP",
    "normal-solo": "普通单排 TPP",
    "normal-solo-fpp": "普通单排 FPP",
    "esports-squad": "竞技四排 TPP",
    "esports-squad-fpp": "竞技四排 FPP",
    "tdm": "团队淘汰赛",
}

# Standard Battle Royale game modes (excludes training, binaryspot, tdm, ibr, bluebomb, lab, war, zombie, etc.)
STANDARD_BR_MODES: frozenset[str] = frozenset(
    {
        "solo",
        "solo-fpp",
        "duo",
        "duo-fpp",
        "squad",
        "squad-fpp",
        "normal-solo",
        "normal-solo-fpp",
        "normal-duo",
        "normal-duo-fpp",
        "normal-squad",
        "normal-squad-fpp",
        "esports-solo",
        "esports-solo-fpp",
        "esports-duo",
        "esports-duo-fpp",
        "esports-squad",
        "esports-squad-fpp",
    }
)

_NON_BR_MAP_TOKENS: tuple[str, ...] = (
    "range_main",
    "binaryspot",
    "tutorial",
    "training",
    "arcade",
    "tdm",
    "boardwalk",
    "pillar",
    "arena",
)


def is_standard_br_match(map_name: str, game_mode: str) -> bool:
    """Return True only for standard Battle Royale matches (excludes Training, BinarySpot, TDM, Arcade, etc.)."""
    mode = (game_mode or "").strip().lower()
    if mode not in STANDARD_BR_MODES:
        return False
    m_low = (map_name or "").strip().lower()
    if not m_low:
        return False
    return not any(tok in m_low for tok in _NON_BR_MAP_TOKENS)

# Friendly Chinese attachment labels matching PUBG CN UI
_ATTACHMENT_ZH_RULES: tuple[tuple[str, str], ...] = (
    ("Scope15x", "15倍瞄准镜"),
    ("PM2_01", "15倍瞄准镜"),
    ("CQBSS", "8倍瞄准镜"),
    ("Scope6x", "6倍瞄准镜"),
    ("ACOG", "4倍瞄准镜"),
    ("Scope3x", "3倍瞄准镜"),
    ("Aimpoint", "2倍瞄准镜"),
    ("DotSight_01", "红点瞄准镜"),
    ("Holosight", "全息瞄准镜"),
    ("Thermal", "热成像瞄准镜"),
    ("SideRail_DotSight", "侧面瞄具"),
    ("Suppressor", "消音器"),
    ("Compensator", "枪口补偿器"),
    ("FlashHider", "消焰器"),
    ("MuzzleBrake", "枪口制退器"),
    ("Choke", "收束器"),
    ("Duckbill", "鸭嘴枪口"),
    ("ExtendedQuickDraw", "快速扩容弹匣"),
    ("Extended_", "扩容弹匣"),
    ("QuickDraw", "快速弹匣"),
    ("AngledForeGrip", "直角前握把"),
    ("LightweightForeGrip", "轻型握把"),
    ("HalfGrip", "半截式握把"),
    ("ThumbGrip", "拇指握把"),
    ("LaserPointer", "激光瞄准器"),
    ("Foregrip", "垂直握把"),
    ("CheekPad", "托腮板"),
    ("BulletLoops", "子弹袋"),
    ("HeavyStock", "重型枪托"),
    ("Composite", "战术枪托"),
    ("UZI_C", "折叠枪托"),
)


@lru_cache(maxsize=8)
def _load_dict(filename: str) -> dict[str, str]:
    path = ASSETS_DIR / filename
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


def _resolve_existing_terrain(rel_path: str) -> str:
    if not rel_path:
        return ""
    candidate = STATIC_DIR / rel_path.removeprefix("static/")
    return rel_path if candidate.is_file() else ""


def get_map_info(map_id: str) -> MapInfo:
    """Resolve official PUBG map identifier to MapInfo with runtime terrain availability check."""
    base: MapInfo | None = MAP_CATALOG.get(map_id)
    if base is None:
        lower = (map_id or "").lower()
        for key, info in MAP_CATALOG.items():
            if key.lower() == lower or info.en_name.lower() == lower:
                base = info
                break
    if base is not None:
        return MapInfo(
            map_id=base.map_id,
            en_name=base.en_name,
            zh_name=base.zh_name,
            size_cm=base.size_cm,
            image_file=base.image_file,
            terrain_file=_resolve_existing_terrain(base.terrain_file),
            heightmap_file=_resolve_existing_terrain(base.heightmap_file),
        )
    official_names = _load_dict("mapName.json")
    en_name = official_names.get(map_id, map_id or "Unknown")
    return MapInfo(
        map_id=map_id or "Unknown",
        en_name=en_name,
        zh_name=en_name,
        size_cm=816000.0,
        image_file="static/maps/erangel.webp",
        terrain_file=_resolve_existing_terrain("static/maps/terrain/erangel.webp"),
        heightmap_file=_resolve_existing_terrain("static/maps/height/erangel.png"),
    )


def pubg_cm_to_norm(x_cm: float, y_cm: float, map_id: str) -> tuple[float, float]:
    """Convert PUBG Telemetry cm coordinates to normalized [0.0, 1.0] map space."""
    info = get_map_info(map_id)
    size = info.size_cm if info.size_cm > 0 else 816000.0
    nx = max(0.0, min(1.0, float(x_cm) / size))
    ny = max(0.0, min(1.0, float(y_cm) / size))
    return (nx, ny)


def resolve_weapon_name(causer_name: str) -> str:
    """Convert telemetry damageCauserName or Item_Weapon_* ID into readable weapon name."""
    if not causer_name:
        return "未知"
    dmg_map = _load_dict("damageCauserName.json")
    if causer_name in dmg_map:
        return dmg_map[causer_name]
    item_map = _load_dict("itemId.json")
    if causer_name in item_map:
        return item_map[causer_name]
    cleaned = causer_name
    for prefix in ("Item_Weapon_", "Weap", "BP_", "Proj"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    if cleaned.endswith("_C"):
        cleaned = cleaned[:-2]
    return cleaned or causer_name


def resolve_attachment_name(item_id: str) -> str:
    """Translate PUBG weapon attachment itemId into concise Chinese label."""
    if not item_id:
        return ""
    for needle, zh in _ATTACHMENT_ZH_RULES:
        if needle in item_id:
            return zh
    item_map = _load_dict("itemId.json")
    return item_map.get(item_id, item_id.replace("Item_Attach_Weapon_", "").removesuffix("_C"))


def resolve_gear_slot_and_level(item_id: str) -> tuple[str, int] | None:
    """Identify helmet/vest/backpack slot and level (1..3) from itemId."""
    if not item_id:
        return None
    if item_id.startswith("Item_Head_"):
        slot = "helmet"
    elif item_id.startswith("Item_Armor_"):
        slot = "vest"
    elif item_id.startswith("Item_Back_") and "Parachute" not in item_id:
        slot = "backpack"
    else:
        return None

    if "Lv3" in item_id or "LV3" in item_id:
        return (slot, 3)
    if "Lv2" in item_id or "LV2" in item_id or "BlueBlocker" in item_id:
        return (slot, 2)
    if "Lv1" in item_id or "LV1" in item_id:
        return (slot, 1)
    return (slot, 1)


def resolve_body_part(damage_reason: str) -> str:
    """Map Telemetry damageReason to normalized body zone key."""
    key = (damage_reason or "").strip().lower()
    if key == "headshot":
        return "head"
    if key == "torsoshot":
        return "torso"
    if key == "pelvisshot":
        return "pelvis"
    if key == "armshot":
        return "arm"
    if key == "legshot":
        return "leg"
    return "other"


def resolve_damage_type(category: str) -> str:
    """Convert telemetry damageTypeCategory into readable text."""
    if not category:
        return ""
    mapping = _load_dict("damageTypeCategory.json")
    return mapping.get(category, category)


def resolve_game_mode(game_mode: str) -> str:
    """Convert PUBG gameMode identifier into friendly Chinese/English text."""
    key = (game_mode or "").strip().lower()
    if key in _GAME_MODE_ZH:
        return _GAME_MODE_ZH[key]
    official = _load_dict("gameMode.json")
    return official.get(key, game_mode or "未知模式")
