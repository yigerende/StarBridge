"""Disposable PostgreSQL and simulated upstream; never sends live account mutations."""
import base64
from contextlib import contextmanager
from datetime import timedelta
import json
from unittest.mock import patch, AsyncMock
import unittest

from fastapi.testclient import TestClient
from backend import db, refund_mothers as mothers
from backend.common import now, encrypt, BusinessError
from backend.main import app
from backend.refund_mother_upstream import TeamClient, UpstreamError, identity, open_secret, seal_secret
import test_refunds as fixture


def token(email='mother@example.test', user='owner', team='team-fixture', expiry=None):
    value = {'email': email, 'exp': expiry or int((now()+timedelta(hours=1)).timestamp()), 'https://api.openai.com/auth': {'chatgpt_user_id': user, 'chatgpt_account_id': team}}
    return 'e30.' + base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=') + '.fixture'


class FakeClient:
    calls = []
    rosters = {}
    holds = {}
    invites = {}
    free = 1
    lose = False
    refreshes = 0

    def __init__(self, at, team, proxy, heartbeat=None):
        self.team, self.token, self.heartbeat = team, at, heartbeat
        self.proxy = proxy
        self.headers = {'Authorization': 'Bearer ' + at}

    def close(self): pass
    def path(self, path): return '/accounts/' + self.team + path
    def members(self, held=False): return [dict(r) for r in (self.holds if held else self.rosters).get(self.team, [])]
    def collection(self, kind='users'): return self.invites.get(self.team, [])
    def capacity(self): return {'standard': {'total': 1, 'remaining': self.free, 'held': 0}, 'premium': {'total': 3, 'remaining': 1, 'held': len(self.holds.get(self.team, []))}, 'checked_at': now().isoformat()}
    def plan(self): return {'email': identity(self.token)['email'], 'expires_at': (now()+timedelta(days=10)).isoformat(), 'subscription_plan': 'team', 'checked_at': now().isoformat()}
    def refresh(self, rt):
        type(self).refreshes += 1
        who = identity(self.token)
        return {'access_token': token(who['email'], who['user_id'], who['team_id']), 'refresh_token': 'rotated-private'}

    def request(self, method, path, payload=None):
        if self.heartbeat: self.heartbeat()
        if method == 'GET' and path == '/me': return {'email': identity(self.headers['Authorization'][7:])['email']}
        self.calls.append((method, path, payload))
        if path.endswith(('/invites', '/invites/request')):
            self.invites[self.team] = [{'id': 'invite-1', 'email_address': 'child@example.test'}]
        elif path.endswith('/invites/accept') or method == 'PATCH':
            self.rosters[self.team] = [dict(MotherTests.member, active=True, deactivated_time='')]
            self.holds[self.team] = []
        elif path.endswith('/seat/update'):
            self.rosters[self.team][0]['seat_type'] = 'default'
        elif method == 'DELETE':
            self.rosters[self.team] = []
        else: raise AssertionError((method, path))
        if self.lose: raise UpstreamError('response lost')
        return {'success': True}


