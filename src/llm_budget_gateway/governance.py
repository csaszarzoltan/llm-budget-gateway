"""Governance, compliance, FinOps, automation and privacy domain service."""
from __future__ import annotations
import hashlib,json,secrets,sqlite3,time
from typing import Callable,Mapping,Sequence
class PermissionDenied(Exception): pass
_LEVEL={'viewer':0,'auditor':1,'operator':2,'privacy':3,'admin':4}
class GovernanceService:
 def __init__(self,path:str,clock:Callable[[],int]|None=None):
  self.clock=clock or (lambda:int(time.time()));self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
  self.db.executescript('''PRAGMA foreign_keys=ON;
  CREATE TABLE IF NOT EXISTS membership(tenant TEXT,user TEXT,role TEXT,PRIMARY KEY(tenant,user));
  CREATE TABLE IF NOT EXISTS object(id TEXT PRIMARY KEY,tenant TEXT,kind TEXT,state TEXT,payload TEXT,evidence TEXT,created INTEGER);
  CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,tenant TEXT,control TEXT,payload TEXT,created INTEGER);
  CREATE TABLE IF NOT EXISTS privacy(tenant TEXT PRIMARY KEY,retention_days INTEGER,regions TEXT);
  CREATE TABLE IF NOT EXISTS record(id TEXT PRIMARY KEY,tenant TEXT,region TEXT,kind TEXT,payload TEXT,created INTEGER);
  CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,tenant TEXT,actor TEXT,action TEXT,object_id TEXT,state TEXT,created INTEGER);''');self.db.commit()
 def _need(self,role,minimum):
  if _LEVEL.get(role,-1)<_LEVEL[minimum]:raise PermissionDenied(minimum)
 def _audit(self,t,a,action,obj,state):self.db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?)',(secrets.token_hex(8),t,a,action,obj,state,self.clock()));self.db.commit()
 def add_membership(self,t,actor,user,role):
  self._need(actor,'admin');
  if role not in _LEVEL:raise ValueError('invalid role')
  self.db.execute('INSERT OR REPLACE INTO membership VALUES(?,?,?)',(t,user,role));self.db.commit();self._audit(t,actor,'membership.set',user,'active')
 def authorize(self,t,user,required):
  row=self.db.execute('SELECT role FROM membership WHERE tenant=? AND user=?',(t,user)).fetchone()
  if not row or _LEVEL[row[0]]<_LEVEL[required]:raise PermissionDenied(required)
  return True
 def propose(self,t,role,kind,payload,evidence):
  self._need(role,'operator');oid=secrets.token_hex(8);safe={k:v for k,v in payload.items() if k not in {'secret','prompt'}}
  self.db.execute('INSERT INTO object VALUES(?,?,?,?,?,?,?)',(oid,t,kind,'proposed',json.dumps(safe),evidence,self.clock()));self.db.commit();self._audit(t,role,'recommendation.propose',oid,'proposed');return {'id':oid,'state':'proposed'}
 def approve(self,t,role,oid):self._need(role,'admin');self.db.execute("UPDATE object SET state='approved' WHERE tenant=? AND id=? AND state='proposed'",(t,oid));self.db.commit();self._audit(t,role,'recommendation.approve',oid,'approved')
 def get_recommendation(self,t,oid):
  row=self.db.execute('SELECT id,kind,state,payload,evidence FROM object WHERE tenant=? AND id=?',(t,oid)).fetchone()
  if not row:raise KeyError(oid)
  return dict(row)|{'payload':json.loads(row['payload'])}
 def record_evidence(self,t,role,control,payload):
  self._need(role,'admin');clean={k:v for k,v in payload.items() if k not in {'prompt','secret','authorization'}};eid=secrets.token_hex(8)
  self.db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',(eid,t,control,json.dumps(clean,sort_keys=True),self.clock()));self.db.commit();return eid
 def evidence_package(self,t,role):
  self._need(role,'auditor');items=[dict(x) for x in self.db.execute('SELECT id,control,payload,created FROM evidence WHERE tenant=? ORDER BY id',(t,))];raw=json.dumps(items,sort_keys=True,separators=(',',':'));return {'tenant':t,'items':items,'sha256':hashlib.sha256(raw.encode()).hexdigest()}
 def forecast(self,values:Sequence[float],budget:float):
  if not values or budget<0 or any(x<0 for x in values):raise ValueError('non-negative history and budget required')
  total=sum(values);base=sum(values[:-1])/max(1,len(values)-1);last=values[-1];anomaly=len(values)>2 and base>0 and last>=base*2
  return {'total':total,'remaining':max(0,budget-total),'anomaly':anomaly,'forecast_next':last,'explanation':f'Latest {last:.2f}; prior mean {base:.2f}.'}
 def reliability_decision(self,t,role,route,failures):
  self._need(role,'operator');payload={'route':route,'failures':failures};x=self.propose(t,role,'reliability',payload,'three-failure threshold' if failures>=3 else 'healthy')
  action='shift_traffic' if failures>=3 else 'keep_route';self.db.execute("UPDATE object SET state='awaiting_approval',payload=? WHERE id=?",(json.dumps(payload|{'action':action}),x['id']));self.db.commit();return {'id':x['id'],'action':action,'state':'awaiting_approval'}
 def rollback(self,t,role,oid):self._need(role,'admin');self.db.execute("UPDATE object SET state='rolled_back' WHERE tenant=? AND id=?",(t,oid));self.db.commit();self._audit(t,role,'object.rollback',oid,'rolled_back')
 def activity(self,t,role):self._need(role,'auditor');return [dict(x) for x in self.db.execute('SELECT * FROM audit WHERE tenant=? ORDER BY rowid DESC',(t,))]
 def set_privacy_policy(self,t,role,retention_days,regions):
  self._need(role,'privacy');
  if retention_days<1 or not regions:raise ValueError('retention and regions required')
  self.db.execute('INSERT OR REPLACE INTO privacy VALUES(?,?,?)',(t,retention_days,json.dumps(sorted(set(regions)))));self.db.commit();self._audit(t,role,'privacy.set',t,'active')
 def store_record(self,t,region,kind,payload):
  p=self.db.execute('SELECT regions FROM privacy WHERE tenant=?',(t,)).fetchone()
  if not p or region not in json.loads(p[0]):raise ValueError('region denied')
  safe={k:v for k,v in payload.items() if k not in {'prompt','secret'}};self.db.execute('INSERT INTO record VALUES(?,?,?,?,?,?)',(secrets.token_hex(8),t,region,kind,json.dumps(safe),self.clock()));self.db.commit()
 def export_tenant(self,t,role):self._need(role,'privacy');return [dict(x) for x in self.db.execute('SELECT id,region,kind,payload,created FROM record WHERE tenant=?',(t,))]
 def delete_expired(self,t,role):
  self._need(role,'privacy');p=self.db.execute('SELECT retention_days FROM privacy WHERE tenant=?',(t,)).fetchone();cut=self.clock()-p[0]*86400;cur=self.db.execute('DELETE FROM record WHERE tenant=? AND created<?',(t,cut));self.db.commit();self._audit(t,role,'retention.delete',t,'complete');return cur.rowcount
