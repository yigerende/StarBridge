CREATE TABLE IF NOT EXISTS refunds (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), email TEXT NOT NULL,
 credentials BYTEA NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 fee BIGINT NOT NULL CHECK(fee >= 0), fee_status TEXT NOT NULL DEFAULT 'frozen',
 message TEXT NOT NULL DEFAULT '申请已保存，等待退款服务接入。',
 provider_reference TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK(status IN ('queued','processing','verifying','succeeded','attention','banned','failed','cancelled')),
 CHECK(fee_status IN ('frozen','settled','released'))
);
CREATE INDEX IF NOT EXISTS refunds_user_time ON refunds(user_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS refunds_active_account ON refunds(user_id,lower(email))
 WHERE status IN ('queued','processing','verifying','attention');
CREATE TABLE IF NOT EXISTS refund_events (
 id BIGSERIAL PRIMARY KEY, refund_id TEXT NOT NULL REFERENCES refunds(id),
 message TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE refund_events ADD COLUMN IF NOT EXISTS internal BOOLEAN NOT NULL DEFAULT false;
CREATE TABLE IF NOT EXISTS refund_idempotency (
 user_id TEXT NOT NULL REFERENCES users(id), key TEXT NOT NULL, request_hash TEXT NOT NULL,
 refund_ids JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(user_id,key)
);
CREATE TABLE IF NOT EXISTS refund_settings (
 id BOOLEAN PRIMARY KEY DEFAULT true CHECK(id), enabled BOOLEAN NOT NULL DEFAULT false,
 max_parallel INT NOT NULL DEFAULT 2 CHECK(max_parallel BETWEEN 1 AND 8),
 login_attempts INT NOT NULL DEFAULT 2 CHECK(login_attempts BETWEEN 1 AND 3),
 retry_seconds INT NOT NULL DEFAULT 30 CHECK(retry_seconds BETWEEN 5 AND 300),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO refund_settings(id) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS refund_proxies (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, secret BYTEA NOT NULL, enabled BOOLEAN NOT NULL DEFAULT true,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS refund_mothers (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, team_id TEXT NOT NULL UNIQUE,
 secret BYTEA NOT NULL, proxy_id TEXT NOT NULL REFERENCES refund_proxies(id),
 seat_type TEXT NOT NULL DEFAULT 'default' CHECK(seat_type IN ('default','prolite')),
 enabled BOOLEAN NOT NULL DEFAULT true, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Retired proxies remain private for already assigned orders and their recovery.
ALTER TABLE refund_proxies ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS refund_execution (
 refund_id TEXT PRIMARY KEY REFERENCES refunds(id), mother_id TEXT NOT NULL REFERENCES refund_mothers(id),
 step TEXT NOT NULL DEFAULT 'assigned' CHECK(step IN ('assigned','inviting','invited','login','ready','submitting','accepted','removing','completed','failed')),
 child_user_id TEXT NOT NULL DEFAULT '', accepted_at TIMESTAMPTZ, completed_at TIMESTAMPTZ,
 lease_token TEXT NOT NULL DEFAULT '', lease_until TIMESTAMPTZ,
 released BOOLEAN NOT NULL DEFAULT false, updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Removing a mother hides configuration without deleting historical execution
-- references or credentials still needed by its reserved task to finish cleanup.
ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE refund_mothers DROP CONSTRAINT IF EXISTS refund_mothers_email_key;
ALTER TABLE refund_mothers DROP CONSTRAINT IF EXISTS refund_mothers_team_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS refund_mothers_active_email ON refund_mothers(email) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS refund_mothers_active_team ON refund_mothers(team_id) WHERE deleted_at IS NULL;
-- Release a verified-clean mother independently of the unresolved service fee.
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS verification_only BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS mother_released_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS refund_execution_mother_reserved ON refund_execution(mother_id) WHERE NOT released AND mother_released_at IS NULL;
DROP INDEX IF EXISTS refund_execution_mother_serial;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS progress_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE refunds ALTER COLUMN message SET DEFAULT '申请已受理';

ALTER TABLE refund_settings ADD COLUMN IF NOT EXISTS child_proxy_id TEXT REFERENCES refund_proxies(id);
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS child_proxy_id TEXT REFERENCES refund_proxies(id);
ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS refresh_secret BYTEA;
ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS plan_type TEXT NOT NULL DEFAULT '';
ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ;
ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS auth_failed_fingerprint TEXT;
ALTER TABLE refund_settings ADD COLUMN IF NOT EXISTS mother_failover JSONB NOT NULL DEFAULT '{"enabled":true,"max_switches":3,"retry_seconds":30}';
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS mother_switch_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS failed_mothers TEXT[] NOT NULL DEFAULT '{}';

ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS check_json JSONB;
ALTER TABLE refund_proxies ADD COLUMN IF NOT EXISTS check_json JSONB;

ALTER TABLE refund_settings ADD COLUMN IF NOT EXISTS mother_interval_seconds INTEGER NOT NULL DEFAULT 0 CHECK(mother_interval_seconds BETWEEN 0 AND 86400);
CREATE INDEX IF NOT EXISTS refund_execution_mother_completed ON refund_execution(mother_id,completed_at DESC) WHERE completed_at IS NOT NULL;
ALTER TABLE refund_settings ADD COLUMN IF NOT EXISTS recovery JSONB NOT NULL DEFAULT '{"enabled":true,"max_retries":3,"verify_intervals":[30,60,120,300],"cleanup_intervals":[15,30,60],"alert_seconds":600,"cleanup_failed":true}';
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS recovery_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS recovery_started_at TIMESTAMPTZ;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS verify_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS cleanup_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS personal_baseline JSONB;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS last_diagnostic JSONB NOT NULL DEFAULT '{}';
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS terminal_code TEXT NOT NULL DEFAULT '' CHECK (terminal_code IN ('','password_verification_failed'));
CREATE INDEX IF NOT EXISTS refund_execution_recovery_due ON refund_execution(next_attempt_at) WHERE NOT released;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS fallback_phase TEXT NOT NULL DEFAULT '';
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS fallback_cycles INTEGER NOT NULL DEFAULT 0;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS fallback_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS completion_basis TEXT NOT NULL DEFAULT '';
UPDATE refund_settings SET recovery=recovery || '{"plan_fallback":{"enabled":true,"max_cycles":2,"max_attempts":3,"retry_seconds":30}}'::jsonb
 WHERE NOT recovery ? 'plan_fallback';
UPDATE refund_settings SET recovery=recovery || '{"verification_http_statuses":[404]}'::jsonb
 WHERE NOT recovery ? 'verification_http_statuses';
CREATE TABLE IF NOT EXISTS refund_execution_diagnostics (
 id BIGSERIAL PRIMARY KEY, refund_id TEXT NOT NULL REFERENCES refunds(id),
 diagnostic JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS refund_execution_diagnostics_request ON refund_execution_diagnostics(refund_id,id);
CREATE INDEX IF NOT EXISTS refund_events_public_time ON refund_events(refund_id,created_at) WHERE NOT internal;


ALTER TABLE refund_settings ADD COLUMN IF NOT EXISTS accepting boolean NOT NULL DEFAULT false;
ALTER TABLE refund_settings ADD COLUMN IF NOT EXISTS service_fee bigint NOT NULL DEFAULT 10000 CHECK(service_fee BETWEEN 0 AND 10000000);
ALTER TABLE ledger ADD COLUMN IF NOT EXISTS refund_order_id text REFERENCES refunds(id);
CREATE UNIQUE INDEX IF NOT EXISTS refund_settle_once ON ledger(refund_order_id) WHERE business='refund' AND kind='charge';
CREATE INDEX IF NOT EXISTS ledger_activity_order ON ledger(user_id,business,(COALESCE(order_id,registration_order_id,refund_order_id)));

-- Child proxy failover is scheduler state; the protocol keeps its original steps.
ALTER TABLE refund_proxies ADD COLUMN IF NOT EXISTS target TEXT NOT NULL DEFAULT 'both' CHECK(target IN ('both','mother','child'));
ALTER TABLE refund_proxies ADD COLUMN IF NOT EXISTS failure_until TIMESTAMPTZ;
ALTER TABLE refund_proxies ADD COLUMN IF NOT EXISTS failure_at TIMESTAMPTZ;
ALTER TABLE refund_proxies ADD COLUMN IF NOT EXISTS failure_code TEXT NOT NULL DEFAULT '';
ALTER TABLE refund_proxies ADD COLUMN IF NOT EXISTS probe_until TIMESTAMPTZ;
ALTER TABLE refund_settings ADD COLUMN IF NOT EXISTS child_proxy_backups TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE refund_settings ADD COLUMN IF NOT EXISTS child_proxy_cooldown_seconds INTEGER NOT NULL DEFAULT 300 CHECK(child_proxy_cooldown_seconds BETWEEN 30 AND 3600);
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS child_proxy_switch_pending BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE refund_execution ADD COLUMN IF NOT EXISTS child_proxy_switches INTEGER NOT NULL DEFAULT 0;
