-- Actual service settlements across all three businesses, in wallet minor units.
-- Keep order identity scoped by both business and customer; refunds reduce only
-- their original order's growth. Wallet credits and freezes are not consumption.
CREATE OR REPLACE VIEW membership_order_spend AS
SELECT l.user_id,l.business,
 COALESCE(l.order_id,l.registration_order_id,l.refund_order_id) AS order_id,
 GREATEST(0,-sum(l.amount))::bigint AS spent,max(l.created_at) AS updated_at
FROM ledger l
WHERE ((l.kind='charge' AND l.amount<0) OR (l.kind='refund' AND l.amount>0))
 AND ((l.business='subscription' AND l.order_id IS NOT NULL)
   OR (l.business='registration' AND l.registration_order_id IS NOT NULL)
   OR (l.business='refund' AND l.refund_order_id IS NOT NULL))
GROUP BY l.user_id,l.business,COALESCE(l.order_id,l.registration_order_id,l.refund_order_id);
