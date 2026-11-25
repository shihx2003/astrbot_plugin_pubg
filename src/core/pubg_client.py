"""Async PUBG Official Developer API client with 10 RPM rate limiting and CDN control."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

PUBG_API_BASE_URL = "https://api.pubg.com"
MAX_REQUESTS_PER_MINUTE = 10
DEFAULT_MIN_INTERVAL_SECONDS = 6.2
DEFAULT_TELEMETRY_CONCURRENCY = 2


class PubgApiError(Exception):
    """Error raised when PUBG Official API fails."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


class AsyncRateLimiter:
    """Global async sliding-window + spacing rate limiter (default <= 10 RPM)."""

    def __init__(
        self,
        max_requests: int = MAX_REQUESTS_PER_MINUTE,
        period_seconds: float = 60.0,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        clock: Any = None,
        sleeper: Any = None,
    ) -> None:
        self.max_requests = max(1, int(max_requests))
        self.period_seconds = float(period_seconds)
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._clock = clock or time.monotonic
        self._sleeper = sleeper or asyncio.sleep
        self._lock = asyncio.Lock()
        self._timestamps: deque[float] = deque()
        self._blocked_until: float = 0.0

    def penalize(self, wait_seconds: float) -> None:
        """Push back next allowed request time (e.g., after HTTP 429)."""
        if wait_seconds <= 0:
            return
        target = self._clock() + float(wait_seconds)
        if target > self._blocked_until:
            self._blocked_until = target

    async def acquire(self) -> None:
        """Wait until a request slot is available under both RPM and spacing bounds."""
        async with self._lock:
            while True:
                now = self._clock()
                # Drop timestamps outside sliding window
                while self._timestamps and (now - self._timestamps[0]) >= self.period_seconds:
                    self._timestamps.popleft()

                wait_for = 0.0
                if self._blocked_until > now:
                    wait_for = max(wait_for, self._blocked_until - now)

                if len(self._timestamps) >= self.max_requests:
                    window_wait = self.period_seconds - (now - self._timestamps[0]) + 0.05
                    wait_for = max(wait_for, window_wait)

                if self._timestamps and self.min_interval_seconds > 0:
                    spacing_wait = self.min_interval_seconds - (now - self._timestamps[-1])
                    wait_for = max(wait_for, spacing_wait)

                if wait_for > 0:
                    logger.debug("PUBG API rate limiter sleeping %.2fs", wait_for)
                    await self._sleeper(wait_for)
                    continue

                self._timestamps.append(self._clock())
                return


