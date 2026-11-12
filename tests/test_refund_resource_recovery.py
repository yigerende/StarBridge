"""Isolated PostgreSQL and fake upstream; no customer resource is contacted."""
import base64
import os
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg2
from psycopg2 import sql
from fastapi.testclient import TestClient
from backend import db, refund_resource_recovery as recovery
from backend.common import now, digest
from backend.refund_mother_upstream import seal_secret, UpstreamError
from backend.main import app
from backend.seed import DEFAULTS


class FakeTeam:
    members = []
    invites = []
    remaining = 1
    error = None
    no_effect = False
    calls = []

    def __init__(self, at, team, proxy, heartbeat=None):
        assert at == 'mother-token' and team == 'fixture-team'
        assert proxy == 'http://mother-proxy.invalid:8080', 'must use the bound mother proxy'
        self.heartbeat = heartbeat

    def close(self): pass
    def path(self, path): return path
    def capacity(self):
        if self.heartbeat: self.heartbeat()
        if self.error: raise self.error
        return {'standard': {'remaining': self.remaining}, 'premium': {'remaining': self.remaining}}
    def collection(self, kind):
        if self.heartbeat: self.heartbeat()
        return list(self.members if kind == 'users' else self.invites)
    def request(self, method, path, body=None):
        if self.heartbeat: self.heartbeat()
        self.calls.append((method, path, body))
        if not self.no_effect:
            if path == '/invites': type(self).invites = []
            else: type(self).members = []
            type(self).remaining = 1
        return {}


class ResourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'resource_test_' + secrets.token_hex(8)
        self.conn = psycopg2.connect('postgresql://gptreg_test@127.0.0.1:18983/postgres', connect_timeout=5)
        self.conn.autocommit = True
        with self.conn.cursor() as c:
            c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.env = patch.dict(os.environ, {'DATABASE_URL': 'postgresql://gptreg_test@127.0.0.1:18983/postgres?options=-csearch_path%3D'+self.schema,
                                          'MASTER_KEY': base64.b64encode(secrets.token_bytes(32)).decode()})
        self.env.start()
        with db.transaction() as q:
            for name in ('schema.sql', 'refund_schema.sql', 'refund_mother_schema.sql'):
                q.cursor.execute((Path(__file__).parents[1]/'backend'/name).read_text(encoding='utf-8'))
        for key, value in DEFAULTS.items():
            db.execute('INSERT INTO settings(key,value) VALUES($1,$2) ON CONFLICT DO NOTHING',key,value)
        db.execute("INSERT INTO users(id,email,name,password_hash,invite_code,role,balance) VALUES('admin','admin@example.test','Admin','fixture','admin','admin',10000),('user','user@example.test','User','fixture','user','member',10000)")
        for user in ('admin','user'):
            db.execute("INSERT INTO sessions(hash,user_id,expires_at) VALUES($1,$2,now()+interval '1 hour')",digest('fixture-'+user),user)
        db.execute("INSERT INTO refund_proxies(id,name,secret,target) VALUES('proxy','Mother proxy',$1,'mother')",seal_secret('proxy:proxy','http://mother-proxy.invalid:8080'))
        db.execute("INSERT INTO refund_mothers(id,name,email,team_id,proxy_id,secret) VALUES('mother','Mother A','mother@example.test','fixture-team','proxy',$1)",seal_secret('mother:mother','mother-token'))
        self.refund('order', 'child@example.test')
        FakeTeam.members=[]; FakeTeam.invites=[]; FakeTeam.calls=[]; FakeTeam.remaining=1; FakeTeam.error=None; FakeTeam.no_effect=False
        self.fake=patch.object(recovery,'TeamClient',FakeTeam);self.fake.start()
        self.maintenance=patch.object(recovery.deployment,'maintenance_active',return_value=False);self.maintenance.start()

    def tearDown(self):
        self.fake.stop();self.maintenance.stop();db.close();self.env.stop()
        with self.conn.cursor() as c:c.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.conn.close()

    def refund(self, id, email):
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status) VALUES($1,'user',$2,decode('00','hex'),'failed',100,'released')",id,email)

    def task(self, state='watching', id='task', email='child@example.test', user_id='child-id', refund_id='order'):
        db.execute("""INSERT INTO refund_resource_tasks(id,refund_id,mother_id,team_id,email,user_id,state,watch_until)
          VALUES($1,$2,'mother','fixture-team',$3,$4,$5,now()-interval '1 second')""",id,refund_id,email,user_id,state)

    def run_task(self):
        db.execute("UPDATE refund_resource_tasks SET next_at=now()-interval '1 second'")
        task = recovery.claim();self.assertIsNotNone(task)
        recovery.run(task)
        return db.one('SELECT * FROM refund_resource_tasks WHERE id=$1',task['id'])

    def test_late_join_restarts_observation_and_manual_recovery(self):
        self.task();db.execute("UPDATE refund_resource_tasks SET clean_checks=1")
        FakeTeam.members=[{'email':'child@example.test','id':'child-id','role':'member'}];FakeTeam.remaining=0
        row=self.run_task()
        self.assertEqual(row['state'],'watching');self.assertEqual(row['clean_checks'],1)
        self.assertGreater(row['watch_until'],now());self.assertEqual(len(FakeTeam.calls),1)
        db.execute("UPDATE refund_resource_tasks SET watch_until=now()-interval '1 second'")
        self.assertEqual(self.run_task()['state'],'done')
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)
        self.assertEqual(db.one("SELECT status,fee_status FROM refunds WHERE id='order'"),dict(status='failed',fee_status='released'))

    def test_delete_200_with_residual_member_never_reports_clean(self):
        self.task('cleaning');FakeTeam.members=[{'email':'child@example.test','id':'child-id','role':'member'}]
        FakeTeam.no_effect=True;FakeTeam.remaining=0
        for _ in range(3):row=self.run_task()
        self.assertEqual(row['state'],'cleaning');self.assertEqual(row['clean_checks'],0)
        self.assertEqual(row['attempts'],3);self.assertGreater(row['next_at'],now()+timedelta(seconds=290))
        # The operator removed the member elsewhere; observe without repeating DELETE.
        FakeTeam.members=[];FakeTeam.remaining=1;before=len(FakeTeam.calls)
        self.run_task();db.execute("UPDATE refund_resource_tasks SET watch_until=now()-interval '1 second'")
        self.assertEqual(self.run_task()['state'],'done');self.assertEqual(len(FakeTeam.calls),before)

    def test_unknown_submission_stays_read_only_even_if_member_reappears(self):
        self.task('review')
        self.assertEqual(self.run_task()['state'],'review')
        FakeTeam.members=[{'email':'child@example.test','id':'child-id','role':'member'}]
        self.assertEqual(self.run_task()['state'],'review');self.assertEqual(FakeTeam.calls,[])
        FakeTeam.members=[];self.run_task()
        self.assertEqual(self.run_task()['state'],'done');self.assertEqual(FakeTeam.calls,[])

    def test_null_or_changed_email_uses_verified_id_and_owner_is_protected(self):
        for email in (None,'changed@example.test'):
            self.task('cleaning',id=str(email))
            FakeTeam.members=[{'email':email,'id':'child-id','role':'member'}]
            self.run_task();self.assertFalse(FakeTeam.members)
            db.execute('DELETE FROM refund_resource_tasks')
        self.task('cleaning');FakeTeam.members=[{'email':None,'id':'child-id','role':'account-owner'}]
        before=len(FakeTeam.calls)
        self.assertEqual(self.run_task()['state'],'cleaning');self.assertEqual(len(FakeTeam.calls),before)

    def test_live_order_or_management_lease_prevents_cleanup_and_claim(self):
        self.task('cleaning')
        db.execute("INSERT INTO refund_execution(refund_id,mother_id,step) VALUES('order','mother','login')")
        self.assertIsNone(recovery.claim())
        db.execute("UPDATE refund_execution SET released=true")
        db.execute("INSERT INTO refund_mother_leases VALUES('fixture-team','manual',now()+interval '1 minute')")
        self.assertIsNone(recovery.claim())
        db.execute('DELETE FROM refund_mother_leases')
        with ThreadPoolExecutor(max_workers=8) as pool:claims=list(pool.map(lambda _:recovery.claim(),range(8)))
        self.assertEqual(sum(t is not None for t in claims),1)
        db.execute("UPDATE refund_resource_tasks SET lease_until=now()-interval '1 second'")
        self.assertIsNotNone(recovery.claim())

    def test_disabled_mother_still_cleans_and_missing_proxy_never_connects(self):
        self.task('cleaning');db.execute("UPDATE refund_mothers SET enabled=false")
        self.assertEqual(self.run_task()['state'],'watching')
        db.execute("UPDATE refund_proxies SET enabled=false")
        row=self.run_task();self.assertEqual(row['state'],'cleaning');self.assertEqual(FakeTeam.calls,[])

    def test_other_team_live_identity_is_not_removed_through_changed_email(self):
        self.task('cleaning')
        self.refund('other','changed@example.test')
        db.execute("INSERT INTO refund_mothers(id,name,email,team_id,proxy_id,secret) VALUES('other','Mother B','other@example.test','other-team','proxy',$1)",seal_secret('mother:other','other-token'))
        db.execute("INSERT INTO refund_execution(refund_id,mother_id,step,child_user_id) VALUES('other','other','login','child-id')")
        FakeTeam.members=[{'email':'changed@example.test','id':'child-id','role':'member'}]
        self.assertEqual(self.run_task()['state'],'cleaning');self.assertEqual(FakeTeam.calls,[])

    def test_maintenance_and_expired_task_token_cannot_start_mutation(self):
        self.task('cleaning')
        with patch.object(recovery.deployment,'maintenance_active',return_value=True):
            self.assertIsNone(recovery.claim());recovery.tick()
        task=recovery.claim();db.execute("UPDATE refund_resource_tasks SET lease_token='new-owner'")
        FakeTeam.members=[{'email':'child@example.test','id':'child-id','role':'member'}]
        recovery.run(task)
        self.assertEqual(FakeTeam.calls,[])
        self.assertEqual(db.scalar('SELECT lease_token FROM refund_resource_tasks'),'new-owner')

    def test_failed_snapshot_and_full_capacity_do_not_free_resource(self):
        self.task();FakeTeam.error=UpstreamError('upstream unavailable',503)
        self.assertEqual(self.run_task()['state'],'cleaning')
        FakeTeam.error=None;FakeTeam.remaining=0
        self.assertEqual(self.run_task()['state'],'cleaning')
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)

    def test_health_probe_persists_cleanup_before_delete_and_keeps_manual_disabled(self):
        db.execute("INSERT INTO refund_execution(refund_id,mother_id,released) VALUES('order','mother',true)")
        db.execute("INSERT INTO refund_resource_health(scope,blocked,next_at,reason) VALUES('fixture-team',true,now(),'mother_capacity')")
        db.execute("UPDATE refund_mothers SET enabled=false")
        FakeTeam.members=[{'email':'child@example.test','id':'child-id','role':'member'}];FakeTeam.remaining=0
        recovery.probe_health();self.assertEqual(FakeTeam.calls,[])
        self.assertEqual(db.scalar('SELECT count(*) FROM refund_resource_tasks'),1)
        self.run_task();db.execute("UPDATE refund_resource_tasks SET watch_until=now()-interval '1 second'")
        self.run_task();db.execute("UPDATE refund_resource_health SET next_at=now()")
        recovery.probe_health();self.assertEqual(db.scalar('SELECT count(*) FROM refund_resource_health'),0)
        self.assertFalse(db.scalar("SELECT enabled FROM refund_mothers WHERE id='mother'"))

    def test_admin_api_pagination_permissions_and_no_credentials(self):
        for i in range(12):self.task(id=f't{i:02}',email=f'child{i:02}@example.test')
        client=TestClient(app,cookies={'xupay_session':'fixture-admin'})
        url='/api/admin/refund/resource-recovery'
        response=client.get(url,params={'page':2,'page_size':10,'sort_by':'email','sort_dir':'asc'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['total'],12);self.assertEqual(len(response.json()['items']),2)
        self.assertNotIn('mother-token',response.text);self.assertNotIn('proxy.invalid',response.text)
        self.assertEqual(client.post(url+'/t00/retry',json={}).status_code,200)
        db.execute("INSERT INTO refund_resource_health(scope,blocked,next_at,reason) VALUES('fixture-team',true,now()+interval '5 minutes','mother_capacity')")
        self.assertEqual(client.post(url+'/health/fixture-team/retry',json={}).status_code,200)
        self.assertLessEqual(db.scalar('SELECT next_at FROM refund_resource_health'),now())
        self.assertEqual(TestClient(app,cookies={'xupay_session':'fixture-user'}).post(url+'/health/fixture-team/retry',json={}).status_code,403)
        self.assertEqual(TestClient(app,cookies={'xupay_session':'fixture-user'}).get(url).status_code,403)
        self.assertEqual(TestClient(app).get(url).status_code,401)


if __name__=='__main__':unittest.main()
