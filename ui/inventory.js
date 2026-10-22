/* CS2 inventory module: setup flow, inventory tab, item detail, and API key manager. */
(() => {
  const RARITIES = ["gold", "covert", "classified", "restricted", "milspec", "industrial", "consumer", "other"];
  const RARITY_LABEL = {
    gold: "Gold", covert: "Covert", classified: "Classified", restricted: "Restricted",
    milspec: "Mil-Spec", industrial: "Industrial", consumer: "Consumer", other: "Other",
  };
  const RARITY_HEX = {
    gold: "#e4ae39", covert: "#eb4b4b", classified: "#d32ce6", restricted: "#8847ff",
    milspec: "#4b69ff", industrial: "#5e98d9", consumer: "#b0c3d9", other: "#73808f",
  };
  const CATS = [
    ["all", "All"], ["weapons", "Weapons"], ["knives", "Knives"], ["gloves", "Gloves"],
    ["agents", "Agents"], ["stickers", "Stickers"], ["cases", "Cases"], ["other", "Other"],
  ];
  const MAIN_CATS = new Set(["weapons", "knives", "gloves", "agents", "stickers", "cases"]);
  const CAT_COLORS = {
    weapons: "#69a7ff", knives: "#e4ae39", gloves: "#eb6b8b", agents: "#a57aff",
    stickers: "#5ec8e0", cases: "#c9865a", other: "#8b97a2",
  };
  const SORTS = [
    ["rarity", "Rarity"], ["price-desc", "Price: high to low"], ["price-asc", "Price: low to high"],
    ["newest", "Newest first"], ["oldest", "Oldest first"], ["name", "Name A to Z"], ["float", "Best float"],
  ];
  const FLOAT_TIERS = [
    { label: "FN", max: 0.07 }, { label: "MW", max: 0.15 }, { label: "FT", max: 0.38 },
    { label: "WW", max: 0.45 }, { label: "BS", max: 1 },
  ];
  const SKINNED = new Set(["weapons", "knives", "gloves"]);
  const PREFS_KEY = "argusInventoryView";
  const ICON = {
    lock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>',
    eye: '<svg viewBox="0 0 24 24"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></svg>',
    eyeOff: '<svg viewBox="0 0 24 24"><path d="M3 3l18 18" /><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.7 8.4 2 12 2 12s3.6 7 10 7a9.6 9.6 0 0 0 5.4-1.6" /><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" /></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2.2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></svg>',
    box: '<svg viewBox="0 0 24 24"><path d="M3.5 7.6 12 3.3l8.5 4.3v8.8L12 20.7l-8.5-4.3z" /><path d="M3.5 7.6 12 11.9l8.5-4.3M12 11.9v8.8" /></svg>',
  };

  const el = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const notify = (message, kind = "info") => { if (typeof toast === "function") toast(message, kind); };
  const hasPrice = (item) => item && item.priceCents != null && Number.isFinite(Number(item.priceCents));

  const state = {
    enabled: false,
    info: {},
    data: null,
    loading: false,
    loadError: "",
    needsSignIn: false,
    cooldownUntil: 0,
    pollTimer: 0,
    shownValue: 0,
    valueTarget: null,
    keyBadWarned: false,
    view: [],
    byKey: new Map(),
  };
  const prefs = loadPrefs();

  function loadPrefs() {
    const base = { cat: "all", sort: "rarity", rarities: [], trade: "any", flags: [], stack: true, size: 170 };
    try {
      const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
      return { ...base, ...saved, rarities: Array.isArray(saved.rarities) ? saved.rarities : [], flags: Array.isArray(saved.flags) ? saved.flags : [] };
    } catch {
      return base;
    }
  }

  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch { /* storage unavailable */ }
  }

  // ---------------------------------------------------------------- helpers

  function fmtUsd(cents) {
    const n = Number(cents);
    if (!Number.isFinite(n)) return "";
    return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(n / 100);
  }

  function rarityOf(item) {
    const key = `${item?.rarityName || ""} ${item?.type || ""} ${item?.name || ""}`.toLowerCase().replace(/[^a-z0-9]+/g, "");
    if (key.includes("highgrade")) return "milspec";
    if (key.includes("remarkable")) return "restricted";
    if (key.includes("exotic")) return "classified";
    if (key.includes("sticker") && key.includes("extraordinary")) return "covert";
    if (item?.rarity && item.rarity !== "other") return item.rarity;
    if (key.includes("extraordinary") || key.includes("contraband")) return "gold";
    return RARITIES.includes(item?.rarity) ? item.rarity : "other";
  }

  function catOf(item) {
    return MAIN_CATS.has(item?.category) ? item.category : "other";
  }

  function unlockAt(item) {
    if (!item || item.tradable) return 0;
    const m = /after\s+([A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})(?:\s*\((\d{1,2}:\d{2}(?::\d{2})?)\))?/i.exec(item.tradeHold || "");
    if (!m) return 0;
    const at = Date.parse(`${m[1].replace(".", "")} ${m[2] || "00:00:00"} GMT`);
    return Number.isFinite(at) ? at : 0;
  }

  function countdown(ms) {
    const sec = Math.max(0, Math.floor(ms / 1000));
    const d = Math.floor(sec / 86400);
    const h = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${m}m`;
    return `${Math.max(1, m)}m`;
  }

  function ago(ms) {
    const sec = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (sec < 45) return "just now";
    if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
    return new Date(ms).toLocaleDateString();
  }

  function wearText(value) {
    const wear = Number(value);
    if (!Number.isFinite(wear) || wear <= 0 || wear >= 1) return "";
    return wear.toFixed(8).replace(/0+$/, "").replace(/\.$/, "");
  }

  function splitName(item) {
    const raw = String(item.shortName || item.name || "Unknown");
    const idx = raw.indexOf(" | ");
    if (idx < 0) return { weapon: "", skin: raw };
    return { weapon: raw.slice(0, idx), skin: raw.slice(idx + 3) };
  }

  // Steam images carry different amounts of transparent padding. The backend measures
  // each image's visible area; cropping to it makes every item show at a similar size.
  function cropInset(box, margin = 0.035) {
    if (!Array.isArray(box) || box.length !== 4) return "";
    const [x0, y0, x1, y1] = box.map(Number);
    if (![x0, y0, x1, y1].every(Number.isFinite)) return "";
    const top = Math.max(0, y0 - margin);
    const left = Math.max(0, x0 - margin);
    const bottom = Math.max(0, 1 - y1 - margin);
    const right = Math.max(0, 1 - x1 - margin);
    if (top + bottom < 0.04 && left + right < 0.04) return "";
    const pct = (v) => `${(v * 100).toFixed(2)}%`;
    return `inset(${pct(top)} ${pct(right)} ${pct(bottom)} ${pct(left)})`;
  }

  function cropStyle(box) {
    const inset = cropInset(box);
    return inset ? ` style="object-view-box:${inset}"` : "";
  }

  function originalIcon(item) {
    return String(item.icon || item.iconLarge || "").replace(/\/\d+f?x\d+f?\/?$/, "");
  }

  const originalBoxes = new Map();

  async function cropHero(item) {
    const img = el("csiItemBody")?.querySelector(".csi-item-hero img");
    if (!img) return;
    const reveal = () => img.classList.remove("is-pending");
    const fallback = setTimeout(reveal, 1200);
    let box = originalBoxes.get(item.iconKey);
    if (!box && item.iconKey) {
      try {
        const res = await api()?.inv_icon_box_original(item.iconKey);
        if (res?.ok) {
          box = res.box;
          originalBoxes.set(item.iconKey, box);
        }
      } catch { /* show uncropped */ }
    }
    const inset = cropInset(box, 0.03);
    if (inset && img.isConnected) img.style.setProperty("object-view-box", inset);
    clearTimeout(fallback);
    reveal();
  }

  async function pollIconBoxes() {
    clearTimeout(state.boxTimer);
    if (!state.data) return;
    let res;
    try {
      res = await api()?.inv_icon_boxes();
    } catch {
      return;
    }
    if (!res?.ok) return;
    let changed = false;
    for (const item of state.data.items || []) {
      const box = res.boxes?.[item.iconKey];
      if (box && !item.box) {
        item.box = box;
        changed = true;
      }
    }
    if (changed) {
      el("csiGrid")?.querySelectorAll(".csi-card[data-key] .csi-art img").forEach((img) => {
        const row = state.byKey.get(img.closest(".csi-card").dataset.key);
        const inset = cropInset(row?.item.box);
        if (inset) img.style.setProperty("object-view-box", inset);
      });
    }
    if (res.pending) state.boxTimer = setTimeout(pollIconBoxes, 1500);
  }

  function floatBar(value) {
    const wear = Number(value);
    if (!Number.isFinite(wear) || wear <= 0 || wear >= 1) return "";
    const pos = Math.min(99.5, Math.max(0.5, wear * 100));
    return `<div class="csi-float" title="Float ${esc(wearText(wear))}"><i style="left:${pos}%"></i></div>`;
  }

  function tierOf(wear) {
    return FLOAT_TIERS.find((tier) => wear < tier.max) || FLOAT_TIERS[FLOAT_TIERS.length - 1];
  }

  function badges(item) {
    return [
      item.statTrak ? '<i class="csi-b-st" title="StatTrak">ST</i>' : "",
      item.souvenir ? '<i class="csi-b-sv" title="Souvenir">SV</i>' : "",
      item.stickers?.length ? `<i class="csi-b-stk" title="${item.stickers.length} sticker${item.stickers.length === 1 ? "" : "s"} applied">&#10022;${item.stickers.length}</i>` : "",
      item.nametag ? `<i class="csi-b-tag" title="Name tag: ${esc(item.nametag)}">TAG</i>` : "",
    ].join("");
  }

  function tabActive() {
    return Boolean(el("inventory")?.classList.contains("active"));
  }

  function openOverlay(node) {
    if (!node) return;
    node.hidden = false;
    requestAnimationFrame(() => node.classList.add("open"));
  }

  function closeOverlay(node) {
    if (!node) return;
    node.classList.remove("open");
    node.hidden = true;
  }

  async function openLink(url) {
    const href = String(url || "").trim();
    if (!href) return;
    try {
      const res = await api()?.open_url(href);
      if (res && !res.ok) notify(res.error || "Could not open link", "error");
    } catch {
      notify("Could not open link", "error");
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.cssText = "position:fixed;opacity:0;pointer-events:none";
      document.body.append(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    }
  }

  function syncShell() {
    if (typeof syncModules === "function") syncModules();
  }

  // ---------------------------------------------------------------- module state

  function applyInfo(info) {
    if (!info || typeof info !== "object" || info.ok === false) return;
    state.info = { ...state.info, ...info };
    if (info.cooldown) state.cooldownUntil = Date.now() + info.cooldown * 1000;
    paintAccount();
    paintHeroProfile();
    paintPricingUi();
  }

  async function loadInfo() {
    try {
      const info = await api()?.inv_state();
      if (!info) return;
      applyInfo(info);
    } catch { /* bridge not ready */ }
  }

  // On/off is part of the Argus config (cs2_inventory), like the aim modules.
  // app.js calls this whenever a config loads or switches.
  function applyConfig(on) {
    state.enabled = Boolean(on);
    const toggle = el("cs2_inventory");
    if (toggle) toggle.checked = state.enabled;
    if (!state.enabled) stopPricePoll();
  }

  function setEnabled(on) {
    applyConfig(on);
    if (typeof queueUpdate === "function") queueUpdate("cs2_inventory", state.enabled);
    syncShell();
  }

  async function onToggle(event) {
    const toggle = event.currentTarget;
    if (!toggle.checked) {
      setEnabled(false);
      notify("CS2 inventory turned off");
      return;
    }
    const steps = [];
    if (!state.info.signedIn) steps.push("steam");
    if (!state.info.pricing) steps.push("pricing");
    const ok = steps.length ? await runSetup(steps) : true;
    if (!ok) {
      toggle.checked = false;
      syncShell();
      return;
    }
    setEnabled(true);
    notify("CS2 inventory is on", "success");
    if (typeof showTab === "function") showTab("inventory");
  }

  // ---------------------------------------------------------------- setup flow

  const setup = { steps: [], index: 0, resolve: null, token: 0, signing: false };

  function paintSetupSteps() {
    const shown = new Set(setup.steps);
    if (shown.has("pricing")) shown.add("key");
    const order = ["steam", "pricing", "key", "leetify"].filter((s) => shown.has(s));
    const current = setup.steps[setup.index];
    const at = order.indexOf(current);
    document.querySelectorAll("#csiSteps li").forEach((li) => {
      const idx = order.indexOf(li.dataset.step);
      li.hidden = idx < 0 || order.length < 2;
      li.classList.toggle("done", idx >= 0 && idx < at);
      li.classList.toggle("active", idx === at);
    });
  }

  function showPane(name, back = false) {
    document.querySelectorAll("#csiSetupStage .csi-setup-pane").forEach((pane) => {
      const on = pane.dataset.pane === name;
      pane.classList.toggle("active", on);
      pane.classList.toggle("back", on && back);
    });
    paintSetupSteps();
    if (name === "steam") paintSteamPane(false);
    if (name === "pricing") paintWho();
    if (name === "leetify") {
      const input = el("csiLeetifyKey");
      if (input) {
        input.value = "";
        input.type = "password";
        setTimeout(() => input.focus(), 80);
      }
      el("csiLeetifyError").hidden = true;
    }
    if (name === "key") {
      const input = el("csiSetupKey");
      if (input) {
        input.value = "";
        input.type = "password";
        setTimeout(() => input.focus(), 80);
      }
      el("csiSetupError").hidden = true;
      const skip = document.querySelector('[data-setup="skip-key"]');
      if (skip) skip.textContent = setup.steps.includes("pricing") ? "Back" : "Cancel";
    }
  }

  function paintWho() {
    const who = el("csiSetupWho");
    if (!who) return;
    const info = state.info;
    if (!info.signedIn) {
      who.innerHTML = "";
      return;
    }
    const avatar = info.avatar ? `<img src="${esc(info.avatar)}" alt="">` : "";
    who.innerHTML = `${avatar}<span>Signed in as <strong>${esc(info.persona || info.steam64)}</strong></span>`;
  }

  function paintSteamPane(waiting) {
    const wait = el("csiSteamWait");
    const btn = document.querySelector('[data-setup="steam"]');
    if (wait) wait.hidden = !waiting;
    if (btn) {
      btn.disabled = waiting;
      btn.classList.toggle("is-busy", waiting);
      btn.textContent = waiting ? "Waiting for Steam…" : "Sign in with Steam";
    }
  }

  function runSetup(steps) {
    if (setup.resolve) finishSetup(false);
    return new Promise((resolve) => {
      setup.steps = steps.slice();
      setup.index = 0;
      setup.resolve = resolve;
      setup.token += 1;
      showPane(setup.steps[0]);
      openOverlay(el("csiSetup"));
    });
  }

  function finishSetup(ok) {
    const resolve = setup.resolve;
    setup.resolve = null;
    setup.token += 1;
    if (setup.signing) {
      setup.signing = false;
      api()?.inv_cancel_sign_in();
    }
    closeOverlay(el("csiSetup"));
    if (resolve) resolve(ok);
  }

  function nextStep() {
    setup.index += 1;
    if (setup.index >= setup.steps.length) finishSetup(true);
    else showPane(setup.steps[setup.index]);
  }

  async function setupSignIn() {
    if (setup.signing) return;
    const token = setup.token;
    setup.signing = true;
    paintSteamPane(true);
    let res;
    try {
      res = await api()?.inv_sign_in();
    } catch {
      res = { ok: false, error: "Could not open Steam sign-in." };
    }
    if (token !== setup.token) return;
    setup.signing = false;
    paintSteamPane(false);
    if (res && res.ok !== false && res.signedIn) {
      applyInfo(res);
      notify(`Signed in as ${res.persona || res.steam64}`, "success");
      nextStep();
      return;
    }
    if (res?.busy) return;
    notify(res?.cancelled ? "The Steam window was closed before signing in." : res?.error || "Could not sign in to Steam", "error");
  }

  async function setupSaveKey() {
    const input = el("csiSetupKey");
    const btn = document.querySelector('[data-setup="save-key"]');
    const error = el("csiSetupError");
    const value = String(input?.value || "").trim();
    if (!value) {
      error.textContent = "Paste your CSFloat key first.";
      error.hidden = false;
      input?.focus();
      return;
    }
    btn.classList.add("is-busy");
    btn.textContent = "Checking…";
    error.hidden = true;
    const token = setup.token;
    let res;
    try {
      res = await api()?.inv_save_key("csfloat", value);
    } catch {
      res = { ok: false, error: "Could not save the key." };
    }
    btn.classList.remove("is-busy");
    btn.textContent = "Save key";
    if (token !== setup.token) return;
    if (!res?.ok) {
      error.textContent = res?.error || "Could not save the key.";
      error.hidden = false;
      return;
    }
    state.info.pricing = true;
    state.info.keyBad = false;
    state.keyBadWarned = false;
    paintKeysSummary(res.keys);
    paintPricingUi();
    if (res.warning) notify(res.warning);
    else notify("CSFloat key saved. Prices are loading.", "success");
    nextStep();
    refreshPrices(true);
  }

  async function setupSaveLeetify() {
    const input = el("csiLeetifyKey");
    const btn = document.querySelector('[data-setup="save-leetify"]');
    const error = el("csiLeetifyError");
    const value = String(input?.value || "").trim();
    if (!value) {
      error.textContent = "Paste your Leetify key, or press Skip.";
      error.hidden = false;
      input?.focus();
      return;
    }
    btn.classList.add("is-busy");
    btn.textContent = "Checking\u2026";
    error.hidden = true;
    const token = setup.token;
    let res;
    try {
      res = await api()?.inv_save_key("leetify", value);
    } catch {
      res = { ok: false, error: "Could not save the key." };
    }
    btn.classList.remove("is-busy");
    btn.textContent = "Save key";
    if (token !== setup.token) return;
    if (!res?.ok) {
      error.textContent = res?.error || "Could not save the key.";
      error.hidden = false;
      return;
    }
    paintKeysSummary(res.keys);
    notify(res.warning || "Leetify key saved", res.warning ? "info" : "success");
    nextStep();
  }

  function bindSetup() {
    el("csiSetup")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-setup]");
      if (event.target === el("csiSetup")) {
        if (!setup.signing) finishSetup(false);
        return;
      }
      if (!btn) return;
      const act = btn.dataset.setup;
      if (act === "cancel") finishSetup(false);
      else if (act === "steam") setupSignIn();
      else if (act === "no-pricing") nextStep();
      else if (act === "yes-pricing") {
        setup.steps.splice(setup.index + 1, 0, "key");
        nextStep();
      } else if (act === "save-key") setupSaveKey();
      else if (act === "skip-key") {
        if (setup.steps.includes("pricing")) {
          setup.steps.splice(setup.index, 1);
          setup.index -= 1;
          showPane("pricing", true);
        } else finishSetup(false);
      } else if (act === "save-leetify") setupSaveLeetify();
      else if (act === "skip-leetify") nextStep();
    });
    el("csiSetupKey")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") setupSaveKey();
    });
    el("csiLeetifyKey")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") setupSaveLeetify();
    });
  }

  // ---------------------------------------------------------------- inventory data

  async function loadInventory(force = false) {
    if (state.loading) return;
    const bridge = api();
    if (!bridge) return;
    state.loading = true;
    state.loadError = "";
    paintRefresh();
    if (!state.data) renderSkeleton();
    let res;
    try {
      res = await bridge.inv_load(Boolean(force));
    } catch {
      res = { ok: false, error: "Could not reach Steam" };
    }
    state.loading = false;
    if (res?.cooldown) state.cooldownUntil = Date.now() + res.cooldown * 1000;
    if (!res?.ok) {
      state.needsSignIn = Boolean(res?.needsSignIn);
      state.loadError = res?.error || "Could not load inventory";
      if (state.data) notify(state.loadError, "error");
    } else {
      state.needsSignIn = false;
      if (res.data) state.data = res.data;
      if (res.notice) notify(res.notice);
      else if (force && !res.cached) notify(`Inventory updated · ${res.data?.count ?? 0} items`, "success");
    }
    paintRefresh();
    renderAll(true);
    startPricePoll();
    pollIconBoxes();
  }

  function startPricePoll() {
    stopPricePoll();
    if (!state.info.pricing || !state.data) return;
    state.pollTimer = setInterval(() => refreshPrices(false), 2500);
    refreshPrices(false);
  }

  function stopPricePoll() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = 0;
  }

  async function refreshPrices(force) {
    if (!state.data || (!force && !tabActive())) return;
    let res;
    try {
      res = await api()?.inv_prices();
    } catch {
      return;
    }
    if (!res?.ok) return;
    state.info.pricing = Boolean(res.hasKey);
    state.info.keyBad = Boolean(res.keyBad);
    if (res.keyBad && !state.keyBadWarned) {
      state.keyBadWarned = true;
      notify("CSFloat rejected your key. Update it in Settings › Manage API keys.", "error");
    }
    let changed = false;
    for (const item of state.data.items || []) {
      const row = res.prices?.[item.name];
      if (!row) continue;
      if (item.priceCents !== row.cents || item.priceState !== row.state) {
        item.priceCents = row.cents;
        item.priceState = row.state;
        item.priceListingId = row.listingId || "";
        changed = true;
      }
    }
    if (changed) {
      if (prefs.sort.startsWith("price")) renderGrid(false);
      else patchPrices();
      paintHero();
      paintStatus();
    }
    paintPricingUi();
    if (!res.pending && !res.loading && !res.queued) stopPricePoll();
  }

  // ---------------------------------------------------------------- view model

  function stackKey(item) {
    const hasFloat = item.wear != null && Number.isFinite(Number(item.wear)) && Number(item.wear) > 0;
    const unique = SKINNED.has(item.category) || hasFloat || item.stickers?.length || item.nametag;
    return unique ? `id:${item.id}` : `n:${item.name}|${item.tradable ? 1 : 0}`;
  }

  function matchesFilters(item, query) {
    if (prefs.cat !== "all" && catOf(item) !== prefs.cat) return false;
    if (prefs.rarities.length && !prefs.rarities.includes(rarityOf(item))) return false;
    if (prefs.trade === "tradable" && !item.tradable) return false;
    if (prefs.trade === "locked" && item.tradable) return false;
    for (const flag of prefs.flags) {
      if (flag === "statTrak" && !item.statTrak) return false;
      if (flag === "souvenir" && !item.souvenir) return false;
      if (flag === "stickers" && !item.stickers?.length) return false;
      if (flag === "nametag" && !item.nametag) return false;
    }
    if (query) {
      const hay = `${item.name} ${item.shortName} ${item.weapon} ${item.type} ${item.collection} ${item.nametag}`.toLowerCase();
      if (!query.split(/\s+/).every((word) => hay.includes(word))) return false;
    }
    return true;
  }

  function buildView() {
    const items = state.data?.items || [];
    const query = String(el("csiSearch")?.value || "").trim().toLowerCase();
    const rows = [];
    const stacks = new Map();
    for (const item of items) {
      if (!matchesFilters(item, query)) continue;
      const amount = Math.max(1, Number(item.amount) || 1);
      const key = prefs.stack ? stackKey(item) : `id:${item.id}`;
      const found = stacks.get(key);
      if (found) {
        found.qty += amount;
        found.ids.push(item.id);
        continue;
      }
      const row = { key, item, qty: amount, ids: [item.id] };
      stacks.set(key, row);
      rows.push(row);
    }
    const name = (row) => String(row.item.shortName || row.item.name || "").toLowerCase();
    const price = (row) => (hasPrice(row.item) ? Number(row.item.priceCents) : null);
    const rank = (id) => { try { return BigInt(String(id || "0")); } catch { return 0n; } };
    const cmpBig = (a, b) => (a > b ? 1 : a < b ? -1 : 0);
    const byRarity = (a, b) => RARITIES.indexOf(rarityOf(a.item)) - RARITIES.indexOf(rarityOf(b.item));
    const sorters = {
      rarity: (a, b) => byRarity(a, b) || (price(b) ?? -1) - (price(a) ?? -1) || name(a).localeCompare(name(b)),
      "price-desc": (a, b) => (price(b) ?? -1) - (price(a) ?? -1) || byRarity(a, b),
      "price-asc": (a, b) => (price(a) ?? Infinity) - (price(b) ?? Infinity) || byRarity(a, b),
      newest: (a, b) => cmpBig(rank(b.item.id), rank(a.item.id)),
      oldest: (a, b) => cmpBig(rank(a.item.id), rank(b.item.id)),
      name: (a, b) => name(a).localeCompare(name(b)),
      float: (a, b) => {
        const fa = Number.isFinite(Number(a.item.wear)) && a.item.wear > 0 ? Number(a.item.wear) : 2;
        const fb = Number.isFinite(Number(b.item.wear)) && b.item.wear > 0 ? Number(b.item.wear) : 2;
        return fa - fb || byRarity(a, b);
      },
    };
    rows.sort(sorters[prefs.sort] || sorters.rarity);
    return rows;
  }

  function totals(items) {
    let cents = 0, priced = 0, pending = 0, count = 0, tradable = 0;
    const byCat = {};
    for (const item of items) {
      const amount = Math.max(1, Number(item.amount) || 1);
      count += amount;
      if (item.tradable) tradable += amount;
      if (hasPrice(item)) {
        cents += Number(item.priceCents) * amount;
        priced += amount;
        byCat[catOf(item)] = (byCat[catOf(item)] || 0) + Number(item.priceCents) * amount;
      } else if (item.priceState === "pending") pending += amount;
    }
    return { cents, priced, pending, count, tradable, byCat };
  }

  // ---------------------------------------------------------------- rendering

  function renderAll(animate) {
    paintHero();
    paintCats();
    paintRarities();
    paintFilterUi();
    renderGrid(animate);
    paintStatus();
  }

  function renderSkeleton() {
    const grid = el("csiGrid");
    if (!grid) return;
    grid.innerHTML = Array.from({ length: 12 }, () =>
      '<article class="csi-card is-skeleton r-other"><div class="csi-art"></div><div class="csi-meta"><i></i><i></i><i></i></div></article>',
    ).join("");
    el("csiStatus").textContent = "Loading your inventory from Steam…";
  }

  function emptyState(title, text, action = "") {
    return `<div class="csi-empty">${ICON.box}<strong>${esc(title)}</strong><p>${esc(text)}</p>${action}</div>`;
  }

  function cardHtml(row, index, animate) {
    const item = row.item;
    const rarity = rarityOf(item);
    const { weapon, skin } = splitName(item);
    const sub = [item.exterior, weapon || (item.type && !item.type.includes("|") ? item.type : "")].filter(Boolean).join(" · ");
    const unlock = unlockAt(item);
    const lock = !item.tradable
      ? `<span class="csi-lock" title="${esc(item.tradeHold || "Not tradable")}">${ICON.lock}${unlock > Date.now() ? esc(countdown(unlock - Date.now())) : ""}</span>`
      : "";
    const img = item.icon ? `<img src="${esc(item.icon)}" alt="" loading="lazy" decoding="async" draggable="false"${cropStyle(item.box)}>` : "";
    const qty = row.qty > 1 ? `<em class="csi-qty">×${row.qty}</em>` : "";
    const anim = animate && index < 40 ? ` is-enter" style="--i:${index}` : "";
    return `<article class="csi-card r-${rarity}${anim}" data-key="${esc(row.key)}" tabindex="0" role="button" aria-label="${esc(item.shortName || item.name)}">
      <div class="csi-card-top"><span class="csi-badges">${badges(item)}</span>${qty}</div>
      <div class="csi-art">${img}</div>
      <div class="csi-meta">
        <small>${esc(sub || item.rarityName || RARITY_LABEL[rarity])}</small>
        <strong>${esc(skin)}</strong>
        <div class="csi-foot">${priceHtml(item)}${lock}</div>
        ${floatBar(item.wear)}
      </div>
    </article>`;
  }

  function priceHtml(item, fresh = false) {
    if (!state.info.pricing) return '<b class="csi-price is-empty"></b>';
    if (hasPrice(item)) return `<b class="csi-price${fresh ? " is-new" : ""}">${esc(fmtUsd(item.priceCents))}</b>`;
    if (item.priceState === "pending") return '<b class="csi-price is-pending" title="Looking up the lowest CSFloat listing"></b>';
    if (item.priceState === "keybad") return '<b class="csi-price is-none" title="CSFloat rejected your key">Key rejected</b>';
    return '<b class="csi-price is-none" title="No CSFloat buy-now listing for this exact item">No listing</b>';
  }

  function renderGrid(animate) {
    const grid = el("csiGrid");
    if (!grid) return;
    if (!state.data) {
      if (state.loading) return renderSkeleton();
      if (state.needsSignIn || !state.info.signedIn) {
        grid.innerHTML = emptyState("Sign in to Steam", "Sign in once and Argus keeps you signed in on this PC.", '<button type="button" class="csi-btn" data-csi="signin">Sign in with Steam</button>');
      } else if (state.loadError) {
        grid.innerHTML = emptyState("Could not load your inventory", state.loadError, '<button type="button" class="csi-btn" data-csi="retry">Try again</button>');
      } else {
        grid.innerHTML = emptyState("No inventory yet", "Hit Refresh to load your items from Steam.");
      }
      state.view = [];
      state.byKey = new Map();
      return;
    }
    const rows = buildView();
    state.view = rows;
    state.byKey = new Map(rows.map((row) => [row.key, row]));
    if (!rows.length) {
      const any = (state.data.items || []).length;
      grid.innerHTML = any
        ? emptyState("Nothing matches", "Try another type, clear a filter, or change the search.", '<button type="button" class="csi-btn csi-btn-ghost" data-csi="clear">Clear filters</button>')
        : emptyState("No CS2 items", "This inventory is empty. Hit Refresh to check again.");
      return;
    }
    grid.innerHTML = rows.map((row, i) => cardHtml(row, i, animate)).join("");
    grid.querySelectorAll(".csi-art img").forEach((img) => {
      if (img.complete && img.naturalWidth) img.classList.add("is-loaded");
    });
  }

  function patchPrices() {
    const grid = el("csiGrid");
    if (!grid) return;
    grid.querySelectorAll(".csi-card[data-key]").forEach((card) => {
      const row = state.byKey.get(card.dataset.key);
      const node = card.querySelector(".csi-price");
      if (!row || !node) return;
      const wasPrice = !node.classList.contains("is-pending") && !node.classList.contains("is-none") && !node.classList.contains("is-empty");
      const html = priceHtml(row.item, !wasPrice && hasPrice(row.item));
      if (node.outerHTML !== html) node.outerHTML = html;
    });
  }

  function paintStatus() {
    const status = el("csiStatus");
    if (!status || !state.data) return;
    const rows = state.view;
    const count = rows.reduce((sum, row) => sum + row.qty, 0);
    const shown = totals(rows.map((row) => ({ ...row.item, amount: row.qty })));
    const parts = [`<b>${count.toLocaleString()}</b> item${count === 1 ? "" : "s"}`];
    if (prefs.stack && rows.length !== count) parts.push(`${rows.length.toLocaleString()} stacks`);
    if (state.info.pricing && shown.priced) parts.push(`<b>${esc(fmtUsd(shown.cents))}</b> in view`);
    if (state.info.pricing && shown.pending) parts.push(`pricing ${shown.pending.toLocaleString()}…`);
    status.innerHTML = parts.join(" · ");
  }

  function animateValue(node, to) {
    if (!node || state.valueTarget === to) return;
    state.valueTarget = to;
    const from = state.shownValue;
    if (document.body.classList.contains("reduce-motion") || Math.abs(to - from) < 1) {
      state.shownValue = to;
      node.textContent = fmtUsd(to);
      return;
    }
    const start = performance.now();
    const dur = 900;
    const step = (now) => {
      if (state.valueTarget !== to) return;
      const t = Math.min(1, (now - start) / dur);
      state.shownValue = Math.round(from + (to - from) * (1 - Math.pow(1 - t, 3)));
      node.textContent = fmtUsd(state.shownValue);
      if (t < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function paintHeroProfile() {
    const info = state.info;
    const persona = el("csiPersona");
    if (persona) persona.textContent = info.persona || (info.signedIn ? "Your inventory" : "Not signed in");
    const wrap = el("csiAvatarWrap");
    const img = el("csiAvatar");
    if (img && wrap) {
      if (info.avatar && img.getAttribute("src") !== info.avatar) {
        img.onload = () => wrap.classList.add("has-img");
        img.src = info.avatar;
      }
      wrap.classList.toggle("has-img", Boolean(info.avatar) && img.complete && img.naturalWidth > 0);
    }
  }

  function paintHero() {
    paintHeroProfile();
    const data = state.data;
    const items = data?.items || [];
    const t = totals(items);
    const updated = el("csiUpdated");
    if (updated) {
      updated.textContent = data?.fetchedAt
        ? `Updated ${ago(Number(data.fetchedAt))}${state.info.steam64 ? ` · ${state.info.steam64}` : ""}`
        : state.loading ? "Loading from Steam…" : "Not loaded yet";
    }
    const value = el("csiValue");
    const valueNote = el("csiValueNote");
    if (!state.info.pricing) {
      if (value) value.textContent = "—";
      if (valueNote) valueNote.textContent = "Prices are off";
      state.shownValue = 0;
      state.valueTarget = null;
    } else if (t.priced) {
      animateValue(value, t.cents);
      if (valueNote) valueNote.textContent = t.pending ? `Pricing ${t.pending} more…` : `${t.priced.toLocaleString()} items priced by CSFloat`;
    } else {
      if (value) value.textContent = t.pending ? "…" : "—";
      state.shownValue = 0;
      state.valueTarget = null;
      if (valueNote) valueNote.textContent = t.pending ? "Loading CSFloat prices…" : "No prices yet";
    }
    el("csiCount").textContent = data ? t.count.toLocaleString() : "—";
    el("csiCountNote").textContent = data ? `${countUnique(items)} unique` : " ";
    el("csiTradable").textContent = data ? t.tradable.toLocaleString() : "—";
    el("csiTradableNote").textContent = data ? `${(t.count - t.tradable).toLocaleString()} trade locked` : " ";
    paintTop(items);
    paintMix(items, t);
  }

  function countUnique(items) {
    return new Set(items.map((item) => item.name)).size.toLocaleString();
  }

  function paintTop(items) {
    const btn = el("csiTop");
    if (!btn) return;
    let best = null;
    for (const item of items) {
      if (!best) { best = item; continue; }
      const a = hasPrice(item) ? Number(item.priceCents) : -1;
      const b = hasPrice(best) ? Number(best.priceCents) : -1;
      if (a > b || (a === b && RARITIES.indexOf(rarityOf(item)) < RARITIES.indexOf(rarityOf(best)))) best = item;
    }
    const art = el("csiTopArt");
    RARITIES.forEach((r) => btn.classList.remove(`r-${r}`));
    if (!best) {
      btn.disabled = true;
      el("csiTopName").textContent = "—";
      el("csiTopNote").textContent = " ";
      if (art) art.hidden = true;
      return;
    }
    const rarity = rarityOf(best);
    btn.disabled = false;
    btn.dataset.id = best.id;
    btn.classList.add(`r-${rarity}`);
    el("csiShell")?.style.setProperty("--csi-top-rgb", hexRgb(RARITY_HEX[rarity]));
    el("csiTopName").textContent = best.shortName || best.name;
    el("csiTopNote").textContent = hasPrice(best) ? fmtUsd(best.priceCents) : best.rarityName || RARITY_LABEL[rarity];
    if (art) {
      art.hidden = !best.icon;
      if (best.icon) art.src = best.icon;
    }
  }

  function hexRgb(hex) {
    const n = parseInt(String(hex || "#000").slice(1), 16);
    return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
  }

  function paintMix(items, t) {
    const mix = el("csiMix");
    if (!mix) return;
    const byValue = state.info.pricing && t.priced > 0;
    const weights = {};
    if (byValue) Object.assign(weights, t.byCat);
    else items.forEach((item) => { weights[catOf(item)] = (weights[catOf(item)] || 0) + Math.max(1, Number(item.amount) || 1); });
    const total = Object.values(weights).reduce((a, b) => a + b, 0);
    if (!total) {
      mix.hidden = true;
      return;
    }
    mix.hidden = false;
    const cats = CATS.map(([id]) => id).filter((id) => weights[id] > 0).sort((a, b) => weights[b] - weights[a]);
    const key = cats.map((c) => `${c}:${Math.round((weights[c] / total) * 1000)}`).join("|") + (byValue ? "v" : "c");
    if (mix.dataset.key === key) return;
    mix.dataset.key = key;
    el("csiMixBar").innerHTML = cats.map((c, i) =>
      `<i style="--c:${CAT_COLORS[c]};width:${(weights[c] / total) * 100}%;animation-delay:${i * 70}ms" title="${esc(labelOf(c))}"></i>`).join("");
    el("csiMixLegend").innerHTML = cats.map((c) => {
      const share = (weights[c] / total) * 100;
      const amount = byValue ? fmtUsd(weights[c]) : `${weights[c].toLocaleString()} items`;
      return `<span style="--c:${CAT_COLORS[c]}"><i></i>${esc(labelOf(c))} <b>${share < 1 ? "<1" : Math.round(share)}% · ${esc(amount)}</b></span>`;
    }).join("");
  }

  function labelOf(cat) {
    return (CATS.find(([id]) => id === cat) || [cat, cat])[1];
  }

  function paintCats() {
    const wrap = el("csiCats");
    if (!wrap) return;
    const items = state.data?.items || [];
    const counts = { all: 0 };
    items.forEach((item) => {
      const amount = Math.max(1, Number(item.amount) || 1);
      counts.all += amount;
      counts[catOf(item)] = (counts[catOf(item)] || 0) + amount;
    });
    const glider = wrap.querySelector(".csi-glider");
    wrap.querySelectorAll(".csi-cat").forEach((node) => node.remove());
    CATS.forEach(([id, label]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "csi-cat";
      btn.dataset.cat = id;
      btn.setAttribute("role", "tab");
      btn.classList.toggle("active", prefs.cat === id);
      btn.setAttribute("aria-selected", prefs.cat === id ? "true" : "false");
      btn.disabled = Boolean(state.data) && id !== "all" && !counts[id];
      btn.innerHTML = `${esc(label)}${state.data ? `<em>${(counts[id] || 0).toLocaleString()}</em>` : ""}`;
      wrap.append(btn);
    });
    if (glider) wrap.prepend(glider);
    requestAnimationFrame(moveGlider);
  }

  function moveGlider() {
    const wrap = el("csiCats");
    const glider = wrap?.querySelector(".csi-glider");
    const active = wrap?.querySelector(".csi-cat.active");
    if (!glider || !active || !active.offsetWidth) return;
    glider.style.width = `${active.offsetWidth}px`;
    glider.style.transform = `translateX(${active.offsetLeft}px)`;
  }

  function paintRarities() {
    const wrap = el("csiRarities");
    if (!wrap) return;
    const counts = {};
    (state.data?.items || []).forEach((item) => {
      if (prefs.cat !== "all" && catOf(item) !== prefs.cat) return;
      counts[rarityOf(item)] = (counts[rarityOf(item)] || 0) + Math.max(1, Number(item.amount) || 1);
    });
    wrap.innerHTML = RARITIES.filter((r) => counts[r] || prefs.rarities.includes(r)).map((r) =>
      `<button type="button" class="csi-rarity r-${r}${prefs.rarities.includes(r) ? " active" : ""}" data-rarity="${r}"><i></i>${RARITY_LABEL[r]}<em>${(counts[r] || 0).toLocaleString()}</em></button>`,
    ).join("") || '<span class="csi-status">No items yet</span>';
  }

  function paintFilterUi() {
    document.querySelectorAll("#csiTradeSeg [data-trade]").forEach((btn) => btn.classList.toggle("active", btn.dataset.trade === prefs.trade));
    document.querySelectorAll("#csiFlags [data-flag]").forEach((btn) => btn.classList.toggle("active", prefs.flags.includes(btn.dataset.flag)));
    const stack = el("csiStack");
    if (stack) stack.checked = prefs.stack;
    const size = el("csiSize");
    if (size) size.value = String(prefs.size);
    el("csiShell")?.style.setProperty("--csi-card", `${prefs.size}px`);
    if (window.ArgusTheme?.paintRangeFill && size) window.ArgusTheme.paintRangeFill(size);
    const active = prefs.rarities.length + prefs.flags.length + (prefs.trade !== "any" ? 1 : 0);
    const badge = el("csiFilterCount");
    if (badge) {
      badge.hidden = !active;
      badge.textContent = String(active);
    }
    const label = el("csiSortLabel");
    if (label) label.textContent = (SORTS.find(([id]) => id === prefs.sort) || SORTS[0])[1];
  }

  function paintRefresh() {
    const btn = el("csiRefresh");
    if (!btn) return;
    const label = btn.querySelector("span");
    const left = state.cooldownUntil - Date.now();
    btn.classList.toggle("is-loading", state.loading);
    if (state.loading) {
      btn.disabled = true;
      label.textContent = "Loading…";
    } else if (left > 0) {
      const mins = Math.max(1, Math.ceil(left / 60000));
      btn.disabled = true;
      label.textContent = mins >= 60 ? `Wait ${Math.ceil(mins / 60)}h` : `Wait ${mins}m`;
      btn.title = "Steam is rate-limiting inventory requests";
    } else {
      btn.disabled = !state.info.signedIn;
      label.textContent = "Refresh";
      btn.title = "";
    }
  }

  function paintPricingUi() {
    const add = el("csiAddPricing");
    if (!add) return;
    add.hidden = Boolean(state.info.pricing) && !state.info.keyBad;
    add.querySelector("span").textContent = state.info.keyBad ? "Fix CSFloat key" : "Add prices";
  }

  // ---------------------------------------------------------------- item detail

  function openItem(key) {
    const row = state.byKey.get(key) || findRowById(key);
    if (!row) return;
    const item = row.item;
    const rarity = rarityOf(item);
    const { weapon, skin } = splitName(item);
    const wear = Number(item.wear);
    const hasFloat = Number.isFinite(wear) && wear > 0 && wear < 1;
    const unlock = unlockAt(item);
    const total = totals(state.data?.items || []).cents;
    const facts = [];
    if (item.exterior) facts.push(["Exterior", item.exterior]);
    if (hasFloat) facts.push(["Float", wearText(wear)]);
    if (item.pattern != null && Number.isFinite(Number(item.pattern)) && SKINNED.has(item.category)) facts.push(["Pattern", String(item.pattern)]);
    facts.push(["Quantity", `×${row.qty}`]);
    facts.push(["Rarity", item.rarityName || RARITY_LABEL[rarity]]);
    if (SKINNED.has(item.category)) facts.push(["StatTrak", item.statTrak ? "Yes" : "No"]);
    facts.push(["Tradable", item.tradable ? "Yes" : "No"]);
    if (!item.tradable && unlock > Date.now()) facts.push(["Unlocks in", countdown(unlock - Date.now())]);
    facts.push(["Marketable", item.marketable ? "Yes" : "No"]);
    if (item.collection) facts.push(["Collection", item.collection]);
    if (item.nametag) facts.push(["Name tag", `“${item.nametag}”`]);
    if (hasPrice(item) && total > 0) {
      const share = ((Number(item.priceCents) * row.qty) / total) * 100;
      facts.push(["Share of value", `${share < 0.1 ? "<0.1" : share.toFixed(1)}%`]);
    }
    const ticks = hasFloat ? `<div class="csi-ticks">${FLOAT_TIERS.map((tier, i) => {
      const from = i ? FLOAT_TIERS[i - 1].max : 0;
      return `<span style="left:${from * 100}%;width:${(tier.max - from) * 100}%" class="${tierOf(wear) === tier ? "is-on" : ""}">${tier.label}</span>`;
    }).join("")}</div>` : "";
    const floatBlock = hasFloat ? `<div class="csi-item-float">
        <div class="csi-item-float-head"><span>Float</span><strong>${esc(wearText(wear))}</strong></div>
        ${floatBar(wear)}${ticks}
      </div>` : "";
    const price = !state.info.pricing ? "" : hasPrice(item)
      ? `<div class="csi-item-price"><strong>${esc(fmtUsd(item.priceCents))}</strong><small>${row.qty > 1 ? `${esc(fmtUsd(item.priceCents * row.qty))} for ${row.qty} · ` : ""}lowest CSFloat buy-now</small></div>`
      : `<div class="csi-item-price"><small>${item.priceState === "pending" ? "Looking up the CSFloat price…" : "No CSFloat buy-now listing for this exact item"}</small></div>`;
    const stickers = (item.stickers || []).map((s) => `<div class="csi-sticker">${s.icon ? `<img src="${esc(s.icon)}" alt="" draggable="false">` : ""}<span>${esc(s.name || "Sticker")}</span></div>`).join("");
    const contents = (item.contents || []).filter((c) => c?.name);
    const listed = new Set(contents.map((c) => String(c.name).toLowerCase()));
    const notes = (item.notes || []).filter((note) => note && !listed.has(String(note).toLowerCase())
      && !/contains one of the following/i.test(note)
      && !(contents.length && (String(note).includes("|") || /exceedingly rare/i.test(note)))
      && note !== item.tradeHold);
    const links = [];
    if (item.inspect && !/%propid:|%owner_steamid%|%assetid%/i.test(item.inspect)) links.push(["settings-save", "Inspect in game", item.inspect]);
    if (item.marketable !== false && item.name) links.push(["reset-btn", "Steam market", `https://steamcommunity.com/market/listings/730/${encodeURIComponent(item.name)}`]);
    if (state.info.steam64) links.push(["reset-btn", "Open on Steam", `https://steamcommunity.com/profiles/${state.info.steam64}/inventory/#730_${item.contextId || 2}_${item.id}`]);
    if (item.name && item.marketable !== false) {
      const params = new URLSearchParams({ sort_by: "lowest_price", type: "buy_now", market_hash_name: item.name });
      links.push(["reset-btn", "CSFloat", item.priceListingId ? `https://csfloat.com/item/${encodeURIComponent(item.priceListingId)}` : `https://csfloat.com/search?${params}`]);
    }
    const img = originalIcon(item);
    el("csiItemBody").innerHTML = `
      <div class="csi-item-hero r-${rarity}">${img ? `<img class="is-pending" src="${esc(img)}" alt="" draggable="false">` : ""}</div>
      <div class="csi-item-main r-${rarity}">
        <p class="csi-item-kicker"><span>${esc(labelOf(catOf(item)))} · ${esc(item.rarityName || RARITY_LABEL[rarity])}</span><span class="csi-badges">${badges(item)}</span></p>
        <h3 id="csiItemTitle">${weapon ? `<span>${esc(weapon)} | </span>` : ""}${esc(skin)}</h3>
        ${price}
        ${floatBlock}
        <div class="csi-facts">${facts.map(([label, value]) => {
          const tone = value === "Yes" ? "is-yes" : value === "No" ? "is-no" : "";
          return `<div><span>${esc(label)}</span><strong class="${tone}" title="${esc(value)}">${esc(value)}</strong></div>`;
        }).join("")}</div>
        ${stickers ? `<p class="csi-section-label">Stickers</p><div class="csi-stickers">${stickers}</div>` : ""}
        ${contents.length ? `<p class="csi-section-label">Contains</p><div class="csi-contents">${contents.map((c) => `<span class="r-${RARITIES.includes(c.rarity) ? c.rarity : "other"}">${esc(c.name)}</span>`).join("")}</div>` : ""}
        ${notes.length ? `<p class="csi-section-label">Details</p><div class="csi-notes">${notes.map((n) => `<p>${esc(n)}</p>`).join("")}</div>` : ""}
        ${links.length ? `<div class="csi-links">${links.map(([cls, label, href]) => `<button type="button" class="${cls}" data-link="${esc(href)}">${esc(label)}</button>`).join("")}</div>` : ""}
      </div>`;
    openOverlay(el("csiItem"));
    cropHero(item);
  }

  function findRowById(id) {
    const item = (state.data?.items || []).find((row) => String(row.id) === String(id));
    return item ? { key: `id:${item.id}`, item, qty: Math.max(1, Number(item.amount) || 1), ids: [item.id] } : null;
  }

  // ---------------------------------------------------------------- settings card + keys

  function paintAccount() {
    const info = state.info;
    const img = el("csiAccountAvatar");
    if (img) {
      if (info.avatar) img.src = info.avatar;
      else img.removeAttribute("src");
    }
    const name = el("csiAccountName");
    const meta = el("csiAccountMeta");
    const btn = el("csiAccountBtn");
    if (name) name.textContent = info.signedIn ? info.persona || "Steam account" : "Steam";
    if (meta) meta.textContent = info.signedIn ? `Signed in · ${info.steam64}` : "Not signed in";
    if (btn && btn.dataset.confirm !== "1") btn.textContent = info.signedIn ? "Sign out" : "Sign in";
    paintKeysSummary();
  }

  function paintKeysSummary(rows) {
    const summary = el("csiKeysSummary");
    if (!summary) return;
    if (Array.isArray(rows)) state.keys = rows;
    const keys = state.keys || [];
    const saved = keys.filter((k) => k.set);
    if (!keys.length) summary.textContent = state.info.pricing ? "CSFloat · saved" : "No keys saved";
    else summary.textContent = saved.length ? saved.map((k) => `${k.label} · ${k.unverified ? "unverified" : k.valid ? "saved" : "rejected"}`).join(", ") : "No keys saved";
  }

  async function onAccountButton() {
    const btn = el("csiAccountBtn");
    if (!state.info.signedIn) {
      const ok = await runSetup(["steam"]);
      if (ok && state.enabled && tabActive()) loadInventory(true);
      return;
    }
    if (btn.dataset.confirm !== "1") {
      btn.dataset.confirm = "1";
      btn.textContent = "Confirm sign out";
      btn.classList.add("is-on");
      clearTimeout(btn._timer);
      btn._timer = setTimeout(() => {
        btn.dataset.confirm = "";
        btn.classList.remove("is-on");
        paintAccount();
      }, 3500);
      return;
    }
    clearTimeout(btn._timer);
    btn.dataset.confirm = "";
    btn.classList.remove("is-on");
    btn.disabled = true;
    const res = await api()?.inv_sign_out();
    btn.disabled = false;
    state.data = null;
    state.shownValue = 0;
    state.valueTarget = null;
    stopPricePoll();
    applyInfo(res);
    renderAll(false);
    notify("Signed out of Steam");
    window.dispatchEvent(new CustomEvent("argus:steam-signout"));
  }

  const keyUi = {};

  async function openKeys() {
    openOverlay(el("csiKeys"));
    await refreshKeys();
  }

  async function refreshKeys() {
    try {
      const res = await api()?.inv_keys();
      if (res?.ok) paintKeysSummary(res.keys);
    } catch { /* ignore */ }
    renderKeys();
  }

  function renderKeys() {
    const list = el("csiKeyList");
    if (!list) return;
    list.innerHTML = (state.keys || []).map((key) => {
      const ui = keyUi[key.name] || (keyUi[key.name] = { mode: "view", revealed: "", msg: "", tone: "" });
      const pill = key.set
        ? key.unverified ? '<span class="csi-pill is-warn" title="Leetify&#39;s key check did not confirm this key. Stats still work.">Unverified</span>' : key.valid ? '<span class="csi-pill is-ok">Saved</span>' : '<span class="csi-pill is-bad">Rejected</span>'
        : '<span class="csi-pill is-off">Not set</span>';
      let body = "";
      if (ui.mode === "edit") {
        body = `<div class="csi-key-edit csi-key-input">
            <input type="password" data-key-input="${esc(key.name)}" autocomplete="off" spellcheck="false" placeholder="Paste your ${esc(key.label)} key" />
            <button type="button" class="csi-eye" data-key-act="peek" aria-label="Show key">${ICON.eye}</button>
          </div>
          <button type="button" class="text-btn csi-key-link" data-link="${esc(key.url)}">Get a key: ${esc(key.where)} &rarr;</button>
          ${ui.msg ? `<p class="csi-key-msg is-${ui.tone || "error"}">${esc(ui.msg)}</p>` : ""}
          <div class="csi-key-actions">
            <button type="button" class="reset-btn" data-key-act="cancel">Cancel</button>
            <button type="button" class="settings-save" data-key-act="save">${key.set ? "Update key" : "Save key"}</button>
          </div>`;
      } else if (ui.mode === "delete") {
        body = `<p class="csi-key-confirm">Delete the ${esc(key.label)} key? ${key.name === "csfloat" ? "Inventory prices stop until you add a key again." : ""}</p>
          <div class="csi-key-actions">
            <button type="button" class="reset-btn" data-key-act="cancel">Cancel</button>
            <button type="button" class="danger-btn" data-key-act="confirm-delete">Delete key</button>
          </div>`;
      } else if (key.set) {
        body = `<div class="csi-key-value">
            <code class="${ui.revealed ? "is-revealed" : ""}">${esc(ui.revealed || key.masked)}</code>
            <button type="button" class="csi-icon-btn${ui.revealed ? " is-on" : ""}" data-key-act="reveal" title="${ui.revealed ? "Hide key" : "View key"}" aria-label="${ui.revealed ? "Hide key" : "View key"}">${ui.revealed ? ICON.eyeOff : ICON.eye}</button>
            <button type="button" class="csi-icon-btn" data-key-act="copy" title="Copy key" aria-label="Copy key">${ICON.copy}</button>
          </div>
          ${ui.msg ? `<p class="csi-key-msg is-${ui.tone || "warn"}">${esc(ui.msg)}</p>` : ""}
          <div class="csi-key-actions">
            <button type="button" class="reset-btn" data-key-act="delete">Delete</button>
            <button type="button" class="settings-save" data-key-act="edit">Update</button>
          </div>`;
      } else {
        body = `<div class="csi-key-actions">
            <button type="button" class="settings-save" data-key-act="edit">Add key</button>
          </div>`;
      }
      return `<div class="csi-key" data-key-name="${esc(key.name)}">
          <div class="csi-key-head"><div><strong>${esc(key.label)}</strong><small>${esc(key.use)}</small></div>${pill}</div>
          ${body}
        </div>`;
    }).join("");
    const input = list.querySelector("[data-key-input]");
    if (input) setTimeout(() => input.focus(), 50);
  }

  async function keyAction(name, act, btn) {
    const ui = keyUi[name] || (keyUi[name] = { mode: "view", revealed: "", msg: "", tone: "" });
    const bridge = api();
    if (act === "edit") Object.assign(ui, { mode: "edit", msg: "", revealed: "" });
    else if (act === "delete") Object.assign(ui, { mode: "delete", msg: "" });
    else if (act === "cancel") Object.assign(ui, { mode: "view", msg: "" });
    else if (act === "peek") {
      const input = btn.parentElement.querySelector("input");
      input.type = input.type === "password" ? "text" : "password";
      btn.classList.toggle("is-on", input.type === "text");
      return;
    } else if (act === "reveal") {
      if (ui.revealed) ui.revealed = "";
      else {
        const res = await bridge?.inv_reveal_key(name);
        if (!res?.ok) return notify(res?.error || "Could not read the key", "error");
        ui.revealed = res.value;
      }
    } else if (act === "copy") {
      const res = ui.revealed ? { ok: true, value: ui.revealed } : await bridge?.inv_reveal_key(name);
      if (!res?.ok) return notify(res?.error || "Could not read the key", "error");
      notify((await copyText(res.value)) ? "Key copied" : "Could not copy the key", "success");
      return;
    } else if (act === "save") {
      const input = el("csiKeyList").querySelector(`[data-key-input="${CSS.escape(name)}"]`);
      const value = String(input?.value || "").trim();
      if (!value) {
        ui.msg = "Paste a key first.";
        ui.tone = "error";
      } else {
        btn.classList.add("is-busy");
        btn.textContent = "Checking…";
        const res = await bridge?.inv_save_key(name, value);
        if (!res?.ok) {
          ui.msg = res?.error || "Could not save the key.";
          ui.tone = "error";
        } else {
          paintKeysSummary(res.keys);
          Object.assign(ui, { mode: "view", revealed: "", msg: res.warning || "", tone: "warn" });
          notify(`${name === "csfloat" ? "CSFloat" : name} key saved`, "success");
          if (name === "csfloat") onPricingChanged(true);
        }
      }
    } else if (act === "confirm-delete") {
      const res = await bridge?.inv_delete_key(name);
      if (!res?.ok) return notify(res?.error || "Could not delete the key", "error");
      paintKeysSummary(res.keys);
      Object.assign(ui, { mode: "view", revealed: "", msg: "" });
      notify("Key deleted");
      if (name === "csfloat") onPricingChanged(false);
    }
    renderKeys();
  }

  function onPricingChanged(on) {
    state.info.pricing = on;
    state.info.keyBad = false;
    state.keyBadWarned = false;
    paintPricingUi();
    paintKeysSummary();
    if (!state.data) return;
    refreshPrices(true).then(() => {
      renderGrid(false);
      paintHero();
      paintStatus();
      if (on) startPricePoll();
    });
  }

  // ---------------------------------------------------------------- events

  function bindHoverLight(grid) {
    let frame = 0;
    let last = null;
    grid.addEventListener("pointermove", (event) => {
      last = event;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        const card = last.target.closest?.(".csi-card:not(.is-skeleton)");
        if (!card) return;
        const rect = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${((last.clientX - rect.left) / rect.width) * 100}%`);
        card.style.setProperty("--my", `${((last.clientY - rect.top) / rect.height) * 100}%`);
      });
    });
  }

  function closeSortMenu() {
    const menu = el("csiSortMenu");
    if (menu) menu.hidden = true;
    el("csiSortBtn")?.setAttribute("aria-expanded", "false");
  }

  function bindTab() {
    const grid = el("csiGrid");
    if (!grid) return;
    bindHoverLight(grid);
    grid.addEventListener("load", (event) => {
      if (event.target.tagName === "IMG") event.target.classList.add("is-loaded");
    }, true);
    grid.addEventListener("error", (event) => {
      if (event.target.tagName === "IMG") event.target.remove();
    }, true);
    grid.addEventListener("click", (event) => {
      const action = event.target.closest("[data-csi]");
      if (action) {
        const act = action.dataset.csi;
        if (act === "signin") onAccountButton();
        else if (act === "retry") loadInventory(true);
        else if (act === "clear") clearFilters();
        return;
      }
      const card = event.target.closest(".csi-card[data-key]");
      if (card) openItem(card.dataset.key);
    });
    grid.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const card = event.target.closest(".csi-card[data-key]");
      if (!card) return;
      event.preventDefault();
      openItem(card.dataset.key);
    });
    el("csiRefresh")?.addEventListener("click", () => loadInventory(true));
    el("csiAddPricing")?.addEventListener("click", async () => {
      const ok = await runSetup(["key"]);
      if (ok) onPricingChanged(true);
    });
    el("csiTop")?.addEventListener("click", (event) => {
      const id = event.currentTarget.dataset.id;
      if (id) openItem(`id:${id}`);
    });
    el("csiCats")?.addEventListener("click", (event) => {
      const btn = event.target.closest(".csi-cat");
      if (!btn || btn.disabled || prefs.cat === btn.dataset.cat) return;
      prefs.cat = btn.dataset.cat;
      savePrefs();
      el("csiCats").querySelectorAll(".csi-cat").forEach((node) => {
        node.classList.toggle("active", node === btn);
        node.setAttribute("aria-selected", node === btn ? "true" : "false");
      });
      moveGlider();
      paintRarities();
      renderGrid(true);
      paintStatus();
    });
    let searchTimer = 0;
    el("csiSearch")?.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { renderGrid(false); paintStatus(); }, 120);
    });
    el("csiSearch")?.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && event.currentTarget.value) {
        event.stopPropagation();
        event.currentTarget.value = "";
        renderGrid(false);
        paintStatus();
      }
    });
    const sortMenu = el("csiSortMenu");
    el("csiSortBtn")?.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!sortMenu.hidden) return closeSortMenu();
      sortMenu.innerHTML = SORTS.map(([id, label]) => `<button type="button" role="option" data-sort="${id}" class="${prefs.sort === id ? "active" : ""}">${esc(label)}</button>`).join("");
      sortMenu.hidden = false;
      event.currentTarget.setAttribute("aria-expanded", "true");
    });
    sortMenu?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-sort]");
      if (!btn) return;
      prefs.sort = btn.dataset.sort;
      savePrefs();
      closeSortMenu();
      paintFilterUi();
      renderGrid(true);
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest("#csiSortDrop")) closeSortMenu();
    });
    el("csiFilterBtn")?.addEventListener("click", (event) => {
      const panel = el("csiFilters");
      panel.hidden = !panel.hidden;
      event.currentTarget.setAttribute("aria-expanded", panel.hidden ? "false" : "true");
      if (!panel.hidden && window.ArgusTheme?.paintRangeFill) window.ArgusTheme.paintRangeFill(el("csiSize"));
    });
    el("csiRarities")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-rarity]");
      if (!btn) return;
      const r = btn.dataset.rarity;
      prefs.rarities = prefs.rarities.includes(r) ? prefs.rarities.filter((x) => x !== r) : [...prefs.rarities, r];
      savePrefs();
      btn.classList.toggle("active", prefs.rarities.includes(r));
      paintFilterUi();
      renderGrid(true);
      paintStatus();
    });
    el("csiTradeSeg")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-trade]");
      if (!btn || prefs.trade === btn.dataset.trade) return;
      prefs.trade = btn.dataset.trade;
      savePrefs();
      paintFilterUi();
      renderGrid(true);
      paintStatus();
    });
    el("csiFlags")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-flag]");
      if (!btn) return;
      const f = btn.dataset.flag;
      prefs.flags = prefs.flags.includes(f) ? prefs.flags.filter((x) => x !== f) : [...prefs.flags, f];
      savePrefs();
      paintFilterUi();
      renderGrid(true);
      paintStatus();
    });
    el("csiStack")?.addEventListener("change", (event) => {
      prefs.stack = event.currentTarget.checked;
      savePrefs();
      renderGrid(true);
      paintStatus();
    });
    el("csiSize")?.addEventListener("input", (event) => {
      prefs.size = Number(event.currentTarget.value) || 170;
      el("csiShell")?.style.setProperty("--csi-card", `${prefs.size}px`);
      if (window.ArgusTheme?.paintRangeFill) window.ArgusTheme.paintRangeFill(event.currentTarget);
    });
    el("csiSize")?.addEventListener("change", savePrefs);
    el("csiClear")?.addEventListener("click", clearFilters);
    window.addEventListener("resize", () => { if (tabActive()) moveGlider(); });
  }

  function clearFilters() {
    Object.assign(prefs, { cat: "all", rarities: [], trade: "any", flags: [] });
    const search = el("csiSearch");
    if (search) search.value = "";
    savePrefs();
    renderAll(true);
  }

  function bindDialogs() {
    el("csiItemClose")?.addEventListener("click", () => closeOverlay(el("csiItem")));
    el("csiItem")?.addEventListener("click", (event) => {
      if (event.target === el("csiItem")) closeOverlay(el("csiItem"));
      const link = event.target.closest("[data-link]");
      if (link) openLink(link.dataset.link);
    });
    el("csiKeysClose")?.addEventListener("click", () => closeOverlay(el("csiKeys")));
    el("csiKeys")?.addEventListener("click", (event) => {
      if (event.target === el("csiKeys")) return closeOverlay(el("csiKeys"));
      const link = event.target.closest("[data-link]");
      if (link) return openLink(link.dataset.link);
      const btn = event.target.closest("[data-key-act]");
      const row = btn?.closest("[data-key-name]");
      if (btn && row) keyAction(row.dataset.keyName, btn.dataset.keyAct, btn);
    });
    el("csiKeys")?.addEventListener("keydown", (event) => {
      const input = event.target.closest("[data-key-input]");
      if (input && event.key === "Enter") keyAction(input.dataset.keyInput, "save", input.closest(".csi-key").querySelector('[data-key-act="save"]'));
    });
    document.addEventListener("click", (event) => {
      const link = event.target.closest("#csiSetup [data-link]");
      if (link) openLink(link.dataset.link);
      const eye = event.target.closest("[data-eye]");
      if (eye) {
        const input = el(eye.dataset.eye);
        input.type = input.type === "password" ? "text" : "password";
        eye.classList.toggle("is-on", input.type === "text");
      }
    });
    el("csiManageKeys")?.addEventListener("click", openKeys);
    el("csiAccountBtn")?.addEventListener("click", onAccountButton);
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (!el("csiItem")?.hidden) { closeOverlay(el("csiItem")); event.stopPropagation(); return; }
        if (!el("csiKeys")?.hidden) { closeOverlay(el("csiKeys")); event.stopPropagation(); return; }
        if (!el("csiSetup")?.hidden) { finishSetup(false); event.stopPropagation(); return; }
        return;
      }
      if (event.key === "/" && tabActive() && !event.ctrlKey && !event.metaKey) {
        const tag = document.activeElement?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || document.querySelector(".confirm-overlay.open, .palette-overlay.open")) return;
        event.preventDefault();
        el("csiSearch")?.focus();
      }
    }, true);
  }

  // ---------------------------------------------------------------- scroll rail
  // Argus hides native scrollbars, so the inventory gets its own: a draggable thumb,
  // click-to-jump track, and a section index for the current sort that appears on hover.

  const rail = { root: null, track: null, thumb: null, marks: null, top: null, frame: 0, drag: null, sections: [], geo: null, dirty: true };

  function scroller() {
    return document.querySelector(".main");
  }

  function sectionOf(item) {
    if (prefs.sort === "rarity") {
      const r = rarityOf(item);
      return { key: r, label: RARITY_LABEL[r], color: RARITY_HEX[r] };
    }
    if (prefs.sort === "name") {
      const ch = String(item.shortName || item.name || "").trim().charAt(0).toUpperCase();
      const key = /[A-Z]/.test(ch) ? ch : /[0-9]/.test(ch) ? "0-9" : "#";
      return { key, label: key };
    }
    if (prefs.sort === "price-desc" || prefs.sort === "price-asc") {
      if (!state.info.pricing) return null;
      const cents = hasPrice(item) ? Number(item.priceCents) : null;
      const band = cents == null ? ["none", "No price"]
        : cents >= 10000 ? ["100", "$100+"] : cents >= 1000 ? ["10", "$10+"] : cents >= 100 ? ["1", "$1+"] : ["0", "Under $1"];
      return { key: band[0], label: band[1] };
    }
    if (prefs.sort === "float") {
      const wear = Number(item.wear);
      if (item.wear == null || !Number.isFinite(wear) || wear <= 0) return { key: "none", label: "No float" };
      const tier = tierOf(wear);
      return { key: tier.label, label: tier.label };
    }
    return null;
  }

  function buildRail() {
    const root = document.createElement("div");
    root.className = "csi-rail";
    root.innerHTML = `<div class="csi-rail-track"><div class="csi-rail-marks"></div><div class="csi-rail-thumb"></div></div>`;
    const top = document.createElement("button");
    top.type = "button";
    top.className = "csi-top-btn";
    top.setAttribute("aria-label", "Back to top");
    top.innerHTML = '<svg viewBox="0 0 24 24"><path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" /></svg>';
    document.body.append(root, top);
    Object.assign(rail, {
      root, top,
      track: root.querySelector(".csi-rail-track"),
      thumb: root.querySelector(".csi-rail-thumb"),
      marks: root.querySelector(".csi-rail-marks"),
    });

    const smooth = () => (document.body.classList.contains("reduce-motion") ? "auto" : "smooth");
    top.addEventListener("click", () => scroller()?.scrollTo({ top: 0, behavior: smooth() }));
    root.addEventListener("wheel", (event) => {
      event.preventDefault();
      scroller()?.scrollBy({ top: event.deltaY * 2 });
    }, { passive: false });

    rail.track.addEventListener("pointerdown", (event) => {
      const geo = rail.geo;
      const sc = scroller();
      if (!geo || !sc || event.button !== 0) return;
      const label = event.target.closest(".csi-rail-mark");
      if (label) {
        sc.scrollTo({ top: Number(label.dataset.scroll) || 0, behavior: smooth() });
        return;
      }
      event.preventDefault();
      const rect = rail.track.getBoundingClientRect();
      if (event.target === rail.thumb) {
        rail.drag = { y: event.clientY, scroll: sc.scrollTop };
      } else {
        const y = event.clientY - rect.top - geo.thumbH / 2;
        const target = (Math.min(Math.max(y, 0), geo.travel) / geo.travel) * geo.max;
        sc.scrollTo({ top: target, behavior: smooth() });
        rail.drag = { y: event.clientY, scroll: target };
      }
      rail.track.setPointerCapture(event.pointerId);
      root.classList.add("is-dragging");
    });
    rail.track.addEventListener("pointermove", (event) => {
      const geo = rail.geo;
      const sc = scroller();
      if (!rail.drag || !geo || !sc) return;
      const delta = ((event.clientY - rail.drag.y) / Math.max(1, geo.travel)) * geo.max;
      sc.scrollTo({ top: rail.drag.scroll + delta, behavior: "auto" });
    });
    const endDrag = () => {
      rail.drag = null;
      root.classList.remove("is-dragging");
    };
    rail.track.addEventListener("pointerup", endDrag);
    rail.track.addEventListener("pointercancel", endDrag);
  }

  function scheduleRail(remeasure = false) {
    if (remeasure) rail.dirty = true;
    if (rail.frame) return;
    rail.frame = requestAnimationFrame(() => {
      rail.frame = 0;
      updateRail();
    });
  }

  function measureRail(sc) {
    const titlebar = document.querySelector(".titlebar");
    const topEdge = Math.max(12, (titlebar?.getBoundingClientRect().bottom || 0) + 12);
    rail.root.style.top = `${topEdge}px`;
    const trackH = rail.track.clientHeight;
    const max = Math.max(0, sc.scrollHeight - sc.clientHeight);
    const thumbH = Math.max(44, Math.min(trackH, (trackH * sc.clientHeight) / Math.max(1, sc.scrollHeight)));
    rail.geo = { max, trackH, thumbH, travel: Math.max(1, trackH - thumbH) };
    rail.thumb.style.height = `${thumbH}px`;

    const scTop = sc.getBoundingClientRect().top;
    const sections = [];
    let prev = null;
    el("csiGrid")?.querySelectorAll(".csi-card[data-key]").forEach((card) => {
      const row = state.byKey.get(card.dataset.key);
      const sec = row && sectionOf(row.item);
      if (!sec) return;
      if (prev && prev.key === sec.key) {
        prev.count += row.qty;
        return;
      }
      const scroll = Math.min(max, Math.max(0, card.getBoundingClientRect().top - scTop + sc.scrollTop - 96));
      prev = { ...sec, count: row.qty, scroll };
      sections.push(prev);
    });
    rail.sections = sections;
    let lastY = -Infinity;
    rail.marks.innerHTML = sections.length > 1 ? sections.map((sec) => {
      const y = (max ? sec.scroll / max : 0) * rail.geo.travel + thumbH / 2;
      const showLabel = y - lastY >= 26;
      if (showLabel) lastY = y;
      const color = sec.color || "var(--accent)";
      return `<div class="csi-rail-mark${showLabel ? "" : " is-tick"}" data-scroll="${Math.round(sec.scroll)}" style="top:${y.toFixed(1)}px;--c:${color}">
          <b></b><span><i></i>${esc(sec.label)}<em>${sec.count.toLocaleString()}</em></span>
        </div>`;
    }).join("") : "";
    rail.dirty = false;
  }

  function updateRail() {
    if (!rail.root) return;
    const sc = scroller();
    const show = Boolean(sc && tabActive() && state.data && sc.scrollHeight - sc.clientHeight > 40);
    rail.root.classList.toggle("is-on", show);
    rail.top.classList.toggle("is-on", show && sc.scrollTop > 700);
    if (!show) return;
    if (rail.dirty || !rail.geo || rail.geo.max !== Math.max(0, sc.scrollHeight - sc.clientHeight)) measureRail(sc);
    const { max, travel } = rail.geo;
    rail.thumb.style.transform = `translateY(${(max ? sc.scrollTop / max : 0) * travel}px)`;
  }

  function bindRail() {
    buildRail();
    const sc = scroller();
    sc?.addEventListener("scroll", () => scheduleRail(), { passive: true });
    window.addEventListener("resize", () => scheduleRail(true));
    const grid = el("csiGrid");
    if (grid) new MutationObserver(() => scheduleRail(true)).observe(grid, { childList: true });
    const shell = el("csiShell");
    if (shell && window.ResizeObserver) new ResizeObserver(() => scheduleRail(true)).observe(shell);
    const panel = el("inventory");
    if (panel) new MutationObserver(() => scheduleRail(true)).observe(panel, { attributes: true, attributeFilter: ["class"] });
  }

  // ---------------------------------------------------------------- lifecycle

  function onShow() {
    paintCats();
    paintFilterUi();
    paintRefresh();
    if (!state.data && !state.loading) {
      if (state.info.signedIn) loadInventory(false);
      else renderAll(false);
    } else {
      paintHero();
      if (state.info.pricing && !state.pollTimer) startPricePoll();
    }
  }

  setInterval(() => {
    paintRefresh();
    if (tabActive() && state.data) {
      const updated = el("csiUpdated");
      if (updated && state.data.fetchedAt) updated.textContent = `Updated ${ago(Number(state.data.fetchedAt))}${state.info.steam64 ? ` · ${state.info.steam64}` : ""}`;
    }
  }, 30000);

  function init() {
    el("cs2_inventory")?.addEventListener("change", onToggle);
    bindSetup();
    bindTab();
    bindDialogs();
    bindRail();
    paintFilterUi();
    loadInfo().then(() => api()?.inv_keys?.().then((res) => res?.ok && paintKeysSummary(res.keys)).catch(() => {}));
  }

  window.ArgusInventory = {
    get info() { return { ...state.info }; },
    runSetup,
    refreshInfo: loadInfo,
    get enabled() { return state.enabled; },
    applyConfig,
    onShow,
  };

  if (window.pywebview && window.pywebview.api) init();
  else window.addEventListener("pywebviewready", init, { once: true });
})();
