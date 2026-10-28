"""
TrackIt System - Authentication Routes
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from datetime import datetime, timezone
from services.firebase_service import FirebaseService
from services.email_service import (
    send_password_reset_email, send_google_account_notice, WEB_BASE_URL,
)
import os
import time
from typing import Optional

auth_bp = Blueprint('auth', __name__)

# Valid roles for the web admin panel
ADMIN_ROLES = {'coordinator', 'adviser', 'supervisor', 'chairman'}
# Valid roles for the mobile app
STUDENT_ROLES = {'student'}
ALL_ROLES = ADMIN_ROLES | STUDENT_ROLES

# ── Password-reset rate limiting (in-memory) ────────────────────────────────
# {email: [unix_timestamps]} — allows max 1 request per 60s, 3 per hour
_reset_rate_log: dict = {}
# {ip: [unix_timestamps]} — max 10 requests per hour from one IP
_reset_ip_log: dict = {}

RESET_RATE_MIN_INTERVAL = 60       # seconds between requests for same email
RESET_RATE_MAX_PER_HOUR = 3         # max requests per email per hour
RESET_IP_MAX_PER_HOUR = 10          # max requests per IP per hour


def _check_rate_limit(email: str, ip: str) -> Optional[str]:
    """Return an error message if rate-limited, otherwise None."""
    now = time.time()
    one_hour_ago = now - 3600

    # Per-IP limit
    ip_reqs = [t for t in _reset_ip_log.get(ip, []) if t > one_hour_ago]
    if len(ip_reqs) >= RESET_IP_MAX_PER_HOUR:
        return 'Too many reset requests. Please try again later.'
    # Per-email limit: at least 60s since last request
    email_reqs = [t for t in _reset_rate_log.get(email, []) if t > one_hour_ago]
    if email_reqs and (now - email_reqs[-1]) < RESET_RATE_MIN_INTERVAL:
        return 'Please wait a minute before requesting another reset link.'
    if len(email_reqs) >= RESET_RATE_MAX_PER_HOUR:
        return 'Too many reset requests for this email. Please try again later.'

    # Record the request
    _reset_rate_log.setdefault(email, []).append(now)
    _reset_ip_log.setdefault(ip, []).append(now)
    # Clean old entries
    _reset_rate_log[email] = [t for t in _reset_rate_log[email] if t > one_hour_ago]
    _reset_ip_log[ip] = [t for t in _reset_ip_log[ip] if t > one_hour_ago]
    return None


# Shared service instance — set by app.py so all blueprints use the same object.
# Falls back to creating its own instance if app.py hasn't injected one yet.
_firebase_service = None


def set_firebase_service(service: FirebaseService):
    """Called by app.py to inject the shared FirebaseService instance."""
    global _firebase_service
    _firebase_service = service


def _get_service() -> FirebaseService:
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service


@auth_bp.route('/login', methods=['POST'])
def login():
    """User login endpoint"""
    svc = _get_service()
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    email = data.get('email')
    id_number = data.get('idNumber')
    password = data.get('password')
    role = data.get('role', '').strip().lower() if data.get('role') else None

    if not password:
        return jsonify({'error': 'Password is required'}), 400

    # Support both email and ID number login
    if email:
        result = svc.verify_user(email, password, role)
    elif id_number:
        result = svc.verify_user_by_id(id_number, password, role)
    else:
        return jsonify({'error': 'Email or ID number is required'}), 400

    if result['success']:
        user = result['user']
        # If a role was specified (web admin login), enforce it matches the stored role
        # Also accept legacy aliases: admin = coordinator, employee = adviser
        ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}
        stored_role = user.get('role', '').lower()
        effective_role = ROLE_ALIASES.get(stored_role, stored_role)
        if role and effective_role != role:
            return jsonify({'error': f'Access denied. Your account role is not "{role}".'}), 403
        # Ensure user object reflects the effective role
        user['role'] = effective_role
        access_token = create_access_token(
            identity=user['id'],
            additional_claims={'role': effective_role},
        )
        return jsonify({
            'success': True,
            'token': access_token,
            'user': user
        }), 200
    else:
        return jsonify({'error': result['error']}), 401


@auth_bp.route('/logout', methods=['POST'])
@jwt_required()
def logout():
    """User logout endpoint with automatic checkout"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    existing = svc.get_today_attendance(current_user_id, today)

    if existing and existing.get('checkIn') and not existing.get('checkOut'):
        check_out = datetime.now(timezone.utc)
        check_out_iso = check_out.isoformat()

        # Close any open timeLogs entry so Flutter's isCheckedIn resolves correctly
        time_logs = existing.get('timeLogs') or []
        open_idx = next(
            (i for i, e in reversed(list(enumerate(time_logs)))
             if e.get('checkIn') and not e.get('checkOut')),
            None
        )
        if open_idx is not None:
            entry = time_logs[open_idx]
            entry['checkOut'] = check_out_iso
            try:
                ci = datetime.fromisoformat(entry['checkIn'])
                entry['workHours'] = round((check_out - ci).total_seconds() / 3600, 2)
            except Exception:
                pass
            time_logs[open_idx] = entry

        # Sum all completed log entries for total work hours
        total_hours = sum(float(e.get('workHours') or 0) for e in time_logs if e.get('checkOut'))

        update_data = {
            'timeLogs': time_logs,
            'checkOut': check_out_iso,
            'workHours': round(total_hours, 2),
            'autoCheckout': True,
            'updatedAt': check_out_iso,
        }

        result = svc.update_attendance_record(existing['id'], update_data)
        if result['success']:
            return jsonify({
                'message': 'Logged out successfully',
                'autoCheckout': True,
                'workHours': round(total_hours, 2)
            }), 200

    return jsonify({'message': 'Logged out successfully'}), 200


