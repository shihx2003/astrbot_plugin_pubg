/**
 * High-DPI HTML5 Canvas 2D Tactical Map Renderer (v2).
 * Features:
 * - Dual-layer 2K HD maps: Default + Topographic Terrain toggle (`./static/maps/...`)
 * - Dashed movement trails in PUBG in-game slot colors (1=Yellow, 2=Orange, 3=Blue, 4=Green)
 *   and team colors for tracked opponents, with dark halo for readability on light terrain maps
 * - PUBG Esports team-colored circular badges with team number on EVERY player dot
 * - Larger, highlighted markers for our own squad (with slot #1-4) and selected opponents
 * - Mouse drag/wheel zoom & Mobile single-finger pan + two-finger pinch zoom
 */

function getMapAssetVer() {
  return (typeof window !== "undefined" && window.__SQUADLENS_VER__) || "dev";
}

function withMapVer(url) {
  if (!url) return "";
  const v = encodeURIComponent(getMapAssetVer());
  return url.includes("?") ? `${url}&v=${v}` : `${url}?v=${v}`;
}

/**
 * Extend a directed flight path segment (p1 -> p2) cleanly to map borders [0, sizeCm]^2.
 */
export function extendPlaneToMapBounds(p1, p2, sizeCm = 816000) {
  const x1 = Number(p1?.[0] || 0);
  const y1 = Number(p1?.[1] || 0);
  const x2 = Number(p2?.[0] || 0);
  const y2 = Number(p2?.[1] || 0);
  const dx = x2 - x1;
  const dy = y2 - y1;
  if (Math.hypot(dx, dy) < 1e-3) return [[x1, y1], [x2, y2]];

  const ts = [];
  if (Math.abs(dx) > 1e-6) {
    for (const bx of [0, sizeCm]) {
      const t = (bx - x1) / dx;
      const y = y1 + dy * t;
      if (y >= -1 && y <= sizeCm + 1) ts.push(t);
    }
  }
  if (Math.abs(dy) > 1e-6) {
    for (const by of [0, sizeCm]) {
      const t = (by - y1) / dy;
      const x = x1 + dx * t;
      if (x >= -1 && x <= sizeCm + 1) ts.push(t);
    }
  }
  if (ts.length < 2) return [[x1, y1], [x2, y2]];
  const tMin = Math.min(...ts);
  const tMax = Math.max(...ts);
  if (tMax - tMin < 1e-4) return [[x1, y1], [x2, y2]];
  const clamp = (v) => Math.max(0, Math.min(sizeCm, v));
  return [
    [clamp(x1 + dx * tMin), clamp(y1 + dy * tMin)],
    [clamp(x1 + dx * tMax), clamp(y1 + dy * tMax)],
  ];
}

