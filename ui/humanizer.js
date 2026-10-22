/* Humanizer: a 5-minute aim session that records how you aim.
 *
 * Five parts, 60 s each: Gridshot, Flicking, Reaction, Tracking, Click
 * reaction. The session runs in a full-window stage with pointer lock, so the
 * mouse turns the view the way it does in a game and the raw movement is what
 * gets recorded. When the last part ends the run is analysed here (the
 * high-rate samples never leave the menu), abnormal values are dropped with
 * Tukey's fences, and the resulting profile plus a compact replay are saved
 * through humanizer.py.
 *
 * A warm-up is the same five parts at 20 s each. It is for getting the hands
 * going before a game: it shows how the warm-up went next to the profile and
 * saves nothing.
 *
 * A profile is required: with none, one from an older layout, or one older
 * than two weeks, the menu keeps the user on this page until a run is done.
 * Nothing here reads or changes the aim modules.
 */
(() => {
  const PHASE_INFO = {
    gridshot: { label: "Gridshot", blurb: "Three targets at a time. Click them as fast as you can.", icon: "grid" },
    flicking: { label: "Flicking", blurb: "One small target at a time, anywhere around you. Snap to it and click.", icon: "flick" },
    reaction: { label: "Reaction", blurb: "A ball starts on your crosshair and slides left or right. Follow it and click it, then come back to the centre ring.", icon: "react" },
    tracking: { label: "Tracking", blurb: "Keep your crosshair on the moving target. No clicking needed.", icon: "track" },
    click: { label: "Click reaction", blurb: "A red circle sits in the middle. Keep your crosshair on it, and click the moment it turns green. Clicking early starts it over.", icon: "click" },
  };
  const PHASE_KEYS = ["gridshot", "flicking", "reaction", "tracking", "click"];
  const makeLayout = (ms) => PHASE_KEYS.map((key, i) => ({ key, start: i * ms, end: (i + 1) * ms }));
  const PHASE_MS = 60000;
  const WARMUP_PHASE_MS = 20000;
  const LAYOUT = makeLayout(PHASE_MS);
  const WARMUP_LAYOUT = makeLayout(WARMUP_PHASE_MS);
  // Runs saved before the Reaction part existed: three 100 s parts.
  const LEGACY_LAYOUT = ["gridshot", "flicking", "tracking"].map((key, i) => ({ key, start: i * 100000, end: (i + 1) * 100000 }));
  const TOTAL_MS = LAYOUT[LAYOUT.length - 1].end;
  const SESSION_MIN = Math.round(TOTAL_MS / 60000);
  const layoutEnd = (layout) => layout[layout.length - 1].end;
  const PROFILE_VERSION = 2;
  const COUNTDOWN_MS = 3000;
  const GRID_R = 26;
  const FLICK_R = 19;
  const BALL_R = 22;
  const TRACK_R = 24;
  const CENTRE_R = 28;
  const CLICK_R = 46;
  // Click reaction: the circle stays red this long before turning green, a click
  // on green after this long counts as a miss, and a fresh circle appears after a
  // short pause.
  const CLICK_WAIT_MS = [1000, 3200];
  const CLICK_TIMEOUT_MS = 1500;
  const CLICK_GAP_MS = [350, 700];
  // Below this after green is a guess made before it showed, not a reaction.
  const CLICK_MIN_RT_MS = 100;
  const CLICK_RGB = { wait: "255, 84, 96", go: "78, 230, 132" };
  const FLICK_TIMEOUT_MS = 2600;
  const AIM_KEY = "argusHumanizerAim";
  const SOUND_KEY = "argusHumanizerSound";
  const CM_MIN = 5;
  const CM_MAX = 200;
  // Mouse events stop while the mouse is still, so a longer gap between two
  // samples means the hand was at rest in between.
  const REST_GAP_MS = 20;

  // key, label, unit, digits, group, description, discrete
  const METRICS = [
    ["reaction_ms", "Reaction time", "ms", 0, "Reaction", "From a Reaction ball starting to move to your mouse starting to follow it."],
    ["catch_ms", "Catch time", "ms", 0, "Reaction", "From a Reaction ball starting to move to clicking it."],
    ["click_rt_ms", "Click reaction", "ms", 0, "Click reaction", "From the circle turning green to clicking it. This is what the trigger's Humanized delay draws from."],
    ["flick_ms", "Flick time", "ms", 0, "Flicks", "From starting to move toward a target to clicking it."],
    ["peak_speed", "Peak flick speed", "px/s", 0, "Flicks", "Fastest point of each flick."],
    ["time_to_peak", "Time to peak speed", "%", 0, "Flicks", "How far through a flick top speed is reached. People tend to peak early and brake for longer."],
    ["overshoot_pct", "Overshoot", "%", 1, "Flicks", "How far past the target a flick travelled, as a share of its distance."],
    ["curve_pct", "Path curve", "%", 1, "Flicks", "Largest sideways drift from a straight line, as a share of the distance."],
    ["click_offset_px", "Click offset", "px", 1, "Flicks", "Distance from the target's centre when you clicked it."],
    ["corrections", "Corrections per flick", "", 2, "Flicks", "Extra small movements after the main flick before clicking.", true],
    ["click_hold_ms", "Click hold", "ms", 0, "Clicks", "How long the mouse button stays down on a click."],
    ["throughput", "Throughput", "bits/s", 2, "Flicks", "Fitts's law: how much target difficulty you clear per second."],
    ["track_error_px", "Tracking error", "px", 1, "Tracking", "Average distance from the moving target's centre."],
    ["on_target_pct", "Time on target", "%", 1, "Tracking", "Share of the time the crosshair was inside the target."],
    ["track_lag_ms", "Tracking lag", "ms", 0, "Tracking", "How far behind the target your crosshair runs, in time."],
    ["jitter_px", "Micro-jitter", "px", 2, "Tracking", "Small, fast wobble in your aim on top of the smooth motion."],
  ];
  const METRIC = new Map(METRICS.map((m) => [m[0], { key: m[0], label: m[1], unit: m[2], digits: m[3], group: m[4], desc: m[5], discrete: Boolean(m[6]) }]));
  const OVERVIEW_KEYS = ["reaction_ms", "click_rt_ms", "flick_ms", "peak_speed", "overshoot_pct", "curve_pct", "click_offset_px", "track_lag_ms", "jitter_px", "catch_ms"];

  const ICONS = {
    grid: '<svg viewBox="0 0 24 24"><circle cx="6" cy="7" r="2.4" /><circle cx="17" cy="9" r="2.4" /><circle cx="10" cy="17" r="2.4" /></svg>',
    flick: '<svg viewBox="0 0 24 24"><circle cx="18" cy="6" r="2.6" /><path d="M4 19 15.5 8.5" /><path d="M4 14v5h5" /></svg>',
    react: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="2.6" /><path d="M3 12h4M17 12h4" /><path d="m5.5 9.5-2.5 2.5 2.5 2.5M18.5 9.5l2.5 2.5-2.5 2.5" /></svg>',
    track: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" /><path d="M3 12h3M18 12h3M12 3v3M12 18v3" /><path d="M16.5 7.5c2 1.3 3 2.9 3 4.5" /></svg>',
    click: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2.4" /><path d="M12 2v3M2 12h3" /></svg>',
    warm: '<svg viewBox="0 0 24 24"><path d="M12 3c1 3.2 4.5 4.6 4.5 9a4.5 4.5 0 0 1-9 0c0-1.7.8-2.8 1.6-3.8.4 1.2 1 1.8 1.8 2 0-2.4.2-4.2 1.1-7.2Z" /><path d="M9.5 20h5" /></svg>',
    play: '<svg viewBox="0 0 24 24" class="hz-fill"><path d="M8 5.5v13l10.5-6.5z" /></svg>',
    pause: '<svg viewBox="0 0 24 24"><path d="M8 5.5v13M16 5.5v13" /></svg>',
    replay: '<svg viewBox="0 0 24 24"><path d="M4 12a8 8 0 1 0 2.3-5.7" /><path d="M4 4v5h5" /><path d="M10.5 9.5v5l4-2.5z" /></svg>',
    rerun: '<svg viewBox="0 0 24 24"><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></svg>',
    stats: '<svg viewBox="0 0 24 24"><path d="M4 20V11M10 20V5M16 20v-6M21 20H3" /></svg>',
    trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16" /><path d="M10 11v6M14 11v6" /><path d="M6 7l1 13h10l1-13" /><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /></svg>',
    lock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>',
  };

  const el = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const notify = (message, kind = "info") => { if (typeof toast === "function") toast(message, kind); };
  const rand = (a, b) => a + Math.random() * (b - a);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const r1 = (v) => Math.round(v * 10) / 10;
  const label = (key) => PHASE_INFO[key]?.label || key;

  const page = {
    profile: null, runs: [], loaded: false, loading: false, intro: false, stats: false,
    required: false, reason: null, dueAt: null, rerunDays: 14,
    paths: null, deleting: null,
  };

  /* Sensitivity is set as cm of mouse travel per 360 degree turn, at the
   * mouse's DPI, so it is the same number in any game. The session view uses
   * a standard FOV (90 degrees at 4:3, about 106 at 16:9, so the horizontal
   * FOV follows the window shape), which makes pixels per degree at the
   * crosshair depend on height only. */
  const aim = readAim();

  function readAim() {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(AIM_KEY) || "null"); } catch { /* storage unavailable */ }
    const dpi = Number(saved?.dpi);
    const cm = Number(saved?.cm360);
    return {
      dpi: Number.isFinite(dpi) && dpi >= 100 ? clamp(dpi, 100, 32000) : 800,
      cm360: Number.isFinite(cm) && cm > 0 ? clamp(cm, CM_MIN, CM_MAX) : 40,
    };
  }

  function saveAim() {
    try { localStorage.setItem(AIM_KEY, JSON.stringify(aim)); } catch { /* storage unavailable */ }
  }

  function pxPerDeg(viewH) {
    return (viewH / 1.5) * (Math.PI / 180);
  }

  // World pixels the view turns per mouse count.
  function pxPerCount(viewH, a = aim) {
    const countsPer360 = (a.cm360 / 2.54) * a.dpi;
    return (360 / countsPer360) * pxPerDeg(viewH);
  }

  function aimSummary(a = aim) {
    return `${Number(a.cm360).toFixed(1)} cm/360 at ${Math.round(a.dpi)} DPI`;
  }

  /* Sounds, made with Web Audio so there are no files to load: a low
   * "thock" on every shot and a warm chime on every hit that climbs a
   * semitone per hit in a row (up to an octave) and drops back on a miss.
   * Everything runs through a gentle top roll-off, a short room echo and a
   * soft compressor so it sounds full rather than tinny. The audio context
   * starts on the Start / Test click (browsers only allow sound after a
   * user gesture). */
  const COMBO_MAX_STEPS = 12;
  const sound = (() => {
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(SOUND_KEY) || "null"); } catch { /* storage unavailable */ }
    const vol = Number(saved?.vol);
    const cfg = { on: saved?.on !== false, vol: Number.isFinite(vol) ? clamp(vol, 0, 1) : 0.6 };
    let ctx = null;
    let master = null;
    let tone = null;
    let noise = null;

    function ensure() {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      if (!ctx) {
        try { ctx = new AC({ latencyHint: "interactive" }); } catch { return null; }
        // tone (roll-off) -> master (volume) -> compressor -> speakers,
        // with a little of tone fed through a short, darkened echo.
        const comp = ctx.createDynamicsCompressor();
        comp.threshold.value = -16;
        comp.knee.value = 12;
        comp.ratio.value = 4;
        comp.attack.value = 0.002;
        comp.release.value = 0.12;
        comp.connect(ctx.destination);
        master = ctx.createGain();
        master.connect(comp);
        tone = ctx.createBiquadFilter();
        tone.type = "lowpass";
        tone.frequency.value = 4200;
        tone.Q.value = 0.5;
        tone.connect(master);
        const delay = ctx.createDelay(0.2);
        delay.delayTime.value = 0.043;
        const feedback = ctx.createGain();
        feedback.gain.value = 0.3;
        const dark = ctx.createBiquadFilter();
        dark.type = "lowpass";
        dark.frequency.value = 2000;
        const wet = ctx.createGain();
        wet.gain.value = 0.22;
        tone.connect(delay);
        delay.connect(dark).connect(feedback).connect(delay);
        dark.connect(wet).connect(master);
        noise = ctx.createBuffer(1, Math.round(ctx.sampleRate * 0.08), ctx.sampleRate);
        const data = noise.getChannelData(0);
        for (let i = 0; i < data.length; i += 1) data[i] = Math.random() * 2 - 1;
      }
      if (ctx.state === "suspended") ctx.resume().catch(() => { /* stays silent */ });
      master.gain.value = cfg.vol;
      return ctx;
    }

    // Envelope: near-silent -> peak in `attack` s -> fades out by `end` s.
    function envelope(t, peak, attack, end) {
      const g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(peak, t + attack);
      g.gain.exponentialRampToValueAtTime(0.0001, t + end);
      g.connect(tone);
      return g;
    }

    function noiseBurst(t, filters, peak, end) {
      const src = ctx.createBufferSource();
      src.buffer = noise;
      let node = src;
      filters.forEach(([type, freq, q]) => {
        const f = ctx.createBiquadFilter();
        f.type = type;
        f.frequency.value = freq;
        f.Q.value = q;
        node = node.connect(f);
      });
      node.connect(envelope(t, peak, 0.0008, end));
      src.start(t);
      src.stop(t + end + 0.02);
    }

    function sweep(t, from, to, over, peak, end) {
      const osc = ctx.createOscillator();
      osc.type = "sine";
      osc.frequency.setValueAtTime(from, t);
      osc.frequency.exponentialRampToValueAtTime(to, t + over);
      osc.connect(envelope(t, peak, 0.002, end));
      osc.start(t);
      osc.stop(t + end + 0.02);
    }

    function click(t) {
      // Mid "thock" from filtered noise, a low thump, and a whisper of top
      // end so it stays defined.
      noiseBurst(t, [["highpass", 280, 0.7], ["lowpass", 1900 * rand(0.94, 1.06), 0.8]], 0.34, 0.032);
      sweep(t, 150, 62, 0.04, 0.42, 0.06);
      noiseBurst(t, [["highpass", 3600, 0.7]], 0.045, 0.01);
    }

    function hit(t, streak) {
      const steps = Math.min(Math.max(1, streak) - 1, COMBO_MAX_STEPS);
      const f0 = 523.25 * 2 ** (steps / 12) * rand(0.997, 1.003);  // C5 upward
      // Fundamental plus a slightly detuned twin for warmth, and soft
      // overtones that fade faster, each starting a touch sharp (a pluck).
      [[1, 0.22, 0.4], [1.004, 0.09, 0.36], [2, 0.07, 0.2], [3, 0.022, 0.12]].forEach(([mul, peak, end]) => {
        const osc = ctx.createOscillator();
        osc.type = "sine";
        osc.frequency.setValueAtTime(f0 * mul * 1.012, t);
        osc.frequency.exponentialRampToValueAtTime(f0 * mul, t + 0.03);
        osc.connect(envelope(t, peak, 0.003, end));
        osc.start(t);
        osc.stop(t + end + 0.02);
      });
      sweep(t, 190, 95, 0.06, 0.16, 0.09);
    }

    function play(kind, streak = 1) {
      if (!cfg.on || cfg.vol <= 0 || !ensure()) return;
      const t = ctx.currentTime + 0.001;
      if (kind === "click") click(t);
      else if (kind === "hit") hit(t, streak);
    }

    function save() {
      try { localStorage.setItem(SOUND_KEY, JSON.stringify(cfg)); } catch { /* storage unavailable */ }
      if (master) master.gain.value = cfg.vol;
    }

    return { cfg, ensure, play, save };
  })();

  // The session and the sensitivity test take over the whole monitor; the
  // app puts the window back as it was afterwards.
  function fullscreen(on) {
    try {
      const p = api()?.humanizer_fullscreen?.(Boolean(on));
      if (p && typeof p.catch === "function") p.catch(() => { /* stays windowed */ });
    } catch { /* stays windowed */ }
  }

  /* Pointer lock with raw, unaccelerated counts where the browser offers it.
   * `onRaw(bool)` reports which one was granted. */
  function lockPointer(onRaw) {
    const plain = () => {
      onRaw(false);
      try {
        const p = stage.canvas.requestPointerLock();
        if (p && typeof p.catch === "function") p.catch(() => { /* needs a click */ });
      } catch { /* needs a click */ }
    };
    try {
      const p = stage.canvas.requestPointerLock({ unadjustedMovement: true });
      if (p && typeof p.then === "function") p.then(() => onRaw(true), plain);
      else onRaw(true);
    } catch {
      plain();
    }
  }

  function accentRgb() {
    const raw = getComputedStyle(document.body).getPropertyValue("--accent-rgb").trim();
    return raw || "121, 242, 176";
  }

  function fmt(key, value) {
    const m = METRIC.get(key);
    if (!m || value == null || !Number.isFinite(Number(value))) return "—";
    let v = Number(value);
    if (Math.abs(v) < 0.5 * 10 ** -m.digits) v = 0;  // no "-0"
    const text = v.toLocaleString("en-US", { minimumFractionDigits: m.digits, maximumFractionDigits: m.digits });
    if (!m.unit) return text;
    return m.unit === "%" ? `${text}%` : `${text} ${m.unit}`;
  }

  function mmss(ms) {
    const s = Math.max(0, Math.ceil(ms / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function phaseAt(layout, t) {
    for (let i = 0; i < layout.length; i += 1) if (t < layout[i].end) return i;
    return layout.length - 1;
  }

  // ================================================================ statistics

  function quantile(sorted, q) {
    if (!sorted.length) return NaN;
    const pos = (sorted.length - 1) * q;
    const lo = Math.floor(pos);
    const hi = Math.ceil(pos);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
  }

  /* Drop abnormally high or low values with Tukey's fences (outside 1.5x the
   * interquartile range), then describe what is left. Small whole-number
   * counts barely spread, so for those only absurd highs are dropped. */
  function summarize(values, discrete = false, keepValues = false) {
    const clean = values.filter(Number.isFinite).sort((a, b) => a - b);
    let kept = clean;
    if (clean.length >= 4) {
      const q1 = quantile(clean, 0.25);
      const q3 = quantile(clean, 0.75);
      const iqr = q3 - q1;
      const lo = discrete ? -Infinity : q1 - 1.5 * iqr;
      const hi = discrete ? Math.max(q3 + 3, quantile(clean, 0.98)) : q3 + 1.5 * iqr;
      if (discrete || iqr > 0) kept = clean.filter((v) => v >= lo && v <= hi);
    }
    const n = kept.length;
    const out = { n, removed: clean.length - n, median: null, mean: null, std: null, p10: null, p90: null, hist: [] };
    // The trigger draws from these, so they are stored, not just described.
    if (keepValues) out.values = kept.slice(0, 200).map((v) => Math.round(v * 10) / 10);
    if (!n) return out;
    const mean = kept.reduce((a, b) => a + b, 0) / n;
    const std = Math.sqrt(kept.reduce((a, b) => a + (b - mean) ** 2, 0) / n);
    Object.assign(out, {
      median: quantile(kept, 0.5),
      mean,
      std,
      p10: quantile(kept, 0.1),
      p90: quantile(kept, 0.9),
      min: kept[0],
      max: kept[n - 1],
    });
    const bins = 12;
    const span = kept[n - 1] - kept[0];
    out.hist = new Array(bins).fill(0);
    kept.forEach((v) => {
      const i = span > 0 ? Math.min(bins - 1, Math.floor(((v - kept[0]) / span) * bins)) : Math.floor(bins / 2);
      out.hist[i] += 1;
    });
    return out;
  }

  // ================================================================ analysis

  function lastIndexAtOrBefore(arr, t) {
    let lo = 0;
    let hi = arr.length - 1;
    if (hi < 0 || arr[0] > t) return -1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (arr[mid] <= t) lo = mid;
      else hi = mid - 1;
    }
    return lo;
  }

  function firstIndexAtOrAfter(arr, t) {
    const i = lastIndexAtOrBefore(arr, t);
    return i >= 0 && arr[i] === t ? i : i + 1;
  }

  function interp(ts, xs, ys, t) {
    const i = lastIndexAtOrBefore(ts, t);
    if (i < 0) return [xs[0], ys[0]];
    if (i >= ts.length - 1 || xs[i + 1] == null) return [xs[i], ys[i]];
    const span = ts[i + 1] - ts[i];
    const f = span > 0 ? (t - ts[i]) / span : 0;
    return [xs[i] + (xs[i + 1] - xs[i]) * f, ys[i] + (ys[i + 1] - ys[i]) * f];
  }

  // Where a target was at time t. Reaction balls slide at a fixed speed.
  function posAt(tgt, t) {
    return tgt.vx ? [tgt.x + (tgt.vx * (t - tgt.t)) / 1000, tgt.y] : [tgt.x, tgt.y];
  }

  function analyze(rec) {
    const S = rec.samples;
    const n = S.t.length;
    const layout = rec.layout || LAYOUT;
    const keyOf = (p) => layout[p]?.key;

    // Velocity over a ~10 ms window centred on each sample: raw per-event
    // deltas at 1000 Hz are too noisy to read directly, and a centred window
    // does not shift movement onsets later.
    const vx = new Float64Array(n);
    const vy = new Float64Array(n);
    const speed = new Float64Array(n);
    let j = 0;
    let k = 0;
    for (let i = 0; i < n; i += 1) {
      while (j < i && S.t[i] - S.t[j] > 5) j += 1;
      if (k < i) k = i;
      while (k + 1 < n && S.t[k + 1] - S.t[i] <= 5) k += 1;
      const dt = S.t[k] - S.t[j];
      if (dt > 0) {
        vx[i] = ((S.x[k] - S.x[j]) / dt) * 1000;
        vy[i] = ((S.y[k] - S.y[j]) / dt) * 1000;
      } else if (i > 0 && S.t[i] - S.t[i - 1] <= REST_GAP_MS) {
        vx[i] = vx[i - 1];
        vy[i] = vy[i - 1];
      }
      speed[i] = Math.hypot(vx[i], vy[i]);
    }

    // Speed smoothed over +-8 ms, for finding peaks and sub-movements
    // without tripping over whole-count steps.
    const sm = new Float64Array(n);
    {
      const pre = new Float64Array(n + 1);
      for (let i = 0; i < n; i += 1) pre[i + 1] = pre[i] + speed[i];
      let lo = 0;
      let hi = 0;
      for (let i = 0; i < n; i += 1) {
        while (S.t[i] - S.t[lo] > 8) lo += 1;
        if (hi < i) hi = i;
        while (hi + 1 < n && S.t[hi + 1] - S.t[i] <= 8) hi += 1;
        sm[i] = (pre[hi + 1] - pre[lo]) / (hi + 1 - lo);
      }
    }
    // Speed at an arbitrary time: zero inside a rest gap.
    const speedAt = (t) => {
      const i = lastIndexAtOrBefore(S.t, t);
      return i >= 0 && t - S.t[i] <= REST_GAP_MS ? sm[i] : 0;
    };

    /* Start of the movement that reaches index `peak` along (ux, uy): walk
     * back while each count still stepped that way without a rest gap, so
     * the onset is the first count of the movement. Returns -1 when the hand
     * was already moving that way at index `from`. */
    const onsetOf = (peak, from, ux, uy) => {
      const stepAlong = (i) => (S.x[i] - S.x[i - 1]) * ux + (S.y[i] - S.y[i - 1]) * uy;
      const joined = (i) => i > 0 && S.t[i] - S.t[i - 1] <= REST_GAP_MS;
      let o = peak;
      while (o > from && joined(o) && stepAlong(o) > 0) o -= 1;
      if (o === from && joined(from) && stepAlong(from) > 0) return -1;
      return o;
    };

    const targets = new Map();
    const removedAt = new Map();
    rec.events.forEach((e) => {
      if (e.k === "s") targets.set(e.id, e);
      if (e.k === "r") removedAt.set(e.id, e.t);
    });

    const raw = Object.fromEntries(METRICS.map((m) => [m[0], []]));
    const profileBins = new Array(20).fill(0);
    let profileCount = 0;
    const stats = Object.fromEntries(layout.map((p) => [p.key, { hits: 0, clicks: 0, timeouts: 0, falseStarts: 0 }]));
    const lastHit = layout.map((p) => p.start);

    rec.events.forEach((e) => {
      const key = keyOf(e.p);
      const ps = stats[key];
      if (!ps) return;
      if (e.k === "r" && e.how === "timeout") ps.timeouts += 1;
      if (e.k !== "c" || key === "tracking") return;
      ps.clicks += 1;
      if (e.early) ps.falseStarts += 1;
      if (Number.isFinite(e.hold)) raw.click_hold_ms.push(e.hold);
      if (e.hit == null) return;
      ps.hits += 1;
      const tgt = targets.get(e.hit);
      if (!tgt) return;
      if (key === "click") {
        // Green to click. A "reaction" under CLICK_MIN_RT_MS was decided
        // before the circle changed, so it says nothing about reacting.
        const rt = e.t - tgt.go;
        if (Number.isFinite(rt) && rt >= CLICK_MIN_RT_MS && rt <= CLICK_TIMEOUT_MS) raw.click_rt_ms.push(rt);
        return;
      }
      if (key === "reaction") {
        raw.catch_ms.push(e.t - tgt.t);
        return;
      }
      const [tx, ty] = posAt(tgt, e.t);
      raw.click_offset_px.push(Math.hypot(e.x - tx, e.y - ty));

      // Gridshot flicks start at the previous hit; Flicking ones at the spawn.
      const startT = key === "flicking" ? tgt.t : Math.max(lastHit[e.p], tgt.t);
      lastHit[e.p] = e.t;
      const i0 = firstIndexAtOrAfter(S.t, startT);
      const i1 = lastIndexAtOrBefore(S.t, e.t);
      if (i0 >= n || i1 - i0 < 4) return;
      const [ax, ay] = interp(S.t, S.x, S.y, startT);
      const d0 = Math.hypot(tx - ax, ty - ay);
      if (d0 < 60) return;
      const ux = (tx - ax) / d0;
      const uy = (ty - ay) / d0;

      let peakDir = 0;
      let peakDirIdx = i0;
      for (let i = i0; i <= i1; i += 1) {
        const d = vx[i] * ux + vy[i] * uy;
        if (d > peakDir) { peakDir = d; peakDirIdx = i; }
      }
      if (peakDir < 150) return;
      let onset = onsetOf(peakDirIdx, i0, ux, uy);
      if (onset < 0) onset = i0;  // chained straight on from the last target
      const onsetT = S.t[onset];
      const mt = e.t - onsetT;
      if (mt < 40) return;

      // Geometry of the flick itself, from where it started.
      const sx = S.x[onset];
      const sy = S.y[onset];
      const dist = Math.hypot(tx - sx, ty - sy);
      if (dist < 40) return;
      const fx = (tx - sx) / dist;
      const fy = (ty - sy) / dist;
      let peak = 0;
      let peakIdx = onset;
      for (let i = onset; i <= i1; i += 1) if (sm[i] > peak) { peak = sm[i]; peakIdx = i; }
      if (peak <= 0) return;

      raw.flick_ms.push(mt);
      raw.peak_speed.push(peak);
      raw.time_to_peak.push(((S.t[peakIdx] - onsetT) / mt) * 100);

      let maxAlong = 0;
      let maxSide = 0;
      for (let i = onset; i <= i1; i += 1) {
        const px = S.x[i] - sx;
        const py = S.y[i] - sy;
        maxAlong = Math.max(maxAlong, px * fx + py * fy);
        maxSide = Math.max(maxSide, Math.abs(px * fy - py * fx));
      }
      raw.overshoot_pct.push((Math.max(0, maxAlong - dist) / dist) * 100);
      raw.curve_pct.push((maxSide / dist) * 100);

      // Sub-movements: each time smoothed speed climbs back above 8% of the
      // main peak after dropping under 5% (a stop, a turn back, or a rest
      // gap). The first is the flick itself; the rest are corrections.
      const hiT = Math.max(60, peak * 0.08);
      const loT = Math.max(40, peak * 0.05);
      let units = 0;
      let moving = false;
      for (let i = onset; i <= i1; i += 1) {
        const gap = i > onset && S.t[i] - S.t[i - 1] > REST_GAP_MS;
        if (gap || sm[i] < loT) moving = false;
        if (!moving && sm[i] > hiT) { moving = true; units += 1; }
      }
      raw.corrections.push(Math.max(0, units - 1));
      raw.throughput.push(Math.log2(dist / (2 * tgt.r) + 1) / (mt / 1000));

      // Normalised speed shape of the flick, for the profile curve: speed
      // at 20 evenly spaced moments between onset and click.
      for (let b = 0; b < 20; b += 1) profileBins[b] += speedAt(onsetT + ((b + 0.5) / 20) * mt) / peak;
      profileCount += 1;
    });

    // ---------------------------------------------------------- reaction
    // The ball starts on the crosshair and moves along x, so reaction is
    // spawn to the start of horizontal movement the same way. Movement already
    // under way when it appeared, or starting within 80 ms, is a guess.
    targets.forEach((s) => {
      if (keyOf(s.p) !== "reaction" || !s.vx) return;
      const dir = Math.sign(s.vx);
      const end = Math.min(removedAt.get(s.id) ?? Infinity, s.t + 1500);
      const i0 = firstIndexAtOrAfter(S.t, s.t);
      const i1 = lastIndexAtOrBefore(S.t, end);
      if (i0 >= n || i1 < i0) return;
      let peak = 0;
      let peakIdx = i0;
      for (let i = i0; i <= i1; i += 1) if (vx[i] * dir > peak) { peak = vx[i] * dir; peakIdx = i; }
      if (peak < 120) return;
      const onset = onsetOf(peakIdx, i0, dir, 0);
      if (onset < 0) return;
      const rt = S.t[onset] - s.t;
      if (rt >= 80) raw.reaction_ms.push(rt);
    });

    // ---------------------------------------------------------- tracking
    const track = layout.find((p) => p.key === "tracking") || { start: 0, end: 0 };
    const F = rec.frames;
    const tf = { t: [], x: [], y: [], tx: [], ty: [] };
    for (let i = 0; i < F.t.length; i += 1) {
      if (F.t[i] < track.start || F.t[i] > track.end || F.tx[i] == null) continue;
      tf.t.push(F.t[i]); tf.x.push(F.x[i]); tf.y.push(F.y[i]); tf.tx.push(F.tx[i]); tf.ty.push(F.ty[i]);
    }
    let onTargetMs = 0;
    let trackMs = 0;
    for (let seg = track.start; seg < track.end; seg += 2000) {
      let errSum = 0;
      let count = 0;
      let on = 0;
      for (let i = Math.max(0, lastIndexAtOrBefore(tf.t, seg)); i < tf.t.length && tf.t[i] < seg + 2000; i += 1) {
        if (tf.t[i] < seg) continue;
        const err = Math.hypot(tf.x[i] - tf.tx[i], tf.y[i] - tf.ty[i]);
        errSum += err;
        count += 1;
        if (err <= rec.trackR) on += 1;
      }
      if (count >= 10) {
        raw.track_error_px.push(errSum / count);
        raw.on_target_pct.push((on / count) * 100);
      }
    }
    for (let i = 1; i < tf.t.length; i += 1) {
      const dt = tf.t[i] - tf.t[i - 1];
      if (dt <= 0 || dt > 100) continue;
      trackMs += dt;
      if (Math.hypot(tf.x[i] - tf.tx[i], tf.y[i] - tf.ty[i]) <= rec.trackR) onTargetMs += dt;
    }

    // Lag: the delay that best lines your crosshair up with where the target
    // was, measured per 5 s window.
    for (let seg = track.start; seg < track.end; seg += 5000) {
      const idx = [];
      for (let i = 0; i < tf.t.length; i += 1) if (tf.t[i] >= seg + 400 && tf.t[i] < seg + 5000) idx.push(i);
      if (idx.length < 30) continue;
      let best = 0;
      let bestErr = Infinity;
      for (let tau = 0; tau <= 400; tau += 5) {
        let sum = 0;
        for (const i of idx) {
          const [px, py] = interp(tf.t, tf.tx, tf.ty, tf.t[i] - tau);
          sum += Math.hypot(tf.x[i] - px, tf.y[i] - py);
        }
        if (sum < bestErr) { bestErr = sum; best = tau; }
      }
      raw.track_lag_ms.push(best);
    }

    // Jitter: distance from a 30 ms moving average of the raw samples.
    const cx = [0];
    const cy = [0];
    for (let i = 0; i < n; i += 1) { cx.push(cx[i] + S.x[i]); cy.push(cy[i] + S.y[i]); }
    for (let seg = track.start; seg < track.end; seg += 2000) {
      const a = Math.max(0, lastIndexAtOrBefore(S.t, seg));
      let lo = a;
      let hi = a;
      let sum = 0;
      let count = 0;
      for (let i = a; i < n && S.t[i] < seg + 2000; i += 1) {
        if (S.t[i] < seg) continue;
        while (lo < n && S.t[lo] < S.t[i] - 15) lo += 1;
        while (hi < n && S.t[hi] <= S.t[i] + 15) hi += 1;
        const m = hi - lo;
        if (m < 3) continue;
        sum += (S.x[i] - (cx[hi] - cx[lo]) / m) ** 2 + (S.y[i] - (cy[hi] - cy[lo]) / m) ** 2;
        count += 1;
      }
      if (count >= 20) raw.jitter_px.push(Math.sqrt(sum / count));
    }

    const metrics = {};
    METRICS.forEach(([key]) => { metrics[key] = summarize(raw[key], METRIC.get(key).discrete, key === "click_rt_ms"); });

    const acc = (s) => (s.clicks ? (s.hits / s.clicks) * 100 : 0);
    const clicking = ["gridshot", "flicking", "reaction"].map((key) => stats[key]).filter(Boolean);
    const hits = clicking.reduce((a, s) => a + s.hits, 0);
    const clicks = clicking.reduce((a, s) => a + s.clicks, 0);
    const phases = {};
    layout.forEach(({ key }) => {
      phases[key] = key === "tracking"
        ? { onTargetPct: trackMs > 0 ? (onTargetMs / trackMs) * 100 : 0 }
        : { ...stats[key], accuracy: acc(stats[key]) };
    });
    return {
      version: PROFILE_VERSION,
      createdAt: new Date().toISOString(),
      durationS: layoutEnd(layout) / 1000,
      sens: rec.sens,
      aim: rec.aim || null,
      view: rec.view,
      samplesHz: n > 1 ? Math.round((n - 1) / ((S.t[n - 1] - S.t[0]) / 1000)) : 0,
      overall: { score: Math.round(rec.score), hits, clicks, accuracy: clicks ? (hits / clicks) * 100 : 0 },
      phases,
      metrics,
      velocityProfile: profileCount ? profileBins.map((v) => v / profileCount) : [],
      flicksAnalysed: profileCount,
    };
  }

  // ================================================================ stage

  const stage = {};

  function ensureStage() {
    if (stage.root) return stage;
    const root = document.createElement("div");
    root.className = "hz-stage";
    root.id = "hzStage";
    root.hidden = true;
    root.innerHTML = `
      <canvas class="hz-canvas"></canvas>
      <div class="hz-hud">
        <div class="hz-bar"></div>
        <div class="hz-hud-row">
          <span class="hz-phase"></span>
          <strong class="hz-time"></strong>
          <span class="hz-score"></span>
        </div>
      </div>
      <div class="hz-center" hidden></div>
      <div class="hz-replay" hidden>
        <button type="button" class="hz-play" aria-label="Play or pause"></button>
        <span class="hz-clock">0:00</span>
        <input type="range" class="hz-seek" min="0" max="${TOTAL_MS}" step="10" value="0" aria-label="Replay position" />
        <div class="hz-chips"></div>
        <div class="hz-speeds">${[0.5, 1, 2, 4].map((s) => `<button type="button" data-speed="${s}">${s}x</button>`).join("")}</div>
        <button type="button" class="hz-close" aria-label="Close replay">Close</button>
      </div>`;
    document.body.append(root);
    Object.assign(stage, {
      root,
      canvas: root.querySelector(".hz-canvas"),
      hud: root.querySelector(".hz-hud"),
      barBox: root.querySelector(".hz-bar"),
      bar: [],
      phase: root.querySelector(".hz-phase"),
      time: root.querySelector(".hz-time"),
      score: root.querySelector(".hz-score"),
      center: root.querySelector(".hz-center"),
      replay: root.querySelector(".hz-replay"),
      layout: LAYOUT,
      w: 0,
      h: 0,
    });
    stage.ctx = stage.canvas.getContext("2d");
    window.addEventListener("resize", sizeStage);
    return stage;
  }

  // Progress bar segments and replay chips for a run's part layout.
  function setLayout(layout) {
    stage.layout = layout;
    stage.barBox.style.gridTemplateColumns = `repeat(${layout.length}, 1fr)`;
    stage.barBox.innerHTML = layout.map((p) => `<i title="${esc(label(p.key))}"><b></b></i>`).join("");
    stage.bar = [...stage.barBox.querySelectorAll("b")];
    stage.replay.querySelector(".hz-chips").innerHTML = layout.map((p, i) => `<button type="button" data-jump="${i}">${esc(label(p.key))}</button>`).join("");
  }

  function sizeStage() {
    if (!stage.root || stage.root.hidden) return;
    const dpr = window.devicePixelRatio || 1;
    stage.w = stage.root.clientWidth;
    stage.h = stage.root.clientHeight;
    stage.canvas.width = Math.round(stage.w * dpr);
    stage.canvas.height = Math.round(stage.h * dpr);
    stage.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function showCenter(html) {
    stage.center.innerHTML = html;
    stage.center.hidden = !html;
  }

  /* One frame of the world. `view` is the recorded view size; the scene is
   * drawn in that space and scaled to fit the stage, so replays of a run made
   * in a different window size still line up. */
  function drawScene(s) {
    const ctx = stage.ctx;
    const W = stage.w;
    const H = stage.h;
    const vw = s.view.w;
    const vh = s.view.h;
    const k = Math.min(W / vw, H / vh);
    ctx.save();
    ctx.fillStyle = "#05070a";
    ctx.fillRect(0, 0, W, H);
    ctx.translate((W - vw * k) / 2, (H - vh * k) / 2);
    ctx.scale(k, k);
    ctx.beginPath();
    ctx.rect(0, 0, vw, vh);
    ctx.clip();
    ctx.fillStyle = "#080b0f";
    ctx.fillRect(0, 0, vw, vh);
    const cx = vw / 2 - s.cam.x;
    const cy = vh / 2 - s.cam.y;
    const accent = s.accent;

    // Floor grid, moving with the view so turning reads as turning.
    const g = 90;
    ctx.strokeStyle = "rgba(255,255,255,0.045)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = ((cx % g) + g) % g; x < vw; x += g) { ctx.moveTo(x, 0); ctx.lineTo(x, vh); }
    for (let y = ((cy % g) + g) % g; y < vh; y += g) { ctx.moveTo(0, y); ctx.lineTo(vw, y); }
    ctx.stroke();
    ctx.strokeStyle = `rgba(${accent}, 0.1)`;
    ctx.beginPath();
    ctx.arc(cx, cy, Math.min(vw, vh) * 0.42, 0, Math.PI * 2);
    ctx.stroke();

    // Reaction: the ring to come back to, lit while a ball is on its way.
    if (s.centre) {
      ctx.save();
      ctx.setLineDash([5, 6]);
      ctx.lineWidth = 2;
      ctx.strokeStyle = s.centre.ready ? `rgba(${accent}, 0.85)` : "rgba(255,255,255,0.28)";
      ctx.beginPath();
      ctx.arc(cx, cy, CENTRE_R, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      ctx.strokeStyle = "rgba(255,255,255,0.08)";
      ctx.beginPath();
      ctx.moveTo(cx - vw * 0.45, cy);
      ctx.lineTo(cx + vw * 0.45, cy);
      ctx.stroke();
    }

    if (s.trail && s.trail.length > 1) {
      ctx.lineCap = "round";
      ctx.lineWidth = 2;
      for (let i = 1; i < s.trail.length; i += 1) {
        const a = s.trail[i - 1];
        const b = s.trail[i];
        ctx.strokeStyle = `rgba(${accent}, ${0.06 + 0.6 * (i / s.trail.length)})`;
        ctx.beginPath();
        ctx.moveTo(a[0] + cx, a[1] + cy);
        ctx.lineTo(b[0] + cx, b[1] + cy);
        ctx.stroke();
      }
    }

    const target = (x, y, r, rgb) => {
      const sx = x + cx;
      const sy = y + cy;
      const grad = ctx.createRadialGradient(sx - r * 0.3, sy - r * 0.3, r * 0.1, sx, sy, r);
      grad.addColorStop(0, `rgba(${rgb}, 1)`);
      grad.addColorStop(1, `rgba(${rgb}, 0.55)`);
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = "rgba(255,255,255,0.7)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,0.85)";
      ctx.beginPath();
      ctx.arc(sx, sy, Math.max(2, r * 0.18), 0, Math.PI * 2);
      ctx.fill();
    };
    s.targets.forEach((t) => target(t.x, t.y, t.r, t.rgb || accent));
    if (s.track) {
      ctx.fillStyle = `rgba(${accent}, 0.1)`;
      ctx.beginPath();
      ctx.arc(s.track.x + cx, s.track.y + cy, s.track.r * 1.9, 0, Math.PI * 2);
      ctx.fill();
      target(s.track.x, s.track.y, s.track.r, "255, 196, 92");
    }

    (s.fx || []).forEach((f) => {
      const age = clamp(f.age / 380, 0, 1);
      ctx.strokeStyle = f.miss ? `rgba(255, 107, 120, ${1 - age})` : `rgba(${accent}, ${1 - age})`;
      ctx.lineWidth = f.miss ? 1.5 : 2.5;
      ctx.beginPath();
      ctx.arc(f.x + cx, f.y + cy, (f.miss ? 5 : f.r) + age * (f.miss ? 10 : 22), 0, Math.PI * 2);
      ctx.stroke();
    });

    // Crosshair, fixed in the middle like in a game.
    const mx = vw / 2;
    const my = vh / 2;
    const arm = (x1, y1, x2, y2) => {
      ctx.strokeStyle = "rgba(0,0,0,0.75)";
      ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    };
    arm(mx - 10, my, mx - 4, my);
    arm(mx + 4, my, mx + 10, my);
    arm(mx, my - 10, mx, my - 4);
    arm(mx, my + 4, mx, my + 10);

    // Click reaction: a line of text under the circle.
    if (s.banner) {
      ctx.font = "600 15px Inter, sans-serif";
      ctx.textAlign = "center";
      ctx.fillStyle = s.banner.warn ? "rgba(255, 130, 140, 0.95)" : "rgba(255,255,255,0.75)";
      ctx.fillText(s.banner.text, mx, my + 92);
    }

    // Sensitivity test: a heading strip like a game compass, and hints.
    if (s.compass) {
      const { ppd } = s.compass;
      const heading = s.cam.x / ppd;
      const y = 78;
      const span = vw / 2 / ppd;
      const wrap = (d) => ((Math.round(d) % 360) + 360) % 360;
      ctx.font = "500 11px 'DM Mono', monospace";
      ctx.textAlign = "center";
      for (let d = Math.ceil((heading - span) / 5) * 5; d <= heading + span; d += 5) {
        const x = mx + (d - heading) * ppd;
        const major = d % 45 === 0;
        ctx.strokeStyle = major ? "rgba(255,255,255,0.55)" : "rgba(255,255,255,0.2)";
        ctx.lineWidth = major ? 2 : 1;
        ctx.beginPath();
        ctx.moveTo(x, y);
        ctx.lineTo(x, y + (major ? 12 : d % 15 === 0 ? 8 : 5));
        ctx.stroke();
        if (major) {
          ctx.fillStyle = wrap(d) === 0 ? `rgba(${accent}, 1)` : "rgba(255,255,255,0.6)";
          ctx.fillText(`${wrap(d)}°`, x, y + 26);
        }
      }
      ctx.fillStyle = `rgba(${accent}, 1)`;
      ctx.beginPath();
      ctx.moveTo(mx, y - 2);
      ctx.lineTo(mx - 6, y - 10);
      ctx.lineTo(mx + 6, y - 10);
      ctx.closePath();
      ctx.fill();
      const turns = Math.floor((Math.abs(heading) + 0.5) / 360);
      ctx.fillText(`${wrap(heading)}° · ${turns} full turn${turns === 1 ? "" : "s"}`, mx, y + 44);

      const lines = [
        "Scroll or ↑/↓ to change cm/360 · Shift for fine steps · Esc to keep it and return",
        `Move the mouse ${s.compass.cm.toFixed(1)} cm sideways and the compass should come back to the same heading`,
      ];
      if (s.compass.raw === false) lines.push("Raw mouse input is not available here, so Windows pointer speed and acceleration still apply");
      ctx.font = "500 12px Inter, sans-serif";
      lines.forEach((text, i) => {
        ctx.fillStyle = i === 2 ? "rgba(255, 184, 107, 0.9)" : "rgba(255,255,255,0.55)";
        ctx.fillText(text, mx, vh - 60 + i * 20);
      });
    }
    ctx.restore();
  }

  function paintBar(ms) {
    stage.bar.forEach((b, i) => {
      const p = stage.layout[i];
      b.style.transform = `scaleX(${clamp((ms - p.start) / (p.end - p.start), 0, 1)})`;
    });
  }

  // ================================================================ session

  let session = null;

  class Session {
    constructor(opts = {}) {
      // A warm-up is the same parts, shorter, and is never saved.
      this.warmup = Boolean(opts.warmup);
      this.layout = this.warmup ? WARMUP_LAYOUT : LAYOUT;
      this.clickNextAt = null;
      this.banner = null;
      this.aim = { ...aim };
      this.sens = 0;
      this.raw = null;
      this.state = "idle";
      this.phase = 0;
      this.activeMs = 0;
      this.offset = 0;
      this.cam = { x: 0, y: 0 };
      this.targets = [];
      this.track = null;
      this.fx = [];
      this.nextId = 1;
      this.score = 0;
      this.hits = 0;
      this.clicks = 0;
      this.pendingClick = null;
      this.flickNextAt = 0;
      this.ballAt = null;
      this.streak = 0;
      this.quitArmed = false;
      this.rec = {
        samples: { t: [], x: [], y: [] },
        frames: { t: [], x: [], y: [], tx: [], ty: [] },
        events: [],
      };
      this.onMove = this.onMove.bind(this);
      this.onDown = this.onDown.bind(this);
      this.onUp = this.onUp.bind(this);
      this.onLock = this.onLock.bind(this);
      this.onCenter = this.onCenter.bind(this);
      this.tick = this.tick.bind(this);
    }

    get key() {
      return this.layout[this.phase].key;
    }

    open() {
      ensureStage();
      setLayout(this.layout);
      stage.root.hidden = false;
      stage.root.dataset.mode = "game";
      stage.replay.hidden = true;
      stage.hud.hidden = false;
      sizeStage();
      this.view = { w: stage.w, h: stage.h };
      this.sens = pxPerCount(this.view.h, this.aim);
      this.accent = accentRgb();
      document.addEventListener("pointermove", this.onMove);
      document.addEventListener("pointerdown", this.onDown);
      document.addEventListener("pointerup", this.onUp);
      document.addEventListener("pointerlockchange", this.onLock);
      stage.center.addEventListener("click", this.onCenter);
      api()?.humanizer_session(true);
      fullscreen(true);
      this.lock();
      this.countdown(0);
      this.frame = requestAnimationFrame(this.tick);
    }

    close() {
      this.state = "closed";
      cancelAnimationFrame(this.frame);
      document.removeEventListener("pointermove", this.onMove);
      document.removeEventListener("pointerdown", this.onDown);
      document.removeEventListener("pointerup", this.onUp);
      document.removeEventListener("pointerlockchange", this.onLock);
      stage.center.removeEventListener("click", this.onCenter);
      if (this.locked) document.exitPointerLock();
      api()?.humanizer_session(false);
      fullscreen(false);
      stage.root.hidden = true;
      showCenter("");
      session = null;
      render();
    }

    lock() {
      // Once any lock has fallen back to OS-adjusted movement, say so.
      lockPointer((raw) => { this.raw = this.raw === false ? false : raw; });
    }

    get locked() {
      return document.pointerLockElement === stage.canvas;
    }

    clock(wall = performance.now()) {
      return this.state === "running" ? wall + this.offset : this.activeMs;
    }

    countdown(phase) {
      this.phase = phase;
      this.state = "countdown";
      this.countdownEnd = performance.now() + COUNTDOWN_MS;
      this.cam.x = 0;
      this.cam.y = 0;
      const p = PHASE_INFO[this.key];
      showCenter(`<div class="hz-card hz-count">
          <span class="hz-count-icon">${ICONS[p.icon]}</span>
          <p class="eyebrow">${this.warmup ? "WARM-UP · " : ""}PART ${phase + 1} OF ${this.layout.length}</p>
          <h2>${esc(p.label)}</h2>
          <p>${esc(p.blurb)}</p>
          <b class="hz-count-num">3</b>
        </div>`);
    }

    run() {
      this.state = "running";
      this.offset = this.activeMs - performance.now();
      showCenter("");
      // A circle taken away by a pause comes back after a moment.
      if (this.key === "click" && !this.targets.length && this.clickNextAt == null) {
        this.clickNextAt = this.clock() + rand(400, 800);
      }
    }

    begin() {
      this.targets = [];
      this.track = null;
      this.fx = [];
      this.ballAt = null;
      this.clickNextAt = null;
      this.banner = null;
      this.streak = 0;
      this.run();
      const now = this.clock();
      if (this.key === "gridshot") for (let i = 0; i < 3; i += 1) this.spawnGrid(now);
      if (this.key === "flicking") this.flickNextAt = now + 500;
      if (this.key === "tracking") this.track = { x: 0, y: 0, vx: 0, vy: 0, gx: 0, gy: 0, r: TRACK_R, turnAt: now };
    }

    pause() {
      if (this.state !== "running" && this.state !== "countdown") return;
      if (this.state === "running") this.activeMs = this.clock();
      this.pausedFrom = this.state;
      this.state = "paused";
      this.quitArmed = false;
      this.pendingClick = null;
      this.ballAt = null;
      // A circle that may already be green cannot be timed across a pause.
      if (this.key === "click") {
        this.targets.slice().forEach((tgt) => this.remove(tgt, this.activeMs, "pause"));
        this.clickNextAt = null;
        this.banner = null;
      }
      const fresh = this.activeMs === 0;
      showCenter(`<div class="hz-card">
          <p class="eyebrow">${fresh ? "READY" : "PAUSED"}</p>
          <h2>${fresh ? "Click to lock the mouse" : "Click to resume"}</h2>
          <p>${fresh ? "The session needs the mouse locked to the window before it can start." : "The clock is stopped and nothing is recorded while paused."}</p>
          <div class="hz-card-actions">
            <button type="button" class="dash-primary" data-hz="resume">${fresh ? "Start" : "Resume"}</button>
            <button type="button" class="dash-secondary" data-hz="quit">Quit session</button>
          </div>
        </div>`);
    }

    resume() {
      if (this.state !== "paused") return;
      if (this.pausedFrom === "countdown") this.countdown(this.phase);
      else this.run();
    }

    onCenter(event) {
      const act = event.target.closest("[data-hz]")?.dataset.hz;
      if (act === "quit") {
        if (!this.quitArmed) {
          this.quitArmed = true;
          event.target.closest("button").textContent = this.warmup ? "Click again to stop the warm-up" : "Click again to discard this run";
          return;
        }
        this.close();
        if (this.warmup) notify("Warm-up stopped");
        else notify(page.required ? "Session discarded. The humanizer still needs a completed run." : "Humanizer session discarded");
        return;
      }
      if (act === "done") {
        this.close();
        return;
      }
      if (act === "retry-save") {
        saveRun(this);
        return;
      }
      if (this.state === "paused") this.lock();
    }

    onLock() {
      if (this.locked) {
        if (this.state === "paused") this.resume();
      } else if (this.state === "running" || this.state === "countdown") {
        this.pause();
      }
    }

    onMove(e) {
      if (!this.locked) return;
      const list = typeof e.getCoalescedEvents === "function" ? e.getCoalescedEvents() : [];
      const events = list.length ? list : [e];
      const end = this.layout[this.phase].end;
      for (const ev of events) {
        const mx = ev.movementX || 0;
        const my = ev.movementY || 0;
        if (Math.abs(mx) > 900 || Math.abs(my) > 900) continue;  // jump when the lock engages
        this.cam.x += mx * this.sens;
        this.cam.y += my * this.sens;
        if (this.state === "running") {
          const S = this.rec.samples;
          S.t.push(Math.round(Math.min(end, ev.timeStamp + this.offset) * 10) / 10);
          S.x.push(r1(this.cam.x));
          S.y.push(r1(this.cam.y));
        }
      }
    }

    onDown(e) {
      if (!this.locked || e.button !== 0 || this.state !== "running" || this.key === "tracking") return;
      const t = this.clock(e.timeStamp);
      if (this.key === "click") {
        this.clickShot(t);
        return;
      }
      let hit = null;
      let hitPos = null;
      for (const tgt of this.targets) {
        const pos = posAt(tgt, t);
        if (Math.hypot(this.cam.x - pos[0], this.cam.y - pos[1]) <= tgt.r) { hit = tgt; hitPos = pos; break; }
      }
      const click = { k: "c", t: Math.round(t), x: r1(this.cam.x), y: r1(this.cam.y), hit: hit ? hit.id : null, p: this.phase };
      this.rec.events.push(click);
      this.pendingClick = click;
      this.clicks += 1;
      this.streak = hit ? this.streak + 1 : 0;
      sound.play("click");
      if (hit) sound.play("hit", this.streak);
      if (!hit) {
        this.fx.push({ x: this.cam.x, y: this.cam.y, r: 5, age: 0, miss: true });
        return;
      }
      this.hits += 1;
      this.score += 100;
      this.remove(hit, t, "hit");
      this.fx.push({ x: hitPos[0], y: hitPos[1], r: hit.r, age: 0 });
      if (this.key === "gridshot") this.spawnGrid(t, hit.cell);
      if (this.key === "flicking") this.flickNextAt = t + rand(250, 550);
      if (this.key === "reaction") this.ballAt = null;
    }

    onUp(e) {
      if (e.button !== 0 || !this.pendingClick) return;
      const hold = this.clock(e.timeStamp) - this.pendingClick.t;
      if (hold >= 0 && hold < 1500) this.pendingClick.hold = Math.round(hold);
      this.pendingClick = null;
    }

    spawn(x, y, r, t, extra = {}) {
      const tgt = { id: this.nextId++, x: r1(x), y: r1(y), r, t, ...extra };
      this.targets.push(tgt);
      const event = { k: "s", t: Math.round(t), id: tgt.id, x: tgt.x, y: tgt.y, r, p: this.phase };
      if (extra.vx) event.vx = extra.vx;
      this.rec.events.push(event);
      tgt.evt = event;  // lets the Click reaction part stamp the moment it turns green
      return tgt;
    }

    setBanner(text, warn, t, ms = 1100) {
      this.banner = { text, warn, until: t + ms };
    }

    /* Click reaction: a red circle in the middle. After a random wait it turns
     * green, and the time from green to the click is the reaction. It only
     * turns green with the crosshair on it, so a hand that has wandered off
     * is never timed. Clicking it while red is a false start and restarts the
     * wait; a green circle nobody clicks within CLICK_TIMEOUT_MS is a miss. */
    stepClick(t) {
      const tgt = this.targets[0];
      if (!tgt) {
        if (this.clickNextAt != null && t >= this.clickNextAt) {
          this.clickNextAt = null;
          const circle = this.spawn(0, 0, CLICK_R, t);
          circle.goAt = t + rand(CLICK_WAIT_MS[0], CLICK_WAIT_MS[1]);
          circle.go = null;
        }
        return;
      }
      if (tgt.go == null) {
        if (t >= tgt.goAt && Math.hypot(this.cam.x - tgt.x, this.cam.y - tgt.y) <= tgt.r) {
          tgt.go = Math.round(t);
          tgt.evt.go = tgt.go;
        }
      } else if (t - tgt.go > CLICK_TIMEOUT_MS) {
        this.remove(tgt, t, "timeout");
        this.fx.push({ x: tgt.x, y: tgt.y, r: tgt.r, age: 0, miss: true });
        this.streak = 0;
        this.setBanner("Too slow", true, t);
        this.clickNextAt = t + rand(CLICK_GAP_MS[0], CLICK_GAP_MS[1]);
      }
    }

    clickShot(t) {
      const tgt = this.targets[0];
      const on = Boolean(tgt) && Math.hypot(this.cam.x - tgt.x, this.cam.y - tgt.y) <= tgt.r;
      const green = Boolean(tgt) && tgt.go != null;
      const hit = on && green ? tgt : null;
      const click = { k: "c", t: Math.round(t), x: r1(this.cam.x), y: r1(this.cam.y), hit: hit ? hit.id : null, p: this.phase };
      if (on && !green) click.early = 1;
      this.rec.events.push(click);
      this.pendingClick = click;
      this.clicks += 1;
      sound.play("click");
      if (!hit) {
        this.streak = 0;
        this.fx.push({ x: this.cam.x, y: this.cam.y, r: 5, age: 0, miss: true });
        if (click.early) {
          tgt.goAt = t + rand(CLICK_WAIT_MS[0], CLICK_WAIT_MS[1]);
          this.setBanner("Too soon. Wait for green.", true, t);
        }
        return;
      }
      this.streak += 1;
      this.hits += 1;
      this.score += 100;
      sound.play("hit", this.streak);
      const rt = t - hit.go;
      this.remove(hit, t, "hit");
      this.fx.push({ x: hit.x, y: hit.y, r: hit.r, age: 0 });
      this.setBanner(`${Math.round(rt)} ms`, false, t, 900);
      this.clickNextAt = t + rand(CLICK_GAP_MS[0], CLICK_GAP_MS[1]);
    }

    remove(tgt, t, how) {
      this.targets = this.targets.filter((x) => x !== tgt);
      this.rec.events.push({ k: "r", t: Math.round(t), id: tgt.id, how, p: this.phase });
    }

    spawnGrid(t, avoid) {
      const cw = Math.min(130, this.view.w * 0.11);
      const ch = Math.min(115, this.view.h * 0.14);
      const used = new Set(this.targets.map((x) => x.cell));
      const free = [];
      for (let c = 0; c < 5; c += 1) {
        for (let r = 0; r < 3; r += 1) {
          const cell = `${c}:${r}`;
          if (!used.has(cell) && cell !== avoid) free.push({ cell, x: (c - 2) * cw, y: (r - 1) * ch });
        }
      }
      const pick = free[Math.floor(Math.random() * free.length)];
      if (pick) this.spawn(pick.x, pick.y, GRID_R, t, { cell: pick.cell });
    }

    spawnFlick(t) {
      const bx = this.view.w * 0.4;
      const by = this.view.h * 0.34;
      let x = 0;
      let y = 0;
      for (let i = 0; i < 12; i += 1) {
        const a = rand(0, Math.PI * 2);
        const d = rand(170, 480);
        x = clamp(this.cam.x + Math.cos(a) * d, -bx, bx);
        y = clamp(this.cam.y + Math.sin(a) * d, -by, by);
        if (Math.hypot(x - this.cam.x, y - this.cam.y) >= 150) break;
      }
      this.spawn(x, y, FLICK_R, t);
    }

    /* Reaction: once the crosshair is back in the centre ring, wait a random
     * 0.1-1 s (restarting if it leaves), then launch a ball from the centre
     * along the horizontal axis. A ball that gets away counts as missed. */
    stepReaction(t) {
      const ball = this.targets[0];
      if (ball) {
        const [x] = posAt(ball, t);
        ball.drawX = x;
        if (Math.abs(x) > this.view.w * 0.45) {
          this.remove(ball, t, "timeout");
          this.fx.push({ x, y: ball.y, r: ball.r, age: 0, miss: true });
          this.ballAt = null;
          this.streak = 0;
        }
        return;
      }
      const off = Math.hypot(this.cam.x, this.cam.y);
      if (this.ballAt == null) {
        if (off <= CENTRE_R) this.ballAt = t + rand(100, 1000);
      } else if (off > CENTRE_R * 1.6) {
        this.ballAt = null;
      } else if (t >= this.ballAt) {
        this.ballAt = null;
        const vx = Math.round(rand(200, 520)) * (Math.random() < 0.5 ? -1 : 1);
        this.spawn(0, 0, BALL_R, t, { vx });
      }
    }

    stepTrack(t, dt) {
      const tr = this.track;
      if (t >= tr.turnAt) {
        // Strafe-like: mostly sideways, sometimes a stop, speed varies.
        const stop = Math.random() < 0.18;
        const speed = stop ? 0 : rand(160, 420);
        const angle = rand(-0.38, 0.38) + (Math.random() < 0.5 ? 0 : Math.PI);
        tr.gx = Math.cos(angle) * speed;
        tr.gy = Math.sin(angle) * speed;
        tr.turnAt = t + (stop ? rand(180, 420) : rand(350, 1300));
      }
      const bx = this.view.w * 0.36;
      const by = this.view.h * 0.26;
      if (Math.abs(tr.x) > bx && Math.sign(tr.gx) === Math.sign(tr.x)) tr.gx = -tr.gx || -Math.sign(tr.x) * 220;
      if (Math.abs(tr.y) > by && Math.sign(tr.gy) === Math.sign(tr.y)) tr.gy = -tr.gy || -Math.sign(tr.y) * 90;
      const f = Math.min(1, (dt / 1000) * 9);
      tr.vx += (tr.gx - tr.vx) * f;
      tr.vy += (tr.gy - tr.vy) * f;
      tr.x += (tr.vx * dt) / 1000;
      tr.y += (tr.vy * dt) / 1000;
      if (Math.hypot(this.cam.x - tr.x, this.cam.y - tr.y) <= tr.r) this.score += (dt / 1000) * 40;
    }

    tick(wall) {
      if (this.state === "closed") return;
      const dtWall = Math.min(100, wall - (this.lastWall || wall));
      this.lastWall = wall;

      // The window goes fullscreen (or reports no size while hidden) just
      // after opening. Until the first part starts, follow the stage size;
      // from then on the recorded view is fixed.
      const unstarted = this.state !== "running" && !this.rec.samples.t.length && !this.rec.frames.t.length;
      if (!this.view.w || !this.view.h || (unstarted && stage.w && stage.h && (stage.w !== this.view.w || stage.h !== this.view.h))) {
        sizeStage();
        this.view = { w: stage.w || 1280, h: stage.h || 720 };
        this.sens = pxPerCount(this.view.h, this.aim);
      }

      if (this.state === "countdown") {
        const left = this.countdownEnd - performance.now();
        const num = stage.center.querySelector(".hz-count-num");
        if (num) num.textContent = String(Math.max(1, Math.ceil(left / 1000)));
        // Never start the clock without the mouse locked: nothing could be
        // aimed or recorded. Ask for a click, which also lets the lock in.
        if (left <= 0) {
          if (this.locked) this.begin();
          else this.pause();
        }
      }

      if (this.state === "running") {
        const t = this.clock();
        const end = this.layout[this.phase].end;
        if (this.key === "flicking") {
          if (!this.targets.length && t >= this.flickNextAt) this.spawnFlick(t);
          this.targets.slice().forEach((tgt) => {
            if (t - tgt.t > FLICK_TIMEOUT_MS) {
              this.remove(tgt, t, "timeout");
              this.fx.push({ x: tgt.x, y: tgt.y, r: tgt.r, age: 0, miss: true });
              this.flickNextAt = t + rand(250, 550);
              this.streak = 0;
            }
          });
        }
        if (this.key === "reaction") this.stepReaction(t);
        if (this.key === "click") this.stepClick(t);
        if (this.key === "tracking" && this.track) this.stepTrack(t, dtWall);
        const F = this.rec.frames;
        F.t.push(Math.round(Math.min(t, end)));
        F.x.push(r1(this.cam.x));
        F.y.push(r1(this.cam.y));
        F.tx.push(this.track ? r1(this.track.x) : null);
        F.ty.push(this.track ? r1(this.track.y) : null);
        if (t >= end) {
          this.activeMs = end;
          this.state = "between";
          this.targets.slice().forEach((tgt) => this.remove(tgt, end, "phase"));
          this.track = null;
          this.pendingClick = null;
          if (this.phase + 1 < this.layout.length) this.countdown(this.phase + 1);
          else this.finish();
        }
      }

      this.fx = this.fx.filter((f) => (f.age += dtWall) < 380);
      const active = this.clock();
      paintBar(active);
      stage.phase.textContent = `${this.warmup ? "Warm-up " : ""}${this.phase + 1}/${this.layout.length} · ${label(this.key)}`;
      stage.time.textContent = mmss(this.layout[this.phase].end - active);
      const acc = this.clicks ? Math.round((this.hits / this.clicks) * 100) : 100;
      stage.score.textContent = `${this.streak >= 2 ? `${this.streak}x combo · ` : ""}${Math.round(this.score).toLocaleString("en-US")} pts · ${acc}%`;
      const reaction = this.key === "reaction" && this.state === "running";
      const shown = this.key === "click"
        ? this.targets.map((tgt) => ({ ...tgt, rgb: tgt.go != null ? CLICK_RGB.go : CLICK_RGB.wait }))
        : this.targets.map((tgt) => (tgt.vx ? { ...tgt, x: tgt.drawX ?? tgt.x } : tgt));
      drawScene({
        view: this.view,
        cam: this.cam,
        targets: shown,
        track: this.track,
        fx: this.fx,
        accent: this.accent,
        centre: reaction ? { ready: this.ballAt != null } : null,
        banner: this.key === "click" && this.banner && active < this.banner.until ? this.banner : null,
      });
      this.frame = requestAnimationFrame(this.tick);
    }

    finish() {
      this.state = "done";
      if (this.locked) document.exitPointerLock();
      api()?.humanizer_session(false);
      if (this.warmup) {
        this.finishWarmup();
        return;
      }
      showCenter(`<div class="hz-card"><p class="eyebrow">SESSION COMPLETE</p><h2>Working out your profile…</h2><p>Reading ${this.rec.samples.t.length.toLocaleString("en-US")} mouse samples.</p></div>`);
      setTimeout(() => {
        try {
          this.profile = analyze({
            samples: this.rec.samples,
            frames: this.rec.frames,
            events: this.rec.events,
            layout: this.layout,
            sens: this.sens,
            aim: { ...this.aim, pxPerDeg: pxPerDeg(this.view.h), rawInput: this.raw !== false },
            view: this.view,
            trackR: TRACK_R,
            score: this.score,
          });
        } catch (err) {
          showCenter(`<div class="hz-card"><p class="eyebrow">SOMETHING WENT WRONG</p><h2>Could not analyse the run</h2><p>${esc(String(err))}</p><div class="hz-card-actions"><button type="button" class="dash-primary" data-hz="done">Close</button></div></div>`);
          return;
        }
        this.replay = {
          version: PROFILE_VERSION,
          view: this.view,
          sens: this.sens,
          trackR: TRACK_R,
          phases: this.layout,
          frames: this.rec.frames,
          events: this.rec.events,
        };
        saveRun(this);
      }, 60);
    }

    /* A warm-up is measured with the same analysis as a run, shown next to the
     * saved profile, and thrown away. Nothing reaches the disk. */
    finishWarmup() {
      showCenter('<div class="hz-card"><p class="eyebrow">WARM-UP COMPLETE</p><h2>Reading how it went…</h2></div>');
      setTimeout(() => {
        let p = null;
        try {
          p = analyze({
            samples: this.rec.samples,
            frames: this.rec.frames,
            events: this.rec.events,
            layout: this.layout,
            sens: this.sens,
            aim: { ...this.aim, pxPerDeg: pxPerDeg(this.view.h), rawInput: this.raw !== false },
            view: this.view,
            trackR: TRACK_R,
            score: this.score,
          });
        } catch { /* the card below still reports points and accuracy */ }
        const base = page.profile?.metrics || {};
        const line = (key, label) => {
          const now = p?.metrics?.[key]?.median;
          if (now == null) return "";
          const ref = base[key]?.median;
          const delta = ref != null && ref > 0 ? ((now - ref) / ref) * 100 : null;
          const note = delta == null ? "" : `<small>${Math.abs(delta) < 1 ? "same as" : `${Math.abs(delta).toFixed(0)}% ${delta > 0 ? "slower than" : "quicker than"}`} your profile (${fmt(key, ref)})</small>`;
          return `<div><span>${esc(label)}</span><b>${fmt(key, now)}</b>${note}</div>`;
        };
        const acc = this.clicks ? Math.round((this.hits / this.clicks) * 100) : 0;
        showCenter(`<div class="hz-card hz-done">
            <p class="eyebrow">WARM-UP COMPLETE</p>
            <h2>${Math.round(this.score).toLocaleString("en-US")} points</h2>
            <div class="hz-done-grid">
              <div><span>Accuracy</span><b>${acc}%</b></div>
              <div><span>Hits</span><b>${this.hits}</b></div>
              ${line("click_rt_ms", "Click reaction")}
              ${line("flick_ms", "Flick time")}
              ${line("reaction_ms", "Reaction")}
              ${p?.phases?.tracking ? `<div><span>On target</span><b>${p.phases.tracking.onTargetPct.toFixed(1)}%</b></div>` : ""}
            </div>
            <p>Nothing was saved. Your profile and past runs are unchanged.</p>
            <div class="hz-card-actions"><button type="button" class="dash-primary" data-hz="done">Done</button></div>
          </div>`);
      }, 60);
    }
  }

  // Too little clean data for the numbers to mean much.
  function thin(p) {
    const n = (key) => p.metrics?.[key]?.n || 0;
    return (p.flicksAnalysed || 0) < 15 || n("reaction_ms") < 5 || n("track_lag_ms") < 3;
  }

  function applyState(res) {
    page.profile = res.profile ?? null;
    page.runs = res.runs || [];
    page.required = Boolean(res.required);
    page.reason = res.reason || null;
    page.dueAt = res.dueAt || null;
    page.rerunDays = res.rerunDays || page.rerunDays;
    if (res.paths) page.paths = res.paths;
    page.loaded = true;
  }

  async function saveRun(s) {
    const p = s.profile;
    showCenter('<div class="hz-card"><p class="eyebrow">SESSION COMPLETE</p><h2>Saving…</h2></div>');
    let res;
    try {
      res = await api()?.humanizer_save_run(JSON.stringify({ profile: p, replay: s.replay }));
    } catch (err) {
      res = { ok: false, error: String(err) };
    }
    if (!res?.ok) {
      showCenter(`<div class="hz-card"><p class="eyebrow">NOT SAVED YET</p><h2>Could not save the run</h2><p>${esc(res?.error || "The app did not answer.")}</p>
          <div class="hz-card-actions"><button type="button" class="dash-primary" data-hz="retry-save">Try again</button><button type="button" class="dash-secondary" data-hz="done">Close</button></div></div>`);
      return;
    }
    applyState(res);
    page.intro = false;
    enforce();
    render();
    const m = p.metrics;
    showCenter(`<div class="hz-card hz-done">
        <p class="eyebrow">SESSION COMPLETE</p>
        <h2>${Math.round(p.overall.score).toLocaleString("en-US")} points</h2>
        <div class="hz-done-grid">
          <div><span>Accuracy</span><b>${p.overall.accuracy.toFixed(1)}%</b></div>
          <div><span>Reaction</span><b>${fmt("reaction_ms", m.reaction_ms.median)}</b></div>
          <div><span>On target</span><b>${p.phases.tracking.onTargetPct.toFixed(1)}%</b></div>
          <div><span>Flicks read</span><b>${p.flicksAnalysed}</b></div>
        </div>
        <p>${thin(p) ? "Saved, but this run had very little clean data to learn from. A rerun will give a more reliable profile." : `Your humanizer profile is saved. It is due again in ${page.rerunDays} days.`}</p>
        <div class="hz-card-actions"><button type="button" class="dash-primary" data-hz="done">View profile</button></div>
      </div>`);
  }

  function startSession(opts = {}) {
    if (session || replay || tester) return;
    // A warm-up needs a profile to sit next to; without one the full run is
    // what is asked for.
    session = new Session({ warmup: Boolean(opts.warmup) && Boolean(page.profile) && !page.required });
    session.open();
  }

  // ================================================================ sensitivity test

  /* Free practice with the current cm/360: flick targets, a heading compass
   * (move the mouse your cm/360 and it comes back to the same heading), and
   * the scroll wheel or arrow keys to adjust while playing. Nothing is
   * recorded. Esc (or losing the lock) ends it and keeps the value. */
  let tester = null;

  class SensTest {
    constructor() {
      this.cam = { x: 0, y: 0 };
      this.targets = [];
      this.fx = [];
      this.hits = 0;
      this.streak = 0;
      this.nextAt = 0;
      this.raw = null;
      this.started = false;
      this.onMove = this.onMove.bind(this);
      this.onDown = this.onDown.bind(this);
      this.onWheel = this.onWheel.bind(this);
      this.onKey = this.onKey.bind(this);
      this.onLock = this.onLock.bind(this);
      this.onCenter = this.onCenter.bind(this);
      this.tick = this.tick.bind(this);
    }

    get locked() {
      return document.pointerLockElement === stage.canvas;
    }

    open() {
      ensureStage();
      setLayout([]);
      stage.root.hidden = false;
      stage.root.dataset.mode = "test";
      stage.replay.hidden = true;
      stage.hud.hidden = false;
      sizeStage();
      this.view = { w: stage.w || 1280, h: stage.h || 720 };
      this.accent = accentRgb();
      document.addEventListener("pointermove", this.onMove);
      document.addEventListener("pointerdown", this.onDown);
      document.addEventListener("wheel", this.onWheel, { passive: false });
      document.addEventListener("keydown", this.onKey, true);
      document.addEventListener("pointerlockchange", this.onLock);
      stage.center.addEventListener("click", this.onCenter);
      api()?.humanizer_session(true);
      fullscreen(true);
      this.lock();
      showCenter(`<div class="hz-card"><p class="eyebrow">SENSITIVITY TEST</p><h2>Click to lock the mouse</h2>
          <p>Flick at the targets and scroll to change cm/360 while you play. Esc keeps the value and returns.</p>
          <div class="hz-card-actions"><button type="button" class="dash-primary" data-hz="lock">Start test</button><button type="button" class="dash-secondary" data-hz="close">Cancel</button></div></div>`);
      this.frame = requestAnimationFrame(this.tick);
    }

    lock() {
      lockPointer((raw) => { this.raw = raw; });
    }

    close() {
      cancelAnimationFrame(this.frame);
      document.removeEventListener("pointermove", this.onMove);
      document.removeEventListener("pointerdown", this.onDown);
      document.removeEventListener("wheel", this.onWheel);
      document.removeEventListener("keydown", this.onKey, true);
      document.removeEventListener("pointerlockchange", this.onLock);
      stage.center.removeEventListener("click", this.onCenter);
      if (this.locked) document.exitPointerLock();
      api()?.humanizer_session(false);
      fullscreen(false);
      stage.root.hidden = true;
      showCenter("");
      tester = null;
      render();
      if (this.started) notify(`Sensitivity set to ${aimSummary()}`, "success");
    }

    onCenter(event) {
      const act = event.target.closest("[data-hz]")?.dataset.hz;
      if (act === "close") this.close();
      else this.lock();
    }

    onLock() {
      if (this.locked) {
        this.started = true;
        showCenter("");
      } else if (this.started) {
        this.close();
      }
    }

    adjust(delta) {
      aim.cm360 = clamp(Math.round((aim.cm360 + delta) * 10) / 10, CM_MIN, CM_MAX);
      saveAim();
    }

    onWheel(event) {
      if (!this.locked) return;
      event.preventDefault();
      const step = event.shiftKey ? 0.1 : aim.cm360 >= 20 ? 1 : 0.5;
      this.adjust(event.deltaY > 0 ? step : -step);  // wheel down = slower
    }

    onKey(event) {
      if (!this.locked) {
        if (event.key === "Escape") { event.stopPropagation(); this.close(); }
        return;
      }
      const step = event.shiftKey ? 0.1 : 1;
      if (event.key === "ArrowUp") { event.preventDefault(); this.adjust(-step); }
      if (event.key === "ArrowDown") { event.preventDefault(); this.adjust(step); }
    }

    onMove(e) {
      if (!this.locked) return;
      const list = typeof e.getCoalescedEvents === "function" ? e.getCoalescedEvents() : [];
      const sens = pxPerCount(this.view.h);
      for (const ev of list.length ? list : [e]) {
        const mx = ev.movementX || 0;
        const my = ev.movementY || 0;
        if (Math.abs(mx) > 900 || Math.abs(my) > 900) continue;
        this.cam.x += mx * sens;
        this.cam.y = clamp(this.cam.y + my * sens, -90 * pxPerDeg(this.view.h), 90 * pxPerDeg(this.view.h));
      }
    }

    onDown(e) {
      if (!this.locked || e.button !== 0) return;
      const hit = this.targets.find((t) => Math.hypot(this.cam.x - t.x, this.cam.y - t.y) <= t.r);
      this.streak = hit ? this.streak + 1 : 0;
      sound.play("click");
      if (hit) sound.play("hit", this.streak);
      if (!hit) {
        this.fx.push({ x: this.cam.x, y: this.cam.y, r: 5, age: 0, miss: true });
        return;
      }
      this.hits += 1;
      this.targets = [];
      this.fx.push({ x: hit.x, y: hit.y, r: hit.r, age: 0 });
      this.nextAt = performance.now() + rand(200, 450);
    }

    tick(wall) {
      const dt = Math.min(100, wall - (this.lastWall || wall));
      this.lastWall = wall;
      // Nothing is recorded here, so simply follow the stage (it goes
      // fullscreen just after opening).
      if (!this.view.h || (stage.w && stage.h && (stage.w !== this.view.w || stage.h !== this.view.h))) {
        sizeStage();
        this.view = { w: stage.w || 1280, h: stage.h || 720 };
      }
      const ppd = pxPerDeg(this.view.h);
      if (this.locked && !this.targets.length && performance.now() >= this.nextAt) {
        // Anywhere from a small correction to a wide flick, mostly sideways.
        const a = rand(0, Math.PI * 2);
        const deg = rand(12, 70);
        this.targets = [{ x: this.cam.x + Math.cos(a) * deg * ppd, y: clamp(this.cam.y + Math.sin(a) * deg * ppd * 0.45, -40 * ppd, 40 * ppd), r: FLICK_R }];
      }
      this.fx = this.fx.filter((f) => (f.age += dt) < 380);
      stage.phase.textContent = "Sensitivity test";
      stage.time.textContent = `${aim.cm360.toFixed(1)} cm/360`;
      stage.score.textContent = `${this.streak >= 2 ? `${this.streak}x combo · ` : ""}${this.hits} hits · ${Math.round(aim.dpi)} DPI`;
      drawScene({
        view: this.view, cam: this.cam, targets: this.targets, fx: this.fx, accent: this.accent,
        compass: { ppd, cm: aim.cm360, raw: this.raw },
      });
      this.frame = requestAnimationFrame(this.tick);
    }
  }

  function startTest() {
    if (session || replay || tester) return;
    tester = new SensTest();
    tester.open();
  }

  // ================================================================ replay

  let replay = null;

  class Replay {
    constructor(run) {
      const r = run.replay;
      this.layout = Array.isArray(r.phases) && r.phases.length ? r.phases : LEGACY_LAYOUT;
      this.view = r.view;
      this.F = r.frames;
      this.trackR = r.trackR || TRACK_R;
      this.t = 0;
      this.speed = 1;
      this.playing = true;
      this.end = this.F.t.length ? this.F.t[this.F.t.length - 1] : this.layout[this.layout.length - 1].end;
      this.accent = accentRgb();
      const spans = new Map();
      r.events.forEach((e) => {
        if (e.k === "s") spans.set(e.id, { ...e, start: e.t, end: Infinity });
        if (e.k === "r" && spans.has(e.id)) spans.get(e.id).end = e.t;
      });
      this.spans = [...spans.values()];
      this.byId = spans;
      this.clicks = r.events.filter((e) => e.k === "c");
      this.tick = this.tick.bind(this);
      this.onKey = this.onKey.bind(this);
    }

    open() {
      ensureStage();
      setLayout(this.layout);
      stage.root.hidden = false;
      stage.root.dataset.mode = "replay";
      stage.replay.hidden = false;
      stage.hud.hidden = false;
      showCenter("");
      sizeStage();
      const rp = stage.replay;
      rp.querySelector(".hz-seek").max = String(this.end);
      rp.onclick = (event) => {
        const btn = event.target.closest("button");
        if (!btn) return;
        if (btn.classList.contains("hz-play")) this.toggle();
        else if (btn.classList.contains("hz-close")) this.close();
        else if (btn.dataset.speed) { this.speed = Number(btn.dataset.speed); this.paintControls(); }
        else if (btn.dataset.jump != null) { this.t = this.layout[Number(btn.dataset.jump)].start; this.playing = true; this.paintControls(); }
      };
      rp.querySelector(".hz-seek").oninput = (event) => { this.t = Number(event.target.value); };
      document.addEventListener("keydown", this.onKey, true);
      this.paintControls();
      this.last = performance.now();
      this.frame = requestAnimationFrame(this.tick);
    }

    close() {
      cancelAnimationFrame(this.frame);
      document.removeEventListener("keydown", this.onKey, true);
      stage.replay.onclick = null;
      stage.root.hidden = true;
      replay = null;
    }

    toggle() {
      if (!this.playing && this.t >= this.end) this.t = 0;
      this.playing = !this.playing;
      this.paintControls();
    }

    onKey(event) {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); this.close(); }
      else if (event.key === " ") { event.preventDefault(); event.stopPropagation(); this.toggle(); }
      else if (event.key === "ArrowRight") { event.preventDefault(); this.t = Math.min(this.end, this.t + 5000); }
      else if (event.key === "ArrowLeft") { event.preventDefault(); this.t = Math.max(0, this.t - 5000); }
    }

    paintControls() {
      const rp = stage.replay;
      rp.querySelector(".hz-play").innerHTML = this.playing ? ICONS.pause : ICONS.play;
      rp.querySelectorAll("[data-speed]").forEach((b) => b.classList.toggle("active", Number(b.dataset.speed) === this.speed));
    }

    tick(wall) {
      const dt = Math.min(100, wall - this.last);
      this.last = wall;
      if (this.playing) {
        this.t += dt * this.speed;
        if (this.t >= this.end) { this.t = this.end; this.playing = false; this.paintControls(); }
      }
      const F = this.F;
      const t = this.t;
      const phase = phaseAt(this.layout, t);
      const key = this.layout[phase].key;
      const i = Math.max(0, lastIndexAtOrBefore(F.t, t));
      const [camX, camY] = F.t.length ? interp(F.t, F.x, F.y, t) : [0, 0];
      let track = null;
      if (key === "tracking" && F.tx[i] != null) {
        const [tx, ty] = interp(F.t, F.tx, F.ty, t);
        track = { x: tx, y: ty, r: this.trackR };
      }
      const targets = this.spans
        .filter((s) => s.start <= t && t < s.end)
        .map((s) => {
          const [x, y] = posAt(s, t);
          // Click reaction circles are red until the moment they turned green.
          const rgb = this.layout[s.p]?.key === "click" ? (s.go != null && t >= s.go ? CLICK_RGB.go : CLICK_RGB.wait) : undefined;
          return { x, y, r: s.r, rgb };
        });
      const fx = [];
      this.clicks.forEach((c) => {
        if (c.t > t || t - c.t >= 380) return;
        const span = c.hit != null ? this.byId.get(c.hit) : null;
        if (span) {
          const [x, y] = posAt(span, c.t);
          fx.push({ x, y, r: span.r, age: t - c.t });
        } else {
          fx.push({ x: c.x, y: c.y, r: 5, age: t - c.t, miss: true });
        }
      });
      // The last 450 ms of aim path, drawn in the world so flicks show up.
      // The view is re-centred between parts, so never trail across one.
      const from = Math.max(t - 450, this.layout[phase].start);
      const trail = [];
      for (let q = Math.max(0, lastIndexAtOrBefore(F.t, from)); q <= i && q < F.t.length; q += 1) {
        if (F.t[q] >= from) trail.push([F.x[q], F.y[q]]);
      }
      trail.push([camX, camY]);

      paintBar(t);
      stage.phase.textContent = `Replay · ${label(key)}`;
      stage.time.textContent = `${mmss(t)} / ${mmss(this.end)}`;
      stage.score.textContent = `${this.speed}x`;
      stage.replay.querySelector(".hz-clock").textContent = `${mmss(t)} / ${mmss(this.end)}`;
      const seek = stage.replay.querySelector(".hz-seek");
      if (document.activeElement !== seek) seek.value = String(Math.round(t));
      seek.style.setProperty("--hz-p", String(this.end > 0 ? t / this.end : 0));
      stage.replay.querySelectorAll("[data-jump]").forEach((b) => b.classList.toggle("active", Number(b.dataset.jump) === phase));
      drawScene({
        view: this.view, cam: { x: camX, y: camY }, targets, track, fx, trail, accent: this.accent,
        centre: key === "reaction" ? { ready: false } : null,
      });
      this.frame = requestAnimationFrame(this.tick);
    }
  }

  async function openReplay(runId) {
    if (!runId || replay || session) return;
    let res;
    try {
      res = await api()?.humanizer_run(runId);
    } catch (err) {
      res = { ok: false, error: String(err) };
    }
    if (!res?.ok) {
      notify(res?.error || "Could not load that run", "error");
      return;
    }
    replay = new Replay(res.run);
    replay.open();
  }

  // ================================================================ required runs

  /* With no current profile the rest of the menu stays locked: showTab()
   * asks locks() first and lands here instead. */
  function locks(tab) {
    return page.required && tab !== "humanizer";
  }

  let lastNudge = 0;
  function nudge() {
    const now = Date.now();
    if (now - lastNudge < 2500) return;
    lastNudge = now;
    notify("Finish the humanizer session first", "info");
  }

  function activeTab() {
    return document.querySelector(".tab-panel.active")?.id || null;
  }

  function enforce() {
    document.body.classList.toggle("hz-required", page.required);
    if (page.required && !session && activeTab() !== "humanizer" && typeof showTab === "function") showTab("humanizer");
  }

  // The deadline can pass while the menu is open.
  setInterval(() => {
    if (!page.loaded || page.required || session || !page.dueAt) return;
    if (Date.now() / 1000 >= page.dueAt) {
      page.required = true;
      page.reason = "expired";
      page.intro = false;
      render();
      enforce();
    }
  }, 60000);

  function requiredBanner() {
    if (!page.required) return "";
    const text = {
      missing: `Argus needs your humanizer profile before you continue. Complete the ${SESSION_MIN} minute session to unlock the rest of the app.`,
      outdated: `Your profile is from an older version of the humanizer. Run the new ${SESSION_MIN} minute session to continue.`,
      expired: `Your last run was more than ${page.rerunDays} days ago. Run it again to keep your profile current and unlock the app.`,
    }[page.reason] || `Complete the ${SESSION_MIN} minute session to continue.`;
    return `<div class="hz-required-note">${ICONS.lock}<p><strong>${page.reason === "missing" ? "Required before you continue" : "Rerun required"}</strong><span>${esc(text)}</span></p></div>`;
  }

  function dueText() {
    if (!page.dueAt) return "";
    const days = Math.ceil((page.dueAt * 1000 - Date.now()) / 86400000);
    if (days <= 0) return "Rerun due now";
    return days === 1 ? "Rerun due tomorrow" : `Rerun due in ${days} days`;
  }

  // ================================================================ page

  function hist(summary) {
    const h = summary.hist || [];
    if (!h.length) return '<div class="hz-hist is-empty"></div>';
    const max = Math.max(1, ...h);
    return `<div class="hz-hist">${h.map((v) => `<i style="height:${Math.max(8, (v / max) * 100)}%"></i>`).join("")}</div>`;
  }

  function renderIntro(box) {
    const canGoBack = Boolean(page.profile) && !page.required;
    box.innerHTML = `
      ${requiredBanner()}
      <header class="hz-hero">
        <div class="hz-hero-copy">
          <p class="eyebrow">HUMANIZER</p>
          <h2>${page.profile ? "Run the humanizer again" : "Record how you aim"}</h2>
          <p>A ${SESSION_MIN} minute session in ${LAYOUT.length} parts. Argus records how your mouse actually moves: how fast you react, how you flick, how far you overshoot and how smoothly you track. It keeps your typical values and leaves out anything abnormally high or low.${page.profile ? " The new run becomes your profile; older runs stay in the history." : ""}</p>
        </div>
      </header>
      <div class="hz-phases">
        ${LAYOUT.map((p, i) => `<article class="hz-phase-card" style="--i:${i}">
            <span class="hz-phase-icon">${ICONS[PHASE_INFO[p.key].icon]}</span>
            <p class="eyebrow">PART ${i + 1} · ${(p.end - p.start) / 1000} S</p>
            <h3>${esc(label(p.key))}</h3>
            <p>${esc(PHASE_INFO[p.key].blurb)}</p>
          </article>`).join("")}
      </div>
      <article class="hz-panel hz-setup">
        <div class="hz-sens">
          <span>
            <strong>Sensitivity</strong>
            <small>Use the cm/360 you play with. Your mouse DPI is needed to turn mouse movement into centimetres.</small>
          </span>
          <output id="hzCmOut"></output>
          <input type="range" id="hzCm" min="10" max="120" step="0.5" aria-label="cm per 360" />
          <div class="hz-sens-fields">
            <label><span>cm/360</span><input type="number" id="hzCmNum" min="${CM_MIN}" max="${CM_MAX}" step="0.1" /></label>
            <label><span>Mouse DPI</span><input type="number" id="hzDpi" min="100" max="32000" step="50" /></label>
            <button type="button" class="dash-secondary hz-test-btn" data-hz-page="test">${ICONS.react}<span>Test sensitivity</span></button>
          </div>
        </div>
        <div class="hz-sound">
          <label class="setting-toggle">
            <span>
              <strong>Sounds</strong>
              <small>A click on every shot and a chime on every hit that climbs with each hit in a row, in the session and the sensitivity test.</small>
            </span>
            <input type="checkbox" id="hzSoundOn"${sound.cfg.on ? " checked" : ""} />
            <i></i>
          </label>
          <div class="hz-sound-vol"${sound.cfg.on ? "" : " data-off"}>
            <span>Volume</span>
            <input type="range" id="hzSoundVol" min="0" max="100" step="1" value="${Math.round(sound.cfg.vol * 100)}" aria-label="Sound volume" />
            <output id="hzSoundOut">${Math.round(sound.cfg.vol * 100)}%</output>
            <button type="button" class="reset-btn" id="hzSoundCombo">Preview combo</button>
          </div>
        </div>
        <ul class="hz-notes">
          <li>The session fills the window and locks the mouse. <b>Esc</b> pauses it.</li>
          <li>Each part starts with a 3 second countdown. The run is only saved once all ${LAYOUT.length} parts are done.</li>
          <li>Your profile is due again every ${page.rerunDays} days. Nothing here changes how the aim modules behave.</li>
          ${canGoBack ? `<li>Warm-up runs the same ${WARMUP_LAYOUT.length} parts for ${WARMUP_PHASE_MS / 1000} seconds each, to get your hands going before a game. It shows how you did next to your profile and saves nothing.</li>` : ""}
        </ul>
        <div class="hz-setup-actions">
          ${canGoBack ? '<button type="button" class="dash-secondary" data-hz-page="back">Back to profile</button>' : ""}
          ${canGoBack ? `<button type="button" class="dash-secondary" data-hz-page="warmup">${ICONS.warm}<span>Warm up</span></button>` : ""}
          <button type="button" class="dash-primary" data-hz-page="start">${ICONS.play}<span>Start session</span></button>
        </div>
      </article>`;
    bindAimFields(box);
    bindSoundFields(box);
  }

  // Sound on/off and volume; changing either plays a sample hit.
  function bindSoundFields(box) {
    const on = box.querySelector("#hzSoundOn");
    const vol = box.querySelector("#hzSoundVol");
    const out = box.querySelector("#hzSoundOut");
    const row = box.querySelector(".hz-sound-vol");
    const paint = () => { if (window.ArgusTheme?.paintRangeFill) window.ArgusTheme.paintRangeFill(vol); };
    let lastSample = 0;
    const sample = () => {
      const now = performance.now();
      if (now - lastSample < 120) return;
      lastSample = now;
      sound.play("click");
      sound.play("hit");
    };
    // Eight hits in a row, so the climb can be heard.
    let comboTimers = [];
    box.querySelector("#hzSoundCombo").addEventListener("click", () => {
      comboTimers.forEach(clearTimeout);
      sound.ensure();
      comboTimers = Array.from({ length: 8 }, (_, i) => setTimeout(() => {
        sound.play("click");
        sound.play("hit", i + 1);
      }, i * 190));
    });
    on.addEventListener("change", () => {
      sound.cfg.on = on.checked;
      row.toggleAttribute("data-off", !on.checked);
      sound.save();
      sample();
    });
    vol.addEventListener("input", () => {
      sound.cfg.vol = Number(vol.value) / 100;
      out.textContent = `${vol.value}%`;
      sound.save();
      paint();
      sample();
    });
    paint();
  }

  /* The cm/360 slider and field stay in step. The field being typed in is
   * never overwritten. */
  function bindAimFields(box) {
    const slider = box.querySelector("#hzCm");
    const cm = box.querySelector("#hzCmNum");
    const dpi = box.querySelector("#hzDpi");
    const out = box.querySelector("#hzCmOut");
    const paint = (skip) => {
      if (skip !== slider) slider.value = String(clamp(aim.cm360, Number(slider.min), Number(slider.max)));
      if (skip !== cm) cm.value = aim.cm360.toFixed(1);
      if (skip !== dpi) dpi.value = String(Math.round(aim.dpi));
      out.textContent = `${aim.cm360.toFixed(1)} cm/360`;
      if (window.ArgusTheme?.paintRangeFill) window.ArgusTheme.paintRangeFill(slider);
    };
    const num = (input) => {
      const v = Number(input.value);
      return Number.isFinite(v) && v > 0 ? v : null;
    };
    slider.addEventListener("input", () => { aim.cm360 = Number(slider.value); saveAim(); paint(slider); });
    cm.addEventListener("input", () => {
      const v = num(cm);
      if (v && v >= CM_MIN && v <= CM_MAX) { aim.cm360 = v; saveAim(); paint(cm); }
    });
    dpi.addEventListener("input", () => {
      const v = num(dpi);
      if (v && v >= 100 && v <= 32000) { aim.dpi = v; saveAim(); paint(dpi); }
    });
    // Tidy whatever was typed once the field is left.
    [cm, dpi].forEach((input) => input.addEventListener("change", () => paint()));
    paint();
  }

  function renderOverview(box) {
    const p = page.profile;
    const m = p.metrics || {};
    const when = new Date(p.createdAt);
    const vp = Array.isArray(p.velocityProfile) ? p.velocityProfile : [];
    let peakAt = 0;
    vp.forEach((v, i) => { if (v > vp[peakAt]) peakAt = i; });
    const top = Math.max(1, ...vp);
    const pts = vp.map((v, i) => `${((i / Math.max(1, vp.length - 1)) * 100).toFixed(1)},${(44 - (v / top) * 40).toFixed(1)}`);
    const curve = pts.length
      ? `<svg viewBox="0 0 100 46" preserveAspectRatio="none" class="hz-curve" aria-hidden="true">
          <path class="hz-curve-area" d="M0,46 L${pts.join(" L")} L100,46 Z" />
          <path class="hz-curve-line" d="M${pts.join(" L")}" />
        </svg>`
      : '<p class="hz-muted">Not enough clean flicks to draw a shape.</p>';

    const card = (key, i) => {
      const s = m[key] || {};
      const meta = METRIC.get(key);
      return `<article class="hz-metric" style="--i:${i}" title="${esc(meta.desc)}">
          <span>${esc(meta.label)}</span>
          <b>${fmt(key, s.median)}</b>
          <small>${s.p10 != null ? `usually ${fmt(key, s.p10)} to ${fmt(key, s.p90)}` : "not enough data"}</small>
          ${hist(s)}
        </article>`;
    };

    const rows = METRICS.map(([key]) => {
      const s = m[key] || {};
      const meta = METRIC.get(key);
      return `<tr title="${esc(meta.desc)}">
          <td><strong>${esc(meta.label)}</strong><small>${esc(meta.desc)}</small></td>
          <td class="hz-num">${fmt(key, s.median)}</td>
          <td class="hz-num">${s.p10 != null ? `${fmt(key, s.p10)} – ${fmt(key, s.p90)}` : "—"}</td>
          <td class="hz-num">${fmt(key, s.std)}</td>
          <td class="hz-num">${s.n ?? 0}</td>
          <td class="hz-num${s.removed ? " is-cut" : ""}">${s.removed ?? 0}</td>
          <td>${hist(s)}</td>
        </tr>`;
    }).join("");

    const ph = p.phases || {};
    const part = (key, text) => (ph[key] ? `<div><span>${ICONS[PHASE_INFO[key].icon]}</span><p><strong>${label(key)}</strong><small>${text(ph[key])}</small></p></div>` : "");
    const clickPart = (s) => `${s.hits ?? 0} hits · ${(s.accuracy ?? 0).toFixed(1)}% accuracy${s.timeouts ? ` · ${s.timeouts} got away` : ""}`;
    const date = (d) => `${d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })} · ${d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`;
    const due = dueText();
    const paths = page.paths;
    const feeding = paths && paths.ok
      ? `Your aim is shaped by ${paths.flicks.toLocaleString("en-US")} flicks taken from ${paths.runs === 1 ? "this run" : `all ${paths.runs} runs`}. Deleting a run removes its flicks too.`
      : "Not enough recorded flicks yet to shape aim with.";
    box.innerHTML = `
      <header class="hz-hero">
        <div class="hz-hero-copy">
          <p class="eyebrow">HUMANIZER · ${esc(date(when))}${due ? ` · ${esc(due.toUpperCase())}` : ""}</p>
          <h2>Your aim profile</h2>
          <p>${Math.round(p.overall.score).toLocaleString("en-US")} points · ${p.overall.accuracy.toFixed(1)}% accuracy · ${p.overall.hits} hits · ${p.flicksAnalysed} flicks read${p.aim ? ` · ${esc(aimSummary(p.aim))}` : ""}${p.samplesHz ? ` · ~${p.samplesHz.toLocaleString("en-US")} samples/s` : ""}</p>
          ${p.aim && p.aim.rawInput === false ? '<p class="hz-raw-warn">Recorded without raw mouse input, so Windows pointer speed and acceleration were applied.</p>' : ""}
        </div>
        <div class="hz-hero-actions">
          <button type="button" class="dash-primary" data-hz-page="replay">${ICONS.replay}<span>Replay</span></button>
          <button type="button" class="dash-secondary" data-hz-page="warmup" title="${WARMUP_LAYOUT.length} parts, ${WARMUP_PHASE_MS / 1000} s each. Nothing is saved.">${ICONS.warm}<span>Warm up</span></button>
          <button type="button" class="dash-secondary" data-hz-page="rerun">${ICONS.rerun}<span>Rerun</span></button>
          <button type="button" class="dash-secondary${page.stats ? " is-on" : ""}" data-hz-page="stats">${ICONS.stats}<span>Stats</span></button>
        </div>
      </header>

      ${thin(p) ? `<div class="hz-warn"><strong>Not much to go on.</strong> This run had little clean data (${p.flicksAnalysed || 0} flicks, ${m.reaction_ms?.n || 0} reactions), so the numbers below are rough. Rerun the humanizer for a reliable profile.</div>` : ""}
      <div class="hz-metrics">${OVERVIEW_KEYS.map(card).join("")}</div>

      <article class="hz-panel hz-stats"${page.stats ? "" : " hidden"}>
        <div class="hz-panel-head"><div><p class="eyebrow">STATS</p><h3>Everything measured</h3></div></div>
        <div class="hz-table-wrap">
          <table class="hz-table">
            <thead><tr><th>Measure</th><th>Typical</th><th>Usual range</th><th>Spread</th><th>Kept</th><th>Outliers</th><th>Spread shape</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
        <p class="hz-muted">Typical is the median. Usual range covers the middle 80% of attempts and spread is one standard deviation. Values more than 1.5x the middle range above or below it count as abnormal and are left out (outliers), so a missed flick or a moment of distraction does not skew the profile.</p>
      </article>

      <div class="hz-split">
        <article class="hz-panel">
          <div class="hz-panel-head"><div><p class="eyebrow">FLICKS</p><h3>Flick shape</h3></div></div>
          ${curve}
          <p class="hz-muted">Average speed through a flick, from starting to move (left) to clicking (right).${vp.length ? ` You reach top speed about ${Math.round(((peakAt + 0.5) / vp.length) * 100)}% of the way through, then brake into the target.` : ""}</p>
        </article>
        <article class="hz-panel">
          <div class="hz-panel-head"><div><p class="eyebrow">PARTS</p><h3>By part</h3></div></div>
          <div class="hz-parts">
            ${part("gridshot", clickPart)}
            ${part("flicking", clickPart)}
            ${part("reaction", clickPart)}
            ${part("tracking", (s) => `${(s.onTargetPct ?? 0).toFixed(1)}% of the time on target`)}
            ${part("click", (s) => `${s.hits ?? 0} clicks on green · ${s.falseStarts ?? 0} false start${s.falseStarts === 1 ? "" : "s"}${s.timeouts ? ` · ${s.timeouts} too slow` : ""}`)}
          </div>
        </article>
      </div>

      <article class="hz-panel">
        <div class="hz-panel-head"><div><p class="eyebrow">HISTORY</p><h3>Past runs</h3></div><small>${page.runs.length} of up to ${page.paths?.maxRuns ?? 10} kept</small></div>
        <p class="hz-muted hz-feeding">${feeding}</p>
        <div class="hz-runs">${page.runs.map((r) => {
          const current = r.id === p.runId;
          const armed = page.deleting === r.id;
          return `<div class="hz-run${current ? " is-current" : ""}">
              <p><strong>${esc(r.createdAt ? date(new Date(r.createdAt)) : r.id)}</strong><small>${Math.round(r.score || 0).toLocaleString("en-US")} points · ${Number(r.accuracy || 0).toFixed(1)}% accuracy${current ? " · current profile" : ""}</small></p>
              <span class="hz-run-actions">
                <button type="button" class="reset-btn" data-hz-replay="${esc(r.id)}">${ICONS.replay}<span>Replay</span></button>
                <button type="button" class="reset-btn hz-del${armed ? " is-armed" : ""}" data-hz-delete="${esc(r.id)}" title="Delete this run">${ICONS.trash}<span>${armed ? "Delete?" : "Delete"}</span></button>
              </span>
            </div>`;
        }).join("") || '<p class="hz-muted">No runs yet.</p>'}</div>
      </article>`;
  }

  function render() {
    const box = el("hzPage");
    if (!box) return;
    if (!page.loaded) {
      box.innerHTML = '<div class="hz-loading"><i class="csi-spin"></i>Loading humanizer…</div>';
      return;
    }
    if (!page.profile || page.intro || page.required) renderIntro(box);
    else renderOverview(box);
  }

  async function load() {
    if (page.loading || !api()) return;
    page.loading = true;
    render();
    try {
      const res = await api().humanizer_state();
      if (res?.ok) applyState(res);
    } catch { /* bridge not ready yet */ }
    page.loading = false;
    if (page.loaded) {
      render();
      enforce();
    }
  }

  function bind() {
    const box = el("hzPage");
    box?.addEventListener("click", (event) => {
      const replayBtn = event.target.closest("[data-hz-replay]");
      if (replayBtn) {
        page.deleting = null;
        openReplay(replayBtn.dataset.hzReplay);
        return;
      }
      const deleteBtn = event.target.closest("[data-hz-delete]");
      if (deleteBtn) {
        deleteRun(deleteBtn.dataset.hzDelete);
        return;
      }
      // A click anywhere else takes back a pending delete.
      if (page.deleting) {
        page.deleting = null;
        render();
      }
      const act = event.target.closest("[data-hz-page]")?.dataset.hzPage;
      // This click is the user gesture that lets the page play sound.
      if ((act === "start" || act === "test") && sound.cfg.on) sound.ensure();
      if (act === "warmup" && sound.cfg.on) sound.ensure();
      if (act === "start") startSession();
      else if (act === "warmup") startSession({ warmup: true });
      else if (act === "test") startTest();
      else if (act === "rerun") { page.intro = true; render(); box.scrollIntoView({ block: "start" }); }
      else if (act === "back") { page.intro = false; render(); }
      else if (act === "replay") openReplay(page.profile?.runId);
      else if (act === "stats") {
        page.stats = !page.stats;
        render();
        if (page.stats) box.querySelector(".hz-stats")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
    // Check for a profile as soon as the app bridge is up, so a missing or
    // expired one locks the menu straight away.
    if (api()) load();
    else window.addEventListener("pywebviewready", load, { once: true });
  }

  /* Deleting a run throws away real data, so the first click only arms the
   * button and any other click disarms it again. */
  async function deleteRun(runId) {
    if (!runId || session || replay) return;
    if (page.deleting !== runId) {
      page.deleting = runId;
      render();
      return;
    }
    page.deleting = null;
    let res;
    try {
      res = await api()?.humanizer_delete_run(runId);
    } catch (err) {
      res = { ok: false, error: String(err) };
    }
    if (!res?.ok) {
      notify(res?.error || "Could not delete that run", "error");
      render();
      return;
    }
    applyState(res);
    render();
    enforce();
    const left = page.paths?.ok ? `${page.paths.flicks.toLocaleString("en-US")} flicks left` : "no flicks left to shape aim with";
    notify(`Run deleted, ${left}`, "success");
  }

  function onShow() {
    if (!page.loaded) load();
    else render();
  }

  window.ArgusHumanizer = { onShow, analyze, summarize, locks, nudge, start: startSession, test: startTest, reload: load };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, { once: true });
  else bind();
})();
