// Panneau « Chaudière » de l'intégration De Dietrich Smart : programmes horaires, courbe de chauffe,
// consommation et journal.
// Repris de l'interface de l'application ancienne application (mêmes règles et mêmes textes).
// Aucune ressource externe : Lit est servi par l'intégration.
import { LitElement, html, svg, css, nothing } from './lit-all.min.js';

const T = 'de_dietrich_smart';
// Jours du programme : 0 = dimanche (format du cloud) ; affichage du lundi au dimanche.
const DAYS = ['Dimanche', 'Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi'];
const DAYS_S = ['Dim.', 'Lun.', 'Mar.', 'Mer.', 'Jeu.', 'Ven.', 'Sam.'];
const WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0];
const MAX_PER_DAY = 6;
const MODE = { schedule: 'programme', override: 'dérogation', manual: 'manuel', antifrost: 'hors-gel' };
const DHW_MODE = { schedule: 'programme', comfort: 'confort permanent', eco: 'éco (hors-gel)', boost: 'boost', off: 'arrêt' };
const KINDS = [['', 'Tout'], ['defaut', 'Défauts'], ['pression', 'Pression'], ['mode', 'Modes'], ['consigne', 'Consignes'],
  ['ecs', 'Eau chaude'], ['connexion', 'Connexion'], ['reglage', 'Réglages'], ['programme', 'Programmes']];
const ACTION = { consigne: 'Consigne', mode: 'Mode', programme: 'Programme', cheminee: 'Mode cheminée', ecs_mode: 'Mode eau chaude',
  ecs_consigne: 'Consigne eau chaude', ecs_boost: 'Boost eau chaude', activite: 'Activité', courbe: 'Courbe de chauffe',
  vacances: 'Vacances', creneaux: 'Créneaux', ecs_creneaux: 'Créneaux eau chaude', annulation: 'Annulation' };
const DUR = { '1h': 'pendant 1 h', '2h': 'pendant 2 h', next: "jusqu'au prochain créneau", permanent: 'en permanence' };

