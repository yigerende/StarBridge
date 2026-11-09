import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest

import psycopg2
from psycopg2 import sql

ROOT=Path(__file__).resolve().parents[1]
VENDOR=ROOT/'services/gpt-register'
spec=importlib.util.spec_from_file_location('registration_migrate',ROOT/'scripts/registration_migrate.py')
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
BASE='postgresql://gptreg_test@127.0.0.1:18983/'


class RegistrationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.admin=psycopg2.connect(BASE+'postgres',connect_timeout=5);self.admin.autocommit=True
        self.names=['registration_migration_'+secrets.token_hex(6) for _ in range(2)]
        for name in self.names:
            with self.admin.cursor() as cur:cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        self.source,self.target=[BASE+name for name in self.names]
        with psycopg2.connect(self.target) as conn,conn.cursor() as cur:
            cur.execute((ROOT/'backend/schema.sql').read_text(encoding='utf-8'))
            cur.execute("INSERT INTO users(id,email,name,password_hash,invite_code,balance,frozen) VALUES('existing','alice@example.test','Alice','keep-existing-password','keep-invite',1000,200)")
            cur.execute("INSERT INTO settings VALUES('payment','{\"marker\":\"preserve-production\"}')")
        script="""const {Database}=require('./src/platform/db');
          const db=new Database(process.env.DATABASE_URL);
          db.initialize().then(()=>db.close()).catch(e=>{console.error(e.message);process.exit(1)});"""
        for url,integrated in [(self.source,'0'),(self.target,'1')]:
            subprocess.run(['node','-e',script],cwd=VENDOR,env={**os.environ,'DATABASE_URL':url,'XUFU_INTEGRATION':integrated},
                           capture_output=True,check=True,timeout=30)
        with psycopg2.connect(self.source) as conn,conn.cursor() as cur:
            cur.execute("INSERT INTO platform_users(id,email,name,password_hash,points) VALUES('old-alice','alice@example.test','old Alice','source-password',25),('old-bob','bob@example.test','Bob','legacy-hash',20)")
            cur.execute("UPDATE platform_settings SET value=value||'{\"orders_open\":false,\"worker_enabled\":false,\"price_standard\":5,\"price_oauth\":8}'")

    def tearDown(self):
        for name in self.names:
            with self.admin.cursor() as cur:
                cur.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s',(name,))
                cur.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        self.admin.close()

    def test_import_preserves_existing_wallet_and_config_and_is_idempotent(self):
        with psycopg2.connect(self.source) as conn,conn.cursor() as cur:
            cur.execute("UPDATE platform_users SET invited_by='old-alice' WHERE id='old-bob'")
        result=migration.migrate(self.source,self.target)
        self.assertTrue(result['copied']);self.assertEqual(result['users'],2)
        with psycopg2.connect(self.target) as conn,conn.cursor() as cur:
            cur.execute("SELECT balance,frozen,password_hash,invite_code FROM users WHERE id='existing'")
            self.assertEqual(cur.fetchone(),(3500,200,'keep-existing-password','keep-invite'))
            cur.execute("SELECT balance,password_hash FROM users WHERE email='bob@example.test'")
            self.assertEqual(cur.fetchone(),(2000,'legacy-hash'))
            cur.execute("SELECT invited_by FROM users WHERE email='bob@example.test'")
            self.assertEqual(cur.fetchone()[0],'existing')
            cur.execute("SELECT value->>'marker' FROM settings WHERE key='payment'")
            self.assertEqual(cur.fetchone()[0],'preserve-production')
            cur.execute("SELECT value->>'price_standard' FROM platform_settings WHERE id=1")
            self.assertEqual(cur.fetchone()[0],'5')
        self.assertTrue(migration.migrate(self.source,self.target)['already_migrated'])
        with psycopg2.connect(self.target) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM ledger');self.assertEqual(cur.fetchone()[0],2)

    def test_source_must_be_paused_and_failed_import_rolls_back(self):
        with psycopg2.connect(self.source) as conn,conn.cursor() as cur:
            cur.execute("UPDATE platform_settings SET value=jsonb_set(value,'{orders_open}','true')")
        with self.assertRaisesRegex(RuntimeError,'Pause source'):migration.migrate(self.source,self.target)
        with psycopg2.connect(self.target) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM platform_users');self.assertEqual(cur.fetchone()[0],0)
            cur.execute("SELECT balance FROM users WHERE id='existing'");self.assertEqual(cur.fetchone()[0],1000)

    def test_inconsistent_source_freeze_rolls_back_all_imports_and_credits(self):
        with psycopg2.connect(self.source) as conn,conn.cursor() as cur:cur.execute("UPDATE platform_users SET frozen=1 WHERE id='old-alice'")
        with self.assertRaisesRegex(RuntimeError,'frozen balance'):migration.migrate(self.source,self.target)
        with psycopg2.connect(self.target) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM registration_migrations');self.assertEqual(cur.fetchone()[0],0)
            cur.execute('SELECT count(*) FROM platform_users');self.assertEqual(cur.fetchone()[0],0)
            cur.execute("SELECT balance,frozen FROM users WHERE id='existing'");self.assertEqual(cur.fetchone(),(1000,200))

    def test_integer_order_and_ledger_migrate_to_decimal_columns_without_value_changes(self):
        with psycopg2.connect(self.source) as conn,conn.cursor() as cur:
            cur.execute("""INSERT INTO platform_orders(id,user_id,quantity,need_oauth,unit_price,status,charged,released,delivered,source,request_key,request_hash,template,attempt_limit,timeout_minutes)
                VALUES('old-order','old-alice',2,false,5,'partial',5,5,1,'web','old-key','old-hash','{}',2,20)""")
            cur.execute("INSERT INTO platform_ledger(id,user_id,reference,kind,points_delta,frozen_delta,note,points_after,frozen_after) VALUES('old-entry','old-alice','old-settled','charge',0,-5,'old-charge',25,0)")
        result=migration.migrate(self.source,self.target)
        self.assertTrue(result['copied'])
        with psycopg2.connect(self.target) as conn,conn.cursor() as cur:
            cur.execute("SELECT unit_price,charged,released,price_snapshot FROM platform_orders WHERE id='old-order'")
            self.assertEqual(cur.fetchone(),(5,5,5,None))
            cur.execute("SELECT points_delta,frozen_delta,points_after FROM platform_ledger WHERE id='old-entry'")
            self.assertEqual(cur.fetchone(),(0,-5,25))

    def test_runtime_preserves_key_and_refuses_existing_different_configuration(self):
        with tempfile.TemporaryDirectory(prefix='registration-runtime-test-') as directory:
            source=Path(directory)/'source';source.mkdir()
            (source/'platform-secrets.json').write_text(json.dumps({'master':'synthetic-test-key'}))
            (source/'gpt-re.config.json').write_text('{"test":"preserved"}')
            target=Path(directory)/'target'
            manifest=migration.copy_runtime(source,target)
            self.assertEqual(migration.runtime_manifest(target),manifest)
            self.assertEqual(migration.copy_runtime(source,target),manifest)
            (target/'gpt-re.config.json').write_text('{"test":"different"}')
            with self.assertRaisesRegex(RuntimeError,'refusing to overwrite'):migration.copy_runtime(source,target)

    def test_runtime_change_before_commit_rolls_back_wallet_and_data(self):
        def changed():raise RuntimeError('runtime changed')
        with self.assertRaisesRegex(RuntimeError,'runtime changed'):
            migration.migrate(self.source,self.target,before_commit=changed)
        with psycopg2.connect(self.target) as conn,conn.cursor() as cur:
            cur.execute('SELECT count(*) FROM platform_users');self.assertEqual(cur.fetchone()[0],0)
            cur.execute("SELECT balance FROM users WHERE id='existing'");self.assertEqual(cur.fetchone()[0],1000)


if __name__=='__main__':unittest.main()
