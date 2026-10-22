/* Argus appearance engine, ported from OmegaDash. Isolated IIFE so it does not clash with app.js. */
(function () {
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

function paintRangeFill(input) {
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
  if (rangeFillObs) rangeFillObs.observe(input);
}

let rangeFillObs = null;
function paintAllRangeFills() {
  if (!rangeFillObs && typeof ResizeObserver === "function") {
    rangeFillObs = new ResizeObserver((entries) => {
      for (const entry of entries) paintRangeFill(entry.target);
    });
  }
  document.querySelectorAll('input[type="range"]').forEach(paintRangeFill);
}
function logSettingsIsOpen() { return false; }
function parseScriptColor() { return null; }
function normalizeLogColor(value) {
  const hex = String(value || "").trim();
  if (/^#[0-9a-fA-F]{6}$/.test(hex)) return hex.toLowerCase();
  if (/^#[0-9a-fA-F]{3}$/.test(hex)) {
    const r = hex[1];
    const g = hex[2];
    const b = hex[3];
    return `#${r}${r}${g}${g}${b}${b}`.toLowerCase();
  }
  return "";
}
const THEMES = {
  omega: { accent: "#79f2b0", accent2: "#3ddc97", gradient: ["#79f2b0", "#1a3d32"], blue: "#69a7ff", orange: "#ff9d66", red: "#ff6b78" },
  neon: { accent: "#ff4fd8", accent2: "#8b5cff", gradient: ["#ff4fd8", "#7aa2ff"], blue: "#34d9ff", orange: "#ffdf5d", red: "#ff4778" },
  aurora: { accent: "#8b9dff", accent2: "#ba70ff", gradient: ["#8b9dff", "#ba70ff"], blue: "#51e5ff", orange: "#ff9fe5", red: "#ff6f91" },
  ember: { accent: "#ffb347", accent2: "#ff5f6d", gradient: ["#ffb347", "#ff5f6d"], blue: "#7ec8ff", orange: "#ff7547", red: "#ff4d62" },
  violet: { accent: "#3800BA", accent2: "#4a1ad4", gradient: ["#3800BA", "#1c0068"], blue: "#7a8cff", orange: "#ffb07c", red: "#ff6b91" },
  prism: { accent: "#a855f7", accent2: "#7dd3fc", blue: "#67e8f9", orange: "#c4b5fd", red: "#f472b6", anim: "prism" },
  spectrum: { accent: "#ff4d4d", accent2: "#ffe14d", blue: "#3b82f6", orange: "#ff9f1c", red: "#ff3b3b", anim: "spectrum" },
  magma: { accent: "#ff3b30", accent2: "#ffd166", blue: "#ff8a4c", orange: "#ff7a18", red: "#ff2d2d", anim: "magma" },
  tide: { accent: "#22d3ee", accent2: "#2dd4bf", blue: "#38bdf8", orange: "#7dd3fc", red: "#fb7185", anim: "tide" },
  voltage: { accent: "#bef264", accent2: "#22d3ee", blue: "#67e8f9", orange: "#facc15", red: "#fb7185", anim: "voltage" }
};

function defaultCustomTheme(kind) {
  if (kind === "custom-anim") {
    return { name: "Custom", accent: "#e879f9", accent2: "#22d3ee", blue: "#67e8f9", orange: "#facc15", red: "#fb7185", animMode: "rgb" };
  }
  return { name: "Custom", accent: "#5eead4", accent2: "#14b8a6", blue: "#69a7ff", orange: "#ff9d66", red: "#ff6b78", gradientPct: 50, gradientFade: 80, gradientAngle: 135 };
}

const COLOR_SLOT_COUNT = 5;

function normalizeColorSlots(value) {
  const src = Array.isArray(value) ? value : [];
  return Array.from({ length: COLOR_SLOT_COUNT }, (_, i) => {
    const parsed = parseScriptColor(src[i]);
    return parsed ? parsed.hex8 : "";
  });
}

function clampInt(n, min, max, fallback) {
  const v = Math.round(Number(n));
  if (!Number.isFinite(v)) return fallback;
  return Math.min(max, Math.max(min, v));
}

function gradientBlendStops(pos, fade) {
  const p = clampInt(pos, 0, 100, 50);
  const f = clampInt(fade, 0, 100, 80);
  if (f <= 0) return { start: p, end: p };
  const half = f / 2;
  let start = p - half;
  let end = p + half;
  if (start < 0) {
    end = Math.min(100, end - start);
    start = 0;
  }
  if (end > 100) {
    start = Math.max(0, start - (end - 100));
    end = 100;
  }
  if (end < start) end = start;
  return { start, end };
}

function normalizeCustomTheme(value, kind) {
  const base = defaultCustomTheme(kind);
  const raw = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const name = String(raw.name || base.name).trim().slice(0, 18) || base.name;
  const next = {
    name,
    accent: normalizeLogColor(raw.accent) || base.accent,
    accent2: normalizeLogColor(raw.accent2) || base.accent2,
    blue: normalizeLogColor(raw.blue) || base.blue,
    orange: normalizeLogColor(raw.orange) || base.orange,
    red: normalizeLogColor(raw.red) || base.red
  };
  if (kind === "custom-anim") {
    next.animMode = raw.animMode === "fade" ? "fade" : "rgb";
    return next;
  }
  next.gradientPct = clampInt(raw.gradientPct, 0, 100, base.gradientPct);
  next.gradientFade = clampInt(raw.gradientFade, 0, 100, base.gradientFade);
  next.gradientAngle = clampInt(raw.gradientAngle, 0, 360, base.gradientAngle);
  return next;
}

function customThemeSettings(kind) {
  if (kind === "custom-anim") return settings.customAnim || defaultCustomTheme(kind);
  return settings.customStatic || defaultCustomTheme(kind);
}

function isKnownTheme(name) {
  return Boolean(THEMES[name]) || name === "custom" || name === "custom-anim";
}

function themeDef(name = settings?.theme) {
  if (name === "custom") {
    const c = customThemeSettings("custom");
    return { accent: c.accent, accent2: c.accent2, gradient: [c.accent, c.accent2], gradientPct: c.gradientPct, gradientFade: c.gradientFade, gradientAngle: c.gradientAngle, blue: c.blue, orange: c.orange, red: c.red };
  }
  if (name === "custom-anim") {
    const c = customThemeSettings("custom-anim");
    return { accent: c.accent, accent2: c.accent2, blue: c.blue, orange: c.orange, red: c.red, anim: "custom", animMode: c.animMode };
  }
  return THEMES[name] || THEMES.omega;
}

function themeAccent() {
  return themeDef().accent;
}

function isAnimatedTheme(name = settings?.theme) {
  return Boolean(themeDef(name)?.anim);
}

const FROST_SCALE = 2;
const FROST_STRENGTH_DEFAULT = 8;

const defaultSettings = {
  theme: "omega", compactNav: false, altNav: false, frostNav: true, frostLogs: false, frostStrength: FROST_STRENGTH_DEFAULT, frostScale: FROST_SCALE, autoHideNav: false, reduceMotion: false, themeSpeed: 5, simulateLoot: false,
  staticGradients: false, bgFx: "off", bgFxOpts: {},
  hiddenScripts: [], hiddenScriptFields: {}, scriptBaselines: {}, scriptAutoSave: false, cloudSortPopular: false, cloudHideLibraries: true, omegaRestartOnStall: true, fullscreenMaximize: true,
  colorSlots: ["", "", "", "", ""],
  logScriptColors: {}, logScriptHighlight: {}, logScriptHidden: {}, logLineHighlights: {},
  customStatic: defaultCustomTheme("custom"),
  customAnim: defaultCustomTheme("custom-anim")
};

let settings = { ...defaultSettings };

const BG_FX_MODES = ["off", "particles", "neural", "mesh", "hive", "stars"];
const BG_FX_DEFAULT_OPTS = {
  particles: { count: 6, speed: 5, attract: 5, size: 5, links: 3, click: 1, clickAmt: 5 },
  neural: { count: 6, speed: 3, link: 7, mouse: 7, glow: 6, click: 1, clickAmt: 5 },
  mesh: { density: 5, pull: 6, radius: 6, click: 1, clickAmt: 10, clickScale: 2 },
  hive: { density: 5, radius: 6, glow: 6, click: 1, clickAmt: 5 },
  stars: { density: 6, size: 5, sizeJitter: 5, twinkle: 4, twinkleSpeed: 5, parallax: 0, showers: 5, showerJitter: 0, meteorSize: 5, meteorJitter: 5, meteorSpeed: 5, meteorSpeedScale: 2, burnout: 4, burnoutJitter: 4 }
};
const BG_FX_FIELDS = {
  particles: [
    { key: "count", label: "Density", min: 1, max: 10 },
    { key: "speed", label: "Drift", min: 1, max: 10 },
    { key: "attract", label: "Mouse pull", min: 0, max: 10 },
    { key: "size", label: "Size", min: 1, max: 10 },
    { key: "links", label: "Links", min: 0, max: 10 },
    { key: "click", label: "Click effects", type: "toggle" },
    { key: "clickAmt", label: "Click amount", min: 1, max: 10 }
  ],
  neural: [
    { key: "count", label: "Density", min: 1, max: 10 },
    { key: "link", label: "Link distance", min: 1, max: 10 },
    { key: "speed", label: "Drift", min: 1, max: 10 },
    { key: "mouse", label: "Cursor links", min: 0, max: 10 },
    { key: "glow", label: "Glow", min: 0, max: 10 },
    { key: "click", label: "Click effects", type: "toggle" },
    { key: "clickAmt", label: "Click amount", min: 1, max: 10 }
  ],
  mesh: [
    { key: "density", label: "Grid density", min: 1, max: 10 },
    { key: "pull", label: "Warp", min: 0, max: 10 },
    { key: "radius", label: "Area of effect", min: 1, max: 10 },
    { key: "click", label: "Click effects", type: "toggle" },
    { key: "clickAmt", label: "Click amount", min: 1, max: 10 }
  ],
  hive: [
    { key: "density", label: "Cell size", min: 1, max: 10 },
    { key: "radius", label: "Area of effect", min: 1, max: 10 },
    { key: "glow", label: "Glow", min: 0, max: 10 },
    { key: "click", label: "Click effects", type: "toggle" },
    { key: "clickAmt", label: "Click amount", min: 1, max: 10 }
  ],
  stars: [
    { type: "section", label: "Stars" },
    { key: "density", label: "Count", min: 1, max: 10 },
    { key: "size", label: "Size", min: 1, max: 10 },
    { key: "sizeJitter", label: "Size randomness", min: 0, max: 10 },
    { key: "twinkle", label: "Twinkle count", min: 0, max: 10 },
    { key: "twinkleSpeed", label: "Twinkle speed", min: 1, max: 10 },
    { key: "parallax", label: "Mouse parallax", type: "toggle" },
    { type: "section", label: "Shooting stars" },
    { key: "showers", label: "Frequency", min: 0, max: 10 },
    { key: "showerJitter", label: "Random frequency", min: 0, max: 10 },
    { key: "meteorSize", label: "Size", min: 1, max: 10 },
    { key: "meteorJitter", label: "Size randomness", min: 0, max: 10 },
    { key: "meteorSpeed", label: "Speed", min: 1, max: 10 },
    { key: "burnout", label: "Burnout", min: 0, max: 10 },
    { key: "burnoutJitter", label: "Burnout randomness", min: 0, max: 10 }
  ]
};
const BG_FX_HINTS = {
  particles: "Dots drift and pull toward the cursor. Click to ease them apart.",
  neural: "Nearby nodes link into a web. Click to ease the cluster apart.",
  mesh: "A grid that warps and lights up around the cursor.",
  hive: "A honeycomb that lights up around the cursor.",
  stars: "A night sky with shooting stars from above."
};
const BG_FX_TITLES = {
  particles: "Edit particles",
  neural: "Edit neural",
  mesh: "Edit mesh",
  hive: "Edit hive",
  stars: "Edit stars"
};

function normalizeBgFx(value) {
  const name = String(value || "off").toLowerCase();
  if (name === "orbits") return "hive";
  if (name === "ripples") return "stars";
  return BG_FX_MODES.includes(name) ? name : "off";
}

function clampFxOpt(value, min, max, fallback) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

function normalizeBgFxOpts(value) {
  const src = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const out = {};
  for (const [mode, fields] of Object.entries(BG_FX_FIELDS)) {
    const raw = src[mode] && typeof src[mode] === "object" ? src[mode] : {};
    const defaults = BG_FX_DEFAULT_OPTS[mode];
    const next = {};
    for (const field of fields) {
      if (!field.key) continue;
      next[field.key] = clampFxOpt(raw[field.key], field.min ?? 0, field.max ?? 1, defaults[field.key]);
    }
    if (mode === "mesh") {
      if (Number(raw.clickScale) !== 2) {
        if (Object.prototype.hasOwnProperty.call(raw, "clickAmt")) {
          next.clickAmt = Math.min(10, Math.max(1, Math.round(next.clickAmt * 10 / 3)));
        } else {
          next.clickAmt = defaults.clickAmt;
        }
      }
      next.clickScale = 2;
    }
    if (mode === "stars") {
      next.meteorSpeedScale = 2;
    }
    out[mode] = next;
  }
  return out;
}

function bgFxOptsFor(mode) {
  return { ...BG_FX_DEFAULT_OPTS[mode], ...(settings.bgFxOpts?.[mode] || {}) };
}

function saveSettings() {
  localStorage.setItem("argusUi", JSON.stringify(settings));
  const api = window.pywebview?.api;
  if (api?.save_ui_settings) {
    Promise.resolve(api.save_ui_settings(JSON.stringify(settings))).catch(() => {});
  }
}

function whenNativeReady() {
  return new Promise(resolve => {
    if (window.pywebview?.api) return resolve();
    window.addEventListener("pywebviewready", resolve, { once: true });
    setTimeout(resolve, 2000);
  });
}

let themeRaf = 0;
const PRISM_A = [168, 85, 247];
const PRISM_B = [125, 211, 252];
const MAGMA_STOPS = [[255, 45, 48], [255, 122, 24], [255, 209, 102]];
const TIDE_STOPS = [[3, 105, 161], [34, 211, 238], [45, 212, 191]];
const VOLTAGE_STOPS = [[190, 242, 100], [250, 204, 21], [34, 211, 238]];

function hexFromRgb(rgb) {
  return `#${rgb.map(n => n.toString(16).padStart(2, "0")).join("")}`;
}

function mixRgb(a, b, t) {
  return a.map((value, i) => Math.round(value + (b[i] - value) * t));
}

function scaleRgb(rgb, t) {
  const k = Math.min(1, Math.max(0, t));
  return rgb.map(c => Math.round(c * k));
}

function pulseColors(a, b, t) {
  const w = Math.abs(Math.cos(Math.min(1, Math.max(0, t)) * Math.PI));
  return scaleRgb(t < 0.5 ? a : b, w);
}

function mixHsl(a, b, t) {
  const ha = rgbToHsl(a);
  const hb = rgbToHsl(b);
  const ht = Math.min(1, Math.max(0, t));
  const dh = ((hb[0] - ha[0] + 540) % 360) - 180;
  return hslToRgb(
    (ha[0] + dh * ht + 360) % 360,
    ha[1] + (hb[1] - ha[1]) * ht,
    ha[2] + (hb[2] - ha[2]) * ht
  );
}

function pingPong01(elapsed, period = 2800) {
  const x = ((elapsed / Math.max(400, period)) % 2 + 2) % 2;
  return x <= 1 ? x : 2 - x;
}

function rgbLuma(rgb) {
  const r = Number(rgb?.[0]) / 255;
  const g = Number(rgb?.[1]) / 255;
  const b = Number(rgb?.[2]) / 255;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function themeOnAccent(name, rgb) {
  if (name === "neon" || name === "aurora") return "#f4f1ff";
  if (themeDef(name)?.anim) return "#101611";
  return rgbLuma(rgb) < 0.28 ? "#f4f1ff" : "#101611";
}

function hslToRgb(h, s, l) {
  s /= 100;
  l /= 100;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const hp = (((h % 360) + 360) % 360) / 60;
  const x = c * (1 - Math.abs(hp % 2 - 1));
  let r = 0, g = 0, b = 0;
  if (hp < 1) [r, g, b] = [c, x, 0];
  else if (hp < 2) [r, g, b] = [x, c, 0];
  else if (hp < 3) [r, g, b] = [0, c, x];
  else if (hp < 4) [r, g, b] = [0, x, c];
  else if (hp < 5) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  const m = l - c / 2;
  return [r, g, b].map(v => Math.round((v + m) * 255));
}

function rgbToHsl(rgb) {
  const r = Number(rgb[0]) / 255;
  const g = Number(rgb[1]) / 255;
  const b = Number(rgb[2]) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l * 100];
  const d = max - min;
  const s = (l > 0.5 ? d / (2 - max - min) : d / (max + min)) * 100;
  let h = 0;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l * 100];
}

function cycleStops(stops, t) {
  const x = ((t % 1) + 1) % 1 * stops.length;
  const i = Math.floor(x) % stops.length;
  const f = x - Math.floor(x);
  return mixRgb(stops[i], stops[(i + 1) % stops.length], f);
}

function hexToRgbArr(hex) {
  const m = String(hex || "").match(/\w\w/g);
  if (!m || m.length < 3) return [121, 242, 176];
  return m.slice(0, 3).map(v => parseInt(v, 16));
}

function packColors(rgb, rgb2) {
  return { rgb, rgb2, hex: hexFromRgb(rgb), hex2: hexFromRgb(rgb2) };
}

function themeSpeedScale() {
  return (Math.min(10, Math.max(1, Number(settings.themeSpeed) || 5))) / 5;
}

function themeColorsAt(name, elapsed) {
  const theme = themeDef(name);
  const anim = theme.anim;
  if (anim === "spectrum") {
    const hue = (elapsed / 8000) * 360;
    return packColors(hslToRgb(hue, 82, 62), hslToRgb(hue + 48, 78, 58));
  }
  if (anim === "prism") {
    const t = (Math.sin(elapsed / 1400) + 1) / 2;
    return packColors(mixRgb(PRISM_A, PRISM_B, t), mixRgb(PRISM_B, PRISM_A, t));
  }
  if (anim === "magma") {
    const rgb = cycleStops(MAGMA_STOPS, elapsed / 4200);
    return packColors(rgb, cycleStops(MAGMA_STOPS, elapsed / 4200 + 0.33));
  }
  if (anim === "tide") {
    const rgb = cycleStops(TIDE_STOPS, elapsed / 4800);
    return packColors(rgb, cycleStops(TIDE_STOPS, elapsed / 4800 + 0.38));
  }
  if (anim === "voltage") {
    const rgb = cycleStops(VOLTAGE_STOPS, elapsed / 3600);
    return packColors(rgb, cycleStops(VOLTAGE_STOPS, elapsed / 3600 + 0.42));
  }
  if (anim === "custom") {
    const a = hexToRgbArr(theme.accent);
    const b = hexToRgbArr(theme.accent2);
    const t = pingPong01(elapsed, 2800);
    const rgb = theme.animMode === "fade" ? pulseColors(a, b, t) : mixHsl(a, b, t);
    return packColors(rgb, rgb);
  }
  return packColors(hexToRgbArr(theme.accent), hexToRgbArr(theme.accent2));
}

function themeGradientFill(theme, from, to) {
  if (theme.gradientPct == null && theme.gradientFade == null && theme.gradientAngle == null) {
    if (!theme.anim && Array.isArray(theme.gradient) && theme.gradient.length >= 2) {
      return `linear-gradient(135deg, ${theme.gradient[0]}, ${theme.gradient[1]})`;
    }
    return `linear-gradient(135deg, ${from}, ${to})`;
  }
  const angle = clampInt(theme.gradientAngle, 0, 360, 135);
  const { start, end } = gradientBlendStops(theme.gradientPct, theme.gradientFade);
  return `linear-gradient(${angle}deg, ${from} ${start}%, ${to} ${end}%)`;
}

function paintThemeVars(colors) {
  document.documentElement.style.setProperty("--accent", colors.hex);
  document.documentElement.style.setProperty("--accent-2", colors.hex2);
  document.documentElement.style.setProperty("--accent-rgb", colors.rgb.join(", "));
  const theme = themeDef();
  const from = !theme.anim && theme.gradient?.[0] ? theme.gradient[0] : colors.hex;
  const to = !theme.anim && theme.gradient?.[1] ? theme.gradient[1] : colors.hex2;
  document.documentElement.style.setProperty("--theme-gradient", themeGradientFill(theme, from, to));
  document.documentElement.style.setProperty("--gradient-from", from);
  document.documentElement.style.setProperty("--gradient-to", to);
  document.documentElement.style.setProperty("--theme-glow", `rgba(${colors.rgb.join(",")}, .16)`);
  document.documentElement.style.setProperty("--on-accent", themeOnAccent(settings.theme, colors.rgb));
  const stops = document.querySelectorAll("#gaugeGrad stop");
  if (stops[0]) stops[0].setAttribute("stop-color", from);
  if (stops[1]) stops[1].setAttribute("stop-color", to);
}

function paintThemeCharts(hex) {}

function stopThemeAnim() {
  if (themeRaf) cancelAnimationFrame(themeRaf);
  themeRaf = 0;
}

function startThemeAnim() {
  stopThemeAnim();
  if (!isAnimatedTheme() || document.body.classList.contains("reduce-motion")) return;
  const running = themeDef().anim;
  const started = performance.now();
  let lastChart = 0;
  const step = now => {
    if (themeDef().anim !== running) return;
    const colors = themeColorsAt(settings.theme, (now - started) * themeSpeedScale());
    paintThemeVars(colors);
    if (now - lastChart > 160) {
      lastChart = now;
      paintThemeCharts(colors.hex);
    }
    themeRaf = requestAnimationFrame(step);
  };
  themeRaf = requestAnimationFrame(step);
}

function syncStaticGradients() {
  const row = $("#staticGradientRow");
  const input = row?.querySelector("input");
  const staticTheme = !isAnimatedTheme();
  const on = Boolean(settings.staticGradients) && staticTheme;
  document.body.classList.toggle("static-gradients", on);
  row?.classList.toggle("is-disabled", !staticTheme);
  if (input) input.disabled = !staticTheme;
  paintThemeCharts(themeAccent());
}

function syncThemeSpeedUi() {
  const slider = $("#themeSpeed");
  const output = $("#themeSpeedValue");
  const row = $("#themeSpeedRow");
  if (!slider) return;
  slider.value = String(settings.themeSpeed);
  if (output) output.textContent = `${themeSpeedScale().toFixed(1)}×`;
  paintRangeFill(slider);
  const enabled = isAnimatedTheme() && !settings.reduceMotion;
  slider.disabled = !enabled;
  row?.classList.toggle("is-disabled", !enabled);
  document.documentElement.style.setProperty("--theme-anim-ms", `${Math.round(3500 / themeSpeedScale())}ms`);
}

function applyTheme(name, persist = true) {
  const next = isKnownTheme(name) ? name : "omega";
  const theme = themeDef(next);
  settings.theme = next;
  stopThemeAnim();
  document.body.dataset.theme = settings.theme;
  Object.entries(theme).forEach(([key, value]) => {
    if (key === "anim" || key === "gradient" || key === "animMode" || key === "gradientPct" || key === "gradientFade" || key === "gradientAngle") return;
    document.documentElement.style.setProperty(`--${key.replace(/[A-Z]/g, m => `-${m.toLowerCase()}`)}`, value);
  });
  paintThemeVars(themeColorsAt(settings.theme, 0));
  $$(".theme-option[data-theme]").forEach(option => option.classList.toggle("active", option.dataset.theme === settings.theme));
  paintThemeCharts(theme.accent);
  paintCustomThemeCards();
  syncThemeSpeedUi();
  syncStaticGradients();
  if (isAnimatedTheme()) startThemeAnim();
  if (persist) saveSettings();
}

let themeEditKind = "custom";

function paintCustomPreview(el, colors) {
  if (!el || !colors) return;
  el.style.setProperty("--c1", colors.accent);
  el.style.setProperty("--c2", colors.accent2);
  if (colors.animMode != null || el.classList.contains("custom-anim-preview")) {
    el.style.setProperty("--ga", "135deg");
    el.style.setProperty("--gs", "10%");
    el.style.setProperty("--ge", "90%");
    return;
  }
  const angle = clampInt(colors.gradientAngle, 0, 360, 135);
  const { start, end } = gradientBlendStops(colors.gradientPct, colors.gradientFade);
  el.style.setProperty("--ga", `${angle}deg`);
  el.style.setProperty("--gs", `${start}%`);
  el.style.setProperty("--ge", `${end}%`);
}

function paintThemeEditPreview() {
  const el = $("#themeEditGradPreview");
  if (!el) return;
  const c = customThemeSettings(themeEditKind);
  el.style.background = themeGradientFill(c, c.accent, c.accent2);
}

function paintCustomThemeCards() {
  const stat = customThemeSettings("custom");
  const anim = customThemeSettings("custom-anim");
  const statName = $("#customStaticName");
  const animName = $("#customAnimName");
  if (statName) statName.textContent = stat.name;
  if (animName) animName.textContent = anim.name;
  paintCustomPreview($("#customStaticPreview"), stat);
  paintCustomPreview($("#customAnimPreview"), anim);
  if (themeEditorIsOpen()) paintThemeEditPreview();
}

function themeEditorIsOpen() {
  return Boolean($("#themeEditor")?.classList.contains("open"));
}

function fillThemeEditor() {
  const c = customThemeSettings(themeEditKind);
  const isAnim = themeEditKind === "custom-anim";
  const name = $("#themeEditName");
  const title = $("#themeEditTitle");
  const mix = $("#themeEditGradient");
  const mixOut = $("#themeEditGradientValue");
  const fade = $("#themeEditFade");
  const fadeOut = $("#themeEditFadeValue");
  const angle = $("#themeEditAngle");
  const angleOut = $("#themeEditAngleValue");
  const gradBlock = $("#themeEditGradientBlock");
  const animRow = $("#themeEditAnimRow");
  if (name) name.value = c.name;
  if (title) title.textContent = isAnim ? "Edit animated theme" : "Edit theme";
  $$("#themeEditor [data-theme-color]").forEach(input => {
    input.value = c[input.dataset.themeColor] || "#79f2b0";
  });
  if (mix) mix.value = String(c.gradientPct ?? 50);
  if (mixOut) mixOut.textContent = `${c.gradientPct ?? 50}%`;
  if (fade) fade.value = String(c.gradientFade ?? 80);
  if (fadeOut) fadeOut.textContent = `${c.gradientFade ?? 80}%`;
  if (angle) angle.value = String(c.gradientAngle ?? 135);
  if (angleOut) angleOut.textContent = `${c.gradientAngle ?? 135}°`;
  if (gradBlock) gradBlock.hidden = isAnim;
  if (animRow) animRow.hidden = !isAnim;
  $$("#themeEditAnimMode button").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.animMode === (c.animMode || "rgb"));
  });
  paintThemeEditPreview();
  paintAllRangeFills();
}

