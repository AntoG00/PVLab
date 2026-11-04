#!/usr/bin/env python3
"""Private LAN interface; only curated app assets and computed reports are served."""
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'vendor'));sys.path.insert(0,str(HERE.parent))
import argparse,json,mimetypes,time,traceback,threading,os,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from feasibility import engine, assumptions, shared_state, evidence_store, weather_dataset

JOB={'state':'idle','progress':''};JOB_LOCK=threading.Lock()
RESTART_LOCK=threading.Lock();RESTART_SCHEDULED=False
INSTANCE_ID=uuid.uuid4().hex
STATIC_ASSETS={'/':'web/index.html','/favicon.png':'web/favicon.png','/diagnostics.js':'web/diagnostics.js','/app.js':'web/app.js','/schedule.js':'web/schedule.js','/evidence-datasets.js':'web/evidence-datasets.js','/evidence-datasets.css':'web/evidence-datasets.css','/sync.js':'web/sync.js','/onboarding.js':'web/onboarding.js','/consumption-csv.js':'web/consumption-csv.js','/restart.js':'web/restart.js','/style.css':'web/style.css'}

def restart_exec_argv():
    """Preserve the current interpreter flags and script arguments on replacement."""
    return list(getattr(sys,'orig_argv',None) or [sys.executable,*sys.argv])

def restart_server(server,delay=0.4):
    """Stop accepting requests; the main thread replaces this process after serve_forever exits."""
    def replace_process():
        time.sleep(delay)  # let the request handler finish sending its response
        server.shutdown()
    threading.Thread(target=replace_process,name='server-restart',daemon=True).start()

def restart_request(server):
    global RESTART_SCHEDULED
    with JOB_LOCK:
        if hasattr(server, 'pool') and server.pool.has_active():
            return {'error': 'A job is active; retry after completion.'},409
        if JOB['state']=='running':
            return {'error':'The study is running. Wait for it to finish before reloading the server.'},409
        with RESTART_LOCK:
            if RESTART_SCHEDULED:
                return {'error':'The server is already reloading. Wait for it to come back online.'},409
            RESTART_SCHEDULED=True
            try:restart_server(server)
            except Exception:
                RESTART_SCHEDULED=False
                raise
    return {'status':'restarting','instance_id':server.instance_id},200

def decode_object(raw):
    body=json.loads(raw)
    if not isinstance(body,dict):raise ValueError('Request body must be a JSON object')
    return body

def state_get_response(query):
    revisions=parse_qs(query).get('revision',[])
    return shared_state.get_state(revisions[0] if revisions else None)

def state_post_response(body):
    return shared_state.update_state(body)

def run_study(settings):
    try:
        from feasibility.worker_pool import run_local
        from feasibility.profile_store import ProfileStore
        root=ProfileStore().root
        destination=root/'cli-reports'/uuid.uuid4().hex
        destination.mkdir(mode=0o700,parents=True)
        run_local('legacy-report', settings, {'profile_root':str(root/'cli-profiles'), 'report_destination':str(destination)})
        JOB.update(state='complete',progress='Reports ready')
    except Exception as ex:JOB.update(state='error',error=str(ex));traceback.print_exc()


def configure_tenant_storage(server, store, *, allowed_hosts=(), pool=None, limits=None, local_admin_restart=False, catalog_root=None):
    """Bind the transport-independent service and its bounded spawn pool."""
    from feasibility.api import create_service
    from feasibility.worker_pool import WorkerPool
    from feasibility.sessions import Sessions
    server.tenant_store = store
    server.sessions = Sessions(store)
    server.allowed_hosts = tuple(allowed_hosts) or (f'localhost:{server.server_port}', f'127.0.0.1:{server.server_port}')
    server.pool = pool or WorkerPool(store)
    server.service = create_service({'allowed_hosts': server.allowed_hosts, 'limits': limits or {},
        'socket_destination': not bool(allowed_hosts),
        'catalog_root': str(catalog_root) if catalog_root is not None else None,
        'restart_callback': (lambda: restart_request(server)) if local_admin_restart else None}, store, server.pool)


def _draft_defaults(candidate, *, base, supplied):
    """Draft storage validation only; activation still requires model validation."""
    from feasibility.tenant_store import StoreError
    result = dict(base)
    for name, value in candidate.items():
        if name not in base:
            raise StoreError()
        old = base[name]
        if type(value) is not type(old) and not (type(value) in (float, int) and type(old) in (float, int)):
            raise StoreError()
        result[name] = value
    return result