function drawPlaneIcon2D(ctx, x, y, angle, size, isLightTerrain) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(angle);
  ctx.beginPath();
  // Sleek transport aircraft silhouette pointing along +X
  ctx.moveTo(size * 0.95, 0);
  ctx.lineTo(size * 0.25, -size * 0.16);
  ctx.lineTo(-size * 0.12, -size * 0.95);
  ctx.lineTo(-size * 0.36, -size * 0.95);
  ctx.lineTo(-size * 0.15, -size * 0.18);
  ctx.lineTo(-size * 0.68, -size * 0.14);
  ctx.lineTo(-size * 0.92, -size * 0.48);
  ctx.lineTo(-size * 1.02, -size * 0.48);
  ctx.lineTo(-size * 0.88, 0);
  ctx.lineTo(-size * 1.02, size * 0.48);
  ctx.lineTo(-size * 0.92, size * 0.48);
  ctx.lineTo(-size * 0.68, size * 0.14);
  ctx.lineTo(-size * 0.15, size * 0.18);
  ctx.lineTo(-size * 0.36, size * 0.95);
  ctx.lineTo(-size * 0.12, size * 0.95);
  ctx.lineTo(size * 0.25, size * 0.16);
  ctx.closePath();
  ctx.fillStyle = isLightTerrain ? "#0f172a" : "#fff8dc";
  ctx.strokeStyle = isLightTerrain ? "rgba(255, 255, 255, 0.92)" : "rgba(7, 11, 19, 0.92)";
  ctx.lineWidth = 1.8;
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawCarePackage2D(ctx, x, y, cp, currentTime, scale) {
  const isFalling = cp.land_t == null || currentTime < cp.land_t;
  const ageAfterLand = cp.land_t != null ? currentTime - cp.land_t : -1;
  ctx.save();
  ctx.translate(x, y);

  if (isFalling) {
    // Pulsing drop target ring + parachute canopy
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.008);
    ctx.beginPath();
    ctx.arc(0, 0, 10 + pulse * 5, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(248, 113, 113, ${0.85 - pulse * 0.4})`;
    ctx.lineWidth = 1.6;
    ctx.stroke();

    // Mini parachute canopy above crate
    ctx.beginPath();
    ctx.arc(0, -10, 6.5, Math.PI, 0);
    ctx.fillStyle = "rgba(255, 247, 237, 0.95)";
    ctx.strokeStyle = "#0b0f17";
    ctx.lineWidth = 1.2;
    ctx.fill();
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(-5.5, -10);
    ctx.lineTo(-2.5, -3);
    ctx.moveTo(5.5, -10);
    ctx.lineTo(2.5, -3);
    ctx.strokeStyle = "rgba(226, 232, 240, 0.85)";
    ctx.lineWidth = 1;
    ctx.stroke();
  } else if (ageAfterLand >= 0 && ageAfterLand <= 90) {
    // Subtle red smoke halo for freshly landed care packages
    const fade = Math.max(0.15, 1 - ageAfterLand / 90);
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.006);
    ctx.beginPath();
    ctx.arc(0, 0, 9 + pulse * 4, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(239, 68, 68, ${(0.22 * fade).toFixed(3)})`;
    ctx.fill();
    ctx.strokeStyle = `rgba(239, 68, 68, ${(0.65 * fade).toFixed(3)})`;
    ctx.lineWidth = 1.3;
    ctx.stroke();
  }

  // Iconic PUBG red-top / blue-base airdrop crate
  const sz = scale >= 2.2 ? 12 : 10;
  const hs = sz * 0.5;
  ctx.fillStyle = "#1d4ed8";
  ctx.fillRect(-hs, -hs, sz, sz);
  ctx.fillStyle = "#ef4444";
  ctx.fillRect(-hs, -hs, sz, sz * 0.48);
  ctx.fillStyle = "#facc15";
  ctx.fillRect(-hs, -0.8, sz, 1.6);
  ctx.strokeStyle = "#0b0f17";
  ctx.lineWidth = 1.8;
  ctx.strokeRect(-hs, -hs, sz, sz);

  // Compact highlight loot label when zoomed in
  if (!isFalling && scale >= 1.65 && Array.isArray(cp.items) && cp.items.length > 0) {
    const lbl = cp.items.slice(0, 2).join("·");
    ctx.font = "700 10px system-ui, -apple-system, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const tw = ctx.measureText(lbl).width + 8;
    ctx.fillStyle = "rgba(11, 15, 23, 0.84)";
    ctx.fillRect(-tw / 2, hs + 3, tw, 14);
    ctx.fillStyle = "#fde68a";
    ctx.fillText(lbl, 0, hs + 5);
  }
  ctx.restore();
}

const PUBG_SLOT_COLORS = {
  1: "#F5C518",
  2: "#FF8C1A",
  3: "#2EA8FF",
  4: "#3CD070",
};

const TEAM_PALETTE = [
  "#06B6D4", "#3B82F6", "#8B5CF6", "#EC4899", "#F43F5E", "#10B981",
  "#14B8A6", "#6366F1", "#D946EF", "#F97316", "#84CC16", "#0EA5E9",
  "#E11D48", "#22C55E", "#A855F7", "#EAB308", "#FB7185", "#38BDF8",
  "#4ADE80", "#C084FC", "#FDBA74", "#2DD4BF", "#818CF8", "#F472B6",
  "#34D399", "#60A5FA", "#FACC15", "#FB923C", "#A3E635", "#67E8F9",
  "#E879F9", "#FCA5A5",
];

export function getTeamColor(teamId, replay = null) {
  const tid = Math.abs(Number(teamId || 0));
  if (replay?.teams?.[String(tid)]?.color) {
    return replay.teams[String(tid)].color;
  }
  return TEAM_PALETTE[tid % TEAM_PALETTE.length];
}

export function getPlayerVisualColor(player, replay = null) {
  if (!player) return "#CBD5E1";
  if (player.is_squad) {
    if (player.color) return player.color;
    return PUBG_SLOT_COLORS[player.squad_slot] || "#F5C518";
  }
  return player.team_color || getTeamColor(player.team_id, replay);
}

