"""Unified account projections; registration execution remains in GPT Register."""
from . import db
from .common import current, digest, require, now, limit


def installed():
    return bool(db.scalar("SELECT to_regclass('registration_reservations')"))


def summary(user_id=None):
    if not installed():return {'orders':0,'pending':0,'delivered':0,'spent':0}
    return db.one("""SELECT count(*) AS orders,count(*) FILTER(WHERE o.status IN ('queued','running','review')) AS pending,
      coalesce(sum(o.delivered),0) AS delivered,coalesce(sum((o.charged*100)::bigint),0) AS spent
      FROM platform_orders o JOIN platform_users p ON p.id=o.user_id WHERE ($1::text IS NULL OR p.xufu_user_id=$1)""",user_id)


def reward_source():
    subscription="SELECT inviter_id,invitee_id,rate_percent,fee,granted,entitled,recovered,created_at FROM invitation_rewards"
    if not installed():return '('+subscription+')'
    registration="""SELECT i.xufu_user_id AS inviter_id,p.xufu_user_id AS invitee_id,r.rate_percent,(r.fee*100)::bigint AS fee,
      r.amount::bigint*100 AS granted,r.amount::bigint*100 AS entitled,0::bigint AS recovered,r.created_at
      FROM platform_partner_rewards r JOIN platform_users p ON p.id=r.invitee_id
      JOIN platform_users i ON i.id=r.inviter_id"""
    return '('+subscription+' UNION ALL '+registration+')'
