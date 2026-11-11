-- StarBridge-owned post-delivery data. The registration engine tables stay intact.
CREATE TABLE IF NOT EXISTS registration_session_capture_start (
 id boolean PRIMARY KEY DEFAULT true CHECK(id),
 enabled_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO registration_session_capture_start(id) VALUES(true) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS registration_session_captures (
 completion_key text PRIMARY KEY,
 asset_id text NOT NULL,
 email text NOT NULL DEFAULT '',
 completed_at timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','ready','failed')),
 attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(),
 lease_token text,
 lease_until timestamptz,
 session_cipher text,
 fetched_at timestamptz,
 error_code text NOT NULL DEFAULT '',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS registration_session_capture_due
 ON registration_session_captures(available_at) WHERE state IN ('queued','running');
CREATE INDEX IF NOT EXISTS registration_session_capture_asset
 ON registration_session_captures(asset_id,completed_at DESC);