function commitThemeEditor() {
  const key = themeEditKind === "custom-anim" ? "customAnim" : "customStatic";
  const next = { ...customThemeSettings(themeEditKind) };
  next.name = String($("#themeEditName")?.value || "").trim().slice(0, 18) || defaultCustomTheme(themeEditKind).name;
  $$("#themeEditor [data-theme-color]").forEach(input => {
    const hex = normalizeLogColor(input.value);
    if (hex) next[input.dataset.themeColor] = hex;
  });
  if (themeEditKind === "custom-anim") {
    delete next.gradientPct;
    delete next.gradientFade;
    delete next.gradientAngle;
    next.animMode = $("#themeEditAnimMode .active")?.dataset.animMode === "fade" ? "fade" : "rgb";
  } else {
    next.gradientPct = clampInt($("#themeEditGradient")?.value, 0, 100, 50);
    next.gradientFade = clampInt($("#themeEditFade")?.value, 0, 100, 80);
    next.gradientAngle = clampInt($("#themeEditAngle")?.value, 0, 360, 135);
    const mixOut = $("#themeEditGradientValue");
    const fadeOut = $("#themeEditFadeValue");
    const angleOut = $("#themeEditAngleValue");
    if (mixOut) mixOut.textContent = `${next.gradientPct}%`;
    if (fadeOut) fadeOut.textContent = `${next.gradientFade}%`;
    if (angleOut) angleOut.textContent = `${next.gradientAngle}°`;
  }
  settings[key] = next;
  paintCustomThemeCards();
  if (settings.theme !== themeEditKind) {
    applyTheme(themeEditKind);
    return;
  }
  const theme = themeDef(themeEditKind);
  Object.entries(theme).forEach(([prop, value]) => {
    if (prop === "anim" || prop === "gradient" || prop === "animMode" || prop === "gradientPct" || prop === "gradientFade" || prop === "gradientAngle") return;
    document.documentElement.style.setProperty(`--${prop.replace(/[A-Z]/g, m => `-${m.toLowerCase()}`)}`, value);
  });
  if (!theme.anim || !themeRaf) {
    paintThemeVars(themeColorsAt(themeEditKind, 0));
    paintThemeCharts(theme.accent);
  }
  saveSettings();
}

