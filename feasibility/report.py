"""Render engine results without performing financial or physical calculations."""
from .legacy_profile import legacy_text
from pathlib import Path
from datetime import datetime,timezone
import html,json,math
from . import engine
HERE=Path(__file__).resolve().parent

def _legacy_report_label():
    """Optional private adapter metadata; v2 never reads the legacy label."""
    from .runtime_context import runtime_if_initialized
    ctx = runtime_if_initialized()
    if ctx is None or ctx.household_profile is not None:
        return 'PV Lab'
    from .profile_store import ProfileStore
    store = ProfileStore()
    with store._directory(()) as fd:
        import os
        if 'legacy-report-labels.json' not in os.listdir(fd):
            return 'PV Lab'
        value = json.loads(store._read(fd, 'legacy-report-labels.json', 4096))
    label = value.get('place_label') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('schema_version') != 1 or type(label) is not str or
            not 1 <= len(label) <= 120 or not label.replace(' ', '').isalpha()):
        raise ValueError('Invalid legacy report metadata')
    return label


def profile_disclosures(profile, audit, revision, years, solar_parameters=None):
    """Presentation provenance only; never infer measurement from modeled arrays."""
    projection = audit.get('projection', {})
    fiscal = profile['fiscal']
    thermal = profile['thermal'].get('end_uses', [])
    return {
        'profile_id': profile['id'], 'input_revision': revision,
        'quality': {
            'measured_training_fraction': projection.get('measured_training_fraction'),
            'synthetic_training_fraction': projection.get('synthetic_training_fraction'),
            'projected_output_fraction': audit.get('synthesized_fraction'),
        },
        'weather_years': list(years),
        'market': dict(profile['market']),
        'solar_parameters': solar_parameters,
        'shading_fractions': {r['id']: r['shading_fraction'] for r in profile['roofs']},
        'solar_loss': 'AC-equivalent PVGIS input; uniform additional attenuation, not measured beam geometry; compiled losses applied once.',
        'thermal': [{'id': u['id'], 'carrier': u['carrier'], 'action': u['action']} for u in thermal],
        'fiscal': {'rule_revision': fiscal.get('rule_revision'), 'region': fiscal.get('gas_region'),
                   'as_of': fiscal['parameters'].get('as_of'), 'mode': fiscal['parameters'].get('mode'),
                   'effective_from': fiscal['parameters'].get('effective_from'), 'effective_to': fiscal['parameters'].get('effective_to')},
        'limitations': [
            'Gross household electricity demand is required. Grid imports alone omit behind-the-meter generation and are insufficient without reconciliation.',
            'All target hourly loads are projected models. Measured/synthetic training fractions describe the source shape, not measured target hours.',
            'Historical weather min–max is a scenario range, not a confidence interval.',
            'Synthetic timing scenario sensitivity: pending independent flat/daytime/evening jobs; no range has been calculated.',
            'PUN export is a comparison benchmark only. Retail contracts are fixed only; indexed retail billing is unsupported.',
            'Fiscal parameters are user-confirmed approximations; current legal applicability and provider availability/terms remain unverified.',
            'Thermal replacements require explicit baseline carriers and separated embedded electric demand; no inferred subtraction.',
            'Reports can contain household data. Store privately, share deliberately and delete local exports separately from server data.',
            'Evidence selection is a non-calculator preview. Independently verify standalone Google Solar suggestions; local raster workflows are legacy reference only.',
        ],
    }


def csv_cell(value):
    """Neutralize spreadsheet formula prefixes, including after whitespace."""
    text = str(value)
    if text.lstrip().startswith(('=', '+', '-', '@')) or text.startswith(('\t', '\r', '\n')):
        text = "'" + text
    return text


def owned_report_text(result, identity, presentation=None):
    """Plain Markdown with escaped labels; the HTML adapter escapes it again."""
    payload = {'identity': identity, 'disclosures': presentation, 'result': result}
    raw = json.dumps(payload, default=lambda v: v.tolist(), ensure_ascii=False, allow_nan=False, indent=2)
    return '# Household simulation result\n\n' + '\n'.join('    ' + line for line in html.escape(raw).splitlines()) + '\n'


def owned_report_csv(result, identity, presentation=None):
    import csv
    import io
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    writer.writerow(['section', 'field', 'value'])
    for section, values in (('identity', identity), ('disclosures', presentation or {}), ('result', result)):
        if not isinstance(values, dict):
            values = {'value': values}
        for key, value in values.items():
            text = value if isinstance(value, str) else json.dumps(value, default=lambda v: v.tolist(), ensure_ascii=False, allow_nan=False)
            writer.writerow([csv_cell(section), csv_cell(key), csv_cell(text)])
    return stream.getvalue()


def fmt(value,digits=0):
    return 'non recuperato / non definito' if value is None else f'{value:,.{digits}f}'
def band(metric,digits=0):return f"{fmt(metric['min'],digits)} – {fmt(metric['max'],digits)}"
def table(headers,rows):
    return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(str(v) for v in row)+' |' for row in rows)

def _format_window_parts(windows):
    w_parts = []
    if not isinstance(windows, list):
        return ["finestre non valide"]
    for idx, w in enumerate(windows):
        wid = w.get('id') if isinstance(w, dict) else None
        wid = wid or f"finestra_{idx + 1}"
        if not isinstance(w, dict) or 'start_minute' not in w or 'end_minute' not in w:
            w_parts.append(f"{wid}: orari mancanti / non definiti")
            continue
        sm = w['start_minute']
        em = w['end_minute']
        if not isinstance(sm, int) or not isinstance(em, int) or isinstance(sm, bool) or isinstance(em, bool):
            w_parts.append(f"{wid}: formato minuti non valido")
            continue
        if sm < 0 or sm > 1439 or em < 0 or em > 1440:
            w_parts.append(f"{wid}: minuti fuori intervallo ({sm}–{em})")
            continue
        sh, s_min = divmod(sm, 60)
        eh, e_min = divmod(em, 60)
        if sm == em:
            w_parts.append(f"{wid}: inattiva ({sh:02d}:{s_min:02d}–{eh:02d}:{e_min:02d}, 0 min)")
        elif sm == 0 and em == 1440:
            w_parts.append(f"{wid}: all-day 24h (00:00–24:00)")
        elif sm > em:
            w_parts.append(f"{wid}: notturna ({sh:02d}:{s_min:02d}–{eh:02d}:{e_min:02d}, scavalco mezzanotte)")
        else:
            w_parts.append(f"{wid}: {sh:02d}:{s_min:02d}–{eh:02d}:{e_min:02d}")
    return w_parts

