"""Request decoding guards for the calculator HTTP API."""
import json
import io
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch,Mock

import feasibility.server as server_module
from feasibility.server import STATIC_ASSETS, decode_object, restart_exec_argv, restart_request, restart_server
import test_support


class RequestDecodingTests(unittest.TestCase):
    def test_only_json_objects_are_accepted(self):
        self.assertEqual(decode_object(b'{"settings":{}}'), {'settings': {}})
        for raw in [b'[]', b'null', b'"text"', b'12']:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'JSON object'):
                decode_object(raw)

    def test_malformed_json_remains_a_client_error(self):
        with self.assertRaises(json.JSONDecodeError):
                decode_object(b'{broken')


class RestartEndpointTests(unittest.TestCase):
    def setUp(self):
        self.previous_job=server_module.JOB.copy()
        self.previous_scheduled=server_module.RESTART_SCHEDULED
        server_module.JOB.update(state='idle',progress='')
        server_module.RESTART_SCHEDULED=False

    def tearDown(self):
        server_module.JOB.clear();server_module.JOB.update(self.previous_job)
        server_module.RESTART_SCHEDULED=self.previous_scheduled

    def test_restart_request_schedules_same_server_replacement_and_returns_old_instance(self):
        httpd=type('Server',(),{'instance_id':'test-instance-old'})()
        with patch.object(server_module,'restart_server') as restart:
            body,status=restart_request(httpd)
        self.assertEqual(status,200)
        self.assertEqual(body,{'status':'restarting','instance_id':'test-instance-old'})
        restart.assert_called_once_with(httpd)
        self.assertTrue(server_module.RESTART_SCHEDULED)

    def test_restart_is_refused_while_study_is_running(self):
        server_module.JOB.update(state='running',progress='Computing')
        with patch.object(server_module,'restart_server') as restart:
            body,status=restart_request(object())
        self.assertEqual(status,409)
        self.assertIn('study is running',body['error'])
        restart.assert_not_called()

    def test_duplicate_restart_request_is_refused(self):
        server_module.RESTART_SCHEDULED=True
        with patch.object(server_module,'restart_server') as restart:
            body,status=restart_request(object())
        self.assertEqual(status,409)
        self.assertIn('already reloading',body['error'])
        restart.assert_not_called()

    def test_restart_helper_stops_server_and_preserves_interpreter_invocation(self):
        server=Mock()
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        with patch.object(server_module.threading,'Thread',ImmediateThread),patch.object(server_module.time,'sleep') as sleep:
            restart_server(server,delay=.4)
        sleep.assert_called_once_with(.4)
        server.shutdown.assert_called_once_with()
        self.assertEqual(restart_exec_argv(),list(getattr(sys,'orig_argv',None) or [sys.executable,*sys.argv]))


class ConfigurationOptimizerRouteTests(unittest.TestCase):
    def test_optimizer_route_passes_separate_search_object(self):
        payload={'settings':{'load_scenario':'ev'},'search':{'objective':'npv_eur','battery_max_modules':2}}
        class Request:
            headers={'Content-Length':str(len(json.dumps(payload).encode()))}
            rfile=io.BytesIO(json.dumps(payload).encode())
            path='/api/optimize-configurations'
            response=None
            status=None
            def send(self,obj,status=200):self.response=obj;self.status=status
        request=Request()
        with patch.object(server_module.engine,'optimize_configuration',return_value={'status':'SCENARIO_PENDING','search':payload['search']}) as optimize:
            server_module.LegacyLocalHandler.do_POST(request)
        self.assertEqual(request.status,200)
        self.assertEqual(request.response['status'],'SCENARIO_PENDING')
        self.assertEqual(request.response['search'],payload['search'])
        self.assertIsInstance(request.response['compute_seconds'],float)
        optimize.assert_called_once_with(payload['settings'],payload['search'])

