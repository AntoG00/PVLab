'use strict';
const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
function getCompass(deg) {
  return COMPASS[Math.round((((Number(deg) % 360) + 360) % 360) / 45) % 8];
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
    this.setRoofs(options.initialRoofs || []);
  }

  getRoofs() { return this.roofs.map(r => ({ ...r })); }

  setRoofs(roofs) {
    if(roofs.length>12)throw Error('ROOF_LIMIT: at most twelve planes');
    this.roofs = (roofs || []).map((r, i) => this._sanitize(r, i));
    this.render();
  }

  _sanitize(r, i) {
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
    if(this.roofs.length>=12)throw Error('ROOF_LIMIT: at most twelve planes');
    const r = this._sanitize(roof || {}, this.roofs.length);
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
      row.setAttribute('data-id', r.id);
      const leg = d.createElement('legend');
      leg.textContent = r.label || ('Roof ' + (idx + 1));
      row.appendChild(leg);

      const field = (txt, type, val, min, max, step, onInput) => {
        const lbl = d.createElement('label');
        lbl.textContent = txt + ': ';
        const inp = d.createElement('input');
        inp.type = type;
        inp.value = val;
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
      hint.textContent = ' (' + getCompass(r.azimuth_deg) + ')';
      const azLbl = field('Azimuth (°)', 'number', r.azimuth_deg, 0, 359.99, 0.01, v => {
        r.azimuth_deg = v.trim()===''?NaN:Number(v);
        hint.textContent = ' (' + getCompass(r.azimuth_deg) + ')';
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
      del.textContent = 'Remove ' + r.label;
      del.setAttribute('aria-label', 'Remove ' + r.label);
      del.addEventListener('click', () => this.removeRoof(r.id));
      row.appendChild(del);
      this.listEl.appendChild(row);
    });

    const addBtn = d.createElement('button');
    addBtn.type = 'button';
    addBtn.textContent = '+ Add Roof';
    addBtn.addEventListener('click', () => this.addRoof());
    this.addBtn = addBtn;

    this.container.appendChild(this.statusEl);
    this.container.appendChild(this.listEl);
    this.container.appendChild(addBtn);
  }
}


if (typeof window !== 'undefined') window.RoofListEditor = RoofListEditor;


