"""Hand-calculated finance, hourly constraints and data conservation acceptance tests."""
import calendar,copy,json,math,unittest
import numpy as np
from unittest.mock import patch
from feasibility import engine as e
from feasibility import assumptions
from test_support import IsolatedDefaultsTestCase

class FinanceTests(IsolatedDefaultsTestCase):
    def test_configuration_optimizer_exhaustively_searches_real_integer_layouts_and_fixed_equipment(self):
        calls=[]
        def fake_evaluate(settings):
            valid=e.validate(settings);calls.append(valid)
            panels=valid['south_panels'];modules=valid['battery_modules']
            # Deliberately make the NPV winner differ from the autarky winner.
            scores={'autarky':panels+modules,'npv_eur':10-abs(panels-1)*3-modules*2,
                    'self_consumption':panels,'payback_years':panels+modules+1,
                    'discounted_payback_years':panels+modules+2}
            metrics={key:{'min':value,'median':value,'mean':value,'max':value} for key,value in scores.items()}
            metrics['simple_payback_of_mean_cash_flows']=float(scores['payback_years'])
            metrics['discounted_payback_of_mean_cash_flows']=float(scores['discounted_payback_years'])
            return {'settings':valid,'layout':{'theoretical':panels>15,'kwp':panels*.4,'panel_count':panels,'counts':{'south':panels}},
                    'metrics':metrics,'finance':{'capex_eur':100+panels*10+modules*20,'pv_incremental':{'npv':scores['npv_eur']},'whole_package':{'npv':scores['npv_eur']-100},'lcoe':{'median':.1}}}
        settings={'south_panels':18,'battery_modules':3,'battery_module_kwh':4.2,'battery_kwh':12.6,
                  'panel_watts':500,'inverter_kw':7.25,'charge_kw':4,'discharge_kw':6,'quote_total':9999}
        with patch('feasibility.engine.evaluate',side_effect=fake_evaluate):
            autarky=e.optimize_configuration(settings,{'panel_maxima':{'south':3,'west':0,'east':0,'dormer':0,'flat':0,'secondary_south':0},'battery_max_modules':2})
            npv=e.optimize_configuration(settings,{'objective':'npv_eur','panel_maxima':{'south':3,'west':0,'east':0,'dormer':0,'flat':0,'secondary_south':0},'battery_max_modules':2})
            pv_bounded=e.optimize_configuration({**settings,'pv_max_kwp':1.0},{'panel_maxima':{'south':3,'west':0,'east':0,'dormer':0,'flat':0,'secondary_south':0},'battery_max_modules':0})
        self.assertTrue(autarky['search']['exact'])
        self.assertEqual(autarky['search']['search_space_size'],12)
        self.assertEqual(autarky['best_candidate']['settings']['south_panels'],3)
        self.assertEqual(autarky['best_candidate']['settings']['battery_modules'],2)
        self.assertEqual(npv['best_candidate']['settings']['south_panels'],1)
        self.assertEqual(npv['best_candidate']['settings']['battery_modules'],0)
        self.assertTrue(pv_bounded['candidates'])
        self.assertTrue(all(c['settings']['south_panels']*0.5<=1.0 for c in pv_bounded['candidates']))
        self.assertEqual(len(autarky['candidates']),10)  # two positive-battery zero-PV rows are physically invalid
        for candidate in autarky['candidates']:
            row=candidate['settings']
            self.assertEqual((row['inverter_kw'],row['charge_kw'],row['discharge_kw'],row['panel_watts']),(7.25,4,6,500))
            self.assertEqual(row['battery_module_kwh'],4.2)
            self.assertAlmostEqual(row['battery_kwh'],row['battery_modules']*4.2)
            self.assertIsNone(row['quote_total'])
        self.assertTrue(any(row['settings']['south_panels']==3 for row in autarky['candidates']))

    def test_configuration_optimizer_ignores_current_zero_pv_and_battery_total(self):
        def fake_evaluate(settings):
            valid=e.validate(settings);value=valid['south_panels']+valid['battery_modules']
            metrics={k:{'median':value,'mean':value} for k in ['npv_eur','autarky','self_consumption','payback_years','discounted_payback_years']}
            metrics['simple_payback_of_mean_cash_flows']=float(value)
            metrics['discounted_payback_of_mean_cash_flows']=float(value)
            finance={'capex_eur':value,'pv_incremental':{'npv':value},'whole_package':{'npv':value},'lcoe':{}}
            return {'settings':valid,'layout':{'theoretical':False,'kwp':0,'panel_count':value,'counts':{}},'metrics':metrics,'finance':finance}
        settings={'south_panels':0,'west_panels':0,'east_panels':0,'dormer_panels':0,'flat_panels':0,'secondary_south_panels':0,
                  'battery_modules':0,'battery_kwh':0,'battery_module_kwh':6}
        with patch('feasibility.engine.evaluate',side_effect=fake_evaluate):
            result=e.optimize_configuration(settings,{'panel_maxima':{'south':2,'west':0,'east':0,'dormer':0,'flat':0,'secondary_south':0},'battery_max_modules':1})
        self.assertEqual(result['best_candidate']['settings']['south_panels'],2)
        self.assertEqual(result['best_candidate']['settings']['battery_module_kwh'],6)
        self.assertEqual(result['best_candidate']['settings']['battery_kwh'],6)

    def test_configuration_optimizer_validates_integer_bounds_and_labels_large_search(self):
        for search in [{'battery_min_modules':3,'battery_max_modules':2},{'battery_max_modules':1.5},
                       {'panel_maxima':{'south':-1}},{'panel_maxima':{'roof':2}}]:
            with self.subTest(search=search),self.assertRaises(ValueError):e.optimize_configuration({},search)
        maxima={'south':30,'west':0,'east':0,'dormer':0,'flat':0,'secondary_south':0}
        def fake_evaluate(settings):
            valid=e.validate(settings);value=valid['south_panels']
            metrics={k:{'median':value,'mean':value} for k in ['npv_eur','autarky','self_consumption','payback_years','discounted_payback_years']}
            metrics['simple_payback_of_mean_cash_flows']=float(value)
            metrics['discounted_payback_of_mean_cash_flows']=float(value)
            finance={'capex_eur':value,'pv_incremental':{'npv':value},'whole_package':{'npv':value},'lcoe':{}}
            return {'settings':valid,'layout':{'theoretical':value>15,'kwp':value*.4,'panel_count':value,'counts':{}},'metrics':metrics,'finance':finance}
        with patch('feasibility.engine.evaluate',side_effect=fake_evaluate):
            result=e.optimize_configuration({}, {'panel_maxima':maxima,'battery_max_modules':50})
        self.assertFalse(result['search']['exact'])
        self.assertEqual(result['search']['search_method'],'sampled_discrete_coordinate_refinement')
        self.assertTrue(result['search']['best_found'])
        self.assertFalse(result['search']['certified_global_optimum'])
        self.assertLessEqual(result['search']['evaluated_count'],result['search']['evaluation_budget'])
        self.assertLessEqual(result['search']['evaluation_budget'],500)
        self.assertEqual(result['best_candidate']['settings']['south_panels'],30)

    def test_configuration_optimizer_keeps_huge_bounds_lazy_and_rejects_battery_without_any_pv(self):
        empty_roofs={name:0 for name in e.OPTIMIZE_ROOFS}
        with self.assertRaisesRegex(ValueError,'No feasible configurations'):
            e.optimize_configuration({}, {'panel_maxima':empty_roofs,'battery_min_modules':1,'battery_max_modules':1})
        maxima={**empty_roofs,'south':100_000_000}
        def fake_evaluate(settings):
            valid=e.validate(settings);value=valid['south_panels']
            metrics={k:{'median':float(value),'mean':float(value)} for k in ['npv_eur','autarky','self_consumption','payback_years','discounted_payback_years']}
            metrics['simple_payback_of_mean_cash_flows']=float(value)
            metrics['discounted_payback_of_mean_cash_flows']=float(value)
            finance={'capex_eur':float(value),'pv_incremental':{'npv':float(value)},'whole_package':{'npv':float(value)},'lcoe':{}}
            return {'settings':valid,'layout':{'theoretical':True,'kwp':value*.4,'panel_count':value,'counts':{}},'metrics':metrics,'finance':finance}
        with patch('feasibility.engine.evaluate',side_effect=fake_evaluate):
            result=e.optimize_configuration({}, {'panel_maxima':maxima,'battery_max_modules':0})
        self.assertEqual(result['search']['requested_panel_maxima']['south'],100_000_000)
        self.assertFalse(result['search']['exact'])
        self.assertLessEqual(result['search']['evaluated_count'],400)
        self.assertEqual(result['best_candidate']['settings']['south_panels'],100_000_000)
        self.assertTrue(result['best_candidate']['layout']['theoretical'])
        self.assertTrue(result['search']['best_at_panel_maxima']['south'])

    def test_unbounded_layout_and_battery_inputs_and_module_capacity(self):
        s=e.validate({'south_panels':100,'west_panels':25,'east_panels':30,'dormer_panels':12,
                      'flat_panels':14,'secondary_south_panels':9,'panel_watts':520,
                      'battery_kwh':90})
        self.assertEqual(s['south_panels'],100)
        self.assertEqual(s['panel_watts'],520)
        self.assertEqual(s['battery_kwh'],90)
        modules=e.validate({'battery_modules':4,'battery_module_kwh':7.5})
        self.assertEqual(modules['battery_kwh'],30)
        self.assertEqual(modules['battery_modules'],4)
        explicit=e.validate({'battery_kwh':22,'battery_modules':4,'battery_module_kwh':5})
        self.assertEqual(explicit['battery_kwh'],22)
        self.assertEqual(explicit['battery_module_kwh'],5.5)
        zero_count=e.validate({'battery_modules':0,'battery_module_kwh':7})
        self.assertEqual(zero_count['battery_kwh'],0)
        self.assertEqual(zero_count['battery_module_kwh'],7)
        repaired=e.validate({'battery_modules':0,'battery_module_kwh':7,'battery_kwh':9})
        self.assertEqual((repaired['battery_modules'],repaired['battery_module_kwh']),(1,9))
        for invalid in [{'east_panels':-1},{'east_panels':1.5},{'panel_watts':0},
                        {'battery_modules':1.5},{'battery_module_kwh':-1}]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):e.validate(invalid)
        records={item['name']:item for item in e.records()}
        self.assertIsNone(records['south_panels']['bounds'][1])
        self.assertIsNone(records['battery_kwh']['bounds'][1])

    def test_sweep_includes_custom_layout_total_and_equipment_metadata(self):
        def fake_evaluate(settings):
            valid=e.validate(settings)
            zero={'min':0,'median':0,'max':0,'mean':0}
            metrics={name:dict(zero) for name in ['npv_eur','autarky','self_consumption','payback_years','discounted_payback_years']}
            metrics['simple_payback_of_mean_cash_flows']=0.0
            metrics['discounted_payback_of_mean_cash_flows']=0.0
            return {'settings':valid,'layout':{'theoretical':valid['south_panels']>22,'counts':{},'kwp':1},'metrics':metrics,'energy':{},'finance':{}}
        selected={'south_panels':27,'west_panels':2,'battery_modules':2,'battery_module_kwh':3.5,
                  'inverter_kw':7.25,'charge_kw':4,'discharge_kw':6,'panel_watts':500}
        with patch('feasibility.engine.evaluate',side_effect=fake_evaluate):
            result=e.sweep(selected)
        custom=next(c for c in result['candidates'] if c['label']=='custom' and c['load_scenario']=='baseline')
        self.assertEqual(custom['battery_kwh'],7)
        self.assertEqual(custom['settings']['inverter_kw'],7.25)
        self.assertEqual(custom['settings']['charge_kw'],4)
        self.assertEqual(custom['settings']['discharge_kw'],6)
        self.assertEqual(custom['settings']['battery_modules'],2)
        self.assertEqual(custom['settings']['battery_module_kwh'],3.5)
        preset=next(c for c in result['candidates'] if c['label']=='12S' and c['load_scenario']=='baseline' and c['battery_kwh']==7)
        self.assertEqual(preset['settings']['battery_kwh'],7)
        self.assertAlmostEqual(preset['settings']['battery_modules']*preset['settings']['battery_module_kwh'],7)
    def test_discount_and_multiple_irr(self):
        self.assertAlmostEqual(e.npv([-100,110],.1),0)
        self.assertAlmostEqual(e.irr_roots([-100,110])[0],.1)
        roots=e.irr_roots([-100,230,-132]);self.assertEqual(len(roots),2)
        self.assertAlmostEqual(roots[0],.1);self.assertAlmostEqual(roots[1],.2)
    def test_persistent_crossing_after_replacement(self):
        self.assertAlmostEqual(e.persistent_payback([-100,120,-100,100]),2.8)
        self.assertIsNone(e.persistent_payback([-100,120,-100]))
    def test_zero_payback_plateau_does_not_hide_later_recovery(self):
        # Cumulative cash flow reaches zero, falls below zero, then recovers.
        self.assertAlmostEqual(e.persistent_payback([-100,100,0,-20,30]),3+2/3)
    def test_loan_zero_interest_and_terminal_debt(self):
        p,b=e.loan_schedule(1200,0,2,2);np.testing.assert_allclose(p,[600,600]);self.assertAlmostEqual(b[-1],0)
        p,b=e.loan_schedule(1200,0,2,1);self.assertAlmostEqual(p[-1],1200);self.assertAlmostEqual(b[-1],0)
    def test_unpaid_weather_years_are_not_dropped(self):
        paybacks=[2,3]+[None]*17
        censored=e.payback_envelope(paybacks)
        self.assertIsNone(censored['median']);self.assertIsNone(censored['max'])
        self.assertEqual(censored['recovered_weather_years'],2)
        self.assertEqual(censored['weather_count'],len(paybacks))
    def test_load_efficiency_affects_pv_and_whole_house_payback_differently(self):
        settings={'load_scenario':'electrified','south_panels':18,'west_panels':0,'east_panels':0,
                  'battery_kwh':20,'inverter_kw':8.5,'quote_total':10000,
                  'scop':3,'dhw_heating_type':'resistance'}
        resistance=e.evaluate(settings)
        heat_pump=e.evaluate({**settings,'dhw_heating_type':'heat_pump','dhw_scop':3})
        more_efficient_heating=e.evaluate({**settings,'scop':5.1})
        for improved in [heat_pump,more_efficient_heating]:
            self.assertLess(improved['energy']['load_kwh']['median'],resistance['energy']['load_kwh']['median'])
            self.assertLess(improved['energy']['grid_import_kwh']['median'],resistance['energy']['grid_import_kwh']['median'])
            self.assertGreater(improved['energy']['export_kwh']['median'],resistance['energy']['export_kwh']['median'])
            self.assertGreater(improved['metrics']['payback_years']['median'],resistance['metrics']['payback_years']['median'])
            self.assertGreater(improved['metrics']['package_npv_eur']['median'],resistance['metrics']['package_npv_eur']['median'])
        self.assertGreater(heat_pump['finance']['whole_package']['simple_payback']['median'],0)
        self.assertLess(heat_pump['finance']['whole_package']['simple_payback']['median'],resistance['finance']['whole_package']['simple_payback']['median'])
        self.assertLess(heat_pump['finance']['whole_package']['discounted_payback']['median'],resistance['finance']['whole_package']['discounted_payback']['median'])
        self.assertLess(more_efficient_heating['finance']['whole_package']['discounted_payback']['median'],resistance['finance']['whole_package']['discounted_payback']['median'])
    def test_invoice_gas_and_catalog_calibration(self):
        s=e.defaults()
        from feasibility.runtime_context import current_runtime
        from feasibility.fiscal import gas_cost
        supplied=current_runtime().metadata['presentation']
        expected=gas_cost(volume=float(e.GAS_MONTHLY.sum()),days=365,year=1,
                          prior_year_smc=0.,include_fixed=True,settings=s)['total_eur']
        self.assertAlmostEqual(e.gas_bill(s),expected)
        kwh=supplied['electric_invoice_kwh']
        invoice=e.current_invoice_check(s,kwh=kwh)
        self.assertEqual(invoice['kwh'],kwh)
        self.assertAlmostEqual(invoice['total_eur'],sum(invoice[k] for k in
            ('commodity_eur','regulated_eur','excise_eur','fixed_eur','vat_eur')))
        expected_capex=s['pv_fixed_cost']+s['pv_cost_kwp']*6
        self.assertAlmostEqual(e.installed_capex({**s,'battery_kwh':0},6),expected_capex)
        self.assertAlmostEqual(e.installed_capex({**s,'battery_kwh':0,'deduction_price_premium':.2},6),
                               expected_capex*(1.2 if s['deduction_rate']>0 else 1.))
        self.assertEqual(e.installed_capex({**s,'quote_total':10000,'deduction_price_premium':.2},6),10000)
    def test_retail_commodity_energy_plus_dispatching(self):
        cases = [
            ('snapshot', .098494, .01846790, .014730 + .031515 + .001638, 8.09225, 4.5, 181.71),
            ('fastweb', .119, .016776, .047883, 12.0, 3.0, 198.89),
        ]
        for name, energy, dispatch, regulated, supplier, power, expected in cases:
            with self.subTest(case=name):
                s = e.validate({**e.defaults(), 'retail_commodity': energy + dispatch,
                                'retail_regulated': regulated, 'supplier_fixed_month': supplier,
                                'network_fixed_month': 1.92, 'power_fixed_kw_month': 1.97555556,
                                'baseline_power_kw': power, 'electric_excise': .0227, 'electric_vat': .1})
                monthly = e.electric_bill(9360, s) / 12
                self.assertEqual(round(monthly, 2), expected)
                without_dispatch = e.electric_bill(9360, {**s, 'retail_commodity': energy}) / 12
                self.assertAlmostEqual(monthly - without_dispatch, 780 * dispatch * 1.1)
                fixed = (supplier + 1.92 + 1.97555556 * power) * 1.1
                self.assertAlmostEqual(e.electric_bill(0, s) / 12, fixed)
    def test_real_and_nominal_consistency(self):
        inflation=.02;rreal=.03;rnom=(1+inflation)*(1+rreal)-1
        cash=np.array([-1000,400,400,400.]);real=cash/(1+inflation)**np.arange(4)
        self.assertAlmostEqual(e.npv(cash,rnom),e.npv(real,rreal))
    def test_equity_discounted_payback_uses_initial_equity_and_debt_service(self):
        def financial(loan_fraction,benefits):
            horizon=len(benefits)
            annual=np.zeros((horizon,1,len(e.FIELDS)))
            annual[:,0,0]=np.asarray(benefits)/.1
            annual[:,0,5]=0
            settings=e.validate({'horizon_years':horizon,'south_panels':12,'battery_kwh':0,'quote_total':100,'deduction_rate':0,'real_discount_rate':.1,'inflation':0,'retail_commodity':.1,'retail_regulated':0,'electric_excise':0,'electric_vat':0,'supplier_fixed_month':0,'network_fixed_month':0,'power_fixed_kw_month':0,'om_annual':0,'insurance_annual':0,'cleaning_annual':0,'gse_fee_eur_kw_year':0,'inverter_replace_year':0,'battery_replace_year':0,'loan_fraction':loan_fraction,'loan_apr':0,'loan_years':2,'loan_upfront_fees_or_tax':0,'pmg_election':False})
            with patch('feasibility.engine.price_profile',return_value=(np.ones((1,1)),'flat_solar_capture_benchmark',None)),patch('feasibility.engine.data',return_value={'audit':{'annual_kwh':5000}}):
                return e._financial(settings,annual,4.8)[0]

        partial=financial(.5,[60,60])
        partial_flows=partial['cashflows_by_weather']['equity'][0]
        np.testing.assert_allclose(partial_flows,[-50,35,35])
        # Discount monthly debt at its payment dates, while annual benefits arrive at year end.
        debt_pv = [sum((50 / 24) / 1.1 ** (m / 12) for m in range(1 + 12*y, 13 + 12*y)) for y in range(2)]
        expected_partial = 1 + (50 - 60/1.1 + debt_pv[0]) / (60/1.1**2 - debt_pv[1])
        self.assertAlmostEqual(partial['equity']['discounted_payback']['median'],expected_partial,places=12)

        full=financial(1,[30,30,60])
        full_flows=full['cashflows_by_weather']['equity'][0]
        np.testing.assert_allclose(full_flows,[0,-20,-20,60])
        # The last negative cumulative balance is at year 2; recovery must remain persistent.
        expected_full = 2 + (2*sum(debt_pv) - 30/1.1 - 30/1.1**2) / (60/1.1**3)
        self.assertAlmostEqual(full['equity']['discounted_payback']['median'],expected_full,places=12)
        self.assertGreater(full['equity']['discounted_payback']['median'],2.5,'early post-debt losses must postpone payback after full financing')

    def test_financed_project_discounted_payback_includes_interest_once(self):
        def financial(benefits,loan_fraction,apr,loan_years=2,**extra):
            horizon=len(benefits)
            annual=np.zeros((horizon,1,len(e.FIELDS)))
            annual[:,0,0]=np.asarray(benefits)/.1
            settings=e.validate({'horizon_years':horizon,'south_panels':12,'battery_kwh':0,'quote_total':100,'deduction_rate':0,'real_discount_rate':.1,'inflation':0,'retail_commodity':.1,'retail_regulated':0,'electric_excise':0,'electric_vat':0,'supplier_fixed_month':0,'network_fixed_month':0,'power_fixed_kw_month':0,'om_annual':0,'insurance_annual':0,'cleaning_annual':0,'gse_fee_eur_kw_year':0,'inverter_replace_year':0,'battery_replace_year':0,'loan_fraction':loan_fraction,'loan_apr':apr,'loan_years':loan_years,'loan_upfront_fees_or_tax':0,'pmg_election':False,**extra})
            with patch('feasibility.engine.price_profile',return_value=(np.ones((1,1)),'flat_solar_capture_benchmark',None)),patch('feasibility.engine.data',return_value={'audit':{'annual_kwh':5000}}):
                return e._financial(settings,annual,4.8)

        rate=.05/12
        monthly_payment=50*rate/(1-(1+rate)**-24)
        balance_after_year1=50*(1+rate)**12-monthly_payment*(((1+rate)**12-1)/rate)
        first_year_interest=12*monthly_payment-(50-balance_after_year1)
        second_year_interest=12*monthly_payment-balance_after_year1
        for loan_fraction in [.5,1]:
            finance,cash=financial([60,60,60],loan_fraction,0)
            project_flows=finance['cashflows_by_weather']['pv_incremental'][0]
            financed=finance['cashflows_by_weather']['financed_project'][0]
            np.testing.assert_allclose(financed,project_flows,atol=1e-12)

        partial,partial_cash=financial([60,60,60],.5,.05)
        full,full_cash=financial([60,60,60],1,.05)
        self.assertAlmostEqual(partial_cash[1]['loan_interest'],first_year_interest,places=10)
        self.assertAlmostEqual(partial_cash[2]['loan_interest'],second_year_interest,places=10)
        self.assertAlmostEqual(partial_cash[1]['loan_interest']+partial_cash[1]['loan_principal'],partial_cash[1]['debt_service'],places=10)
        self.assertGreater(partial_cash[1]['loan_interest'],0)
        self.assertAlmostEqual(full_cash[1]['loan_interest'],2*partial_cash[1]['loan_interest'],places=10)
        self.assertAlmostEqual(full_cash[2]['loan_interest'],2*partial_cash[2]['loan_interest'],places=10)
        for year in [1,2]:
            project=partial['cashflows_by_weather']['pv_incremental'][0][year]
            financed=partial['cashflows_by_weather']['financed_project'][0][year]
            self.assertAlmostEqual(project-financed,partial_cash[year]['loan_interest'],places=10)
            self.assertAlmostEqual(partial_cash[year]['loan_principal']+partial_cash[year]['loan_interest'],partial_cash[year]['debt_service'],places=10)
        partial_flows=partial['cashflows_by_weather']['financed_project'][0]
        after_year1=-100+(60-first_year_interest)/1.1
        expected_partial_payback=1+(-after_year1)/((60-second_year_interest)/1.1**2)
        self.assertAlmostEqual(partial_flows[1],60-first_year_interest,places=10)
        self.assertAlmostEqual(partial_flows[2],60-second_year_interest,places=10)
        self.assertAlmostEqual(partial['financed_project']['discounted_payback']['median'],expected_partial_payback,places=10)

        full_flows=full['cashflows_by_weather']['financed_project'][0]
        self.assertAlmostEqual(full_flows[1],60-2*first_year_interest,places=10)
        self.assertAlmostEqual(full_flows[2],60-2*second_year_interest,places=10)
        after_year2=-100+(60-2*first_year_interest)/1.1+(60-2*second_year_interest)/1.1**2
        expected_full_payback=2+(-after_year2)/(60/1.1**3)
        self.assertAlmostEqual(full['financed_project']['discounted_payback']['median'],expected_full_payback,places=10)
        high_rate=financial([60,60,60],1,.2)[0]['financed_project']['discounted_payback']['median']
        self.assertGreater(high_rate,expected_full_payback,'higher interest delays discounted payback')

        real,real_cash=financial([60,60,60],.5,.05,cashflow_mode='real',inflation=.02)
        nominal_year2=real_cash[2]
        factor=1.02**2
        self.assertAlmostEqual(nominal_year2['display_loan_interest'],nominal_year2['loan_interest']/factor,places=10)
        self.assertAlmostEqual(nominal_year2['display_loan_principal'],nominal_year2['loan_principal']/factor,places=10)
        self.assertAlmostEqual(nominal_year2['display_financed_project_range']['median'],nominal_year2['financed_project_range']['median']/factor,places=10)
        self.assertIn('display_residual',nominal_year2)
        self.assertIn('display_replacement',nominal_year2)
        self.assertAlmostEqual(nominal_year2['display_residual'],nominal_year2['residual']/factor,places=10)
        self.assertAlmostEqual(nominal_year2['display_replacement'],nominal_year2['replacement']/factor,places=10)

        # When a loan extends beyond the horizon, the terminal balloon is principal,
        # not interest in the financed-project flow.
        truncated,truncated_cash=financial([60,60],.5,.12,loan_years=5)
        self.assertAlmostEqual(truncated_cash[2]['loan_balance'],0)
        self.assertAlmostEqual(sum(c['loan_principal'] for c in truncated_cash[1:]),50,places=9)
        self.assertGreater(truncated_cash[2]['loan_principal'],truncated_cash[2]['loan_interest'])
        self.assertAlmostEqual(truncated_cash[2]['debt_service'],truncated_cash[2]['loan_principal']+truncated_cash[2]['loan_interest'],places=10)

        recovery,recovery_cash=financial([120,0,0,100],0,0,inverter_replace_year=2,inverter_replacement_cost=80)
        recovery_flows=recovery['cashflows_by_weather']['financed_project'][0]
        self.assertAlmostEqual(recovery['financed_project']['discounted_payback']['median'],e.persistent_payback(recovery_flows,.1),places=12)
        self.assertGreater(recovery['financed_project']['discounted_payback']['median'],3.5,'a later replacement loss must delay persistent payback')
        censored=financial([20,20],0,0)[0]['financed_project']['discounted_payback']
        self.assertIsNone(censored['median'])
        self.assertEqual(censored['unrecovered_weather_years'],1)

