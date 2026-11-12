"""Login-only subprocess; reuse the imported authentication without its refund flow."""
import json
from pathlib import Path
import sys


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services/refund-core/runtime'))
    from manager_oauth import bridge
    from manager_oauth.adapter import ProjectProtocolLogin

    class Login(ProjectProtocolLogin):
        def log(self, step, message, level='info'):
            if step == 'waiting_code':
                raise RuntimeError('additional verification required')
            # Authentication logs are not sent to the website or persisted.

    data = json.loads(sys.stdin.read(131073))
    payload = {'email': data['email'], 'password': data['password'], '_totp_secret': data.get('totp_secret', ''),
               'proxy': data['proxy'], 'credential_mode': 'chatgpt_at', 'configured_login_mode': 'password_totp',
               'selected_login_mode': 'password_totp', 'allow_sms': False, 'skip_phone_verification': True}
    bridge.configure(payload)
    flow = None
    try:
        flow = Login('mother-management', payload)
        session = flow.login()
        print(json.dumps({'ok': True, 'access_token': session.get('access_token') or session.get('accessToken'),
                          'refresh_token': session.get('refresh_token') or session.get('refreshToken') or ''}))
    except Exception:
        print(json.dumps({'ok': False}))
    finally:
        if flow:
            flow.close()


if __name__ == '__main__':
    main()