def _format_ac_windows_desc(s):
    if not isinstance(s, dict):
        return "programmazione non valida / non definita"
    sched_mode = s.get('heating_schedule_mode', 'scheduled')
    if sched_mode not in ('scheduled', 'continuous'):
        return f"modalità programmazione non riconosciuta ({sched_mode})"
    raw_windows = s.get('ac_heating_windows')
    if sched_mode == 'continuous':
        if raw_windows:
            w_parts = _format_window_parts(raw_windows)
            return f"disponibilità continua 24h (modello storico continuo senza finestre di spegnimento; finestre canoniche conservate: {'; '.join(w_parts)})"
        return "disponibilità continua 24h (modello storico continuo senza finestre di spegnimento)"

    windows = raw_windows
    if windows is None:
        from .schedule import LEGACY_HEATING_KEYS
        if any(k in s for k in LEGACY_HEATING_KEYS):
            try:
                from .schedule import migrate_and_resolve_schedule
                windows = migrate_and_resolve_schedule(s)
            except Exception:
                return "programmazione non valida / non risolvibile"
        else:
            return "programmazione non definita / non disponibile"
    if not isinstance(windows, list):
        return "programmazione non valida (formato finestre non corretto)"
    if not windows:
        return "nessuna finestra operativa definita (programmazione vuota / bozza)"
    w_parts = _format_window_parts(windows)
    return "; ".join(w_parts)

def _format_ac_clock_desc(s):
    if not isinstance(s, dict) or 'timezone_mode' not in s or s.get('timezone_mode') is None:
        return "contesto orario non definito / non disponibile"
    tz_mode = s.get('timezone_mode')
    if tz_mode == 'civil':
        return "orario civile Europe/Rome con gestione DST (CET/CEST)"
    if tz_mode == 'standard':
        return "orario solare standard fisso UTC+1 (senza cambi DST)"
    return f"contesto orario non standard ({tz_mode})"

def _format_demand_pct(s, evaluated_pct=None):
    pct = s.get('ac_heating_additional_demand_pct') if isinstance(s, dict) else None
    if pct is None and evaluated_pct is not None:
        pct = evaluated_pct
    if pct is None:
        return "non disponibile"
    if isinstance(pct, bool):
        return "non valida (booleano non ammesso)"
    try:
        val = float(pct)
        if not math.isfinite(val):
            return "non valida (valore non finito)"
        if val.is_integer():
            return f"{int(val)}%"
        s_val = str(val)
        if '.' in s_val and 'e' not in s_val:
            s_val = s_val.rstrip('0').rstrip('.')
        return f"{s_val}%"
    except (ValueError, TypeError):
        return str(pct)

def _extract_ac_evaluated(selected, s):
    is_baseline = bool(s.get('load_scenario') == 'baseline')
    enduses = (selected.get('enduses') or {}) if isinstance(selected, dict) else {}

    if is_baseline:
        return {
            'is_baseline': True,
            'baseline_useful_kwh': None,
            'requested_useful_kwh': None,
            'counterfactual_0pct_kwh': None,
            'requested_electricity_kwh': None,
            'extra_electricity_kwh': None,
            'scop': s.get('scop'),
            'diagnostics': None,
        }

    # Authoritative canonical engine enduses names from feasibility/engine.py
    baseline_useful = enduses.get('ac_heating_baseline_useful_kwh')
    requested_useful = enduses.get('ac_heating_requested_useful_kwh')
    cf_0pct = enduses.get('ac_heating_baseline_electricity_kwh')
    req_elec = enduses.get('ac_heating_requested_electricity_kwh')
    extra_elec = enduses.get('ac_heating_extra_electricity_kwh')
    eval_pct = enduses.get('ac_heating_additional_demand_pct')
    eval_scop = enduses.get('ac_heating_scop')
    diag = enduses.get('ac_heating_allocation_diagnostics')

    return {
        'is_baseline': False,
        'baseline_useful_kwh': float(baseline_useful) if baseline_useful is not None else None,
        'requested_useful_kwh': float(requested_useful) if requested_useful is not None else None,
        'counterfactual_0pct_kwh': float(cf_0pct) if cf_0pct is not None else None,
        'requested_electricity_kwh': float(req_elec) if req_elec is not None else None,
        'extra_electricity_kwh': float(extra_elec) if extra_elec is not None else None,
        'demand_pct': float(eval_pct) if eval_pct is not None else s.get('ac_heating_additional_demand_pct'),
        'scop': float(eval_scop) if eval_scop is not None else s.get('scop'),
        'diagnostics': diag if isinstance(diag, dict) else None,
    }

