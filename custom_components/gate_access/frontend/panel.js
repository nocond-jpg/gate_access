// Gate Access — admin panel (custom element, receives the authenticated `hass`).
const CSS = `
:host{
  --bg:#14171c; --panel:#1b1f26; --line:#2a2f38; --ink:#e8e6df;
  --muted:#8b9099; --brass:#c8952f; --brass-hi:#e8b45a; --danger:#c9524a; --ok:#5aa06a;
  display:block; min-height:100vh; background:var(--bg); color:var(--ink);
  font-family:'Inter',system-ui,sans-serif;
}
*{box-sizing:border-box}
.wrap{max-width:920px;margin:0 auto;padding:28px 20px 64px}
header{display:flex;align-items:center;gap:12px;margin-bottom:14px}
header ha-icon{--mdc-icon-size:26px;color:var(--brass)}
h1{font-size:1.15rem;font-weight:600;letter-spacing:.01em;margin:0}
header .sub{color:var(--muted);font-size:.82rem;margin-left:auto}

.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:22px}
.tabs button{background:transparent;border:0;color:var(--muted);padding:11px 16px;
  font-size:.9rem;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.tabs button.on{color:var(--ink);border-bottom-color:var(--brass);font-weight:600}

.new{display:flex;gap:10px;align-items:stretch;margin-bottom:12px}
.new input{flex:1;background:var(--panel);border:1px solid var(--line);color:var(--ink);
  border-radius:10px;padding:12px 14px;font-size:.95rem}
.new input:focus{outline:none;border-color:var(--brass)}
.new button{background:var(--brass);color:#1a1206;border:0;border-radius:10px;
  padding:0 20px;font-weight:600;font-size:.92rem;cursor:pointer;white-space:nowrap}
.new button:hover{background:var(--brass-hi)}
.new button:disabled{opacity:.5;cursor:default}

.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px}
.seg{display:inline-flex;background:var(--panel);border:1px solid var(--line);
  border-radius:10px;overflow:hidden}
.seg button{background:transparent;border:0;color:var(--muted);padding:9px 14px;
  font-size:.82rem;cursor:pointer;border-right:1px solid var(--line)}
.seg button:last-child{border-right:0}
.seg button.on{background:var(--brass);color:#1a1206;font-weight:600}
.controls input,.sel{background:var(--panel);border:1px solid var(--line);color:var(--ink);
  border-radius:10px;padding:9px 12px;font-size:.86rem;color-scheme:dark}
.controls input:focus,.sel:focus{outline:none;border-color:var(--brass)}
.hint{color:var(--muted);font-size:.78rem}

.quick{display:none;flex-wrap:wrap;gap:8px;align-items:center;margin-top:22px}
.quick .qlabel{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);width:100%;margin-bottom:2px}
.quick button{background:transparent;border:1px solid var(--brass);color:var(--brass-hi);
  border-radius:10px;padding:10px 14px;font-size:.86rem;font-weight:600;cursor:pointer;
  display:inline-flex;align-items:center;gap:8px}
.quick button:hover{background:var(--brass);color:#1a1206}
.quick button:disabled{opacity:.5;cursor:default}

.msg{min-height:20px;font-size:.82rem;margin:8px 2px 18px}
.msg.err{color:var(--danger)} .msg.ok{color:var(--brass-hi)}

.filters{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:14px}
.filters .fl{display:flex;align-items:center;gap:6px}
.filters .fl span{color:var(--muted);font-size:.78rem}

.bar{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.bar .count{color:var(--muted);font-size:.8rem;text-transform:uppercase;letter-spacing:.08em}
.bar .spacer{flex:1}
.ghost{background:transparent;border:1px solid var(--line);color:var(--muted);
  border-radius:8px;padding:7px 12px;font-size:.8rem;cursor:pointer}
.ghost:hover{color:var(--ink);border-color:var(--brass)}
.ghost.danger:hover{color:#fff;border-color:var(--danger);background:var(--danger)}
.hdel{background:transparent;border:1px solid var(--line);color:var(--muted);
  border-radius:6px;padding:2px 8px;font-size:.78rem;cursor:pointer;margin-left:8px}
.hdel:hover{color:#fff;border-color:var(--danger);background:var(--danger)}

.tag{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:14px 16px;margin-bottom:10px}
.tag.dead{opacity:.6}
.head{display:grid;grid-template-columns:auto 1fr auto;gap:10px 14px;align-items:center;cursor:pointer}
.chev{color:var(--muted);font-size:.8rem;transition:transform .15s;user-select:none}
.tag.open .chev{transform:rotate(90deg)}
.who{font-weight:600;font-size:.98rem;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.when{color:var(--muted);font-size:.76rem;margin-top:3px}
.target-tag{color:var(--brass-hi);font-size:.76rem}
.pill{font-size:.68rem;font-weight:600;letter-spacing:.02em;padding:3px 8px;
  border-radius:999px;border:1px solid var(--line);color:var(--muted);white-space:nowrap}
.pill.live{color:var(--ok);border-color:#2f4636}
.pill.warn{color:var(--brass-hi);border-color:#4a3a1c}
.pill.dead{color:var(--danger);border-color:#4a2a28}
.actions{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.actions button{background:transparent;border:1px solid var(--line);color:var(--muted);
  border-radius:8px;padding:7px 11px;font-size:.78rem;cursor:pointer}
.actions button:hover{color:var(--ink);border-color:var(--brass)}
.actions .del:hover{color:#fff;border-color:var(--danger);background:var(--danger)}
.url{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.78rem;
  color:var(--brass-hi);background:#0f1216;border:1px solid var(--line);border-radius:8px;
  padding:9px 11px;margin-top:10px;overflow-x:auto;white-space:nowrap}

.body{display:none;margin-top:14px;padding-top:14px;border-top:1px solid var(--line)}
.tag.open .body{display:block}
.section-label{font-size:.72rem;text-transform:uppercase;letter-spacing:.08em;
  color:var(--muted);margin:2px 0 8px}
.editor{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:18px}
.editor .save{background:var(--brass);color:#1a1206;border:0;border-radius:8px;
  padding:8px 14px;font-weight:600;font-size:.8rem;cursor:pointer}
.editor .save:hover{background:var(--brass-hi)}
.hist{display:flex;flex-direction:column;gap:6px}
.hrow{display:flex;align-items:center;gap:10px;font-size:.82rem}
.hrow .ht{color:var(--muted);font-variant-numeric:tabular-nums;min-width:118px}
.hrow .hs{font-size:.68rem;padding:2px 7px;border-radius:999px;border:1px solid var(--line)}
.hrow .hs.opened{color:var(--ok);border-color:#2f4636}
.hrow .hs.expired{color:var(--danger);border-color:#4a2a28}
.hrow .hs.closed{color:#7aa2c4;border-color:#2c3b49}
.stats{display:flex;flex-direction:column;gap:8px;margin-bottom:22px}
.srow{display:flex;align-items:center;gap:14px;background:var(--panel);
  border:1px solid var(--line);border-radius:12px;padding:12px 16px}
.srow .sname{font-weight:600;flex:1}
.srow .snum{display:flex;flex-direction:column;align-items:center;min-width:52px}
.srow .snum b{font-size:1.05rem;color:var(--brass-hi)}
.srow .snum em{font-style:normal;color:var(--muted);font-size:.68rem;text-transform:uppercase;letter-spacing:.06em}
.hrow .htg{color:var(--brass-hi);font-size:.76rem}
.hrow .hn{font-weight:600}
.hrow .hsrc{color:var(--muted);font-size:.72rem;margin-left:auto;
  border:1px solid var(--line);border-radius:999px;padding:2px 8px}
.hist .none{color:var(--muted);font-size:.82rem}

.empty{border:1px dashed var(--line);border-radius:12px;padding:34px;text-align:center;
  color:var(--muted);font-size:.9rem}

.settings .item{display:flex;justify-content:space-between;gap:16px;padding:12px 0;
  border-bottom:1px solid var(--line)}
.settings .item .k{color:var(--muted);font-size:.86rem}
.settings .item .v{text-align:right}
.settings .chips{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end}
.settings .chip{font-size:.76rem;padding:3px 9px;border-radius:999px;border:1px solid var(--line);
  color:var(--brass-hi)}
.settings .open-settings{margin-top:20px;background:var(--brass);color:#1a1206;border:0;
  border-radius:10px;padding:11px 16px;font-weight:600;font-size:.9rem;cursor:pointer}
.settings .note{color:var(--muted);font-size:.82rem;margin-top:14px;line-height:1.5}
`;

