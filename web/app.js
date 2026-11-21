(() => {
  "use strict";

  const PROTOCOL_VERSION = "1.0.0";
  const STORAGE_DEVICE_ID = "gcs_shimeji_web_device_id";

  // UI elements
  const canvas = document.getElementById("petCanvas");
  const ctx = canvas.getContext("2d", { alpha: true });
  const stageEl = document.getElementById("stage");
  const deviceLabel = document.getElementById("deviceLabel");
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");
  const bannerEl = document.getElementById("banner");
  const metaInfo = document.getElementById("metaInfo");
  const emptyOverlay = document.getElementById("emptyOverlay");
  const emptyTitle = document.getElementById("emptyTitle");
  const emptyHint = document.getElementById("emptyHint");
  const offerOverlay = document.getElementById("offerOverlay");
  const offerSubtitle = document.getElementById("offerSubtitle");
  const offerProgress = document.getElementById("offerProgress");
  const acceptBtn = document.getElementById("acceptBtn");
  const declineBtn = document.getElementById("declineBtn");
  const jumpBtn = document.getElementById("jumpBtn");
  const sendBtn = document.getElementById("sendBtn");
  const edgeLeft = document.getElementById("edgeLeft");
  const edgeRight = document.getElementById("edgeRight");
  const edgeTop = document.getElementById("edgeTop");

  const DEFAULT_ANIMS = {
    idle: { row: 0, frames: 4, fps: 4, loop: true },
    walk: { row: 1, frames: 6, fps: 8, loop: true },
    run: { row: 2, frames: 6, fps: 12, loop: true },
    jump: { row: 3, frames: 3, fps: 8, loop: false },
    sleep: { row: 4, frames: 4, fps: 2, loop: true },
    grab: { row: 5, frames: 2, fps: 6, loop: true },
    handoff: { row: 6, frames: 4, fps: 10, loop: false },
    receive: { row: 7, frames: 4, fps: 10, loop: false },
    landing: { row: 8, frames: 3, fps: 10, loop: false },
  };

  function getOrCreateDeviceId() {
    if (window.GcsAndroidBridge && typeof window.GcsAndroidBridge.getDeviceId === "function") {
      try {
        const nativeId = window.GcsAndroidBridge.getDeviceId();
        if (nativeId && nativeId.trim()) return nativeId.trim();
      } catch (_) {}
    }
    try {
      const existing = localStorage.getItem(STORAGE_DEVICE_ID);
      if (existing) return existing;
      const suffix = Math.random().toString(16).slice(2, 10);
      const created = `android-web-${suffix}`;
      localStorage.setItem(STORAGE_DEVICE_ID, created);
      return created;
    } catch (_) {
      return `android-web-${Math.random().toString(16).slice(2, 10)}`;
    }
  }

  function triggerHaptic(kind) {
    if (window.GcsAndroidBridge && typeof window.GcsAndroidBridge.vibrate === "function") {
      try {
        window.GcsAndroidBridge.vibrate(kind || "light");
        return;
      } catch (_) {}
    }
    if (navigator.vibrate) {
      navigator.vibrate(kind === "offer" ? [45, 50, 45] : 22);
    }
  }

  function notifyAndroidStatus(status, message) {
    if (window.GcsAndroidBridge && typeof window.GcsAndroidBridge.onConnectionStatus === "function") {
      try {
        window.GcsAndroidBridge.onConnectionStatus(status, message || "");
      } catch (_) {}
    }
  }

  function randomId(prefix) {
    return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(16).slice(2, 8)}`;
  }

  const state = {
    deviceId: getOrCreateDeviceId(),
    deviceName: (window.GcsAndroidBridge && window.GcsAndroidBridge.getDeviceName)
      ? (() => { try { return window.GcsAndroidBridge.getDeviceName(); } catch (_) { return "Android App"; } })()
      : "Android Web Companion",
    wsUrl: null,
    ws: null,
    connected: false,
    reconnectDelayMs: 1000,
    reconnectTimer: null,
    pingTimer: null,

    // Avatar sprite config
    frameW: 32,
    frameH: 32,
    scale: 4,
    animations: { ...DEFAULT_ANIMS },
    spriteSheet: null,
    spriteLoaded: false,

    // Hub authoritative state
    ownerDeviceId: null,
    handoffState: "IDLE",
    devices: [],
    pendingOutgoingId: null,
    incomingOffer: null, // { handoff_id, source_device_id, source_device_name, edge, expires_at_ms, timeout_ms }

    // Local pet physics & animation
    stageW: 360,
    stageH: 540,
    x: 140,
    y: 360,
    vx: 0,
    vy: 0,
    facing: 1,
    grounded: true,
    animation: "idle",
    animFrame: 0,
    animAcc: 0,
    aiTimer: 2.0,
    aiMode: "idle",

    // Drag & edge handoff
    dragging: false,
    dragPointerId: null,
    dragOffsetX: 0,
    dragOffsetY: 0,
    activeEdge: null,
    edgeDwellSec: 0,
    lastSyncMs: 0,
  };

  deviceLabel.textContent = state.deviceId;

  function setConnectionUi(mode, bannerText) {
    statusBadge.classList.remove("connected", "reconnecting", "offline");
    statusBadge.classList.add(mode);
    statusText.textContent = mode.toUpperCase();
    if (bannerText) bannerEl.textContent = bannerText;
    notifyAndroidStatus(mode, bannerText || "");
  }

  function resizeCanvas() {
    const rect = stageEl.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    state.stageW = Math.max(200, Math.floor(rect.width));
    state.stageH = Math.max(240, Math.floor(rect.height));
    canvas.width = Math.floor(state.stageW * dpr);
    canvas.height = Math.floor(state.stageH * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;

    const floorY = getFloorY();
    if (state.y > floorY) state.y = floorY;
    const petW = getPetW();
    state.x = Math.max(0, Math.min(state.x, Math.max(0, state.stageW - petW)));
  }

  function getPetW() {
    return state.frameW * state.scale;
  }

  function getPetH() {
    return state.frameH * state.scale;
  }

  function getFloorY() {
    return Math.max(20, Math.floor(state.stageH * 0.82) - getPetH());
  }

  function setAnimation(name) {
    if (!state.animations[name]) name = "idle";
    if (state.animation !== name) {
      state.animation = name;
      state.animFrame = 0;
      state.animAcc = 0;
    }
  }

  function isLocalPetVisible() {
    return (
      state.ownerDeviceId === state.deviceId &&
      state.handoffState !== "PENDING_CONFIRMATION" &&
      !state.pendingOutgoingId
    );
  }

  function connectedPeers() {
    return state.devices.filter((d) => d.connected !== false);
  }

  function findWindowsPeer() {
    const peers = connectedPeers();
    return (
      peers.find((d) => d.device_id !== state.deviceId && d.device_type === "windows") ||
      peers.find((d) => d.device_id !== state.deviceId) ||
      null
    );
  }

  function updateOverlays() {
    const visible = isLocalPetVisible();
    const hasWinPeer = Boolean(findWindowsPeer());
    sendBtn.disabled = !(state.connected && visible && hasWinPeer && !state.pendingOutgoingId);
    jumpBtn.disabled = !visible;

    if (visible) {
      emptyOverlay.classList.add("hidden");
    } else {
      emptyOverlay.classList.remove("hidden");
      if (!state.connected) {
        emptyTitle.textContent = "Connecting to Hub...";
        emptyHint.textContent = "Keep your Android phone and Windows PC on the same local Wi-Fi.";
      } else if (state.pendingOutgoingId) {
        emptyTitle.textContent = "Sending Pet to Windows...";
        emptyHint.textContent = "Show an open palm on the Windows camera or click Accept on PC.";
      } else {
        emptyTitle.textContent = "Pet is on Windows PC";
        emptyHint.textContent = "Drag the pet to your Windows monitor edge (or right-click -> Send to Android).";
      }
    }

    if (state.incomingOffer) {
      offerOverlay.classList.remove("hidden");
      const now = Date.now();
      const remMs = Math.max(0, state.incomingOffer.expires_at_ms - now);
      const frac = Math.max(0, Math.min(1, remMs / Math.max(1000, state.incomingOffer.timeout_ms)));
      offerProgress.style.width = `${(frac * 100).toFixed(1)}%`;
      const sender = state.incomingOffer.source_device_name || state.incomingOffer.source_device_id;
      offerSubtitle.textContent = `From ${sender} — ${(remMs / 1000).toFixed(1)}s left`;
      if (remMs <= 0) {
        state.incomingOffer = null;
        offerOverlay.classList.add("hidden");
      }
    } else {
      offerOverlay.classList.add("hidden");
    }

    metaInfo.textContent = `Peers: ${connectedPeers().length} | Anim: ${state.animation}`;
  }

  function updateEdgeGlow(edge) {
    edgeLeft.classList.toggle("active", edge === "left");
    edgeRight.classList.toggle("active", edge === "right");
    edgeTop.classList.toggle("active", edge === "top");
  }

  function sendEnvelope(type, payload) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    const envelope = {
      type,
      protocol_version: PROTOCOL_VERSION,
      message_id: randomId("msg"),
      timestamp: Date.now() / 1000,
      payload: payload || {},
    };
    state.ws.send(JSON.stringify(envelope));
  }

  function applyAuthoritativeState(st) {
    if (!st) return;
    const prevOwner = state.ownerDeviceId;
    state.ownerDeviceId = st.owner || null;
    state.handoffState = st.handoff_state || "IDLE";

    if (state.ownerDeviceId === state.deviceId && prevOwner !== state.deviceId) {
      const petW = getPetW();
      const floorY = getFloorY();
      const pos = st.position || {};
      const xRaw = typeof pos.x === "number" ? pos.x : (state.stageW - petW) / 2;
      const yRaw = typeof pos.y === "number" ? pos.y : floorY;
      state.x = Math.max(16, Math.min(state.stageW - petW - 16, xRaw <= 1.0 && xRaw >= 0 ? xRaw * (state.stageW - petW) : xRaw));
      state.y = Math.max(24, Math.min(floorY, yRaw <= 1.0 && yRaw >= 0 ? yRaw * floorY : yRaw));
      state.vx = 0;
      state.vy = 0;
      state.grounded = state.y >= floorY - 2;
      setAnimation(st.animation || "receive");
    }

    if (state.handoffState === "IDLE") {
      state.pendingOutgoingId = null;
    }
    updateOverlays();
  }

  function handleMessage(raw) {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch (_) {
      return;
    }
    const type = msg.type;
    const payload = msg.payload || {};

    switch (type) {
      case "register_ack":
        state.devices = payload.devices || [];
        applyAuthoritativeState(payload.avatar_state);
        setConnectionUi("connected", `Connected to Hub (${connectedPeers().length} online)`);
        break;

      case "device_list":
        state.devices = payload.devices || [];
        updateOverlays();
        break;

      case "state_sync":
        applyAuthoritativeState(payload);
        break;

      case "handoff_pending":
        if (payload.source_device_id === state.deviceId) {
          state.pendingOutgoingId = payload.handoff_id;
          state.handoffState = "PENDING_CONFIRMATION";
          bannerEl.textContent = `Sending pet to ${payload.target_device_id}... waiting for confirmation`;
          updateOverlays();
        }
        break;

      case "handoff_offer":
        if (payload.handoff_id) {
          const timeoutMs = (payload.timeout_seconds || 6.0) * 1000;
          state.incomingOffer = {
            handoff_id: payload.handoff_id,
            source_device_id: payload.source_device_id || "windows",
            source_device_name: payload.source_device_name || payload.source_device_id || "Windows PC",
            edge: payload.edge || "left",
            timeout_ms: timeoutMs,
            expires_at_ms: Date.now() + timeoutMs,
          };
          triggerHaptic("offer");
          bannerEl.textContent = `Incoming pet from ${state.incomingOffer.source_device_name}! Tap to receive.`;
          updateOverlays();
        }
        break;

      case "handoff_complete":
        state.pendingOutgoingId = null;
        state.incomingOffer = null;
        state.handoffState = "IDLE";
        if (payload.avatar_state) {
          applyAuthoritativeState(payload.avatar_state);
        } else {
          state.ownerDeviceId = payload.new_owner;
        }
        if (payload.new_owner === state.deviceId) {
          const floorY = getFloorY();
          const petW = getPetW();
          const edge = payload.edge || "left";
          state.x = edge === "right" ? state.stageW - petW - 18 : edge === "left" ? 18 : (state.stageW - petW) / 2;
          state.y = Math.min(floorY, Math.max(30, (payload.normalized_y ?? 0.5) * floorY));
          state.vx = 0;
          state.vy = 0;
          state.grounded = state.y >= floorY - 2;
          triggerHaptic("confirm");
          setAnimation("receive");
          bannerEl.textContent = "Pet received on Android!";
        } else {
          bannerEl.textContent = `Pet transferred to ${payload.new_owner}`;
        }
        updateOverlays();
        break;

      case "handoff_cancel":
        state.pendingOutgoingId = null;
        state.incomingOffer = null;
        state.handoffState = "IDLE";
        if (payload.avatar_state) {
          applyAuthoritativeState(payload.avatar_state);
        }
        bannerEl.textContent = `Transfer cancelled (${payload.reason || "timeout"}) — pet safely restored.`;
        updateOverlays();
        break;

      case "error":
        bannerEl.textContent = `Hub: ${payload.message || payload.code || "notice"}`;
        break;

      default:
        break;
    }
  }

  function requestHandoffToWindows(edge) {
    const target = findWindowsPeer();
    if (!target || !isLocalPetVisible() || state.pendingOutgoingId) return;
    triggerHaptic("light");
    setAnimation("handoff");
    sendEnvelope("handoff_request", {
      source_device_id: state.deviceId,
      target_device_id: target.device_id,
      edge: edge || "top",
      normalized_y: Math.max(0, Math.min(1, state.y / Math.max(1, getFloorY()))),
      idempotency_key: randomId("idem"),
    });
    bannerEl.textContent = `Sending pet to ${target.device_name || target.device_id}...`;
  }

  function confirmIncomingOffer(accepted) {
    if (!state.incomingOffer) return;
    const handoffId = state.incomingOffer.handoff_id;
    triggerHaptic(accepted ? "confirm" : "light");
    sendEnvelope("handoff_confirm", {
      handoff_id: handoffId,
      accepted: Boolean(accepted),
      confirmation_source: "touch",
      landing_position: { x: Math.round((state.stageW - getPetW()) / 2), y: Math.round(getFloorY()) },
    });
    if (!accepted) {
      state.incomingOffer = null;
      updateOverlays();
      bannerEl.textContent = "Handoff declined.";
    } else {
      bannerEl.textContent = "Confirming handoff...";
    }
  }

  function connectWebSocket() {
    if (!state.wsUrl) return;
    clearTimeout(state.reconnectTimer);
    clearInterval(state.pingTimer);

    setConnectionUi("reconnecting", `Connecting to ${state.wsUrl}...`);
    let ws;
    try {
      ws = new WebSocket(state.wsUrl);
    } catch (_) {
      scheduleReconnect();
      return;
    }
    state.ws = ws;

    ws.onopen = () => {
      state.connected = true;
      state.reconnectDelayMs = 1000;
      sendEnvelope("register_device", {
        device_id: state.deviceId,
        device_type: "android",
        device_name: state.deviceName,
        screen_width: state.stageW,
        screen_height: state.stageH,
        capabilities: ["touch", "display", "webview"],
      });
      state.pingTimer = setInterval(() => {
        if (state.ws && state.ws.readyState === WebSocket.OPEN) {
          sendEnvelope("ping", { sent_at: Date.now() / 1000 });
        }
      }, 4500);
    };

    ws.onmessage = (event) => handleMessage(event.data);
    ws.onerror = () => {};
    ws.onclose = () => {
      state.connected = false;
      clearInterval(state.pingTimer);
      state.incomingOffer = null;
      state.pendingOutgoingId = null;
      updateOverlays();
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    setConnectionUi("offline", `Disconnected. Reconnecting in ${(state.reconnectDelayMs / 1000).toFixed(1)}s...`);
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(() => {
      state.reconnectDelayMs = Math.min(12000, Math.floor(state.reconnectDelayMs * 1.6));
      connectWebSocket();
    }, state.reconnectDelayMs);
  }

  function getCanvasPoint(evt) {
    const rect = canvas.getBoundingClientRect();
    return { x: evt.clientX - rect.left, y: evt.clientY - rect.top };
  }

  canvas.addEventListener("pointerdown", (evt) => {
    if (!isLocalPetVisible()) return;
    const pt = getCanvasPoint(evt);
    const pad = 18;
    const petW = getPetW();
    const petH = getPetH();
    if (
      pt.x >= state.x - pad &&
      pt.x <= state.x + petW + pad &&
      pt.y >= state.y - pad &&
      pt.y <= state.y + petH + pad
    ) {
      state.dragging = true;
      state.dragPointerId = evt.pointerId;
      state.dragOffsetX = pt.x - state.x;
      state.dragOffsetY = pt.y - state.y;
      state.vx = 0;
      state.vy = 0;
      state.grounded = false;
      state.edgeDwellSec = 0;
      state.activeEdge = null;
      setAnimation("grab");
      triggerHaptic("light");
      try { canvas.setPointerCapture(evt.pointerId); } catch (_) {}
    } else {
      const targetX = Math.max(0, Math.min(state.stageW - petW, pt.x - petW / 2));
      const dx = targetX - state.x;
      if (Math.abs(dx) > 10) {
        state.facing = dx >= 0 ? 1 : -1;
        state.vx = state.facing * 95;
        state.aiMode = "walk";
        state.aiTimer = Math.min(2.6, Math.abs(dx) / 95);
        setAnimation("walk");
      }
    }
  });

  canvas.addEventListener("pointermove", (evt) => {
    if (!state.dragging || evt.pointerId !== state.dragPointerId) return;
    const pt = getCanvasPoint(evt);
    const petW = getPetW();
    state.x = Math.max(0, Math.min(state.stageW - petW, pt.x - state.dragOffsetX));
    state.y = Math.max(0, Math.min(getFloorY(), pt.y - state.dragOffsetY));

    const margin = 16;
    let edge = null;
    if (state.x <= margin) edge = "left";
    else if (state.x + petW >= state.stageW - margin) edge = "right";
    else if (state.y <= margin) edge = "top";

    if (edge !== state.activeEdge) {
      state.activeEdge = edge;
      state.edgeDwellSec = 0;
      updateEdgeGlow(edge);
    }
  });

  function releaseDrag() {
    if (!state.dragging) return;
    const triggerEdge = state.activeEdge && state.edgeDwellSec >= 0.35 ? state.activeEdge : null;
    state.dragging = false;
    state.dragPointerId = null;
    state.activeEdge = null;
    state.edgeDwellSec = 0;
    updateEdgeGlow(null);

    if (triggerEdge && findWindowsPeer()) {
      requestHandoffToWindows(triggerEdge);
    } else {
      state.grounded = state.y >= getFloorY() - 2;
      setAnimation(state.grounded ? "landing" : "jump");
    }
  }

  canvas.addEventListener("pointerup", releaseDrag);
  canvas.addEventListener("pointercancel", releaseDrag);

  acceptBtn.addEventListener("click", () => confirmIncomingOffer(true));
  declineBtn.addEventListener("click", () => confirmIncomingOffer(false));
  jumpBtn.addEventListener("click", () => {
    if (!isLocalPetVisible() || state.dragging) return;
    state.vy = -420;
    state.grounded = false;
    setAnimation("jump");
    triggerHaptic("light");
  });
  sendBtn.addEventListener("click", () => requestHandoffToWindows("top"));

  let lastTs = performance.now();

  function tick(now) {
    const dt = Math.min(0.05, Math.max(0.001, (now - lastTs) / 1000));
    lastTs = now;

    if (state.incomingOffer) {
      updateOverlays();
    }

    if (isLocalPetVisible()) {
      const petW = getPetW();
      const floorY = getFloorY();

      if (state.dragging) {
        if (state.activeEdge && findWindowsPeer()) {
          state.edgeDwellSec += dt;
          if (state.edgeDwellSec >= 0.6) {
            const edge = state.activeEdge;
            state.dragging = false;
            state.activeEdge = null;
            state.edgeDwellSec = 0;
            updateEdgeGlow(null);
            requestHandoffToWindows(edge);
          }
        }
      } else {
        if (!state.grounded || state.y < floorY) {
          state.vy += 1250 * dt;
          state.y += state.vy * dt;
          if (state.y >= floorY) {
            state.y = floorY;
            state.vy = 0;
            state.grounded = true;
            setAnimation("landing");
            state.aiTimer = 0.35;
          }
        } else {
          state.aiTimer -= dt;
          if (state.aiTimer <= 0) {
            const r = Math.random();
            if (r < 0.45) {
              state.aiMode = "walk";
              state.facing = Math.random() < 0.5 ? -1 : 1;
              state.vx = state.facing * 72;
              state.aiTimer = 1.6 + Math.random() * 2.4;
              setAnimation("walk");
            } else if (r < 0.82) {
              state.aiMode = "idle";
              state.vx = 0;
              state.aiTimer = 1.8 + Math.random() * 2.5;
              setAnimation("idle");
            } else {
              state.aiMode = "sleep";
              state.vx = 0;
              state.aiTimer = 3.5 + Math.random() * 2.5;
              setAnimation("sleep");
            }
          }

          if (state.aiMode === "walk") {
            state.x += state.vx * dt;
            if (state.x <= 8) {
              state.x = 8;
              state.facing = 1;
              state.vx = Math.abs(state.vx);
            } else if (state.x >= state.stageW - petW - 8) {
              state.x = state.stageW - petW - 8;
              state.facing = -1;
              state.vx = -Math.abs(state.vx);
            }
          }
        }
      }

      const anim = state.animations[state.animation] || DEFAULT_ANIMS.idle;
      const frameDur = 1 / Math.max(1, anim.fps || 4);
      state.animAcc += dt;
      while (state.animAcc >= frameDur) {
        state.animAcc -= frameDur;
        if (state.animFrame + 1 < anim.frames) {
          state.animFrame += 1;
        } else if (anim.loop) {
          state.animFrame = 0;
        } else if (state.animation === "landing" || state.animation === "receive") {
          setAnimation("idle");
        }
      }

      const syncInterval = state.dragging || Math.abs(state.vx) > 1 ? 120 : 600;
      if (state.connected && now - state.lastSyncMs >= syncInterval) {
        state.lastSyncMs = now;
        const petState = state.dragging ? "dragged" : (!state.grounded ? "falling" : state.animation);
        sendEnvelope("state_update", {
          device_id: state.deviceId,
          position: { x: Math.round(state.x), y: Math.round(state.y) },
          animation: state.animation,
          state: petState,
          facing: state.facing >= 0 ? "right" : "left",
        });
      }
    }

    render();
    requestAnimationFrame(tick);
  }

  function render() {
    ctx.clearRect(0, 0, state.stageW, state.stageH);
    ctx.imageSmoothingEnabled = false;

    const floorLineY = Math.floor(state.stageH * 0.82);
    ctx.fillStyle = "rgba(102, 252, 241, 0.24)";
    ctx.fillRect(0, floorLineY, state.stageW, 2);

    if (!isLocalPetVisible()) return;

    const petW = getPetW();
    const petH = getPetH();
    const drawX = Math.round(state.x);
    const drawY = Math.round(state.y);

    const shadowScale = Math.max(0.45, 1 - (getFloorY() - state.y) / 260);
    ctx.fillStyle = "rgba(0, 0, 0, 0.34)";
    ctx.beginPath();
    ctx.ellipse(
      drawX + petW / 2,
      floorLineY + 6,
      (petW * 0.34) * shadowScale,
      7 * shadowScale,
      0,
      0,
      Math.PI * 2
    );
    ctx.fill();

    if (state.spriteLoaded && state.spriteSheet) {
      const anim = state.animations[state.animation] || DEFAULT_ANIMS.idle;
      const sx = (state.animFrame % Math.max(1, anim.frames)) * state.frameW;
      const sy = anim.row * state.frameH;

      ctx.save();
      ctx.imageSmoothingEnabled = false;
      if (state.facing < 0) {
        ctx.translate(drawX + petW, drawY);
        ctx.scale(-1, 1);
        ctx.drawImage(state.spriteSheet, sx, sy, state.frameW, state.frameH, 0, 0, petW, petH);
      } else {
        ctx.drawImage(state.spriteSheet, sx, sy, state.frameW, state.frameH, drawX, drawY, petW, petH);
      }
      ctx.restore();
    } else {
      ctx.fillStyle = "#66fcf1";
      ctx.fillRect(drawX + 24, drawY + 24, petW - 48, petH - 32);
      ctx.fillStyle = "#0b111c";
      ctx.fillRect(drawX + 42, drawY + 46, 12, 12);
      ctx.fillRect(drawX + petW - 54, drawY + 46, 12, 12);
    }

    if (state.dragging && state.activeEdge && findWindowsPeer()) {
      const pct = Math.min(1, state.edgeDwellSec / 0.6);
      ctx.fillStyle = "rgba(102, 252, 241, 0.9)";
      ctx.fillRect(drawX, Math.max(6, drawY - 12), Math.round(petW * pct), 6);
    }
  }

  async function loadConfigAndBoot() {
    resizeCanvas();
    window.addEventListener("resize", resizeCanvas);

    const params = new URLSearchParams(window.location.search);
    const queryWs = params.get("ws");

    let wsPort = 8765;
    let spriteUrl = "/assets/avatar/spritesheet.png";

    try {
      const resp = await fetch("/api/config", { cache: "no-store" });
      if (resp.ok) {
        const cfg = await resp.json();
        if (cfg.ws_port) wsPort = cfg.ws_port;
        if (cfg.sprite_sheet_url) spriteUrl = cfg.sprite_sheet_url;
        if (cfg.avatar_config) {
          const vis = cfg.avatar_config.visual || {};
          if (vis.frame_width) state.frameW = vis.frame_width;
          if (vis.frame_height) state.frameH = vis.frame_height;
          if (vis.scale) state.scale = Math.max(3, Math.min(5, vis.scale));
          if (cfg.avatar_config.animations) {
            state.animations = cfg.avatar_config.animations;
          }
        }
      }
    } catch (_) {}

    const host = window.location.hostname || "127.0.0.1";
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    state.wsUrl = queryWs || `${proto}//${host}:${wsPort}`;

    const img = new Image();
    img.onload = () => {
      state.spriteSheet = img;
      state.spriteLoaded = true;
    };
    img.src = `${spriteUrl}?t=${Date.now()}`;

    state.x = Math.max(16, (state.stageW - getPetW()) / 2);
    state.y = getFloorY();
    updateOverlays();
    connectWebSocket();
    requestAnimationFrame(tick);
  }

  loadConfigAndBoot();
})();