function closeThemeEditor() {
  const overlay = $("#themeEditor");
  if (!overlay) return;
  overlay.classList.remove("open");
  overlay.hidden = true;
}

function openThemeEditor(kind) {
  themeEditKind = kind === "custom-anim" ? "custom-anim" : "custom";
  fillThemeEditor();
  applyTheme(themeEditKind);
  const overlay = $("#themeEditor");
  if (!overlay) return;
  overlay.hidden = false;
  requestAnimationFrame(() => overlay.classList.add("open"));
}

function bindThemeEditor() {
  $$("[data-edit-theme]").forEach(btn => {
    btn.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      openThemeEditor(btn.dataset.editTheme);
    });
  });
  $("#themeEditorClose")?.addEventListener("click", closeThemeEditor);
  $("#themeEditor")?.addEventListener("click", event => {
    if (event.target.id !== "themeEditor") return;
    closeThemeEditor();
  });
  $("#themeEditName")?.addEventListener("input", commitThemeEditor);
  $("#themeEditGradient")?.addEventListener("input", commitThemeEditor);
  $("#themeEditFade")?.addEventListener("input", commitThemeEditor);
  $("#themeEditAngle")?.addEventListener("input", commitThemeEditor);
  $$("#themeEditor [data-theme-color]").forEach(input => {
    input.addEventListener("input", commitThemeEditor);
  });
  $("#themeEditAnimMode")?.addEventListener("click", event => {
    const btn = event.target.closest("[data-anim-mode]");
    if (!btn) return;
    $$("#themeEditAnimMode button").forEach(el => el.classList.toggle("active", el === btn));
    commitThemeEditor();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    if ($("#confirmModal")?.classList?.contains("open")) return;
    if (logSettingsIsOpen()) return;
    if (!themeEditorIsOpen()) return;
    event.preventDefault();
    closeThemeEditor();
  });
}

