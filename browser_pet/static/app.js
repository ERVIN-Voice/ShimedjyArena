(() => {
  "use strict";

  // ------------------------------------------------------------------ DOM refs
  const canvas = document.getElementById("petCanvas");
  const ctx = canvas.getContext("2d", { alpha: true });
  const stageEl = document.getElementById("stage");
  const connDot = document.getElementById("connDot");
  const tabNameInput = document.getElementById("tabNameInput");
  const throwsBadge = document.getElementById("throwsBadge");
  const tabsRibbon = document.getElementById("tabsRibbon");
  const portalLeft = document.getElementById("portalLeft");
  const portalRight = document.getElementById("portalRight");
  const portalLeftName = document.getElementById("portalLeftName");
  const portalRightName = document.getElementById("portalRightName");
  const radarOverlay = document.getElementById("radarOverlay");
  const radarTitle = document.getElementById("radarTitle");
  const radarDesc = document.getElementById("radarDesc");
  const summonBtn = document.getElementById("summonBtn");
  const radarSpawnBtn = document.getElementById("radarSpawnBtn");
  const toastEl = document.getElementById("toast");
  const dockEl = document.getElementById("dock");

  const ballBtn = document.getElementById("ballBtn");
  const snackBtn = document.getElementById("snackBtn");
  const jumpBtn = document.getElementById("jumpBtn");
  const spawnBtn = document.getElementById("spawnBtn");
  const wanderBtn = document.getElementById("wanderBtn");
  const emotionBar = document.getElementById("emotionBar");
  const noteInput = document.getElementById("noteInput");
  const themeSelect = document.getElementById("themeSelect");
  const toggleHudBtn = document.getElementById("toggleHudBtn");
  const soundBtn = document.getElementById("soundBtn");
  const fullscreenBtn = document.getElementById("fullscreenBtn");

  // ------------------------------------------------------------------ Sprites & Palettes
  const FRAME_W = 32;
  const FRAME_H = 32;
  const PET_SCALE = 4;
  const PET_W = FRAME_W * PET_SCALE;
  const PET_H = FRAME_H * PET_SCALE;

  const ANIMS = {
    idle:    { row: 0, frames: 4, fps: 4,  loop: true },
    walk:    { row: 1, frames: 6, fps: 8,  loop: true },
    run:     { row: 2, frames: 6, fps: 12, loop: true },
    jump:    { row: 3, frames: 3, fps: 8,  loop: false },
    sleep:   { row: 4, frames: 4, fps: 2,  loop: true },
    grab:    { row: 5, frames: 2, fps: 6,  loop: true },
    handoff: { row: 6, frames: 4, fps: 10, loop: false },
    receive: { row: 7, frames: 4, fps: 10, loop: false },
    landing: { row: 8, frames: 3, fps: 10, loop: false },
  };

  const BASE_PALETTE = [
    [86, 198, 224],
    [146, 232, 248],
    [48, 138, 174],
  ];

  const EMOTION_PALETTES = {
    neutral: BASE_PALETTE,
    happy:   [[255, 213, 79],  [255, 241, 168], [212, 155, 26]],
    love:    [[255, 110, 168], [255, 180, 210], [201, 59, 118]],
    excited: [[76, 232, 138],  [166, 249, 197], [31, 164, 90]],
    scared:  [[122, 148, 232], [185, 202, 252], [71, 94, 179]],
    dizzy:   [[180, 124, 255], [221, 194, 255], [123, 70, 199]],
    angry:   [[255, 77, 77],   [255, 154, 154], [189, 34, 34]],
    sleepy:  [[136, 140, 224], [191, 194, 245], [85, 89, 168]],
  };

  let baseSprite = null;
  let spriteLoaded = false;
  const recoloredCache = {};

  function loadSprite() {
    const img = new Image();
    img.onload = () => {
      baseSprite = img;
      spriteLoaded = true;
    };
    img.src = "/assets/spritesheet.png";
  }

  function getSheet(emotion) {
    if (!spriteLoaded || !baseSprite) return null;
    const key = EMOTION_PALETTES[emotion] ? emotion : "neutral";
    if (key === "neutral") return baseSprite;
    if (recoloredCache[key]) return recoloredCache[key];

    const off = document.createElement("canvas");
    off.width = baseSprite.naturalWidth || baseSprite.width;
    off.height = baseSprite.naturalHeight || baseSprite.height;
    const octx = off.getContext("2d");
    octx.drawImage(baseSprite, 0, 0);
    const data = octx.getImageData(0, 0, off.width, off.height);
    const px = data.data;
    const target = EMOTION_PALETTES[key];

    for (let i = 0; i < px.length; i += 4) {
      if (px[i + 3] < 20) continue;
      for (let c = 0; c < 3; c++) {
        if (
          Math.abs(px[i] - BASE_PALETTE[c][0]) <= 14 &&
          Math.abs(px[i + 1] - BASE_PALETTE[c][1]) <= 14 &&
          Math.abs(px[i + 2] - BASE_PALETTE[c][2]) <= 14
        ) {
          px[i] = target[c][0];
          px[i + 1] = target[c][1];
          px[i + 2] = target[c][2];
          break;
        }
      }
    }
    octx.putImageData(data, 0, 0);
    recoloredCache[key] = off;
    return off;
  }

  // ------------------------------------------------------------------ 8-bit WebAudio SFX
  let soundEnabled = true;
  let audioCtx = null;

  function playTone(freqStart, freqEnd, duration, type = "triangle", gainVal = 0.08) {
    if (!soundEnabled) return;
    try {
      if (!audioCtx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return;
        audioCtx = new AC();
      }
      if (audioCtx.state === "suspended") audioCtx.resume();
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freqStart, now);
      osc.frequency.exponentialRampToValueAtTime(Math.max(40, freqEnd), now + duration);
      gain.gain.setValueAtTime(gainVal, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + duration);
      osc.connect(gain);
      gain.connect(audioCtx.destination);
      osc.start(now);
      osc.stop(now + duration);
    } catch (_) {}
  }

  function sfxPortalOut() { playTone(320, 920, 0.16, "sine", 0.09); }
  function sfxPortalIn()  { playTone(780, 340, 0.18, "triangle", 0.1); }
  function sfxBounce()    { playTone(260, 440, 0.09, "square", 0.05); }
  function sfxPurr()      { playTone(520, 740, 0.14, "sine", 0.06); }
  function sfxEat()       { playTone(420, 680, 0.1, "square", 0.06); }

  // ------------------------------------------------------------------ Runtime State
  const TAB_ID_KEY = "shimeji_browser_tab_id_v1";
  const TAB_NAME_KEY = "shimeji_browser_tab_name_v1";
  const THEME_KEY = "shimeji_browser_theme_v1";

  function detectBrowserName() {
    const ua = navigator.userAgent;
    if (ua.includes("Edg/")) return "Edge";
    if (ua.includes("OPR/") || ua.includes("Opera")) return "Opera";
    if (ua.includes("YaBrowser")) return "Yandex";
    if (ua.includes("Firefox/")) return "Firefox";
    if (ua.includes("Chrome/")) return "Chrome";
    if (ua.includes("Safari/")) return "Safari";
    return "Browser";
  }

  function detectPlatformShort() {
    const ua = navigator.userAgent;
    if (/Android/i.test(ua)) return "Android";
    if (/iPhone|iPad/i.test(ua)) return "iPhone";
    if (/Windows/i.test(ua)) return "Windows";
    if (/Mac/i.test(ua)) return "Mac";
    if (/Linux/i.test(ua)) return "Linux";
    return "ПК";
  }

  let tabId = sessionStorage.getItem(TAB_ID_KEY);
  if (!tabId) {
    tabId = "tab-" + Math.random().toString(36).slice(2, 10);
    sessionStorage.setItem(TAB_ID_KEY, tabId);
  }

  const savedLabel = localStorage.getItem(TAB_NAME_KEY) || `${detectPlatformShort()} · ${detectBrowserName()}`;
  tabNameInput.value = savedLabel;

  const state = {
    ws: null,
    connected: false,
    width: window.innerWidth,
    height: window.innerHeight - 100,
    tabs: [],
    remotePets: [],
    neighbors: { left: null, right: null },
    totalThrows: 0,
    autoWander: false,
    selectedEmotion: "neutral",

    // Local owned pets (physics simulated locally at 60 FPS)
    localPets: new Map(), // pet_id -> LocalPet
    balls: [],
    snacks: [],
    particles: [],
    portalRings: [],

    // Active drag state (either pet or ball)
    drag: null, // { kind: 'pet'|'ball', obj, offsetX, offsetY, samples: [{x,y,t}], overTabId: null }
    hoverPetting: { petId: null, lastX: 0, lastDir: 0, reversals: 0, lastTime: 0, cooldown: 0 },
  };

  let toastTimer = null;
  function showToast(text, ms = 2400) {
    toastEl.textContent = text;
    toastEl.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.add("hidden"), ms);
  }

  // ------------------------------------------------------------------ Local Pet & Toys
  function createLocalPet(data, entryEdge = "top", ny = 0.25, vx = 0, vy = 0, noteText = "", noteFrom = "") {
    const W = state.width;
    const H = state.height;
    let x = W * 0.5 - PET_W * 0.5;
    let y = Math.max(12, Math.min(H - PET_H, ny * H - PET_H * 0.5));
    if (entryEdge === "left") {
      x = -PET_W * 0.35;
    } else if (entryEdge === "right") {
      x = W - PET_W * 0.65;
    } else {
      x = Math.max(24, Math.min(W - PET_W - 24, (data.nx ?? 0.5) * W - PET_W * 0.5));
      y = 12;
    }

    return {
      pet_id: data.pet_id,
      name: data.name || "Чубрик",
      emotion: data.emotion || "neutral",
      x,
      y,
      vx,
      vy,
      facing: vx < -10 ? "left" : (vx > 10 ? "right" : (data.facing || "right")),
      anim: "receive",
      frame: 0,
      frameTimer: 0,
      onGround: false,
      aiTimer: 1.4 + Math.random() * 2.2,
      aiMode: "idle", // idle | walk | run | sleep
      speechText: noteText || "",
      speechFrom: noteFrom || "",
      speechTimer: noteText ? 6.5 : 0,
      portalImmunity: 0.45, // prevents instant re-bounce back into entry edge
      effectPhase: Math.random() * 6.28,
    };
  }

  function spawnPortalRing(x, y, color) {
    state.portalRings.push({ x, y, r: 12, maxR: 92, alpha: 1.0, color });
    for (let i = 0; i < 16; i++) {
      const ang = (Math.PI * 2 * i) / 16 + Math.random() * 0.2;
      const sp = 110 + Math.random() * 240;
      state.particles.push({
        x, y,
        vx: Math.cos(ang) * sp,
        vy: Math.sin(ang) * sp,
        size: 4 + Math.random() * 4,
        color,
        life: 0.55 + Math.random() * 0.25,
        maxLife: 0.8,
        kind: "spark",
      });
    }
  }

  function spawnHearts(x, y, count = 5) {
    for (let i = 0; i < count; i++) {
      state.particles.push({
        x: x + (Math.random() - 0.5) * 60,
        y: y + (Math.random() - 0.5) * 20,
        vx: (Math.random() - 0.5) * 45,
        vy: -70 - Math.random() * 55,
        size: 14,
        color: "#ff6ea8",
        life: 1.05 + Math.random() * 0.35,
        maxLife: 1.4,
        kind: "heart",
      });
    }
  }

  function spawnBall(entryEdge = null, ny = 0.35, vx = 260, vy = -180, color = null) {
    const colors = ["#ff6b6b", "#4cc9f0", "#ffd166", "#52e596", "#c77dff"];
    const c = color || colors[Math.floor(Math.random() * colors.length)];
    let x = state.width * (0.25 + Math.random() * 0.5);
    let y = 70;
    if (entryEdge === "left") {
      x = 18;
      y = Math.max(24, Math.min(state.height - 40, ny * state.height));
    } else if (entryEdge === "right") {
      x = state.width - 18;
      y = Math.max(24, Math.min(state.height - 40, ny * state.height));
    }
    if (state.balls.length >= 4) state.balls.shift();
    state.balls.push({
      id: "b-" + Math.random().toString(36).slice(2, 7),
      x, y, vx, vy,
      r: 18,
      color: c,
      rot: 0,
      portalImmunity: entryEdge ? 0.35 : 0,
      kickCooldown: 0,
    });
  }

  function spawnSnack() {
    const kinds = ["🍪", "🍩", "🍓", "🍿", "🍕"];
    if (state.snacks.length >= 4) state.snacks.shift();
    state.snacks.push({
      x: 80 + Math.random() * Math.max(120, state.width - 160),
      y: 30,
      vy: 40,
      emoji: kinds[Math.floor(Math.random() * kinds.length)],
    });
  }

  // ------------------------------------------------------------------ WebSocket Sync
  let reconnectTimer = null;

  function wsSend(obj) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify(obj));
    }
  }

  function connectWS() {
    clearTimeout(reconnectTimer);
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws`;
    const ws = new WebSocket(url);
    state.ws = ws;

    ws.onopen = () => {
      state.connected = true;
      connDot.classList.remove("offline");
      connDot.classList.add("online");
      wsSend({
        type: "hello",
        tab_id: tabId,
        label: tabNameInput.value.trim() || savedLabel,
        pc_name: detectPlatformShort(),
        browser: detectBrowserName(),
        width: Math.round(state.width),
        height: Math.round(state.height),
      });
    };

    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch (_) { return; }

      if (msg.type === "welcome") {
        tabId = msg.tab_id;
        sessionStorage.setItem(TAB_ID_KEY, tabId);
        if (msg.label && !localStorage.getItem(TAB_NAME_KEY)) {
          tabNameInput.value = msg.label;
        }
      } else if (msg.type === "room_state") {
        state.tabs = msg.tabs || [];
        state.remotePets = msg.pets || [];
        state.neighbors = msg.neighbors || { left: null, right: null };
        state.totalThrows = msg.total_throws || 0;
        throwsBadge.textContent = `🚀 ${state.totalThrows}`;

        // Sync ownership strictly with authoritative server
        const ownedIds = new Set();
        for (const p of state.remotePets) {
          if (p.owner_tab_id === tabId) {
            ownedIds.add(p.pet_id);
            if (!state.localPets.has(p.pet_id)) {
              state.localPets.set(p.pet_id, createLocalPet(p, "top", 0.2, 0, 100));
            }
          }
        }
        for (const pid of Array.from(state.localPets.keys())) {
          if (!ownedIds.has(pid)) {
            state.localPets.delete(pid);
          }
        }

        renderRibbonAndPortals();
        updateRadar();
      } else if (msg.type === "pet_arrive") {
        const p = msg.pet;
        const pet = createLocalPet(
          p,
          msg.entry_edge || "top",
          msg.ny ?? 0.35,
          msg.vx ?? 420,
          msg.vy ?? -180,
          msg.note_text || "",
          msg.note_from || "",
        );
        state.localPets.set(p.pet_id, pet);
        const rx = msg.entry_edge === "left" ? 16 : (msg.entry_edge === "right" ? state.width - 16 : pet.x + PET_W * 0.5);
        const ry = pet.y + PET_H * 0.5;
        spawnPortalRing(rx, ry, msg.entry_edge === "left" ? "#4cc9f0" : "#ff6ea8");
        sfxPortalIn();
        if (msg.note_text && msg.note_from) {
          showToast(`💌 ${msg.note_from}: «${msg.note_text}»`, 3800);
        }
        updateRadar();
      } else if (msg.type === "pet_departed") {
        state.localPets.delete(msg.pet_id);
        updateRadar();
      } else if (msg.type === "ball_arrive") {
        spawnBall(msg.entry_edge || "left", msg.ny ?? 0.4, msg.vx ?? 600, msg.vy ?? -200, msg.color);
        const rx = msg.entry_edge === "left" ? 16 : state.width - 16;
        const ry = (msg.ny ?? 0.4) * state.height;
        spawnPortalRing(rx, ry, msg.color || "#ffd166");
        sfxPortalIn();
        showToast(`⚽ Прилетел мячик${msg.from_label ? " от " + msg.from_label : ""}!`, 2000);
      }
    };

    ws.onclose = () => {
      state.connected = false;
      connDot.classList.remove("online");
      connDot.classList.add("offline");
      reconnectTimer = setTimeout(connectWS, 1500);
    };
  }

  // ------------------------------------------------------------------ UI Rendering
  function renderRibbonAndPortals() {
    // Side portals
    if (state.neighbors.left) {
      portalLeft.classList.remove("hidden");
      portalLeftName.textContent = state.neighbors.left.label;
    } else {
      portalLeft.classList.add("hidden");
    }

    if (state.neighbors.right) {
      portalRight.classList.remove("hidden");
      portalRightName.textContent = state.neighbors.right.label;
    } else {
      portalRight.classList.add("hidden");
    }

    // Tabs ribbon
    tabsRibbon.innerHTML = "";
    state.tabs.forEach((t, idx) => {
      const chip = document.createElement("div");
      chip.className = "tab-chip" + (t.tab_id === tabId ? " self" : "");
      chip.dataset.tabId = t.tab_id;

      const labelSpan = document.createElement("span");
      labelSpan.textContent = (t.tab_id === tabId ? "📍 " : "🖥️ ") + t.label;
      chip.appendChild(labelSpan);

      if (t.pet_count > 0) {
        const badge = document.createElement("span");
        badge.className = "pet-badge";
        badge.textContent = `👾 ${t.pet_count}`;
        chip.appendChild(badge);
      }

      if (t.tab_id !== tabId && state.localPets.size > 0) {
        const sendBtn = document.createElement("button");
        sendBtn.className = "chip-btn";
        sendBtn.textContent = "🚀";
        sendBtn.title = `Кинуть Чубрика на «${t.label}»`;
        sendBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          const firstPet = state.localPets.values().next().value;
          if (firstPet) {
            throwPetTo(firstPet, "portal", t.tab_id, 650, -300);
          }
        });
        chip.appendChild(sendBtn);
      }

      if (state.tabs.length >= 2) {
        if (idx > 0) {
          const leftBtn = document.createElement("button");
          leftBtn.className = "chip-btn";
          leftBtn.textContent = "‹";
          leftBtn.title = "Сдвинуть экран влево";
          leftBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            wsSend({ type: "move_tab", tab_id: t.tab_id, direction: -1 });
          });
          chip.appendChild(leftBtn);
        }
        if (idx < state.tabs.length - 1) {
          const rightBtn = document.createElement("button");
          rightBtn.className = "chip-btn";
          rightBtn.textContent = "›";
          rightBtn.title = "Сдвинуть экран вправо";
          rightBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            wsSend({ type: "move_tab", tab_id: t.tab_id, direction: 1 });
          });
          chip.appendChild(rightBtn);
        }
      }

      tabsRibbon.appendChild(chip);
    });
  }

  function updateRadar() {
    if (state.localPets.size > 0) {
      radarOverlay.classList.add("hidden");
      return;
    }
    radarOverlay.classList.remove("hidden");
    if (state.remotePets.length > 0) {
      const first = state.remotePets[0];
      const owner = state.tabs.find((t) => t.tab_id === first.owner_tab_id);
      radarTitle.textContent = owner
        ? `${first.name} сейчас на: ${owner.label}`
        : `${first.name} путешествует между вкладками`;
      radarDesc.textContent =
        "Откройте этот же адрес на другом ПК или вкладке и кидайте Чубрика прямо за левый/правый край экрана!";
    } else {
      radarTitle.textContent = "Ожидаем подключения к серверу...";
      radarDesc.textContent = "Убедитесь, что Shimeji Browser Server (.exe) запущен.";
    }
  }

  function throwPetTo(pet, edge, targetTabId, vx, vy) {
    const note = noteInput.value.trim();
    const px = edge === "left" ? 16 : (edge === "right" ? state.width - 16 : pet.x + PET_W * 0.5);
    const py = pet.y + PET_H * 0.5;
    spawnPortalRing(px, py, edge === "left" ? "#4cc9f0" : "#ff6ea8");
    sfxPortalOut();
    state.localPets.delete(pet.pet_id);
    updateRadar();

    wsSend({
      type: "throw_pet",
      pet_id: pet.pet_id,
      edge,
      target_tab_id: targetTabId || null,
      ny: Math.max(0.1, Math.min(0.86, (pet.y + PET_H * 0.5) / Math.max(1, state.height))),
      vx,
      vy,
      emotion: pet.emotion,
      note_text: note,
    });
    if (note) noteInput.value = "";
  }

  // ------------------------------------------------------------------ Canvas Resize
  function resizeCanvas() {
    const rect = stageEl.getBoundingClientRect();
    state.width = Math.max(300, rect.width);
    state.height = Math.max(240, rect.height);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(state.width * dpr);
    canvas.height = Math.round(state.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = false;
    wsSend({ type: "resize", width: Math.round(state.width), height: Math.round(state.height) });
  }
  window.addEventListener("resize", resizeCanvas);

  // ------------------------------------------------------------------ Pointer Drag, Throw & Head-Petting
  function stagePoint(e) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function highlightTabChipUnder(clientX, clientY) {
    let hitTabId = null;
    const chips = tabsRibbon.querySelectorAll(".tab-chip");
    chips.forEach((chip) => {
      const r = chip.getBoundingClientRect();
      const hit = clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom && chip.dataset.tabId !== tabId;
      chip.classList.toggle("drop-hot", hit);
      if (hit) hitTabId = chip.dataset.tabId;
    });
    return hitTabId;
  }

  canvas.addEventListener("pointerdown", (e) => {
    const pt = stagePoint(e);

    // 1) Check balls first
    for (let i = state.balls.length - 1; i >= 0; i--) {
      const b = state.balls[i];
      if (Math.hypot(pt.x - b.x, pt.y - b.y) <= b.r + 14) {
        state.drag = {
          kind: "ball",
          obj: b,
          offsetX: pt.x - b.x,
          offsetY: pt.y - b.y,
          samples: [{ x: pt.x, y: pt.y, t: performance.now() }],
          overTabId: null,
        };
        canvas.setPointerCapture(e.pointerId);
        return;
      }
    }

    // 2) Check pets
    const petsArr = Array.from(state.localPets.values());
    for (let i = petsArr.length - 1; i >= 0; i--) {
      const p = petsArr[i];
      if (pt.x >= p.x && pt.x <= p.x + PET_W && pt.y >= p.y && pt.y <= p.y + PET_H) {
        p.anim = "grab";
        p.vx = 0;
        p.vy = 0;
        p.onGround = false;
        state.drag = {
          kind: "pet",
          obj: p,
          offsetX: pt.x - p.x,
          offsetY: pt.y - p.y,
          samples: [{ x: pt.x, y: pt.y, t: performance.now() }],
          overTabId: null,
        };
        canvas.setPointerCapture(e.pointerId);
        return;
      }
    }
  });

  window.addEventListener("pointermove", (e) => {
    const pt = stagePoint(e);

    if (state.drag) {
      const d = state.drag;
      const now = performance.now();
      d.samples.push({ x: pt.x, y: pt.y, t: now });
      while (d.samples.length > 8 && now - d.samples[0].t > 100) {
        d.samples.shift();
      }

      if (d.kind === "pet") {
        d.obj.x = pt.x - d.offsetX;
        d.obj.y = pt.y - d.offsetY;
        const cx = d.obj.x + PET_W * 0.5;
        portalLeft.classList.toggle("hot", Boolean(state.neighbors.left) && cx < 74);
        portalRight.classList.toggle("hot", Boolean(state.neighbors.right) && cx > state.width - 74);
        d.overTabId = highlightTabChipUnder(e.clientX, e.clientY);
      } else if (d.kind === "ball") {
        d.obj.x = pt.x - d.offsetX;
        d.obj.y = pt.y - d.offsetY;
        portalLeft.classList.toggle("hot", Boolean(state.neighbors.left) && d.obj.x < 64);
        portalRight.classList.toggle("hot", Boolean(state.neighbors.right) && d.obj.x > state.width - 64);
      }
      return;
    }

    // Hover head-petting (without clicking!)
    const nowSec = performance.now() / 1000;
    const hp = state.hoverPetting;
    if (nowSec < hp.cooldown) return;
    for (const p of state.localPets.values()) {
      if (pt.x >= p.x + 16 && pt.x <= p.x + PET_W - 16 && pt.y >= p.y + 4 && pt.y <= p.y + PET_H * 0.55) {
        const dx = pt.x - hp.lastX;
        if (hp.petId !== p.pet_id) {
          hp.petId = p.pet_id;
          hp.lastX = pt.x;
          hp.lastDir = 0;
          hp.reversals = 0;
          hp.lastTime = nowSec;
        } else if (Math.abs(dx) > 12) {
          const dir = dx > 0 ? 1 : -1;
          if (hp.lastDir !== 0 && dir !== hp.lastDir && nowSec - hp.lastTime < 1.2) {
            hp.reversals += 1;
          }
          hp.lastDir = dir;
          hp.lastX = pt.x;
          hp.lastTime = nowSec;
          if (hp.reversals >= 3) {
            hp.reversals = 0;
            hp.cooldown = nowSec + 2.5;
            p.emotion = "love";
            p.speechText = "Муррр~ 💕";
            p.speechFrom = "";
            p.speechTimer = 2.6;
            spawnHearts(p.x + PET_W * 0.5, p.y + 18, 6);
            sfxPurr();
          }
        }
        break;
      }
    }
  });

  window.addEventListener("pointerup", (e) => {
    if (!state.drag) return;
    const d = state.drag;
    state.drag = null;
    portalLeft.classList.remove("hot");
    portalRight.classList.remove("hot");
    highlightTabChipUnder(-999, -999);

    // Compute release fling velocity
    let vx = 0;
    let vy = 0;
    if (d.samples.length >= 2) {
      const first = d.samples[0];
      const last = d.samples[d.samples.length - 1];
      const dt = Math.max(0.016, (last.t - first.t) / 1000);
      vx = Math.max(-2200, Math.min(2200, (last.x - first.x) / dt));
      vy = Math.max(-1600, Math.min(1400, (last.y - first.y) / dt));
    }

    if (d.kind === "pet") {
      const p = d.obj;
      // 1) Dropped onto a specific tab card in top bar!
      if (d.overTabId) {
        throwPetTo(p, "portal", d.overTabId, vx || 620, Math.min(-240, vy));
        return;
      }
      // 2) Released right inside left/right portal zone!
      const cx = p.x + PET_W * 0.5;
      if (state.neighbors.left && (cx < 62 || (cx < 140 && vx < -520))) {
        throwPetTo(p, "left", null, Math.min(-620, vx), vy);
        return;
      }
      if (state.neighbors.right && (cx > state.width - 62 || (cx > state.width - 140 && vx > 520))) {
        throwPetTo(p, "right", null, Math.max(620, vx), vy);
        return;
      }
      // 3) Otherwise release with real momentum (if thrown hard toward edge, physics will cross portal!)
      p.vx = vx;
      p.vy = vy;
      p.facing = vx < -20 ? "left" : (vx > 20 ? "right" : p.facing);
      p.anim = Math.hypot(vx, vy) > 250 ? "jump" : "idle";
      p.portalImmunity = 0;
    } else if (d.kind === "ball") {
      const b = d.obj;
      if (state.neighbors.left && b.x < 56) {
        sendBallAcross(b, "left", Math.min(-720, vx), vy);
        return;
      }
      if (state.neighbors.right && b.x > state.width - 56) {
        sendBallAcross(b, "right", Math.max(720, vx), vy);
        return;
      }
      b.vx = vx;
      b.vy = vy;
      b.portalImmunity = 0;
    }
  });

  function sendBallAcross(ball, edge, vx, vy) {
    const idx = state.balls.indexOf(ball);
    if (idx >= 0) state.balls.splice(idx, 1);
    spawnPortalRing(edge === "left" ? 16 : state.width - 16, ball.y, ball.color);
    sfxPortalOut();
    wsSend({
      type: "throw_ball",
      edge,
      ny: Math.max(0.08, Math.min(0.88, ball.y / Math.max(1, state.height))),
      vx,
      vy,
      color: ball.color,
    });
  }

  // ------------------------------------------------------------------ Physics & AI Loop
  const GRAVITY = 1550;
  let lastFrameTime = performance.now();
  let lastSyncSent = 0;

  function stepPhysics(dt) {
    const W = state.width;
    const H = state.height;
    const floorY = H - 6;

    // 1) Update snacks
    for (let i = state.snacks.length - 1; i >= 0; i--) {
      const s = state.snacks[i];
      s.vy += GRAVITY * 0.7 * dt;
      s.y += s.vy * dt;
      if (s.y >= floorY - 20) {
        s.y = floorY - 20;
        s.vy = -s.vy * 0.25;
        if (Math.abs(s.vy) < 20) s.vy = 0;
      }
    }

    // 2) Update balls (can also fly through portals!)
    for (let i = state.balls.length - 1; i >= 0; i--) {
      const b = state.balls[i];
      if (state.drag && state.drag.kind === "ball" && state.drag.obj === b) continue;
      if (b.portalImmunity > 0) b.portalImmunity -= dt;
      if (b.kickCooldown > 0) b.kickCooldown -= dt;

      b.vy += GRAVITY * 0.85 * dt;
      b.x += b.vx * dt;
      b.y += b.vy * dt;
      b.rot += (b.vx / 40) * dt;

      // Floor bounce
      if (b.y + b.r >= floorY) {
        b.y = floorY - b.r;
        if (Math.abs(b.vy) > 130) sfxBounce();
        b.vy = -b.vy * 0.72;
        b.vx *= 0.94;
        if (Math.abs(b.vy) < 35) b.vy = 0;
      }
      // Ceiling
      if (b.y - b.r < 4) {
        b.y = 4 + b.r;
        b.vy = Math.abs(b.vy) * 0.7;
      }
      // Left / Right Portal OR Wall bounce
      if (b.x - b.r < 0) {
        if (state.neighbors.left && b.portalImmunity <= 0 && b.vx < -280) {
          sendBallAcross(b, "left", b.vx, b.vy);
          continue;
        }
        b.x = b.r;
        b.vx = Math.abs(b.vx) * 0.76;
      } else if (b.x + b.r > W) {
        if (state.neighbors.right && b.portalImmunity <= 0 && b.vx > 280) {
          sendBallAcross(b, "right", b.vx, b.vy);
          continue;
        }
        b.x = W - b.r;
        b.vx = -Math.abs(b.vx) * 0.76;
      }
    }

    // 3) Update pets
    for (const p of Array.from(state.localPets.values())) {
      p.effectPhase += dt * 3.5;
      if (p.portalImmunity > 0) p.portalImmunity -= dt;
      if (p.speechTimer > 0) {
        p.speechTimer -= dt;
        if (p.speechTimer <= 0) {
          p.speechText = "";
          p.speechFrom = "";
        }
      }

      const isDragged = state.drag && state.drag.kind === "pet" && state.drag.obj === p;
      if (!isDragged) {
        p.vy += GRAVITY * dt;
        p.x += p.vx * dt;
        p.y += p.vy * dt;

        // Floor collision
        if (p.y + PET_H >= floorY) {
          if (!p.onGround && p.vy > 360) {
            p.anim = "landing";
            p.frame = 0;
            sfxBounce();
          }
          p.y = floorY - PET_H;
          p.vy = 0;
          p.onGround = true;
          // Apply ground friction when sliding after throw
          if (Math.abs(p.vx) > 220) {
            p.vx *= Math.pow(0.06, dt);
          }
        } else {
          p.onGround = false;
        }

        // Ceiling collision
        if (p.y < 2) {
          p.y = 2;
          p.vy = Math.abs(p.vy) * 0.4;
        }

        // Portal edge crossing when flung or auto-wandering!
        const fastLeft = p.vx < -260 || (state.autoWander && p.vx < -40);
        const fastRight = p.vx > 260 || (state.autoWander && p.vx > 40);

        if (p.x + PET_W * 0.35 < 0 && state.neighbors.left && p.portalImmunity <= 0 && fastLeft) {
          throwPetTo(p, "left", null, Math.min(-520, p.vx), p.vy);
          continue;
        }
        if (p.x + PET_W * 0.65 > W && state.neighbors.right && p.portalImmunity <= 0 && fastRight) {
          throwPetTo(p, "right", null, Math.max(520, p.vx), p.vy);
          continue;
        }

        // Bounce off walls if no portal crossing
        if (p.x < 0) {
          p.x = 0;
          p.vx = Math.abs(p.vx) * 0.55;
          p.facing = "right";
        } else if (p.x + PET_W > W) {
          p.x = W - PET_W;
          p.vx = -Math.abs(p.vx) * 0.55;
          p.facing = "left";
        }

        // Chase snacks or balls!
        let chased = false;
        if (p.onGround && state.snacks.length > 0) {
          const s = state.snacks[0];
          const dx = s.x - (p.x + PET_W * 0.5);
          if (Math.abs(dx) < 38 && Math.abs(s.y - (p.y + PET_H * 0.65)) < 54) {
            state.snacks.shift();
            p.emotion = "love";
            p.speechText = "Ням-ням! 😋";
            p.speechFrom = "";
            p.speechTimer = 2.4;
            p.vy = -340;
            p.onGround = false;
            spawnHearts(p.x + PET_W * 0.5, p.y + 12, 5);
            sfxEat();
          } else {
            p.vx = Math.sign(dx) * 210;
            p.facing = dx >= 0 ? "right" : "left";
            p.anim = "run";
            chased = true;
          }
        } else if (p.onGround && state.balls.length > 0) {
          const b = state.balls[0];
          const dx = b.x - (p.x + PET_W * 0.5);
          const dist = Math.hypot(dx, b.y - (p.y + PET_H * 0.6));
          if (dist < 56 && b.kickCooldown <= 0) {
            b.vx = (dx >= 0 ? 1 : -1) * (420 + Math.random() * 340);
            b.vy = -520 - Math.random() * 240;
            b.kickCooldown = 0.65;
            p.vy = -320;
            p.onGround = false;
            p.emotion = "excited";
            sfxBounce();
          } else if (Math.abs(dx) > 24) {
            p.vx = Math.sign(dx) * 195;
            p.facing = dx >= 0 ? "right" : "left";
            p.anim = "run";
            chased = true;
          }
        }

        // Autonomous Shimeji behavior
        if (!chased && p.onGround && Math.abs(p.vx) <= 220) {
          p.aiTimer -= dt;
          if (p.aiTimer <= 0) {
            const roll = Math.random();
            if (roll < 0.34) {
              p.aiMode = "idle";
              p.vx = 0;
              p.anim = "idle";
              p.aiTimer = 1.6 + Math.random() * 2.5;
            } else if (roll < 0.78) {
              p.aiMode = "walk";
              const dir = Math.random() < 0.5 ? -1 : 1;
              p.vx = dir * (95 + Math.random() * 55);
              p.facing = dir > 0 ? "right" : "left";
              p.anim = "walk";
              p.aiTimer = 2.0 + Math.random() * 3.0;
            } else if (roll < 0.92) {
              p.vy = -520 - Math.random() * 140;
              p.vx = (Math.random() - 0.5) * 260;
              p.facing = p.vx >= 0 ? "right" : "left";
              p.onGround = false;
              p.anim = "jump";
              p.aiTimer = 1.8;
            } else {
              p.aiMode = "sleep";
              p.vx = 0;
              p.anim = "sleep";
              p.aiTimer = 4.0 + Math.random() * 3.0;
            }
          }
        }
      }

      // Advance sprite frame
      const spec = ANIMS[p.anim] || ANIMS.idle;
      p.frameTimer += dt * spec.fps;
      if (p.frameTimer >= 1) {
        p.frameTimer -= Math.floor(p.frameTimer);
        if (p.frame + 1 < spec.frames) {
          p.frame += 1;
        } else if (spec.loop) {
          p.frame = 0;
        } else if (p.onGround) {
          p.anim = Math.abs(p.vx) > 30 ? "walk" : "idle";
          p.frame = 0;
        }
      }
    }

    // 4) Particles & Portal Rings
    for (let i = state.particles.length - 1; i >= 0; i--) {
      const pt = state.particles[i];
      pt.x += pt.vx * dt;
      pt.y += pt.vy * dt;
      if (pt.kind === "spark") pt.vy += 240 * dt;
      pt.life -= dt;
      if (pt.life <= 0) state.particles.splice(i, 1);
    }
    for (let i = state.portalRings.length - 1; i >= 0; i--) {
      const rg = state.portalRings[i];
      rg.r += 240 * dt;
      rg.alpha -= 2.2 * dt;
      if (rg.alpha <= 0) state.portalRings.splice(i, 1);
    }

    // Periodic lightweight state report (~4 Hz)
    const now = performance.now();
    if (now - lastSyncSent > 260 && state.localPets.size > 0) {
      lastSyncSent = now;
      for (const p of state.localPets.values()) {
        wsSend({
          type: "pet_state",
          pet_id: p.pet_id,
          nx: (p.x + PET_W * 0.5) / Math.max(1, W),
          ny: (p.y + PET_H * 0.5) / Math.max(1, H),
          vx: p.vx,
          vy: p.vy,
          facing: p.facing,
          animation: p.anim,
          emotion: p.emotion,
        });
      }
    }
  }

  // ------------------------------------------------------------------ Drawing
  function drawScene() {
    const W = state.width;
    const H = state.height;
    ctx.clearRect(0, 0, W, H);

    // Subtle floor line
    const floorColor = getComputedStyle(document.body).getPropertyValue("--floor-color").trim();
    if (floorColor && floorColor !== "rgba(0, 0, 0, 0)") {
      ctx.fillStyle = floorColor;
      ctx.fillRect(0, H - 6, W, 6);
    }

    // Portal shockwave rings
    for (const rg of state.portalRings) {
      ctx.save();
      ctx.globalAlpha = Math.max(0, rg.alpha);
      ctx.strokeStyle = rg.color;
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.arc(rg.x, rg.y, rg.r, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    // Snacks
    ctx.font = "30px 'Segoe UI Emoji', sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const s of state.snacks) {
      ctx.fillText(s.emoji, s.x, s.y);
    }

    // Pixel bouncy balls
    for (const b of state.balls) {
      ctx.save();
      ctx.translate(b.x, b.y);
      ctx.rotate(b.rot);
      ctx.fillStyle = "#182032";
      ctx.beginPath();
      ctx.arc(0, 0, b.r + 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = b.color;
      ctx.beginPath();
      ctx.arc(0, 0, b.r, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "rgba(255,255,255,0.65)";
      ctx.fillRect(-b.r * 0.5, -b.r * 0.5, 6, 6);
      ctx.restore();
    }

    // Pets
    for (const p of state.localPets.values()) {
      const sheet = getSheet(p.emotion);
      const spec = ANIMS[p.anim] || ANIMS.idle;
      const sx = (p.frame % spec.frames) * FRAME_W;
      const sy = spec.row * FRAME_H;

      // Ground shadow
      const distGround = Math.max(0, (H - 6) - (p.y + PET_H));
      const shadowScale = Math.max(0.25, 1 - distGround / 420);
      ctx.fillStyle = "rgba(0, 0, 0, 0.25)";
      ctx.beginPath();
      ctx.ellipse(p.x + PET_W * 0.5, H - 6, 36 * shadowScale, 8 * shadowScale, 0, 0, Math.PI * 2);
      ctx.fill();

      if (sheet) {
        ctx.save();
        ctx.imageSmoothingEnabled = false;
        if (p.facing === "left") {
          ctx.translate(Math.round(p.x + PET_W), Math.round(p.y));
          ctx.scale(-1, 1);
          ctx.drawImage(sheet, sx, sy, FRAME_W, FRAME_H, 0, 0, PET_W, PET_H);
        } else {
          ctx.drawImage(sheet, sx, sy, FRAME_W, FRAME_H, Math.round(p.x), Math.round(p.y), PET_W, PET_H);
        }
        ctx.restore();
      }

      // Pet name tag if > 1 pet in room
      if (state.remotePets.length > 1) {
        ctx.save();
        ctx.font = "bold 12px 'Segoe UI', sans-serif";
        ctx.textAlign = "center";
        ctx.fillStyle = "rgba(15, 22, 41, 0.72)";
        const tw = ctx.measureText(p.name).width + 12;
        ctx.fillRect(p.x + PET_W * 0.5 - tw * 0.5, p.y - 16, tw, 18);
        ctx.fillStyle = "#eef6ff";
        ctx.fillText(p.name, p.x + PET_W * 0.5, p.y - 4);
        ctx.restore();
      }

      // Speech / Postal note bubble above pet
      if (p.speechText) {
        drawSpeechBubble(
          p.x + PET_W * 0.5,
          p.y - (state.remotePets.length > 1 ? 26 : 10),
          p.speechFrom ? `${p.speechFrom}: ${p.speechText}` : p.speechText,
        );
      }
    }

    // Particles
    for (const pt of state.particles) {
      ctx.save();
      ctx.globalAlpha = Math.max(0, pt.life / pt.maxLife);
      if (pt.kind === "heart") {
        ctx.font = "16px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("💖", pt.x, pt.y);
      } else {
        ctx.fillStyle = pt.color;
        ctx.fillRect(Math.round(pt.x), Math.round(pt.y), pt.size, pt.size);
      }
      ctx.restore();
    }
  }

  function drawSpeechBubble(cx, bottomY, text) {
    ctx.save();
    ctx.font = "bold 13px 'Segoe UI', sans-serif";
    const padX = 12;
    const w = Math.min(280, ctx.measureText(text).width + padX * 2);
    const h = 30;
    const x = Math.max(12, Math.min(state.width - w - 12, cx - w * 0.5));
    const y = Math.max(10, bottomY - h);

    ctx.fillStyle = "#111a2e";
    ctx.strokeStyle = "#4cc9f0";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, 10);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = "#f2f8ff";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, x + w * 0.5, y + h * 0.5 + 1, w - 12);
    ctx.restore();
  }

  function frameLoop(now) {
    const dt = Math.min(0.05, Math.max(0.001, (now - lastFrameTime) / 1000));
    lastFrameTime = now;
    stepPhysics(dt);
    drawScene();
    requestAnimationFrame(frameLoop);
  }

  // ------------------------------------------------------------------ Controls Wiring
  function commitTabName() {
    const val = tabNameInput.value.trim();
    if (!val) return;
    localStorage.setItem(TAB_NAME_KEY, val);
    wsSend({ type: "rename_tab", label: val });
    showToast(`Имя вкладки: ${val}`);
  }
  tabNameInput.addEventListener("change", commitTabName);
  tabNameInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      commitTabName();
      tabNameInput.blur();
    }
  });

  ballBtn.addEventListener("click", () => {
    spawnBall(null, 0.3, (Math.random() - 0.5) * 420, -240);
    sfxBounce();
  });

  snackBtn.addEventListener("click", () => {
    spawnSnack();
  });

  jumpBtn.addEventListener("click", () => {
    for (const p of state.localPets.values()) {
      p.vy = -640;
      p.onGround = false;
      p.anim = "jump";
      sfxBounce();
    }
  });

  spawnBtn.addEventListener("click", () => wsSend({ type: "spawn_pet" }));
  radarSpawnBtn.addEventListener("click", () => wsSend({ type: "spawn_pet" }));

  summonBtn.addEventListener("click", () => {
    wsSend({ type: "summon_pet" });
  });

  wanderBtn.addEventListener("click", () => {
    state.autoWander = !state.autoWander;
    wanderBtn.classList.toggle("active", state.autoWander);
    showToast(state.autoWander ? "🌀 Авто-прогулка по вкладкам включена!" : "Авто-прогулка выключена");
  });

  emotionBar.addEventListener("click", (e) => {
    const btn = e.target.closest(".emo-btn");
    if (!btn) return;
    const emo = btn.dataset.emo;
    state.selectedEmotion = emo;
    emotionBar.querySelectorAll(".emo-btn").forEach((b) => b.classList.toggle("active", b === btn));
    for (const p of state.localPets.values()) {
      p.emotion = emo;
      if (emo === "love") spawnHearts(p.x + PET_W * 0.5, p.y + 12, 4);
    }
  });

  const savedTheme = localStorage.getItem(THEME_KEY) || "theme-space";
  document.body.className = savedTheme;
  themeSelect.value = savedTheme;
  themeSelect.addEventListener("change", () => {
    document.body.className = themeSelect.value;
    localStorage.setItem(THEME_KEY, themeSelect.value);
  });

  toggleHudBtn.addEventListener("click", () => {
    dockEl.classList.toggle("collapsed");
    resizeCanvas();
  });

  soundBtn.addEventListener("click", () => {
    soundEnabled = !soundEnabled;
    soundBtn.textContent = soundEnabled ? "🔊" : "🔇";
  });

  fullscreenBtn.addEventListener("click", () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen?.().catch(() => {});
    } else {
      document.exitFullscreen?.().catch(() => {});
    }
  });

  // Boot!
  loadSprite();
  resizeCanvas();
  connectWS();
  requestAnimationFrame(frameLoop);
})();
