"""
TrackIt — Email Service
Sends account-creation emails to new users via Gmail SMTP.

Configuration (set in environment variables or .env):
  TRACKIT_EMAIL_FROM     sender Gmail address  e.g.  trackit.ojt@gmail.com
  TRACKIT_EMAIL_PASSWORD Gmail App Password    (NOT your Gmail login password)
  TRACKIT_WEB_URL        base URL of the web admin  e.g.  http://127.0.0.1:5502/web_admin

Gmail App Password setup:
  1. Enable 2-Step Verification on your Gmail account.
  2. Go to Google Account → Security → App Passwords.
  3. Generate a password for "Mail / Windows Computer".
  4. Paste that 16-char string as TRACKIT_EMAIL_PASSWORD.
"""

import os
import smtplib
import ssl
import string
import random
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path


# ── Config ────────────────────────────────────────────────────────────────────
SMTP_HOST         = 'smtp.gmail.com'
SMTP_SSL_PORT     = 465       # Gmail SSL — faster and more reliable than STARTTLS 587
SMTP_STARTTLS_PORT = 587
SMTP_TIMEOUT      = 30        # 30s is enough; Render's network is fast

_LAST_ERROR = None

# NOTE: Do NOT read env vars here at module level — they may not be loaded yet.
# Always call _get_email_config() at send time so load_dotenv() has already run.

def _ensure_dotenv():
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / '.env'
        load_dotenv(env_path, override=False)
    except ImportError:
        pass


def last_email_error():
    return _LAST_ERROR


def _set_error(msg: str):
    global _LAST_ERROR
    _LAST_ERROR = msg
    print(f"[Email] {msg}")


def _get_email_config():
    """Read email credentials from environment at call time (never at import time)."""
    _ensure_dotenv()
    ef = (os.environ.get('TRACKIT_EMAIL_FROM') or '').strip()
    # Gmail App Passwords are 16 chars; strip spaces if the user pasted "xxxx xxxx xxxx xxxx"
    ep = ''.join((os.environ.get('TRACKIT_EMAIL_PASSWORD') or '').split())
    wu = os.environ.get('TRACKIT_WEB_URL', 'http://127.0.0.1:5502/web_admin')
    return ef, ep, wu

# Keep these as lazy properties for backwards-compat with any code that imports them
def _EMAIL_FROM():  return os.environ.get('TRACKIT_EMAIL_FROM', '')
def _EMAIL_PASS():  return os.environ.get('TRACKIT_EMAIL_PASSWORD', '')

# Module-level aliases — evaluated lazily via property trick isn't possible for plain
# variables, so we expose a helper instead. Code that did `from email_service import EMAIL_FROM`
# should use email_service.EMAIL_FROM which we keep live via __getattr__ below.
EMAIL_FROM    = os.environ.get('TRACKIT_EMAIL_FROM', '')
EMAIL_PASS    = os.environ.get('TRACKIT_EMAIL_PASSWORD', '')
WEB_BASE_URL  = os.environ.get('TRACKIT_WEB_URL', 'http://127.0.0.1:5502/web_admin')

print(f"[Email Service] Loaded. EMAIL_FROM present at import: {bool(EMAIL_FROM)}")


# ── Temp-password generator ───────────────────────────────────────────────────

