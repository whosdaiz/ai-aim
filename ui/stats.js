/* CS2 stats module: Leetify profile, ratings, performance, map ranks and matches. */
(() => {
  const MAP_NAMES = {
    de_dust2: "Dust II", de_mirage: "Mirage", de_inferno: "Inferno", de_nuke: "Nuke", de_ancient: "Ancient",
    de_anubis: "Anubis", de_vertigo: "Vertigo", de_overpass: "Overpass", de_train: "Train", de_cache: "Cache",
    de_edin: "Edin", cs_italy: "Italy", cs_office: "Office", cs_agency: "Agency", de_thera: "Thera",
    de_mills: "Mills", de_basalt: "Basalt", de_memento: "Memento", de_assembly: "Assembly", de_palais: "Palais",
    de_whistle: "Whistle", de_brewery: "Brewery", de_dogtown: "Dogtown", de_jura: "Jura", de_grail: "Grail",
    de_golden: "Golden", de_rooftop: "Rooftop", de_sanctum: "Sanctum", ar_shoots: "Shoots", ar_baggage: "Baggage",
    ar_pool_day: "Pool Day",
  };
  const COMP_RANKS = [
    "Silver I", "Silver II", "Silver III", "Silver IV", "Silver Elite", "Silver Elite Master",
    "Gold Nova I", "Gold Nova II", "Gold Nova III", "Gold Nova Master", "Master Guardian I",
    "Master Guardian II", "Master Guardian Elite", "Distinguished Master Guardian", "Legendary Eagle",
    "Legendary Eagle Master", "Supreme Master First Class", "Global Elite",
  ];
  const PREMIER_TIERS = [
    [30000, "#fed700"], [25000, "#eb4b4b"], [20000, "#e02fe6"], [15000, "#b76cff"],
    [10000, "#6b83ff"], [5000, "#8dc6ff"], [0, "#b0c3d9"],
  ];
  const PAGE = 20;

  const el = (id) => document.getElementById(id);
  const api = () => (window.pywebview && window.pywebview.api) || null;
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);
  const notify = (message, kind = "info") => { if (typeof toast === "function") toast(message, kind); };
  const isNum = (v) => v != null && v !== "" && Number.isFinite(Number(v));

  const state = { enabled: false, info: {}, profile: null, loading: false, error: null, loadedAt: 0, src: "all", shown: PAGE, matches: [] };

  // ---------------------------------------------------------------- formatting

  function num(value, digits = 1) {
    if (!isNum(value)) return "—";
    return Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function signed(value, digits = 2) {
    if (!isNum(value)) return "—";
    const n = Number(value);
    return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}`;
  }

  function mapName(id) {
    const key = String(id || "");
    if (MAP_NAMES[key]) return MAP_NAMES[key];
    const bare = key.replace(/^(de|cs|ar|dz|gd)_/, "").replace(/_/g, " ");
    return bare ? bare.replace(/\b\w/g, (c) => c.toUpperCase()) : "Unknown map";
  }

  function premierColor(rating) {
    const n = Number(rating) || 0;
    return (PREMIER_TIERS.find(([min]) => n >= min) || PREMIER_TIERS[PREMIER_TIERS.length - 1])[1];
  }

  function faceitColor(level) {
    const n = Number(level) || 0;
    if (n >= 10) return "#fe1f00";
    if (n >= 8) return "#ff6309";
    if (n >= 4) return "#ffc800";
    if (n >= 2) return "#1ce400";
    return "#eeeeee";
  }

  function compRank(rank) {
    const n = Number(rank) || 0;
    return n >= 1 && n <= COMP_RANKS.length ? COMP_RANKS[n - 1] : "Unranked";
  }

  // Map emblems and rank badges are loaded from public GitHub repos through jsDelivr.
  const MAP_ICON_BASE = "https://cdn.jsdelivr.net/gh/MurkyYT/cs2-map-icons@main/images/";
  const RANK_ICON_BASE = "https://cdn.jsdelivr.net/gh/ItzArty/csgo-rank-icons@main/";

  function mapIcon(id) {
    const key = String(id || "");
    return /^[a-z0-9_]+$/.test(key) ? `${MAP_ICON_BASE}${key}.png` : "";
  }

  function rankImg(rank, mode = "matchmaking", cls = "cst-rank-img") {
    const n = Math.max(0, Math.min(18, Math.round(Number(rank) || 0)));
    const name = compRank(n);
    return `<img class="${cls}" src="${RANK_ICON_BASE}${mode}/${n}.svg" alt="${esc(name)}" title="${esc(name)}" draggable="false" loading="lazy" />`;
  }

  function when(iso) {
    const t = Date.parse(iso || "");
    if (!Number.isFinite(t)) return "";
    const sec = Math.max(0, (Date.now() - t) / 1000);
    if (sec < 3600) return `${Math.max(1, Math.round(sec / 60))} min ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
    if (sec < 86400 * 7) return `${Math.round(sec / 86400)} d ago`;
    return new Date(t).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function matchSource(m) {
    if (Number(m.rank_type) === 11) return { key: "premier", label: "Premier" };
    if (Number(m.rank_type) === 12) return { key: "competitive", label: "Competitive" };
    const src = String(m.data_source || "").toLowerCase();
    if (src.includes("faceit")) return { key: "other", label: "FACEIT" };
    if (src.includes("wingman")) return { key: "other", label: "Wingman" };
    return { key: "other", label: src ? src.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : "Match" };
  }

  function rankBadge(m) {
    if (!isNum(m.rank) || Number(m.rank) <= 0) return "";
    if (Number(m.rank_type) === 11) {
      return `<span class="cst-mini-premier" style="--c:${premierColor(m.rank)}">${Number(m.rank).toLocaleString("en-US")}</span>`;
    }
    if (Number(m.rank_type) === 12) {
      return `<span class="cst-mini-rank">${rankImg(m.rank)}<em>${esc(compRank(m.rank))}</em></span>`;
    }
    return "";
  }

  function outcomeOf(m) {
    const o = String(m.outcome || "").toLowerCase();
    return o === "win" ? "win" : o === "loss" ? "loss" : "tie";
  }

  function leetifyProfileUrl(steam64) {
    return `https://leetify.com/app/profile/${encodeURIComponent(steam64)}`;
  }

  function leetifyMatchUrl(id) {
    return `https://leetify.com/app/match-details/${encodeURIComponent(id)}`;
  }

  async function openLink(url) {
    try {
      const res = await api()?.open_url(url);
      if (res && !res.ok) notify(res.error || "Could not open link", "error");
    } catch {
      notify("Could not open link", "error");
    }
  }

  function tabActive() {
    return Boolean(el("stats")?.classList.contains("active"));
  }

  function syncShell() {
    if (typeof syncModules === "function") syncModules();
  }

  // ---------------------------------------------------------------- module state

  async function loadInfo() {
    try {
      const info = await api()?.stats_state();
      if (!info?.ok) return;
      state.info = info;
    } catch { /* bridge not ready */ }
  }

  // On/off is part of the Argus config (cs2_stats), like the aim modules.
  // app.js calls this whenever a config loads or switches.
  function applyConfig(on) {
    state.enabled = Boolean(on);
    const toggle = el("cs2_stats");
    if (toggle) toggle.checked = state.enabled;
  }

  function setEnabled(on) {
    applyConfig(on);
    if (typeof queueUpdate === "function") queueUpdate("cs2_stats", state.enabled);
    syncShell();
  }

  async function onToggle(event) {
    const toggle = event.currentTarget;
    if (!toggle.checked) {
      setEnabled(false);
      notify("CS2 stats turned off");
      return;
    }
    await loadInfo();
    toggle.checked = true;
    const steps = [];
    if (!state.info.signedIn) steps.push("steam");
    if (!state.info.hasKey) steps.push("leetify");
    const setup = window.ArgusInventory?.runSetup;
    const ok = steps.length && setup ? await setup(steps) : true;
    if (!ok) {
      toggle.checked = false;
      syncShell();
      return;
    }
    await loadInfo();
    if (!state.info.signedIn) {
      toggle.checked = false;
      syncShell();
      notify("Sign in to Steam to use CS2 stats", "error");
      return;
    }
    setEnabled(true);
    state.profile = null;
    notify("CS2 stats is on", "success");
    if (typeof showTab === "function") showTab("stats");
  }

  // ---------------------------------------------------------------- loading

  async function load(force = false) {
    if (state.loading) return;
    state.loading = true;
    paintRefresh();
    if (!state.profile) renderSkeleton();
    let res;
    try {
      res = await api()?.stats_profile(Boolean(force));
    } catch {
      res = { ok: false, error: "Could not reach Leetify." };
    }
    state.loading = false;
    paintRefresh();
    if (!res?.ok) {
      state.error = res || { error: "Could not load stats." };
      if (state.profile) notify(state.error.error, "error");
      else renderEmpty();
      return;
    }
    state.error = null;
    state.profile = res.profile;
    state.loadedAt = Date.now();
    render(true);
    if (force) notify("Stats updated", "success");
  }

  function paintRefresh() {
    const btn = el("cstRefresh");
    if (!btn) return;
    btn.disabled = state.loading;
    btn.classList.toggle("is-loading", state.loading);
    btn.querySelector("span").textContent = state.loading ? "Loading…" : "Refresh";
  }

  function showBody(show) {
    el("cstBody").hidden = !show;
    el("cstEmpty").hidden = show;
  }

  function renderSkeleton() {
    showBody(true);
    el("cstMeta").textContent = "Loading from Leetify…";
    const block = (h) => `<i class="cst-skel" style="height:${h}px"></i>`;
    el("cstRanks").innerHTML = [1, 2, 3].map(() => `<div class="cst-rank is-skel">${block(54)}</div>`).join("");
    el("cstRatings").innerHTML = block(150);
    el("cstForm").innerHTML = block(150);
    el("cstPerf").innerHTML = block(180);
    el("cstMaps").innerHTML = block(90);
    el("cstMatches").innerHTML = block(220);
  }

  function renderEmpty() {
    showBody(false);
    const err = state.error || {};
    const box = el("cstEmpty");
    let action = '<button type="button" class="csi-btn" data-cst="retry">Try again</button>';
    let title = "Could not load your stats";
    if (err.needsSignIn) {
      title = "Sign in to Steam";
      action = '<button type="button" class="csi-btn" data-cst="signin">Sign in with Steam</button>';
    } else if (err.notFound) {
      title = "No Leetify profile yet";
      action = '<button type="button" class="csi-btn" data-link="https://leetify.com/">Open leetify.com</button><button type="button" class="csi-btn csi-btn-ghost" data-cst="retry">Check again</button>';
    } else if (err.rateLimited) {
      title = "Leetify is busy";
    }
    box.innerHTML = `<div class="csi-empty">
        <svg viewBox="0 0 24 24"><path d="M4 20V11M10 20V5M16 20v-6M21 20H3" /></svg>
        <strong>${esc(title)}</strong>
        <p>${esc(err.error || "")}</p>
        <div class="cst-empty-actions">${action}</div>
      </div>`;
    el("cstName").textContent = state.info.persona || "Your stats";
    el("cstMeta").textContent = state.info.steam64 || "";
    el("cstRanks").innerHTML = "";
    el("cstViewProfile").hidden = true;
  }

  // ---------------------------------------------------------------- render

  function render(animate) {
    const p = state.profile;
    if (!p) return;
    showBody(true);
    renderHero(p);
    renderRatings(p.rating || {}, animate);
    renderForm(p.recent_matches || []);
    renderPerf(p.stats || {});
    renderMaps(p.ranks?.competitive || []);
    state.matches = p.recent_matches || [];
    state.shown = PAGE;
    renderMatches();
  }

  function renderHero(p) {
    const info = { ...state.info, ...(window.ArgusInventory?.info || {}) };
    el("cstName").textContent = p.name || info.persona || "Your stats";
    const since = Date.parse(p.first_match_date || "");
    const parts = [];
    if (isNum(p.total_matches)) parts.push(`${Number(p.total_matches).toLocaleString("en-US")} matches`);
    if (isNum(p.winrate)) parts.push(`${(Number(p.winrate) * 100).toFixed(1)}% win rate`);
    if (Number.isFinite(since)) parts.push(`tracked since ${new Date(since).toLocaleDateString(undefined, { month: "short", year: "numeric" })}`);
    if (String(p.privacy_mode || "") && p.privacy_mode !== "public") parts.push(`privacy: ${p.privacy_mode}`);
    el("cstMeta").textContent = parts.join(" · ");
    const wrap = el("cstAvatarWrap");
    const img = el("cstAvatar");
    if (info.avatar && img.getAttribute("src") !== info.avatar) {
      img.onload = () => wrap.classList.add("has-img");
      img.src = info.avatar;
    }
    const view = el("cstViewProfile");
    view.hidden = !p.steam64_id;
    view.dataset.link = p.steam64_id ? leetifyProfileUrl(p.steam64_id) : "";

    const r = p.ranks || {};
    const cards = [];
    if (isNum(r.premier) && Number(r.premier) > 0) {
      cards.push(`<div class="cst-rank" style="--c:${premierColor(r.premier)}">
          <span>Premier</span>
          <div class="cst-premier"><i></i><b>${Number(r.premier).toLocaleString("en-US")}</b></div>
        </div>`);
    }
    if (isNum(r.faceit) && Number(r.faceit) > 0) {
      cards.push(`<div class="cst-rank" style="--c:${faceitColor(r.faceit)}">
          <span>FACEIT</span>
          <div class="cst-faceit"><i>${Number(r.faceit)}</i><b>${isNum(r.faceit_elo) ? `${Number(r.faceit_elo).toLocaleString("en-US")} <small>elo</small>` : ""}</b></div>
        </div>`);
    }
    if (isNum(r.leetify)) {
      const tone = Number(r.leetify) >= 0 ? "is-pos" : "is-neg";
      cards.push(`<div class="cst-rank cst-rank-leetify">
          <span>Leetify Rating</span>
          <div class="cst-leet ${tone}"><b>${esc(signed(r.leetify, 2))}</b></div>
        </div>`);
    }
    if (isNum(r.wingman) && Number(r.wingman) > 0) {
      cards.push(`<div class="cst-rank">
          <span>Wingman</span>
          <div class="cst-rank-badge">${rankImg(r.wingman, "wingman", "cst-rank-img is-lg")}<b>${esc(compRank(r.wingman))}</b></div>
        </div>`);
    }
    if (isNum(r.renown) && Number(r.renown) > 0) {
      cards.push(`<div class="cst-rank"><span>Renown</span><div class="cst-leet"><b>${Number(r.renown).toLocaleString("en-US")}</b></div></div>`);
    }
    if (isNum(p.winrate)) {
      const pct = Number(p.winrate) * 100;
      cards.push(`<div class="cst-rank cst-rank-win">
          <span>Win rate</span>
          <div class="cst-win"><svg viewBox="0 0 36 36"><circle cx="18" cy="18" r="15" /><circle cx="18" cy="18" r="15" class="cst-win-arc" style="--p:${pct.toFixed(2)}" /></svg><b>${pct.toFixed(1)}%</b></div>
        </div>`);
    }
    el("cstRanks").innerHTML = cards.join("");

    const bans = Array.isArray(p.bans) ? p.bans : [];
    const banBox = el("cstBans");
    banBox.hidden = !bans.length;
    banBox.innerHTML = bans.map((b) => `<span>${esc(b.platform || "Ban")}${b.platform_nickname ? ` · ${esc(b.platform_nickname)}` : ""}${b.banned_since ? ` · since ${esc(new Date(b.banned_since).toLocaleDateString())}` : ""}</span>`).join("");
  }

  function renderRatings(rating, animate) {
    const rings = [["aim", "Aim"], ["positioning", "Positioning"], ["utility", "Utility"]].map(([key, label], i) => {
      const v = Number(rating[key]);
      const pct = isNum(rating[key]) ? Math.max(0, Math.min(100, v)) : 0;
      return `<div class="cst-ring" style="--p:${pct.toFixed(2)};--d:${animate ? i * 120 : 0}ms">
          <svg viewBox="0 0 80 80"><circle cx="40" cy="40" r="34" /><circle cx="40" cy="40" r="34" class="cst-ring-arc${animate ? " is-anim" : ""}" /></svg>
          <b>${esc(num(rating[key], 1))}</b>
          <span>${label}</span>
        </div>`;
    }).join("");
    const signedRows = [["clutch", "Clutch"], ["opening", "Opening"], ["ct_leetify", "CT Leetify"], ["t_leetify", "T Leetify"]];
    const maxAbs = Math.max(0.0001, ...signedRows.map(([k]) => Math.abs(Number(rating[k]) || 0)));
    const bars = signedRows.map(([key, label]) => {
      const v = Number(rating[key]) || 0;
      const width = (Math.abs(v) / maxAbs) * 50;
      const side = v >= 0 ? "is-pos" : "is-neg";
      return `<div class="cst-bar-row">
          <span>${label}</span>
          <div class="cst-bar ${side}"><i style="width:${width.toFixed(1)}%"></i></div>
          <b class="${side}">${esc(isNum(rating[key]) ? signed(rating[key], 4) : "—")}</b>
        </div>`;
    }).join("");
    el("cstRatings").innerHTML = `<div class="cst-rings">${rings}</div><div class="cst-bars">${bars}</div>`;
  }

  function renderForm(matches) {
    const recent = matches.slice(0, 40).reverse();
    const box = el("cstForm");
    if (!recent.length) {
      box.innerHTML = '<p class="cst-muted">No recent matches.</p>';
      el("cstFormSummary").innerHTML = "";
      return;
    }
    const maxAbs = Math.max(0.0001, ...recent.map((m) => Math.abs(Number(m.leetify_rating) || 0)));
    box.innerHTML = `<div class="cst-form-bars">${recent.map((m, i) => {
      const v = Number(m.leetify_rating) || 0;
      const h = Math.max(4, (Math.abs(v) / maxAbs) * 100);
      return `<button type="button" class="cst-form-bar is-${outcomeOf(m)} ${v >= 0 ? "is-up" : "is-down"}" data-match="${esc(m.id)}" data-i="${i}" style="--h:${h.toFixed(1)}%;--d:${i * 14}ms" aria-label="${esc(mapName(m.map_name))} ${esc(outcomeOf(m))}"><i></i></button>`;
    }).join("")}</div><div class="cst-form-axis"><span>older</span><span>latest</span></div>`;
    box._recent = recent;

    const last = matches.slice(0, 20);
    const count = { win: 0, loss: 0, tie: 0 };
    last.forEach((m) => { count[outcomeOf(m)] += 1; });
    let streak = 0;
    const first = matches[0] ? outcomeOf(matches[0]) : "";
    for (const m of matches) {
      if (outcomeOf(m) !== first) break;
      streak += 1;
    }
    const streakLabel = first === "win" ? `W${streak}` : first === "loss" ? `L${streak}` : `T${streak}`;
    el("cstFormSummary").innerHTML = `<span class="is-win">${count.win}W</span><span class="is-loss">${count.loss}L</span><span class="is-tie">${count.tie}T</span><em title="Current streak">${streakLabel}</em>`;
  }

  function perfTile(label, value, unit, pct) {
    const bar = isNum(pct) ? `<div class="cst-tile-bar"><i style="width:${Math.max(0, Math.min(100, Number(pct))).toFixed(1)}%"></i></div>` : "";
    return `<div class="cst-tile"><span>${esc(label)}</span><b>${esc(value)}${value !== "—" && unit ? `<small>${esc(unit)}</small>` : ""}</b>${bar}</div>`;
  }

  function renderPerf(s) {
    const pctTile = (label, key) => perfTile(label, num(s[key], 1), "%", s[key]);
    const groups = [
      ["Aim", [
        perfTile("Reaction Time", num(s.reaction_time_ms, 0), "ms"),
        perfTile("Preaim", num(s.preaim, 1), "°"),
        pctTile("Head Accuracy", "accuracy_head"),
        pctTile("Spray Accuracy", "spray_accuracy"),
        pctTile("Accuracy (Enemy Spotted)", "accuracy_enemy_spotted"),
        pctTile("Counter-Strafing", "counter_strafing_good_shots_ratio"),
      ]],
      ["Duels & trades", [
        pctTile("CT Opening Duel Success", "ct_opening_duel_success_percentage"),
        pctTile("T Opening Duel Success", "t_opening_duel_success_percentage"),
        pctTile("CT Opening Aggression Success", "ct_opening_aggression_success_rate"),
        pctTile("T Opening Aggression Success", "t_opening_aggression_success_rate"),
        pctTile("Trade Kills Success", "trade_kills_success_percentage"),
        pctTile("Traded Deaths Success", "traded_deaths_success_percentage"),
        perfTile("Trade Kill Opportunities / Round", num(s.trade_kill_opportunities_per_round, 2), ""),
      ]],
      ["Utility", [
        perfTile("Flashbangs Thrown", num(s.flashbang_thrown, 2), ""),
        perfTile("Flashbang Hit Foe / Flashbang", num(s.flashbang_hit_foe_per_flashbang, 2), ""),
        perfTile("Flashbang Hit Friend / Flashbang", num(s.flashbang_hit_friend_per_flashbang, 2), ""),
        perfTile("Flashbang Hit Foe Avg Duration", num(s.flashbang_hit_foe_avg_duration, 2), "s"),
        perfTile("Flashbang Leading to Kill", num(s.flashbang_leading_to_kill, 2), ""),
        perfTile("HE Foes Damage Avg", num(s.he_foes_damage_avg, 2), ""),
        perfTile("HE Friends Damage Avg", num(s.he_friends_damage_avg, 2), ""),
        perfTile("Utility on Death Avg", num(s.utility_on_death_avg, 1), ""),
      ]],
    ];
    el("cstPerf").innerHTML = groups.map(([title, tiles]) => `<div class="cst-perf-group"><p>${esc(title)}</p><div class="cst-tiles">${tiles.join("")}</div></div>`).join("");
  }

  function renderMaps(list) {
    const box = el("cstMaps");
    const rows = (Array.isArray(list) ? list : []).slice().sort((a, b) => (Number(b.rank) || 0) - (Number(a.rank) || 0) || mapName(a.map_name).localeCompare(mapName(b.map_name)));
    if (!rows.length) {
      box.innerHTML = '<p class="cst-muted">No competitive map ranks yet.</p>';
      return;
    }
    const card = (m, i) => {
      const rank = Number(m.rank) || 0;
      const icon = mapIcon(m.map_name);
      const name = mapName(m.map_name);
      return `<div class="cst-map${rank ? "" : " is-unranked"}" style="--d:${i * 45}ms">
          ${icon ? `<img class="cst-map-bg" src="${icon}" alt="" draggable="false" />` : ""}
          <div class="cst-map-emblem"><span>${esc(name.slice(0, 2))}</span>${icon ? `<img src="${icon}" alt="" draggable="false" />` : ""}</div>
          <div class="cst-map-info"><strong>${esc(name)}</strong><small>${esc(compRank(rank))}</small></div>
          ${rankImg(rank, "matchmaking", "cst-map-rank")}
        </div>`;
    };
    box.innerHTML = rows.map(card).join("");
  }

  function renderMatches() {
    const box = el("cstMatches");
    const rows = state.matches.filter((m) => state.src === "all" || matchSource(m).key === state.src);
    document.querySelectorAll("#cstMatchSeg [data-src]").forEach((b) => b.classList.toggle("active", b.dataset.src === state.src));
    if (!rows.length) {
      box.innerHTML = '<p class="cst-muted">No matches of this type in your recent history.</p>';
      el("cstMore").hidden = true;
      return;
    }
    const shown = rows.slice(0, state.shown);
    box.innerHTML = shown.map((m) => {
      const o = outcomeOf(m);
      const src = matchSource(m);
      const score = Array.isArray(m.score) ? m.score : [];
      const v = Number(m.leetify_rating);
      return `<button type="button" class="cst-match is-${o}" data-match="${esc(m.id)}">
          <i class="cst-match-stripe"></i>
          <span class="cst-match-map"><strong>${esc(mapName(m.map_name))}</strong><small>${esc(src.label)} · ${esc(when(m.finished_at))}</small></span>
          <span class="cst-match-score"><b>${esc(score[0] ?? "–")}</b><em>:</em><b>${esc(score[1] ?? "–")}</b></span>
          <span class="cst-match-rank">${rankBadge(m)}</span>
          <span class="cst-match-aim"><small>HS</small>${esc(num(m.accuracy_head, 1))}%<small>Spray</small>${esc(num(m.spray_accuracy, 1))}%<small>RT</small>${esc(num(m.reaction_time_ms, 0))}ms</span>
          <span class="cst-match-rating ${isNum(m.leetify_rating) ? (v >= 0 ? "is-pos" : "is-neg") : ""}">${esc(signed(m.leetify_rating, 4))}</span>
        </button>`;
    }).join("");
    el("cstMore").hidden = rows.length <= state.shown;
  }

  // ---------------------------------------------------------------- match dialog

  async function openMatch(id) {
    const overlay = el("cstMatch");
    const body = el("cstMatchBody");
    if (!overlay || !id) return;
    const summary = state.matches.find((m) => m.id === id);
    body.innerHTML = `<div class="cst-match-head"><p class="eyebrow">MATCH</p><h3 id="cstMatchTitle">${esc(mapName(summary?.map_name))}</h3><p class="cst-muted">Loading scoreboard from Leetify…</p></div><i class="cst-skel" style="height:260px;margin:0 24px 24px"></i>`;
    overlay.hidden = false;
    requestAnimationFrame(() => overlay.classList.add("open"));
    let res;
    try {
      res = await api()?.stats_match(id);
    } catch {
      res = { ok: false, error: "Could not reach Leetify." };
    }
    if (overlay.hidden) return;
    if (!res?.ok) {
      body.innerHTML = `<div class="cst-match-head"><p class="eyebrow">MATCH</p><h3 id="cstMatchTitle">${esc(mapName(summary?.map_name))}</h3><p class="cst-muted">${esc(res?.error || "Could not load this match.")}</p>
        <div class="cst-match-links"><button type="button" class="cst-leetify-link" data-link="${esc(leetifyMatchUrl(id))}">View on Leetify</button></div></div>`;
      return;
    }
    renderMatch(res.match, summary);
  }

  // Each view is a set of columns: [header, field, format, better, full name].
  // Values are shown as Leetify returns them; fractions display as percentages and
  // reaction time in milliseconds, the way Leetify shows them.
  const MATCH_VIEWS = [
    { key: "score", label: "Scoreboard", bars: false, cols: [
      ["K", "total_kills", 0, "max", "Kills"], ["D", "total_deaths", 0, "min", "Deaths"], ["A", "total_assists", 0, "max", "Assists"],
      ["K/D", "kd_ratio", 2, "max", "K/D Ratio"], ["ADR", "dpr", 1, "max", "Damage per Round"], ["HS kills", "total_hs_kills", 0, "max", "Headshot Kills"],
      ["MVP", "mvps", 0, "max", "MVPs"], ["Rating", "leetify_rating", "rating", "max", "Leetify Rating"],
    ] },
    { key: "aim", label: "Aim", bars: true, cols: [
      ["Acc. spotted", "accuracy_enemy_spotted", "pct", "max", "Accuracy (Enemy Spotted)"], ["Head acc.", "accuracy_head", "pct", "max", "Head Accuracy"],
      ["Spray acc.", "spray_accuracy", "pct", "max", "Spray Accuracy"], ["Counter-strafe", "counter_strafing_shots_good_ratio", "pct", "max", "Counter-Strafing"],
      ["Reaction", "reaction_time", "ms", "min", "Reaction Time"], ["Preaim", "preaim", "deg", "min", "Preaim"],
    ] },
    { key: "utility", label: "Utility", bars: true, cols: [
      ["Flashes", "flashbang_thrown", 0, "max", "Flashbangs Thrown"], ["Flash hits", "flashbang_hit_foe", 0, "max", "Flashbang Hit Foe"],
      ["Flash assists", "flash_assist", 0, "max", "Flash Assists"], ["Flash \u2192 kill", "flashbang_leading_to_kill", 0, "max", "Flashbang Leading to Kill"],
      ["HE dmg", "he_foes_damage_avg", 1, "max", "HE Foes Damage Avg"], ["HE", "he_thrown", 0, "max", "HE Thrown"],
      ["Molotovs", "molotov_thrown", 0, "max", "Molotovs Thrown"], ["Smokes", "smoke_thrown", 0, "max", "Smokes Thrown"],
    ] },
    { key: "impact", label: "Impact", bars: true, cols: [
      ["2K", "multi2k", 0, "max", "2 Kill Rounds"], ["3K", "multi3k", 0, "max", "3 Kill Rounds"], ["4K", "multi4k", 0, "max", "4 Kill Rounds"],
      ["5K", "multi5k", 0, "max", "5 Kill Rounds"], ["Trade kills", "trade_kills_succeed", 0, "max", "Trade Kills"],
      ["Traded deaths", "traded_deaths_succeed", 0, "max", "Traded Deaths"], ["Survived", "rounds_survived_percentage", "pct", "max", "Rounds Survived"],
      ["Damage", "total_damage", 0, "max", "Total Damage"],
    ] },
  ];
  const VIEW_MS = 7000;
  const matchView = { m: null, summary: null, index: 0 };

  function fmtCell(value, format) {
    if (!isNum(value)) return "\u2014";
    const v = Number(value);
    if (format === "pct") return `${(v * 100).toFixed(1)}%`;
    if (format === "ms") return `${Math.round(v * 1000)} ms`;
    if (format === "deg") return `${v.toFixed(1)}\u00b0`;
    if (format === "rating") return signed(v, 4);
    return typeof format === "number" && format > 0 ? num(v, format) : String(Math.round(v));
  }

  function viewTables(view, m) {
    const me = state.profile?.steam64_id || state.info.steam64;
    const players = Array.isArray(m.stats) ? m.stats : [];
    const scores = {};
    (m.team_scores || []).forEach((t) => { scores[t.team_number] = t.score; });
    const myTeam = players.find((p) => p.steam64_id === me)?.initial_team_number;
    const teams = [...new Set(players.map((p) => p.initial_team_number))].sort((a, b) => (a === myTeam ? -1 : b === myTeam ? 1 : a - b));
    // Best value per column across both teams; bars are drawn relative to it.
    const best = view.cols.map(([, key, , better]) => {
      const vals = players.map((p) => Number(p[key])).filter((v) => Number.isFinite(v) && (better === "max" || v > 0));
      if (vals.length < 2 || Math.min(...vals) === Math.max(...vals)) return null;
      return better === "max" ? Math.max(...vals) : Math.min(...vals);
    });
    const table = (team, t) => {
      const rows = players.filter((p) => p.initial_team_number === team).sort((a, b) => (Number(b.leetify_rating) || 0) - (Number(a.leetify_rating) || 0));
      const side = team === 2 ? "T" : team === 3 ? "CT" : `Team ${team}`;
      return `<div class="cst-team${team === myTeam ? " is-mine" : ""}" style="--t:${t}">
          <div class="cst-team-head"><strong>${team === myTeam ? "Your team" : "Enemy team"}</strong><em>started ${esc(side)}</em><b>${esc(scores[team] ?? "")}</b></div>
          <table>
            <thead><tr><th>Player</th>${view.cols.map(([head, , , , full]) => `<th title="${esc(full)}">${esc(head)}</th>`).join("")}</tr></thead>
            <tbody>${rows.map((p, r) => `<tr class="${p.steam64_id === me ? "is-me" : ""}" style="--r:${r}">
                <td><button type="button" class="cst-player" data-link="${esc(leetifyProfileUrl(p.steam64_id))}" title="View on Leetify">${esc(p.name || p.steam64_id)}</button></td>
                ${view.cols.map(([, key, format, better], c) => {
                  const raw = p[key];
                  const v = Number(raw);
                  const tone = format === "rating" && isNum(raw) ? (v >= 0 ? " is-pos" : " is-neg") : "";
                  const isBest = best[c] != null && isNum(raw) && v === best[c];
                  let bar = "";
                  if (view.bars && best[c] != null && isNum(raw) && v > 0) {
                    const w = better === "max" ? v / best[c] : best[c] / v;
                    bar = `<i class="cst-cell-bar" style="--w:${Math.max(0.04, Math.min(1, w)).toFixed(3)}"></i>`;
                  }
                  return `<td class="${isBest ? "is-best" : ""}${tone}"><span>${esc(fmtCell(raw, format))}</span>${bar}</td>`;
                }).join("")}
              </tr>`).join("")}</tbody>
          </table>
        </div>`;
    };
    return teams.map(table).join("");
  }

  function renderMatch(m, summary) {
    matchView.m = m;
    matchView.summary = summary;
    matchView.index = 0;
    const me = state.profile?.steam64_id || state.info.steam64;
    const players = Array.isArray(m.stats) ? m.stats : [];
    const scores = {};
    (m.team_scores || []).forEach((t) => { scores[t.team_number] = t.score; });
    const myTeam = players.find((p) => p.steam64_id === me)?.initial_team_number;
    const o = summary ? outcomeOf(summary) : "";
    const ourScore = scores[myTeam];
    const theirScore = Object.entries(scores).find(([t]) => Number(t) !== myTeam)?.[1];
    const icon = mapIcon(m.map_name);
    el("cstMatchBody").innerHTML = `
      <div class="cst-match-head is-${o}">
        ${icon ? `<img class="cst-match-emblem" src="${icon}" alt="" draggable="false" />` : ""}
        <p class="eyebrow">${esc(summary ? matchSource(summary).label.toUpperCase() : "MATCH")} \u00b7 ${esc(new Date(m.finished_at).toLocaleString())}</p>
        <h3 id="cstMatchTitle">${esc(mapName(m.map_name))}</h3>
        ${ourScore != null ? `<div class="cst-bigscore"><b class="is-us">${esc(ourScore)}</b><em>:</em><b>${esc(theirScore ?? "")}</b><span>${o === "win" ? "Victory" : o === "loss" ? "Defeat" : o === "tie" ? "Draw" : ""}</span></div>` : ""}
        ${m.has_banned_player ? '<p class="cst-banned">A player in this match has since been banned.</p>' : ""}
      </div>
      <div class="cst-viewbar" role="tablist">${MATCH_VIEWS.map((v, i) => `<button type="button" role="tab" data-view="${i}"><span>${esc(v.label)}</span><i></i></button>`).join("")}</div>
      <div class="cst-views" id="cstViews"></div>
      <div class="cst-match-links"><button type="button" class="cst-leetify-link" data-link="${esc(leetifyMatchUrl(m.id))}">View on Leetify</button></div>`;
    showView(0, true);
  }

  function showView(index, first = false) {
    const box = el("cstViews");
    if (!box || !matchView.m) return;
    matchView.index = (index + MATCH_VIEWS.length) % MATCH_VIEWS.length;
    const view = MATCH_VIEWS[matchView.index];
    box.querySelectorAll(".cst-view.is-out").forEach((node) => node.remove());
    const old = box.querySelector(".cst-view");
    const next = document.createElement("div");
    next.className = `cst-view${first ? "" : " is-in"}`;
    next.dataset.view = view.key;
    next.innerHTML = viewTables(view, matchView.m);
    if (old) {
      old.classList.remove("is-in");
      old.classList.add("is-out");
      old.addEventListener("animationend", () => old.remove(), { once: true });
      setTimeout(() => old.remove(), 600);
    }
    box.append(next);
    // Restart the progress line on the active tab; when it finishes, move on.
    el("cstMatchBody").querySelectorAll(".cst-viewbar [data-view]").forEach((tab) => {
      const on = Number(tab.dataset.view) === matchView.index;
      tab.classList.toggle("active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
      const bar = tab.querySelector("i");
      const fresh = document.createElement("i");
      if (on) {
        fresh.style.animationDuration = `${VIEW_MS}ms`;
        fresh.addEventListener("animationend", () => {
          if (!el("cstMatch").hidden && matchView.m) showView(matchView.index + 1);
        }, { once: true });
      }
      bar.replaceWith(fresh);
    });
  }

  function closeMatch() {
    const overlay = el("cstMatch");
    if (!overlay) return;
    matchView.m = null;
    overlay.classList.remove("open");
    overlay.hidden = true;
  }

  // ---------------------------------------------------------------- attribution

  function loadAttribution() {
    const img = el("cstAttribLogo");
    const text = el("cstAttribText");
    if (!img) return;
    const sources = ["assets/leetify/data-provided-by-leetify.svg", "assets/leetify/data-provided-by-leetify.png"];
    let i = 0;
    img.onload = () => {
      img.hidden = false;
      text.hidden = true;
    };
    img.onerror = () => {
      i += 1;
      if (i < sources.length) img.src = sources[i];
    };
    img.src = sources[0];
  }

  // ---------------------------------------------------------------- events

  function bind() {
    el("cs2_stats")?.addEventListener("change", onToggle);
    el("cstRefresh")?.addEventListener("click", () => load(true));
    const shell = el("cstShell");
    shell?.addEventListener("click", (event) => {
      const link = event.target.closest("[data-link]");
      if (link && link.dataset.link) return openLink(link.dataset.link);
      const act = event.target.closest("[data-cst]");
      if (act) {
        if (act.dataset.cst === "retry") load(true);
        else if (act.dataset.cst === "signin") {
          window.ArgusInventory?.runSetup(["steam"]).then(async (ok) => {
            if (!ok) return;
            await loadInfo();
            load(true);
          });
        }
        return;
      }
      const match = event.target.closest("[data-match]");
      if (match) openMatch(match.dataset.match);
    });
    el("cstMatchSeg")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-src]");
      if (!btn || btn.dataset.src === state.src) return;
      state.src = btn.dataset.src;
      state.shown = PAGE;
      renderMatches();
    });
    el("cstMore")?.addEventListener("click", () => {
      state.shown += PAGE;
      renderMatches();
    });
    const form = el("cstForm");
    const tip = el("cstFormTip");
    form?.addEventListener("pointerover", (event) => {
      const bar = event.target.closest(".cst-form-bar");
      if (!bar || !form._recent) return;
      const m = form._recent[Number(bar.dataset.i)];
      if (!m) return;
      const score = Array.isArray(m.score) ? m.score.join(" : ") : "";
      tip.innerHTML = `<strong>${esc(mapName(m.map_name))}</strong><span class="is-${outcomeOf(m)}">${esc(outcomeOf(m).toUpperCase())} ${esc(score)}</span><span>Leetify Rating ${esc(signed(m.leetify_rating, 4))}</span><small>${esc(when(m.finished_at))}</small>`;
      tip.hidden = false;
      const cardRect = form.closest(".cst-card").getBoundingClientRect();
      const r = bar.getBoundingClientRect();
      const x = Math.min(Math.max(r.left + r.width / 2 - cardRect.left, 70), cardRect.width - 70);
      tip.style.left = `${x}px`;
    });
    form?.addEventListener("pointerleave", () => { tip.hidden = true; });
    el("cstMatchClose")?.addEventListener("click", closeMatch);
    el("cstMatchBody")?.addEventListener("click", (event) => {
      const tab = event.target.closest(".cst-viewbar [data-view]");
      if (tab) showView(Number(tab.dataset.view));
    });
    const hideBroken = (event) => {
      if (event.target.tagName === "IMG") event.target.remove();
    };
    el("cstMaps")?.addEventListener("error", hideBroken, true);
    el("cstMatchBody")?.addEventListener("error", hideBroken, true);
    el("cstMatch")?.addEventListener("click", (event) => {
      if (event.target === el("cstMatch")) return closeMatch();
      const link = event.target.closest("[data-link]");
      if (link && link.dataset.link) openLink(link.dataset.link);
    });
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !el("cstMatch")?.hidden) {
        closeMatch();
        event.stopPropagation();
      }
    }, true);
    window.addEventListener("argus:steam-signout", () => {
      state.profile = null;
      state.error = { needsSignIn: true, error: "Sign in to Steam to see your CS2 stats." };
      loadInfo().then(() => {
        if (tabActive()) renderEmpty();
      });
    });
  }

  function onShow() {
    paintRefresh();
    if (state.loading) return;
    if (!state.profile || Date.now() - state.loadedAt > 60000) load(false);
    else render(false);
  }

  function init() {
    bind();
    loadAttribution();
    loadInfo();
  }

  window.ArgusStats = {
    get enabled() { return state.enabled; },
    applyConfig,
    onShow,
  };

  if (window.pywebview && window.pywebview.api) init();
  else window.addEventListener("pywebviewready", init, { once: true });
})();
