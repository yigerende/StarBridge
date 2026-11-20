"""Password continuation for an explicitly offered passwordless email login.

Uses the existing live auth session and password/TOTP protocol. Optional mailbox
access is permitted only after password verification still requires email OTP.
"""
import time
from urllib.parse import urlsplit
import diagnostics
from manager_oauth import upstream, bridge


class NeedsVerification(Exception):
    code = "extra_verification"

    def __init__(self, after_password=False):
        if after_password:
            self.code = "email_verification_after_password"
        super().__init__(self.code)


class PasswordContinuationMixin:
    def can_pickup_email(self):
        return bool(getattr(self, '_password_verified', False) and self.payload.get('_pickup_url'))

    def prepare_email_code(self):
        if not self.can_pickup_email():
            return False
        if self.payload.get('manual_email_code'):
            return True
        from mailbox_pickup import MailboxReader, MailboxError
        # No mailbox request, DNS lookup or baseline fetch occurs before here.
        self._mail_used = getattr(self, '_mail_used', set())
        failure = None
        code = ''
        diagnostics.record({'stage': 'email_verification', 'side': 'child', 'event': 'start'})
        try:
            if not hasattr(self, '_mail_reader'):
                self._mail_reader = MailboxReader(self.payload['email'], self.payload['_pickup_url'])
            code = self._mail_reader.next_code(getattr(self, '_login_issued_after', 0), self._mail_used)
        except MailboxError as exc:
            failure = exc.code
        if failure:
            error = NeedsVerification(True)
            error.code = failure
            raise error
        bridge.protect_code(code)
        self._mail_used.add(code)
        self.payload['manual_email_code'] = code
        return True

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
            if not allow_password and not self.can_pickup_email():
                raise NeedsVerification(getattr(self, "_password_submitted", False))
            return
        if not url and page not in {"login_password", "mfa_challenge"}:
            incomplete = not page and not data.get("error")
            raise upstream.LoginFlowError("Login response has no usable next step",
                                          code="login_response_incomplete" if incomplete else "login_step_unsupported",
                                          retryable=incomplete)

    def complete_modern_login(self, step, password, issued_after):
        self._login_issued_after = issued_after
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
        self._password_verified = not bool(step.get('error'))
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
        if '/email-verification' in url.lower() and self.can_pickup_email():
            if getattr(self, '_mfa_email_continued', False):
                raise NeedsVerification(True)
            self._mfa_email_continued = True
            return upstream.ChatGPTProtocolLogin.complete_modern_login(
                self, {'page': {'type': 'email_otp_verification'}, 'continue_url': url},
                '', getattr(self, '_login_issued_after', 0))
        return url

    def submit_mfa_verify(self, factor_id, code):
        step = super().submit_mfa_verify(factor_id, code)
        if self.extract_page_type(step) == 'email_otp_verification' and not self.extract_continue_url(step):
            step = {**step, 'continue_url': 'https://auth.openai.com/email-verification'}
        return step

    def submit_modern_code(self, code):
        try:
            step = super().submit_modern_code(code)
        finally:
            self.payload.pop('manual_email_code', None)
        page = self.extract_page_type(step)
        url = self.normalize_auth_url(self.extract_continue_url(step))
        if page in {'email_otp_verification', 'login_password'} or any(p in url for p in ('/email-verification', '/log-in/password')):
            raise NeedsVerification(True)
        diagnostics.record({'stage': 'email_verification', 'side': 'child', 'event': 'response'})
        return step
