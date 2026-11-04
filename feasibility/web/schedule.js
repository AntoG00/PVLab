// feasibility/web/schedule.js
// Standalone classic script for Switch Hour & Seasonal Schedule Optimizer UI

(function(global) {
  'use strict';

  const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  function safeEsc(str) {
    if (typeof esc === 'function') return esc(str);
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function safeNf(val, decimals) {
    if (val == null) return '—';
    if (typeof nf === 'function') return nf(val, decimals);
    return Number(val).toLocaleString('en-IE', {
      minimumFractionDigits: decimals ?? 0,
      maximumFractionDigits: decimals ?? 0
    });
  }

  function formatMonthRange(start, end, activeCount) {
    if (start == null || end == null) return { visible: '—', title: '' };
    const sName = MONTH_NAMES[start - 1] || String(start);
    const eName = MONTH_NAMES[end - 1] || String(end);
    const count = activeCount != null ? ` (${activeCount} mo)` : '';
    return {
      visible: `${sName}–${eName}`,
      title: `${sName}–${eName}${count}`
    };
  }

  function getActiveMonthList(s, e) {
    const list = [];
    if (s == null || e == null) return list;
    if (s <= e) {
      for (let m = s; m <= e; m++) list.push(m);
    } else {
      for (let m = s; m <= 12; m++) list.push(m);
      for (let m = 1; m <= e; m++) list.push(m);
    }
    return list;
  }

  function formatPairedCell({
    meanVal,
    p10Val,
    p90Val,
    improvingYears,
    totalYears,
    isEur = false,
    isBaseline = false,
    compact = false,
    unit = null
  }) {
    if (isBaseline) {
      if (isEur) {
        let npvStr = '—';
        if (meanVal != null && !isNaN(Number(meanVal))) {
          const val = Number(meanVal);
          npvStr = (val > 0 ? `+€${safeNf(val, 0)}` : (val < 0 ? `-€${safeNf(Math.abs(val), 0)}` : '€0'));
        }
        return safeEsc(npvStr);
      }
      return '—';
    }

    let mainStr = '—';
    if (meanVal != null && !isNaN(Number(meanVal))) {
      const val = Number(meanVal);
      if (isEur) {
        mainStr = (val > 0 ? `+€${safeNf(val, 0)}` : (val < 0 ? `-€${safeNf(Math.abs(val), 0)}` : '€0'));
      } else {
        const numStr = safeNf(Math.abs(val), 1);
        const signedNum = (val > 0 ? `+${safeNf(val, 1)}` : (val < 0 ? `-${numStr}` : `${safeNf(0, 1)}`));
        mainStr = compact ? signedNum : `${signedNum} kWh`;
      }
    }

    const parts = [];
    const hasP10 = p10Val != null && !isNaN(Number(p10Val));
    const hasP90 = p90Val != null && !isNaN(Number(p90Val));

    if (hasP10 || hasP90) {
      let rangeStr = '';
      if (hasP10 && hasP90) {
        if (isEur) {
          const v10 = Number(p10Val);
          const v90 = Number(p90Val);
          const s10 = v10 > 0 ? `+€${safeNf(v10, 0)}` : (v10 < 0 ? `-€${safeNf(Math.abs(v10), 0)}` : '€0');
          const s90 = v90 > 0 ? `+€${safeNf(v90, 0)}` : (v90 < 0 ? `-€${safeNf(Math.abs(v90), 0)}` : '€0');
          rangeStr = `${s10}–${s90}`;
        } else {
          const v10 = Number(p10Val);
          const v90 = Number(p90Val);
          const s10 = v10 < 0 ? `-${safeNf(Math.abs(v10), 1)}` : safeNf(v10, 1);
          const s90 = v90 < 0 ? `-${safeNf(Math.abs(v90), 1)}` : safeNf(v90, 1);
          rangeStr = compact ? `${s10}–${s90}` : `${s10}–${s90} kWh`;
        }
      } else {
        const single = hasP10 ? Number(p10Val) : Number(p90Val);
        if (isEur) {
          rangeStr = single > 0 ? `+€${safeNf(single, 0)}` : (single < 0 ? `-€${safeNf(Math.abs(single), 0)}` : '€0');
        } else {
          const sVal = single < 0 ? `-${safeNf(Math.abs(single), 1)}` : safeNf(single, 1);
          rangeStr = compact ? sVal : `${sVal} kWh`;
        }
      }
      const titleUnit = unit || (isEur ? '€/y' : 'kWh/y');
      const titleText = `P10–P90 spread: ${rangeStr} ${compact ? titleUnit + ' ' : ''}across ${safeEsc(totalYears)} weather years (not a confidence interval or future probability)`;
      parts.push(`<span title="${safeEsc(titleText)}">${safeEsc(rangeStr)}</span>`);
    }

    if (improvingYears != null && !isNaN(Number(improvingYears))) {
      const imp = Number(improvingYears);
      const unitStr = imp === 1 ? 'year' : 'years';
      const fullCountTitle = totalYears != null
        ? `${imp}/${totalYears} weather ${unitStr} improved vs Self-Use baseline`
        : `${imp} weather ${unitStr} improved vs Self-Use baseline`;
      const shortCountStr = totalYears != null ? `${imp}/${totalYears} ↑` : `${imp} ↑`;
      parts.push(`<span title="${safeEsc(fullCountTitle)}">${safeEsc(shortCountStr)}</span>`);
    }

    if (parts.length === 0) {
      return safeEsc(mainStr);
    }

    const titleAttr = `title="Historical weather spread across ${safeEsc(totalYears)} weather years (not a confidence interval or future probability)"`;
    const secondLine = parts.join(' · ');
    return `<div>${safeEsc(mainStr)}</div><div class="schedule-paired-range muted" ${titleAttr}>${secondLine}</div>`;
  }

  function getConfigKey(cfg) {
    if (!cfg) return null;
    const priority = String(cfg.routing_priority || (cfg.status === 'benchmark' ? 'dynamic_reserve' : (cfg.status === 'baseline' ? 'baseline' : 'scheduled_hybrid'))).toLowerCase();
    if (priority === 'dynamic_reserve' || cfg.status === 'benchmark' || Boolean(cfg.is_benchmark)) {
      return 'dynamic_reserve';
    }
    if (priority === 'baseline' || cfg.status === 'baseline') {
      return 'baseline';
    }
    const sw = cfg.routing_switch_hour ?? cfg.switch_hour;
    const start = cfg.scheduled_start_month ?? cfg.start;
    const end = cfg.scheduled_end_month ?? cfg.end;
    return `${priority}:${sw}:${start}:${end}`;
  }

  function formatRecLabel(rec, type, rowsData) {
    if (!rec) return '';
    const isDynamic = String(rec.routing_priority || '').toLowerCase() === 'dynamic_reserve' ||
                      rec.status === 'benchmark' || Boolean(rec.is_benchmark);
    const recKey = getConfigKey(rec);
    const matchedRow = (rowsData || []).find(r => getConfigKey(r) === recKey) || {};

    if (isDynamic) {
      if (type === 'npv') {
        const val = rec.net_energy_value_delta_eur ?? matchedRow.net_energy_value_delta_eur;
        if (val != null && Number.isFinite(Number(val))) {
          const numVal = Number(val);
          const sign = numVal >= 0 ? '+' : '-';
          return `Dynamic Reserve · ${sign}€${safeNf(Math.abs(numVal), 0)}/yr`;
        }
        return 'Dynamic Reserve';
      }
      if (type === 'clipping') {
        const saved = rec.clipping_saved_kwh ?? matchedRow.clipping_saved_kwh;
        if (saved != null && Number.isFinite(Number(saved))) {
          const numVal = Number(saved);
          const sign = numVal >= 0 ? '+' : '-';
          return `Dynamic Reserve · ${sign}${safeNf(Math.abs(numVal), 0)} kWh saved`;
        }
        return 'Dynamic Reserve';
      }
      return 'Dynamic Reserve';
    }

    const startM = rec.scheduled_start_month ?? matchedRow.scheduled_start_month ?? 3;
    const endM = rec.scheduled_end_month ?? rec.end ?? matchedRow.scheduled_end_month ?? matchedRow.end ?? 9;
    const sw = rec.routing_switch_hour ?? rec.switch_hour ?? matchedRow.routing_switch_hour ?? matchedRow.switch_hour ?? 12;

    const startName = MONTH_NAMES[startM - 1] || 'Mar';
    const endName = MONTH_NAMES[endM - 1] || 'Sep';
    const baseSchedule = `${startName}–${endName} · switch ${sw}:00`;

    if (type === 'npv') {
      const val = rec.net_energy_value_delta_eur ?? matchedRow.net_energy_value_delta_eur;
      if (val != null && Number.isFinite(Number(val))) {
        const numVal = Number(val);
        const sign = numVal >= 0 ? '+' : '-';
        return `${baseSchedule} · ${sign}€${safeNf(Math.abs(numVal), 0)}/yr`;
      }
      return baseSchedule;
    }

    if (type === 'clipping') {
      const saved = rec.clipping_saved_kwh ?? matchedRow.clipping_saved_kwh;
      if (saved != null && Number.isFinite(Number(saved))) {
        const numVal = Number(saved);
        const sign = numVal >= 0 ? '+' : '-';
        return `${baseSchedule} · ${sign}${safeNf(Math.abs(numVal), 0)} kWh saved`;
      }
      return baseSchedule;
    }

    return baseSchedule;
  }

  function renderSwitchHourOptimizer(data) {
    const target = document.getElementById('switchHourOptimizer');
    if (!target) return;

    const opt = data?.schedule_optimizer;
    if (!opt || !Array.isArray(opt.rows) || opt.rows.length === 0) {
      target.innerHTML = '';
      return;
    }

    const rowsData = opt.rows;
    const hasExplicitNpv = Boolean(opt.recommendation_npv);
    const hasExplicitClip = Boolean(opt.recommendation_clipping);
    const rawRecNpv = opt.recommendation_npv || (opt.recommendation_clipping ? null : opt.recommendation) || null;
    const rawRecClipping = opt.recommendation_clipping || (opt.recommendation_npv ? null : opt.recommendation) || null;

    const npvKey = getConfigKey(rawRecNpv);
    const clipKey = getConfigKey(rawRecClipping);
    const isDual = Boolean(rawRecNpv && rawRecClipping && npvKey && clipKey && npvKey !== clipKey);

    const elapsed = opt.elapsed_seconds;
    const timeBasis = opt.time_basis;
    const horizonYears = opt.horizon_years ?? 20;

    const elapsedStr = elapsed != null ? ` (${Number(elapsed).toFixed(2)}s)` : '';
    const basisStr = timeBasis ? safeEsc(timeBasis) : '';
    const metaStr = basisStr ? `${basisStr}${elapsedStr}` : (elapsedStr ? elapsedStr.trim() : '');

    const capturedData = data;
    const capturedFingerprint = typeof inputFingerprint === 'function' ? inputFingerprint() : null;

    function applyConfig(targetConfig) {
      if (!targetConfig) return;
      if (typeof loanPending !== 'undefined' && loanPending) {
        console.warn('Schedule optimizer apply skipped: loanPending');
        return;
      }
      if (typeof pending !== 'undefined' && pending) {
        console.warn('Schedule optimizer apply skipped: pending');
        return;
      }
      if (typeof document !== 'undefined' && document.querySelector && document.querySelector('.chart-pending')) {
        return;
      }
      if (capturedFingerprint !== null && typeof inputFingerprint === 'function' && capturedFingerprint !== inputFingerprint()) {
        return;
      }
      if (typeof behaviorMatrixCache !== 'undefined' && behaviorMatrixCache !== capturedData) {
        console.warn('Schedule optimizer apply skipped: stale behaviorMatrixCache');
        return;
      }
      if (typeof acceptedInputFingerprint !== 'undefined' && typeof inputFingerprint === 'function' && typeof settings !== 'undefined' && typeof quote !== 'undefined') {
        if (acceptedInputFingerprint !== inputFingerprint(settings, quote)) {
          console.warn('Schedule optimizer apply skipped: acceptedInputFingerprint mismatch');
          return;
        }
      }

      const priority = String(targetConfig.routing_priority || (targetConfig.status === 'benchmark' ? 'dynamic_reserve' : 'scheduled_hybrid')).toLowerCase();

      if (typeof settings !== 'undefined') {
        if (priority === 'dynamic_reserve') {
          settings.routing_priority = 'dynamic_reserve';
        } else {
          settings.routing_priority = targetConfig.routing_priority || 'scheduled_hybrid';
          settings.routing_switch_hour = targetConfig.routing_switch_hour ?? targetConfig.switch_hour ?? 12;
          settings.scheduled_start_month = targetConfig.scheduled_start_month ?? 3;
          settings.scheduled_end_month = targetConfig.scheduled_end_month ?? targetConfig.end ?? 9;
        }
      }

      if (typeof renderControls === 'function') renderControls();
      if (typeof schedule === 'function') schedule();
    }

    const hasAnyP25 = rowsData.some(r => r.summer_p25_peak_soc_pct != null
      || Object.prototype.hasOwnProperty.call(r, 'min_p25_peak_soc_pct'));
    const headers = [
      { label: 'Switch', title: 'Switch hour or strategy' },
      { label: 'Months', title: 'Active months' },
      { label: 'Curt. kWh · avg', title: 'PV curtailed · arithmetic mean across weather years; matches the annual Sankey and monthly clipping profile' },
      { label: 'Saved kWh/y', title: 'Clipping saved · paired arithmetic mean / yr' },
      {
        label: hasAnyP25 ? 'Min P25 SoC' : 'Peak SoC',
        title: hasAnyP25
          ? 'P25 Peak SoC · minimum active-month median-across-weather monthly P25 daily peak'
          : 'P25 Peak SoC · summer median of monthly peak medians across weather years'
      },
      { label: 'Import kWh · avg', title: 'Annual grid imports · average across weather years; hover values for P10–P90 weather variation' },
      { label: 'Autarky · avg', title: '1 − average annual grid imports / average annual demand; same energy basis as the annual Sankey' },
      { label: 'Year 1 €/y', title: 'Net energy value Δ · paired arithmetic mean Year 1' },
      { label: `${horizonYears}Y NPV €`, title: `Lifetime equity NPV delta · paired mean over ${horizonYears} years vs Self-Use; each historical weather profile repeats throughout the horizon` },
      { label: 'Best for', title: 'Optimization eligibility, verification status, and winner identity' },
      { label: 'Apply', title: 'Apply schedule configuration to settings' }
    ];

    const tableRows = rowsData.map((row, rowIndex) => {
      const isBenchmark = String(row.status || '').toLowerCase() === 'benchmark' ||
                          String(row.routing_priority || '').toLowerCase() === 'dynamic_reserve' ||
                          Boolean(row.is_benchmark);
      const isBaseline = !isBenchmark && (
        String(row.status || '').toLowerCase() === 'baseline' ||
        String(row.routing_priority || '').toLowerCase() === 'battery_first' ||
        String(row.routing_priority || '').toLowerCase() === 'baseline' ||
        (row.switch_hour == null && row.routing_switch_hour == null)
      );

      const sw = row.switch_hour ?? row.routing_switch_hour;
      const swStr = isBaseline ? 'Self-Use' : (isBenchmark ? 'Dynamic Reserve' : (sw != null ? `${sw}:00` : '—'));
      const swTitle = isBaseline
        ? 'Self-Use baseline 24/7'
        : (isBenchmark
          ? 'Dynamic Reserve (Look-Ahead) benchmark · daily look-ahead reserve, not a fixed switch-hour schedule'
          : (sw != null ? `Switch hour ${sw}:00` : ''));
      const swCell = swTitle ? `<span title="${safeEsc(swTitle)}">${safeEsc(swStr)}</span>` : safeEsc(swStr);

      let monthsCell = '—';
      if (isBenchmark) {
        monthsCell = '<span title="Dynamic look-ahead across all eligible periods">Look-ahead</span>';
      } else if (!isBaseline) {
        const startM = row.scheduled_start_month;
        const endM = row.scheduled_end_month ?? row.end;
        const activeCount = Array.isArray(row.active_months)
          ? row.active_months.length
          : (typeof row.active_months === 'number' ? row.active_months : null);

        if (startM != null && endM != null) {
          const mObj = formatMonthRange(startM, endM, activeCount);
          monthsCell = `<span title="${safeEsc(mObj.title)}">${safeEsc(mObj.visible)}</span>`;
        } else if (activeCount != null) {
          monthsCell = `<span title="${safeEsc(`${activeCount} active months`)}">${safeEsc(`${activeCount} mo`)}</span>`;
        }
      }

      const clippedStr = row.clipped_kwh != null ? safeNf(row.clipped_kwh, 1) : '—';
      const clippedTitle = row.clipped_kwh != null ? `${safeNf(row.clipped_kwh, 1)} kWh` : '';
      const clippedCell = clippedTitle ? `<span title="${safeEsc(clippedTitle)}">${safeEsc(clippedStr)}</span>` : safeEsc(clippedStr);
      const hasP25 = row.summer_p25_peak_soc_pct != null;
      const socVal = hasP25 ? row.summer_p25_peak_soc_pct : (hasAnyP25 ? null : row.summer_max_soc_pct);
      const socStr = socVal != null ? `${safeNf(socVal, 1)}%` : '—';
      const socTitle = hasP25
        ? `Minimum active-month median-across-weather monthly P25 daily peak: ${safeNf(socVal, 1)}%${row.summer_max_soc_pct != null ? ` (legacy median: ${safeNf(row.summer_max_soc_pct, 1)}%)` : ''}`
        : `Summer median of monthly peak medians: ${safeNf(socVal, 1)}%`;
      const socCell = `<span title="${safeEsc(socTitle)}">${safeEsc(socStr)}</span>`;
      const importStr = row.grid_import_kwh != null ? safeNf(row.grid_import_kwh, 0) : '—';
      const hasImportRange = [row.grid_import_p10_kwh, row.grid_import_p90_kwh].every(v => v != null && Number.isFinite(Number(v)));
      const importTitle = row.grid_import_kwh != null ? `Average: ${safeNf(row.grid_import_kwh, 0)} kWh${hasImportRange ? ` · P10–P90: ${safeNf(row.grid_import_p10_kwh, 0)}–${safeNf(row.grid_import_p90_kwh, 0)} kWh across weather years` : ''}` : '';
      const importCell = importTitle ? `<span title="${safeEsc(importTitle)}">${safeEsc(importStr)}</span>` : safeEsc(importStr);
      const autarkyStr = row.autarky != null ? `${safeNf(row.autarky * 100, 1)}%` : '—';
      const hasAutarkyRange = [row.autarky_p10, row.autarky_p90].every(v => v != null && Number.isFinite(Number(v)));
      const autarkyTitle = `1 − average imports / average demand${hasAutarkyRange ? ` · Annual autarky P10–P90: ${safeNf(row.autarky_p10 * 100, 1)}–${safeNf(row.autarky_p90 * 100, 1)}% across weather years` : ''}`;
      const autarkyCell = `<span title="${safeEsc(autarkyTitle)}">${safeEsc(autarkyStr)}</span>`;

      const totalYears = row.weather_year_count ?? opt.weather_year_count ?? null;

      const clippingSavedCell = formatPairedCell({
        meanVal: row.clipping_saved_kwh,
        p10Val: row.clipping_saved_p10_kwh,
        p90Val: row.clipping_saved_p90_kwh,
        improvingYears: row.clipping_improving_years,
        totalYears,
        isEur: false,
        isBaseline,
        compact: true
      });

      const netEnergyValueCell = formatPairedCell({
        meanVal: row.net_energy_value_delta_eur,
        p10Val: row.net_energy_value_delta_p10_eur,
        p90Val: row.net_energy_value_delta_p90_eur,
        improvingYears: row.value_improving_years,
        totalYears,
        isEur: true,
        isBaseline,
        compact: true
      });

      const equityNpvCell = formatPairedCell({
        meanVal: isBaseline ? 0 : row.equity_npv_delta,
        p10Val: row.equity_npv_delta_p10,
        p90Val: row.equity_npv_delta_p90,
        improvingYears: row.equity_npv_improving_years,
        totalYears,
        isEur: true,
        isBaseline,
        compact: true,
        unit: '€'
      });

      const statuses={baseline:'Self-Use baseline',benchmark:'Benchmark',feasible:'Eligible',disabled:'Physical dispatch required',zero_battery:'No battery',no_eligible_months:'No positive-value months',infeasible_qualifying:'No qualifying months',infeasible_soc:'Peak SoC drop infeasible',infeasible_bill:'No bill saving',infeasible_npv:'No NPV gain',infeasible_clipping:'No clipping reduction',infeasible_format:'Unavailable',infeasible_verification:'Verification failed'};
      let statusVisible = '—';
      let statusTitle = '';
      if (row.status === 'benchmark') {
        statusVisible = 'Benchmark';
        statusTitle = 'Dynamic look-ahead benchmark (unconstrained by fixed schedule)';
      } else if (row.status === 'feasible') {
        if (row.metrics_basis === 'verified_trace') {
          statusVisible = 'Verified';
          statusTitle = 'Eligible · verified trace';
        } else {
          statusVisible = 'Estimated';
          statusTitle = 'Eligible · estimate';
        }
      } else if (row.status) {
        statusVisible = row.status === 'baseline' ? 'Baseline' : (statuses[row.status] || String(row.status));
        statusTitle = statuses[row.status] || String(row.status);
      }
      const statusCell = statusTitle ? `<span title="${safeEsc(statusTitle)}">${safeEsc(statusVisible)}</span>` : safeEsc(statusVisible);

      const rowKey = getConfigKey(row);
      const isNpvWinner = Boolean(npvKey && rowKey === npvKey);
      const isClipWinner = Boolean(clipKey && rowKey === clipKey);

      let optimalForCell = '—';
      if (isNpvWinner && isClipWinner) {
        optimalForCell = '<div style="display:flex;flex-direction:column;gap:2px;"><span class="badge" style="display:inline-block;white-space:normal;padding:2px 4px;font-size:0.75rem;line-height:1.2;" title="Optimal for lifetime equity NPV (Arbitrage + Clipping)">★ Best NPV</span><span class="badge" style="display:inline-block;white-space:normal;padding:2px 4px;font-size:0.75rem;line-height:1.2;" title="Optimal for clipping reduction (Max kWh Saved)">★ Min Clipping</span></div>';
      } else if (isNpvWinner) {
        optimalForCell = '<span class="badge" style="display:inline-block;white-space:normal;padding:2px 4px;font-size:0.75rem;line-height:1.2;" title="Optimal for lifetime equity NPV (Arbitrage + Clipping)">★ Best NPV</span>';
      } else if (isClipWinner) {
        optimalForCell = '<span class="badge" style="display:inline-block;white-space:normal;padding:2px 4px;font-size:0.75rem;line-height:1.2;" title="Optimal for clipping reduction (Max kWh Saved)">★ Min Clipping</span>';
      } else {
        optimalForCell = statusCell;
      }

      const rowStatus = String(row.status || '').toLowerCase();
      const isPlaceholder = rowStatus === 'no_eligible_months' || rowStatus === 'no_eligible' ||
                            rowStatus === 'disabled' || rowStatus === 'zero_battery' ||
                            rowStatus === 'infeasible_format' || rowStatus === 'infeasible_qualifying' ||
                            (row.clipped_kwh == null && row.net_energy_value_delta_eur == null && row.grid_import_kwh == null);
      const canApplyRow = !isBaseline && !isPlaceholder && (isBenchmark || sw != null);
      let applyCell = '—';
      if (canApplyRow) {
        applyCell = `<button type="button" class="btn btn-xs btn-outline-primary schedule-apply-row-btn" data-row-idx="${rowIndex}" style="padding:2px 6px;font-size:0.75rem;line-height:1.2;white-space:nowrap;cursor:pointer;" title="Apply this configuration">Apply</button>`;
      }

      return [
        swCell,
        monthsCell,
        clippedCell,
        clippingSavedCell,
        socCell,
        importCell,
        autarkyCell,
        netEnergyValueCell,
        equityNpvCell,
        optimalForCell,
        applyCell
      ];
    });

    const colWidths = [10, 8, 7, 12, 8, 8, 7, 12, 9, 13, 6];
    const colgroupHtml = `<colgroup>${colWidths.map(w => `<col style="width:${w}%;">`).join('')}</colgroup>`;
    const theadHtml = `<thead><tr>${headers.map(h => `<th style="padding:6px 3px;font-size:10px;line-height:1.2;white-space:normal;" title="${safeEsc(h.title)}">${safeEsc(h.label)}</th>`).join('')}</tr></thead>`;
    const tbodyHtml = `<tbody>${tableRows.map(r => `<tr>${r.map((c, i) => {
      const isWrap = i === 0 || i === 9;
      return `<td style="padding:6px 3px;font-size:12px;line-height:1.2;vertical-align:middle;${isWrap ? 'white-space:normal;' : 'white-space:nowrap;'}">${c}</td>`;
    }).join('')}</tr>`).join('')}</tbody>`;
    const renderedTable = `<table class="schedule-optimizer-table" style="table-layout:fixed;width:100%;min-width:0;font-size:12px;border-collapse:collapse;">${colgroupHtml}${theadHtml}${tbodyHtml}</table>`;

    let headerButtonsHtml = '';
    if (isDual) {
      const npvLabel = formatRecLabel(rawRecNpv, 'npv', rowsData);
      const clipLabel = formatRecLabel(rawRecClipping, 'clipping', rowsData);
      headerButtonsHtml = `
        <div class="schedule-optimizer-actions" style="display:flex;gap:8px;flex-direction:column;align-items:flex-end;margin-left:auto;max-width:100%;">
          <button type="button" class="btn btn-sm btn-primary apply-optimal-btn solar-choice-apply" style="width:auto;margin-top:0;background:color-mix(in srgb,var(--green) 70%,#000) !important;color:#fff !important;border-color:var(--green) !important;" id="applyOptimalScheduleBtn" data-rec-type="npv">
            Apply Max-NPV schedule (${safeEsc(npvLabel)})
          </button>
          <button type="button" class="btn btn-sm btn-outline-primary apply-optimal-btn solar-choice-apply secondary" style="width:auto;margin-top:0;background:transparent !important;color:var(--green) !important;border-color:var(--green) !important;" id="applyMinClippingScheduleBtn" data-rec-type="clipping">
            Apply Min-Clipping schedule (${safeEsc(clipLabel)})
          </button>
        </div>
      `;
    } else if (hasExplicitNpv && !hasExplicitClip) {
      const npvLabel = formatRecLabel(rawRecNpv, 'npv', rowsData);
      headerButtonsHtml = `
        <button type="button" class="btn btn-sm btn-primary apply-optimal-btn solar-choice-apply" style="width:auto;margin-top:0;background:color-mix(in srgb,var(--green) 70%,#000) !important;color:#fff !important;border-color:var(--green) !important;" id="applyOptimalScheduleBtn" data-rec-type="npv">
          Apply Max-NPV schedule (${safeEsc(npvLabel)})
        </button>
      `;
    } else if (!hasExplicitNpv && hasExplicitClip) {
      const clipLabel = formatRecLabel(rawRecClipping, 'clipping', rowsData);
      headerButtonsHtml = `
        <button type="button" class="btn btn-sm btn-outline-primary apply-optimal-btn solar-choice-apply secondary" style="width:auto;margin-top:0;background:transparent !important;color:var(--green) !important;border-color:var(--green) !important;" id="applyOptimalScheduleBtn" data-rec-type="clipping">
          Apply Min-Clipping schedule (${safeEsc(clipLabel)})
        </button>
      `;
    } else {
      const singleRec = rawRecNpv || rawRecClipping || null;
      let singleLabel = 'no recommendation';
      if (singleRec) {
        if (!hasExplicitNpv && !hasExplicitClip && getConfigKey(singleRec) !== 'dynamic_reserve') {
          const sM = singleRec.scheduled_start_month ?? 3;
          const eM = singleRec.scheduled_end_month ?? singleRec.end ?? 9;
          const h = singleRec.routing_switch_hour ?? singleRec.switch_hour ?? 12;
          singleLabel = `${MONTH_NAMES[sM - 1] || 'Mar'}–${MONTH_NAMES[eM - 1] || 'Sep'} · switch ${h}:00`;
        } else {
          singleLabel = formatRecLabel(singleRec, 'npv', rowsData);
        }
      }
      headerButtonsHtml = `
        <button type="button" class="btn btn-sm btn-primary apply-optimal-btn solar-choice-apply" style="width:auto;margin-top:0;background:color-mix(in srgb,var(--green) 70%,#000) !important;color:#fff !important;border-color:var(--green) !important;" id="applyOptimalScheduleBtn" ${!singleRec ? 'disabled' : ''}>
          ${singleRec ? `Apply optimal schedule (${safeEsc(singleLabel)})` : 'Apply optimal schedule (no recommendation)'}
        </button>
      `;
    }

    let totalTaxFraction = 0;
    let hasTax = false;
    if (typeof settings !== 'undefined' && settings) {
      if (typeof settings.export_irpef === 'number' && Number.isFinite(settings.export_irpef)) {
        totalTaxFraction += settings.export_irpef;
        hasTax = true;
      }
      if (typeof settings.regional_additional_tax === 'number' && Number.isFinite(settings.regional_additional_tax)) {
        totalTaxFraction += settings.regional_additional_tax;
        hasTax = true;
      }
      if (typeof settings.municipal_additional_tax === 'number' && Number.isFinite(settings.municipal_additional_tax)) {
        totalTaxFraction += settings.municipal_additional_tax;
        hasTax = true;
      }
    }
    const taxPercent = hasTax ? totalTaxFraction * 100 : null;

    let rampYears = null;
    let rampFactor = null;
    if (typeof settings !== 'undefined' && settings) {
      if (typeof settings.export_price_future_years === 'number' && Number.isFinite(settings.export_price_future_years)) {
        rampYears = settings.export_price_future_years;
      }
      const f = settings.actual_price_future_factor ?? settings.export_price_future_factor;
      if (typeof f === 'number' && Number.isFinite(f)) {
        rampFactor = f;
      }
    }

    const isEmpiricalCsud = typeof settings !== 'undefined' && settings?.export_price_profile === 'empirical_csud';

    let profileExplanation = '';
    if (isEmpiricalCsud) {
      profileExplanation = ' Fixed schedules capture empirical CSUD 07:00–10:00 AM morning price arbitrage versus 12:00–13:00 midday physical inverter clipping by deferring battery charging until solar peak, while Dynamic Reserve continuously maintains headroom on predicted clipping days.';
    } else {
      profileExplanation = ' Without empirical CSUD export pricing, schedules focus on avoiding 12:00–13:00 midday physical inverter clipping rather than 07:00–10:00 AM morning tariff arbitrage, while all-day Dynamic Reserve adapts to forecast irradiance.';
    }

    const toleranceExplanation = ' Optimization balances lifetime NPV against physical curtailment; active months may accept minor monthly value loss within clipping tolerances to maximize overall seasonal savings.';

    let taxRampStr = '';
    if (taxPercent != null && rampYears != null) {
      taxRampStr = ` Financial metrics evaluate lifetime equity NPV accounting for ${safeNf(taxPercent, 1)}% export tax (${safeNf(rampYears, 0)}-year price ramp${rampFactor != null ? ` to factor ${safeNf(rampFactor, 2)}` : ''}).`;
    } else if (taxPercent != null) {
      taxRampStr = ` Financial metrics evaluate lifetime equity NPV accounting for ${safeNf(taxPercent, 1)}% export tax.`;
    } else if (rampYears != null) {
      taxRampStr = ` Financial metrics evaluate lifetime equity NPV with a ${safeNf(rampYears, 0)}-year price ramp.`;
    }

    target.innerHTML = `
      <div class="card schedule-optimizer-card" style="margin-top:16px;">
        <div class="schedule-optimizer-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
          <div>
            <h4 style="margin:0;font-size:0.95rem;font-weight:600;">Switch-hour trade-off · 09:00–14:00</h4>
            ${metaStr ? `<span class="muted" style="font-size:0.8rem;">${metaStr}</span>` : ''}
          </div>
          ${headerButtonsHtml}
        </div>
        <div class="table-scroll-wrapper">
          ${renderedTable}
        </div>
        <small class="schedule-optimizer-note muted" title="Recommendations compare paired lifetime equity NPV and clipping saved versus Self-Use in the same weather year.">Headline energy columns and Year 1 value show arithmetic means across all weather years, matching the annual Sankey and monthly clipping profile for the same applied settings; autarky is 1 − average imports / average demand. Sub-lines show P10–P90 historical weather spread and improving weather years. Hover imports and autarky for P10–P90 weather ranges. Fixed schedules evaluated across lifetime equity NPV and physical clipping saved axes; ranges: P10–P90; ↑: weather years improved. All selected spans verified. Historical weather spread, not a confidence interval or future probability.</small>
        <p class="schedule-optimizer-explanation muted" style="margin:8px 0 0 0;font-size:0.8rem;line-height:1.4;">
          <strong>Dispatch dynamics:</strong>${profileExplanation}${toleranceExplanation}${taxRampStr}
        </p>
      </div>
    `;

    const applyNpvBtn = target.querySelector('#applyOptimalScheduleBtn');
    if (applyNpvBtn) {
      applyNpvBtn.addEventListener('click', function() {
        const targetRec = isDual ? rawRecNpv : (rawRecNpv || rawRecClipping || null);
        if (targetRec) applyConfig(targetRec);
      });
    }

    if (isDual) {
      const applyClipBtn = target.querySelector('#applyMinClippingScheduleBtn');
      if (applyClipBtn && rawRecClipping) {
        applyClipBtn.addEventListener('click', function() {
          applyConfig(rawRecClipping);
        });
      }
    }

    const rowApplyBtns = (target.querySelectorAll ? target.querySelectorAll('.schedule-apply-row-btn') : null) || [];
    Array.from(rowApplyBtns).forEach(btn => {
      btn.addEventListener('click', function() {
        const idx = parseInt(this.dataset.rowIdx, 10);
        const r = rowsData[idx];
        if (r) applyConfig(r);
      });
    });
  }

  function resetSwitchHourOptimizer(message) {
    const target = document.getElementById('switchHourOptimizer');
    if (!target) return;
    if (message) {
      const isPending = typeof message === 'string' && (message.includes('Waiting for dispatch simulation') || message.includes('Simulation pending'));
      const spinnerText = isPending ? '<span class="progress-spinner" aria-hidden="true"></span> ' : '';
      const busyAttrs = isPending ? ' role="status" aria-live="polite" aria-busy="true"' : '';
      target.innerHTML = `<div class="schedule-optimizer-reset muted" style="padding:12px;text-align:center;"${busyAttrs}><p style="margin:0;display:inline-flex;align-items:center;justify-content:center;gap:8px;">${spinnerText}${safeEsc(message)}</p></div>`;
    } else {
      target.innerHTML = '';
    }
  }

  let scheduleListenerAttached = false;
  function initScheduleEventListeners() {
    if (scheduleListenerAttached) return;
    if (typeof document === 'undefined') return;
    scheduleListenerAttached = true;

    document.addEventListener('click', function(e) {
      const pill = e.target.closest('#pvClippingTable [data-schedule-month]');
      if (!pill) return;

      // disabled when pending
      if (pill.disabled || pill.hasAttribute('disabled') || pill.getAttribute('aria-disabled') === 'true') return;
      if (typeof loanPending !== 'undefined' && loanPending) return;
      if (typeof pending !== 'undefined' && pending) return;
      if (typeof acceptedInputFingerprint !== 'undefined' && typeof inputFingerprint === 'function' && typeof settings !== 'undefined' && typeof quote !== 'undefined') {
        if (acceptedInputFingerprint !== inputFingerprint(settings, quote)) return;
      }
      if (document.querySelector('.chart-pending')) return;
      if (typeof settings === 'undefined' || typeof behaviorMatrixCache === 'undefined' || !behaviorMatrixCache) return;

      const m = parseInt(pill.dataset.scheduleMonth, 10);
      if (isNaN(m) || m < 1 || m > 12) return;

      const activePriority = (typeof behaviorMatrixCache !== 'undefined' && behaviorMatrixCache?.selected_config?.routing_priority) || settings.routing_priority;
      const isScheduled = activePriority === 'scheduled_hybrid';

      let start = parseInt(settings.scheduled_start_month, 10);
      let end = parseInt(settings.scheduled_end_month, 10);
      if (isNaN(start)) start = 3;
      if (isNaN(end)) end = 9;

      if (!isScheduled) {
        settings.routing_priority = 'scheduled_hybrid';
        settings.scheduled_start_month = m;
        settings.scheduled_end_month = m;
      } else {
        settings.routing_priority = 'scheduled_hybrid';
        const activeList = getActiveMonthList(start, end);
        const isActive = activeList.includes(m);

        if (isActive) {
          if (activeList.length <= 1) {
            // single month remove switches battery_first
            settings.routing_priority = 'battery_first';
          } else if (m === start) {
            // Trim start
            settings.scheduled_start_month = (start % 12) + 1;
          } else if (m === end) {
            // Trim end
            settings.scheduled_end_month = end === 1 ? 12 : end - 1;
          } else {
            // Interior removal trims shorter side
            const idx = activeList.indexOf(m);
            const distFromStart = idx + 1;
            const distToEnd = activeList.length - idx;
            if (distFromStart <= distToEnd) {
              settings.scheduled_start_month = (m % 12) + 1;
            } else {
              settings.scheduled_end_month = m === 1 ? 12 : m - 1;
            }
          }
        } else {
          // Add month to contiguous inclusive range (wrapped supported)
          const distToStart = (start - m + 12) % 12;
          const distFromEnd = (m - end + 12) % 12;
          if (distToStart < distFromEnd) {
            settings.scheduled_start_month = m;
          } else {
            settings.scheduled_end_month = m;
          }
          settings.routing_priority = 'scheduled_hybrid';
        }
      }

      if (typeof renderControls === 'function') renderControls();
      if (typeof schedule === 'function') schedule();
    });

    // Disclose range semantics via mouseover/title
    document.addEventListener('mouseover', function(e) {
      const pill = e.target.closest('#pvClippingTable [data-schedule-month]');
      if (!pill || typeof settings === 'undefined') return;

      const m = parseInt(pill.dataset.scheduleMonth, 10);
      if (isNaN(m) || m < 1 || m > 12) return;

      const mName = MONTH_NAMES[m - 1] || String(m);
      const activePriority = (typeof behaviorMatrixCache !== 'undefined' && behaviorMatrixCache?.selected_config?.routing_priority) || settings.routing_priority;
      const isScheduled = activePriority === 'scheduled_hybrid';

      let start = parseInt(settings.scheduled_start_month, 10);
      let end = parseInt(settings.scheduled_end_month, 10);
      if (isNaN(start)) start = 3;
      if (isNaN(end)) end = 9;

      if (!isScheduled) {
        pill.title = `Switch to Scheduled AM/PM mode starting with ${mName}`;
        return;
      }

      const activeList = getActiveMonthList(start, end);
      const isActive = activeList.includes(m);

      if (!isActive) {
        const distToStart = (start - m + 12) % 12;
        const distFromEnd = (m - end + 12) % 12;
        pill.title = distToStart < distFromEnd
          ? `Add ${mName} (extends schedule start)`
          : `Add ${mName} (extends schedule end)`;
      } else if (activeList.length <= 1) {
        pill.title = `Remove ${mName} (switches work mode to Self-Use 24/7)`;
      } else if (m === start) {
        pill.title = `Remove ${mName} (advances schedule start to ${MONTH_NAMES[start % 12]})`;
      } else if (m === end) {
        pill.title = `Remove ${mName} (retreats schedule end to ${MONTH_NAMES[(end === 1 ? 12 : end - 1) - 1]})`;
      } else {
        const idx = activeList.indexOf(m);
        const distFromStart = idx + 1;
        const distToEnd = activeList.length - idx;
        pill.title = distFromStart <= distToEnd
          ? `Remove ${mName} (interior month: trims shorter start side)`
          : `Remove ${mName} (interior month: trims shorter end side)`;
      }
    });
  }

  initScheduleEventListeners();

  global.renderSwitchHourOptimizer = renderSwitchHourOptimizer;
  global.resetSwitchHourOptimizer = resetSwitchHourOptimizer;

})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));
