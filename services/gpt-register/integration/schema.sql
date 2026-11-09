ALTER TABLE platform_users ADD COLUMN IF NOT EXISTS xufu_user_id text REFERENCES users(id);
CREATE UNIQUE INDEX IF NOT EXISTS registration_identity_unique ON platform_users(xufu_user_id);
ALTER TABLE platform_ledger ALTER COLUMN points_after TYPE numeric(20,2);
ALTER TABLE platform_ledger ALTER COLUMN frozen_after TYPE numeric(20,2);
ALTER TABLE platform_ledger ADD COLUMN IF NOT EXISTS unified_entry_id bigint REFERENCES ledger(id);
DROP TRIGGER IF EXISTS platform_partner_settlement ON platform_orders;
ALTER TABLE platform_orders ALTER COLUMN unit_price TYPE numeric(20,2);
ALTER TABLE platform_orders ALTER COLUMN charged TYPE numeric(20,2);
ALTER TABLE platform_orders ALTER COLUMN released TYPE numeric(20,2);
ALTER TABLE platform_orders ADD COLUMN IF NOT EXISTS price_snapshot jsonb;
ALTER TABLE platform_ledger ALTER COLUMN points_delta TYPE numeric(20,2);
ALTER TABLE platform_ledger ALTER COLUMN frozen_delta TYPE numeric(20,2);
ALTER TABLE platform_partner_rewards ALTER COLUMN fee TYPE numeric(20,2);
CREATE TABLE IF NOT EXISTS registration_reservations (
 order_id text PRIMARY KEY REFERENCES platform_orders(id),
 user_id text NOT NULL REFERENCES users(id), reserved bigint NOT NULL CHECK(reserved>=0),
 charged bigint NOT NULL DEFAULT 0 CHECK(charged>=0), released bigint NOT NULL DEFAULT 0 CHECK(released>=0),
 CHECK(charged+released<=reserved)
);
CREATE OR REPLACE VIEW registration_members AS
 SELECT p.id,u.email,u.name,u.password_hash,u.role,
 CASE WHEN u.deleted_at IS NOT NULL THEN 'disabled' ELSE u.status END AS status,
 ((u.balance-u.frozen)::float8/100) AS points,(u.frozen::float8/100) AS frozen,
 u.created_at,u.invite_code,p.invited_by,p.xufu_user_id
 FROM platform_users p JOIN users u ON u.id=p.xufu_user_id;

DROP FUNCTION IF EXISTS registration_post_ledger(text,text,text,integer,integer,text);
CREATE OR REPLACE FUNCTION registration_post_ledger(
 member_id text, entry_reference text, entry_kind text, point_delta numeric,
 freeze_delta numeric, entry_note text
) RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE owner_id text; owner users; prior platform_ledger; registration_order text;
        amount bigint; frozen_amount bigint; entry_id text; shared_entry_id bigint; reservation registration_reservations;
