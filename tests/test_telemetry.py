"""Unit tests for map coordinate conversion, v2 match parsing, and Telemetry decimation."""

from __future__ import annotations

import gzip
import json

import pytest

from src.bot.formatter import build_replay_url, format_help_message, format_match_detail
from src.core.maps import (
    get_map_info,
    pubg_cm_to_norm,
    resolve_attachment_name,
    resolve_gear_slot_and_level,
    resolve_weapon_name,
)
from src.core.models import PUBG_SLOT_COLORS, SquadMember
from src.core.telemetry import (
    REPLAY_SCHEMA_VERSION,
    STATE_DBNO,
    STATE_DEAD,
    parse_match_response,
    parse_telemetry_to_replay_gzip,
)


def _sample_squad() -> list[SquadMember]:
    # No hardcoded fixed slot alias or color: should auto-assign in-game slot colors!
    return [
        SquadMember(ign="Alpha", alias="", platform="steam", color="", account_id="acc.1"),
        SquadMember(ign="Bravo", alias="", platform="steam", color="", account_id="acc.2"),
        SquadMember(ign="Charlie", alias="", platform="steam", color="", account_id="acc.3"),
        SquadMember(ign="Delta", alias="", platform="steam", color="", account_id="acc.4"),
    ]


def _sample_match_json() -> dict:
    return {
        "data": {
            "id": "match-001",
            "attributes": {
                "mapName": "Baltic_Main",
                "gameMode": "squad-fpp",
                "createdAt": "2026-11-25T10:00:00Z",
                "duration": 1500,
            },
        },
        "included": [
            {
                "type": "asset",
                "id": "asset-1",
                "attributes": {"name": "telemetry", "URL": "https://cdn.example/t.json"},
            },
            {
                "type": "participant",
                "id": "p1",
                "attributes": {
                    "stats": {
                        "playerId": "acc.1",
                        "name": "Alpha",
                        "kills": 5,
                        "assists": 2,
                        "DBNOs": 4,
                        "damageDealt": 640.5,
                        "headshotKills": 2,
                        "revives": 1,
                        "timeSurvived": 1500,
                        "winPlace": 2,
                        "deathType": "byplayer",
                        "teamKills": 0,
                    }
                },
            },
            {
                "type": "participant",
                "id": "p2",
                "attributes": {
                    "stats": {
                        "playerId": "acc.2",
                        "name": "Bravo",
                        "kills": 1,
                        "assists": 0,
                        "DBNOs": 1,
                        "damageDealt": 120.0,
                        "headshotKills": 0,
                        "revives": 0,
                        "timeSurvived": 180,
                        "winPlace": 2,
                        "deathType": "byplayer",
                        "teamKills": 0,
                    }
                },
            },
            {
                "type": "participant",
                "id": "p_enemy",
                "attributes": {
                    "stats": {
                        "playerId": "acc.99",
                        "name": "EnemyAce",
                        "kills": 8,
                        "damageDealt": 999.0,
                        "timeSurvived": 1500,
                        "winPlace": 1,
                        "deathType": "alive",
                    }
                },
            },
            {
                "type": "roster",
                "id": "r1",
                "attributes": {"won": "false", "stats": {"teamId": 7, "rank": 2}},
                "relationships": {
                    "participants": {"data": [{"type": "participant", "id": "p1"}, {"type": "participant", "id": "p2"}]}
                },
            },
            {
                "type": "roster",
                "id": "r2",
                "attributes": {"won": "true", "stats": {"teamId": 12, "rank": 1}},
                "relationships": {
                    "participants": {"data": [{"type": "participant", "id": "p_enemy"}]}
                },
            },
        ],
    }