export function interpolateTrack(track, t) {
  if (!track || track.length === 0) return null;
  if (t <= track[0][0]) {
    const p = track[0];
    return { x: p[1], y: p[2], hp: p[3], state: p[4], idx: 0 };
  }
  const lastIdx = track.length - 1;
  if (t >= track[lastIdx][0]) {
    const p = track[lastIdx];
    return { x: p[1], y: p[2], hp: p[3], state: p[4], idx: lastIdx };
  }

  let lo = 0;
  let hi = lastIdx;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (track[mid][0] <= t) lo = mid;
    else hi = mid;
  }
  const a = track[lo];
  const b = track[lo + 1];
  const dt = b[0] - a[0];
  const ratio = dt > 0 ? (t - a[0]) / dt : 0;
  return {
    x: a[1] + (b[1] - a[1]) * ratio,
    y: a[2] + (b[2] - a[2]) * ratio,
    hp: a[3],
    state: a[4],
    idx: lo,
  };
}

function interpolateZone(zones, t) {
  if (!zones || zones.length === 0) return null;
  if (t <= zones[0][0]) return zones[0];
  const last = zones.length - 1;
  if (t >= zones[last][0]) return zones[last];
  let lo = 0;
  let hi = last;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (zones[mid][0] <= t) lo = mid;
    else hi = mid;
  }
  const a = zones[lo];
  const b = zones[lo + 1];
  const dt = b[0] - a[0];
  const r = dt > 0 ? (t - a[0]) / dt : 0;
  return [
    t,
    a[1] + (b[1] - a[1]) * r,
    a[2] + (b[2] - a[2]) * r,
    a[3] + (b[3] - a[3]) * r,
    a[4] + (b[4] - a[4]) * r,
    a[5] + (b[5] - a[5]) * r,
    a[6] + (b[6] - a[6]) * r,
  ];
}

/**
 * 3-stage adaptive icon & trail metrics for 2D Canvas zoom levels:
 * - Stage 1 (Zoomed out, scale < 1.8): crisp readable dots without cluttering the full island
 * - Stage 2 (Mid tactical, 1.8 <= scale < 5.0): comfortable tactical badge size
 * - Stage 3 (Close compound zoom, scale >= 5.0): compact icons so buildings & spacing stay crystal clear
 */
function get2DZoomLod(scale) {
  if (scale < 1.8) {
    const t = Math.max(0, Math.min(1, (scale - 0.8) / 1.0));
    return {
      enemyR: 5.0 + t * 1.3,
      trackedR: 7.4 + t * 1.1,
      squadR: 8.4 + t * 1.1,
      deadR: 3.3 + t * 0.5,
      deadHighlightR: 5.2 + t * 0.8,
      fontEnemy: 7.5,
      fontHighlight: 9.5,
      labelFont: 10.5,
      trailSquadW: 2.5,
      trailOtherW: 2.0,
    };
  }
  if (scale < 5.0) {
    return {
      enemyR: 6.3,
      trackedR: 8.5,
      squadR: 9.5,
      deadR: 3.8,
      deadHighlightR: 6.0,
      fontEnemy: 8.2,
      fontHighlight: 10.0,
      labelFont: 11.0,
      trailSquadW: 2.8,
      trailOtherW: 2.2,
    };
  }
  // Stage 3: zoomed deep into a compound -> gently compact icons so map buildings remain clearly visible
  const t = Math.max(0, Math.min(1, (scale - 5.0) / 6.0));
  return {
    enemyR: 6.3 - t * 1.2,
    trackedR: 8.5 - t * 1.3,
    squadR: 9.5 - t * 1.4,
    deadR: 3.8 - t * 0.6,
    deadHighlightR: 6.0 - t * 0.9,
    fontEnemy: 7.8,
    fontHighlight: 9.2,
    labelFont: 10.0,
    trailSquadW: 2.4 - t * 0.4,
    trailOtherW: 1.9 - t * 0.3,
  };
}

export class MapRenderer {
  constructor(canvas, onPlayerTap) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.onPlayerTap = onPlayerTap;

    this.replay = null;
    this.mapLayer = "default"; // "default" | "terrain"
    this.defaultImg = null;
    this.defaultLoaded = false;
    this.terrainImg = null;
    this.terrainLoaded = false;

    this.scale = 1.0;
    this.offsetX = 0;
    this.offsetY = 0;
    this.baseSize = 600;
    this.width = 600;
    this.height = 600;

    this._screenDots = [];
    this._pointers = new Map();
    this._dragStart = null;
    this._pinchStart = null;
    this._movedSinceDown = false;