class DayHourRouteTests(unittest.TestCase):
    def _make_request(self, payload):
        class Request:
            headers={'Content-Length':str(len(json.dumps(payload).encode()))}
            rfile=io.BytesIO(json.dumps(payload).encode())
            path='/api/dayhour'
            response=None
            status=None
            def send(self,obj,status=200):self.response=obj;self.status=status
        req=Request()
        server_module.LegacyLocalHandler.do_POST(req)
        return req

    def test_default_daily_route_backward_compatibility(self):
        payload={'settings':{'load_scenario':'baseline'},'weather_year':2015,'date':'2015-06-21','hour':12}
        with patch.object(server_module.engine,'dayhour',return_value={'period':'day','weather_year':2015}) as mock_dh:
            req=self._make_request(payload)
        self.assertEqual(req.status,200)
        self.assertEqual(req.response['period'],'day')
        mock_dh.assert_called_once_with({'load_scenario':'baseline'},2015,'2015-06-21',12)

    def test_default_forecast_daily_route(self):
        payload={'settings':{},'mode':'forecast_2027','date':'2027-01-01','hour':12,'representative':'median'}
        with patch.object(server_module.engine,'dayhour_forecast',return_value={'mode':'forecast_2027','period':'day'}) as mock_fc:
            req=self._make_request(payload)
        self.assertEqual(req.status,200)
        self.assertEqual(req.response['period'],'day')
        mock_fc.assert_called_once_with({},'2027-01-01',12,None,'median')

    def test_monthly_historical_route(self):
        payload={'settings':{'load_scenario':'baseline'},'period':'month','weather_year':2020,'month':7,'hour':14}
        with patch.object(server_module.engine,'dayhour_monthly',return_value={'period':'month','month':7}) as mock_m:
            req=self._make_request(payload)
        self.assertEqual(req.status,200)
        self.assertEqual(req.response['period'],'month')
        mock_m.assert_called_once_with({'load_scenario':'baseline'},2020,7,14)

    def test_monthly_forecast_route(self):
        payload={'settings':{},'period':'month','mode':'forecast_2027','month':2,'hour':12,'scenario':'electrified','representative':'high'}
        with patch.object(server_module.engine,'dayhour_forecast_monthly',return_value={'mode':'forecast_2027','period':'month','month':2}) as mock_fcm:
            req=self._make_request(payload)
        self.assertEqual(req.status,200)
        self.assertEqual(req.response['period'],'month')
        mock_fcm.assert_called_once_with({},2,12,'electrified','high')

    def test_dayhour_route_strict_validation(self):
        # Invalid period
        req=self._make_request({'settings':{},'period':'annual'})
        self.assertEqual(req.status,400)
        self.assertIn('period must be day or month',req.response['error'])

        # Period month with boolean month
        req=self._make_request({'settings':{},'period':'month','month':True})
        self.assertEqual(req.status,400)
        self.assertIn('month must be an integer from 1 to 12',req.response['error'])

        # Period month with invalid month integer
        req=self._make_request({'settings':{},'period':'month','month':13})
        self.assertEqual(req.status,400)
        self.assertIn('month must be an integer from 1 to 12',req.response['error'])

        # Period month with missing month
        req=self._make_request({'settings':{},'period':'month'})
        self.assertEqual(req.status,400)
        self.assertIn('month must be an integer from 1 to 12',req.response['error'])