class PhysicsTests(IsolatedDefaultsTestCase):
    def test_inverter_and_discharge_modeling_bounds_cover_powerwall_example(self):
        for inverter in [11,11.04,20]:
            self.assertEqual(e.validate({'inverter_kw':inverter})['inverter_kw'],inverter)
        for discharge in [11.04,20]:
            self.assertEqual(e.validate({'discharge_kw':discharge})['discharge_kw'],discharge)
        for settings in [{'inverter_kw':20.01},{'discharge_kw':20.01},{'charge_kw':10.01}]:
            with self.assertRaises(ValueError):e.validate(settings)
        quote=e.quote_evaluate({'south_panels':0,'battery_kwh':0,'inverter_kw':11.04},{'continuous_discharge_kw':11.04})
        self.assertEqual(quote['result']['settings']['inverter_kw'],11.04)
        self.assertEqual(quote['result']['settings']['discharge_kw'],11.04)
        record_map={r['name']:r for r in e.records()}
        for name,bounds in [('inverter_kw',[3,20]),('discharge_kw',[0,20])]:
            self.assertEqual(record_map[name]['bounds'],bounds)
            self.assertEqual(record_map[name]['source'],'Neutral modeling example; profile-specific evidence required')
            self.assertIsNone(record_map[name]['date_accessed'])
            self.assertIn('not a product rating',record_map[name]['description'])

    def test_high_power_dispatch_clips_pv_and_shares_inverter_with_battery(self):
        pv=np.array([12.,0.,2.]);load=np.array([2.,20.,20.]);valid=np.ones(3,dtype=np.bool_)
        row,trace=e.dispatch_year(pv,load,30.,10.,20.,1.,11.04,30.,np.ones(3),valid)
        self.assertAlmostEqual(row[9],.96,places=12)
        self.assertAlmostEqual(trace[0,0],11.04,places=12)
        self.assertAlmostEqual(trace[2,5],2.,places=12)
        self.assertAlmostEqual(trace[2,7],9.04,places=12)
        self.assertTrue(np.all(trace[:,5]+trace[:,7]<=11.04+1e-12))
        self.assertLess(row[13],1e-10)
        # Existing 3 kW behavior remains unchanged.
        small,_=e.dispatch_year(np.array([4.]),np.array([1.]),2.,2.,2.,.9,3.,0.,np.ones(1),np.ones(1,dtype=np.bool_))
        self.assertAlmostEqual(small[1],3.)
        self.assertAlmostEqual(small[9],1.)

        # Exercise the expanded limit against the selected roof and load profiles.
        settings=e.validate({'south_panels':12,'west_panels':2,'battery_kwh':10,'charge_kw':5,'discharge_kw':11.04,'inverter_kw':11.04})
        d=e.data();actual_pv=e.pv_profile(settings)[0];actual_load=e.load_profiles(settings)[0][0]
        actual,actual_trace=e.dispatch_year(actual_pv,actual_load,settings['battery_kwh'],settings['charge_kw'],settings['discharge_kw'],math.sqrt(settings['roundtrip_efficiency']),settings['inverter_kw'],0.,e.synthetic_prices()[0],d['valid'][0])
        self.assertLess(actual[13],1e-9)
        self.assertTrue(np.any(actual_trace[:,7]>0))
        self.assertTrue(np.all(actual_trace[:,5]+actual_trace[:,7]<=11.04+1e-10))

    def test_sweep_sizes_large_pv_candidates_above_old_inverter_cap(self):
        seen=[]
        def fake_evaluate(settings):
            seen.append(dict(settings))
            metric=lambda value:{'median':value,'mean':value}
            return {'layout':{'theoretical':bool(settings.get('dormer_south_proxy'))},
                    'metrics':{'npv_eur':metric(0),'autarky':metric(0),'self_consumption':metric(0),'payback_years':metric(10),'discounted_payback_years':metric(10),
                               'simple_payback_of_mean_cash_flows':10.0,'discounted_payback_of_mean_cash_flows':10.0},
                    'energy':{},'finance':{}}
        with patch('feasibility.engine.evaluate',side_effect=fake_evaluate):
            result=e.sweep({'panel_watts':400})
        large=next(s for s in seen if s['south_panels']==20 and s['secondary_south_panels']==4 and s['dormer_panels']==4 and s['battery_kwh']==0)
        self.assertEqual(large['inverter_kw'],12)
        self.assertIn('within 3–20 kW modeling bounds',result['definition'])

    def test_user_ac_scop_ratings_and_heating_only_change(self):
        record=next(r for r in e.records() if r['name']=='scop')
        self.assertEqual(e.defaults()['scop'],4.0)
        self.assertEqual(record['status'],'proposed')
        self.assertIsNone(record['date_accessed'])
        self.assertEqual(record['plausible_range'],list(assumptions.SPEC['scop'][2]))
        self.assertNotIn('provided_ratings',record)
        profiles=[]
        for scop in [4.0,5.1]:
            s=e.validate({'load_scenario':'ev','scop':scop})
            load,ev,end=e.load_profiles(s);profiles.append((load,ev,end))
        a,b=profiles
        self.assertAlmostEqual(b[2]['heating_kwh']/a[2]['heating_kwh'],4.0/5.1)
        self.assertAlmostEqual(a[2]['heating_kwh'],a[2]['heating_smc']*e.defaults()['gas_pcs']*e.defaults()['boiler_efficiency']/4.0)
        for key in ['heating_smc','dhw_smc','cooking_smc','dhw_kwh','cooking_kwh','ev_kwh']:
            self.assertEqual(a[2][key],b[2][key])
        np.testing.assert_array_equal(a[1],b[1])
        heating_deltas=(a[0]-b[0]).sum(axis=1)
        self.assertAlmostEqual(float(heating_deltas.mean()),a[2]['heating_kwh']-b[2]['heating_kwh'],places=7)
        self.assertTrue(np.all(heating_deltas>0))
        baseline_a=e.load_profiles(e.validate({'scop':4.0}))[0]
        baseline_b=e.load_profiles(e.validate({'scop':5.1}))[0]
        np.testing.assert_array_equal(baseline_a,baseline_b)
        for invalid in [0,-1,8.1,float('nan'),float('inf'),True]:
            with self.assertRaises(ValueError):e.validate({'scop':invalid})
        edited=e.evaluate({'scop':3,'horizon_years':1})
        edited_record=next(r for r in edited['assumptions'] if r['name']=='scop')
        self.assertEqual(edited_record['status'],'proposed')
        self.assertEqual(edited_record['base_status'],'proposed')
        self.assertNotIn('provided_ratings',edited_record)

    def test_dhw_resistance_and_heat_pump_use_distinct_efficiency_controls(self):
        base=e.validate({'load_scenario':'electrified','dhw_heating_type':'resistance'})
        heat_pump=e.validate({'load_scenario':'electrified','dhw_heating_type':'heat_pump','dhw_scop':3.5})
        _,_,resistance_end=e.load_profiles(base);_,_,hp_end=e.load_profiles(heat_pump)
        self.assertAlmostEqual(resistance_end['dhw_kwh'],resistance_end['dhw_smc']*base['gas_pcs']*base['boiler_efficiency']/base['dhw_resistance_efficiency'])
        self.assertAlmostEqual(hp_end['dhw_kwh'],hp_end['dhw_smc']*heat_pump['gas_pcs']*heat_pump['boiler_efficiency']/3.5)
        self.assertLess(hp_end['dhw_kwh'],resistance_end['dhw_kwh'])

    def test_absolute_loan_can_include_conversion_without_changing_tax_basis(self):
        horizon=2;annual=np.zeros((horizon,1,len(e.FIELDS)))
        settings=e.validate({'horizon_years':horizon,'south_panels':12,'battery_kwh':0,'quote_total':100,'deduction_rate':.5,'loan_mode':'amount','loan_amount':1200,'loan_include_electrification':True,'load_scenario':'electrified','real_discount_rate':0,'inflation':0,'loan_apr':0,'loan_years':2,'loan_upfront_fees_or_tax':0})
        with patch('feasibility.engine.price_profile',return_value=(np.ones((1,1)),'flat_solar_capture_benchmark',None)),patch('feasibility.engine.data',return_value={'audit':{'annual_kwh':5000}}):
            finance,cash=e._financial(settings,annual,4.8)
        self.assertEqual(finance['loan_principal_eur'],1200)
        self.assertEqual(finance['loan_scope'],'solar_and_conversion')
        self.assertEqual(finance['eligible_deduction_capex_eur'],100)
        self.assertEqual(cash[0]['equity'],-100-(settings['electrification_capex']+settings['gas_disconnect_cost'])+1200)
        self.assertEqual(cash[0]['financed_project'],-100-(settings['electrification_capex']+settings['gas_disconnect_cost']))
        for row in cash[1:]:
            self.assertAlmostEqual(row['financed_project'],row['whole_package']-row['loan_interest'])
            self.assertAlmostEqual(row['equity'],row['whole_package']-row['debt_service'])
        self.assertAlmostEqual(sum(row['loan_principal'] for row in cash[1:]),1200)
        with patch('feasibility.engine.price_profile',return_value=(np.ones((1,1)),'flat_solar_capture_benchmark',None)),patch('feasibility.engine.data',return_value={'audit':{'annual_kwh':5000}}):
            capped,_=e._financial(e.validate({**settings,'loan_include_electrification':False}),annual,4.8)
        self.assertEqual(capped['loan_principal_eur'],100)
        fraction_settings=e.validate({**settings,'loan_mode':'fraction','loan_fraction':.5})
        with patch('feasibility.engine.price_profile',return_value=(np.ones((1,1)),'flat_solar_capture_benchmark',None)),patch('feasibility.engine.data',return_value={'audit':{'annual_kwh':5000}}):
            included,_=e._financial(fraction_settings,annual,4.8)
            excluded,_=e._financial(e.validate({**fraction_settings,'loan_include_electrification':False}),annual,4.8)
        self.assertAlmostEqual(included['loan_principal_eur'],.5*(100+6150))
        self.assertAlmostEqual(included['annual_deduction_eur'],excluded['annual_deduction_eur'])
    def test_dispatch_hand_balance_limits_and_efficiency(self):
        pv=np.array([4.,0.,0.]);load=np.array([1.,1.,3.]);valid=np.ones(3,dtype=np.bool_)
        row,trace=e.dispatch_year(pv,load,2.,2.,2.,.9,3.,0.,np.ones(3),valid)
        self.assertAlmostEqual(row[1],3);self.assertAlmostEqual(row[9],1)
        self.assertAlmostEqual(row[3],2);self.assertAlmostEqual(row[4],1.62)
        self.assertAlmostEqual(row[5],2.38);self.assertAlmostEqual(row[7],.38)
        self.assertLess(row[-1],1e-10)
        self.assertTrue(np.all(trace[:,4]>=0));self.assertTrue(np.all(trace[:,4]<=2))
        self.assertTrue(np.all(trace[:,5]+trace[:,7]<=3))
        self.assertAlmostEqual(row[1]+row[5],row[0]+row[6]+row[7]+row[8])
    def test_leap_profile_energy_and_gas_allocation(self):
        d=e.data();self.assertEqual(d['valid'].sum(),sum(8784 if calendar.isleap(int(y)) else 8760 for y in d['years']))
        for mode in ['civil','standard']:
            np.testing.assert_allclose(d['load_'+mode].sum(axis=1),sum(d['audit']['monthly_kwh']),atol=1e-8,rtol=0)
        s=e.validate({'load_scenario':'electrified'});load,ev,end=e.load_profiles(s)
        self.assertAlmostEqual(end['heating_smc']+end['dhw_smc']+end['cooking_smc'],e.GAS_MONTHLY.sum())
        extra=end['heating_kwh']+end['dhw_kwh']+end['cooking_kwh']
        self.assertAlmostEqual(float(load.sum(axis=1).mean()),sum(e.data()['audit']['monthly_kwh'])+extra,places=7)
        self.assertTrue(np.all(load.sum(axis=1)>sum(e.data()['audit']['monthly_kwh'])))
    def test_no_pv_identical_scenario_regression(self):
        for scenario in ['baseline','electrified','ev']:
            r=e.evaluate({'south_panels':0,'battery_kwh':0,'load_scenario':scenario,'horizon_years':2})
            self.assertEqual(r['energy']['pv_kwh']['max'],0)
            self.assertAlmostEqual(r['finance']['pv_incremental']['npv']['max'],0)
            self.assertAlmostEqual(r['energy']['grid_import_kwh']['mean'],r['energy']['load_kwh']['mean'])
            json.dumps(r,allow_nan=False)
        r=e.evaluate({'south_panels':0,'battery_kwh':0,'load_growth':1.2,'horizon_years':2})
        self.assertAlmostEqual(r['finance']['whole_package']['npv']['max'],0,places=7)
    def test_validation_and_actual_metadata_values(self):
        for bad in [{'battery_kwh':float('nan')},{'horizon_years':1.5},{'export_price_future_years':1.5},{'export_price_future_years':0},{'deduction_rate':.3},{'junk':1}]:
            with self.assertRaises(ValueError):e.validate(bad)
        self.assertEqual(e.validate({'south_panels':23})['south_panels'],23)
        self.assertEqual(e.validate({'south_panels':22,'west_panels':11,'east_panels':6,'dormer_panels':4,'flat_panels':8,'secondary_south_panels':5})['secondary_south_panels'],5)
        r=e.evaluate({'horizon_years':1,'battery_kwh':0,'south_panels':10})
        self.assertEqual(next(x for x in r['assumptions'] if x['name']=='south_panels')['value'],10)
        self.assertEqual(len(r['annual_cashflows'][1]['pv_incremental_range']),6)
        self.assertEqual(r['metrics']['financed_discounted_payback_years'],r['finance']['financed_project']['discounted_payback'])
    def test_flat_capture_applied_once_and_tax_deduction_capacity(self):
        s=e.defaults();s.update(horizon_years=1,battery_kwh=0,deduction_rate=.5,deduction_tax_capacity=50,principal_residence_confirmed=True,actual_price_future_factor=.75)
        r=e.evaluate(s);cf=r['annual_cashflows'][1]
        self.assertEqual(cf['tax_deduction'],50)
        if r['finance']['export_price_source']=='flat_solar_capture_benchmark':
            self.assertAlmostEqual(cf['gross_export'],r['energy']['export_kwh']['mean']*.128*.74,places=6)
            fee=.7*4.8*1.22;self.assertAlmostEqual(cf['gse_fees'],fee)
            self.assertAlmostEqual(cf['export_tax'],(cf['gross_export']-fee)*(.33+.032+.008))
    def test_export_future_factor_ramps_linearly_then_holds_for_both_price_sources(self):
        h=20;annual=np.zeros((h,2,len(e.FIELDS)))
        annual[:,:,0]=1000;annual[:,:,5]=500;annual[:,:,1]=800
        annual[:,:,6]=150;annual[:,:,11]=150
        def run(mode,factor,**extra):
            settings=e.validate({'horizon_years':h,'south_panels':12,'battery_kwh':0,'quote_total':0,'deduction_rate':0,'om_annual':0,'insurance_annual':0,'cleaning_annual':0,'inverter_replace_year':0,'export_irpef':.3,'regional_additional_tax':0,'municipal_additional_tax':0,'export_fee_tax_deductible':True,'pmg_election':False,'export_market_price':.128,'export_capture_factor':.74,'actual_price_future_factor':factor,'export_price_future_years':10,'inflation':.02,'energy_real_escalation':.03,**extra})
            with patch('feasibility.engine.price_profile',return_value=(np.ones((2,1)),'actual_hourly_gme_profile' if mode=='actual' else 'flat_solar_capture_benchmark',None)),patch('feasibility.engine.data',return_value={'audit':{'annual_kwh':5000}}):
                return e._financial(settings,annual,4.8)[1]
        for mode in ['flat','actual']:
            capture=.74 if mode=='flat' else 1.
            reference=run(mode,1.0);reduced=run(mode,.75)
            self.assertAlmostEqual(reduced[1]['gross_export']/reference[1]['gross_export'],1.,places=12)
            self.assertAlmostEqual(reduced[6]['gross_export']/reference[6]['gross_export'],.875,places=12)
            self.assertAlmostEqual(reference[2]['gross_export'],150*.128*capture*1.02*1.03,places=12)
            rising=run(mode,1.25)
            self.assertAlmostEqual(rising[6]['gross_export']/reference[6]['gross_export'],1.125,places=12)
            for year in range(1,h+1):
                self.assertAlmostEqual(reference[year]['avoided_import'],reduced[year]['avoided_import'],places=12)
                self.assertAlmostEqual(reference[year]['with_system_electric_bill'],reduced[year]['with_system_electric_bill'],places=12)
            for year in [11,20]:
                base=reference[year]['gross_export'];low=reduced[year]['gross_export']
                self.assertAlmostEqual(low/base,.75,places=12)
                self.assertAlmostEqual(reference[year]['export_tax']-reduced[year]['export_tax'],(base-low)*.3,places=12)
                self.assertAlmostEqual(reference[year]['net_export']-reduced[year]['net_export'],(base-low)*.7,places=12)
            # Custom duration reaches the target at year 1 + duration and holds after it.
            fast=run(mode,.75,export_price_future_years=3)
            self.assertAlmostEqual(fast[2]['gross_export']/reference[2]['gross_export'],1-(1-.75)/3,places=12)
            self.assertAlmostEqual(fast[2]['gross_export'],150*.128*capture*(1-(1-.75)/3)*1.02*1.03,places=12)
            for year in [4,20]:self.assertAlmostEqual(fast[year]['gross_export']/reference[year]['gross_export'],.75,places=12)
        # The actual hourly profile is normalized; it must not receive capture a second time.
        actual=run('actual',.75)
        self.assertAlmostEqual(actual[1]['gross_export'],150*.128,places=12)
        self.assertNotAlmostEqual(actual[1]['gross_export'],150*.128*.74,places=6)
        reached=run('actual',.75)
        self.assertAlmostEqual(reached[11]['gross_export'],150*.128*.75*1.02**10*1.03**10,places=12)

    def test_pmg_floor_is_applied_after_export_factor(self):
        h=2;annual=np.zeros((h,2,len(e.FIELDS)))
        annual[:,:,0]=1000;annual[:,:,5]=500;annual[:,:,1]=800
        annual[:,:,6]=150;annual[:,:,11]=150
        def run(factor):
            settings=e.validate({'horizon_years':h,'south_panels':12,'battery_kwh':0,'quote_total':0,'deduction_rate':0,'om_annual':0,'insurance_annual':0,'cleaning_annual':0,'inverter_replace_year':0,'pmg_election':True,'pmg_price':.08,'export_market_price':.128,'export_capture_factor':.74,'actual_price_future_factor':factor,'export_price_future_years':1,'inflation':0,'energy_real_escalation':0})
            with patch('feasibility.engine.price_profile',return_value=(np.ones((2,1)),'flat_solar_capture_benchmark',None)),patch('feasibility.engine.data',return_value={'audit':{'annual_kwh':5000}}):
                return e._financial(settings,annual,4.8)[1]
        full=run(1);reduced=run(.75)
        # Year 1 starts at the reference and year 2 applies the future factor before PMG.
        self.assertAlmostEqual(full[1]['gross_export'],150*.128*.74)
        self.assertAlmostEqual(reduced[1]['gross_export'],150*.128*.74)
        self.assertAlmostEqual(full[2]['gross_export'],150*.128*.74)
        self.assertAlmostEqual(reduced[2]['gross_export'],150*.08)
    def test_fair_price_known_one_year_answer(self):
        s={'horizon_years':1,'battery_kwh':0,'deduction_rate':0,'real_discount_rate':0,'inflation':0,'om_annual':0,'quote_total':0}
        free=e.evaluate(s);expected=free['finance']['pv_incremental']['npv']['median']
        root=e.fair_price(s,'npv',0)
        self.assertAlmostEqual(root['price_eur'],expected,places=4)
        self.assertEqual(len(root['roots_by_weather']),e.weather_period().count)
        self.assertLessEqual(root['price_range_eur']['min'],root['price_eur'])
        self.assertGreaterEqual(root['price_range_eur']['max'],root['price_eur'])
        with self.assertRaises(ValueError):e.fair_price({'horizon_years':3},'simple_payback',5)

    def test_component_medians_asymmetric_weather_and_mean_reconciliation(self):
        h = 2; weather = 3
        annual = np.zeros((h, weather, len(e.FIELDS)))
        # Year 1 (yy=0): synthetic asymmetric weather distributions across 3 weather outcomes
        annual[0, :, 0] = [3000., 3000., 3000.]  # load_kwh
        annual[0, :, 5] = [2000., 1500., 500.]   # grid_import_kwh
        annual[0, :, 11] = [100., 500., 600.]    # export_price_weighted_kwh
        annual[0, :, 6] = [500., 100., 400.]     # export_kwh
        # Year 2 (yy=1): identical distribution to ensure multi-year consistency
        annual[1] = annual[0]

        settings = e.validate({
            'horizon_years': h,
            'south_panels': 12,
            'battery_kwh': 0,
            'quote_total': 1000,
            'deduction_rate': 0,
            'om_annual': 0,
            'insurance_annual': 0,
            'cleaning_annual': 0,
            'inverter_replace_year': 0,
            'battery_replace_year': 0,
            'loan_fraction': 0,
            'loan_apr': 0,
            'real_discount_rate': 0.05,
            'inflation': 0.0,
            'energy_real_escalation': 0.0,
            'retail_commodity': 0.20,
            'retail_regulated': 0.0,
            'electric_excise': 0.0,
            'electric_vat': 0.0,
            'supplier_fixed_month': 0.0,
            'network_fixed_month': 0.0,
            'power_fixed_kw_month': 0.0,
            'export_irpef': 0.0,
            'regional_additional_tax': 0.0,
            'municipal_additional_tax': 0.0,
            'export_market_price': 0.10,
            'export_capture_factor': 1.0,
            'actual_price_future_factor': 1.0,
            'export_price_future_years': 1,
            'pmg_election': False,
            'cer_enabled': True,
            'cer_eligible_export_share': 1.0,
            'cer_tariff': 0.10,
            'cer_user_allocation': 1.0,
            'load_scenario': 'baseline',
        })

        with patch('feasibility.engine.price_profile', return_value=(np.ones((weather, 1)), 'flat_solar_capture_benchmark', None)), \
             patch('feasibility.engine.data', return_value={'audit': {'annual_kwh': 5000}}):
            finance, cash = e._financial(settings, annual, 3.0)

        # Year 0 is initial outlay, not an operating year; should not contain component_medians
        self.assertNotIn('component_medians', cash[0])

        for y in range(1, h + 1):
            c = cash[y]
            self.assertIn('component_medians', c)
            meds = c['component_medians']
            self.assertEqual(set(meds.keys()), {'avoided_import', 'net_export_cer', 'avoided_package_bills'})
            for k, val in meds.items():
                self.assertIsInstance(val, float)

            # 1. Avoided import: [200, 300, 500] -> median 300, mean 333.33...
            self.assertEqual(meds['avoided_import'], 300.0)
            self.assertNotEqual(c['avoided_import'], meds['avoided_import'])
            self.assertAlmostEqual(c['avoided_import'], 1000.0 / 3)

            # 2. Net export + CER: net_export=[10, 50, 60], cer=[50, 10, 40]
            # Combined array: [60, 60, 100] -> median 60, mean 73.33...
            self.assertEqual(meds['net_export_cer'], 60.0)
            self.assertNotEqual(c['net_export'] + c['cer'], meds['net_export_cer'])
            self.assertAlmostEqual(c['net_export'] + c['cer'], 220.0 / 3)
            # Prove combined array median != sum of individual component medians (50 + 40 = 90)
            separate_medians_sum = float(np.median([10., 50., 60.])) + float(np.median([50., 10., 40.]))
            self.assertEqual(separate_medians_sum, 90.0)
            self.assertNotEqual(meds['net_export_cer'], separate_medians_sum)

            # 3. Avoided package bills: baseline electric (1000) - with_pv ([400, 300, 100])
            # Combined array: [600, 700, 900] -> median 700, mean 733.33...
            self.assertEqual(meds['avoided_package_bills'], 700.0)
            mean_avoided_package_bills = c['baseline_package_bill'] - c['with_system_electric_bill'] - c['remaining_gas_bill']
            self.assertNotEqual(mean_avoided_package_bills, meds['avoided_package_bills'])
            self.assertAlmostEqual(mean_avoided_package_bills, 2200.0 / 3)

            # 4. Mean reconciliation remains valid for diagnostic means
            comp_sum_pv = (c['avoided_import'] + c['net_export'] + c['cer'] + c['tax_deduction']
                           - c['maintenance'] - c['replacement'] + c['residual'])
            self.assertAlmostEqual(c['pv_incremental'], comp_sum_pv)
            comp_sum_package = (mean_avoided_package_bills + c['net_export'] + c['cer'] + c['tax_deduction']
                                - c['maintenance'] - c['replacement'] + c['residual'])
            self.assertAlmostEqual(c['whole_package'], comp_sum_package)

        # 5. JSON serialization validates clean serializability
        dumped = json.dumps(cash, allow_nan=False)
        self.assertIsInstance(dumped, str)
        reloaded = json.loads(dumped)
        self.assertEqual(reloaded[1]['component_medians']['avoided_import'], 300.0)
        self.assertEqual(reloaded[1]['component_medians']['net_export_cer'], 60.0)
        self.assertEqual(reloaded[1]['component_medians']['avoided_package_bills'], 700.0)