def write_reports(study, dest=None):
    if dest is None:
        dest = HERE / 'reports'
    else:
        dest = Path(dest)
    dest.mkdir(exist_ok=True)
    c=study['calibration'];base=study['base']
    s=study.get('baseline_settings',{})
    hp_on=bool(s.get('planning_heat_pump',False))
    selected = study.get('selected') or study.get('evaluated')
    if selected is None and hasattr(engine, 'evaluate'):
        try:
            selected = engine.evaluate(s)
        except Exception:
            selected = None
    label = _legacy_report_label()
    lines=[f'# {label} — scenari fotovoltaico e accumulo','',
    '**SCENARI IN ATTESA DI CONFERMA.** Gli intervalli meteo usano tutte le annate storiche separatamente. Gli intervalli congiunti combinano i percorsi finanziari espliciti e due stress: non rappresentano probabilità né tutte le combinazioni possibili. Le assunzioni finanziarie proposte non diventano confermate selezionando i valori predefiniti.','',
    legacy_text('report_generic_248','Nessun sopralluogo è implicito. Ingombri, struttura, stringhe e distanze richiedono conferma dell’installatore; ogni layout deriva dai limiti espliciti del profilo.'),'',
    '## Dati e controlli', '',table(['Controllo','Risultato calcolato'],[
    ['Consumo elettrico misurato annuo (kWh)',fmt(c['annual_kwh'],2)],['Gas annuo misurato (Smc)',fmt(c['annual_gas_smc'])],['Fattura elettrica riprodotta (EUR)',fmt(c['invoice']['total_eur'],2)],['Run rate elettrico annuo (EUR)',fmt(c['annual_electric_bill_eur'],2)],['Run rate gas approssimato (EUR)',fmt(c['annual_gas_runrate_eur'],2)],['Annate / ore simulate',f"{c['weather_count']} / {c['hour_count']}"],['Configurazioni / casi finanziari / stress',f"{study['counts']['candidates']} / {study['counts']['financial_candidate_cases']} / {study['counts']['joint_stress_cases']}"]]),'',
    legacy_text('report_generic_251','La tariffazione annualizzata applica i parametri forniti e non ricostruisce la spesa delle fatture. Le convenzioni temporali conservano i totali mensili; la copertura va verificata nel profilo.'),'',
    '## Confronti coerenti','',
    'NPV incrementale FV confronta FV+accumulo con lo stesso carico futuro senza FV. NPV del pacchetto confronta elettrificazione+FV con elettricità storica+gas, contabilizzando conversione, disconnessione e variazione della potenza contrattuale. I costi e benefici di acquisto e mobilità dell’auto sono esclusi.','']
    is_baseline=bool(s.get('load_scenario')=='baseline')
    if not hp_on:
        ac_eval = _extract_ac_evaluated(selected, s)
        sched_desc = _format_ac_windows_desc(s)
        clock_desc = _format_ac_clock_desc(s)
        pct_desc = _format_demand_pct(s, evaluated_pct=ac_eval.get('demand_pct'))
        bal_temp = fmt(s.get('heating_balance_temp', 18.0), 1)

        lines += [legacy_text('scop_heading','## Scenario SCOP del climatizzatore'),'',
        f"{legacy_text('report_scop_263_0','Scenario SCOP configurato: ')}{fmt(study['baseline_settings']['scop'],1)}{legacy_text('report_scop_263_1',' kWh termici/kWh elettrici. L’applicabilità e le prestazioni richiedono evidenza esplicita. La domanda termica proviene dal profilo.')}",'',
        table(['Carico','Clima nominale','SCOP','Elettricità riscaldamento kWh/anno','Domanda totale kWh/anno (meteo)','NPV pacchetto EUR (meteo)'],[[x['load_scenario'],x['climate'],fmt(x['value'],1),fmt(x['result']['enduses']['heating_kwh'],2),band(x['result']['energy']['load_kwh']),band(x['result']['metrics']['package_npv_eur'])] for x in study.get('scop_scenarios', [])]),'',
        f"**Programmazione operativa e contesto temporale**: {sched_desc} ({clock_desc}). I confini al minuto definiscono l'ammissibilità dell'allocazione all'interno del modello orario (frazione attiva dell'ora di orologio), non la coincidenza solare FV sub-oraria. Nei grafici e nelle serie temporali orarie, la visualizzazione a tempo solare standard fisso UTC+1 può differire dalle etichette della programmazione civile locale durante il periodo di ora legale estiva (Europe/Rome DST).",'',
        f"**Fabbisogno termico e bilancio energetico**: fabbisogno termico utile aggiuntivo selezionato pari a {pct_desc} rispetto alla sola conversione della quota gas storica. Il budget mensile di energia termica utile deriva dai consumi storici di gas metano e resta rigorosamente prefissato: le condizioni meteo storiche ne redistribuiscono unicamente l'allocazione oraria tra le sole ore permesse, pesata sui gradi-giorno sotto la temperatura di bilancio ({bal_temp}°C). Il consumo elettrico di riscaldamento è calcolato dividendo il calore termico utile richiesto per lo SCOP nominale una sola volta (nessun moltiplicatore secondario o doppio derating applicato). L'eventuale fabbisogno aggiuntivo rappresenta un incremento di servizio e comfort termico desiderato, non una maggiore sostituzione di combustibile: non genera pertanto ulteriori risparmi sulla bolletta gas storica (il credito per combustibile evitato resta vincolato al consumo storico sostituito).",'',
        ]

        if is_baseline:
            lines += [
                "**Scenario base storico selezionato (`baseline`)**: il climatizzatore per riscaldamento è ESCLUSO dal profilo di carico elettrico. I consumi elettrici rimangono quelli storici misurati e il riscaldamento domestico rimane interamente a carico della caldaia a gas esistente; nessun consumo elettrico o calore utile del climatizzatore è conteggiato nel baseline storico. I valori di calore utile richiesto e consumo elettrico AC si riferiscono agli scenari di elettrificazione (`electrified` o `ev`).",'',
            ]
        else:
            cf_val = f"{fmt(ac_eval['counterfactual_0pct_kwh'], 2)} kWh_e/anno" if ac_eval['counterfactual_0pct_kwh'] is not None else "non disponibile"
            req_e_val = f"{fmt(ac_eval['requested_electricity_kwh'], 2)} kWh_e/anno" if ac_eval['requested_electricity_kwh'] is not None else "non disponibile"
            extra_e_val = f"{fmt(ac_eval['extra_electricity_kwh'], 2)} kWh_e/anno" if ac_eval['extra_electricity_kwh'] is not None else "non disponibile"
            base_th_val = f"{fmt(ac_eval['baseline_useful_kwh'], 0)} kWh_th/anno" if ac_eval['baseline_useful_kwh'] is not None else "non disponibile"
            req_th_val = f"{fmt(ac_eval['requested_useful_kwh'], 0)} kWh_th/anno" if ac_eval['requested_useful_kwh'] is not None else "non disponibile"
            scop_val = fmt(ac_eval['scop'], 1) if ac_eval['scop'] is not None else "non disponibile"

            eval_rows = [
                ['Fabbisogno termico utile di riferimento (conversione storica 0% extra)', base_th_val],
                ['Fabbisogno termico utile richiesto nello scenario', req_th_val],
                ['Controfattuale elettrico 0% AC (sostituzione gas a SCOP nominale)', cf_val],
                ['Elettricità riscaldamento AC richiesta nello scenario', req_e_val],
                ['Consumo elettrico aggiuntivo (extra kWh oltre il controfattuale 0%)', extra_e_val],
                ['SCOP nominale applicato (calore utile richiesto / elettricità richiesta)', scop_val],
            ]

            diag = ac_eval['diagnostics']
            if isinstance(diag, dict):
                nom_dur = diag.get('nominal_daily_union_duration_hours')
                max_int = diag.get('max_modeled_interval_average_electrical_demand_kw')
                max_p = diag.get('max_modeled_permitted_duration_electrical_requirement_kw')
                max_th = diag.get('max_modeled_permitted_duration_useful_heat_requirement_kw')
                realizations = diag.get('realizations', [])

                if nom_dur is not None:
                    eval_rows.append(['Durata nominale unione finestre permesse', f"{fmt(nom_dur, 2)} ore/giorno"])
                if realizations:
                    elig_list = [r['eligible_schedule_duration_hours'] for r in realizations if 'eligible_schedule_duration_hours' in r]
                    if elig_list:
                        min_e, max_e = min(elig_list), max(elig_list)
                        e_str = f"{min_e:,.1f} ore/anno" if min_e == max_e else f"{min_e:,.1f} – {max_e:,.1f} ore/anno"
                        eval_rows.append(['Durata annuale effettiva ammissibile (realizzazioni meteo)', e_str])
                if max_int is not None:
                    eval_rows.append(['Picco medio orario elettrico su intervallo (interval-average)', f"{fmt(max_int, 2)} kW_e"])
                if max_p is not None:
                    eval_rows.append(['Requisito potenza elettrica nella durata permessa (permitted-duration)', f"{fmt(max_p, 2)} kW_e"])
                if max_th is not None:
                    eval_rows.append(['Requisito potenza termica utile nella durata permessa', f"{fmt(max_th, 2)} kW_th"])

            lines += [
                table(['Parametro o flusso climatizzatore (AC heating)','Valore nello scenario corrente'], eval_rows),'',
                "**Requisiti operativi di potenza e limiti del modello**: la potenza media d'intervallo orario (`interval-average`, calcolata su tutti i 60 minuti dell'ora) differisce dal requisito di potenza calcolato limitatamente alla sola durata effettiva attiva nella finestra (`permitted-duration`). Nelle finestre parziali o al minuto la potenza operativa durante il periodo attivo è proporzionalmente più elevata della media d'intervallo. L'adeguatezza dell'apparecchiatura e il comfort termico non sono valutati dal modello (nessuna attestazione di potenza sufficiente o di raggiungimento delle temperature interne desiderate). Non è simulata la dinamica termica dell'involucro edilizio (inerzia delle strutture, transitori di raffreddamento a impianto spento o picchi di spunto all'avvio). Il modello non esegue stime arbitrarie di capacità né espande le finestre orarie dell'utente se i requisiti superano la potenza delle macchine, né assume che lo SCOP nominale costituisca una capacità istantanea garantita alle temperature minime esterne.",'',
            ]
    elif is_baseline:
        lines += ['## Pompa di calore aria-acqua (pianificazione)','',
        f"Pianificazione pompa di calore aria-acqua attiva (taglia nominale provvisoria {fmt(s.get('hp_nominal_kw', 9.0), 1)} kW).",'',
        "**Scenario base storico selezionato (`baseline`)**: la pompa di calore è ESCLUSA da questo scenario. Il fabbisogno termico e i consumi elettrici storici rimangono invariati senza sostituzione del generatore esistente; nessun servizio termico o elettrico della pompa di calore è conteggiato nel baseline storico.",'',
        "Per visualizzare il dimensionamento numerico, i fabbisogni termici erogati, i consumi elettrici del compressore e il fattore di prestazione stagionale (SPF), selezionare un profilo di carico elettrificato (`electrified` o `ev`).",'',
        ]
    else:
        hp=selected.get('heat_pump') or {}
        env=hp.get('envelopes',{})
        gf=hp.get('gas_finance',{})
        meta=hp.get('metadata',{})
        ref_gas=meta.get('reference_gas_allocation')
        if not ref_gas:
            try:
                from .heatpump_demand import allocate_gas_consumption
                alloc = allocate_gas_consumption(
                    summer_gas_base=float(s.get('summer_gas_base', 20.0)),
                    cooking_smc_annual=float(s.get('cooking_smc_annual', 20.0)),
                    gas_pcs=float(s.get('gas_pcs', 11.0)),
                    boiler_efficiency=float(s.get('boiler_efficiency', 0.80)),
                )
                ref_gas = {
                    'heating_reference_smc': float(alloc.heating_smc.sum()),
                    'heating_useful_kwh': float(alloc.heating_useful_kwh.sum()),
                    'dhw_reference_smc': float(alloc.dhw_smc.sum()),
                    'dhw_gross_useful_kwh': float(alloc.dhw_gross_useful_kwh.sum()),
                    'cooking_reference_smc': float(alloc.cooking_smc.sum()),
                }
            except Exception:
                ref_gas = {}
        if ref_gas is None:
            ref_gas = {}
        rad_exp = s.get('hp_radiator_exponent', 1.3)
        rad_exp_str = str(rad_exp) if rad_exp in (1.3, 1.4) else fmt(rad_exp, 2)
        sched_mode = s.get('hp_schedule_mode', 'continuous')
        if sched_mode == 'scheduled':
            sched_desc = f"programmazione oraria (finestre mattina {s.get('hp_morning_on', 6)}:00–{s.get('hp_morning_off', 8)}:00 e sera {s.get('hp_evening_on', 16)}:00–{s.get('hp_evening_off', 20)}:00)"
        else:
            sched_desc = "disponibilità continua 24h (senza finestre di spegnimento programmato)"
        hygiene_on = bool(s.get('hp_hygiene_enabled', False))
        if hygiene_on:
            hygiene_desc = f"ciclo antilegionella abilitato a {fmt(s.get('hp_hygiene_temp', 60.0), 1)}°C (ciclo termico opzionale; nessuna attestazione di adeguatezza sanitaria)"
        else:
            hygiene_desc = f"ciclo antilegionella opzionale DISABILITATO (nessuna attestazione di adeguatezza sanitaria; soglia configurabile {fmt(s.get('hp_hygiene_temp', 60.0), 1)}°C)"
        ws_mode = bool(s.get('hp_weather_sensitive', False))
        tz_mode = s.get('timezone_mode', 'civil')
        demand_src_desc = (
            "fabbisogno termico sensibile al meteo (scalato con i gradi-ora dell'insieme meteo)"
            if ws_mode else
            "fabbisogno termico basato su budget mensili da bolletta gas storica (non scalato con i gradi-ora meteo)"
        )
        clock_desc = (
            "gestione ora civile Roma DST (+1 anno bisestile)"
            if tz_mode == 'civil' else
            "orario solare standard fisso UTC+1 (+1 anno bisestile)"
        )
        lines += ['## Pompa di calore aria-acqua (pianificazione)','',
        f"Pianificazione pompa di calore aria-acqua attiva per riscaldamento idronico (radiatori) e produzione ACS con accumulo. {legacy_text('report_period','Riferimento termico del profilo')}: {fmt(c['annual_gas_smc'])} Smc (riscaldamento {fmt(ref_gas.get('heating_reference_smc'))} Smc / {fmt(ref_gas.get('heating_useful_kwh'), 0)} kWh termici utili; ACS {fmt(ref_gas.get('dhw_reference_smc'))} Smc / {fmt(ref_gas.get('dhw_gross_useful_kwh'), 0)} kWh termici lordi con base estiva {fmt(s.get('summer_gas_base', 20))} Smc/mese; cottura {fmt(ref_gas.get('cooking_reference_smc', s.get('cooking_smc_annual', 20.0)))} Smc gas). Il rendimento stagionale della caldaia a gas esistente $\\eta = {fmt(s.get('boiler_efficiency', 0.80), 2)}$ è una stima dell'utente non verificata ({legacy_text('boiler_age','caldaia esistente;')} non misurata con contatore di calore).",'',
        f"Taglia nominale provvisoria {fmt(s.get('hp_nominal_kw', 9.0), 1)} kW modificabile, modellata con mappa generica fallback basata su sensibilità termodinamiche tipo Carnot (o mappa personalizzata da costruttore). Nessun modello commerciale specifico è stato selezionato. I rating di brochure del costruttore, come per [Vaillant aroTHERM plus](https://www.vaillant.com/en/products/arothermplus/) (accesso 2026-10-14), riportano punti di prova a carico parziale (es. A7/W35 a potenza parzializzata e alto COP) e non devono essere scambiati per una mappa di capacità massima continua del compressore.",'',
        f"Curva climatica impostata con mandata tra {fmt(s.get('hp_curve_flow_mild', 35.0), 1)}°C a {fmt(s.get('hp_curve_outdoor_mild', 15.0), 1)}°C e {fmt(s.get('hp_curve_flow_cold', 55.0), 1)}°C a {fmt(s.get('hp_curve_outdoor_cold', -5.0), 1)}°C (con clamping). Derating emissione radiatori secondo metodologia EN 442 $\\Delta T 50$ con esponente caratteristico $n = {rad_exp_str}$ approssimato e modificabile: [Guida tecnica Stelrad fattori di correzione radiatori](https://www.stelrad.ie/wp-content/uploads/2021/10/Stelrad-Correction-Factor-Oct-21.pdf) (accesso 2026-10-14). L'osservazione dell'utente con mandata caldaia ~60°C e mantenimento di ~18°C indoor non identifica la temperatura di ritorno dell'acqua né la potenza nominale dei radiatori installati; dispersione d'involucro ($UA$) e superfici radianti restano non identificate in assenza di sopralluogo tecnico.",'',
        f"Il riscaldamento opera con {sched_desc} e setback impostato a {fmt(s.get('hp_setback_delta', 0.0), 1)}°C; non è modellata la massa termica dinamica dell'edificio e i deficit termici rimangono esplicitamente non serviti. Serbatoio ACS da {fmt(s.get('hp_tank_litres', 200.0), 0)} litri (perdite {fmt(s.get('hp_tank_loss_w_k', 1.0), 1)} W/K, soglia minima servizio {fmt(s.get('hp_tank_service_temp', 40.0), 1)}°C, {hygiene_desc}) con priorità condivisa ACS: durante il reintegro del serbatoio l'erogazione al riscaldamento viene sospesa o limitata.",'',
        f"Eventuali deficit di capacità (riscaldamento non servito {band(env.get('annual_unmet_space_heat_kwh', {'min':0,'max':0}))} kWh/anno) e prelievi ACS non qualificati sono gestiti con resistenza integrativa da {fmt(s.get('hp_backup_kw', 3.0), 1)} kW (efficienza 1.0) e consumi ausiliari ({fmt(s.get('hp_external_pump_kw', 0.06)*1000, 0)} W pompa secondaria, {fmt(s.get('hp_standby_kw', 0.02)*1000, 0)} W standby). La fornitura gas e il relativo contatore sono mantenuti a meno che la cottura non sia convertita a induzione (`hp_electric_cooking=True`) E non sia richiesta la disconnessione (`hp_disconnect_gas=True`); in caso di mantenimento, restano dovute le quote fisse di rete/contatore ({fmt(gf.get('actual_remaining_gas_cost_eur', 150.0), 2)} EUR/anno spesa residua). Costo iniziale apparecchiatura EUR {fmt(s.get('hp_capex', 10000.0), 2)} a tempo zero, manutenzione annuale EUR {fmt(s.get('hp_annual_maintenance', 100.0), 2)}/anno; i crediti di combustibile evitato ({band(gf.get('avoided_gas_credit_eur', {'min':0,'max':0}))} EUR/anno) vengono trattenuti per la quota di energia termica non servita.",'',
        f"{legacy_text('rated_ac_label','I rating SCOP del climatizzatore')} non sono applicabili alla pompa di calore idronica. I consumi elettrici sono calcolati ora per ora in base alle condizioni ambientali e curve idroniche con {demand_src_desc}, senza impiegare budget mensili elettrici prefissati. L'insieme delle {engine.weather_period().count} annate meteo storiche PVGIS ({engine.weather_period().label}) è accoppiato con l'anno di installazione previsto ({clock_desc}); in assenza di rilievi meteo orari coincidenti con {legacy_text('weather_period','il periodo di misura del profilo')}, la climatologia storica funge da proxy e i profili orari dettagliati restano non convalidati.",'',
        table(['Parametro / Flusso termico o elettrico','Intervallo meteo annuo simulato'],[
            ['Fabbisogno termico utile riscaldamento erogato (kWh_th)',band(env.get('annual_delivered_space_heat_kwh',{'min':0,'max':0}))],
            ['Fabbisogno termico utile ACS erogato (kWh_th)',band(env.get('annual_delivered_draw_heat_kwh',{'min':0,'max':0}))],
            ['Consumo elettrico compressore riscaldamento (kWh_e)',band(env.get('annual_space_compressor_electricity_kwh',{'min':0,'max':0}))],
            ['Consumo elettrico compressore ACS (kWh_e)',band(env.get('annual_dhw_compressor_electricity_kwh',{'min':0,'max':0}))],
            ['Consumo elettrico ausiliari e standby (kWh_e)',band(env.get('annual_auxiliary_electricity_kwh',{'min':0,'max':0}))],
            ['Consumo elettrico totale pompa di calore (kWh_e)',band(env.get('annual_total_electricity_kwh',{'min':0,'max':0}))],
            ['Fattore di prestazione stagionale utile (SPF calore consegnato)',band(env.get('seasonal_performance_factor_delivered_heat',{'min':0,'max':0}),2)],
            ['Deficit termico non servito riscaldamento (kWh_th)',band(env.get('annual_unmet_space_heat_kwh',{'min':0,'max':0}))],
            ['Risparmio gas accreditato (Smc)',band(gf.get('gas_volumes',{}).get('avoided_smc',{'min':0,'max':0}))],
            ['Credito monetario gas evitato (EUR)',band(gf.get('avoided_gas_credit_eur',{'min':0,'max':0}),2)],
        ]),'']
    no=[x for x in base['candidates'] if x['label']=='no_pv']
    lines += [table(['Carico senza FV','Import annuo (kWh, meteo)','Conversione (EUR)','NPV FV (EUR)','NPV pacchetto (EUR, meteo)'],[[x['load_scenario'],band(x['metrics']['grid_import_kwh']),fmt(x['finance']['conversion_capex_eur']),band(x['metrics']['npv_eur']),band(x['metrics']['package_npv_eur'])] for x in no]),'',
    '## Taglie pratiche e intervalli congiunti','',
    legacy_text('report_generic_395','Le taglie sono limitate dai tetti espliciti del profilo. Le frontiere confrontano NPV, payback, autarchia e autoconsumo; non costituiscono una verifica strutturale o una raccomandazione di installazione.'),'']
    for load in ['baseline','electrified','ev']:
        rows=[x for x in study['candidate_joint_envelopes'] if x['load_scenario']==load and not x['theoretical'] and x['label'] in ['no_pv','8S','10S','12S','15S']]
        lines += [f'### {load}','',table(['Layout','Accumulo utile kWh','NPV FV EUR (congiunto)','Payback anni (congiunto)','Autarchia frazione (congiunta)'],[[x['label'],x['battery_kwh'],band(x['metrics']['npv_eur']),band(x['metrics']['payback_years'],1),band(x['metrics']['autarky'],3)] for x in rows]),'']
    lines += ['## Accumulo rispetto allo stesso FV senza accumulo','',table(['Caso','Costo aggiuntivo EUR','NPV incrementale accumulo EUR (meteo)','Payback accumulo anni'],[[x['id'],fmt(x['incremental_capex_eur']),band(x['battery_incremental_npv_eur']),band(x['battery_incremental_payback_years'],1)] for x in study['battery_counterfactuals'] if '_12S_' in x['id']]),'']
    lines += ['## Classifiche per obiettivo e scenario','', 'Ogni riga indica il primo caso pratico secondo l’obiettivo specificato, senza raccomandazione unica. L’elenco completo e le frontiere sono in study.json. Payback nullo indica mancato recupero entro l’orizzonte; il caso senza investimento può dominare quando rendimenti sono negativi.','',table(['Percorso / detrazione','Carico','Obiettivo','Primo caso'],[[r['scenario'],r['load_scenario'],r['objective'],r['ordered_ids'][0]] for r in study['rankings']]),'']
    lines += ['## Sensibilità e conferme da ottenere','',
    'Il registro input_ledger.json distingue intervalli plausibili dai limiti di validazione. La sensibilità include capacità IRPEF disponibile nulla e piena, detrazione nulla/36%/50%, percorsi energia inferiori/piatti/superiori, prestito e interpretazioni alternative dei benchmark. Sono casi espliciti, non una distribuzione del mercato. La capacità annuale di detrazione deve essere confermata dal contribuente.','',table(['Parametro','Valore alternativo','NPV FV EUR (meteo)','Payback anni (meteo)'],[[x['parameter'],json.dumps(x['value']),band(x['metrics']['npv_eur']),band(x['metrics']['payback_years'],1)] for x in study['sensitivity']['cases'] if 'metrics' in x]),'',
    '## Costi, fonti e conflitti','',
    f"Il catalogo fornito per 6 kWp vale EUR {fmt(c['calibrated_6kwp_eur'])}; la formula originale fornita produce EUR {fmt(c['original_cost_formula_6kwp_eur'])}. Il modello conserva il fisso e calibra il marginale al pacchetto; la formula originale resta una sensibilità. I benchmark commerciali sono dati forniti dall’utente, senza distribuzione statistica né nuovi riscontri web.",
    f"Il percorso baseload×capture produce EUR/kWh {fmt(c['base_times_capture_eur_kwh'],5)}, mentre il prezzo capture fornito è {fmt(c['provided_capture_eur_kwh'],5)}: entrambi sono valutati senza applicare il capture due volte. Il fattore export parte da 1 nel primo anno simulato, varia linearmente fino al moltiplicatore obiettivo dopo gli anni trascorsi impostati e poi resta costante. Il prezzo unitario applica una sola volta fattore, inflazione ed escalation reale energia; PMG, imposte e commissioni seguono questo calcolo. La rampa non cambia importazioni o dispatch fisico. Manca una serie PZO oraria osservata, pertanto la base usa un benchmark piatto; non stima la cannibalizzazione oraria dell’export.",
    f"{legacy_text('cer_note','CER dipende da selezione esplicita, regole applicabili e corrispondenza oraria; nessuna configurazione locale viene ereditata.')}",'',
    '## Confronto rendimento Solar API / PVGIS','',table(['Orientamento','PVGIS ombreggiato beam kWh/kWp','Tutte radiazioni ombreggiate','Google DC','Google AC illustrativo'],[[r['orientation'],band(r['pvgis_beam_shaded']),band(r['pvgis_all_light_shaded']),fmt(r['google_dc_kwh_kwp']),fmt(r['google_ac_kwh_kwp'])] for r in c['roofs']]),'',
    legacy_text('report_generic_408','Il ponte DC→AC è un’approssimazione dichiarata, non una misura. Non si applica nuovamente a dati che includono già le perdite; la geometria non verifica stringhe o fattibilità.'),'',
    '## Metodo e limiti','',
    'Capitale al tempo zero, flussi nominali a fine anno; tasso nominale=(1+tasso reale)×(1+inflazione)−1. La visualizzazione reale deflaziona gli stessi flussi mantenendo NPV coerente. LCOE scontato per kWh AC prodotto, distinto dalla variante al netto incentivi. Detrazione in dieci rate nominali, limite annuale di imposta disponibile e cap spesa; quote non usate perse. PMG applicato come massimo sui ricavi annuali aggregati prima di fee e imposta marginale. Prestito separa rendimento del progetto e capitale proprio.']
    hp_method_desc = (
        ' nello scenario baseline storico la pompa di calore è esclusa dai carichi;'
        if is_baseline else
        (' la domanda termica è sensibile al meteo e i consumi elettrici della pompa di calore sono calcolati dinamicamente ora per ora senza budget mensili elettrici prefissati né SCOP del climatizzatore.'
         if ws_mode else
         ' la domanda termica usa budget mensili storici e i consumi elettrici della pompa di calore sono calcolati dinamicamente ora per ora senza budget mensili elettrici prefissati né SCOP del climatizzatore.')
    ) if hp_on else (
        ' nello scenario baseline storico il climatizzatore è escluso dai carichi elettrici;'
        if is_baseline else
        ' il riscaldamento a climatizzatore converte i fabbisogni mensili utili storici delle bollette gas con lo SCOP nominale (e quota addizionale selezionata), allocati nelle sole finestre orarie permesse pesati sui gradi-giorno senza modificare i risparmi gas storici.'
    )
    lines += ['Accumulo con vincoli orari di capacità, potenza, SOC ed efficienza; degrado a passi annuali da calendario e cicli. Architettura AC equivalente condivisa con clipping prima della carica; standby, stringhe/bypass e picchi intraora esclusi. Ogni annata viene ripetuta per tutta la vita del progetto;' + hp_method_desc + ' Preventivo: tempi pagamento conservati come evidenza ma non modellati; capitale iniziale resta anticipato.','',
    'Preventivi richiedono modelli/datasheet, potenze continue, DoD, efficienza e curve di degrado, garanzie, layout e condizioni. Un prezzo economicamente sostenibile deriva dal target del modello; non costituisce prezzo di mercato. Il solver IRR risolve NPV al rendimento richiesto: flussi con sostituzioni possono avere radici multiple.','']
    markdown='\n'.join(lines)
    (dest/'executive.md').write_text(markdown)
    # No optional Markdown dependency: retain accessible preformatted tables.
    body=html.escape(markdown)
    (dest/'executive.html').write_text('<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(label)+' — studio</title><style>body{font:15px/1.5 system-ui;color:#183b33;max-width:1100px;margin:24px auto;padding:16px}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media print{body{font-size:10px}}</style><h1>'+html.escape(label)+' — studio calcolato</h1><p><a href="/">Calcolatore</a> · <a href="/report.md">Markdown</a> · <a href="/api/study">Dati completi JSON</a></p><pre>'+body+'</pre></html>')
    ledger_data = selected.get('assumptions') if isinstance(selected, dict) and 'assumptions' in selected else study.get('assumptions', engine.records() if hasattr(engine, 'records') else [])
    for name,obj in [('study.json',study),('input_ledger.json',ledger_data)]:
        (dest/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False))
    # Preserve executed acceptance results rather than claiming a rerun.
    verification=dest/'verification.json'
    evidence=json.loads(verification.read_text()) if verification.exists() else {}
    evidence.update(report_generated_utc=datetime.now(timezone.utc).isoformat(),engine='feasibility/engine.py',scenario_counts=study['counts'],calibration={k:c[k] for k in ['invoice','annual_kwh','annual_gas_smc','weather_count','hour_count']},status='Executed model evidence; test run recorded separately')
    verification.write_text(json.dumps(evidence,indent=2,allow_nan=False))
    write_summary(study, dest=dest, selected=selected)
    return str(dest)

def write_summary(study, dest=None, selected=None):
    """One-page entry point; all values are copied from computed study JSON."""
    if dest is None:
        dest = HERE / 'reports'
    else:
        dest = Path(dest)
    c=study['calibration'];rows=[]
    s=study.get('baseline_settings',{})
    hp_on=bool(s.get('planning_heat_pump',False))
    if selected is None:
        selected = study.get('selected') or study.get('evaluated')
    if selected is None and not hp_on and hasattr(engine, 'evaluate'):
        try:
            selected = engine.evaluate(s)
        except Exception:
            selected = None
    for load in ['baseline','electrified','ev']:
        for battery in [0,5]:
            x=next((r for r in study['candidate_joint_envelopes'] if r['load_scenario']==load and r['label']=='12S' and r['battery_kwh']==battery), None)
            if x is not None:
                rows.append([load,battery,band(x['metrics']['npv_eur']),band(x['metrics']['payback_years'],1),band(x['metrics']['autarky'],3)])
    ranks=[r for r in study['rankings'] if r['objective']=='npv_eur' and r['scenario'] in ['lower_deduction_0','flat_deduction_0.5'] and r.get('ordered_ids')]
    label = _legacy_report_label()
    lines=[f'# {label} — sintesi per decidere','',
    legacy_text('report_generic_464','Le taglie sono limitate dai tetti espliciti del profilo. Le frontiere confrontano NPV, payback, autarchia e autoconsumo; non costituiscono una verifica strutturale o una raccomandazione di installazione.'),'',
    'Il FV si confronta con lo stesso carico futuro senza FV. L’elettrificazione si confronta separatamente con elettricità storica+gas, includendo costo di conversione e disconnessione. L’EV include elettrificazione, senza prezzo dell’auto né benefici della mobilità.','']
    rad_exp = s.get('hp_radiator_exponent', 1.3)
    rad_exp_str = str(rad_exp) if rad_exp in (1.3, 1.4) else fmt(rad_exp, 2)
    boiler_eff_str = fmt(s.get('boiler_efficiency', 0.80), 2)
    is_summary_baseline = bool(s.get('load_scenario') == 'baseline')
    if not hp_on:
        ac_eval = _extract_ac_evaluated(selected, s)
        sched_desc = _format_ac_windows_desc(s)
        clock_desc = _format_ac_clock_desc(s)
        pct_desc = _format_demand_pct(s, evaluated_pct=ac_eval.get('demand_pct'))

        summary_p1 = f"{legacy_text('report_scop_476_0','Scenario SCOP configurato: ')}{fmt(study['baseline_settings']['scop'],1)}{legacy_text('report_scop_476_1',' kWh termici/kWh elettrici. L’applicabilità e le prestazioni richiedono evidenza esplicita. La domanda termica proviene dal profilo.')}"

        summary_p2 = (
            f"Programmazione AC: {sched_desc} ({clock_desc}). I confini al minuto supportano sovrapposizioni parziali nel modello orario senza risolvere la coincidenza solare sub-oraria (la visualizzazione oraria fissa UTC+1 può differire dai cartellini civili DST). "
            f"Fabbisogno termico aggiuntivo selezionato: {pct_desc}. Il budget mensile deriva dal gas storico ed è fisso per ciascun mese (il meteo orario ne redistribuisce unicamente l'allocazione entro la durata permessa); l'elettricità è calcolata dividendo il calore utile per lo SCOP una sola volta, e l'eventuale extra fabbisogno non genera ulteriori risparmi sulla bolletta gas storica."
        )

        if is_summary_baseline:
            summary_p3 = "Nello scenario base storico (`baseline`), il climatizzatore è escluso dal profilo di carico elettrico (riscaldamento a gas invariato senza consumi AC); i valori di calore utile richiesto e controfattuali AC operano negli scenari elettrificati (`electrified` o `ev`)."
        else:
            cf_val = f"{fmt(ac_eval['counterfactual_0pct_kwh'], 2)} kWh/anno" if ac_eval['counterfactual_0pct_kwh'] is not None else "non disponibile"
            req_e_val = f"{fmt(ac_eval['requested_electricity_kwh'], 2)} kWh/anno" if ac_eval['requested_electricity_kwh'] is not None else "non disponibile"
            extra_e_val = f"{fmt(ac_eval['extra_electricity_kwh'], 2)} kWh/anno" if ac_eval['extra_electricity_kwh'] is not None else "non disponibile"
            req_th_val = f"{fmt(ac_eval['requested_useful_kwh'], 0)} kWh_th/anno" if ac_eval['requested_useful_kwh'] is not None else "non disponibile"

            diag = ac_eval.get('diagnostics')
            if isinstance(diag, dict):
                p_avg = diag.get('max_modeled_interval_average_electrical_demand_kw')
                p_dur = diag.get('max_modeled_permitted_duration_electrical_requirement_kw')
                p_th = diag.get('max_modeled_permitted_duration_useful_heat_requirement_kw')
                nom_d = diag.get('nominal_daily_union_duration_hours')
                realizations = diag.get('realizations', [])
                elig_list = [r['eligible_schedule_duration_hours'] for r in realizations if 'eligible_schedule_duration_hours' in r]
                dur_parts = []
                if nom_d is not None:
                    dur_parts.append(f"{fmt(nom_d, 2)} h/giorno nominali")
                if elig_list:
                    min_e, max_e = min(elig_list), max(elig_list)
                    e_s = f"{min_e:,.1f} h/anno" if min_e == max_e else f"{min_e:,.1f}–{max_e:,.1f} h/anno"
                    dur_parts.append(f"{e_s} ammissibili")
                dur_summary = f" (durata: {', '.join(dur_parts)})" if dur_parts else ""

                p_avg_s = f"{fmt(p_avg, 2)} kW_e" if p_avg is not None else "non disponibile"
                p_dur_s = f"{fmt(p_dur, 2)} kW_e" if p_dur is not None else "non disponibile"
                p_th_s = f"{fmt(p_th, 2)} kW_th" if p_th is not None else "non disponibile"
                diag_summary = f" Diagnostica di potenza{dur_summary}: picco medio d'intervallo orario {p_avg_s} vs potenza richiesta nella durata permessa {p_dur_s} ({p_th_s} termici); adeguatezza macchine, comfort e dinamica d'involucro non modellati."
            else:
                diag_summary = ""

            summary_p3 = (
                f"Valutazione nello scenario corrente: controfattuale 0% AC {cf_val}; elettricità riscaldamento richiesta {req_e_val} (extra richiesta {extra_e_val}); calore utile richiesto {req_th_val} (distinto dal profilo base storico senza AC).{diag_summary}"
            )

        lines += [f"{summary_p1}\n\n{summary_p2}\n\n{summary_p3}",'']
    elif is_summary_baseline:
        lines += [f"Pianificazione pompa di calore aria-acqua attiva (taglia nominale provvisoria {fmt(s.get('hp_nominal_kw',9.0),1)} kW, curva climatica ed emissione radiatori EN 442 n={rad_exp_str}). Nello scenario base storico selezionato (`baseline`), la pompa di calore è esclusa dal fabbisogno: i flussi della pompa di calore operano negli scenari elettrificati (`electrified` o `ev`). Rendimento caldaia esistente {boiler_eff_str} non verificato.",'']
    else:
        lines += [f"Pianificazione pompa di calore aria-acqua attiva (taglia nominale provvisoria {fmt(s.get('hp_nominal_kw',9.0),1)} kW, curva climatica ed emissione radiatori EN 442 n={rad_exp_str}). Servizio idronico condiviso riscaldamento e ACS con accumulo; rendimento caldaia esistente {boiler_eff_str} non verificato. La fornitura gas è mantenuta se la cottura resta a gas; i rating SCOP del climatizzatore non sono applicabili e il consumo elettrico non usa budget fissi mensili.",'']
    lines += ['## Esempio di confronto, senza raccomandazione','',
    'Caso 12 pannelli Sud, con/senza accumulo: intervalli congiunti da tutte le annate meteo, percorsi energia inferiori/piatti/superiori, detrazioni alternative e due stress finanziari/tecnici. Non sono probabilità né estremi di tutte le combinazioni. Un limite payback non definito significa mancato recupero in almeno un caso.','',
    table(['Carico','Accumulo kWh','NPV FV EUR','Payback anni','Autarchia frazione'],rows),'',
    '## Classifica condizionata dall’obiettivo NPV','',table(['Percorso / detrazione','Carico','Primo caso pratico'],[[r['scenario'],r['load_scenario'],r['ordered_ids'][0]] for r in ranks]),'',
    'Queste classifiche usano mediane meteo a parità di percorso. Payback, autarchia e autoconsumo possono scegliere casi diversi: tutte le frontiere e i controfattuali dell’accumulo sono nello studio completo.','',
    '## Verifiche e conferme','',
    f"Il motore riproduce {fmt(c['annual_kwh'],2)} kWh/anno elettrici, {fmt(c['annual_gas_smc'])} Smc/anno e fattura di EUR {fmt(c['invoice']['total_eur'],2)}. L’energia si conserva in ogni intervallo; il verbale verifica i vincoli per tutta la vita. Il run rate gas di EUR {fmt(c['annual_gas_runrate_eur'],2)} è una tariffazione annualizzata approssimata, non la spesa storica delle dodici fatture.",
    'Prima di valutare un’offerta: confermare capacità IRPEF annuale disponibile (controllo modificabile, incluse sensibilità nulla/piena), costi/scope/IVA, modelli e garanzie, potenze e degrado dell’accumulo, struttura e layout del tetto. Le rate di detrazione inutilizzabili sono perse. Tutte le ipotesi finanziarie proposte restano pendenti.',
    legacy_text('report_generic_532','Il benchmark export applica capture una sola volta. La rampa futura cambia la remunerazione, non il dispatch fisico. PMG, imposte e commissioni seguono il prezzo export. CER richiede evidenza e selezione esplicite.'),'',
    legacy_text('report_generic_533','[Report completo](executive.md) · [Studio e flussi JSON](study.json) · [Registro input](input_ledger.json) · [Verifiche](verification.json)'),'']
    target=dest/'executive_summary.md';target.write_text('\n'.join(lines));return target