@auth_bp.route('/register', methods=['POST'])
def register():
    """
    Web-admin registration endpoint.

    Security model:
    - Only Gmail addresses (@gmail.com) are accepted.
    - The email must exist in the Firestore 'authorized_coordinators' collection,
      which is managed exclusively by the system administrator.
    - The role is ALWAYS set to 'coordinator' server-side — the client cannot
      choose or influence it.
    - Passwords are hashed before storage.
    - Duplicate registrations (same email + coordinator role) are rejected.
    """
    svc = _get_service()
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    name     = data.get('name', '').strip()
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')

    # ── Basic field validation ────────────────────────────────────────────────
    if not name:
        return jsonify({'error': 'Full name is required'}), 400

    if not email:
        return jsonify({'error': 'Gmail address is required'}), 400

    if not password:
        return jsonify({'error': 'Password is required'}), 400

    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    # ── Gmail-only gate ───────────────────────────────────────────────────────
    if not email.endswith('@gmail.com'):
        return jsonify({'error': 'Only Gmail addresses (@gmail.com) are accepted for web admin registration.'}), 400

    # ── Authorized coordinator check (Firestore) ──────────────────────────────
    # The 'authorized_coordinators' collection is managed by the system admin.
    # Only emails listed there can register through this endpoint.
    if svc.db:
        try:
            auth_docs = list(
                svc.db.collection('authorized_coordinators')
                .where('email', '==', email)
                .limit(1)
                .stream()
            )
            if not auth_docs:
                print(f"[REGISTER] Unauthorized Gmail attempt: {email!r}")
                return jsonify({
                    'error': 'This Gmail address is not authorized for web admin access. '
                             'Contact the system administrator to request access.'
                }), 403
        except Exception as e:
            print(f"[REGISTER] Error checking authorized_coordinators: {e}")
            return jsonify({'error': 'Could not verify authorization. Please try again.'}), 500

    # ── Duplicate registration check ──────────────────────────────────────────
    if svc.db:
        try:
            existing = list(
                svc.db.collection('users')
                .where('email', '==', email)
                .where('role', '==', 'coordinator')
                .limit(1)
                .stream()
            )
            if existing:
                return jsonify({'error': 'An account with this Gmail address is already registered.'}), 400
        except Exception as e:
            print(f"[REGISTER] Error checking duplicate: {e}")

    # ── Create the user with role='coordinator' (never from client input) ─────
    role = 'coordinator'
    try:
        result = svc.create_user(email, password, name, role)
    except Exception as e:
        print(f"[ERROR] Registration exception: {e}")
        return jsonify({'error': f'Registration error: {str(e)}'}), 500

    if result.get('success'):
        user = result['user']
        access_token = create_access_token(identity=user['id'])
        print(f"[REGISTER] New coordinator registered: {email!r} name={name!r}")
        return jsonify({'success': True, 'token': access_token, 'user': user}), 201
    else:
        error_msg = result.get('error', 'Registration failed')
        print(f"[ERROR] Registration failed for {email!r}: {error_msg}")
        return jsonify({'error': error_msg}), 400