let bgFxRaf = 0;
let bgFxMode = "off";
let bgFxPts = [];
let bgFxRipples = [];
let bgFxMouse = { x: 0, y: 0, tx: 0, ty: 0, on: false };
let bgFxBound = false;
let bgFxSeedKey = "";
let bgFxLastRipple = 0;

function accentRgbArr() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--accent-rgb");
  const parts = raw.split(",").map(n => Number(String(n).trim()));
  if (parts.length >= 3 && parts.every(n => Number.isFinite(n))) return parts;
  return [121, 242, 176];
}

function rgbaAccent(rgb, a) {
  return `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;
}

function syncBgFxPicker() {
  const current = normalizeBgFx(settings.bgFx);
  $$("[data-bg-fx]").forEach(btn => btn.classList.toggle("active", btn.dataset.bgFx === current));
  $("#bgFxGroup")?.classList.toggle("is-disabled", Boolean(settings.reduceMotion));
}

function setBgFx(name) {
  settings.bgFx = normalizeBgFx(name);
  syncBgFx();
  saveSettings();
}

function stopBgFx() {
  if (bgFxRaf) cancelAnimationFrame(bgFxRaf);
  bgFxRaf = 0;
  bgFxMode = "off";
  bgFxPts = [];
  bgFxRipples = [];
  bgFxSeedKey = "";
  const canvas = $("#bgFxCanvas");
  if (canvas) {
    canvas.hidden = true;
    const ctx = canvas.getContext("2d");
    ctx?.clearRect(0, 0, canvas.width, canvas.height);
  }
  document.body.classList.remove("has-bg-fx");
}

function bindBgFxPointer() {
  if (bgFxBound) return;
  bgFxBound = true;
  document.addEventListener("mousemove", event => {
    bgFxMouse.tx = event.clientX;
    bgFxMouse.ty = event.clientY;
    bgFxMouse.on = true;
  }, { passive: true });
  document.addEventListener("mouseleave", () => { bgFxMouse.on = false; });
  document.addEventListener("click", event => {
    if (!bgFxOptsFor(bgFxMode).click) return;
    const cfg = bgFxOptsFor(bgFxMode);
    const amt = bgFxMode === "mesh"
      ? Math.max(0.08, (cfg.clickAmt || 10) / 10 * 0.6)
      : Math.max(0.2, (cfg.clickAmt || 5) / 5);
    const x = event.clientX;
    const y = event.clientY;
    if (bgFxMode === "particles" || bgFxMode === "neural") {
      const force = (bgFxMode === "neural" ? 2.35 : 2.8) * amt;
      const radius = 120 + amt * 85;
      burstBgFx(x, y, force, radius);
      return;
    }
    if (bgFxMode === "mesh") {
      burstBgMesh(x, y, amt);
      return;
    }
    if (bgFxMode === "hive") {
      const glowR = 90 + (cfg.radius || 5) * 24;
      bgFxRipples.push({ x, y, r: Math.max(40, glowR - 12), vr: 3.2 + amt * 3.4, life: 1 });
      if (bgFxRipples.length > 16) bgFxRipples.shift();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      if (bgFxRaf) cancelAnimationFrame(bgFxRaf);
      bgFxRaf = 0;
      return;
    }
    if (normalizeBgFx(settings.bgFx) !== "off" && !settings.reduceMotion) startBgFx();
  });
}

function seedBgFx(mode, w, h) {
  const cfg = bgFxOptsFor(mode);
  const pts = [];
  if (mode === "mesh") {
    const gap = Math.max(28, 76 - cfg.density * 4.4);
    const cols = Math.max(8, Math.ceil(w / gap) + 2);
    const rows = Math.max(6, Math.ceil(h / gap) + 2);
    for (let y = 0; y < rows; y++) {
      for (let x = 0; x < cols; x++) {
        const rx = (x - 0.5) * (w / (cols - 1));
        const ry = (y - 0.5) * (h / (rows - 1));
        pts.push({ x: rx, y: ry, rx, ry, vx: 0, vy: 0, heat: 0, col: x, row: y, cols, rows });
      }
    }
    return pts;
  }
  if (mode === "hive") {
    const size = Math.max(16, 38 - cfg.density * 2);
    const hexW = size * Math.sqrt(3);
    const hexH = size * 1.5;
    const cols = Math.ceil(w / hexW) + 3;
    const rows = Math.ceil(h / hexH) + 3;
    for (let row = 0; row < rows; row++) {
      for (let col = 0; col < cols; col++) {
        pts.push({
          cx: (col + (row % 2) * 0.5) * hexW,
          cy: row * hexH,
          size
        });
      }
    }
    return pts;
  }
  if (mode === "stars") {
    const count = Math.round(90 + cfg.density * 32);
    const base = 0.35 + cfg.size * 0.28;
    const jitter = cfg.sizeJitter / 10;
    for (let i = 0; i < count; i++) {
      const roll = 1 + (Math.random() * 2 - 1) * jitter * 0.9;
      pts.push({
        x: Math.random() * w,
        y: Math.random() * h,
        z: 0.2 + Math.random() * 0.8,
        phase: Math.random() * Math.PI * 2,
        size: Math.max(0.22, base * roll),
        twinkling: false,
        twinkleUntil: 0,
        twinkleRate: 0.008,
        gradStar: Math.random() < 0.09
      });
    }
    return pts;
  }
  if (mode === "neural") {
    const count = Math.round(52 + cfg.count * 16);
    const clusters = [];
    for (let i = 0; i < 6; i++) {
      clusters.push({
        x: w * (0.12 + Math.random() * 0.76),
        y: h * (0.14 + Math.random() * 0.72)
      });
    }
    for (let i = 0; i < count; i++) {
      let x;
      let y;
      if (Math.random() < 0.64) {
        const c = clusters[i % clusters.length];
        const ang = Math.random() * Math.PI * 2;
        const rad = Math.pow(Math.random(), 0.65) * Math.min(w, h) * 0.24;
        x = c.x + Math.cos(ang) * rad;
        y = c.y + Math.sin(ang) * rad;
      } else {
        x = Math.random() * w;
        y = Math.random() * h;
      }
      pts.push({
        x, y,
        z: 0.22 + Math.random() * 0.78,
        vx: (Math.random() - 0.5) * 0.22,
        vy: (Math.random() - 0.5) * 0.22,
        vz: (Math.random() - 0.5) * 0.0035
      });
    }
    return pts;
  }
  const count = Math.round(40 + cfg.count * 12);
  for (let i = 0; i < count; i++) {
    pts.push({
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.35,
      vy: (Math.random() - 0.5) * 0.35,
      r: 1.1 + Math.random() * 1.3
    });
  }
  return pts;
}

function startBgFx() {
  const canvas = $("#bgFxCanvas");
  const mode = settings.reduceMotion ? "off" : normalizeBgFx(settings.bgFx);
  syncBgFxPicker();
  if (!canvas || mode === "off") {
    stopBgFx();
    return;
  }
  bindBgFxPointer();
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    stopBgFx();
    return;
  }
  if (!bgFxMouse.tx && !bgFxMouse.ty) {
    bgFxMouse.tx = window.innerWidth * 0.62;
    bgFxMouse.ty = window.innerHeight * 0.42;
    bgFxMouse.x = bgFxMouse.tx;
    bgFxMouse.y = bgFxMouse.ty;
  }
  canvas.hidden = false;
  document.body.classList.add("has-bg-fx");
  if (bgFxMode !== mode) {
    bgFxMode = mode;
    bgFxPts = [];
    bgFxRipples = [];
    bgFxSeedKey = "";
  }
  if (bgFxRaf) cancelAnimationFrame(bgFxRaf);

  const step = () => {
    const w = document.documentElement.clientWidth || window.innerWidth;
    const h = document.documentElement.clientHeight || window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.25);
    const bw = Math.floor(w * dpr);
    const bh = Math.floor(h * dpr);
    if (canvas.width !== bw || canvas.height !== bh) {
      canvas.width = bw;
      canvas.height = bh;
      canvas.style.width = "100%";
      canvas.style.height = "100%";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    const cfg = bgFxOptsFor(mode);
    const seedKey = mode === "mesh"
      ? `mesh:${cfg.density}:${Math.ceil(w / Math.max(28, 76 - cfg.density * 4.4))}`
      : mode === "hive"
        ? `hive:${cfg.density}:${w >> 6}x${h >> 6}`
        : mode === "stars"
          ? `stars:${cfg.density}:${cfg.size}:${cfg.sizeJitter}:${w >> 7}x${h >> 7}`
          : `${mode}:${cfg.count || 0}:${w >> 7}x${h >> 7}`;
    if (bgFxSeedKey !== seedKey) {
      bgFxSeedKey = seedKey;
      bgFxPts = seedBgFx(mode, w, h);
    }

    bgFxMouse.x += (bgFxMouse.tx - bgFxMouse.x) * 0.12;
    bgFxMouse.y += (bgFxMouse.ty - bgFxMouse.y) * 0.12;
    if (!bgFxMouse.on) {
      bgFxMouse.tx += (w * 0.55 - bgFxMouse.tx) * 0.01;
      bgFxMouse.ty += (h * 0.42 - bgFxMouse.ty) * 0.01;
    }

    const rgb = accentRgbArr();
    ctx.clearRect(0, 0, w, h);

    if (mode === "mesh") drawBgMesh(ctx, w, h, rgb);
    else if (mode === "hive") drawBgHive(ctx, rgb);
    else if (mode === "stars") drawBgStars(ctx, w, h, rgb);
    else if (mode === "neural") drawBgNeural(ctx, w, h, rgb);
    else drawBgNodes(ctx, w, h, rgb);

    bgFxRaf = requestAnimationFrame(step);
  };
  bgFxRaf = requestAnimationFrame(step);
}

function syncBgFx() {
  const mode = normalizeBgFx(settings.bgFx);
  settings.bgFx = mode;
  syncBgFxPicker();
  if (mode === "off" || settings.reduceMotion) {
    stopBgFx();
    return;
  }
  if (bgFxRaf && bgFxMode === mode) return;
  startBgFx();
}

function burstBgFx(x, y, force, radius) {
  for (const p of bgFxPts) {
    if (p.x == null || p.y == null || p.rx != null) continue;
    const dx = p.x - x;
    const dy = p.y - y;
    const d = Math.hypot(dx, dy) || 1;
    if (d > radius) continue;
    const falloff = (1 - d / radius) ** 1.45;
    p.vx = (p.vx || 0) + (dx / d) * force * falloff;
    p.vy = (p.vy || 0) + (dy / d) * force * falloff;
    p.stun = Math.max(p.stun || 0, 0.42 + falloff * 0.38);
    p.glowTint = Math.max(p.glowTint || 0, Math.min(1, falloff ** 0.78));
  }
}

function burstBgMesh(x, y, amt = 1) {
  const radius = 210 + amt * 90;
  for (const p of bgFxPts) {
    if (p.rx == null) continue;
    const dx = p.rx - x;
    const dy = p.ry - y;
    const d = Math.hypot(dx, dy) || 1;
    if (d > radius) continue;
    const falloff = (1 - d / radius) ** 0.9;
    p.heat = Math.max(p.heat || 0, falloff);
    p.glowTint = Math.max(p.glowTint || 0, falloff);
    p.vx = (p.vx || 0) + (dx / d) * 22 * amt * falloff;
    p.vy = (p.vy || 0) + (dy / d) * 22 * amt * falloff;
  }
}

function bgFxWaveEnergy(x, y, radius) {
  let energy = 0;
  if (radius > 0) {
    energy = Math.max(0, 1 - Math.hypot(x - bgFxMouse.x, y - bgFxMouse.y) / radius);
  }
  for (const wave of bgFxRipples) {
    const band = 1 - Math.abs(Math.hypot(x - wave.x, y - wave.y) - wave.r) / 34;
    if (band > 0) energy = Math.max(energy, band * wave.life);
  }
  return energy;
}

function drawBgNodes(ctx, w, h, rgb) {
  const cfg = bgFxOptsFor("particles");
  const pts = bgFxPts;
  const mx = bgFxMouse.x;
  const my = bgFxMouse.y;
  const reach = 80 + cfg.attract * 18;
  const reach2 = reach * reach;
  const link = cfg.links <= 0 ? 0 : 40 + cfg.links * 12;
  const link2 = link * link;
  const pull = cfg.attract * 0.012;
  const drift = 0.55 + cfg.speed * 0.12;
  const size = 0.55 + cfg.size * 0.18;

  for (const p of pts) {
    p.stun = Math.max(0, (p.stun || 0) - 0.0053);
    p.glowTint = Math.max(0, (p.glowTint || 0) * 0.9956);
    const dx = mx - p.x;
    const dy = my - p.y;
    const d2 = dx * dx + dy * dy;
    if (!p.stun && pull && d2 < reach2 && d2 > 16) {
      const d = Math.sqrt(d2) || 1;
      p.vx += (dx / d) * pull;
      p.vy += (dy / d) * pull;
    }
    p.vx *= p.stun ? 0.985 : 0.96;
    p.vy *= p.stun ? 0.985 : 0.96;
    p.x += p.vx * (p.stun ? 1.35 : drift);
    p.y += p.vy * (p.stun ? 1.35 : drift);
    if (p.x < -24) p.x = w + 24;
    else if (p.x > w + 24) p.x = -24;
    if (p.y < -24) p.y = h + 24;
    else if (p.y > h + 24) p.y = -24;
  }

  if (link) {
    ctx.lineWidth = 0.7;
    for (let i = 0; i < pts.length; i++) {
      const a = pts[i];
      for (let j = i + 1; j < pts.length; j++) {
        const b = pts[j];
        const dx = a.x - b.x;
        const dy = a.y - b.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > link2) continue;
        const t = 1 - d2 / link2;
        const color = fxClickTint(rgb, ((a.glowTint || 0) + (b.glowTint || 0)) / 2);
        ctx.strokeStyle = rgbaAccent(color, 0.14 * t);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
  }

  for (const p of pts) {
    ctx.fillStyle = rgbaAccent(fxClickTint(rgb, p.glowTint || 0), 0.55);
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.r * size, 0, Math.PI * 2);
    ctx.fill();
  }

  for (const wave of bgFxRipples) {
    wave.r += wave.vr;
    wave.life -= 0.02;
    if (wave.life <= 0) continue;
    ctx.strokeStyle = rgbaAccent(rgb, 0.28 * wave.life);
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(wave.x, wave.y, wave.r, 0, Math.PI * 2);
    ctx.stroke();
  }
  bgFxRipples = bgFxRipples.filter(wave => wave.life > 0);
}

function drawBgNeural(ctx, w, h, rgb) {
  const cfg = bgFxOptsFor("neural");
  const pts = bgFxPts;
  const mx = bgFxMouse.x;
  const my = bgFxMouse.y;
  const link = 72 + cfg.link * 14;
  const link2 = link * link;
  const mouseR = cfg.mouse <= 0 ? 0 : 48 + cfg.mouse * 16;
  const mouseR2 = mouseR * mouseR;
  const glow = cfg.glow / 10;
  const drift = 0.35 + cfg.speed * 0.12;

  for (const p of pts) {
    p.z += p.vz;
    if (p.z < 0.2 || p.z > 1) p.vz *= -1;
    p.z = Math.min(1, Math.max(0.2, p.z));
    p.stun = Math.max(0, (p.stun || 0) - 0.0047);
    p.glowTint = Math.max(0, (p.glowTint || 0) * 0.9956);
    if (!p.stun && mouseR && bgFxMouse.on) {
      const dx = mx - p.x;
      const dy = my - p.y;
      const d2 = dx * dx + dy * dy;
      if (d2 < mouseR2 && d2 > 36) {
        const d = Math.sqrt(d2) || 1;
        p.vx += (dx / d) * 0.01 * (cfg.mouse / 10);
        p.vy += (dy / d) * 0.01 * (cfg.mouse / 10);
      }
    }
    p.vx *= p.stun ? 0.99 : 0.992;
    p.vy *= p.stun ? 0.99 : 0.992;
    p.x += p.vx * (p.stun ? 1.45 : drift);
    p.y += p.vy * (p.stun ? 1.45 : drift);
    if (p.x < -30) p.x = w + 30;
    else if (p.x > w + 30) p.x = -30;
    if (p.y < -30) p.y = h + 30;
    else if (p.y > h + 30) p.y = -30;
  }

  ctx.lineCap = "round";
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i];
    for (let j = i + 1; j < pts.length; j++) {
      const b = pts[j];
      if (Math.abs(a.z - b.z) > 0.42) continue;
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d2 = dx * dx + dy * dy;
      if (d2 > link2) continue;
      const t = 1 - Math.sqrt(d2) / link;
      const depth = Math.min(a.z, b.z);
      const color = fxClickTint(rgb, ((a.glowTint || 0) + (b.glowTint || 0)) / 2);
      ctx.lineWidth = 0.55 + depth * 0.7;
      ctx.strokeStyle = rgbaAccent(color, (0.08 + t * t * 0.42) * depth);
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
  }

  if (mouseR) {
    for (const p of pts) {
      const dx = p.x - mx;
      const dy = p.y - my;
      const d2 = dx * dx + dy * dy;
      if (d2 > mouseR2) continue;
      const t = 1 - Math.sqrt(d2) / mouseR;
      const color = fxClickTint(rgb, p.glowTint || 0);
      ctx.lineWidth = 0.7 + p.z * 0.5;
      ctx.strokeStyle = rgbaAccent(color, 0.16 + t * 0.38 * p.z);
      ctx.beginPath();
      ctx.moveTo(mx, my);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }
  }

  for (const p of pts) {
    const hit = p.glowTint || 0;
    const tint = fxClickTint(rgb, hit);
    const core = mixRgb(tint, [255, 255, 255], 0.28 * p.z * (1 - hit * 0.7));
    if (glow) {
      ctx.fillStyle = rgbaAccent(tint, (0.07 + hit * 0.16) * glow * p.z);
      ctx.beginPath();
      ctx.arc(p.x, p.y, (3.2 + p.z * 5.5) * (0.45 + glow), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = `rgba(${core[0]},${core[1]},${core[2]},${0.4 + p.z * 0.55})`;
    ctx.beginPath();
    ctx.arc(p.x, p.y, 0.7 + p.z * 1.85, 0, Math.PI * 2);
    ctx.fill();
  }

  if (mouseR) {
    ctx.fillStyle = rgbaAccent(rgb, 0.22);
    ctx.beginPath();
    ctx.arc(mx, my, 2.2, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawBgMesh(ctx, w, h, rgb) {
  const cfg = bgFxOptsFor("mesh");
  const pts = bgFxPts;
  const mx = bgFxMouse.x;
  const my = bgFxMouse.y;
  const cols = pts[0]?.cols || 1;
  const radius = 90 + cfg.radius * 22;
  for (const wave of bgFxRipples) {
    wave.r += wave.vr;
    wave.life -= 0.016;
  }
  bgFxRipples = bgFxRipples.filter(wave => wave.life > 0);
  for (const p of pts) {
    const dx = p.rx - mx;
    const dy = p.ry - my;
    const dist = Math.hypot(dx, dy) || 1;
    const inAoE = cfg.pull > 0 && dist < radius && (p.heat || 0) < 0.35;
    const pull = inAoE ? Math.min(12 + cfg.pull * 4, (2400 + cfg.pull * 180) / (dist + 28)) : 0;
    p.vx = (p.vx || 0) * 0.9;
    p.vy = (p.vy || 0) * 0.9;
    p.x += p.vx;
    p.y += p.vy;
    const heat = p.heat || 0;
    const settle = heat > 0.12 ? 0.03 : 0.16;
    const tx = p.rx - (dx / dist) * pull;
    const ty = p.ry - (dy / dist) * pull;
    p.x += (tx - p.x) * settle;
    p.y += (ty - p.y) * settle;
    p.heat = Math.max(0, heat * 0.965);
    p.glowTint = Math.max(0, (p.glowTint || 0) * 0.987);
  }
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    const right = (p.col + 1) < cols ? pts[i + 1] : null;
    const down = pts[i + cols];
    const drawSeg = (other) => {
      if (!other) return;
      const x = (p.x + other.x) / 2;
      const y = (p.y + other.y) / 2;
      const energy = Math.max(bgFxWaveEnergy(x, y, radius), (p.heat + (other.heat || 0)) / 2);
      const color = fxClickTint(rgb, (p.glowTint + (other.glowTint || 0)) / 2);
      ctx.lineWidth = 0.8 + energy * 1.1;
      ctx.strokeStyle = rgbaAccent(color, 0.07 + energy * 0.5);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      ctx.lineTo(other.x, other.y);
      ctx.stroke();
    };
    if (right && right.row === p.row) drawSeg(right);
    if (down) drawSeg(down);
  }
  for (const p of pts) {
    const near = Math.max(bgFxWaveEnergy(p.x, p.y, radius), p.heat || 0);
    ctx.fillStyle = rgbaAccent(fxClickTint(rgb, p.glowTint || 0), 0.1 + near * 0.62);
    ctx.beginPath();
    ctx.arc(p.x, p.y, 1.05 + near * 1.6, 0, Math.PI * 2);
    ctx.fill();
  }
}

function hexPath(ctx, cx, cy, size) {
  const start = -Math.PI / 6;
  ctx.moveTo(cx + size * Math.cos(start), cy + size * Math.sin(start));
  for (let i = 1; i <= 6; i++) {
    const a = start + i * Math.PI / 3;
    ctx.lineTo(cx + size * Math.cos(a), cy + size * Math.sin(a));
  }
  ctx.closePath();
}

function themeGradientRgb() {
  const css = getComputedStyle(document.documentElement).getPropertyValue("--gradient-to").trim();
  if (css) return hexToRgbArr(css);
  const theme = themeDef();
  return hexToRgbArr(theme.gradient?.[1] || theme.accent2);
}

function fxWaveTint(rgb, life) {
  if (!settings.staticGradients || isAnimatedTheme()) return rgb;
  const u = Math.min(1, Math.max(0, (1 - life) / 0.68));
  const t = u * u * (3 - 2 * u);
  return mixRgb(rgb, themeGradientRgb(), t);
}

function fxClickTint(rgb, heat) {
  if (!settings.staticGradients || isAnimatedTheme()) return rgb;
  const t = Math.min(1, Math.max(0, heat));
  if (t < 0.02) return rgb;
  return mixRgb(rgb, themeGradientRgb(), t);
}

function drawBgHive(ctx, rgb) {
  const cfg = bgFxOptsFor("hive");
  const cells = bgFxPts;
  const radius = 90 + cfg.radius * 24;
  const glow = cfg.glow / 10;
  const size = cells[0]?.size || 22;
  for (const wave of bgFxRipples) {
    wave.r += wave.vr;
    wave.life -= 0.012;
    wave.tint = fxWaveTint(rgb, wave.life);
  }
  bgFxRipples = bgFxRipples.filter(wave => wave.life > 0);

  ctx.lineJoin = "round";
  ctx.lineWidth = 1;
  ctx.strokeStyle = rgbaAccent(rgb, 0.055 + glow * 0.04);
  ctx.beginPath();
  for (const cell of cells) hexPath(ctx, cell.cx, cell.cy, size);
  ctx.stroke();

  for (const cell of cells) {
    let energy = radius > 0 ? Math.max(0, 1 - Math.hypot(cell.cx - bgFxMouse.x, cell.cy - bgFxMouse.y) / radius) : 0;
    let color = rgb;
    for (const wave of bgFxRipples) {
      const band = 1 - Math.abs(Math.hypot(cell.cx - wave.x, cell.cy - wave.y) - wave.r) / 34;
      if (band <= 0) continue;
      const next = band * wave.life;
      if (next > energy) {
        energy = next;
        color = wave.tint || rgb;
      }
    }
    if (energy < 0.05) continue;
    const alpha = (0.12 + energy * 0.62) * (0.45 + glow);
    ctx.lineWidth = 1 + energy * 1.4;
    ctx.strokeStyle = rgbaAccent(color, alpha);
    ctx.beginPath();
    hexPath(ctx, cell.cx, cell.cy, size);
    ctx.stroke();
    if (energy > 0.35) {
      ctx.fillStyle = rgbaAccent(color, 0.04 * energy * glow);
      ctx.fill();
    }
  }
}

function starShowerDelay(cfg) {
  if (cfg.showers <= 0) return 1e9;
  const base = Math.max(520, 3600 - cfg.showers * 300);
  const jitter = Math.max(0, Math.min(10, Number(cfg.showerJitter) || 0)) / 10;
  const minMul = 1 - jitter * 0.85;
  const maxMul = 1 + jitter * 2.8;
  return base * (minMul + Math.random() * Math.max(0, maxMul - minMul));
}

function spawnAmbientMeteor(w, h) {
  const cfg = bgFxOptsFor("stars");
  const x = w * (0.04 + Math.random() * 0.92);
  const y = -22 - Math.random() * 40;
  const ang = Math.PI / 2 + (Math.random() * 2 - 1) * 0.95;
  const n = Math.min(10, Math.max(1, Number(cfg.meteorSpeed) || 5));
  const oldN = n >= 5 ? n - 4 : n / 5;
  const speed = 3.6 + oldN * 1.35 + Math.random() * 2.2;
  const jitter = cfg.meteorJitter / 10;
  const sizeRoll = 1 + (Math.random() * 2 - 1) * jitter * 0.85;
  const size = Math.max(0.35, (0.45 + cfg.meteorSize * 0.22) * sizeRoll);
  const burn = cfg.burnout / 10;
  const rand = cfg.burnoutJitter / 10;
  let dieY = h + 120;
  if (burn > 0) {
    const base = 1.12 - burn * 0.94;
    const minT = base + (0.1 - base) * rand;
    const maxT = base + (1.14 - base) * rand;
    const travel = minT + Math.random() * Math.max(0.02, maxT - minT);
    dieY = y + Math.max(70, travel * h);
  }
  const gradStar = Math.random() < 0.04;
  bgFxRipples.push({
    x,
    y,
    spawnY: y,
    dieY,
    vx: Math.cos(ang) * speed,
    vy: Math.sin(ang) * speed,
    life: 1,
    len: (36 + cfg.meteorSize * 6) * sizeRoll,
    width: size,
    star: true,
    gradStar,
    whiteStar: !gradStar && Math.random() < 0.1
  });
  if (bgFxRipples.length > 14) bgFxRipples.shift();
}

function tickStarTwinkle(pts, want, now) {
  let active = 0;
  for (const p of pts) {
    if ((p.twinkleUntil || 0) > now) active++;
    else p.twinkling = false;
  }
  let guard = 0;
  while (active < want && guard++ < 16) {
    const p = pts[Math.floor(Math.random() * pts.length)];
    if (!p || p.twinkling) continue;
    p.twinkling = true;
    const speed = 0.0025 + (bgFxOptsFor("stars").twinkleSpeed / 10) * 0.018;
    p.twinkleRate = speed * (0.55 + Math.random() * 0.9);
    p.twinkleUntil = now + (260 + Math.random() * 820) / (0.55 + (bgFxOptsFor("stars").twinkleSpeed / 10) * 0.9);
    p.phase = Math.random() * Math.PI * 2;
    active++;
  }
}

function drawBgStars(ctx, w, h, rgb) {
  const cfg = bgFxOptsFor("stars");
  const now = performance.now();
  const mx = bgFxMouse.x;
  const my = bgFxMouse.y;
  const want = Math.round(bgFxPts.length * (cfg.twinkle / 10) * 0.42);
  tickStarTwinkle(bgFxPts, want, now);
  if (cfg.showers <= 0) {
    bgFxLastRipple = 0;
  } else if (!bgFxLastRipple) {
    bgFxLastRipple = now + starShowerDelay(cfg);
  } else if (now >= bgFxLastRipple) {
    spawnAmbientMeteor(w, h);
    bgFxLastRipple = now + starShowerDelay(cfg);
  }

  for (const p of bgFxPts) {
    const ox = cfg.parallax ? (mx - w / 2) * 0.016 * p.z : 0;
    const oy = cfg.parallax ? (my - h / 2) * 0.016 * p.z : 0;
    let pulse = 1;
    if (p.twinkling && (p.twinkleUntil || 0) > now) {
      pulse = 0.22 + 0.78 * (0.5 + 0.5 * Math.sin(now * p.twinkleRate + p.phase));
    }
    const alpha = (0.22 + p.z * 0.65) * pulse;
    if (p.gradStar == null) p.gradStar = Math.random() < 0.09;
    const useGrad = p.gradStar && settings.staticGradients && !isAnimatedTheme();
    const core = useGrad
      ? (p.z > 0.72 ? mixRgb(themeGradientRgb(), [255, 255, 255], 0.28) : themeGradientRgb())
      : (p.z > 0.72 ? mixRgb(rgb, [255, 255, 255], 0.55) : [220, 228, 235]);
    ctx.fillStyle = `rgba(${core[0]},${core[1]},${core[2]},${alpha})`;
    ctx.beginPath();
    ctx.arc(p.x + ox, p.y + oy, p.size * (0.55 + p.z * 0.7), 0, Math.PI * 2);
    ctx.fill();
  }

  ctx.lineCap = "round";
  for (const m of bgFxRipples) {
    if (!m.star) continue;
    m.x += m.vx;
    m.y += m.vy;
    if (m.y > (m.dieY ?? h + 80) || m.x < -80 || m.x > w + 80) continue;
    if ((m.dieY ?? h + 80) > h + 40) {
      m.life = 1;
    } else {
      const span = Math.max(60, m.dieY - (m.spawnY ?? -30));
      const progress = (m.y - (m.spawnY ?? -30)) / span;
      m.life = progress < 0.7 ? 1 : Math.max(0, (1 - progress) / 0.3);
    }
    if (m.life <= 0) continue;
    const ang = Math.atan2(m.vy, m.vx);
    const tail = m.len * m.life;
    const x2 = m.x - Math.cos(ang) * tail;
    const y2 = m.y - Math.sin(ang) * tail;
    const useGrad = m.gradStar && settings.staticGradients && !isAnimatedTheme();
    const gRgb = useGrad ? themeGradientRgb() : rgb;
    const white = Boolean(m.whiteStar) && !useGrad;
    const tip = white
      ? `rgba(255,255,255,${0.85 * m.life})`
      : rgbaAccent(gRgb, 0.9 * m.life);
    const grad = ctx.createLinearGradient(x2, y2, m.x, m.y);
    grad.addColorStop(0, "rgba(255,255,255,0)");
    grad.addColorStop(0.55, rgbaAccent(gRgb, (white ? 0.18 : 0.28) * m.life));
    grad.addColorStop(1, tip);
    ctx.strokeStyle = grad;
    ctx.lineWidth = (m.width || 1.2) * (1 + m.life * 0.35);
    ctx.beginPath();
    ctx.moveTo(x2, y2);
    ctx.lineTo(m.x, m.y);
    ctx.stroke();
    ctx.fillStyle = white ? `rgba(255,255,255,${0.8 * m.life})` : rgbaAccent(gRgb, 0.85 * m.life);
    ctx.beginPath();
    ctx.arc(m.x, m.y, 0.7 + (m.width || 1.2) * 0.7, 0, Math.PI * 2);
    ctx.fill();
  }
  bgFxRipples = bgFxRipples.filter(m => m.star && m.life > 0 && m.x > -80 && m.x < w + 80 && m.y < h + 80);
}

let bgFxEditMode = "neural";

function bgFxEditorIsOpen() {
  return Boolean($("#bgFxEditor")?.classList.contains("open"));
}

function closeBgFxEditor() {
  const overlay = $("#bgFxEditor");
  if (!overlay) return;
  overlay.classList.remove("open");
  overlay.hidden = true;
}

function fillBgFxEditor() {
  const mode = bgFxEditMode;
  const title = $("#bgFxEditTitle");
  const hint = $("#bgFxEditHint");
  const box = $("#bgFxEditFields");
  if (title) title.textContent = BG_FX_TITLES[mode] || "Edit background";
  if (hint) hint.textContent = BG_FX_HINTS[mode] || "";
  if (!box) return;
  const cfg = bgFxOptsFor(mode);
  box.innerHTML = (BG_FX_FIELDS[mode] || []).map(field => {
    if (field.type === "section") {
      return `<p class="theme-group-label bg-fx-edit-section">${field.label}</p>`;
    }
    if (field.type === "toggle") {
      const on = Boolean(cfg[field.key]);
      return `<label class="setting-toggle bg-fx-edit-toggle"><span><strong>${field.label}</strong></span><input type="checkbox" data-bg-fx-opt="${field.key}"${on ? " checked" : ""}><i></i></label>`;
    }
    return `<label class="theme-edit-slider"><span>${field.label} <output data-bg-fx-out="${field.key}">${cfg[field.key]}</output></span><input type="range" min="${field.min}" max="${field.max}" step="1" value="${cfg[field.key]}" data-bg-fx-opt="${field.key}"></label>`;
  }).join("");
  paintAllRangeFills();
}

function commitBgFxEditor() {
  const mode = bgFxEditMode;
  const fields = BG_FX_FIELDS[mode] || [];
  const defaults = BG_FX_DEFAULT_OPTS[mode] || {};
  const next = { ...defaults };
  for (const field of fields) {
    if (!field.key) continue;
    const input = $(`#bgFxEditFields [data-bg-fx-opt="${field.key}"]`);
    const value = field.type === "toggle"
      ? (input?.checked ? 1 : 0)
      : clampFxOpt(input?.value, field.min, field.max, defaults[field.key]);
    next[field.key] = value;
    const out = $(`#bgFxEditFields [data-bg-fx-out="${field.key}"]`);
    if (out) out.textContent = String(next[field.key]);
  }
  settings.bgFxOpts = normalizeBgFxOpts({ ...settings.bgFxOpts, [mode]: next });
  saveSettings();
}

