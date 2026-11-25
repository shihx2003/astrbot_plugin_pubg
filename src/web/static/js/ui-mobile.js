/**
 * Mobile-First UI controller for SquadLens Dashboard + 2D Tactical Replay (v2).
 * Includes:
 * - Default / Terrain map switcher
 * - PUBG esports-style killfeed with team number badges & colored team categorization
 * - XiaoHeiHe-style expandable knock/kill detail panel (gear, weapon attachments, body hit-zone SVG, assists)
 * - Clickable player names everywhere opening the Player Match Inspector Modal
 */

import {
  clearServerCache,
  downloadCacheBackupZip,
  downloadMatchReplay,
  fetch3DConfig,
  fetchAuthStatus,
  fetchMatchDetail,
  fetchMatchReplay,
  fetchRecentMatches,
  fetchSquad,
  searchPlayerMatches,
  verifyAccessCode,
} from "./api.js";
import { getPlayerVisualColor, getTeamColor } from "./map-renderer.js";
import { ReplayController, formatClock } from "./replay-controller.js";

const $ = (id) => document.getElementById(id);

const state = {
  squad: [],
  matches: [],
  cacheStats: { cached_matches: 0, cached_replays: 0, replays_total_bytes: 0, max_cached_replays: 200 },
  enable3d: true,
  matchLimit: 10,
  resultFilter: "all",
  mapFilter: "all",
  isPlayerSearch: false,
  currentMatch: null,
  currentReplay: null,
  config3d: null,
  show3dBuildings: true,
  eventFilter: "all",
  playerFilter: "",
  trackSort: "rank",
  expandedEvents: new Set(),
  modalPlayerId: null,
};

const SVG_KNOCK = `<svg class="kf-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="17.5" cy="7" r="2.2"/><path d="M5 17h4.2l2.5-4.2 3.1 1.4-1.4 2.8H19v2H5v-2zm7.2-5.5 1.8-3 2.5 1.1-1.3 2.7-3-.8z"/></svg>`;
const SVG_KILL = `<svg class="kf-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M18.3 5.71 12 12l6.3 6.29-1.41 1.42L10.59 13.4 4.29 19.71 2.88 18.29 9.17 12 2.88 5.71 4.29 4.29l6.3 6.3 6.29-6.3z"/></svg>`;
const SVG_HEADSHOT = `<svg class="kf-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.2" fill="currentColor"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/></svg>`;
const SVG_REVIVE = `<svg class="kf-icon" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M10 3h4v7h7v4h-7v7h-4v-7H3v-4h7z"/></svg>`;

const controller = new ReplayController($("replay-canvas"), {
  onStateChange: syncReplayUI,
  onTimeTick: (t, dur) => {
    $("timeline-slider").value = String(t);
    $("time-current").textContent = formatClock(t);
    $("time-total").textContent = formatClock(dur);
  },
  onFrame: (aliveCount) => {
    if (typeof aliveCount === "number") {
      $("hud-alive-badge").textContent = `存活 ${aliveCount}`;
    }
  },
});

function formatTimeCN(iso) {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch {
    return iso;
  }
}

function escapeHtml(str) {
  return String(str || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderGearPills(gear) {
  const [h = 0, v = 0, b = 0] = Array.isArray(gear) ? gear : [0, 0, 0];
  const pill = (label, lv) =>
    `<span class="gear-pill ${lv ? `lv${lv}` : ""}">${label}${lv ? ` Lv${lv}` : " 无"}</span>`;
  return `${pill("头", h)}${pill("甲", v)}${pill("包", b)}`;
}

function renderBodySilhouetteSvg(hitZones) {
  const hz = hitZones || {};
  const fillFor = (key) => {
    const info = hz[key];
    if (!info || !info.dmg) return "#334155";
    if (key === "head") return "#ef4444";
    if (info.dmg >= 50) return "#f97316";
    return "#f5c518";
  };
  const legendParts = [];
  const zh = { head: "头", torso: "躯干", pelvis: "骨盆", arm: "臂", leg: "腿", other: "其他" };
  for (const [k, v] of Object.entries(hz)) {
    if (v?.dmg) legendParts.push(`${zh[k] || k}:${Math.round(v.dmg)}`);
  }
  return `
    <div class="body-silhouette-wrap">
      <svg class="body-svg" viewBox="0 0 80 130" aria-label="受击部位">
        <circle cx="40" cy="18" r="11" fill="${fillFor("head")}" />
        <rect x="26" y="33" width="28" height="32" rx="6" fill="${fillFor("torso")}" />
        <rect x="28" y="67" width="24" height="14" rx="4" fill="${fillFor("pelvis")}" />
        <rect x="12" y="35" width="11" height="38" rx="5" fill="${fillFor("arm")}" />
        <rect x="57" y="35" width="11" height="38" rx="5" fill="${fillFor("arm")}" />
        <rect x="27" y="83" width="11" height="42" rx="5" fill="${fillFor("leg")}" />
        <rect x="42" y="83" width="11" height="42" rx="5" fill="${fillFor("leg")}" />
      </svg>
      <div class="zone-legend">${legendParts.length ? legendParts.join(" ") : "无直接命中"}</div>
    </div>`;
}

function showToast(msg, type = "info", durationMs = 2800) {
  const host = $("toast-container");
  if (!host || !msg) return;
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => {
    el.style.opacity = "0";
    el.style.transition = "opacity 0.2s ease";
    setTimeout(() => el.remove(), 220);
  }, durationMs);
}

function formatBytes(bytes) {
  const n = Math.max(0, Number(bytes || 0));
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

function isErangelMapName(mapName, mapDisplay) {
  const a = String(mapName || "").toLowerCase();
  const b = String(mapDisplay || "").toLowerCase();
  return a.includes("baltic") || a.includes("erangel") || b.includes("erangel") || b.includes("艾伦格");
}

function buildMatchShareUrl(matchId) {
  try {
    return new URL(`./match/${encodeURIComponent(matchId)}`, window.location.href).href;
  } catch {
    return `./match/${encodeURIComponent(matchId)}`;
  }
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  document.execCommand("copy");
  ta.remove();
}

function updateCacheBadges(cache) {
  if (cache) {
    state.cacheStats = {
      cached_matches: Number(cache.cached_matches || 0),
      cached_replays: Number(cache.cached_replays || 0),
      replays_total_bytes: Number(cache.replays_total_bytes || 0),
      max_cached_replays: Number(cache.max_cached_replays || 200),
    };
  }
  const c = state.cacheStats;
  const sizeTxt = formatBytes(c.replays_total_bytes);
  $("cache-badge").textContent = `缓存 ${c.cached_replays}/${c.max_cached_replays} · ${sizeTxt} ⚙`;
  if ($("tm-cached-matches")) {
    $("tm-cached-matches").textContent = String(c.cached_matches);
    $("tm-cached-replays").textContent = `${c.cached_replays} / ${c.max_cached_replays}`;
    $("tm-cached-size").textContent = sizeTxt;
  }
}

function showStatus(msg) {
  const el = $("dashboard-status");
  if (!msg) {
    el.classList.add("hidden");
    el.textContent = "";
    return;
  }
  el.textContent = msg;
  el.classList.remove("hidden");
}

function renderSquadChips() {
  const box = $("squad-chips");
  if (!state.squad.length) {
    box.innerHTML = `<div class="subtle-pill">尚未配置固定小队成员，请在 AstrBot WebUI 中填写 squad_members</div>`;
    return;
  }
  // Aggregate recent stats per squad member from loaded matches (deduplicated per match)
  const memberStats = new Map();
  if (!state.isPlayerSearch) {
    for (const m of state.matches) {
      const seenInMatch = new Set();
      for (const p of m.squad_players || []) {
        const key = String(p.name || "").toLowerCase();
        if (!key || seenInMatch.has(key)) continue;
        seenInMatch.add(key);
        const cur = memberStats.get(key) || { games: 0, kills: 0, dmg: 0 };
        cur.games += 1;
        cur.kills += Number(p.kills || 0);
        cur.dmg += Number(p.damage_dealt || 0);
        memberStats.set(key, cur);
      }
    }
  }

  const slotDefaults = ["#F5C518", "#FF8C1A", "#2EA8FF", "#3CD070"];
  box.innerHTML = state.squad
    .map((m, idx) => {
      const displayName = m.display_name || m.ign;
      const showIgnPrefix = displayName.toLowerCase() !== String(m.ign || "").toLowerCase();
      const st = memberStats.get(String(m.ign || "").toLowerCase());
      const subText =
        st && st.games > 0
          ? `${showIgnPrefix ? `${m.ign} · ` : ""}均伤 ${Math.round(st.dmg / st.games)} · ${st.kills}杀 (${st.games}场)`
          : `${showIgnPrefix ? `${m.ign} · ` : ""}${m.platform} · 点查战绩`;
      return `
      <div class="squad-chip" role="button" tabindex="0" data-squad-ign="${escapeHtml(m.ign)}" data-squad-platform="${escapeHtml(m.platform || "steam")}" title="点击查看 ${escapeHtml(m.ign)} 的近期战绩">
        <span class="color-dot" style="background:${escapeHtml(m.color || slotDefaults[idx % 4])}"></span>
        <div class="squad-chip-text">
          <span class="squad-chip-name">${escapeHtml(displayName)}</span>
          <span class="squad-chip-sub">${escapeHtml(subText)}</span>
        </div>
      </div>`;
    })
    .join("");

  box.querySelectorAll(".squad-chip").forEach((chip) => {
    const trigger = () => {
      const ign = chip.dataset.squadIgn;
      const plat = chip.dataset.squadPlatform || "steam";
      if (!ign) return;
      $("search-ign").value = ign;
      $("search-platform").value = plat;
      runPlayerSearch(ign, plat);
    };
    chip.addEventListener("click", trigger);
    chip.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        trigger();
      }
    });
  });
}