class DayHourTests(IsolatedDefaultsTestCase):
    def test_daily_slice_matches_full_chronological_dispatch_and_conserves_energy(self):
        s=e.validate({'south_panels':10,'battery_kwh':5,'charge_kw':2,'discharge_kw':2})
        result=e.dayhour(s,2024,'2024-07-02',14);dates=e._local_standard_dates()[years_index(2024)]
        trace=e._dayhour_trace(tuple((k,s[k]) for k in e.ENERGY_KEYS),years_index(2024))
        indices=[i for i,(date,_,_) in enumerate(dates) if date=='2024-07-02']
        self.assertEqual(result['available_hours'],list(range(24)))
        np.testing.assert_allclose([row['pv_kwh'] for row in result['hours']],trace[indices,0])
        np.testing.assert_allclose([row['soc_end_kwh'] for row in result['hours']],trace[indices,4])
        self.assertAlmostEqual(result['selected_hour']['hour'],14)
        self.assertLess(result['max_balance_error_kwh'],1e-9)
        self.assertAlmostEqual(result['totals']['pv_kwh'],trace[indices,0].sum())
        self.assertAlmostEqual(result['hours'][0]['soc_start_kwh'],trace[indices[0]-1,4])
        self.assertGreaterEqual(result['hours'][0]['soc_start_kwh'],0)
        self.assertLessEqual(result['soc_end_kwh'],result['battery_capacity_kwh'])

    def test_actual_timeline_boundary_and_leap_day(self):
        jan=e.dayhour({'south_panels':0,'battery_kwh':0},2023,'2023-01-01')
        self.assertEqual(jan['available_hours'],list(range(1,24)))
        self.assertEqual(jan['unavailable_hours'],[0])
        self.assertEqual(jan['final_local_slot_outside_picker'],'2024-01-01')
        self.assertIsNone(jan['selected_hour'])
        self.assertEqual(e._local_standard_dates()[years_index(2023)][-1][:2],('2024-01-01',0))
        leap=e.dayhour({'south_panels':0,'battery_kwh':0},2024,'2024-02-29')
        self.assertEqual(leap['available_hours'],list(range(24)))
        with self.assertRaisesRegex(ValueError,'date year'):
            e.dayhour({'south_panels':0,'battery_kwh':0},2024,'2023-02-28')
        with self.assertRaises(ValueError):e.dayhour({'south_panels':0,'battery_kwh':0},2023,'2023-02-29')
        self.assertIsNone(e.dayhour({'south_panels':0,'battery_kwh':0},2023,'2023-01-01',0)['selected_hour'])

    def test_quote_normalized_settings_are_used_and_zero_system_is_valid(self):
        normalized=e.quote_evaluate({'south_panels':10,'battery_kwh':4},{'battery_nameplate_kwh':10,'battery_dod':.7,'continuous_charge_kw':1.25,'continuous_discharge_kw':1.5})['result']['settings']
        daily=e.dayhour(normalized,2023,'2023-06-21',12)
        self.assertEqual(daily['settings']['battery_kwh'],7)
        self.assertEqual(daily['settings']['charge_kw'],1.25)
        self.assertEqual(daily['settings']['discharge_kw'],1.5)
        self.assertAlmostEqual(daily['settings']['battery_modules']*daily['settings']['battery_module_kwh'],7)
        empty=e.dayhour({'south_panels':0,'battery_kwh':0},2023,'2023-06-21',12)
        self.assertEqual(empty['totals']['pv_kwh'],0)
        self.assertEqual(empty['totals']['charge_input_kwh'],0)
        self.assertEqual(empty['totals']['discharge_delivered_kwh'],0)

    def test_2027_forecast_is_complete_balanced_and_uses_coherent_weather_runs(self):
        s=e.validate({'south_panels':10,'battery_kwh':5,'charge_kw':2,'discharge_kw':2,'load_scenario':'electrified'})
        r=e.dayhour_forecast(s,'2027-01-01',0,'electrified','median')
        self.assertEqual(r['available_hours'],list(range(24)))
        self.assertEqual(r['forecast_year'],2027);self.assertEqual(r['scenario'],'electrified')
        self.assertEqual(len(r['annual_scenarios']),3)
        self.assertLess(r['max_balance_error_kwh'],1e-9)
        runs=e._forecast_2027_runs(tuple((k,s[k]) for k in e.ENERGY_KEYS))
        self.assertEqual(len(runs['traces']),e.weather_period().count)
        self.assertTrue(all(len(trace)==8760 for trace in runs['traces']))
        self.assertEqual([x['label'] for x in r['annual_scenarios']],['low','median','high'])
        picked=[x['pv_kwh'] for x in r['annual_scenarios']]
        self.assertLessEqual(picked[0],picked[1]);self.assertLessEqual(picked[1],picked[2])
        mapped=e._forecast_mapped_series(s)
        for analog in r['annual_scenarios']:
            wi=years_index(analog['weather_year'])
            expected=float(mapped['mapped_load'][wi].sum())
            self.assertAlmostEqual(analog['load_kwh'],expected,places=6)
        baseline=e.dayhour_forecast(s,'2027-01-01',scenario='baseline')
        ev_case=e.dayhour_forecast(s,'2027-01-01',scenario='ev')
        self.assertGreater(r['annual_scenarios'][1]['load_kwh'],baseline['annual_scenarios'][1]['load_kwh'])
        self.assertGreater(ev_case['annual_scenarios'][1]['load_kwh'],r['annual_scenarios'][1]['load_kwh'])
        day=e.dayhour_forecast(s,'2027-06-21',12,'electrified','median')
        self.assertAlmostEqual(day['hours'][12]['soc_start_kwh'],day['hours'][11]['soc_end_kwh'])
        with self.assertRaisesRegex(ValueError,'in 2027'):e.dayhour_forecast(s,'2028-01-01')
        with self.assertRaisesRegex(ValueError,'0 to 23'):e.dayhour_forecast(s,'2027-01-01',24)

    def test_2027_annual_pv_is_post_inverter_clipping(self):
        s=e.validate({'south_panels':22,'secondary_south_panels':5,'inverter_kw':3,'battery_kwh':0,'load_scenario':'baseline'})
        runs=e._forecast_2027_runs(tuple((k,s[k]) for k in e.ENERGY_KEYS))
        self.assertTrue(any(x['pv_input_kwh']>x['pv_kwh']+1 for x in runs['annual']))
        for i,annual in enumerate(runs['annual']):
            self.assertAlmostEqual(float(runs['traces'][i][:,0].sum()),annual['pv_kwh'],places=6)

    def test_monthly_historical_aggregation_and_energy_conservation(self):
        s=e.validate({'south_panels':10,'battery_kwh':5,'charge_kw':2,'discharge_kw':2})
        wi=years_index(2024);dates=e._local_standard_dates()[wi]
        trace=e._dayhour_trace(tuple((k,s[k]) for k in e.ENERGY_KEYS),wi)
        res=e.dayhour_monthly(s,2024,7,14)
        indices=[i for i,(stamp,_,mm) in enumerate(dates) if int(stamp.split('-')[0])==2024 and mm==7]
        self.assertEqual(res['days_in_month'],31)
        self.assertEqual(res['total_samples'],31*24)
        self.assertEqual(res['available_hours'],list(range(24)))
        self.assertEqual(res['unavailable_hours'],[])
        self.assertEqual(res['selected_hour']['hour'],14)
        eff=math.sqrt(s['roundtrip_efficiency'])
        
        # Independently calculate means for each hour bin 0..23
        for h in range(24):
            h_indices=[i for i in indices if dates[i][1]==h]
            self.assertEqual(len(h_indices),31)
            row=res['hours'][h]
            self.assertEqual(row['hour'],h)
            self.assertEqual(row['sample_count'],31)
            self.assertAlmostEqual(row['pv_kwh'],float(trace[h_indices,0].mean()))
            self.assertAlmostEqual(row['load_kwh'],float(trace[h_indices,1].mean()))
            self.assertAlmostEqual(row['grid_import_kwh'],float(trace[h_indices,2].mean()))
            self.assertAlmostEqual(row['grid_export_kwh'],float(trace[h_indices,3].mean()))
            self.assertAlmostEqual(row['soc_end_kwh'],float(trace[h_indices,4].mean()))
            self.assertAlmostEqual(row['direct_kwh'],float(trace[h_indices,5].mean()))
            self.assertAlmostEqual(row['charge_input_kwh'],float(trace[h_indices,6].mean()))
            self.assertAlmostEqual(row['discharge_delivered_kwh'],float(trace[h_indices,7].mean()))
            prev=np.array([float(trace[i-1,4]) if i>0 else 0. for i in h_indices])
            self.assertAlmostEqual(row['soc_start_kwh'],float(prev.mean()))
            loss=trace[h_indices,6]*(1-eff)+trace[h_indices,7]*(1/eff-1)
            self.assertAlmostEqual(row['battery_loss_kwh'],float(loss.mean()))
            self.assertLess(abs(row['balance_error_kwh']),1e-9)
        
        self.assertLess(res['max_balance_error_kwh'],1e-9)
        # Totals match exact sum of original intervals
        self.assertAlmostEqual(res['totals']['pv_kwh'],float(trace[indices,0].sum()))
        self.assertAlmostEqual(res['totals']['load_kwh'],float(trace[indices,1].sum()))
        self.assertAlmostEqual(res['totals']['grid_import_kwh'],float(trace[indices,2].sum()))
        self.assertAlmostEqual(res['totals']['grid_export_kwh'],float(trace[indices,3].sum()))
        # Daily averages match month total / days_in_month and sum of hourly arithmetic means for full months
        for k in ['pv_kwh','load_kwh','direct_kwh','charge_input_kwh','discharge_delivered_kwh','grid_import_kwh','grid_export_kwh','battery_loss_kwh']:
            self.assertAlmostEqual(res['daily_averages'][k],res['totals'][k]/31)
            self.assertAlmostEqual(res['daily_averages'][k],sum(r[k] for r in res['hours']))
            self.assertAlmostEqual(res['hourly_averages'][k],res['totals'][k]/(31*24))
        self.assertAlmostEqual(res['mean_soc_kwh'],float(trace[indices,4].mean()))
        self.assertAlmostEqual(res['soc_start_kwh'],res['hours'][0]['soc_start_kwh'])
        self.assertAlmostEqual(res['soc_end_kwh'],res['hours'][-1]['soc_end_kwh'])

    def test_monthly_historical_leap_and_january_boundary_coverage(self):
        s={'south_panels':10,'battery_kwh':5,'charge_kw':2,'discharge_kw':2}
        # Leap year 2024 February has 29 days
        feb_leap=e.dayhour_monthly(s,2024,2)
        self.assertEqual(feb_leap['days_in_month'],29)
        self.assertEqual(feb_leap['total_samples'],29*24)
        self.assertTrue(all(r['sample_count']==29 for r in feb_leap['hours']))
        # Non-leap year 2023 February has 28 days
        feb_nonleap=e.dayhour_monthly(s,2023,2)
        self.assertEqual(feb_nonleap['days_in_month'],28)
        self.assertEqual(feb_nonleap['total_samples'],28*24)
        self.assertTrue(all(r['sample_count']==28 for r in feb_nonleap['hours']))
        # January in 2023: 743 observations, hour 0 has 30 samples, hours 1..23 have 31 samples
        jan=e.dayhour_monthly(s,2023,1)
        self.assertEqual(jan['days_in_month'],31)
        self.assertEqual(jan['total_samples'],743)
        self.assertEqual(jan['hours'][0]['sample_count'],30)
        self.assertTrue(all(r['sample_count']==31 for r in jan['hours'][1:]))
        self.assertEqual(jan['available_hours'],list(range(24)))
        self.assertEqual(jan['unavailable_hours'],[])
        self.assertLess(jan['max_balance_error_kwh'],1e-9)
        # Verify the slot 2024-01-01 00:00 is NOT included in 2023 January
        timeline=e._local_standard_dates()[years_index(2023)]
        self.assertEqual(timeline[-1][:2],('2024-01-01',0))
        # January totals use the historical temperature-dependent heating profile.
        jan_settings=e.validate({'south_panels':10,'battery_kwh':5,'load_scenario':'electrified','scop':2})
        jan_10_5=e.dayhour_monthly(jan_settings,2023,1)
        jan_mask=np.array([slot[0].startswith('2023-01-') for slot in timeline])
        source_load=e.load_profiles(jan_settings)[0][years_index(2023)]
        self.assertAlmostEqual(jan_10_5['totals']['load_kwh'],float(source_load[:len(timeline)][jan_mask].sum()),places=9)
        self.assertAlmostEqual(jan_10_5['daily_averages']['load_kwh'],jan_10_5['totals']['load_kwh']/31,places=12)
        sum_bin_means=sum(r['load_kwh'] for r in jan_10_5['hours'])
        self.assertAlmostEqual(sum_bin_means,jan_10_5['daily_averages']['load_kwh']+jan_10_5['hours'][0]['load_kwh']/31,places=12)
        self.assertNotAlmostEqual(jan_10_5['daily_averages']['load_kwh'],sum_bin_means,places=4)
        self.assertAlmostEqual(jan_10_5['hourly_averages']['load_kwh'],jan_10_5['totals']['load_kwh']/743,places=12)
        # Continuous heating retains the same partial-calendar accounting identity.
        continuous_settings=e.validate({**jan_settings,'heating_schedule_mode':'continuous'})
        jan_10_5_cont=e.dayhour_monthly(continuous_settings,2023,1)
        continuous_load=e.load_profiles(continuous_settings)[0][years_index(2023)]
        self.assertAlmostEqual(jan_10_5_cont['totals']['load_kwh'],float(continuous_load[:len(timeline)][jan_mask].sum()),places=9)
        self.assertAlmostEqual(sum(r['load_kwh'] for r in jan_10_5_cont['hours']),jan_10_5_cont['daily_averages']['load_kwh']+jan_10_5_cont['hours'][0]['load_kwh']/31,places=12)
        weighted_soc=sum(r['soc_end_kwh']*r['sample_count'] for r in jan_10_5['hours'])/743
        self.assertAlmostEqual(jan_10_5['mean_soc_kwh'],weighted_soc,places=12)
        unweighted_soc=float(np.mean([r['soc_end_kwh'] for r in jan_10_5['hours']]))
        self.assertNotAlmostEqual(jan_10_5['mean_soc_kwh'],unweighted_soc,places=6)
        self.assertTrue(jan_10_5['coverage']['partial'])
        self.assertEqual(jan_10_5['coverage']['missing_samples'],1)
        self.assertEqual(jan_10_5['coverage']['valid_samples'],743)
        # SOC continuity across month boundaries
        trace=e._dayhour_trace(tuple((k,e.validate(s)[k]) for k in e.ENERGY_KEYS),years_index(2024))
        jan_indices=[i for i,(stamp,_,mm) in enumerate(e._local_standard_dates()[years_index(2024)]) if int(stamp.split('-')[0])==2024 and mm==1]
        feb_indices=[i for i,(stamp,_,mm) in enumerate(e._local_standard_dates()[years_index(2024)]) if int(stamp.split('-')[0])==2024 and mm==2]
        self.assertEqual(feb_indices[0],jan_indices[-1]+1)
        # First interval of Feb starts with exactly the end SOC of Jan's last interval
        feb_res=e.dayhour_monthly(s,2024,2)
        self.assertAlmostEqual(feb_res['first_interval_soc_start_kwh'],float(trace[jan_indices[-1],4]))

    def test_monthly_zero_battery_and_quote_settings(self):
        empty=e.dayhour_monthly({'south_panels':10,'battery_kwh':0},2023,6,12)
        self.assertEqual(empty['totals']['charge_input_kwh'],0)
        self.assertEqual(empty['totals']['discharge_delivered_kwh'],0)
        self.assertEqual(empty['totals']['battery_loss_kwh'],0)
        self.assertEqual(empty['soc_start_kwh'],0)
        self.assertEqual(empty['soc_end_kwh'],0)
        self.assertLess(empty['max_balance_error_kwh'],1e-9)

    def test_monthly_forecast_aggregation_and_uncertainty(self):
        s=e.validate({'south_panels':10,'battery_kwh':5,'charge_kw':2,'discharge_kw':2,'load_scenario':'electrified'})
        res=e.dayhour_forecast_monthly(s,2,12,'electrified','median')
        self.assertEqual(res['days_in_month'],28)
        self.assertEqual(res['total_samples'],28*24)
        self.assertEqual(res['forecast_year'],2027)
        self.assertEqual(res['scenario'],'electrified')
        self.assertEqual(res['representative'],'median')
        self.assertEqual(len(res['uncertainty_by_hour']),24)
        self.assertLess(res['max_balance_error_kwh'],1e-9)
        # Verify uncertainty calculation:
        # Envelopes must equal percentiles of per-run monthly means across the 19 runs
        runs=e._forecast_2027_runs(tuple((k,s[k]) for k in e.ENERGY_KEYS))
        start_hour=31*24;end_hour=start_hour+28*24
        per_run_means=np.array([tr[start_hour:end_hour].reshape(28,24,8).mean(axis=0) for tr in runs['traces']])
        for h in range(24):
            expected_p10=float(np.percentile(per_run_means[:,h,0],10))
            expected_p50=float(np.percentile(per_run_means[:,h,0],50))
            expected_p90=float(np.percentile(per_run_means[:,h,0],90))
            band=res['uncertainty_by_hour'][h]['pv_kwh']
            self.assertAlmostEqual(band['p10'],expected_p10)
            self.assertAlmostEqual(band['p50'],expected_p50)
            self.assertAlmostEqual(band['p90'],expected_p90)
            self.assertLessEqual(band['p10'],band['p50'])
            self.assertLessEqual(band['p50'],band['p90'])

    def test_monthly_validation_and_backward_compatibility(self):
        s={'south_panels':10,'battery_kwh':5}
        # Strict rejection of boolean months
        with self.assertRaisesRegex(ValueError,'month'):e.dayhour_monthly(s,2024,True)
        with self.assertRaisesRegex(ValueError,'month'):e.dayhour_monthly(s,2024,False)
        with self.assertRaisesRegex(ValueError,'month'):e.dayhour_forecast_monthly(s,True)
        # Strict rejection of out-of-range months
        with self.assertRaisesRegex(ValueError,'month'):e.dayhour_monthly(s,2024,0)
        with self.assertRaisesRegex(ValueError,'month'):e.dayhour_monthly(s,2024,13)
        with self.assertRaisesRegex(ValueError,'month'):e.dayhour_monthly(s,2024,'7')
        with self.assertRaisesRegex(ValueError,'month'):e.dayhour_forecast_monthly(s,0)
        with self.assertRaisesRegex(ValueError,'month'):e.dayhour_forecast_monthly(s,13)
        # Strict rejection of invalid hours
        with self.assertRaisesRegex(ValueError,'hour'):e.dayhour_monthly(s,2024,5,24)
        with self.assertRaisesRegex(ValueError,'hour'):e.dayhour_monthly(s,2024,5,True)
        with self.assertRaisesRegex(ValueError,'hour'):e.dayhour_forecast_monthly(s,5,24)
        with self.assertRaisesRegex(ValueError,'hour'):e.dayhour_forecast_monthly(s,5,True)
        # Daily backward compatibility
        daily_hist=e.dayhour(s,2024,'2024-07-02')
        self.assertEqual(daily_hist['period'],'day')
        daily_fc=e.dayhour_forecast(s,'2027-01-01')
        self.assertEqual(daily_fc['period'],'day')
        # Delegation through optional period/month kwargs
        del_hist=e.dayhour(s,2024,None,month=7,period='month')
        self.assertEqual(del_hist['period'],'month')
        self.assertEqual(del_hist['month'],7)
        del_fc=e.dayhour_forecast(s,None,month=7,period='month')
        self.assertEqual(del_fc['period'],'month')
        self.assertEqual(del_fc['month'],7)
        with self.assertRaisesRegex(ValueError,'period'):e.dayhour(s,2024,'2024-07-02',period='annual')
        with self.assertRaisesRegex(ValueError,'period'):e.dayhour_forecast(s,'2027-01-01',period='annual')
        # Rejection of invalid period when month is present and date is None
        with self.assertRaisesRegex(ValueError,'period'):e.dayhour(s,2024,None,month=7,period='annual')
        with self.assertRaisesRegex(ValueError,'period'):e.dayhour_forecast(s,None,month=7,period='annual')
        # Valid aliases and defaults
        del_daily=e.dayhour(s,2024,None,month=7,period='daily')
        self.assertEqual(del_daily['period'],'month')
        del_default=e.dayhour(s,2024,None,month=7)
        self.assertEqual(del_default['period'],'month')
        del_fc_default=e.dayhour_forecast(s,None,month=7)
        self.assertEqual(del_fc_default['period'],'month')
        del_monthly=e.dayhour(s,2024,None,month=7,period='monthly')
        self.assertEqual(del_monthly['period'],'month')

    def test_monthly_max_balance_error_no_cancellation(self):
        s={'south_panels':10,'battery_kwh':5}
        wi=years_index(2024)
        dates=e._local_standard_dates()[wi]
        jan_indices=[i for i,(stamp,_,mm) in enumerate(dates) if int(stamp.split('-')[0])==2024 and mm==1]
        real_trace=e._dayhour_trace(tuple((k,e.validate(s)[k]) for k in e.ENERGY_KEYS),wi)
        h12_indices=[i for i in jan_indices if dates[i][1]==12]
        self.assertGreaterEqual(len(h12_indices),2)
        idx1=h12_indices[0];idx2=h12_indices[1]
        synth_trace=real_trace.copy()
        synth_trace[idx1,0]+=0.25
        synth_trace[idx2,0]-=0.25
        with patch('feasibility.engine._dayhour_trace',return_value=synth_trace):
            res=e.dayhour_monthly(s,2024,1)
            self.assertAlmostEqual(res['hours'][12]['balance_error_kwh'],0.0,places=9)
            self.assertLess(res['max_mean_balance_error_kwh'],1e-9)
            self.assertAlmostEqual(res['max_balance_error_kwh'],0.25,places=6)
        runs=e._forecast_2027_runs(tuple((k,e.validate(s)[k]) for k in e.ENERGY_KEYS))
        synth_runs={**runs,'traces':[tr.copy() for tr in runs['traces']]}
        ri=runs['picks']['median']
        synth_runs['traces'][ri][12,0]+=0.35
        synth_runs['traces'][ri][36,0]-=0.35
        with patch('feasibility.engine._forecast_2027_runs',return_value=synth_runs):
            fc_res=e.dayhour_forecast_monthly(s,1,representative='median')
            self.assertAlmostEqual(fc_res['hours'][12]['balance_error_kwh'],0.0,places=9)
            self.assertLess(fc_res['max_mean_balance_error_kwh'],1e-9)
            self.assertAlmostEqual(fc_res['max_balance_error_kwh'],0.35,places=6)

    def test_monthly_soc_median_quantiles_and_full_days(self):
        s = e.validate({
            'installation_year': 2026,
            'south_panels': 17,
            'panel_watts': 400.0,
            'inverter_kw': 5.0,
            'battery_kwh': 7.0,
            'charge_kw': 10.0,
            'discharge_kw': 10.0,
            'roundtrip_efficiency': 0.9,
            'load_scenario': 'electrified',
            'scop': 3.5,
            'dhw_heating_type': 'heat_pump',
            'dhw_scop': 3.0,
            'heating_on_hour': 8,
            'heating_off_hour': 11,
            'heating_evening_on_hour': 16,
            'heating_evening_off_hour': 20,
        })
        res = e.dayhour_forecast_monthly(s, month=3, hour=12, scenario='electrified', representative='median')
        h12 = res['selected_hour']
        self.assertIsNotNone(h12)
        key = tuple((k, s[k]) for k in e.ENERGY_KEYS)
        version = e.price_fingerprint(s)
        runs = e._forecast_2027_runs(key, version)
        ri = runs['picks']['median']
        trace = runs['traces'][ri]
        start_hour = (31 + 28) * 24
        raw_socs = [float(trace[start_hour + d * 24 + 12, 4]) for d in range(31)]

        expected_mean = float(np.mean(raw_socs))
        expected_median = float(np.median(raw_socs))
        self.assertAlmostEqual(h12['soc_end_kwh'], expected_mean, places=12)
        self.assertAlmostEqual(h12['soc_end_median_kwh'], expected_median, places=12)
        self.assertNotAlmostEqual(h12['soc_end_kwh'], h12['soc_end_median_kwh'], places=2)
        self.assertAlmostEqual(h12['soc_end_median_kwh'], 7.0, places=8)

        expected_p10 = float(np.percentile(raw_socs, 10))
        expected_p90 = float(np.percentile(raw_socs, 90))
        self.assertAlmostEqual(h12['soc_end_p10_kwh'], expected_p10, places=12)
        self.assertAlmostEqual(h12['soc_end_p90_kwh'], expected_p90, places=12)

        expected_full = sum(1 for v in raw_socs if v >= 7.0 - 1e-8)
        self.assertEqual(h12['soc_full_days'], expected_full)
        self.assertEqual(h12['soc_sample_days'], 31)
        self.assertAlmostEqual(h12['soc_full_fraction'], expected_full / 31, places=12)
        self.assertEqual(h12['soc_daily_stats']['soc_full_days'], expected_full)

        res_low = e.dayhour_forecast_monthly(s, month=3, hour=12, scenario='electrified', representative='low')
        ri_low = runs['picks']['low']
        trace_low = runs['traces'][ri_low]
        raw_socs_low = [float(trace_low[start_hour + d * 24 + 12, 4]) for d in range(31)]
        self.assertAlmostEqual(res_low['selected_hour']['soc_end_median_kwh'], float(np.median(raw_socs_low)), places=12)
        self.assertAlmostEqual(res_low['selected_hour']['soc_end_p10_kwh'], float(np.percentile(raw_socs_low, 10)), places=12)
        self.assertNotEqual(res_low['selected_hour']['soc_end_p10_kwh'], h12['soc_end_p10_kwh'])
        self.assertNotEqual([h['soc_end_median_kwh'] for h in res_low['hours']], [h['soc_end_median_kwh'] for h in res['hours']])

        weather_p50 = res['uncertainty_by_hour'][12]['soc_end_kwh']['p50']
        self.assertNotAlmostEqual(h12['soc_end_median_kwh'], weather_p50, places=2)

    def test_monthly_soc_zero_capacity_edge_case(self):
        s = {'south_panels': 10, 'battery_kwh': 0}
        hist_empty = e.dayhour_monthly(s, 2023, 6, hour=12)
        h_hist = hist_empty['selected_hour']
        self.assertEqual(h_hist['soc_end_median_kwh'], 0.0)
        self.assertEqual(h_hist['soc_end_p10_kwh'], 0.0)
        self.assertEqual(h_hist['soc_end_p90_kwh'], 0.0)
        self.assertEqual(h_hist['soc_full_days'], 0)
        self.assertEqual(h_hist['soc_full_fraction'], 0.0)
        self.assertEqual(h_hist['soc_sample_days'], 30)

        fc_empty = e.dayhour_forecast_monthly(s, month=6, hour=12)
        h_fc = fc_empty['selected_hour']
        self.assertEqual(h_fc['soc_end_median_kwh'], 0.0)
        self.assertEqual(h_fc['soc_end_p10_kwh'], 0.0)
        self.assertEqual(h_fc['soc_end_p90_kwh'], 0.0)
        self.assertEqual(h_fc['soc_full_days'], 0)
        self.assertEqual(h_fc['soc_full_fraction'], 0.0)
        self.assertEqual(h_fc['soc_sample_days'], 30)

    def test_monthly_soc_historical_january_partial_coverage(self):
        s = {'south_panels': 10, 'battery_kwh': 5}
        jan = e.dayhour_monthly(s, 2023, 1)
        h0 = jan['hours'][0]
        h12 = jan['hours'][12]
        self.assertEqual(h0['sample_count'], 30)
        self.assertEqual(h0['soc_sample_days'], 30)
        self.assertLessEqual(h0['soc_full_days'], 30)
        self.assertEqual(h12['sample_count'], 31)
        self.assertEqual(h12['soc_sample_days'], 31)
        self.assertLessEqual(h12['soc_full_days'], 31)

    def test_monthly_soc_leap_year_february(self):
        s = {'south_panels': 10, 'battery_kwh': 5, 'installation_year': 2027}
        feb_leap = e.dayhour_forecast_monthly(s, month=2, hour=12)
        self.assertEqual(feb_leap['days_in_month'], 29)
        self.assertEqual(feb_leap['selected_hour']['soc_sample_days'], 29)
        for row in feb_leap['hours']:
            self.assertEqual(row['soc_sample_days'], 29)
            self.assertLessEqual(row['soc_full_days'], 29)

        hist_leap = e.dayhour_monthly(s, 2024, 2, hour=12)
        self.assertEqual(hist_leap['days_in_month'], 29)
        self.assertEqual(hist_leap['selected_hour']['soc_sample_days'], 29)


