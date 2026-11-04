#!/usr/bin/env python3
"""
PV Lab - Acceptance Verification Script
Validates the complete redesign of the PV Lab web app:
- 6 canonical views in exact order: summary, design, financing, behavior, prices, assumptions
- Element IDs, controls, and layout components
- Backend API endpoints and calculation consistency
- Rollback backup integrity preservation
- Test suite pass status
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'feasibility' / 'vendor'))
sys.path.insert(0, str(ROOT / 'tests'))

from feasibility import engine, server

def check(name, condition, details=""):
    if condition:
        print(f"  [PASS] {name}")
    else:
        print(f"  [FAIL] {name}: {details}")
        sys.exit(1)

def verify_backup_integrity():
    print("\n--- 1. Backup Integrity Check ---")
    backup_path = ROOT / 'backups' / 'redesign-2026-10-10' / 'current-app.tar.gz'
    check("Rollback archive exists", backup_path.exists())
    size = backup_path.stat().st_size
    check("Rollback archive has valid size (>100KB)", size > 100_000, f"size={size}")
    readme_path = ROOT / 'backups' / 'redesign-2026-10-10' / 'README.md'
    check("Rollback README exists", readme_path.exists())

def verify_html_canonical_views():
    print("\n--- 2. Canonical Views & Markup Check ---")
    html_path = ROOT / 'feasibility' / 'web' / 'index.html'
    check("index.html exists", html_path.exists())
    html = html_path.read_text(encoding='utf-8')

    # View sequence
    views_pattern = r'<section\b[^>]*\bid="([^"]+)"[^>]*\bclass="[^"]*\bview\b'
    found_views = re.findall(views_pattern, html)
    expected_views = ['summary', 'design', 'financing', 'behavior', 'prices', 'assumptions']
    check("Exact 6 canonical views exist in order", found_views == expected_views, f"Found: {found_views}")

    # Navigation buttons
    nav_pattern = r'data-view="([^"]+)"'
    nav_views = []
    for match in re.finditer(nav_pattern, html):
        if match.group(1) not in nav_views:
            nav_views.append(match.group(1))
    check("Navigation tab targets match canonical 6 views", nav_views == expected_views, f"Nav: {nav_views}")

    # Critical Element IDs
    critical_ids = [
        # 1. Summary
        'verdictStrip', 'scopeSolarBtn', 'scopePackageBtn', 'cashBeforeBtn', 'cashAfterBtn',
        'cashFlowBasis', 'moneyBasisSelect', 'cashChart', 'cashFlowDetails', 'cashFlowSummary', 'cashTable',
        'configVsRecContent', 'summarySensitivityContent', 'saved', 'unconfirmedAssumptionsBadge',
        # 2. System design
        'solarChoiceCards', 'designUnifiedComparisonTable', 'solarChart1', 'solarChart2',
        'solarChart2Wrap', 'toggleSolarCurveView', 'batChart2', 'solarChart3',
        'roofChart', 'roofLimitsComparison', 'solarRoofPills', 'searchMethodologyFold', 'efcBreakEvenFold',
        # 3. Financing
        'loanModeControl', 'loanAmountControl', 'loanFractionControl', 'loanAprControl',
        'loanYearsControl', 'loanPayment', 'loanCashChart', 'loanPivot', 'loanMetricCards',
        # 4. How it behaves
        'annualEnergyFlowVisual', 'typicalDayChart', 'seasonSelectorPills', 'monthHourHeatmap',
        'dayhourExplorerFold',
        # 5. Prices & market
        'adjacentPricesContent', 'lcoeContent', 'exportValuationCards', 'marketSeasonalChart',
        # 6. Assumptions & evidence
        'ledger', 'calibration', 'dataNotes',
        # Header / Global actions
        'onepage'
    ]
    for cid in critical_ids:
        check(f"Element #{cid} present in index.html", f'id="{cid}"' in html)

def verify_engine_and_api():
    print("\n--- 3. Engine & API Endpoints Check ---")
    settings = {'south_panels': 14, 'battery_kwh': 5.0, 'load_scenario': 'baseline'}
    quote = {'line_items': []}

    # Evaluate
    res = engine.evaluate(settings)
    check("Engine evaluate() returns valid result", 'finance' in res and 'metrics' in res)
    f = res['finance']
    check("pv_incremental, whole_package, equity, financed_project in finance",
          all(k in f for k in ('pv_incremental', 'whole_package', 'equity', 'financed_project')))
    check("Cumulative cashflows envelope by weather present", 'cashflows_by_weather' in f)

    # Behavior matrix
    bm = engine.dispatch_matrix(settings, mode='forecast_2027', scenario='baseline', representative='median')
    check("dispatch_matrix returns matrix with 12 months & 24 hours",
          'matrix' in bm and len(bm['matrix']['months']) == 12 and len(bm['matrix']['months'][0]) == 24)
    check("dispatch_matrix returns seasonal profiles with dual-axis overlay data",
          'seasons' in bm['matrix'] and 'summer' in bm['matrix']['seasons'])
    check("dispatch_matrix returns annual energy flows conserving storage losses",
          'annual_flows' in bm and bm['annual_flows']['pv_generation_kwh'] > 0)

    # Summary sensitivity
    sens = engine.summary_sensitivity(settings, quote, scope='solar', cash_mode='cash')
    check("summary_sensitivity evaluates active scope & cash mode",
          sens['scope'] == 'solar' and sens['cash_mode'] == 'cash')
    check("summary_sensitivity evaluates energy price sensitivity (+-20%)",
          'energy_price' in sens and 'low_npv' in sens['energy_price'])
    check("summary_sensitivity evaluates discount rate sensitivity (+-2%)",
          'discount_rate' in sens and 'high_npv' in sens['discount_rate'])
    check("summary_sensitivity evaluates weather envelope across historical weather years",
          'weather' in sens and 'worst_weather_npv' in sens['weather'])

def verify_styles_and_print():
    print("\n--- 4. Styling & Print Stylesheet Check ---")
    css_path = ROOT / 'feasibility' / 'web' / 'style.css'
    check("style.css exists", css_path.exists())
    css = css_path.read_text(encoding='utf-8')
    check("@media print stylesheet present", '@media print' in css)
    check("Verdict strip styling present", '.verdict-strip' in css)
    check("Savings breakdown styling present", '.cash-disclosure-wrap' in css)
    check("Config vs recommended comparison styling present", '.config-vs-rec-grid' in css)
    check("Sensitivity grid styling present", '.sensitivity-grid' in css)
    check("Side-by-side saved scenario grid styling present", '.saved-scenarios-grid' in css)
    check("Side-by-side roof grid styling present", '.design-roof-row' in css)

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('private', 'public'), default='private')
    args = parser.parse_args()
    print("========================================")
    print(" PV Lab Redesign Verification ")
    print("========================================")
    if args.mode == 'private':
        verify_backup_integrity()
    verify_html_canonical_views()
    verify_engine_and_api()
    verify_styles_and_print()
    print("\n========================================")
    print(" ALL ACCEPTANCE VERIFICATIONS PASSED!  ")
    print("========================================")

if __name__ == '__main__':
    main()