function getFilteredMatches() {
  return state.matches.filter((m) => {
    if (state.resultFilter === "chicken" && !m.is_chicken_dinner) return false;
    if (state.resultFilter === "top5" && (!m.squad_win_place || m.squad_win_place > 5)) return false;
    if (state.resultFilter === "cached" && !m.is_replay_cached) return false;
    if (state.mapFilter !== "all" && (m.map_display_name || m.map_name) !== state.mapFilter) return false;
    return true;
  });
}

function syncMapFilterOptions() {
  const sel = $("filter-map-select");
  if (!sel) return;
  const maps = [...new Set(state.matches.map((m) => m.map_display_name || m.map_name).filter(Boolean))];
  const prev = state.mapFilter;
  sel.innerHTML =
    `<option value="all">全部地图 (${maps.length})</option>` +
    maps.map((mp) => `<option value="${escapeHtml(mp)}">${escapeHtml(mp)}</option>`).join("");
  if (maps.includes(prev)) {
    sel.value = prev;
  } else {
    state.mapFilter = "all";
    sel.value = "all";
  }
  $("matches-filter-bar").classList.toggle("hidden", state.matches.length === 0);
}

function renderSquadKpi(matches) {
  const bar = $("squad-kpi-bar");
  if (!bar) return;
  if (!matches || matches.length === 0) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    return;
  }
  const n = matches.length;
  let wins = 0;
  let top5 = 0;
  let rankSum = 0;
  let rankCnt = 0;
  let killsSum = 0;
  let dmgSum = 0;
  const playerDmg = new Map();

  for (const m of matches) {
    if (m.is_chicken_dinner) wins += 1;
    if (m.squad_win_place && m.squad_win_place <= 5) top5 += 1;
    if (m.squad_win_place) {
      rankSum += Number(m.squad_win_place);
      rankCnt += 1;
    }
    killsSum += Number(m.squad_total_kills || 0);
    dmgSum += Number(m.squad_total_damage || 0);
    for (const p of m.squad_players || []) {
      const label = p.display_name || p.name;
      if (!label) continue;
      const cur = playerDmg.get(label) || { dmg: 0, kills: 0, games: 0 };
      cur.dmg += Number(p.damage_dealt || 0);
      cur.kills += Number(p.kills || 0);
      cur.games += 1;
      playerDmg.set(label, cur);
    }
  }

  let mvpName = "--";
  let mvpSub = "暂无统计";
  let bestDmg = -1;
  for (const [name, st] of playerDmg.entries()) {
    if (st.dmg > bestDmg) {
      bestDmg = st.dmg;
      mvpName = name;
      mvpSub = `总伤 ${Math.round(st.dmg)} · ${st.kills} 杀`;
    }
  }

  const winRate = Math.round((wins / n) * 100);
  const top5Rate = Math.round((top5 / n) * 100);
  const avgRank = rankCnt ? `#${(rankSum / rankCnt).toFixed(1)}` : "--";
  const avgKills = (killsSum / n).toFixed(1);
  const avgDmg = Math.round(dmgSum / n);

  bar.innerHTML = `
    <div class="kpi-card ${wins > 0 ? "accent" : ""}">
      <span class="kpi-label">🍗 吃鸡 / 胜率</span>
      <span class="kpi-value">${wins} 场 · ${winRate}%</span>
      <span class="kpi-sub">前五率 ${top5Rate}% (${top5}/${n})</span>
    </div>
    <div class="kpi-card">
      <span class="kpi-label">🏆 场均排名</span>
      <span class="kpi-value">${avgRank}</span>
      <span class="kpi-sub">统计近 ${n} 场对局</span>
    </div>
    <div class="kpi-card">
      <span class="kpi-label">🔫 小队场均击杀</span>
      <span class="kpi-value">${avgKills}</span>
      <span class="kpi-sub">合计 ${killsSum} 击杀</span>
    </div>
    <div class="kpi-card">
      <span class="kpi-label">💥 小队场均伤害</span>
      <span class="kpi-value">${avgDmg}</span>
      <span class="kpi-sub">合计 ${Math.round(dmgSum)} 伤害</span>
    </div>
    <div class="kpi-card kpi-mvp">
      <div class="kpi-mvp-left">
        <span class="kpi-label">🔥 近期伤害王</span>
        <span class="kpi-value">${escapeHtml(mvpName)}</span>
      </div>
      <span class="kpi-sub">${escapeHtml(mvpSub)}</span>
    </div>
  `;
  bar.classList.remove("hidden");
}