function openBgFxEditor(mode) {
  const name = normalizeBgFx(mode);
  if (name === "off") return;
  bgFxEditMode = name;
  if (settings.bgFx !== name) setBgFx(name);
  fillBgFxEditor();
  const overlay = $("#bgFxEditor");
  if (!overlay) return;
  overlay.hidden = false;
  requestAnimationFrame(() => overlay.classList.add("open"));
}

function bindBgFxEditor() {
  $$("[data-edit-bg-fx]").forEach(btn => {
    btn.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      openBgFxEditor(btn.dataset.editBgFx);
    });
  });
  $("#bgFxEditorClose")?.addEventListener("click", closeBgFxEditor);
  $("#bgFxEditReset")?.addEventListener("click", () => {
    settings.bgFxOpts = normalizeBgFxOpts({
      ...settings.bgFxOpts,
      [bgFxEditMode]: { ...BG_FX_DEFAULT_OPTS[bgFxEditMode] }
    });
    fillBgFxEditor();
    saveSettings();
  });
  $("#bgFxEditFields")?.addEventListener("input", event => {
    if (!event.target.closest("[data-bg-fx-opt]")) return;
    commitBgFxEditor();
  });
  $("#bgFxEditFields")?.addEventListener("change", event => {
    if (!event.target.closest("[data-bg-fx-opt]")) return;
    commitBgFxEditor();
  });
  $("#bgFxEditor")?.addEventListener("click", event => {
    if (event.target.id !== "bgFxEditor") return;
    closeBgFxEditor();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || !bgFxEditorIsOpen()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    closeBgFxEditor();
  });
}

