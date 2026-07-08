// Gate Access — Lovelace card to create a new gate/wicket link from a dashboard.
const MODES = [
  ["permanent", "Stały"],
  ["until", "Czasowy"],
  ["once", "Jednorazowy"],
];

const STYLE = `
  .box{padding:16px}
  .row{display:flex;gap:8px;margin-bottom:10px}
  input,select{flex:1;background:var(--card-background-color,#1b1f26);
    border:1px solid var(--divider-color,#2a2f38);color:var(--primary-text-color,#e8e6df);
    border-radius:10px;padding:11px 13px;font-size:.95rem}
  input:focus,select:focus{outline:none;border-color:var(--primary-color,#c8952f)}
  .seg{display:inline-flex;border:1px solid var(--divider-color,#2a2f38);
    border-radius:10px;overflow:hidden;margin-bottom:12px}
  .seg button{background:transparent;border:0;color:var(--secondary-text-color,#8b9099);
    padding:9px 14px;font-size:.82rem;cursor:pointer;border-right:1px solid var(--divider-color,#2a2f38)}
  .seg button:last-child{border-right:0}
  .seg button.on{background:var(--primary-color,#c8952f);color:#1a1206;font-weight:600}
  .create{width:100%;background:var(--primary-color,#c8952f);color:#1a1206;border:0;
    border-radius:10px;padding:12px;font-weight:600;font-size:.95rem;cursor:pointer}
  .create:disabled{opacity:.5;cursor:default}
  .msg{min-height:18px;font-size:.82rem;margin-top:10px;color:var(--secondary-text-color,#8b9099)}
  .msg.err{color:var(--error-color,#c9524a)}
  .result{margin-top:12px;display:none}
  .result.show{display:block}
  .link{font-family:ui-monospace,monospace;font-size:.78rem;color:var(--primary-color,#e8b45a);
    background:var(--secondary-background-color,#0f1216);border:1px solid var(--divider-color,#2a2f38);
    border-radius:8px;padding:9px 11px;overflow-x:auto;white-space:nowrap}
  .copy{margin-top:8px;background:transparent;border:1px solid var(--divider-color,#2a2f38);
    color:var(--secondary-text-color,#8b9099);border-radius:8px;padding:7px 12px;
    font-size:.8rem;cursor:pointer}
`;

class GateAccessCard extends HTMLElement {
  setConfig(config) { this._config = config || {}; this._mode = "permanent"; }
  set hass(hass) {
    this._hass = hass;
    if (!this._built) { this._built = true; this._build(); }
    if (!this._targetsLoaded) { this._targetsLoaded = true; this._loadTargets(); }
  }
  getCardSize() { return 3; }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    const title = this._config.title || "Nowy link do bramy";
    root.innerHTML = `
      <ha-card header="${title}">
        <style>${STYLE}</style>
        <div class="box">
          <div class="row"><input id="name" placeholder="Imię osoby" autocomplete="off" /></div>
          <div class="row" id="targetrow" style="display:none"><select id="target"></select></div>
          <div class="seg" id="seg"></div>
          <input id="until" type="datetime-local"
                 style="display:none;width:100%;margin-bottom:12px" />
          <button class="create" id="create">Utwórz link</button>
          <div class="msg" id="msg"></div>
          <div class="result" id="result">
            <div class="link" id="link"></div>
            <button class="copy" id="copy">Kopiuj link</button>
          </div>
        </div>
      </ha-card>`;

    this.$ = (id) => root.getElementById(id);
    const seg = this.$("seg");
    for (const [key, label] of MODES) {
      const b = document.createElement("button");
      b.textContent = label; b.dataset.mode = key;
      if (key === this._mode) b.classList.add("on");
      b.addEventListener("click", () => this._setMode(key));
      seg.appendChild(b);
    }
    this.$("create").addEventListener("click", () => this._create());
    this.$("name").addEventListener("keydown", (e) => { if (e.key === "Enter") this._create(); });
    this.$("copy").addEventListener("click", () => {
      navigator.clipboard.writeText(this.$("link").textContent).catch(() => {});
    });
  }

  async _loadTargets() {
    try {
      const t = await this._hass.callApi("GET", "gate_access/targets");
      const targets = t.targets || [];
      if (targets.length > 1) {
        const sel = this.$("target");
        sel.innerHTML = "";
        for (const x of targets) {
          const o = document.createElement("option");
          o.value = x.entity_id; o.textContent = x.name;
          sel.appendChild(o);
        }
        this.$("targetrow").style.display = "";
      }
    } catch (e) { /* single target or unavailable */ }
  }

  _setMode(mode) {
    this._mode = mode;
    this.shadowRoot.querySelectorAll("#seg button").forEach((b) =>
      b.classList.toggle("on", b.dataset.mode === mode));
    const until = this.$("until");
    until.style.display = mode === "until" ? "" : "none";
    if (mode === "until" && !until.value) {
      const d = new Date(Date.now() + 24 * 3600 * 1000);
      d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
      until.value = d.toISOString().slice(0, 16);
    }
  }

  _flash(text, err) {
    const m = this.$("msg");
    m.textContent = text || ""; m.className = "msg" + (err ? " err" : "");
  }

  async _create() {
    const name = this.$("name").value.trim();
    if (!name) { this._flash("Wpisz imię osoby.", true); return; }
    const payload = { name };
    if (this._mode === "until") {
      const v = this.$("until").value;
      if (!v) { this._flash("Wybierz datę wygaśnięcia.", true); return; }
      payload.expires_at = new Date(v).toISOString();
    } else if (this._mode === "once") {
      payload.uses_total = 1;
    }
    const trow = this.$("targetrow");
    if (trow && trow.style.display !== "none") payload.target = this.$("target").value;

    this.$("create").disabled = true;
    this._flash("Tworzę…");
    try {
      const user = await this._hass.callApi("POST", "gate_access/users", payload);
      const url = `${location.origin}/api/webhook/${user.webhook_id}`;
      this.$("link").textContent = url;
      this.$("result").classList.add("show");
      this._flash(`Gotowe: ${user.name}.`);
      this.$("name").value = "";
      navigator.clipboard.writeText(url).catch(() => {});
    } catch (e) {
      this._flash(e?.body?.message || "Nie udało się utworzyć linku.", true);
    } finally {
      this.$("create").disabled = false;
    }
  }
}

customElements.define("gate-access-card", GateAccessCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "gate-access-card",
  name: "Gate Access – nowy link",
  description: "Tworzy link otwierający wybrany obiekt (brama/furtka): stały, czasowy lub jednorazowy.",
});