function renderMatchesList() {
  const list = $("matches-list");
  const filtered = getFilteredMatches();
  renderSquadKpi(filtered.length ? filtered : state.matches);

  const hintEl = $("filter-count-hint");
  if (hintEl) {
    hintEl.textContent =
      filtered.length === state.matches.length
        ? `共 ${state.matches.length} 场`
        : `筛选 ${filtered.length} / ${state.matches.length} 场`;
  }

  if (!state.matches.length) {
    list.innerHTML = `<div class="status-banner">暂无近期对局数据。请确认已配置有效的 PUBG API Key 和小队成员 IGN，或点击右上角「刷新战绩」。</div>`;
    return;
  }

  if (!filtered.length) {
    list.innerHTML = `<div class="status-banner">当前筛选条件下暂无匹配对局，请切换上方筛选标签或地图。</div>`;
    return;
  }

  list.innerHTML = filtered
    .map((m) => {
      const rankHtml = m.is_chicken_dinner
        ? `<span class="rank-badge chicken">🍗 吃鸡 #1</span>`
        : `<span class="rank-badge">#${m.squad_win_place || "?"}</span>`;
      const cachedTag = m.is_replay_cached
        ? `<span class="mini-tag cached" title="回放已缓存在本地，点开秒进">⚡ 已缓存</span>`
        : "";
      const tag3d =
        state.enable3d && isErangelMapName(m.map_name, m.map_display_name)
          ? `<span class="mini-tag tag-3d" title="该地图支持 3D 地形回放">3D</span>`
          : "";
      const tkCount = (m.highlights?.team_damage_events || []).length;
      const tkTag = tkCount > 0 ? `<span class="mini-tag tk" title="本局出现友伤事件">💥 友伤</span>` : "";

      const playersHtml = (m.squad_players || [])
        .map(
          (p) => `
          <div class="squad-mini-row">
            <div class="squad-mini-left">
              ${p.squad_slot ? `<span class="slot-badge" style="background:${escapeHtml(p.color || "#F5C518")}">${p.squad_slot}</span>` : `<span class="color-dot" style="background:${escapeHtml(p.color || "#9ca3af")}"></span>`}
              <span class="squad-mini-name">${escapeHtml(p.display_name || p.name)}</span>
            </div>
            <span class="squad-mini-stats">杀 ${p.kills} · 倒 ${p.dbnos} · 伤 ${Math.round(p.damage_dealt || 0)}</span>
          </div>`
        )
        .join("");

      const hl = m.highlights || {};
      const hlText = hl.top_damage_player
        ? `💥 伤害王: ${escapeHtml(hl.top_damage_player)} (${Math.round(hl.top_damage || 0)})`
        : "";

      const tagsHtml = `${cachedTag}${tag3d}${tkTag}`;
      return `
        <article class="match-card ${m.is_chicken_dinner ? "chicken" : ""}">
          <div class="match-card-top">
            <div class="match-map-wrap">
              <div class="match-map">${escapeHtml(m.map_display_name || m.map_name)}</div>
              ${tagsHtml ? `<div class="match-tags-row">${tagsHtml}</div>` : ""}
            </div>
            ${rankHtml}
          </div>
          <div class="match-meta-row">
            <span>${escapeHtml(m.game_mode_display || m.game_mode)}</span>
            <span>🕒 ${escapeHtml(formatTimeCN(m.created_at))}</span>
            <span>⏱ ${formatClock(m.duration)}</span>
            <span>🔥 ${m.squad_total_kills} 杀 / ${Math.round(m.squad_total_damage || 0)} 伤</span>
          </div>
          <div class="squad-mini-table">${playersHtml}</div>
          <div class="match-card-footer">
            <span class="highlight-pill">${hlText}</span>
            <div class="match-card-actions">
              <button class="card-icon-btn btn-share-match" data-match-id="${escapeHtml(m.match_id)}" title="复制本局回放直达链接" aria-label="复制本局回放链接">🔗</button>
              <button class="card-icon-btn btn-download-match" data-match-id="${escapeHtml(m.match_id)}" data-platform="${escapeHtml(m.platform || "steam")}" title="下载本局回放数据包 (.json)" aria-label="下载本局回放">↓</button>
              <button class="btn-primary btn-open-replay" data-match-id="${escapeHtml(m.match_id)}" data-platform="${escapeHtml(m.platform || "steam")}">
                🗺️ 战术回放
              </button>
            </div>
          </div>
        </article>`;
    })
    .join("");

  list.querySelectorAll(".btn-open-replay").forEach((btn) => {
    btn.addEventListener("click", () => {
      openReplay(btn.dataset.matchId, btn.dataset.platform);
    });
  });
  list.querySelectorAll(".btn-share-match").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const shareUrl = buildMatchShareUrl(btn.dataset.matchId);
      try {
        await copyText(shareUrl);
        showToast("已复制本局回放直达链接，可直接发群里打开", "success");
      } catch {
        showToast(`复制失败，请手动复制：${shareUrl}`, "error", 4500);
      }
    });
  });
  list.querySelectorAll(".btn-download-match").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mid = btn.dataset.matchId;
      const plat = btn.dataset.platform;
      btn.disabled = true;
      btn.textContent = "…";
      try {
        const res = await downloadMatchReplay(mid, plat, "json");
        showToast(`已下载回放 ${res.filename} (${formatBytes(res.size)})`, "success");
        // Refresh cached status silently
        const target = state.matches.find((x) => x.match_id === mid);
        if (target) target.is_replay_cached = true;
        fetchSquad().then((sq) => updateCacheBadges(sq.cache)).catch(() => {});
        renderMatchesList();
      } catch (err) {
        if (err.status === 401 || err.authRequired) {
          openAuthModal();
          return;
        }
        showToast(`下载失败：${err.message}`, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "↓";
      }
    });
  });
}

async function loadDashboard(refresh = false) {
  showStatus(refresh ? "正在从 PUBG 官方接口同步最新战绩..." : "正在加载小队与近期对局...");
  const refreshBtn = $("btn-refresh-matches");
  if (refreshBtn) refreshBtn.disabled = true;
  try {
    const [sq, mt] = await Promise.all([
      fetchSquad(),
      fetchRecentMatches(state.matchLimit, refresh),
    ]);
    state.squad = sq.squad_members || [];
    state.enable3d = sq.enable_3d_replay !== false;
    state.isPlayerSearch = false;
    updateCacheBadges(sq.cache || {});
    if (sq.default_platform) {
      $("search-platform").value = sq.default_platform;
    }
    state.matches = mt.matches || [];
    $("matches-title").textContent = "近期对局记录";
    $("btn-reset-squad").classList.add("hidden");
    $("match-limit-select").classList.remove("hidden");
    syncMapFilterOptions();
    renderSquadChips();
    renderMatchesList();
    showStatus("");
    if (refresh) {
      showToast(`已同步最新战绩（当前展示 ${state.matches.length} 场）`, "success");
    }
  } catch (err) {
    if (err.status === 401 || err.authRequired) {
      openAuthModal();
      return;
    }
    showStatus(`加载失败：${err.message}`);
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
  }
}

function syncTopbarHeight() {
  const bar = document.querySelector(".topbar");
  if (!bar) return;
  const h = Math.ceil(bar.getBoundingClientRect().height);
  if (h > 0) {
    document.documentElement.style.setProperty("--topbar-h", `${h}px`);
    controller.resizeActive();
  }
}

async function openReplay(matchId, platform = "") {
  document.body.classList.add("in-replay");
  $("dashboard-view").classList.add("hidden");
  $("replay-view").classList.remove("hidden");
  $("btn-back-dashboard").classList.remove("hidden");
  $("replay-header-meta").classList.remove("hidden");
  syncTopbarHeight();
  $("replay-header-meta").textContent = `对局 ${matchId.slice(0, 8)}...`;
  $("replay-loading").classList.remove("hidden");
  $("replay-loading-text").textContent = "正在下载并解析官方 Telemetry 2D 回放（含装备、配件与受击部位）...";
  state.expandedEvents.clear();

  const url = new URL(window.location.href);
  url.searchParams.set("match", matchId);
  window.history.replaceState({}, "", url.toString());

  try {
    const [detailRes, replay, cfg3d] = await Promise.all([
      fetchMatchDetail(matchId, platform).catch(() => ({ match: null })),
      fetchMatchReplay(matchId, platform),
      fetch3DConfig().catch(() => ({ enabled: false })),
    ]);
    state.currentMatch = detailRes.match;
    state.currentReplay = replay;
    state.config3d = cfg3d;
    $("replay-header-meta").textContent = `${replay.map_name} · ${replay.game_mode}`;
    $("timeline-slider").max = String(replay.duration || 100);
    $("time-total").textContent = formatClock(replay.duration || 0);

    // Show 2D/3D switcher on both PC & mobile when console enable_3d_replay=true AND map is Erangel
    const mapIdLower = String(replay.map_id || "").toLowerCase();
    const mapNameLower = String(replay.map_name || "").toLowerCase();
    const isErangel =
      (Array.isArray(cfg3d?.supported_maps) && cfg3d.supported_maps.includes(replay.map_id)) ||
      mapIdLower.includes("baltic") ||
      mapIdLower.includes("erangel") ||
      mapNameLower.includes("erangel") ||
      mapNameLower.includes("艾伦格");
    const can3D = Boolean(cfg3d?.enabled && isErangel);
    $("hud-dim-switch").classList.toggle("hidden", !can3D);
    $("replay-3d-host").classList.add("hidden");
    $("replay-canvas").classList.remove("hidden");
    $("hud-3d-status").classList.add("hidden");

    // Enable/disable Terrain button based on map availability
    const terBtn = $("btn-layer-terrain");
    if (replay.terrain_image) {
      terBtn.disabled = false;
      terBtn.title = "切换为高清地形图";
    } else {
      terBtn.disabled = true;
      terBtn.title = "当前地图暂未配置地形图（可将地形图放入 static/maps/terrain/ 目录自动启用）";
    }

    controller.load(replay);
    renderTrackLists();
    renderEventsList();
    renderScoreboard();
    $("replay-loading").classList.add("hidden");

    // Mark match as cached locally and update cache stats badge
    const matched = state.matches.find((x) => x.match_id === matchId);
    if (matched && !matched.is_replay_cached) {
      matched.is_replay_cached = true;
      renderMatchesList();
    }
    fetchSquad().then((sq) => updateCacheBadges(sq.cache)).catch(() => {});
  } catch (err) {
    if (err.status === 401 || err.authRequired) {
      openAuthModal();
      return;
    }
    $("replay-loading-text").textContent = `加载回放失败：${err.message}`;
  }
}

function closeReplay() {
  document.body.classList.remove("in-replay");
  controller.destroy();
  closePlayerModal();
  $("replay-view").classList.add("hidden");
  $("dashboard-view").classList.remove("hidden");
  $("btn-back-dashboard").classList.add("hidden");
  $("replay-header-meta").classList.add("hidden");
  $("replay-drawer").classList.remove("open");
  syncTopbarHeight();
  const url = new URL(window.location.href);
  url.searchParams.delete("match");
  window.history.replaceState({}, "", url.toString());
}

function syncReplayUI(ui) {
  $("btn-play-pause").textContent = ui.playing ? "⏸" : "▶";
  $("btn-speed").textContent = `${ui.speed}x`;
  $("timeline-slider").value = String(ui.currentTime);
  $("time-current").textContent = formatClock(ui.currentTime);
  $("time-total").textContent = formatClock(ui.duration);

  $("btn-layer-default").classList.toggle("active", ui.mapLayer !== "terrain");
  $("btn-layer-terrain").classList.toggle("active", ui.mapLayer === "terrain");
  $("btn-dim-2d").classList.toggle("active", ui.dimMode !== "3d");
  $("btn-dim-3d").classList.toggle("active", ui.dimMode === "3d");

  const hintEl = document.querySelector(".touch-hint");
  if (hintEl) {
    hintEl.textContent =
      ui.dimMode === "3d"
        ? "左键/单指/WASD 平移 · 右键/双指旋转俯仰 · 滚轮指向缩放"
        : "单指拖拽 · 双指缩放 · 点选地图玩家追踪虚线轨迹";
  }

  if (ui.followId && state.currentReplay?.players?.[ui.followId]) {
    const p = state.currentReplay.players[ui.followId];
    $("hud-follow-text").textContent = `跟随: ${p.display_name || p.name}`;
    $("hud-follow-badge").classList.remove("hidden");
  } else {
    $("hud-follow-badge").classList.add("hidden");
  }
  renderTrackLists();
}

function getPlayerFinalRank(p, replay) {
  if (!p) return 0;
  const direct = Number(p.win_place || 0);
  if (direct > 0) return direct;
  const teamRank = Number(replay?.teams?.[String(p.team_id || 0)]?.rank || 0);
  if (teamRank > 0) return teamRank;
  return 0;
}

function renderTrackLists() {
  const replay = state.currentReplay;
  if (!replay) return;
  const players = Object.values(replay.players || {});
  const squad = players
    .filter((p) => p.is_squad)
    .sort((a, b) => (a.squad_slot || 99) - (b.squad_slot || 99));
  const keyword = state.playerFilter.trim().toLowerCase();
  const cleanRankKw = keyword.replace(/^#/, "");
  const others = players
    .filter((p) => !p.is_squad)
    .filter((p) => {
      if (!keyword) return true;
      const rank = getPlayerFinalRank(p, replay);
      return (
        String(p.name || "").toLowerCase().includes(keyword) ||
        String(p.team_id || "").includes(cleanRankKw) ||
        (rank > 0 && String(rank) === cleanRankKw)
      );
    })
    .sort((a, b) => {
      if (state.trackSort === "rank") {
        const ra = getPlayerFinalRank(a, replay) || 999;
        const rb = getPlayerFinalRank(b, replay) || 999;
        if (ra !== rb) return ra - rb;
        const ta = a.team_id || 999;
        const tb = b.team_id || 999;
        if (ta !== tb) return ta - tb;
        if ((b.kills || 0) !== (a.kills || 0)) return (b.kills || 0) - (a.kills || 0);
        return (b.damage || 0) - (a.damage || 0);
      }
      // Sort by team_id ascending
      const ta = a.team_id || 999;
      const tb = b.team_id || 999;
      if (ta !== tb) return ta - tb;
      if ((b.kills || 0) !== (a.kills || 0)) return (b.kills || 0) - (a.kills || 0);
      return (b.damage || 0) - (a.damage || 0);
    });

  const renderRow = (p) => {
    const tracked = controller.trackedIds.has(p.account_id);
    const following = controller.followId === p.account_id;
    const color = getPlayerVisualColor(p, replay);
    const teamColor = getTeamColor(p.team_id, replay);
    const rank = getPlayerFinalRank(p, replay);
    const badgeHtml = p.is_squad
      ? `<span class="slot-badge" style="background:${escapeHtml(color)}">${p.squad_slot || "?"}号</span>`
      : `<span class="team-badge" style="background:${escapeHtml(teamColor)}" title="队伍 #${p.team_id || "?"}">${p.team_id || "?"}</span>`;

    let rankHtml = "";
    if (rank === 1) {
      rankHtml = `<span class="rank-inline win">🍗 第1名</span>`;
    } else if (rank > 1 && rank <= 3) {
      rankHtml = `<span class="rank-inline top3">第${rank}名</span>`;
    } else if (rank > 0) {
      rankHtml = `<span class="rank-inline">第${rank}名</span>`;
    }
    const subParts = [];
    if (rankHtml) subParts.push(rankHtml);
    else if (p.is_squad && p.team_id) subParts.push(`队#${p.team_id}`);
    subParts.push(`杀 ${p.kills || 0}`);
    subParts.push(`伤 ${Math.round(p.damage || 0)}`);

    return `
      <div class="player-row ${tracked ? "tracked" : ""} ${rank === 1 ? "rank-winner" : ""}">
        <div class="player-row-main" data-track-aid="${escapeHtml(p.account_id)}">
          <input type="checkbox" ${tracked ? "checked" : ""} aria-label="追踪路线" />
          ${badgeHtml}
          <div class="player-row-meta">
            <div class="player-row-name">
              <span class="clickable-name" data-inspect-aid="${escapeHtml(p.account_id)}" style="color:${p.is_squad ? escapeHtml(color) : "#eef2f8"}">${escapeHtml(p.display_name || p.name)}</span>
            </div>
            <div class="player-row-sub">${subParts.join(" · ")}</div>
          </div>
        </div>
        <div class="row-actions">
          ${!p.is_squad && p.team_id ? `<button class="follow-btn" data-track-team="${p.team_id}" title="追踪整队">整队</button>` : ""}
          <button class="follow-btn ${following ? "active" : ""}" data-follow-aid="${escapeHtml(p.account_id)}">
            ${following ? "跟随中" : "跟随"}
          </button>
        </div>
      </div>`;
  };

  $("squad-track-list").innerHTML = squad.length
    ? squad.map(renderRow).join("")
    : `<div class="panel-hint">本局无已绑定小队成员</div>`;
  $("other-track-list").innerHTML = others.slice(0, 120).map(renderRow).join("");

  const bindRowHandlers = (container) => {
    container.querySelectorAll("[data-track-aid]").forEach((el) => {
      el.addEventListener("click", () => controller.toggleTrack(el.dataset.trackAid));
    });
    container.querySelectorAll("[data-inspect-aid]").forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        openPlayerModal(el.dataset.inspectAid);
      });
    });
    container.querySelectorAll("[data-track-team]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        controller.trackTeam(Number(btn.dataset.trackTeam));
      });
    });
    container.querySelectorAll("[data-follow-aid]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        controller.setFollow(btn.dataset.followAid);
      });
    });
  };
  bindRowHandlers($("squad-track-list"));
  bindRowHandlers($("other-track-list"));
}

