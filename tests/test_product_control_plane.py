from __future__ import annotations
import json, threading
from llm_budget_gateway.control_plane import ControlPlane, PermissionDenied, PolicyDenied

def cp(tmp_path): return ControlPlane(str(tmp_path/'control.db'), clock=lambda: 1_700_000_000)

def test_admin_dashboard_and_audit(tmp_path):
 c=cp(tmp_path); c.configure_workspace('t1','admin','Production'); d=c.dashboard('t1','viewer')
 assert d['workspace']['name']=='Production'; assert d['setup']['complete'] is False; assert c.audit_events('t1')

def test_key_lifecycle_rbac_hashes_and_rotates(tmp_path):
 c=cp(tmp_path); issued=c.issue_key('t1','admin','Build bot',['gpt-4o'],expires_at=1_700_000_100,idempotency_key='x')
 assert issued['secret'].startswith('gw_'); assert issued==c.issue_key('t1','admin','Build bot',['gpt-4o'],expires_at=1_700_000_100,idempotency_key='x')
 assert issued['secret'] not in json.dumps(c.list_keys('t1','admin'))
 replacement=c.rotate_key('t1','admin',issued['id'],overlap_seconds=30); assert replacement['id']!=issued['id']
 c.revoke_key('t1','admin',issued['id']); assert c.authenticate(issued['secret']) is None

def test_budget_reservation_is_atomic_and_reconciles(tmp_path):
 c=cp(tmp_path); c.set_budget('t1','admin','global',10.0)
 wins=[]
 def run(i):
  try: wins.append(c.reserve('t1','key1','r'+str(i),6.0)['id'])
  except ValueError: pass
 ts=[threading.Thread(target=run,args=(i,)) for i in range(2)]; [t.start() for t in ts]; [t.join() for t in ts]
 assert len(wins)==1
 c.reconcile(wins[0],4.0); assert c.budget_status('t1','global')['spent']==4.0

def test_spend_observability_alerts_and_export(tmp_path):
 c=cp(tmp_path); c.set_budget('t1','admin','global',10.0); r=c.reserve('t1','k','req',8.0); c.reconcile(r['id'],8.0,model='gpt-4o',latency_ms=120)
 c.create_alert('t1','admin','high spend',0.7,'webhook:test')
 assert c.evaluate_alerts('t1')[0]['state']=='triggered'; assert 'gpt-4o' in c.export_spend_csv('t1','viewer')

def test_policy_routing_fail_closed_and_redacts(tmp_path):
 c=cp(tmp_path); c.put_policy('t1','security','eu-only',{'blocked_terms':['secret'],'allowed_models':['gpt-4o'],'regions':['eu']})
 assert c.evaluate_policy('t1','gpt-4o','hello','eu')['allowed']
 try: c.evaluate_policy('t1','gpt-4o','my secret','eu')
 except PolicyDenied as e: assert e.code=='blocked_content'
 else: raise AssertionError('policy must deny')
 assert 'secret' not in json.dumps(c.policy_decisions('t1','auditor')).lower()

def test_health_routing_circuit_breaker_and_cache(tmp_path):
 c=cp(tmp_path); c.put_route('t1','operator','chat',[{'name':'a','weight':1,'region':'eu'},{'name':'b','weight':1,'region':'eu'}],cache_ttl=60)
 assert c.choose_deployment('t1','chat','req1')['name'] in {'a','b'}
 c.record_deployment_result('t1','chat','a',False); c.record_deployment_result('t1','chat','a',False); c.record_deployment_result('t1','chat','a',False)
 assert c.choose_deployment('t1','chat','req2')['name']=='b'
 c.cache_put('t1','chat','hash','answer'); assert c.cache_get('t1','chat','hash')=='answer'

def test_control_ui_accessibility_and_responsive_contract():
 from llm_budget_gateway.control_api import UI
 assert "lang='en'" in UI and "href='#content'" in UI
 assert "aria-live='polite'" in UI and ':focus-visible' in UI
 assert '@media(max-width:850px)' in UI and "aria-current='page'" in UI

def test_versioned_admin_api_contract(tmp_path):
 from llm_budget_gateway.control_api import create_control_app
 app=create_control_app(str(tmp_path/'api.db'))
 paths={r.path for r in app.routes}
 assert {'/control','/v1/admin/dashboard','/v1/admin/keys','/v1/admin/budgets/{scope}','/v1/admin/spend.csv'} <= paths