    this._bindEvents();
    this.resize();
  }

  setReplay(replay) {
    this.replay = replay;
    this.mapLayer = "default";
    this.defaultLoaded = false;
    this.terrainLoaded = false;

    this.defaultImg = new Image();
    this.defaultImg.decoding = "async";
    this.defaultImg.onload = () => {
      this.defaultLoaded = true;
    };
    const relImg = String(replay.map_image || "static/maps/erangel.webp").replace(/^\/+/, "");
    this.defaultImg.src = withMapVer(`./${relImg}`);

    // Progressive 4K upgrade for Erangel (auto-negotiates to ~1.3MB AVIF on supported browsers!)
    this._hdUpgradeUrl =
      relImg.endsWith("/erangel.webp") || relImg === "erangel.webp"
        ? withMapVer("./static/maps/hd/erangel_4k.webp")
        : "";
    this._hdUpgradeStarted = false;
    if (this._hdUpgradeUrl) {
      const startHd = () => this._maybeLoadHdDefault();
      if (typeof window.requestIdleCallback === "function") {
        window.requestIdleCallback(startHd, { timeout: 2500 });
      } else {
        setTimeout(startHd, 1200);
      }
    }

    if (replay.terrain_image) {
      this.terrainImg = new Image();
      this.terrainImg.onload = () => {
        this.terrainLoaded = true;
      };
      const relTer = String(replay.terrain_image).replace(/^\/+/, "");
      this.terrainImg.src = withMapVer(`./${relTer}`);
    } else {
      this.terrainImg = null;
    }
    this.resetView();
  }

  setMapLayer(layer) {
    if (layer === "terrain" && this.replay?.terrain_image) {
      this.mapLayer = "terrain";
    } else {
      this.mapLayer = "default";
    }
  }

  resize() {
    const rect = this.canvas.parentElement?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    this.width = rect.width;
    this.height = rect.height;
    this.canvas.width = Math.round(rect.width * dpr);
    this.canvas.height = Math.round(rect.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.baseSize = Math.min(this.width, this.height - 84);
    if (this.baseSize < 240) this.baseSize = Math.min(this.width, this.height);
  }

  resetView() {
    this.resize();
    this.scale = 1.0;
    this.offsetX = (this.width - this.baseSize) / 2;
    this.offsetY = Math.max(8, (this.height - 76 - this.baseSize) / 2);
  }

  _maybeLoadHdDefault() {
    if (this._hdUpgradeStarted || !this._hdUpgradeUrl) return;
    this._hdUpgradeStarted = true;
    const targetUrl = this._hdUpgradeUrl;
    const hdImg = new Image();
    hdImg.decoding = "async";
    hdImg.onload = () => {
      if (this._hdUpgradeUrl === targetUrl) {
        this.defaultImg = hdImg;
        this.defaultLoaded = true;
      }
    };
    hdImg.src = targetUrl;
  }

  zoomBy(factor, cx = this.width / 2, cy = (this.height - 70) / 2) {
    const newScale = Math.max(0.75, Math.min(16, this.scale * factor));
    const ratio = newScale / this.scale;
    this.offsetX = cx - (cx - this.offsetX) * ratio;
    this.offsetY = cy - (cy - this.offsetY) * ratio;
    this.scale = newScale;
    if (this.scale >= 2.0) {
      this._maybeLoadHdDefault();
    }
  }

  focusWorldPoint(xCm, yCm, targetScale = null) {
    if (!this.replay) return;
    const size = this.replay.map_size || 816000;
    if (targetScale !== null) {
      this.scale = Math.max(1.0, Math.min(14, targetScale));
    }
    const nx = xCm / size;
    const ny = yCm / size;
    const mapPx = this.baseSize * this.scale;
    this.offsetX = this.width / 2 - nx * mapPx;
    this.offsetY = (this.height - 76) / 2 - ny * mapPx;
  }

  worldToScreen(xCm, yCm) {
    const size = this.replay?.map_size || 816000;
    const mapPx = this.baseSize * this.scale;
    return {
      x: this.offsetX + (xCm / size) * mapPx,
      y: this.offsetY + (yCm / size) * mapPx,
    };
  }

  getPlayerColor(player) {
    return getPlayerVisualColor(player, this.replay);
  }

  render(state) {
    const { currentTime, trackedIds, followId, flashEvent, showPlane = true, showAirdrops = true } = state;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    ctx.fillStyle = "#06090f";
    ctx.fillRect(0, 0, this.width, this.height);

    if (!this.replay) return 0;

    if (followId && this.replay.tracks[followId]) {
      const pos = interpolateTrack(this.replay.tracks[followId], currentTime);
      if (pos) {
        this.focusWorldPoint(pos.x, pos.y, null);
      }
    }

    const mapPx = this.baseSize * this.scale;
    const sizeCm = this.replay.map_size || 816000;

    // 1. Official HD Map (Default or Topographic Terrain)
    const activeImg =
      this.mapLayer === "terrain" && this.terrainLoaded
        ? this.terrainImg
        : this.defaultLoaded
          ? this.defaultImg
          : null;

    if (activeImg) {
      ctx.drawImage(activeImg, this.offsetX, this.offsetY, mapPx, mapPx);
    } else {
      ctx.fillStyle = "#121a26";
      ctx.fillRect(this.offsetX, this.offsetY, mapPx, mapPx);
      ctx.strokeStyle = "#1f2c40";
      ctx.lineWidth = 1;
      for (let i = 0; i <= 8; i += 1) {
        const g = (mapPx / 8) * i;
        ctx.beginPath();
        ctx.moveTo(this.offsetX + g, this.offsetY);
        ctx.lineTo(this.offsetX + g, this.offsetY + mapPx);
        ctx.moveTo(this.offsetX, this.offsetY + g);
        ctx.lineTo(this.offsetX + mapPx, this.offsetY + g);
        ctx.stroke();
      }
    }

    const isLightTerrain = this.mapLayer === "terrain" && this.terrainLoaded;
    const lod = get2DZoomLod(this.scale);

    // 2. Plane flight path (toggleable, extended edge-to-edge with directional chevrons & plane icon)
    if (showPlane && this.replay.plane && this.replay.plane.length === 2) {
      const [wStart, wEnd] = extendPlaneToMapBounds(this.replay.plane[0], this.replay.plane[1], sizeCm);
      const p1 = this.worldToScreen(wStart[0], wStart[1]);
      const p2 = this.worldToScreen(wEnd[0], wEnd[1]);
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const distPx = Math.hypot(dx, dy);
      if (distPx > 6) {
        const angle = Math.atan2(dy, dx);
        ctx.save();
        ctx.lineCap = "round";
        ctx.setLineDash([10, 7]);
        // Dark halo beneath dashed line for crisp contrast on any map brightness
        ctx.strokeStyle = isLightTerrain ? "rgba(255, 255, 255, 0.65)" : "rgba(7, 11, 19, 0.8)";
        ctx.lineWidth = 3.8;
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();

        ctx.strokeStyle = isLightTerrain ? "rgba(15, 23, 42, 0.82)" : "rgba(255, 248, 220, 0.86)";
        ctx.lineWidth = 1.9;
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();

        // Directional chevrons along flight route
        ctx.setLineDash([]);
        const ux = dx / distPx;
        const uy = dy / distPx;
        const stepPx = Math.max(90, Math.min(180, distPx / 5));
        for (let d = stepPx * 0.6; d < distPx - 28; d += stepPx) {
          const cx = p1.x + ux * d;
          const cy = p1.y + uy * d;
          ctx.save();
          ctx.translate(cx, cy);
          ctx.rotate(angle);
          ctx.beginPath();
          ctx.moveTo(-4.5, -4.5);
          ctx.lineTo(3.5, 0);
          ctx.lineTo(-4.5, 4.5);
          ctx.strokeStyle = isLightTerrain ? "rgba(255, 255, 255, 0.78)" : "rgba(7, 11, 19, 0.86)";
          ctx.lineWidth = 3.4;
          ctx.stroke();
          ctx.strokeStyle = isLightTerrain ? "#0f172a" : "#fde68a";
          ctx.lineWidth = 1.8;
          ctx.stroke();
          ctx.restore();
        }

        // Animated transport airplane icon during opening flight (or docked near entry/exit arrow)
        const pf = this.replay.plane_flight;
        let iconPt = null;
        if (Array.isArray(pf) && pf.length === 6 && pf[3] > pf[0] && currentTime <= pf[3] + 15) {
          const f = Math.max(0, Math.min(1.15, (currentTime - pf[0]) / (pf[3] - pf[0])));
          const wx = pf[1] + (pf[4] - pf[1]) * f;
          const wy = pf[2] + (pf[5] - pf[2]) * f;
          iconPt = this.worldToScreen(wx, wy);
        } else {
          // Subtle directional plane marker near 14% along route so origin & heading are always obvious
          iconPt = { x: p1.x + ux * Math.min(48, distPx * 0.14), y: p1.y + uy * Math.min(48, distPx * 0.14) };
        }
        if (iconPt) {
          drawPlaneIcon2D(ctx, iconPt.x, iconPt.y, angle, 9.5, isLightTerrain);
        }
        ctx.restore();
      }
    }

    // 3. Safe zone (white / dark-slate on light terrain) & Bluezone (blue)
    const zone = interpolateZone(this.replay.zones, currentTime);
    if (zone) {
      const [, sx, sy, sr, px, py, pr] = zone;
      if (pr > 0) {
        const pc = this.worldToScreen(px, py);
        const pRad = (pr / sizeCm) * mapPx;
        ctx.save();
        ctx.strokeStyle = isLightTerrain ? "rgba(2, 132, 199, 0.92)" : "rgba(56, 189, 248, 0.92)";
        ctx.lineWidth = 2.2;
        ctx.beginPath();
        ctx.arc(pc.x, pc.y, pRad, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
      if (sr > 0) {
        const sc = this.worldToScreen(sx, sy);
        const sRad = (sr / sizeCm) * mapPx;
        ctx.save();
        ctx.strokeStyle = isLightTerrain ? "rgba(15, 23, 42, 0.85)" : "rgba(255, 255, 255, 0.92)";
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(sc.x, sc.y, sRad, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
    }

    // 3.5 Care packages / Airdrops (toggleable)
    if (showAirdrops && Array.isArray(this.replay.care_packages) && this.replay.care_packages.length > 0) {
      for (const cp of this.replay.care_packages) {
        if (currentTime < (cp.spawn_t ?? 0)) continue;
        const sc = this.worldToScreen(cp.x, cp.y);
        drawCarePackage2D(ctx, sc.x, sc.y, cp, currentTime, this.scale);
      }
    }

    // 4. Dashed movement trails for all tracked players (bound squad + selected opponents)
    const players = this.replay.players || {};
    const tracks = this.replay.tracks || {};
    for (const aid of trackedIds) {
      const track = tracks[aid];
      const p = players[aid];
      if (!track || track.length < 2 || !p) continue;
      const color = this.getPlayerColor(p);

      const buildTrailPath = () => {
        ctx.beginPath();
        let started = false;
        for (let i = 0; i < track.length; i += 1) {
          const pt = track[i];
          if (pt[0] > currentTime) break;
          const sc = this.worldToScreen(pt[1], pt[2]);
          if (!started) {
            ctx.moveTo(sc.x, sc.y);
            started = true;
          } else {
            ctx.lineTo(sc.x, sc.y);
          }
        }
        const interp = interpolateTrack(track, currentTime);
        if (interp && started) {
          const scNow = this.worldToScreen(interp.x, interp.y);
          ctx.lineTo(scNow.x, scNow.y);
        }
        return started;
      };

      ctx.save();
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      const dashPattern = p.is_squad ? [8, 5] : [5, 4];

      // Subtle dark outline beneath dashed trail for razor-sharp contrast on light terrain maps
      ctx.setLineDash(dashPattern);
      if (buildTrailPath()) {
        const lineW = p.is_squad ? lod.trailSquadW : lod.trailOtherW;
        ctx.strokeStyle = "rgba(11, 15, 23, 0.72)";
        ctx.lineWidth = lineW + 1.7;
        ctx.stroke();

        ctx.strokeStyle = color;
        ctx.lineWidth = lineW;
        ctx.stroke();
      }

      // Parachute landing pin
      if (p.landing && p.landing[0] <= currentTime) {
        const lp = this.worldToScreen(p.landing[1], p.landing[2]);
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.strokeStyle = "#0b0f17";
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(lp.x, lp.y, 4, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
      ctx.restore();
    }

    // 5. Recent combat lines (within +-3.5s or highlighted event)
    const activeEvents = (this.replay.events || []).filter(
      (ev) =>
        (ev.type === "kill" || ev.type === "knock" || ev.type === "team_damage") &&
        Math.abs(ev.t - currentTime) <= 3.5 &&
        (ev.ax || ev.vx)
    );
    if (flashEvent && !activeEvents.includes(flashEvent)) {
      activeEvents.push(flashEvent);
    }
    for (const ev of activeEvents) {
      const a = this.worldToScreen(ev.ax || ev.vx, ev.ay || ev.vy);
      const v = this.worldToScreen(ev.vx || ev.ax, ev.vy || ev.ay);
      const isHighlight = flashEvent === ev;
      ctx.save();
      ctx.strokeStyle = ev.is_team_kill
        ? "#ef4444"
        : ev.type === "kill"
          ? "#facc15"
          : "#fb923c";
      ctx.lineWidth = isHighlight ? 3.2 : 2;
      ctx.setLineDash([5, 3]);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(v.x, v.y);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.strokeStyle = ev.type === "kill" ? "#facc15" : "#fb923c";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(v.x, v.y, isHighlight ? 11 : 7, 0, Math.PI * 2);
      ctx.stroke();

      if (isHighlight || trackedIds.has(ev.attacker_id) || trackedIds.has(ev.victim_id)) {
        const label = `${ev.weapon}${ev.distance ? ` ${Math.round(ev.distance)}m` : ""}`;
        ctx.font = "bold 11px sans-serif";
        const tw = ctx.measureText(label).width;
        ctx.fillStyle = "rgba(11, 15, 23, 0.88)";
        ctx.fillRect(v.x + 10, v.y - 20, tw + 10, 18);
        ctx.fillStyle = "#fde047";
        ctx.fillText(label, v.x + 15, v.y - 7);
      }
      ctx.restore();
    }

    // 6. All player dots with PUBG Esports team badges & enlarged squad/selected markers
    this._screenDots = [];
    let aliveCount = 0;
    const drawOrder = Object.values(players).sort((a, b) => {
      const sa = (a.is_squad ? 4 : 0) + (trackedIds.has(a.account_id) ? 2 : 0);
      const sb = (b.is_squad ? 4 : 0) + (trackedIds.has(b.account_id) ? 2 : 0);
      return sa - sb;
    });

    for (const p of drawOrder) {
      const track = tracks[p.account_id];
      if (!track || track.length === 0) continue;
      const pos = interpolateTrack(track, currentTime);
      if (!pos) continue;
      if (pos.state !== 2) aliveCount += 1;

      const sc = this.worldToScreen(pos.x, pos.y);
      const isTracked = trackedIds.has(p.account_id);
      const isHighlight = p.is_squad || isTracked;
      const color = this.getPlayerColor(p);
      const badgeNum = p.is_squad
        ? String(p.squad_slot || p.team_id || "")
        : String(p.team_id || "");

      this._screenDots.push({ account_id: p.account_id, x: sc.x, y: sc.y, isTracked });

      ctx.save();
      if (pos.state === 2) {
        // Dead marker (team-colored ×)
        if (!isHighlight) ctx.globalAlpha = 0.42;
        const r = isHighlight ? lod.deadHighlightR : lod.deadR;
        ctx.strokeStyle = "#0b0f17";
        ctx.lineWidth = isHighlight ? 3.8 : 2.5;
        ctx.beginPath();
        ctx.moveTo(sc.x - r, sc.y - r);
        ctx.lineTo(sc.x + r, sc.y + r);
        ctx.moveTo(sc.x + r, sc.y - r);
        ctx.lineTo(sc.x - r, sc.y + r);
        ctx.stroke();

        ctx.strokeStyle = color;
        ctx.lineWidth = isHighlight ? 2.2 : 1.5;
        ctx.stroke();
      } else {
        // 3-stage adaptive radius: readable zoomed out, compact zoomed into buildings
        const radius = p.is_squad ? lod.squadR : isTracked ? lod.trackedR : lod.enemyR;

        if (isHighlight) {
          ctx.strokeStyle = p.is_squad ? "#ffffff" : "#fde047";
          ctx.lineWidth = 1.9;
          ctx.beginPath();
          ctx.arc(sc.x, sc.y, radius + 2.0, 0, Math.PI * 2);
          ctx.stroke();
        }

        ctx.fillStyle = pos.state === 1 ? "#f97316" : color;
        ctx.strokeStyle = "#0b0f17";
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.arc(sc.x, sc.y, radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();

        if (pos.state === 1) {
          ctx.strokeStyle = "#f97316";
          ctx.lineWidth = 1.7;
          ctx.beginPath();
          ctx.arc(sc.x, sc.y, radius + 3.8, 0, Math.PI * 2);
          ctx.stroke();
        }

        // Crisp team number / squad slot number inside EVERY player dot
        if (badgeNum) {
          ctx.fillStyle = "#0b0f17";
          const fsz = isHighlight ? lod.fontHighlight : lod.fontEnemy;
          ctx.font = `800 ${fsz}px sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(badgeNum, sc.x, sc.y + 0.5);
        }
      }

      // Name pill for squad & tracked opponents
      if (isHighlight) {
        const prefix = p.is_squad && p.squad_slot ? `${p.squad_slot}.` : p.team_id ? `#${p.team_id} ` : "";
        const tag = `${prefix}${p.display_name || p.name}`;
        ctx.font = `bold ${lod.labelFont}px sans-serif`;
        ctx.textAlign = "left";
        ctx.textBaseline = "alphabetic";
        const tw = ctx.measureText(tag).width;
        const yOff = (p.is_squad ? lod.squadR : lod.trackedR) + 14;
        ctx.fillStyle = "rgba(11, 15, 23, 0.84)";
        ctx.fillRect(sc.x - tw / 2 - 4, sc.y - yOff, tw + 8, 15);
        ctx.fillStyle = color;
        ctx.fillText(tag, sc.x - tw / 2, sc.y - yOff + 11.5);
      }
      ctx.restore();
    }

    return aliveCount;
  }

  _bindEvents() {
    const c = this.canvas;

    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const rect = c.getBoundingClientRect();
      const factor = e.deltaY < 0 ? 1.18 : 1 / 1.18;
      this.zoomBy(factor, e.clientX - rect.left, e.clientY - rect.top);
    }, { passive: false });

    c.addEventListener("pointerdown", (e) => {
      c.setPointerCapture(e.pointerId);
      this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      this._movedSinceDown = false;
      if (this._pointers.size === 1) {
        this._dragStart = {
          x: e.clientX,
          y: e.clientY,
          ox: this.offsetX,
          oy: this.offsetY,
        };
      } else if (this._pointers.size === 2) {
        const pts = [...this._pointers.values()];
        const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
        const rect = c.getBoundingClientRect();
        this._pinchStart = {
          dist: Math.max(1, dist),
          scale: this.scale,
          midX: (pts[0].x + pts[1].x) / 2 - rect.left,
          midY: (pts[0].y + pts[1].y) / 2 - rect.top,
          ox: this.offsetX,
          oy: this.offsetY,
        };
      }
    });

    c.addEventListener("pointermove", (e) => {
      if (!this._pointers.has(e.pointerId)) return;
      this._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

      if (this._pointers.size === 2 && this._pinchStart) {
        this._movedSinceDown = true;
        const pts = [...this._pointers.values()];
        const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
        const rect = c.getBoundingClientRect();
        const curMidX = (pts[0].x + pts[1].x) / 2 - rect.left;
        const curMidY = (pts[0].y + pts[1].y) / 2 - rect.top;
        const targetScale = Math.max(0.75, Math.min(16, this._pinchStart.scale * (dist / this._pinchStart.dist)));
        const ratio = targetScale / this._pinchStart.scale;
        this.scale = targetScale;
        this.offsetX = curMidX - (this._pinchStart.midX - this._pinchStart.ox) * ratio;
        this.offsetY = curMidY - (this._pinchStart.midY - this._pinchStart.oy) * ratio;
      } else if (this._pointers.size === 1 && this._dragStart) {
        const dx = e.clientX - this._dragStart.x;
        const dy = e.clientY - this._dragStart.y;
        if (Math.hypot(dx, dy) > 6) {
          this._movedSinceDown = true;
        }
        this.offsetX = this._dragStart.ox + dx;
        this.offsetY = this._dragStart.oy + dy;
      }
    });

    const endPointer = (e) => {
      const wasSingle = this._pointers.size === 1;
      this._pointers.delete(e.pointerId);
      if (wasSingle && !this._movedSinceDown) {
        const rect = c.getBoundingClientRect();
        this._handleTap(e.clientX - rect.left, e.clientY - rect.top);
      }
      if (this._pointers.size < 2) {
        this._pinchStart = null;
      }
      if (this._pointers.size === 1) {
        const [pt] = [...this._pointers.values()];
        this._dragStart = { x: pt.x, y: pt.y, ox: this.offsetX, oy: this.offsetY };
      } else {
        this._dragStart = null;
      }
    };

    c.addEventListener("pointerup", endPointer);
    c.addEventListener("pointercancel", endPointer);
    window.addEventListener("resize", () => this.resize());
  }

  _handleTap(sx, sy) {
    let best = null;
    let bestDist = 22;
    for (const dot of this._screenDots) {
      const d = Math.hypot(dot.x - sx, dot.y - sy);
      if (d < bestDist) {
        bestDist = d;
        best = dot;
      }
    }
    if (best && this.onPlayerTap) {
      this.onPlayerTap(best.account_id);
    }
  }
}
