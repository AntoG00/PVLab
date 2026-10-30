/* Run with Node: exercise the actual daily-view renderer with a valid API fixture. */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../feasibility/web/app.js'),'utf8');
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:id==='hourSlider'?'12':'',hidden:false});return elements.get(id);};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const nf=(n,d=0)=>Number(n??0).toLocaleString('en-IE',{maximumFractionDigits:d,minimumFractionDigits:d});
const chartTarget=detail=>`tabindex="0" role="img" aria-label="${esc(detail)}"`;
const table=(headers,rows)=>`<table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(x=>`<td>${esc(x)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
const title=s=>({baseline:'Historical electricity',electrified:'Electrified · gas eliminated',ev:'Electrified + EV'}[s]||s);
const start=source.indexOf('function dayhourDateForYear(');
const end=source.indexOf('async function refreshDayhour()',start);
assert(start>=0&&end>start,'daily renderer functions are present');
const context=vm.createContext({$,esc,nf,chartTarget,table,title,console,dayhourData:null,fixture:null});
const metaStart=source.indexOf('function getWeatherEnsembleMeta(');
const metaEnd=source.indexOf('\nfunction ',metaStart+1);
if(metaStart>=0&&metaEnd>metaStart)vm.runInContext(source.slice(metaStart,metaEnd),context);
vm.runInContext(source.slice(start,end),context);
const now=new Date(),month=String(now.getMonth()+1).padStart(2,'0'),day=String(now.getDate()).padStart(2,'0');
assert.equal(vm.runInContext('dayhourDateForYear(2015)',context),`2015-${month}-${day}`,'initial date maps the browser-local current month/day to 2015');
elements.get('weatherDate').value='2020-02-29';
assert.equal(vm.runInContext('dayhourDateForYear(2015)',context),'2015-02-28','Feb 29 clamps to Feb 28 in a non-leap weather year');
elements.get('weatherDate').value='';
const hours=Array.from({length:24},(_,hour)=>({hour,pv_kwh:hour===12?2.4:0,load_kwh:.7,direct_kwh:hour===12?.7:0,charge_input_kwh:hour===12?1.5:0,discharge_delivered_kwh:hour===20?.5:0,grid_import_kwh:hour===20?.2:0,grid_export_kwh:hour===12?.2:0,battery_loss_kwh:0,soc_start_kwh:hour>=12&&hour<20?1.35:0,soc_end_kwh:hour>=12&&hour<20?1.35:0,balance_error_kwh:0}));
const totals={pv_kwh:2.4,load_kwh:16.8,direct_kwh:.7,charge_input_kwh:1.5,discharge_delivered_kwh:.5,grid_import_kwh:.2,grid_export_kwh:.2,battery_loss_kwh:0};
const data={weather_year:2015,date:'2015-06-21',timezone:'UTC+01:00 fixed local standard time',available_hours:Array.from({length:24},(_,i)=>i),hours,totals,battery_capacity_kwh:5,soc_start_kwh:0,soc_end_kwh:0,max_balance_error_kwh:0,selected_hour:hours[12],first_year:true};
context.fixture=data;
vm.runInContext('renderDayhour(fixture)',context);
assert.equal($('solarFlowTitle').textContent, 'PV generation and household use · kWh per hour');
assert.equal($('gridBatteryTitle').textContent, 'Battery and grid flows · kWh per hour');
assert.equal($('socTitle').textContent, 'Battery state of charge · kWh stored at end of hour');
assert.match($('dayhourStatus').textContent,/2015-06-21/);
assert.match($('hourSummary').innerHTML,/Battery state of charge/);
assert.match($('dailySolarFlow').innerHTML,/Hourly PV production/);
assert.match($('dailyGridBattery').innerHTML,/Battery discharge delivered/);
assert.match($('dailySoc').innerHTML,/Battery state of charge/);
assert.match($('dayTotals').innerHTML,/Maximum hourly energy-balance residual/);
const forecast={...data,weather_count:19,mode:'forecast_2027',forecast_year:2027,scenario:'electrified',representative:'median',weather_year:2015,forecast_note:'historical analogues, not an actual weather forecast',uncertainty_by_hour:Array.from({length:24},(_,hour)=>({hour,pv_kwh:{p10:0,p50:1,p90:2},load_kwh:{p10:.5,p50:.7,p90:.9},grid_import_kwh:{p10:0,p50:.2,p90:.4},grid_export_kwh:{p10:0,p50:.1,p90:.3},soc_end_kwh:{p10:0,p50:1,p90:2}})),annual_scenarios:[{label:'low',weather_year:2014,pv_kwh:4,load_kwh:8,grid_import_kwh:5},{label:'median',weather_year:2015,pv_kwh:5,load_kwh:8,grid_import_kwh:4},{label:'high',weather_year:2011,pv_kwh:6,load_kwh:8,grid_import_kwh:3}]};
context.forecast=forecast;vm.runInContext('renderDayhour(forecast)',context);
assert.match($('dayhourStatus').textContent,/not an actual 2027 weather forecast/);
assert.match($('dayhourStatus').textContent,/Electrified · gas eliminated/);
assert.match($('dayhourUncertainty').innerHTML,/marginal P10 \/ P50 \/ P90/);
assert.match($('dayhourUncertainty').innerHTML,/median/);
assert.match($('dailySolarFlow').innerHTML,/Marginal weather range across 19 runs/);
assert.match($('dailySolarFlow').innerHTML,/source weather year 2015/);
assert.match($('dailySoc').innerHTML,/Marginal weather range across 19 runs/);

// Explicit 21 metadata: tooltips and subtitles reflect 21 runs and not 19
const forecast21={...forecast,weather_count:21};
context.forecast21=forecast21;
vm.runInContext('renderDayhour(forecast21)',context);
assert.match($('dailySolarFlow').innerHTML,/Marginal weather range across 21 runs/);
assert.doesNotMatch($('dailySolarFlow').innerHTML,/across 19 runs/);
assert.match($('dailySoc').innerHTML,/Marginal weather range across 21 runs/);
assert.doesNotMatch($('dailySoc').innerHTML,/across 19 runs/);

// Missing metadata: falls back to generic text and does not invent 19
const forecastMissing={...forecast};
delete forecastMissing.weather_count;
context.forecastMissing=forecastMissing;
vm.runInContext('renderDayhour(forecastMissing)',context);
assert.match($('dailySolarFlow').innerHTML,/Marginal weather range across historical runs/);
assert.doesNotMatch($('dailySolarFlow').innerHTML,/across 19 runs/);
assert.match($('dailySoc').innerHTML,/Marginal weather range across historical runs/);
assert.doesNotMatch($('dailySoc').innerHTML,/across 19 runs/);
// Restore forecast fixture
vm.runInContext('renderDayhour(forecast)',context);

// --- Monthly view fixtures & tests ---
assert.equal(vm.runInContext('maxDaysInMonth(2020, 2)', context), 29, '2020 leap Feb has 29 days');
assert.equal(vm.runInContext('maxDaysInMonth(2027, 2)', context), 28, '2027 non-leap Feb has 28 days');
assert.equal(vm.runInContext('maxDaysInMonth(2027, 4)', context), 30, 'April has 30 days');
assert.equal(vm.runInContext('maxDaysInMonth(2027, 7)', context), 31, 'July has 31 days');

// Test date/month syncing
$('weatherDate').value = '2027-04-15';
$('dayhourMonth').value = '1';
vm.runInContext('syncMonthFromWeatherDate()', context);
assert.equal($('dayhourMonth').value, '4', 'syncMonthFromWeatherDate sets month from date');

$('dayhourMonth').value = '2';
$('dayhourMode').value = 'forecast_2027';
vm.runInContext('syncWeatherDateToMonth()', context);
assert.equal($('weatherDate').value, '2027-02-15', 'syncWeatherDateToMonth sets Feb with clamped day');

$('weatherDate').value = '2027-01-31';
$('dayhourMonth').value = '4';
vm.runInContext('syncWeatherDateToMonth()', context);
assert.equal($('weatherDate').value, '2027-04-30', 'syncWeatherDateToMonth clamps day 31 to 30 in April');

// Monthly Historical Fixture
const monthlyHours = Array.from({length: 24}, (_, hour) => ({
  hour,
  pv_kwh: hour === 12 ? 2.5 : 0,
  load_kwh: 0.8,
  direct_kwh: hour === 12 ? 0.8 : 0,
  charge_input_kwh: hour === 12 ? 1.4 : 0,
  discharge_delivered_kwh: hour === 20 ? 0.6 : 0,
  grid_import_kwh: hour === 20 ? 0.2 : (hour === 12 ? 0 : 0.8),
  grid_export_kwh: hour === 12 ? 0.3 : 0,
  battery_loss_kwh: 0.05,
  soc_start_kwh: hour >= 12 && hour < 20 ? 1.5 : 0.1,
  soc_end_kwh: hour >= 12 && hour < 20 ? 1.5 : 0.1,
  balance_error_kwh: 0,
  sample_count: hour === 0 ? 30 : 31
}));
const monthlyTotals = {
  pv_kwh: 2.5 * 31,
  load_kwh: 0.8 * 743 / 31,
  direct_kwh: 0.8 * 31,
  charge_input_kwh: 1.4 * 31,
  discharge_delivered_kwh: 0.6 * 31,
  grid_import_kwh: 18.2,
  grid_export_kwh: 0.3 * 31,
  battery_loss_kwh: 0.05 * 743
};
const monthlyDailyAverages = {
  pv_kwh: 2.5,
  load_kwh: 0.8 * 24,
  direct_kwh: 0.8,
  charge_input_kwh: 1.4,
  discharge_delivered_kwh: 0.6,
  grid_import_kwh: 18.2 / 31,
  grid_export_kwh: 0.3,
  battery_loss_kwh: 0.05 * 24
};
const monthlyHistorical = {
  period: 'month',
  month: 1,
  month_name: 'January',
  weather_year: 2005,
  year: 2005,
  date: '2005-01',
  timezone: 'UTC+01:00 fixed local standard time',
  resolution: 'Monthly average by hour (31 calendar days, 743 hourly intervals)',
  available_hours: Array.from({length: 24}, (_, i) => i),
  unavailable_hours: [],
  hours: monthlyHours,
  totals: monthlyTotals,
  month_totals: monthlyTotals,
  daily_averages: monthlyDailyAverages,
  days_in_month: 31,
  total_samples: 743,
  battery_capacity_kwh: 5,
  soc_start_kwh: 0.1,
  soc_end_kwh: 0.1,
  mean_soc_kwh: 0.5,
  first_interval_soc_start_kwh: 0,
  last_interval_soc_end_kwh: 0.15,
  max_balance_error_kwh: 1e-15,
  selected_hour: monthlyHours[12],
  first_year: true
};

context.monthlyHistorical = monthlyHistorical;
vm.runInContext('renderDayhour(monthlyHistorical)', context);
assert.equal($('solarFlowTitle').textContent, 'PV generation and household use · mean flows · kWh per hour');
assert.equal($('gridBatteryTitle').textContent, 'Battery and grid flows · mean flows · kWh per hour');
assert.equal($('socTitle').textContent, 'Battery state of charge · mean end-of-hour SOC · kWh stored');
assert.match($('dayhourStatus').textContent, /January 2005 monthly average/);
assert.match($('dayhourStatus').textContent, /743 hourly observations/);
assert.match($('dayhourStatus').textContent, /Arithmetic means across simulated intervals, not a single dispatch day/);
assert.match($('dayChartTitle').textContent, /Monthly average hourly energy flows · January 2005 · kWh per hour \(mean\)/);
assert.match($('hourSummary').innerHTML, /kWh \(mean\)/);
assert.match($('hourSummary').innerHTML, /31 days averaged/);
assert.match($('dailySolarFlow').innerHTML, /Monthly average hourly PV production/);
assert.match($('dailySolarFlow').innerHTML, /kWh\/h mean/);
assert.match($('dailySolarFlow').innerHTML, /monthly mean/);
assert.match($('dailyGridBattery').innerHTML, /Monthly average hourly AC battery charge input/);
assert.match($('dailySoc').innerHTML, /Mean battery state of charge at the end of each hour across the month, in stored kWh/);
assert.match($('dailySoc').innerHTML, /Mean end-of-hour state of charge: 1\.500 kWh stored \(mean across 31 days\)/);
assert.match($('dayTotals').innerHTML, /Monthly energy summary/);
assert.match($('dayTotals').innerHTML, /Month total \(kWh\/month\)/);
assert.match($('dayTotals').innerHTML, /Daily average \(kWh\/day\)/);
assert.match($('dayTotals').innerHTML, /Hourly mean \(kWh\/h\)/);
assert.match($('dayTotals').innerHTML, /SOC is stored energy \(not summed\)/);
assert.match($('dayTotals').innerHTML, /Coverage: 743 hourly observations across 31 calendar days/);
assert.match($('dayTotals').innerHTML, /without assuming missing intervals are zero/);

// Restore daily subheadings on daily re-render
vm.runInContext('renderDayhour(fixture)', context);
assert.equal($('solarFlowTitle').textContent, 'PV generation and household use · kWh per hour');
assert.equal($('gridBatteryTitle').textContent, 'Battery and grid flows · kWh per hour');
assert.equal($('socTitle').textContent, 'Battery state of charge · kWh stored at end of hour');

// Monthly Forecast Fixture
const monthlyForecast = {
  ...monthlyHistorical,
  weather_count: 19,
  mode: 'forecast_2027',
  month: 2,
  month_name: 'February',
  forecast_year: 2027,
  year: 2027,
  date: '2027-02',
  scenario: 'electrified',
  representative: 'median',
  days_in_month: 28,
  total_samples: 28 * 24,
  uncertainty_by_hour: Array.from({length: 24}, (_, hour) => ({
    hour,
    pv_kwh: {p10: 0, p50: 1.2, p90: 2.3},
    load_kwh: {p10: 0.5, p50: 0.8, p90: 1.1},
    grid_import_kwh: {p10: 0, p50: 0.3, p90: 0.6},
    grid_export_kwh: {p10: 0, p50: 0.2, p90: 0.5},
    soc_end_kwh: {p10: 0.2, p50: 1.0, p90: 2.5}
  })),
  annual_scenarios: [
    {label: 'low', weather_year: 2014, pv_kwh: 4, load_kwh: 8, grid_import_kwh: 5},
    {label: 'median', weather_year: 2015, pv_kwh: 5, load_kwh: 8, grid_import_kwh: 4},
    {label: 'high', weather_year: 2011, pv_kwh: 6, load_kwh: 8, grid_import_kwh: 3}
  ]
};

context.monthlyForecast = monthlyForecast;
vm.runInContext('renderDayhour(monthlyForecast)', context);
assert.match($('dayhourStatus').textContent, /February 2027 monthly average/);
assert.match($('dayhourStatus').textContent, /Electrified · gas eliminated/);
assert.match($('dayhourStatus').textContent, /28 calendar days \(672 hourly intervals\)/);
assert.match($('dayhourStatus').textContent, /Historical weather scenarios, not an actual 2027 weather forecast/);
assert.equal($('solarFlowTitle').textContent, 'PV generation and household use · mean flows · kWh per hour');
assert.equal($('gridBatteryTitle').textContent, 'Battery and grid flows · mean flows · kWh per hour');
assert.equal($('socTitle').textContent, 'Battery state of charge · mean end-of-hour SOC · kWh stored');
assert.match($('dayChartTitle').textContent, /Monthly average hourly energy flows · February 2027 · kWh per hour \(mean\)/);
assert.match($('dayhourUncertainty').innerHTML, /Monthly mean hourly range across all 19 historical weather runs/);
assert.match($('dayhourUncertainty').innerHTML, /marginal P10 \/ P50 \/ P90/);
assert.match($('dayhourUncertainty').innerHTML, /Selected hour \(12:00–13:00\) · monthly mean kWh/);
assert.match($('dailySolarFlow').innerHTML, /Marginal monthly mean range across 19 runs \(P10–P90\)/);
assert.match($('dailySoc').innerHTML, /Marginal monthly mean range across 19 runs \(P10–P90\)/);

// Explicit 21 metadata for monthly forecast
const monthlyForecast21 = { ...monthlyForecast, weather_count: 21 };
context.monthlyForecast21 = monthlyForecast21;
vm.runInContext('renderDayhour(monthlyForecast21)', context);
assert.match($('dayhourUncertainty').innerHTML, /Monthly mean hourly range across all 21 historical weather runs/);
assert.doesNotMatch($('dayhourUncertainty').innerHTML, /across all 19 historical weather runs/);
assert.match($('dailySolarFlow').innerHTML, /Marginal monthly mean range across 21 runs \(P10–P90\)/);
assert.doesNotMatch($('dailySolarFlow').innerHTML, /across 19 runs/);
assert.match($('dailySoc').innerHTML, /Marginal monthly mean range across 21 runs \(P10–P90\)/);
assert.doesNotMatch($('dailySoc').innerHTML, /across 19 runs/);

// Missing metadata for monthly forecast: generic fallback, no invented 19
const monthlyForecastMissing = { ...monthlyForecast };
delete monthlyForecastMissing.weather_count;
context.monthlyForecastMissing = monthlyForecastMissing;
vm.runInContext('renderDayhour(monthlyForecastMissing)', context);
assert.match($('dayhourUncertainty').innerHTML, /Monthly mean hourly range across all historical weather runs/);
assert.doesNotMatch($('dayhourUncertainty').innerHTML, /across all 19 historical weather runs/);
assert.match($('dailySolarFlow').innerHTML, /Marginal monthly mean range across historical runs \(P10–P90\)/);
assert.doesNotMatch($('dailySolarFlow').innerHTML, /across 19 runs/);
assert.match($('dailySoc').innerHTML, /Marginal monthly mean range across historical runs \(P10–P90\)/);
assert.doesNotMatch($('dailySoc').innerHTML, /across 19 runs/);
// Restore monthlyForecast
vm.runInContext('renderDayhour(monthlyForecast)', context);

// --- Modern Monthly Forecast with within-month day SOC statistics ---
const modernMonthlyHours = monthlyHours.map(h => ({
  ...h,
  soc_end_median_kwh: h.hour === 12 ? 8.28 : 1.5,
  soc_end_p10_kwh: h.hour === 12 ? 0.46 : 0.2,
  soc_end_p90_kwh: h.hour === 12 ? 8.28 : 2.5,
  soc_full_days: h.hour === 12 ? 22 : 0,
  soc_sample_days: h.sample_count,
  soc_full_fraction: h.hour === 12 ? 22 / 31 : 0.0,
}));
const modernMonthlyForecast = {
  ...monthlyForecast,
  battery_capacity_kwh: 8.28,
  hours: modernMonthlyHours,
  selected_hour: modernMonthlyHours[12],
};
context.modernMonthlyForecast = modernMonthlyForecast;
vm.runInContext('renderDayhour(modernMonthlyForecast)', context);
assert.equal($('socTitle').textContent, 'Battery state of charge · median end-of-hour SOC across days · kWh stored');
assert.match($('dailySoc').innerHTML, /Median across days/);
assert.match($('dailySoc').innerHTML, /10th–90th percentile across days/);
assert.match($('dailySoc').innerHTML, /Battery full on 22\/31 sampled days · at end of 12:00–13:00/);
assert.match($('dailySoc').innerHTML, /polygon/);
assert.match($('dailySoc').innerHTML, /Median end-of-hour state of charge: 8\.280 kWh stored/);
assert.match($('dailySoc').innerHTML, /10th–90th percentile across days: 0\.460–8\.280 kWh/);
assert.match($('dailySoc').innerHTML, /Monthly mean for context: 1\.500 kWh/);
assert.match($('dailySoc').innerHTML, /Battery full: 22\/31 sampled days \(71\.0%\) at end of hour/);
assert.match($('dailySoc').innerHTML, /Marginal distribution across days; not a single dispatch day\./);
// Old weather-uncertainty tooltip should NOT be appended to the median curve dots
assert.doesNotMatch($('dailySoc').innerHTML, /Marginal monthly mean range across 19 runs/);
// Uncertainty table explicitly labels monthly mean SOC across weather runs
assert.match($('dayhourUncertainty').innerHTML, /Monthly mean battery SOC across weather runs/);
// Selected hour card in hourSummary
assert.match($('hourSummary').innerHTML, /8\.28 <small>kWh stored \(median\)<\/small>/);
assert.match($('hourSummary').innerHTML, /Battery full on 22\/31 sampled days · at end of 12:00–13:00/);
assert.match($('hourSummary').innerHTML, /10th–90th percentile: 0\.46–8\.28 kWh · mean 1\.50 kWh/);
// Other selected hour cards remain arithmetic means
assert.match($('hourSummary').innerHTML, /PV production/);
assert.match($('hourSummary').innerHTML, /kWh \(mean\)/);

// Zero capacity edge case in modern monthly mode
const zeroCapHours = modernMonthlyHours.map(h => ({
  ...h,
  soc_end_median_kwh: 0,
  soc_end_p10_kwh: 0,
  soc_end_p90_kwh: 0,
  soc_full_days: 0,
  soc_full_fraction: 0,
}));
const zeroCapMonthly = {
  ...modernMonthlyForecast,
  battery_capacity_kwh: 0,
  hours: zeroCapHours,
  selected_hour: zeroCapHours[12],
};
context.zeroCapMonthly = zeroCapMonthly;
vm.runInContext('renderDayhour(zeroCapMonthly)', context);
assert.match($('dailySoc').innerHTML, /No battery installed/);
assert.doesNotMatch($('dailySoc').innerHTML, /Battery full on/);
assert.match($('hourSummary').innerHTML, /No battery installed/);

// --- Weather mean day & month contract ---
const makeMean = base => {
  const hours = base.hours.map(h => ({...h, soc_end_kwh: h.hour === 12 ? 3.4 : 0.8, soc_end_median_kwh: h.hour === 12 ? 1.7 : 0.4}));
  return {
    ...base,
    aggregation: 'weather_mean',
    representative: 'mean',
    weather_count: 19,
    weather_year: null,
    hours,
    selected_hour: hours[12],
    uncertainty_by_hour: base.uncertainty_by_hour.map(u => ({...u, soc_end_kwh: {p10: 0.5, p50: 2.1, p90: 4.4}})),
  };
};

const meanDay = makeMean(forecast);
context.meanDay = meanDay;
vm.runInContext('renderDayhour(meanDay)', context);
assert.match($('dayhourStatus').textContent, /19.*(?:arithmetic )?mean/i);
assert.match($('dayhourStatus').textContent + $('dayhourUncertainty').innerHTML, /P10[–-]P90/);
assert.doesNotMatch($('dayhourStatus').textContent, /\bnull\b/);
assert.doesNotMatch($('dailySoc').innerHTML, /weather year null/i);
assert.match($('dailySoc').innerHTML, /polygon/);
assert.match($('dailySoc').innerHTML, /3\.4/);
assert.match($('dailySoc').innerHTML, /P10[–-]P90/);

const meanMonth = makeMean(modernMonthlyForecast);
context.meanMonth = meanMonth;
vm.runInContext('renderDayhour(meanMonth)', context);
assert.match($('dayhourStatus').textContent, /19.*(?:arithmetic )?mean/i);
assert.match($('dayhourStatus').textContent + $('dayhourUncertainty').innerHTML, /P10[–-]P90/);
assert.doesNotMatch($('dayhourStatus').textContent, /\bnull\b/);
assert.doesNotMatch($('dailySoc').innerHTML, /weather year null/i);
assert.doesNotMatch($('socTitle').textContent, /median end-of-hour SOC across days/i);
assert.doesNotMatch($('hourSummary').innerHTML, /kWh stored \(median\)/);
assert.match($('socTitle').textContent, /mean/i);
assert.match($('hourSummary').innerHTML, /3\.4/);
assert.match($('hourSummary').innerHTML, /0\.50–4\.40 kWh/, 'SoC card uses weather percentiles, not legacy across-day percentiles');
assert.doesNotMatch($('hourSummary').innerHTML, /1\.7.*median/i);
assert.match($('dailySoc').innerHTML, /polygon/);
assert.match($('dailySoc').innerHTML, /3\.4/);
assert.match($('dailySoc').innerHTML, /P10[–-]P90/);

// --- Selection functions & event regression tests ---
const selStart = source.indexOf('function selectDayhourPeriod()');
const selEnd = source.indexOf('// One delegated tooltip survives', selStart);
assert(selStart >= 0 && selEnd > selStart, 'selection functions are present');
const evStart = source.indexOf("$('dayhourMode').onchange=");
const evEnd = source.indexOf("$('reset').onclick=", evStart);
assert(evStart >= 0 && evEnd > evStart, 'event handler wiring is present');

context.current = { result: { weather_years: [2005, 2015, 2020] } };
context.refreshCalls = 0;
vm.runInContext('function refreshDayhour() { refreshCalls++; }', context);
vm.runInContext(source.slice(selStart, selEnd), context);
vm.runInContext(source.slice(evStart, evEnd), context);

// 1. Test setupDayhour initial state and defaults
$('weatherYear').value = '';
$('dayhourPeriod').value = 'day';
$('dayhourMode').value = 'historical';
vm.runInContext('setupDayhour()', context);
assert.equal($('weatherYear').value, '2015', 'setupDayhour defaults to 2015');
assert.match($('weatherDate').value, /^2015-\d\d-\d\d$/, 'setupDayhour sets weatherDate for 2015');
assert.equal($('weatherYearControl').hidden, false, 'weatherYearControl visible in historical');
assert.equal($('representativeControl').hidden, true, 'representativeControl hidden in historical');

// 2. Test Historical July monthly selection preserved when switching to forecast and back
$('dayhourMode').value = 'historical';
$('weatherYear').value = '2015';
$('dayhourPeriod').value = 'month';
$('dayhourMonth').value = '7'; // July
$('dayhourMonth').onchange();
assert.equal($('weatherDate').value.slice(0, 7), '2015-07', 'weatherDate synced to July 2015');

const callsBeforeMode = context.refreshCalls;
$('dayhourMode').value = 'forecast_2027';
$('dayhourMode').onchange();
assert.equal(context.refreshCalls, callsBeforeMode + 1, 'mode change triggers refreshDayhour');
assert.equal($('weatherYearControl').hidden, true, 'weatherYearControl hidden in forecast');
assert.equal($('representativeControl').hidden, false, 'representativeControl visible in forecast');
assert.equal($('dayhourMonth').value, '7', 'July is preserved in month dropdown when switching to forecast');
assert.equal($('weatherDate').value.slice(0, 7), '2027-07', 'weatherDate preserves July (2027-07) in forecast');

// Switch back to historical mode
$('dayhourMode').value = 'historical';
$('weatherYear').value = '2015';
$('dayhourMode').onchange();
assert.equal($('weatherYearControl').hidden, false, 'weatherYearControl shown in historical');
assert.equal($('representativeControl').hidden, true, 'representativeControl hidden in historical');
assert.equal($('dayhourMonth').value, '7', 'July is preserved in month dropdown when switching back to historical');
assert.equal($('weatherDate').value.slice(0, 7), '2015-07', 'weatherDate preserves July (2015-07) in historical');

// 3. Test Leap Feb 29 date clamped to Feb 28 on forecast 2027 and non-leap historical years
$('dayhourMode').value = 'historical';
$('weatherYear').value = '2020';
$('dayhourPeriod').value = 'day';
$('weatherDate').value = '2020-02-29';
$('weatherDate').onchange();
assert.equal($('dayhourMonth').value, '2', 'month synced to February');

// Switch to forecast mode (2027 is non-leap)
$('dayhourMode').value = 'forecast_2027';
$('dayhourMode').onchange();
assert.equal($('weatherDate').value, '2027-02-28', 'Feb 29 clamped to Feb 28 in forecast 2027');
assert.equal($('dayhourMonth').value, '2', 'month remains February');

// Switch to non-leap historical year 2015
$('dayhourMode').value = 'historical';
$('weatherYear').value = '2015';
$('dayhourMode').onchange();
assert.equal($('weatherDate').value, '2015-02-28', 'Feb 28 preserved in non-leap 2015');

// In leap year 2020, change weatherYear directly via weatherYear.onchange
$('weatherYear').value = '2020';
$('weatherDate').value = '2020-02-29';
$('weatherYear').value = '2015';
$('weatherYear').onchange();
assert.equal($('weatherDate').value, '2015-02-28', 'changing weatherYear from 2020 to 2015 clamps Feb 29 to Feb 28');

// In monthly mode with leap year 2020 Feb 29 date
$('dayhourPeriod').value = 'month';
$('dayhourMonth').value = '2';
$('weatherYear').value = '2020';
$('weatherDate').value = '2020-02-29';
$('dayhourMode').value = 'forecast_2027';
$('dayhourMode').onchange();
assert.equal($('weatherDate').value, '2027-02-28', 'monthly mode clamps Feb 29 to Feb 28 when switching to forecast');
assert.equal($('dayhourMonth').value, '2');

// 4. Test Period toggles between day and month keeping month/date in sync
$('dayhourMode').value = 'forecast_2027';
$('dayhourPeriod').value = 'day';
$('weatherDate').value = '2027-07-15';
$('weatherDate').onchange();
assert.equal($('dayPickerControl').hidden, false);
assert.equal($('monthPickerControl').hidden, true);
assert.equal($('dayhourMonth').value, '7');

// Toggle to monthly period
$('dayhourPeriod').value = 'month';
$('dayhourPeriod').onchange();
assert.equal($('dayPickerControl').hidden, true);
assert.equal($('monthPickerControl').hidden, false);
assert.equal($('dayhourMonth').value, '7', 'switching to month syncs month from 2027-07-15');

// User selects October (month 10) in monthly mode
$('dayhourMonth').value = '10';
$('dayhourMonth').onchange();
assert.equal($('weatherDate').value, '2027-10-15', 'changing month updates weatherDate to 2027-10-15');

// Toggle back to daily period
$('dayhourPeriod').value = 'day';
$('dayhourPeriod').onchange();
assert.equal($('dayPickerControl').hidden, false);
assert.equal($('monthPickerControl').hidden, true);
assert.equal($('weatherDate').value, '2027-10-15', 'switching back to day preserves October date');

// Test clamping on 31-day month to 30-day month
$('weatherDate').value = '2027-01-31';
$('dayhourPeriod').value = 'month';
$('dayhourPeriod').onchange();
assert.equal($('dayhourMonth').value, '1');
$('dayhourMonth').value = '4';
$('dayhourMonth').onchange();
assert.equal($('weatherDate').value, '2027-04-30', 'changing to 30-day month clamps day 31 to 30');
$('dayhourPeriod').value = 'day';
$('dayhourPeriod').onchange();
assert.equal($('weatherDate').value, '2027-04-30', 'switching back to day retains 2027-04-30');

// 5. Hour navigation buttons and event firing
$('hourSlider').value = '12';
$('previousHour').onclick();
assert.equal($('hourSlider').value, '11', 'previousHour steps down');
assert.equal($('selectedHourLabel').textContent, '11:00–12:00');
$('nextHour').onclick();
assert.equal($('hourSlider').value, '12', 'nextHour steps up');
assert.equal($('selectedHourLabel').textContent, '12:00–13:00');

// Controls trigger refreshDayhour
const callsBeforeControls = context.refreshCalls;
$('dayhourScenario').onchange();
assert.equal(context.refreshCalls, callsBeforeControls + 1);
$('weatherRepresentative').onchange();
assert.equal(context.refreshCalls, callsBeforeControls + 2);

console.log('Day & hour renderer fixture passed.');

// --- Hourly flow chart weather range (P10-P90 whiskers) tests ---
context.rangeSeries = [['charge_input_kwh', '#e6b65b', 'Charge'], ['grid_import_kwh', '#9865a8', 'Import']];
const makeRangeU = () => Array.from({length: 24}, (_, hour) => ({
  hour,
  charge_input_kwh: hour === 12 ? {p10: 0.2, p50: 1.0, p90: 8.5} : {p10: 0.1, p50: 0.4, p90: 0.8},
  grid_import_kwh: {p10: 0.05, p50: 0.15, p90: 0.3}
}));
const countRanges = () => ($('dailyGridBattery').innerHTML.match(/class=["']hourly-weather-range["']/g) || []).length;

// Test Day range rendering, legend, and outlier bounds
context.rangeFixture = {...context.meanDay, uncertainty_by_hour: makeRangeU()};
vm.runInContext("renderHourlyFlowChart('dailyGridBattery', rangeFixture, 12, rangeSeries)", context);
assert.equal(countRanges(), 48, 'Day forecast draws 48 weather range whisker paths (2 series * 24h)');
assert.match($('dailyGridBattery').innerHTML, /whiskers:\s*P10[–-]P90 weather spread/, 'mean Day includes whisker legend');
assert.match($('dailyGridBattery').innerHTML, /Charge: 1\.500 kWh/, 'bar tooltip value unchanged with 3 decimals');
const rangeGroups = [...$('dailyGridBattery').innerHTML.matchAll(/<g class="hourly-weather-range"[^>]*>(.*?)<\/g>/gs)];
assert.equal(rangeGroups.length, 48, 'Coordinate checks cover every rendered range');
for (const [, markup] of rangeGroups) {
  const ys = [...markup.matchAll(/\by[12]="([^"]+)"/g)].map(m => Number(m[1]));
  assert.equal(ys.length, 6, 'Each whisker has a stem and two caps');
  ys.forEach(y => assert(Number.isFinite(y) && y >= 18 && y <= 274, `y=${y} inside plot area for outlier P90`));
}

// Test Month range rendering and legend
context.rangeFixture = {...context.meanMonth, uncertainty_by_hour: makeRangeU()};
vm.runInContext("renderHourlyFlowChart('dailyGridBattery', rangeFixture, 12, rangeSeries)", context);
assert.equal(countRanges(), 48, 'Month forecast draws 48 weather range whisker paths');
assert.match($('dailyGridBattery').innerHTML, /whiskers:\s*P10[–-]P90 weather spread/, 'mean Month includes whisker legend');

// Historical mode must omit ranges even if stale uncertainty_by_hour is present
context.rangeFixture = {...context.fixture, mode: 'historical', uncertainty_by_hour: makeRangeU()};
vm.runInContext("renderHourlyFlowChart('dailyGridBattery', rangeFixture, 12, rangeSeries)", context);
assert.equal(countRanges(), 0, 'historical mode draws 0 range whiskers despite uncertainty present');

// Missing / null / NaN / inverted ranges omitted without NaN SVG
context.rangeFixture = {
  ...context.forecast,
  uncertainty_by_hour: [
    {hour: 0, charge_input_kwh: null, grid_import_kwh: {p10: 0.1, p90: 0.5}},
    {hour: 1, charge_input_kwh: {p10: NaN, p90: 1.0}},
    {hour: 2, charge_input_kwh: {p10: 2.0, p90: 1.0}},
    {hour: 3}
  ]
};
vm.runInContext("renderHourlyFlowChart('dailyGridBattery', rangeFixture, 12, rangeSeries)", context);
assert.doesNotMatch($('dailyGridBattery').innerHTML, /NaN/, 'SVG output must not contain NaN');
assert(countRanges() < 48, 'invalid or missing ranges omitted');