const spMin = (sp) => { const [h, m] = sp.time.split(':').map(Number); return h * 60 + m; };
const hm = (min) => `${String(Math.floor(min / 60)).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;
const fDeg = (v) => (v == null ? '—' : `${String(Math.round(v * 10) / 10).replace('.', ',').replace('-', '−')} °C`);
const fShort = (v) => (v == null ? '?' : String(Math.round(v * 10) / 10).replace('.', ','));
const daysLabel = (days = []) => (days.length === 7 ? 'toute la semaine'
  : WEEK_ORDER.filter((d) => days.includes(d)).map((d) => DAYS[d].toLowerCase()).join(', '));

// Créneaux d'un jour, en minutes depuis minuit : le changement en vigueur à 00:00 est le
// dernier des jours précédents (ou de la fin de semaine).
function daySegments(week, day) {
  if (!week?.length) return [];
  const key = (sp) => sp.day * 1440 + spMin(sp);
  const ordered = [...week].sort((a, b) => key(a) - key(b));
  const before = ordered.filter((sp) => sp.day < day);
  let cur = before.length ? before[before.length - 1] : ordered[ordered.length - 1];
  let start = 0;
  const segs = [];
  for (const sp of ordered.filter((x) => x.day === day)) {
    const m = spMin(sp);
    if (m > start) segs.push({ start, end: m, sp: cur });
    start = m;
    cur = sp;
  }
  segs.push({ start, end: 1440, sp: cur });
  return segs;
}

// Courbe de chauffe : départ ≈ pied + pente × (20 − T ext), plafonné au départ maxi (approximation).
const flowAt = (slope, base, outdoor, maxFlow) => {
  const f = base + slope * (20 - outdoor);
  return maxFlow == null ? f : Math.min(f, maxFlow);
};
// Dépassement des bornes du plancher (même calcul que le serveur) : 0 si la courbe est sûre.
function floorExcess(slope, base, maxFlow, g) {
  if (!g?.active) return 0;
  const span = 20 - g.base_temp;
  let e = 0;
  if (slope < g.slope[0]) e += (g.slope[0] - slope) * span; else if (slope > g.slope[1]) e += (slope - g.slope[1]) * span;
  if (base < g.base[0]) e += g.base[0] - base; else if (base > g.base[1]) e += base - g.base[1];
  const f = flowAt(slope, base, g.base_temp, maxFlow);
  if (f > g.max_floor_flow) e += f - g.max_floor_flow;
  return e;
}
const nDays = (n) => `${n} jour${n > 1 ? 's' : ''}`;
const fNum = (v, n = 2) => (v == null ? '—' : String(Math.round(v * 10 ** n) / 10 ** n).replace('.', ',').replace('-', '−'));
const RECO = { keep: 'Garder la courbe', raise_base: 'Monter le pied', lower_base: 'Baisser le pied', raise_slope: 'Monter la pente',
  lower_slope: 'Baisser la pente', switch_to_floor: 'Passer à une courbe de plancher', insufficient_data: 'Données insuffisantes' };
const CONF = { high: 'confiance haute', medium: 'confiance moyenne', low: 'confiance basse' };

// Intensité d'un créneau selon sa consigne (14 °C pâle, 28 °C soutenu).
const intensity = (t) => (t == null ? 0 : Math.round(Math.max(0.1, Math.min(0.6, (t - 14) / 14)) * 100));

class DdSmartPanel extends LitElement {
  static properties = {
    hass: { attribute: false },
    narrow: { type: Boolean },
    _entries: { state: true },
    _entryId: { state: true },
    _tab: { state: true },
    _prog: { state: true },
    _editor: { state: true },
    _actWanted: { state: true },
    _toast: { state: true },
    _journal: { state: true },
    _jTab: { state: true },
    _jKind: { state: true },
    _error: { state: true },
    _now: { state: true },
    _curve: { state: true },
    _draft: { state: true },
    _confirm: { state: true },
    _sim: { state: true },
    _simSource: { state: true },
    _conso: { state: true },
  };

  constructor() {
    super();
    this._entries = null;
    this._tab = 'programs';
    this._prog = null;
    this._editor = null;
    this._actWanted = {};
    this._actTimer = {};
    this._toast = null;
    this._journal = null;
    this._jTab = 'events';
    this._jKind = '';
    this._error = '';
    this._now = Date.now();
    this._lastLoad = 0;
    this._curve = null;
    this._draft = null;
    this._confirm = null;
    this._sim = null;
    this._simSource = 'forecast';
    this._conso = null;
  }

  connectedCallback() {
    super.connectedCallback();
    this._tick = setInterval(() => { this._now = Date.now(); this._load(); }, 30000);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    clearInterval(this._tick);
    clearTimeout(this._toastTimer);
  }

  updated(changed) {
    // Rechargement à l'arrivée de hass, puis au plus toutes les 15 s quand l'état change.
    if (changed.has('hass') && this.hass && Date.now() - this._lastLoad > 15000) this._load();
  }

  async _ws(msg) {
    return this.hass.callWS(msg);
  }

  async _load() {
    if (!this.hass) return;
    this._lastLoad = Date.now();
    try {
      const { entries } = await this._ws({ type: `${T}/overview` });
      this._entries = entries;
      if (!entries.some((e) => e.entry_id === this._entryId)) this._entryId = entries[0]?.entry_id;
      this._error = '';
      if (this._tab === 'journal') await this._loadJournal();
      if (this._tab === 'curve') await this._loadCurve();
      if (this._tab === 'conso') await this._loadConso();
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  async _loadJournal() {
    if (!this._entryId) return;
    const msg = { type: `${T}/journal`, entry_id: this._entryId, limit: 100 };
    if (this._jKind) msg.kind = this._jKind;
    this._journal = await this._ws(msg);
  }

  _loadTab(k) {
    if (k === 'journal') this._loadJournal();
    if (k === 'curve') this._loadCurve();
    if (k === 'conso') this._loadConso();
  }

  async _loadCurve() {
    if (!this._entryId) return;
    this._curve = await this._ws({ type: `${T}/curve`, entry_id: this._entryId });
    await this._loadSim();
  }

  async _loadConso() {
    if (!this._entryId) return;
    this._conso = await this._ws({ type: `${T}/consumption`, entry_id: this._entryId });
  }

  // Export CSV : fichier préparé par le serveur, enregistré par le navigateur (BOM pour Excel).
  async _exportCsv(period) {
    try {
      const { filename, csv } = await this._ws({ type: `${T}/consumption_csv`, entry_id: this._entryId, period });
      const url = URL.createObjectURL(new Blob(['\ufeff', csv], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      this._showToast(e.message || String(e), null, null, true);
    }
  }

  async _loadSim() {
    if (!this._entryId) return;
    this._sim = await this._ws({ type: `${T}/simulate`, entry_id: this._entryId, source: this._simSource });
  }

  get _entry() { return this._entries?.find((e) => e.entry_id === this._entryId); }
  get _appl() { return this._entry?.state?.appliances?.[0]; }
  get _zone() { return this._appl?.zones?.[0]; }
  get _dhw() { return this._appl?.dhw?.[0]; }
  // Écritures réservées aux administrateurs (le serveur les refuse aux autres).
  get _admin() { return this.hass?.user?.is_admin !== false; }
  get _acts() { return Object.fromEntries((this._zone?.activities || []).map((a) => [a.n, a])); }

  _today() {
    const tz = this.hass?.config?.time_zone || 'Europe/Paris';
    const wd = new Intl.DateTimeFormat('en-US', { weekday: 'short', timeZone: tz }).format(new Date(this._now));
    return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(wd);
  }

  _nowMin() {
    const tz = this.hass?.config?.time_zone || 'Europe/Paris';
    const [h, m] = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz })
      .format(new Date(this._now)).split(':').map(Number);
    return h * 60 + m;
  }

  _when(ts) {
    const tz = this.hass?.config?.time_zone || 'Europe/Paris';
    return new Intl.DateTimeFormat('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', timeZone: tz })
      .format(new Date(ts * 1000));
  }

  // --- Commandes -------------------------------------------------------------

  async _command(msg) {
    try {
      const res = await this._ws({ ...msg, entry_id: this._entryId });
      this._showToast(res.message, res.undo_remaining ? res.command_id : null, res.undo_remaining);
      setTimeout(() => this._load(), 8000);
      return res;
    } catch (e) {
      this._showToast(e.message || String(e), null, null, true);
      return null;
    }
  }

  _showToast(text, commandId = null, undoRemaining = null, error = false) {
    clearTimeout(this._toastTimer);
    this._toast = { text, commandId, error };
    // Délai restant donné par le serveur : l'horloge du navigateur peut être décalée.
    const ms = undoRemaining ? Math.max(4000, undoRemaining * 1000) : 6000;
    this._toastTimer = setTimeout(() => { this._toast = null; }, ms);
  }

  async _undo(commandId) {
    this._toast = null;
    await this._command({ type: `${T}/undo`, command_id: commandId });
    if (this._tab === 'journal') this._loadJournal();
  }

  _actBump(n, delta) {
    const a = this._acts[n];
    const cur = this._actWanted[n] ?? a?.setpoint;
    const step = a?.step || 0.5;
    const next = cur == null ? 18 : Math.min(a?.max ?? 30, Math.max(a?.min ?? 5, Math.round((cur + Math.sign(delta) * step) * 10) / 10));
    this._actWanted = { ...this._actWanted, [n]: next };
    clearTimeout(this._actTimer[n]);
    // Les appuis rapprochés n'envoient qu'une commande.
    this._actTimer[n] = setTimeout(async () => {
      await this._command({ type: `${T}/set_activity`, activity: n, setpoint: next });
      const { [n]: _, ...rest } = this._actWanted;
      this._actWanted = rest;
    }, 1200);
  }

  // --- Courbe de chauffe -----------------------------------------------------

  _curveBump(part, delta) {
    const c = this._curve?.curve;
    if (!c) return;
    const d = this._draft ?? { slope: c.slope, base: c.base };
    const step = part === 'slope' ? c.slope_step || 0.1 : c.base_step || 0.5;
    const lo = part === 'slope' ? c.slope_min ?? 0 : c.base_min ?? 15;
    const hi = part === 'slope' ? c.slope_max ?? 4 : c.base_max ?? 80;
    const v = Math.min(hi, Math.max(lo, Math.round((d[part] + Math.sign(delta) * step) * 100) / 100));
    this._draft = { ...d, [part]: v };
  }

  // Problème du brouillon pour le plancher ; une courbe moins dangereuse qu'aujourd'hui reste permise.
  _draftProblem() {
    const { curve: c, guard: g } = this._curve || {};
    const d = this._draft;
    if (!c || !d) return '';
    const e = floorExcess(d.slope, d.base, c.max_flow, g);
    if (!e) return '';
    if (e < floorExcess(c.slope, c.base, c.max_flow, g)) return 'Toujours hors des bornes du plancher, mais moins qu\'aujourd\'hui.';
    const f = flowAt(d.slope, d.base, g.base_temp, c.max_flow);
    return f > g.max_floor_flow
      ? `Départ de ${fNum(f, 1)} °C à ${fNum(g.base_temp, 0)} °C : au-delà des ${fNum(g.max_floor_flow, 0)} °C admis par le plancher.`
      : `Hors des bornes du plancher (pente ${fNum(g.slope[0])} à ${fNum(g.slope[1])}, pied ${fNum(g.base[0], 0)} à ${fNum(g.base[1], 0)} °C).`;
  }

  _draftBlocked() {
    const { curve: c, guard: g } = this._curve || {};
    const d = this._draft;
    if (!c || !d || (d.slope === c.slope && d.base === c.base)) return true;
    const e = floorExcess(d.slope, d.base, c.max_flow, g);
    return e > 0 && e >= floorExcess(c.slope, c.base, c.max_flow, g);
  }

  _openConfirm() {
    this._confirm = { ...this._draft };
    this.updateComplete.then(() => this.renderRoot.querySelector('dialog.confirm')?.showModal());
  }

  _closeConfirm() {
    this.renderRoot.querySelector('dialog.confirm')?.close();
    this._confirm = null;
  }

  async _applyCurve() {
    const d = this._confirm;
    this._closeConfirm();
    const res = await this._command({ type: `${T}/set_heating_curve`, slope: d.slope, base: d.base });
    if (!res) return;
    this._draft = null;
    // Affichage immédiat, sans attendre la relève du cloud.
    this._curve = { ...this._curve, curve: { ...this._curve.curve, slope: d.slope, base: d.base } };
    setTimeout(() => this._loadCurve(), 8000);
  }

  // --- Éditeur d'un jour -----------------------------------------------------

  _openEditor(kind, day) {
    const z = this._zone, d = this._dhw;
    const program = this._prog ?? z?.program ?? 1;
    const week = kind === 'dhw' ? d?.week : z?.week?.[String(program)];
    if (!week) return;
    const rows = week.filter((sp) => sp.day === day).sort((a, b) => spMin(a) - spMin(b)).map((sp) => ({ ...sp }));
    this._editor = { kind, program, day, week, rows, copy: new Set() };
    this.updateComplete.then(() => this.renderRoot.querySelector('dialog.editor')?.showModal());
  }

  _closeEditor() {
    this.renderRoot.querySelector('dialog.editor')?.close();
    this._editor = null;
  }

  // Semaine modifiée : le jour édité, et chaque jour coché, reçoivent les créneaux saisis.
  _editedWeek(ed = this._editor) {
    const days = [ed.day, ...ed.copy];
    const keep = ed.week.filter((sp) => !days.includes(sp.day));
    const rows = ed.rows.filter((r) => r.time).map((r) => (ed.kind === 'dhw'
      ? { time: r.time, comfort: !!r.comfort }
      : { time: r.time, activity: r.activity, setpoint: r.setpoint ?? null }));
    return [...keep, ...days.flatMap((day) => rows.map((r) => ({ day, ...r })))];
  }

  _editorProblem() {
    const ed = this._editor;
    const times = ed.rows.map((r) => r.time);
    if (times.some((t) => !t)) return 'Heure manquante.';
    const dup = times.find((t, i) => times.indexOf(t) !== i);
    if (dup) return `Deux créneaux à ${dup}.`;
    if (ed.rows.length > MAX_PER_DAY) return `${MAX_PER_DAY} créneaux au plus par jour.`;
    if (!this._editedWeek().length) return 'Le programme doit garder au moins un créneau.';
    return '';
  }

  _editRow(i, patch) {
    const rows = this._editor.rows.map((r, j) => (j === i ? { ...r, ...patch } : r));
    this._editor = { ...this._editor, rows };
  }

  _addRow() {
    const ed = this._editor;
    const last = ed.rows.length ? Math.max(...ed.rows.map((r) => (r.time ? spMin(r) : 0))) : 6 * 60;
    const time = hm(Math.min(23 * 60 + 50, ed.rows.length ? last + 60 : last));
    const prev = ed.rows[ed.rows.length - 1];
    const row = ed.kind === 'dhw' ? { time, comfort: !(prev?.comfort ?? false) }
      : { time, activity: this._zone?.activities?.[0]?.n ?? 1, setpoint: null };
    this._editor = { ...ed, rows: [...ed.rows, row] };
  }

  _toggleCopy(day, on) {
    const copy = new Set(this._editor.copy);
    if (on) copy.add(day); else copy.delete(day);
    this._editor = { ...this._editor, copy };
  }

  async _saveEditor() {
    if (this._editorProblem()) return;
    const ed = this._editor;
    const week = this._editedWeek();
    const msg = ed.kind === 'dhw' ? { type: `${T}/set_dhw_week`, switch_points: week }
      : { type: `${T}/set_week`, program: ed.program, switch_points: week };
    const res = await this._command(msg);
    if (!res) return;
    // Affichage immédiat, sans attendre la relève du cloud.
    if (ed.kind === 'dhw') this._dhw.week = week; else this._zone.week[String(ed.program)] = week;
    this._closeEditor();
  }

  // --- Rendu -----------------------------------------------------------------

  render() {
    return html`
      <div class="toolbar">
        <ha-menu-button .hass=${this.hass} .narrow=${this.narrow}></ha-menu-button>
        <div class="title">Chaudière</div>
        ${this._entries?.length > 1 ? html`
          <select class="entry" aria-label="Chaudière" @change=${(e) => { this._entryId = e.target.value; this._load(); }}>
            ${this._entries.map((e) => html`<option value=${e.entry_id} ?selected=${e.entry_id === this._entryId}>${e.title}</option>`)}
          </select>` : nothing}
      </div>
      <nav class="tabs" role="tablist">
        ${[['programs', 'Programmes'], ['curve', 'Courbe'], ['conso', 'Consommation'], ['journal', 'Journal']].map(([k, l]) => html`
          <button role="tab" aria-selected=${this._tab === k} class=${this._tab === k ? 'on' : ''}
            @click=${() => { this._tab = k; this._loadTab(k); }}>${l}</button>`)}
      </nav>
      <main>
        ${this._error ? html`<div class="card error">${this._error}</div>` : nothing}
        ${this._entries == null ? html`<div class="card muted">Chargement…</div>`
          : !this._entries.length ? html`<div class="card muted">Aucune chaudière configurée.</div>`
          : this._tab === 'programs' ? this._renderPrograms()
          : this._tab === 'curve' ? this._renderCurve()
          : this._tab === 'conso' ? this._renderConso() : this._renderJournal()}
      </main>
      ${this._editor ? this._renderEditor() : nothing}
      ${this._confirm ? this._renderConfirm() : nothing}
      ${this._toast ? html`
        <div class="toast ${this._toast.error ? 'err' : ''}" role="status">
          <span>${this._toast.text}</span>
          ${this._toast.commandId ? html`<button @click=${() => this._undo(this._toast.commandId)}>Annuler</button>` : nothing}
        </div>` : nothing}
    `;
  }

  _renderPrograms() {
    const z = this._zone, d = this._dhw;
    if (!z) return html`<div class="card muted">Aucune zone de chauffage.</div>`;
    const n = this._prog ?? z.program ?? 1;
    const active = n === z.program;
    const week = z.week?.[String(n)] || [];
    const A = this._acts;
    const used = new Set(week.map((sp) => sp.activity));
    return html`
      <section class="card">
        <div class="head">
          <h2>Programmes de chauffage</h2>
          <div class="seg-ctl" role="group" aria-label="Programme affiché">
            ${(z.programs || [1, 2, 3]).map((p) => html`
              <button aria-pressed=${p === n} class=${p === z.program ? 'active-prog' : ''} @click=${() => { this._prog = p; }}>${p}</button>`)}
          </div>
        </div>
        <p class="sub">${active
          ? html`<span class="dot good"></span><span><b>Programme ${n}</b>, suivi par le thermostat${z.mode === 'schedule' || z.mode === 'override' ? '' : ` quand il est au programme (actuellement : ${MODE[z.mode] ?? z.mode})`}</span>`
          : html`<span><b>Programme ${n}</b>, non utilisé (le programme actif se change dans l'entité « Programme »)</span>`}</p>
        ${week.length ? html`
          <div class="week" role="list">
            ${WEEK_ORDER.map((day) => this._weekRow(day, daySegments(week, day), A, active))}
          </div>
          <div class="scale" aria-hidden="true"><span>0 h</span><span>6 h</span><span>12 h</span><span>18 h</span><span>24 h</span></div>
          <p class="help">${this._admin ? 'Touchez un jour pour modifier ses créneaux.' : 'Lecture seule : les modifications sont réservées aux administrateurs.'}</p>`
          : html`<p class="muted">Programme ${n} pas encore lu sur le cloud.</p>`}
      </section>
      ${(z.activities || []).length ? html`
        <section class="card">
          <h2>Consignes des activités</h2>
          <p class="help">Lues sur le thermostat${this._admin ? " ; une modification ici s'applique au thermostat" : ''}.</p>
          <ul class="acts">
            ${z.activities.map((a) => {
              const v = this._actWanted[a.n] ?? a.setpoint;
              return html`<li class=${used.has(a.n) ? '' : 'unused'}>
                <span class="sw" style="--i:${intensity(v)}%" aria-hidden="true"></span>
                <span class="lbl">${a.name}<span class="help">${this._actWanted[a.n] != null ? 'envoi…' : used.has(a.n) ? '' : `pas dans le programme ${n}`}</span></span>
                ${this._admin ? html`<span class="stepper">
                  <button aria-label="Baisser la consigne ${a.name}" @click=${() => this._actBump(a.n, -1)}>−</button>
                  <span class="v" aria-live="polite">${fDeg(v)}</span>
                  <button aria-label="Monter la consigne ${a.name}" @click=${() => this._actBump(a.n, 1)}>+</button>
                </span>` : html`<span class="stepper"><span class="v">${fDeg(v)}</span></span>`}
              </li>`;
            })}
          </ul>
        </section>` : nothing}
      ${d?.week?.length ? html`
        <section class="card">
          <h2>Eau chaude</h2>
          <p class="help">${d.mode === 'schedule' || d.mode === 'boost'
            ? `Confort (${fDeg(d.comfort_setpoint)}) aux heures colorées, température réduite (${fDeg(d.reduced_setpoint)}) ailleurs.`
            : `Suivi en mode programme seulement (actuellement : ${DHW_MODE[d.mode] ?? d.mode ?? '—'}). Confort aux heures colorées, réduit ailleurs.`}</p>
          <div class="week" role="list">
            ${WEEK_ORDER.map((day) => this._dhwRow(day, daySegments(d.week, day)))}
          </div>
          <div class="scale" aria-hidden="true"><span>0 h</span><span>6 h</span><span>12 h</span><span>18 h</span><span>24 h</span></div>
        </section>` : nothing}
    `;
  }

  _weekRow(day, segs, A, active) {
    const today = this._today();
    const name = (sp) => A[sp.activity]?.name ?? `Activité ${sp.activity}`;
    const temp = (sp) => sp.setpoint ?? A[sp.activity]?.setpoint ?? null;
    const said = segs.map((g) => `${hm(g.start)} ${name(g.sp)} ${temp(g.sp) == null ? '' : fDeg(temp(g.sp))}`.trim()).join(', ');
    const wide = this.narrow ? 30 : 13;
    return html`<button class="wrow ${day === today ? 'today' : ''}" role="listitem"
        aria-label="${DAYS[day]} : ${said || 'aucun créneau'}${this._admin ? '. Modifier' : ''}" @click=${() => this._admin && this._openEditor('zone', day)}>
      <span class="wday">${DAYS_S[day]}</span>
      <span class="band">
        ${segs.map((g) => {
          const w = ((g.end - g.start) / 1440) * 100, t = temp(g.sp);
          return html`<span class="seg ${t == null ? 'unk' : ''}" style="left:${(g.start / 1440) * 100}%;width:${w}%;--i:${intensity(t)}%"
            title="${DAYS[day]} ${hm(g.start)}–${hm(g.end)} · ${name(g.sp)} · ${fDeg(t)}">${w >= wide ? `${name(g.sp)} ${fShort(t)}°` : w > 8 ? fShort(t) : ''}</span>`;
        })}
        ${day === today && active ? html`<span class="now" style="left:${(this._nowMin() / 1440) * 100}%"></span>` : nothing}
      </span>
    </button>`;
  }

  _dhwRow(day, segs) {
    const today = this._today();
    const said = segs.filter((g) => g.sp.comfort).map((g) => `confort ${hm(g.start)}–${hm(g.end)}`).join(', ');
    return html`<button class="wrow ${day === today ? 'today' : ''}" role="listitem"
        aria-label="${DAYS[day]} : ${said || 'réduit toute la journée'}${this._admin ? '. Modifier' : ''}" @click=${() => this._admin && this._openEditor('dhw', day)}>
      <span class="wday">${DAYS_S[day]}</span>
      <span class="band">${segs.map((g) => {
        const w = ((g.end - g.start) / 1440) * 100;
        return html`<span class="seg ${g.sp.comfort ? 'comfort' : 'reduced'}" style="left:${(g.start / 1440) * 100}%;width:${w}%"
          title="${DAYS[day]} ${hm(g.start)}–${hm(g.end)} · ${g.sp.comfort ? 'confort' : 'réduit'}">${g.sp.comfort && w > 14 ? 'Confort' : ''}</span>`;
      })}</span>
    </button>`;
  }

  _renderEditor() {
    const ed = this._editor, dhw = ed.kind === 'dhw', A = this._acts;
    const problem = this._editorProblem();
    const segs = daySegments(this._editedWeek(), ed.day);
    const first = segs[0]?.sp;
    const firstName = first ? (dhw ? (first.comfort ? 'confort' : 'réduit') : A[first.activity]?.name ?? `Activité ${first.activity}`) : '';
    const dup = new Set(ed.rows.map((r) => r.time).filter((t, i, all) => t && all.indexOf(t) !== i));
    return html`<dialog class="editor" @close=${() => { this._editor = null; }} @cancel=${() => { this._editor = null; }}>
      <h3>${dhw ? `${DAYS[ed.day]}, eau chaude` : `${DAYS[ed.day]}, programme ${ed.program}`}</h3>
      <p class="help">${dhw ? 'Heures de passage en confort ou en température réduite.'
        : ed.program === this._zone?.program ? "Programme actif : la modification s'applique tout de suite au chauffage."
          : "Programme inactif : la modification s'appliquera quand il sera activé."}</p>
      <div class="band preview" aria-hidden="true">${segs.map((g) => {
        const w = ((g.end - g.start) / 1440) * 100;
        const t = dhw ? null : g.sp.setpoint ?? A[g.sp.activity]?.setpoint ?? null;
        return html`<span class="seg ${dhw ? (g.sp.comfort ? 'comfort' : 'reduced') : ''}" style="left:${(g.start / 1440) * 100}%;width:${w}%;--i:${intensity(t)}%"></span>`;
      })}</div>
      <p class="help">${!ed.rows.length ? (first ? `Aucun changement ce jour : ${firstName} toute la journée, suite de la veille.` : '')
        : segs[0].start === 0 && !ed.rows.some((r) => r.time === '00:00') ? `De 0 h au premier créneau : ${firstName}, suite de la veille.` : ''}</p>
      <ul class="rows">
        ${ed.rows.map((r, i) => html`<li class=${dup.has(r.time) ? 'bad' : ''}>
          ${this._timePicker(r.time, i)}
          ${dhw ? html`<select aria-label="Eau chaude à partir de ${r.time}" @change=${(e) => this._editRow(i, { comfort: e.target.value === '1' })}>
              <option value="1" ?selected=${r.comfort}>Confort</option><option value="0" ?selected=${!r.comfort}>Réduit</option></select>`
            : html`<select aria-label="Activité à partir de ${r.time}" @change=${(e) => this._editRow(i, { activity: Number(e.target.value), setpoint: null })}>
              ${(this._zone?.activities || []).map((a) => html`<option value=${a.n} ?selected=${a.n === r.activity}>${a.name}${a.setpoint != null ? ` ${fShort(a.setpoint)}°` : ''}</option>`)}</select>`}
          <button class="del" aria-label="Supprimer le créneau de ${r.time || 'heure vide'}"
            @click=${() => { this._editor = { ...ed, rows: ed.rows.filter((_, j) => j !== i) }; }}>×</button>
        </li>`)}
      </ul>
      <button class="add" ?disabled=${ed.rows.length >= MAX_PER_DAY} @click=${() => this._addRow()}>+ Ajouter un créneau</button>
      <fieldset class="copy"><legend>Appliquer aussi à</legend>
        ${WEEK_ORDER.map((day) => day === ed.day
          ? html`<label class="self"><input type="checkbox" checked disabled>${DAYS_S[day]}</label>`
          : html`<label><input type="checkbox" .checked=${ed.copy.has(day)} @change=${(e) => this._toggleCopy(day, e.target.checked)}>${DAYS_S[day]}</label>`)}
      </fieldset>
      <p class="err" role="alert">${problem}</p>
      <div class="actions">
        <button class="ghost" @click=${() => this._closeEditor()}>Annuler</button>
        <button class="primary" ?disabled=${!!problem} @click=${() => this._saveEditor()}>Enregistrer</button>
      </div>
    </dialog>`;
  }

  // Heure sur 24 h, minutes par pas de 10 (le champ natif suit la langue du système : 12 h en anglais).
  _timePicker(time, i) {
    const [h, m] = (time || '06:00').split(':').map(Number);
    const mins = [0, 10, 20, 30, 40, 50];
    if (!mins.includes(m)) mins.push(m);
    const set = (hh, mm) => this._editRow(i, { time: hm(hh * 60 + mm) });
    return html`<span class="time">
      <select aria-label="Heure du créneau ${i + 1}" @change=${(e) => set(Number(e.target.value), m)}>
        ${Array.from({ length: 24 }, (_, x) => html`<option value=${x} ?selected=${x === h}>${String(x).padStart(2, '0')} h</option>`)}
      </select>
      <select aria-label="Minutes du créneau ${i + 1}" @change=${(e) => set(h, Number(e.target.value))}>
        ${mins.sort((a, b) => a - b).map((x) => html`<option value=${x} ?selected=${x === m}>${String(x).padStart(2, '0')}</option>`)}
      </select>
    </span>`;
  }

  _renderCurve() {
    const cv = this._curve;
    if (!cv) return html`<div class="card muted">Chargement…</div>`;
    const { curve: c, guard: g, now, diagnosis: dg } = cv;
    if (!c) return html`<div class="card muted">Courbe de chauffe pas encore lue sur le cloud.</div>`;
    const d = this._draft;
    const shown = d ?? (dg && !['keep', 'insufficient_data'].includes(dg.action) ? { slope: dg.slope, base: dg.base } : null);
    const problem = this._draftProblem();
    const baseFlow = (s, b) => flowAt(s, b, g.base_temp, c.max_flow);
    return html`
      <section class="card">
        <h2>Courbe de chauffe</h2>
        <p class="sub"><span>Pente <b>${fNum(c.slope)}</b>, pied <b>${fNum(c.base, 1)} °C</b>${now?.flow != null
          ? html` · départ estimé maintenant <b>${fDeg(now.flow)}</b> pour ${fDeg(now.outdoor)} dehors` : ''}</span></p>
        ${g.violation ? html`<p class="warn" role="alert">Courbe dangereuse pour le plancher : ${g.violation}.</p>` : nothing}
        ${this._curveChart(c, g, now, shown, d ? 'Réglage en cours' : 'Proposition du diagnostic')}
        <p class="help">Approximation : la notice ne donne qu'un graphique, et l'influence de la sonde d'ambiance n'est pas connue.</p>
      </section>
      ${this._admin ? html`<section class="card">
        <h2>Réglage</h2>
        <ul class="acts">
          ${[['slope', 'Pente', (v) => fNum(v)], ['base', 'Pied de courbe', (v) => `${fNum(v, 1)} °C`]].map(([k, l, f]) => html`<li>
            <span class="lbl">${l}<span class="help">${d && d[k] !== c[k] ? `actuel : ${f(c[k])}` : ''}</span></span>
            <span class="stepper">
              <button aria-label="Baisser : ${l}" @click=${() => this._curveBump(k, -1)}>−</button>
              <span class="v" aria-live="polite">${f((d ?? c)[k])}</span>
              <button aria-label="Monter : ${l}" @click=${() => this._curveBump(k, 1)}>+</button>
            </span></li>`)}
        </ul>
        <p class="help">Départ à ${fDeg(g.base_temp)} dehors : ${fDeg(baseFlow((d ?? c).slope, (d ?? c).base))}${g.active
          ? ` (plancher : ${fDeg(g.max_floor_flow)} au plus)` : ''}.</p>
        <p class="err" role="alert">${problem}</p>
        <div class="actions">
          <button class="ghost" ?disabled=${!d} @click=${() => { this._draft = null; }}>Rétablir</button>
          <button class="primary" ?disabled=${this._draftBlocked()} @click=${() => this._openConfirm()}>Appliquer…</button>
        </div>
      </section>` : nothing}
      ${this._renderSimulation(c, shown, d ? 'réglage en cours' : 'proposée')}
      ${this._renderDiagnosis(dg)}
    `;
  }

  _renderSimulation(c, prop, propName) {
    const sim = this._sim;
    const tz = this.hass?.config?.time_zone || 'Europe/Paris';
    // Départ recalculé ici : la courbe proposée suit le réglage en cours sans nouvel appel.
    const rows = (sim?.rows || []).map((r) => ({ ...r, ts: new Date(r.ts), current: flowAt(c.slope, c.base, r.outdoor, c.max_flow),
      proposed: prop ? flowAt(prop.slope, prop.base, r.outdoor, c.max_flow) : null }));
    const dayKey = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', timeZone: tz });
    const days = [];
    for (const r of rows) {
      const k = dayKey.format(r.ts);
      let d = days[days.length - 1];
      if (!d || d.k !== k) { d = { k, out: [], cur: [], prop: [] }; days.push(d); }
      d.out.push(r.outdoor); d.cur.push(r.current); if (r.proposed != null) d.prop.push(r.proposed);
    }
    const range = (a, n = 0) => (a.length ? `${fNum(Math.min(...a), n)} à ${fNum(Math.max(...a), n)} °C` : '—');
    return html`<section class="card">
      <div class="head"><h2>Simulation</h2>
        <div class="seg-ctl" role="group" aria-label="Températures extérieures simulées">
          ${[['forecast', 'Prévisions'], ['history', '7 derniers jours']].map(([k, l]) => html`
            <button aria-pressed=${this._simSource === k} @click=${() => { this._simSource = k; this._sim = null; this._loadSim(); }}>${l}</button>`)}
        </div></div>
      <p class="help">Départ demandé par la courbe pour les températures extérieures ${this._simSource === 'forecast'
        ? 'prévues' : 'relevées'}. Ne prédit pas la température des pièces.</p>
      ${!sim ? html`<p class="muted">Chargement…</p>`
        : sim.error ? html`<p class="muted">${sim.error}</p>`
        : html`${this._simChart(rows, sim, propName)}
          <div class="tbl"><table>
            <thead><tr><th>Jour</th><th>Extérieur</th><th>Départ actuel</th>${prop ? html`<th>Départ ${propName === 'proposée' ? 'proposé' : 'réglé'}</th>` : nothing}</tr></thead>
            <tbody>${days.map((d) => html`<tr><td>${d.k}</td><td>${range(d.out)}</td><td>${range(d.cur)}</td>${prop ? html`<td>${range(d.prop)}</td>` : nothing}</tr>`)}</tbody>
          </table></div>`}
    </section>`;
  }

  _simChart(rows, sim, propName) {
    if (rows.length < 2) return nothing;
    const W = this.narrow ? 360 : 640, H = this.narrow ? 200 : 220;
    const L = 40, R = 10, TOP = 10, B = 26;
    const vals = rows.flatMap((r) => [r.current, r.proposed]).filter((v) => v != null);
    const yMin = Math.floor(Math.min(...vals) / 5) * 5 - 5, yMax = Math.max(Math.ceil(Math.max(...vals) / 5) * 5 + 5, sim.guard_active ? sim.max_floor_flow + 5 : 0);
    const t0 = rows[0].ts.getTime(), t1 = rows[rows.length - 1].ts.getTime();
    const x = (ts) => L + ((ts.getTime() - t0) / (t1 - t0)) * (W - L - R);
    const y = (v) => TOP + ((yMax - v) / (yMax - yMin)) * (H - TOP - B);
    const pts = (k) => rows.filter((r) => r[k] != null).map((r) => `${x(r.ts).toFixed(1)},${y(r[k]).toFixed(1)}`).join(' ');
    const tz = this.hass?.config?.time_zone || 'Europe/Paris';
    // Heure locale en chiffres seuls (en français, « 00 h »).
    const hour = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', hourCycle: 'h23', timeZone: tz });
    const wd = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', timeZone: tz });
    const midnights = rows.filter((r, i) => i > 0 && Number(hour.format(r.ts)) === 0);
    const ys = [];
    for (let v = Math.ceil(yMin / 10) * 10; v <= yMax; v += 10) ys.push(v);
    const peak = Math.max(...rows.map((r) => r.current));
    return html`<figure class="chart">
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Départ actuel jusqu'à ${fDeg(peak)} sur la période.">
        ${ys.map((v) => svg`<line x1=${L} x2=${W - R} y1=${y(v)} y2=${y(v)} class="grid"></line>
          <text x=${L - 6} y=${y(v) + 4} text-anchor="end" class="tick">${v}</text>`)}
        ${midnights.map((r) => svg`<line x1=${x(r.ts)} x2=${x(r.ts)} y1=${TOP} y2=${H - B} class="base"></line>
          <text x=${x(r.ts) + 4} y=${H - B + 16} class="tick">${wd.format(r.ts)}</text>`)}
        ${sim.guard_active ? svg`<line x1=${L} x2=${W - R} y1=${y(sim.max_floor_flow)} y2=${y(sim.max_floor_flow)} class="limit"></line>` : nothing}
        <polyline points=${pts('current')} class="cur"></polyline>
        ${rows.some((r) => r.proposed != null) ? svg`<polyline points=${pts('proposed')} class="prop"></polyline>` : nothing}
      </svg>
      <figcaption class="legend">
        <span><i class="k cur"></i>Départ, courbe actuelle (°C)</span>
        ${rows.some((r) => r.proposed != null) ? html`<span><i class="k prop"></i>Départ, courbe ${propName}</span>` : nothing}
        ${sim.guard_active ? html`<span><i class="k lim"></i>Maxi du plancher (${fDeg(sim.max_floor_flow)})</span>` : nothing}
      </figcaption>
    </figure>`;
  }

  _curveChart(c, g, now, prop, propLabel) {
    const W = this.narrow ? 360 : 640, H = this.narrow ? 260 : 300;
    const L = 40, R = 10, TOP = 10, B = 34;
    const flows = [flowAt(c.slope, c.base, -20, c.max_flow), prop ? flowAt(prop.slope, prop.base, -20, c.max_flow) : 0, g.max_floor_flow];
    const yMax = Math.max(60, Math.ceil(Math.max(...flows) / 10) * 10), yMin = 10;
    const x = (t) => L + ((20 - t) / 40) * (W - L - R);
    const y = (v) => TOP + ((yMax - v) / (yMax - yMin)) * (H - TOP - B);
    const line = (s, b) => Array.from({ length: 41 }, (_, i) => 20 - i).map((t) => `${x(t).toFixed(1)},${y(flowAt(s, b, t, c.max_flow)).toFixed(1)}`).join(' ');
    const xs = [20, 10, 0, -10, -20], ys = [];
    for (let v = yMin; v <= yMax; v += 10) ys.push(v);
    const said = `Courbe actuelle : départ ${fDeg(flowAt(c.slope, c.base, 10, c.max_flow))} à 10 °C dehors, ${fDeg(flowAt(c.slope, c.base, 0, c.max_flow))} à 0 °C, ${fDeg(flowAt(c.slope, c.base, -10, c.max_flow))} à −10 °C.`;
    return html`<figure class="chart">
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label=${said}>
        <defs><pattern id="dd-hatch" patternUnits="userSpaceOnUse" width="8" height="8" patternTransform="rotate(45)">
          <rect width="3" height="8" class="hatch"></rect></pattern></defs>
        ${g.active ? svg`<rect x=${L} y=${TOP} width=${W - L - R} height=${y(g.max_floor_flow) - TOP} fill="url(#dd-hatch)"></rect>
          <line x1=${L} x2=${W - R} y1=${y(g.max_floor_flow)} y2=${y(g.max_floor_flow)} class="limit"></line>
          <text x=${W - R - 4} y=${TOP + 14} text-anchor="end" class="lbl-err">Interdit au plancher (au-delà de ${fNum(g.max_floor_flow, 0)} °C)</text>` : nothing}
        ${ys.map((v) => svg`<line x1=${L} x2=${W - R} y1=${y(v)} y2=${y(v)} class="grid"></line>
          <text x=${L - 6} y=${y(v) + 4} text-anchor="end" class="tick">${v}</text>`)}
        ${xs.map((t) => svg`<text x=${x(t)} y=${H - B + 16} text-anchor="middle" class="tick">${fNum(t, 0)}</text>`)}
        <text x=${(L + W - R) / 2} y=${H - 4} text-anchor="middle" class="tick">Température extérieure (°C)</text>
        <line x1=${x(g.base_temp)} x2=${x(g.base_temp)} y1=${TOP} y2=${H - B} class="base"></line>
        <polyline points=${line(c.slope, c.base)} class="cur"></polyline>
        ${prop ? svg`<polyline points=${line(prop.slope, prop.base)} class="prop"></polyline>` : nothing}
        ${now?.flow != null ? svg`<circle cx=${x(Math.max(-20, Math.min(20, now.outdoor)))} cy=${y(now.flow)} r="5" class="pt"></circle>` : nothing}
      </svg>
      <figcaption class="legend">
        <span><i class="k cur"></i>Actuelle (pente ${fNum(c.slope)}, pied ${fNum(c.base, 1)} °C)</span>
        ${prop ? html`<span><i class="k prop"></i>${propLabel} (pente ${fNum(prop.slope)}, pied ${fNum(prop.base, 1)} °C)</span>` : nothing}
        ${now?.flow != null ? html`<span><i class="k pt"></i>Maintenant</span>` : nothing}
        <span><i class="k base"></i>Température de base (${fDeg(g.base_temp)})</span>
      </figcaption>
    </figure>`;
  }

  _renderDiagnosis(dg) {
    if (!dg) return html`<section class="card"><h2>Diagnostic</h2><p class="muted">Pas encore calculé.</p></section>`;
    const proposal = !['keep', 'insufficient_data'].includes(dg.action);
    const usable = proposal && (dg.confidence !== 'low' || dg.action === 'switch_to_floor');
    const B = dg.bands || {};
    return html`<section class="card">
      <div class="head"><h2>Diagnostic</h2>${dg.action === 'insufficient_data' ? nothing
        : html`<span class="badge ${dg.confidence}">${CONF[dg.confidence]}</span>`}</div>
      <p class="sub"><span><b>${RECO[dg.action] ?? dg.action}</b>${proposal ? ` : pente ${fNum(dg.slope)}, pied ${fNum(dg.base, 1)} °C` : ''}</span></p>
      <ul class="reasons">${dg.reasons.map((r) => html`<li>${r}</li>`)}</ul>
      ${dg.days ? html`<div class="tbl"><table>
        <thead><tr><th>Bande</th><th>Heures occupées</th><th>Écart à la consigne</th><th>Part de chauffe</th><th>Remontée</th></tr></thead>
        <tbody>${['mild', 'fresh', 'cold'].map((k) => { const b = B[k] || {}; return html`<tr class=${b.counted ? '' : 'thin'}>
          <td>${b.label}</td><td>${b.occupied ?? 0}${b.counted ? '' : ' (trop peu)'}</td>
          <td>${b.gap == null ? '—' : `${b.gap > 0 ? '+' : ''}${fNum(b.gap, 1)} °C`}</td>
          <td>${b.ratio == null ? '—' : `${fNum(b.ratio, 0)} %`}</td>
          <td>${b.recovery_h == null ? '—' : `${fNum(b.recovery_h, 1)} h`}</td></tr>`; })}</tbody>
      </table></div>
      <p class="help">${fNum(dg.days, 1)} jours de mesures sur les 14 derniers${dg.short_cycles_per_day != null
        ? ` · ${fNum(dg.short_cycles_per_day, 1)} cycles courts par jour` : ''}. Heures occupées : en consigne de confort.</p>` : nothing}
      ${proposal && this._admin ? html`<div class="actions">
        ${usable ? html`<button class="primary" @click=${() => { this._draft = { slope: dg.slope, base: dg.base }; this._openConfirm(); }}>Appliquer la proposition…</button>`
          : html`<p class="help">Confiance basse : attendez plus de mesures avant d'appliquer.</p>`}
      </div>` : nothing}
    </section>`;
  }

  _renderConfirm() {
    const d = this._confirm, c = this._curve?.curve, g = this._curve?.guard;
    return html`<dialog class="confirm" @close=${() => { this._confirm = null; }} @cancel=${() => { this._confirm = null; }}>
      <h3>Changer la courbe de chauffe ?</h3>
      <p>Pente ${fNum(c.slope)} → <b>${fNum(d.slope)}</b>, pied ${fNum(c.base, 1)} → <b>${fNum(d.base, 1)} °C</b>.</p>
      <p class="help">Départ à ${fDeg(g.base_temp)} dehors : ${fDeg(flowAt(d.slope, d.base, g.base_temp, c.max_flow))}.
        Le changement s'applique tout de suite à la chaudière ; il peut être annulé pendant 2 minutes.</p>
      <div class="actions">
        <button class="ghost" @click=${() => this._closeConfirm()}>Annuler</button>
        <button class="primary" @click=${() => this._applyCurve()}>Appliquer</button>
      </div>
    </dialog>`;
  }

  _renderConso() {
    const cs = this._conso;
    if (!cs) return html`<div class="card muted">Chargement…</div>`;
    const s = cs.summary, y = s.yesterday;
    const kwh = (v) => (v == null ? '—' : `${fNum(v, 0)} kWh`);
    const eur = (v) => (v == null ? '' : ` · ${fNum(v, 2)} €`);
    const pct = (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${fNum(v, 0)} %`);
    const cmp = (c, label) => html`<div class="tile">
      <span class="tl">${label}</span>
      <span class="tv">${kwh(c.total)}<span class="tu">${eur(c.cost)}</span></span>
      ${c.last_year == null ? html`<span class="help">Pas de données l'an dernier.</span>` : html`
        <span class="help">${pct(c.delta_pct)} sur l'an dernier (${c.days_compared < c.days ? `${kwh(c.compared_total)} contre ` : ''}${kwh(c.last_year)})</span>
        <span class="help">${c.delta_corrected_pct == null ? 'Correction climatique impossible (DJU manquants).'
          : html`<b>${pct(c.delta_corrected_pct)}</b> à climat égal (${fNum(c.compared_dju, 0)} DJU contre ${fNum(c.last_year_dju, 0)})`}</span>
        ${c.days_compared < c.days ? html`<span class="help">Comparé sur les ${c.days_compared} jours connus des deux côtés, sur ${c.days}.</span>` : nothing}`}
    </div>`;
    const src = cs.sources || {};
    return html`
      <section class="card">
        <h2>Consommation de gaz</h2>
        <div class="tiles">
          <div class="tile"><span class="tl">Hier</span>
            <span class="tv">${kwh(y?.total)}<span class="tu">${eur(y?.cost)}</span></span>
            <span class="help">Chauffage ${kwh(y?.heating)}, eau chaude ${kwh(y?.dhw)}${y?.dju != null ? ` · ${fNum(y.dju, 1)} DJU` : ''}</span></div>
          ${cmp(s.month, 'Mois en cours, jusqu\'à hier')}
          ${cmp(s.season, `Saison ${s.season.label.replace('-', '–')}`)}
          <div class="tile"><span class="tl">Chauffage par degré-jour, 30 jours</span>
            <span class="tv">${s.kwh_per_dju_30d == null ? '—' : `${fNum(s.kwh_per_dju_30d, 2)} kWh/DJU`}</span>
            <span class="help">${kwh(s.heating_30d)} pour ${fNum(s.dju_30d, 0)} DJU. Plus bas = moins de gaz à climat égal.</span></div>
        </div>
        <p class="help">DJU base ${cs.dju_base} °C (méthode météo). Températures : ${[src.ha ? `capteur de la chaudière (${nDays(src.ha)})` : '',
          src['open-meteo'] ? `Open-Meteo (${nDays(src['open-meteo'])})` : ''].filter(Boolean).join(', ') || 'aucune pour l\'instant'}.</p>
      </section>
      <section class="card">
        <h2>Par mois</h2>
        <div class="tbl"><table>
          <thead><tr><th>Mois</th><th>Chauffage</th><th>Eau chaude</th><th>Total</th>${cs.price ? html`<th>Coût</th>` : nothing}<th>DJU</th><th>kWh/DJU</th></tr></thead>
          <tbody>${[...cs.months].reverse().map((m) => html`<tr>
            <td>${new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${m.month}-15T12:00:00Z`))}</td>
            <td>${kwh(m.heating)}</td><td>${kwh(m.dhw)}</td><td>${kwh(m.total)}</td>${cs.price ? html`<td>${m.cost == null ? '—' : `${fNum(m.cost, 0)} €`}</td>` : nothing}
            <td>${m.dju == null ? '—' : fNum(m.dju, 0)}</td><td>${m.dju && m.heating != null ? fNum(m.heating / m.dju, 2) : '—'}</td></tr>`)}</tbody>
        </table></div>
      </section>
      <section class="card">
        <h2>Export</h2>
        <p class="help">Fichiers CSV pour un tableur (séparateur « ; », virgule décimale). L'historique de chaque entité s'exporte aussi depuis l'Historique de Home Assistant.</p>
        <div class="actions start">
          <button class="primary" @click=${() => this._exportCsv('days')}>Jours (CSV)</button>
          <button class="primary" @click=${() => this._exportCsv('hours')}>Heures (CSV)</button>
        </div>
      </section>`;
  }

  _renderJournal() {
    const j = this._journal;
    return html`<section class="card">
      <div class="head">
        <h2>Journal</h2>
        <div class="seg-ctl" role="group" aria-label="Contenu du journal">
          ${[['events', 'Événements'], ['commands', 'Commandes']].map(([k, l]) => html`
            <button aria-pressed=${this._jTab === k} @click=${() => { this._jTab = k; }}>${l}</button>`)}
        </div>
      </div>
      ${this._jTab === 'events' ? html`<div class="pills">${KINDS.map(([k, l]) => html`
        <button class="pill" aria-pressed=${this._jKind === k} @click=${() => { this._jKind = k; this._loadJournal(); }}>${l}</button>`)}</div>` : nothing}
      ${j == null ? html`<p class="muted">Chargement…</p>` : html`<ul class="journal">
        ${this._jTab === 'events'
          ? (j.events.length ? j.events.map((e) => this._eventItem(e)) : html`<li class="muted">Aucun événement.</li>`)
          : (j.commands.length ? j.commands.map((c) => this._cmdItem(c)) : html`<li class="muted">Aucune commande envoyée depuis Home Assistant.</li>`)}
      </ul>`}
    </section>`;
  }

  _eventItem(e) {
    const who = e.data?.origin ? `Origine : ${e.data.origin}` : '';
    return html`<li><span class="dot ${e.level}"></span>
      <span class="what">${e.message}${who ? html`<span class="who">${who}</span>` : nothing}</span>
      <span class="when">${this._when(e.ts)}</span></li>`;
  }

  _cmdItem(c) {
    const p = c.params || {};
    let what = ACTION[c.action] || c.action;
    if (c.action === 'consigne') what += ` ${fDeg(p.setpoint)} ${DUR[p.duration] || ''}`;
    else if (c.action === 'mode') what += ` : ${MODE[p.mode] ?? p.mode}`;
    else if (c.action === 'programme') what += ` ${p.program}`;
    else if (c.action === 'cheminee') what += p.active ? ' activé' : ' désactivé';
    else if (c.action === 'ecs_mode') what += ` : ${DHW_MODE[p.mode] ?? p.mode}`;
    else if (c.action === 'ecs_consigne') what += ` ${p.kind === 'comfort' ? 'confort' : 'réduite'} ${fDeg(p.value)}`;
    else if (c.action === 'activite') what += ` ${p.name ?? p.activity} : ${fDeg(p.setpoint)}`;
    else if (c.action === 'courbe') what += ` : pente ${String(p.slope).replace('.', ',')}, pied ${fDeg(p.base)}`;
    else if (c.action === 'creneaux') what += ` du programme ${p.program} : ${daysLabel(p.days)}`;
    else if (c.action === 'ecs_creneaux') what += ` : ${daysLabel(p.days)}`;
    else if (c.action === 'vacances') what += p.start ? ` : départ le ${p.start}, retour le ${p.end}` : ' annulées';
    const who = [c.origin, c.ok === false ? `échec : ${c.error}` : null, c.undone ? 'annulée' : null].filter(Boolean).join(' · ');
    const undoable = this._admin && this._entry?.undo?.command_id === c.id;
    return html`<li><span class="dot ${c.ok === false ? 'critical' : 'good'}"></span>
      <span class="what">${what}<span class="who">${who}</span></span>
      <span class="when">${this._when(c.ts)}${undoable ? html`<button class="link" @click=${() => this._undo(c.id)}>Annuler</button>` : nothing}</span></li>`;
  }

  static styles = css`
    :host { display: block; min-height: 100vh; background: var(--primary-background-color); color: var(--primary-text-color);
      font-family: var(--ha-font-family-body, Roboto, system-ui, sans-serif); --s: var(--primary-color, #03a9f4); --r: 12px; }
    .toolbar { display: flex; align-items: center; gap: 8px; height: 56px; padding: 0 12px; background: var(--app-header-background-color, var(--primary-color));
      color: var(--app-header-text-color, #fff); }
    .title { font-size: 20px; flex: 1; }
    .entry { font: inherit; border-radius: 8px; padding: 4px 8px; }
    .tabs { display: flex; gap: 4px; padding: 8px 12px 0; border-bottom: 1px solid var(--divider-color); background: var(--card-background-color);
      overflow-x: auto; scrollbar-width: none; }
    .tabs button { flex: none; white-space: nowrap; font: inherit; background: none; border: 0; padding: 10px 14px; color: var(--secondary-text-color); border-bottom: 2px solid transparent; cursor: pointer; }
    .tabs button.on { color: var(--s); border-bottom-color: var(--s); font-weight: 500; }
    main { max-width: 960px; margin: 0 auto; padding: 16px; display: grid; gap: 16px; }
    .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, var(--r)); padding: 16px;
      box-shadow: var(--ha-card-box-shadow, none); border: 1px solid var(--divider-color); min-width: 0; }
    .card.error { color: var(--error-color); }
    h2 { margin: 0; font-size: 18px; font-weight: 500; }
    h3 { margin: 0 0 4px; font-size: 18px; font-weight: 500; }
    .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .sub { margin: 8px 0 12px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
    .help { color: var(--secondary-text-color); font-size: 13px; margin: 4px 0 8px; }
    .muted { color: var(--secondary-text-color); }
    .dot { width: 10px; height: 10px; border-radius: 50%; flex: none; background: var(--secondary-text-color); display: inline-block; }
    .dot.good { background: var(--success-color, #43a047); }
    .dot.warning { background: var(--warning-color, #ffa600); }
    .dot.critical { background: var(--error-color, #db4437); }
    .dot.info { background: var(--s); }
    .seg-ctl { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 999px; overflow: hidden; }
    .seg-ctl button { font: inherit; background: none; border: 0; padding: 6px 14px; color: var(--primary-text-color); cursor: pointer; min-width: 44px; min-height: 36px; }
    .seg-ctl button[aria-pressed='true'] { background: color-mix(in srgb, var(--s) 18%, transparent); color: var(--s); font-weight: 500; }
    .seg-ctl button.active-prog::after { content: '•'; margin-left: 4px; color: var(--success-color, #43a047); }
    .week { display: grid; gap: 6px; }
    .wrow { display: grid; grid-template-columns: 44px 1fr; align-items: center; gap: 8px; width: 100%; padding: 0; border: 0; background: none;
      color: inherit; font: inherit; cursor: pointer; text-align: left; border-radius: 8px; }
    .wrow:focus-visible { outline: 2px solid var(--s); outline-offset: 2px; }
    .wrow.today .wday { color: var(--s); font-weight: 600; }
    .wday { font-size: 13px; color: var(--secondary-text-color); }
    .band { position: relative; height: 32px; border-radius: 8px; overflow: hidden; background: color-mix(in srgb, var(--secondary-text-color) 8%, transparent); }
    .seg { position: absolute; top: 0; bottom: 0; display: flex; align-items: center; justify-content: center; font-size: 12px; white-space: nowrap;
      overflow: hidden; border-right: 1px solid var(--card-background-color); background: color-mix(in srgb, var(--s) var(--i, 20%), transparent); }
    .seg.unk { background: repeating-linear-gradient(45deg, transparent 0 4px, color-mix(in srgb, var(--secondary-text-color) 20%, transparent) 4px 6px); }
    .seg.comfort { background: color-mix(in srgb, var(--state-water_heater-active-color, #ff9800) 45%, transparent); }
    .seg.reduced { background: transparent; }
    .now { position: absolute; top: -2px; bottom: -2px; width: 2px; background: var(--error-color, #db4437); }
    .scale { display: flex; justify-content: space-between; margin: 4px 0 0 52px; font-size: 11px; color: var(--secondary-text-color); }
    .acts { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
    .acts li { display: flex; align-items: center; gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--divider-color); }
    .acts li:last-child { border-bottom: 0; }
    .acts li.unused .lbl { color: var(--secondary-text-color); }
    .sw { width: 14px; height: 14px; border-radius: 4px; background: color-mix(in srgb, var(--s) var(--i, 20%), transparent); flex: none; }
    .lbl { flex: 1; display: flex; flex-direction: column; min-width: 0; }
    .lbl .help { margin: 0; font-size: 12px; }
    .stepper { display: inline-flex; align-items: center; gap: 4px; }
    .stepper button { width: 40px; height: 40px; border-radius: 50%; border: 1px solid var(--divider-color); background: none; color: inherit; font-size: 20px; cursor: pointer; }
    .stepper .v { min-width: 64px; text-align: center; font-variant-numeric: tabular-nums; font-weight: 500; }
    dialog { border: 0; border-radius: var(--r); padding: 20px; width: min(480px, calc(100vw - 32px)); box-sizing: border-box;
      background: var(--card-background-color); color: var(--primary-text-color); }
    dialog::backdrop { background: rgba(0, 0, 0, 0.45); }
    .preview { margin: 8px 0 4px; }
    .rows { list-style: none; margin: 8px 0; padding: 0; display: grid; gap: 8px; }
    .rows li { display: grid; grid-template-columns: auto 1fr 40px; gap: 8px; align-items: center; }
    .rows li.bad .time select { outline: 2px solid var(--error-color); }
    .time { display: inline-flex; gap: 4px; }
    .time select { padding: 8px 4px; }
    .rows input, .rows select { font: inherit; padding: 8px; border-radius: 8px; border: 1px solid var(--divider-color);
      background: var(--card-background-color); color: inherit; min-width: 0; }
    .del { width: 40px; height: 40px; border-radius: 50%; border: 0; background: none; color: var(--secondary-text-color); font-size: 22px; cursor: pointer; }
    .add { font: inherit; background: none; border: 1px dashed var(--divider-color); border-radius: 8px; padding: 8px; width: 100%; color: var(--s); cursor: pointer; }
    .add:disabled { color: var(--disabled-text-color, #999); cursor: default; }
    .copy { border: 0; margin: 12px 0 0; padding: 0; display: flex; flex-wrap: wrap; gap: 4px 12px; }
    .copy legend { font-size: 13px; color: var(--secondary-text-color); margin-bottom: 4px; }
    .copy label { display: inline-flex; align-items: center; gap: 4px; min-height: 32px; }
    .copy .self { color: var(--secondary-text-color); }
    .err { color: var(--error-color); min-height: 1.2em; font-size: 13px; }
    .actions { display: flex; justify-content: flex-end; gap: 8px; }
    .actions button { font: inherit; border-radius: 999px; padding: 8px 18px; cursor: pointer; border: 0; min-height: 40px; }
    .ghost { background: none; color: var(--s); }
    .primary { background: var(--s); color: var(--text-primary-color, #fff); }
    .primary:disabled { opacity: 0.5; cursor: default; }
    .pills { display: flex; flex-wrap: wrap; gap: 6px; margin: 12px 0; }
    .pill { font: inherit; font-size: 13px; border: 1px solid var(--divider-color); background: none; color: inherit; border-radius: 999px; padding: 4px 12px; cursor: pointer; min-height: 32px; }
    .pill[aria-pressed='true'] { background: color-mix(in srgb, var(--s) 18%, transparent); border-color: var(--s); color: var(--s); }
    .journal { list-style: none; margin: 8px 0 0; padding: 0; }
    .journal li { display: grid; grid-template-columns: 10px 1fr auto; gap: 10px; align-items: baseline; padding: 10px 0; border-bottom: 1px solid var(--divider-color); }
    .journal li:last-child { border-bottom: 0; }
    .journal li.muted { display: block; }
    .what { display: flex; flex-direction: column; min-width: 0; overflow-wrap: anywhere; }
    .who { font-size: 12px; color: var(--secondary-text-color); }
    .when { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
    .link { font: inherit; background: none; border: 0; color: var(--s); cursor: pointer; padding: 0; }
    .toast { position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%); display: flex; align-items: center; gap: 16px;
      background: var(--primary-text-color); color: var(--card-background-color); padding: 12px 16px; border-radius: 8px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3); max-width: calc(100vw - 32px); box-sizing: border-box; z-index: 10; }
    .toast.err { background: var(--error-color); color: #fff; }
    .toast button { font: inherit; font-weight: 600; background: none; border: 0; color: var(--s); cursor: pointer; text-transform: uppercase; }
    .toast.err button { color: #fff; }
    .warn { color: var(--error-color); background: color-mix(in srgb, var(--error-color) 10%, transparent); padding: 8px 12px; border-radius: 8px; margin: 0 0 8px; }
    .chart { margin: 8px 0 0; }
    .chart svg { width: 100%; height: auto; display: block; }
    .chart .grid { stroke: var(--divider-color); }
    .chart .tick { fill: var(--secondary-text-color); font-size: 11px; }
    .chart .hatch { fill: var(--error-color); opacity: 0.22; }
    .chart .limit { stroke: var(--error-color); stroke-width: 1.5; }
    .chart .lbl-err { fill: var(--error-color); font-size: 11px; paint-order: stroke; stroke: var(--card-background-color); stroke-width: 3px; }
    .chart .base { stroke: var(--secondary-text-color); stroke-dasharray: 2 4; }
    .chart .cur { fill: none; stroke: var(--s); stroke-width: 2.5; }
    .chart .prop { fill: none; stroke: var(--warning-color, #ffa600); stroke-width: 2.5; stroke-dasharray: 7 5; }
    .chart .pt { fill: var(--s); stroke: var(--card-background-color); stroke-width: 2; }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 12px; color: var(--secondary-text-color); margin-top: 6px; }
    .legend span { display: inline-flex; align-items: center; gap: 6px; }
    .k { display: inline-block; width: 18px; height: 0; border-top: 2.5px solid var(--s); }
    .k.prop { border-top: 2.5px dashed var(--warning-color, #ffa600); }
    .k.pt { width: 10px; height: 10px; border: 0; border-radius: 50%; background: var(--s); }
    .k.base { border-top: 1.5px dotted var(--secondary-text-color); }
    .k.lim { border-top: 1.5px solid var(--error-color); }
    .badge { font-size: 12px; border-radius: 999px; padding: 2px 10px; border: 1px solid var(--divider-color); color: var(--secondary-text-color); }
    .badge.high { color: var(--success-color, #43a047); border-color: currentColor; }
    .badge.medium { color: var(--warning-color, #ffa600); border-color: currentColor; }
    .reasons { margin: 0 0 8px; padding-left: 20px; display: grid; gap: 4px; }
    .tbl { overflow-x: auto; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
    th { font-weight: 500; color: var(--secondary-text-color); }
    tr.thin td { color: var(--secondary-text-color); }
    .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-top: 12px; }
    .tile { display: flex; flex-direction: column; gap: 2px; padding: 12px; border-radius: 8px; background: color-mix(in srgb, var(--secondary-text-color) 6%, transparent); }
    .tile .help { margin: 0; }
    .tl { font-size: 13px; color: var(--secondary-text-color); }
    .tv { font-size: 22px; font-weight: 500; font-variant-numeric: tabular-nums; }
    .tu { font-size: 14px; font-weight: 400; color: var(--secondary-text-color); }
    .actions.start { justify-content: flex-start; flex-wrap: wrap; }
    td { font-variant-numeric: tabular-nums; }
    @media (max-width: 600px) {
      .tabs { padding: 8px 4px 0; gap: 0; }
      .tabs button { padding: 10px 10px; }
      main { padding: 12px; gap: 12px; }
      .card { padding: 12px; }
    }
  `;
}

customElements.define('dd-smart-panel', DdSmartPanel);
