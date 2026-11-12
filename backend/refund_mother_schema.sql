-- Management state shares the main database; no refund order or wallet is created.
ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS management_json JSONB NOT NULL DEFAULT '{}';
ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS last_refreshed_at TIMESTAMPTZ;
ALTER TABLE refund_mothers ADD COLUMN IF NOT EXISTS refresh_attempt_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS refund_mother_leases (
 team_id TEXT PRIMARY KEY, token TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS refund_mother_children (
 email TEXT PRIMARY KEY, user_id TEXT NOT NULL, credentials_enc TEXT NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS refund_member_leases (
 email TEXT PRIMARY KEY, token TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL
);
INSERT INTO settings(key,value) VALUES('refund_mother_management', '{"auto_refresh":true}') ON CONFLICT DO NOTHING;
