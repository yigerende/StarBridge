import unittest
from datetime import datetime, timezone
from test_dashboard import DashboardTests as Fixture
from backend import db, order_timing
from backend.dashboard import overview


class OrderTimingTests(unittest.TestCase):
    setUp=Fixture.setUp
    tearDown=Fixture.tearDown
    sub=Fixture.sub
    reg=Fixture.reg
    ledger=Fixture.ledger

    def test_payment_success_counts_before_followup_and_defaults_to_today(self):
        self.sub('paid',activation='pending',renewal='review')
        self.sub('unknown',status='review')
        self.sub('old',created_at='2020-01-01T00:00:00Z')
        self.sub('admin-paid','admin',renewal='review')
        self.reg('registration')
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status) VALUES('refund','u','x@test.example','unused','succeeded',300,'settled')")
        self.ledger('sub','charge',-500)
        self.ledger('sub-refund','refund',100)
        self.ledger('reg','charge',-123,'registration')
        self.ledger('refund','charge',-300,'refund')
        result=overview(self.admin_user,admin=True)
        self.assertEqual(result['start'],result['end'])
        self.assertEqual(result['totals']['completed'],3)
        self.assertEqual(result['queues']['subscription']['period_completed'],1)
        self.assertEqual(result['queues']['subscription']['period_settled'],400)
        self.assertEqual(result['queues']['registration']['period_completed'],1)
        self.assertEqual(result['queues']['registration']['period_settled'],123)
        self.assertEqual(result['queues']['refund']['period_completed'],1)
        self.assertEqual(result['queues']['refund']['period_settled'],300)
        self.assertEqual(overview(self.admin_user,admin=True,include_admin=True)['totals']['completed'],4)

    def test_subscription_uses_recorded_milestones_and_ignores_later_note_edits(self):
        self.sub('paid',created_at='2026-01-01T00:00:00Z',renewal='review')
        db.execute("INSERT INTO order_events(order_id,stage,message,created_at) VALUES('paid','processing','test','2026-01-01T00:00:10Z')")
        db.execute("INSERT INTO payment_trace_events(order_id,phase,state,code,created_at) VALUES('paid','result','completed','payment_succeeded','2026-01-01T00:00:40Z'),('paid','complete','review','review','2026-01-01T00:01:00Z')")
        rows=[{'id':'paid'}];order_timing.subscriptions(rows)
        timing=rows[0]['timing']
        self.assertEqual(timing['payment_seconds'],30)
        self.assertEqual(timing['total_seconds'],60)
        self.assertFalse(timing['ongoing'])
        db.execute("UPDATE orders SET updated_at=now(),note='edited' WHERE id='paid'")
        order_timing.subscriptions(rows)
        self.assertEqual(rows[0]['timing'],timing)
        db.execute("UPDATE orders SET renewal='retrying' WHERE id='paid'")
        order_timing.subscriptions(rows)
        self.assertTrue(rows[0]['timing']['ongoing'])
        self.assertIsNone(rows[0]['timing']['finished_at'])
        self.assertGreater(rows[0]['timing']['total_seconds'],60)

    def test_unknown_history_and_queued_orders_do_not_fabricate_payment_duration(self):
        self.sub('history');self.sub('queued',status='queued')
        rows=[{'id':'history'},{'id':'queued'}];order_timing.subscriptions(rows)
        self.assertIsNone(rows[0]['timing']['total_seconds'])
        self.assertIsNone(rows[0]['timing']['payment_seconds'])
        self.assertTrue(rows[1]['timing']['ongoing'])
        self.assertIsNone(rows[1]['timing']['payment_seconds'])
        self.assertGreaterEqual(rows[1]['timing']['total_seconds'],0)

    def test_refund_finishing_time_is_immutable_ledger_time_including_zero_fee(self):
        db.execute("INSERT INTO refunds(id,user_id,email,credentials,status,fee,fee_status,created_at) VALUES('r','u','x@test.example','unused','succeeded',0,'settled','2026-01-01T00:00:00Z'),('q','u','q@test.example','unused','queued',0,'frozen',now())")
        db.execute("INSERT INTO ledger(user_id,refund_order_id,kind,amount,balance_after,frozen_after,reference,description,business,created_at) VALUES('u','r','charge',0,1000,0,'refund:r','test','refund','2026-01-01T00:02:00Z')")
        rows=[{'id':'r'},{'id':'q'}];order_timing.refunds(rows)
        self.assertEqual(rows[0]['timing']['total_seconds'],120)
        self.assertFalse(rows[0]['timing']['ongoing'])
        self.assertTrue(rows[1]['timing']['ongoing'])
        self.assertEqual(order_timing.seconds(datetime(2026,1,2,tzinfo=timezone.utc),datetime(2026,1,1,tzinfo=timezone.utc)),None)