class DefaultsRouteTests(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        tpath = Path(self._temp_dir.name)
        p1 = patch.object(server_module.assumptions, 'USER_DEFAULTS_PATH', tpath / 'user_defaults.json')
        p2 = patch.object(server_module.shared_state, 'STATE_PATH', tpath / 'current_scenario.json')
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _make_request(self, payload):
        class Request:
            headers={'Content-Length':str(len(json.dumps(payload).encode()))}
            rfile=io.BytesIO(json.dumps(payload).encode())
            path='/api/defaults'
            response=None
            status=None
            def send(self,obj,status=200):self.response=obj;self.status=status
        req=Request()
        server_module.LegacyLocalHandler.do_POST(req)
        return req

    def test_non_battery_default_preserves_behavior(self):
        with patch.object(server_module.assumptions, 'save_user_default', return_value={'value': 12000}) as mock_save:
            req = self._make_request({'name': 'deduction_tax_capacity', 'value': 12000})
        self.assertEqual(req.status, 200)
        self.assertEqual(req.response['name'], 'deduction_tax_capacity')
        self.assertEqual(req.response['value'], 12000)
        self.assertIs(req.response['restart_required'], True)
        self.assertIn('record', req.response)
        self.assertNotIn('defaults', req.response)
        mock_save.assert_called_once_with('deduction_tax_capacity', 12000, server_module.engine.validate, battery_settings=None)

    def test_battery_default_with_context_returns_linked_defaults_and_records(self):
        ctx = {
            'battery_mixed_sizes': True,
            'battery_modules': 3,
            'battery_secondary_modules': 2,
            'battery_module_kwh': 5.0,
            'battery_secondary_module_kwh': 6.9,
            'battery_kwh': 18.8,
        }
        with patch.object(server_module.assumptions, 'save_user_default', return_value={'value': 2}) as mock_save:
            req = self._make_request({
                'name': 'battery_secondary_modules',
                'value': 2,
                'battery_settings': ctx
            })
        self.assertEqual(req.status, 200)
        self.assertEqual(req.response['name'], 'battery_secondary_modules')
        self.assertEqual(req.response['value'], 2)
        self.assertIn('defaults', req.response)
        self.assertIn('records', req.response)
        self.assertEqual(set(req.response['defaults'].keys()), set(server_module.assumptions.BATTERY_SETTINGS))
        record_names = {r['name'] for r in req.response['records']}
        self.assertEqual(record_names, set(server_module.assumptions.BATTERY_SETTINGS))
        mock_save.assert_called_once_with('battery_secondary_modules', 2, server_module.engine.validate, battery_settings=ctx)

    def test_battery_default_rejects_invalid_context_type(self):
        req = self._make_request({
            'name': 'battery_modules',
            'value': 2,
            'battery_settings': 'invalid_string'
        })
        self.assertEqual(req.status, 400)
        self.assertIn('battery_settings must be an object', req.response['error'])

    def test_battery_default_end_to_end_persistence_differing_from_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_defaults = Path(temp_dir) / 'user_defaults.json'
            temp_scenario = Path(temp_dir) / 'current_scenario.json'
            ctx = {
                'battery_mixed_sizes': True,
                'battery_modules': 3,
                'battery_secondary_modules': 2,
                'battery_module_kwh': 5.0,
                'battery_secondary_module_kwh': 6.9,
                'battery_kwh': 18.8,
            }
            with patch.object(server_module.assumptions, 'USER_DEFAULTS_PATH', temp_defaults), \
                 patch.object(server_module.shared_state, 'STATE_PATH', temp_scenario):
                req = self._make_request({
                    'name': 'battery_modules',
                    'value': 3,
                    'battery_settings': ctx
                })
            self.assertEqual(req.status, 200)
            self.assertEqual(req.response['name'], 'battery_modules')
            self.assertEqual(req.response['value'], 3)
            self.assertEqual(req.response['defaults']['battery_mixed_sizes'], True)
            self.assertEqual(req.response['defaults']['battery_modules'], 3)
            self.assertEqual(req.response['defaults']['battery_secondary_modules'], 2)
            self.assertEqual(req.response['defaults']['battery_kwh'], 18.8)
            saved = json.loads(temp_defaults.read_text())['overrides']
            self.assertEqual(saved['battery_modules']['value'], 3)
            self.assertEqual(saved['battery_secondary_modules']['value'], 2)
            self.assertEqual(saved['battery_mixed_sizes']['value'], True)
            self.assertIs(req.response['restart_required'], True)
            self.assertFalse(server_module.engine.defaults()['battery_mixed_sizes'])

    def test_api_solar_sizing_sensitivity_route(self):
        class Request:
            headers = {'Content-Length': '0'}
            rfile = io.BytesIO(b'{}')
            path = '/api/solar-sizing-sensitivity'
            response = None
            status = None
            def send(self, obj, status=200):
                self.response = obj
                self.status = status

        req = Request()
        mock_res = {'status': 'SUCCESS', 'scenarios': {'base': {}}, 'comparisons': []}
        with patch.object(server_module.engine, 'solar_sizing_sensitivity', return_value=mock_res) as mock_sens:
            payload = {'settings': {'load_scenario': 'baseline'}, 'options': {'financial_basis': 'project'}, 'sensitivity_options': {'export_revenue_delta_pct': 25.0}}
            body_bytes = json.dumps(payload).encode()
            req.headers = {'Content-Length': str(len(body_bytes))}
            req.rfile = io.BytesIO(body_bytes)
            server_module.LegacyLocalHandler.do_POST(req)

        self.assertEqual(req.status, 200)
        self.assertEqual(req.response['status'], 'SUCCESS')
        self.assertIn('compute_seconds', req.response)
        mock_sens.assert_called_once_with({'load_scenario': 'baseline'}, {'financial_basis': 'project'}, None, {'export_revenue_delta_pct': 25.0})

    def test_api_solar_sizing_sensitivity_genuine_engine_route_with_panel_maxima(self):
        """Genuine unmocked engine execution through POST /api/solar-sizing-sensitivity with real panel_maxima."""
        class Request:
            headers = {'Content-Length': '0'}
            rfile = io.BytesIO(b'{}')
            path = '/api/solar-sizing-sensitivity'
            response = None
            status = None
            def send(self, obj, status=200):
                self.response = obj
                self.status = status

        req = Request()
        # Real-shaped payload with tiny domain: all panel_maxima 0 and 0 battery
        payload = {
            'settings': {
                'load_scenario': 'baseline',
                'inverter_kw': 6.0
            },
            'options': {
                'panel_maxima': {'south': 0, 'west': 0, 'east': 0, 'dormer': 0, 'flat': 0, 'secondary_south': 0},
                'battery_max_kwh': 0,
                'financial_basis': 'project'
            },
            'sensitivity_options': {
                'export_revenue_delta_pct': 20.0,
                'installed_cost_delta_pct': 20.0,
                'household_consumption_delta_pct': 10.0,
                'financing_apr_delta_pp': 2.0
            }
        }
        body_bytes = json.dumps(payload).encode()
        req.headers = {'Content-Length': str(len(body_bytes))}
        req.rfile = io.BytesIO(body_bytes)

        server_module.LegacyLocalHandler.do_POST(req)

        self.assertEqual(req.status, 200)
        self.assertEqual(req.response['status'], 'SUCCESS')
        self.assertIn('scenarios', req.response)
        self.assertIn('base', req.response['scenarios'])
        self.assertIn('compute_seconds', req.response)
        base_fin = req.response['scenarios']['base']['roles']['financial']
        self.assertTrue(base_fin['is_no_install'])
        self.assertEqual(base_fin['panel_count'], 0)
        self.assertEqual(base_fin['battery_kwh'], 0.0)



if __name__ == '__main__':
    unittest.main()


def test_cli_defaults_to_loopback_without_changing_explicit_lan_support():
    import ast
    tree=ast.parse(Path(server_module.__file__).read_text())
    host=[kw.value.value for call in ast.walk(tree)
          if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute)
          and call.func.attr=='add_argument' and call.args
          and isinstance(call.args[0],ast.Constant) and call.args[0].value=='--host'
          for kw in call.keywords if kw.arg=='default' and isinstance(kw.value,ast.Constant)]
    assert host==['127.0.0.1']