class MotherTests(unittest.TestCase):
    tearDown = fixture.RefundTests.tearDown
    member = {'id':'child-1','email':'child@example.test','name':'Child','role':'standard-user','seat_type':'prolite',
              'created_time':'2026-01-01T00:00:00Z','active':False,'deactivated_time':'2026-11-11T00:00:00Z','reclaimable_seat_type':'prolite'}

    def setUp(self):
        fixture.RefundTests.setUp(self)
        db.execute("INSERT INTO refund_proxies(id,name,secret,target) VALUES('proxy','Fixture',$1,'both')", seal_secret('proxy:proxy', 'http://fixture.invalid:8080'))
        db.execute("UPDATE refund_settings SET child_proxy_id='proxy'")
        db.execute("INSERT INTO refund_mothers(id,name,email,team_id,secret,refresh_secret,proxy_id,token_expires_at) VALUES('mother','Fixture mother','mother@example.test','team-fixture',$1,$2,'proxy',now()+interval '1 hour')", seal_secret('mother:mother', token()), seal_secret('mother-refresh:mother', 'private-rt'))
        db.execute("INSERT INTO refund_mother_children(email,user_id,credentials_enc) VALUES('child@example.test','child-1',$1)", encrypt(json.dumps({'access_token':token('child@example.test','child-1'), 'refresh_token':'private-child-rt'})))
        FakeClient.calls = []; FakeClient.rosters = {}; FakeClient.holds = {'team-fixture':[dict(self.member)]}; FakeClient.invites = {}; FakeClient.free = 1; FakeClient.lose = False; FakeClient.refreshes = 0
        self.fake = patch.object(mothers, 'TeamClient', FakeClient); self.fake.start(); self.addCleanup(self.fake.stop)
        self.client = TestClient(app, cookies={'xupay_session':'test-session-a'})
        self.user = TestClient(app, cookies={'xupay_session':'test-session-u'})
        self.root = '/api/admin/refund/mother-management'

    def test_admin_only_credentials_and_complete_member_pagination(self):
        for path in (self.root+'/summary',self.root+'/mother/credentials',self.root+'/mother/members'):
            self.assertEqual(self.user.get(path).status_code,403,path)
            self.assertEqual(TestClient(app).get(path).status_code,401,path)
        FakeClient.rosters['team-fixture']=[dict(self.member,id='c'+str(i),email=f'user{i:04}@example.test',active=True) for i in range(525)]
        for size in (10,50,100,500):
            r=self.client.get(self.root+'/mother/members',params={'page_size':size,'sort_by':'email','sort_dir':'desc'})
            self.assertEqual(r.status_code,200,r.text);d=r.json();self.assertEqual(d['total'],525);self.assertEqual(len(d['items']),size);self.assertEqual(d['items'][0]['email'],'user0524@example.test')
        self.assertEqual(len(self.client.get(self.root+'/mother/members?page=2&page_size=500').json()['items']),25)
        self.assertEqual(self.client.get(self.root+'/mother/members?sort_by=secret').status_code,400)
        d=self.client.get(self.root+'/summary');self.assertNotIn('private-rt',d.text);self.assertNotIn(token(),d.text)
        r=self.client.get(self.root+'/mother/credentials');self.assertEqual(r.json()['refresh_token'],'private-rt')
        self.assertEqual(db.scalar("SELECT count(*) FROM audit_events WHERE action='refund.mother.credentials'"),1)
        self.assertEqual(self.client.post(self.root+'/mother/plan',json={}).status_code,200)
        self.assertEqual(self.client.post(self.root+'/mother/capacity',json={}).status_code,200)
        self.assertEqual(self.client.post(self.root+'/mother/refresh',json={}).status_code,200)
        self.assertEqual(self.client.post(self.root+'/mother/refresh',json={}).status_code,200)
        self.assertEqual(FakeClient.refreshes,1)
        self.assertEqual(open_secret('mother-refresh:mother',db.scalar("SELECT refresh_secret FROM refund_mothers WHERE id='mother'")),'rotated-private')

    def test_resource_metrics_use_current_snapshot_and_sort_all_rows(self):
        db.execute("UPDATE refund_mothers SET management_json=$1,check_json=$2 WHERE id='mother'",
                   {'capacity':{'standard':{'total':2,'remaining':0,'held':0},'premium':{'total':2,'remaining':0,'held':0},'checked_at':'2026-01-01T00:00:00Z'}},
                   {'seats':{'standard':{'total':2,'remaining':1,'held':0},'premium':{'total':4,'remaining':3,'held':1}},'checked_at':'2026-02-01T00:00:00Z'})
        db.execute("INSERT INTO refund_mothers(id,name,email,team_id,secret,proxy_id) VALUES('second','Second','second@example.test','second-team',$1,'proxy')", seal_secret('mother:second',token()))
        FakeClient.rosters['team-fixture']=[dict(self.member,active=True,deactivated_time=''),dict(self.member,id='owner',email='mother@example.test',role='account-owner',active=True)]
        self.client.get(self.root+'/mother/members?q=child')
        data=self.client.get(self.root+'/summary').json()['items']
        summary=next(r for r in data if r['id']=='mother')
        self.assertEqual(summary['management_json']['members']['count'],1)
        self.assertEqual(summary['management_json']['capacity']['premium']['remaining'],3)
        self.assertNotIn('check_json',summary)
        with patch('backend.refunds.call',new=AsyncMock(return_value={'mothers':[{'id':'second','name':'Second','enabled':True},{'id':'mother','name':'First','enabled':True}]})):
            path='/api/admin/refund/resources/mothers?sort_by=premium_remaining&sort_dir=desc&page_size=1'
            response=self.client.get(path)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['total'],2)
            self.assertEqual(response.json()['items'][0]['id'],'mother')
            self.assertEqual(response.json()['items'][0]['premium_remaining'],3)
            self.assertEqual(self.client.get(path+'&page=2').json()['items'][0]['id'],'second')
        self.assertEqual(self.client.get('/api/admin/refund/seat-recovery/tasks').status_code,404)

    def test_inline_resource_changes_are_scoped_and_keep_busy_credentials(self):
        path=self.root+'/mother/resource'
        self.assertEqual(self.user.patch(path,json={'enabled':False}).status_code,403)
        self.assertEqual(self.client.patch(path,json={'enabled':'false'}).status_code,400)
        self.assertEqual(self.client.patch(path,json={'name':'stale form'}).status_code,400)
        original=db.one("SELECT secret,refresh_secret FROM refund_mothers WHERE id='mother'")
        db.execute("UPDATE refund_mothers SET check_json='{\"valid\":true}'")
        db.execute("INSERT INTO refund_proxies(id,name,secret,target) VALUES('new-proxy','New',$1,'mother')",seal_secret('proxy:new-proxy','http://new.invalid:8080'))
        with mothers.team_operation('mother'):
            self.assertEqual(self.client.patch(path,json={'enabled':False}).status_code,200)
            self.assertEqual(self.client.patch(path,json={'proxy_id':'new-proxy'}).status_code,409)
        self.assertEqual(db.scalar("SELECT check_json FROM refund_mothers WHERE id='mother'"),{'valid':True})
        self.assertEqual(db.one("SELECT secret,refresh_secret FROM refund_mothers WHERE id='mother'"),original)
        self.assertEqual(self.client.patch(path,json={'proxy_id':'new-proxy'}).status_code,200)
        self.assertEqual(db.scalar("SELECT proxy_id FROM refund_mothers WHERE id='mother'"),'new-proxy')
        self.assertIsNone(db.scalar("SELECT check_json FROM refund_mothers WHERE id='mother'"))
        db.execute("UPDATE refund_proxies SET deleted_at=now() WHERE id='new-proxy'")
        self.assertEqual(self.client.patch(path,json={'enabled':False}).status_code,200)

    def test_management_protects_inflight_resources_and_member_identity(self):
        with mothers.team_operation('mother', child_email='child@example.test'):
            with self.assertRaises(BusinessError):
                with mothers.team_operation('mother'): pass
            self.assertEqual(db.scalar('SELECT count(*) FROM refund_member_leases WHERE expires_at>now()'),1)
        self.assertEqual(db.scalar('SELECT count(*) FROM refund_member_leases'),0)
        FakeClient.rosters['team-fixture']=[dict(self.member,active=True,deactivated_time='',role='account-owner')]
        payload={'email':'child@example.test','user_id':'child-1','team_id':'team-fixture','method':'mother_kick'}
        self.assertEqual(self.user.post(self.root+'/mother/members/remove',json=payload).status_code,403)
        self.assertEqual(self.client.post(self.root+'/mother/members/remove',json=payload).status_code,409)
        FakeClient.rosters['team-fixture'][0]['role']='standard-user'
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,fee,status) VALUES('busy','u','child@example.test',decode('00','hex'),10,'processing')")
        db.execute("INSERT INTO refund_execution(refund_id,mother_id) VALUES('busy','mother')")
        self.assertEqual(self.client.post(self.root+'/mother/refresh',json={}).status_code,409)
        self.assertEqual(self.client.post(self.root+'/mother/members/remove',json=payload).status_code,409)
        self.assertEqual(FakeClient.calls,[])
        db.execute("UPDATE refund_execution SET released=true WHERE refund_id='busy'")
        db.execute("UPDATE refunds SET status='failed' WHERE id='busy'")
        FakeClient.lose=True
        self.assertEqual(self.client.post(self.root+'/mother/members/remove',json=payload).status_code,200)
        self.assertEqual([c[0] for c in FakeClient.calls],['DELETE'])
        self.assertEqual(db.scalar('SELECT count(*) FROM ledger'),0)

    def test_child_import_login_refresh_and_primary_network_failover(self):
        data={'email':'child@example.test','user_id':'child-1','session':json.dumps({'password':'private-password','totp_secret':'fixture-totp'})}
        self.assertEqual(self.user.post(self.root+'/children',json=data).status_code,403)
        r=self.client.post(self.root+'/children',json=data);self.assertEqual(r.status_code,200,r.text)
        saved=db.scalar("SELECT credentials_enc FROM refund_mother_children WHERE email='child@example.test'")
        self.assertNotIn('private-password',saved)
        with patch.object(mothers,'login_child',return_value={'access_token':token('child@example.test','child-1')}) as login:
            with mothers.child_client('child@example.test','child-1','team-fixture',lambda:None):pass
            self.assertEqual(login.call_count,1)
            with mothers.child_client('child@example.test','child-1','team-fixture',lambda:None):pass
            self.assertEqual(login.call_count,1)
        wrong=self.client.post(self.root+'/children',json={**data,'session':token('other@example.test','other')})
        self.assertEqual(wrong.status_code,400)
        self.assertEqual(self.client.post(self.root+'/children',json={'session':'e30.@@@@.fixture'}).status_code,400)
        db.execute("INSERT INTO refund_proxies(id,name,secret,target) VALUES('backup','Backup',$1,'child')",seal_secret('proxy:backup','http://backup.invalid:8080'))
        db.execute("UPDATE refund_settings SET child_proxy_backups=ARRAY['backup']")
        original=FakeClient.request;requests=[]
        def network(client,method,path,payload=None):
            requests.append(client.proxy)
            if client.proxy=='http://fixture.invalid:8080':raise UpstreamError('offline')
            return original(client,method,path,payload)
        with patch.object(FakeClient,'request',new=network):
            with mothers.child_client('child@example.test','child-1','team-fixture',lambda:None) as client:
                self.assertEqual(client.proxy,'http://backup.invalid:8080')
        self.assertEqual(len(requests),2)
        self.assertTrue(db.scalar("SELECT failure_until>now() FROM refund_proxies WHERE id='proxy'"))
        db.execute("UPDATE refund_proxies SET failure_until=NULL")
        with patch.object(FakeClient,'request',side_effect=UpstreamError('forbidden',403)) as forbidden:
            with self.assertRaises(UpstreamError):
                with mothers.child_client('child@example.test','child-1','team-fixture',lambda:None):pass
            self.assertEqual(forbidden.call_count,1)

    def test_unknown_refresh_is_not_replayed(self):
        with patch.object(FakeClient,'refresh',side_effect=UpstreamError('lost refresh response')) as refresh:
            self.assertEqual(self.client.post(self.root+'/mother/refresh',json={}).status_code,502)
            self.assertEqual(self.client.post(self.root+'/mother/refresh',json={}).status_code,400)
            self.assertEqual(refresh.call_count,1)
        db.execute("UPDATE refund_mother_children SET credentials_enc=$1",encrypt(json.dumps({'access_token':token('child@example.test','child-1',expiry=int(now().timestamp())-10),'refresh_token':'rotating-rt'})))
        with patch.object(FakeClient,'refresh',side_effect=UpstreamError('lost refresh response')) as refresh:
            for _ in range(2):
                with self.assertRaises(BusinessError):
                    with mothers.child_client('child@example.test','child-1','team-fixture',lambda:None):pass
            self.assertEqual(refresh.call_count,1)



class UpstreamParsingTests(unittest.TestCase):
    def client(self):
        return TeamClient(token(),'team-fixture','http://fixture.invalid:8080')

    def test_pagination_rejects_partial_or_repeated_rosters(self):
        c=self.client()
        try:
            with patch.object(c,'request',return_value={'items':[{'id':'1','email':'x@example.test'}]*25,'total':26}):
                with self.assertRaises(BusinessError):c.collection()
            with patch.object(c,'request',return_value={'items':[],'total':1}):
                with self.assertRaises(BusinessError):c.collection()
            with patch.object(c,'request',return_value={'seat_capacity':[{'type':'default','paid':1,'held':0,'available':1}]}):
                with self.assertRaises(BusinessError):c.capacity()
        finally:c.close()


if __name__=='__main__':unittest.main()
