/**
 * Relative-path REST API client for SquadLens.
 * Strictly uses `./api/...` relative URLs for seamless FRP + Nginx HTTPS subpath compatibility.
 */

const ACCESS_CODE_KEY = "squadlens_access_code";

export function getSavedAccessCode() {
  try {
    return sessionStorage.getItem(ACCESS_CODE_KEY) || "";
  } catch {
    return "";
  }
}

export function setSavedAccessCode(code) {
  try {
    if (code) {
      sessionStorage.setItem(ACCESS_CODE_KEY, code);
    } else {
      sessionStorage.removeItem(ACCESS_CODE_KEY);
    }
  } catch {
    // Ignore storage quota errors in private mode
  }
}

async function apiFetch(relUrl, options = {}) {
  const headers = new Headers(options.headers || {});
  const code = getSavedAccessCode();
  if (code) {
    headers.set("X-Access-Code", code);
  }
  const resp = await fetch(relUrl, {
    credentials: "same-origin",
    ...options,
    headers,
  });
  let payload = null;
  try {
    payload = await resp.json();
  } catch {
    payload = {};
  }
  if (!resp.ok) {
    const err = new Error(payload?.error || `请求失败 (${resp.status})`);
    err.status = resp.status;
    err.authRequired = Boolean(payload?.auth_required);
    throw err;
  }
  return payload;
}

export function fetchAuthStatus() {
  return apiFetch("./api/auth/status");
}

export async function verifyAccessCode(code) {
  const res = await apiFetch("./api/auth/verify", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  setSavedAccessCode(code);
  return res;
}

export function fetchSquad() {
  return apiFetch("./api/squad");
}

export function fetch3DConfig() {
  return apiFetch("./api/3d/config");
}

export function fetchRecentMatches(limit = 10, refresh = true) {
  const q = new URLSearchParams({
    limit: String(limit),
    refresh: refresh ? "1" : "0",
  });
  return apiFetch(`./api/matches?${q.toString()}`);
}

export function fetchMatchDetail(matchId, platform = "") {
  const q = platform ? `?platform=${encodeURIComponent(platform)}` : "";
  return apiFetch(`./api/matches/${encodeURIComponent(matchId)}${q}`);
}

export function fetchMatchReplay(matchId, platform = "") {
  const q = platform ? `?platform=${encodeURIComponent(platform)}` : "";
  return apiFetch(`./api/matches/${encodeURIComponent(matchId)}/replay${q}`);
}

export function searchPlayerMatches(ign, platform = "steam", limit = 5) {
  const q = new URLSearchParams({
    ign,
    platform,
    limit: String(limit),
  });
  return apiFetch(`./api/players/search?${q.toString()}`);
}

export function clearServerCache(scope = "replays") {
  return apiFetch("./api/cache/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
}

function parseDownloadFilename(contentDisposition, fallback) {
  if (!contentDisposition) return fallback;
  const match = /filename\*=UTF-8''([^;]+)|filename="([^"]+)"|filename=([^;\s]+)/i.exec(contentDisposition);
  if (!match) return fallback;
  try {
    return decodeURIComponent(match[1] || match[2] || match[3] || fallback);
  } catch {
    return match[2] || match[3] || fallback;
  }
}

export async function downloadBinary(relUrl, fallbackFilename = "download.bin") {
  const headers = new Headers();
  const code = getSavedAccessCode();
  if (code) {
    headers.set("X-Access-Code", code);
  }
  const resp = await fetch(relUrl, {
    credentials: "same-origin",
    headers,
  });
  if (!resp.ok) {
    let payload = {};
    try {
      payload = await resp.json();
    } catch {
      payload = {};
    }
    const err = new Error(payload?.error || `下载失败 (${resp.status})`);
    err.status = resp.status;
    err.authRequired = Boolean(payload?.auth_required);
    throw err;
  }
  const blob = await resp.blob();
  const filename = parseDownloadFilename(resp.headers.get("Content-Disposition"), fallbackFilename);
  const blobUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(blobUrl), 3000);
  return { filename, size: blob.size };
}

export function downloadMatchReplay(matchId, platform = "", format = "json") {
  const q = new URLSearchParams();
  if (platform) q.set("platform", platform);
  if (format && format !== "json") q.set("format", format);
  const qs = q.toString() ? `?${q.toString()}` : "";
  const ext = format === "gz" ? "replay.json.gz" : "replay.json";
  return downloadBinary(
    `./api/matches/${encodeURIComponent(matchId)}/download${qs}`,
    `squadlens_${matchId.slice(0, 8)}.${ext}`
  );
}

export function downloadCacheBackupZip() {
  return downloadBinary("./api/cache/export", "squadlens_backup.zip");
}