class SummerAcMultiplierTests(IsolatedDefaultsTestCase):
    def test_default_backward_compatibility(self):
        s_default = e.defaults()
        self.assertEqual(s_default['summer_ac_multiplier'], 1.0)
        s_explicit = e.validate({'summer_ac_multiplier': 1.0})
        s_omitted = e.validate({})
        self.assertEqual(s_explicit['summer_ac_multiplier'], 1.0)
        self.assertEqual(s_omitted['summer_ac_multiplier'], 1.0)

        load_exp, ev_exp, end_exp = e.load_profiles(s_explicit)
        load_omit, ev_omit, end_omit = e.load_profiles(s_omitted)
        np.testing.assert_array_equal(load_exp, load_omit)
        np.testing.assert_array_equal(ev_exp, ev_omit)
        self.assertEqual(end_exp['summer_ac_extra_kwh'], 0.0)
        self.assertEqual(end_exp['summer_ac_multiplier'], 1.0)
        self.assertAlmostEqual(end_exp['summer_ac_baseline_kwh'], 5.0*122, places=4)

        r_exp = e.evaluate({'summer_ac_multiplier': 1.0, 'south_panels': 10, 'battery_kwh': 5})
        r_omit = e.evaluate({'south_panels': 10, 'battery_kwh': 5})
        self.assertAlmostEqual(r_exp['metrics']['pv_kwh']['median'], r_omit['metrics']['pv_kwh']['median'])
        self.assertAlmostEqual(r_exp['metrics']['grid_import_kwh']['median'], r_omit['metrics']['grid_import_kwh']['median'])
        self.assertAlmostEqual(r_exp['metrics']['npv_eur']['median'], r_omit['metrics']['npv_eur']['median'])

    def test_baseline_cooling_estimation_from_measured_data(self):
        est = e.estimated_baseline_cooling()
        m = e.data()['audit']['monthly_kwh']
        expected_aug_daily = m[7] / 31.0
        expected_oct_daily = m[9] / 31.0
        expected_daily = max(0.0, expected_aug_daily - expected_oct_daily)
        expected_summer = expected_daily * 122

        self.assertAlmostEqual(est['august_daily_kwh'], expected_aug_daily, places=7)
        self.assertAlmostEqual(est['october_daily_kwh'], expected_oct_daily, places=7)
        self.assertAlmostEqual(est['daily_cooling_kwh'], expected_daily, places=7)
        self.assertEqual(est['summer_days'], 122)
        self.assertAlmostEqual(est['summer_baseline_kwh'], expected_summer, places=7)
        self.assertAlmostEqual(est['summer_baseline_kwh'], 610.0, places=7)

    def test_zero_estimated_ac_yields_no_increment(self):
        fake_audit = copy.deepcopy(e.data()['audit'])
        fake_audit['monthly_kwh'][7] = fake_audit['monthly_kwh'][9] / 2
        fake_data = dict(e.data())
        fake_data['audit'] = fake_audit
        est = e.estimated_baseline_cooling(fake_data)
        self.assertEqual(est['daily_cooling_kwh'], 0.0)
        self.assertEqual(est['summer_baseline_kwh'], 0.0)

        with patch('feasibility.engine.data', return_value=fake_data):
            e._load_cache.cache_clear()
            try:
                s = e.validate({'summer_ac_multiplier': 1.5})
                load, _, end = e.load_profiles(s)
                self.assertEqual(end['summer_ac_extra_kwh'], 0.0)
                self.assertEqual(end['summer_ac_baseline_kwh'], 0.0)
            finally:
                e._load_cache.cache_clear()

    def test_requested_annual_increase_across_scenarios_and_weather_years(self):
        est_base = e.estimated_baseline_cooling()['summer_baseline_kwh']
        mult = 1.3
        expected_extra = (mult - 1.0) * est_base

        for scenario in ['baseline', 'electrified', 'ev']:
            s_base = e.validate({'load_scenario': scenario, 'summer_ac_multiplier': 1.0})
            s_mult = e.validate({'load_scenario': scenario, 'summer_ac_multiplier': mult})
            l_base, _, end_base = e.load_profiles(s_base)
            l_mult, _, end_mult = e.load_profiles(s_mult)

            self.assertAlmostEqual(end_mult['summer_ac_extra_kwh'], expected_extra, places=8)
            self.assertAlmostEqual(end_mult['summer_ac_kwh'], est_base + expected_extra, places=8)
            self.assertEqual(end_mult['summer_ac_multiplier'], mult)

            for wi in range(len(e.data()['years'])):
                diff = float(l_mult[wi].sum() - l_base[wi].sum())
                self.assertAlmostEqual(diff, expected_extra, places=8,
                    msg=f'Year {e.data()["years"][wi]} did not preserve requested annual extra in scenario {scenario}')

    def test_summer_only_placement_and_invalid_zero(self):
        d = e.data()
        m = d['month']
        v = d['valid']
        s_base = e.validate({'summer_ac_multiplier': 1.0})
        s_mult = e.validate({'summer_ac_multiplier': 1.3})
        l_base, _, _ = e.load_profiles(s_base)
        l_mult, _, _ = e.load_profiles(s_mult)
        diff = l_mult - l_base

        non_summer = (m < 6) | (m > 9)
        self.assertEqual(np.max(np.abs(diff[non_summer])), 0.0)
        self.assertEqual(np.max(np.abs(diff[~v])), 0.0)

        summer_valid = v & (m >= 6) & (m <= 9)
        self.assertTrue(np.all(diff[summer_valid] >= 0.0))

        cool_deg = np.maximum(d['temperature'] - e.COOLING_BALANCE_TEMP, 0.0)
        zero_cd = summer_valid & (cool_deg == 0.0)
        self.assertEqual(np.max(np.abs(diff[zero_cd])), 0.0)
        pos_cd = summer_valid & (cool_deg > 0.0)
        self.assertTrue(np.all(diff[pos_cd] > 0.0))

    def test_zero_cooling_degrees_uniform_summer_fallback(self):
        fake_data = dict(e.data())
        fake_temp = np.full_like(e.data()['temperature'], 20.0)
        fake_data['temperature'] = fake_temp

        with patch('feasibility.engine.data', return_value=fake_data):
            e._load_cache.cache_clear()
            try:
                est_base = e.estimated_baseline_cooling(fake_data)['summer_baseline_kwh']
                s_base = e.validate({'summer_ac_multiplier': 1.0})
                s_mult = e.validate({'summer_ac_multiplier': 1.3})
                l_base, _, _ = e.load_profiles(s_base)
                l_mult, _, end = e.load_profiles(s_mult)
                diff = l_mult - l_base
                expected_extra = (1.3 - 1.0) * est_base

                m = fake_data['month']
                v = fake_data['valid']
                summer_valid = v & (m >= 6) & (m <= 9)

                self.assertEqual(np.max(np.abs(diff[~summer_valid])), 0.0)
                summer_hours_count = int(summer_valid[0].sum())
                expected_hourly = expected_extra / summer_hours_count
                np.testing.assert_allclose(diff[0, summer_valid[0]], expected_hourly, rtol=1e-10)
                self.assertAlmostEqual(float(diff[0].sum()), expected_extra, places=8)
            finally:
                e._load_cache.cache_clear()

    def test_validation_bounds_and_invalid_inputs(self):
        for bad in [
            {'summer_ac_multiplier': 0.99},
            {'summer_ac_multiplier': 0.0},
            {'summer_ac_multiplier': -1.0},
            {'summer_ac_multiplier': 3.01},
            {'summer_ac_multiplier': 5.0},
            {'summer_ac_multiplier': float('nan')},
            {'summer_ac_multiplier': float('inf')},
            {'summer_ac_multiplier': 'not_a_number'},
            {'summer_ac_multiplier': True},
        ]:
            with self.assertRaises(ValueError):
                e.validate(bad)

        for good in [1.0, 1.05, 1.3, 2.0, 3.0]:
            self.assertEqual(e.validate({'summer_ac_multiplier': good})['summer_ac_multiplier'], float(good))

    def test_load_growth_interaction(self):
        s_g1_m1 = e.validate({'load_growth': 1.0, 'summer_ac_multiplier': 1.0})
        s_g1_m13 = e.validate({'load_growth': 1.0, 'summer_ac_multiplier': 1.3})
        s_g12_m1 = e.validate({'load_growth': 1.2, 'summer_ac_multiplier': 1.0})
        s_g12_m13 = e.validate({'load_growth': 1.2, 'summer_ac_multiplier': 1.3})

        l_g1_m1, _, _ = e.load_profiles(s_g1_m1)
        l_g1_m13, _, _ = e.load_profiles(s_g1_m13)
        l_g12_m1, _, _ = e.load_profiles(s_g12_m1)
        l_g12_m13, _, _ = e.load_profiles(s_g12_m13)

        ac_extra_g1 = l_g1_m13 - l_g1_m1
        ac_extra_g12 = l_g12_m13 - l_g12_m1

        np.testing.assert_allclose(ac_extra_g1, ac_extra_g12, atol=1e-12)
        np.testing.assert_allclose(l_g12_m1, l_g1_m1 * 1.2, atol=1e-12)
        np.testing.assert_allclose(l_g12_m13, l_g1_m1 * 1.2 + ac_extra_g1, atol=1e-12)

    def test_cache_refresh_and_downstream_evaluations(self):
        s1 = {'summer_ac_multiplier': 1.0, 'south_panels': 10, 'battery_kwh': 5}
        s2 = {'summer_ac_multiplier': 1.3, 'south_panels': 10, 'battery_kwh': 5}

        r1 = e.evaluate(s1)
        r2 = e.evaluate(s2)
        self.assertGreater(r2['energy']['load_kwh']['median'], r1['energy']['load_kwh']['median'])
        self.assertGreater(r2['energy']['grid_import_kwh']['median'], r1['energy']['grid_import_kwh']['median'])
        self.assertLess(r2['energy']['autarky']['median'], r1['energy']['autarky']['median'])

        dh1 = e.dayhour(s1, 2023, '2023-07-20')
        dh2 = e.dayhour(s2, 2023, '2023-07-20')
        self.assertGreater(dh2['totals']['load_kwh'], dh1['totals']['load_kwh'])

        fc1 = e.dayhour_forecast(s1, '2027-08-10')
        fc2 = e.dayhour_forecast(s2, '2027-08-10')
        self.assertGreater(fc2['totals']['load_kwh'], fc1['totals']['load_kwh'])

        cd1 = e.chart_data(s1)
        cd2 = e.chart_data(s2)
        self.assertEqual(cd1['summer_ac_multiplier'], 1.0)
        self.assertEqual(cd2['summer_ac_multiplier'], 1.3)
        self.assertGreater(cd2['scenarios'][0]['series']['load_kwh']['annual']['median'],
                           cd1['scenarios'][0]['series']['load_kwh']['annual']['median'])

        bs1 = e.battery_sizing({'summer_ac_multiplier': 1.0, 'south_panels': 12, 'battery_kwh': 5.0}, {'step': 2, 'max_capacity': 10})
        bs2 = e.battery_sizing({'summer_ac_multiplier': 1.3, 'south_panels': 12, 'battery_kwh': 5.0}, {'step': 2, 'max_capacity': 10})
        self.assertGreater(bs2['chart1_capacity_dispatch']['capacities'][1]['grid_import_kwh']['median'],
                           bs1['chart1_capacity_dispatch']['capacities'][1]['grid_import_kwh']['median'])


def years_index(year):return list(map(int,e.data()['years'])).index(year)

if __name__=='__main__':unittest.main()

