"""One-time, transactional import from a stopped GPT Register deployment."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

SEEDS = {'platform_settings', 'platform_risk_settings', 'platform_risk_scan', 'session_proxy_controls'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def runtime_manifest(directory):
    directory = Path(directory).resolve(strict=True)
    require((directory/'platform-secrets.json').is_file(), 'Registration encryption master key is missing')
    result = {}
    for path in directory.rglob('*'):
        require(not path.is_symlink(), 'Runtime must not contain symbolic links')
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def copy_runtime(source, target):
    source, target = Path(source).resolve(strict=True), Path(target).resolve()
    require(source != target and source not in target.parents and target not in source.parents,
            'Source and target runtime directories must be separate')
    original = runtime_manifest(source)
    if target.exists() and any(target.iterdir()):
        require(runtime_manifest(target) == original, 'Existing target runtime differs; refusing to overwrite')
    else:
        shutil.copytree(source, target, dirs_exist_ok=True)
    if os.name != 'nt':
        for path in [target, *target.rglob('*')]:
            path.chmod(0o700 if path.is_dir() else 0o600)
    require(runtime_manifest(target) == original, 'Runtime copy verification failed')
    return original


def tables(cursor):
    cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() AND (tablename LIKE 'platform_%' OR tablename LIKE 'session_proxy_%') ORDER BY tablename")
    return [row[0] for row in cursor.fetchall()]


def ordered_tables(cursor, names):
    cursor.execute("""SELECT a.relname,b.relname FROM pg_constraint c
        JOIN pg_class a ON a.oid=c.conrelid JOIN pg_class b ON b.oid=c.confrelid
        JOIN pg_namespace n ON n.oid=a.relnamespace
        WHERE c.contype='f' AND n.nspname=current_schema()""")
    edges = cursor.fetchall()
    pending, result = set(names), []
    while pending:
        ready = sorted(name for name in pending if not any(a == name and b in pending and b != name for a, b in edges))
        require(ready, 'Unsupported cyclic registration foreign keys')
        result.extend(ready)
        pending.difference_update(ready)
    return result


def fingerprint(cursor, table, columns):
    cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s AND data_type='numeric'", (table,))
    numeric = {row[0] for row in cursor.fetchall()}
    projected = [sql.SQL('trim_scale({}) AS {}').format(sql.Identifier(name), sql.Identifier(name))
                 if name in numeric else sql.Identifier(name) for name in columns]
    query = sql.SQL("SELECT count(*),md5(coalesce(string_agg(h,'' ORDER BY h),'')) FROM (SELECT md5(row_to_json(t)::text) h FROM (SELECT {} FROM {}) t) hashes")
    cursor.execute(query.format(sql.SQL(',').join(projected), sql.Identifier(table)))
    return cursor.fetchone()


def migrate(source_url, target_url, source_schema='public', target_schema='public', before_commit=None):
    require(source_url != target_url or source_schema != target_schema, 'Source and target must be separate')
    for name in (source_schema, target_schema):
        require(re.fullmatch('[a-zA-Z_][a-zA-Z0-9_]*', name), 'Invalid database schema')
    source = psycopg2.connect(source_url, connect_timeout=5)
    target = psycopg2.connect(target_url, connect_timeout=5)
    try:
        with source, target, source.cursor() as src, target.cursor() as dst:
            src.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(source_schema)))
            dst.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target_schema)))
            for cur in (src, dst):
                cur.execute("SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='120s'")
                cur.execute('SELECT pg_try_advisory_xact_lock(8963002)')
                require(cur.fetchone()[0], 'A registration worker is still running')
            dst.execute('SELECT (SELECT count(*) FROM registration_migrations)+(SELECT count(*) FROM registration_imports)')
            if dst.fetchone()[0]:
                return {'already_migrated': True, 'copied': False}
            names = tables(src)
            require('platform_users' in names and 'platform_orders' in names, 'Source is not GPT Register')
            src.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(sql.SQL(',').join(map(sql.Identifier, names))))
            src.execute("SELECT value->>'orders_open',value->>'worker_enabled' FROM platform_settings WHERE id=1")
            require(src.fetchone() == ('false', 'false'), 'Pause source admission and queue before migrating')
            src.execute("SELECT count(*) FROM platform_items WHERE status IN ('queued','running','oauth_pending','review')")
            require(src.fetchone()[0] == 0, 'Source registration orders have not drained')
            src.execute("SELECT count(*) FROM session_proxy_bindings WHERE status IN ('reserved','active','stopping')")
            require(src.fetchone()[0] == 0, 'Source engine proxy sessions are still active')
            for name in names:
                dst.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name)))
                count = dst.fetchone()[0]
                require(count == 0 or name in SEEDS and count == 1, 'Target registration data is not empty: ' + name)
            for table, trigger in [('platform_orders', 'platform_partner_admission'),
                                   ('platform_orders', 'platform_partner_settlement'),
                                   ('platform_api_keys', 'registration_key_identity')]:
                dst.execute(sql.SQL('ALTER TABLE {} DISABLE TRIGGER {}').format(sql.Identifier(table), sql.Identifier(trigger)))
            for name in SEEDS:
                dst.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(name)))
            fingerprints = {}
            for name in ordered_tables(src, names):
                src.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position", (name,))
                columns = [r[0] for r in src.fetchall()]
                parts = sql.SQL(',').join(map(sql.Identifier, columns))
                buffer = io.BytesIO()
                src.copy_expert(sql.SQL('COPY {} ({}) TO STDOUT').format(sql.Identifier(name), parts), buffer)
                buffer.seek(0)
                dst.copy_expert(sql.SQL('COPY {} ({}) FROM STDIN').format(sql.Identifier(name), parts), buffer)
                original = fingerprint(src, name, columns)
                require(fingerprint(dst, name, columns) == original, 'Imported table verification failed: ' + name)
                fingerprints[name] = {'rows': original[0], 'hash': original[1]}
            with target.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute('SELECT * FROM platform_users ORDER BY id')
                source_users = cur.fetchall()
                for user in source_users:
                    cur.execute('SELECT id FROM users WHERE lower(email)=lower(%s) AND deleted_at IS NULL FOR UPDATE', (user['email'],))
                    matched = cur.fetchone()
                    owner = matched['id'] if matched else 'registration:' + user['id']
                    if not matched:
                        cur.execute('INSERT INTO users(id,email,name,password_hash,role,status,invite_code,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                                    (owner, user['email'].lower(), user['name'], user['password_hash'], user['role'], user['status'], secrets.token_hex(8), user['created_at']))
                    amount, frozen = (user['points'] + user['frozen']) * 100, user['frozen'] * 100
                    cur.execute('UPDATE users SET balance=balance+%s,frozen=frozen+%s,updated_at=now() WHERE id=%s RETURNING balance,frozen', (amount, frozen, owner))
                    balance = cur.fetchone()
                    cur.execute("INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description,business,available_change,frozen_change) VALUES(%s,'migration',%s,%s,%s,%s,'注册平台余额迁入','registration',%s,%s)",
                                (owner, amount, balance['balance'], balance['frozen'], 'registration:migrate:'+user['id'], user['points']*100, frozen))
                    cur.execute('UPDATE platform_users SET xufu_user_id=%s WHERE id=%s', (owner, user['id']))
                    cur.execute('INSERT INTO registration_migrations(source_user_id,target_user_id,amount,frozen) VALUES(%s,%s,%s,%s)', (user['id'], owner, amount, frozen))
            dst.execute("""UPDATE users u SET invited_by=i.xufu_user_id FROM platform_users p
                JOIN platform_users i ON i.id=p.invited_by
                WHERE u.id=p.xufu_user_id AND u.invited_by IS NULL AND u.id<>i.xufu_user_id""")
            dst.execute("""INSERT INTO registration_reservations(order_id,user_id,reserved,charged,released)
                SELECT o.id,p.xufu_user_id,(o.unit_price*o.quantity*100)::bigint,(o.charged*100)::bigint,(o.released*100)::bigint
                FROM platform_orders o JOIN platform_users p ON p.id=o.user_id""")
            dst.execute("""SELECT count(*) FROM platform_users p WHERE p.frozen::bigint*100<>
                (SELECT coalesce(sum(reserved-charged-released),0) FROM registration_reservations r WHERE r.user_id=p.xufu_user_id)""")
            require(dst.fetchone()[0] == 0, 'Source frozen balance does not reconcile with registration orders')
            dst.execute('UPDATE platform_api_keys k SET xufu_credential_hash=md5(u.password_hash) FROM platform_users p JOIN users u ON u.id=p.xufu_user_id WHERE k.user_id=p.id')
            for table, trigger in [('platform_orders', 'platform_partner_admission'),
                                   ('platform_orders', 'platform_partner_settlement'),
                                   ('platform_api_keys', 'registration_key_identity')]:
                dst.execute(sql.SQL('ALTER TABLE {} ENABLE TRIGGER {}').format(sql.Identifier(table), sql.Identifier(trigger)))
            dst.execute("""SELECT table_name,column_name,column_default FROM information_schema.columns
                WHERE table_schema=current_schema() AND column_default LIKE 'nextval(%'
                AND (table_name LIKE 'platform_%' OR table_name LIKE 'session_proxy_%')""")
            for table, column, _ in dst.fetchall():
                dst.execute('SELECT pg_get_serial_sequence(%s,%s)', (table, column))
                sequence = dst.fetchone()[0]
                dst.execute(sql.SQL('SELECT max({}) FROM {}').format(sql.Identifier(column), sql.Identifier(table)))
                maximum = dst.fetchone()[0]
                if maximum is not None:
                    dst.execute('SELECT setval(%s,%s,true)', (sequence, maximum))
            dst.execute('INSERT INTO registration_imports(id) VALUES(1)')
            if before_commit:before_commit()
            return {'already_migrated': False, 'copied': True, 'users': len(source_users), 'tables': fingerprints}
    finally:
        source.close()
        target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-runtime', required=True)
    parser.add_argument('--target-runtime', required=True)
    args = parser.parse_args()
    manifest = copy_runtime(args.source_runtime, args.target_runtime)
    def verify_runtime():
        require(runtime_manifest(args.source_runtime) == manifest, 'Source runtime changed during migration; keep workers stopped')
        require(runtime_manifest(args.target_runtime) == manifest, 'Target runtime changed during migration; keep workers stopped')
    result = migrate(os.environ['GPTREG_SOURCE_DATABASE_URL'], os.environ['DATABASE_URL'], before_commit=verify_runtime)
    result['runtime_files'] = len(manifest)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