@auth_bp.route('/me', methods=['GET'])
@jwt_required()
def get_current_user():
    """Get current user information"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    if user:
        # Never send the password hash to the browser
        user.pop('password', None)
        return jsonify(user), 200
    else:
        return jsonify({'error': 'User not found'}), 404


@auth_bp.route('/change-password', methods=['POST'])
@jwt_required()
def change_password():
    """Change user password.

    Verifies the current password two ways:
    1. Direct Firestore doc lookup by user_id (from JWT) — fast path.
    2. If that fails, re-verify via email + password login — handles cases
       where the JWT identity resolves to a different doc than the one that
       matched during login (e.g. multiple docs sharing the same email).
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json()

    current_password = data.get('currentPassword')
    new_password = data.get('newPassword')

    if not current_password or not new_password:
        return jsonify({'error': 'Current and new passwords are required'}), 400

    if not new_password or len(new_password) < 6:
        return jsonify({'error': 'New password must be at least 6 characters'}), 400

    # ── Try direct change by user_id ─────────────────────────────────────────
    result = svc.change_password(current_user_id, current_password, new_password)
    if result['success']:
        # Clear the must-change flag once password is updated
        try:
            svc.update_user(current_user_id, {'mustChangePassword': False})
        except Exception:
            pass
        print(f"[CHANGE-PW] Success via direct lookup for user_id={current_user_id}")
        return jsonify({'message': 'Password changed successfully'}), 200

    # ── Fallback: re-verify via email if the user logged in by email ─────────
    # Fetch the user record to get their email, then re-authenticate.
    try:
        user = svc.get_user(current_user_id)
        email = (user or {}).get('email', '').strip().lower() if user else ''
        if email:
            verify = svc.verify_user(email, current_password)
            if verify.get('success'):
                # Password matched on the email-based lookup — find the right doc
                matched_user = verify['user']
                matched_id = matched_user.get('id', current_user_id)
                # Save new password to the matched doc
                save_result = svc.change_password(matched_id, current_password, new_password)
                if save_result['success']:
                    print(f"[CHANGE-PW] Success via email fallback for {email} (doc={matched_id})")
                    try:
                        svc.update_user(matched_id, {'mustChangePassword': False})
                    except Exception:
                        pass
                    return jsonify({'message': 'Password changed successfully'}), 200
                # If still failing, force-write: the verify proved identity
                if svc.db:
                    from services.firebase_service import _hash_password
                    svc.db.collection('users').document(matched_id).update(
                        {'password': _hash_password(new_password), 'mustChangePassword': False}
                    )
                    print(f"[CHANGE-PW] Force-written for {email} (doc={matched_id})")
                    return jsonify({'message': 'Password changed successfully'}), 200
    except Exception as fb_err:
        print(f"[CHANGE-PW] Fallback error: {fb_err}")

    print(f"[CHANGE-PW] Failed for user_id={current_user_id}: {result.get('error')}")
    return jsonify({'error': result.get('error', 'Current password is incorrect')}), 400


