// Panneau « Chaudière » de l'intégration De Dietrich Smart : programmes horaires et journal.
// Repris de l'interface de l'application ancienne application (mêmes règles et mêmes textes).
// Aucune ressource externe : Lit est servi par l'intégration.
import { LitElement, html, css, nothing } from './lit-all.min.js';

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
const fDeg = (v) => (v == null ? '—' : `${String(Math.round(v * 10) / 10).replace('.', ',')} °C`);
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

  get _entry() { return this._entries?.find((e) => e.entry_id === this._entryId); }
  get _appl() { return this._entry?.state?.appliances?.[0]; }
  get _zone() { return this._appl?.zones?.[0]; }
  get _dhw() { return this._appl?.dhw?.[0]; }
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
      this._showToast(res.message, res.undo_until ? res.command_id : null, res.undo_until);
      setTimeout(() => this._load(), 8000);
      return res;
    } catch (e) {
      this._showToast(e.message || String(e), null, null, true);
      return null;
    }
  }

  _showToast(text, commandId = null, undoUntil = null, error = false) {
    clearTimeout(this._toastTimer);
    this._toast = { text, commandId, error };
    const ms = undoUntil ? Math.max(4000, undoUntil * 1000 - Date.now()) : 6000;
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

  // --- Éditeur d'un jour -----------------------------------------------------

  _openEditor(kind, day) {
    const z = this._zone, d = this._dhw;
    const program = this._prog ?? z?.program ?? 1;
    const week = kind === 'dhw' ? d?.week : z?.week?.[String(program)];
    if (!week) return;
    const rows = week.filter((sp) => sp.day === day).sort((a, b) => spMin(a) - spMin(b)).map((sp) => ({ ...sp }));
    this._editor = { kind, program, day, week, rows, copy: new Set() };
    this.updateComplete.then(() => this.renderRoot.querySelector('dialog')?.showModal());
  }

  _closeEditor() {
    this.renderRoot.querySelector('dialog')?.close();
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
        ${[['programs', 'Programmes'], ['journal', 'Journal']].map(([k, l]) => html`
          <button role="tab" aria-selected=${this._tab === k} class=${this._tab === k ? 'on' : ''}
            @click=${() => { this._tab = k; if (k === 'journal') this._loadJournal(); }}>${l}</button>`)}
      </nav>
      <main>
        ${this._error ? html`<div class="card error">${this._error}</div>` : nothing}
        ${this._entries == null ? html`<div class="card muted">Chargement…</div>`
          : !this._entries.length ? html`<div class="card muted">Aucune chaudière configurée.</div>`
          : this._tab === 'programs' ? this._renderPrograms() : this._renderJournal()}
      </main>
      ${this._editor ? this._renderEditor() : nothing}
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
          <p class="help">Touchez un jour pour modifier ses créneaux.</p>`
          : html`<p class="muted">Programme ${n} pas encore lu sur le cloud.</p>`}
      </section>
      ${(z.activities || []).length ? html`
        <section class="card">
          <h2>Consignes des activités</h2>
          <p class="help">Lues sur le thermostat ; une modification ici s'applique au thermostat.</p>
          <ul class="acts">
            ${z.activities.map((a) => {
              const v = this._actWanted[a.n] ?? a.setpoint;
              return html`<li class=${used.has(a.n) ? '' : 'unused'}>
                <span class="sw" style="--i:${intensity(v)}%" aria-hidden="true"></span>
                <span class="lbl">${a.name}<span class="help">${this._actWanted[a.n] != null ? 'envoi…' : used.has(a.n) ? '' : `pas dans le programme ${n}`}</span></span>
                <span class="stepper">
                  <button aria-label="Baisser la consigne ${a.name}" @click=${() => this._actBump(a.n, -1)}>−</button>
                  <span class="v" aria-live="polite">${fDeg(v)}</span>
                  <button aria-label="Monter la consigne ${a.name}" @click=${() => this._actBump(a.n, 1)}>+</button>
                </span>
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
        aria-label="${DAYS[day]} : ${said || 'aucun créneau'}. Modifier" @click=${() => this._openEditor('zone', day)}>
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
        aria-label="${DAYS[day]} : ${said || 'réduit toute la journée'}. Modifier" @click=${() => this._openEditor('dhw', day)}>
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
    return html`<dialog @close=${() => { this._editor = null; }} @cancel=${() => { this._editor = null; }}>
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
    const undoable = this._entry?.undo?.command_id === c.id;
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
    .tabs { display: flex; gap: 4px; padding: 8px 12px 0; border-bottom: 1px solid var(--divider-color); background: var(--card-background-color); }
    .tabs button { font: inherit; background: none; border: 0; padding: 10px 14px; color: var(--secondary-text-color); border-bottom: 2px solid transparent; cursor: pointer; }
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
    @media (max-width: 600px) {
      main { padding: 12px; gap: 12px; }
      .card { padding: 12px; }
    }
  `;
}

customElements.define('dd-smart-panel', DdSmartPanel);
