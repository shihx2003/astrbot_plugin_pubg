/**
 * Lazy-loaded Three.js WebGL 3D Tactical Replay Engine for Erangel.
 * Loaded ONLY when `enable_3d_replay === true` AND user switches to 3D mode.
 *
 * Features:
 * - Zero external CDN: imports bundled local `./vendor/three/*` ES modules
 * - Displaces 256x256 terrain mesh using Erangel 512x512 heightmap + 2K satellite/terrain texture
 * - Automatically loads optional `buildings.glb` (3D architectural white-box models) when present
 * - Renders 3D dashed trails following terrain + parachute z-altitude, team/slot badges,
 *   3D safe/blue zone walls, and 3D combat laser beams
 */

import * as THREE from "../vendor/three/three.module.min.js";
import { OrbitControls } from "../vendor/three/OrbitControls.js";
import { GLTFLoader } from "../vendor/three/GLTFLoader.js";
import { getPlayerVisualColor, interpolateTrack } from "./map-renderer.js";

const WORLD_SIZE = 800; // Maps 816,000 cm -> 800 Three.js units (1 unit = 10.2m)
const TERRAIN_SEGMENTS = 320;
const TERRAIN_MAX_HEIGHT = 46; // Visual elevation scale in Three.js units (clear dramatic hills)
const SEA_LEVEL_Y = 3.2;
const ASSET_VER = "0.5.4";

function withAssetVer(url) {
  if (!url) return "";
  return url.includes("?") ? `${url}&v=${ASSET_VER}` : `${url}?v=${ASSET_VER}`;
}

/**
 * Create a crisp procedural repeating dashed-line texture with baked-in dark outline,
 * replicating the sleek 2D Canvas dashed trail look in 3D WebGL (colored center + dark halo + clean gaps).
 */
function createDashedTrailTexture() {
  const c = document.createElement("canvas");
  c.width = 64;
  c.height = 16;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, 64, 16);

  // Dark outline halo around the dash segment (x: 1..43, y: 1..15)
  ctx.fillStyle = "rgba(6, 10, 18, 0.94)";
  ctx.fillRect(1, 1, 42, 14);

  // Pure white inner dash core (tinted by material.color to exact PUBG slot/team color)
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(2.5, 4.2, 39, 7.6);

  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.anisotropy = 8;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.needsUpdate = true;
  return tex;
}

/**
 * Compute world units per screen pixel at a given distance from the camera.
 */
function computeWorldPerPx(camera, hostEl, dist) {
  const viewportH = Math.max(240, hostEl?.clientHeight || window.innerHeight || 600);
  return (2 * Math.max(15, dist) * Math.tan(THREE.MathUtils.degToRad(camera.fov * 0.5))) / viewportH;
}

/**
 * Build a flat, terrain-hugging ribbon mesh (triangle strip) along 3D polyline points
 * with smooth mitered joins and arc-length UVs (for crisp dashed trails).
 */