class LegacyLocalHandler(BaseHTTPRequestHandler):
    """Explicit private compatibility adapter, never installed by the server CLI."""
    def log_message(self,*args):pass
    def send(self,obj,status=200,kind='application/json',headers=None):
        raw=obj if isinstance(obj,bytes) else json.dumps(obj,allow_nan=False).encode() if kind=='application/json' else obj.encode() if isinstance(obj,str) else obj
        self.send_response(status);self.send_header('Content-Type',kind+'; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        for key,value in (headers or {}).items():self.send_header(key,value)
        self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        parsed=urlsplit(self.path);path=parsed.path
        if path=='/api/evidence/datasets':
            try:return self.send(evidence_store.inventory())
            except (ValueError,OSError) as ex:return self.send({'error':str(ex)},400)
        if path=='/api/health':return self.send({'status':'ok','engine':'feasibility/engine.py','port':self.server.server_port,'instance_id':self.server.instance_id})
        if path=='/api/schema':return self.send({'defaults':engine.defaults(),'records':engine.records(),'calibration':engine.calibration(),'audit':engine.data()['audit'],'empirical_prices':engine.empirical_month_hour_data('oct2025_sep2026')})
        if path=='/api/empirical-prices':
            query=parse_qs(parsed.query)
            window=query.get('window',['oct2025_sep2026'])[0]
            data=engine.empirical_month_hour_data(window)
            return self.send(data if data else {'error':'Empirical price data not available'},200 if data else 404)
        if path=='/api/state':
            return self.send(state_get_response(parsed.query))
        if path=='/api/job':return self.send(JOB)
        if path=='/api/study':
            f=HERE/'reports/study.json'
            if not f.exists():
                return self.send({'error':'Study not yet computed'},404)
            try:
                raw=f.read_bytes()
                data=json.loads(raw)
            except Exception:
                return self.send({'error':'Malformed study report on disk; rerun required.','stale':True},409)
            if not isinstance(data,dict):
                return self.send({'error':'Malformed study report; rerun required.','stale':True},409)
            saved_meta=data.get('weather_dataset')
            saved_identity=saved_meta.get('identity') if isinstance(saved_meta,dict) else None
            current_identity=list(engine.weather_dataset.active_weather_dataset_identity())
            if saved_identity is None or saved_identity!=current_identity:
                return self.send({'error':'Study was generated under a previous weather dataset; rerun required.','stale':True,'saved_identity':saved_identity,'current_identity':current_identity},409)
            return self.send(raw,kind='application/octet-stream')
        if path not in STATIC_ASSETS:return self.send({'error':'Not found'},404)
        f=HERE/STATIC_ASSETS[path]
        if not f.is_file():return self.send({'error':'Artifact not yet ready'},404)
        return self.send(f.read_bytes(),kind=mimetypes.guess_type(f.name)[0] or 'text/plain')
    def do_POST(self):
        try:
            if self.path.split('?')[0]=='/api/evidence/datasets':
                return self.send(evidence_store.receive_upload(self))
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<2_000_000:raise ValueError('Request body missing or too large')
            body=decode_object(self.rfile.read(length));settings=body.get('settings',{})
            if not isinstance(settings,dict):raise ValueError('Settings must be an object')
            path=self.path.split('?')[0];start=time.perf_counter()
            if path=='/api/evidence/datasets/selection':return self.send(evidence_store.set_inclusion(body))
            elif path=='/api/state':return self.send(state_post_response(body))
            elif path=='/api/restart':
                response,status=restart_request(self.server)
                return self.send(response,status)
            elif path=='/api/evaluate':result=engine.quote_evaluate(settings,body.get('quote'))
            elif path=='/api/loan-solve':result=engine.solve_loan_amount(settings,body.get('quote'))
            elif path=='/api/defaults':
                name=body.get('name')
                if not isinstance(name,str) or name not in assumptions.SPEC:raise ValueError('name must be a known assumption')
                if body.get('action') == 'reset' or body.get('reset') is True:
                    assumptions.reset_user_default(name)
                    all_records=assumptions.persisted_records()
                    all_defaults=assumptions.persisted_defaults()
                    record=next(r for r in all_records if r['name']==name)
                    if name in assumptions.SCHEDULE_SETTINGS:
                        sched_records=[r for r in all_records if r['name'] in assumptions.SCHEDULE_SETTINGS]
                        sched_defaults={k:all_defaults[k] for k in assumptions.SCHEDULE_SETTINGS}
                        return self.send({'restart_required':True,'name':name,'reset':True,'record':record,'defaults':sched_defaults,'records':sched_records})
                    if name in assumptions.BATTERY_SETTINGS:
                        bat_records=[r for r in all_records if r['name'] in assumptions.BATTERY_SETTINGS]
                        bat_defaults={k:all_defaults[k] for k in assumptions.BATTERY_SETTINGS}
                        return self.send({'restart_required':True,'name':name,'reset':True,'record':record,'defaults':bat_defaults,'records':bat_records})
                    return self.send({'restart_required':True,'name':name,'reset':True,'record':record,'defaults':{name:all_defaults[name]}})
                if 'value' not in body:raise ValueError('value is required')
                battery_ctx=body.get('battery_settings')
                if battery_ctx is not None and not isinstance(battery_ctx,dict):
                    raise ValueError('battery_settings must be an object')
                sched_ctx=body.get('schedule_settings')
                if sched_ctx is not None and not isinstance(sched_ctx,dict):
                    raise ValueError('schedule_settings must be an object')
                save_kw = {'battery_settings': battery_ctx}
                if sched_ctx is not None:
                    save_kw['schedule_settings'] = sched_ctx
                entry = assumptions.save_user_default(name, body['value'], engine.validate, **save_kw)
                if name in assumptions.SCHEDULE_SETTINGS:
                    all_records=assumptions.persisted_records()
                    all_defaults=assumptions.persisted_defaults()
                    sched_records=[r for r in all_records if r['name'] in assumptions.SCHEDULE_SETTINGS]
                    sched_defaults={k:all_defaults[k] for k in assumptions.SCHEDULE_SETTINGS}
                    record=next(r for r in all_records if r['name']==name)
                    return self.send({'restart_required':True,'name':name,'value':entry['value'],'record':record,'defaults':sched_defaults,'records':sched_records})
                if name in assumptions.BATTERY_SETTINGS:
                    all_records=assumptions.persisted_records()
                    all_defaults=assumptions.persisted_defaults()
                    bat_records=[r for r in all_records if r['name'] in assumptions.BATTERY_SETTINGS]
                    bat_defaults={k:all_defaults[k] for k in assumptions.BATTERY_SETTINGS}
                    record=next(r for r in all_records if r['name']==name)
                    return self.send({'restart_required':True,'name':name,'value':entry['value'],'record':record,'defaults':bat_defaults,'records':bat_records})
                record=next(r for r in assumptions.persisted_records() if r['name']==name)
                return self.send({'restart_required':True,'name':name,'value':entry['value'],'record':record})
            elif path=='/api/dayhour':
                has_period='period' in body or 'aggregation' in body
                period=body.get('period',body.get('aggregation'))
                if has_period:
                    if period not in ('day','month','daily','monthly'):
                        raise ValueError('period must be day or month')
                    is_monthly=period in ('month','monthly')
                else:
                    is_monthly='month' in body and 'date' not in body
                if is_monthly:
                    month=body.get('month')
                    if isinstance(month,bool) or not isinstance(month,int) or not 1<=month<=12:
                        raise ValueError('month must be an integer from 1 to 12')
                    if body.get('mode') in ('forecast_2027', 'forecast'):
                        result=engine.dayhour_forecast_monthly(settings,month,body.get('hour'),body.get('scenario'),body.get('representative','median'))
                    else:
                        result=engine.dayhour_monthly({**settings,'load_scenario':body.get('scenario',settings.get('load_scenario','baseline'))},body.get('weather_year'),month,body.get('hour'))
                else:
                    if body.get('mode') in ('forecast_2027', 'forecast'):result=engine.dayhour_forecast(settings,body.get('date'),body.get('hour'),body.get('scenario'),body.get('representative','median'))
                    else:result=engine.dayhour({**settings,'load_scenario':body.get('scenario',settings.get('load_scenario','baseline'))},body.get('weather_year'),body.get('date'),body.get('hour'))
            elif path=='/api/fair-price':result=engine.fair_price(settings,body.get('metric','npv'),body.get('target',0))
            elif path=='/api/sweep':result=engine.sweep(settings)
            elif path=='/api/optimize-configurations':result=engine.optimize_configuration(settings,body.get('search'))
            elif path=='/api/battery-sizing':result=engine.battery_sizing(settings,body.get('options'),body.get('quote'))
            elif path=='/api/solar-sizing':result=engine.solar_sizing(settings,body.get('options'),body.get('quote'))
            elif path=='/api/solar-sizing-sensitivity':result=engine.solar_sizing_sensitivity(settings,body.get('options'),body.get('quote'),body.get('sensitivity_options'))
            elif path=='/api/sensitivity':result=engine.sensitivity(settings)
            elif path=='/api/summary-sensitivity':result=engine.summary_sensitivity(settings,body.get('quote'),body.get('scope','solar'),body.get('cash_mode','cash'))
            elif path=='/api/behavior-matrix':result=engine.dispatch_matrix(settings,body.get('mode','forecast_2027'),body.get('scenario'),body.get('representative','median'))
            elif path=='/api/study':
                engine.validate(settings)
                with JOB_LOCK:
                    if JOB['state']=='running':return self.send({'error':'A study is already running'},409)
                    if RESTART_SCHEDULED:return self.send({'error':'The server is reloading; retry after it comes back online.'},409)
                    JOB.update(state='running',progress='Starting candidates')
                    threading.Thread(target=run_study,args=(settings,),daemon=True).start()
                return self.send(JOB)
            else:return self.send({'error':'Not found'},404)
            result['compute_seconds']=time.perf_counter()-start;return self.send(result)
        except (ValueError,KeyError,TypeError) as ex:return self.send({'error':str(ex)},400)
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as ex:traceback.print_exc();return self.send({'error':str(ex)},500)

class Handler(LegacyLocalHandler):
    """Session-authenticated HTTP adapter; no legacy fallback."""
    def _route(self, method):
        from feasibility.api import TooLarge
        from feasibility.tenant_store import StoreError
        from feasibility.sessions import guard_host
        service = getattr(self.server, 'service', None)
        if service is None:
            return self.send({'error': 'Service unavailable'}, 503)
        self.connection.settimeout(getattr(self.server, 'body_timeout', 10))
        try:
            local = self.connection.getsockname()[0]
            authority = f'[{local}]:{self.server.server_port}' if ':' in local else f'{local}:{self.server.server_port}'
            service.check_transport(self.headers, self.client_address[0], authority)
            if self.headers.get_all('Transfer-Encoding', []):
                raise StoreError()
            raw = b''
            if method == 'GET' and self.headers.get_all('Content-Length', []) not in ([], ['0']):
                raise StoreError()
            if method != 'GET':
                lengths = self.headers.get_all('Content-Length', [])
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 12:
                    raise StoreError()
                length = int(lengths[0])
                upload = self.path.split('?')[0].endswith(('/consumption', '/market-catalog')) or self.path.split('?')[0] == '/api/evidence/datasets'
                if self.path.split('?')[0] == '/api/evidence/datasets':
                    from urllib.parse import unquote
                    if Path(unquote(self.headers.get('X-Dataset-Filename', ''))).suffix.lower() == '.json':
                        upload = False
                limit = service.limits['upload_bytes' if upload else 'json_bytes']
                if length > limit:
                    raise TooLarge()
                if length == 0 and method != 'DELETE':
                    raise StoreError()
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise StoreError()
            path = urlsplit(self.path).path
            if method == 'GET' and path in STATIC_ASSETS and STATIC_ASSETS[path].startswith('web/'):
                target = HERE / STATIC_ASSETS[path]
                if target.is_file():
                    return self.send(target.read_bytes(), kind=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
            response = service.request(method, self.path, headers=self.headers, body=raw, client_ip=self.client_address[0],
                                       _transport_checked=True, local_authority=authority)
            headers = dict(response.headers)
            kind = headers.pop('Content-Type', 'application/json')
            headers['Content-Security-Policy'] = "default-src 'self'; object-src 'none'; frame-ancestors 'none'"
            return self.send(response.body, response.status, kind=kind, headers=headers)
        except StoreError as exc:
            self.close_connection = True
            return self.send({'error': exc.message}, exc.status, headers={'Retry-After': '60'} if exc.status == 429 else None)
        except (TimeoutError, ConnectionError):
            self.close_connection = True
            return self.send({'error': 'Request body incomplete'}, 400)

    def do_GET(self):
        return self._route('GET')

    def do_POST(self):
        return self._route('POST')

    def do_PATCH(self):
        return self._route('PATCH')

    def do_DELETE(self):
        return self._route('DELETE')


if __name__=='__main__':
    import signal
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    ap=argparse.ArgumentParser();ap.add_argument('--host',default='127.0.0.1');ap.add_argument('--port',type=int,default=8000);ap.add_argument('--allowed-host',action='append',default=[]);ap.add_argument('--local-admin-restart',action='store_true');ap.add_argument('--price-catalog',type=Path);args=ap.parse_args()
    from feasibility.tenant_store import TenantStore
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    server.instance_id=INSTANCE_ID
    configure_tenant_storage(server, TenantStore(), allowed_hosts=args.allowed_host, local_admin_restart=args.local_admin_restart,
                             catalog_root=args.price_catalog.resolve(strict=True) if args.price_catalog else None)
    print(f'PV Lab ready at http://{args.host}:{args.port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.pool.close()
        server.server_close()
    if RESTART_SCHEDULED:
        os.execv(sys.executable,restart_exec_argv())
