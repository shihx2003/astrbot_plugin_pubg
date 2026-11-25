/**
 * 2D Replay playback state machine: timeline, speeds (1x-20x), map layer switching,
 * arbitrary player & full-team tracking, killer-squad comparison, and jump-to-event (T - 3s).
 */

import { MapRenderer } from "./map-renderer.js";

const SPEEDS = [1, 2, 5, 10, 20];

export function formatClock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${String(m).padStart(2, "0")}:${String(rem).padStart(2, "0")}`;
}

export class ReplayController {
  constructor(canvas, callbacks = {}) {
    this.callbacks = callbacks;
    this.renderer = new MapRenderer(canvas, (accountId) => this.toggleTrack(accountId));
    this.renderer3d = null;
    this.dimMode = "2d"; // "2d" | "3d"
    this.replay = null;
    this.playing = false;
    this.speedIndex = 2; // default 5x
    this.currentTime = 0;
    this.duration = 0;
    this.mapLayer = "default";
    this.showPlane = true;
    this.showAirdrops = true;
    this.trackedIds = new Set();
    this.followId = null;
    this.flashEvent = null;
    this._flashUntil = 0;
    this._rafId = null;
    this._lastFrameTs = 0;
  }

  get speed() {
    return SPEEDS[this.speedIndex];
  }

  load(replay) {
    this.replay = replay;
    this.duration = Math.max(1, Number(replay.duration || 0));
    this.currentTime = 0;
    this.playing = false;
    this.mapLayer = "default";
    this.followId = null;
    this.flashEvent = null;
    this.trackedIds = new Set();

    for (const p of Object.values(replay.players || {})) {
      if (p.is_squad) {
        this.trackedIds.add(p.account_id);
      }
    }
    if (this.trackedIds.size === 0) {
      const first4 = Object.values(replay.players || {}).slice(0, 4);
      for (const p of first4) {
        this.trackedIds.add(p.account_id);
      }
    }

    this.renderer.setReplay(replay);
    this._startLoop();
    this._notifyState();
  }

  destroy() {
    this.playing = false;
    this.dimMode = "2d";
    if (this.renderer3d) {
      this.renderer3d.dispose();
      this.renderer3d = null;
    }
    if (this._rafId !== null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
  }

  attach3DRenderer(renderer3d) {
    if (this.renderer3d && this.renderer3d !== renderer3d) {
      this.renderer3d.dispose();
    }
    this.renderer3d = renderer3d;
  }

  setDimMode(mode) {
    this.dimMode = mode === "3d" && this.renderer3d ? "3d" : "2d";
    this.resizeActive();
    this._notifyState();
  }

  resizeActive() {
    this.renderer.resize();
    if (this.renderer3d) this.renderer3d.resize();
  }

  zoomActive(factor) {
    if (this.dimMode === "3d" && this.renderer3d) {
      this.renderer3d.zoomBy(factor);
    } else {
      this.renderer.zoomBy(factor);
    }
  }

  resetActiveView() {
    this.setFollow(null);
    if (this.dimMode === "3d" && this.renderer3d) {
      this.renderer3d.resetView();
    } else {
      this.renderer.resetView();
    }
  }

  setMapLayer(layer) {
    if (!this.replay) return;
    if (layer === "terrain" && !this.replay.terrain_image) return;
    this.mapLayer = layer === "terrain" ? "terrain" : "default";
    this.renderer.setMapLayer(this.mapLayer);
    if (this.renderer3d) this.renderer3d.setMapLayer(this.mapLayer);
    this._notifyState();
  }

  setShowPlane(visible) {
    this.showPlane = Boolean(visible);
    this._notifyState();
  }

  setShowAirdrops(visible) {
    this.showAirdrops = Boolean(visible);
    this._notifyState();
  }

  togglePlay() {
    if (!this.replay) return;
    if (this.currentTime >= this.duration) {
      this.currentTime = 0;
    }
    this.playing = !this.playing;
    this._lastFrameTs = performance.now();
    this._notifyState();
  }

  seek(t) {
    this.currentTime = Math.max(0, Math.min(this.duration, Number(t || 0)));
    this._notifyState();
  }

  cycleSpeed() {
    this.speedIndex = (this.speedIndex + 1) % SPEEDS.length;
    this._notifyState();
    return this.speed;
  }

  toggleTrack(accountId) {
    if (!accountId) return;
    if (this.trackedIds.has(accountId)) {
      this.trackedIds.delete(accountId);
      if (this.followId === accountId) {
        this.followId = null;
      }
    } else {
      this.trackedIds.add(accountId);
    }
    this._notifyState();
  }

  trackTeam(teamId) {
    if (!this.replay || !teamId) return;
    const tid = Number(teamId);
    const members = Object.values(this.replay.players || {}).filter((p) => Number(p.team_id) === tid);
    const allTracked = members.length > 0 && members.every((p) => this.trackedIds.has(p.account_id));
    for (const m of members) {
      if (allTracked) this.trackedIds.delete(m.account_id);
      else this.trackedIds.add(m.account_id);
    }
    this._notifyState();
  }

  setFollow(accountId) {
    if (!accountId || this.followId === accountId) {
      this.followId = null;
    } else {
      this.followId = accountId;
      this.trackedIds.add(accountId);
    }
    this._notifyState();
  }

  trackSquadOnly() {
    if (!this.replay) return;
    this.trackedIds = new Set();
    for (const p of Object.values(this.replay.players || {})) {
      if (p.is_squad) this.trackedIds.add(p.account_id);
    }
    this._notifyState();
  }

  clearExtraTracks() {
    this.trackSquadOnly();
  }

  trackKillerSquad() {
    if (!this.replay) return 0;
    const players = this.replay.players || {};
    const squadIds = new Set(
      Object.values(players)
        .filter((p) => p.is_squad)
        .map((p) => p.account_id)
    );
    const killerTeamIds = new Set();
    for (const ev of this.replay.events || []) {
      if (ev.type === "kill" && squadIds.has(ev.victim_id) && ev.attacker_id && !squadIds.has(ev.attacker_id)) {
        const kp = players[ev.attacker_id];
        if (kp && kp.team_id) killerTeamIds.add(kp.team_id);
      }
    }
    if (killerTeamIds.size === 0) return 0;
    let added = 0;
    for (const p of Object.values(players)) {
      if (killerTeamIds.has(p.team_id)) {
        this.trackedIds.add(p.account_id);
        added += 1;
      }
    }
    this._notifyState();
    return added;
  }

  jumpToEvent(ev) {
    if (!ev || !this.replay) return;
    this.currentTime = Math.max(0, Number(ev.t || 0) - 3);
    this.playing = false;
    this.flashEvent = ev;
    this._flashUntil = performance.now() + 6500;
    this.followId = null;

    if (ev.attacker_id && this.replay.players?.[ev.attacker_id]) {
      this.trackedIds.add(ev.attacker_id);
    }
    if (ev.victim_id && this.replay.players?.[ev.victim_id]) {
      this.trackedIds.add(ev.victim_id);
    }

    const focusX = ev.vx || ev.ax;
    const focusY = ev.vy || ev.ay;
    const focusZ = ev.vz || ev.az || 0;
    if (focusX || focusY) {
      this.renderer.focusWorldPoint(focusX, focusY, Math.max(this.renderer.scale, 3.2));
      if (this.renderer3d) {
        this.renderer3d.focusWorldPoint(focusX, focusY, focusZ);
      }
    }
    this._notifyState();
  }

  _startLoop() {
    if (this._rafId !== null) cancelAnimationFrame(this._rafId);
    this._lastFrameTs = performance.now();
    const tick = (now) => {
      const dt = Math.min(0.1, (now - this._lastFrameTs) / 1000);
      this._lastFrameTs = now;
      if (this.playing && this.replay) {
        this.currentTime += dt * this.speed;
        if (this.currentTime >= this.duration) {
          this.currentTime = this.duration;
          this.playing = false;
          this._notifyState();
        } else if (this.callbacks.onTimeTick) {
          this.callbacks.onTimeTick(this.currentTime, this.duration);
        }
      }
      if (this.flashEvent && now > this._flashUntil) {
        this.flashEvent = null;
      }
      const renderState = {
        currentTime: this.currentTime,
        trackedIds: this.trackedIds,
        followId: this.followId,
        flashEvent: this.flashEvent,
        showPlane: this.showPlane,
        showAirdrops: this.showAirdrops,
      };
      const aliveCount =
        this.dimMode === "3d" && this.renderer3d
          ? this.renderer3d.render(renderState)
          : this.renderer.render(renderState);
      if (this.callbacks.onFrame) {
        this.callbacks.onFrame(aliveCount);
      }
      this._rafId = requestAnimationFrame(tick);
    };
    this._rafId = requestAnimationFrame(tick);
  }

  _notifyState() {
    if (this.callbacks.onStateChange) {
      this.callbacks.onStateChange({
        playing: this.playing,
        speed: this.speed,
        currentTime: this.currentTime,
        duration: this.duration,
        mapLayer: this.mapLayer,
        dimMode: this.dimMode,
        showPlane: this.showPlane,
        showAirdrops: this.showAirdrops,
        trackedIds: this.trackedIds,
        followId: this.followId,
      });
    }
  }
}
