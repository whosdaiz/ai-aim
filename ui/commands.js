/* Commands for the search palette (Ctrl+K).
 *
 * Typing a command word turns the palette into a command line: "help" lists
 * everything, "on trigger" switches a module on, "set trigger fov 8" moves a
 * slider, "config CS2" loads a config. Anything that is not a command falls
 * through to the normal search, so the palette still finds pages and settings.
 *
 * Commands reuse the controls that already exist: switches are flipped and
 * sliders are moved through their own inputs, so config saving, setup flows
 * (CS2 inventory / stats) and validation all behave exactly as if clicked.
 */
(() => {
  const TERMINAL = '<svg viewBox="0 0 24 24"><rect x="3" y="4.5" width="18" height="15" rx="2.5" /><path d="m7 10 3 2.5L7 15M12.5 15H17" /></svg>';
  const INFO = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5.5M12 7.8v.4" /></svg>';

  const el = (id) => document.getElementById(id);
  const norm = (text) => String(text || "").toLowerCase().replace(/\s+/g, " ").trim();
  const words = (text) => norm(text).split(" ").filter(Boolean);
  const say = (message, kind = "success") => { if (typeof toast === "function") toast(message, kind); };
  const api = () => (window.pywebview && window.pywebview.api) || null;

  function icon(svg) {
    const span = document.createElement("span");
    span.innerHTML = svg;
    return span.firstChild;
  }

  function entry(fields) {
    return { kind: "Command", sub: "", page: "", icon: icon(TERMINAL), ...fields };
  }

  function info(title, sub = "", fields = {}) {
    return { kind: "Info", title, sub, page: "", icon: icon(INFO), group: fields.group || "Result", ...fields };
  }

  function hidden(node, stop) {
    return typeof hiddenWithin === "function" ? hiddenWithin(node, stop) : false;
  }

  function pageName(panel) {
    const tab = panel.dataset.tabPanel || panel.id;
    const nav = document.querySelector(`.sidebar .nav-item[data-tab="${tab}"] span`);
    return nav ? nav.textContent.trim() : tab;
  }

  // Every search word has to appear somewhere; a label that starts with the
  // first word ranks highest, so "on trig" puts Trigger above Magnet trigger.
  function score(label, hay, query) {
    const q = words(query);
    if (!q.length) return 1;
    const l = norm(label);
    const h = norm(hay);
    if (!q.every((w) => h.includes(w))) return -1;
    let s = l.startsWith(q[0]) ? 8 : 0;
    if (l === q.join(" ")) s += 10;
    q.forEach((w) => { if (l.includes(w)) s += 2; });
    return s;
  }

  function ranked(items, query, limit = 12) {
    return items
      .map((item) => ({ item, s: score(item.label, item.hay, query) }))
      .filter((x) => x.s >= 0)
      .sort((a, b) => b.s - a.s)
      .slice(0, limit)
      .map((x) => x.item);
  }

  // ---------------------------------------------------------------- targets

  function pages() {
    return [...document.querySelectorAll(".sidebar .nav-item[data-tab]")]
      .filter((nav) => getComputedStyle(nav).display !== "none")
      .map((nav) => {
        const label = nav.querySelector("span")?.textContent.trim() || nav.dataset.tab;
        return { label, hay: `${label} ${nav.dataset.tab}`, tab: nav.dataset.tab, svg: nav.querySelector("svg") };
      });
  }

  // Modules first, then every other on/off setting on any page.
  function switches() {
    const out = [];
    const seen = new Set();
    document.querySelectorAll("#mhList .mod-card").forEach((card) => {
      const input = card.querySelector(".module-pill input");
      if (!input) return;
      const label = card.querySelector(".module-copy strong")?.firstChild?.textContent.trim() || input.id;
      seen.add(input.id);
      out.push({
        label,
        hay: `${label} ${input.id.replace(/_/g, " ")} module`,
        input,
        page: "Module",
        locked: card.classList.contains("rp-only") && !document.body.classList.contains("input-rp2040"),
      });
    });
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.querySelectorAll(".setting-toggle").forEach((row) => {
        const input = row.querySelector('input[type="checkbox"]');
        if (!input || (input.id && seen.has(input.id)) || hidden(row, panel)) return;
        const label = row.querySelector("strong")?.textContent.trim();
        if (!label) return;
        const page = pageName(panel);
        out.push({ label, hay: `${label} ${page}`, input, page, locked: input.disabled });
      });
    });
    return out;
  }

  function sliders() {
    const out = [];
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.querySelectorAll('input[type="range"]').forEach((input) => {
        const row = input.closest(".setting-row, .theme-edit-name, label");
        if (!row || hidden(row, panel)) return;
        let label = row.querySelector("strong")?.textContent.trim() || input.id;
        if (input.classList.contains("range-min")) label += " (min)";
        if (input.classList.contains("range-max")) label += " (max)";
        const page = pageName(panel);
        out.push({ label, hay: `${label} ${page} ${input.id.replace(/_/g, " ")}`, input, page });
      });
    });
    return out;
  }

  function valueText(input, value = input.value) {
    if (input.id && typeof formatValue === "function" && typeof DECIMALS === "object" && input.id in DECIMALS) {
      return formatValue(input.id, value);
    }
    const out = input.id && document.querySelector(`output[for="${input.id}"]`);
    return value === input.value && out ? out.textContent.trim() : String(value);
  }

  function themes() {
    return [...document.querySelectorAll(".theme-option[data-theme]")].map((btn) => {
      const label = btn.querySelector("strong")?.textContent.trim() || btn.dataset.theme;
      return {
        label,
        hay: `${label} ${btn.dataset.theme} ${btn.querySelector("small")?.textContent || ""}`,
        btn,
        sub: btn.querySelector("small")?.textContent.trim() || "",
      };
    });
  }

  function configs() {
    const names = typeof configNames !== "undefined" && Array.isArray(configNames) ? configNames : [];
    return names.map((name) => ({ label: name, hay: name }));
  }

  function games() {
    const catalog = typeof GAMES === "object" && GAMES ? GAMES : {};
    return Object.values(catalog).map((game) => ({
      label: game.name || game.id,
      hay: `${game.name || ""} ${game.id}`,
      id: game.id,
    }));
  }

  // ---------------------------------------------------------------- actions

  function flip(target, value) {
    const input = target.input;
    if (target.locked) {
      say(`${target.label} needs RP2040 input. Switch it in Settings.`, "error");
      return;
    }
    const next = value == null ? !input.checked : Boolean(value);
    if (input.checked === next) {
      say(`${target.label} is already ${next ? "on" : "off"}`, "info");
      return;
    }
    input.checked = next;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    say(`${target.label} ${next ? "on" : "off"}`);
  }

  function setSlider(target, raw) {
    const input = target.input;
    const min = Number(input.min || 0);
    const max = Number(input.max || 100);
    const step = Number(input.step) || 1;
    let value = Math.min(max, Math.max(min, Number(raw)));
    value = Math.round((value - min) / step) * step + min;
    value = Number(value.toFixed(6));
    input.value = String(value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    if (window.ArgusTheme?.paintRangeFill) window.ArgusTheme.paintRangeFill(input);
    if (typeof repaintDualRanges === "function") repaintDualRanges();
    const clamped = Number(raw) !== value ? ` (limited to ${valueText(input, min)} to ${valueText(input, max)})` : "";
    say(`${target.page} · ${target.label} set to ${valueText(input, value)}${clamped}`);
  }

  async function loadConfig(name) {
    const bridge = api();
    if (!bridge) return;
    if (typeof switchingConfig !== "undefined" && switchingConfig) return;
    switchingConfig = true;
    clearTimeout(debounceTimer);
    pending = {};
    try {
      const result = await bridge.select_config(name);
      paintConfigs(result);
      if (result.settings) renderSettings(result.settings);
      setError(result.ok ? "" : result.error || "Could not load config");
      if (result.ok) say(`Loaded ${activeConfig || name}`);
    } finally {
      switchingConfig = false;
    }
  }

  async function newConfig(name) {
    const result = await api()?.create_config(name);
    if (!result?.ok) {
      say(result?.error || "Could not create config", "error");
      return;
    }
    paintConfigs(result);
    say(`Created config ${activeConfig || name}`);
  }

  async function loadGame(game) {
    const result = await api()?.select_game(game.id);
    if (!result?.ok) {
      say(result?.error || "Could not load game", "error");
      return;
    }
    if (result.settings) renderSettings(result.settings);
    say(`Game set to ${game.label}`);
  }

  function click(id, message) {
    const btn = el(id);
    if (!btn || btn.disabled) {
      say("That is not available right now", "error");
      return;
    }
    btn.click();
    if (message) say(message);
  }

  // ---------------------------------------------------------------- commands

  const COMMANDS = [
    {
      name: "help", aliases: ["?", "commands"], usage: "help",
      desc: "List every command and what it does.",
      suggest: () => COMMANDS.map((cmd) => entry({
        group: "Commands",
        title: cmd.usage,
        sub: cmd.desc + (cmd.example ? `  e.g. ${cmd.example}` : ""),
        fill: `${cmd.name} `,
      })),
    },
    {
      name: "go", aliases: ["open", "page"], usage: "go <page>", example: "go trigger",
      desc: "Open a page.",
      suggest: (args) => {
        const found = ranked(pages(), args, 16);
        if (!found.length) return [info(`No page matches "${args}"`, "Turned-off modules have no page until they are on.")];
        return found.map((p) => entry({
          group: "Open page", title: `Open ${p.label}`, icon: p.svg ? p.svg.cloneNode(true) : icon(TERMINAL),
          complete: `go ${p.label.toLowerCase()}`, run: () => showTab(p.tab),
        }));
      },
    },
    ...[["on", ["enable"], true, "Turn a module or switch on.", "on trigger"],
        ["off", ["disable"], false, "Turn a module or switch off.", "off dry run"],
        ["toggle", ["flip"], null, "Flip a module or switch.", "toggle visuals"]].map(([name, aliases, value, desc, example]) => ({
      name, aliases, usage: `${name} <module or switch>`, desc, example,
      suggest: (args) => {
        if (!args) {
          return [info(`Type what to switch ${name === "toggle" ? "" : name}`.trim(), "A module name like trigger, or a switch like dry run or prediction guide.")];
        }
        const found = ranked(switches(), args);
        if (!found.length) return [info(`Nothing to switch matches "${args}"`)];
        return found.map((t) => {
          const state = t.input.checked ? "on" : "off";
          const verb = value == null ? (t.input.checked ? "off" : "on") : value ? "on" : "off";
          return entry({
            group: "Switch",
            title: `Turn ${t.label} ${verb}`,
            sub: `${t.page} · currently ${state}${t.locked ? " · needs RP2040" : ""}`,
            complete: `${name} ${t.label.toLowerCase()}`,
            run: () => flip(t, value),
          });
        });
      },
    })),
    {
      name: "set", aliases: [], usage: "set <setting> <value>", example: "set trigger fov 8",
      desc: "Move a slider to a value.",
      suggest: (args) => {
        const parts = words(args);
        const last = parts[parts.length - 1];
        const hasValue = parts.length > 1 && last !== undefined && last !== "" && !Number.isNaN(Number(last));
        const query = hasValue ? parts.slice(0, -1).join(" ") : parts.join(" ");
        if (!query) return [info("Type a setting, then a number", "For example: set director fov 120")];
        const found = ranked(sliders(), query);
        if (!found.length) return [info(`No slider matches "${query}"`)];
        return found.map((t) => {
          const range = `${valueText(t.input, t.input.min)} to ${valueText(t.input, t.input.max)}`;
          if (hasValue) {
            return entry({
              group: "Set",
              title: `Set ${t.page} · ${t.label} to ${valueText(t.input, Number(last))}`,
              sub: `Now ${valueText(t.input)} · range ${range}`,
              run: () => setSlider(t, Number(last)),
            });
          }
          return entry({
            group: "Set",
            title: `${t.page} · ${t.label}`,
            sub: `Now ${valueText(t.input)} · range ${range} · add a number`,
            // Keep the page in the fill: "set fov" alone would match every FOV slider.
            fill: `set ${`${t.page} ${t.label}`.toLowerCase().replace(/[()·]/g, "").replace(/\s+/g, " ")} `,
          });
        });
      },
    },
    {
      name: "config", aliases: ["load", "cfg"], usage: "config <name>", example: "config CS2",
      desc: "Switch to a saved config.",
      suggest: (args) => {
        const found = ranked(configs(), args, 20);
        if (!found.length) return [info(`No config matches "${args}"`, "Use new <name> to create one.")];
        const current = typeof activeConfig === "string" ? activeConfig : "";
        return found.map((c) => entry({
          group: "Load config",
          title: `Load ${c.label}`,
          sub: c.label === current ? "Already loaded · reloads the saved version" : "Unsaved changes on the current config are dropped",
          complete: `config ${c.label}`,
          run: () => loadConfig(c.label),
        }));
      },
    },
    {
      name: "save", aliases: [], usage: "save",
      desc: "Save the current settings to the active config.",
      suggest: () => [entry({ group: "Run", title: `Save ${typeof activeConfig === "string" && activeConfig ? activeConfig : "config"}`, run: () => click("configSave") })],
    },
    {
      name: "new", aliases: ["create"], usage: "new <name>", example: "new Scoped",
      desc: "Create a config from the current settings.",
      suggest: (args) => {
        const name = String(args || "").trim();
        if (!name) return [info("Type a name for the new config")];
        return [entry({ group: "Run", title: `Create config "${name}"`, sub: "Starts from your current settings", run: () => newConfig(name) })];
      },
    },
    {
      name: "game", aliases: ["model"], usage: "game <name>", example: "game cs2",
      desc: "Switch the game and its detection model.",
      suggest: (args) => {
        const found = ranked(games(), args, 20);
        if (!found.length) return [info(`No game matches "${args}"`)];
        return found.map((g) => entry({
          group: "Game", title: `Switch to ${g.label}`, complete: `game ${g.label.toLowerCase()}`, run: () => loadGame(g),
        }));
      },
    },
    {
      name: "start", aliases: ["run"], usage: "start", desc: "Start screen capture and detection.",
      suggest: () => [entry({ group: "Run", title: "Start capture", run: () => click("start-capture") })],
    },
    {
      name: "stop", aliases: [], usage: "stop", desc: "Stop screen capture.",
      suggest: () => [entry({ group: "Run", title: "Stop capture", run: () => click("stop-capture") })],
    },
    {
      name: "theme", aliases: [], usage: "theme <name>", example: "theme neon",
      desc: "Change the menu theme.",
      suggest: (args) => {
        const found = ranked(themes(), args, 20);
        if (!found.length) return [info(`No theme matches "${args}"`)];
        return found.map((t) => entry({
          group: "Theme",
          title: `Theme: ${t.label}`,
          sub: `${t.sub}${t.btn.classList.contains("active") ? " · current" : ""}`,
          complete: `theme ${t.label.toLowerCase()}`,
          run: () => { t.btn.click(); say(`Theme set to ${t.label}`); },
        }));
      },
    },
    {
      name: "modules", aliases: ["list", "mods"], usage: "modules",
      desc: "Show which modules are on and off.",
      suggest: () => {
        const cards = [...document.querySelectorAll("#mhList .mod-card")];
        return cards
          .map((card) => {
            const on = Boolean(card.querySelector(".module-pill input")?.checked);
            const label = card.querySelector(".module-copy strong")?.firstChild?.textContent.trim() || card.dataset.mod;
            const key = card.querySelector(".mod-card-key:not([hidden])")?.textContent || "";
            return { on, entry: info(label, [on ? "On" : "Off", key].filter(Boolean).join(" · "), {
              group: on ? "On" : "Off",
              run: () => { showTab("modules"); window.ArgusModules?.select(card.dataset.mod); },
            }) };
          })
          .sort((a, b) => Number(b.on) - Number(a.on))
          .map((x) => x.entry);
      },
    },
    {
      name: "status", aliases: ["info"], usage: "status",
      desc: "Capture, game, config and performance at a glance.",
      suggest: async () => {
        const res = await api()?.get_status();
        const s = res?.status || {};
        const settings = typeof lastSettings === "object" && lastSettings ? lastSettings : {};
        const fmt = (v, d = 0) => (Number.isFinite(Number(v)) ? Number(v).toFixed(d) : "—");
        return [
          info("Capture", s.running ? "Running" : "Idle"),
          info("Game", el("dockGameName")?.textContent.trim() || "None selected"),
          info("Config", typeof activeConfig === "string" && activeConfig ? activeConfig : "—"),
          info("Input", String(settings.input_method || "windows") === "rp2040" ? "RP2040" : "Windows API"),
          info("Dry run", res?.dry_run ? "On (nothing is injected)" : "Off"),
          info("Performance", s.running
            ? `${fmt(s.loop_fps)} loop fps · ${fmt(s.vision_fps)} vision fps · ${fmt(s.vision_infer_ms, 1)} ms inference`
            : "Start capture to see live numbers"),
        ];
      },
    },
    {
      name: "hide", aliases: ["close"], usage: "hide",
      desc: "Hide the menu and hand focus back to the game.",
      suggest: () => [entry({ group: "Run", title: "Hide the menu", run: () => api()?.hide_menu() })],
    },
    {
      name: "clearlog", aliases: ["clearlogs", "cls"], usage: "clearlog",
      desc: "Clear the lines shown in the Logger.",
      suggest: () => [entry({ group: "Run", title: "Clear the logger view", run: () => click("loggerClear", "Logger cleared") })],
    },
  ];

  const BY_NAME = new Map();
  COMMANDS.forEach((cmd) => [cmd.name, ...(cmd.aliases || [])].forEach((n) => BY_NAME.set(n, cmd)));

  /* What the palette should show for the typed text, or null for plain search.
   * { entries, merge } where merge means "also show normal search results". */
  function resolve(text) {
    const raw = String(text || "").replace(/^\s+/, "");
    if (!raw) return null;
    const match = raw.match(/^(\S+)(?:\s+(.*))?$/);
    const head = match[1].toLowerCase();
    const args = (match[2] || "").trim();
    const cmd = BY_NAME.get(head);
    if (cmd) {
      const out = cmd.suggest(args);
      if (out && typeof out.then === "function") return out.then((entries) => ({ entries, merge: false }));
      return { entries: out, merge: false };
    }
    // Partly typed command word: offer the commands, keep normal search too.
    if (!/\s/.test(raw.trim())) {
      const starts = COMMANDS.filter((c) => [c.name, ...(c.aliases || [])].some((n) => n.startsWith(head) && n !== head));
      if (starts.length) {
        return {
          entries: starts.slice(0, 4).map((c) => entry({ group: "Commands", title: c.usage, sub: c.desc, fill: `${c.name} ` })),
          merge: true,
        };
      }
    }
    return null;
  }

  function tip() {
    return entry({
      group: "Commands",
      title: "Type help to see commands",
      sub: "Run actions from here, like on trigger, set trigger fov 8, or config CS2",
      fill: "help",
    });
  }

  window.ArgusCommands = { resolve, tip, list: COMMANDS };
})();
