"""Password continuation for an explicitly offered passwordless email login.

Uses the existing live auth session and password/TOTP protocol. No OTP pickup,
new authorization, proxy allocation, or refund operation belongs here.
"""
import time
from urllib.parse import urlsplit
import diagnostics
from manager_oauth import upstream


class NeedsVerification(Exception):
    code = "extra_verification"

    def __init__(self, after_password=False):
        if after_password:
            self.code = "email_verification_after_password"
        super().__init__(self.code)


class PasswordContinuationMixin:
    def read_password_login_session(self):
        # The browser reads this endpoint after "Continue with password".
        # Reuse this login's cookie jar; never import a captured HAR session.
        url = "https://auth.openai.com/api/accounts/client_auth_session_dump"
        response = self.request(url, headers=self.headers(url, {
            "Accept": "application/json", "Origin": "https://auth.openai.com",
            "Referer": "https://auth.openai.com/log-in/password",
        }))
        if response.status != 200:
            raise upstream.LoginFlowError("Cannot read current login session",
                                          code="login_auth_failed", status=response.status, retryable=False)
        data = response.json()
        session = data.get("client_auth_session") if isinstance(data, dict) else None
        if not isinstance(session, dict) or not session:
            raise upstream.LoginFlowError("Current login session is missing",
                                          code="login_response_incomplete", retryable=True)
        expected = str(self.payload.get("email") or "").strip().casefold()
        username = session.get("username")
        identities = [session.get("email")]
        if isinstance(username, dict) and username.get("kind") == "email":
            identities.append(username.get("value"))
        identities = [v for v in identities if v is not None]
        if not expected or not identities or any(not isinstance(v, str) or v.strip().casefold() != expected for v in identities):
            raise upstream.LoginFlowError("Current login session identity does not match",
                                          code="login_auth_failed", retryable=False)
        if data.get("error") or session.get("email_verification_mode") != "passwordless_login":
            raise upstream.LoginFlowError("Current login step changed",
                                          code="login_step_unsupported", retryable=False)
        self.response_auth_session = session

    def inspect_login_step(self, step, operation, allow_password=False):
        data = step if isinstance(step, dict) else {}
        self._login_step_evidence = diagnostics.login_step(data, operation)
        page = self.extract_page_type(data).lower()
        url = self.normalize_auth_url(self.extract_continue_url(data))
        self.raise_if_passkey_or_challenge(data, url)
        # The imported helper also calls all MFA "phone"; TOTP has its own
        # supported path, so classify only explicit phone pages/URLs here.
        phone_markers = ("phone_verification", "phone-verification", "phone_otp", "phone-otp", "add-phone", "select-channel")
        if any(marker in page or marker in urlsplit(url).path.lower() for marker in phone_markers):
            raise upstream.LoginFlowError("Phone verification required",
                                          code="phone_verification_skipped", retryable=False)
        if page == "email_otp_verification" or "/email-verification" in url.lower():
            if not allow_password:
                raise NeedsVerification(getattr(self, "_password_submitted", False))
            return
        if not url and page not in {"login_password", "mfa_challenge"}:
            incomplete = not page and not data.get("error")
            raise upstream.LoginFlowError("Login response has no usable next step",
                                          code="login_response_incomplete" if incomplete else "login_step_unsupported",
                                          retryable=incomplete)

    def complete_modern_login(self, step, password, issued_after):
        data = step if isinstance(step, dict) else {}
        # A passwordless offer before password submission is different from a
        # mandatory email challenge after password/MFA. Never loop between them.
        eligible = (self.payload.get("_email_password_fallback", True) is True
                    and bool(password)
                    and self.extract_page_type(data).lower() == "email_otp_verification"
                    and self.extract_email_verification_mode(data).lower() == "passwordless_login"
                    and not getattr(self, "_password_submitted", False)
                    and not getattr(self, "_password_switch_attempted", False))
        self.inspect_login_step(data, "identifier", allow_password=eligible)
        if eligible:
            self._password_switch_attempted = True
            diagnostics.record({"stage": "password_fallback", "side": "child", "event": "start"})
            started = time.monotonic()
            self.read_password_login_session()
            self.sentinel_token = upstream.generate_openai_sentinel_token(self.device_id, "password_verify", self.proxy_url)
            # HAR: after reading the session, password/verify uses that session.
            # Feed its real response into the existing MFA/callback handling.
            data = self.submit_modern_password(password)
            diagnostics.record({"stage": "password_fallback", "side": "child", "event": "response"},
                               duration_ms=min(900000, int((time.monotonic() - started) * 1000)))
        return super().complete_modern_login(data, password, issued_after)

    def submit_modern_password(self, password):
        if getattr(self, "_password_submitted", False):
            raise upstream.LoginFlowError("Password already submitted in this login session",
                                          code="login_step_unsupported", retryable=False)
        self._password_submitted = True
        step = super().submit_modern_password(password)
        self.inspect_login_step(step, "password")
        page = self.extract_page_type(step).lower()
        url = self.normalize_auth_url(self.extract_continue_url(step))
        if page == "login_password" or "/log-in/password" in url.lower():
            raise upstream.LoginFlowError("Password login did not advance",
                                          code="login_step_unsupported", retryable=False)
        return step

    def complete_totp_challenge(self, step, secret):
        url = super().complete_totp_challenge(step, secret)
        # An email/phone challenge after MFA is mandatory. Never send it back
        # into the passwordless-offer branch or treat it as a completed login.
        self.inspect_login_step({"continue_url": url}, "mfa_verify")
        return url
