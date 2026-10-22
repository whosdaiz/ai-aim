/* Modules tab: loadout header, filters, grouped cards, and the inspector panel.
 *
 * The cards hold the real module checkboxes (same ids as before), so app.js,
 * config saving, and the inventory/stats setup flows keep working unchanged.
 * app.js calls ArgusModules.refresh() at the end of every syncModules().
 */
(() => {
  const GROUPS = [
    ["aim", "Aim"],
    ["fire", "Fire"],
    ["tools", "Tools"],
    ["extras", "Extras"],
  ];
  const el = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const state = { selected: "", filter: "all", query: "" };

  const cards = () => [...document.querySelectorAll("#mhList .mod-card")];
  const cardFor = (id) => document.querySelector(`#mhList .mod-card[data-mod="${id}"]`);
  const inputOf = (card) => card?.querySelector(".module-pill input");
  const isOn = (card) => Boolean(inputOf(card)?.checked);
  const isLocked = (card) => card.classList.contains("rp-only") && !document.body.classList.contains("input-rp2040");

  function nameOf(card) {
    const strong = card.querySelector(".module-copy strong");
    return strong ? strong.firstChild.textContent.trim() : card.dataset.mod;
  }

  function settings() {
    return typeof lastSettings === "object" && lastSettings ? lastSettings : {};
  }

  function keyLabel(value) {
    return typeof bindLabel === "function" ? bindLabel(value) : String(value || "Unbound");
  }

  // Live key / mode shown on cards that have one.
  function keyChip(id) {
    const s = settings();
    if (id === "aim_director") {
      const mode = String(s.aim_director_mode || "always");
      if (mode === "always") return "Always on";
      return `${mode === "toggle" ? "Toggle" : "Hold"} · ${keyLabel(s.aim_director_key)}`;
    }
    if (id === "trigger_enabled") return `Hold · ${keyLabel(s.hold_trigger)}`;
    if (id === "magnet") return `Hold · ${keyLabel(s.magnet_key)}`;
    if (id === "tap_assist" || id === "rp_one_tap_assist") return "Your left click";
    if (id === "spray_acquire") return "While spraying";
    return "";
  }

  function navIcon(tab) {
    const svg = document.querySelector(`.sidebar .nav-item[data-tab="${tab}"] svg`);
    return svg ? svg.cloneNode(true) : null;
  }

  // ---------------------------------------------------------------- header

  function buildGroups() {
    const box = el("mhGroups");
    if (!box) return;
    box.innerHTML = GROUPS.map(([key, label]) => {
      const n = cards().filter((c) => c.dataset.group === key).length;
      return `<button type="button" class="mh-gstat" data-jump="${key}">
          <span>${esc(label)}</span>
          <b data-gcount="${key}">0/${n}</b>
          <i class="mh-pips">${cards().filter((c) => c.dataset.group === key).map((c) => `<em data-pip="${esc(c.dataset.mod)}"></em>`).join("")}</i>
        </button>`;
    }).join("");
  }

  function refresh() {
    const all = cards();
    if (!all.length) return;
    const on = all.filter(isOn);
    el("mhOnCount").textContent = String(on.length);
    el("mhOnWord").textContent = String(on.length);
    el("mhTotal").textContent = String(all.length);
    const arc = el("mhRingArc");
    if (arc) arc.style.setProperty("--p", String(all.length ? on.length / all.length : 0));
    const cfg = typeof activeConfig === "string" && activeConfig ? activeConfig : "";
    el("mhConfigNote").innerHTML = cfg
      ? `Saved with your <b>${esc(cfg)}</b> config. Press Save in the title bar to keep changes.`
      : "Switches are saved with your config.";

    GROUPS.forEach(([key]) => {
      const inGroup = all.filter((c) => c.dataset.group === key);
      const onCount = inGroup.filter(isOn).length;
      const text = `${onCount}/${inGroup.length}`;
      const g = document.querySelector(`[data-gcount="${key}"]`);
      if (g) g.textContent = text;
      const head = document.querySelector(`[data-group-count="${key}"]`);
      if (head) head.textContent = `${onCount} on`;
      const off = document.querySelector(`[data-group-off="${key}"]`);
      if (off) off.hidden = onCount === 0;
    });

    all.forEach((card) => {
      const id = card.dataset.mod;
      const pip = document.querySelector(`[data-pip="${id}"]`);
      if (pip) pip.classList.toggle("is-on", isOn(card));
      card.classList.toggle("is-locked", isLocked(card));
      const chip = card.querySelector(".mod-card-key");
      const text = keyChip(id);
      if (chip) {
        chip.hidden = !text;
        chip.textContent = text;
      }
      const stateEl = card.querySelector(".mod-card-state");
      if (stateEl) stateEl.innerHTML = `<i></i>${isLocked(card) ? "Needs RP2040" : isOn(card) ? "On" : "Off"}`;
    });
    applyFilter();
    if (state.selected) paintInspectorState();
  }

  // ---------------------------------------------------------------- filter + search

  function applyFilter() {
    const q = state.query.trim().toLowerCase();
    let shown = 0;
    cards().forEach((card) => {
      const matchesFilter = state.filter === "all" || (state.filter === "on" ? isOn(card) : !isOn(card));
      const text = card.querySelector(".module-copy")?.textContent.toLowerCase() || "";
      const matchesQuery = !q || q.split(/\s+/).every((w) => text.includes(w));
      const show = matchesFilter && matchesQuery;
      card.classList.toggle("is-filtered", !show);
      if (show) shown += 1;
    });
    document.querySelectorAll("#mhList .mh-group").forEach((group) => {
      group.hidden = !group.querySelector(".mod-card:not(.is-filtered)");
    });
    const empty = el("mhEmpty");
    if (empty) empty.hidden = shown > 0;
  }

  // ---------------------------------------------------------------- inspector

  function select(id, focusCard = false) {
    const card = cardFor(id);
    if (!card) return;
    state.selected = id;
    cards().forEach((c) => c.classList.toggle("is-selected", c === card));
    if (focusCard) card.focus({ preventScroll: true });
    renderInspector(card);
  }

  function renderInspector(card) {
    const box = el("mhInspector");
    if (!box) return;
    const id = card.dataset.mod;
    const tpl = card.querySelector("template.mod-card-detail");
    const detail = document.createElement("div");
    if (tpl) detail.append(tpl.content.cloneNode(true));
    const lead = detail.querySelector(".mod-lead")?.innerHTML || "";
    const facts = [...detail.querySelectorAll(".mod-facts span")].map((span) => {
      const label = span.querySelector("em")?.textContent || "";
      const value = span.textContent.replace(label, "").trim();
      return [label, value];
    });
    const steps = [...detail.querySelectorAll(".mod-steps > div")].map((d) => [d.querySelector("strong")?.textContent || "", d.querySelector("p")?.innerHTML || ""]);
    const rpTag = card.querySelector(".rp-tag") ? '<em class="mh-rp">RP2040</em>' : "";
    const key = keyChip(id);

    box.innerHTML = `<div class="mh-insp" data-for="${esc(id)}">
        <div class="mh-insp-head">
          <span class="mh-insp-icon"></span>
          <div class="mh-insp-title">
            <p class="eyebrow">${esc((GROUPS.find(([k]) => k === card.dataset.group) || ["", ""])[1].toUpperCase())}</p>
            <h3>${esc(nameOf(card))} ${rpTag}</h3>
          </div>
        </div>
        <div class="mh-insp-actions">
          <button type="button" class="mh-insp-toggle" data-insp="toggle"></button>
          <button type="button" class="reset-btn mh-insp-open" data-insp="open">Open settings
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg>
          </button>
        </div>
        <div class="mh-insp-lock" hidden>
          <span>Needs RP2040 input. It rewrites your real mouse's packets in hardware.</span>
          <button type="button" class="text-btn" data-insp="rp">Open Settings →</button>
        </div>
        ${key ? `<p class="mh-insp-key"><span>Key</span><b>${esc(key)}</b></p>` : ""}
        ${lead ? `<p class="mh-insp-lead">${lead}</p>` : ""}
        ${facts.length ? `<div class="mh-insp-facts">${facts.map(([l, v]) => `<div><span>${esc(l)}</span><b>${esc(v)}</b></div>`).join("")}</div>` : ""}
        ${steps.length ? `<ol class="mh-insp-steps">${steps.map(([t, p], i) => `<li style="--s:${i}"><strong>${esc(t)}</strong><p>${p}</p></li>`).join("")}</ol>` : ""}
      </div>`;
    const icon = navIcon(card.dataset.tab);
    if (icon) box.querySelector(".mh-insp-icon").append(icon);
    paintInspectorState();
  }

  function paintInspectorState() {
    const box = el("mhInspector");
    const card = cardFor(state.selected);
    if (!box || !card) return;
    const on = isOn(card);
    const locked = isLocked(card);
    box.querySelector(".mh-insp")?.classList.toggle("is-on", on);
    const toggle = box.querySelector('[data-insp="toggle"]');
    if (toggle) {
      toggle.textContent = on ? "Turn off" : "Turn on";
      toggle.classList.toggle("is-on", on);
      toggle.disabled = locked;
    }
    const open = box.querySelector('[data-insp="open"]');
    if (open) {
      open.disabled = !on || locked;
      open.title = on ? "" : "Turn it on to open its settings";
    }
    const lock = box.querySelector(".mh-insp-lock");
    if (lock) lock.hidden = !locked;
    const key = box.querySelector(".mh-insp-key b");
    if (key) key.textContent = keyChip(state.selected);
  }

  function flip(card, value) {
    const input = inputOf(card);
    if (!input || isLocked(card) || input.checked === value) return;
    input.checked = value;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // ---------------------------------------------------------------- events

  function bind() {
    const list = el("mhList");
    list?.addEventListener("click", (event) => {
      const off = event.target.closest("[data-group-off]");
      if (off) {
        cards().filter((c) => c.dataset.group === off.dataset.groupOff && isOn(c)).forEach((c) => flip(c, false));
        return;
      }
      if (event.target.closest(".module-pill")) return;
      const card = event.target.closest(".mod-card");
      if (card) select(card.dataset.mod);
    });
    list?.addEventListener("dblclick", (event) => {
      const card = event.target.closest(".mod-card");
      if (card && !event.target.closest(".module-pill") && isOn(card) && typeof showTab === "function") showTab(card.dataset.tab);
    });
    list?.addEventListener("keydown", (event) => {
      const card = event.target.closest(".mod-card");
      if (!card || event.target !== card) return;
      if (event.key === "Enter") {
        event.preventDefault();
        select(card.dataset.mod);
      } else if (event.key === " ") {
        event.preventDefault();
        flip(card, !isOn(card));
      } else if (["ArrowRight", "ArrowDown", "ArrowLeft", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        const visible = cards().filter((c) => !c.classList.contains("is-filtered"));
        const i = visible.indexOf(card);
        const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
        const next = visible[(i + step + visible.length) % visible.length];
        if (next) select(next.dataset.mod, true);
      }
    });
    // Turning a module on selects it so its details are right there.
    list?.addEventListener("change", (event) => {
      const card = event.target.closest(".mod-card");
      if (card && event.target.checked) select(card.dataset.mod);
      requestAnimationFrame(refresh);
    });

    el("mhInspector")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-insp]");
      const card = cardFor(state.selected);
      if (!btn || !card) return;
      if (btn.dataset.insp === "toggle") flip(card, !isOn(card));
      else if (btn.dataset.insp === "open" && typeof showTab === "function") showTab(card.dataset.tab);
      else if (btn.dataset.insp === "rp" && typeof showTab === "function") showTab("settings");
    });

    el("mhFilter")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-filter]");
      if (!btn) return;
      state.filter = btn.dataset.filter;
      el("mhFilter").querySelectorAll("[data-filter]").forEach((b) => b.classList.toggle("active", b === btn));
      applyFilter();
    });
    el("mhSearch")?.addEventListener("input", (event) => {
      state.query = event.currentTarget.value;
      applyFilter();
    });
    el("mhGroups")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-jump]");
      const group = btn && document.querySelector(`#mhList .mh-group[data-group="${btn.dataset.jump}"]`);
      if (!group) return;
      group.scrollIntoView({ block: "start", behavior: document.body.classList.contains("reduce-motion") ? "auto" : "smooth" });
      group.classList.remove("is-flash");
      void group.offsetWidth;
      group.classList.add("is-flash");
    });
  }

  function init() {
    cards().forEach((card) => {
      const icon = navIcon(card.dataset.tab);
      const slot = card.querySelector(".mod-card-icon");
      if (icon && slot && !slot.firstChild) slot.append(icon);
    });
    buildGroups();
    bind();
    refresh();
    const first = cards().find(isOn) || cards()[0];
    if (first) select(first.dataset.mod);
  }

  window.ArgusModules = { refresh, select };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