BEGIN
 SELECT xufu_user_id INTO owner_id FROM platform_users WHERE id=member_id;
 IF owner_id IS NULL THEN RAISE EXCEPTION 'Unified registration identity is missing'; END IF;
 SELECT * INTO owner FROM users WHERE id=owner_id FOR UPDATE;
 SELECT * INTO prior FROM platform_ledger WHERE user_id=member_id AND reference=entry_reference;
 IF FOUND THEN
  IF prior.kind<>entry_kind OR prior.points_delta<>point_delta OR prior.frozen_delta<>freeze_delta
   THEN RAISE EXCEPTION 'Conflicting registration settlement'; END IF;
  RETURN false;
 END IF;
 IF point_delta IS NULL OR freeze_delta IS NULL OR point_delta<>round(point_delta,2) OR freeze_delta<>round(freeze_delta,2)
  THEN RAISE EXCEPTION 'Registration amounts require at most two decimal places'; END IF;
 amount=((point_delta+freeze_delta)*100)::bigint;
 frozen_amount=(freeze_delta*100)::bigint;
 IF entry_kind='reserve' THEN
  registration_order=substring(entry_reference FROM 9);
  IF entry_reference NOT LIKE 'reserve:%' OR point_delta<>-freeze_delta OR freeze_delta<0
   OR owner.status<>'active' OR owner.deleted_at IS NOT NULL
   OR NOT EXISTS(SELECT 1 FROM platform_orders WHERE id=registration_order AND user_id=member_id
       AND unit_price*quantity*100=frozen_amount)
   THEN RAISE EXCEPTION 'Invalid registration reservation'; END IF;
  INSERT INTO registration_reservations(order_id,user_id,reserved) VALUES(registration_order,owner_id,frozen_amount);
 ELSIF entry_kind IN ('charge','release') THEN
  SELECT i.order_id INTO registration_order FROM platform_items i JOIN platform_orders o ON o.id=i.order_id
   WHERE i.id=split_part(entry_reference,':',2) AND o.user_id=member_id
   AND o.unit_price*100=-frozen_amount;
  SELECT * INTO reservation FROM registration_reservations WHERE order_id=registration_order FOR UPDATE;
  IF NOT FOUND OR reservation.user_id<>owner_id OR frozen_amount>0
   OR reservation.reserved-reservation.charged-reservation.released < -frozen_amount
   OR entry_reference<>entry_kind||':'||split_part(entry_reference,':',2)
   THEN RAISE EXCEPTION 'Registration reservation does not cover settlement'; END IF;
  IF EXISTS(SELECT 1 FROM platform_ledger WHERE user_id=member_id AND
     reference=(CASE entry_kind WHEN 'charge' THEN 'release:' ELSE 'charge:' END)||split_part(entry_reference,':',2))
   THEN RAISE EXCEPTION 'Registration item was already settled'; END IF;
  IF entry_kind='charge' THEN
   IF point_delta<>0 OR NOT EXISTS(SELECT 1 FROM platform_accounts
      WHERE item_id=split_part(entry_reference,':',2) AND order_id=registration_order AND user_id=member_id)
    THEN RAISE EXCEPTION 'Account delivery must precede charging'; END IF;
   UPDATE registration_reservations SET charged=charged-frozen_amount WHERE order_id=registration_order;
  ELSE
   IF point_delta<>-freeze_delta THEN RAISE EXCEPTION 'Invalid release'; END IF;
   UPDATE registration_reservations SET released=released-frozen_amount WHERE order_id=registration_order;
  END IF;
 ELSIF entry_kind NOT IN ('redeem','adjust','invite_reward') OR freeze_delta<>0 THEN
  RAISE EXCEPTION 'Unsupported registration wallet operation';
 END IF;
 IF owner.balance+amount<owner.frozen+frozen_amount OR owner.frozen+frozen_amount<0 THEN
  RAISE EXCEPTION 'Insufficient available balance';
 END IF;
 UPDATE users SET balance=balance+amount,frozen=frozen+frozen_amount,updated_at=now()
  WHERE id=owner_id RETURNING * INTO owner;
 entry_id=md5(random()::text||clock_timestamp()::text);
 INSERT INTO platform_ledger(id,user_id,reference,kind,points_delta,frozen_delta,note,points_after,frozen_after)
  VALUES(entry_id,member_id,entry_reference,entry_kind,point_delta,freeze_delta,entry_note,
   (owner.balance-owner.frozen)::numeric/100,owner.frozen::numeric/100);
 INSERT INTO ledger(user_id,kind,amount,balance_after,frozen_after,reference,description,
                    business,registration_order_id,available_change,frozen_change)
  VALUES(owner_id,CASE entry_kind WHEN 'reserve' THEN 'freeze' WHEN 'release' THEN 'unfreeze' ELSE entry_kind END,
   amount,owner.balance,owner.frozen,'registration:'||entry_reference,entry_note,'registration',registration_order,
   (point_delta*100)::bigint,frozen_amount) RETURNING id INTO shared_entry_id;
 UPDATE platform_ledger SET unified_entry_id=shared_entry_id WHERE id=entry_id;
 RETURN true;
END $$;

CREATE OR REPLACE FUNCTION platform_partner_admit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE inviter text; joined timestamptz; rules jsonb; rate integer;
BEGIN
 SELECT value INTO rules FROM settings WHERE key='invites';
 IF rules->>'enabled' IS DISTINCT FROM 'true' THEN RETURN NEW; END IF;
 SELECT i.id,u.created_at INTO inviter,joined FROM platform_users p JOIN users u ON u.id=p.xufu_user_id
  JOIN platform_users i ON i.xufu_user_id=u.invited_by WHERE p.id=NEW.user_id;
 IF inviter IS NOT NULL AND inviter<>NEW.user_id AND NEW.created_at>=joined THEN
  SELECT (s->>'rate_percent')::int INTO rate FROM jsonb_array_elements(rules->'stages') s
   WHERE NEW.created_at<joined+(s->>'until_days')::int*interval '1 day' ORDER BY (s->>'until_days')::int LIMIT 1;
  IF rate>0 THEN INSERT INTO platform_partner_candidates VALUES(NEW.id,NEW.user_id,inviter,rate) ON CONFLICT DO NOTHING; END IF;
 END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION platform_partner_settle() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE candidate platform_partner_candidates; target integer; previous integer;
BEGIN
 IF NEW.charged<=OLD.charged THEN RETURN NEW; END IF;
 SELECT * INTO candidate FROM platform_partner_candidates WHERE order_id=NEW.id;
 IF NOT FOUND THEN RETURN NEW; END IF;
 target=floor(NEW.charged*candidate.rate_percent/100);
 INSERT INTO platform_partner_rewards(order_id,invitee_id,inviter_id,rate_percent)
  VALUES(NEW.id,candidate.invitee_id,candidate.inviter_id,candidate.rate_percent) ON CONFLICT DO NOTHING;
 SELECT amount INTO previous FROM platform_partner_rewards WHERE order_id=NEW.id FOR UPDATE;
 IF target>previous THEN
  PERFORM registration_post_ledger(candidate.inviter_id,'partner:'||NEW.id||':'||target,
   'invite_reward',target-previous,0,'注册订单伙伴奖励');
 END IF;
 UPDATE platform_partner_rewards SET fee=NEW.charged,amount=target,updated_at=now() WHERE order_id=NEW.id;
 RETURN NEW;
END $$;
CREATE TRIGGER platform_partner_settlement AFTER UPDATE OF charged ON platform_orders
 FOR EACH ROW EXECUTE FUNCTION platform_partner_settle();