def _sample_telemetry_events() -> list[dict]:
    return [
        {
            "_T": "LogMatchStart",
            "_D": "2026-11-25T10:00:00.000Z",
            "mapName": "Baltic_Main",
            "characters": [
                {"character": {"accountId": "acc.1", "name": "Alpha", "teamId": 7}},
                {"character": {"accountId": "acc.2", "name": "Bravo", "teamId": 7}},
                {"character": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12}},
            ],
        },
        # Aircraft positions (should go to plane[], NOT ground trail!)
        {
            "_T": "LogPlayerPosition",
            "_D": "2026-11-25T10:00:05.000Z",
            "character": {"accountId": "acc.1", "name": "Alpha", "teamId": 7, "health": 100, "location": {"x": 100000, "y": 100000}, "isInAircraft": True},
            "common": {"isGame": 0.1},
        },
        {
            "_T": "LogPlayerPosition",
            "_D": "2026-11-25T10:00:20.000Z",
            "character": {"accountId": "acc.1", "name": "Alpha", "teamId": 7, "health": 100, "location": {"x": 500000, "y": 500000}, "isInAircraft": True},
            "common": {"isGame": 0.1},
        },
        # Landing + high-frequency ground positions
        {
            "_T": "LogParachuteLanding",
            "_D": "2026-11-25T10:00:40.000Z",
            "character": {"accountId": "acc.1", "name": "Alpha", "teamId": 7, "health": 100, "location": {"x": 400000, "y": 400000}},
            "common": {"isGame": 1.0},
        },
        {
            "_T": "LogPlayerPosition",
            "_D": "2026-11-25T10:00:40.300Z",
            "character": {"accountId": "acc.1", "name": "Alpha", "teamId": 7, "health": 100, "location": {"x": 400100, "y": 400100}},
            "common": {"isGame": 1.0},
        },
        {
            "_T": "LogPlayerPosition",
            "_D": "2026-11-25T10:00:42.500Z",
            "character": {"accountId": "acc.1", "name": "Alpha", "teamId": 7, "health": 100, "location": {"x": 402000, "y": 402000}},
            "common": {"isGame": 1.0},
        },
        # Enemy equips Lv3 Helmet, Lv2 Vest, M416 + 6x Scope + Suppressor
        {
            "_T": "LogItemEquip",
            "_D": "2026-11-25T10:00:44.000Z",
            "character": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "location": {"x": 410000, "y": 405000}},
            "item": {"itemId": "Item_Head_G_01_Lv3_C", "category": "Equipment"},
            "common": {"isGame": 1.0},
        },
        {
            "_T": "LogItemEquip",
            "_D": "2026-11-25T10:00:44.200Z",
            "character": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "location": {"x": 410000, "y": 405000}},
            "item": {"itemId": "Item_Armor_D_01_Lv2_C", "category": "Equipment"},
            "common": {"isGame": 1.0},
        },
        {
            "_T": "LogItemAttach",
            "_D": "2026-11-25T10:00:44.500Z",
            "character": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "location": {"x": 410000, "y": 405000}},
            "parentItem": {"itemId": "Item_Weapon_HK416_C", "attachedItems": []},
            "childItem": {"itemId": "Item_Attach_Weapon_Upper_Scope6x_C"},
            "common": {"isGame": 1.0},
        },
        {
            "_T": "LogItemAttach",
            "_D": "2026-11-25T10:00:44.600Z",
            "character": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "location": {"x": 410000, "y": 405000}},
            "parentItem": {"itemId": "Item_Weapon_HK416_C", "attachedItems": []},
            "childItem": {"itemId": "Item_Attach_Weapon_Muzzle_Suppressor_Large_C"},
            "common": {"isGame": 1.0},
        },
        # Enemy position
        {
            "_T": "LogPlayerPosition",
            "_D": "2026-11-25T10:00:45.000Z",
            "character": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "health": 100, "location": {"x": 410000, "y": 405000}},
            "common": {"isGame": 1.0},
        },
        # Safe / Blue zone
        {
            "_T": "LogGameStatePeriodic",
            "_D": "2026-11-25T10:01:00.000Z",
            "gameState": {
                "safetyZonePosition": {"x": 408000, "y": 408000},
                "safetyZoneRadius": 200000,
                "poisonGasWarningPosition": {"x": 408000, "y": 408000},
                "poisonGasWarningRadius": 400000,
            },
        },
        # Care package (airdrop) spawn & land
        {
            "_T": "LogCarePackageSpawn",
            "_D": "2026-11-25T10:01:10.000Z",
            "itemPackage": {
                "itemPackageId": "Carapackage_RedBox_C_0",
                "location": {"x": 415000, "y": 402000, "z": 50000},
                "items": [
                    {"itemId": "Item_Weapon_AWM_C", "category": "Weapon"},
                    {"itemId": "Item_Head_G_01_Lv3_C", "category": "Equipment"},
                ],
            },
            "common": {"isGame": 1.0},
        },
        {
            "_T": "LogCarePackageLand",
            "_D": "2026-11-25T10:01:38.000Z",
            "itemPackage": {
                "itemPackageId": "Carapackage_RedBox_C_0",
                "location": {"x": 415200, "y": 402400, "z": 1800},
                "items": [
                    {"itemId": "Item_Weapon_AWM_C", "category": "Weapon"},
                    {"itemId": "Item_Head_G_01_Lv3_C", "category": "Equipment"},
                    {"itemId": "Item_Armor_C_01_Lv3_C", "category": "Equipment"},
                ],
            },
            "common": {"isGame": 1.0},
        },
        # Friendly fire (Alpha hits Bravo)
        {
            "_T": "LogPlayerTakeDamage",
            "_D": "2026-11-25T10:01:30.000Z",
            "attacker": {"accountId": "acc.1", "name": "Alpha", "teamId": 7, "location": {"x": 403000, "y": 403000}},
            "victim": {"accountId": "acc.2", "name": "Bravo", "teamId": 7, "location": {"x": 403500, "y": 403500}},
            "damage": 45.0,
            "damageCauserName": "ProjGrenade_C",
            "damageReason": "TorsoShot",
        },
        # Enemy shoots Bravo in head + torso right before knock
        {
            "_T": "LogPlayerTakeDamage",
            "_D": "2026-11-25T10:01:58.000Z",
            "attacker": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "location": {"x": 410000, "y": 405000}},
            "victim": {"accountId": "acc.2", "name": "Bravo", "teamId": 7, "location": {"x": 403500, "y": 403500}},
            "damage": 72.0,
            "damageCauserName": "WeapHK416_C",
            "damageReason": "HeadShot",
        },
        {
            "_T": "LogPlayerMakeGroggy",
            "_D": "2026-11-25T10:02:00.000Z",
            "attacker": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "location": {"x": 410000, "y": 405000}},
            "victim": {"accountId": "acc.2", "name": "Bravo", "teamId": 7, "location": {"x": 403500, "y": 403500}},
            "damageCauserName": "WeapHK416_C",
            "damageReason": "HeadShot",
            "distance": 6500.0,
        },
        {
            "_T": "LogPlayerKillV2",
            "_D": "2026-11-25T10:02:08.000Z",
            "killer": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "location": {"x": 410000, "y": 405000}},
            "victim": {"accountId": "acc.2", "name": "Bravo", "teamId": 7, "location": {"x": 403500, "y": 403500}},
            "killerDamageInfo": {
                "damageCauserName": "WeapHK416_C",
                "damageReason": "HeadShot",
                "distance": 6500.0,
            },
        },
        {
            "_T": "LogMatchEnd",
            "_D": "2026-11-25T10:25:00.000Z",
            "characters": [
                {"character": {"accountId": "acc.99", "name": "EnemyAce", "teamId": 12, "ranking": 1}},
                {"character": {"accountId": "acc.1", "name": "Alpha", "teamId": 7, "ranking": 2}},
                {"character": {"accountId": "acc.2", "name": "Bravo", "teamId": 7, "ranking": 2}},
            ],
        },
    ]