function frostEditorIsOpen() {
  return Boolean($("#frostNavEditor")?.classList.contains("open"));
}

function closeFrostEditor() {
  const overlay = $("#frostNavEditor");
  if (!overlay) return;
  overlay.classList.remove("open");
  overlay.hidden = true;
}

function openFrostEditor() {
  applyFrostStrength();
  const overlay = $("#frostNavEditor");
  if (!overlay) return;
  overlay.hidden = false;
  requestAnimationFrame(() => overlay.classList.add("open"));
}

function bindFrostEditor() {
  $("#frostNavEdit")?.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    openFrostEditor();
  });
  $("#frostNavEditorClose")?.addEventListener("click", closeFrostEditor);
  $("#frostNavEditReset")?.addEventListener("click", () => {
    settings.frostStrength = FROST_STRENGTH_DEFAULT;
    settings.frostLogs = false;
    const frostLogsToggle = document.querySelector("[data-setting='frostLogs']");
    if (frostLogsToggle) frostLogsToggle.checked = false;
    document.body.classList.toggle("frost-logs", false);
    applyFrostStrength();
    saveSettings();
  });
  $("#frostStrength")?.addEventListener("input", event => {
    settings.frostStrength = Math.min(10, Math.max(1, Math.round(Number(event.target.value) || FROST_STRENGTH_DEFAULT)));
    applyFrostStrength();
    saveSettings();
  });
  $("#frostNavEditor")?.addEventListener("click", event => {
    if (event.target.id !== "frostNavEditor") return;
    closeFrostEditor();
  });
  document.addEventListener("keydown", event => {
    if (event.key !== "Escape" || !frostEditorIsOpen()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    closeFrostEditor();
  });
}