'use strict';
const Onboarding = (()=>{
 const clone=v=>JSON.parse(JSON.stringify(v));
 const fail=(code,message)=>{throw Object.assign(Error(code+': '+message),{code});};
 function numbers(text,count=12){
  const values=(String(text).includes(',')?String(text).split(','):String(text).includes(';')?String(text).split(';'):String(text).trim().split(/\s+/)).map(v=>v.trim()===''?NaN:Number(v));
  if(values.length!==count||values.some(v=>!Number.isFinite(v)||v<0))fail('MONTHLY_TOTALS','Enter all twelve nonnegative monthly totals; zero is allowed.');return values;
 }
 function validateDraft(profile,load,{gross=false,existingPv=false,reconciled=false}={}){
  if(profile.country!=='IT'||profile.timezone!=='Europe/Rome')fail('UNSUPPORTED_LOCATION','Only Italy / Europe/Rome is supported.');
  if(!gross||existingPv&&!reconciled)fail('GROSS_LOAD_REQUIRED','Reconcile grid readings with existing PV/storage to provide gross household demand.');
  if(!profile.location||!Number.isFinite(profile.location.latitude)||!Number.isFinite(profile.location.longitude)||Math.abs(profile.location.latitude)>90||Math.abs(profile.location.longitude)>180)fail('LOCATION_REQUIRED','Enter valid manual coordinates.');
  if(!profile.consumption.acknowledged)fail('ACKNOWLEDGEMENT_REQUIRED','Confirm modeled timing and synthetic coverage uncertainty.');
  if(profile.consumption.kind!=='annual')numbers((load.monthly_totals||[]).join(','));
  else if(!Number.isFinite(load.policy.annual_kwh)||load.policy.annual_kwh<0)fail('ANNUAL_TOTAL_REQUIRED','Enter nonnegative annual kWh.');
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
 class Workflow {
  constructor(client,{onChange=()=>{}}={}){this.client=client;this.onChange=onChange;this.status='draft';this.profile=null;this.load=null;this.jobId=null;}
  reset(){this.status='draft';this.profile=null;this.load=null;this.jobId=null;}
  async select(id,revision){this.reset();this.client.select(id,revision);const value=await this.client.profileRequest('');this.profile=value.profile;const validation=await this.client.profileRequest('/validation');this.status=validation.ready?'ready':'draft';this.client.ready=this.status==='ready';this.onChange();return value;}
  async create(){this.reset();this.client.clear();const value=await this.client.request('/api/profiles',{});await this.select(value.id,value.revision);return value;}
  async save(profile,load,checks){
   validateDraft(profile,load,checks);this.client.ready=false;this.status='draft';this.onChange();
   await this.client.profileRequest('',{profile},{method:'PATCH'});
   const validation=await this.client.profileRequest('/validation');
   profile=clone(profile);profile.fiscal.rule_revision=validation.fiscal_rule_revision;
   await this.client.profileRequest('',{profile},{method:'PATCH'});this.profile=profile;this.load=clone(load);return this.client.profileRequest('/validation');
  }
  async upload(file,metadata){
   this.status='draft';this.client.ready=false;
   const value=await this.client.profileRequest('/consumption',file,{raw:true,headers:{'Content-Type':'text/csv','X-Consumption-Metadata':JSON.stringify(metadata)}});this.onChange();return value;
  }
  async generate(years,solar,onProgress=()=>{}){
   const generation=this.client.generation;this.status='draft';this.client.ready=false;
   try{
    const preliminary=await this.client.profileRequest('/validation');
    const blockers=(preliminary.blockers||[]).filter(code=>code!=='COMPILED_CANDIDATE_REQUIRED');
    if(blockers.length)fail('PROFILE_NOT_READY',blockers.join(', '));
    const job=await this.client.profileRequest('/solar-jobs',{revision:this.client.revision,years,solar,load:this.load});this.jobId=job.id;
    await this.client.waitJob(job.id,{generation,onProgress});
    await this.client.profileRequest('/compile',{revision:this.client.revision,job_id:job.id});
    const validation=await this.client.profileRequest('/validation');
    if(!validation.ready)fail('PROFILE_NOT_READY','Review validation blockers before evaluation.');
    this.client.ready=true;this.status='ready';this.onChange();return validation;
   }catch(error){if(generation===this.client.generation){this.status='draft';this.client.ready=false;this.onChange();}throw error;}
  }
 }
 function mount({root,client,onReady=()=>{},onClear=()=>{}}){
  const d=root.ownerDocument,fields={},workflow=new Workflow(client),el=(tag,text,parent=root)=>{const n=d.createElement(tag);if(text)n.textContent=text;parent.appendChild(n);return n;};
  root.textContent='';el('h2','Household setup');
  el('p','Inputs persist locally until you explicitly delete them. Deletion removes household inputs and results; no automatic recovery backup is made. Browser-session cookies identify this household on this LAN.');
  const status=el('p');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  const run=fn=>async event=>{const target=event?.currentTarget;if(target?.tagName==='BUTTON')target.disabled=true;try{await fn();}catch(error){if(error.name==='AbortError'||error.code==='STALE_RESPONSE')return;const diag=error.diagnostic;status.textContent=error.message+(diag?' ['+diag.code+(diag.row?' · row '+diag.row:'')+']':'');}finally{if(target?.tagName==='BUTTON')target.disabled=false;}};
  const button=(label,fn,parent=root)=>{const b=el('button',label,parent);b.type='button';b.onclick=run(fn);return b;};
  const field=(parent,key,label,{type='text',options=null,value='',required=true}={})=>{
   const wrap=el('label',label+' ',parent),input=el(options?'select':'input',null,wrap);input.name=key;
   if(options){for(const option of ['',...options]){const o=el('option',option||'Choose…',input);o.value=option;o.defaultSelected=option===value;}}else input.type=type;
   input.value=value;if(!options)input.defaultValue=value;if(type==='number')input.step='any';input.required=required;fields[key]=input;return input;
  };
  const section=(title)=>{const s=el('fieldset');el('legend',title,s);return s;};
  const selector=field(root,'profile','Profile',{options:[],required:false});
  const refresh=async()=>{const data=await client.request('/api/profiles');selector.textContent='';el('option','Select a profile',selector).value='';for(const p of data.profiles){const o=el('option',p.id,selector);o.value=p.id;}selector.value=client.profileId||'';};
  const form=el('form');form.inert=true;form.onsubmit=e=>e.preventDefault();
  const location=section('1 · Location');form.appendChild(location);
  field(location,'country','Country',{options:['IT'],value:'IT'});field(location,'timezone','Timezone',{options:['Europe/Rome'],value:'Europe/Rome'});
  field(location,'latitude','Latitude (manual)',{type:'number'}).step='any';field(location,'longitude','Longitude (manual)',{type:'number'}).step='any';
  const consumption=section('2 · Gross consumption');form.appendChild(consumption);
  field(consumption,'kind','Basis',{options:['interval','monthly','annual']});
  field(consumption,'gross','I provide gross household demand, not grid imports',{type:'checkbox'});
  field(consumption,'existingPv','Existing PV or battery',{type:'checkbox',required:false});field(consumption,'reconciled','Existing PV/battery readings reconciled to gross load',{type:'checkbox',required:false});
  field(consumption,'coverage_start','Measurement period start',{type:'date'});field(consumption,'coverage_end','Measurement period end (exclusive)',{type:'date'});
  field(consumption,'totals','Twelve monthly totals Jan–Dec (kWh, including zero)',{required:false});field(consumption,'annual','Annual kWh (annual basis only)',{type:'number',required:false});
  field(consumption,'variant','Modeled timing variant',{options:['flat','evening','daytime'],value:'evening'});field(consumption,'ack','I acknowledge modeled timing, synthetic coverage and annual-only timing uncertainty',{type:'checkbox'});
  el('p','Monthly/partial CSV: provide all twelve totals. Annual-only: energy follows calendar days; timing is uncertain. Synthetic variants represent sensitivity, not confidence intervals. Repeated DST hours need UTC offsets/fold; nonexistent spring times are rejected. Fixed-clock sources do not recover civil DST truth.',consumption);
  field(consumption,'csv','CSV file',{type:'file',required:false}).accept='.csv';
  field(consumption,'timestamp','Timestamp column',{required:false});field(consumption,'energy','Energy / interval-average power column',{required:false});
  field(consumption,'fold','Optional DST fold column (0/1)',{required:false});
  field(consumption,'unit','CSV unit',{options:['kWh','Wh','average_kW'],required:false});field(consumption,'timestamp_convention','CSV timestamps',{options:['start','end'],required:false});
  field(consumption,'delimiter','CSV delimiter',{options:[',',';','\t'],required:false});field(consumption,'decimal_separator','Decimal separator',{options:['.',','],required:false});field(consumption,'duration_seconds','Interval seconds',{options:['900','3600'],required:false});field(consumption,'csv_start','CSV declared coverage start (ISO timestamp with offset)',{required:false});field(consumption,'csv_end','CSV declared coverage end, exclusive (ISO timestamp with offset)',{required:false});field(consumption,'synthetic_completion','Explicit synthetic timing completion for partial interval coverage',{type:'checkbox',required:false});field(consumption,'fixed_clock','Explicit fixed UTC+1 clock (no civil DST)',{type:'checkbox',required:false});
  const roofs=section('3 · Roof planes');form.appendChild(roofs);
  el('p','Manual entry is always available. Compass: N=0°, E=90°, S=180°, W=270°. Capacity is a user limit, not panel fit or structural/electrical advice. Coarse shading is uniform additional attenuation, not measured shadow geometry.',roofs);
  el('p','Optional local tools/google_solar_helper.py uses your own Google account/key outside this app. No keys or Google responses are accepted here. Cached content has retrieval/expiry restrictions; persistent inputs must be independently entered and verified. Do not assume derived profiles evade retention limits.',roofs);
  const roofRoot=el('div',null,roofs),roofEditor=new RoofListEditor(roofRoot,{onChange:()=>{client.invalidate();workflow.status='draft';onClear();}});
  const thermal=section('4 · Thermal end uses and planned additions');form.appendChild(thermal);
  el('p','Leave unchanged existing electric demand included in gross consumption. Replacement of embedded electrical demand requires separated hourly data. Enter monthly useful heat OR monthly measured carrier input, conversion and efficiency/COP; no personalized defaults.',thermal);
  for(const id of ['space_heat','dhw','cooking']){
   const row=el('fieldset',null,thermal);el('legend',id,row);
   field(row,id+'_enabled','Include this end use',{type:'checkbox',required:false});field(row,id+'_carrier','Carrier',{options:['gas','electricity','biomass','district','none'],required:false});field(row,id+'_action','Planned action',{options:['unchanged','add','replace'],required:false});
   field(row,id+'_included','Already included in gross electrical load',{type:'checkbox',required:false});
   field(row,id+'_heat','Twelve monthly useful heat totals kWh (or leave blank for measured input)',{required:false});field(row,id+'_input','Twelve measured monthly quantities (Smc gas / kg biomass / kWh district or electricity)',{required:false});
   field(row,id+'_conversion','kWh per measured unit / gas PCS',{type:'number',required:false}).step='any';field(row,id+'_factor','Efficiency (non-electric) / COP (electric)',{type:'number',required:false}).step='any';
   field(row,id+'_fixed','Annual fixed carrier costs EUR',{type:'number',required:false});field(row,id+'_variable','Annual variable carrier costs EUR',{type:'number',required:false});field(row,id+'_bill','Annual bill EUR (annual-bill approximation)',{type:'number',required:false});
  }
  for(const id of ['ev','cooling']){field(thermal,id+'_included',id+' already included in gross load',{type:'checkbox',required:false});field(thermal,id+'_action',id+' action',{options:['unchanged','add','replace'],value:'unchanged',required:false});field(thermal,id+'_incremental',id+' incremental annual kWh (add only)',{type:'number',required:false});}
  field(thermal,'baseline_cooling_kwh','Existing cooling annual kWh (explicit, zero allowed)',{type:'number'});
  const market=section('5 · Market and fiscal confirmation');form.appendChild(market);
  field(market,'zone','Wholesale zone',{options:['NORD','CNOR','CSUD','SUD','CALA','SICI','SARD']});field(market,'series','Series (PUN is comparison only)',{options:['NORD','CNOR','CSUD','SUD','CALA','SICI','SARD','PUN']});field(market,'valuation','Valuation',{options:['shape_scaled','absolute_proxy','flat']});field(market,'price_revision','Pinned price catalogue digest (not needed for explicit flat)',{required:false});field(market,'window_start','Price window start',{type:'date'});field(market,'window_end','Price window end (exclusive)',{type:'date'});
  field(market,'fiscal_mode','Gas fiscal mode',{options:['parameters','annual_bill']});field(market,'gas_region','Applicable gas region identifier',{required:false});field(market,'annual_bill_eur','Gas annual bill EUR (approximation mode)',{type:'number',required:false});
  for(const k of ['electric_excise','electric_vat','pmg_price','pmg_nominal_escalation','gse_fee_eur_kw_year','gse_fee_vat','export_irpef','regional_additional_tax','municipal_additional_tax','deduction_rate','gas_excise_0_120','gas_excise_120_480','gas_excise_480_1560','gas_excise_over_1560','gas_regional_0_120','gas_regional_over_120','gas_vat_reduced','gas_vat_standard'])field(market,k,k+' (rates as fractions; EUR where named)',{type:'number',required:false}).step='any';
  for(const k of ['effective_from','effective_to','as_of'])field(market,k,'Fiscal '+k,{type:'date'});field(market,'sources','Fiscal sources (one URL or reference per line)');field(market,'confirmed','I confirm fiscal applicability, sources and rates; these are not automatically current legal advice',{type:'checkbox'});
  const review=section('6 · Review and generate');form.appendChild(review);
  field(review,'years','Historical weather years, comma separated');field(review,'reference_year','Annual allocation calendar year',{type:'number'});field(review,'loss_percent','System loss percent',{type:'number'});field(review,'mounting','Mounting',{options:['Free standing 2025','Integrated 2025']});field(review,'technology','Module technology',{options:['cSi']});
  field(review,'providerConsent','I authorize sending entered coordinates/roof geometry and years to JRC PVGIS 6 SARAH-3 and coordinates/years to Open-Meteo historical ERA5',{type:'checkbox'});
  el('p','Destination: photovoltaic-geographic-information-system.ec.europa.eu/api/v6/power/broadband (JRC PVGIS) and archive-api.open-meteo.com/v1/archive. No fallback to another location or provider. Live availability/terms remain unverified.',review);
  const preview=el('pre',null,review);preview.setAttribute('aria-label','Validation and input review');
  const val=k=>fields[k].value,check=k=>fields[k].checked,num=k=>{if(!val(k).trim())fail('FIELD_REQUIRED',k+' is required.');const n=Number(val(k));if(!Number.isFinite(n)||n<0)fail('NUMBER_REQUIRED',k+' must be finite and nonnegative.');return n;};
  function collect(){
   const kind=val('kind'),coverage={start:val('coverage_start'),end:val('coverage_end'),complete:true},policy={mode:kind==='interval'?'measured':'synthetic',leap_policy:'february_month_hour',reference_year:num('reference_year'),acknowledged:check('ack'),synthetic_completion:kind!=='interval'||check('synthetic_completion'),variant:val('variant')};
   if(kind==='annual')policy.annual_kwh=num('annual');
   const load={monthly_totals:kind==='annual'?null:numbers(val('totals')),policy};
   const end_uses=[];for(const id of ['space_heat','dhw','cooking'])if(check(id+'_enabled')){
    const carrier=val(id+'_carrier'),factor=val(id+'_factor')?num(id+'_factor'):null,monthly=val(id+'_input')?numbers(val(id+'_input')):null;
    const use={id,carrier,measured_input:monthly?monthly.reduce((a,b)=>a+b,0):null,unit:monthly?({gas:'smc',biomass:'kg',district:'kwh',electricity:'kwh'}[carrier]||null):null,monthly_useful_kwh:val(id+'_heat')?numbers(val(id+'_heat')):null,efficiency:carrier==='electricity'?null:factor,cop:carrier==='electricity'?factor:null,annual_fixed_cost:num(id+'_fixed'),annual_variable_cost:num(id+'_variable'),included_in_electric_load:check(id+'_included'),action:val(id+'_action'),artifact_id:null,coverage,period_relation:'same'};
    if(monthly){use.monthly_input=monthly;use.conversion_kwh_per_unit=num(id+'_conversion');}if(val(id+'_bill'))use.annual_bill_eur=num(id+'_bill');end_uses.push(use);
   }
   const parameters={policy_version:'italy-fiscal-v1',mode:val('fiscal_mode'),confirmed:check('confirmed'),effective_from:val('effective_from'),effective_to:val('effective_to'),as_of:val('as_of'),sources:val('sources').split('\n').filter(Boolean)};
   for(const k of Object.keys(fields))if(/^(electric_excise|electric_vat|pmg_price|pmg_nominal_escalation|gse_fee_eur_kw_year|gse_fee_vat|export_irpef|regional_additional_tax|municipal_additional_tax|deduction_rate|gas_excise_.*|gas_regional_.*|gas_vat_.*|annual_bill_eur)$/.test(k)&&val(k)!=='')parameters[k]=num(k);
   const profile={schema_version:1,id:client.profileId,country:val('country'),timezone:val('timezone'),location:{latitude:Number(val('latitude')),longitude:Number(val('longitude'))},consumption:{artifact_id:kind==='interval'?(workflow.profile?.consumption.artifact_id||null):null,kind,coverage,projection_policy:kind==='interval'?(check('fixed_clock')?'standard_month_hour':'civil_month_hour'):'synthetic_v1',acknowledged:check('ack')},roofs:roofEditor.getRoofs(),thermal:{end_uses,electric_loads:['ev','cooling'].map(id=>({id,included_in_electric_load:check(id+'_included'),action:val(id+'_action'),artifact_id:null,incremental_annual_kwh:val(id+'_incremental')?num(id+'_incremental'):null}))},market:{zone:val('zone'),series:val('series'),window_start:val('window_start'),window_end:val('window_end'),valuation_mode:val('valuation'),price_revision:val('price_revision')||null},fiscal:{country:'IT',rule_revision:null,gas_region:val('gas_region')||null,parameters},default_settings:{retail_contract:'fixed',baseline_cooling_kwh:num('baseline_cooling_kwh')}};
   if(!val('latitude').trim()||!val('longitude').trim())fail('LOCATION_REQUIRED','Enter coordinates.');
   return {profile,load,checks:{gross:check('gross'),existingPv:check('existingPv'),reconciled:check('reconciled')}};
  }
  function hydrate(profile){
   if(!profile)return;
   const put=(key,value)=>{if(value!==null&&value!==undefined&&fields[key]){if(fields[key].type==='checkbox')fields[key].checked=Boolean(value);else fields[key].value=String(value);}};
   for(const key of ['country','timezone'])put(key,profile[key]);
   for(const key of ['latitude','longitude'])put(key,profile.location[key]);
   put('kind',profile.consumption.kind);put('ack',profile.consumption.acknowledged);
   put('coverage_start',profile.consumption.coverage.start);put('coverage_end',profile.consumption.coverage.end);
   put('baseline_cooling_kwh',profile.default_settings.baseline_cooling_kwh);
   for(const key of ['zone','series','price_revision','window_start','window_end'])put(key,profile.market[key]);put('valuation',profile.market.valuation_mode);
   put('gas_region',profile.fiscal.gas_region);put('fiscal_mode',profile.fiscal.parameters.mode);
   for(const [key,value]of Object.entries(profile.fiscal.parameters))put(key,key==='sources'?value.join('\n'):value);
   for(const u of profile.thermal.end_uses){
    put(u.id+'_enabled',true);put(u.id+'_carrier',u.carrier);put(u.id+'_action',u.action);put(u.id+'_included',u.included_in_electric_load);
    put(u.id+'_heat',u.monthly_useful_kwh?.join(','));put(u.id+'_input',u.monthly_input?.join(','));put(u.id+'_conversion',u.conversion_kwh_per_unit);put(u.id+'_factor',u.carrier==='electricity'?u.cop:u.efficiency);
    put(u.id+'_fixed',u.annual_fixed_cost);put(u.id+'_variable',u.annual_variable_cost);put(u.id+'_bill',u.annual_bill_eur);
   }
   for(const u of profile.thermal.electric_loads||[]){put(u.id+'_included',u.included_in_electric_load);put(u.id+'_action',u.action);put(u.id+'_incremental',u.incremental_annual_kwh);}
  }
  const save=async()=>{if(!client.profileId)fail('PROFILE_REQUIRED','Create or select a draft first.');if(!form.reportValidity())return false;const v=collect();const result=await workflow.save(v.profile,v.load,v.checks);preview.textContent=JSON.stringify({profile:workflow.profile,validation:result},null,2);status.textContent='Draft saved. Review inputs before generating.';return true;};
  button('Create new draft',async()=>{form.reset();form.inert=true;roofEditor.setRoofs([]);preview.textContent='';await workflow.create();form.inert=false;await refresh();status.textContent='New draft created. Enter all inputs.';onClear();});
  selector.onchange=run(async()=>{const id=selector.value;form.reset();form.inert=true;roofEditor.setRoofs([]);preview.textContent='';status.textContent='Loading profile…';if(!id){workflow.reset();client.clear();return;}await workflow.select(id);form.inert=false;hydrate(workflow.profile);roofEditor.setRoofs(workflow.profile?.roofs||[]);preview.textContent=JSON.stringify(workflow.profile,null,2);status.textContent=workflow.status==='ready'?'Ready profile selected.':'Draft selected. Confirm gross demand and re-enter monthly/annual totals, CSV mapping and weather-generation choices to revise it.';onClear();});
  // Any unsaved input invalidates the ready state immediately.
  form.addEventListener('input',event=>{
   client.invalidate();workflow.status='draft';
   if(['csv','timestamp','energy','fold','unit','timestamp_convention','delimiter','decimal_separator','duration_seconds','fixed_clock','csv_start','csv_end'].includes(event.target?.name)&&workflow.profile?.consumption)workflow.profile.consumption.artifact_id=null;
   onClear();
  });
  button('Save and review draft',save,review);
  button('Validate CSV preview',async()=>{if(!await save())return;if(val('kind')!=='interval')fail('CSV_BASIS_REQUIRED','Choose interval consumption to use a CSV.');const file=fields.csv.files?.[0];if(!file)fail('FILE_REQUIRED','Choose a CSV.');if(file.size>16*1024*1024)fail('FILE_TOO_LARGE','CSV exceeds 16 MiB.');const metadata=csvMetadata({timestamp:val('timestamp'),energy:val('energy'),fold:val('fold'),unit:val('unit'),duration_seconds:val('duration_seconds'),delimiter:val('delimiter'),decimal_separator:val('decimal_separator'),timezone:val('timezone'),timestamp_convention:val('timestamp_convention'),fixed_clock:check('fixed_clock'),existing_pv:check('existingPv'),coverage_start:val('csv_start'),coverage_end:val('csv_end')});if(!val('csv_start')||!val('csv_end'))fail('COVERAGE_REQUIRED','Declare the exact CSV range with UTC offsets. Partial coverage is allowed with all monthly totals.');const result=await workflow.upload(file,metadata);workflow.profile.consumption.artifact_id=result.artifact_id;preview.textContent=JSON.stringify(result,null,2);status.textContent='CSV preview validated. Review coverage and modeled totals.';},consumption);
  button('Generate solar data and compile (retry)',async()=>{
   if(!check('providerConsent'))fail('PROVIDER_CONSENT_REQUIRED','Review coordinate destinations before generation.');
   if(!await save())return;
   if(workflow.profile.consumption.kind==='interval'&&!workflow.profile.consumption.artifact_id)fail('CSV_REQUIRED','Validate the mapped CSV first.');
   const years=val('years').split(/[,\s]+/).filter(Boolean).map(Number);if(!years.length||years.length>30||years.some((v,i)=>!Number.isInteger(v)||v<1900||v>2100||(i&&v<=years[i-1])))fail('YEARS_INVALID','Choose 1–30 sorted unique historical years.');
   await workflow.generate(years,{loss_percent:num('loss_percent'),mounting:val('mounting'),technology:val('technology'),source_policy:{solar:'pvgis6_sarah3',temperature:'openmeteo_era5',hour_mapping:'pvgis6-sarah3-utc-observation-v1'}},state=>{status.textContent='Solar generation: '+state;});status.textContent='Compiled and ready. You may open the calculation views.';
  },review);
  button('Open calculation views',async()=>{const validation=await client.profileRequest('/validation');if(!validation.ready||!client.ready)fail('PROFILE_NOT_READY','Save, generate and compile a valid profile first.');await onReady(workflow.profile);},review);
  button('Delete selected profile',async()=>{await client.deleteProfile();workflow.reset();form.reset();form.inert=true;roofEditor.setRoofs([]);await refresh();preview.textContent='';status.textContent='Profile and its jobs deleted.';});
  button('Delete all household data',async()=>{await client.deleteHousehold();workflow.reset();form.inert=true;selector.textContent='';preview.textContent='';form.reset();roofEditor.setRoofs([]);status.textContent='Household deleted. Reload to create a fresh session.';});
  button('Reconnect / reload profile list',async()=>{client.clear();workflow.reset();form.reset();form.inert=true;roofEditor.setRoofs([]);preview.textContent='';await refresh();status.textContent='Session connected. Select a profile.';});
  return {workflow,fields,roofEditor,refresh,ready:()=>client.ready};
 }
 return {numbers,validateDraft,csvMetadata,Workflow,mount,RoofListEditor,getCompass};
})();
if(typeof module!=='undefined')module.exports=Onboarding;
if(typeof window!=='undefined')window.Onboarding=Onboarding;