@auth_bp.route('/refresh', methods=['POST'])
@jwt_required()
def refresh_token():
    """Refresh access token"""
    current_user_id = get_jwt_identity()
    access_token = create_access_token(identity=current_user_id)
    return jsonify({'access_token': access_token}), 200


# ── GET /api/auth/google-client-id — public, no JWT ──────────────────────────
@auth_bp.route('/google-client-id', methods=['GET'])
def get_google_client_id():
    """Returns the configured Google OAuth client ID so the frontend
    can initialise Google Identity Services without hardcoding it."""
    client_id = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
    return jsonify({'clientId': client_id}), 200


# ── POST /api/auth/google-login ───────────────────────────────────────────────

# Roles that are allowed to use the web admin panel via Google Sign-In.
# Students must use the mobile app — they are excluded intentionally.
GOOGLE_ALLOWED_ROLES = {'coordinator', 'adviser', 'supervisor', 'chairman'}


@auth_bp.route('/google-login', methods=['POST'])
def google_login():
    """
    Web-panel Google Sign-In for authorised staff roles.

    Flow:
      1. Frontend (Google Identity Services) obtains a credential (ID token).
      2. POST that token here as { "credential": "<id_token>" }.
      3. We verify the token with Google's tokeninfo endpoint (audience check).
      4. We look up the verified Google email in Firestore.
      5. We confirm the matched account has a web-panel role (coordinator,
         adviser, supervisor, or chairman).  Students are not allowed.
      6. On success, return a TrackIt JWT containing the user's actual role so
         the frontend can redirect to the correct dashboard.

    Security:
      - The Google ID token is verified server-side via Google's tokeninfo API.
      - The audience claim is validated against GOOGLE_CLIENT_ID so tokens from
        other apps cannot be replayed here.
      - Only pre-registered email addresses that exist in Firestore are accepted.
      - The role stored in Firestore is authoritative — we never trust the client.
      - The previous localStorage session is cleared by the frontend before this
        call, preventing stale account data from leaking across logins.

    Configuration:
      GOOGLE_CLIENT_ID  — your OAuth 2.0 client ID from Google Cloud Console.
                          Set it in backend/.env.  If not set, the endpoint
                          returns 503 so the frontend can show a helpful message.
    """
    import requests as req

    svc = _get_service()
    data = request.get_json(silent=True) or {}
    credential = (data.get('credential') or '').strip()

    if not credential:
        return jsonify({'error': 'Google credential (ID token) is required'}), 400

    google_client_id = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
    if not google_client_id or google_client_id == 'your-google-client-id.apps.googleusercontent.com':
        return jsonify({
            'error': 'Google Sign-In is not configured. '
                     'Set GOOGLE_CLIENT_ID in backend/.env and restart the backend.'
        }), 503

    # ── Verify the ID token with Google ──────────────────────────────────────
    try:
        resp = req.get(
            'https://oauth2.googleapis.com/tokeninfo',
            params={'id_token': credential},
            timeout=8,
        )
        token_data = resp.json()
    except Exception as e:
        print(f"[Google Login] Token verification request failed: {e}")
        return jsonify({'error': 'Could not reach Google servers. Try again.'}), 502

    if resp.status_code != 200:
        error_desc = token_data.get('error_description') or token_data.get('error', 'Invalid token')
        return jsonify({'error': f'Google token invalid: {error_desc}'}), 401

    # ── Validate audience (prevent token reuse from other apps) ───────────────
    aud = token_data.get('aud', '')
    if aud != google_client_id:
        print(f"[Google Login] Audience mismatch: got {aud!r}, expected {google_client_id!r}")
        return jsonify({'error': 'Google token audience mismatch'}), 401

    google_email = (token_data.get('email') or '').strip().lower()
    if not google_email:
        return jsonify({'error': 'Could not retrieve email from Google token'}), 401

    if not token_data.get('email_verified'):
        return jsonify({'error': 'Google account email is not verified'}), 401

    # ── Look up the user in Firestore by verified Google email ───────────────
    # get_all_users() returns every user doc with passwords already stripped.
    all_users = svc.get_all_users()
    matched_user = None
    print(f"[Google Login] Looking up email={google_email!r} among {len(all_users)} users")

    for u in all_users:
        u_email = (u.get('email') or '').strip().lower()
        if u_email != google_email:
            continue

        raw_role = (u.get('role') or '').strip()
        # Normalise legacy aliases
        effective_role = {'admin': 'coordinator', 'employee': 'adviser'}.get(
            raw_role.lower(), raw_role.lower()
        )
        print(f"[Google Login] Email matched — id={u.get('id')!r} role={raw_role!r} effective={effective_role!r}")

        if effective_role not in GOOGLE_ALLOWED_ROLES:
            # Email is registered but the role is not a web-panel role (e.g. student)
            print(f"[Google Login] Role {effective_role!r} not allowed via Google Sign-In")
            return jsonify({
                'error': (
                    f'No authorised Track-IT account is linked to this Google account '
                    f'({google_email}). '
                    'Student accounts must use the mobile app. '
                    'Contact the system administrator if you believe this is an error.'
                )
            }), 403

        # Store the normalised role back so the JWT and response are consistent
        u['role'] = effective_role
        matched_user = u
        break

    if matched_user is None:
        print(f"[Google Login] No Track-IT account found for {google_email!r}")
        return jsonify({
            'error': (
                f'No authorised Track-IT account is linked to this Google account '
                f'({google_email}). '
                'Ask the system administrator to register your email address in Track-IT.'
            )
        }), 403

    # ── Issue a TrackIt JWT scoped to this user's actual role ─────────────────
    user_id = matched_user['id']
    effective_role = matched_user['role']

    # ── Enrich/correct the stored name using Google's verified name ───────────
    # If the Firestore record has a missing or generic placeholder name
    # (e.g. "Coordinator", "Admin", "Admin User"), replace it with the
    # real display name from the verified Google ID token so the dashboard
    # immediately shows the correct person's name.
    google_name = (token_data.get('name') or '').strip()
    google_given = (token_data.get('given_name') or '').strip()
    google_family = (token_data.get('family_name') or '').strip()
    best_google_name = google_name or ' '.join(filter(None, [google_given, google_family]))

    PLACEHOLDER_NAMES = {
        '', 'coordinator', 'adviser', 'supervisor', 'chairman', 'chairperson',
        'admin', 'admin user', 'administrator', 'user', 'staff', 'teacher',
    }
    stored_name = (matched_user.get('name') or '').strip()
    if best_google_name and stored_name.lower() in PLACEHOLDER_NAMES:
        try:
            svc.update_user(user_id, {'name': best_google_name})
            matched_user['name'] = best_google_name
            print(f"[Google Login] Updated name from {stored_name!r} → {best_google_name!r} for {google_email}")
        except Exception as name_err:
            print(f"[Google Login] Could not update name: {name_err}")
            # Non-fatal — still return the Google name in the response even if DB write failed
            matched_user['name'] = best_google_name

    access_token = create_access_token(
        identity=user_id,
        additional_claims={'role': effective_role},
    )
    # Password is already stripped by get_all_users() — remove defensively anyway
    matched_user.pop('password', None)
    print(f"[Google Login] [OK] {google_email} ({effective_role}) signed in via Google")
    return jsonify({
        'success': True,
        'token': access_token,
        'user': matched_user,
    }), 200


