"""Independent, explicit heating-budget oracles; never capture production outputs.

The legacy branch exists solely to check the untouched historical fixture arrays.
The current oracle implements the ensemble contract from 284e820: monthly mean
useful heat, degree-hour allocation, and one harmonic COP calibration per ensemble.
"""
import numpy as np


def heating_oracle(settings, temperature, months, hours, valid, civil_hours,
                   gas_monthly, *, legacy=False):
    temp = np.asarray(temperature, float)
    month = np.broadcast_to(months, temp.shape)
    active = np.broadcast_to(valid, temp.shape)
    if settings.get('load_scenario') == 'baseline':
        return np.zeros_like(temp)
    if settings['heating_schedule_mode'] == 'continuous':
        fractions = np.ones_like(temp)
    else:
        # Derive the union minute by minute, independently of schedule adapters.
        minute_mask = np.zeros(1440, bool)
        for window in settings['ac_heating_windows']:
            start, end = window['start_minute'], window['end_minute']
            for minute in range(1440):
                if (start <= minute < end if start <= end else minute >= start or minute < end):
                    minute_mask[minute] = True
        clock = civil_hours if settings['timezone_mode'] == 'civil' else hours
        fractions = minute_mask.reshape(24, 60).mean(axis=1)[clock]
        fractions = np.broadcast_to(fractions, temp.shape)
    gas = np.asarray(gas_monthly, float)
    useful = (gas - np.minimum(gas, settings['summer_gas_base']))
    useful = useful * settings['gas_pcs'] * settings['boiler_efficiency']
    useful = useful * (1.0 + settings.get('ac_heating_additional_demand_pct', 0) / 100)
    thermal = np.zeros_like(temp)
    for mm, budget in enumerate(useful, 1):
        mask = active & (month == mm) & (fractions > 0)
        weights = np.where(mask, fractions * np.maximum(settings['heating_balance_temp'] - temp, 0), 0)
        if legacy:
            for row in range(len(temp)):
                if weights[row].sum() == 0:
                    weights[row] = np.where(mask[row], fractions[row], 0)
            denom = weights.sum(axis=1, keepdims=True)
            thermal += np.divide(weights * (budget / settings['scop']), denom,
                                 out=np.zeros_like(weights), where=denom > 0)
        else:
            denominator = weights.sum() / len(temp)
            if denominator > 0:
                thermal += budget * (weights / denominator)
    if legacy:
        return thermal
    raw_cop = np.minimum(1.60, np.maximum(0.45, 1 + .025 * (temp - 7)))
    if thermal.sum() == 0:
        return thermal
    harmonic_cop = thermal.sum() / (thermal / raw_cop).sum()
    return thermal * harmonic_cop / (settings['scop'] * raw_cop)
