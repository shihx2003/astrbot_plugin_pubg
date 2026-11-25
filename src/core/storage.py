"""Async SQLite cache repository and LRU replay storage for SquadLens."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite
from astrbot.api import logger

from .models import MatchSummary


class SquadStorage:
    """SQLite cache + compressed replay disk store with automatic LRU eviction.

    `data_dir` MUST be injected externally (via `StarTools.get_data_dir()` in AstrBot,
    or `tmp_path` in pytest).
    """

    def __init__(self, data_dir: Path, max_cached_replays: int = 200) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "pubg_cache.db"
        self.replays_dir = self.data_dir / "replays"
        self.max_cached_replays = max(1, int(max_cached_replays))
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """Create directories, open SQLite connection, and ensure schema tables exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.replays_dir.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute("PRAGMA synchronous=NORMAL;")
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS players (
                platform TEXT NOT NULL,
                ign_lower TEXT NOT NULL,
                ign TEXT NOT NULL,
                account_id TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (platform, ign_lower)
            );

            CREATE INDEX IF NOT EXISTS idx_players_account_id ON players(account_id);

            CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                created_at TEXT NOT NULL,
                duration INTEGER NOT NULL,
                map_name TEXT NOT NULL,
                game_mode TEXT NOT NULL,
                telemetry_url TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                fetched_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_matches_created_at ON matches(created_at DESC);

            CREATE TABLE IF NOT EXISTS player_matches (
                account_id TEXT NOT NULL,
                match_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (account_id, match_id)
            );

            CREATE INDEX IF NOT EXISTS idx_pm_account_created
                ON player_matches(account_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS replays (
                match_id TEXT PRIMARY KEY,
                file_name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                created_at REAL NOT NULL,
                last_accessed_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_replays_lru ON replays(last_accessed_at ASC);

            CREATE TABLE IF NOT EXISTS kv_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SquadStorage is not initialized. Call initialize() first.")
        return self._conn

    # ── Player Account ID Cache ────────────────────────────────────────────────

    async def upsert_player(self, platform: str, ign: str, account_id: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            """
            INSERT INTO players (platform, ign_lower, ign, account_id, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(platform, ign_lower) DO UPDATE SET
                ign = excluded.ign,
                account_id = excluded.account_id,
                updated_at = excluded.updated_at
            """,
            (platform.lower(), ign.lower(), ign, account_id, time.time()),
        )
        await conn.commit()

    async def get_player_account_id(self, platform: str, ign: str) -> str | None:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT account_id FROM players WHERE platform = ? AND ign_lower = ?",
            (platform.lower(), ign.lower()),
        ) as cur:
            row = await cur.fetchone()
            return str(row["account_id"]) if row else None

    # ── Match Cache ────────────────────────────────────────────────────────────

    async def save_match(self, summary: MatchSummary, participant_account_ids: list[str] | None = None) -> None:
        conn = self._require_conn()
        summary_json = json.dumps(summary.to_dict(), ensure_ascii=False)
        await conn.execute(
            """
            INSERT INTO matches (
                match_id, platform, created_at, duration, map_name,
                game_mode, telemetry_url, summary_json, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                summary_json = excluded.summary_json,
                telemetry_url = excluded.telemetry_url,
                fetched_at = excluded.fetched_at
            """,
            (
                summary.match_id,
                summary.platform,
                summary.created_at,
                summary.duration,
                summary.map_name,
                summary.game_mode,
                summary.telemetry_url,
                summary_json,
                time.time(),
            ),
        )
        ids = set(participant_account_ids or [])
        for sp in summary.squad_players:
            if sp.account_id:
                ids.add(sp.account_id)
        for aid in ids:
            await conn.execute(
                """
                INSERT OR REPLACE INTO player_matches (account_id, match_id, created_at)
                VALUES (?, ?, ?)
                """,
                (aid, summary.match_id, summary.created_at),
            )
        await conn.commit()

    async def get_match(self, match_id: str) -> MatchSummary | None:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT summary_json FROM matches WHERE match_id = ?",
            (match_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return MatchSummary.from_dict(json.loads(row["summary_json"]))

    async def get_recent_matches_for_accounts(
        self,
        account_ids: list[str],
        limit: int = 10,
    ) -> list[MatchSummary]:
        clean_ids = [aid for aid in account_ids if aid]
        if not clean_ids:
            return []
        conn = self._require_conn()
        placeholders = ",".join("?" for _ in clean_ids)
        query = f"""
            SELECT DISTINCT m.summary_json, m.created_at
            FROM matches m
            INNER JOIN player_matches pm ON m.match_id = pm.match_id
            WHERE pm.account_id IN ({placeholders})
            ORDER BY m.created_at DESC
            LIMIT ?
        """
        async with conn.execute(query, (*clean_ids, int(limit))) as cur:
            rows = await cur.fetchall()
            return [MatchSummary.from_dict(json.loads(r["summary_json"])) for r in rows]

    # ── Compressed Replay Cache with LRU Eviction ──────────────────────────────

    def _safe_replay_filename(self, match_id: str) -> str:
        safe = "".join(ch for ch in match_id if ch.isalnum() or ch in ("-", "_"))
        return f"{safe}.replay.json.gz"

    async def save_replay_gzip(self, match_id: str, gzip_bytes: bytes) -> Path:
        conn = self._require_conn()
        file_name = self._safe_replay_filename(match_id)
        file_path = self.replays_dir / file_name
        tmp_path = self.replays_dir / f"{file_name}.tmp"
        tmp_path.write_bytes(gzip_bytes)
        tmp_path.replace(file_path)

        now = time.time()
        await conn.execute(
            """
            INSERT INTO replays (match_id, file_name, size_bytes, created_at, last_accessed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                file_name = excluded.file_name,
                size_bytes = excluded.size_bytes,
                last_accessed_at = excluded.last_accessed_at
            """,
            (match_id, file_name, len(gzip_bytes), now, now),
        )
        await conn.commit()
        await self.evict_lru_replays()
        return file_path

    async def get_replay_gzip(self, match_id: str) -> bytes | None:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT file_name FROM replays WHERE match_id = ?",
            (match_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None

        file_path = self.replays_dir / str(row["file_name"])
        if not file_path.is_file():
            await conn.execute("DELETE FROM replays WHERE match_id = ?", (match_id,))
            await conn.commit()
            return None

        data = file_path.read_bytes()
        await conn.execute(
            "UPDATE replays SET last_accessed_at = ? WHERE match_id = ?",
            (time.time(), match_id),
        )
        await conn.commit()
        return data

    async def has_replay(self, match_id: str) -> bool:
        conn = self._require_conn()
        async with conn.execute(
            "SELECT file_name FROM replays WHERE match_id = ?",
            (match_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return False
            return (self.replays_dir / str(row["file_name"])).is_file()

    async def get_cached_replay_ids(self, match_ids: list[str] | None = None) -> set[str]:
        """Return match_ids that have an intact cached replay file on disk."""
        conn = self._require_conn()
        if match_ids is not None:
            clean = [mid for mid in match_ids if mid]
            if not clean:
                return set()
            placeholders = ",".join("?" for _ in clean)
            sql = f"SELECT match_id, file_name FROM replays WHERE match_id IN ({placeholders})"
            params: tuple[Any, ...] = tuple(clean)
        else:
            sql = "SELECT match_id, file_name FROM replays"
            params = ()

        cached: set[str] = set()
        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        for row in rows:
            if (self.replays_dir / str(row["file_name"])).is_file():
                cached.add(str(row["match_id"]))
        return cached

    async def list_cached_replays(self) -> list[dict[str, Any]]:
        """List all intact cached replay files metadata."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT match_id, file_name, size_bytes, created_at, last_accessed_at "
            "FROM replays ORDER BY created_at DESC"
        ) as cur:
            rows = await cur.fetchall()

        items: list[dict[str, Any]] = []
        for row in rows:
            fpath = self.replays_dir / str(row["file_name"])
            if fpath.is_file():
                items.append(
                    {
                        "match_id": str(row["match_id"]),
                        "file_name": str(row["file_name"]),
                        "file_path": fpath,
                        "size_bytes": int(row["size_bytes"] or 0),
                        "created_at": float(row["created_at"] or 0.0),
                        "last_accessed_at": float(row["last_accessed_at"] or 0.0),
                    }
                )
        return items

    async def list_all_cached_matches(self, limit: int = 200) -> list[MatchSummary]:
        """List cached match summaries ordered by newest first (for backup export)."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT summary_json FROM matches ORDER BY created_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ) as cur:
            rows = await cur.fetchall()
        return [MatchSummary.from_dict(json.loads(r["summary_json"])) for r in rows]

    async def clear_replays(self) -> dict[str, int]:
        """Delete all cached replay files and clear the replays table."""
        conn = self._require_conn()
        async with conn.execute("SELECT file_name, size_bytes FROM replays") as cur:
            rows = await cur.fetchall()

        removed = 0
        freed_bytes = 0
        seen_names: set[str] = set()
        for row in rows:
            fname = str(row["file_name"])
            seen_names.add(fname)
            fpath = self.replays_dir / fname
            try:
                if fpath.is_file():
                    freed_bytes += fpath.stat().st_size
                    fpath.unlink()
                    removed += 1
            except OSError as exc:
                logger.warning("Failed to remove replay file %s: %s", fpath, exc)

        # Also clean up any orphaned .replay.json.gz / .tmp files in replays_dir
        if self.replays_dir.is_dir():
            for fpath in self.replays_dir.iterdir():
                if fpath.is_file() and fpath.name not in seen_names and (
                    fpath.name.endswith(".replay.json.gz") or fpath.name.endswith(".tmp")
                ):
                    try:
                        freed_bytes += fpath.stat().st_size
                        fpath.unlink()
                        if fpath.name.endswith(".replay.json.gz"):
                            removed += 1
                    except OSError as exc:
                        logger.warning("Failed to remove orphaned replay file %s: %s", fpath, exc)

        await conn.execute("DELETE FROM replays")
        await conn.commit()
        logger.info("Cleared %d cached replay file(s) (%d bytes freed)", removed, freed_bytes)
        return {"removed_replays": removed, "removed_matches": 0, "freed_bytes": freed_bytes}

    async def clear_matches_and_replays(self) -> dict[str, int]:
        """Clear cached matches and replay files while preserving player IDs and bot kv_state."""
        res = await self.clear_replays()
        conn = self._require_conn()
        async with conn.execute("SELECT COUNT(*) AS cnt FROM matches") as cur:
            row = await cur.fetchone()
            match_cnt = int(row["cnt"]) if row else 0
        await conn.execute("DELETE FROM player_matches")
        await conn.execute("DELETE FROM matches")
        await conn.commit()
        res["removed_matches"] = match_cnt
        logger.info(
            "Cleared %d cached match(es) and %d replay(s) (%d bytes freed)",
            match_cnt,
            res["removed_replays"],
            res["freed_bytes"],
        )
        return res

    async def evict_lru_replays(self) -> int:
        """Delete oldest-accessed cached replay files exceeding max_cached_replays."""
        conn = self._require_conn()
        async with conn.execute("SELECT COUNT(*) AS cnt FROM replays") as cur:
            row = await cur.fetchone()
            total = int(row["cnt"]) if row else 0

        overflow = total - self.max_cached_replays
        if overflow <= 0:
            return 0

        async with conn.execute(
            "SELECT match_id, file_name FROM replays ORDER BY last_accessed_at ASC LIMIT ?",
            (overflow,),
        ) as cur:
            victims = await cur.fetchall()

        removed = 0
        for v in victims:
            mid = str(v["match_id"])
            fpath = self.replays_dir / str(v["file_name"])
            try:
                if fpath.is_file():
                    fpath.unlink()
            except OSError as exc:
                logger.warning("Failed to remove evicted replay %s: %s", fpath, exc)
            await conn.execute("DELETE FROM replays WHERE match_id = ?", (mid,))
            removed += 1

        await conn.commit()
        logger.info("LRU evicted %d cached replay(s) (limit=%d)", removed, self.max_cached_replays)
        return removed

    async def get_cache_stats(self) -> dict[str, Any]:
        conn = self._require_conn()
        async with conn.execute("SELECT COUNT(*) AS cnt, COALESCE(SUM(size_bytes), 0) AS total_bytes FROM replays") as cur:
            r = await cur.fetchone()
        async with conn.execute("SELECT COUNT(*) AS cnt FROM matches") as cur:
            m = await cur.fetchone()
        return {
            "cached_matches": int(m["cnt"]) if m else 0,
            "cached_replays": int(r["cnt"]) if r else 0,
            "replays_total_bytes": int(r["total_bytes"]) if r else 0,
            "max_cached_replays": self.max_cached_replays,
        }

    # ── KV State (Watermark & Bound Broadcast Sessions) ────────────────────────

    async def get_state(self, key: str, default: str | None = None) -> str | None:
        conn = self._require_conn()
        async with conn.execute("SELECT value FROM kv_state WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return str(row["value"]) if row else default

    async def set_state(self, key: str, value: str) -> None:
        conn = self._require_conn()
        await conn.execute(
            "INSERT INTO kv_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await conn.commit()