async function loadPersistedSettings() {
  let local = {};
  let file = {};
  try { local = JSON.parse(localStorage.getItem("argusUi") || "{}"); } catch { local = {}; }
  if (window.pywebview?.api?.load_ui_settings) {
    try {
      const result = await window.pywebview.api.load_ui_settings();
      if (result?.ok && result.data && typeof result.data === "object") file = result.data;
    } catch { /* keep localStorage copy */ }
  }
  const persisted = { ...local, ...file };
  if (Number(persisted.frostScale) !== FROST_SCALE) {
    if (Object.prototype.hasOwnProperty.call(persisted, "frostStrength")) {
      const old = Math.min(10, Math.max(1, Math.round(Number(persisted.frostStrength) || 5)));
      persisted.frostStrength = Math.min(10, old + 3);
    }
    persisted.frostScale = FROST_SCALE;
  }
  settings = { ...defaultSettings, ...persisted };
  if (settings.theme === "ice") settings.theme = "violet";
  if (!isKnownTheme(settings.theme)) settings.theme = "omega";
  settings.themeSpeed = Math.min(10, Math.max(1, Math.round(Number(settings.themeSpeed) || 5)));
  settings.frostStrength = Math.min(10, Math.max(1, Math.round(Number(settings.frostStrength) || FROST_STRENGTH_DEFAULT)));
  settings.frostScale = FROST_SCALE;
  settings.staticGradients = Boolean(settings.staticGradients);
  settings.bgFx = normalizeBgFx(settings.bgFx);
  settings.bgFxOpts = normalizeBgFxOpts(settings.bgFxOpts);
  settings.autoHideNav = Boolean(settings.autoHideNav);
  settings.altNav = Boolean(settings.altNav);
  settings.frostNav = settings.frostNav !== false;
  settings.compactNav = Boolean(settings.compactNav);
  settings.reduceMotion = Boolean(settings.reduceMotion);
  settings.customStatic = normalizeCustomTheme(settings.customStatic, "custom");
  settings.customAnim = normalizeCustomTheme(settings.customAnim, "custom-anim");
}

function hydrateSettings() {
  $$("[data-setting]").forEach(input => { input.checked = Boolean(settings[input.dataset.setting]); });
  document.body.classList.toggle("reduce-motion", settings.reduceMotion);
  syncNavLayout();
  applyFrostStrength();
  syncThemeSpeedUi();
  syncStaticGradients();
  applyTheme(settings.theme, false);
  syncBgFx();
  paintAllRangeFills();
  requestAnimationFrame(paintAllRangeFills);
}

