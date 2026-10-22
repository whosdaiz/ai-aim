const RANGE_IDS = [
  "aim_director_humanizer",
  "aim_corrector_humanizer",
  "tracking_assist_humanizer",
  "spray_acquire_humanizer",
  "magnet_humanizer",
  "tap_humanizer",
  "trigger_humanizer",
  "rp_one_tap_humanizer",
  "aim_director_radius",
  "prediction_lead_ms",
  "prediction_strength",
  "prediction_max_px",
  "prediction_track_frames",
  "aim_director_strength",
  "aim_director_deadzone_px",
  "aim_director_max_step",
  "aim_corrector_radius",
  "aim_corrector_strength",
  "aim_corrector_max_bend",
  "tracking_assist_radius",
  "tracking_assist_strength",
  "tracking_assist_breakaway",
  "tracking_assist_dwell_ms",
  "spray_acquire_radius",
  "spray_acquire_strength",
  "spray_acquire_max_step",
  "spray_switch_delay_ms",
  "spray_switch_delay_max_ms",
  "tap_max_nudge",
  "tap_range",
  "trigger_radius",
  "trigger_interval_ms",
  "trigger_interval_max_ms",
  "trigger_initial_delay_ms",
  "magnet_trigger_radius",
  "magnet_trigger_interval_ms",
  "magnet_trigger_interval_max_ms",
  "magnet_trigger_initial_delay_ms",
  "magnet_radius",
  "magnet_speed",
  "magnet_smooth",
  "magnet_strength",
  "rp_aim_slowdown",
  "rp_aim_ramp",
  "rp_aim_ramp_out",
  "rp_aim_radius",
  "rp_one_tap_fov",
  "rp_one_tap_reset_fov",
  "rp_one_tap_speed",
  "rp_one_tap_smooth",
  "rp_one_tap_cooldown",
  "rp_one_tap_hold",
  "conf",
  "fps",
  "output_idx",
];

const BIND_IDS = ["hold_trigger", "magnet_key", "menu_hotkey", "pause_hotkey", "aim_director_key"];
const TEXT_IDS = ["rp2040_port"];
const NUMBER_IDS = ["mouse_dpi", "mouse_sens"];
const TOGGLE_IDS = [
  "dry_run",
  "auto_pause_menu",
  "trigger_humanized_delay",
  "magnet_trigger_humanized_delay",
  "tap_assist",
  "tap_on_trigger",
  "trigger_enabled",
  "magnet",
  "rp_aim_assist",
  "rp_aim_release_on_shot",
  "rp_one_tap_assist",
  "visuals_enabled",
  "logging_enabled",
  "aim_director",
  "target_prediction",
  "vis_prediction",
  "aim_director_deadzone",
  "aim_corrector",
  "tracking_assist",
  "spray_acquire",
  "vis_target_box",
  "vis_aim_director_fov",
  "vis_aim_director_spot",
  "vis_aim_corrector_fov",
  "vis_aim_corrector_spot",
  "vis_tracking_assist_fov",
  "vis_tracking_assist_spot",
  "vis_spray_acquire_fov",
  "vis_spray_acquire_spot",
  "vis_tap_fov",
  "vis_tap_spot",
  "vis_trigger_fov",
  "vis_trigger_spot",
  "vis_magnet_fov",
  "vis_magnet_spot",
  "vis_rp_aim_fov",
  "vis_rp_aim_spot",
  "vis_rp_one_tap_fov",
  "vis_rp_one_tap_reset",
  "vis_rp_one_tap_spot",
];
const SELECT_IDS = ["aim_director_curve", "aim_director_mode"];
const DIRECTOR_MODE_HINTS = {
  always: "Pulls whenever a picked bone is inside FOV.",
  toggle: "Press the key to turn the pull on, press again to turn it off.",
  hold: "Pulls only while the key is held down.",
};
const AIM_CURVES = [
  { id: "linear", name: "Linear", at: (t) => t },
  { id: "ease_in", name: "Ease in", at: (t) => t * t },
  { id: "ease_out", name: "Ease out", at: (t) => 1 - (1 - t) * (1 - t) },
  { id: "ease_in_out", name: "Ease in out", at: (t) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2) },
  { id: "ease_in_cubic", name: "Ease in cubic", at: (t) => t * t * t },
  { id: "ease_out_cubic", name: "Ease out cubic", at: (t) => 1 - (1 - t) ** 3 },
  { id: "exponential", name: "Exponential", at: (t) => (t === 0 ? 0 : 2 ** (10 * t - 10)) },
  { id: "ease_out_expo", name: "Ease out expo", at: (t) => (t === 1 ? 1 : 1 - 2 ** (-10 * t)) },
  { id: "sigmoid", name: "Sigmoid", at: (t) => t * t * (3 - 2 * t) },
];
const AIM_CURVE_MAP = new Map(AIM_CURVES.map((curve) => [curve.id, curve]));
const BONE_ALIASES = {
  spine2: "chest",
  spine1: "stomach",
  head_bone: "head",
};
const CS2_BONES = [
  { id: 7, key: "head", name: "Head", x: 49.7, y: 12.4, side: "right" },
  { id: 6, key: "neck", name: "Neck", x: 49.5, y: 19.5, side: "right" },
  { id: 5, key: "spine3", name: "Spine 3", x: 49.4, y: 26.2, side: "left" },
  { id: 4, key: "chest", name: "Chest", x: 49.4, y: 31.2, side: "right" },
  { id: 3, key: "stomach", name: "Stomach", x: 49.3, y: 36.5, side: "left" },
  { id: 1, key: "pelvis", name: "Pelvis", x: 49.2, y: 43.8, side: "left" },
  { id: 13, key: "shoulder_r", name: "Shoulder R", x: 41.2, y: 19.8, side: "left" },
  { id: 14, key: "elbow_r", name: "Elbow R", x: 30.5, y: 27.7, side: "left" },
  { id: 9, key: "shoulder_l", name: "Shoulder L", x: 57.4, y: 19.8, side: "right" },
  { id: 10, key: "elbow_l", name: "Elbow L", x: 63.6, y: 30.4, side: "right" },
  { id: 19, key: "hip_r", name: "Hip R", x: 42.8, y: 46.6, side: "left" },
  { id: 20, key: "knee_r", name: "Knee R", x: 41.4, y: 71.1, side: "left" },
  { id: 21, key: "ankle_r", name: "Ankle R", x: 40.8, y: 89.2, side: "left" },
  { id: 16, key: "hip_l", name: "Hip L", x: 56.6, y: 46.6, side: "right" },
  { id: 17, key: "knee_l", name: "Knee L", x: 60.2, y: 71.1, side: "right" },
  { id: 18, key: "ankle_l", name: "Ankle L", x: 62.0, y: 89.2, side: "right" },
];
const CS2_BONE_MAP = new Map(CS2_BONES.map((bone) => [bone.id, bone]));
const CS2_BONE_BY_KEY = new Map(CS2_BONES.map((bone) => [bone.key, bone]));
const TRIGGER_BONE_OPTIONS = CS2_BONES.map((bone) => bone.key);
const BONE_STUDIOS = {
  tap: {
    title: "Tap bones",
    fields: [{ id: "tap_bone", label: "Nudge toward", single: true }],
  },
  trigger: {
    title: "Trigger bones",
    fields: [{ id: "trigger_bones", label: "Fire on" }],
  },
  magnet: {
    title: "Magnet trigger bones",
    fields: [
      { id: "magnet_trigger_bones", label: "Fire on" },
      { id: "magnet_bones", label: "Pull toward" },
    ],
  },
  director: {
    title: "Aim director bones",
    fields: [{ id: "aim_director_bone", label: "Pull toward" }],
  },
  rpaim: {
    title: "Aim assist bones",
    fields: [{ id: "rp_aim_bones", label: "Slow on" }],
  },
  corrector: {
    title: "Aim corrector bone",
    fields: [{ id: "aim_corrector_bone", label: "Correct around", single: true }],
  },
};
const BONE_FIELD_IDS = [
  ...new Set(
    Object.values(BONE_STUDIOS).flatMap((studio) => studio.fields.map((field) => field.id))
  ),
];

const DECIMALS = {
  prediction_lead_ms: 0,
  prediction_strength: 0,
  prediction_max_px: 0,
  prediction_track_frames: 0,
  aim_director_radius: 0,
  aim_director_strength: 0,
  aim_director_deadzone_px: 0,
  aim_director_humanizer: 0,
  aim_corrector_humanizer: 0,
  tracking_assist_humanizer: 0,
  spray_acquire_humanizer: 0,
  magnet_humanizer: 0,
  tap_humanizer: 0,
  trigger_humanizer: 0,
  rp_one_tap_humanizer: 0,
  aim_director_max_step: 1,
  aim_corrector_radius: 0,
  aim_corrector_strength: 0,
  aim_corrector_max_bend: 1,
  tracking_assist_radius: 0,
  tracking_assist_strength: 0,
  tracking_assist_breakaway: 1,
  tracking_assist_dwell_ms: 0,
  spray_acquire_radius: 0,
  spray_acquire_strength: 0,
  spray_acquire_max_step: 1,
  spray_switch_delay_ms: 0,
  spray_switch_delay_max_ms: 0,
  tap_max_nudge: 0,
  tap_range: 0,
  trigger_radius: 0,
  trigger_interval_ms: 0,
  trigger_interval_max_ms: 0,
  trigger_initial_delay_ms: 0,
  magnet_trigger_radius: 0,
  magnet_trigger_interval_ms: 0,
  magnet_trigger_interval_max_ms: 0,
  magnet_trigger_initial_delay_ms: 0,
  magnet_radius: 0,
  magnet_speed: 1,
  magnet_smooth: 0,
  magnet_strength: 0,
  rp_aim_slowdown: 0,
  rp_aim_ramp: 0,
  rp_aim_ramp_out: 0,
  rp_aim_radius: 0,
  rp_one_tap_fov: 0,
  rp_one_tap_reset_fov: 0,
  rp_one_tap_speed: 0,
  rp_one_tap_smooth: 0,
  rp_one_tap_cooldown: 0,
  rp_one_tap_hold: 0,
  conf: 2,
  fps: 0,
  output_idx: 0,
};

const UNITS = {
  prediction_lead_ms: "ms",
  prediction_strength: "%",
  prediction_max_px: "px",
  prediction_track_frames: "frames",
  aim_director_radius: "px",
  aim_director_strength: "%",
  aim_director_deadzone_px: "px",
  aim_director_humanizer: "%",
  aim_corrector_humanizer: "%",
  tracking_assist_humanizer: "%",
  spray_acquire_humanizer: "%",
  magnet_humanizer: "%",
  tap_humanizer: "%",
  trigger_humanizer: "%",
  rp_one_tap_humanizer: "%",
  aim_director_max_step: "px",
  aim_corrector_radius: "px",
  aim_corrector_strength: "%",
  aim_corrector_max_bend: "°",
  tracking_assist_radius: "px",
  tracking_assist_strength: "%",
  tracking_assist_breakaway: "px",
  tracking_assist_dwell_ms: "ms",
  spray_acquire_radius: "px",
  spray_acquire_strength: "%",
  spray_acquire_max_step: "px",
  spray_switch_delay_ms: "ms",
  spray_switch_delay_max_ms: "ms",
  tap_max_nudge: "px",
  tap_range: "px",
  trigger_radius: "px",
  trigger_interval_ms: "ms",
  trigger_interval_max_ms: "ms",
  trigger_initial_delay_ms: "ms",
  magnet_trigger_radius: "px",
  magnet_trigger_interval_ms: "ms",
  magnet_trigger_interval_max_ms: "ms",
  magnet_trigger_initial_delay_ms: "ms",
  magnet_radius: "px",
  magnet_speed: "px",
  magnet_smooth: "%",
  magnet_strength: "%",
  rp_aim_slowdown: "%",
  rp_aim_ramp: "ms",
  rp_aim_ramp_out: "ms",
  rp_aim_radius: "px",
  rp_one_tap_fov: "px",
  rp_one_tap_reset_fov: "px",
  rp_one_tap_speed: "px/s",
  rp_one_tap_smooth: "%",
  rp_one_tap_cooldown: "ms",
  rp_one_tap_hold: "ms",
  fps: "fps",
};

let applyingRemote = false;
let debounceTimer = null;
let pending = {};
let loaded = false;
let lastSettings = {};
let listenId = null;
let listenPrev = "";
let suppressBindClick = false;
const bonePicker = {
  open: false,
  studio: "",
  option: "",
  selected: [],
  single: false,
  nearId: null,
};

