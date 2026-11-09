-- Project integration events into the single operations ledger. Engine tables stay intact.
ALTER TABLE risk_events ADD COLUMN IF NOT EXISTS registration_event_id bigint;
CREATE UNIQUE INDEX IF NOT EXISTS risk_registration_event ON risk_events(registration_event_id);
ALTER TABLE audit_events ADD COLUMN IF NOT EXISTS registration_event_id bigint;
CREATE UNIQUE INDEX IF NOT EXISTS audit_registration_event ON audit_events(registration_event_id);

CREATE OR REPLACE FUNCTION registration_risk_projection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO risk_events(registration_event_id,user_id,category,severity,ip,detail,resolved,created_at,occurrences,last_seen)
 SELECT NEW.id,p.xufu_user_id,NEW.category,
  CASE NEW.severity WHEN 'critical' THEN 'high' WHEN 'warning' THEN 'medium' ELSE 'low' END,
  NEW.source_ip,NEW.message,NEW.resolved_at IS NOT NULL OR NEW.severity='info',NEW.first_seen,NEW.occurrences,NEW.last_seen
 FROM (SELECT 1) x LEFT JOIN platform_users p ON p.id=NEW.user_id
 ON CONFLICT(registration_event_id) DO UPDATE SET detail=excluded.detail,severity=excluded.severity,
  occurrences=excluded.occurrences,last_seen=excluded.last_seen,resolved=excluded.resolved;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS registration_risk_projection ON platform_risk_events;
CREATE TRIGGER registration_risk_projection AFTER INSERT OR UPDATE ON platform_risk_events
 FOR EACH ROW EXECUTE FUNCTION registration_risk_projection();

CREATE OR REPLACE FUNCTION registration_risk_resolve() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.registration_event_id IS NOT NULL AND NEW.resolved AND NOT OLD.resolved AND pg_trigger_depth()=1 THEN
  UPDATE platform_risk_events SET resolved_at=coalesce(resolved_at,now()) WHERE id=NEW.registration_event_id;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS registration_risk_resolve ON risk_events;
CREATE TRIGGER registration_risk_resolve AFTER UPDATE OF resolved ON risk_events
 FOR EACH ROW EXECUTE FUNCTION registration_risk_resolve();

CREATE OR REPLACE FUNCTION registration_audit_projection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO audit_events(registration_event_id,actor_id,action,target,detail,ip,created_at)
 SELECT NEW.id,p.xufu_user_id,'registration.'||NEW.action,coalesce(NEW.target,''),NEW.detail,'',NEW.created_at
 FROM (SELECT 1) x LEFT JOIN platform_users p ON p.id=NEW.actor_id
 ON CONFLICT(registration_event_id) DO NOTHING;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS registration_audit_projection ON platform_audit;
CREATE TRIGGER registration_audit_projection AFTER INSERT ON platform_audit
 FOR EACH ROW EXECUTE FUNCTION registration_audit_projection();

INSERT INTO risk_events(registration_event_id,user_id,category,severity,ip,detail,resolved,created_at,occurrences,last_seen)
 SELECT e.id,p.xufu_user_id,e.category,CASE e.severity WHEN 'critical' THEN 'high' WHEN 'warning' THEN 'medium' ELSE 'low' END,
 e.source_ip,e.message,e.resolved_at IS NOT NULL OR e.severity='info',e.first_seen,e.occurrences,e.last_seen
 FROM platform_risk_events e LEFT JOIN platform_users p ON p.id=e.user_id
 ON CONFLICT(registration_event_id) DO NOTHING;
INSERT INTO audit_events(registration_event_id,actor_id,action,target,detail,ip,created_at)
 SELECT e.id,p.xufu_user_id,'registration.'||e.action,coalesce(e.target,''),e.detail,'',e.created_at
 FROM platform_audit e LEFT JOIN platform_users p ON p.id=e.actor_id
 ON CONFLICT(registration_event_id) DO NOTHING;
