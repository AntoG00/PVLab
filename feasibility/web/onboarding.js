'use strict';
const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
function getCompass(deg) {
  if (deg == null || (typeof deg === 'string' && !deg.trim())) return '';
  const n = Number(deg);
  if (!Number.isFinite(n)) return '';
  return COMPASS[Math.round((((n % 360) + 360) % 360) / 45) % 8];
}

class RoofListEditor {
  constructor(container, options = {}) {
    this.container = container;
    this.doc = options.doc || container.ownerDocument || (typeof document !== 'undefined' ? document : null);
    this.onChange = options.onChange || (() => {});
    this.roofs = [];
    this.statusEl = this.doc.createElement('div');
    this.statusEl.setAttribute('role', 'status');
    this.statusEl.setAttribute('aria-live', 'polite');
    this.listEl = this.doc.createElement('div');
    this.setRoofs(options.initialRoofs);
  }

  getRoofs() { return this.roofs.map(r => ({ ...r })); }

  setRoofs(roofs) {
    if (roofs == null) roofs = [];
    if (!Array.isArray(roofs)) throw Error('ROOF_INVALID: expected array of roof planes');
    if (roofs.length > 12) throw Error('ROOF_LIMIT: at most twelve planes');
    this.roofs = roofs.map((r, i) => this._sanitize(r, i));
    this.render();
  }

  _sanitize(r, i) {
    if (!r || typeof r !== 'object' || Array.isArray(r)) throw Error('ROOF_INVALID: each roof plane must be an object');
    return {
      id: r.id != null ? String(r.id) : 'roof_' + (i + 1) + '_' + Math.random().toString(36).slice(2, 6),
      label: String(r.label || ('Roof ' + (i + 1))),
      tilt_deg: Number(r.tilt_deg ?? NaN),
      azimuth_deg: Number(r.azimuth_deg ?? NaN),
      max_kwp: Number(r.max_kwp ?? NaN),
      shading_fraction: Number(r.shading_fraction ?? NaN)
    };
  }

  addRoof(roof) {
    if (this.roofs.length >= 12) throw Error('ROOF_LIMIT: at most twelve planes');
    const r = this._sanitize(roof == null ? {} : roof, this.roofs.length);
    this.roofs.push(r);
    this.render();
    this.statusEl.textContent = 'Added ' + r.label;
    const inp = this.listEl.lastElementChild?.querySelector('input');
    if (inp && inp.focus) inp.focus();
    this.onChange(this.getRoofs());
    return r;
  }

  removeRoof(id) {
    const idx = this.roofs.findIndex(r => r.id === id);
    if (idx === -1) return;
    const [removed] = this.roofs.splice(idx, 1);
    this.render();
    this.statusEl.textContent = 'Removed ' + removed.label;
    if (this.addBtn && this.addBtn.focus) this.addBtn.focus();
    this.onChange(this.getRoofs());
  }

  render() {
    const d = this.doc;
    this.container.textContent = '';
    this.listEl.textContent = '';
    this.roofs.forEach((r, idx) => {
      const row = d.createElement('fieldset');
      row.className = 'roof-plane';
      row.setAttribute('data-id', r.id);
      const leg = d.createElement('legend');
      leg.textContent = r.label || ('Roof ' + (idx + 1));
      row.appendChild(leg);

      const field = (txt, type, val, min, max, step, onInput) => {
        const lbl = d.createElement('label');
        lbl.textContent = txt + ': ';
        const inp = d.createElement('input');
        inp.type = type;
        inp.value = type === 'number' && !Number.isFinite(val) ? '' : val;
        if (min != null) inp.min = String(min);
        if (max != null) inp.max = String(max);
        if (step != null) inp.step = String(step);
        inp.addEventListener('input', () => onInput(inp.value));
        lbl.appendChild(inp);
        return lbl;
      };

      row.appendChild(field('Label', 'text', r.label, null, null, null, v => {
        r.label = String(v);
        leg.textContent = r.label;
        this.onChange(this.getRoofs());
      }));
      row.appendChild(field('Tilt (°)', 'number', r.tilt_deg, 0, 90, 0.1, v => {
        r.tilt_deg = v.trim()===''?NaN:Number(v);
        this.onChange(this.getRoofs());
      }));
      const hint = d.createElement('span');
      hint.className = 'compass-hint';
      const updateCompass = deg => {
        const c = getCompass(deg);
        hint.textContent = c ? ' (' + c + ')' : '';
        hint.hidden = !c;
      };
      updateCompass(r.azimuth_deg);
      const azLbl = field('Azimuth (°)', 'number', r.azimuth_deg, 0, 359.99, 0.01, v => {
        r.azimuth_deg = v.trim()===''?NaN:Number(v);
        updateCompass(r.azimuth_deg);
        this.onChange(this.getRoofs());
      });
      azLbl.appendChild(hint);
      row.appendChild(azLbl);
      row.appendChild(field('Capacity limit kWp (positive)', 'number', r.max_kwp, 0, null, 0.1, v => {
        r.max_kwp = v.trim()===''?NaN:Number(v);
        this.onChange(this.getRoofs());
      }));
      row.appendChild(field('Coarse shading fraction (0–1)', 'number', r.shading_fraction, 0, 1, 0.05, v => {
        r.shading_fraction = v.trim()===''?NaN:Number(v);
        this.onChange(this.getRoofs());
      }));

      const del = d.createElement('button');
      del.type = 'button';
      del.className = 'danger roof-remove-btn';
      del.textContent = 'Remove ' + r.label;
      del.setAttribute('aria-label', 'Remove ' + r.label);
      del.addEventListener('click', () => this.removeRoof(r.id));
      const actions = d.createElement('div');
      actions.className = 'roof-actions';
      actions.appendChild(del);
      row.appendChild(actions);
      this.listEl.appendChild(row);
    });

    const atLimit = this.roofs.length >= 12;
    const addBtn = d.createElement('button');
    addBtn.type = 'button';
    addBtn.textContent = '+ Add Roof';
    addBtn.disabled = atLimit;
    if (atLimit) {
      addBtn.setAttribute('aria-disabled', 'true');
      addBtn.title = 'Maximum of 12 roof planes reached';
    }
    addBtn.addEventListener('click', () => {
      if (addBtn.disabled || this.roofs.length >= 12) return;
      this.addRoof();
    });
    this.addBtn = addBtn;

    const note = d.createElement('p');
    note.className = 'field-help roof-limit-note';
    note.textContent = atLimit
      ? 'Maximum limit of 12 roof planes reached. Remove a roof plane to add another.'
      : '';
    note.hidden = !atLimit;
    this.limitNote = note;

    this.container.appendChild(this.statusEl);
    this.container.appendChild(this.listEl);
    this.container.appendChild(addBtn);
    if (atLimit) {
      this.container.appendChild(note);
    }
  }
}



const SUPPORTED_YEARS = Object.freeze(Array.from({ length: 21 }, (_, i) => 2005 + i));

class YearMenu {
  constructor(container, options = {}) {
    this.container = container;
    this.doc = options.doc || container.ownerDocument || (typeof document !== 'undefined' ? document : null);
    this.supported = options.supportedYears || SUPPORTED_YEARS;
    this.verified = options.verified !== undefined ? Boolean(options.verified) : true;
    this.compiled = false;
    this.selected = new Set(options.initialYears || []);
    this._onChange = options.onChange || (() => {});
    this.inputs = new Map();
    this._val = '';
    this.render();
  }

  getSelectedYears() {
    return Array.from(this.selected).sort((a, b) => a - b);
  }

  setSelectedYears(years) {
    const list = Array.isArray(years) ? years : String(years || '').split(/[,\s]+/).filter(Boolean).map(Number);
    for (const y of list) {
      if (!this.supported.includes(y)) throw Object.assign(Error('YEARS_INVALID: unsupported weather year ' + y), { code: 'YEARS_INVALID' });
    }
    this.selected = new Set(list);
    this._sync();
    this._onChange(this.getSelectedYears());
  }

  setSupportedYears(years) {
    const list = Array.from(years || []);
    this.compiled = false;
    this.supported = list.length ? Object.freeze(list) : SUPPORTED_YEARS;
    this.verified = list.length > 0;
    const nextSelected = new Set();
    for (const y of this.selected) {
      if (list.length && this.supported.includes(y)) nextSelected.add(y);
    }
    this.selected = nextSelected;
    this.render();
    this._onChange(this.getSelectedYears());
  }

  setCompiledYears(years) {
    const list = Array.from(years || []).filter(y => SUPPORTED_YEARS.includes(Number(y))).map(Number);
    this.compiled = list.length > 0;
    this.verified = false;
    this.supported = list.length ? Object.freeze(list) : SUPPORTED_YEARS;
    this.selected = new Set(list);
    this.render();
    this._onChange(this.getSelectedYears());
  }

  setVerified(verified) {
    this.verified = Boolean(verified);
    this.render();
  }

  _sync() {
    for (const [y, cb] of this.inputs) cb.checked = this.selected.has(y);
    const count = this.selected.size;
    const isCompiled = Boolean(this.compiled);
    const total = (this.verified || isCompiled) ? this.supported.length : 0;
    if (this.allCb) {
      this.allCb.checked = total > 0 && count === total;
      this.allCb.indeterminate = total > 0 && count > 0 && count < total;
    }
    if (this.summaryBadge) {
      if (total === 0) {
        this.summaryBadge.textContent = 'No years available';
      } else {
        const minYear = this.supported[0], maxYear = this.supported[total - 1];
        const range = minYear === maxYear ? `${minYear}` : `${minYear}–${maxYear}`;
        if (isCompiled && !this.verified) {
          this.summaryBadge.textContent = count === total ? `Compiled ${total} years (${range})` : `${count} / ${total} years selected`;
        } else {
          this.summaryBadge.textContent = count === total ? `All ${total} years (${range})` : `${count} / ${total} years selected`;
        }
      }
    }
    this._val = this.getSelectedYears().join(', ');
  }

  render() {
    const d = this.doc;
    this.container.textContent = '';
    const details = d.createElement('details');
    details.className = 'year-menu-details';

    const isCompiled = Boolean(this.compiled);
    const total = (this.verified || isCompiled) ? this.supported.length : 0;
    const minYear = total > 0 ? this.supported[0] : null;
    const maxYear = total > 0 ? this.supported[total - 1] : null;
    const range = total > 0 ? (minYear === maxYear ? `${minYear}` : `${minYear}–${maxYear}`) : '';

    const summary = d.createElement('summary');
    summary.className = 'year-menu-summary';
    const sumTitle = d.createElement('span');
    if (isCompiled && !this.verified) {
      sumTitle.textContent = total > 0 ? `Historical weather years (compiled profile ${range}): ` : 'Historical weather years (none available): ';
    } else {
      sumTitle.textContent = total > 0 ? `Historical weather years (${range}): ` : 'Historical weather years (none available): ';
    }
    const badge = d.createElement('span');
    badge.className = 'year-summary-badge';
    this.summaryBadge = badge;
    summary.appendChild(sumTitle);
    summary.appendChild(badge);
    details.appendChild(summary);

    const help = d.createElement('p');
    help.className = 'year-menu-help';
    if (isCompiled && !this.verified) {
      help.textContent = total > 0
        ? `Compiled model uses ${range} (${total} historical weather ensemble years). Save draft and check solar coverage to discover current provider years for re-generation.`
        : 'No supported weather years available. Save profile and check solar coverage to load supported years.';
    } else {
      help.textContent = total > 0
        ? `PVGIS SARAH time series extend from ${range} (${total} weather ensemble years).`
        : 'No supported weather years available. Save profile and check solar coverage to load supported years.';
    }
    details.appendChild(help);

    const selectAllLbl = d.createElement('label');
    selectAllLbl.className = 'year-select-all';
    const allCb = d.createElement('input');
    allCb.type = 'checkbox';
    allCb.name = 'weather_year';
    allCb.value = 'all';
    allCb.setAttribute('aria-label', total > 0
      ? `Select all historical weather years (${range})`
      : 'Select all historical weather years (none available)');
    allCb.disabled = (!this.verified && !isCompiled) || total === 0;
    allCb.onchange = () => {
      this.selected = allCb.checked ? new Set(this.supported) : new Set();
      this._sync();
      this.input?.dispatchEvent?.(new (this.doc.defaultView?.Event || Event)('input', { bubbles: true }));
      this._onChange(this.getSelectedYears());
    };
    this.allCb = allCb;
    const allTxt = d.createElement('span');
    allTxt.textContent = 'Select all';
    selectAllLbl.appendChild(allCb);
    selectAllLbl.appendChild(allTxt);
    details.appendChild(selectAllLbl);

    const grid = d.createElement('div');
    grid.className = 'year-checkbox-grid';
    grid.setAttribute('role', 'group');
    grid.setAttribute('aria-label', 'Historical weather years');

    this.inputs.clear();
    for (const y of ((this.verified || isCompiled) ? this.supported : [])) {
      const lbl = d.createElement('label');
      lbl.className = 'year-checkbox-item';
      const cb = d.createElement('input');
      cb.type = 'checkbox';
      cb.name = 'weather_year';
      cb.value = String(y);
      cb.disabled = !this.verified && !isCompiled;
      cb.onchange = () => {
        if (cb.checked) this.selected.add(y); else this.selected.delete(y);
        this._sync();
        this.input?.dispatchEvent?.(new (this.doc.defaultView?.Event || Event)('input', { bubbles: true }));
        this._onChange(this.getSelectedYears());
      };
      this.inputs.set(y, cb);
      const span = d.createElement('span');
      span.textContent = String(y);
      lbl.appendChild(cb);
      lbl.appendChild(span);
      grid.appendChild(lbl);
    }
    details.appendChild(grid);

    if (!this.input) {
      const hidden = d.createElement('input');
      hidden.type = 'hidden';
      hidden.name = 'years';
      hidden.getSelectedYears = () => this.getSelectedYears();
      Object.defineProperty(hidden, 'value', {
        get: () => this._val,
        set: (v) => {
          const s = String(v ?? '');
          if (s === this._val) return;
          this.setSelectedYears(s);
        },
        configurable: true
      });
      this.input = hidden;
    }
    this.input.disabled = false;
    details.appendChild(this.input);

    this.container.appendChild(details);
    this._sync();
  }
}

if (typeof window !== 'undefined') {
  window.RoofListEditor = RoofListEditor;
  window.YearMenu = YearMenu;
  window.SUPPORTED_YEARS = SUPPORTED_YEARS;
}


