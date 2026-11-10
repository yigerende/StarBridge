"""Server-only, read-only reconciliation. Absence is never acceptance evidence."""
import time
import diagnostics


class VerificationUnavailable(Exception):
    def __init__(self, status=0, network_code=0):
        self.status, self.network_code = status, network_code


def personal_snapshot(flow, session):
    token, _ = flow.validate_team(session)
    started = time.monotonic()
    diagnostics.request_event("personal_check", "start")
    try:
        response = flow.web_session.get(
            "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
            headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                     "Origin": "https://chatgpt.com", "Referer": "https://chatgpt.com/"},
            timeout=45, allow_redirects=False)
    except Exception as exc:
        diagnostics.request_event("personal_check", "error", exc=exc, started=started)
        raise VerificationUnavailable(network_code=getattr(exc, "code", 0)) from None
    diagnostics.request_event("personal_check", "response", response=response, started=started)
    if response.status_code != 200:
        raise VerificationUnavailable(response.status_code)
    try:
        data = response.json()
        rows = data["accounts"]
        if isinstance(rows, dict):
            rows = list(rows.values())
        if not isinstance(rows, list) or not rows:
            raise ValueError()
        personal = {}
        for row in rows:
            account = row["account"]
            if not isinstance(account, dict) or not isinstance(account.get("account_id"), str) or not account["account_id"]:
                raise ValueError()
            if account.get("structure") == "personal":
                if type(account.get("is_deactivated")) is not bool:
                    raise ValueError()
                state = "deactivated" if account["is_deactivated"] else "active"
                key = account["account_id"]
                if key in personal and personal[key] != state:
                    raise ValueError()
                personal[key] = state
        if len(personal) > 1:
            raise ValueError()
        if not personal:
            return {"id": "", "state": "missing"}
        key, state = next(iter(personal.items()))
        if len(key) > 150:
            raise ValueError()
        return {"id": key, "state": state}
    except (KeyError, TypeError, ValueError):
        raise VerificationUnavailable(200) from None


def verification_code(baseline, current):
    if not baseline or baseline.get("state") == "unknown":
        return "verification_no_baseline"
    if current["state"] == "missing":
        return "verification_missing"
    if current["id"] != baseline.get("id"):
        return "verification_changed"
    if current["state"] == "deactivated":
        return "verification_deactivated"
    return "verification_active"


def verify(job, checkpoint, login_factory, payload):
    started = time.monotonic()
    checkpoint("verification_started")
    flow = None
    try:
        flow = login_factory(job["id"], payload)
        session = flow.login()
        _, user_id = flow.validate_team(session)
        if not job.get("child_user_id") or user_id != job["child_user_id"]:
            diagnostic = {"code": "member_identity"}
        else:
            current = personal_snapshot(flow, session)
            diagnostic = {"code": verification_code(job.get("personal_baseline"), current), "http_status": 200}
    except VerificationUnavailable as exc:
        diagnostic = {"code": "verification_unavailable", **diagnostics.failure(exc, "personal_check"), "http_status": exc.status, "network_code": exc.network_code}
    except Exception as exc:
        diagnostic = {"code": "verification_unavailable", **diagnostics.failure(exc, diagnostics.stage(getattr(flow, "last_stage", "")), flow=flow)}
    finally:
        if flow is not None:
            flow.close()
    diagnostic["duration_ms"] = min(900000, int((time.monotonic() - started) * 1000))
    checkpoint("verification_result", {"diagnostic": diagnostic})
    # These account observations do not prove export acceptance or a refund.
    # No POST, membership removal, success checkpoint or settlement is allowed.
    return {"ok": False, **diagnostic}