function renderKillfeedDetailHtml(ev) {
  const d = ev.detail;
  if (!d) return "";
  const atts = d.attachments?.length
    ? d.attachments.map((a) => `<span class="att-chip">${escapeHtml(a)}</span>`).join("")
    : `<span class="zone-legend">无配件记录</span>`;
  const assistsText = d.assists?.length
    ? d.assists.map((a) => `${escapeHtml(a.name)}(${Math.round(a.damage)})`).join("、")
    : "-";
  return `
    <div class="kf-detail">
      <div class="kf-detail-col">
        <div class="kf-detail-label">攻击方装备</div>
        <div class="gear-row">${renderGearPills(d.attacker_gear)}</div>
        <div class="kf-detail-label">${escapeHtml(ev.weapon)} 配件</div>
        <div class="att-chips">${atts}</div>
      </div>
      ${renderBodySilhouetteSvg(d.hit_zones)}
      <div class="kf-detail-col">
        <div class="kf-detail-label">受击方装备</div>
        <div class="gear-row">${renderGearPills(d.victim_gear)}</div>
        <div class="kf-detail-label">伤害明细</div>
        <div class="dmg-kv"><span>距离</span><strong>${ev.distance ? `${Math.round(ev.distance)}m` : "-"}</strong></div>
        <div class="dmg-kv"><span>武器伤害</span><strong>${d.weapon_damage ? Math.round(d.weapon_damage) : "-"}</strong></div>
        <div class="dmg-kv"><span>环境伤害</span><strong>${d.env_damage ? Math.round(d.env_damage) : "-"}</strong></div>
        <div class="dmg-kv"><span>助攻</span><strong>${assistsText}</strong></div>
      </div>
    </div>`;
}