function $(id) {
  return document.getElementById(id);
}

function formatValue(id, value) {
  const digits = DECIMALS[id];
  const number = Number(value);
  if (Number.isNaN(number)) return String(value);
  const text = digits === 0 ? String(Math.round(number)) : number.toFixed(digits);
  const unit = UNITS[id];
  if (unit === "%" || unit === "°") return `${text}${unit}`;
  return unit ? `${text} ${unit}` : text;
}

let GAMES = {};

function applyGamesCatalog(list) {
  GAMES = {};
  (Array.isArray(list) ? list : []).forEach((game) => {
    if (!game || !game.id) return;
    GAMES[game.id] = game;
  });
  renderGameTiles();
}

function gameIdFromEngine(path) {
  const file = String(path || "").split(/[/\\]/).pop() || "";
  const lower = file.toLowerCase();
  return (
    Object.keys(GAMES).find((id) => String(GAMES[id].engine || "").toLowerCase() === lower) ||
    ""
  );
}

function renderGameTiles() {
  const grid = $("gamePickerGrid");
  if (!grid) return;
  const games = Object.values(GAMES).sort((a, b) => {
    const generic = Number(Boolean(a.generic)) - Number(Boolean(b.generic));
    if (generic) return generic;
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
  grid.innerHTML = games
    .map((game) => {
      const hint = game.generic ? "generic model" : "click to choose game";
      const art = game.art
        ? `<img src="${game.art}" alt="" />`
        : `<span class="game-tile-placeholder">UNIVERSAL</span>`;
      return `<button type="button" class="game-tile${game.generic ? " is-generic" : ""}" data-game="${game.id}">
        ${art}
        <span class="game-tile-meta">
          <strong>${game.name}</strong>
          <small>${hint}</small>
        </span>
      </button>`;
    })
    .join("");
}

function paintBrandGame(spec) {
  const brand = $("titlebar-game");
  const art = $("titlebar-game-art");
  if (!brand) return;
  if (spec) {
    brand.classList.add("has-game");
    brand.classList.toggle("is-generic", Boolean(spec.generic) || !spec.art);
    brand.title = spec.name || "";
    if (art) {
      if (spec.art) art.src = spec.art;
      else art.removeAttribute("src");
    }
    return;
  }
  brand.classList.remove("has-game", "is-generic");
  brand.title = "";
  if (art) art.removeAttribute("src");
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

let configNames = [];
let activeConfig = "";
let switchingConfig = false;

function paintConfigs(state) {
  if (!state) return;
  if (Array.isArray(state.configs)) configNames = state.configs.map((name) => String(name));
  if (state.active_config != null) activeConfig = String(state.active_config);
  const label = $("configSelectLabel");
  if (label) label.textContent = activeConfig || "Config";
  const menu = $("configMenu");
  if (!menu) return;
  menu.innerHTML = configNames
    .map((name) => {
      const selected = name === activeConfig;
      return `<button type="button" role="option" class="cfg-drop-item${selected ? " active" : ""}" data-config="${escapeHtml(name)}" aria-selected="${selected}">${escapeHtml(name)}</button>`;
    })
    .join("");
}

function placeConfigMenu() {
  const btn = $("configSelect");
  const menu = $("configMenu");
  if (!btn || !menu) return;
  const rect = btn.getBoundingClientRect();
  menu.style.top = `${Math.round(rect.bottom + 6)}px`;
  menu.style.left = `${Math.round(rect.left)}px`;
  menu.style.minWidth = `${Math.round(rect.width)}px`;
}

function closeConfigDrop() {
  const drop = $("configDrop");
  const btn = $("configSelect");
  const menu = $("configMenu");
  if (drop) drop.classList.remove("open");
  if (btn) btn.setAttribute("aria-expanded", "false");
  if (menu) menu.hidden = true;
}

function openConfigDrop() {
  const drop = $("configDrop");
  const btn = $("configSelect");
  const menu = $("configMenu");
  if (!drop || !menu) return;
  drop.classList.add("open");
  if (btn) btn.setAttribute("aria-expanded", "true");
  menu.hidden = false;
  placeConfigMenu();
}

function formatCalSens(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "0.86";
  return String(parseFloat(n.toPrecision(8)));
}

function paintSensHint(spec) {
  const hint = $("mouse_sens_hint");
  if (!hint) return;
  if (!spec || !spec.name) {
    hint.textContent = "Your in-game sensitivity.";
    return;
  }
  hint.textContent = `Your ${spec.name} sensitivity.`;
}

function paintGame(settings) {
  const id = String(settings.game || gameIdFromEngine(settings.engine) || "");
  const spec = GAMES[id];
  paintBrandGame(spec);
  paintSensHint(spec);
  const wrap = $("game-selected");
  const name = $("game-name");
  const hint = $("game-hint");
  const art = $("game-art");
  if (!wrap || !name) return;
  if (spec) {
    wrap.classList.add("has-game");
    wrap.classList.toggle("is-generic", Boolean(spec.generic) || !spec.art);
    wrap.title = settings.engine || "";
    name.textContent = spec.name;
    if (hint) hint.textContent = spec.generic ? "generic model" : "click to choose game";
    if (art) {
      if (spec.art) art.src = spec.art;
      else art.removeAttribute("src");
    }
  } else {
    wrap.classList.remove("has-game", "is-generic");
    wrap.title = "";
    name.textContent = "No game selected";
    if (hint) hint.textContent = "click to choose game";
    if (art) art.removeAttribute("src");
  }
  document.querySelectorAll("#gamePicker [data-game]").forEach((btn) => {
    btn.classList.toggle("is-on", btn.dataset.game === id);
  });
  const label = spec ? spec.name : "No game selected";
  const artSrc = spec && spec.art ? spec.art : "";
  [["dockGameArt", "dockGameName"], ["dashGameArt", "dashGameName"]].forEach(([artId, nameId]) => {
    const img = $(artId);
    const text = $(nameId);
    if (text) text.textContent = label;
    if (img) {
      if (artSrc) img.src = artSrc;
      else img.removeAttribute("src");
      img.closest(".dock-art, .dash-hero-art")?.classList.toggle("has-art", Boolean(artSrc));
    }
  });
}

function parseBones(value) {
  const raw = Array.isArray(value)
    ? value.map((item) => String(item).trim())
    : String(value || "").split(",");
  const seen = new Set();
  const out = [];
  raw.forEach((item) => {
    let key = String(item || "").trim().toLowerCase().replace(/\s+/g, "_");
    if (!key) return;
    key = BONE_ALIASES[key] || key;
    if (!CS2_BONE_BY_KEY.has(key) || seen.has(key)) return;
    seen.add(key);
    out.push(key);
  });
  return out;
}

function boneLabel(key) {
  return CS2_BONE_BY_KEY.get(key)?.name || String(key || "").replace(/_/g, " ");
}

function formatBoneSummary(value) {
  const names = parseBones(value).map(boneLabel);
  return names.length ? names.join(", ") : "—";
}

function esc(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function boneFieldSpec(id) {
  for (const studio of Object.values(BONE_STUDIOS)) {
    const field = studio.fields.find((item) => item.id === id);
    if (field) return field;
  }
  return null;
}

function setBoneValue(id, keys, persist) {
  let unique = parseBones(Array.isArray(keys) ? keys.join(",") : keys);
  const spec = boneFieldSpec(id);
  if (spec && spec.single) unique = unique.slice(0, 1);
  const text = unique.join(",");
  const input = $(id);
  if (input) input.value = text;
  lastSettings[id] = text;
  const summary = $(`${id}_summary`);
  if (summary) summary.textContent = formatBoneSummary(text);
  if (persist && !applyingRemote) queueUpdate(id, text);
}

function renderBones(settings) {
  BONE_FIELD_IDS.forEach((id) => {
    if (settings[id] == null && lastSettings[id] == null) return;
    setBoneValue(id, settings[id] != null ? settings[id] : lastSettings[id], false);
  });
  if (bonePicker.open && bonePicker.option) {
    bonePicker.selected = parseBones(lastSettings[bonePicker.option]);
    paintBonePickSelection();
    paintBoneStudioList();
  }
}

function hideBonePickTip() {
  bonePicker.nearId = null;
  const tip = $("bonePickTip");
  if (tip) {
    tip.hidden = true;
    tip.textContent = "";
  }
  document.querySelectorAll(".bone-dot.is-near").forEach((el) => el.classList.remove("is-near"));
}

function paintBonePickSelection() {
  const chosen = new Set(bonePicker.selected);
  document.querySelectorAll("#bonePickDots [data-bone-key]").forEach((el) => {
    const on = chosen.has(el.dataset.boneKey);
    el.classList.toggle("is-on", on);
    el.setAttribute("aria-pressed", String(on));
  });
}

function paintBoneStudioList() {
  const list = $("boneStudioList");
  const spec = BONE_STUDIOS[bonePicker.studio];
  if (!list || !spec) return;
  list.innerHTML = spec.fields
    .map((field) => {
      const keys = parseBones(lastSettings[field.id]);
      const on = field.id === bonePicker.option;
      const detail = field.single
        ? keys.length
          ? boneLabel(keys[0])
          : "—"
        : `Bones: ${keys.length ? keys.map(boneLabel).join(", ") : "—"}`;
      return `<button type="button" class="bone-studio-item${on ? " is-on" : ""}" data-bone-option="${esc(field.id)}">
        <strong>${esc(field.label)}</strong>
        <small>${esc(detail)}</small>
      </button>`;
    })
    .join("");
}

function selectBoneStudioOption(id) {
  const spec = BONE_STUDIOS[bonePicker.studio];
  const field = spec && spec.fields.find((item) => item.id === id);
  if (!field) return;
  bonePicker.option = field.id;
  bonePicker.single = Boolean(field.single);
  bonePicker.selected = parseBones(lastSettings[field.id]);
  if (bonePicker.single) bonePicker.selected = bonePicker.selected.slice(0, 1);
  hideBonePickTip();
  paintBonePickSelection();
  paintBoneStudioList();
}

function toggleBonePick(key) {
  if (!CS2_BONE_BY_KEY.has(key) || !bonePicker.option) return;
  if (bonePicker.single) {
    bonePicker.selected = [key];
  } else {
    const i = bonePicker.selected.indexOf(key);
    if (i >= 0) bonePicker.selected.splice(i, 1);
    else bonePicker.selected.push(key);
  }
  paintBonePickSelection();
  setBoneValue(bonePicker.option, bonePicker.selected, true);
  paintBoneStudioList();
}

function updateBonePickHover(event) {
  const frame = $("bonePickFrame");
  const tip = $("bonePickTip");
  if (!frame || !tip || !bonePicker.open) return;
  const reach = Math.max(32, Math.min(frame.clientWidth, frame.clientHeight) * 0.1);
  let best = null;
  let bestDist = reach;
  document.querySelectorAll("#bonePickDots [data-bone-id]").forEach((el) => {
    const box = el.getBoundingClientRect();
    const dist = Math.hypot(
      event.clientX - (box.left + box.width / 2),
      event.clientY - (box.top + box.height / 2)
    );
    if (dist < bestDist) {
      bestDist = dist;
      best = el;
    }
  });
  if (!best) {
    hideBonePickTip();
    return;
  }
  const id = Number(best.dataset.boneId);
  if (bonePicker.nearId !== id) {
    document.querySelectorAll(".bone-dot.is-near").forEach((el) => el.classList.remove("is-near"));
    best.classList.add("is-near");
    bonePicker.nearId = id;
    const bone = CS2_BONE_MAP.get(id);
    const x = Number(best.style.getPropertyValue("--x").replace("%", ""));
    let side = bone?.side || "right";
    if (x < 22) side = "right";
    else if (x > 78) side = "left";
    tip.dataset.side = side;
    tip.style.setProperty("--x", `${Number.isFinite(x) ? x : 50}%`);
    tip.style.setProperty("--y", best.style.getPropertyValue("--y") || "50%");
    tip.innerHTML = bone ? `${esc(bone.name)} <em>${id}</em>` : String(id);
  }
  tip.hidden = false;
}

function closeBoneStudio() {
  hideBonePickTip();
  bonePicker.open = false;
  bonePicker.studio = "";
  bonePicker.option = "";
  bonePicker.selected = [];
  bonePicker.single = false;
  const overlay = $("boneStudio");
  if (overlay) {
    overlay.hidden = true;
    overlay.classList.remove("is-bones");
  }
  document.body.classList.remove("script-editor-open");
}

function openBoneStudio(studioId, optionId) {
  const spec = BONE_STUDIOS[studioId];
  const overlay = $("boneStudio");
  const body = $("boneStudioBody");
  const title = $("boneStudioTitle");
  if (!spec || !overlay || !body) return;
  bonePicker.studio = studioId;
  bonePicker.open = true;
  if (title) title.textContent = spec.title;
  body.innerHTML = `
    <div class="bone-studio">
      <div class="bone-studio-main">
        <div class="bone-pick-stage">
          <div class="bone-pick-frame" id="bonePickFrame">
            <img src="assets/cs2-player.png" alt="CS2 player model" draggable="false">
            <div class="bone-pick-dots" id="bonePickDots"></div>
            <div class="bone-pick-tip" id="bonePickTip" hidden></div>
          </div>
        </div>
      </div>
      <div class="bone-studio-list" id="boneStudioList"></div>
    </div>`;
  const dots = $("bonePickDots");
  if (dots) {
    dots.innerHTML = CS2_BONES.map(
      (bone) =>
        `<button type="button" class="bone-dot" data-bone-id="${bone.id}" data-bone-key="${esc(bone.key)}" data-side="${bone.side}" style="--x:${bone.x}%;--y:${bone.y}%" aria-pressed="false" aria-label="${esc(bone.name)}"></button>`
    ).join("");
  }
  overlay.hidden = false;
  overlay.classList.add("is-bones");
  document.body.classList.add("script-editor-open");
  const start =
    optionId && spec.fields.some((field) => field.id === optionId)
      ? optionId
      : spec.fields[0].id;
  selectBoneStudioOption(start);
}

function bindBoneStudio() {
  document.addEventListener("click", (event) => {
    const openBtn = event.target.closest(".script-bone-editor[data-bone-studio]");
    if (openBtn) {
      event.preventDefault();
      openBoneStudio(openBtn.dataset.boneStudio, openBtn.dataset.boneOption || "");
      return;
    }
    if (!bonePicker.open) return;
    const optionBtn = event.target.closest("[data-bone-option]");
    if (optionBtn && optionBtn.closest("#boneStudio")) {
      event.preventDefault();
      selectBoneStudioOption(optionBtn.dataset.boneOption);
      return;
    }
    const dot = event.target.closest("#bonePickDots [data-bone-key]");
    if (dot) {
      event.preventDefault();
      toggleBonePick(dot.dataset.boneKey);
    }
  });
  $("boneStudioBack")?.addEventListener("click", (event) => {
    event.preventDefault();
    closeBoneStudio();
  });
  $("boneStudio")?.addEventListener("click", (event) => {
    if (event.target.id === "boneStudio") closeBoneStudio();
  });
  $("boneStudioBody")?.addEventListener("pointermove", (event) => {
    if (!bonePicker.open || !event.target.closest("#bonePickFrame")) return;
    updateBonePickHover(event);
  });
  $("boneStudioBody")?.addEventListener("pointerout", (event) => {
    if (!event.target.closest("#bonePickFrame")) return;
    if (event.relatedTarget?.closest?.("#bonePickFrame")) return;
    hideBonePickTip();
  });
}

function findAimCurve(id) {
  return AIM_CURVE_MAP.get(String(id || "").trim()) || AIM_CURVE_MAP.get("ease_out");
}

function curvePath(fn, w = 112, h = 64, pad = 8) {
  const pts = [];
  for (let i = 0; i <= 36; i++) {
    const t = i / 36;
    const x = pad + t * (w - pad * 2);
    const y = h - pad - Math.min(1, Math.max(0, fn(t))) * (h - pad * 2);
    pts.push(`${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`);
  }
  return pts.join(" ");
}

function curveSvg(fn, on = false) {
  return `<svg viewBox="0 0 112 64" aria-hidden="true"><path d="${curvePath(fn)}" fill="none" stroke="${on ? "var(--accent)" : "#8b96a1"}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
}

function paintCurveButton(id, value) {
  const curve = findAimCurve(value);
  const input = $(id);
  if (input) input.value = curve.id;
  lastSettings[id] = curve.id;
  const btn = document.querySelector(`[data-curve-for="${id}"]`);
  if (btn) btn.innerHTML = `${curveSvg(curve.at, true)}<span>${esc(curve.name)}</span>`;
}

function closeCurvePop() {
  const pop = $("aimDirectorCurvePop");
  if (pop) pop.hidden = true;
}

function openCurvePop(fieldId) {
  const pop = $("aimDirectorCurvePop");
  if (!pop) return;
  const active = findAimCurve($(fieldId)?.value).id;
  pop.innerHTML = AIM_CURVES.map(
    (curve) =>
      `<button type="button" class="curve-card${curve.id === active ? " is-on" : ""}" data-curve-id="${esc(curve.id)}" data-curve-field="${esc(fieldId)}">
        ${curveSvg(curve.at, curve.id === active)}
        <span>${esc(curve.name)}</span>
      </button>`
  ).join("");
  pop.hidden = false;
}

function bindCurvePicker() {
  document.addEventListener("click", (event) => {
    const openBtn = event.target.closest("[data-curve-for]");
    if (openBtn) {
      event.preventDefault();
      const pop = $("aimDirectorCurvePop");
      if (pop && !pop.hidden && pop.querySelector(`[data-curve-field="${openBtn.dataset.curveFor}"]`)) {
        closeCurvePop();
        return;
      }
      openCurvePop(openBtn.dataset.curveFor);
      return;
    }
    const card = event.target.closest("#aimDirectorCurvePop [data-curve-id]");
    if (card) {
      event.preventDefault();
      const id = card.dataset.curveField;
      paintCurveButton(id, card.dataset.curveId);
      if (!applyingRemote) queueUpdate(id, card.dataset.curveId);
      closeCurvePop();
      return;
    }
    if (!event.target.closest("#aimDirectorCurvePop")) closeCurvePop();
  });
}

function parseValue(id, raw) {
  if (TOGGLE_IDS.includes(id)) return Boolean(raw);
  if (TEXT_IDS.includes(id) || SELECT_IDS.includes(id) || BIND_IDS.includes(id)) return raw;
  return Number(raw);
}

function setError(message) {
  const el = $("error-text");
  if (!message) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = message;
}

function updateRangeFill(input) {
  if (window.ArgusTheme && typeof window.ArgusTheme.paintRangeFill === "function") {
    window.ArgusTheme.paintRangeFill(input);
    return;
  }
  if (!input || input.type !== "range") return;
  const min = Number(input.min);
  const max = Number(input.max);
  const value = Number(input.value);
  const span = max - min;
  const p = span <= 0 ? 0 : Math.max(0, Math.min(1, (value - min) / span));
  const thumb = 14;
  const width = input.clientWidth;
  input.style.setProperty("--p", String(p));
  if (width > 0) {
    input.style.setProperty("--fill", `${p * Math.max(0, width - thumb) + thumb}px`);
  } else {
    input.style.removeProperty("--fill");
  }
}

function setOutput(input, id, value) {
  const wrap = input.closest(".control") || input.parentElement;
  const output = (wrap && wrap.querySelector(`output[for="${id}"]`)) || (wrap && wrap.querySelector("output"));
  if (output) output.textContent = formatValue(id, value);
  updateRangeFill(input);
  markRangeChanged(input);
}

function moduleOn(id) {
  return Boolean($(id) && $(id).checked);
}

const LOGGER_MAX = 800;
const LOG_TAG_ORDER = [
  "ERROR",
  "Argus",
  "Status",
  "Capture",
  "Settings",
  "Logger",
  "Game",
  "Config",
  "RP2040",
  "Visuals",
  "AimAssist",
  "AimDirector",
  "AimCorrector",
  "TrackingAssist",
  "SprayAssist",
  "OneTapAssist",
  "TapAssist",
  "Trigger",
  "MagnetTrigger",
];
const ERROR_LOG_COLOR = "#ff6b78";
const LEGACY_LOG_DEFAULTS = {
  ERROR: "#ff6b78",
  Argus: "#79f2b0",
  Status: "#69a7ff",
  Capture: "#3ddc97",
  Settings: "#c5ced4",
  Logger: "#9aa8b4",
  Game: "#ff9d66",
  Config: "#b8c2cc",
  RP2040: "#69a7ff",
  Visuals: "#79f2b0",
  AimAssist: "#3ddc97",
  AimDirector: "#79f2b0",
  AimCorrector: "#ff9d66",
  TrackingAssist: "#69a7ff",
  SprayAssist: "#ff9d66",
  OneTapAssist: "#79f2b0",
  TapAssist: "#c5ced4",
  Trigger: "#ff6b78",
  MagnetTrigger: "#69a7ff",
};
let lastLogId = 0;
let loggerLineCount = 0;
let logsRefreshing = false;
let logColors = {};
let logTagOrder = LOG_TAG_ORDER.slice();
let loggerRows = [];
let loggerNoteTimer = 0;

function loggerTabActive() {
  const panel = $("logger");
  return Boolean(panel && panel.classList.contains("active"));
}

function loggerPinnedToBottom(view) {
  return view.scrollHeight - view.scrollTop - view.clientHeight < 28;
}

function themeAccentHex() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  if (raw.startsWith("#") && (raw.length === 4 || raw.length === 7)) {
    if (raw.length === 4) {
      return `#${raw[1]}${raw[1]}${raw[2]}${raw[2]}${raw[3]}${raw[3]}`;
    }
    return raw;
  }
  const rgb = raw.match(/(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
  if (rgb) {
    return `#${[rgb[1], rgb[2], rgb[3]].map((part) => Number(part).toString(16).padStart(2, "0")).join("")}`;
  }
  return "#79f2b0";
}

function isErrorTag(tag, err) {
  return Boolean(err) || tag === "ERROR";
}

function colorForTag(tag, err) {
  if (logColors[tag]) return logColors[tag];
  if (isErrorTag(tag, err)) return ERROR_LOG_COLOR;
  return themeAccentHex();
}

function paintLoggerLine(row, rec) {
  const tag = rec.tag || (rec.err ? "ERROR" : "");
  const msg = rec.msg != null ? String(rec.msg) : "";
  row.dataset.tag = tag;
  row.className = rec.err ? "logger-line is-err" : "logger-line";
  if (logColors[tag]) row.style.setProperty("--log-color", logColors[tag]);
  else if (isErrorTag(tag, rec.err)) row.style.setProperty("--log-color", ERROR_LOG_COLOR);
  else row.style.removeProperty("--log-color");
  row.replaceChildren();
  const tagEl = document.createElement("span");
  tagEl.className = "logger-tag";
  tagEl.textContent = `[${tag}]`;
  const msgEl = document.createElement("span");
  msgEl.className = "logger-msg";
  msgEl.textContent = msg ? ` ${msg}` : "";
  row.append(tagEl, msgEl);
}

function restyleLoggerLines() {
  const view = $("loggerView");
  if (!view) return;
  const rows = view.querySelectorAll(".logger-line");
  rows.forEach((row, index) => {
    const rec = loggerRows[index];
    if (rec) paintLoggerLine(row, rec);
  });
}

function appendLogLines(lines) {
  const view = $("loggerView");
  if (!view || !lines.length) return;
  const stick = loggerPinnedToBottom(view);
  const frag = document.createDocumentFragment();
  for (const rec of lines) {
    const row = document.createElement("div");
    paintLoggerLine(row, rec);
    frag.appendChild(row);
    loggerRows.push({
      tag: rec.tag || (rec.err ? "ERROR" : ""),
      msg: rec.msg != null ? String(rec.msg) : "",
      err: Boolean(rec.err),
      line: rec.line || `[${rec.tag || "?"}] ${rec.msg || ""}`,
    });
  }
  view.appendChild(frag);
  loggerLineCount += lines.length;
  while (loggerLineCount > LOGGER_MAX && view.firstChild) {
    view.removeChild(view.firstChild);
    loggerRows.shift();
    loggerLineCount -= 1;
  }
  if (stick) view.scrollTop = view.scrollHeight;
}

function renderLoggerColorGrid() {
  const grid = $("loggerColorGrid");
  if (!grid) return;
  grid.innerHTML = logTagOrder
    .map((tag) => {
      const color = colorForTag(tag, tag === "ERROR");
      return `<label class="logger-color-row"><span>[${escapeHtml(tag)}]</span><input type="color" class="log-color" data-log-tag="${escapeHtml(tag)}" value="${escapeHtml(color)}" /></label>`;
    })
    .join("");
}

async function persistLogColors() {
  try {
    localStorage.setItem("argusLogColors", JSON.stringify(logColors));
  } catch {
    /* ignore */
  }
  try {
    if (window.pywebview?.api?.save_log_colors) {
      await window.pywebview.api.save_log_colors(logColors);
    }
  } catch {
    /* ignore */
  }
}

async function loadLogColors() {
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem("argusLogColors") || "{}");
  } catch {
    stored = {};
  }
  try {
    if (window.pywebview?.api?.load_log_colors) {
      const result = await window.pywebview.api.load_log_colors();
      if (result?.ok && result.colors && typeof result.colors === "object") {
        stored = { ...stored, ...result.colors };
      }
    }
    if (window.pywebview?.api?.get_log_tags) {
      const result = await window.pywebview.api.get_log_tags();
      if (result?.ok && Array.isArray(result.tags) && result.tags.length) {
        logTagOrder = result.tags.map((item) => item.tag);
      }
    }
  } catch {
    /* keep defaults */
  }
  logColors = {};
  for (const [tag, color] of Object.entries(stored || {})) {
    const legacy = LEGACY_LOG_DEFAULTS[tag];
    if (legacy && String(color).toLowerCase() === legacy.toLowerCase()) continue;
    logColors[tag] = color;
  }
  renderLoggerColorGrid();
  restyleLoggerLines();
}

function openLoggerColorPicker() {
  const overlay = $("loggerColorPicker");
  if (!overlay) return;
  renderLoggerColorGrid();
  overlay.hidden = false;
  requestAnimationFrame(() => overlay.classList.add("open"));
}

function closeLoggerColorPicker() {
  const overlay = $("loggerColorPicker");
  if (!overlay) return;
  overlay.classList.remove("open");
  overlay.hidden = true;
}

function showLoggerNote(text) {
  const note = $("loggerExportNote");
  if (!note) return;
  note.hidden = false;
  note.textContent = text;
  requestAnimationFrame(() => note.classList.add("show"));
  clearTimeout(loggerNoteTimer);
  loggerNoteTimer = setTimeout(() => {
    note.classList.remove("show");
    loggerNoteTimer = setTimeout(() => {
      note.hidden = true;
      note.textContent = "";
    }, 220);
  }, 2400);
}

function buildLoggerHtml() {
  const rows = loggerRows
    .map((rec) => {
      const color = colorForTag(rec.tag, rec.err);
      const tag = escapeHtml(rec.tag || "LOG");
      const msg = escapeHtml(rec.msg || "");
      return `<div class="line"><span class="tag" style="color:${color}">[${tag}]</span><span class="msg">${msg ? ` ${msg}` : ""}</span></div>`;
    })
    .join("\n");
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Argus session log</title>
  <style>
    body { margin: 0; padding: 28px; background: #080c10; color: #c5ced4; font: 13px/1.55 "DM Mono", ui-monospace, monospace; }
    h1 { margin: 0 0 6px; color: #edf2f5; font: 600 18px Inter, sans-serif; }
    p { margin: 0 0 22px; color: #6e7a86; font: 11px Inter, sans-serif; }
    .line { white-space: pre-wrap; word-break: break-word; }
    .line { color: #c5ced4; }
    .tag { font-weight: 700; }
    .msg { color: #c5ced4; }
  </style>
</head>
<body>
  <h1>Argus session log</h1>
  <p>${escapeHtml($("loggerPath")?.textContent || "current session")}</p>
  ${rows}
</body>
</html>`;
}

async function exportLoggerHtml() {
  if (!loggerRows.length) {
    setError("Nothing to export");
    return;
  }
  const html = buildLoggerHtml();
  try {
    if (window.pywebview?.api?.export_logs_html) {
      const result = await window.pywebview.api.export_logs_html(html);
      if (!result.ok) {
        setError(result.error || "Could not export log");
        return;
      }
      setError("");
      showLoggerNote(result.path ? `exported to ${result.path}` : "exported");
      return;
    }
  } catch (err) {
    setError(String(err));
  }
}

async function refreshLogs() {
  if (!loggerTabActive() || logsRefreshing || !window.pywebview || !window.pywebview.api) return;
  logsRefreshing = true;
  try {
    const result = await window.pywebview.api.get_logs(lastLogId);
    const pathEl = $("loggerPath");
    if (pathEl && result.path) pathEl.textContent = result.path;
    const lines = Array.isArray(result.lines) ? result.lines : [];
    if (lines.length) appendLogLines(lines);
    if (result.next_id != null) lastLogId = result.next_id;
  } catch (err) {
    setError(String(err));
  } finally {
    logsRefreshing = false;
  }
}

function showTab(id) {
  // Without a current humanizer profile the rest of the menu stays locked.
  if (window.ArgusHumanizer && window.ArgusHumanizer.locks(id)) {
    window.ArgusHumanizer.nudge();
    id = "humanizer";
  }
  const tabs = document.querySelectorAll(".nav-item[data-tab]");
  const panels = document.querySelectorAll(".tab-panel");
  const titles = {
    home: "Dashboard",
    modules: "Modules",
    visual: "Visual",
    humanizer: "Humanizer",
    director: "Aim director",
    prediction: "Target prediction",
    corrector: "Aim corrector",
    tracking: "Tracking assist",
    spray: "Spray assist",
    tap: "Tap assist",
    trigger: "Trigger",
    magnet: "Magnet trigger",
    rpaim: "Aim assist",
    onetap: "One tap assist",
    inventory: "CS2 inventory",
    stats: "CS2 stats",
    logger: "Logger",
    settings: "Settings",
  };
  const eyebrows = {
    home: "Overview",
    visual: "Overview",
    humanizer: "Overview",
    director: "Aim",
    prediction: "Aim",
    corrector: "Aim",
    tracking: "Aim",
    spray: "Aim",
    rpaim: "Aim",
    tap: "Fire",
    trigger: "Fire",
    magnet: "Fire",
    onetap: "Fire",
    inventory: "Extras",
    stats: "Extras",
    modules: "System",
    logger: "System",
    settings: "System",
  };
  const subs = {
    home: "Session status, live performance and quick module switches.",
    visual: "Choose which guides the click-through overlay draws on the game.",
    humanizer: "A 4 minute aim session that records how you really move your mouse.",
    director: "A small continuous pull toward a bone you pick.",
    prediction: "Leads a moving player, with your own view motion removed.",
    corrector: "Bends a flick up or down to meet a bone's height.",
    tracking: "Softens small slips once you are already on a player.",
    spray: "Slides sideways onto the closest player while you fire.",
    rpaim: "Slows your real mouse over bones you pick, in hardware.",
    tap: "One nudge toward a bone at the moment you click.",
    trigger: "Fires while its key is held and you are on a bone.",
    magnet: "Hold its key to pull onto a bone and fire.",
    onetap: "Cleans a first click onto the head, leaves sprays alone.",
    inventory: "Every item on your Steam account, with filters and prices.",
    stats: "Your ranks, ratings and recent matches, from Leetify.",
    modules: "Turn modules on or off and read how each one behaves.",
    logger: "A live view of this session's log.",
    settings: "Appearance, input, game and safety.",
  };
  tabs.forEach((item) => item.classList.toggle("active", item.dataset.tab === id));
  panels.forEach((page) => {
    const panelId = page.dataset.tabPanel || page.id;
    page.classList.toggle("active", panelId === id);
  });
  const title = $("pageTitle");
  const eyebrow = $("pageEyebrow");
  if (title) title.textContent = titles[id] || id;
  if (eyebrow) eyebrow.textContent = eyebrows[id] || "";
  if ($("pageCrumb")) $("pageCrumb").textContent = titles[id] || id;
  if ($("pageSub")) $("pageSub").textContent = subs[id] || "";
  const main = document.querySelector(".main");
  if (main) main.scrollTop = 0;
  if (window.ArgusTheme && typeof window.ArgusTheme.paintAllRangeFills === "function") {
    window.ArgusTheme.paintAllRangeFills();
  }
  if (id === "logger") refreshLogs();
  if (id === "inventory" && window.ArgusInventory) window.ArgusInventory.onShow();
  if (id === "stats" && window.ArgusStats) window.ArgusStats.onShow();
  if (id === "humanizer" && window.ArgusHumanizer) window.ArgusHumanizer.onShow();
}

const MODULE_TABS = {
  visuals_enabled: "visual",
  aim_director: "director",
  target_prediction: "prediction",
  aim_corrector: "corrector",
  tracking_assist: "tracking",
  spray_acquire: "spray",
  rp_aim_assist: "rpaim",
  tap_assist: "tap",
  trigger_enabled: "trigger",
  magnet: "magnet",
  rp_one_tap_assist: "onetap",
  cs2_inventory: "inventory",
  cs2_stats: "stats",
  logging_enabled: "logger",
};

function syncNavSections() {
  document.querySelectorAll(".nav-section").forEach((section) => {
    const visible = [...section.querySelectorAll(".nav-item")].some(
      (item) => getComputedStyle(item).display !== "none",
    );
    section.hidden = !visible;
  });
}

function buildModuleTiles() {
  const grid = $("moduleTiles");
  if (!grid) return;
  grid.replaceChildren();
  Object.keys(MODULE_TABS).forEach((sourceId) => {
    const source = $(sourceId);
    const row = source?.closest(".module-row");
    if (!row) return;
    const tab = MODULE_TABS[source.id];
    const tile = document.createElement("div");
    tile.className = "mod-tile";
    tile.classList.toggle("rp-only", row.classList.contains("rp-only"));
    tile.dataset.source = source.id;
    const open = document.createElement("button");
    open.type = "button";
    open.className = "mod-tile-open";
    const icon = document.createElement("span");
    icon.className = "mod-tile-icon";
    const navIcon = tab && document.querySelector(`.sidebar .nav-item[data-tab="${tab}"] svg`);
    if (navIcon) icon.append(navIcon.cloneNode(true));
    const text = document.createElement("span");
    text.className = "mod-tile-text";
    const name = document.createElement("strong");
    const strong = row.querySelector(".module-copy strong");
    name.textContent = strong ? strong.firstChild.textContent.trim() : source.id;
    const tag = strong?.querySelector(".rp-tag");
    if (tag) {
      const chip = document.createElement("em");
      chip.textContent = tag.textContent;
      name.append(chip);
    }
    const desc = document.createElement("small");
    desc.textContent = row.querySelector(".module-copy small")?.textContent || "";
    text.append(name, desc);
    open.append(icon, text);
    open.addEventListener("click", () => {
      if (source.checked && tab) showTab(tab);
      else showTab("modules");
    });
    const pill = document.createElement("label");
    pill.className = "module-pill";
    pill.setAttribute("aria-label", `Toggle ${name.textContent}`);
    const mirror = document.createElement("input");
    mirror.type = "checkbox";
    mirror.addEventListener("change", () => {
      source.checked = mirror.checked;
      source.dispatchEvent(new Event("change", { bubbles: true }));
    });
    pill.append(mirror, document.createElement("i"));
    tile.append(open, pill);
    grid.append(tile);
  });
  syncModuleTiles();
}

function syncModuleTiles() {
  document.querySelectorAll("#moduleTiles .mod-tile").forEach((tile) => {
    const on = moduleOn(tile.dataset.source);
    tile.classList.toggle("is-on", on);
    const mirror = tile.querySelector("input");
    if (mirror) mirror.checked = on;
  });
}

let lastRunning = null;
const STAT_HISTORY = 48;
const statHistory = {};

function sparkPaths(values) {
  const max = Math.max(1, ...values) * 1.15;
  const step = 100 / (STAT_HISTORY - 1);
  const offset = STAT_HISTORY - values.length;
  const points = values.map((v, i) => [(offset + i) * step, 26 - (v / max) * 24]);
  const line = points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  const fill = points.length ? `${line} L100 28 L${points[0][0].toFixed(2)} 28 Z` : "";
  return { line, fill };
}

const LAT_SEGMENTS = ["infer", "age", "compute", "send"];

function fmtLatency(value) {
  const v = Number(value);
  if (value == null || !Number.isFinite(v)) return "—";
  return v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
}

function paintLatency(status, running, pauseLabel, dry) {
  const root = $("dashLatency");
  if (!root) return;
  const lat = status.latency || {};
  const stages = lat.stages || {};
  root.querySelectorAll("tr[data-lat]").forEach((row) => {
    const s = stages[row.dataset.lat] || {};
    row.children[1].textContent = fmtLatency(s.avg);
    row.children[2].textContent = fmtLatency(s.p95);
    row.children[3].textContent = fmtLatency(s.max);
  });
  const total = stages.total || {};
  $("latTotalAvg").textContent = fmtLatency(total.avg);
  $("latTotalP95").textContent = fmtLatency(total.p95);
  $("latTotalP99").textContent = fmtLatency(total.p99);
  $("latTotalMax").textContent = fmtLatency(total.max);

  // What the average frame spent in each stage between grab and send.
  const parts = LAT_SEGMENTS.map((key) => Math.max(0, Number(stages[key]?.avg) || 0));
  const sum = parts.reduce((a, b) => a + b, 0);
  root.querySelectorAll("#latBar i").forEach((seg) => {
    const part = parts[LAT_SEGMENTS.indexOf(seg.dataset.seg)] || 0;
    seg.style.width = sum > 0 ? `${((part / sum) * 100).toFixed(2)}%` : "0";
  });

  const bins = lat.hist?.bins || [];
  const hist = $("latHist");
  const key = bins.join(",");
  if (hist && hist.dataset.key !== key) {
    hist.dataset.key = key;
    const top = Math.max(1, ...bins);
    hist.innerHTML = bins.map((v) => `<i style="height:${Math.max(4, (v / top) * 100).toFixed(1)}%"></i>`).join("");
  }
  if ($("latHistHi")) $("latHistHi").textContent = bins.length ? `${fmtLatency(lat.hist.hi)} ms` : "—";

  const pill = $("latState");
  if (pill) {
    const sending = running && total.ago != null && total.ago < 3;
    let text = "Capture idle";
    if (running && pauseLabel) text = `Paused · ${pauseLabel}`;
    else if (sending) text = dry ? "Live · dry run" : "Live";
    else if (running) text = "Waiting for input to time";
    else if ((total.n || 0) > 0 || (stages.infer?.n || 0) > 0) text = "Capture idle · last run";
    pill.classList.toggle("is-on", sending && !pauseLabel);
    const label = pill.querySelector("span");
    if (label && label.textContent !== text) label.textContent = text;
  }
}

function paintDashboard(status, settings) {
  const running = Boolean(status.running);
  const dry = Boolean(settings.dry_run);
  const pauseReason = running ? String(status.pause_reason || "") : "";
  const pauseLabel = pauseReason === "menu" ? "menu open" : pauseReason === "hotkey" ? "pause key" : "";
  document.body.classList.toggle("is-running", running);
  document.body.classList.toggle("is-paused", Boolean(pauseLabel));
  const stateText = running
    ? (pauseLabel ? "Paused" : dry ? "Running · dry run" : "Live")
    : "Idle";
  if ($("dockStatus")) $("dockStatus").textContent = stateText;
  if ($("dashState")) {
    $("dashState").textContent = running
      ? (pauseLabel ? `Capture running · assist paused (${pauseLabel})` : `Capture ${dry ? "running in dry run" : "live"}`)
      : "Capture idle";
  }
  paintLatency(status, running, pauseLabel, dry);
  document.querySelectorAll("[data-capture-toggle]").forEach((btn) => {
    btn.setAttribute("aria-label", running ? "Stop capture" : "Start capture");
    const label = btn.querySelector("span");
    if (label) label.textContent = running ? "Stop capture" : "Start capture";
  });
  if (lastRunning !== null && lastRunning !== running) {
    toast(running ? "Capture started" : "Capture stopped", running ? "success" : "info");
  }
  lastRunning = running;
  const target = $("dashTarget");
  if (target) {
    const locked = running && Boolean(status.target_present);
    target.classList.toggle("is-on", locked);
    target.querySelector("span").textContent = locked ? "Target locked" : "No target";
  }
  document.querySelectorAll(".stat[data-stat]").forEach((stat) => {
    const key = stat.dataset.stat;
    const value = running ? Number(status[key]) || 0 : 0;
    const history = (statHistory[key] ||= []);
    history.push(value);
    if (history.length > STAT_HISTORY) history.shift();
    const shown = stat.querySelector("strong b");
    if (shown) shown.textContent = key.endsWith("_ms") ? value.toFixed(1) : String(Math.round(value));
    const { line, fill } = sparkPaths(history);
    stat.querySelector(".spark-line")?.setAttribute("d", line);
    stat.querySelector(".spark-fill")?.setAttribute("d", fill);
  });
  if ($("dashMode")) $("dashMode").textContent = dry ? "Dry run" : "Live input";
  if ($("dashInput")) {
    $("dashInput").textContent = String(settings.input_method || "windows") === "rp2040" ? "RP2040" : "Windows API";
  }
  if ($("dashHotkey")) $("dashHotkey").textContent = settings.menu_hotkey || "insert";
  if ($("dashPause")) $("dashPause").textContent = settings.pause_hotkey || "Unbound";
  if ($("dashConfig")) $("dashConfig").textContent = activeConfig || "Default";
}

function bindDashboard() {
  buildModuleTiles();
  document.querySelectorAll("[data-capture-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = document.body.classList.contains("is-running") ? $("stop-capture") : $("start-capture");
      if (target && !target.disabled) target.click();
      else if (!lastSettings.engine) openGamePicker();
    });
  });
  document.querySelectorAll("[data-open-game]").forEach((btn) => btn.addEventListener("click", openGamePicker));
  document.querySelectorAll("[data-go-tab]").forEach((btn) => {
    btn.addEventListener("click", () => showTab(btn.dataset.goTab));
  });
}

function syncModules() {
  const vis = moduleOn("visuals_enabled");
  const aimDirector = moduleOn("aim_director");
  const prediction = moduleOn("target_prediction");
  const aimCorrector = moduleOn("aim_corrector");
  const trackingAssist = moduleOn("tracking_assist");
  const sprayAcquire = moduleOn("spray_acquire");
  const tap = moduleOn("tap_assist");
  const trig = moduleOn("trigger_enabled");
  const mag = moduleOn("magnet");
  const rpInput = String(lastSettings.input_method || "windows") === "rp2040";
  const rpAim = moduleOn("rp_aim_assist");
  const rpOneTap = moduleOn("rp_one_tap_assist");
  const logging = moduleOn("logging_enabled");
  const inventory = Boolean(window.ArgusInventory && window.ArgusInventory.enabled);
  const stats = Boolean(window.ArgusStats && window.ArgusStats.enabled);
  const body = document.body;
  body.classList.toggle("mod-visuals", vis);
  body.classList.toggle("mod-aim-director", aimDirector);
  body.classList.toggle("mod-prediction", prediction);
  body.classList.toggle("mod-aim-corrector", aimCorrector);
  body.classList.toggle("mod-tracking-assist", trackingAssist);
  body.classList.toggle("mod-spray-acquire", sprayAcquire);
  body.classList.toggle("mod-tap", tap);
  body.classList.toggle("mod-trigger", trig);
  body.classList.toggle("mod-magnet", mag);
  body.classList.toggle("input-rp2040", rpInput);
  body.classList.toggle("mod-rp-aim", rpAim);
  body.classList.toggle("mod-rp-one-tap", rpOneTap);
  body.classList.toggle("mod-logger", logging);
  body.classList.toggle("mod-inventory", inventory);
  body.classList.toggle("mod-stats", stats);
  const directorDeadzone = moduleOn("aim_director_deadzone");
  const deadzoneRow = $("aimDirectorDeadzoneRow");
  const deadzoneInput = $("aim_director_deadzone_px");
  if (deadzoneRow) deadzoneRow.classList.toggle("is-disabled", !directorDeadzone);
  if (deadzoneInput) deadzoneInput.disabled = !directorDeadzone;
  document.querySelectorAll(".module-row").forEach((row) => {
    const input = row.querySelector("input");
    row.classList.toggle("is-on", Boolean(input && input.checked));
  });
  const active = document.querySelector(".tab-panel.active");
  const id = active && (active.dataset.tabPanel || active.id);
  if (
    (id === "visual" && !vis) ||
    (id === "director" && !aimDirector) ||
    (id === "prediction" && !prediction) ||
    (id === "corrector" && !aimCorrector) ||
    (id === "tracking" && !trackingAssist) ||
    (id === "spray" && !sprayAcquire) ||
    (id === "tap" && !tap) ||
    (id === "trigger" && !trig) ||
    (id === "magnet" && !mag) ||
    (id === "rpaim" && (!rpAim || !rpInput)) ||
    (id === "onetap" && (!rpOneTap || !rpInput)) ||
    (id === "logger" && !logging) ||
    (id === "inventory" && !inventory) ||
    (id === "stats" && !stats)
  ) {
    showTab("home");
  }
  syncHumanDelay();
  syncNavSections();
  syncModuleTiles();
  if (window.ArgusTheme && typeof window.ArgusTheme.syncAltNavHit === "function") {
    window.ArgusTheme.syncAltNavHit();
  }
  if (window.ArgusModules) window.ArgusModules.refresh();
}

const HUMAN_DELAY_ROWS = [
  { toggle: "trigger_humanized_delay", row: "triggerInitialDelayRow", range: "trigger_initial_delay_ms" },
  { toggle: "magnet_trigger_humanized_delay", row: "magnetInitialDelayRow", range: "magnet_trigger_initial_delay_ms" },
];

function humanDelayHint() {
  const cr = lastSettings.click_reaction || {};
  const n = Number(cr.n) || 0;
  if (!n) {
    return "Draws the wait from your own click reactions. Do a Humanizer run with the Click reaction part first; until then the delay below is used.";
  }
  return `Draws each wait from ${n} of your own click reactions (${Math.round(cr.min)} to ${Math.round(cr.max)} ms, typically ${Math.round(cr.median)} ms) instead of one fixed delay.`;
}

// With recorded reactions to draw from, the fixed delay is not used, so grey
// it out. With none, the fixed delay is what actually applies: leave it live.
function syncHumanDelay() {
  const have = (Number(lastSettings.click_reaction?.n) || 0) > 0;
  const hint = humanDelayHint();
  for (const { toggle, row, range } of HUMAN_DELAY_ROWS) {
    const box = $(toggle);
    const rowEl = $(row);
    const input = $(range);
    if (!box || !rowEl || !input) continue;
    const humanized = box.checked && have;
    rowEl.classList.toggle("is-disabled", humanized);
    input.disabled = humanized;
    const note = box.closest(".setting-toggle")?.querySelector("[data-human-delay-hint]");
    if (note && note.textContent !== hint) note.textContent = hint;
  }
}

function bindModuleWarn() {
  window.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (bonePicker.open) {
      closeBoneStudio();
      return;
    }
    if ($("aimDirectorCurvePop") && !$("aimDirectorCurvePop").hidden) {
      closeCurvePop();
      return;
    }
  });
}

function bindLabel(value) {
  const text = String(value || "").trim();
  return text || "Unbound";
}

function bindButtons(id) {
  return document.querySelectorAll(`[data-bind="${id}"]`);
}

function setBindButton(id, value) {
  if (listenId === id) return;
  const text = String(value || "").trim();
  bindButtons(id).forEach((btn) => {
    btn.textContent = bindLabel(text);
    btn.classList.toggle("is-unbound", !text);
    btn.classList.remove("is-listening");
  });
}

function cancelListen() {
  if (!listenId) return;
  const id = listenId;
  listenId = null;
  setBindButton(id, listenPrev);
  listenPrev = "";
}

function commitBind(name) {
  if (!listenId) return;
  const id = listenId;
  listenId = null;
  listenPrev = "";
  suppressBindClick = true;
  lastSettings[id] = name;
  setBindButton(id, name);
  if (id === "menu_hotkey") {
    const hint = $("hotkey-hint");
    if (hint) hint.textContent = name || "insert";
  }
  queueUpdate(id, name);
  setTimeout(() => {
    suppressBindClick = false;
  }, 0);
}

function mouseBindName(button) {
  if (button === 0) return "mouse1";
  if (button === 1) return "mouse3";
  if (button === 2) return "mouse2";
  if (button === 3) return "mouse4";
  if (button === 4) return "mouse5";
  return "";
}

function keyBindName(event) {
  const code = String(event.code || "");
  const named = {
    ShiftLeft: "lshift",
    ShiftRight: "rshift",
    ControlLeft: "lctrl",
    ControlRight: "rctrl",
    AltLeft: "lalt",
    AltRight: "ralt",
    MetaLeft: "lwin",
    MetaRight: "rwin",
    Space: "space",
    Tab: "tab",
    CapsLock: "capslock",
    Enter: "enter",
    NumpadEnter: "enter",
    Backspace: "backspace",
    Insert: "insert",
    Home: "home",
    End: "end",
    Delete: "delete",
    PageUp: "pageup",
    PageDown: "pagedown",
    ArrowUp: "arrowup",
    ArrowDown: "arrowdown",
    ArrowLeft: "arrowleft",
    ArrowRight: "arrowright",
  };
  if (named[code]) return named[code];
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase();
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  const fKey = fKeyName(event);
  if (fKey) return fKey;
  const key = String(event.key || "").toLowerCase();
  if (key.length === 1 && key !== " ") return key;
  return "";
}

function fKeyName(event) {
  const code = String(event.code || "");
  const key = String(event.key || "");
  const match = /^(?:F|f)([1-9]|1[0-9]|2[0-4])$/.exec(code) || /^(?:F|f)([1-9]|1[0-9]|2[0-4])$/.exec(key);
  if (match) return `f${match[1]}`;
  const vk = Number(event.keyCode || event.which || 0);
  if (vk >= 112 && vk <= 135) return `f${vk - 111}`;
  return "";
}

function startListen(id) {
  if (listenId === id) return;
  if (listenId) cancelListen();
  listenId = id;
  listenPrev = String(lastSettings[id] || "").trim();
  bindButtons(id).forEach((btn) => {
    btn.classList.add("is-listening");
    btn.classList.remove("is-unbound");
    btn.textContent = "Press a key…";
  });
}

function bindKeyListen() {
  BIND_IDS.forEach((id) => {
    bindButtons(id).forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (suppressBindClick) {
          suppressBindClick = false;
          return;
        }
        startListen(id);
      });
    });
  });
  window.addEventListener(
    "keydown",
    (event) => {
      if (!listenId) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Escape" || event.code === "Escape") {
        cancelListen();
        return;
      }
      if (event.repeat) return;
      const name = keyBindName(event);
      if (name) commitBind(name);
    },
    true
  );
  window.addEventListener(
    "keyup",
    (event) => {
      if (!listenId) return;
      event.preventDefault();
      event.stopPropagation();
      const name = keyBindName(event);
      if (name) commitBind(name);
    },
    true
  );
  window.addEventListener(
    "mousedown",
    (event) => {
      if (!listenId) return;
      event.preventDefault();
      event.stopPropagation();
      const name = mouseBindName(event.button);
      if (name) commitBind(name);
    },
    true
  );
  window.addEventListener(
    "pointerdown",
    (event) => {
      if (!listenId || event.pointerType !== "mouse") return;
      event.preventDefault();
      event.stopPropagation();
      const name = mouseBindName(event.button);
      if (name) commitBind(name);
    },
    true
  );
  window.addEventListener(
    "contextmenu",
    (event) => {
      if (!listenId) return;
      event.preventDefault();
      event.stopPropagation();
    },
    true
  );
  window.addEventListener(
    "auxclick",
    (event) => {
      if (!listenId) return;
      event.preventDefault();
      event.stopPropagation();
      const name = mouseBindName(event.button);
      if (name) commitBind(name);
    },
    true
  );
}

function paintDirectorMode(value) {
  const mode = DIRECTOR_MODE_HINTS[value] ? value : "always";
  document.querySelectorAll("#aimDirectorModeSeg [data-director-mode]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.directorMode === mode);
  });
  if ($("aimDirectorModeHint")) $("aimDirectorModeHint").textContent = DIRECTOR_MODE_HINTS[mode];
  if ($("aimDirectorKeyRow")) $("aimDirectorKeyRow").hidden = mode === "always";
  if ($("aimDirectorKeyHint")) {
    $("aimDirectorKeyHint").textContent = mode === "hold" ? "Hold to pull." : "Press to turn the pull on or off.";
  }
}

function bindDirectorMode() {
  const input = $("aim_director_mode");
  document.querySelectorAll("#aimDirectorModeSeg [data-director-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!input || input.value === btn.dataset.directorMode) return;
      input.value = btn.dataset.directorMode;
      paintDirectorMode(input.value);
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
}

function paintInputMethod(value) {
  const method = value === "rp2040" ? "rp2040" : "windows";
  document.querySelectorAll("#inputMethodSeg [data-input-method]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.inputMethod === method);
  });
}

function renderSettings(settings) {
  applyingRemote = true;
  lastSettings = { ...lastSettings, ...settings };
  if (settings.input_method != null) paintInputMethod(settings.input_method);
  for (const id of RANGE_IDS) {
    if (settings[id] == null) continue;
    const input = $(id);
    if (!input) continue;
    input.value = settings[id];
    setOutput(input, id, settings[id]);
  }
  for (const id of TEXT_IDS) {
    if (settings[id] == null) continue;
    const input = $(id);
    if (input) input.value = settings[id];
  }
  for (const id of NUMBER_IDS) {
    if (settings[id] == null) continue;
    const input = $(id);
    if (input) input.value = settings[id];
  }
  for (const id of BIND_IDS) {
    if (settings[id] == null) continue;
    setBindButton(id, settings[id]);
  }
  for (const id of TOGGLE_IDS) {
    if (settings[id] == null) continue;
    const input = $(id);
    if (input) input.checked = Boolean(settings[id]);
  }
  for (const id of SELECT_IDS) {
    if (settings[id] == null) continue;
    const input = $(id);
    if (input) input.value = settings[id];
    if (id === "aim_director_curve") paintCurveButton(id, settings[id]);
    if (id === "aim_director_mode") paintDirectorMode(settings[id]);
  }
  if (settings.cs2_inventory != null && window.ArgusInventory) window.ArgusInventory.applyConfig(settings.cs2_inventory);
  if (settings.cs2_stats != null && window.ArgusStats) window.ArgusStats.applyConfig(settings.cs2_stats);
  paintGame(settings);
  const hint = $("hotkey-hint");
  if (hint) hint.textContent = settings.menu_hotkey || "insert";
  syncModules();
  renderBones(settings);
  repaintDualRanges();
  applyingRemote = false;
}

function renderStatus(status, settings) {
  const merged = { ...lastSettings, ...settings };
  const running = Boolean(status.running);
  const dry = Boolean(merged.dry_run);
  $("run-dot").classList.toggle("on", running && !dry);
  if (status.click_reaction) {
    lastSettings.click_reaction = status.click_reaction;
    syncHumanDelay();
  }
  paintDashboard(status, merged);
  $("start-capture").disabled = running || !merged.engine;
  $("stop-capture").disabled = !running;
  const rp = $("rp2040-status");
  const handshake = $("rp2040-handshake");
  if (rp) {
    const connected = Boolean(status.rp2040_connected);
    const rpSelected = String(merged.input_method || "windows") === "rp2040";
    const port = status.rp2040_port || "?";
    let step = String(status.rp2040_handshake || "");
    if (connected) step = "ack";
    else if (!step && running && rpSelected) step = "syn";
    else if (!rpSelected) step = "idle";
    else if (!step) step = "idle";
    let text = "Using Windows API";
    if (rpSelected) {
      if (step === "ack" || connected) text = `Handshake established on ${port}`;
      else if (step === "synack") text = `SYN-ACK on ${port} — sending ACK`;
      else if (step === "syn") text = `SYN sent on ${port} — waiting for SYN-ACK`;
      else if (step === "fail") text = "No SYN-ACK — is the Feather's USB-C plugged in and running the HID firmware?";
      else if (running) text = "Waiting for SYN-ACK";
      else text = "Handshake when capture starts";
    }
    rp.classList.toggle("is-on", connected);
    rp.querySelector("span").textContent = text;
    if (handshake) {
      handshake.hidden = !rpSelected;
      handshake.dataset.state = rpSelected ? step : "idle";
    }
  }
  if (status.error) setError(status.error);
}

async function flushSettings() {
  const updates = pending;
  pending = {};
  if (!Object.keys(updates).length) return;
  try {
    const result = await window.pywebview.api.set_settings(updates);
    if (!result.ok) {
      setError(result.error || "Could not apply settings");
      if (result.settings) renderSettings(result.settings);
      return;
    }
    setError("");
    if (result.settings) renderSettings(result.settings);
  } catch (err) {
    setError(String(err));
  }
}

function queueUpdate(id, value) {
  pending[id] = value;
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(flushSettings, 50);
}

function bindTriggerBones() {
  bindBoneStudio();
}

function bindTabs() {
  document.querySelectorAll(".nav-item[data-tab]").forEach((tab) => {
    tab.addEventListener("click", () => {
      showTab(tab.dataset.tab);
    });
  });
}

function openConfigNamePicker() {
  closeConfigDrop();
  const overlay = $("configNamePicker");
  const input = $("configNameInput");
  if (!overlay) return;
  if (input) input.value = "";
  overlay.hidden = false;
  requestAnimationFrame(() => {
    overlay.classList.add("open");
    if (input) input.focus();
  });
}

function closeConfigNamePicker() {
  const overlay = $("configNamePicker");
  if (!overlay) return;
  overlay.classList.remove("open");
  overlay.hidden = true;
}

function openConfigDeletePicker() {
  closeConfigDrop();
  const overlay = $("configDeletePicker");
  const text = $("configDeleteText");
  if (!overlay) return;
  const name = activeConfig || "this config";
  if (text) text.textContent = `Delete ${name}? This cannot be undone.`;
  overlay.hidden = false;
  requestAnimationFrame(() => overlay.classList.add("open"));
}

function closeConfigDeletePicker() {
  const overlay = $("configDeletePicker");
  if (!overlay) return;
  overlay.classList.remove("open");
  overlay.hidden = true;
}

function showConfigSaved(name) {
  toast(`Saved to ${name}`, "success");
}

function toast(message, kind = "info") {
  const stack = $("toastStack");
  if (!stack || !message) return;
  const item = document.createElement("div");
  item.className = `toast-item is-${kind}`;
  item.setAttribute("role", "status");
  const dot = document.createElement("i");
  const text = document.createElement("span");
  text.textContent = message;
  item.append(dot, text);
  stack.append(item);
  while (stack.children.length > 4) stack.firstElementChild.remove();
  setTimeout(() => {
    item.classList.add("is-leaving");
    setTimeout(() => item.remove(), 260);
  }, 2600);
}

function openGamePicker() {
  const overlay = $("gamePicker");
  if (!overlay) return;
  overlay.hidden = false;
  requestAnimationFrame(() => overlay.classList.add("open"));
}

function closeGamePicker() {
  const overlay = $("gamePicker");
  if (!overlay) return;
  overlay.classList.remove("open");
  overlay.hidden = true;
}

function bindModuleDetails() {
  document.querySelectorAll(".module-expand").forEach((btn) => {
    btn.addEventListener("click", () => {
      const detail = document.getElementById(btn.getAttribute("aria-controls") || "");
      const row = btn.closest(".module-row");
      const open = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", open ? "false" : "true");
      if (detail) detail.hidden = open;
      if (row) row.classList.toggle("is-open", !open);
    });
  });
}

const PALETTE_ACTIONS = [
  { title: "Save config", sub: "Write current settings to the active config", button: "configSave" },
  { title: "New config", sub: "Create a config from the current settings", button: "configNew" },
  { title: "Choose game", sub: "Switch the detection model", button: "titlebar-game" },
  { title: "Start capture", sub: "Begin screen capture and detection", button: "start-capture" },
  { title: "Stop capture", sub: "Stop screen capture", button: "stop-capture" },
];
const palette = { entries: [], shown: [], index: 0, token: 0 };

function hiddenWithin(el, stop) {
  for (let node = el; node && node !== stop; node = node.parentElement) {
    if (getComputedStyle(node).display === "none") return true;
  }
  return false;
}

function navItemFor(tab) {
  return [...document.querySelectorAll(`.nav-item[data-tab="${tab}"]`)].find(
    (item) => getComputedStyle(item).display !== "none",
  );
}

function rowLabels(row) {
  if (row.classList.contains("module-row")) {
    return {
      title: row.querySelector(".module-copy strong")?.textContent,
      sub: row.querySelector(".module-copy small")?.textContent,
    };
  }
  const strong = row.querySelector("span strong");
  if (strong) return { title: strong.textContent, sub: row.querySelector("span small")?.textContent };
  const span = row.querySelector(":scope > span");
  return { title: span?.firstChild?.textContent, sub: span?.querySelector("small")?.textContent };
}

function paletteEntries() {
  const entries = [];
  document.querySelectorAll(".sidebar .nav-item[data-tab]").forEach((nav) => {
    if (getComputedStyle(nav).display === "none") return;
    const title = nav.querySelector("span")?.textContent.trim() || nav.dataset.tab;
    entries.push({ kind: "Page", title, sub: "", page: title, tab: nav.dataset.tab, icon: nav.querySelector("svg") });
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const tab = panel.dataset.tabPanel || panel.id;
    const nav = navItemFor(tab);
    if (!nav) return;
    const page = nav.querySelector("span")?.textContent.trim() || tab;
    panel.querySelectorAll(".setting-row, .setting-toggle:not(.frost-switch), .field, .module-row, .setting-frost").forEach((row) => {
      if (hiddenWithin(row, panel)) return;
      const { title, sub } = rowLabels(row);
      const clean = String(title || "").trim();
      if (!clean) return;
      entries.push({ kind: "Setting", title: clean, sub: String(sub || "").trim(), page, tab, row, icon: nav.querySelector("svg") });
    });
  });
  PALETTE_ACTIONS.forEach((action) => {
    const button = $(action.button);
    if (!button || button.disabled) return;
    entries.push({ kind: "Action", title: action.title, sub: action.sub, page: "", button });
  });
  return entries;
}

function paletteScore(entry, words) {
  const title = entry.title.toLowerCase();
  const hay = `${title} ${entry.sub.toLowerCase()} ${entry.page.toLowerCase()}`;
  if (!words.every((word) => hay.includes(word))) return -1;
  let score = title.startsWith(words[0]) ? 6 : 0;
  words.forEach((word) => {
    if (title.includes(word)) score += 2;
  });
  if (entry.kind !== "Setting") score += 1;
  return score;
}

// Normal search: pages, settings and actions, each tagged with its group.
function paletteSearchItems(text) {
  const words = text.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) {
    return palette.entries
      .filter((entry) => entry.kind !== "Setting")
      .map((entry) => ({ ...entry, group: `${entry.kind}s` }));
  }
  return palette.entries
    .map((entry) => ({ entry, score: paletteScore(entry, words) }))
    .filter((item) => item.score >= 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, 40)
    .map((item) => ({ ...item.entry, group: "Results" }));
}

function renderPalette() {
  const input = $("paletteInput");
  if (!input) return;
  const text = input.value;
  const commands = window.ArgusCommands;
  const result = commands ? commands.resolve(text) : null;
  palette.token = (palette.token || 0) + 1;
  const token = palette.token;
  const compose = (res) => {
    if (!res) {
      const items = paletteSearchItems(text);
      return !text.trim() && commands ? [commands.tip(), ...items] : items;
    }
    return res.merge ? [...res.entries, ...paletteSearchItems(text)] : res.entries;
  };
  if (result && typeof result.then === "function") {
    drawPalette([{ kind: "Info", group: "Working", title: "Working\u2026", sub: "", page: "" }]);
    result
      .then((res) => {
        if (token === palette.token && input.value === text) drawPalette(compose(res));
      })
      .catch((err) => {
        if (token === palette.token) drawPalette([{ kind: "Info", group: "Error", title: String(err), sub: "", page: "" }]);
      });
    return;
  }
  drawPalette(compose(result));
}

function drawPalette(items) {
  const list = $("paletteList");
  if (!list) return;
  palette.shown = items;
  palette.index = Math.max(0, items.findIndex((entry) => entry.kind !== "Info"));
  if (palette.index < 0 || !items.length) palette.index = 0;
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "palette-empty";
    empty.textContent = "No matches. Type help to see commands.";
    list.append(empty);
    return;
  }
  let group = "";
  items.forEach((entry, index) => {
    const heading = entry.group || "Results";
    if (heading !== group) {
      group = heading;
      const label = document.createElement("div");
      label.className = "palette-group";
      label.textContent = heading;
      list.append(label);
    }
    const item = document.createElement("button");
    item.type = "button";
    item.className = `palette-item is-${String(entry.kind || "").toLowerCase()}`;
    item.setAttribute("role", "option");
    item.dataset.index = String(index);
    const icon = document.createElement("span");
    icon.className = "palette-icon";
    if (entry.icon) icon.append(entry.icon.cloneNode(true));
    else icon.textContent = "\u26a1";
    const textBox = document.createElement("span");
    textBox.className = "palette-text";
    const strong = document.createElement("strong");
    strong.textContent = entry.title;
    textBox.append(strong);
    const detail = entry.kind === "Setting" ? [entry.page, entry.sub].filter(Boolean).join(" \u00b7 ") : entry.sub;
    if (detail) {
      const small = document.createElement("small");
      small.textContent = detail;
      textBox.append(small);
    }
    const kind = document.createElement("span");
    kind.className = "palette-kind";
    kind.textContent = entry.fill != null && !entry.run ? "Tab" : entry.kind;
    item.append(icon, textBox, kind);
    list.append(item);
  });
  paintPaletteActive();
}

function paintPaletteActive() {
  document.querySelectorAll("#paletteList .palette-item").forEach((item) => {
    const on = Number(item.dataset.index) === palette.index;
    item.classList.toggle("is-active", on);
    item.setAttribute("aria-selected", String(on));
    if (on) item.scrollIntoView({ block: "nearest" });
  });
}

function openPalette() {
  const overlay = $("cmdPalette");
  const input = $("paletteInput");
  if (!overlay || !input || !overlay.hidden) return;
  palette.entries = paletteEntries();
  input.value = "";
  overlay.hidden = false;
  renderPalette();
  requestAnimationFrame(() => {
    overlay.classList.add("open");
    input.focus();
  });
}

function closePalette() {
  const overlay = $("cmdPalette");
  if (!overlay || overlay.hidden) return;
  overlay.classList.remove("open");
  overlay.hidden = true;
}

function spotlightRow(row) {
  requestAnimationFrame(() => {
    row.scrollIntoView({ block: "center", behavior: "smooth" });
    row.classList.remove("is-spotlight");
    void row.offsetWidth;
    row.classList.add("is-spotlight");
    setTimeout(() => row.classList.remove("is-spotlight"), 1900);
  });
}

function fillPalette(text) {
  const input = $("paletteInput");
  if (!input) return;
  input.value = text;
  input.focus();
  input.setSelectionRange(text.length, text.length);
  renderPalette();
}

function runPaletteEntry(entry) {
  if (!entry) return;
  if (entry.fill != null && !entry.run) {
    fillPalette(entry.fill);
    return;
  }
  if (entry.run) {
    closePalette();
    Promise.resolve()
      .then(() => entry.run())
      .catch((err) => toast(String(err && err.message ? err.message : err), "error"));
    return;
  }
  if (entry.kind === "Info") return;
  closePalette();
  if (entry.button) {
    entry.button.click();
    return;
  }
  showTab(entry.tab);
  if (entry.row) spotlightRow(entry.row);
}

function bindPalette() {
  const overlay = $("cmdPalette");
  const input = $("paletteInput");
  const list = $("paletteList");
  if (!overlay || !input || !list) return;
  document.querySelectorAll("[data-palette-open]").forEach((btn) => btn.addEventListener("click", openPalette));
  input.addEventListener("input", renderPalette);
  input.addEventListener("keydown", (event) => {
    const count = palette.shown.length;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!count) return;
      palette.index = (palette.index + (event.key === "ArrowDown" ? 1 : count - 1)) % count;
      paintPaletteActive();
    } else if (event.key === "Enter") {
      event.preventDefault();
      runPaletteEntry(palette.shown[palette.index]);
    } else if (event.key === "Tab") {
      // Complete the highlighted command, like a shell.
      event.preventDefault();
      const entry = palette.shown[palette.index];
      const completion = entry && (entry.complete != null ? entry.complete : entry.fill);
      if (completion != null) fillPalette(completion.endsWith(" ") ? completion : `${completion} `);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closePalette();
    }
  });
  list.addEventListener("mousemove", (event) => {
    const item = event.target.closest(".palette-item");
    if (!item || Number(item.dataset.index) === palette.index) return;
    palette.index = Number(item.dataset.index);
    paintPaletteActive();
  });
  list.addEventListener("click", (event) => {
    const item = event.target.closest(".palette-item");
    if (item) runPaletteEntry(palette.shown[Number(item.dataset.index)]);
  });
  overlay.addEventListener("mousedown", (event) => {
    if (event.target === overlay) closePalette();
  });
  document.addEventListener("keydown", (event) => {
    if (listenId || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
    if (event.code === "KeyK") {
      event.preventDefault();
      if (overlay.hidden) openPalette();
      else closePalette();
    } else if (event.code === "KeyS") {
      event.preventDefault();
      $("configSave")?.click();
    }
  });
}

function bindNavTips() {
  const tip = $("navTip");
  if (!tip) return;
  const hide = () => {
    tip.classList.remove("show");
    tip.hidden = true;
  };
  document.querySelectorAll(".sidebar .nav-item[data-tab]").forEach((item) => {
    const label = item.querySelector("span:not(.nav-dot)");
    item.addEventListener("mouseenter", () => {
      if (!label || getComputedStyle(label).display !== "none") return;
      const rect = item.getBoundingClientRect();
      tip.textContent = label.textContent.trim();
      tip.style.top = `${Math.round(rect.top + rect.height / 2)}px`;
      tip.style.left = `${Math.round(rect.right + 12)}px`;
      tip.hidden = false;
      requestAnimationFrame(() => tip.classList.add("show"));
    });
    item.addEventListener("mouseleave", hide);
    item.addEventListener("click", hide);
  });
}

function commitRange(input, raw) {
  const min = Number(input.min);
  const max = Number(input.max);
  const step = Number(input.step) || 1;
  let value = Number(raw);
  if (!Number.isFinite(value)) return;
  value = Math.min(max, Math.max(min, value));
  value = min + Math.round((value - min) / step) * step;
  const digits = (String(input.step).split(".")[1] || "").length;
  const next = value.toFixed(digits);
  if (next === input.value) return;
  input.value = next;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
}

function markRangeChanged(input) {
  const row = input.closest(".setting-row.control");
  if (!row || row.classList.contains("dual-range")) return;
  const initial = input.getAttribute("value");
  if (initial != null) row.classList.toggle("is-changed", Number(input.value) !== Number(initial));
}

function editRangeValue(input, output) {
  if (input.disabled || output.hidden) return;
  const editor = document.createElement("input");
  editor.type = "number";
  editor.className = "value-edit";
  editor.min = input.min;
  editor.max = input.max;
  editor.step = input.step || "1";
  editor.value = input.value;
  output.hidden = true;
  output.after(editor);
  editor.focus();
  editor.select();
  let done = false;
  const finish = (commit) => {
    if (done) return;
    done = true;
    if (commit) commitRange(input, editor.value);
    editor.remove();
    output.hidden = false;
  };
  editor.addEventListener("keydown", (event) => {
    if (event.key === "Enter") finish(true);
    else if (event.key === "Escape") finish(false);
  });
  editor.addEventListener("blur", () => finish(true));
}

function unwrapLabel(label) {
  if (label.tagName !== "LABEL") return label;
  const div = document.createElement("div");
  for (const attr of label.attributes) div.setAttribute(attr.name, attr.value);
  div.append(...label.childNodes);
  label.replaceWith(div);
  return div;
}

function repaintDualRanges() {
  document.querySelectorAll(".dual-range-track").forEach((track) => {
    const fill = track.querySelector(".dual-range-fill");
    const lo = track.querySelector("input.range-min");
    const hi = track.querySelector("input.range-max");
    if (!fill || !lo || !hi) return;
    const sliderMin = Number(lo.min);
    const span = Number(lo.max) - sliderMin || 1;
    const a = Math.min(Number(lo.value), Number(hi.value));
    const b = Math.max(Number(lo.value), Number(hi.value));
    fill.style.left = `${((a - sliderMin) / span) * 100}%`;
    fill.style.width = `${Math.max(0, ((b - a) / span) * 100)}%`;
  });
}

function bindDualRange(row) {
  const lo = row.querySelector("input.range-min");
  const hi = row.querySelector("input.range-max");
  if (!lo || !hi) return;
  [[lo, row.querySelector(`output[for="${lo.id}"]`)], [hi, row.querySelector(`output[for="${hi.id}"]`)]].forEach(
    ([input, output]) => {
      if (!output) return;
      output.classList.add("is-editable");
      output.title = "Click to type a value";
      output.addEventListener("click", (event) => {
        event.preventDefault();
        editRangeValue(input, output);
      });
    }
  );
  lo.addEventListener("input", () => {
    if (Number(lo.value) > Number(hi.value)) {
      hi.value = lo.value;
      hi.dispatchEvent(new Event("input", { bubbles: true }));
    }
    repaintDualRanges();
  });
  hi.addEventListener("input", () => {
    if (Number(hi.value) < Number(lo.value)) {
      lo.value = hi.value;
      lo.dispatchEvent(new Event("input", { bubbles: true }));
    }
    repaintDualRanges();
  });
  const title = row.querySelector("span strong");
  const loDefault = lo.getAttribute("value");
  const hiDefault = hi.getAttribute("value");
  if (title && (loDefault != null || hiDefault != null)) {
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "value-reset";
    reset.title = "Reset to default";
    reset.setAttribute("aria-label", "Reset to default");
    reset.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.4-5.7" /><path d="M4 4v4.5h4.5" /></svg>';
    reset.addEventListener("click", (event) => {
      event.preventDefault();
      if (loDefault != null) commitRange(lo, loDefault);
      if (hiDefault != null) commitRange(hi, hiDefault);
      toast(`${title.firstChild.textContent.trim()} reset`, "info");
    });
    title.append(reset);
    const syncChanged = () => {
      row.classList.toggle(
        "is-changed",
        (loDefault != null && Number(lo.value) !== Number(loDefault)) ||
          (hiDefault != null && Number(hi.value) !== Number(hiDefault))
      );
    };
    lo.addEventListener("input", syncChanged);
    hi.addEventListener("input", syncChanged);
    syncChanged();
  }
  repaintDualRanges();
}

function bindRangeValues() {
  document.querySelectorAll(".setting-row.control").forEach((label) => {
    if (label.classList.contains("dual-range")) return;
    const row = unwrapLabel(label);
    const input = row.querySelector('input[type="range"]');
    const output = row.querySelector("output");
    if (!input || !output) return;
    output.classList.add("is-editable");
    output.title = "Click to type a value";
    output.addEventListener("click", (event) => {
      event.preventDefault();
      editRangeValue(input, output);
    });
    const initial = input.getAttribute("value");
    const title = row.querySelector("span strong");
    if (initial == null || !title) return;
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "value-reset";
    reset.title = "Reset to default";
    reset.setAttribute("aria-label", "Reset to default");
    reset.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.4-5.7" /><path d="M4 4v4.5h4.5" /></svg>';
    reset.addEventListener("click", (event) => {
      event.preventDefault();
      commitRange(input, initial);
      toast(`${title.firstChild.textContent.trim()} reset`, "info");
    });
    title.append(reset);
    markRangeChanged(input);
  });
}

function bindControls() {
  bindTabs();
  bindPalette();
  bindNavTips();
  bindRangeValues();
  document.querySelectorAll(".dual-range").forEach(bindDualRange);
  bindDashboard();
  bindDirectorMode();
  bindModuleDetails();
  bindKeyListen();
  bindModuleWarn();
  bindCurvePicker();
  for (const id of RANGE_IDS) {
    const input = $(id);
    if (!input) continue;
    const eventName = id === "fps" || id === "output_idx" || id === "conf" ? "change" : "input";
    input.addEventListener(eventName, (event) => {
      const value = parseValue(id, event.target.value);
      setOutput(event.target, id, value);
      if (!applyingRemote) queueUpdate(id, value);
    });
    if (eventName === "change") {
      input.addEventListener("input", (event) => {
        setOutput(event.target, id, parseValue(id, event.target.value));
      });
    }
  }
  for (const id of TEXT_IDS) {
    const input = $(id);
    if (!input) continue;
    input.addEventListener("change", (event) => {
      if (!applyingRemote) queueUpdate(id, event.target.value);
    });
  }
  for (const id of NUMBER_IDS) {
    const input = $(id);
    if (!input) continue;
    const push = (event) => {
      const value = Number(event.target.value);
      if (!Number.isFinite(value) || applyingRemote) return;
      queueUpdate(id, value);
    };
    input.addEventListener("change", push);
  }
  for (const id of TOGGLE_IDS) {
    const input = $(id);
    if (!input) continue;
    input.addEventListener("change", (event) => {
      syncModules();
      if (!applyingRemote) queueUpdate(id, event.target.checked);
    });
  }
  for (const id of SELECT_IDS) {
    const input = $(id);
    if (!input) continue;
    input.addEventListener("change", (event) => {
      lastSettings[id] = event.target.value;
      if (!applyingRemote) queueUpdate(id, event.target.value);
    });
  }
  document.querySelectorAll("#inputMethodSeg [data-input-method]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const method = btn.dataset.inputMethod === "rp2040" ? "rp2040" : "windows";
      if (String(lastSettings.input_method || "windows") === method) return;
      lastSettings.input_method = method;
      paintInputMethod(method);
      syncModules();
      queueUpdate("input_method", method);
    });
  });
  bindTriggerBones();
  if (window.ArgusTheme && typeof window.ArgusTheme.paintAllRangeFills === "function") {
    window.ArgusTheme.paintAllRangeFills();
  }
  const gameSelected = $("game-selected");
  const titlebarGame = $("titlebar-game");
  const gamePicker = $("gamePicker");
  const gamePickerClose = $("gamePickerClose");
  if (gameSelected) gameSelected.addEventListener("click", openGamePicker);
  if (titlebarGame) titlebarGame.addEventListener("click", openGamePicker);
  const configSelect = $("configSelect");
  const configMenu = $("configMenu");
  if (configSelect) {
    configSelect.addEventListener("click", (event) => {
      event.stopPropagation();
      if ($("configDrop")?.classList.contains("open")) closeConfigDrop();
      else openConfigDrop();
    });
  }
  if (configMenu) {
    configMenu.addEventListener("click", async (event) => {
      const item = event.target.closest("[data-config]");
      if (!item) return;
      const name = item.dataset.config;
      closeConfigDrop();
      if (!name || switchingConfig) return;
      switchingConfig = true;
      clearTimeout(debounceTimer);
      pending = {};
      try {
        const result = await window.pywebview.api.select_config(name);
        paintConfigs(result);
        if (result.settings) renderSettings(result.settings);
        setError(result.ok ? "" : result.error || "Could not load config");
        if (result.ok) toast(`Loaded ${activeConfig || name}`, "success");
      } catch (err) {
        setError(String(err));
      } finally {
        switchingConfig = false;
      }
    });
  }
  document.addEventListener("click", (event) => {
    if (event.target.closest("#titlebar-configs, #configMenu")) return;
    closeConfigDrop();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeConfigDrop();
  });
  const configNew = $("configNew");
  if (configNew) configNew.addEventListener("click", openConfigNamePicker);
  const configDelete = $("configDelete");
  if (configDelete) configDelete.addEventListener("click", openConfigDeletePicker);
  const configSave = $("configSave");
  if (configSave) {
    configSave.addEventListener("click", async () => {
      try {
        const result = await window.pywebview.api.save_config();
        paintConfigs(result);
        if (!result.ok) {
          setError(result.error || "Could not save config");
          return;
        }
        setError("");
        showConfigSaved(result.active_config || activeConfig);
      } catch (err) {
        setError(String(err));
      }
    });
  }
  const configDeletePicker = $("configDeletePicker");
  const configDeleteCancel = $("configDeleteCancel");
  const configDeleteConfirm = $("configDeleteConfirm");
  if (configDeleteCancel) configDeleteCancel.addEventListener("click", closeConfigDeletePicker);
  if (configDeletePicker) {
    configDeletePicker.addEventListener("click", (event) => {
      if (event.target === configDeletePicker) closeConfigDeletePicker();
    });
  }
  if (configDeleteConfirm) {
    configDeleteConfirm.addEventListener("click", async () => {
      try {
        const result = await window.pywebview.api.delete_config(activeConfig);
        if (!result.ok) {
          setError(result.error || "Could not delete config");
          return;
        }
        setError("");
        paintConfigs(result);
        if (result.settings) renderSettings(result.settings);
        closeConfigDeletePicker();
      } catch (err) {
        setError(String(err));
      }
    });
  }
  const configPicker = $("configNamePicker");
  const configNameCancel = $("configNameCancel");
  const configNameCreate = $("configNameCreate");
  const configNameInput = $("configNameInput");
  if (configNameCancel) configNameCancel.addEventListener("click", closeConfigNamePicker);
  if (configPicker) {
    configPicker.addEventListener("click", (event) => {
      if (event.target === configPicker) closeConfigNamePicker();
    });
  }
  const createConfig = async () => {
    try {
      const result = await window.pywebview.api.create_config((configNameInput && configNameInput.value) || "");
      if (!result.ok) {
        setError(result.error || "Could not create config");
        return;
      }
      setError("");
      paintConfigs(result);
      closeConfigNamePicker();
    } catch (err) {
      setError(String(err));
    }
  };
  if (configNameCreate) configNameCreate.addEventListener("click", createConfig);
  if (configNameInput) {
    configNameInput.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        createConfig();
      } else if (event.key === "Escape") {
        closeConfigNamePicker();
      }
    });
  }
  if (gamePickerClose) gamePickerClose.addEventListener("click", closeGamePicker);
  if (gamePicker) {
    gamePicker.addEventListener("click", (event) => {
      if (event.target === gamePicker) closeGamePicker();
    });
  }
  const gameGrid = $("gamePickerGrid");
  if (gameGrid) {
    gameGrid.addEventListener("click", async (event) => {
      const btn = event.target.closest("[data-game]");
      if (!btn) return;
      const game = btn.dataset.game;
      if (!game) return;
      try {
        const result = await window.pywebview.api.select_game(game);
        if (!result.ok) {
          setError(result.error || "Could not load game");
          return;
        }
        setError("");
        if (result.settings) renderSettings(result.settings);
        closeGamePicker();
      } catch (err) {
        setError(String(err));
      }
    });
  }
  const loggerClear = $("loggerClear");
  if (loggerClear) {
    loggerClear.addEventListener("click", () => {
      const view = $("loggerView");
      if (view) view.replaceChildren();
      loggerLineCount = 0;
      loggerRows = [];
    });
  }
  const loggerColors = $("loggerColors");
  if (loggerColors) loggerColors.addEventListener("click", openLoggerColorPicker);
  const loggerExport = $("loggerExport");
  if (loggerExport) loggerExport.addEventListener("click", exportLoggerHtml);
  const loggerColorPicker = $("loggerColorPicker");
  const loggerColorClose = $("loggerColorClose");
  const loggerColorGrid = $("loggerColorGrid");
  if (loggerColorClose) loggerColorClose.addEventListener("click", closeLoggerColorPicker);
  if (loggerColorPicker) {
    loggerColorPicker.addEventListener("click", (event) => {
      if (event.target === loggerColorPicker) closeLoggerColorPicker();
    });
  }
  if (loggerColorGrid) {
    loggerColorGrid.addEventListener("input", (event) => {
      const input = event.target.closest("[data-log-tag]");
      if (!input) return;
      logColors[input.dataset.logTag] = input.value;
      restyleLoggerLines();
    });
    loggerColorGrid.addEventListener("change", persistLogColors);
  }
  $("start-capture").addEventListener("click", async () => {
    try {
      const result = await window.pywebview.api.start_capture();
      if (!result.ok) {
        setError(result.error || "Could not start capture");
        return;
      }
      setError("");
    } catch (err) {
      setError(String(err));
    }
  });
  $("stop-capture").addEventListener("click", async () => {
    try {
      await window.pywebview.api.stop_capture();
    } catch (err) {
      setError(String(err));
    }
  });
}

async function refreshState() {
  if (document.hidden) return;
  try {
    if (!loaded && !Object.keys(pending).length) {
      const state = await window.pywebview.api.get_state();
      if (state.games) applyGamesCatalog(state.games);
      paintConfigs(state);
      renderSettings(state.settings);
      renderStatus(state.status, state.settings);
      loaded = true;
      return;
    }
    const state = await window.pywebview.api.get_status();
    renderStatus(state.status, {
      dry_run: state.dry_run,
      engine: state.engine,
    });
    if (loggerTabActive()) await refreshLogs();
  } catch (err) {
    setError(String(err));
  }
}

async function init() {
  if (window.ArgusTheme) await window.ArgusTheme.init();
  bindControls();
  await loadLogColors();
  await refreshState();
  setInterval(refreshState, 500);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshState();
  });
}

window.addEventListener("pywebviewready", init);