# ── POST /api/auth/google-login-code ─────────────────────────────────────────
@auth_bp.route('/google-login-code', methods=['POST'])
def google_login_code():
    """
    Exchange a Google OAuth2 authorization code for user info and a TrackIt JWT.
    Called by the popup-based OAuth2 flow in login.html.
    """
    import requests as req

    svc = _get_service()
    data = request.get_json(silent=True) or {}
    code         = (data.get('code') or '').strip()
    redirect_uri = (data.get('redirectUri') or '').strip()

    if not code:
        return jsonify({'error': 'Authorization code is required'}), 400

    google_client_id     = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
    google_client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()

    if not google_client_id:
        return jsonify({'error': 'Google Sign-In is not configured on the server.'}), 503

    # Exchange code for tokens
    try:
        token_resp = req.post(
            'https://oauth2.googleapis.com/token',
            data={
                'code':          code,
                'client_id':     google_client_id,
                'client_secret': google_client_secret,
                'redirect_uri':  redirect_uri,
                'grant_type':    'authorization_code',
            },
            timeout=10,
        )
        token_data = token_resp.json()
    except Exception as e:
        print(f"[Google Login Code] Token exchange failed: {e}")
        return jsonify({'error': 'Could not reach Google servers. Try again.'}), 502

    if token_resp.status_code != 200:
        err = token_data.get('error_description') or token_data.get('error', 'Token exchange failed')
        print(f"[Google Login Code] Token exchange error: {err}")
        return jsonify({'error': f'Google authentication failed: {err}'}), 401

    access_token = token_data.get('access_token', '')
    if not access_token:
        return jsonify({'error': 'No access token received from Google'}), 401

    # Get verified user info
    try:
        ui_resp = req.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=8,
        )
        userinfo = ui_resp.json()
    except Exception as e:
        return jsonify({'error': 'Could not retrieve Google account info.'}), 502

    if ui_resp.status_code != 200:
        return jsonify({'error': 'Could not verify Google account.'}), 401

    verified_email = (userinfo.get('email') or '').strip().lower()
    if not verified_email:
        return jsonify({'error': 'Could not retrieve email from Google'}), 401
    if not userinfo.get('email_verified'):
        return jsonify({'error': 'Google account email is not verified'}), 401

    google_name = (userinfo.get('name') or '').strip()
    print(f"[Google Login Code] Verified email: {verified_email!r} name: {google_name!r}")

    # Look up in Firestore
    all_users = svc.get_all_users()
    matched_user = None
    for u in all_users:
        u_email = (u.get('email') or '').strip().lower()
        if u_email != verified_email:
            continue
        raw_role = (u.get('role') or '').strip()
        effective_role = {'admin': 'coordinator', 'employee': 'adviser'}.get(
            raw_role.lower(), raw_role.lower()
        )
        if effective_role not in GOOGLE_ALLOWED_ROLES:
            return jsonify({'error': (
                f'No authorised Track-IT account is linked to {verified_email}. '
                'Student accounts must use the mobile app.'
            )}), 403
        u['role'] = effective_role
        matched_user = u
        break

    if not matched_user:
        return jsonify({'error': (
            f'No authorised Track-IT account is linked to {verified_email}. '
            'Ask the system administrator to register your email in Track-IT.'
        )}), 403

    user_id       = matched_user['id']
    effective_role = matched_user['role']

    # Update placeholder name if needed
    PLACEHOLDER_NAMES = {'', 'coordinator', 'adviser', 'supervisor', 'chairman',
                         'chairperson', 'admin', 'admin user', 'administrator', 'user', 'staff'}
    stored_name = (matched_user.get('name') or '').strip()
    if google_name and stored_name.lower() in PLACEHOLDER_NAMES:
        try:
            svc.update_user(user_id, {'name': google_name})
            matched_user['name'] = google_name
        except Exception:
            matched_user['name'] = google_name

    jwt_token = create_access_token(
        identity=user_id,
        additional_claims={'role': effective_role},
    )
    matched_user.pop('password', None)
    print(f"[Google Login Code] [OK] {verified_email} ({effective_role}) signed in")
    return jsonify({'success': True, 'token': jwt_token, 'user': matched_user}), 200