function renderEventsList() {
  const replay = state.currentReplay;
  if (!replay) return;
  const players = replay.players || {};
  const squadIds = new Set(
    Object.values(players)
      .filter((p) => p.is_squad)
      .map((p) => p.account_id)
  );

  const evs = (replay.events || []).filter((ev) => {
    if (state.eventFilter === "tk") {
      return ev.type === "team_damage" || ev.is_team_kill;
    }
    if (state.eventFilter === "squad") {
      if (ev.type === "chicken") return true;
      return squadIds.size === 0 || squadIds.has(ev.attacker_id) || squadIds.has(ev.victim_id);
    }
    return ev.type === "kill" || ev.type === "knock" || ev.type === "revive" || ev.type === "chicken";
  });

  const container = $("events-list");
  if (!evs.length) {
    container.innerHTML = `<div class="panel-hint">当前筛选下暂无事件记录</div>`;
    return;
  }

  const teamBadge = (aid, rawTeamId, side) => {
    const p = players[aid];
    const tid = p?.team_id || rawTeamId || 0;
    if (!tid) return "";
    const bg = p?.is_squad ? getPlayerVisualColor(p, replay) : getTeamColor(tid, replay);
    return `<span class="team-badge" style="background:${escapeHtml(bg)}" title="队伍 #${tid}">${tid}</span>`;
  };

  const nameSpan = (aid, fallbackName) => {
    const p = players[aid];
    const label = p ? p.display_name || p.name : fallbackName || "环境";
    if (!aid || !p) return `<span class="kf-pname">${escapeHtml(label)}</span>`;
    const color = p.is_squad ? getPlayerVisualColor(p, replay) : "#eef2f8";
    return `<span class="kf-pname clickable-name" data-inspect-aid="${escapeHtml(aid)}" style="color:${escapeHtml(color)}">${escapeHtml(label)}</span>`;
  };

  container.innerHTML = evs
    .map((ev, idx) => {
      const isSquadEv = squadIds.has(ev.attacker_id) || squadIds.has(ev.victim_id);
      const cls = ev.is_team_kill || ev.type === "team_damage" ? "tk-ev" : isSquadEv ? "squad-ev" : "";
      const evKey = `${ev.t}-${ev.type}-${ev.attacker_id}-${ev.victim_id}`;
      const isExpanded = state.expandedEvents.has(evKey);
      const hasDetail = Boolean(ev.detail);

      if (ev.type === "chicken") {
        return `
          <div class="kf-card squad-ev ev-chicken">
            <div class="kf-row" data-ev-idx="${idx}">
              <span class="kf-time">${formatClock(ev.t)}</span>
              <div class="kf-chicken-banner">🍗 <strong>${escapeHtml(ev.attacker_name)}</strong> 赢得本局吃鸡</div>
              <span class="kf-expand-spacer" aria-hidden="true"></span>
            </div>
          </div>`;
      }

      const actionCls = ev.is_team_kill || ev.type === "team_damage" ? "tk" : ev.type;
      const iconHtml =
        ev.type === "knock"
          ? SVG_KNOCK
          : ev.type === "revive"
            ? SVG_REVIVE
            : SVG_KILL;
      const hsHtml = ev.is_headshot ? SVG_HEADSHOT : "";
      const distStr = ev.distance ? ` ${Math.round(ev.distance)}m` : "";
      const hsCls = ev.is_headshot ? " ev-hs" : "";

      return `
        <div class="kf-card ${cls} ev-${actionCls}${hsCls}">
          <div class="kf-row" data-ev-idx="${idx}">
            <span class="kf-time">${formatClock(ev.t)}</span>
            <div class="kf-main">
              <span class="kf-player left" title="${escapeHtml(ev.attacker_name)}">
                ${teamBadge(ev.attacker_id, ev.attacker_team_id, "left")}
                ${nameSpan(ev.attacker_id, ev.attacker_name)}
              </span>
              <span class="kf-action ${actionCls}${ev.is_headshot ? " hs" : ""}" title="${escapeHtml(ev.weapon)}${distStr}">
                <span class="kf-weapon-label">${escapeHtml(ev.weapon)}</span>
                <span class="kf-icons">${iconHtml}${hsHtml}</span>
              </span>
              <span class="kf-player right" title="${escapeHtml(ev.victim_name)}">
                ${nameSpan(ev.victim_id, ev.victim_name)}
                ${teamBadge(ev.victim_id, ev.victim_team_id, "right")}
              </span>
            </div>
            ${
              hasDetail
                ? `<button class="kf-expand-btn ${isExpanded ? "open" : ""}" data-expand-key="${escapeHtml(evKey)}" aria-label="展开交战详情">${isExpanded ? "▴" : "▾"}</button>`
                : `<span class="kf-expand-spacer" aria-hidden="true"></span>`
            }
          </div>
          ${isExpanded && hasDetail ? renderKillfeedDetailHtml(ev) : ""}
        </div>`;
    })
    .join("");

  container.querySelectorAll(".kf-row").forEach((row) => {
    row.addEventListener("click", () => {
      const ev = evs[Number(row.dataset.evIdx)];
      controller.jumpToEvent(ev);
      $("replay-drawer").classList.remove("open");
    });
  });

  container.querySelectorAll("[data-expand-key]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const key = btn.dataset.expandKey;
      if (state.expandedEvents.has(key)) state.expandedEvents.delete(key);
      else state.expandedEvents.add(key);
      renderEventsList();
    });
  });

  container.querySelectorAll("[data-inspect-aid]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      openPlayerModal(el.dataset.inspectAid);
    });
  });
}

