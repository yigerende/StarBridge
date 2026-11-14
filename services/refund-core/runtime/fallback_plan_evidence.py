"""Bounded, credential-free facts for private fallback-plan diagnostics."""
import re
import diagnostics
from manager_oauth import upstream

ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _+()-]{0,63}\Z")
MISSING = object()
REASONS = {
    "personal email mismatch": "session_email_mismatch",
    "personal account mismatch": "session_account_mismatch",
    "personal user mismatch": "session_user_mismatch",
    "live identity mismatch": "live_email_mismatch",
    "invalid plan response": "invalid_response", "missing accounts": "missing_accounts",
    "account not found": "account_missing", "not the authenticated personal account": "personal_account_mismatch",
    "invalid entitlement": "invalid_entitlement", "invalid plan": "invalid_plan",
    "unknown personal plan": "unknown_plan", "invalid subscription state": "invalid_subscription_state",
    "conflicting personal plan": "conflicting_plan", "missing plan": "missing_plan",
    "conflicting subscription state": "conflicting_subscription_state",
    "plan query failed": "query_failed",
}


def object_value(value):
    return value if isinstance(value, dict) else {}


def snapshot(session, job, data=MISSING, me=MISSING):
    fields, types, checks = {}, {}, {}

    def field(key, value=MISSING, is_id=False):
        types[key] = ("missing" if value is MISSING else "null" if value is None else
                      "boolean" if type(value) is bool else "string" if isinstance(value, str) else
                      "object" if isinstance(value, dict) else "array" if isinstance(value, list) else
                      "number" if type(value) in (int, float) else "other")
        if isinstance(value, str) and (ID if is_id else LABEL).fullmatch(value):
            fields[key] = value
        elif type(value) is bool:
            checks[key] = value

    session = object_value(session)
    claims = upstream.jwt_payload(session.get("access_token") or session.get("accessToken") or "")
    auth = object_value(claims.get("https://api.openai.com/auth"))
    account = object_value(session.get("account"))
    for key, value in (("token.account_id", auth.get("chatgpt_account_id", MISSING)),
                       ("session.account_id", account.get("id", MISSING)),
                       ("expected.team_id", object_value(job.get("mother")).get("team_id", MISSING)),
                       ("expected.personal_id", object_value(job.get("personal_baseline")).get("id", MISSING))):
        field(key, value, True)
    field("token.plan_type", auth.get("chatgpt_plan_type", MISSING))
    field("session.plan_type", account.get("planType", MISSING))
    field("session.structure", account.get("structure", MISSING))
    field("session.is_deactivated", account.get("isDeactivated", MISSING))
    aid = auth.get("chatgpt_account_id")
    checks["session_account_matches"] = isinstance(aid, str) and bool(aid) and account.get("id") in (None, aid)
    checks["child_id_matches"] = bool(job.get("child_user_id")) and auth.get("chatgpt_user_id") == job.get("child_user_id")
    if me is not MISSING:
        email = object_value(me).get("email")
        checks["live_email_matches"] = isinstance(email, str) and email.casefold() == job["account"]["email"].casefold()

    keys, truncated = [], False
    if data is not MISSING:
        field("response", data)
        accounts = object_value(data).get("accounts", MISSING)
        field("accounts", accounts)
        accounts = object_value(accounts)
        key = aid if accounts.get(aid) else "default"
        field("selected_key", key, True)
        item = accounts.get(key, MISSING)
        field("item", item)
        for prefix, row in (("account", object_value(item).get("account", MISSING)),
                            ("default", object_value(accounts.get("default")).get("account", MISSING))):
            field(prefix, row)
            obj = object_value(row)
            field(prefix + ".account_id", obj.get("account_id", MISSING), True)
            for name in ("structure", "plan_type", "is_deactivated"):
                field(prefix + "." + name, obj.get(name, MISSING))
        selected = object_value(object_value(item).get("account"))
        checks["account_id_matches"] = isinstance(aid, str) and bool(aid) and selected.get("account_id") == aid
        checks["personal_structure"] = selected.get("structure") == "personal"
        entitlement = object_value(item).get("entitlement", MISSING)
        field("entitlement", entitlement)
        for name in ("subscription_plan", "plan_type", "has_active_subscription"):
            field("entitlement." + name, object_value(entitlement).get(name, MISSING))
        keys = [k for k in accounts if isinstance(k, str) and ID.fullmatch(k)][:16]
        truncated = len(accounts) > len(keys)
    return {"fields": fields, "types": types, "checks": checks,
            "account_keys": keys, "truncated": truncated}


def record(session, job, *, phase, result="observed", error=None, data=MISSING,
           me=MISSING, http_status=0, wait_seconds=0, elapsed_ms=0):
    # Diagnostics must never turn a valid plan result into a business failure.
    try:
        evidence = snapshot(session, job, data, me)
        evidence.update(phase=phase, result=result, wait_seconds=wait_seconds,
                        elapsed_ms=max(0, min(900000, int(elapsed_ms))))
        if error is not None:
            evidence["reason"] = REASONS.get(str(error), "query_error")
        diagnostics.record({"stage": "fallback_plan", "side": "child", "event": "response",
                            "plan_check": evidence}, http_status=http_status)
    except Exception:
        pass