@auth_bp.route('/google-login-email', methods=['POST'])
def google_login_email():
    """
    OAuth2 access-token fallback for Google Sign-In.
    Called when GIS One Tap is suppressed and we use the OAuth2 popup instead.
    Verifies the access token with Google's userinfo endpoint, then looks up
    the user in Firestore by email — same security model as /google-login.
    """
    import requests as req

    svc = _get_service()
    data = request.get_json(silent=True) or {}
    access_token = (data.get('accessToken') or '').strip()
    claimed_email = (data.get('email') or '').strip().lower()

    if not access_token:
        return jsonify({'error': 'Access token is required'}), 400

    # Verify access token with Google
    try:
        resp = req.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=8,
        )
        userinfo = resp.json()
    except Exception as e:
        return jsonify({'error': 'Could not reach Google servers. Try again.'}), 502

    if resp.status_code != 200:
        return jsonify({'error': 'Invalid Google access token'}), 401

    verified_email = (userinfo.get('email') or '').strip().lower()
    print(f"[Google Login Email] Verified email from Google: {verified_email!r}")
    if not verified_email:
        return jsonify({'error': 'Could not retrieve email from Google'}), 401

    if not userinfo.get('email_verified'):
        return jsonify({'error': 'Google account email is not verified'}), 401

    # Extra safety: if frontend sent an email claim, it must match
    if claimed_email and claimed_email != verified_email:
        return jsonify({'error': 'Email mismatch'}), 401

    google_name = (userinfo.get('name') or '').strip()
    given = (userinfo.get('given_name') or '').strip()
    family = (userinfo.get('family_name') or '').strip()
    best_name = google_name or ' '.join(filter(None, [given, family]))

    # Look up user in Firestore
    all_users = svc.get_all_users()
    matched_user = None
    for u in all_users:
        u_email = (u.get('email') or '').strip().lower()
        if u_email != verified_email:
            continue
        raw_role = (u.get('role') or '').strip()
        effective_role = {'admin': 'coordinator', 'employee': 'adviser'}.get(
            raw_role.lower(), raw_role.lower()
        )
        if effective_role not in GOOGLE_ALLOWED_ROLES:
            return jsonify({'error': (
                f'No authorised Track-IT account is linked to this Google account ({verified_email}). '
                'Student accounts must use the mobile app.'
            )}), 403
        u['role'] = effective_role
        matched_user = u
        break

    if not matched_user:
        return jsonify({'error': (
            f'No authorised Track-IT account is linked to this Google account ({verified_email}). '
            'Ask the system administrator to register your email in Track-IT.'
        )}), 403

    user_id = matched_user['id']
    effective_role = matched_user['role']

    # Update placeholder name if needed
    PLACEHOLDER_NAMES = {'', 'coordinator', 'adviser', 'supervisor', 'chairman',
                         'chairperson', 'admin', 'admin user', 'administrator', 'user', 'staff'}
    stored_name = (matched_user.get('name') or '').strip()
    if best_name and stored_name.lower() in PLACEHOLDER_NAMES:
        try:
            svc.update_user(user_id, {'name': best_name})
            matched_user['name'] = best_name
        except Exception:
            matched_user['name'] = best_name

    access_token_jwt = create_access_token(
        identity=user_id,
        additional_claims={'role': effective_role},
    )
    matched_user.pop('password', None)
    print(f"[Google Login Email] [OK] {verified_email} ({effective_role}) signed in via OAuth2 popup")
    return jsonify({'success': True, 'token': access_token_jwt, 'user': matched_user}), 200