function renderScoreboard() {
  const m = state.currentMatch;
  const hlBox = $("replay-highlights");
  const sb = $("replay-scoreboard");
  if (!m) {
    hlBox.innerHTML = "暂无单局摘要统计";
    sb.innerHTML = "";
    return;
  }
  const hl = m.highlights || {};
  const lines = [];
  if (hl.top_damage_player) lines.push(`💥 全场伤害王：${escapeHtml(hl.top_damage_player)} (${Math.round(hl.top_damage || 0)})`);
  if (hl.fastest_death_player && !m.is_chicken_dinner) {
    lines.push(`📦 成盒最快奖：${escapeHtml(hl.fastest_death_player)} (${formatClock(hl.fastest_death_sec)})`);
  }
  if (hl.team_damage_events?.length) {
    lines.push(`💣 友伤事件：${hl.team_damage_events.length} 次`);
  }
  hlBox.innerHTML = lines.length ? lines.join("<br>") : "本局暂无特殊高光事件";

  sb.innerHTML = (m.squad_players || [])
    .map(
      (p) => `
      <div class="player-row">
        <div class="player-row-meta">
          <div class="player-row-name">
            ${p.squad_slot ? `<span class="slot-badge" style="background:${escapeHtml(p.color || "#F5C518")}">${p.squad_slot}号位</span> ` : ""}
            <span class="clickable-name" data-inspect-aid="${escapeHtml(p.account_id)}" style="color:${escapeHtml(p.color || "#fff")}">${escapeHtml(p.display_name || p.name)}</span>
          </div>
          <div class="player-row-sub">
            击杀:${p.kills} · 助攻:${p.assists} · 击倒:${p.dbnos} · 伤害:${Math.round(p.damage_dealt || 0)} · 爆头:${p.headshots} · 救人:${p.revives} · 存活:${formatClock(p.time_survived)}
          </div>
        </div>
      </div>`
    )
    .join("");

  sb.querySelectorAll("[data-inspect-aid]").forEach((el) => {
    el.addEventListener("click", () => openPlayerModal(el.dataset.inspectAid));
  });
}

function openPlayerModal(accountId) {
  const replay = state.currentReplay;
  const p = replay?.players?.[accountId];
  if (!p) return;
  state.modalPlayerId = accountId;

  const teamColor = getTeamColor(p.team_id, replay);
  const pColor = getPlayerVisualColor(p, replay);

  $("pm-team-badge").textContent = `#${p.team_id || "?"}`;
  $("pm-team-badge").style.background = teamColor;

  const slotBadge = $("pm-slot-badge");
  if (p.is_squad && p.squad_slot) {
    slotBadge.textContent = `${p.squad_slot}号位`;
    slotBadge.style.background = pColor;
    slotBadge.classList.remove("hidden");
  } else {
    slotBadge.classList.add("hidden");
  }

  $("pm-name").textContent = p.display_name || p.name;
  $("pm-subtitle").textContent = `IGN: ${p.name} · 队伍 #${p.team_id || "?"} · 排名 #${p.win_place || "-"} · 存活 ${formatClock(p.time_survived)}`;

  $("pm-stats-grid").innerHTML = [
    ["击杀", p.kills || 0],
    ["击倒", p.dbnos || 0],
    ["伤害", Math.round(p.damage || 0)],
    ["爆头", p.headshots || 0],
    ["救援", p.revives || 0],
    ["友伤", Math.round(p.team_damage || 0)],
  ]
    .map(
      ([lbl, val]) => `
      <div class="pm-stat-box">
        <div class="pm-stat-val">${val}</div>
        <div class="pm-stat-lbl">${lbl}</div>
      </div>`
    )
    .join("");

  $("pm-gear-row").innerHTML = renderGearPills(p.gear);

  const weapons = p.weapons || [];
  $("pm-weapons-list").innerHTML = weapons.length
    ? weapons
        .map(
          (w) => `
          <div class="pm-weapon-card">
            <div class="pm-weapon-top">
              <span>${escapeHtml(w.name)}</span>
              <span class="pm-weapon-stat">杀 ${w.kills || 0} · 倒 ${w.knocks || 0} · 伤 ${Math.round(w.damage || 0)} · 爆头 ${w.headshots || 0}</span>
            </div>
            <div class="att-chips">
              ${w.attachments?.length ? w.attachments.map((a) => `<span class="att-chip">${escapeHtml(a)}</span>`).join("") : `<span class="zone-legend">无配件记录</span>`}
            </div>
          </div>`
        )
        .join("")
    : `<div class="panel-hint">暂无武器使用或伤害记录</div>`;

  const pEvents = (replay.events || []).filter(
    (ev) => ev.attacker_id === accountId || ev.victim_id === accountId
  );
  $("pm-events-list").innerHTML = pEvents.length
    ? pEvents
        .map(
          (ev, idx) => `
          <button class="btn-secondary full-width" style="text-align:left;padding:8px 10px" data-pm-ev-idx="${idx}">
            [${formatClock(ev.t)}] ${escapeHtml(ev.attacker_name)} [${escapeHtml(ev.weapon)}] → ${escapeHtml(ev.victim_name)}${ev.distance ? ` (${Math.round(ev.distance)}m)` : ""}
          </button>`
        )
        .join("")
    : `<div class="panel-hint">本局暂无个人交战记录</div>`;

  $("pm-events-list").querySelectorAll("[data-pm-ev-idx]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const ev = pEvents[Number(btn.dataset.pmEvIdx)];
      closePlayerModal();
      controller.jumpToEvent(ev);
      $("replay-drawer").classList.remove("open");
    });
  });

  $("pm-btn-track").textContent = controller.trackedIds.has(accountId) ? "取消追踪路线" : "追踪虚线轨迹";
  $("pm-btn-follow").textContent = controller.followId === accountId ? "取消视角跟随" : "视角跟随";
  $("player-modal").classList.remove("hidden");
}

function closePlayerModal() {
  state.modalPlayerId = null;
  $("player-modal").classList.add("hidden");
}

function openAuthModal() {
  $("auth-modal").classList.remove("hidden");
  $("auth-input").focus();
}

async function runPlayerSearch(ign, platform) {
  if (!ign) return;
  closeReplay();
  showStatus(`正在搜索玩家 ${ign} (${platform})...`);
  try {
    const res = await searchPlayerMatches(ign, platform, 8);
    state.isPlayerSearch = true;
    state.matches = res.matches || [];
    state.resultFilter = "all";
    state.mapFilter = "all";
    document.querySelectorAll("[data-res-filter]").forEach((b) => {
      b.classList.toggle("active", b.dataset.resFilter === "all");
    });
    $("matches-title").textContent = `玩家 ${res.player.name} (${res.player.platform}) 近期对局`;
    $("btn-reset-squad").classList.remove("hidden");
    $("match-limit-select").classList.add("hidden");
    syncMapFilterOptions();
    renderMatchesList();
    showStatus("");
  } catch (err) {
    if (err.status === 401 || err.authRequired) {
      openAuthModal();
      return;
    }
    showStatus(`搜索失败：${err.message}`);
  }
}

function openToolsModal(focusSection = "") {
  updateCacheBadges();
  fetchSquad().then((sq) => updateCacheBadges(sq.cache)).catch(() => {});
  $("tools-modal").classList.remove("hidden");
  const dlSec = $("tools-download-sec");
  const ccSec = $("tools-cache-sec");
  dlSec.classList.remove("pulse-highlight");
  ccSec.classList.remove("pulse-highlight");
  if (focusSection === "download") {
    dlSec.classList.add("pulse-highlight");
  } else if (focusSection === "cache") {
    ccSec.classList.add("pulse-highlight");
    ccSec.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

function closeToolsModal() {
  $("tools-modal").classList.add("hidden");
}

function triggerTextDownload(content, filename, mime = "text/plain;charset=utf-8") {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 3000);
}

function csvEscape(val) {
  const s = String(val ?? "");
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replaceAll('"', '""')}"`;
  }
  return s;
}

function exportMatchesCsv() {
  const rows = getFilteredMatches();
  if (!rows.length) {
    showToast("当前列表没有可导出的对局数据", "error");
    return;
  }
  const header = [
    "对局时间",
    "对局ID",
    "平台",
    "地图",
    "模式",
    "小队排名",
    "是否吃鸡",
    "时长(秒)",
    "小队总击杀",
    "小队总伤害",
    "成员明细(姓名:杀/倒/伤)",
    "本地已缓存回放",
  ];
  const lines = [header.map(csvEscape).join(",")];
  for (const m of rows) {
    const memberDetail = (m.squad_players || [])
      .map((p) => `${p.display_name || p.name}:${p.kills}/${p.dbnos}/${Math.round(p.damage_dealt || 0)}`)
      .join(" | ");
    lines.push(
      [
        m.created_at || "",
        m.match_id || "",
        m.platform || "",
        m.map_display_name || m.map_name || "",
        m.game_mode_display || m.game_mode || "",
        m.squad_win_place || "",
        m.is_chicken_dinner ? "是" : "否",
        m.duration || 0,
        m.squad_total_kills || 0,
        Math.round(m.squad_total_damage || 0),
        memberDetail,
        m.is_replay_cached ? "是" : "否",
      ]
        .map(csvEscape)
        .join(",")
    );
  }
  const stamp = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  triggerTextDownload(`\uFEFF${lines.join("\r\n")}`, `squadlens_matches_${stamp}.csv`, "text/csv;charset=utf-8");
  showToast(`已导出 ${rows.length} 场战绩到 CSV 表格`, "success");
}