function buildRibbonGeometry(points, halfWidth, { closed = false, uvPeriod = 8.0 } = {}) {
  const n = points.length;
  if (n < 2) return null;

  const positions = new Float32Array(n * 2 * 3);
  const uvs = new Float32Array(n * 2 * 2);
  const segCount = closed ? n : n - 1;
  const indices = new Uint32Array(segCount * 6);

  const dirPrev = new THREE.Vector3();
  const dirNext = new THREE.Vector3();
  const perp = new THREE.Vector3();
  let cumDist = 0;

  for (let i = 0; i < n; i += 1) {
    const curr = points[i];
    const prev = i > 0 ? points[i - 1] : closed ? points[n - 1] : curr;
    const next = i < n - 1 ? points[i + 1] : closed ? points[0] : curr;

    if (i > 0) {
      cumDist += curr.distanceTo(points[i - 1]);
    }

    dirPrev.set(curr.x - prev.x, 0, curr.z - prev.z);
    dirNext.set(next.x - curr.x, 0, next.z - curr.z);
    const lenP = dirPrev.length();
    const lenN = dirNext.length();
    if (lenP > 1e-5) dirPrev.divideScalar(lenP);
    if (lenN > 1e-5) dirNext.divideScalar(lenN);

    let tx = 0;
    let tz = 0;
    if (lenP > 1e-5 && lenN > 1e-5) {
      tx = dirPrev.x + dirNext.x;
      tz = dirPrev.z + dirNext.z;
      const tLen = Math.hypot(tx, tz);
      if (tLen > 1e-4) {
        tx /= tLen;
        tz /= tLen;
      } else {
        tx = dirNext.x;
        tz = dirNext.z;
      }
    } else if (lenN > 1e-5) {
      tx = dirNext.x;
      tz = dirNext.z;
    } else if (lenP > 1e-5) {
      tx = dirPrev.x;
      tz = dirPrev.z;
    } else {
      tx = 1;
      tz = 0;
    }

    // Perpendicular in XZ plane, with gentle miter clamp to avoid sharp spikes at hairpin turns
    perp.set(-tz, 0, tx);
    let widthScale = 1.0;
    if (lenP > 1e-5 && lenN > 1e-5) {
      const dot = Math.max(0.38, -dirPrev.z * perp.x + dirPrev.x * perp.z);
      widthScale = Math.min(1.5, 1 / dot);
    }
    const hw = halfWidth * widthScale;

    const vIdx = i * 2;
    positions[vIdx * 3] = curr.x - perp.x * hw;
    positions[vIdx * 3 + 1] = curr.y;
    positions[vIdx * 3 + 2] = curr.z - perp.z * hw;

    positions[(vIdx + 1) * 3] = curr.x + perp.x * hw;
    positions[(vIdx + 1) * 3 + 1] = curr.y;
    positions[(vIdx + 1) * 3 + 2] = curr.z + perp.z * hw;

    const u = uvPeriod > 1e-4 ? cumDist / uvPeriod : 0;
    uvs[vIdx * 2] = u;
    uvs[vIdx * 2 + 1] = 0;
    uvs[(vIdx + 1) * 2] = u;
    uvs[(vIdx + 1) * 2 + 1] = 1;
  }

  for (let i = 0; i < segCount; i += 1) {
    const a = i * 2;
    const b = a + 1;
    const nextI = i === n - 1 ? 0 : i + 1;
    const c = nextI * 2;
    const d = c + 1;
    const off = i * 6;
    indices[off] = a;
    indices[off + 1] = c;
    indices[off + 2] = b;
    indices[off + 3] = b;
    indices[off + 4] = c;
    indices[off + 5] = d;
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geo.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
  geo.setIndex(new THREE.BufferAttribute(indices, 1));
  return geo;
}

function makeBadgeTexture(text, bgColor, highlight) {
  const c = document.createElement("canvas");
  c.width = 128;
  c.height = 128;
  const ctx = c.getContext("2d");
  if (highlight) {
    ctx.beginPath();
    ctx.arc(64, 64, 58, 0, Math.PI * 2);
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 8;
    ctx.stroke();
  }
  ctx.beginPath();
  ctx.arc(64, 64, 48, 0, Math.PI * 2);
  ctx.fillStyle = bgColor;
  ctx.fill();
  ctx.strokeStyle = "#0b0f17";
  ctx.lineWidth = 6;
  ctx.stroke();

  ctx.fillStyle = "#0b0f17";
  const label = String(text || "");
  ctx.font = label.length >= 2 ? "800 44px sans-serif" : "800 50px sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(label, 64, 66);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}

/**
 * 3-stage adaptive screen-pixel diameter for 3D player icons:
 * - Far overview (d >= 360): keep readable minimum pixels so players never vanish when zoomed out
 * - Mid tactical (120 <= d < 360): balanced tactical badge size
 * - Close street-level (d < 120): cap & gently compact pixel size so buildings/roads stay clear
 */
function compute3DIconPixelSize(camDist, isSquad, isTracked) {
  const tier = isSquad ? 2 : isTracked ? 1 : 0;
  // [farPx, midPx, closePx]
  const table = [
    [13.5, 17.0, 15.5], // regular enemies
    [18.5, 22.5, 20.5], // tracked opponents
    [21.0, 26.0, 23.0], // own squad
  ];
  const [farPx, midPx, closePx] = table[tier];
  if (camDist >= 360) {
    const t = Math.min(1, (camDist - 360) / 500);
    return farPx - t * 1.5;
  }
  if (camDist >= 120) {
    const t = (camDist - 120) / 240; // 0 at close-mid, 1 at far
    return midPx + (farPx - midPx) * t;
  }
  const t = Math.max(0, Math.min(1, (camDist - 25) / 95)); // 0 very close, 1 at 120
  return closePx + (midPx - closePx) * t;
}

export class Erangel3DRenderer {
  constructor(hostEl, onPlayerTap) {
    this.host = hostEl;
    this.onPlayerTap = onPlayerTap;
    this.replay = null;
    this.config3d = null;
    this.mapLayer = "default";

    this.heightPixels = null;
    this.heightSize = 0;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x070b13);
    this.scene.fog = new THREE.FogExp2(0x070b13, 0.00072);

    this.camera = new THREE.PerspectiveCamera(48, 1, 1, 3500);
    this.camera.position.set(0, 320, 440);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.host.innerHTML = "";
    this.host.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    // Map-style free navigation (like pubgmaps.gg / Google Earth / MapControls):
    // Left-drag / 1-finger = pan across map ground plane; Right-drag / 2-finger = rotate & tilt;
    // Scroll wheel = zoom directly toward cursor position!
    this.controls.enablePan = true;
    this.controls.enableRotate = true;
    this.controls.enableZoom = true;
    this.controls.screenSpacePanning = false;
    this.controls.zoomToCursor = true;
    this.controls.panSpeed = 1.25;
    this.controls.rotateSpeed = 0.75;
    this.controls.zoomSpeed = 1.35;
    this.controls.mouseButtons = {
      LEFT: THREE.MOUSE.PAN,
      MIDDLE: THREE.MOUSE.ROTATE,
      RIGHT: THREE.MOUSE.ROTATE,
    };
    this.controls.touches = {
      ONE: THREE.TOUCH.PAN,
      TWO: THREE.TOUCH.DOLLY_ROTATE,
    };
    this.controls.minPolarAngle = 0.05;
    this.controls.maxPolarAngle = Math.PI * 0.485;
    this.controls.minDistance = 18;
    this.controls.maxDistance = 1350;
    this.controls.target.set(0, 12, 0);

    // Prevent browser context menu on right-click drag rotation
    this.renderer.domElement.addEventListener("contextmenu", (e) => e.preventDefault());

    // Bright balanced daylight lighting: crisp sunlit warm-cream rooftops + soft olive-cream walls
    const hemi = new THREE.HemisphereLight(0xffffff, 0x707460, 1.32);
    this.scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xfffceb, 1.55);
    sun.position.set(240, 420, 210);
    this.scene.add(sun);

    this.terrainMesh = null;
    this.waterMesh = null;
    this.buildingsGroup = null;
    this.safeWall = null;
    this.blueWall = null;
    this.trailsGroup = new THREE.Group();
    this.playersGroup = new THREE.Group();
    this.combatGroup = new THREE.Group();
    this.scene.add(this.trailsGroup, this.playersGroup, this.combatGroup);

    this._texLoader = new THREE.TextureLoader();
    this._dashTex = createDashedTrailTexture();
    this._defaultTex = null;
    this._terrainTex = null;
    this._badgeCache = new Map();
    this._playerSprites = new Map();
    this._playerPins = new Map();
    this._trailMeshes = new Map();
    this._lastTrailKey = "";
    this._lastTrailTime = -999;
    this._lastTrailWidthBucket = -1;
    this._lastZoneWidthBucket = -1;

    this._raycaster = new THREE.Raycaster();
    this._pointerDown = null;
    this._keysDown = new Set();
    this._onUserInteract = null;
    this._bindEvents();
    this.resize();
  }

  setOnUserInteract(cb) {
    this._onUserInteract = cb;
  }

  _bindEvents() {
    const dom = this.renderer.domElement;
    this.controls.addEventListener("start", () => {
      if (this._onUserInteract) this._onUserInteract();
    });
    dom.addEventListener("pointerdown", (e) => {
      this._pointerDown = { x: e.clientX, y: e.clientY, button: e.button };
    });
    dom.addEventListener("pointerup", (e) => {
      if (!this._pointerDown) return;
      const moved = Math.hypot(e.clientX - this._pointerDown.x, e.clientY - this._pointerDown.y);
      const btn = this._pointerDown.button;
      this._pointerDown = null;
      if (btn !== 0 || moved > 6) return;
      const rect = dom.getBoundingClientRect();
      const ndc = new THREE.Vector2(
        ((e.clientX - rect.left) / rect.width) * 2 - 1,
        -((e.clientY - rect.top) / rect.height) * 2 + 1
      );
      this._raycaster.setFromCamera(ndc, this.camera);
      const sprites = [...this._playerSprites.values()];
      const hits = this._raycaster.intersectObjects(sprites, false);
      if (hits.length > 0 && this.onPlayerTap) {
        const aid = hits[0].object.userData?.accountId;
        if (aid) this.onPlayerTap(aid);
      }
    });

    this._keyDownHandler = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const k = e.key.toLowerCase();
      if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(k)) {
        this._keysDown.add(k);
        if (this._onUserInteract) this._onUserInteract();
      }
    };
    this._keyUpHandler = (e) => {
      this._keysDown.delete(e.key.toLowerCase());
    };
    window.addEventListener("keydown", this._keyDownHandler);
    window.addEventListener("keyup", this._keyUpHandler);
  }

  _applyKeyboardPan() {
    if (this._keysDown.size === 0) return;
    const forward = new THREE.Vector3();
    this.camera.getWorldDirection(forward);
    forward.y = 0;
    if (forward.lengthSq() < 1e-6) forward.set(0, 0, -1);
    forward.normalize();
    const right = new THREE.Vector3().crossVectors(forward, new THREE.Vector3(0, 1, 0)).normalize();

    const dist = this.camera.position.distanceTo(this.controls.target);
    const step = Math.max(1.2, dist * 0.018);
    const delta = new THREE.Vector3();
    if (this._keysDown.has("w") || this._keysDown.has("arrowup")) delta.addScaledVector(forward, step);
    if (this._keysDown.has("s") || this._keysDown.has("arrowdown")) delta.addScaledVector(forward, -step);
    if (this._keysDown.has("d") || this._keysDown.has("arrowright")) delta.addScaledVector(right, step);
    if (this._keysDown.has("a") || this._keysDown.has("arrowleft")) delta.addScaledVector(right, -step);
    if (delta.lengthSq() > 0) {
      this.camera.position.add(delta);
      this.controls.target.add(delta);
    }
  }

  resize() {
    const rect = this.host.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return;
    this.camera.aspect = rect.width / rect.height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(rect.width, rect.height, false);
  }

  resetView() {
    this.controls.target.set(0, 12, 0);
    this.camera.position.set(0, 320, 440);
    this.controls.update();
  }

  zoomBy(factor) {
    const offset = this.camera.position.clone().sub(this.controls.target);
    offset.multiplyScalar(1 / factor);
    const len = Math.max(30, Math.min(1100, offset.length()));
    offset.setLength(len);
    this.camera.position.copy(this.controls.target).add(offset);
    this.controls.update();
  }

  worldTo3D(xCm, yCm, zCm = 0, { allowAirborne = true, yBias = 1.15 } = {}) {
    const sizeCm = this.replay?.map_size || 816000;
    const nx = Math.max(0, Math.min(1, xCm / sizeCm));
    const nz = Math.max(0, Math.min(1, yCm / sizeCm));
    const wx = (nx - 0.5) * WORLD_SIZE;
    const wz = (nz - 0.5) * WORLD_SIZE;
    const terrainY = Math.max(SEA_LEVEL_Y, this.sampleTerrainHeight(nx, nz));
    let wy = terrainY + yBias;
    if (allowAirborne && zCm > 0) {
      // Smoothly lift genuine airborne/parachute segments without spiky ground jitter
      const zUnits = (zCm / sizeCm) * WORLD_SIZE * 1.15;
      const aboveGround = zUnits - terrainY;
      if (aboveGround > 14) {
        wy = terrainY + yBias + (aboveGround - 14);
      }
    }
    return new THREE.Vector3(wx, wy, wz);
  }

  sampleTerrainHeight(nx, nz) {
    if (!this.heightPixels || !this.heightSize) return SEA_LEVEL_Y;
    const s = this.heightSize;
    const fx = Math.max(0, Math.min(s - 1, nx * (s - 1)));
    const fz = Math.max(0, Math.min(s - 1, nz * (s - 1)));
    const x0 = Math.floor(fx);
    const z0 = Math.floor(fz);
    const x1 = Math.min(s - 1, x0 + 1);
    const z1 = Math.min(s - 1, z0 + 1);
    const tx = fx - x0;
    const tz = fz - z0;

    const h00 = this.heightPixels[(z0 * s + x0) * 4];
    const h10 = this.heightPixels[(z0 * s + x1) * 4];
    const h01 = this.heightPixels[(z1 * s + x0) * 4];
    const h11 = this.heightPixels[(z1 * s + x1) * 4];

    const hx0 = h00 + (h10 - h00) * tx;
    const hx1 = h01 + (h11 - h01) * tx;
    const val = (hx0 + (hx1 - hx0) * tz) / 255.0;
    return val * TERRAIN_MAX_HEIGHT;
  }

  _buildTerrainConformingTrailPoints(track, currentTime, yBias) {
    const sizeCm = this.replay?.map_size || 816000;
    const rawPts = [];
    let lastWx = null;
    let lastWz = null;

    const pushRaw = (xCm, yCm, zCm) => {
      const nx = Math.max(0, Math.min(1, xCm / sizeCm));
      const nz = Math.max(0, Math.min(1, yCm / sizeCm));
      const wx = (nx - 0.5) * WORLD_SIZE;
      const wz = (nz - 0.5) * WORLD_SIZE;
      // Filter tiny indoor looting micro-jitter (< 0.55 world units ~= 5.5m)
      if (lastWx !== null && Math.hypot(wx - lastWx, wz - lastWz) < 0.55) {
        return;
      }
      lastWx = wx;
      lastWz = wz;
      rawPts.push({ xCm, yCm, zCm, wx, wz });
    };

    for (let i = 0; i < track.length; i += 1) {
      const pt = track[i];
      if (pt[0] > currentTime) break;
      pushRaw(pt[1], pt[2], pt.length >= 6 ? pt[5] : 0);
    }

    // Always anchor the trail tip cleanly to the live interpolated player position
    const tip = interpolateTrack(track, currentTime);
    if (tip) {
      const tipRaw = track[tip.idx] || track[0];
      const tipZ = tipRaw.length >= 6 ? tipRaw[5] : 0;
      const nx = Math.max(0, Math.min(1, tip.x / sizeCm));
      const nz = Math.max(0, Math.min(1, tip.y / sizeCm));
      const wx = (nx - 0.5) * WORLD_SIZE;
      const wz = (nz - 0.5) * WORLD_SIZE;
      if (rawPts.length === 0 || Math.hypot(wx - rawPts[rawPts.length - 1].wx, wz - rawPts[rawPts.length - 1].wz) > 0.08) {
        rawPts.push({ xCm: tip.x, yCm: tip.y, zCm: tipZ, wx, wz });
      }
    }

    if (rawPts.length < 2) return [];

    // Subdivide long segments along the terrain surface so trails NEVER clip into hills!
    const maxStep = 2.4;
    const dense = [];
    for (let i = 0; i < rawPts.length - 1; i += 1) {
      const a = rawPts[i];
      const b = rawPts[i + 1];
      const dist = Math.hypot(b.wx - a.wx, b.wz - a.wz);
      const steps = Math.max(1, Math.min(28, Math.ceil(dist / maxStep)));
      for (let s = 0; s < steps; s += 1) {
        const f = s / steps;
        const xCm = a.xCm + (b.xCm - a.xCm) * f;
        const yCm = a.yCm + (b.yCm - a.yCm) * f;
        const zCm = a.zCm + (b.zCm - a.zCm) * f;
        dense.push(this.worldTo3D(xCm, yCm, zCm, { allowAirborne: true, yBias }));
      }
    }
    const last = rawPts[rawPts.length - 1];
    dense.push(this.worldTo3D(last.xCm, last.yCm, last.zCm, { allowAirborne: true, yBias }));

    // Light Chaikin-style smoothing on Y so slopes feel silky without losing accuracy
    for (let i = 1; i < dense.length - 1; i += 1) {
      const avgY = (dense[i - 1].y + dense[i].y * 2 + dense[i + 1].y) * 0.25;
      if (avgY > dense[i].y) dense[i].y = avgY;
    }
    return dense;
  }

  focusWorldPoint(xCm, yCm, zCm = 0) {
    const pt = this.worldTo3D(xCm, yCm, zCm);
    this.controls.target.copy(pt);
    this.camera.position.set(pt.x + 55, pt.y + 68, pt.z + 82);
    this.controls.update();
  }

  async load(replay, config3d, mapLayer = "default") {
    this.replay = replay;
    this.config3d = config3d || {};
    this.mapLayer = mapLayer;
    this._clearDynamicScene();

    const relDefault = `./${String(replay.map_image || "static/maps/erangel.webp").replace(/^\/+/, "")}`;
    this._defaultTex = await this._loadTexture(withAssetVer(relDefault));
    if (replay.terrain_image) {
      const relTer = `./${String(replay.terrain_image).replace(/^\/+/, "")}`;
      this._terrainTex = await this._loadTexture(withAssetVer(relTer)).catch(() => null);
    }

    const hmUrl = config3d?.erangel?.heightmap_url || (replay.heightmap_image ? `./${replay.heightmap_image}` : "");
    if (hmUrl) {
      await this._loadHeightmap(withAssetVer(hmUrl));
    }
    this._buildTerrain();
    this._buildZones();

    // Auto-load built-in or user-provided Erangel 3D buildings GLB (version-busted so updates apply immediately!)
    const glbUrl = config3d?.erangel?.buildings_url;
    if (glbUrl) {
      await this._loadBuildingsGlb(withAssetVer(glbUrl), glbUrl.includes("assets_3d/"));
    }
    this.resetView();

    // Progressive 4K texture upgrade on GPUs supporting >= 4096 textures (background, non-blocking)
    const maxTex = this.renderer?.capabilities?.maxTextureSize || 2048;
    if (maxTex >= 4096 && relDefault.endsWith("/erangel.webp")) {
      this._loadTexture(withAssetVer("./static/maps/hd/erangel_4k.webp"))
        .then((hdTex) => {
          if (!this.terrainMesh || !this.replay) {
            hdTex.dispose();
            return;
          }
          const prev = this._defaultTex;
          this._defaultTex = hdTex;
          if (this.mapLayer !== "terrain" && this.terrainMesh.material) {
            this.terrainMesh.material.map = hdTex;
            this.terrainMesh.material.needsUpdate = true;
          }
          if (prev && prev !== hdTex) prev.dispose();
        })
        .catch(() => {});
    }
  }

  setMapLayer(layer) {
    this.mapLayer = layer === "terrain" ? "terrain" : "default";
    if (this.terrainMesh?.material) {
      const tex = this.mapLayer === "terrain" && this._terrainTex ? this._terrainTex : this._defaultTex;
      if (tex) {
        this.terrainMesh.material.map = tex;
        this.terrainMesh.material.needsUpdate = true;
      }
    }
  }

  _loadTexture(url) {
    return new Promise((resolve, reject) => {
      this._texLoader.load(
        url,
        (tex) => {
          tex.colorSpace = THREE.SRGBColorSpace;
          tex.anisotropy = 8;
          resolve(tex);
        },
        undefined,
        reject
      );
    });
  }

  _loadHeightmap(url) {
    return new Promise((resolve) => {
      const img = new Image();
      img.crossOrigin = "anonymous";
      img.onload = () => {
        const c = document.createElement("canvas");
        c.width = img.width;
        c.height = img.height;
        const ctx = c.getContext("2d");
        ctx.drawImage(img, 0, 0);
        this.heightPixels = ctx.getImageData(0, 0, img.width, img.height).data;
        this.heightSize = img.width;
        resolve();
      };
      img.onerror = () => resolve();
      img.src = url;
    });
  }

  _buildTerrain() {
    if (this.terrainMesh) {
      this.scene.remove(this.terrainMesh);
      this.terrainMesh.geometry.dispose();
      this.terrainMesh.material.dispose();
    }
    if (this.waterMesh) {
      this.scene.remove(this.waterMesh);
      this.waterMesh.geometry.dispose();
      this.waterMesh.material.dispose();
    }

    const geo = new THREE.PlaneGeometry(WORLD_SIZE, WORLD_SIZE, TERRAIN_SEGMENTS, TERRAIN_SEGMENTS);
    geo.rotateX(-Math.PI / 2);

    if (this.heightPixels && this.heightSize) {
      const pos = geo.attributes.position;
      for (let i = 0; i < pos.count; i += 1) {
        const x = pos.getX(i);
        const z = pos.getZ(i);
        const nx = x / WORLD_SIZE + 0.5;
        const nz = z / WORLD_SIZE + 0.5;
        pos.setY(i, this.sampleTerrainHeight(nx, nz));
      }
      pos.needsUpdate = true;
      geo.computeVertexNormals();
    }

    const activeTex = this.mapLayer === "terrain" && this._terrainTex ? this._terrainTex : this._defaultTex;
    const mat = new THREE.MeshStandardMaterial({
      map: activeTex,
      roughness: 0.92,
      metalness: 0.04,
    });
    this.terrainMesh = new THREE.Mesh(geo, mat);
    this.scene.add(this.terrainMesh);

    // Subtle ocean surface plane
    const waterGeo = new THREE.PlaneGeometry(WORLD_SIZE * 1.12, WORLD_SIZE * 1.12);
    waterGeo.rotateX(-Math.PI / 2);
    const waterMat = new THREE.MeshStandardMaterial({
      color: 0x0c3454,
      transparent: true,
      opacity: 0.42,
      roughness: 0.28,
      metalness: 0.15,
    });
    this.waterMesh = new THREE.Mesh(waterGeo, waterMat);
    this.waterMesh.position.y = SEA_LEVEL_Y;
    this.scene.add(this.waterMesh);
  }

  setBuildingsVisible(visible) {
    if (this.buildingsGroup) {
      this.buildingsGroup.visible = Boolean(visible);
    }
  }

  async _loadBuildingsGlb(url, isCustomOverride = false) {
    if (this.buildingsGroup) {
      this.scene.remove(this.buildingsGroup);
      this.buildingsGroup = null;
    }
    const loader = new GLTFLoader();
    try {
      const gltf = await loader.loadAsync(url);
      const root = gltf.scene;
      // Bright sunlit warm cream-yellow architectural palette matching pubgmaps.gg reference
      // (outward & upward normals baked into built-in GLB guarantee bright sunlit roofs + clean shaded walls!)
      const whiteBoxMat = new THREE.MeshStandardMaterial({
        color: 0xf7f2b4,
        emissive: 0x5c572c,
        emissiveIntensity: 0.34,
        roughness: 0.82,
        metalness: 0.0,
        side: isCustomOverride ? THREE.DoubleSide : THREE.FrontSide,
      });
      root.traverse((obj) => {
        if (obj.isMesh && !obj.material?.map) {
          obj.material = whiteBoxMat;
        }
      });

      // Auto-scale oversized raw cm models to WORLD_SIZE if needed
      const box = new THREE.Box3().setFromObject(root);
      const size = new THREE.Vector3();
      box.getSize(size);
      const maxSpan = Math.max(size.x, size.z);
      if (maxSpan > WORLD_SIZE * 2) {
        const s = WORLD_SIZE / maxSpan;
        root.scale.setScalar(s);
        const scaledBox = new THREE.Box3().setFromObject(root);
        const center = new THREE.Vector3();
        scaledBox.getCenter(center);
        root.position.set(-center.x, SEA_LEVEL_Y - scaledBox.min.y, -center.z);
      }

      this.buildingsGroup = root;
      this.scene.add(root);
    } catch {
      // Non-fatal: continue with 3D terrain if GLB fails to parse
    }
  }

  _buildZones() {
    if (this.safeWall) {
      this.scene.remove(this.safeWall);
      this.safeWall.geometry?.dispose?.();
      this.safeWall.material?.dispose?.();
    }
    if (this.blueWall) {
      this.scene.remove(this.blueWall);
      this.blueWall.geometry?.dispose?.();
      this.blueWall.material?.dispose?.();
    }

    const makeRingMesh = (colorHex) => {
      const mat = new THREE.MeshBasicMaterial({
        color: colorHex,
        transparent: true,
        opacity: 0.92,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
      const mesh = new THREE.Mesh(new THREE.BufferGeometry(), mat);
      mesh.visible = false;
      mesh.frustumCulled = false;
      mesh.renderOrder = 4;
      return mesh;
    };

    this.safeWall = makeRingMesh(0xffffff);
    this.blueWall = makeRingMesh(0x38bdf8);
    this.scene.add(this.safeWall, this.blueWall);
    this._lastZoneSig = "";
  }

  _updateZoneRingLine(meshObj, cxCm, cyCm, radiusCm, sizeCm, yOffset, halfWidth) {
    if (!meshObj) return;
    if (!radiusCm || radiusCm <= 0) {
      meshObj.visible = false;
      return;
    }
    const segs = 160;
    const pts = [];
    for (let i = 0; i < segs; i += 1) {
      const theta = (i / segs) * Math.PI * 2;
      const wxCm = cxCm + Math.cos(theta) * radiusCm;
      const wyCm = cyCm + Math.sin(theta) * radiusCm;
      const nx = wxCm / sizeCm;
      const nz = wyCm / sizeCm;
      const wx = (nx - 0.5) * WORLD_SIZE;
      const wz = (nz - 0.5) * WORLD_SIZE;
      const clampedNx = Math.max(0, Math.min(1, nx));
      const clampedNz = Math.max(0, Math.min(1, nz));
      const wy = Math.max(SEA_LEVEL_Y, this.sampleTerrainHeight(clampedNx, clampedNz)) + yOffset;
      pts.push(new THREE.Vector3(wx, wy, wz));
    }
    const geo = buildRibbonGeometry(pts, halfWidth, { closed: true });
    if (!geo) {
      meshObj.visible = false;
      return;
    }
    meshObj.geometry?.dispose?.();
    meshObj.geometry = geo;
    meshObj.visible = true;
  }

  _clearDynamicScene() {
    for (const grp of [this.trailsGroup, this.playersGroup, this.combatGroup]) {
      while (grp.children.length > 0) {
        const ch = grp.children[0];
        grp.remove(ch);
        ch.geometry?.dispose?.();
        ch.material?.dispose?.();
      }
    }
    this._playerSprites.clear();
    this._playerPins.clear();
    this._trailMeshes.clear();
    this._lastTrailKey = "";
    this._lastZoneSig = "";
  }

  _getBadgeSpriteMaterial(key, text, color, highlight) {
    const cacheKey = `${key}:${text}:${color}:${highlight}`;
    let mat = this._badgeCache.get(cacheKey);
    if (!mat) {
      const tex = makeBadgeTexture(text, color, highlight);
      mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
      this._badgeCache.set(cacheKey, mat);
    }
    return mat;
  }

  render(state) {
    if (!this.replay) return 0;
    const { currentTime, trackedIds, followId, flashEvent } = state;
    const sizeCm = this.replay.map_size || 816000;

    const camTargetDist = Math.max(20, this.camera.position.distanceTo(this.controls.target));
    const worldPerPxAtTarget = computeWorldPerPx(this.camera, this.host, camTargetDist);
    const widthBucket = Math.round(Math.log2(camTargetDist) * 4);

    // 1. Update terrain-hugging 3D safe zone (thin white line) & bluezone (thin cyan line)
    const zones = this.replay.zones || [];
    if (zones.length > 0 && this.safeWall && this.blueWall) {
      let zf = zones[0];
      for (let i = 0; i < zones.length; i += 1) {
        if (zones[i][0] <= currentTime) zf = zones[i];
        else break;
      }
      const [, sx, sy, sr, px, py, pr] = zf;
      const zoneSig = `${Math.round(sx)},${Math.round(sy)},${Math.round(sr)},${Math.round(px)},${Math.round(py)},${Math.round(pr)}@${widthBucket}`;
      if (zoneSig !== this._lastZoneSig) {
        this._lastZoneSig = zoneSig;
        // Sleek ~1.8px screen-space line matching the 2D blue/white zone rings
        const zoneHW = Math.max(0.14, Math.min(0.85, worldPerPxAtTarget * 0.95));
        this._updateZoneRingLine(this.safeWall, sx, sy, sr, sizeCm, 1.35, zoneHW);
        this._updateZoneRingLine(this.blueWall, px, py, pr, sizeCm, 1.55, zoneHW * 1.12);
      }
    }

    // 2. Update 3D player markers & follow camera
    const players = this.replay.players || {};
    const tracks = this.replay.tracks || {};
    let aliveCount = 0;

    for (const p of Object.values(players)) {
      const track = tracks[p.account_id];
      if (!track || track.length === 0) continue;
      const pos = interpolateTrack(track, currentTime);
      if (!pos) continue;
      const rawPt = track[pos.idx] || track[0];
      const zCm = rawPt.length >= 6 ? rawPt[5] : 0;
      const isDead = pos.state === 2;
      if (!isDead) aliveCount += 1;

      const isTracked = trackedIds.has(p.account_id);
      const isHighlight = p.is_squad || isTracked;
      const color = getPlayerVisualColor(p, this.replay);
      const badgeText = p.is_squad ? String(p.squad_slot || p.team_id || "") : String(p.team_id || "");

      let sprite = this._playerSprites.get(p.account_id);
      const mat = this._getBadgeSpriteMaterial(p.account_id, badgeText, pos.state === 1 ? "#f97316" : color, isHighlight);
      if (!sprite) {
        sprite = new THREE.Sprite(mat);
        sprite.userData = { accountId: p.account_id };
        sprite.renderOrder = isHighlight ? 20 : 10;
        this.playersGroup.add(sprite);
        this._playerSprites.set(p.account_id, sprite);
      } else {
        sprite.material = mat;
        sprite.renderOrder = isHighlight ? 20 : 10;
      }

      const wPos = this.worldTo3D(pos.x, pos.y, zCm);
      sprite.visible = !isDead || isHighlight;
      sprite.material.opacity = isDead ? 0.38 : 1.0;

      // 3-stage adaptive pixel-clamped scaling: readable far away, never oversized balloons up close!
      const viewportH = Math.max(240, this.host.clientHeight || window.innerHeight || 600);
      const camDist = Math.max(15, this.camera.position.distanceTo(wPos));
      const worldPerPx = (2 * camDist * Math.tan(THREE.MathUtils.degToRad(this.camera.fov * 0.5))) / viewportH;
      const targetPx = compute3DIconPixelSize(camDist, p.is_squad, isTracked);
      const scaleSize = Math.max(1.2, Math.min(26, targetPx * worldPerPx));
      const hoverY = Math.max(1.2, Math.min(6.0, scaleSize * 0.48));

      sprite.position.set(wPos.x, wPos.y + hoverY, wPos.z);
      sprite.scale.set(scaleSize, scaleSize, 1);

      if (followId === p.account_id) {
        this.controls.target.lerp(wPos, 0.14);
      }
    }

    // 3. Rebuild crisp thin 2D-style dashed terrain-conforming trails (dark outline + clean gaps)
    const trailKey = `${[...trackedIds].sort().join(",")}`;
    if (
      trailKey !== this._lastTrailKey ||
      widthBucket !== this._lastTrailWidthBucket ||
      Math.abs(currentTime - this._lastTrailTime) >= 0.35
    ) {
      this._lastTrailKey = trailKey;
      this._lastTrailTime = currentTime;
      this._lastTrailWidthBucket = widthBucket;
      while (this.trailsGroup.children.length > 0) {
        const ch = this.trailsGroup.children[0];
        this.trailsGroup.remove(ch);
        ch.geometry?.dispose?.();
        ch.material?.dispose?.();
      }

      // Screen-space thin dashed width (~3.4px total including dark outline -> ~1.9px colored core, matching 2D!)
      const baseHalfW = Math.max(0.12, Math.min(0.95, worldPerPxAtTarget * 1.85));
      const dashPeriod = Math.max(0.9, Math.min(14, worldPerPxAtTarget * 13.5));

      let layerIdx = 0;
      for (const aid of trackedIds) {
        const track = tracks[aid];
        const p = players[aid];
        if (!track || track.length < 2 || !p) continue;

        // Tiny per-player height stratification prevents z-fighting when squadmates ride in the same car
        const slotIdx = p.is_squad ? (Number(p.squad_slot || 1) - 1) : (4 + (layerIdx % 6));
        const yBias = 1.05 + slotIdx * 0.12;
        layerIdx += 1;

        const pts = this._buildTerrainConformingTrailPoints(track, currentTime, yBias);
        if (pts.length < 2) continue;

        const hw = p.is_squad ? baseHalfW : baseHalfW * 0.85;
        const colorStr = getPlayerVisualColor(p, this.replay);
        const geo = buildRibbonGeometry(pts, hw, { closed: false, uvPeriod: dashPeriod });
        if (geo) {
          const mat = new THREE.MeshBasicMaterial({
            color: new THREE.Color(colorStr),
            map: this._dashTex,
            transparent: true,
            opacity: 0.96,
            alphaTest: 0.06,
            side: THREE.DoubleSide,
            depthWrite: false,
          });
          const mesh = new THREE.Mesh(geo, mat);
          mesh.renderOrder = 6 + slotIdx;
          this.trailsGroup.add(mesh);
        }
      }
    }

    // 4. 3D combat laser beams for recent/highlighted kills & knocks
    while (this.combatGroup.children.length > 0) {
      const ch = this.combatGroup.children[0];
      this.combatGroup.remove(ch);
      ch.geometry?.dispose?.();
      ch.material?.dispose?.();
    }
    const activeEvs = (this.replay.events || []).filter(
      (ev) =>
        (ev.type === "kill" || ev.type === "knock" || ev.type === "team_damage") &&
        Math.abs(ev.t - currentTime) <= 3.5 &&
        (ev.ax || ev.vx)
    );
    if (flashEvent && !activeEvs.includes(flashEvent)) activeEvs.push(flashEvent);
    const beamHW = Math.max(0.12, Math.min(0.65, worldPerPxAtTarget * 0.95));
    for (const ev of activeEvs) {
      const a = this.worldTo3D(ev.ax || ev.vx, ev.ay || ev.vy, ev.az || 0, { yBias: 2.0 });
      const v = this.worldTo3D(ev.vx || ev.ax, ev.vy || ev.ay, ev.vz || 0, { yBias: 2.0 });
      const mid = a.clone().lerp(v, 0.5);
      mid.y += Math.min(7, a.distanceTo(v) * 0.07);
      const colorHex = ev.is_team_kill ? 0xef4444 : ev.type === "kill" ? 0xfacc15 : 0xfb923c;
      const beamGeo = buildRibbonGeometry([a, mid, v], beamHW, { closed: false });
      if (beamGeo) {
        const mat = new THREE.MeshBasicMaterial({
          color: colorHex,
          transparent: true,
          opacity: 0.94,
          side: THREE.DoubleSide,
          depthWrite: false,
        });
        const mesh = new THREE.Mesh(beamGeo, mat);
        mesh.renderOrder = 18;
        this.combatGroup.add(mesh);
      }
    }

    this._applyKeyboardPan();
    this.controls.update();
    // Gently keep pan target inside map bounds while staying completely free across the entire island
    const limit = WORLD_SIZE * 0.56;
    const clampedX = Math.max(-limit, Math.min(limit, this.controls.target.x));
    const clampedZ = Math.max(-limit, Math.min(limit, this.controls.target.z));
    if (clampedX !== this.controls.target.x || clampedZ !== this.controls.target.z) {
      this.camera.position.x += clampedX - this.controls.target.x;
      this.camera.position.z += clampedZ - this.controls.target.z;
      this.controls.target.x = clampedX;
      this.controls.target.z = clampedZ;
    }
    this.renderer.render(this.scene, this.camera);
    return aliveCount;
  }

  dispose() {
    window.removeEventListener("keydown", this._keyDownHandler);
    window.removeEventListener("keyup", this._keyUpHandler);
    this._clearDynamicScene();
    for (const mat of this._badgeCache.values()) {
      mat.map?.dispose?.();
      mat.dispose();
    }
    this._badgeCache.clear();
    this._dashTex?.dispose?.();
    this.controls.dispose();
    this.renderer.dispose();
    this.host.innerHTML = "";
  }
}