def generate_temp_password(length: int = 10) -> str:
    """Generate a random temporary password that meets the 6-char minimum."""
    chars = string.ascii_letters + string.digits + '!@#$'
    # Ensure at least one of each character class
    pwd = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice('!@#$'),
    ]
    pwd += [random.choice(chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return ''.join(pwd)


# ── Email sending ─────────────────────────────────────────────────────────────

def _open_smtp():
    """Open an authenticated Gmail SMTP connection. Prefer SSL 465 (faster here)."""
    global _LAST_ERROR
    email_from, email_pass, _ = _get_email_config()

    if not email_from or not email_pass:
        _set_error(
            'NOT CONFIGURED — set TRACKIT_EMAIL_FROM and '
            'TRACKIT_EMAIL_PASSWORD in backend/.env then restart the backend.'
        )
        return None
    if email_from == 'your.gmail@gmail.com' or email_pass == 'abcdefghijklmnop':
        _set_error(
            'PLACEHOLDER VALUES DETECTED — replace them in backend/.env with a '
            'real Gmail address and App Password, then restart.'
        )
        return None

    attempts = [
        ('ssl', SMTP_SSL_PORT),
        ('starttls', SMTP_STARTTLS_PORT),
    ]
    last_err = None
    for mode, port in attempts:
        try:
            print(f"[Email] Connecting to {SMTP_HOST}:{port} ({mode})...")
            if mode == 'ssl':
                ctx = ssl.create_default_context()
                server = smtplib.SMTP_SSL(
                    SMTP_HOST, port, timeout=SMTP_TIMEOUT, context=ctx
                )
                server.ehlo()
            else:
                server = smtplib.SMTP(SMTP_HOST, port, timeout=SMTP_TIMEOUT)
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            server.login(email_from, email_pass)
            print(f"[Email] Login successful via {mode}:{port}")
            _LAST_ERROR = None
            return server
        except smtplib.SMTPAuthenticationError as e:
            _set_error(
                'Gmail login failed — App Password is wrong or expired. '
                'Generate a new one at Google Account → Security → App Passwords.'
            )
            print(f"[Email] Auth error: {e}")
            return None
        except Exception as e:
            last_err = e
            print(f"[Email] {mode}:{port} failed: {type(e).__name__}: {e}")
            try:
                server.close()  # type: ignore[name-defined]
            except Exception:
                pass

    _set_error(f'Could not connect to Gmail SMTP: {type(last_err).__name__}: {last_err}')
    return None


class SmtpSession:
    """Reusable SMTP connection for bulk sends (Excel import)."""

    def __init__(self):
        self.server = None

    def __enter__(self):
        self.server = _open_smtp()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.server:
            try:
                self.server.quit()
            except Exception:
                try:
                    self.server.close()
                except Exception:
                    pass
        self.server = None
        return False

    def send(self, to: str, subject: str, html_body: str) -> bool:
        if not self.server:
            self.server = _open_smtp()
            if not self.server:
                return False
        ok = _deliver(self.server, to, subject, html_body)
        if not ok:
            # Connection may have dropped — try one fresh reconnect
            try:
                self.server.close()
            except Exception:
                pass
            self.server = _open_smtp()
            if not self.server:
                return False
            ok = _deliver(self.server, to, subject, html_body)
        return ok


def smtp_session() -> SmtpSession:
    return SmtpSession()


def _deliver(server, to: str, subject: str, html_body: str) -> bool:
    email_from, _, _ = _get_email_config()
    if not to or '@' not in to:
        _set_error(f'Invalid recipient address: {to!r}')
        return False
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = f'TrackIt OJT System <{email_from}>'
    msg['To']      = to
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    try:
        server.sendmail(email_from, [to], msg.as_string())
        print(f"[Email] [OK] Sent to {to}: {subject}")
        return True
    except smtplib.SMTPRecipientsRefused as e:
        _set_error(f'Recipient refused: {to}')
        print(f"[Email] [FAIL] Recipient refused: {to}. Error: {e}")
        return False
    except Exception as e:
        _set_error(f'Failed to send to {to}: {type(e).__name__}: {e}')
        return False


def _send(to: str, subject: str, html_body: str, smtp=None) -> bool:
    """Send a single email. Returns True on success, False on failure."""
    print(f"[Email] Attempting to send to {to}...")
    if smtp is not None:
        return smtp.send(to, subject, html_body)

    server = _open_smtp()
    if not server:
        print(f"[Email] Skipping email to {to}")
        return False
    try:
        return _deliver(server, to, subject, html_body)
    finally:
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass


# ── Role-specific email templates ─────────────────────────────────────────────

def _base_html(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  body {{ font-family: Arial, sans-serif; background:#f4f4f4; margin:0; padding:0; }}
  .wrap {{ max-width:600px; margin:30px auto; background:#fff; border-radius:10px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,.1); }}
  .header {{ background:#1565C0; padding:28px 32px; }}
  .header h1 {{ color:#fff; margin:0; font-size:22px; }}
  .header p {{ color:rgba(255,255,255,.8); margin:4px 0 0; font-size:13px; }}
  .body {{ padding:28px 32px; color:#333; line-height:1.6; }}
  .cred-box {{ background:#F0F4FF; border:1px solid #C3D1F5; border-radius:8px; padding:16px 20px; margin:18px 0; }}
  .cred-box .label {{ font-size:11px; color:#666; text-transform:uppercase; letter-spacing:.5px; margin-bottom:2px; }}
  .cred-box .val {{ font-size:16px; font-weight:bold; color:#1565C0; font-family:monospace; }}
  .btn {{ display:inline-block; background:#1565C0; color:#fff!important; text-decoration:none;
          padding:12px 28px; border-radius:6px; font-weight:bold; margin-top:16px; font-size:14px; }}
  .warn {{ background:#FFF8E1; border-left:4px solid #F9A825; padding:12px 16px;
           border-radius:4px; margin:16px 0; font-size:13px; color:#555; }}
  .footer {{ background:#f9f9f9; padding:16px 32px; font-size:11px; color:#999; border-top:1px solid #eee; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>🎓 TrackIt OJT System</h1>
    <p>USTP Oroquieta Campus — Internship Monitoring System</p>
  </div>
  <div class="body">
    <h2 style="margin-top:0;color:#1565C0">{title}</h2>
    {body_html}
  </div>
  <div class="footer">
    This is an automated message from TrackIt OJT System. Do not reply to this email.
  </div>
</div>
</body>
</html>"""


def send_staff_welcome(to: str, name: str, role: str, temp_password: str,
                       login_identifier: str, smtp=None) -> bool:
    """
    Email for Adviser / Supervisor / Chairperson — web login.
    login_identifier is their email address (used to log in).
    """
    _, _, web_base_url = _get_email_config()
    role_label = role.capitalize()
    login_url  = f"{web_base_url}/login.html"
    body = f"""
<p>Hello <strong>{name}</strong>,</p>
<p>Your <strong>{role_label}</strong> account on TrackIt has been created by the Coordinator.
   Below are your temporary login credentials for the <strong>web portal</strong>.</p>

<div class="cred-box">
  <div class="label">Email / Username</div>
  <div class="val">{login_identifier}</div>
</div>
<div class="cred-box">
  <div class="label">Temporary Password</div>
  <div class="val">{temp_password}</div>
</div>

<a href="{login_url}" class="btn">Login to TrackIt Web Portal →</a>

<div class="warn">
  ⚠️ <strong>You must change your password</strong> after your first login.
  Go to <em>Settings → Change Password</em> once you are logged in.
</div>

<p>If you have any questions, contact your OJT Coordinator.</p>
"""
    return _send(to, f'Your TrackIt Account — {role_label} Login Credentials', _base_html(
        f'Welcome to TrackIt, {name}!', body), smtp=smtp)


def send_student_welcome(to: str, name: str, id_number: str,
                         temp_password: str, smtp=None) -> bool:
    """
    Email for Students — Flutter mobile app login.
    Students log in with their ID Number.
    """
    body = f"""
<p>Hello <strong>{name}</strong>,</p>
<p>Your <strong>Student (Intern)</strong> account on TrackIt has been created.
   Use the <strong>TrackIt mobile app</strong> to check in, submit your internship plan,
   and track your OJT hours.</p>

<div class="cred-box">
  <div class="label">Student ID Number (Login)</div>
  <div class="val">{id_number}</div>
</div>
<div class="cred-box">
  <div class="label">Temporary Password</div>
  <div class="val">{temp_password}</div>
</div>

<p style="margin-top:20px"><strong>How to access the app:</strong></p>
<ol style="color:#444;font-size:14px;line-height:1.8">
  <li>Download or open the <strong>TrackIt</strong> mobile app on your Android device.</li>
  <li>On the login screen, enter your <strong>Student ID Number</strong> and the temporary password above.</li>
  <li>Select your role as <strong>Student / Intern</strong>.</li>
  <li>After logging in, go to <em>Profile → Settings</em> to set a new permanent password.</li>
</ol>

<div class="warn">
  ⚠️ <strong>Change your password</strong> in the app after your first login
  (Settings → Change Password).
</div>

<p>If you need assistance, contact your OJT Coordinator or Adviser.</p>
"""
    return _send(to, 'Your TrackIt Account — Mobile App Login Credentials', _base_html(
        f'Welcome to TrackIt, {name}!', body), smtp=smtp)


def send_welcome_email(user: dict, temp_password: str, smtp=None) -> bool:
    """
    Dispatch the correct welcome email based on the user's role.
    user dict must have: name, email, role, and optionally idNumber.
    """
    role  = (user.get('role') or 'student').lower().strip()
    email = (user.get('email') or '').strip()
    name  = (user.get('name') or 'User').strip()

    if not email:
        print(f"[Email] No email address for user '{name}' — skipping welcome email")
        return False

    if role == 'student':
        id_number = (user.get('idNumber') or user.get('id_number') or '').strip()
        return send_student_welcome(email, name, id_number, temp_password, smtp=smtp)
    else:
        return send_staff_welcome(email, name, role, temp_password,
                                  login_identifier=email, smtp=smtp)


# ── Password reset email ──────────────────────────────────────────────────────

def send_password_reset_email(to: str, name: str, reset_link: str) -> bool:
    """
    Send a password-reset email with a one-time, expiring link.
    The link points to the self-hosted reset_password.html page.
    """
    body = f"""
<p>Hello <strong>{name}</strong>,</p>
<p>We received a request to reset your TrackIt password. Click the button below
   to set a new password. <strong>This link will expire in 1 hour</strong> and can
   only be used once.</p>

<a href="{reset_link}" class="btn">Reset My Password →</a>

<p style="margin-top:20px;font-size:13px;color:#666">
  If the button above doesn't work, copy and paste this link into your browser:
  <br><span style="font-family:monospace;font-size:12px;word-break:break-all">{reset_link}</span>
</p>

<div class="warn">
  ⚠️ <strong>If you did not request a password reset</strong>, you can safely
  ignore this email — your password will not be changed.
</div>

<p style="margin-top:16px">For your security, this link expires in 1 hour and
   works only once. After resetting, you will need to log in with your new password.</p>
"""
    return _send(to, 'Reset Your TrackIt Password', _base_html('Password Reset Request', body))


def send_google_account_notice(to: str, name: str, login_url: str) -> bool:
    """
    Email sent when a user tries to reset a password for an account that uses
    Google Sign-In (no password managed by TrackIt). Redirects them to Google.
    """
    body = f"""
<p>Hello <strong>{name}</strong>,</p>
<p>We received a request to reset the password for your TrackIt account.
   However, your account uses <strong>Google Sign-In</strong> — TrackIt does not
   store or manage your password, so there is no password to reset.</p>

<p>To sign in, please use the <strong>Sign in with Google</strong> button on the
   TrackIt login page:</p>

<a href="{login_url}" class="btn">Go to TrackIt Login →</a>

<div class="warn">
  ⚠️ <strong>If you cannot access your Google account</strong>, please recover it
  through Google's account recovery process at
  <a href="https://accounts.google.com/signin/recovery">accounts.google.com/signin/recovery</a>.
</div>

<p>If you did not make this request, you can safely ignore this email.</p>
"""
    return _send(to, 'Your TrackIt Account Uses Google Sign-In', _base_html('Google Sign-In Required', body))