function exportMatchesJson() {
  const rows = getFilteredMatches();
  if (!rows.length) {
    showToast("当前列表没有可导出的对局数据", "error");
    return;
  }
  const payload = {
    exported_at: new Date().toISOString(),
    count: rows.length,
    matches: rows,
  };
  const stamp = new Date().toISOString().slice(0, 10).replaceAll("-", "");
  triggerTextDownload(JSON.stringify(payload, null, 2), `squadlens_matches_${stamp}.json`, "application/json;charset=utf-8");
  showToast(`已导出 ${rows.length} 场战绩 JSON`, "success");
}

async function handleClearServerCache(scope) {
  const confirmMsg =
    scope === "matches"
      ? "确定清除全部服务端「对局记录与回放文件」缓存吗？\n（固定小队绑定和 QQ 机器人自动播报进度会保留，清理后点击刷新战绩可重新同步）"
      : "确定清除全部服务端「回放文件 (.replay.json.gz)」缓存吗？\n（对局战绩列表会保留，下次打开回放时将重新解析）";
  if (!window.confirm(confirmMsg)) return;

  const btn = scope === "matches" ? $("btn-clear-all-cache") : $("btn-clear-replays");
  btn.disabled = true;
  try {
    const res = await clearServerCache(scope);
    updateCacheBadges(res.cache || {});
    const cl = res.cleared || {};
    const freed = formatBytes(cl.freed_bytes || 0);
    if (scope === "matches") {
      showToast(`已清理 ${cl.removed_matches || 0} 场对局与 ${cl.removed_replays || 0} 份回放（释放 ${freed}）`, "success", 3600);
      closeToolsModal();
      await loadDashboard(false);
    } else {
      showToast(`已清理 ${cl.removed_replays || 0} 份回放缓存（释放 ${freed}）`, "success", 3400);
      for (const m of state.matches) m.is_replay_cached = false;
      renderMatchesList();
    }
  } catch (err) {
    if (err.status === 401 || err.authRequired) {
      openAuthModal();
      return;
    }
    showToast(`清理缓存失败：${err.message}`, "error");
  } finally {
    btn.disabled = false;
  }
}