def test_map_coordinate_normalization_and_dictionaries() -> None:
    erangel = get_map_info("Baltic_Main")
    assert erangel.size_cm == 816000.0
    assert erangel.image_file == "static/maps/erangel.webp"
    assert erangel.terrain_file == "static/maps/terrain/erangel.webp"
    assert pubg_cm_to_norm(408000.0, 204000.0, "Baltic_Main") == pytest.approx((0.5, 0.25))

    sanhok = get_map_info("Savage_Main")
    assert sanhok.size_cm == 408000.0
    assert pubg_cm_to_norm(204000.0, 408000.0, "Savage_Main") == pytest.approx((0.5, 1.0))

    assert resolve_weapon_name("WeapHK416_C") == "M416"
    assert resolve_weapon_name("ProjGrenade_C") == "Frag Grenade"
    assert resolve_attachment_name("Item_Attach_Weapon_Upper_Scope6x_C") == "6倍瞄准镜"
    assert resolve_gear_slot_and_level("Item_Head_G_01_Lv3_C") == ("helmet", 3)


def test_parse_match_response_dynamic_slots_and_qq_segmentation_safety() -> None:
    squad = _sample_squad()
    summary, all_aids = parse_match_response(_sample_match_json(), "steam", squad)
    assert summary.match_id == "match-001"
    assert summary.squad_win_place == 2
    assert summary.squad_total_kills == 6
    assert summary.highlights.fastest_death_player == "Bravo"
    assert summary.highlights.top_damage_player == "Alpha"
    assert "acc.99" in all_aids

    by_name = {p.name: p for p in summary.squad_players}
    assert by_name["Alpha"].squad_slot == 1
    assert by_name["Alpha"].color == PUBG_SLOT_COLORS[1]
    assert by_name["Bravo"].squad_slot == 2
    assert by_name["Bravo"].color == PUBG_SLOT_COLORS[2]
    assert by_name["Bravo"].death_order == 1

    # Verify QQ outputs & replay links contain ZERO reply-segmentation punctuation
    url = build_replay_url("https://pubg.example.com", summary.match_id)
    assert url == "https://pubg.example.com/match/match-001"
    assert "?" not in url
    report = format_match_detail(summary, web_public_url="https://pubg.example.com", enable_web=True)
    help_text = format_help_message()
    for ch in ("。", "？", "！", "?", "!"):
        assert ch not in report
        assert ch not in help_text