class PubgApiClient:
    """Async PUBG API client with built-in 10 RPM protection & Telemetry CDN semaphore."""

    def __init__(
        self,
        api_key: str,
        *,
        rate_limiter: AsyncRateLimiter | None = None,
        telemetry_concurrency: int = DEFAULT_TELEMETRY_CONCURRENCY,
        timeout_seconds: float = 30.0,
        session: aiohttp.ClientSession | None = None,
        max_retries: int = 3,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.rate_limiter = rate_limiter or AsyncRateLimiter()
        self._telemetry_sem = asyncio.Semaphore(max(1, int(telemetry_concurrency)))
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._external_session = session is not None
        self._session = session
        self._max_retries = max(1, int(max_retries))

    def update_api_key(self, api_key: str) -> None:
        self.api_key = (api_key or "").strip()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
            self._external_session = False
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._external_session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            raise PubgApiError("尚未配置 PUBG API Key，请在 AstrBot 插件设置中填写 pubg_api_key。", 401)
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/vnd.api+json",
        }

    async def _request_json(self, path: str, *, use_rate_limit: bool = True) -> dict[str, Any]:
        url = f"{PUBG_API_BASE_URL}{path}"
        headers = self._auth_headers()
        session = await self._ensure_session()

        for attempt in range(1, self._max_retries + 1):
            if use_rate_limit:
                await self.rate_limiter.acquire()

            try:
                async with session.get(url, headers=headers) as resp:
                    status = resp.status
                    if status == 200:
                        return await resp.json()

                    if status == 429:
                        reset_hdr = resp.headers.get("X-RateLimit-Reset")
                        retry_after = 2.0 ** attempt * 3.0
                        if reset_hdr:
                            try:
                                reset_ts = float(reset_hdr)
                                delta = reset_ts - time.time()
                                if 0 < delta <= 120:
                                    retry_after = delta + 0.5
                            except ValueError:
                                pass
                        logger.warning(
                            "PUBG API 429 Too Many Requests on %s (attempt %d/%d), backing off %.1fs",
                            path,
                            attempt,
                            self._max_retries,
                            retry_after,
                        )
                        self.rate_limiter.penalize(retry_after)
                        if attempt < self._max_retries:
                            await asyncio.sleep(retry_after)
                            continue
                        raise PubgApiError("PUBG API 触发频率限制 (429)，请稍后再试。", 429)

                    body_text = await resp.text()
                    if status == 401:
                        raise PubgApiError("PUBG API Key 无效或未授权 (401)。", 401)
                    if status == 404:
                        raise PubgApiError(f"未找到目标玩家或对局记录 (404): {path}", 404)
                    if 500 <= status < 600 and attempt < self._max_retries:
                        backoff = 1.5 * attempt
                        logger.warning("PUBG API %d on %s, retrying in %.1fs", status, path, backoff)
                        await asyncio.sleep(backoff)
                        continue
                    raise PubgApiError(f"PUBG API 请求失败 ({status}): {body_text[:200]}", status)
            except aiohttp.ClientError as exc:
                if attempt < self._max_retries:
                    await asyncio.sleep(1.5 * attempt)
                    continue
                raise PubgApiError(f"网络请求 PUBG API 失败: {exc}") from exc

        raise PubgApiError("PUBG API 请求超过最大重试次数。")

    @staticmethod
    def _parse_players_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        players: list[dict[str, Any]] = []
        for item in payload.get("data", []):
            attrs = item.get("attributes") or {}
            rel_matches = ((item.get("relationships") or {}).get("matches") or {}).get("data") or []
            match_ids = [str(m["id"]) for m in rel_matches if isinstance(m, dict) and m.get("id")]
            players.append(
                {
                    "account_id": str(item.get("id") or ""),
                    "name": str(attrs.get("name") or ""),
                    "shard_id": str(attrs.get("shardId") or ""),
                    "match_ids": match_ids,
                }
            )
        return players

    async def fetch_players_by_names(self, platform: str, names: list[str]) -> list[dict[str, Any]]:
        """Resolve up to N player IGNs in chunks of 10 (10 RPM protected)."""
        clean_names = [n.strip() for n in names if n and n.strip()]
        if not clean_names:
            return []
        results: list[dict[str, Any]] = []
        for i in range(0, len(clean_names), 10):
            chunk = clean_names[i : i + 10]
            encoded = ",".join(quote(n, safe="") for n in chunk)
            payload = await self._request_json(
                f"/shards/{platform}/players?filter[playerNames]={encoded}",
                use_rate_limit=True,
            )
            results.extend(self._parse_players_payload(payload))
        return results

    async def fetch_players_by_ids(self, platform: str, account_ids: list[str]) -> list[dict[str, Any]]:
        """Query players by cached account_id in chunks of 10 (10 RPM protected)."""
        clean_ids = [aid.strip() for aid in account_ids if aid and aid.strip()]
        if not clean_ids:
            return []
        results: list[dict[str, Any]] = []
        for i in range(0, len(clean_ids), 10):
            chunk = clean_ids[i : i + 10]
            encoded = ",".join(quote(aid, safe="") for aid in chunk)
            payload = await self._request_json(
                f"/shards/{platform}/players?filter[playerIds]={encoded}",
                use_rate_limit=True,
            )
            results.extend(self._parse_players_payload(payload))
        return results

    async def fetch_match(self, platform: str, match_id: str) -> dict[str, Any]:
        """Fetch full match object. Official /matches endpoint does not consume 10 RPM player quota."""
        return await self._request_json(
            f"/shards/{platform}/matches/{quote(match_id, safe='')}",
            use_rate_limit=False,
        )

    async def download_telemetry_bytes(self, telemetry_url: str) -> bytes:
        """Download raw Telemetry JSON (possibly gzipped) from PUBG CDN with concurrency cap."""
        if not telemetry_url:
            raise PubgApiError("该对局缺少官方 Telemetry 数据地址。", 404)
        session = await self._ensure_session()
        headers = {"Accept-Encoding": "gzip"}
        async with self._telemetry_sem:
            for attempt in range(1, self._max_retries + 1):
                try:
                    async with session.get(telemetry_url, headers=headers) as resp:
                        if resp.status == 200:
                            # aiohttp transparently decompresses gzip when auto_decompress=True,
                            # returning clean JSON bytes; build_replay_sync handles both plain & gzip.
                            return await resp.read()
                        if resp.status >= 500 and attempt < self._max_retries:
                            await asyncio.sleep(1.5 * attempt)
                            continue
                        raise PubgApiError(f"下载 Telemetry 失败 ({resp.status})", resp.status)
                except aiohttp.ClientError as exc:
                    if attempt < self._max_retries:
                        await asyncio.sleep(1.5 * attempt)
                        continue
                    raise PubgApiError(f"下载 Telemetry 网络异常: {exc}") from exc
        raise PubgApiError("下载 Telemetry 失败。")