async function handleClearBrowserCache() {
  if (!window.confirm("确定清除当前浏览器保存的界面偏好与前端缓存并强刷页面吗？")) return;
  try {
    localStorage.clear();
  } catch {
    // ignore
  }
  try {
    if ("caches" in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch {
    // ignore
  }
  showToast("浏览器本地缓存已清理，正在重新加载...", "success", 1200);
  setTimeout(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("_r", String(Date.now()));
    window.location.replace(url.toString());
  }, 350);
}

function bindUIEvents() {
  $("auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = $("auth-input").value.trim();
    $("auth-error").classList.add("hidden");
    try {
      await verifyAccessCode(code);
      $("auth-modal").classList.add("hidden");
      await bootstrapAfterAuth();
    } catch (err) {
      $("auth-error").textContent = err.message;
      $("auth-error").classList.remove("hidden");
    }
  });

  // Player modal controls
  $("pm-close").addEventListener("click", closePlayerModal);
  $("player-modal").addEventListener("click", (e) => {
    if (e.target === $("player-modal")) closePlayerModal();
  });
  $("pm-btn-track").addEventListener("click", () => {
    if (!state.modalPlayerId) return;
    controller.toggleTrack(state.modalPlayerId);
    $("pm-btn-track").textContent = controller.trackedIds.has(state.modalPlayerId) ? "取消追踪路线" : "追踪虚线轨迹";
  });
  $("pm-btn-track-team").addEventListener("click", () => {
    const p = state.currentReplay?.players?.[state.modalPlayerId];
    if (p?.team_id) controller.trackTeam(p.team_id);
  });
  $("pm-btn-follow").addEventListener("click", () => {
    if (!state.modalPlayerId) return;
    controller.setFollow(state.modalPlayerId);
    $("pm-btn-follow").textContent = controller.followId === state.modalPlayerId ? "取消视角跟随" : "视角跟随";
  });
  $("pm-btn-search").addEventListener("click", () => {
    const p = state.currentReplay?.players?.[state.modalPlayerId];
    if (!p?.name) return;
    const plat = state.currentReplay?.platform || $("search-platform").value || "steam";
    $("search-ign").value = p.name;
    $("search-platform").value = plat;
    runPlayerSearch(p.name, plat);
  });

  $("btn-refresh-matches").addEventListener("click", () => loadDashboard(true));
  $("btn-reset-squad").addEventListener("click", () => loadDashboard(false));
  $("btn-back-dashboard").addEventListener("click", closeReplay);

  // Homepage download & cache tools modal
  $("btn-open-download").addEventListener("click", () => openToolsModal("download"));
  $("btn-open-cache").addEventListener("click", () => openToolsModal("cache"));
  $("cache-badge").addEventListener("click", () => openToolsModal("cache"));
  $("tools-close").addEventListener("click", closeToolsModal);
  $("tools-modal").addEventListener("click", (e) => {
    if (e.target === $("tools-modal")) closeToolsModal();
  });

  $("btn-export-csv").addEventListener("click", exportMatchesCsv);
  $("btn-export-json").addEventListener("click", exportMatchesJson);
  $("btn-export-zip").addEventListener("click", async () => {
    const btn = $("btn-export-zip");
    btn.disabled = true;
    try {
      const res = await downloadCacheBackupZip();
      showToast(`已打包下载 ${res.filename} (${formatBytes(res.size)})`, "success", 3400);
    } catch (err) {
      if (err.status === 401 || err.authRequired) {
        openAuthModal();
        return;
      }
      showToast(`打包下载失败：${err.message}`, "error");
    } finally {
      btn.disabled = false;
    }
  });
  $("btn-clear-replays").addEventListener("click", () => handleClearServerCache("replays"));
  $("btn-clear-all-cache").addEventListener("click", () => handleClearServerCache("matches"));
  $("btn-clear-browser").addEventListener("click", handleClearBrowserCache);

  // Match filters & limit selector
  $("match-limit-select").addEventListener("change", (e) => {
    state.matchLimit = Number(e.target.value) || 10;
    loadDashboard(false);
  });
  $("filter-map-select").addEventListener("change", (e) => {
    state.mapFilter = e.target.value || "all";
    renderMatchesList();
  });
  document.querySelectorAll("[data-res-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-res-filter]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.resultFilter = btn.dataset.resFilter || "all";
      renderMatchesList();
    });
  });

  // Replay view quick share & download buttons
  $("btn-share-current-replay").addEventListener("click", async () => {
    const mid = state.currentReplay?.match_id;
    if (!mid) return;
    const shareUrl = buildMatchShareUrl(mid);
    try {
      await copyText(shareUrl);
      showToast("已复制本局回放链接", "success");
    } catch {
      showToast(`复制失败：${shareUrl}`, "error", 4500);
    }
  });
  $("btn-download-current-replay").addEventListener("click", async () => {
    const mid = state.currentReplay?.match_id;
    const plat = state.currentReplay?.platform || "";
    if (!mid) return;
    const btn = $("btn-download-current-replay");
    btn.disabled = true;
    try {
      const res = await downloadMatchReplay(mid, plat, "json");
      showToast(`已下载 ${res.filename} (${formatBytes(res.size)})`, "success");
    } catch (err) {
      showToast(`下载失败：${err.message}`, "error");
    } finally {
      btn.disabled = false;
    }
  });

  // Keyboard shortcuts (Escape closes modals; Space / [ / ] control replay playback)
  window.addEventListener("keydown", (e) => {
    const tag = document.activeElement?.tagName;
    if (e.key === "Escape") {
      if (!$("tools-modal").classList.contains("hidden")) closeToolsModal();
      if (!$("player-modal").classList.contains("hidden")) closePlayerModal();
      return;
    }
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (!document.body.classList.contains("in-replay")) return;
    if (e.code === "Space") {
      e.preventDefault();
      controller.togglePlay();
    } else if (e.key === "[") {
      e.preventDefault();
      controller.seek(Math.max(0, (controller.currentTime || 0) - 5));
    } else if (e.key === "]") {
      e.preventDefault();
      controller.seek(Math.min(controller.duration || 0, (controller.currentTime || 0) + 5));
    }
  });

  $("search-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await runPlayerSearch($("search-ign").value.trim(), $("search-platform").value);
  });

  $("btn-layer-default").addEventListener("click", () => controller.setMapLayer("default"));
  $("btn-layer-terrain").addEventListener("click", () => controller.setMapLayer("terrain"));

  $("btn-dim-2d").addEventListener("click", () => {
    $("replay-3d-host").classList.add("hidden");
    $("replay-canvas").classList.remove("hidden");
    $("hud-3d-status").classList.add("hidden");
    controller.setDimMode("2d");
  });

  $("btn-dim-3d").addEventListener("click", async () => {
    if (!state.currentReplay || !state.config3d?.enabled) return;
    $("replay-canvas").classList.add("hidden");
    $("replay-3d-host").classList.remove("hidden");
    if (!controller.renderer3d) {
      $("replay-loading").classList.remove("hidden");
      $("replay-loading-text").textContent = state.config3d?.erangel?.has_buildings
        ? "正在加载艾伦格 3D 地形与建筑白模..."
        : "正在初始化艾伦格 3D 立体地形回放...";
      try {
        const mod = await import("./renderer-3d.js?v=0.5.3");
        const r3d = new mod.Erangel3DRenderer($("replay-3d-host"), (aid) => controller.toggleTrack(aid));
        r3d.setOnUserInteract(() => {
          if (controller.followId) controller.setFollow(null);
        });
        await r3d.load(state.currentReplay, state.config3d, controller.mapLayer);
        r3d.setBuildingsVisible(state.show3dBuildings);
        controller.attach3DRenderer(r3d);
      } catch (err) {
        $("replay-3d-host").classList.add("hidden");
        $("replay-canvas").classList.remove("hidden");
        $("replay-loading-text").textContent = `3D 初始化失败：${err.message}`;
        return;
      } finally {
        $("replay-loading").classList.add("hidden");
      }
    }
    const badge = $("hud-3d-status");
    if (state.config3d?.erangel?.has_buildings) {
      badge.textContent = state.show3dBuildings ? "🏢 建筑白模: 开" : "🏢 建筑白模: 关";
      badge.disabled = false;
    } else {
      badge.textContent = "3D 地形";
      badge.disabled = true;
    }
    badge.classList.remove("hidden");
    controller.setDimMode("3d");
  });

  $("hud-3d-status").addEventListener("click", () => {
    if (!controller.renderer3d || !state.config3d?.erangel?.has_buildings) return;
    state.show3dBuildings = !state.show3dBuildings;
    controller.renderer3d.setBuildingsVisible(state.show3dBuildings);
    $("hud-3d-status").textContent = state.show3dBuildings ? "🏢 建筑白模: 开" : "🏢 建筑白模: 关";
  });

  $("btn-play-pause").addEventListener("click", () => controller.togglePlay());
  $("btn-speed").addEventListener("click", () => controller.cycleSpeed());
  $("timeline-slider").addEventListener("input", (e) => controller.seek(Number(e.target.value)));
  $("btn-zoom-in").addEventListener("click", () => controller.zoomActive(1.35));
  $("btn-zoom-out").addEventListener("click", () => controller.zoomActive(1 / 1.35));
  $("btn-reset-view").addEventListener("click", () => controller.resetActiveView());
  $("btn-stop-follow").addEventListener("click", () => controller.setFollow(null));

  $("btn-toggle-drawer").addEventListener("click", () => $("replay-drawer").classList.toggle("open"));
  window.addEventListener("resize", () => syncTopbarHeight());
  const topbarEl = document.querySelector(".topbar");
  if (topbarEl && typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => syncTopbarHeight()).observe(topbarEl);
  }

  // Desktop: horizontal drag to resize sidebar width (+ double-click reset + persist in localStorage)
  const drawer = $("replay-drawer");
  const resizer = $("drawer-resizer");
  const DRAWER_W_KEY = "squadlens_drawer_width";
  try {
    const savedW = Number(localStorage.getItem(DRAWER_W_KEY));
    if (savedW >= 340 && savedW <= 900) {
      drawer.style.setProperty("--drawer-w", `${savedW}px`);
    }
  } catch {
    // ignore storage errors
  }

  if (resizer) {
    let resizing = false;
    resizer.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      resizing = true;
      resizer.classList.add("active");
      resizer.setPointerCapture(e.pointerId);
    });
    resizer.addEventListener("pointermove", (e) => {
      if (!resizing) return;
      const maxW = Math.min(820, Math.floor(window.innerWidth * 0.68));
      const nextW = Math.max(340, Math.min(maxW, Math.round(window.innerWidth - e.clientX)));
      drawer.style.setProperty("--drawer-w", `${nextW}px`);
      controller.resizeActive();
    });
    const stopResize = (e) => {
      if (!resizing) return;
      resizing = false;
      resizer.classList.remove("active");
      try {
        resizer.releasePointerCapture(e.pointerId);
        const finalW = Math.round(drawer.getBoundingClientRect().width);
        localStorage.setItem(DRAWER_W_KEY, String(finalW));
      } catch {
        // ignore
      }
      controller.resizeActive();
    };
    resizer.addEventListener("pointerup", stopResize);
    resizer.addEventListener("pointercancel", stopResize);
    resizer.addEventListener("dblclick", () => {
      drawer.style.setProperty("--drawer-w", "468px");
      try {
        localStorage.removeItem(DRAWER_W_KEY);
      } catch {
        // ignore
      }
      controller.resizeActive();
    });
  }

  // Mobile: vertical drag on bottom drawer handle to adjust drawer height or swipe down to close
  const handle = $("drawer-handle");
  if (handle) {
    let startY = 0;
    let startH = 0;
    let moved = false;
    handle.addEventListener("pointerdown", (e) => {
      startY = e.clientY;
      startH = drawer.getBoundingClientRect().height;
      moved = false;
      drawer.classList.add("dragging");
      handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener("pointermove", (e) => {
      if (!drawer.classList.contains("dragging")) return;
      const dy = startY - e.clientY;
      if (Math.abs(dy) > 5) moved = true;
      const nextH = Math.max(160, Math.min(Math.round(window.innerHeight * 0.88), Math.round(startH + dy)));
      drawer.style.height = `${nextH}px`;
    });
    const endMobileDrag = (e) => {
      if (!drawer.classList.contains("dragging")) return;
      drawer.classList.remove("dragging");
      try {
        handle.releasePointerCapture(e.pointerId);
      } catch {
        // ignore
      }
      if (!moved) {
        drawer.classList.toggle("open");
        return;
      }
      const curH = drawer.getBoundingClientRect().height;
      if (curH < window.innerHeight * 0.24) {
        drawer.classList.remove("open");
        drawer.style.height = "";
      }
    };
    handle.addEventListener("pointerup", endMobileDrag);
    handle.addEventListener("pointercancel", endMobileDrag);
  }

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      $(`tab-${btn.dataset.tab}`).classList.add("active");
    });
  });

  $("btn-track-squad-only").addEventListener("click", () => controller.trackSquadOnly());
  $("btn-clear-extra-tracks").addEventListener("click", () => controller.clearExtraTracks());
  $("btn-track-killer-team").addEventListener("click", () => controller.trackKillerSquad());
  $("player-filter-input").addEventListener("input", (e) => {
    state.playerFilter = e.target.value;
    renderTrackLists();
  });

  document.querySelectorAll("[data-track-sort]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-track-sort]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.trackSort = btn.dataset.trackSort || "rank";
      renderTrackLists();
    });
  });

  document.querySelectorAll("[data-ev-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-ev-filter]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.eventFilter = btn.dataset.evFilter;
      renderEventsList();
    });
  });
}

async function bootstrapAfterAuth() {
  const params = new URLSearchParams(window.location.search);
  const matchId = params.get("match");
  await loadDashboard(false);
  if (matchId) {
    await openReplay(matchId);
  }
}

async function init() {
  try {
    const url = new URL(window.location.href);
    if (url.searchParams.has("_r")) {
      url.searchParams.delete("_r");
      window.history.replaceState({}, "", url.toString());
    }
  } catch {
    // ignore
  }
  bindUIEvents();
  try {
    const auth = await fetchAuthStatus();
    if (auth.auth_required && !auth.authenticated) {
      openAuthModal();
      return;
    }
    await bootstrapAfterAuth();
  } catch (err) {
    showStatus(`无法连接到 SquadLens 服务：${err.message}`);
  }
}

init();