function applyFrostStrength() {
  const n = Math.min(10, Math.max(1, Math.round(Number(settings.frostStrength) || FROST_STRENGTH_DEFAULT)));
  settings.frostStrength = n;
  const blur = Math.max(0, 4 + (n - 4) * 4);
  const shift = (n - 8) * 0.04;
  const clampFill = value => Math.min(0.94, Math.max(0.22, value)).toFixed(2);
  const root = document.documentElement.style;
  root.setProperty("--nav-frost", `${blur}px`);
  root.setProperty("--nav-frost-fill", clampFill(0.78 - shift));
  root.setProperty("--nav-frost-fill-fx", clampFill(0.62 - shift));
  root.setProperty("--nav-frost-alt", clampFill(0.62 - shift));
  root.setProperty("--nav-frost-alt-fx", clampFill(0.5 - shift));
  root.setProperty("--nav-frost-sheet", clampFill(0.78 - shift));
  const slider = $("#frostStrength");
  const output = $("#frostStrengthValue");
  if (slider && slider.value !== String(n)) slider.value = String(n);
  if (output) output.textContent = String(n);
  if (slider) paintRangeFill(slider);
}

let altNavHitObs = null;
function syncAltNavHit() {
  const bar = document.querySelector(".alt-nav-bar");
  const root = document.documentElement.style;
  if (!settings.altNav || !settings.autoHideNav || !bar) {
    altNavHitObs?.disconnect();
    altNavHitObs = null;
    root.removeProperty("--alt-nav-hit-w");
    return;
  }
  const apply = () => {
    if (!settings.altNav || !settings.autoHideNav) return;
    const width = Math.round(bar.getBoundingClientRect().width + 240);
    root.setProperty("--alt-nav-hit-w", `${Math.max(400, width)}px`);
  };
  apply();
  requestAnimationFrame(apply);
  if (!altNavHitObs) altNavHitObs = new ResizeObserver(apply);
  altNavHitObs.disconnect();
  altNavHitObs.observe(bar);
}

function syncNavLayout() {
  document.body.classList.toggle("alt-nav", Boolean(settings.altNav));
  document.body.classList.toggle("frost-nav", settings.frostNav !== false);
  document.body.classList.toggle("compact-nav", Boolean(settings.compactNav));
  document.body.classList.toggle("auto-hide-nav", Boolean(settings.autoHideNav));
  if (!settings.autoHideNav || settings.altNav) document.body.classList.remove("nav-reveal");
  syncAltNavHit();
}

function bindSidebarAutoHide() {
  const EDGE = 120;
  const update = event => {
    if (!settings.autoHideNav || settings.altNav) {
      document.body.classList.remove("nav-reveal");
      return;
    }
    const titleH = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--titlebar-h")) || 38;
    const sidebar = document.querySelector(".sidebar");
    const right = sidebar ? sidebar.getBoundingClientRect().right : 0;
    if (event.clientY < titleH) {
      if (event.clientX > Math.max(right, 0)) document.body.classList.remove("nav-reveal");
      return;
    }
    const near = event.clientX <= EDGE;
    const over = event.clientX >= 0 && event.clientX <= Math.max(right, 0);
    document.body.classList.toggle("nav-reveal", near || over);
  };
  document.addEventListener("mousemove", update, { passive: true });
  document.addEventListener("mouseleave", () => document.body.classList.remove("nav-reveal"));
}

function bindWindowChrome() {
  $("#winMin")?.addEventListener("click", () => {
    window.pywebview?.api?.window_minimize?.();
  });
  $("#winMax")?.addEventListener("click", () => { toggleWindowMax(); });
  $("#winClose")?.addEventListener("click", () => {
    window.pywebview?.api?.window_close?.();
  });

  const drag = $(".titlebar-drag");
  let dragState = null;
  let dragRaf = 0;
  let dragPos = null;
  const endDrag = () => {
    dragState = null;
    dragPos = null;
    if (dragRaf) cancelAnimationFrame(dragRaf);
    dragRaf = 0;
  };
  drag?.addEventListener("pointerdown", async event => {
    if (event.button !== 0 || event.target.closest(".win-btn, button, select, #titlebar-game, #titlebar-configs, #configMenu")) return;
    if (document.body.classList.contains("is-max")) return;
    event.preventDefault();
    try { drag.setPointerCapture(event.pointerId); } catch { /* ignore */ }
    let geo = null;
    try { geo = await window.pywebview?.api?.window_geometry?.(); } catch { geo = null; }
    if (!geo?.ok) return;
    if ((event.buttons & 1) === 0) return;
    dragState = { id: event.pointerId, mx: event.screenX, my: event.screenY, x: geo.x, y: geo.y };
  });
  drag?.addEventListener("pointermove", event => {
    if (!dragState || event.pointerId !== dragState.id) return;
    if ((event.buttons & 1) === 0) { endDrag(); return; }
    dragPos = {
      x: dragState.x + (event.screenX - dragState.mx),
      y: dragState.y + (event.screenY - dragState.my)
    };
    if (dragRaf) return;
    dragRaf = requestAnimationFrame(() => {
      dragRaf = 0;
      if (!dragState || !dragPos) return;
      window.pywebview?.api?.window_move?.(dragPos.x, dragPos.y);
    });
  });
  drag?.addEventListener("pointerup", endDrag);
  drag?.addEventListener("pointercancel", endDrag);
  drag?.addEventListener("lostpointercapture", endDrag);
  drag?.addEventListener("dblclick", (event) => {
    if (event.target.closest("#titlebar-game, #titlebar-configs, button, select")) return;
    toggleWindowMax();
  });

  $$(".win-resize").forEach(handle => {
    let resizeState = null;
    let resizeRaf = 0;
    let resizePos = null;
    const endResize = () => {
      resizeState = null;
      resizePos = null;
      if (resizeRaf) cancelAnimationFrame(resizeRaf);
      resizeRaf = 0;
    };
    handle.addEventListener("pointerdown", async event => {
      if (event.button !== 0) return;
      if (document.body.classList.contains("is-max")) return;
      event.preventDefault();
      event.stopPropagation();
      try { handle.setPointerCapture(event.pointerId); } catch { /* ignore */ }
      let geo = null;
      try { geo = await window.pywebview?.api?.window_geometry?.(); } catch { geo = null; }
      if (!geo?.ok) return;
      if ((event.buttons & 1) === 0) return;
      resizeState = {
        id: event.pointerId,
        edge: handle.dataset.edge || "se",
        mx: event.screenX,
        my: event.screenY,
        x: geo.x,
        y: geo.y,
        width: geo.width,
        height: geo.height
      };
    });
    handle.addEventListener("pointermove", event => {
      if (!resizeState || event.pointerId !== resizeState.id) return;
      if ((event.buttons & 1) === 0) { endResize(); return; }
      const dx = event.screenX - resizeState.mx;
      const dy = event.screenY - resizeState.my;
      const edge = resizeState.edge;
      let x = resizeState.x;
      let y = resizeState.y;
      let width = resizeState.width;
      let height = resizeState.height;
      if (edge.includes("e")) width = resizeState.width + dx;
      if (edge.includes("s")) height = resizeState.height + dy;
      if (edge.includes("w")) { x = resizeState.x + dx; width = resizeState.width - dx; }
      if (edge.includes("n")) { y = resizeState.y + dy; height = resizeState.height - dy; }
      resizePos = { x, y, width, height };
      if (resizeRaf) return;
      resizeRaf = requestAnimationFrame(() => {
        resizeRaf = 0;
        if (!resizeState || !resizePos) return;
        window.pywebview?.api?.window_set_bounds?.(resizePos.x, resizePos.y, resizePos.width, resizePos.height);
      });
    });
    handle.addEventListener("pointerup", endResize);
    handle.addEventListener("pointercancel", endResize);
    handle.addEventListener("lostpointercapture", endResize);
  });
}

function syncMaxButton(maximized) {
  const btn = $("#winMax");
  if (!btn) return;
  btn.classList.toggle("is-max", Boolean(maximized));
  btn.setAttribute("aria-label", maximized ? "Restore" : "Maximize");
  document.body.classList.toggle("is-max", Boolean(maximized));
}

async function pullWindowChrome() {
  try {
    const chrome = await window.pywebview?.api?.window_state?.();
    if (chrome && typeof chrome.maximized === "boolean") syncMaxButton(chrome.maximized);
  } catch { /* native bridge not ready */ }
}

function watchWindowChrome() {
  pullWindowChrome();
  [80, 200, 400, 700, 1100, 1600].forEach(ms => setTimeout(pullWindowChrome, ms));
}

async function toggleWindowMax() {
  if (!window.pywebview?.api?.window_toggle_max) return;
  const result = await window.pywebview.api.window_toggle_max();
  syncMaxButton(result?.maximized);
}

function bindThemeUi() {
  document.addEventListener("input", event => {
    if (event.target instanceof HTMLInputElement && event.target.type === "range") {
      paintRangeFill(event.target);
    }
  });
  window.addEventListener("resize", paintAllRangeFills);
  $$(".theme-option[data-theme]").forEach(button => button.addEventListener("click", () => applyTheme(button.dataset.theme)));
  $$("[data-bg-fx]").forEach(button => button.addEventListener("click", () => setBgFx(button.dataset.bgFx)));
  bindThemeEditor();
  bindBgFxEditor();
  bindFrostEditor();
  $("#themeSpeed")?.addEventListener("input", e => {
    settings.themeSpeed = Math.min(10, Math.max(1, Number(e.target.value) || 5));
    syncThemeSpeedUi();
    if (isAnimatedTheme()) startThemeAnim();
    saveSettings();
  });
  $$("[data-setting]").forEach(input => input.addEventListener("change", e => {
    settings[e.target.dataset.setting] = e.target.checked;
    document.body.classList.toggle("reduce-motion", settings.reduceMotion);
    syncNavLayout();
    if (isAnimatedTheme()) {
      if (settings.reduceMotion) stopThemeAnim();
      else startThemeAnim();
    }
    syncBgFx();
    syncThemeSpeedUi();
    syncStaticGradients();
    saveSettings();
  }));
  $("#resetSettings")?.addEventListener("click", () => {
    settings = {
      ...defaultSettings,
      customStatic: defaultCustomTheme("custom"),
      customAnim: defaultCustomTheme("custom-anim"),
      bgFxOpts: {}
    };
    hydrateSettings();
    saveSettings();
  });
}

async function initTheme() {
  await whenNativeReady();
  await loadPersistedSettings();
  bindThemeUi();
  bindWindowChrome();
  bindSidebarAutoHide();
  hydrateSettings();
  watchWindowChrome();
}

window.ArgusTheme = { init: initTheme, syncAltNavHit, paintRangeFill, paintAllRangeFills };
})();