'use strict';
const Onboarding = (()=>{
 const Diagnostics = (typeof window !== 'undefined' && window.Diagnostics) || (typeof globalThis !== 'undefined' && globalThis.Diagnostics) || (typeof require === 'function' ? (()=>{ try{ return require('./diagnostics.js'); }catch(_){} })() : null);
 const clone=v=>JSON.parse(JSON.stringify(v));
 const fail=(code,message)=>{throw Object.assign(Error(code+': '+message),{code});};
 const FRACTIONS = new Set(["electric_vat","pmg_nominal_escalation","gse_fee_vat","export_irpef","regional_additional_tax","municipal_additional_tax","deduction_rate","gas_vat_reduced","gas_vat_standard"]);
 const ZONE_HINTS = {
  NORD: "Zona NORD: Valle d'Aosta, Piemonte, Liguria, Lombardia, Trentino-Alto Adige, Veneto, Friuli-V.G., Emilia-Romagna (Nord Italia / Northern Italy)",
  CNOR: "Zona CNOR: Toscana, Umbria, Marche (Centro-Nord / Central-Northern Italy)",
  CSUD: "Zona CSUD: Lazio, Abruzzo, Molise, Campania (Centro-Sud / Central-Southern Italy)",
  SUD: "Zona SUD: Basilicata, Puglia (Sud Italia / Southern Italy)",
  CALA: "Zona CALA: Calabria",
  SICI: "Zona SICI: Sicilia / Sicily",
  SARD: "Zona SARD: Sardegna / Sardinia"
 };
 const GAS_REGIONS = [
  { value: "ABRUZZO", label: "Abruzzo" },
  { value: "BASILICATA", label: "Basilicata" },
  { value: "CALABRIA", label: "Calabria" },
  { value: "CAMPANIA", label: "Campania" },
  { value: "EMILIA_ROMAGNA", label: "Emilia-Romagna" },
  { value: "FRIULI_VENEZIA_GIULIA", label: "Friuli-Venezia Giulia" },
  { value: "LAZIO", label: "Lazio / Latium" },
  { value: "LIGURIA", label: "Liguria" },
  { value: "LOMBARDIA", label: "Lombardia / Lombardy" },
  { value: "MARCHE", label: "Marche" },
  { value: "MOLISE", label: "Molise" },
  { value: "PIEMONTE", label: "Piemonte / Piedmont" },
  { value: "PUGLIA", label: "Puglia / Apulia" },
  { value: "SARDEGNA", label: "Sardegna / Sardinia" },
  { value: "SICILIA", label: "Sicilia / Sicily" },
  { value: "TOSCANA", label: "Toscana / Tuscany" },
  { value: "TRENTINO_ALTO_ADIGE", label: "Trentino-Alto Adige / Trentino-South Tyrol" },
  { value: "UMBRIA", label: "Umbria" },
  { value: "VALLE_D_AOSTA", label: "Valle d'Aosta / Aosta Valley" },
  { value: "VENETO", label: "Veneto" }
 ];

  function recommendVariant(f1,f2,f3){
   const check=v=>{
    if(typeof v==='number')return Number.isFinite(v)&&v>=0;
    if(typeof v==='string'&&v.trim()!==''){const n=Number(v.trim());return Number.isFinite(n)&&n>=0;}
    return false;
   };
   if(!check(f1)||!check(f2)||!check(f3))return null;
   const n1=Number(f1),n2=Number(f2),n3=Number(f3);
   const s=n1+n2+n3;
   if(!Number.isFinite(s)||s<=0)return null;
   const f1Share=n1/s;
   const f1Pct=Math.round(f1Share*100);
   const f23Pct=Math.round(((n2+n3)/s)*100);
   if(f1Share>=0.38){
    return {variant:'daytime',f1Share,label:'Daytime',message:`Daytime-heavy usage detected (F1 = ${f1Pct}%). Recommended profile: Daytime`};
   }
   if(f1Share>=0.28){
    return {variant:'flat',f1Share,label:'Flat / Continuous',message:`Balanced usage detected (F1 = ${f1Pct}%). Recommended profile: Flat / Continuous`};
   }
   return {variant:'evening',f1Share,label:'Evening',message:`Evening/Night-heavy usage detected (F2+F3 = ${f23Pct}%). Recommended profile: Evening`};
  }
 function numbers(text,count=12){
  const values=(String(text).includes(',')?String(text).split(','):String(text).includes(';')?String(text).split(';'):String(text).trim().split(/\s+/)).map(v=>v.trim()===''?NaN:Number(v));
  if(values.length!==count||values.some(v=>!Number.isFinite(v)||v<0))fail('MONTHLY_TOTALS','Enter all twelve nonnegative monthly totals; zero is allowed.');return values;
 }
 function parseCoordinatePair(text){
  if(typeof text!=='string')return null;
  let s=text.trim();
  if(s.startsWith('@'))s=s.slice(1).trim();
  s=s.replace(/^[(\[]\s*/,'').replace(/\s*[)\]]$/,'');
  const m=s.match(/^([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)(?:\s*[,;]\s*|\s+)([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)$/);
  if(!m)return null;
  const lat=Number(m[1]),lon=Number(m[2]);
  if(!Number.isFinite(lat)||!Number.isFinite(lon))return null;
  if(lat<-90||lat>90||lon<-180||lon>180)return null;
  return {lat:m[1],lon:m[2],latNum:lat,lonNum:lon};
 }
 function validateDraft(profile,load,{gross=false,existingPv=false,reconciled=false}={}){
  if(profile.country!=='IT'||profile.timezone!=='Europe/Rome')fail('UNSUPPORTED_LOCATION','Only Italy / Europe/Rome is supported.');
  if(!gross||existingPv&&!reconciled)fail('GROSS_LOAD_REQUIRED','Reconcile grid readings with existing PV/storage to provide gross household demand.');
  if(!profile.location||!Number.isFinite(profile.location.latitude)||!Number.isFinite(profile.location.longitude)||Math.abs(profile.location.latitude)>90||Math.abs(profile.location.longitude)>180)fail('LOCATION_REQUIRED','Enter valid manual coordinates.');
  if(!profile.consumption.acknowledged)fail('ACKNOWLEDGEMENT_REQUIRED','Confirm modeled timing and synthetic coverage uncertainty.');
  const BAND_KEYS=['monthly_f1','monthly_f2','monthly_f3'];
  const hasProfileBands=BAND_KEYS.some(k=>k in (profile.consumption||{}));
  const hasPolicyBands=BAND_KEYS.some(k=>load.policy&&k in load.policy);
  const hasBands=hasProfileBands||hasPolicyBands;
  if(hasBands){
   if(profile.consumption?.kind!=='monthly')fail('BANDS_NOT_ALLOWED','Time-of-use bands are only permitted with monthly consumption.');
   if(!load.policy)fail('POLICY_REQUIRED','Policy is required for time-of-use bands.');
   for(const target of [profile.consumption,load.policy]){
    for(const k of BAND_KEYS){
     const b=target?.[k];
     if(!Array.isArray(b)||b.length!==12)fail('MONTHLY_TOTALS',k+' must be an array of twelve monthly band totals.');
     if(b.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<0))fail('MONTHLY_TOTALS',k+' must contain twelve nonnegative finite numbers.');
    }
   }
   if('monthly_totals' in profile.consumption||'monthly_totals' in load||(load.policy&&'monthly_totals' in load.policy)){
    fail('MONTHLY_TOTALS_FORBIDDEN','Monthly totals must not be provided alongside time-of-use bands.');
   }
   for(let i=0;i<12;i++){
    const s=profile.consumption.monthly_f1[i]+profile.consumption.monthly_f2[i]+profile.consumption.monthly_f3[i];
    if(!Number.isFinite(s))fail('MONTHLY_TOTALS','Monthly band sums must be finite.');
   }
  }else if(profile.consumption.kind!=='annual'){
   numbers((load.monthly_totals||[]).join(','));
  }else if(!Number.isFinite(load.policy?.annual_kwh)||load.policy.annual_kwh<0)fail('ANNUAL_TOTAL_REQUIRED','Enter nonnegative annual kWh.');
  if(profile.roofs.length>12)fail('ROOF_LIMIT','At most twelve roof planes.');
  const ids=new Set();for(const r of profile.roofs){
   if(ids.has(r.id)||!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(r.id)||!r.label.trim()||r.label.length>120||!Number.isFinite(r.tilt_deg)||r.tilt_deg<0||r.tilt_deg>90||!Number.isFinite(r.azimuth_deg)||r.azimuth_deg<0||r.azimuth_deg>=360||!Number.isFinite(r.max_kwp)||r.max_kwp<=0||!Number.isFinite(r.shading_fraction)||r.shading_fraction<0||r.shading_fraction>1)fail('ROOF_INVALID','Review each roof label, tilt, compass azimuth, positive kWp and shading fraction.');ids.add(r.id);
  }
  for(const u of profile.thermal.end_uses){if(u.action==='replace'&&u.included_in_electric_load&&!u.artifact_id)fail('END_USE_SERIES_REQUIRED','Replacement of embedded electricity requires a separated hourly end-use series. Keep unchanged or provide separated data.');}
  for(const u of profile.thermal.electric_loads||[]){if(u.action==='add'&&(u.included_in_electric_load||!Number.isFinite(u.incremental_annual_kwh)))fail('INCREMENTAL_LOAD_REQUIRED','Only explicitly additional EV/cooling demand can be added.');if(u.action==='replace'&&!u.artifact_id)fail('END_USE_SERIES_REQUIRED','Provide a separated hourly series before replacing existing EV/cooling demand.');}
  if(!profile.fiscal.parameters.confirmed)fail('FISCAL_CONFIRMATION_REQUIRED','Review fiscal inputs and confirm.');
  if(profile.market.valuation_mode!=='flat'&&!/^[a-f0-9]{64}$/.test(profile.market.price_revision||''))fail('PRICE_REVISION_REQUIRED','Select a pinned catalogue revision or explicitly choose flat valuation.');
  return true;
 }
 function csvMetadata(v){
  if(v.coverage_start&&(!v.coverage_end||!/(Z|[+-]\d\d:\d\d)$/.test(v.coverage_start)||!/(Z|[+-]\d\d:\d\d)$/.test(v.coverage_end)))fail('COVERAGE_REQUIRED','CSV bounds need explicit UTC offsets.');
  if(!['kWh','Wh','average_kW'].includes(v.unit))fail('UNIT_REQUIRED','Choose kWh/Wh per interval or interval-average kW.');
  if(!['start','end'].includes(v.timestamp_convention))fail('TIMESTAMP_CONVENTION_REQUIRED','Choose interval start or end timestamps.');
  if(!v.timestamp||!v.energy)fail('COLUMN_REQUIRED','Map timestamp and energy/power columns.');
  return {delimiter:v.delimiter,decimal_separator:v.decimal_separator,columns:{timestamp:v.timestamp,value:v.energy,...(v.fold?{fold:v.fold}:{})},unit:v.unit,duration_seconds:Number(v.duration_seconds),timezone:v.timezone,timestamp_convention:v.timestamp_convention,demand_basis:v.existing_pv?'reconciled_gross':'gross',fixed_clock:v.fixed_clock===true,existing_pv:v.existing_pv===true,...(v.coverage_start?{coverage_start:v.coverage_start,coverage_end:v.coverage_end}:{})};
 }

 const profileChecksStore=new Map();
 const CHECKS_SESSION_KEY='pv_lab_onboarding_checks_v1';
 function getProfileChecks(profileId){
  if(!profileId)return null;
  if(profileChecksStore.has(profileId))return profileChecksStore.get(profileId);
  try{
   const raw=(typeof localStorage!=='undefined'&&localStorage)?localStorage.getItem(CHECKS_SESSION_KEY):null;
   if(raw){
    const all=JSON.parse(raw);
    if(all&&typeof all==='object'&&all[profileId]){
     profileChecksStore.set(profileId,all[profileId]);
     return all[profileId];
    }
   }
  }catch(_){}
  return null;
 }
 function setProfileChecks(profileId,checks){
  if(!profileId||!checks||typeof checks!=='object')return;
  const current=getProfileChecks(profileId)||{};
  const merged={...current,...checks};
  profileChecksStore.set(profileId,merged);
  try{
   if(typeof localStorage!=='undefined'&&localStorage){
    let all={};
    const raw=localStorage.getItem(CHECKS_SESSION_KEY);
    if(raw)try{all=JSON.parse(raw)||{};}catch(_){}
    all[profileId]=merged;
    localStorage.setItem(CHECKS_SESSION_KEY,JSON.stringify(all));
   }
  }catch(_){}
 }
 function removeProfileChecks(profileId){
  if(!profileId)return;
  profileChecksStore.delete(profileId);
  try{
   if(typeof localStorage!=='undefined'&&localStorage){
    const raw=localStorage.getItem(CHECKS_SESSION_KEY);
    if(raw){
     const all=JSON.parse(raw)||{};
     delete all[profileId];
     localStorage.setItem(CHECKS_SESSION_KEY,JSON.stringify(all));
    }
   }
  }catch(_){}
 }
 function clearAllProfileChecks(){
  profileChecksStore.clear();
  try{
   if(typeof localStorage!=='undefined'&&localStorage){
    localStorage.removeItem(CHECKS_SESSION_KEY);
   }
  }catch(_){}
 }
 class Workflow {
  constructor(client,{onChange=()=>{}}={}){this.client=client;this.onChange=onChange;this.status='draft';this.profile=null;this.load=null;this.checks=null;this.jobId=null;this.compiledCandidate=null;this.marketCatalog=null;}
  reset(){this.status='draft';this.profile=null;this.load=null;this.checks=null;this.jobId=null;this.compiledCandidate=null;this.marketCatalog=null;}
  async select(id,revision){
   this.reset();
   this.client.select(id,revision);
   const value=await this.client.profileRequest('');
   if(this.client.profileId!==id)return value;
   this.profile=value.profile;
   const validation=await this.client.profileRequest('/validation');
   if(this.client.profileId!==id)return value;
   this.status=validation.ready?'ready':'draft';
   this.client.ready=this.status==='ready';
   this.compiledCandidate=validation.compiled_candidate||value.compiled_candidate||null;
   this.checks=getProfileChecks(id);
   try{
    const catRes=await this.client.getMarketCatalog(this.profile?.market?.price_revision);
    if(this.client.profileId===id)this.marketCatalog=catRes?.catalog||null;
   }catch(_){
    if(this.client.profileId===id)this.marketCatalog=null;
   }
   if(this.client.profileId===id)this.onChange();
   return value;
  }
  async create(name=''){this.reset();this.client.clear();const value=await this.client.request('/api/profiles',{name});await this.select(value.id,value.revision);return value;}
  async save(profile,load,checks){
   validateDraft(profile,load,checks);this.client.ready=false;this.status='draft';this.onChange();
   await this.client.profileRequest('',{profile},{method:'PATCH'});
   const validation=await this.client.profileRequest('/validation');
   profile=clone(profile);profile.fiscal.rule_revision=validation.fiscal_rule_revision;
   await this.client.profileRequest('',{profile},{method:'PATCH'});this.profile=profile;this.load=clone(load);this.checks=clone(checks);if(profile?.id&&checks)setProfileChecks(profile.id,checks);return this.client.profileRequest('/validation');
  }
  async upload(file,metadata){
   this.status='draft';this.client.ready=false;
   const value=await this.client.profileRequest('/consumption',file,{raw:true,headers:{'Content-Type':'text/csv','X-Consumption-Metadata':JSON.stringify(metadata)}});this.onChange();return value;
  }
  async getMarketCatalog(){
   const id=this.client.profileId;
   const res=await this.client.getMarketCatalog();
   if(this.client.profileId===id){
    this.marketCatalog=res?.catalog||null;
    this.onChange();
   }
   return res;
  }
  async uploadMarketCatalog(file){
   const id=this.client.profileId;
   const res=await this.client.uploadMarketCatalog(file);
   if(this.client.profileId===id){
    this.marketCatalog=res?.catalog||null;
    this.onChange();
   }
   return res;
  }
  async generate(years,solar,onProgress=()=>{}){
   const generation=this.client.generation;this.status='draft';this.client.ready=false;
   try{
    const preliminary=await this.client.profileRequest('/validation');
    const blockers=(preliminary.blockers||[]).filter(code=>code!=='COMPILED_CANDIDATE_REQUIRED');
    if(blockers.length)fail('PROFILE_NOT_READY',blockers.join(', '));
    const job=await this.client.profileRequest('/solar-jobs',{consent:true,revision:this.client.revision,years,solar,load:this.load});this.jobId=job.id;
    await this.client.waitJob(job.id,{generation,onProgress});
    await this.client.profileRequest('/compile',{revision:this.client.revision,job_id:job.id});
    const validation=await this.client.profileRequest('/validation');
    if(!validation.ready)fail('PROFILE_NOT_READY','Review validation blockers before evaluation.');
    this.client.ready=true;this.status='ready';this.onChange();return validation;
   }catch(error){if(generation===this.client.generation){this.status='draft';this.client.ready=false;this.onChange();}throw error;}
  }
 }
 function mount({root,client,onReady=()=>{},onClear=()=>{},adminCatalog=null}){
  const d=root.ownerDocument,fields={},workflow=new Workflow(client),el=(tag,text,parent=root)=>{const n=d.createElement(tag);if(text)n.textContent=text;parent.appendChild(n);return n;};
  // Field accessors must exist before any card builder runs, because sync helpers are invoked during mount.
  const val=k=>fields[k].value,check=k=>fields[k].checked;
  root.textContent='';el('h2','Household setup');
  el('p','Inputs persist locally until you explicitly delete them. Deletion removes household inputs and results; no automatic recovery backup is made. Browser-session cookies identify this household on this LAN.');
  const status=el('p');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const actionErrors=new Map();
  const actionProgress=new Map();
  const isJobError=error=>(Diagnostics?.isJobFailure?.(error))||Boolean(error?.jobId||(error?.diagnostic&&(error.diagnostic.job_id||error.diagnostic.error_code||error.diagnostic.error_name))||(error?.code&&typeof error.code==='string'&&(error.code.startsWith('COMPUTE_')||['PROCESS_EXIT','INTERRUPTED','QUEUE_TIMEOUT','SOLAR_TIMEOUT','SOLAR_NETWORK_ERROR','SOLAR_DATA_GAPS','SOLAR_PROVIDER_ERROR','JOB_FAILED','SYNTHETIC_COMPLETION_REQUIRED'].includes(error.code))));
  const formatReadableError=error=>{
   const isJob=isJobError(error);
   if(isJob){
    const code=error?.code||error?.diagnostic?.error_code;
    if(code==='SYNTHETIC_COMPLETION_REQUIRED'){
     return 'Interval training data does not cover required month/hour cells; enable explicit synthetic completion or supply complete coverage.';
    }
    return 'Calculation engine encountered an internal mathematical or logic error. Review inputs or inspect the sanitized bug report.';
   }
   const msg=(error?.message||String(error)).replace(/^Error:\s*/,'');
   const code=error?.code||(msg.includes(':')?msg.split(':')[0].trim():'');
   if(code==='SYNTHETIC_COMPLETION_REQUIRED'||code==='missing month/hour shape cell; explicit synthetic completion required'||/synthetic_completion_required/i.test(msg)){
    return 'Interval training data does not cover required month/hour cells; enable explicit synthetic completion or supply complete coverage.';
   }
   if(code==='PROFILE_NOT_READY'||code.toLowerCase()==='profile not ready'||/profile_not_ready|profile not ready/i.test(msg)){
    return 'Profile not ready: complete the setup sequence (Step 1: Save draft → Step 2: Check solar coverage → Step 3: Generate data) before opening calculation views.';
   }
   if(code==='SAVE_REQUIRED'||/save_required|save required/i.test(msg)){
    return 'Save required: click Step 1 (Save and review draft) before checking solar coverage.';
   }
   if(code==='COVERAGE_STALE'||/coverage_stale|coverage is unverified|coverage is stale/i.test(msg)){
    return 'Solar coverage is unverified. Click Step 2 (Check solar coverage) before generating solar data.';
   }
   if(code==='PROVIDER_CONSENT_REQUIRED'||/provider_consent_required|consent/i.test(msg)){
    return 'Review coordinate destinations and check the authorization box above before proceeding.';
   }
   if(code==='YEARS_INVALID'||/years_invalid/i.test(msg)){
    return 'Choose sorted unique historical years from the supported weather range.';
   }
   if(code==='LOCATION_REQUIRED'||/location_required/i.test(msg)){
    return 'Enter valid coordinates in Section 1 (Location) before saving.';
   }
   if(code==='PROFILE_REQUIRED'||/profile_required|select a profile first/i.test(msg)){
    return 'Profile required: create a new profile or select an existing one above before proceeding.';
   }
   if(code==='CSV_REQUIRED'||/csv_required/i.test(msg)){
    return 'Validate the mapped CSV before generating solar data.';
   }
   if(code==='CSV_BASIS_REQUIRED'||/csv_basis_required/i.test(msg)){
    return 'Choose interval consumption to use a CSV.';
   }
   if(code==='FILE_REQUIRED'||/file_required/i.test(msg)){
    return 'Choose a file to validate or upload.';
   }
   if(code==='ZIP_REQUIRED'||/zip_required/i.test(msg)){
    return 'Choose at least one GME price ZIP archive (.zip) to upload.';
   }
   if(code==='CATALOG_REQUIRED'||/catalog_required/i.test(msg)){
    return 'No market catalogue available to bind to draft.';
   }
   return msg;
  };
  const showButtonError=(target,error)=>{
   if(!target)return;
   const errEl=actionErrors.get(target);
   if(!errEl)return;
   errEl.textContent='';
   errEl.hidden=false;

   const isJob=isJobError(error);

   if(isJob){
    if(typeof errEl.classList?.add==='function')errEl.classList.add('has-diagnostic');
    const safeText=formatReadableError(error);
    errEl._text='Setup operation failed: '+safeText;
    const header=d.createElement('div');
    header.className='error-header';
    const strong=d.createElement('strong');
    strong.textContent='Setup operation failed: ';
    header.appendChild(strong);
    const msgSpan=d.createElement('span');
    msgSpan.textContent=safeText;
    header.appendChild(msgSpan);
    errEl.appendChild(header);

    const diag=Diagnostics?.renderDiagnosticReport
      ? Diagnostics.renderDiagnosticReport(error,{doc:d,expandable:true})
      : null;
    if(diag){
     errEl.appendChild(diag);
    }
   }else{
    if(typeof errEl.classList?.remove==='function')errEl.classList.remove('has-diagnostic');
    errEl.textContent=formatReadableError(error);
   }
  };
  const setStatus=text=>{
   if(typeof status.classList?.remove==='function')status.classList.remove('has-button-error');
   status.textContent=text;
  };
  let updateHelp=()=>{};
  let updateCatalogUI=()=>{};
  const run=fn=>async function(event){
   const target=event?.currentTarget||this;
   const isBtn=Boolean(target&&(target.tagName==='BUTTON'||target.tag==='button'));
   if(isBtn){
    if(target.disabled||target.getAttribute?.('aria-busy')==='true'||target.attrs?.['aria-busy']==='true')return;
    target.disabled=true;
    if(typeof target.setAttribute==='function')target.setAttribute('aria-busy','true');
    const prog=actionProgress.get(target);
    if(prog){
     prog.wrap.hidden=false;
     prog.text.textContent=target.dataset?.progressText||target._progressText||'Processing…';
    }
    for(const [,errEl] of actionErrors){if(errEl){errEl.hidden=true;errEl.textContent='';}}
   }
   try{
    await fn();
   }catch(error){
    if(error.name==='AbortError'||error.code==='STALE_RESPONSE')return;
    const diag=error.diagnostic;
    const hasButtonErr=isBtn&&actionErrors.has(target);
    if(hasButtonErr){
     showButtonError(target,error);
    }
    const isJob=isJobError(error);
    if(typeof window==='undefined'||!hasButtonErr){
     status.textContent=isJob
      ?(error?.diagnostic?.actionable_message||'Setup operation failed; review inputs or inspect the sanitized bug report.')
      :(error.message+(diag?' ['+diag.code+(diag.row?' · row '+diag.row:'')+']':''));
    }
    if(hasButtonErr&&typeof status.classList?.add==='function'){
     status.classList.add('has-button-error');
    }
   }finally{
    if(isBtn){
     target.disabled=false;
     if(typeof target.removeAttribute==='function')target.removeAttribute('aria-busy');
     const prog=actionProgress.get(target);
     if(prog){
      prog.wrap.hidden=true;
      prog.text.textContent='';
     }
    }
    updateHelp();
   }
  };
  const triggerFormInput=el=>{
   if(typeof el.dispatchEvent==="function"){
    try{
     el.dispatchEvent(new (d.defaultView?.Event||Event)("input",{bubbles:true}));
     return;
    }catch(_){}
   }
   if(el.listeners?.input){try{el.listeners.input({target:el});}catch(_){}}
   if(form?.listeners?.input){try{form.listeners.input({target:el});}catch(_){}}
  };
  const button=(label,fn,parent=root)=>{const b=el('button',label,parent);b.type='button';b.onclick=run(fn);return b;};
  const actionButton=(label,fn,parent)=>{
   const wrap=el('div',null,parent);wrap.className='action-step-controls';
   const b=el('button',label,wrap);b.type='button';b.onclick=run(fn);
   const prog=el('span',null,wrap);prog.className='action-progress';prog.hidden=true;prog.setAttribute('role','status');prog.setAttribute('aria-live','polite');
   const spinner=el('span','',prog);spinner.className='progress-spinner';spinner.setAttribute('aria-hidden','true');
   const progText=el('span','',prog);progText.className='action-progress-text';
   actionProgress.set(b,{wrap:prog,spinner,text:progText});
   const err=el('span','',wrap);err.className='action-error';err.hidden=true;err.setAttribute('role','alert');
   actionErrors.set(b,err);
   return b;
  };
  const field=(parent,key,label,{type="text",options=null,value="",required=true,unit="",min=null,max=null,step=null,help="",emptyOption=true}={})=>{
   const wrap=el("label",label+(unit?" ("+unit+") ":" "),parent);wrap.className="field-wrap";
   if(unit){const u=el("span",unit,wrap);u.className="unit-badge";}
   const input=el(options?"select":"input",null,wrap);input.name=key;
   if(options){
    const list=emptyOption?["",...options]:options;
    for(const option of list){
     const val=typeof option==="object"&&option!==null?option.value:option;
     const text=typeof option==="object"&&option!==null?option.label:(option||"Choose…");
     const o=el("option",text,input);o.value=val;o.defaultSelected=val===value;
    }
   }else input.type=type;
   input.value=value;if(!options)input.defaultValue=value;
   if(type==='checkbox'){input.defaultChecked=Boolean(value);input.checked=Boolean(value);}
   if(min!=null)input.min=String(min);
   if(max!=null)input.max=String(max);
   if(step!=null)input.step=String(step);else if(type==="number")input.step="any";
   input.required=required;
   if(help){const h=el("small",help,wrap);h.className="field-help";}
   fields[key]=input;return input;
  };
  const section=(title)=>{const s=el('fieldset');el('legend',title,s);return s;};
  const setupGroup=el('fieldset',null,root);
  setupGroup.className='profile-setup-group';
  setupGroup.id='profileSetupGroup';
  el('legend','Start here · Household profile',setupGroup);
  const setupControls=el('div',null,setupGroup);
  setupControls.className='profile-setup-controls';
  const selector=field(setupControls,'profile','Profile',{options:[],required:false});
  selector.id='householdProfileSelector';
  selector.setAttribute('aria-describedby','profileSetupExplanation');
  const profileName=field(setupControls,'profileName','Profile name (optional)',{required:false});profileName.maxLength=120;
  button('Create profile',async()=>{if(!profileName.reportValidity())return;form.reset();resetSection2();fields.fiscal_mode.value='parameters';syncFiscalModeUI();updateLiveSummary();if(zoneHint)zoneHint.textContent=ZONE_HINTS['CSUD'];yearMenu.setSelectedYears([]);invalidateCoverage();form.inert=true;roofEditor.setRoofs([]);preview.textContent='';await workflow.create(profileName.value.trim());form.inert=false;dirty=false;await refresh();updateCatalogUI(workflow.marketCatalog);profileName.value='';status.textContent='New draft created. Enter all inputs.';onClear();},setupControls);
  const help=el('p','Setup fields are disabled until you create or select a profile so your inputs belong to the right household profile.',setupGroup);
  help.id='profileSetupExplanation';
  help.className='profile-setup-help';
  const form=el('form',null,root);form.inert=true;form.setAttribute('aria-describedby','profileSetupExplanation');form.onsubmit=e=>e.preventDefault();
  updateHelp=()=>{const disabled=Boolean(form.inert);help.hidden=!disabled;help.textContent=disabled?'Setup fields are disabled until you create or select a profile so your inputs belong to the right household profile.':'';};
  updateHelp();
  let selectProfile=null;
  const refresh=async({hydrate=false}={})=>{
   const data=await client.request('/api/profiles');
   selector.textContent='';
   el('option','Select a profile',selector).value='';
   for(const p of (data.profiles||[])){
    const o=el('option',p.name||p.id,selector);
    o.value=p.id;
   }
   const targetId=client.profileId||selector.value||'';
   const profileExists=data.profiles?.some?.(p=>p.id===targetId);
   if(targetId&&profileExists){
    selector.value=targetId;
    if(selectProfile&&(hydrate||form.inert||!workflow.profile||workflow.profile.id!==targetId)){
     await selectProfile(targetId);
    }
   }else if(targetId){
    selector.value=targetId;
   }else{
    selector.value='';
   }
   updateHelp();
  };
  const location=section('1 · Location');form.appendChild(location);
  field(location,'country','Country',{options:['IT'],value:'IT'});field(location,'timezone','Timezone',{options:['Europe/Rome'],value:'Europe/Rome'});
  const latField=field(location,'latitude','Latitude (manual)',{type:'number'});latField.step='any';
  const lonField=field(location,'longitude','Longitude (manual)',{type:'number'});lonField.step='any';
  const handleCoordPaste=event=>{
   const clip=event.clipboardData||(typeof window!=='undefined'?window.clipboardData:null);
   const text=clip?.getData?.('text/plain')||clip?.getData?.('text')||clip?.getData?.('Text')||event.clipboardText||event.text||'';
   const pair=parseCoordinatePair(text);
   if(pair){
    if(typeof event.preventDefault==='function')event.preventDefault();
    fields.latitude.value=pair.lat;
    fields.longitude.value=pair.lon;
    triggerFormInput(fields.latitude);
    triggerFormInput(fields.longitude);
   }
  };
  latField.addEventListener('paste',handleCoordPaste);
  lonField.addEventListener('paste',handleCoordPaste);
  const consumption=section('2 · Gross consumption');consumption.className='consumption-section market-card';form.appendChild(consumption);

  const kindWrap=el('div',null,consumption);kindWrap.className='field-wrap kind-segmented';
  el('span','Consumption Data Resolution',kindWrap).className='field-label';
  const kindGroup=el('div',null,kindWrap);kindGroup.className='segmented-mode-toggle segmented-control';
  kindGroup.setAttribute('role','radiogroup');kindGroup.setAttribute('aria-label','Consumption Data Resolution');
  const kindRadios={};
  const kindLabels={};
  const kindChoices=[
   {value:'annual',label:'Annual Total (1 number from bill)'},
   {value:'monthly',label:'12 Monthly Values (from bill)'},
   {value:'interval',label:'15-Min / Hourly Meter CSV (E-Distribuzione or Custom CSV)'}
  ];
  let _kind='';
  const kindInput=el('input',null,kindGroup);kindInput.type='hidden';kindInput.name='kind';kindInput.defaultValue='';
  Object.defineProperty(kindInput,'value',{
   configurable:true,
   get(){return _kind;},
   set(v){
    _kind=v||'';
    for(const [val,r]of Object.entries(kindRadios)){
     const active=(val===_kind);
     r.checked=active;
     if(kindLabels[val])kindLabels[val].className='toggle-option segmented-option'+(active?' active':'');
    }
    updateModeVisibility();
   }
  });
  fields.kind=kindInput;
  for(const choice of kindChoices){
   const optLabel=el('label',null,kindGroup);optLabel.className='toggle-option segmented-option';
   const radio=el('input',null,optLabel);radio.type='radio';radio.name='kind_radio';radio.value=choice.value;radio.checked=false;
   el('span',choice.label,optLabel);
   kindRadios[choice.value]=radio;
   kindLabels[choice.value]=optLabel;
   radio.onchange=()=>{if(radio.checked){kindInput.value=choice.value;triggerFormInput(kindInput);}};
  }

  const CHECK_NAMES=['gross','existingPv','reconciled','providerConsent','fixed_clock','synthetic_completion'];
  const updateCheckboxesStorage=(key,checked)=>{
   const pid=client.profileId;
   if(!pid)return;
   setProfileChecks(pid,{[key]:Boolean(checked)});
  };

  const initialCheckboxesWrap=el('div',null,consumption);initialCheckboxesWrap.className='gross-checkboxes-wrap';
  field(initialCheckboxesWrap,'gross','These figures represent total household electricity demand (not net grid imports).',{type:'checkbox',help:'Required. Check this if you do not have solar yet, or if your numbers already include self-consumed solar.'});
  field(initialCheckboxesWrap,'existingPv','I already have an existing PV system or battery',{type:'checkbox',required:false});
  const reconciledWrap=el('div',null,consumption);reconciledWrap.className='reconciled-wrap';
  field(reconciledWrap,'reconciled','My meter readings have been reconciled to total gross household load.',{type:'checkbox',required:false});
  const updateReconciledVisibility=()=>{reconciledWrap.hidden=!fields.existingPv.checked;};
  fields.gross.onchange=()=>{updateCheckboxesStorage('gross',fields.gross.checked);triggerFormInput(fields.gross);};
  fields.existingPv.onchange=()=>{updateReconciledVisibility();updateCheckboxesStorage('existingPv',fields.existingPv.checked);triggerFormInput(fields.existingPv);};
  fields.reconciled.onchange=()=>{updateCheckboxesStorage('reconciled',fields.reconciled.checked);triggerFormInput(fields.reconciled);};
  updateReconciledVisibility();

  const coverageWrap=el('div',null,consumption);coverageWrap.className='coverage-wrap';
  field(coverageWrap,'coverage_start','Measurement period start',{type:'date'});
  field(coverageWrap,'coverage_end','Measurement period end (exclusive)',{type:'date'});

  const setContainerDisabled=(container,disabled)=>{
   const visit=n=>{
    if(!n)return;
    const tag=(n.tag||n.tagName||'').toUpperCase();
    if(['INPUT','SELECT','TEXTAREA','BUTTON'].includes(tag))n.disabled=disabled;
    for(const c of Array.from(n.children||[]))visit(c);
   };
   visit(container);
  };
  const clearField=key=>{
   const input=fields[key];
   if(!input)return;
   if(input.type==='checkbox')input.checked=false;
   else input.value='';
  };

  const annualContainer=el('div',null,consumption);annualContainer.className='consumption-annual-container';
  field(annualContainer,'annual','Annual kWh (annual basis only)',{type:'number',required:false});

  const annualCalcWrap=el('fieldset',null,annualContainer);annualCalcWrap.className='annual-calculator';
  el('legend','Annual F1 / F2 / F3 bill calculator (optional)',annualCalcWrap);
  const calcSub=el('p','Enter your annual F1, F2 and F3 totals from your bill. The calculator computes total annual kWh and suggests the matching timing variant.',annualCalcWrap);
  calcSub.className='card-subtitle';
  const calcGrid=el('div',null,annualCalcWrap);calcGrid.className='calc-grid';
  const calcF1Wrap=el('label','Annual F1 (kWh): ',calcGrid);const calcF1=el('input',null,calcF1Wrap);calcF1.type='number';calcF1.min='0';calcF1.step='any';
  const calcF2Wrap=el('label','Annual F2 (kWh): ',calcGrid);const calcF2=el('input',null,calcF2Wrap);calcF2.type='number';calcF2.min='0';calcF2.step='any';
  const calcF3Wrap=el('label','Annual F3 (kWh): ',calcGrid);const calcF3=el('input',null,calcF3Wrap);calcF3.type='number';calcF3.min='0';calcF3.step='any';
  const calcSummary=el('p','',annualCalcWrap);calcSummary.className='calc-summary';
  const applyVariantBtn=el('button','Apply recommended variant',annualCalcWrap);applyVariantBtn.type='button';applyVariantBtn.hidden=true;
  let recommendedVariant='evening';

  const updateAnnualCalculator=()=>{
   const s1=calcF1.value.trim(),s2=calcF2.value.trim(),s3=calcF3.value.trim();
   if(s1!==''&&s2!==''&&s3!==''){
    const values=[s1,s2,s3].map(Number),sum=values.reduce((a,b)=>a+b,0);
    if(values.every(v=>Number.isFinite(v)&&v>=0)&&Number.isFinite(sum)){
     const rec=recommendVariant(...values);
      fields.annual.value=String(sum);
      triggerFormInput(fields.annual);
      if(rec)recommendedVariant=rec.variant;
      calcSummary.textContent=rec?`Total: ${sum} kWh · F1 = ${(rec.f1Share*100).toFixed(1)}% of annual consumption. ${rec.message}`:'Total: 0 kWh';
      applyVariantBtn.hidden=!rec;
      return;
    }
   }
   calcSummary.textContent='';applyVariantBtn.hidden=true;
  };
  calcF1.oninput=updateAnnualCalculator;calcF2.oninput=updateAnnualCalculator;calcF3.oninput=updateAnnualCalculator;
  applyVariantBtn.onclick=()=>{fields.variant.value=recommendedVariant;triggerFormInput(fields.variant);};

  const MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const monthlyContainer=el('div',null,consumption);monthlyContainer.className='consumption-monthly-container';
  const monthlyToggleWrap=el('div',null,monthlyContainer);monthlyToggleWrap.className='field-wrap monthly-mode-wrap';
  el('span','Monthly entry mode: ',monthlyToggleWrap).className='field-label toggle-label';
  let _monthlyMode='A';
  const modeRadios={};
  const modeLabels={};
  const modeToggleGroup=el('div',null,monthlyToggleWrap);modeToggleGroup.className='segmented-mode-toggle monthly-mode-toggle';
  modeToggleGroup.setAttribute('role','radiogroup');modeToggleGroup.setAttribute('aria-label','Monthly entry mode');
  const modeToggleInput=el('input',null,modeToggleGroup);modeToggleInput.type='hidden';modeToggleInput.name='monthly_mode';modeToggleInput.defaultValue='A';
  Object.defineProperty(modeToggleInput,'value',{
   configurable:true,
   get(){return _monthlyMode;},
   set(m){switchMonthlyMode(m);}
  });
  fields.monthly_mode=modeToggleInput;

  for(const [mKey,mLabel]of[['A','Mode A · 12 Monthly totals (single value per month)'],['B','Mode B · Time-of-use bands (F1 / F2 / F3)']]){
   const opt=el('label',null,modeToggleGroup);opt.className='toggle-option segmented-option'+(mKey==='A'?' active':'');
   const r=el('input',null,opt);r.type='radio';r.name='monthly_mode_radio';r.value=mKey;r.checked=(mKey==='A');
   el('span',mLabel,opt);
   modeRadios[mKey]=r;
   modeLabels[mKey]=opt;
   r.onchange=()=>{if(r.checked)switchMonthlyMode(mKey);};
  }

  const modeAContainer=el('div',null,monthlyContainer);modeAContainer.className='monthly-mode-a';
  const modeAGridWrap=el('div',null,modeAContainer);modeAGridWrap.className='monthly-grid-wrap';
  el('p','Monthly totals grid (Jan–Dec kWh, synchronized with raw input below):',modeAGridWrap);
  const modeAGrid=el('div',null,modeAGridWrap);modeAGrid.className='monthly-grid-1x12';
  const gridTotals=[];
  for(let i=0;i<12;i++){
   const cell=el('div',null,modeAGrid);cell.className='month-col';
   el('span',MONTHS[i],cell).className='month-header';
   const inp=el('input',null,cell);inp.type='number';inp.min='0';inp.step='any';inp.name='totals_'+i;
   inp.setAttribute('aria-label',MONTHS[i]+' total consumption kWh');
   gridTotals.push(inp);
   inp.oninput=()=>{
    fields.totals.value=gridTotals.map(x=>x.value.trim()).join(',');
    triggerFormInput(fields.totals);
   };
  }

  const totalsRawWrap=el('div',null,consumption);totalsRawWrap.className='totals-raw-wrap';
  field(totalsRawWrap,'totals','Twelve monthly totals Jan–Dec (kWh, including zero)',{required:false,help:'Enter one kWh total per month, from January to December, separated by commas. Example: 350,320,300,250,200,180,190,200,220,250,300,340'});
  const syncModeAGridFromTotals=()=>{
   const text=fields.totals.value;
   const vals=(text.includes(',')?text.split(','):text.includes(';')?text.split(';'):text.trim().split(/\s+/));
   for(let i=0;i<12;i++)gridTotals[i].value=vals[i]!==undefined?vals[i].trim():'';
  };
  fields.totals.oninput=syncModeAGridFromTotals;

  const modeBContainer=el('div',null,monthlyContainer);modeBContainer.className='monthly-mode-b';
  modeBContainer.hidden=true;
  const modeBGridWrap=el('div',null,modeBContainer);modeBGridWrap.className='bands-grid-wrap';
  el('p','F1 / F2 / F3 monthly bands grid (Jan–Dec kWh):',modeBGridWrap);
  const modeBTable=el('table',null,modeBGridWrap);modeBTable.className='bands-table';
  const thead=el('thead',null,modeBTable);const hrow=el('tr',null,thead);
  el('th','Band',hrow);
  for(let i=0;i<12;i++)el('th',MONTHS[i],hrow);
  const tbody=el('tbody',null,modeBTable);
  const gridBands={monthly_f1:[],monthly_f2:[],monthly_f3:[]};
  const bandDefs=[
   {key:'monthly_f1',label:'F1',title:'F1 (peak): Mon–Fri 08:00–19:00, excluding national holidays.'},
   {key:'monthly_f2',label:'F2',title:'F2 (mid-peak): Mon–Fri 07:00–08:00 and 19:00–23:00; Sat 07:00–23:00, excluding national holidays.'},
   {key:'monthly_f3',label:'F3',title:'F3 (off-peak): Mon–Sat 00:00–07:00 and 23:00–24:00; all day Sunday and national holidays.'}
  ];
  [calcF1Wrap,calcF2Wrap,calcF3Wrap].forEach((wrap,i)=>wrap.setAttribute('title',bandDefs[i].title));
  for(const b of bandDefs){
   const row=el('tr',null,tbody);
   const th=el('th',b.label,row);th.setAttribute('title',b.title);
   for(let i=0;i<12;i++){
    const td=el('td',null,row);
    const inp=el('input',null,td);inp.type='number';inp.min='0';inp.step='any';inp.name=b.key+'_'+i;
    inp.setAttribute('aria-label',MONTHS[i]+' '+b.label+' consumption kWh');
    gridBands[b.key].push(inp);
    inp.oninput=()=>{
     fields[b.key].value=gridBands[b.key].map(x=>x.value.trim()).join(',');
     triggerFormInput(fields[b.key]);
     updateModeBSumsAndShare();
    };
   }
  }
  const sumRow=el('tr',null,tbody);sumRow.className='sums-row';
  el('th','Month sum',sumRow);
  const sumCells=[];
  for(let i=0;i<12;i++){
   const td=el('td','-',sumRow);td.className='sum-cell';
   sumCells.push(td);
  }
  const f1ShareInfo=el('p','',modeBContainer);f1ShareInfo.className='f1-share-info';

  const modeBRawWrap=el('div',null,modeBContainer);modeBRawWrap.className='bands-raw-wrap';
  field(modeBRawWrap,'monthly_f1','F1 twelve monthly totals (kWh)',{required:false,help:'Comma-separated Jan–Dec totals for band F1'});
  field(modeBRawWrap,'monthly_f2','F2 twelve monthly totals (kWh)',{required:false,help:'Comma-separated Jan–Dec totals for band F2'});
  field(modeBRawWrap,'monthly_f3','F3 twelve monthly totals (kWh)',{required:false,help:'Comma-separated Jan–Dec totals for band F3'});

  const syncModeBGridFromRaw=()=>{
   for(const b of bandDefs){
    const text=fields[b.key].value;
    const vals=(text.includes(',')?text.split(','):text.includes(';')?text.split(';'):text.trim().split(/\s+/));
    for(let i=0;i<12;i++)gridBands[b.key][i].value=vals[i]!==undefined?vals[i].trim():'';
   }
  };
  fields.monthly_f1.oninput=()=>{syncModeBGridFromRaw();updateModeBSumsAndShare();};
  fields.monthly_f2.oninput=()=>{syncModeBGridFromRaw();updateModeBSumsAndShare();};
  fields.monthly_f3.oninput=()=>{syncModeBGridFromRaw();updateModeBSumsAndShare();};

  const updateModeBSumsAndShare=()=>{
   let sumF1=0,sumF2=0,sumF3=0,validAll=true;
   for(let i=0;i<12;i++){
    const s1=gridBands.monthly_f1[i].value.trim(),s2=gridBands.monthly_f2[i].value.trim(),s3=gridBands.monthly_f3[i].value.trim();
    if(s1!==''&&s2!==''&&s3!==''){
     const v1=Number(s1),v2=Number(s2),v3=Number(s3);
     if(Number.isFinite(v1)&&v1>=0&&Number.isFinite(v2)&&v2>=0&&Number.isFinite(v3)&&v3>=0){
      const monthSum=v1+v2+v3;
      if(Number.isFinite(monthSum)){
       sumCells[i].textContent=String(Number(monthSum.toPrecision(12)));
       sumF1+=v1;sumF2+=v2;sumF3+=v3;
      }else{sumCells[i].textContent='-';validAll=false;}
     }else{sumCells[i].textContent='-';validAll=false;}
    }else{sumCells[i].textContent='-';validAll=false;}
   }
   const totalSum=sumF1+sumF2+sumF3;
   if(validAll&&totalSum>0){
   const f1Pct=((sumF1/totalSum)*100).toFixed(1);
    f1ShareInfo.textContent=`F1 = ${f1Pct}% of annual consumption.`;
   }else if(validAll&&totalSum===0){
    f1ShareInfo.textContent='F1 = 0% of annual consumption.';
   }else{
    f1ShareInfo.textContent='';
   }
  };

  const clearBands=()=>{
   for(const b of bandDefs){
    for(let i=0;i<12;i++)gridBands[b.key][i].value='';
    fields[b.key].value='';
   }
   for(let i=0;i<12;i++)sumCells[i].textContent='-';
   f1ShareInfo.textContent='';
  };

  const switchMonthlyMode=(targetMode,silent=false)=>{
   if(targetMode===_monthlyMode)return;
   if(targetMode==='B'){
    clearBands();
    _monthlyMode='B';
   }else{
    if(!silent){
     let canDerive=false,derived=null;
     try{
      const f1Vals=numbers(fields.monthly_f1.value);
      const f2Vals=numbers(fields.monthly_f2.value);
      const f3Vals=numbers(fields.monthly_f3.value);
      const sums=f1Vals.map((v,i)=>v+f2Vals[i]+f3Vals[i]);
      if(sums.every(Number.isFinite)){
       derived=sums;
       canDerive=true;
      }
     }catch(_){}
     if(canDerive&&derived){
      fields.totals.value=derived.join(',');
      syncModeAGridFromTotals();
      triggerFormInput(fields.totals);
     }else{
      fields.totals.value='';
      syncModeAGridFromTotals();
      triggerFormInput(fields.totals);
     }
    }
    clearBands();
    _monthlyMode='A';
   }
   updateMonthlyModeUI();
   if(!silent)triggerFormInput(fields.monthly_mode);
  };

  const updateMonthlyModeUI=()=>{
   const isA=(_monthlyMode==='A');
   const isB=(_monthlyMode==='B');
   if(modeRadios.A)modeRadios.A.checked=isA;
   if(modeRadios.B)modeRadios.B.checked=isB;
   if(modeLabels.A)modeLabels.A.className='toggle-option segmented-option'+(isA?' active':'');
   if(modeLabels.B)modeLabels.B.className='toggle-option segmented-option'+(isB?' active':'');
   modeAContainer.hidden=!isA;
   setContainerDisabled(modeAContainer,!isA);
   modeBContainer.hidden=!isB;
   setContainerDisabled(modeBContainer,!isB);
   const showTotals=(_kind==='interval'||isA);
   totalsRawWrap.hidden=!showTotals;
   setContainerDisabled(totalsRawWrap,!showTotals);
   variantModeBHelper.hidden=!(_kind==='monthly'&&isB);
  };

  const csvContainer=el('div',null,consumption);csvContainer.className='consumption-csv-container';
  const csvFileField=field(csvContainer,'csv','CSV file',{type:'file',required:false});
  csvFileField.accept='.csv';
  csvFileField.multiple=true;
  const csvDrawer=el('details',null,csvContainer);csvDrawer.className='csv-advanced-drawer';
  el('summary','Advanced CSV Format Settings',csvDrawer);
  field(csvDrawer,'timestamp','Timestamp column',{required:false});field(csvDrawer,'energy','Energy / interval-average power column',{required:false});
  field(csvDrawer,'fold','Optional DST fold column (0/1)',{required:false});
  field(csvDrawer,'unit','CSV unit',{options:['kWh','Wh','average_kW'],required:false});field(csvDrawer,'timestamp_convention','CSV timestamps',{options:['start','end'],required:false});
  field(csvDrawer,'delimiter','CSV delimiter',{options:[',',';','\t'],required:false});field(csvDrawer,'decimal_separator','Decimal separator',{options:['.',','],required:false});field(csvDrawer,'duration_seconds','Interval seconds',{options:['900','3600'],required:false});field(csvDrawer,'csv_start','CSV declared coverage start (ISO timestamp with offset)',{required:false});field(csvDrawer,'csv_end','CSV declared coverage end, exclusive (ISO timestamp with offset)',{required:false});field(csvDrawer,'synthetic_completion','Explicit synthetic timing completion for partial interval coverage',{type:'checkbox',required:false});field(csvDrawer,'fixed_clock','Explicit fixed UTC+1 clock (no civil DST)',{type:'checkbox',required:false});
  fields.fixed_clock.onchange=()=>{updateCheckboxesStorage('fixed_clock',fields.fixed_clock.checked);triggerFormInput(fields.fixed_clock);};
  fields.synthetic_completion.onchange=()=>{updateCheckboxesStorage('synthetic_completion',fields.synthetic_completion.checked);triggerFormInput(fields.synthetic_completion);};

  const csvTools=typeof ConsumptionCsv!=='undefined'?ConsumptionCsv:require('./consumption-csv.js');
  const csvImport=csvTools.bindInput({fields,container:csvContainer,form,onChange:triggerFormInput,
   reportError:error=>{status.textContent=error.message;},getScope:()=>client.profileId});
  const variantWrap=el('div',null,consumption);variantWrap.className='variant-wrap';
  field(variantWrap,'variant','Daily Consumption Profile (When do you use the most power?)',{
   options:[
    {value:'evening',label:'Evening Peak (Away during work hours, peak 18:00–23:00)'},
    {value:'daytime',label:'Daytime Peak (Home during the day, peak 09:00–17:00)'},
    {value:'flat',label:'Flat / Continuous (Uniform daytime and evening usage)'}
   ],
   value:'evening'
  });
  const variantModeBHelper=el('p','Sets the hourly shape within each band. Monthly band totals come from your bill.',variantWrap);
  variantModeBHelper.className='field-help mode-b-helper';
  variantModeBHelper.hidden=true;
  field(variantWrap,'ack','I acknowledge modeled timing, synthetic coverage and annual-only timing uncertainty',{type:'checkbox'});
  const varDisclaimer=el('p','Monthly/partial CSV: provide all twelve totals. Annual-only: energy follows calendar days; timing is uncertain. Synthetic variants represent sensitivity, not confidence intervals. Repeated DST hours need UTC offsets/fold; nonexistent spring times are rejected. Fixed-clock sources do not recover civil DST truth.',variantWrap);
  varDisclaimer.className='gross-explanation';

  const updateModeVisibility=()=>{
   const isAnnual=(_kind==='annual');
   const isMonthly=(_kind==='monthly');
   const isInterval=(_kind==='interval');
   const hasSelection=isAnnual||isMonthly||isInterval;

   annualContainer.hidden=!isAnnual;
   setContainerDisabled(annualContainer,!isAnnual);

   monthlyContainer.hidden=!isMonthly;
   setContainerDisabled(monthlyContainer,!isMonthly);

   csvContainer.hidden=!isInterval;
   setContainerDisabled(csvContainer,!isInterval);

   coverageWrap.hidden=!hasSelection;
   setContainerDisabled(coverageWrap,!hasSelection);
   fields.coverage_start.required=hasSelection;
   fields.coverage_end.required=hasSelection;

   variantWrap.hidden=!hasSelection;
   setContainerDisabled(variantWrap,!hasSelection);
   fields.ack.required=hasSelection;

   if(isMonthly){
    updateMonthlyModeUI();
   }else if(isInterval){
    totalsRawWrap.hidden=false;
    setContainerDisabled(totalsRawWrap,false);
    variantModeBHelper.hidden=true;
   }else{
    totalsRawWrap.hidden=true;
    setContainerDisabled(totalsRawWrap,true);
    variantModeBHelper.hidden=true;
   }
  };

  const resetSection2=()=>{
   _kind='';
   for(const r of Object.values(kindRadios))r.checked=false;
   for(const l of Object.values(kindLabels))l.className='toggle-option segmented-option';
   _monthlyMode='A';
   if(modeLabels.A)modeLabels.A.className='toggle-option segmented-option active';
   if(modeLabels.B)modeLabels.B.className='toggle-option segmented-option';
   clearBands();
   for(let i=0;i<12;i++)gridTotals[i].value='';
   calcF1.value='';calcF2.value='';calcF3.value='';calcSummary.textContent='';applyVariantBtn.hidden=true;
   for(const k of CHECK_NAMES){if(fields[k])fields[k].checked=false;}
   updateReconciledVisibility();
   updateModeVisibility();
   resetThermalSection();
  };
  form.addEventListener('reset',()=>{
   if(typeof queueMicrotask==='function')queueMicrotask(resetSection2);
   else setTimeout(resetSection2,0);
  });
  updateModeVisibility();
  const roofs=section('3 · Roof planes');form.appendChild(roofs);
  el('p','Manual entry is always available. Compass: N=0°, E=90°, S=180°, W=270°. Capacity is a user limit, not panel fit or structural/electrical advice. Coarse shading is uniform additional attenuation, not measured shadow geometry.',roofs);
  el('p','Optional local tools/google_solar_helper.py uses your own Google account/key outside this app. No keys or Google responses are accepted here. Cached content has retrieval/expiry restrictions; persistent inputs must be independently entered and verified. Do not assume derived profiles evade retention limits.',roofs);
  const roofRoot=el('div',null,roofs),roofEditor=new RoofListEditor(roofRoot,{onChange:()=>{client.invalidate();workflow.status='draft';onClear();}});
  const thermal=section('4 · Thermal end uses and planned additions');form.appendChild(thermal);
  el('p','Leave unchanged existing electric demand included in gross consumption. Replacement of embedded electrical demand requires separated hourly data. Choose measured carrier input or useful heat, then enter conversion and efficiency/COP; no personalized defaults.',thermal);
  const thermalCardsWrap=el('div',null,thermal);thermalCardsWrap.className='thermal-cards-wrap';
  const THERMAL_CARDS=[
   {id:'space_heat',label:'Space Heating (Winter Heating — Boiler, Heat Pump, Pellet)',subtitle:'Winter space heating. Keep unchanged if the existing system already meets this demand.'},
   {id:'dhw',label:'Domestic Hot Water (DHW — Showers & Taps)',subtitle:'Hot water for showers and taps.'},
   {id:'cooking',label:'Cooking (Gas Stove vs. Induction Hob)',subtitle:'Cooking hob and oven demand.'}
  ];
  const CARRIER_OPTIONS=[
   {value:'gas',label:'Natural Gas (Smc)'},
   {value:'electricity',label:'Electricity (kWh)'},
   {value:'biomass',label:'Biomass / Pellets (kg)'},
   {value:'district',label:'District Heating (kWh)'},
   {value:'none',label:'None'}
  ];
  const ACTION_OPTIONS=[
   {value:'unchanged',label:'Keep unchanged'},
   {value:'add',label:'Add new electric load'},
   {value:'replace',label:'Replace with electric (e.g., Heat Pump / Induction)'}
  ];
  const EV_ACTION_OPTIONS=[
   {value:'unchanged',label:'No change'},
   {value:'add',label:'Add new EV home charging'},
   {value:'replace',label:'Replace'}
  ];
  const COOLING_ACTION_OPTIONS=[
   {value:'unchanged',label:'No change'},
   {value:'add',label:'Add new cooling load'},
   {value:'replace',label:'Replace'}
  ];
  const energyMode={},costMode={},lastCarrier={},thermalCards={},electricCards={};
  const wrapOf=key=>{const input=fields[key];return input&&input.parentNode?input.parentNode:null;};
  const factorPresets={
   gas:[['Standard Boiler: 0.85',0.85],['Condensing Boiler: 0.92',0.92]],
   biomass:[],
   district:[],
   electricity:[['Heat Pump COP: 3.5',3.5]],
   none:[]
  };
  const conversionPresets={gas:[['Standard Gas PCS: 10.7 kWh/Smc',10.7]],biomass:[],district:[],electricity:[],none:[]};
  const factorLabelFor=carrier=>carrier==='electricity'?'Seasonal COP (Coefficient of Performance, > 0)':'System Efficiency (0–1)';
  const factorHintFor=carrier=>carrier==='electricity'?'Seasonal COP must be greater than 0; 3.5 is typical.':(carrier==='gas'||carrier==='biomass'||carrier==='district'?'System efficiency must be between 0 and 1.':'');
  const conversionHintFor=carrier=>carrier==='gas'?'Natural gas PCS: kWh per Smc (for example 10.7).':(carrier==='biomass'?'Biomass LHV: kWh per kg.':(carrier==='electricity'||carrier==='district'?'Conversion is fixed at 1 for electricity and district heating.':''));
  const syncThermalCard=id=>{
   const card=thermalCards[id];if(!card)return;
   const enabled=check(id+'_enabled'),carrier=val(id+'_carrier');
   const electric=carrier==='electricity',none=carrier==='none',measured=energyMode[id]!=='useful',convertible=carrier==='gas'||carrier==='biomass';
   card.status.textContent=enabled?'Included':'Not included';
   card.controls.hidden=!enabled;
   setContainerDisabled(card.controls,!enabled);
   for(const [mode,radio] of Object.entries(card.energyRadios)){
    const active=energyMode[id]===mode;radio.checked=active;
    card.energyLabels[mode].className='toggle-option'+(active?' active':'');
   }
   for(const [mode,radio] of Object.entries(card.costRadios)){
    const active=costMode[id]===mode;radio.checked=active;
    card.costLabels[mode].className='toggle-option'+(active?' active':'');
   }
   const heatWrap=wrapOf(id+'_heat'),inputWrap=wrapOf(id+'_input'),conversionWrap=wrapOf(id+'_conversion'),includedWrap=wrapOf(id+'_included'),fixedWrap=wrapOf(id+'_fixed'),variableWrap=wrapOf(id+'_variable'),billWrap=wrapOf(id+'_bill');
   if(heatWrap)heatWrap.hidden=measured||none;
   if(inputWrap)inputWrap.hidden=!measured||none;
   if(conversionWrap)conversionWrap.hidden=!measured||none;
   if(includedWrap)includedWrap.hidden=!electric;
   if(fixedWrap)fixedWrap.hidden=costMode[id]==='bill';
   if(variableWrap)variableWrap.hidden=costMode[id]==='bill';
   if(billWrap)billWrap.hidden=costMode[id]!=='bill';
   if(carrier==='electricity'||carrier==='district')fields[id+'_conversion'].value='1';
   fields[id+'_heat'].disabled=!enabled||measured||none;
   fields[id+'_input'].disabled=!enabled||!measured||none;
   fields[id+'_conversion'].disabled=!enabled||!measured||none||!convertible;
   fields[id+'_included'].disabled=!enabled||!electric;
   fields[id+'_fixed'].disabled=!enabled||costMode[id]==='bill';
   fields[id+'_variable'].disabled=!enabled||costMode[id]==='bill';
   fields[id+'_bill'].disabled=!enabled||costMode[id]!=='bill';
   fields[id+'_factor'].disabled=!enabled||!measured||none;
   if(card.factorWrap)card.factorWrap.hidden=!measured||none;
   card.factorPresets.hidden=!measured||none||!(factorPresets[carrier]||[]).length;
   card.conversionPresets.hidden=!measured||none||!(conversionPresets[carrier]||[]).length;
   card.conversionHint.hidden=!measured||none;
   card.energyWrap.hidden=none;
   card.factorLabel.textContent=factorLabelFor(carrier);
   card.factorHint.textContent=factorHintFor(carrier);
   card.conversionHint.textContent=conversionHintFor(carrier);
   renderFactorPresets(id);
  };
  const renderFactorPresets=id=>{
   const card=thermalCards[id];if(!card)return;
   const carrier=val(id+'_carrier');
   if(card.renderedCarrier===carrier)return;
   card.renderedCarrier=carrier;
   const host=card.factorPresets;host.textContent='';
   const list=factorPresets[carrier]||[];
   if(list.length){
    el('span','Illustrative presets: ',host).className='preset-label';
    for(const [label,value] of list){
     const btn=el('button',label,host);btn.type='button';btn.className='preset-btn';
     btn.onclick=()=>{fields[id+'_factor'].value=String(value);triggerFormInput(fields[id+'_factor']);};
    }
   }
   const convHost=card.conversionPresets;if(!convHost)return;
   convHost.textContent='';
   const convList=conversionPresets[carrier]||[];
   if(!convList.length)return;
   el('span','Illustrative conversion: ',convHost).className='preset-label';
   for(const [label,value] of convList){
    const btn=el('button',label,convHost);btn.type='button';btn.className='preset-btn';
    btn.onclick=()=>{fields[id+'_conversion'].value=String(value);triggerFormInput(fields[id+'_conversion']);};
   }
  };
  const setEnergyMode=(id,mode)=>{
   if(energyMode[id]===mode)return;
   energyMode[id]=mode;
   if(mode==='useful'){clearField(id+'_input');clearField(id+'_conversion');}
   else clearField(id+'_heat');
   syncThermalCard(id);
   triggerFormInput(fields[id+'_heat']);
  };
  const setCostMode=(id,mode)=>{
   if(costMode[id]===mode)return;
   costMode[id]=mode;
   if(mode==='bill'){clearField(id+'_fixed');clearField(id+'_variable');}
   else clearField(id+'_bill');
   syncThermalCard(id);
   triggerFormInput(fields[id+'_fixed']);
  };
  const syncElectricCard=id=>{
   const card=electricCards[id];if(!card)return;
   const action=val(id+'_action'),add=action==='add';
   card.status.textContent=add?'New load':action==='replace'?'Replacement':'Unchanged';
   const incrementalWrap=wrapOf(id+'_incremental'),includedWrap=wrapOf(id+'_included');
   if(incrementalWrap)incrementalWrap.hidden=id==='ev'&&!add;
   if(includedWrap)includedWrap.hidden=false;
   fields[id+'_incremental'].disabled=!add;
   if(add)fields[id+'_included'].checked=false;
   fields[id+'_included'].disabled=add;
   card.hint.textContent=add?'Enter only demand that is not already in gross consumption.':action==='replace'?'Replacing existing EV/cooling demand requires a separated hourly series; saving will block until it is provided.':'Leave unchanged if this demand is already in gross consumption.';
  };
  function resetThermalSection(){
   for(const meta of THERMAL_CARDS){
    energyMode[meta.id]='measured';costMode[meta.id]='explicit';lastCarrier[meta.id]='';
    if(thermalCards[meta.id])thermalCards[meta.id].renderedCarrier=null;
   }
   syncThermalSection();
  }
  const syncThermalSection=()=>{for(const meta of THERMAL_CARDS)syncThermalCard(meta.id);for(const id of ['ev','cooling'])syncElectricCard(id);};

  for(const meta of THERMAL_CARDS){
   const id=meta.id;
   const card=el('section',null,thermalCardsWrap);card.className='market-card thermal-card';
   const header=el('div',null,card);header.className='thermal-card-header';
   const heading=el('div',null,header);heading.className='thermal-card-heading';
   el('strong',meta.label,heading);
   const status=el('span','',heading);status.className='thermal-card-status';
   const enableWrap=el('div',null,header);enableWrap.className='thermal-card-toggle';
   field(enableWrap,id+'_enabled','Include this end use',{type:'checkbox',required:false});
   const controls=el('div',null,card);controls.className='thermal-card-body';
   const sub=el('p',meta.subtitle,controls);sub.className='card-subtitle';
   const setupGrid=el('div',null,controls);setupGrid.className='card-grid';
   field(setupGrid,id+'_carrier','Carrier',{options:CARRIER_OPTIONS,required:false});
   field(setupGrid,id+'_action','Planned action',{options:ACTION_OPTIONS,value:'unchanged',required:false,emptyOption:false});
   field(setupGrid,id+'_included','Already included in gross electrical load',{type:'checkbox',required:false});
   const energyWrap=el('div',null,controls);energyWrap.className='thermal-method-group';
   el('span','Energy input method',energyWrap).className='field-label';
   const energyGroup=el('div',null,energyWrap);energyGroup.className='segmented-mode-toggle';energyGroup.setAttribute('role','radiogroup');energyGroup.setAttribute('aria-label',meta.label+' energy input method');
   const energyRadios={},energyLabels={};
   for(const [mode,label] of [['measured','Measured carrier input (bill)'],['useful','Useful heat (monthly kWh)']]){
    const lbl=el('label',null,energyGroup);lbl.className='toggle-option'+(mode==='measured'?' active':'');
    const radio=el('input',null,lbl);radio.type='radio';radio.name=id+'_energy_method';radio.value=mode;radio.checked=(mode==='measured');
    el('span',label,lbl);
    energyRadios[mode]=radio;energyLabels[mode]=lbl;
    radio.onchange=()=>{if(radio.checked)setEnergyMode(id,mode);};
   }
   const energyFields=el('div',null,energyWrap);energyFields.className='card-grid';
   field(energyFields,id+'_heat','Monthly useful heat (kWh, Jan–Dec)',{required:false,help:'One useful-heat total per month, January to December, separated by commas.'});
   field(energyFields,id+'_input','Monthly measured carrier input (Smc / kg / kWh, Jan–Dec)',{required:false,help:'One measured quantity per month, January to December, separated by commas.'});
   field(energyFields,id+'_conversion','Carrier conversion (kWh per measured unit)',{type:'number',required:false,help:'Fixed at 1 for electricity and district heating; gas PCS or biomass LHV otherwise.'});
   const factorWrap=el('label',null,energyFields);factorWrap.className='field-wrap';
   const factorLabel=el('span','System Efficiency (0–1)',factorWrap);factorLabel.className='field-label';
   const factorInput=el('input',null,factorWrap);factorInput.type='number';factorInput.name=id+'_factor';factorInput.min='0';factorInput.step='any';factorInput.required=false;factorInput.value='';factorInput.defaultValue='';
   const factorHint=el('small','',factorWrap);factorHint.className='field-help';
   fields[id+'_factor']=factorInput;
   const factorPresetsEl=el('div',null,energyWrap);factorPresetsEl.className='preset-group';
   const conversionPresetsEl=el('div',null,energyWrap);conversionPresetsEl.className='preset-group';
   const conversionHint=el('p','',energyWrap);conversionHint.className='field-help';
   const costWrap=el('div',null,controls);costWrap.className='thermal-method-group';
   el('span','Carrier cost method',costWrap).className='field-label';
   const costGroup=el('div',null,costWrap);costGroup.className='segmented-mode-toggle';costGroup.setAttribute('role','radiogroup');costGroup.setAttribute('aria-label',meta.label+' carrier cost method');
   const costRadios={},costLabels={};
   for(const [mode,label] of [['explicit','Itemized fixed + variable'],['bill','Total annual bill']]){
    const lbl=el('label',null,costGroup);lbl.className='toggle-option'+(mode==='explicit'?' active':'');
    const radio=el('input',null,lbl);radio.type='radio';radio.name=id+'_cost_method';radio.value=mode;radio.checked=(mode==='explicit');
    el('span',label,lbl);
    costRadios[mode]=radio;costLabels[mode]=lbl;
    radio.onchange=()=>{if(radio.checked)setCostMode(id,mode);};
   }
   const costFields=el('div',null,costWrap);costFields.className='card-grid';
   field(costFields,id+'_fixed','Annual fixed carrier cost (€)',{type:'number',required:false});
   field(costFields,id+'_variable','Annual variable carrier cost (€)',{type:'number',required:false});
   field(costFields,id+'_bill','Total annual carrier bill (€)',{type:'number',required:false});
   thermalCards[id]={card,header,status,controls,energyWrap,energyRadios,energyLabels,costRadios,costLabels,factorLabel,factorHint,factorWrap,factorPresets:factorPresetsEl,conversionPresets:conversionPresetsEl,conversionHint,renderedCarrier:null};
   fields[id+'_carrier'].onchange=()=>{
    const next=val(id+'_carrier');
    if(next!==lastCarrier[id])lastCarrier[id]=next;
    if(next!=='electricity')fields[id+'_included'].checked=false;
    syncThermalCard(id);triggerFormInput(fields[id+'_carrier']);
   };
   fields[id+'_action'].onchange=()=>{syncThermalCard(id);triggerFormInput(fields[id+'_action']);};
   fields[id+'_enabled'].onchange=()=>{syncThermalCard(id);triggerFormInput(fields[id+'_enabled']);};
   lastCarrier[id]='';
   syncThermalCard(id);
  }

  const electricGrid=el('div',null,thermalCardsWrap);electricGrid.className='electric-cards-grid';
  const ELECTRIC_CARDS=[
   {id:'ev',label:'Electric Vehicle (EV) Home Charging',subtitle:'Home charging demand. Add only energy that is not already in gross consumption.',presets:[['5,000 km/yr — 900 kWh/yr',900],['10,000 km/yr — 1,800 kWh/yr',1800],['15,000 km/yr — 2,700 kWh/yr',2700]]},
   {id:'cooling',label:'Space Cooling (Air Conditioning)',subtitle:'Air-conditioning demand. Add only energy that is not already in gross consumption.',presets:[['Small: 300 kWh/yr',300],['Medium: 600 kWh/yr',600],['Large: 1,200 kWh/yr',1200]]}
  ];
  for(const meta of ELECTRIC_CARDS){
   const id=meta.id;
   const card=el('section',null,electricGrid);card.className='market-card thermal-card';
   const header=el('div',null,card);header.className='thermal-card-header';
   const heading=el('div',null,header);heading.className='thermal-card-heading';
   el('strong',meta.label,heading);
   const status=el('span','',heading);status.className='thermal-card-status';
   const controls=el('div',null,card);controls.className='thermal-card-body';
   const sub=el('p',meta.subtitle,controls);sub.className='card-subtitle';
   const fieldsGrid=el('div',null,controls);fieldsGrid.className='card-grid';
   field(fieldsGrid,id+'_included','Already included in gross electrical load',{type:'checkbox',required:false});
   field(fieldsGrid,id+'_action','Planned action',{options:id==='ev'?EV_ACTION_OPTIONS:COOLING_ACTION_OPTIONS,value:'unchanged',required:false,emptyOption:false});
   field(fieldsGrid,id+'_incremental','Additional annual electricity (kWh)',{type:'number',required:false});
   if(id==='cooling')field(fieldsGrid,'baseline_cooling_kwh','Existing cooling annual electricity (kWh, zero allowed)',{type:'number',required:false,help:'Used to report existing cooling demand. Required when cooling is included, added or replaced; enter 0 if none. Otherwise, a blank is treated as 0.'});
   const presets=el('div',null,controls);presets.className='preset-group';
   el('span','Illustrative additions: ',presets).className='preset-label';
   for(const [label,value] of meta.presets){
    const btn=el('button',label,presets);btn.type='button';btn.className='preset-btn';
    btn.onclick=()=>{
     if(val(id+'_action')!=='add'){fields[id+'_action'].value='add';triggerFormInput(fields[id+'_action']);}
     fields[id+'_incremental'].value=String(value);triggerFormInput(fields[id+'_incremental']);syncElectricCard(id);
    };
   }
   const hint=el('p','',controls);hint.className='field-help';
   electricCards[id]={card,status,controls,presets,hint};
   fields[id+'_action'].onchange=()=>{syncElectricCard(id);triggerFormInput(fields[id+'_action']);};
   syncElectricCard(id);
  }
  syncThermalSection();
  const market=section("5 · Parametri di Mercato e Fisco / Market and Fiscal Confirmation");market.className="market-section";form.appendChild(market);
  const applyPreset=updates=>{
   for(const [k,v] of Object.entries(updates)){if(fields[k]){fields[k].value=String(v);triggerFormInput(fields[k]);}}
   dirty=true;invalidateCoverage();client.invalidate();workflow.status="draft";onClear();updateLiveSummary();
  };

  // Card 5A
  const card5a=el("fieldset",null,market);card5a.className="market-card card-5a";
  el("legend","5A · Offerta Luce e Imposte in Bolletta / Current Electricity Bill",card5a);
  const sub5a=el("p","Prendi lo Scontrino dell'Energia della tua ultima bolletta luce per compilare i costi variabili al kWh. / Use the Energy Summary on your latest electricity bill to enter variable costs per kWh.",card5a);sub5a.className="card-subtitle";
  const presets5a=el("div",null,card5a);presets5a.className="preset-group";
  el("span","Preimpostazioni / Presets: ",presets5a).className="preset-label";
  const btnPrimaCasa=el("button","Prima Casa (0.0227 €/kWh, 10% IVA)",presets5a);btnPrimaCasa.type="button";btnPrimaCasa.className="preset-btn";btnPrimaCasa.onclick=()=>applyPreset({electric_excise:"0.0227",electric_vat:"10"});
  const btnSecondaCasa=el("button","Seconda Casa (0.0227 €/kWh, 10% IVA)",presets5a);btnSecondaCasa.type="button";btnSecondaCasa.className="preset-btn";btnSecondaCasa.onclick=()=>applyPreset({electric_excise:"0.0227",electric_vat:"10"});
  const btnAltriUsi=el("button","Altri Usi / P.IVA (0.0125 €/kWh, 22% IVA)",presets5a);btnAltriUsi.type="button";btnAltriUsi.className="preset-btn";btnAltriUsi.onclick=()=>applyPreset({electric_excise:"0.0125",electric_vat:"22"});
  const grid5a=el("div",null,card5a);grid5a.className="card-grid";
  field(grid5a,"retail_commodity","Quota Energia (Materia Energia variabile) / Energy Commodity (variable)",{type:"number",value:"0.135",unit:"€/kWh",help:"Spesa per l'acquisto dell'energia / Energy purchase cost"});
  field(grid5a,"retail_regulated","Oneri di Rete e Sistema variabili / Variable Network & System Charges",{type:"number",value:"0.045",unit:"€/kWh",help:"Trasporto e gestione contatore + oneri generali / Transport & system charges"});
  field(grid5a,"electric_excise","Imposta Erariale di Consumo (Accisa) / Electricity Excise Tax",{type:"number",value:"0.0227",unit:"€/kWh",help:"Accisa statale applicata ai consumi / State excise duty"});
  field(grid5a,"electric_vat","IVA Elettricità / Electricity VAT",{type:"number",min:0,max:100,value:"10",unit:"%",help:"Aliquota IVA applicata alla bolletta / VAT rate applied to electricity"});
  const hint5a=el("p","Quota per consumi -> Spesa per la vendita vs Spesa per la rete e gli oneri generali; Accise e IVA. / Consumption charges -> Energy supply vs Network & general system charges; Excise tax and VAT.",card5a);hint5a.className="bill-hint";
  const summaryWrap=el("div",null,card5a);summaryWrap.className="live-electricity-summary";summaryWrap.setAttribute("role","status");summaryWrap.setAttribute("aria-live","polite");
  const summaryVal=el("div","",summaryWrap);summaryVal.className="summary-value";
  const summaryExpl=el("div","Risparmio per ogni kWh autoconsumato: evita l'acquisto lordo dalla rete a questo costo variabile stimato. / Savings per self-consumed kWh: avoids gross grid purchases at this estimated variable rate.",summaryWrap);summaryExpl.className="summary-explanation";
  function updateLiveSummary(){
   const commodity=Number(val("retail_commodity"))||0,regulated=Number(val("retail_regulated"))||0,excise=Number(val("electric_excise"))||0,vat=(Number(val("electric_vat"))||0)/100;
   summaryVal.textContent="Costo variabile lordo evitato / Avoided gross variable cost: "+((commodity+regulated+excise)*(1+vat)).toFixed(4)+" €/kWh";
  }

  // Card 5B
  const card5b=el("fieldset",null,market);card5b.className="market-card card-5b";
  el("legend","5B · Bolletta Gas Metano / Current Gas Bill",card5b);
  const sub5b=el("p","Scegli tra stima da spesa annua o aliquote dettagliate per scaglione di consumo. / Choose between annual bill estimate or detailed slab rates.",card5b);sub5b.className="card-subtitle";
  const modeToggle=el("div",null,card5b);modeToggle.className="segmented-mode-toggle";modeToggle.setAttribute("role","radiogroup");modeToggle.setAttribute("aria-label","Modalità Fiscale Gas / Gas Fiscal Mode");
  const lblParams=el("label",null,modeToggle);lblParams.className="toggle-option active";
  const radioParams=el("input",null,lblParams);radioParams.type="radio";radioParams.name="gas_fiscal_mode_choice";radioParams.value="parameters";radioParams.checked=true;radioParams.defaultChecked=true;
  el("span","Aliquote Dettagliate / Detailed Rates",lblParams);
  const lblAnnual=el("label",null,modeToggle);lblAnnual.className="toggle-option";
  const radioAnnual=el("input",null,lblAnnual);radioAnnual.type="radio";radioAnnual.name="gas_fiscal_mode_choice";radioAnnual.value="annual_bill";radioAnnual.checked=false;radioAnnual.defaultChecked=false;
  el("span","Spesa Annua Totale / Annual Bill",lblAnnual);
  const hiddenMode=el("input",null,card5b);hiddenMode.type="hidden";hiddenMode.name="fiscal_mode";hiddenMode.value="parameters";hiddenMode.defaultValue="parameters";fields["fiscal_mode"]=hiddenMode;
  const detailedGasKeys=["gas_excise_0_120","gas_excise_120_480","gas_excise_480_1560","gas_excise_over_1560","gas_regional_0_120","gas_regional_over_120","gas_vat_reduced","gas_vat_standard"];
  const syncFiscalModeUI=()=>{
   const m=(hiddenMode.value==="annual_bill")?"annual_bill":"parameters";
   radioParams.checked=(m==="parameters");radioAnnual.checked=(m==="annual_bill");
   lblParams.className="toggle-option"+(m==="parameters"?" active":"");lblAnnual.className="toggle-option"+(m==="annual_bill"?" active":"");
   annualBillWrap.hidden=(m!=="annual_bill");if(fields.annual_bill_eur)fields.annual_bill_eur.disabled=(m!=="annual_bill");
   detailedGasWrap.hidden=(m!=="parameters");
   for(const k of detailedGasKeys)if(fields[k])fields[k].disabled=(m!=="parameters");
  };
  radioParams.onclick=()=>{hiddenMode.value="parameters";syncFiscalModeUI();triggerFormInput(hiddenMode);};radioParams.onchange=radioParams.onclick;
  radioAnnual.onclick=()=>{hiddenMode.value="annual_bill";syncFiscalModeUI();triggerFormInput(hiddenMode);};radioAnnual.onchange=radioAnnual.onclick;
  hiddenMode.addEventListener("input",syncFiscalModeUI);hiddenMode.addEventListener("change",syncFiscalModeUI);
  field(card5b,"gas_region","Regione Fiscale Gas / Gas Fiscal Region Identifier",{options:[{value:"",label:"Seleziona regione / Select region…"},...GAS_REGIONS],emptyOption:false,required:false,help:"Identificatore regione o zona climatica per accise regionali gas / Regional gas tax identifier (preserves custom identifiers)"});
  const annualBillWrap=el("div",null,card5b);annualBillWrap.className="annual-bill-wrap";annualBillWrap.hidden=true;
  field(annualBillWrap,"annual_bill_eur","Spesa Annua Gas Naturale / Gas Annual Bill EUR",{type:"number",required:false,unit:"€",help:"Spesa annua da bolletta comprensiva di materia, trasporto e imposte / Total annual gas cost from recent bills"});
  const detailedGasWrap=el("div",null,card5b);detailedGasWrap.className="gas-parameters-wrap";
  const presets5b=el("div",null,detailedGasWrap);presets5b.className="preset-group";
  el("span","Preimpostazioni Accise e IVA Gas / Gas Presets: ",presets5b).className="preset-label";
  const btnGasCentroNord=el("button","Centro-Nord",presets5b);btnGasCentroNord.type="button";btnGasCentroNord.className="preset-btn";
  btnGasCentroNord.onclick=()=>applyPreset({gas_excise_0_120:"0.044",gas_excise_120_480:"0.175",gas_excise_480_1560:"0.170",gas_excise_over_1560:"0.186",gas_vat_reduced:"10",gas_vat_standard:"22"});
  const btnGasSud=el("button","Sud e Isole",presets5b);btnGasSud.type="button";btnGasSud.className="preset-btn";
  btnGasSud.onclick=()=>applyPreset({gas_excise_0_120:"0.038",gas_excise_120_480:"0.135",gas_excise_480_1560:"0.120",gas_excise_over_1560:"0.150",gas_vat_reduced:"10",gas_vat_standard:"22"});
  const grid5b=el("div",null,detailedGasWrap);grid5b.className="card-grid";
  field(grid5b,"gas_excise_0_120","Accisa Scaglione 0–120 Smc / Excise 0–120 Smc",{type:"number",required:false,unit:"€/Smc",help:"Usi civili fino a 120 Smc/anno (cottura e ACS) / Domestic up to 120 Smc/yr (cooking and DHW)"});
  field(grid5b,"gas_excise_120_480","Accisa Scaglione 120–480 Smc / Excise 120–480 Smc",{type:"number",required:false,unit:"€/Smc",help:"Consumi da 120 a 480 Smc/anno (riscaldamento base) / 120 to 480 Smc/yr (base heating)"});
  field(grid5b,"gas_excise_480_1560","Accisa Scaglione 480–1560 Smc / Excise 480–1560 Smc",{type:"number",required:false,unit:"€/Smc",help:"Consumi da 480 a 1560 Smc/anno / 480 to 1560 Smc/yr"});
  field(grid5b,"gas_excise_over_1560","Accisa Scaglione oltre 1560 Smc / Excise over 1560 Smc",{type:"number",required:false,unit:"€/Smc",help:"Consumi eccedenti 1560 Smc/anno / Over 1560 Smc/yr"});
  field(grid5b,"gas_regional_0_120","Addizionale Regionale 0–120 Smc / Regional Surcharge 0–120 Smc",{type:"number",required:false,unit:"€/Smc",help:"Addizionale regionale sul gas naturale fino a 120 Smc / Regional surcharge up to 120 Smc"});
  field(grid5b,"gas_regional_over_120","Addizionale Regionale oltre 120 Smc / Regional Surcharge over 120 Smc",{type:"number",required:false,unit:"€/Smc",help:"Addizionale regionale sul gas naturale oltre 120 Smc / Regional surcharge over 120 Smc"});
  field(grid5b,"gas_vat_reduced","IVA Ridotta (fino a 480 Smc) / Reduced VAT (up to 480 Smc)",{type:"number",min:0,max:100,required:false,unit:"%",help:"Aliquota IVA ridotta 10% fino a 480 Smc / 10% reduced VAT up to 480 Smc"});
  field(grid5b,"gas_vat_standard","IVA Ordinaria (oltre 480 Smc) / Standard VAT (over 480 Smc)",{type:"number",min:0,max:100,required:false,unit:"%",help:"Aliquota IVA ordinaria 22% oltre 480 Smc / 22% standard VAT over 480 Smc"});
  syncFiscalModeUI();

  // Card 5C
  const card5c=el("fieldset",null,market);card5c.className="market-card card-5c";
  el("legend","5C · Mercato Elettrico e Vendita al GSE / Grid Export",card5c);
  const sub5c=el("p","Zona di mercato, valorizzazione dell'energia immessa e condizioni di vendita GSE. / Wholesale zone, export valuation, and GSE terms.",card5c);sub5c.className="card-subtitle";
  const grid5c=el("div",null,card5c);grid5c.className="card-grid";
  const zoneInput=field(grid5c,"zone","Zona di Mercato / Wholesale Zone",{options:["NORD","CNOR","CSUD","SUD","CALA","SICI","SARD"],value:"CSUD",emptyOption:false});
  zoneInput.id="profileMarketZone";
  const valInput=field(grid5c,"valuation","Valutazione / Valuation Mode",{options:[{value:"shape_scaled",label:"Profilo Orario Scalato / Scaled Hourly Profile"},{value:"absolute_proxy",label:"Prezzo Orario Storico GME / Historical GME Hourly Price"},{value:"flat",label:"Prezzo Fisso Costante / Constant Price"}],value:"flat",emptyOption:false});
  valInput.id="profileMarketValuation";
  field(grid5c,"pmg_price","Prezzo Minimo Garantito (PMG) / Guaranteed Minimum Price",{type:"number",value:"0.0475",unit:"€/kWh",help:"Prezzo floor annuale indicizzato ISTAT"});
  field(grid5c,"pmg_nominal_escalation","Indicizzazione Nominale PMG / PMG Nominal Escalation",{type:"number",min:0,max:100,value:"1.5",unit:"%/anno",help:"Tasso di rivalutazione annua inflattiva PMG"});
  field(grid5c,"gse_fee_eur_kw_year","Corrispettivo Gestione GSE / GSE Management Fee",{type:"number",value:"0.70",unit:"€/kW/anno",help:"Quota annuale gestione GSE (esente <= 3 kW)"});
  field(grid5c,"gse_fee_vat","IVA Corrispettivo GSE / GSE Fee VAT",{type:"number",min:0,max:100,value:"22",unit:"%",help:"Aliquota IVA applicata alla tariffa GSE"});
  const zoneHint=el("p",ZONE_HINTS["CSUD"],card5c);zoneHint.className="zone-hint";
  const expl5c=el("div",null,card5c);expl5c.className="market-explanation";
  el("p","Il Prezzo Minimo Garantito (PMG) funge da prezzo minimo annuale per il Ritiro Dedicato (RID) per impianti con potenza idonea, indicizzato annualmente. L'applicazione effettiva richiede l'opzione PMG nello scenario di calcolo; il valore inserito qui definisce il floor e non abilita automaticamente il regime. / Guaranteed Minimum Price (PMG) serves as an annual floor price for GSE RID exports, indexed to nominal escalation. Actual application depends on scenario PMG election; entering this rate does not automatically enable PMG.",expl5c);
  el("p","Esenzione corrispettivi GSE: gli impianti con potenza nominale fino a 3 kW sono esenti dal corrispettivo annuale di gestione GSE (0 €/anno). / GSE fee exemption: systems <= 3 kW peak power are exempt from GSE annual management fees.",expl5c);

  // GME Price Archive Upload Section in Market Card 5 (5C)
  const gmeSection=el("div",null,card5c);gmeSection.className="gme-catalog-section";gmeSection.id="gmeCatalogSection";
  const gmeHeader=el("div",null,gmeSection);gmeHeader.className="gme-catalog-header";
  el("h4","Archivio Prezzi GME (Opzionale) / GME Price Archive Import",gmeHeader);
  el("p","Carica gli archivi ZIP ufficiali GME (MGP Prezzi) per abilitare la valorizzazione oraria storica e il profilo orario scalato. L'importazione è facoltativa: puoi sempre scegliere la tariffa flat (prezzo fisso costante). / Import official GME price archives (.zip) to enable historical hourly pricing or scaled shapes for this profile. Optional: flat valuation is always retained.",gmeHeader).className="card-subtitle";
  const gmeControls=el("div",null,gmeSection);gmeControls.className="gme-catalog-controls";
  const gmeFileWrap=el("div",null,gmeControls);gmeFileWrap.className="field-wrap gme-file-wrap";
  const gmeFileLabel=el("label","Archivi ZIP Prezzi GME / GME Price ZIP Archives",gmeFileWrap);gmeFileLabel.className="field-label";gmeFileLabel.setAttribute("for","gmeArchiveInput");
  const gmeFileInput=el("input",null,gmeFileWrap);gmeFileInput.type="file";gmeFileInput.id="gmeArchiveInput";gmeFileInput.name="gme_archives";gmeFileInput.accept=".zip,application/zip,application/x-zip-compressed";gmeFileInput.multiple=true;gmeFileInput.className="file-input gme-file-input";fields["gme_archives"]=gmeFileInput;

  let activeCatalog=null;
  const isShapeOk=(shapeAvailable,series)=>{
   if(typeof shapeAvailable==='boolean')return shapeAvailable;
   if(shapeAvailable&&typeof shapeAvailable==='object'){
    if(series&&series in shapeAvailable)return Boolean(shapeAvailable[series]);
    const vals=Object.values(shapeAvailable);
    return vals.length>0&&vals.every(Boolean);
   }
   return false;
  };

  const handleGmeUpload=async()=>{
   if(!client.profileId){
    fail('PROFILE_REQUIRED','Profile required: create a new profile or select an existing one above before proceeding.');
   }
   const targetProfileId=client.profileId;
   const getArchiveDateKey=file=>{
    const name=file.name||'';
    const m=name.match(/(?:^|[^0-9])(\d{4})[-_]?(\d{2})(?:[-_]?(\d{2}))?/);
    return m?m[1]+m[2]+(m[3]||'01'):'';
   };
   const filesWithIdx=Array.from(gmeFileInput.files||[]).map((file,idx)=>({file,idx,key:getArchiveDateKey(file)}));
   filesWithIdx.sort((a,b)=>{
    if(a.key&&b.key){
     const cmp=a.key.localeCompare(b.key);
     if(cmp!==0)return cmp;
    }
    return a.idx-b.idx;
   });
   const files=filesWithIdx.map(x=>x.file);
   if(files.length===0){
    fail('ZIP_REQUIRED','Choose at least one GME price ZIP archive (.zip) to upload.');
   }
   for(const file of files){
    const name=(file.name||'').toLowerCase();
    if(!name.endsWith('.zip')){
     fail('ZIP_REQUIRED',`File '${file.name||'unnamed'}' is not a .zip archive. Choose GME price ZIP archives only.`);
    }
   }
   setStatus(`Uploading ${files.length} GME price archive(s)…`);
   gmeSaveNotice.hidden=true;
   gmeSaveNotice.textContent='';
   let lastCatalog=null;
   let successCount=0;
   for(let i=0;i<files.length;i++){
    const file=files[i];
    const uploadText=`Uploading archive ${i+1} of ${files.length}…`;
    setStatus(`Uploading GME price archive ${i+1} of ${files.length}: ${file.name}…`);
    const prog=actionProgress.get(importBtn);
    if(prog)prog.text.textContent=uploadText;
    let res;
    try{
     res=await workflow.uploadMarketCatalog(file);
    }catch(err){
     if(err?.diagnostic?.message)err.message=err.diagnostic.message;
     throw err;
    }
    if(client.profileId!==targetProfileId)return;
    lastCatalog=res?.catalog||null;
    successCount++;
    if(lastCatalog){
     workflow.marketCatalog=lastCatalog;
     updateCatalogUI(lastCatalog,{source:lastCatalog.source||'private',markDirty:true,showSaveNotice:true});
     gmeSaveNotice.textContent=`Imported ${successCount} of ${files.length} archive(s). Price catalogue revision updated. Click 'Step 1: Save draft inputs' and recompile in Section 6 to apply updated prices to calculations.`;
    }
   }
   if(client.profileId!==targetProfileId)return;
   if(lastCatalog){
    setStatus(`Successfully imported ${successCount} GME price archive(s). Price revision pinned to draft. Save draft inputs and recompile to apply.`);
    gmeFileInput.value='';
   }else{
    updateCatalogUI(null);
    setStatus('GME upload completed with no catalog returned.');
   }
  };

  const importBtn=actionButton("Import GME price archives",handleGmeUpload,gmeControls);
  importBtn.id="importGmeArchivesBtn";
  importBtn.className="button gme-upload-btn";
  importBtn._progressText="Uploading archives…";

  const gmeStatus=el("div",null,gmeSection);gmeStatus.className="gme-catalog-status";gmeStatus.id="gmeCatalogStatus";gmeStatus.setAttribute("role","status");gmeStatus.setAttribute("aria-live","polite");
  const metaBox=el("div",null,gmeSection);metaBox.id="profileMarketMetadata";metaBox.hidden=true;
  const winWrap=el("div",null,gmeSection);winWrap.className="field-wrap";winWrap.hidden=true;
  el("label","Finestra di valutazione / Valuation window",winWrap).setAttribute("for","profileMarketWindow");
  const winSelect=el("select",null,winWrap);winSelect.id="profileMarketWindow";fields["profileMarketWindow"]=winSelect;
  winSelect.addEventListener('change',()=>{
   const w=JSON.parse(winSelect.value);
   fields.window_start.value=w[0];fields.window_end.value=w[1];
   dirty=true;client.invalidate();workflow.status='draft';onClear();
   updateCatalogUI(activeCatalog);
  });
  const btnRefresh=el("button","Refresh catalogue metadata",gmeSection);btnRefresh.type="button";btnRefresh.id="refreshProfileCatalog";btnRefresh.hidden=true;
  const btnSaveMarket=el("button","Save market selection and reopen setup",gmeSection);btnSaveMarket.type="button";btnSaveMarket.id="saveProfileMarket";btnSaveMarket.hidden=true;
  const gmeSaveNotice=el("div",null,gmeSection);gmeSaveNotice.className="notice gme-save-notice";gmeSaveNotice.id="gmeSaveNotice";gmeSaveNotice.hidden=true;

  // Card 5D
  const card5d=el("fieldset",null,market);card5d.className="market-card card-5d";
  el("legend","5D · Fisco, IRPEF su Esportazioni e Detrazione / Tax and Incentives",card5d);
  const sub5d=el("p","Regime fiscale applicabile ai proventi di cessione e detrazione decennale per investimenti. / Tax regime for grid exports and 10-year building deduction.",card5d);sub5d.className="card-subtitle";
  const presets5d=el("div",null,card5d);presets5d.className="preset-group";
  el("span","Preimpostazioni Scaglioni IRPEF / Tax Bracket Presets: ",presets5d).className="preset-label";
  const btnTax23=el("button","Scaglione 28k€ (23% + 1.5% + 0.8%)",presets5d);btnTax23.type="button";btnTax23.className="preset-btn";
  btnTax23.onclick=()=>applyPreset({export_irpef:"23",regional_additional_tax:"1.5",municipal_additional_tax:"0.8"});
  const btnTax35=el("button","Scaglione 50k€ (35% + 1.7% + 0.8%)",presets5d);btnTax35.type="button";btnTax35.className="preset-btn";
  btnTax35.onclick=()=>applyPreset({export_irpef:"35",regional_additional_tax:"1.7",municipal_additional_tax:"0.8"});
  const btnTax43=el("button","Scaglione >50k€ (43% + 2.0% + 0.8%)",presets5d);btnTax43.type="button";btnTax43.className="preset-btn";
  btnTax43.onclick=()=>applyPreset({export_irpef:"43",regional_additional_tax:"2.0",municipal_additional_tax:"0.8"});
  const grid5d=el("div",null,card5d);grid5d.className="card-grid";
  field(grid5d,"deduction_rate","Aliquota Detrazione / Tax Deduction Rate",{type:"number",min:0,max:100,value:"50",unit:"%",help:"50% prima casa / 36% seconda casa"});
  field(grid5d,"export_irpef","IRPEF su Ricavi Vendita / Export Marginal IRPEF",{type:"number",min:0,max:100,value:"35",unit:"%",help:"Aliquota marginale IRPEF su cessione energia"});
  field(grid5d,"regional_additional_tax","Addizionale Regionale IRPEF / Regional Additional Tax",{type:"number",min:0,max:100,value:"1.7",unit:"%",help:"Addizionale regionale"});
  field(grid5d,"municipal_additional_tax","Addizionale Comunale IRPEF / Municipal Additional Tax",{type:"number",min:0,max:100,value:"0.8",unit:"%",help:"Addizionale comunale"});
  const hints5d=el("div",null,card5d);hints5d.className="tax-hints";
  el("p","Tassazione proventi GSE (730 Redditi Diversi): I corrispettivi per la cessione di energia alla rete (RID) costituiscono redditi diversi ai fini fiscali (Modello 730 / Redditi PF). / Energy export revenues to the grid constitute miscellaneous taxable income subject to IRPEF.",hints5d);
  el("p","Detrazione Fiscale Decennale: La detrazione fiscale per ristrutturazione edilizia / fotovoltaico è recuperata in 10 rate annuali di pari importo (tetto massimo 96.000 €). / Building renovation deduction is recovered over 10 equal annual instalments (cap 96,000 €).",hints5d);
  el("p","Aliquota Detrazione: 50% abitazione principale (prima casa) / 36% seconda casa: valori illustrativi confermati dall'utente per la simulazione fiscale. / 50% primary residence / 36% second home: illustrative user-confirmed rates.",hints5d);

  // Advanced drawer
  const advDetails=el("details",null,market);advDetails.className="fiscal-advanced-details";
  el("summary","Parametri Avanzati e Fonti / Advanced Parameters and Regulatory Sources",advDetails);
  const advGrid=el("div",null,advDetails);advGrid.className="card-grid";
  const seriesInput=field(advGrid,"series","Price series (PUN is comparison only)",{options:["NORD","CNOR","CSUD","SUD","CALA","SICI","SARD","PUN"],value:"CSUD",emptyOption:false});
  seriesInput.id="profileMarketSeries";
  field(advGrid,"price_revision","Pinned price catalogue digest (not needed for explicit flat)",{required:false,help:"Non richiesto per tariffa flat"});
  field(advGrid,"window_start","Price window start",{type:"date"});field(advGrid,"window_end","Price window end (exclusive)",{type:"date"});
  field(advGrid,"effective_from","Fiscal effective_from",{type:"date"});field(advGrid,"effective_to","Fiscal effective_to",{type:"date"});
  field(advGrid,"as_of","Fiscal as_of",{type:"date"});field(advDetails,"sources","Fiscal sources (one URL or reference per line)");
  const confirmWrap=el("div",null,market);confirmWrap.className="fiscal-confirmation-wrap";
  field(confirmWrap,"confirmed","Confermo l'applicabilità fiscale, le fonti e le aliquote inserite; questi valori hanno scopo simulativo e non costituiscono consulenza fiscale o legale. / I confirm fiscal applicability, sources and rates; these are not automatically current legal advice.",{type:"checkbox"});

  updateCatalogUI=(catalog,{source='private',markDirty=false,showSaveNotice=false}={})=>{
   let effectiveCatalog=catalog;
   let effectiveSource=catalog?.source||source;
   if(!effectiveCatalog&&adminCatalog){
    effectiveCatalog=adminCatalog;
    effectiveSource=adminCatalog.source||'admin';
   }
   activeCatalog=effectiveCatalog;
   gmeStatus.textContent='';
   if(!showSaveNotice){
    gmeSaveNotice.hidden=true;
    gmeSaveNotice.textContent='';
   }
   if(!effectiveCatalog){
    winWrap.hidden=true;
    const p=el("p","Nessun catalogo prezzi GME importato per questo profilo. È attiva la modalità tariffa flat (prezzo fisso costante). / No GME price catalogue imported for this profile. Flat valuation mode is active.",gmeStatus);
    p.className="catalog-empty-notice muted";
    if(fields.valuation){
     const flatOpt=Array.from(fields.valuation.options||fields.valuation.children||[]).find(o=>o.value==='flat');
     if(flatOpt)flatOpt.disabled=false;
     const proxyOpt=Array.from(fields.valuation.options||fields.valuation.children||[]).find(o=>o.value==='absolute_proxy');
     if(proxyOpt)proxyOpt.disabled=true;
     const shapeOpt=Array.from(fields.valuation.options||fields.valuation.children||[]).find(o=>o.value==='shape_scaled');
     if(shapeOpt){
      shapeOpt.disabled=true;
      shapeOpt.title='Richiede un catalogo prezzi GME importato / Requires an imported GME price catalogue';
     }
    }
    return;
   }
   const windows=effectiveCatalog.windows||[effectiveCatalog.window||effectiveCatalog.coverage];
   let selected=[val('window_start'),val('window_end')];
   if(markDirty||!selected[0]||!selected[1])selected=effectiveCatalog.window||windows[0];
   const choices=windows.slice();
   if(!choices.some(w=>w[0]===selected[0]&&w[1]===selected[1]))choices.push(selected);
   winWrap.hidden=false;winSelect.textContent='';
   for(const w of choices){const o=el('option',`${w[0]} → ${w[1]}`,winSelect);o.value=JSON.stringify(w);}
   winSelect.value=JSON.stringify(selected);
   if(markDirty){fields.window_start.value=selected[0];fields.window_end.value=selected[1];}
   const year=selected[0].slice(0,4);
   const yearMeta=effectiveCatalog.years?.[year];
   const calendarYear=selected[0]===`${year}-01-01`&&selected[1]===`${Number(year)+1}-01-01`;
   const defaultWindow=effectiveCatalog.window||effectiveCatalog.coverage;
   const isDefault=selected[0]===defaultWindow[0]&&selected[1]===defaultWindow[1];
   const missing=yearMeta?.missing_months||effectiveCatalog.missing_months||[];
   const curSeries=val('series')||'CSUD';
   const shapes=calendarYear&&yearMeta?yearMeta.shape_available:(isDefault?effectiveCatalog.shape_available:null);
   const shapeAvailable=isShapeOk(shapes,curSeries);
   const reason=shapeAvailable?'':(missing.length
    ?`Missing months for ${year}: ${missing.join(', ')}`
    :calendarYear&&yearMeta?.complete?`Nonpositive annual mean for ${curSeries} in ${year}; normalization requires a positive mean`
    :'Select twelve complete calendar months with a positive mean; this window has no normalized hourly shape');
   const summaryCard=el('div',null,gmeStatus);summaryCard.className='catalog-summary-card';
   el('span',effectiveSource==='admin'?'System Default / Admin Fallback':'Profilo Privato / Private Catalog',summaryCard).className='badge catalog-source-badge '+(effectiveSource==='admin'?'badge-admin':'badge-private');
   const grid=el('div',null,summaryCard);grid.className='catalog-summary-grid';
   const row=(label,value,cls)=>{const r=el('div',null,grid);r.className='catalog-row';el('span',label+': ',r).className='catalog-label';el('span',value,r).className=cls||'';};
   row('Revisione / Revision',effectiveCatalog.revision);
   row('Copertura archivio / Archive coverage',(effectiveCatalog.coverage||[]).join(' → '),'catalog-coverage-value');
   row('Finestra di valutazione / Valuation window',selected.join(' → '));
   row(`Mesi mancanti / Missing months (${year})`,missing.length?`${missing.length} missing: ${missing.join(', ')}`:`Complete calendar year ${year} (all 12 months present)`,missing.length?'catalog-missing-tag':'catalog-complete-tag');
   row('Profilo Orario Scalato / Hourly Shape',shapeAvailable?'Disponibile / Available':'Non disponibile / Unavailable: '+reason,shapeAvailable?'catalog-shape-available-tag':'catalog-shape-unavailable-tag');
   const shapeOpt=Array.from(fields.valuation.options||fields.valuation.children||[]).find(o=>o.value==='shape_scaled');
   if(shapeOpt){shapeOpt.disabled=!shapeAvailable;shapeOpt.title=reason;}
   const proxyOpt=Array.from(fields.valuation.options||fields.valuation.children||[]).find(o=>o.value==='absolute_proxy');
   const covered=selected[0]>=effectiveCatalog.coverage[0]&&selected[1]<=effectiveCatalog.coverage[1]&&selected[0]<selected[1];
   if(proxyOpt){proxyOpt.disabled=!covered;proxyOpt.title=covered?'Uses historical prices in the selected window':'Selected window extends beyond imported archive coverage';}
   if(markDirty){
    if(effectiveCatalog.revision&&fields.price_revision){
     fields.price_revision.value=effectiveCatalog.revision;
    }
    if(effectiveCatalog.series&&effectiveCatalog.series.length&&fields.series){
     if(!effectiveCatalog.series.includes(fields.series.value)){
      fields.series.value=effectiveCatalog.series.includes('CSUD')?'CSUD':effectiveCatalog.series[0];
     }
    }
    dirty=true;
    client.invalidate();
    workflow.status='draft';
    onClear();
   }
   if(showSaveNotice){
    gmeSaveNotice.hidden=false;
    gmeSaveNotice.textContent="Price catalogue revision updated. Click 'Step 1: Save draft inputs' and recompile in Section 6 to apply updated prices to calculations.";
   }
  };
  if(fields.series){
   fields.series.addEventListener('change',()=>{
    if(activeCatalog)updateCatalogUI(activeCatalog);
   });
  }
  const review=section('6 · Review and generate');form.appendChild(review);
  const yearRoot=el('div',null,review);
  yearRoot.className='year-menu-root';
  const yearMenu=new YearMenu(yearRoot,{doc:d,verified:false});
  fields['years']=yearMenu.input;
  field(review,'reference_year','Annual allocation calendar year',{type:'number',help:'Calendar year used to align days, months, and leap-year schedules when allocating annual demand.'});
  field(review,'loss_percent','System loss percent',{type:'number',min:0,max:100,step:'any',required:true,help:'Estimated total system losses from cabling, inverter, and soiling (PVGIS default is 14%).'});
  field(review,'mounting','Mounting',{options:['Free standing 2025','Integrated 2025'],help:'Free-standing rack with rear airflow or building-integrated (BIPV) with higher thermal losses.'});
  field(review,'technology','Module technology',{options:['cSi'],help:'Photovoltaic cell material: crystalline silicon (c-Si), standard for mono/polycrystalline panels.'});
   field(review,'providerConsent','I authorize sending entered coordinates/roof geometry and years to JRC PVGIS 5.3 and PVGIS 6 SARAH-3 and coordinates/years to Open-Meteo historical ERA5',{type:'checkbox',required:false});
   fields.providerConsent.onchange=()=>{updateCheckboxesStorage('providerConsent',fields.providerConsent.checked);triggerFormInput(fields.providerConsent);};
   el('p','Destinations: re.jrc.ec.europa.eu/api/v5_3/seriescalc and photovoltaic-geographic-information-system.ec.europa.eu/api/v6/power/broadband (JRC PVGIS); archive-api.open-meteo.com/v1/archive. No fallback to another location. Live availability/terms remain unverified.',review);
  const coverageCache=new Map();let coverageSeq=0,coverageVerified=false,coverageStale=false,coverageRevision=null,coverageLocation=null,dirty=false;
  function invalidateCoverage(){
   coverageSeq++;
   coverageVerified=false;
   coverageStale=true;
   coverageRevision=null;
   coverageLocation=null;
   yearMenu.setSupportedYears([]);
  }
  const checkCoverage=async()=>{
   if(!check('providerConsent'))fail('PROVIDER_CONSENT_REQUIRED','Review coordinate destinations before checking solar coverage.');
   if(client.profileId)setProfileChecks(client.profileId,{providerConsent:true});
   if(!client.profileId)fail('PROFILE_REQUIRED','Create or select a draft first.');
   const loc=workflow.profile?.location;
   const lat=Number(val('latitude')),lon=Number(val('longitude'));
   if(dirty||!loc||!client.revision||!Number.isFinite(loc.latitude)||!Number.isFinite(loc.longitude)||loc.latitude!==lat||loc.longitude!==lon){
    fail('SAVE_REQUIRED','Save profile draft before checking solar coverage.');
   }
   const cacheKey=`${client.profileId}:${client.revision}:${loc.latitude},${loc.longitude}`;
   if(coverageCache.has(cacheKey)){
    const years=coverageCache.get(cacheKey);
    coverageVerified=true;
    coverageStale=false;
    coverageRevision=client.revision;
    coverageLocation=`${loc.latitude},${loc.longitude}`;
    yearMenu.setSupportedYears(years);
    status.textContent='Supported weather years loaded from cache.';
    return;
   }
   const seq=++coverageSeq,reqProfileId=client.profileId,reqRevision=client.revision,reqGen=client.generation;
   status.textContent='Checking supported solar years…';
   try{
    const res=await client.profileRequest('/solar-coverage',{consent:true,revision:client.revision},{method:'POST'});
    if(seq!==coverageSeq||client.profileId!==reqProfileId||client.revision!==reqRevision||client.generation!==reqGen)return;
    if(!res||!Array.isArray(res.years)||!res.years.length)fail('COVERAGE_INVALID','Solar coverage returned no supported years.');
    const seen=new Set(),validYears=[];
    for(const y of res.years){
     const n=Number(y);
     if(!Number.isInteger(n)||!SUPPORTED_YEARS.includes(n)||seen.has(n))fail('COVERAGE_INVALID','Solar coverage returned invalid or duplicate year: '+y);
     seen.add(n);
     validYears.push(n);
    }
    validYears.sort((a,b)=>a-b);
    coverageCache.set(cacheKey,validYears);
    coverageVerified=true;
    coverageStale=false;
    coverageRevision=client.revision;
    coverageLocation=`${loc.latitude},${loc.longitude}`;
    yearMenu.setSupportedYears(validYears);
    status.textContent=`Solar coverage verified: ${validYears.length} supported years.`;
   }catch(err){
    if(seq===coverageSeq){
     invalidateCoverage();
    }
    throw err;
   }
  };
  let preview=null;
  const num=k=>{
   const name=k==='loss_percent'?'System loss percent':k==='baseline_cooling_kwh'?'Existing cooling annual electricity':k;
   if(!val(k).trim())fail('FIELD_REQUIRED',name+' is required.');
   const n=Number(val(k));
   if(!Number.isFinite(n))fail('NUMBER_REQUIRED',name+' must be a finite number.');
   if(k==='loss_percent'||FRACTIONS.has(k)){
    if(n<0||n>100)fail('PERCENT_LIMIT',name+' must be between 0% and 100%.');
    return n;
   }
   if(n<0)fail('NUMBER_REQUIRED',k+' must be finite and nonnegative.');
   return n;
  };
  const numOrZero=k=>val(k).trim()===''?0:num(k);
   function collect(){
    const kind=val('kind');
    if(!kind)fail('KIND_REQUIRED','Choose a consumption basis (annual, monthly, or interval).');
    const coverage={start:val('coverage_start'),end:val('coverage_end'),complete:true},policy={mode:kind==='interval'?'measured':'synthetic',leap_policy:'february_month_hour',reference_year:num('reference_year'),acknowledged:check('ack'),synthetic_completion:kind!=='interval'||check('synthetic_completion'),variant:val('variant')};
    let annual_kwh=null;
    let monthly_totals=null;
    let bands=null;
    if(kind==='annual'){
     annual_kwh=num('annual');
     policy.annual_kwh=annual_kwh;
    }else if(kind==='monthly'&&_monthlyMode==='B'){
     const f1=numbers(val('monthly_f1'));
     const f2=numbers(val('monthly_f2'));
     const f3=numbers(val('monthly_f3'));
     bands={monthly_f1:f1,monthly_f2:f2,monthly_f3:f3};
     policy.monthly_f1=f1;policy.monthly_f2=f2;policy.monthly_f3=f3;
    }else{
     monthly_totals=numbers(val('totals'));
    }
    const load={policy};
    if(monthly_totals!==null)load.monthly_totals=monthly_totals;
    const end_uses=[];for(const id of ['space_heat','dhw','cooking'])if(check(id+'_enabled')){
     const carrier=val(id+'_carrier'),action=val(id+'_action'),measured=energyMode[id]!=='useful',explicit=costMode[id]!=='bill';
     const factor=carrier!=='none'&&val(id+'_factor')?num(id+'_factor'):null;
     const monthly=measured&&val(id+'_input')?numbers(val(id+'_input')):null;
     const useful=!measured&&val(id+'_heat')?numbers(val(id+'_heat')):null;
     const use={id,carrier,measured_input:monthly?monthly.reduce((a,b)=>a+b,0):null,unit:monthly?({gas:'smc',biomass:'kg',district:'kwh',electricity:'kwh'}[carrier]||null):null,monthly_useful_kwh:useful,efficiency:carrier==='electricity'?null:factor,cop:carrier==='electricity'?factor:null,annual_fixed_cost:explicit?numOrZero(id+'_fixed'):0,annual_variable_cost:explicit?numOrZero(id+'_variable'):0,included_in_electric_load:carrier==='electricity'&&check(id+'_included'),action,artifact_id:null,coverage,period_relation:'same'};
     if(monthly){use.monthly_input=monthly;use.conversion_kwh_per_unit=num(id+'_conversion');}
     if(!explicit&&val(id+'_bill'))use.annual_bill_eur=num(id+'_bill');
     end_uses.push(use);
    }
    const parameters={policy_version:'italy-fiscal-v1',mode:val('fiscal_mode'),confirmed:check('confirmed'),effective_from:val('effective_from'),effective_to:val('effective_to'),as_of:val('as_of'),sources:val('sources').split('\n').filter(Boolean)};
    for(const k of Object.keys(fields))if(/^(electric_excise|electric_vat|pmg_price|pmg_nominal_escalation|gse_fee_eur_kw_year|gse_fee_vat|export_irpef|regional_additional_tax|municipal_additional_tax|deduction_rate|gas_excise_.*|gas_regional_.*|gas_vat_.*|annual_bill_eur)$/.test(k)&&val(k)!=='')parameters[k]=FRACTIONS.has(k)?(num(k)/100):num(k);
    const consumptionPayload={artifact_id:kind==='interval'?(workflow.profile?.consumption.artifact_id||null):null,kind,coverage,projection_policy:kind==='interval'?(check('fixed_clock')?'standard_month_hour':'civil_month_hour'):'synthetic_v1',acknowledged:check('ack'),annual_kwh};
    if(bands)Object.assign(consumptionPayload,bands);
    else consumptionPayload.monthly_totals=monthly_totals;
    const profile={schema_version:1,id:client.profileId,country:val('country'),timezone:val('timezone'),location:{latitude:Number(val('latitude')),longitude:Number(val('longitude'))},consumption:consumptionPayload,roofs:roofEditor.getRoofs(),thermal:{end_uses,electric_loads:['ev','cooling'].map(id=>{const action=val(id+'_action'),add=action==='add';return {id,included_in_electric_load:add?false:check(id+'_included'),action,artifact_id:null,incremental_annual_kwh:add&&val(id+'_incremental')?num(id+'_incremental'):null};})},market:{zone:val('zone'),series:val('series'),window_start:val('window_start'),window_end:val('window_end'),valuation_mode:val('valuation'),price_revision:val('price_revision')||null},fiscal:{country:'IT',rule_revision:null,gas_region:val('gas_region')||null,parameters},default_settings:{...(workflow.profile?.default_settings||{}),retail_contract:'fixed',baseline_cooling_kwh:(()=>{
      const act=val('cooling_action'),inc=check('cooling_included');
      if(act==='unchanged'&&!inc)return numOrZero('baseline_cooling_kwh');
      if(!val('baseline_cooling_kwh').trim())fail('FIELD_REQUIRED','Existing cooling annual electricity is required for accurate reported cooling (enter 0 if there is no existing cooling).');
      return num('baseline_cooling_kwh');
     })(),retail_commodity:num('retail_commodity'),retail_regulated:num('retail_regulated')}};
   if(!val('latitude').trim()||!val('longitude').trim())fail('LOCATION_REQUIRED','Enter coordinates.');
   return {profile,load,checks:{gross:check('gross'),existingPv:check('existingPv'),reconciled:check('reconciled')}};
  }
  function hydrate(profile){
   if(!profile)return;
   const pid=profile.id||client.profileId;
   const stored=getProfileChecks(pid);
   if(stored){
    if(typeof stored.gross==='boolean')fields.gross.checked=stored.gross;
    if(typeof stored.existingPv==='boolean'){
     fields.existingPv.checked=stored.existingPv;
     updateReconciledVisibility();
    }
    if(typeof stored.reconciled==='boolean')fields.reconciled.checked=stored.reconciled;
    if(typeof stored.providerConsent==='boolean')fields.providerConsent.checked=stored.providerConsent;
    if(typeof stored.fixed_clock==='boolean')fields.fixed_clock.checked=stored.fixed_clock;
    if(typeof stored.synthetic_completion==='boolean')fields.synthetic_completion.checked=stored.synthetic_completion;
   }else{
    updateReconciledVisibility();
   }
   if(profile.consumption?.projection_policy==='standard_month_hour'&&(!stored||typeof stored.fixed_clock!=='boolean')){
    if(fields.fixed_clock)fields.fixed_clock.checked=true;
   }
   const put=(key,value)=>{
    if(value!==null&&value!==undefined&&fields[key]){
     if(fields[key].type==='checkbox')fields[key].checked=Boolean(value);
     else {
      if(FRACTIONS.has(key)){
       fields[key].value=String(Number((Number(value)*100).toPrecision(12)));
      }else{
       fields[key].value=String(value);
      }
     }
    }
   };
   for(const key of ['country','timezone'])put(key,profile[key]);
   for(const key of ['latitude','longitude'])put(key,profile.location[key]);
   put('kind',profile.consumption.kind);put('ack',profile.consumption.acknowledged);
   if(profile.consumption.coverage){put('coverage_start',profile.consumption.coverage.start);put('coverage_end',profile.consumption.coverage.end);}
   if(profile.consumption.annual_kwh!=null)put('annual',profile.consumption.annual_kwh);
   const hasBands=Boolean(profile.consumption.monthly_f1&&profile.consumption.monthly_f2&&profile.consumption.monthly_f3);
   if(profile.consumption.kind==='monthly'&&hasBands){
    switchMonthlyMode('B',true);
    put('monthly_f1',Array.isArray(profile.consumption.monthly_f1)?profile.consumption.monthly_f1.join(','):profile.consumption.monthly_f1);
    put('monthly_f2',Array.isArray(profile.consumption.monthly_f2)?profile.consumption.monthly_f2.join(','):profile.consumption.monthly_f2);
    put('monthly_f3',Array.isArray(profile.consumption.monthly_f3)?profile.consumption.monthly_f3.join(','):profile.consumption.monthly_f3);
    syncModeBGridFromRaw();
    updateModeBSumsAndShare();
   }else{
    switchMonthlyMode('A',true);
    clearBands();
    if(profile.consumption.monthly_totals!=null){
     put('totals',Array.isArray(profile.consumption.monthly_totals)?profile.consumption.monthly_totals.join(','):profile.consumption.monthly_totals);
     syncModeAGridFromTotals();
    }
   }
   put('baseline_cooling_kwh',profile.default_settings?.baseline_cooling_kwh);
   if(profile.default_settings){
    if(profile.default_settings.retail_commodity!=null)put('retail_commodity',profile.default_settings.retail_commodity);
    if(profile.default_settings.retail_regulated!=null)put('retail_regulated',profile.default_settings.retail_regulated);
   }
   for(const key of ['zone','series','price_revision','window_start','window_end'])put(key,profile.market[key]);put('valuation',profile.market.valuation_mode);
   if(profile.fiscal.gas_region&&fields.gas_region){
    const regVal=String(profile.fiscal.gas_region);
    const existing=Array.from(fields.gas_region.children||[]).some(o=>o.value===regVal);
    if(!existing){
     const opt=el('option',regVal,fields.gas_region);
     opt.value=regVal;
    }
   }
   put('gas_region',profile.fiscal.gas_region??'');put('fiscal_mode',profile.fiscal.parameters.mode);
   for(const [key,value]of Object.entries(profile.fiscal.parameters))put(key,key==='sources'?value.join('\n'):value);
   for(const u of profile.thermal.end_uses){
    const carrier=u.carrier||'',action=u.action||'unchanged',useful=Array.isArray(u.monthly_useful_kwh)?u.monthly_useful_kwh:null;
    const measured=!useful;
    put(u.id+'_enabled',true);put(u.id+'_carrier',carrier);put(u.id+'_action',action);
    put(u.id+'_included',carrier==='electricity'&&Boolean(u.included_in_electric_load));
    energyMode[u.id]=measured?'measured':'useful';lastCarrier[u.id]=carrier;
    if(measured){
     clearField(u.id+'_heat');
     put(u.id+'_input',u.monthly_input?.join(','));put(u.id+'_conversion',(carrier==='gas'||carrier==='biomass')?u.conversion_kwh_per_unit:null);
    }else{
     clearField(u.id+'_input');clearField(u.id+'_conversion');
     put(u.id+'_heat',useful.join(','));
    }
    put(u.id+'_factor',carrier==='electricity'?u.cop:u.efficiency);
    const bill=u.annual_bill_eur!=null;
    costMode[u.id]=bill?'bill':'explicit';
    if(bill){clearField(u.id+'_fixed');clearField(u.id+'_variable');put(u.id+'_bill',u.annual_bill_eur);}
    else{clearField(u.id+'_bill');put(u.id+'_fixed',u.annual_fixed_cost);put(u.id+'_variable',u.annual_variable_cost);}
   }
   for(const u of profile.thermal.electric_loads||[]){
    const action=u.action||'unchanged';
    put(u.id+'_action',action);
    put(u.id+'_included',action==='add'?false:Boolean(u.included_in_electric_load));
    put(u.id+'_incremental',u.incremental_annual_kwh);
   }
   syncThermalSection();
   updateLiveSummary();
   syncFiscalModeUI();
   if(zoneHint)zoneHint.textContent=ZONE_HINTS[val('zone')]||'';
  }
  const save=async()=>{
   if(!client.profileId)fail('PROFILE_REQUIRED','Create or select a draft first.');
   if(!form.reportValidity())return false;
   const v=collect();
   setProfileChecks(client.profileId,{
    gross:Boolean(v.checks.gross),
    existingPv:Boolean(v.checks.existingPv),
    reconciled:Boolean(v.checks.reconciled),
    providerConsent:Boolean(check('providerConsent')),
    fixed_clock:Boolean(check('fixed_clock')),
    synthetic_completion:Boolean(check('synthetic_completion'))
   });
   const result=await workflow.save(v.profile,v.load,v.checks);
   dirty=false;
   invalidateCoverage();
   preview.textContent=JSON.stringify({profile:workflow.profile,validation:result},null,2);
   status.textContent='Draft saved. Review inputs before generating.';
   return true;
  };
  let selectSeq=0;
  selectProfile=async id=>{
   const seq=++selectSeq;
   form.reset();
   resetSection2();
   fields.fiscal_mode.value='parameters';
   syncFiscalModeUI();
   updateLiveSummary();
   if(zoneHint)zoneHint.textContent=ZONE_HINTS['CSUD'];
   yearMenu.setSelectedYears([]);
   invalidateCoverage();
   form.inert=true;
   roofEditor.setRoofs([]);
   preview.textContent='';
   status.textContent='Loading profile…';
   if(!id){
    workflow.reset();
    client.clear();
    updateCatalogUI(null);
    status.textContent='Select or create a profile to continue.';
    return;
   }
   await workflow.select(id);
   if(seq!==selectSeq||client.profileId!==id)return;
   form.inert=false;
   dirty=false;
   hydrate(workflow.profile);
   updateCatalogUI(workflow.marketCatalog);
   roofEditor.setRoofs(workflow.profile?.roofs||[]);
   if(workflow.compiledCandidate){
    const comp=workflow.compiledCandidate;
    if(Array.isArray(comp.years)&&comp.years.length){yearMenu.setCompiledYears(comp.years);}
    if(comp.solar){
     if(comp.solar.loss_percent!=null&&fields.loss_percent)fields.loss_percent.value=String(comp.solar.loss_percent);
     if(comp.solar.mounting&&fields.mounting)fields.mounting.value=String(comp.solar.mounting);
     if(comp.solar.technology&&fields.technology)fields.technology.value=String(comp.solar.technology);
    }
    if(comp.reference_year!=null&&fields.reference_year)fields.reference_year.value=String(comp.reference_year);
   }
   preview.textContent=JSON.stringify(workflow.profile,null,2);
   const cp=workflow.profile?.consumption;
   const hasRestoredTotals=cp?.kind==='annual'?(Number.isFinite(cp?.annual_kwh)&&cp.annual_kwh>=0):((Array.isArray(cp?.monthly_totals)&&cp.monthly_totals.length===12)||(typeof cp?.monthly_totals==='string'&&cp.monthly_totals.trim().split(',').length===12)||(Array.isArray(cp?.monthly_f1)&&cp.monthly_f1.length===12));
   status.textContent=workflow.status==='ready'?'Ready profile selected.':(hasRestoredTotals?'Draft selected. Confirm gross demand and re-enter CSV mapping and weather-generation choices to revise it.':'Draft selected. Confirm gross demand and re-enter monthly/annual totals, CSV mapping and weather-generation choices to revise it.');
   onClear();
  };
  selector.onchange=run(async()=>{ await selectProfile(selector.value); });
  // Any unsaved input invalidates the ready state immediately.
  form.addEventListener('input',event=>{
   if(!['years','weather_year','providerConsent','loss_percent'].includes(event.target?.name)){
    dirty=true;
    invalidateCoverage();
   }
   if(event.target?.name==='loss_percent'){
    const step3Err=actionErrors.get(step3Btn);
    if(step3Err&&/System loss percent|loss_percent/i.test(step3Err.textContent||'')){
     step3Err.hidden=true;
     step3Err.textContent='';
    }
    if(status.textContent&&/System loss percent|loss_percent/i.test(status.textContent)){
     status.textContent='';
     if(typeof status.classList?.remove==='function')status.classList.remove('has-button-error');
    }
   }
   if(event.target?.name&&CHECK_NAMES.includes(event.target.name)){
    updateCheckboxesStorage(event.target.name,Boolean(event.target.checked));
   }
   client.invalidate();workflow.status='draft';
   if(['csv','timestamp','energy','fold','unit','timestamp_convention','delimiter','decimal_separator','duration_seconds','fixed_clock','csv_start','csv_end'].includes(event.target?.name)&&workflow.profile?.consumption)workflow.profile.consumption.artifact_id=null;
   if(['retail_commodity','retail_regulated','electric_excise','electric_vat'].includes(event.target?.name))updateLiveSummary();
   if(event.target?.name==='fiscal_mode')syncFiscalModeUI();
   if(/_(carrier|action|enabled)$/.test(event.target?.name||'')){
    const id=event.target.name.replace(/_(carrier|action|enabled)$/,'');
    if(thermalCards[id])syncThermalCard(id);
    if(electricCards[id])syncElectricCard(id);
   }
   if(event.target?.name==='zone'){
    if(zoneHint)zoneHint.textContent=ZONE_HINTS[val('zone')]||'';
    if(val('series')!=='PUN'&&fields.series)fields.series.value=val('zone');
    if(activeCatalog)updateCatalogUI(activeCatalog);
   }
   onClear();
  });
  const workflowWrap=el('div',null,review);
  workflowWrap.className='final-actions-workflow';

  const intro=el('div',null,workflowWrap);
  intro.className='final-actions-intro';
  el('h4','Final Setup Sequence',intro);
  el('p','Execute steps 1–3 in order to prepare and compile the household energy model. Once compiled, launch Step 4 to view calculation results.',intro);

  const step1=el('div',null,workflowWrap);
  step1.className='action-step';
  const h1=el('div',null,step1);
  h1.className='action-step-header';
  el('span','Step 1 of 4',h1).className='action-step-badge';
  el('strong','Save draft inputs',h1).className='action-step-title';
  el('small','Save profile inputs and coordinates to server (prerequisite before checking solar coverage).',h1).className='action-step-desc';
  const step1Btn=actionButton('Save and review draft',save,step1);
  step1Btn._progressText='Saving draft…';

  const step2=el('div',null,workflowWrap);
  step2.className='action-step';
  const h2=el('div',null,step2);
  h2.className='action-step-header';
  el('span','Step 2 of 4',h2).className='action-step-badge';
  el('strong','Verify solar coverage',h2).className='action-step-title';
  el('small','Query JRC PVGIS for supported weather years at saved coordinates (requires consent above).',h2).className='action-step-desc';
  const step2Btn=actionButton('Check solar coverage',checkCoverage,step2);
  step2Btn._progressText='Checking solar coverage…';

  const step3=el('div',null,workflowWrap);
  step3.className='action-step';
  const h3=el('div',null,step3);
  h3.className='action-step-header';
  el('span','Step 3 of 4',h3).className='action-step-badge';
  el('strong','Generate solar data & compile',h3).className='action-step-title';
  el('small','Simulate hourly PV generation across selected years and compile energy balance model.',h3).className='action-step-desc';
  const step3Btn=actionButton('Generate solar data and compile (retry)',async()=>{
   if(!check('providerConsent'))fail('PROVIDER_CONSENT_REQUIRED','Review coordinate destinations before generation.');
   if(dirty||coverageStale||!coverageVerified||coverageRevision!==client.revision){
    fail('COVERAGE_STALE','Solar coverage is stale or unverified. Save profile and check solar coverage before generating.');
   }
   if(workflow.profile.consumption.kind==='interval'&&!workflow.profile.consumption.artifact_id)fail('CSV_REQUIRED','Validate the mapped CSV first.');
   const years=(typeof fields.years?.getSelectedYears==='function')?fields.years.getSelectedYears():val('years').split(/[,\s]+/).filter(Boolean).map(Number);if(!years.length||years.length>30||years.some((v,i)=>!Number.isInteger(v)||!yearMenu.supported.includes(v)||(i&&v<=years[i-1])))fail('YEARS_INVALID','Choose sorted unique historical years from supported range.');
   const lossPercent=num('loss_percent');
   const prog=actionProgress.get(step3Btn);
   if(prog)prog.text.textContent='Simulating solar PV…';
   await workflow.generate(years,{loss_percent:lossPercent,mounting:val('mounting'),technology:val('technology'),source_policy:{solar:'pvgis53_pvgis6_sarah3',temperature:'openmeteo_era5',hour_mapping:'pvgis53-pvgis6-sarah3-utc-observation-v2'}},state=>{
    setStatus('Solar generation: '+state);
    if(prog)prog.text.textContent='Solar generation: '+state;
   });
   setStatus('Compiled and ready. You may open the calculation views.');
  },step3);
  step3Btn._progressText='Simulating solar PV…';

  const step4=el('div',null,workflowWrap);
  step4.className='action-step action-step-primary';
  const h4=el('div',null,step4);
  h4.className='action-step-header';
  el('span','Step 4 of 4 · Ready',h4).className='action-step-badge';
  el('strong','Open calculation views',h4).className='action-step-title';
  el('small','Launch solar system sizing, battery optimization, and financial feasibility analysis.',h4).className='action-step-desc';
  const step4Btn=actionButton('Open calculation views',async()=>{const validation=await client.profileRequest('/validation');if(!validation.ready||!client.ready)fail('PROFILE_NOT_READY','Save, generate and compile a valid profile first.');await onReady(workflow.profile);},step4);
  step4Btn._progressText='Opening calculation views…';

  preview=el('pre',null,review);preview.setAttribute('aria-label','Validation and input review');
  const csvBtn=actionButton('Validate CSV preview',async()=>{if(val('kind')!=='interval')fail('CSV_BASIS_REQUIRED','Choose interval consumption to use a CSV.');const file=await csvImport.prepare();if(!file)fail('FILE_REQUIRED','Choose a CSV.');if(!await save())return;if(file.size>16*1024*1024)fail('FILE_TOO_LARGE','CSV exceeds 16 MiB.');const metadata=csvMetadata({timestamp:val('timestamp'),energy:val('energy'),fold:val('fold'),unit:val('unit'),duration_seconds:val('duration_seconds'),delimiter:val('delimiter'),decimal_separator:val('decimal_separator'),timezone:val('timezone'),timestamp_convention:val('timestamp_convention'),fixed_clock:check('fixed_clock'),existing_pv:check('existingPv'),coverage_start:val('csv_start'),coverage_end:val('csv_end')});if(!val('csv_start')||!val('csv_end'))fail('COVERAGE_REQUIRED','Declare the exact CSV range with UTC offsets. Partial coverage is allowed with all monthly totals.');const result=await workflow.upload(file,metadata);workflow.profile.consumption.artifact_id=result.artifact_id;preview.textContent=JSON.stringify(result,null,2);setStatus('CSV preview validated. Review coverage and modeled totals.');},csvContainer);
  csvBtn._progressText='Validating CSV preview…';

  const pairingGroup=el('fieldset',null,root);
  pairingGroup.className='profile-setup-group household-pairing-group';
  pairingGroup.id='householdPairingGroup';
  el('legend','Local network access · Link another browser',pairingGroup);

  const pairingNotice=el('div',null,pairingGroup);
  pairingNotice.className='notice pairing-warning';
  pairingNotice.id='pairingPrivacyWarning';
  pairingNotice.setAttribute('role','alert');
  el('strong','⚠️ Privacy notice: ',pairingNotice);
  el('span','Pairing code grants access to ALL profiles, uploaded data, and calculation results in this household. Only share codes with devices you trust on this local network. Joining another household replaces this browser\'s active session with the linked household without deleting unlinked data.',pairingNotice);

  const pairingStatus=el('p',null,pairingGroup);
  pairingStatus.className='pairing-status';
  pairingStatus.id='pairingStatus';
  pairingStatus.setAttribute('role','status');
  pairingStatus.setAttribute('aria-live','polite');

  const pairingGrid=el('div',null,pairingGroup);
  pairingGrid.className='pairing-grid';

  const shareCard=el('div',null,pairingGrid);
  shareCard.className='pairing-card';
  el('h4','Share this household (generate code)',shareCard);
  el('p','Generate a one-time code to grant another browser on this LAN full access to this household\'s profiles and results.',shareCard).className='muted';

  const pairBtn=button('Generate pair code',async()=>{
   pairingStatus.textContent='Generating pair code...';
   const data=typeof client.pair==='function'?await client.pair():await client.request('/api/session/pair',{});
   codeDisplay.textContent=data.code;
   codeWrap.hidden=false;
   codeWrap.removeAttribute('hidden');
   expiresInfo.textContent=`Code expires in ${Math.ceil((data.expires_in||300)/60)} minutes (one-time use).`;
   pairingStatus.textContent='Pair code generated. Share it with your other browser on this LAN.';
  },shareCard);
  pairBtn.id='generatePairCodeBtn';

  const codeWrap=el('div',null,shareCard);
  codeWrap.className='pairing-code-wrap';
  codeWrap.id='pairingCodeWrap';
  codeWrap.hidden=true;
  codeWrap.setAttribute('hidden','');

  const codeBox=el('div',null,codeWrap);
  codeBox.className='pairing-code-box';
  const codeDisplay=el('code','',codeBox);
  codeDisplay.id='pairingCodeDisplay';
  codeDisplay.setAttribute('aria-label','One-time pairing code');
  codeDisplay.onclick=()=>{
   const win=d.defaultView||(typeof window!=='undefined'?window:null);
   const doc=d||(typeof document!=='undefined'?document:null);
   if(win?.getSelection&&doc?.createRange){
    try{
     const range=doc.createRange();
     range.selectNodeContents(codeDisplay);
     const sel=win.getSelection();
     sel.removeAllRanges();
     sel.addRange(range);
    }catch(_){}
   }
  };

  const copyBtn=el('button','Copy code',codeBox);
  copyBtn.type='button';
  copyBtn.id='copyPairCodeBtn';
  copyBtn.className='button';
  copyBtn.onclick=async()=>{
   const text=codeDisplay.textContent;
   if(!text)return;
   const win=d.defaultView||(typeof window!=='undefined'?window:null);
   const doc=d||(typeof document!=='undefined'?document:null);
   const selectCode=()=>{
    if(win?.getSelection&&doc?.createRange){
     try{
      const range=doc.createRange();
      range.selectNodeContents(codeDisplay);
      const sel=win.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
     }catch(_){}
    }
   };
   const clip=win?.navigator?.clipboard||(typeof navigator!=='undefined'?navigator.clipboard:null);
   if(clip&&typeof clip.writeText==='function'){
    try{
     await clip.writeText(text);
     copyBtn.textContent='Copied!';
     pairingStatus.textContent='Pair code copied to clipboard.';
     setTimeout(()=>{copyBtn.textContent='Copy code';},2000);
    }catch(_){
     selectCode();
     pairingStatus.textContent='Clipboard write rejected. Code selected for manual copy.';
     copyBtn.textContent='Select code';
     setTimeout(()=>{copyBtn.textContent='Copy code';},2500);
    }
   }else{
    selectCode();
    pairingStatus.textContent='Clipboard unavailable. Code selected for manual copy.';
    copyBtn.textContent='Select code';
    setTimeout(()=>{copyBtn.textContent='Copy code';},2500);
   }
  };

  const expiresInfo=el('small','',codeWrap);
  expiresInfo.className='field-help';
  expiresInfo.id='pairingCodeExpires';

  const joinCard=el('div',null,pairingGrid);
  joinCard.className='pairing-card';
  el('h4','Join an existing household (enter code)',joinCard);
  el('p','Enter the one-time code generated from another browser on this LAN to switch this browser to that household.',joinCard).className='muted';

  const joinFieldWrap=el('div',null,joinCard);
  joinFieldWrap.className='field-wrap';
  const joinLabel=el('label','Pairing code',joinFieldWrap);
  joinLabel.setAttribute('for','joinPairCodeInput');
  const joinInput=el('input',null,joinFieldWrap);
  joinInput.type='text';
  joinInput.id='joinPairCodeInput';
  joinInput.name='joinPairCode';
  joinInput.placeholder='Paste 43-character pairing code';
  joinInput.autocomplete='off';
  joinInput.spellcheck=false;
  fields.joinPairCode=joinInput;

  const joinBtn=button('Join household',async()=>{
   const code=joinInput.value.trim();
   if(!code){
    pairingStatus.textContent='Enter a pairing code to join.';
    return;
   }
   pairingStatus.textContent='Joining household...';
   try{
    if(typeof client.join==='function'){
     await client.join(code);
    }else{
     const res=await client.request('/api/session/join',{code});
     if(res.csrf_token)client.csrf=res.csrf_token;
     client.clear();
    }
    pairingStatus.textContent='Joined household successfully. Reloading...';
    const win=d.defaultView||(typeof window!=='undefined'?window:null);
    if(win?.location?.reload){
     win.location.reload();
    }
   }catch(err){
    pairingStatus.textContent='Failed to join: '+(err.message||'Invalid or expired code.');
    throw err;
   }
  },joinCard);
  joinBtn.id='joinHouseholdBtn';

  button('Delete selected profile',async()=>{const delId=client.profileId;await client.deleteProfile();removeProfileChecks(delId);workflow.reset();form.reset();resetSection2();yearMenu.setSelectedYears([]);invalidateCoverage();form.inert=true;roofEditor.setRoofs([]);updateCatalogUI(null);await refresh();preview.textContent='';status.textContent='Profile and its jobs deleted.';});
  button('Delete all household data',async()=>{await client.deleteHousehold();clearAllProfileChecks();workflow.reset();form.inert=true;selector.textContent='';preview.textContent='';form.reset();resetSection2();yearMenu.setSelectedYears([]);invalidateCoverage();roofEditor.setRoofs([]);updateCatalogUI(null);status.textContent='Household deleted. Reload to create a fresh session.';});
  button('Reconnect / reload profile list',async()=>{client.clear();workflow.reset();form.reset();resetSection2();yearMenu.setSelectedYears([]);invalidateCoverage();form.inert=true;roofEditor.setRoofs([]);updateCatalogUI(null);preview.textContent='';await refresh();status.textContent='Session connected. Select a profile.';});
  updateCatalogUI(workflow.marketCatalog);
  return {workflow,fields,roofEditor,refresh,ready:()=>client.ready,updateCatalogUI};
 }
 return {numbers,validateDraft,csvMetadata,Workflow,mount,RoofListEditor,getCompass,YearMenu,SUPPORTED_YEARS,GAS_REGIONS,recommendVariant,parseCoordinatePair,getProfileChecks,setProfileChecks,removeProfileChecks,clearAllProfileChecks,Diagnostics};
})();
if(typeof module!=='undefined')module.exports=Onboarding;
if(typeof window!=='undefined')window.Onboarding=Onboarding;
