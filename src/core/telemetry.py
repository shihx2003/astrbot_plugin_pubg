"""Match response parser and off-thread Telemetry decimation engine for 2D replays (v2)."""

from __future__ import annotations

import asyncio
import gzip
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .maps import (
    get_map_info,
    get_team_color,
    resolve_attachment_name,
    resolve_body_part,
    resolve_game_mode,
    resolve_gear_slot_and_level,
    resolve_weapon_name,
)
from .models import (
    MatchHighlights,
    MatchSummary,
    PlayerMatchStats,
    SquadMember,
    slot_color,
)

# Current compact replay schema version (v4 stores final win_place ranking for all players & teams)
REPLAY_SCHEMA_VERSION = 4

# Player state encoding inside compact replay tracks: [t, x, y, hp, state, z]
STATE_ALIVE = 0
STATE_DBNO = 1
STATE_DEAD = 2


def _parse_iso_ts(value: str | None) -> float | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def parse_match_response(
    match_json: dict[str, Any],
    platform: str,
    squad_members: list[SquadMember],
) -> tuple[MatchSummary, list[str]]:
    """Parse PUBG /matches/{id} JSON into MatchSummary + all participant account IDs.

    Derives per-match `squad_slot` (1..4) dynamically from the official roster order
    and assigns PUBG in-game slot colors (1=Yellow, 2=Orange, 3=Blue, 4=Green) unless overridden.
    """
    data = match_json.get("data") or {}
    attrs = data.get("attributes") or {}
    match_id = str(data.get("id") or "")
    map_id = str(attrs.get("mapName") or "")
    map_info = get_map_info(map_id)
    game_mode = str(attrs.get("gameMode") or "")
    created_at = str(attrs.get("createdAt") or "")
    duration = int(attrs.get("duration") or 0)

    included = match_json.get("included") or []
    participants_by_id: dict[str, dict[str, Any]] = {}
    rosters_by_participant: dict[str, dict[str, Any]] = {}
    telemetry_url = ""

    for obj in included:
        obj_type = obj.get("type")
        if obj_type == "participant":
            participants_by_id[str(obj.get("id"))] = obj
        elif obj_type == "asset":
            a_attrs = obj.get("attributes") or {}
            if a_attrs.get("name") == "telemetry" and a_attrs.get("URL"):
                telemetry_url = str(a_attrs["URL"])

    for obj in included:
        if obj.get("type") != "roster":
            continue
        r_attrs = obj.get("attributes") or {}
        r_stats = r_attrs.get("stats") or {}
        rel_p = ((obj.get("relationships") or {}).get("participants") or {}).get("data") or []
        for slot_idx, item in enumerate(rel_p, start=1):
            pid = str(item.get("id") or "")
            if pid:
                rosters_by_participant[pid] = {
                    "team_id": int(r_stats.get("teamId") or 0),
                    "rank": int(r_stats.get("rank") or 0),
                    "won": str(r_attrs.get("won", "false")).lower() == "true",
                    "squad_slot": slot_idx,
                }

    squad_by_aid = {m.account_id: m for m in squad_members if m.account_id}
    squad_by_ign = {m.ign.lower(): m for m in squad_members if m.ign}

    all_account_ids: list[str] = []
    squad_players: list[PlayerMatchStats] = []
    squad_team_ids: set[int] = set()

    for pid, p_obj in participants_by_id.items():
        stats = (p_obj.get("attributes") or {}).get("stats") or {}
        aid = str(stats.get("playerId") or "")
        name = str(stats.get("name") or "")
        if aid:
            all_account_ids.append(aid)

        member = squad_by_aid.get(aid) or squad_by_ign.get(name.lower())
        if member is None:
            continue

        roster = rosters_by_participant.get(pid, {})
        team_id = int(roster.get("team_id") or 0)
        win_place = int(stats.get("winPlace") or roster.get("rank") or 0)
        squad_slot = int(roster.get("squad_slot") or (len(squad_players) + 1))
        if team_id:
            squad_team_ids.add(team_id)

        resolved_color = member.color.strip() or slot_color(squad_slot, len(squad_players))

        squad_players.append(
            PlayerMatchStats(
                account_id=aid,
                name=name,
                team_id=team_id,
                squad_slot=squad_slot,
                kills=int(stats.get("kills") or 0),
                assists=int(stats.get("assists") or 0),
                dbnos=int(stats.get("DBNOs") or 0),
                damage_dealt=float(stats.get("damageDealt") or 0.0),
                headshots=int(stats.get("headshotKills") or 0),
                revives=int(stats.get("revives") or 0),
                time_survived=float(stats.get("timeSurvived") or 0.0),
                win_place=win_place,
                kill_place=int(stats.get("killPlace") or 0),
                death_type=str(stats.get("deathType") or "alive"),
                longest_kill=float(stats.get("longestKill") or 0.0),
                walk_distance=float(stats.get("walkDistance") or 0.0),
                ride_distance=float(stats.get("rideDistance") or 0.0),
                swim_distance=float(stats.get("swimDistance") or 0.0),
                team_kills=int(stats.get("teamKills") or 0),
                alias=member.alias,
                color=resolved_color,
                is_squad_member=True,
            )
        )

    # Also include non-bound random teammates on the same team_id if squad < 4
    if squad_team_ids and len(squad_players) < 4:
        existing_aids = {p.account_id for p in squad_players}
        for pid, p_obj in participants_by_id.items():
            roster = rosters_by_participant.get(pid, {})
            team_id = int(roster.get("team_id") or 0)
            if team_id not in squad_team_ids:
                continue
            stats = (p_obj.get("attributes") or {}).get("stats") or {}
            aid = str(stats.get("playerId") or "")
            if aid in existing_aids:
                continue
            squad_slot = int(roster.get("squad_slot") or (len(squad_players) + 1))
            squad_players.append(
                PlayerMatchStats(
                    account_id=aid,
                    name=str(stats.get("name") or ""),
                    team_id=team_id,
                    squad_slot=squad_slot,
                    kills=int(stats.get("kills") or 0),
                    assists=int(stats.get("assists") or 0),
                    dbnos=int(stats.get("DBNOs") or 0),
                    damage_dealt=float(stats.get("damageDealt") or 0.0),
                    headshots=int(stats.get("headshotKills") or 0),
                    revives=int(stats.get("revives") or 0),
                    time_survived=float(stats.get("timeSurvived") or 0.0),
                    win_place=int(stats.get("winPlace") or roster.get("rank") or 0),
                    kill_place=int(stats.get("killPlace") or 0),
                    death_type=str(stats.get("deathType") or "alive"),
                    longest_kill=float(stats.get("longestKill") or 0.0),
                    team_kills=int(stats.get("teamKills") or 0),
                    alias="",
                    color=slot_color(squad_slot, len(squad_players)),
                    is_squad_member=False,
                )
            )

    # Sort squad players by in-game slot 1..4 cleanly
    squad_players.sort(key=lambda p: (p.squad_slot or 99, p.name.lower()))

    dead_players = sorted(
        [p for p in squad_players if p.death_type != "alive"],
        key=lambda p: p.time_survived,
    )
    for idx, dp in enumerate(dead_players, start=1):
        dp.death_order = idx

    valid_places = [p.win_place for p in squad_players if p.win_place > 0]
    squad_win_place = min(valid_places) if valid_places else 0
    total_kills = sum(p.kills for p in squad_players)
    total_damage = sum(p.damage_dealt for p in squad_players)
    is_chicken = squad_win_place == 1

    highlights = MatchHighlights()
    if dead_players:
        highlights.fastest_death_player = dead_players[0].display_name
        highlights.fastest_death_sec = round(dead_players[0].time_survived, 1)
    if squad_players:
        top_dmg = max(squad_players, key=lambda p: p.damage_dealt)
        highlights.top_damage_player = top_dmg.display_name
        highlights.top_damage = round(top_dmg.damage_dealt, 1)
        top_k = max(squad_players, key=lambda p: (p.kills, p.damage_dealt))
        highlights.top_kills_player = top_k.display_name
        highlights.top_kills = top_k.kills

    summary = MatchSummary(
        match_id=match_id,
        platform=platform,
        map_name=map_id,
        map_display_name=map_info.display_name,
        map_image=map_info.image_file,
        terrain_image=map_info.terrain_file,
        game_mode=game_mode,
        game_mode_display=resolve_game_mode(game_mode),
        created_at=created_at,
        duration=duration,
        telemetry_url=telemetry_url,
        squad_win_place=squad_win_place,
        squad_total_kills=total_kills,
        squad_total_damage=total_damage,
        is_chicken_dinner=is_chicken,
        all_players_count=len(participants_by_id),
        squad_players=squad_players,
        highlights=highlights,
    )
    return summary, all_account_ids