@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    """
    Send a password-reset email to the given address.
    Uses a custom, one-time, expiring token stored in Firestore.

    Security:
      - Always returns 200 with a neutral message (anti-enumeration).
      - Rate-limited per email and per IP.
      - Google-only accounts (no password) get a Google Sign-In notice.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    client_ip = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip() \
        or request.remote_addr or 'unknown'

    if not email or '@' not in email:
        return jsonify({'error': 'A valid email address is required.'}), 400

    # Rate limit check — still return 200 to avoid leaking rate-limit state
    rate_err = _check_rate_limit(email, client_ip)
    if rate_err:
        return jsonify({
            'success': True,
            'message': 'If that email is registered, a password reset link has been sent.',
        }), 200

    svc = _get_service()
    users = svc.find_users_by_email(email)

    if not users:
        # Email not registered — return neutral message, don't send email
        return jsonify({
            'success': True,
            'message': 'If that email is registered, a password reset link has been sent.',
        }), 200

    # Check if ALL matching accounts are Google-only (no password)
    google_only = all(not u.get('password') for u in users)

    if google_only:
        # Account uses Google Sign-In — send a notice email instead
        name = users[0].get('name') or 'User'
        login_url = f"{WEB_BASE_URL}/login.html"
        try:
            send_google_account_notice(email, name, login_url)
        except Exception as e:
            print(f"[FORGOT PASSWORD] Google notice email failed: {e}")
        return jsonify({
            'success': True,
            'message': 'If that email is registered, a password reset link has been sent.',
        }), 200

    # Create the one-time reset token
    raw_token = svc.create_password_reset_token(email)
    if not raw_token:
        return jsonify({
            'success': True,
            'message': 'If that email is registered, a password reset link has been sent.',
        }), 200

    # Build the reset link and send the email
    reset_link = f"{WEB_BASE_URL}/reset_password.html?token={raw_token}"
    name = users[0].get('name') or 'User'
    try:
        sent = send_password_reset_email(email, name, reset_link)
        if not sent:
            print(f"[FORGOT PASSWORD] Email not sent (SMTP not configured?) to {email}")
    except Exception as e:
        print(f"[FORGOT PASSWORD] Email send error: {e}")

    return jsonify({
        'success': True,
        'message': 'If that email is registered, a password reset link has been sent.',
    }), 200


# ── GET /api/auth/verify-reset-token ─────────────────────────────────────────
@auth_bp.route('/verify-reset-token', methods=['GET'])
def verify_reset_token():
    """Check whether a reset token is valid (exists, not expired, not used)."""
    token = (request.args.get('token') or '').strip()
    if not token:
        return jsonify({'valid': False, 'reason': 'No reset token provided.'}), 400

    svc = _get_service()
    result = svc.verify_password_reset_token(token)
    return jsonify(result), 200 if result['valid'] else 400


# ── POST /api/auth/reset-password ────────────────────────────────────────────
@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    """
    Set a new password using a valid reset token.
    Does NOT sign the user in; does NOT change roles or other account info.
    """
    data = request.get_json(silent=True) or {}
    token = (data.get('token') or '').strip()
    new_password = (data.get('newPassword') or '').strip()
    confirm_password = (data.get('confirmPassword') or '').strip()

    if not token:
        return jsonify({'error': 'Invalid or missing reset token.'}), 400
    if not new_password:
        return jsonify({'error': 'New password is required.'}), 400
    if new_password != confirm_password:
        return jsonify({'error': 'Passwords do not match.'}), 400
    if len(new_password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters long.'}), 400

    svc = _get_service()
    token_info = svc.verify_password_reset_token(token)
    if not token_info['valid']:
        return jsonify({'error': token_info.get('reason', 'Invalid reset link.')}), 400

    email = token_info.get('email', '')
    if not email:
        return jsonify({'error': 'Invalid reset link.'}), 400

    # Set the new password (only updates the password field — no role/info change)
    result = svc.set_password_for_email(email, new_password)
    if not result['success']:
        return jsonify({'error': result.get('error', 'Could not reset password.')}), 500

    # Mark the token as used (one-time enforcement)
    svc.use_password_reset_token(token)

    return jsonify({
        'success': True,
        'message': 'Your password has been reset successfully. Please log in with your new password.',
    }), 200