@pytest.mark.asyncio
async def test_telemetry_v2_gear_attachments_hitzones_and_clean_ground_trails() -> None:
    squad = _sample_squad()
    summary, _ = parse_match_response(_sample_match_json(), "steam", squad)
    raw_gzip = gzip.compress(json.dumps(_sample_telemetry_events()).encode("utf-8"))

    replay, out_gz = await parse_telemetry_to_replay_gzip(
        raw_gzip, summary, squad, sample_interval_sec=1.5
    )

    decoded = json.loads(gzip.decompress(out_gz).decode("utf-8"))
    assert decoded["version"] == REPLAY_SCHEMA_VERSION
    assert decoded["map_image"] == "static/maps/erangel.webp"
    assert decoded["terrain_image"] == "static/maps/terrain/erangel.webp"
    assert decoded["heightmap_image"] == "static/maps/height/erangel.png"
    assert len(decoded["plane"]) == 2
    assert len(decoded["plane_flight"]) == 6
    # Edge-to-edge flight path must reach map borders (0 or map_size)
    assert any(c in (0.0, 816000.0) for pt in decoded["plane"] for c in pt)
    assert len(decoded["care_packages"]) == 1
    cp0 = decoded["care_packages"][0]
    assert cp0["spawn_t"] == pytest.approx(70.0)
    assert cp0["land_t"] == pytest.approx(98.0)
    assert cp0["x"] == pytest.approx(415200.0)
    assert "AWM" in cp0["items"]
    assert "三级头" in cp0["items"]
    assert "三级甲" in cp0["items"]
    assert "7" in decoded["teams"] and "12" in decoded["teams"]
    assert decoded["teams"]["12"]["rank"] == 1
    assert decoded["teams"]["7"]["rank"] == 2
    assert decoded["players"]["acc.99"]["win_place"] == 1
    assert decoded["players"]["acc.1"]["win_place"] == 2
    assert decoded["players"]["acc.2"]["win_place"] == 2

    # Aircraft points at t=5s and t=20s MUST NOT pollute ground track!
    alpha_times = [pt[0] for pt in decoded["tracks"]["acc.1"]]
    assert all(len(pt) == 6 for pt in decoded["tracks"]["acc.1"])
    assert 5.0 not in alpha_times
    assert 20.0 not in alpha_times
    assert 40.0 in alpha_times
    assert 42.5 in alpha_times
    assert 40.3 not in alpha_times

    # Verify enemy player gear, weapons & attachments snapshot
    enemy = decoded["players"]["acc.99"]
    assert enemy["is_squad"] is False
    assert enemy["gear"] == [3, 2, 0]
    assert any(w["name"] == "M416" and "6倍瞄准镜" in w["attachments"] for w in enemy["weapons"])

    # Verify DBNO -> DEAD state transitions
    bravo_states = [pt[4] for pt in decoded["tracks"]["acc.2"]]
    assert STATE_DBNO in bravo_states
    assert STATE_DEAD in bravo_states

    # Verify XiaoHeiHe-style knock/kill fight detail breakdown
    kill_ev = next(e for e in replay["events"] if e["type"] == "kill")
    assert kill_ev["weapon"] == "M416"
    assert kill_ev["attacker_team_id"] == 12
    assert kill_ev["victim_team_id"] == 7
    detail = kill_ev["detail"]
    assert detail["attacker_gear"] == [3, 2, 0]
    assert "6倍瞄准镜" in detail["attachments"]
    assert "消音器" in detail["attachments"]
    assert detail["hit_zones"]["head"]["dmg"] == pytest.approx(72.0)
    assert detail["weapon_damage"] == pytest.approx(72.0)