def _loc_xy(character: dict[str, Any] | None) -> tuple[float, float]:
    if not character:
        return (0.0, 0.0)
    loc = character.get("location") or {}
    return (round(float(loc.get("x") or 0.0), 1), round(float(loc.get("y") or 0.0), 1))


def _loc_xyz(character: dict[str, Any] | None) -> tuple[float, float, float]:
    if not character:
        return (0.0, 0.0, 0.0)
    loc = character.get("location") or {}
    return (
        round(float(loc.get("x") or 0.0), 1),
        round(float(loc.get("y") or 0.0), 1),
        round(float(loc.get("z") or 0.0), 1),
    )


def build_replay_sync(
    raw_telemetry_bytes: bytes,
    match_summary: MatchSummary,
    squad_members: list[SquadMember],
    sample_interval_sec: float = 1.5,
) -> tuple[dict[str, Any], bytes]:
    """Synchronously parse, decimate, and gzip-compress Telemetry into a v2 2D replay.

    Key v2 capabilities:
    - Excludes pre-match lobby & in-aircraft coordinates from ground movement trails
      (eliminating straight-line artifacts to map edges while preserving plane flight path).
    - Derives per-team squad slots (1..4) from LogMatchStart / roster order and maps
      bound squad members to PUBG official in-game colors (1=Yellow, 2=Orange, 3=Blue, 4=Green).
    - Tracks live per-player gear (Helmet/Vest/Backpack Lv1-3) and weapon attachments.
    - Enriches every knock/kill event with attacker/victim gear, weapon attachments,
      hit-zone body diagram breakdown (head/torso/pelvis/arm/leg), weapon/env damage, and assists.
    - Builds per-player weapon usage statistics for interactive player inspection modal.
    """
    if raw_telemetry_bytes[:2] == b"\x1f\x8b":
        json_bytes = gzip.decompress(raw_telemetry_bytes)
    else:
        json_bytes = raw_telemetry_bytes

    raw_events: list[dict[str, Any]] = json.loads(json_bytes.decode("utf-8"))
    del json_bytes

    sample_interval = max(0.5, float(sample_interval_sec))
    squad_by_aid = {m.account_id: m for m in squad_members if m.account_id}
    squad_by_ign = {m.ign.lower(): m for m in squad_members if m.ign}

    t0: float | None = None
    map_id = match_summary.map_name
    team_slot_lookup: dict[str, int] = {}
    team_slot_counters: dict[int, int] = defaultdict(int)

    # Pre-seed slot numbers from match_summary (official roster order)
    for sp in match_summary.squad_players:
        if sp.account_id and sp.squad_slot:
            team_slot_lookup[sp.account_id] = sp.squad_slot

    for ev in raw_events:
        if ev.get("_T") == "LogMatchStart":
            t0 = _parse_iso_ts(ev.get("_D"))
            if ev.get("mapName"):
                map_id = str(ev["mapName"])
            for entry in ev.get("characters") or []:
                char = entry.get("character") if isinstance(entry, dict) and "character" in entry else entry
                if not isinstance(char, dict):
                    continue
                aid = str(char.get("accountId") or "")
                tid = int(char.get("teamId") or 0)
                if aid and tid and aid not in team_slot_lookup:
                    team_slot_counters[tid] += 1
                    team_slot_lookup[aid] = team_slot_counters[tid]
            break

    if t0 is None:
        for ev in raw_events:
            ts = _parse_iso_ts(ev.get("_D"))
            if ts is not None:
                t0 = ts
                break
    t0 = t0 or 0.0
    map_info = get_map_info(map_id)

    players: dict[str, dict[str, Any]] = {}
    tracks: dict[str, list[list[float | int]]] = {}
    last_sample_t: dict[str, float] = {}
    player_state: dict[str, int] = {}
    zones: list[list[float | int]] = []
    last_zone_sig: tuple[int, int, int, int, int, int] | None = None
    last_zone_t: float = -999.0
    events: list[dict[str, Any]] = []
    aircraft_points: list[tuple[float, float]] = []
    max_t: float = float(match_summary.duration or 0)

    # Live inventory & fight window trackers (discarded before serialization)
    live_gear: dict[str, dict[str, int]] = defaultdict(lambda: {"helmet": 0, "vest": 0, "backpack": 0})
    peak_gear: dict[str, dict[str, int]] = defaultdict(lambda: {"helmet": 0, "vest": 0, "backpack": 0})
    # weapon_attachments[aid][weapon_display_name] = set of attachment Chinese labels
    weapon_attachments: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    # weapon_stats[aid][weapon_display_name] = {damage, hits, headshots, knocks, kills}
    weapon_stats: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    # recent_hits[victim_id] = list of dicts over last ~20s for fight detail breakdown
    recent_hits: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def _get_wstat(aid: str, weapon_name: str) -> dict[str, Any]:
        wdict = weapon_stats[aid]
        if weapon_name not in wdict:
            wdict[weapon_name] = {"damage": 0.0, "hits": 0, "headshots": 0, "knocks": 0, "kills": 0}
        return wdict[weapon_name]

    def _gear_list(aid: str) -> list[int]:
        g = live_gear[aid]
        return [g["helmet"], g["vest"], g["backpack"]]

    def _summarize_fight(victim_id: str, attacker_id: str, t_now: float, weapon_name: str) -> dict[str, Any]:
        window = [h for h in recent_hits.get(victim_id, []) if (t_now - h["t"]) <= 20.0]
        recent_hits[victim_id] = window

        hit_zones: dict[str, dict[str, Any]] = {
            "head": {"dmg": 0.0, "hits": 0},
            "torso": {"dmg": 0.0, "hits": 0},
            "pelvis": {"dmg": 0.0, "hits": 0},
            "arm": {"dmg": 0.0, "hits": 0},
            "leg": {"dmg": 0.0, "hits": 0},
            "other": {"dmg": 0.0, "hits": 0},
        }
        weapon_dmg = 0.0
        env_dmg = 0.0
        assist_map: dict[str, dict[str, Any]] = {}

        for h in window:
            part = h["part"]
            dmg = h["dmg"]
            if attacker_id and h["aid"] == attacker_id:
                weapon_dmg += dmg
                hit_zones[part]["dmg"] = round(hit_zones[part]["dmg"] + dmg, 1)
                hit_zones[part]["hits"] += 1
            elif not h["aid"] or h["aid"] == victim_id:
                env_dmg += dmg
            else:
                entry = assist_map.setdefault(
                    h["aid"],
                    {"account_id": h["aid"], "name": h["aname"], "damage": 0.0},
                )
                entry["damage"] = round(entry["damage"] + dmg, 1)

        # Compact: omit zero zones
        compact_zones = {k: v for k, v in hit_zones.items() if v["hits"] > 0 or v["dmg"] > 0}
        att_list = sorted(weapon_attachments.get(attacker_id, {}).get(weapon_name, set())) if attacker_id else []
        return {
            "attacker_gear": _gear_list(attacker_id) if attacker_id else [0, 0, 0],
            "victim_gear": _gear_list(victim_id) if victim_id else [0, 0, 0],
            "attachments": att_list,
            "hit_zones": compact_zones,
            "weapon_damage": round(weapon_dmg, 1),
            "env_damage": round(env_dmg, 1),
            "assists": sorted(assist_map.values(), key=lambda x: -x["damage"])[:4],
        }

    def ensure_player(char: dict[str, Any] | None) -> str:
        if not char:
            return ""
        aid = str(char.get("accountId") or "")
        name = str(char.get("name") or "")
        if not aid:
            aid = f"name:{name}" if name else ""
        if not aid:
            return ""
        tid = int(char.get("teamId") or 0)
        if aid not in players:
            member = squad_by_aid.get(aid) or squad_by_ign.get(name.lower())
            slot_num = team_slot_lookup.get(aid, 0)
            if not slot_num and tid:
                team_slot_counters[tid] += 1
                slot_num = team_slot_counters[tid]
                team_slot_lookup[aid] = slot_num
            is_sq = member is not None
            if is_sq:
                p_color = (member.color or "").strip() or slot_color(slot_num, 0)
            else:
                p_color = get_team_color(tid)
            players[aid] = {
                "account_id": aid,
                "name": name or aid,
                "alias": member.alias if member else "",
                "display_name": member.display_name if member else (name or aid),
                "team_id": tid,
                "squad_slot": slot_num,
                "is_squad": is_sq,
                "color": p_color,
                "team_color": get_team_color(tid),
                "kills": 0,
                "assists": 0,
                "dbnos": 0,
                "damage": 0.0,
                "headshots": 0,
                "revives": 0,
                "team_kills": 0,
                "team_damage": 0.0,
                "win_place": int(char.get("ranking") or 0),
                "time_survived": 0.0,
                "landing": None,
                "gear": [0, 0, 0],
                "weapons": [],
            }
            tracks[aid] = []
            player_state[aid] = STATE_ALIVE
        else:
            if name and players[aid]["name"] == aid:
                players[aid]["name"] = name
            if tid and not players[aid]["team_id"]:
                players[aid]["team_id"] = tid
                players[aid]["team_color"] = get_team_color(tid)
                if not players[aid]["is_squad"]:
                    players[aid]["color"] = get_team_color(tid)
            ranking = int(char.get("ranking") or 0)
            if ranking:
                players[aid]["win_place"] = ranking
        return aid

    def append_track_point(
        aid: str,
        t_rel: float,
        x: float,
        y: float,
        hp: float,
        state: int,
        z: float = 0.0,
        force: bool = False,
    ) -> None:
        if not aid or (x == 0.0 and y == 0.0):
            return
        # Guard against out-of-bounds pre-match spawn island / dummy points
        if x < -50000 or y < -50000 or x > map_info.size_cm + 50000 or y > map_info.size_cm + 50000:
            return
        prev_t = last_sample_t.get(aid, -999.0)
        if not force and (t_rel - prev_t) < sample_interval:
            return
        tracks[aid].append([round(t_rel, 1), round(x, 1), round(y, 1), int(round(hp)), state, round(z, 1)])
        last_sample_t[aid] = t_rel

    # Pre-seed match_summary squad players with official totals & derived slot
    for sp in match_summary.squad_players:
        if sp.account_id:
            slot_num = sp.squad_slot or team_slot_lookup.get(sp.account_id, 1)
            team_slot_lookup[sp.account_id] = slot_num
            member = squad_by_aid.get(sp.account_id) or squad_by_ign.get(sp.name.lower())
            p_color = (member.color.strip() if member and member.color else "") or sp.color or slot_color(slot_num, 0)
            players[sp.account_id] = {
                "account_id": sp.account_id,
                "name": sp.name,
                "alias": sp.alias,
                "display_name": sp.display_name,
                "team_id": sp.team_id,
                "squad_slot": slot_num,
                "is_squad": sp.is_squad_member,
                "color": p_color,
                "team_color": get_team_color(sp.team_id),
                "kills": sp.kills,
                "assists": sp.assists,
                "dbnos": sp.dbnos,
                "damage": round(sp.damage_dealt, 1),
                "headshots": sp.headshots,
                "revives": sp.revives,
                "team_kills": sp.team_kills,
                "team_damage": 0.0,
                "win_place": sp.win_place,
                "time_survived": round(sp.time_survived, 1),
                "landing": None,
                "gear": [0, 0, 0],
                "weapons": [],
            }
            tracks[sp.account_id] = []
            player_state[sp.account_id] = STATE_ALIVE

    # Single-pass scan over Telemetry events
    for ev in raw_events:
        ev_type = ev.get("_T")
        ts = _parse_iso_ts(ev.get("_D"))
        if ts is None:
            continue
        t_rel = ts - t0
        if t_rel < -5.0:
            continue
        if t_rel < 0.0:
            t_rel = 0.0
        if t_rel > max_t:
            max_t = t_rel

        common = ev.get("common") or {}
        is_game = float(common.get("isGame")) if "isGame" in common and common.get("isGame") is not None else 1.0

        if ev_type == "LogPlayerPosition":
            char = ev.get("character")
            aid = ensure_player(char)
            if not aid:
                continue
            x, y, z = _loc_xyz(char)
            hp = float(char.get("health") or 0.0)
            vehicle = ev.get("vehicle") or {}
            in_aircraft = bool(
                char.get("isInAircraft")
                or ev.get("isInAircraft")
                or "TransportAircraft" in str(vehicle.get("vehicleId") or "")
                or (0.05 <= is_game < 0.5)
            )
            if in_aircraft:
                if (x != 0.0 or y != 0.0) and (
                    not aircraft_points
                    or math.hypot(x - aircraft_points[-1][0], y - aircraft_points[-1][1]) > 15000
                ):
                    aircraft_points.append((x, y))
                # Do NOT add pre-jump airplane coordinates into player's ground movement trail!
                continue
            if is_game < 0.5:
                # Skip lobby warmup positions
                continue

            state = player_state.get(aid, STATE_ALIVE)
            if state != STATE_DEAD:
                append_track_point(aid, t_rel, x, y, hp, state, z=z, force=len(tracks[aid]) == 0)
                if t_rel > players[aid]["time_survived"]:
                    players[aid]["time_survived"] = round(t_rel, 1)

        elif ev_type == "LogParachuteLanding":
            char = ev.get("character")
            aid = ensure_player(char)
            if not aid:
                continue
            x, y, z = _loc_xyz(char)
            hp = float(char.get("health") or 100.0)
            players[aid]["landing"] = [round(t_rel, 1), x, y, z]
            append_track_point(aid, t_rel, x, y, hp, STATE_ALIVE, z=z, force=True)

        elif ev_type == "LogItemEquip":
            char = ev.get("character")
            item = ev.get("item") or {}
            aid = ensure_player(char)
            if not aid or is_game < 0.5:
                continue
            item_id = str(item.get("itemId") or "")
            gear = resolve_gear_slot_and_level(item_id)
            if gear:
                gslot, glv = gear
                live_gear[aid][gslot] = glv
                if glv > peak_gear[aid][gslot]:
                    peak_gear[aid][gslot] = glv
            elif item.get("category") == "Weapon" and item_id:
                wname = resolve_weapon_name(item_id)
                _get_wstat(aid, wname)
                for att in item.get("attachedItems") or []:
                    lbl = resolve_attachment_name(str(att))
                    if lbl:
                        weapon_attachments[aid][wname].add(lbl)

        elif ev_type == "LogItemUnequip":
            char = ev.get("character")
            item = ev.get("item") or {}
            aid = ensure_player(char)
            if not aid or is_game < 0.5:
                continue
            item_id = str(item.get("itemId") or "")
            gear = resolve_gear_slot_and_level(item_id)
            if gear:
                live_gear[aid][gear[0]] = 0

        elif ev_type == "LogArmorDestroy":
            victim = ev.get("victim")
            item = ev.get("item") or {}
            vid = ensure_player(victim)
            if vid:
                gear = resolve_gear_slot_and_level(str(item.get("itemId") or ""))
                if gear:
                    live_gear[vid][gear[0]] = 0

        elif ev_type == "LogItemAttach":
            char = ev.get("character")
            parent = ev.get("parentItem") or {}
            child = ev.get("childItem") or {}
            aid = ensure_player(char)
            if not aid:
                continue
            wname = resolve_weapon_name(str(parent.get("itemId") or ""))
            att_lbl = resolve_attachment_name(str(child.get("itemId") or ""))
            if wname and att_lbl:
                weapon_attachments[aid][wname].add(att_lbl)
            for att in parent.get("attachedItems") or []:
                lbl = resolve_attachment_name(str(att))
                if lbl:
                    weapon_attachments[aid][wname].add(lbl)

        elif ev_type == "LogItemDetach":
            char = ev.get("character")
            parent = ev.get("parentItem") or {}
            child = ev.get("childItem") or {}
            aid = ensure_player(char)
            if not aid:
                continue
            wname = resolve_weapon_name(str(parent.get("itemId") or ""))
            att_lbl = resolve_attachment_name(str(child.get("itemId") or ""))
            if wname and att_lbl:
                weapon_attachments[aid][wname].discard(att_lbl)

        elif ev_type == "LogGameStatePeriodic":
            gs = ev.get("gameState") or {}
            safe_pos = gs.get("safetyZonePosition") or {}
            poison_pos = gs.get("poisonGasWarningPosition") or {}
            sx = round(float(safe_pos.get("x") or 0.0), 1)
            sy = round(float(safe_pos.get("y") or 0.0), 1)
            sr = round(float(gs.get("safetyZoneRadius") or 0.0), 1)
            px = round(float(poison_pos.get("x") or 0.0), 1)
            py = round(float(poison_pos.get("y") or 0.0), 1)
            pr = round(float(gs.get("poisonGasWarningRadius") or 0.0), 1)
            if sr <= 0 and pr <= 0:
                continue
            sig = (int(sx // 100), int(sy // 100), int(sr // 100), int(px // 100), int(py // 100), int(pr // 100))
            if sig != last_zone_sig or (t_rel - last_zone_t) >= 8.0:
                zones.append([round(t_rel, 1), sx, sy, sr, px, py, pr])
                last_zone_sig = sig
                last_zone_t = t_rel

        elif ev_type == "LogPlayerTakeDamage":
            attacker = ev.get("attacker")
            victim = ev.get("victim")
            damage = float(ev.get("damage") or 0.0)
            if not victim or damage <= 0:
                continue
            vid = ensure_player(victim)
            aid = ensure_player(attacker) if attacker else ""
            if not vid:
                continue

            weapon_raw = str(ev.get("damageCauserName") or "")
            weapon_name = resolve_weapon_name(weapon_raw)
            dmg_reason = str(ev.get("damageReason") or "")
            part = resolve_body_part(dmg_reason)

            # Record hit into sliding fight window for detailed kill/knock breakdown
            recent_hits[vid].append(
                {
                    "t": t_rel,
                    "aid": aid,
                    "aname": players[aid]["display_name"] if aid else "环境",
                    "weapon": weapon_name,
                    "part": part,
                    "dmg": damage,
                }
            )
            if len(recent_hits[vid]) > 40:
                recent_hits[vid] = recent_hits[vid][-30:]

            if not aid or aid == vid:
                continue

            wstat = _get_wstat(aid, weapon_name)
            wstat["damage"] = round(wstat["damage"] + damage, 1)
            wstat["hits"] += 1
            if part == "head":
                wstat["headshots"] += 1

            if not players[aid]["is_squad"]:
                players[aid]["damage"] = round(players[aid]["damage"] + damage, 1)

            a_team = players[aid]["team_id"]
            v_team = players[vid]["team_id"]
            if a_team and v_team and a_team == v_team:
                players[aid]["team_damage"] = round(players[aid]["team_damage"] + damage, 1)
                if players[aid]["is_squad"] or players[vid]["is_squad"]:
                    ax, ay = _loc_xy(attacker)
                    vx, vy = _loc_xy(victim)
                    events.append(
                        {
                            "t": round(t_rel, 1),
                            "type": "team_damage",
                            "attacker_id": aid,
                            "attacker_name": players[aid]["display_name"],
                            "attacker_team_id": a_team,
                            "victim_id": vid,
                            "victim_name": players[vid]["display_name"],
                            "victim_team_id": v_team,
                            "weapon": weapon_name,
                            "weapon_raw": weapon_raw,
                            "damage": round(damage, 1),
                            "distance": round(math.hypot(ax - vx, ay - vy) / 100.0, 1),
                            "is_headshot": part == "head",
                            "is_team_kill": True,
                            "ax": ax,
                            "ay": ay,
                            "vx": vx,
                            "vy": vy,
                        }
                    )

        elif ev_type == "LogPlayerMakeGroggy":
            attacker = ev.get("attacker")
            victim = ev.get("victim")
            vid = ensure_player(victim)
            aid = ensure_player(attacker) if attacker else ""
            vx, vy, vz = _loc_xyz(victim)
            ax, ay, az = _loc_xyz(attacker) if attacker else (vx, vy, vz)
            if vid:
                player_state[vid] = STATE_DBNO
                append_track_point(vid, t_rel, vx, vy, 0.0, STATE_DBNO, z=vz, force=True)
            weapon_raw = str(ev.get("damageCauserName") or "")
            weapon_name = resolve_weapon_name(weapon_raw)
            if aid:
                _get_wstat(aid, weapon_name)["knocks"] += 1
                if not players[aid]["is_squad"]:
                    players[aid]["dbnos"] += 1
            dist_m = float(ev.get("distance") or 0.0) / 100.0
            if dist_m <= 0 and attacker and victim:
                dist_m = math.hypot(ax - vx, ay - vy) / 100.0
            a_team = players[aid]["team_id"] if aid else 0
            v_team = players[vid]["team_id"] if vid else 0
            is_tk = bool(aid and vid and aid != vid and a_team and a_team == v_team)
            detail = _summarize_fight(vid, aid, t_rel, weapon_name)
            events.append(
                {
                    "t": round(t_rel, 1),
                    "type": "knock",
                    "attacker_id": aid,
                    "attacker_name": players[aid]["display_name"] if aid else "环境",
                    "attacker_team_id": a_team,
                    "victim_id": vid,
                    "victim_name": players[vid]["display_name"] if vid else "未知",
                    "victim_team_id": v_team,
                    "weapon": weapon_name,
                    "weapon_raw": weapon_raw,
                    "distance": round(dist_m, 1),
                    "is_headshot": str(ev.get("damageReason") or "") == "HeadShot",
                    "is_team_kill": is_tk,
                    "ax": ax,
                    "ay": ay,
                    "az": az,
                    "vx": vx,
                    "vy": vy,
                    "vz": vz,
                    "detail": detail,
                }
            )

        elif ev_type == "LogPlayerRevive":
            reviver = ev.get("reviver")
            victim = ev.get("victim")
            rid = ensure_player(reviver) if reviver else ""
            vid = ensure_player(victim)
            vx, vy, vz = _loc_xyz(victim)
            rx, ry, rz = _loc_xyz(reviver) if reviver else (vx, vy, vz)
            if vid:
                player_state[vid] = STATE_ALIVE
                append_track_point(vid, t_rel, vx, vy, 10.0, STATE_ALIVE, z=vz, force=True)
            if rid and not players[rid]["is_squad"]:
                players[rid]["revives"] += 1
            events.append(
                {
                    "t": round(t_rel, 1),
                    "type": "revive",
                    "attacker_id": rid,
                    "attacker_name": players[rid]["display_name"] if rid else "队友",
                    "attacker_team_id": players[rid]["team_id"] if rid else 0,
                    "victim_id": vid,
                    "victim_name": players[vid]["display_name"] if vid else "未知",
                    "victim_team_id": players[vid]["team_id"] if vid else 0,
                    "weapon": "救援",
                    "weapon_raw": "Revive",
                    "distance": 0.0,
                    "is_headshot": False,
                    "is_team_kill": False,
                    "ax": rx,
                    "ay": ry,
                    "az": rz,
                    "vx": vx,
                    "vy": vy,
                    "vz": vz,
                }
            )

        elif ev_type in ("LogPlayerKillV2", "LogPlayerKill"):
            victim = ev.get("victim")
            if ev_type == "LogPlayerKillV2":
                killer = ev.get("killer") or ev.get("finisher")
                dmg_info = ev.get("killerDamageInfo") or ev.get("finishDamageInfo") or {}
                weapon_raw = str(dmg_info.get("damageCauserName") or "")
                dmg_reason = str(dmg_info.get("damageReason") or "")
                dist_m = float(dmg_info.get("distance") or 0.0) / 100.0
            else:
                killer = ev.get("killer")
                weapon_raw = str(ev.get("damageCauserName") or "")
                dmg_reason = str(ev.get("damageReason") or "")
                dist_m = float(ev.get("distance") or 0.0) / 100.0

            vid = ensure_player(victim)
            kid = ensure_player(killer) if killer else ""
            vx, vy, vz = _loc_xyz(victim)
            kx, ky, kz = _loc_xyz(killer) if killer else (vx, vy, vz)
            if dist_m <= 0 and killer and victim:
                dist_m = math.hypot(kx - vx, ky - vy) / 100.0

            weapon_name = resolve_weapon_name(weapon_raw)
            if vid:
                player_state[vid] = STATE_DEAD
                append_track_point(vid, t_rel, vx, vy, 0.0, STATE_DEAD, z=vz, force=True)
                players[vid]["time_survived"] = round(t_rel, 1)
                v_rank = int((victim or {}).get("ranking") or 0)
                if v_rank > 0 and not players[vid].get("win_place"):
                    players[vid]["win_place"] = v_rank
            a_team = players[kid]["team_id"] if kid else 0
            v_team = players[vid]["team_id"] if vid else 0
            is_tk = bool(kid and vid and kid != vid and a_team and a_team == v_team)
            if kid and kid != vid:
                _get_wstat(kid, weapon_name)["kills"] += 1
                if not players[kid]["is_squad"]:
                    players[kid]["kills"] += 1
                    if dmg_reason == "HeadShot":
                        players[kid]["headshots"] += 1
                if is_tk:
                    players[kid]["team_kills"] += 1

            detail = _summarize_fight(vid, kid, t_rel, weapon_name)
            events.append(
                {
                    "t": round(t_rel, 1),
                    "type": "kill",
                    "attacker_id": kid,
                    "attacker_name": players[kid]["display_name"] if kid else "环境/毒圈",
                    "attacker_team_id": a_team,
                    "victim_id": vid,
                    "victim_name": players[vid]["display_name"] if vid else "未知",
                    "victim_team_id": v_team,
                    "weapon": weapon_name,
                    "weapon_raw": weapon_raw,
                    "distance": round(dist_m, 1),
                    "is_headshot": dmg_reason == "HeadShot",
                    "is_team_kill": is_tk,
                    "ax": kx,
                    "ay": ky,
                    "az": kz,
                    "vx": vx,
                    "vy": vy,
                    "vz": vz,
                    "detail": detail,
                }
            )

        elif ev_type == "LogMatchEnd":
            for ch_wrapper in ev.get("characters") or []:
                char = ch_wrapper.get("character") if isinstance(ch_wrapper, dict) and "character" in ch_wrapper else ch_wrapper
                if not isinstance(char, dict):
                    continue
                aid = ensure_player(char)
                rank_val = int(char.get("ranking") or 0)
                if aid and rank_val > 0:
                    players[aid]["win_place"] = rank_val
            winner_names = [p["display_name"] for p in players.values() if p.get("win_place") == 1]
            if winner_names:
                events.append(
                    {
                        "t": round(t_rel, 1),
                        "type": "chicken",
                        "attacker_id": "",
                        "attacker_name": ", ".join(winner_names[:4]),
                        "attacker_team_id": 0,
                        "victim_id": "",
                        "victim_name": "",
                        "victim_team_id": 0,
                        "weapon": "吃鸡",
                        "weapon_raw": "WinnerWinnerChickenDinner",
                        "distance": 0.0,
                        "is_headshot": False,
                        "is_team_kill": False,
                        "ax": 0.0,
                        "ay": 0.0,
                        "vx": 0.0,
                        "vy": 0.0,
                    }
                )

    del raw_events

    # Finalize per-player peak gear, weapon loadouts, and team metadata table
    teams: dict[str, dict[str, Any]] = {}
    for aid, pdata in players.items():
        pg = peak_gear.get(aid)
        if pg:
            pdata["gear"] = [pg["helmet"], pg["vest"], pg["backpack"]]
        w_list = []
        for wname, st in weapon_stats.get(aid, {}).items():
            w_list.append(
                {
                    "name": wname,
                    "damage": round(st["damage"], 1),
                    "hits": st["hits"],
                    "headshots": st["headshots"],
                    "knocks": st["knocks"],
                    "kills": st["kills"],
                    "attachments": sorted(weapon_attachments.get(aid, {}).get(wname, set())),
                }
            )
        w_list.sort(key=lambda w: (-w["kills"], -w["damage"]))
        pdata["weapons"] = w_list[:8]

        tid_key = str(pdata.get("team_id") or 0)
        if tid_key != "0":
            t_entry = teams.setdefault(
                tid_key,
                {
                    "team_id": pdata["team_id"],
                    "color": get_team_color(pdata["team_id"]),
                    "is_squad": False,
                    "rank": 0,
                    "members": [],
                },
            )
            if pdata.get("is_squad"):
                t_entry["is_squad"] = True
            p_rank = int(pdata.get("win_place") or 0)
            if p_rank > 0 and (t_entry["rank"] == 0 or p_rank < t_entry["rank"]):
                t_entry["rank"] = p_rank
            t_entry["members"].append(aid)

    # Derive fallback team ranks from survival time for any team missing LogMatchEnd ranking,
    # and propagate team rank to all members whose win_place is still 0.
    unranked_teams: list[tuple[float, str]] = []
    used_ranks = {int(t["rank"]) for t in teams.values() if int(t.get("rank") or 0) > 0}
    for tid_key, t_entry in teams.items():
        if int(t_entry.get("rank") or 0) <= 0:
            max_surv = max(
                (float(players[mid].get("time_survived") or 0.0) for mid in t_entry["members"] if mid in players),
                default=0.0,
            )
            unranked_teams.append((max_surv, tid_key))
    if unranked_teams:
        unranked_teams.sort(key=lambda item: -item[0])
        next_rank = 1
        for _, tid_key in unranked_teams:
            while next_rank in used_ranks:
                next_rank += 1
            teams[tid_key]["rank"] = next_rank
            used_ranks.add(next_rank)
            next_rank += 1

    for tid_key, t_entry in teams.items():
        t_rank = int(t_entry.get("rank") or 0)
        if t_rank > 0:
            for mid in t_entry["members"]:
                if mid in players and int(players[mid].get("win_place") or 0) <= 0:
                    players[mid]["win_place"] = t_rank

    plane: list[list[float]] = []
    if len(aircraft_points) >= 2:
        plane = [
            [round(aircraft_points[0][0], 1), round(aircraft_points[0][1], 1)],
            [round(aircraft_points[-1][0], 1), round(aircraft_points[-1][1], 1)],
        ]

    events.sort(key=lambda e: e["t"])

    replay_data: dict[str, Any] = {
        "version": REPLAY_SCHEMA_VERSION,
        "match_id": match_summary.match_id,
        "platform": match_summary.platform,
        "map_id": map_info.map_id,
        "map_name": map_info.display_name,
        "map_size": map_info.size_cm,
        "map_image": map_info.image_file,
        "terrain_image": map_info.terrain_file,
        "heightmap_image": map_info.heightmap_file,
        "game_mode": match_summary.game_mode_display,
        "created_at": match_summary.created_at,
        "duration": round(max_t, 1),
        "plane": plane,
        "zones": zones,
        "teams": teams,
        "players": players,
        "tracks": tracks,
        "events": events,
    }

    compact_json = json.dumps(replay_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    gzip_bytes = gzip.compress(compact_json, compresslevel=6)
    return replay_data, gzip_bytes


async def parse_telemetry_to_replay_gzip(
    raw_telemetry_bytes: bytes,
    match_summary: MatchSummary,
    squad_members: list[SquadMember],
    sample_interval_sec: float = 1.5,
) -> tuple[dict[str, Any], bytes]:
    """Async wrapper running Telemetry parsing & compression in a worker thread."""
    return await asyncio.to_thread(
        build_replay_sync,
        raw_telemetry_bytes,
        match_summary,
        squad_members,
        sample_interval_sec,
    )
