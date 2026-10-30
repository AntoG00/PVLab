"""Acceptance tests for added price, quote and lifetime boundaries."""
import unittest,json,math
from unittest.mock import patch
import numpy as np
from feasibility import engine as e
from test_support import IsolatedDefaultsTestCase

class IntegrationTests(IsolatedDefaultsTestCase):
    def test_loan_amount_solver_matches_brute_force_and_normalizes_quote(self):
        settings={'horizon_years':3,'south_panels':12,'west_panels':0,'east_panels':0,'battery_kwh':0,
                 'loan_apr':.05,'loan_years':5,'real_discount_rate':.02,'inflation':.01,
                 'loan_include_electrification':False}
        quote={'line_items':[{'net_eur':100/1.1,'quantity':1,'vat':.1}]}
        solved=e.solve_loan_amount(settings,quote)
        self.assertEqual(solved['eligible_cap_eur'],100)
        self.assertEqual(solved['resolution_eur'],1)
        self.assertEqual(solved['search_candidates'],101)
        self.assertEqual(solved['weather_count'],e.weather_period().count)
        self.assertIsNone(solved['median_year20_discounted_impact_eur'],'short horizons do not fabricate a year-20 value')
        normalized,_,_,_=e.normalize_quote(settings,quote)
        normalized.update(loan_mode='amount',loan_amount=0.,loan_fraction=0.)
        counts=sum(normalized[n+'_panels'] for n in ['south','west','east','dormer','flat','secondary_south'])
        version=0;pricefile=e.HERE/'data/hourly_prices.npz'
        if pricefile.exists():version=pricefile.stat().st_mtime_ns
        annual,_=e._energy_cache(tuple((k,normalized[k]) for k in e.ENERGY_KEYS),version)
        base,_=e._financial(normalized,annual,counts*normalized['panel_watts']/1000,calculate_irr=False)
        rate=(1+normalized['real_discount_rate'])*(1+normalized['inflation'])-1
        objective=[]
        for amount in range(101):
            candidate={**normalized,'loan_amount':amount}
            finance,_=e._financial(candidate,annual,counts*candidate['panel_watts']/1000,calculate_irr=False)
            paths=finance['cashflows_by_weather']['equity']
            objective.append(e.payback_envelope([e.persistent_payback(path,rate) for path in paths])['median'])
        finite=[(value,amount) for amount,value in enumerate(objective) if value is not None]
        if finite:
            expected=min(v for v,_ in finite)
            expected_amount=min(a for v,a in finite if abs(v-expected)<1e-10)
            self.assertEqual(solved['status'],'OPTIMAL')
            self.assertAlmostEqual(solved['median_discounted_recovery_years'],expected,places=9)
            self.assertEqual(solved['optimal_amount_eur'],expected_amount)
        else:
            self.assertEqual(solved['status'],'NO_RECOVERY')
            self.assertIsNone(solved['optimal_amount_eur'])
            self.assertIsNone(solved['median_discounted_recovery_years'])
        self.assertEqual(base['loan_principal_eur'],0)
    def test_loan_amount_solver_reports_no_recovery_instead_of_a_fake_optimum(self):
        settings={'horizon_years':2,'loan_apr':.05,'loan_years':5,'south_panels':0,'battery_kwh':0}
        finance={'capex_eur':100.,'conversion_capex_eur':0.,'cashflows_by_weather':{'equity':[[-100.,0.,0.] for _ in range(19)]}}
        with patch.object(e,'_energy_cache',return_value=(np.zeros((2,19,1)),{})), \
             patch.object(e,'_financial',return_value=(finance,[])):
            solved=e.solve_loan_amount(settings,{})
        self.assertEqual(solved['status'],'NO_RECOVERY')
        self.assertIsNone(solved['optimal_amount_eur'])
        self.assertIsNone(solved['median_discounted_recovery_years'])
        self.assertEqual(solved['eligible_cap_eur'],100)
    def test_loan_solver_matches_persistent_payback_with_reversal_censoring_and_fractional_cap(self):
        settings={'horizon_years':11,'loan_apr':.2,'loan_years':5,'real_discount_rate':.08,
                  'inflation':0,'south_panels':0,'battery_kwh':0,'quote_total':165.5,
                  'quote_vat_included':True,'loan_upfront_fees_or_tax':0}
        # Most weather paths recover, with later negative years reversing interim progress.
        # Two paths never recover, so the weather median must retain their censored values.
        representative=np.array([-101.,75.,-42.,95.,-9.,62.,144.,-1.,53.,137.,-49.,114.])
        paths=np.tile(representative,(19,1))
        paths[:17,1:] += np.linspace(-2,2,17)[:,None]
        paths[17:]=np.array([-101.,-5.,-5.,-5.,-5.,-5.,-5.,-5.,-5.,-5.,-5.,-5.])
        finance={'capex_eur':165.5,'conversion_capex_eur':0.,'cashflows_by_weather':{'equity':paths}}
        with patch.object(e,'_energy_cache',return_value=(np.zeros((12,19,1)),{})), \
             patch.object(e,'_financial',return_value=(finance,[])):
            solved=e.solve_loan_amount(settings,{})

        self.assertEqual(solved['eligible_cap_eur'],165.5)
        self.assertEqual(solved['search_candidates'],167)  # 0..165 plus the exact fractional cap
        self.assertEqual(solved['weather_count'],19)
        rate=(1+settings['real_discount_rate'])*(1+settings['inflation'])-1
        candidates=[*range(166),165.5]
        objectives=[]
        for amount in candidates:
            debt,_=e.loan_schedule(amount,settings['loan_apr'],settings['loan_years'],settings['horizon_years'])
            values=[]
            for path in paths:
                equity=path.copy();equity[0]+=amount;equity[1:]-=debt
                values.append(e.persistent_payback(equity,rate))
            objectives.append(e.payback_envelope(values)['median'])
        finite=[(value,amount) for value,amount in zip(objectives,candidates) if value is not None]
        expected=min(value for value,_ in finite)
        expected_amount=min(amount for value,amount in finite if abs(value-expected)<1e-10)
        self.assertEqual(solved['status'],'OPTIMAL')
        self.assertAlmostEqual(solved['median_discounted_recovery_years'],expected,places=9)
        self.assertEqual(solved['optimal_amount_eur'],expected_amount)
        self.assertGreater(expected_amount,0)
        self.assertLess(expected_amount,165)
    def test_monthly_budget_adds_exact_affordable_boundary_and_matches_brute_force(self):
        settings={'horizon_years':5,'loan_apr':.12,'loan_years':4,'real_discount_rate':.03,
                  'inflation':.02,'south_panels':0,'battery_kwh':0,'quote_total':165.5,
                  'quote_vat_included':True,'loan_upfront_fees_or_tax':0}
        factor=(settings['loan_apr']/12)/(1-(1+settings['loan_apr']/12)**(-settings['loan_years']*12))
        budget=80.25*factor
        settings['loan_max_monthly_payment']=budget
        paths=np.tile(np.array([-100.,42.,55.,35.,20.,8.]),(19,1))
        finance={'capex_eur':165.5,'conversion_capex_eur':0.,'cashflows_by_weather':{'equity':paths}}
        with patch.object(e,'_energy_cache',return_value=(np.zeros((6,19,1)),{})), \
             patch.object(e,'_financial',return_value=(finance,[])):
            solved=e.solve_loan_amount(settings,{})
        self.assertEqual(solved['eligible_cap_eur'],165.5)
        self.assertAlmostEqual(solved['affordable_search_cap_eur'],80.25)
        self.assertEqual(solved['search_candidates'],82)  # 0..80 plus exact affordable principal
        self.assertEqual(solved['max_monthly_payment_eur'],budget)
        candidates=[*range(81),80.25]
        rate=(1+settings['real_discount_rate'])*(1+settings['inflation'])-1
        values=[]
        for amount in candidates:
            debt,_=e.loan_schedule(amount,settings['loan_apr'],settings['loan_years'],settings['horizon_years'])
            values.append(e.payback_envelope([e.persistent_payback(path+np.r_[amount,-debt],rate) for path in paths])['median'])
        feasible=[(value,amount) for value,amount in zip(values,candidates) if value is not None]
        best=min(value for value,_ in feasible)
        expected=min(amount for value,amount in feasible if abs(value-best)<1e-10)
        self.assertEqual(solved['status'],'OPTIMAL')
        self.assertAlmostEqual(solved['median_discounted_recovery_years'],best,places=9)
        self.assertEqual(solved['optimal_amount_eur'],expected)
        self.assertLessEqual(solved['optimal_monthly_payment_eur'],budget+1e-10)
    def test_monthly_budget_never_adds_slightly_over_budget_eligible_cap(self):
        settings={'horizon_years':2,'loan_apr':.06,'loan_years':2,'south_panels':0,'battery_kwh':0,
                  'quote_total':100.5,'quote_vat_included':True,'loan_upfront_fees_or_tax':0}
        factor=(settings['loan_apr']/12)/(1-(1+settings['loan_apr']/12)**(-settings['loan_years']*12))
        settings['loan_max_monthly_payment']=(100.5-5e-10)*factor
        finance={'capex_eur':100.5,'conversion_capex_eur':0.,'cashflows_by_weather':{'equity':[[-100.,0.,0.] for _ in range(19)]}}
        with patch.object(e,'_energy_cache',return_value=(np.zeros((3,19,1)),{})), \
             patch.object(e,'_financial',return_value=(finance,[])):
            solved=e.solve_loan_amount(settings,{})
        self.assertLess(solved['affordable_search_cap_eur'],100.5)
        self.assertNotEqual(solved['affordable_search_cap_eur'],100.5)
    def test_monthly_budget_zero_apr_zero_cap_and_validation(self):
        self.assertIsNone(e.validate({})['loan_max_monthly_payment'])
        for invalid in [-.01,100000.01,float('inf'),float('nan')]:
            with self.assertRaises(ValueError):e.validate({'loan_max_monthly_payment':invalid})
        settings={'horizon_years':2,'loan_apr':0,'loan_years':2,'south_panels':0,'battery_kwh':0,
                  'loan_max_monthly_payment':0,'loan_upfront_fees_or_tax':0}
        finance={'capex_eur':100.,'conversion_capex_eur':0.,'cashflows_by_weather':{'equity':[[-100.,0.,0.] for _ in range(19)]}}
        with patch.object(e,'_energy_cache',return_value=(np.zeros((3,19,1)),{})), \
             patch.object(e,'_financial',return_value=(finance,[])):
            solved=e.solve_loan_amount(settings,{})
        self.assertEqual(solved['affordable_search_cap_eur'],0)
        self.assertEqual(solved['search_candidates'],1)
        self.assertIsNone(solved['optimal_monthly_payment_eur'])
        self.assertEqual(solved['eligible_cap_eur'],100)
        self.assertEqual(solved['status'],'NO_RECOVERY')
        settings['loan_max_monthly_payment']=2.5
        with patch.object(e,'_energy_cache',return_value=(np.zeros((3,19,1)),{})), \
             patch.object(e,'_financial',return_value=(finance,[])):
            positive=e.solve_loan_amount(settings,{})
        self.assertEqual(positive['affordable_search_cap_eur'],60)
        self.assertEqual(positive['search_candidates'],61)
    def test_quote_vat_scope_provenance_and_claims(self):
        q={'line_items':[{'net_eur':1000,'quantity':2,'vat':.1}], 'battery_nameplate_kwh':10,'battery_dod':.8,'claimed_yield_kwh':100000,'continuous_charge_kw':2,'continuous_discharge_kw':2}
        r=e.quote_evaluate({'horizon_years':2},q)
        self.assertEqual(r['itemized_total_eur'],2200)
        self.assertEqual(r['result']['finance']['capex_eur'],2200)
        self.assertEqual(r['result']['settings']['battery_kwh'],8)
        self.assertTrue(r['claims'][0]['outside_weather_range'])
        a=next(x for x in r['result']['assumptions'] if x['name']=='battery_kwh')
        self.assertEqual(a['status'],'proposed');self.assertIn('base_source',a)
        self.assertEqual(e.quote_evaluate({}, {})['itemized_total_eur'],None)
        failed=e.quote_evaluate({'horizon_years':1,'quote_total':150000},{'claimed_payback_years':1})
        self.assertTrue(failed['claims'][0]['outside_weather_range'])
        for bad in [{'battery_nameplate_kwh':5,'battery_dod':1.1},{'line_items':[{'net_eur':100,'scope':'conversion'}]},{'roundtrip_efficiency':1.2}]:
            with self.assertRaises(ValueError):e.quote_evaluate({},bad)
        for bad_quote in [[], {'line_items':[None]}, {'line_items':[1]}, 'not an object']:
            with self.assertRaises(ValueError):e.quote_evaluate({},bad_quote)
        saved=json.loads(json.dumps({'settings':r['result']['settings'],'quote':q}))
        self.assertEqual(e.quote_evaluate(saved['settings'],saved['quote'])['result']['metrics'],r['result']['metrics'])
    def test_irr_fair_price_one_year_and_lcoe_weather_roots(self):
        s={'horizon_years':1,'battery_kwh':0,'deduction_rate':0,'inflation':0,'om_annual':0,'quote_total':0}
        free=e.evaluate(s);flows=np.array(free['finance']['cashflows_by_weather']['pv_incremental'])
        root=e.fair_price(s,'irr',.1)
        self.assertAlmostEqual(root['price_eur'],np.median(flows[:,1])/1.1,places=4)
        lcoe=e.fair_price(s,'lcoe',.2);self.assertEqual(len(lcoe['roots_by_weather']),e.weather_period().count)
        self.assertGreater(lcoe['price_range_eur']['max'],lcoe['price_range_eur']['min'])
        for wi,price in enumerate(lcoe['roots_by_weather']):
            self.assertAlmostEqual(e.evaluate({**s,'quote_total':price})['finance']['lcoe_by_weather'][wi],.2,places=6)
        json.dumps(e.evaluate({'south_panels':0,'battery_kwh':0}),allow_nan=False)
    def test_lifetime_all_interval_constraints_and_replacement(self):
        s=e.validate({'load_scenario':'ev','horizon_years':20});d=e.data();pv=e.pv_profile(s);load,ev,_=e.load_profiles(s);eff=math.sqrt(s['roundtrip_efficiency'])
        max_error=0.;intervals=0
        for w in range(len(d['years'])):
            soc=0.;age=0;cycles=0.
            for y in range(s['horizon_years']):
                if y==s['battery_replace_year']:soc=0.;age=0;cycles=0.
                capacity=s['battery_kwh']*(1-s['battery_calendar_fade'])**age*(1-s['battery_cycle_fade'])**cycles
                row,t=e.dispatch_year(pv[w]*(1-s['pv_first_year_loss'])*(1-s['pv_annual_fade'])**y,load[w]+ev[w],capacity,s['charge_kw'],s['discharge_kw'],eff,s['inverter_kw'],soc,e.price_profile()[0][w],d['valid'][w])
                v=t[d['valid'][w]];before=np.r_[min(soc,capacity),v[:-1,4]]
                loss=v[:,6]*(1-eff)+v[:,7]*(1/eff-1)
                error=np.max(np.abs(v[:,0]+v[:,2]-v[:,1]-v[:,3]-loss-(v[:,4]-before)))
                max_error=max(max_error,error);intervals+=len(v)
                self.assertTrue(np.all(v[:,4]>=-1e-8));self.assertTrue(np.all(v[:,4]<=capacity+1e-8))
                self.assertTrue(np.all(v[:,6]<=s['charge_kw']+1e-8));self.assertTrue(np.all(v[:,7]<=s['discharge_kw']+1e-8));self.assertTrue(np.all(v[:,5]+v[:,7]<=s['inverter_kw']+1e-8))
                soc=row[8];cycles+=row[10];age+=1
        self.assertLess(max_error,1e-8);self.assertEqual(intervals,int(d['valid'].sum())*20)
        r=e.evaluate(s);capacity=[x['capacity_kwh']['median'] for x in r['lifetime_energy']]
        self.assertLess(capacity[11],s['battery_kwh']);self.assertAlmostEqual(capacity[12],s['battery_kwh'])
        self.assertGreater(r['annual_cashflows'][12]['replacement'],0)
    def test_real_display_and_plausible_ranges(self):
        r=e.evaluate({'cashflow_mode':'real','horizon_years':2,'loan_fraction':1,'loan_apr':.05})
        self.assertAlmostEqual(r['annual_cashflows'][2]['display_pv_incremental_range']['median'],r['annual_cashflows'][2]['pv_incremental_range']['median']/1.02**2)
        self.assertAlmostEqual(r['annual_cashflows'][2]['display_debt_service'],r['annual_cashflows'][2]['debt_service']/1.02**2)
        records={x['name']:x for x in e.records()}
        self.assertEqual(records['installation_vat']['plausible_range'],[0,.22])
        self.assertEqual(records['loan_apr']['plausible_range'],[0,.30])
if __name__=='__main__':unittest.main()