const MODES = [
  ["permanent", "Stały"],
  ["until", "Do dnia"],
  ["count", "N użyć"],
  ["once", "Jednorazowy"],
];
const FILTERS = [
  ["all", "Wszystkie"],
  ["permanent", "Stały"],
  ["until", "Czasowy"],
  ["count", "N użyć"],
  ["once", "Jednorazowy"],
  ["pending", "Oczekujące"],
  ["disabled", "Wyłączone"],
  ["expired", "Wygasłe"],
];

function localInput(offsetMs) {
  const d = new Date(Date.now() + (offsetMs || 0));
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

class GateAccessPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (!this._ready) {
      this._ready = true; this._mode = "permanent"; this._open = new Set();
      this._history = []; this._targets = []; this._users = [];
      this._tab = "panel"; this._fRestr = "all"; this._fTarget = "all";
      this._hFTarget = "all"; this._hFUser = "all"; this._hFKind = "all";
      this._stats = { enabled: false, stats: [] };
      this._boot();
    }
  }

  _boot() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>${CSS}</style>
      <div class="wrap">
        <header>
          <ha-icon icon="mdi:gate"></ha-icon>
          <h1>Brama – dostęp</h1>
          <span class="sub" id="sub"></span>
        </header>
        <div class="tabs">
          <button data-tab="panel">Panel</button>
          <button data-tab="list">Dostępy</button>
          <button data-tab="history">Historia</button>
          <button data-tab="settings">Ustawienia</button>
        </div>
        <div class="msg" id="msg"></div>

        <div id="tab-panel">
          <div class="new">
            <input id="name" placeholder="Imię osoby, np. Marek" autocomplete="off" />
            <button id="add">Utwórz dostęp</button>
          </div>
          <div class="controls">
            <div class="seg" id="seg"></div>
            <span class="hint dl" id="l-from" style="display:none">od</span>
            <input id="from" type="datetime-local" style="display:none" />
            <span class="hint dl" id="l-until" style="display:none">do</span>
            <input id="until" type="datetime-local" style="display:none" />
            <input id="count" type="number" min="1" max="9999" value="3"
                   style="display:none;width:96px" />
            <span class="hint" id="ttlhint"></span>
          </div>
          <div class="controls" id="targetrow" style="display:none">
            <span class="hint">Obiekt:</span>
            <select id="target" class="sel"></select>
          </div>
          <div class="quick" id="quick"></div>
        </div>

        <div id="tab-list" style="display:none">
          <div class="filters">
            <div class="fl"><span>Ograniczenie:</span>
              <select id="f-restr" class="sel"></select></div>
            <div class="fl" id="f-target-wrap" style="display:none"><span>Obiekt:</span>
              <select id="f-target" class="sel"></select></div>
          </div>
          <div class="bar">
            <span class="count" id="count-label"></span>
            <span class="spacer"></span>
            <button class="ghost" id="refresh">Odśwież</button>
            <button class="ghost" id="purge">Usuń wygasłe</button>
            <button class="ghost" id="csv">CSV</button>
            <button class="ghost" id="json">JSON</button>
          </div>
          <div id="list"></div>
        </div>

        <div id="tab-settings" class="settings" style="display:none">
          <div id="settings-body"></div>
        </div>

        <div id="tab-history" style="display:none">
          <div id="stats-block"></div>
          <div class="filters">
            <div class="fl"><span>Zdarzenie:</span><select id="h-kind" class="sel"></select></div>
            <div class="fl"><span>Obiekt:</span><select id="h-target" class="sel"></select></div>
            <div class="fl"><span>Użytkownik:</span><select id="h-user" class="sel"></select></div>
          </div>
          <div class="bar">
            <span class="count" id="hcount"></span>
            <span class="spacer"></span>
            <button class="ghost" id="hrefresh">Odśwież</button>
            <button class="ghost danger" id="hclear">Usuń wszystko</button>
          </div>
          <div id="hist-list"></div>
        </div>
      </div>`;

    this.$ = (id) => root.getElementById(id);
    root.querySelectorAll(".tabs button").forEach((b) =>
      b.addEventListener("click", () => this._setTab(b.dataset.tab)));

    const seg = this.$("seg");
    for (const [key, label] of MODES) {
      const b = document.createElement("button");
      b.textContent = label; b.dataset.mode = key;
      b.addEventListener("click", () => this._setMode(key));
      seg.appendChild(b);
    }
    this._setMode("permanent");

    const fr = this.$("f-restr");
    for (const [key, label] of FILTERS) {
      const o = document.createElement("option");
      o.value = key; o.textContent = label; fr.appendChild(o);
    }
    fr.addEventListener("change", () => { this._fRestr = fr.value; this._renderList(); });
    this.$("f-target").addEventListener("change", (e) => {
      this._fTarget = e.target.value; this._renderList(); });

    this.$("add").addEventListener("click", () => this._create());
    this.$("name").addEventListener("keydown", (e) => { if (e.key === "Enter") this._create(); });
    this.$("refresh").addEventListener("click", () => this._load());
    this.$("hrefresh").addEventListener("click", () => this._load());
    this.$("h-target").addEventListener("change", (e) => {
      this._hFTarget = e.target.value; this._renderHistory(); });
    this.$("h-user").addEventListener("change", (e) => {
      this._hFUser = e.target.value; this._renderHistory(); });
    this.$("hclear").addEventListener("click", () => this._deleteHistory(null));
    const hk = this.$("h-kind");
    for (const [v, t] of [["all", "Wszystkie"], ["opened", "Otwarcia"],
        ["failed", "Nieudane próby"], ["closed", "Zamknięcia"]]) {
      const o = document.createElement("option");
      o.value = v; o.textContent = t; hk.appendChild(o);
    }
    hk.addEventListener("change", (e) => { this._hFKind = e.target.value; this._renderHistory(); });
    this.$("purge").addEventListener("click", () => this._purge());
    this.$("csv").addEventListener("click", () => this._export("csv"));
    this.$("json").addEventListener("click", () => this._export("json"));

    this._setTab("panel");
    this._load();
  }

  _setTab(tab) {
    this._tab = tab;
    this.shadowRoot.querySelectorAll(".tabs button").forEach((b) =>
      b.classList.toggle("on", b.dataset.tab === tab));
    this.$("tab-panel").style.display = tab === "panel" ? "" : "none";
    this.$("tab-list").style.display = tab === "list" ? "" : "none";
    this.$("tab-history").style.display = tab === "history" ? "" : "none";
    this.$("tab-settings").style.display = tab === "settings" ? "" : "none";
    const subs = {
      panel: "utwórz dostęp lub otwórz obiekt ręcznie",
      list: "rozwiń dostęp, by zmienić ustawienia i zobaczyć historię",
      history: "wszystkie otwarcia: linki, panel, HA, NFC",
      settings: "konfiguracja i udostępnianie",
    };
    this.$("sub").textContent = subs[tab] || "";
    if (tab === "settings") this._loadSettings();
    if (tab === "history") this._renderHistory();
  }

  _setMode(mode) {
    this._mode = mode;
    this.shadowRoot.querySelectorAll("#seg button").forEach((b) =>
      b.classList.toggle("on", b.dataset.mode === mode));
    const timeMode = mode === "until";
    for (const id of ["l-from", "from", "l-until", "until"])
      this.$(id).style.display = timeMode ? "" : "none";
    this.$("count").style.display = mode === "count" ? "" : "none";
    const hints = {
      permanent: "Działa bez ograniczeń, dopóki go nie usuniesz.",
      until: "Okno ważności. „od” puste = od teraz.",
      count: "Działa podaną liczbę razy, potem wygasa.",
      once: "Otwiera raz, potem wygasa.",
    };
    this.$("ttlhint").textContent = hints[mode] || "";
    if (timeMode && !this.$("until").value)
      this.$("until").value = localInput(24 * 3600 * 1000);
  }

  _url(u) { return `${location.origin}/api/webhook/${u.webhook_id}`; }
  _targetName(eid) {
    const t = (this._targets || []).find((x) => x.entity_id === eid);
    return t ? t.name : eid;
  }
  _fillTargetSelect(sel, selected, withAll) {
    sel.innerHTML = "";
    if (withAll) {
      const o = document.createElement("option");
      o.value = "all"; o.textContent = "Wszystkie";
      if (selected === "all") o.selected = true;
      sel.appendChild(o);
    }
    for (const t of this._targets || []) {
      const o = document.createElement("option");
      o.value = t.entity_id; o.textContent = t.name;
      if (t.entity_id === selected) o.selected = true;
      sel.appendChild(o);
    }
  }

  _flash(text, kind) {
    const el = this.$("msg");
    el.textContent = text || "";
    el.className = "msg" + (kind ? " " + kind : "");
  }

  _payloadFrom(mode, fromVal, untilVal, countVal) {
    const p = {};
    if (mode === "until") {
      if (!untilVal) { this._flash("Wybierz datę „do”.", "err"); return null; }
      p.expires_at = new Date(untilVal).toISOString();
      if (fromVal) p.starts_at = new Date(fromVal).toISOString();
    } else if (mode === "count") {
      const n = parseInt(countVal, 10);
      if (!n || n < 1) { this._flash("Podaj liczbę użyć (min. 1).", "err"); return null; }
      p.uses_total = n;
    } else if (mode === "once") {
      p.uses_total = 1;
    }
    return p;
  }

  async _load() {
    try {
      const u = await this._hass.callApi("GET", "gate_access/users");
      this._users = u.users || [];
    } catch (e) {
      this._flash("Nie udało się wczytać listy dostępów. Odśwież stronę.", "err");
      return;
    }
    try {
      const t = await this._hass.callApi("GET", "gate_access/targets");
      this._targets = t.targets || [];
    } catch (e) { this._targets = []; }
    try {
      const h = await this._hass.callApi("GET", "gate_access/history");
      this._history = h.history || [];
    } catch (e) { this._history = []; }
    try {
      const st = await this._hass.callApi("GET", "gate_access/stats");
      this._stats = st || { enabled: false, stats: [] };
    } catch (e) { this._stats = { enabled: false, stats: [] }; }
    try {
      const se = await this._hass.callApi("GET", "gate_access/settings");
      this._settings = se || {};
    } catch (e) { this._settings = {}; }

    // history filters
    this._fillTargetSelect(this.$("h-target"), this._hFTarget, true);
    const names = Array.from(new Set((this._history || []).map((h) => h.name).filter(Boolean))).sort();
    const hu = this.$("h-user");
    hu.innerHTML = "";
    const all = document.createElement("option");
    all.value = "all"; all.textContent = "Wszyscy"; hu.appendChild(all);
    for (const n of names) {
      const o = document.createElement("option");
      o.value = n; o.textContent = n;
      if (n === this._hFUser) o.selected = true;
      hu.appendChild(o);
    }
    if (!names.includes(this._hFUser)) this._hFUser = "all";

    const trow = this.$("targetrow");
    if ((this._targets || []).length > 1) {
      this._fillTargetSelect(this.$("target"), this._targets[0].entity_id, false);
      trow.style.display = "";
      this._fillTargetSelect(this.$("f-target"), this._fTarget, true);
      this.$("f-target-wrap").style.display = "";
    } else {
      trow.style.display = "none";
      this.$("f-target-wrap").style.display = "none";
    }
    this._buildQuickOpen();
    this._renderList();
    if (this._tab === "history") this._renderHistory();
  }

  _buildQuickOpen() {
    const quick = this.$("quick");
    quick.innerHTML = "";
    const targets = this._targets || [];
    if (!targets.length) { quick.style.display = "none"; return; }
    const label = document.createElement("span");
    label.className = "qlabel"; label.textContent = "Szybkie otwarcie";
    quick.appendChild(label);
    for (const t of targets) {
      const b = document.createElement("button");
      b.textContent = t.name;
      b.addEventListener("click", () => this._openNow(t, b));
      quick.appendChild(b);
    }
    quick.style.display = "flex";
  }

  async _openNow(target, btn) {
    btn.disabled = true;
    this._flash(`Otwieram: ${target.name}…`);
    try {
      await this._hass.callApi("POST", "gate_access/open", { target: target.entity_id });
      this._flash(`Otwarto: ${target.name}.`, "ok");
    } catch (e) {
      this._flash(e?.body?.message || "Nie udało się otworzyć.", "err");
    } finally { btn.disabled = false; }
  }

  async _create() {
    const input = this.$("name");
    const name = input.value.trim();
    if (!name) { this._flash("Wpisz imię osoby.", "err"); return; }
    const payload = this._payloadFrom(
      this._mode, this.$("from").value, this.$("until").value, this.$("count").value);
    if (!payload) return;
    payload.name = name;
    if ((this._targets || []).length > 1) payload.target = this.$("target").value;

    this.$("add").disabled = true;
    this._flash("Tworzę dostęp…");
    try {
      const user = await this._hass.callApi("POST", "gate_access/users", payload);
      input.value = "";
      this._flash(`Utworzono dostęp dla ${user.name}. Link skopiowany, jest też w „Dostępy”.`, "ok");
      await this._load();
      this._copy(this._url(user));
    } catch (e) {
      this._flash(e?.body?.message || "Nie udało się utworzyć dostępu.", "err");
    } finally { this.$("add").disabled = false; }
  }

  async _saveType(u, mode, fromVal, untilVal, countVal, target) {
    const payload = this._payloadFrom(mode, fromVal, untilVal, countVal);
    if (!payload) return;
    payload.set_ttl = true;
    if (target) payload.target = target;
    try {
      await this._hass.callApi(
        "PATCH", `gate_access/users/${encodeURIComponent(u.webhook_id)}`, payload);
      this._flash(`Zaktualizowano dostęp: ${u.name}.`, "ok");
      this._load();
    } catch (e) { this._flash(e?.body?.message || "Nie udało się zapisać zmian.", "err"); }
  }

  async _reactivate(u) {
    const payload = { enabled: true };
    if (u.uses_total != null) { payload.set_ttl = true; payload.uses_total = u.uses_total; }
    else if (u.expires_at) {
      payload.set_ttl = true;
      payload.expires_at = new Date(Date.now() + 24 * 3600 * 1000).toISOString();
    }
    try {
      await this._hass.callApi(
        "PATCH", `gate_access/users/${encodeURIComponent(u.webhook_id)}`, payload);
      this._flash(`Reaktywowano: ${u.name}. Ten sam link znów działa.`, "ok");
      this._load();
    } catch (e) { this._flash(e?.body?.message || "Nie udało się reaktywować.", "err"); }
  }

  async _toggle(u) {
    const cur = u.enabled !== false;
    try {
      await this._hass.callApi(
        "PATCH", `gate_access/users/${encodeURIComponent(u.webhook_id)}`,
        { enabled: !cur });
      this._flash(cur ? `Wyłączono dostęp: ${u.name}.` : `Włączono dostęp: ${u.name}.`, "ok");
      this._load();
    } catch (e) { this._flash(e?.body?.message || "Nie udało się zmienić stanu.", "err"); }
  }

  async _reset(u) {
    const def = u.uses_total != null ? u.uses_total : 2;
    const val = prompt(`Reset dostępu „${u.name}” — na ile użyć?`, String(def));
    if (val === null) return;
    const n = parseInt(val, 10);
    if (!n || n < 1) { this._flash("Podaj liczbę użyć (min. 1).", "err"); return; }
    try {
      await this._hass.callApi(
        "PATCH", `gate_access/users/${encodeURIComponent(u.webhook_id)}`,
        { uses_total: n });
      this._flash(`Zresetowano: ${u.name} → ${n} użyć. Ten sam link znów działa.`, "ok");
      this._load();
    } catch (e) {
      this._flash(e?.body?.message || "Nie udało się zresetować.", "err");
    }
  }

  async _remove(u) {
    if (!confirm(`Usunąć dostęp dla ${u.name}? Jego link przestanie działać.`)) return;
    try {
      await this._hass.callApi(
        "DELETE", `gate_access/users/${encodeURIComponent(u.webhook_id)}`);
      this._open.delete(u.webhook_id);
      this._flash(`Usunięto dostęp: ${u.name}.`, "ok");
      this._load();
    } catch (e) { this._flash("Nie udało się usunąć dostępu.", "err"); }
  }

  async _purge() {
    try {
      const res = await this._hass.callApi("POST", "gate_access/purge");
      this._flash(res.purged ? `Usunięto wygasłe: ${res.purged}.` : "Brak wygasłych.", "ok");
      this._load();
    } catch (e) { this._flash("Nie udało się wyczyścić wygasłych.", "err"); }
  }

  async _copy(text) { try { await navigator.clipboard.writeText(text); } catch (e) {} }

  _download(filename, text, mime) {
    const blob = new Blob([text], { type: mime });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = filename; a.click();
    URL.revokeObjectURL(a.href);
  }

  _export(kind) {
    const rows = this._filtered();
    if (!rows.length) { this._flash("Brak dostępów do pobrania (sprawdź filtry).", "err"); return; }
    const stamp = new Date().toISOString().slice(0, 10);
    const flat = (u) => ({
      name: u.name, target: u.target || "", enabled: u.enabled === false ? "nie" : "tak",
      webhook_id: u.webhook_id, created: u.created, starts_at: u.starts_at || "",
      expires_at: u.expires_at || "", uses_total: u.uses_total ?? "",
      uses_left: u.uses_left ?? "", url: this._url(u),
    });
    if (kind === "json") {
      this._download(`brama-dostepy-${stamp}.json`,
        JSON.stringify(rows.map(flat), null, 2), "application/json");
      return;
    }
    const esc = (v) => `"${String(v).replace(/"/g, '""')}"`;
    const head = ["name", "target", "enabled", "webhook_id", "created", "starts_at",
      "expires_at", "uses_total", "uses_left", "url"];
    const lines = [head.join(",")].concat(
      rows.map((u) => { const f = flat(u); return head.map((k) => esc(f[k])).join(","); }));
    this._download(`brama-dostepy-${stamp}.csv`, lines.join("\n"), "text/csv");
  }

  _restrType(u) {
    if (u.expires_at) return "until";
    if (u.uses_total === 1) return "once";
    if (u.uses_total != null) return "count";
    return "permanent";
  }

  _filtered() {
    return (this._users || []).filter((u) => {
      const act = (u.status || {}).activity;
      const f = this._fRestr;
      if (f === "expired") { if (act !== "expired") return false; }
      else if (f === "disabled") { if (act !== "disabled") return false; }
      else if (f === "pending") { if (act !== "pending") return false; }
      else if (f !== "all" && this._restrType(u) !== f) return false;
      if (this._fTarget !== "all" && u.target !== this._fTarget) return false;
      return true;
    });
  }

  _fmtDate(iso) {
    return new Date(iso).toLocaleString("pl-PL",
      { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  _pill(u) {
    const s = u.status || {};
    if (s.activity === "disabled") return { cls: "dead", text: "Wyłączony" };
    if (s.activity === "pending")
      return { cls: "warn", text: "od " + this._fmtDate(u.starts_at) };
    if (s.activity === "expired") return { cls: "dead", text: "Wygasł" };
    if (u.expires_at) return { cls: "warn", text: "do " + this._fmtDate(u.expires_at) };
    if (u.uses_left != null) return { cls: "warn", text: `${u.uses_left}/${u.uses_total} użyć` };
    return { cls: "live", text: "Stały" };
  }

  _fmtTs(iso) {
    return new Date(iso).toLocaleString("pl-PL",
      { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  _sourceLabel(s) {
    const map = { link: "link", panel: "panel", ha: "HA", auto: "automat", "usługa": "usługa" };
    return map[s] || s || "—";
  }

  _statusLabel(s) {
    if (s === "expired") return ["wygasły", "expired"];
    if (s === "disabled") return ["wyłączony", "expired"];
    if (s === "pending") return ["nieaktywny", "closed"];
    if (s === "rate") return ["limit", "expired"];
    if (s === "closed") return ["zamknięto", "closed"];
    return ["otwarto", "opened"];
  }

  _kindOf(status) {
    if (status === "closed") return "closed";
    if (["expired", "disabled", "pending", "rate"].includes(status)) return "failed";
    return "opened";
  }

  _renderStats() {
    const box = this.$("stats-block");
    const st = this._stats || {};
    if (!st.enabled) { box.innerHTML = ""; return; }
    const rows = st.stats || [];
    const cells = rows.map((r) => `
      <div class="srow">
        <span class="sname">${r.name}</span>
        <span class="snum"><b>${r.today}</b><em>dziś</em></span>
        <span class="snum"><b>${r.month}</b><em>mies.</em></span>
        <span class="snum"><b>${r.year}</b><em>rok</em></span>
        <span class="snum"><b>${r.total}</b><em>razem</em></span>
      </div>`).join("");
    box.innerHTML = `<div class="section-label">Statystyki otwarć</div>
      <div class="stats">${cells || '<div class="hist"><div class="none">Brak danych.</div></div>'}</div>`;
  }

  _renderHistory() {
    this._renderStats();
    const box = this.$("hist-list");
    const all = this._history || [];
    const rows = all.filter((h) =>
      (this._hFTarget === "all" || h.target === this._hFTarget) &&
      (this._hFUser === "all" || h.name === this._hFUser) &&
      (this._hFKind === "all" || this._kindOf(h.status) === this._hFKind));
    this.$("hcount").textContent = all.length ? `${rows.length} z ${all.length}` : "";
    if (!all.length) { box.innerHTML = `<div class="empty">Brak zapisanych otwarć.</div>`; return; }
    if (!rows.length) { box.innerHTML = `<div class="empty">Brak zdarzeń pasujących do filtrów.</div>`; return; }
    box.innerHTML = "";
    for (const h of rows.slice(0, 200)) {
      const r = document.createElement("div");
      r.className = "hrow";
      const t = document.createElement("span");
      t.className = "ht"; t.textContent = this._fmtTs(h.ts);
      const nm = document.createElement("span");
      nm.className = "hn"; nm.textContent = h.name || "—";
      const [txt, cls] = this._statusLabel(h.status);
      const s = document.createElement("span");
      s.className = "hs " + cls; s.textContent = txt;
      r.append(t, nm, s);
      if (h.target) {
        const tg = document.createElement("span");
        tg.className = "htg"; tg.textContent = this._targetName(h.target);
        r.appendChild(tg);
      }
      const src = document.createElement("span");
      src.className = "hsrc"; src.textContent = this._sourceLabel(h.source);
      r.appendChild(src);
      const del = document.createElement("button");
      del.className = "hdel"; del.textContent = "✕"; del.title = "Usuń wpis";
      del.addEventListener("click", () => this._deleteHistory(h.id));
      r.appendChild(del);
      box.appendChild(r);
    }
  }

  async _deleteHistory(id) {
    const all = !id;
    if (!confirm(all ? "Usunąć CAŁĄ historię?" : "Usunąć ten wpis?")) return;
    const body = {};
    if (id) body.id = id;
    if (this._settings && this._settings.has_delete_password) {
      const pw = prompt("Hasło do kasowania historii:");
      if (pw === null) return;
      body.password = pw;
    }
    try {
      const res = await this._hass.callApi("POST", "gate_access/history/delete", body);
      this._flash(all ? `Wyczyszczono historię (${res.deleted}).` : "Usunięto wpis.", "ok");
      this._load();
    } catch (e) {
      this._flash(e?.body?.message || "Nie udało się usunąć (hasło?).", "err");
    }
  }

  _buildBody(u) {
    const body = document.createElement("div");
    body.className = "body";
    const el = document.createElement("div");
    el.className = "section-label"; el.textContent = "Ustawienia dostępu";
    body.appendChild(el);

    let mode = this._restrType(u);
    const editor = document.createElement("div");
    editor.className = "editor";
    const seg = document.createElement("div");
    seg.className = "seg";
    const lFrom = document.createElement("span");
    lFrom.className = "hint dl"; lFrom.textContent = "od";
    const from = document.createElement("input");
    from.type = "datetime-local";
    const lUntil = document.createElement("span");
    lUntil.className = "hint dl"; lUntil.textContent = "do";
    const until = document.createElement("input");
    until.type = "datetime-local";
    const count = document.createElement("input");
    count.type = "number"; count.min = "1"; count.max = "9999";
    count.style.width = "96px"; count.value = u.uses_total > 1 ? u.uses_total : 3;
    if (u.starts_at) from.value = new Date(u.starts_at).toISOString().slice(0, 16);
    if (u.expires_at) until.value = new Date(u.expires_at).toISOString().slice(0, 16);

    const applyVis = () => {
      seg.querySelectorAll("button").forEach((b) =>
        b.classList.toggle("on", b.dataset.mode === mode));
      const tm = mode === "until";
      lFrom.style.display = from.style.display = tm ? "" : "none";
      lUntil.style.display = until.style.display = tm ? "" : "none";
      count.style.display = mode === "count" ? "" : "none";
      if (tm && !until.value) until.value = localInput(24 * 3600 * 1000);
    };
    for (const [key, label] of MODES) {
      const b = document.createElement("button");
      b.textContent = label; b.dataset.mode = key;
      b.addEventListener("click", () => { mode = key; applyVis(); });
      seg.appendChild(b);
    }
    let targetSel = null;
    editor.append(seg, lFrom, from, lUntil, until, count);
    if ((this._targets || []).length > 1) {
      targetSel = document.createElement("select");
      targetSel.className = "sel";
      this._fillTargetSelect(targetSel, u.target, false);
      editor.appendChild(targetSel);
    }
    const save = document.createElement("button");
    save.className = "save"; save.textContent = "Zapisz";
    save.addEventListener("click", () =>
      this._saveType(u, mode, from.value, until.value, count.value,
        targetSel ? targetSel.value : null));
    editor.appendChild(save);
    body.appendChild(editor);
    applyVis();

    const hl = document.createElement("div");
    hl.className = "section-label"; hl.textContent = "Historia otwarć";
    body.appendChild(hl);
    const hist = document.createElement("div");
    hist.className = "hist";
    const rows = (this._history || []).filter((h) => h.webhook_id === u.webhook_id);
    if (!rows.length) {
      const none = document.createElement("div");
      none.className = "none"; none.textContent = "Brak otwarć.";
      hist.appendChild(none);
    } else {
      for (const h of rows.slice(0, 30)) {
        const r = document.createElement("div"); r.className = "hrow";
        const t = document.createElement("span");
        t.className = "ht"; t.textContent = this._fmtTs(h.ts);
        const [txt, cls] = this._statusLabel(h.status);
        const s = document.createElement("span");
        s.className = "hs " + cls; s.textContent = txt;
        r.append(t, s);
        if (h.target) {
          const tg = document.createElement("span");
          tg.className = "htg"; tg.textContent = this._targetName(h.target);
          r.appendChild(tg);
        }
        const src = document.createElement("span");
        src.className = "hsrc"; src.textContent = this._sourceLabel(h.source);
        r.appendChild(src);
        hist.appendChild(r);
      }
    }
    body.appendChild(hist);
    return body;
  }

  _renderList() {
    const list = this.$("list");
    const all = this._users || [];
    const rows = this._filtered();
    this.$("count-label").textContent =
      `${rows.length} z ${all.length}`;
    if (!all.length) {
      list.innerHTML = `<div class="empty">Brak dostępów. Utwórz pierwszy w zakładce „Panel”.</div>`;
      return;
    }
    if (!rows.length) {
      list.innerHTML = `<div class="empty">Brak dostępów pasujących do filtrów.</div>`;
      return;
    }
    list.innerHTML = "";
    const many = (this._targets || []).length > 1;
    for (const u of rows) {
      const url = this._url(u);
      const created = (u.created || "").replace("T", " ").replace(/(\+.*|Z)$/, "");
      const pill = this._pill(u);
      const act = (u.status || {}).activity;
      const dead = act === "expired" || act === "disabled";
      const isOpen = this._open.has(u.webhook_id);
      const tag = document.createElement("div");
      tag.className = "tag" + (dead ? " dead" : "") + (isOpen ? " open" : "");
      tag.innerHTML = `
        <div class="head">
          <span class="chev">▶</span>
          <div>
            <div class="who">
              <span class="nm"></span>
              <span class="pill ${pill.cls}"></span>
              ${many ? '<span class="target-tag"></span>' : ""}
            </div>
            <div class="when">utworzono ${created}</div>
          </div>
          <div class="actions">
            <button class="copy">Kopiuj</button>
            <button class="open">Otwórz</button>
            <button class="toggle">${u.enabled === false ? "Włącz" : "Wyłącz"}</button>
            ${(act === "expired" || act === "disabled") ? '<button class="react">Reaktywuj</button>' : ""}
            ${u.uses_total != null ? '<button class="reset">Reset</button>' : ""}
            <button class="del">Usuń</button>
          </div>
        </div>
        <div class="url"></div>`;
      tag.querySelector(".nm").textContent = u.name;
      tag.querySelector(".pill").textContent = pill.text;
      if (many) tag.querySelector(".target-tag").textContent = "→ " + this._targetName(u.target);
      tag.querySelector(".url").textContent = url;

      tag.querySelector(".head").addEventListener("click", (e) => {
        if (e.target.closest(".actions")) return;
        if (this._open.has(u.webhook_id)) this._open.delete(u.webhook_id);
        else this._open.add(u.webhook_id);
        tag.classList.toggle("open");
      });
      const a = tag.querySelector(".actions");
      a.querySelector(".copy").addEventListener("click", () => {
        this._copy(url); this._flash(`Skopiowano link: ${u.name}.`, "ok"); });
      a.querySelector(".open").addEventListener("click", () => window.open(url, "_blank"));
      a.querySelector(".toggle").addEventListener("click", () => this._toggle(u));
      const react = a.querySelector(".react");
      if (react) react.addEventListener("click", () => this._reactivate(u));
      const rst = a.querySelector(".reset");
      if (rst) rst.addEventListener("click", () => this._reset(u));
      a.querySelector(".del").addEventListener("click", () => this._remove(u));
      tag.appendChild(this._buildBody(u));
      list.appendChild(tag);
    }
  }

  async _loadSettings() {
    const box = this.$("settings-body");
    box.innerHTML = `<div class="note">Wczytuję…</div>`;
    let s;
    try {
      s = await this._hass.callApi("GET", "gate_access/settings");
    } catch (e) {
      box.innerHTML = `<div class="note">Nie udało się wczytać ustawień.</div>`;
      return;
    }
    const targets = s.targets || [];
    const chips = targets.length
      ? targets.map((t) => `<span class="chip">${t.name}</span>`).join("")
      : `<span class="k">brak</span>`;
    const close = s.close_after ? `${s.close_after} s` : "wyłączone";
    const share = s.admin_only ? "tylko administratorzy" : "wszyscy użytkownicy";
    box.innerHTML = `
      <div class="item"><span class="k">Obiekty</span>
        <span class="v"><span class="chips">${chips}</span></span></div>
      <div class="item"><span class="k">Auto-zamykanie</span><span class="v">${close}</span></div>
      <div class="item"><span class="k">Plik logu</span><span class="v">${s.log_path || "—"}</span></div>
      <div class="item"><span class="k">Widoczność panelu</span><span class="v">${share}</span></div>
      <button class="open-settings" id="open-settings">Otwórz ustawienia integracji</button>
      <div class="note">Obiekty, auto-zamykanie i udostępnianie panelu zmienisz w ustawieniach
        integracji (menu: „Obiekty, log i auto-zamykanie” oraz „Udostępnianie panelu”).
        Aby żona/domownik widzieli ten panel, w „Udostępnianiu” wyłącz „Tylko administratorzy”.</div>`;
    this.$("open-settings").addEventListener("click", () => {
      history.pushState(null, "", "/config/integrations/integration/gate_access");
      this.dispatchEvent(new CustomEvent("location-changed", { bubbles: true, composed: true }));
    });
  }
}

customElements.define("gate-access-panel", GateAccessPanel);
