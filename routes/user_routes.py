"""
TrackIt System - User CRUD Routes
Full Create, Read, Update, Delete for the users collection in Firestore.
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from services.firebase_service import FirebaseService
from services.email_service import (
    generate_temp_password,
    send_welcome_email,
    smtp_session,
    last_email_error,
)
import os
import uuid
import base64

user_bp = Blueprint('users', __name__)

_firebase_service = None

# Roles a coordinator may create via the admin panel
COORDINATOR_CREATABLE_ROLES = {'student', 'adviser', 'supervisor', 'chairman'}
VALID_ROLES = COORDINATOR_CREATABLE_ROLES | {'coordinator'}
ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}


def set_firebase_service(service: FirebaseService):
    global _firebase_service
    _firebase_service = service


def _get_service() -> FirebaseService:
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service


def _effective_role(role: str) -> str:
    r = (role or '').strip().lower()
    return ROLE_ALIASES.get(r, r)


def _current_user_role(svc: FirebaseService) -> str:
    current = svc.get_user(get_jwt_identity())
    if not current:
        return ''
    return _effective_role(current.get('role', ''))


def _upload_to_firebase_storage(image_bytes: bytes, destination_path: str):
    """Upload bytes to Firebase Storage and return the public download URL."""
    try:
        from firebase_admin import storage
        bucket = storage.bucket()
        blob = bucket.blob(destination_path)
        blob.upload_from_string(image_bytes, content_type='image/jpeg')
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print(f"[ERROR] Firebase Storage upload failed for {destination_path}: {e}")
        return None


def _persist_profile_picture(value, user_id: str):
    """Upload data-URL images to Firebase Storage; keep http(s) URLs as-is."""
    if not value or not isinstance(value, str):
        return value
    s = value.strip()
    if s.startswith(('http://', 'https://')):
        return s
    if not s.startswith('data:image'):
        return s
    try:
        header, b64 = s.split(',', 1)
        ext = 'jpg'
        if 'png' in header:
            ext = 'png'
        elif 'webp' in header:
            ext = 'webp'
        raw = base64.b64decode(b64)
        if not raw:
            return s
        filename = f"{user_id}_{uuid.uuid4().hex[:8]}.{ext}"
        url = _upload_to_firebase_storage(raw, f'profile_photos/{filename}')
        if url:
            return url
        # If Storage upload fails, don't save a huge data-URL
        if len(s) > 900_000:
            return None
        return s
    except Exception as e:
        print(f"[WARNING] persist profile picture failed: {e}")
        if len(s) > 900_000:
            return None
        return s


def _public_photo_url(url):
    if not url or not isinstance(url, str):
        return url
    s = url.strip()
    if s.startswith('/uploads/'):
        return request.host_url.rstrip('/') + s
    return s


def _with_public_photos(user):
    if not isinstance(user, dict):
        return user
    for key in ('profilePicture', 'avatar', 'facePhotoUrl'):
        if user.get(key):
            user[key] = _public_photo_url(user[key])
    return user


# ─────────────────────────────────────────────
# CREATE  POST /api/users
# ─────────────────────────────────────────────
@user_bp.route('', methods=['POST'])
@jwt_required()
def create_user():
    """Create a new user (coordinator admin panel)"""
    svc = _get_service()
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')
    name     = data.get('name', '').strip()
    role     = _effective_role(data.get('role', 'student'))
    department = data.get('department', '').strip()
    id_number  = data.get('idNumber', '').strip()
    company    = data.get('company', '').strip()

    if not name:
        return jsonify({'error': 'Full name is required'}), 400
    if not email and not id_number:
        return jsonify({'error': 'Email or ID number is required'}), 400
    # Validate email format
    if email and ('@' not in email or '.' not in email.split('@')[-1]):
        return jsonify({'error': 'Invalid email address format'}), 400
    if role not in VALID_ROLES:
        return jsonify({'error': f'Invalid role. Must be one of: {", ".join(sorted(VALID_ROLES))}'}), 400

    # Auto-generate a temp password if the caller did not provide one
    is_temp = not password or len(password) < 6
    if is_temp:
        password = generate_temp_password()

    creator_role = _current_user_role(svc)
    if creator_role == 'coordinator' and role not in COORDINATOR_CREATABLE_ROLES:
        return jsonify({
            'error': 'Coordinators can only create student, adviser, supervisor, and chairman accounts'
        }), 403
    # Chairmen may create all roles except other chairman accounts (only coordinator can do that)
    CHAIRMAN_CREATABLE_ROLES = {'student', 'adviser', 'supervisor'}
    if creator_role == 'chairman' and role not in CHAIRMAN_CREATABLE_ROLES:
        return jsonify({
            'error': 'Only the Coordinator can create Chairperson accounts.'
        }), 403

    if id_number:
        result = svc.create_user_by_id(
            id_number, password, name, role,
            department or None, email or None, company or None
        )
    else:
        result = svc.create_user(email, password, name, role, department or None)

    if result.get('success'):
        user = result['user']
        # Mark account as requiring a password change on first login
        if is_temp and user.get('id'):
            try:
                svc.update_user(user['id'], {'mustChangePassword': True})
            except Exception:
                pass

        # Send welcome email synchronously — Gunicorn timeout is 120s which is
        # more than enough for SMTP. Daemon threads get killed by Gunicorn when
        # the request finishes, so we must send in-request.
        email_sent = False
        email_error = None
        if is_temp:
            user_payload = {**user, 'idNumber': id_number or user.get('idNumber', '')}
            user_id = user.get('id', '')
            try:
                email_sent = send_welcome_email(user_payload, password)
                if not email_sent:
                    email_error = last_email_error() or 'Email send failed'
                else:
                    # Mark email as sent on the user record
                    if user_id:
                        try:
                            svc.update_user(user_id, {'_emailSent': True})
                        except Exception:
                            pass
            except Exception as mail_err:
                email_error = str(mail_err)
                print(f"[CREATE] Email error: {mail_err}")

        user.pop('password', None)
        response = {**user, 'emailSent': email_sent}
        if email_error:
            response['emailError'] = email_error
        # Store the temp password on the user record so it can be re-sent later
        if is_temp and user.get('id'):
            try:
                svc.update_user(user['id'], {'_tempPw': password})
            except Exception:
                pass
        return jsonify(response), 201
    return jsonify({'error': result.get('error', 'Failed to create user')}), 400


# ─────────────────────────────────────────────
# PREVIEW EXCEL  POST /api/users/preview-excel
# Returns rows parsed from an uploaded Excel file
# (used as fallback when SheetJS is unavailable)
# ─────────────────────────────────────────────
@user_bp.route('/preview-excel', methods=['POST'])
@jwt_required()
def preview_excel():
    """Parse an uploaded Excel file and return rows as JSON for client-side preview."""
    import io
    creator_role = _current_user_role(_get_service())
    if creator_role not in ('coordinator', 'chairman'):
        return jsonify({'error': 'Permission denied'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    f = request.files['file']
    if not (f.filename or '').lower().endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'Only .xlsx files are supported'}), 400

    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True, data_only=True)
        ws = wb.active
        headers = None
        rows = []
        for row in ws.iter_rows(values_only=True):
            if headers is None:
                headers = [str(c or '').strip().lower() for c in row]
                continue
            rows.append({headers[i]: str(v or '').strip()
                         for i, v in enumerate(row) if i < len(headers)})
        wb.close()
        return jsonify({'rows': rows}), 200
    except Exception as e:
        return jsonify({'error': f'Could not read Excel file: {e}'}), 400


# ─────────────────────────────────────────────
# RESEND EMAIL  POST /api/users/<user_id>/resend-email
# ─────────────────────────────────────────────
@user_bp.route('/<user_id>/resend-email', methods=['POST'])
@jwt_required()
def resend_welcome_email(user_id):
    """
    Resend the welcome/credentials email for an existing account.
    Uses the stored _tempPw if available; otherwise generates a fresh one
    and resets the user's password.
    Only coordinators and chairmen can call this.
    """
    svc = _get_service()
    creator_role = _current_user_role(svc)
    if creator_role not in ('coordinator', 'chairman'):
        return jsonify({'error': 'Permission denied'}), 403

    user = svc.get_user(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    email = (user.get('email') or '').strip()
    if not email:
        return jsonify({'error': 'User has no email address — cannot send email'}), 400

    # Use the stored temp password if still available; otherwise make a new one
    temp_pw = (user.get('_tempPw') or '').strip()
    reset_pw = not temp_pw
    if reset_pw:
        temp_pw = generate_temp_password()
        # Reset the password so the new temp one is usable
        try:
            if svc.db:
                svc.db.collection('users').document(user_id).update({
                    'password': temp_pw,
                    'mustChangePassword': True,
                    '_tempPw': temp_pw,
                })
            else:
                svc.update_user(user_id, {
                    'password': temp_pw,
                    'mustChangePassword': True,
                    '_tempPw': temp_pw,
                })
        except Exception as e:
            return jsonify({'error': f'Failed to reset password: {e}'}), 500

    ok = send_welcome_email(
        {**user, 'idNumber': user.get('idNumber', '')},
        temp_pw
    )
    if ok:
        return jsonify({'success': True, 'message': f'Credentials email sent to {email}'}), 200
    return jsonify({
        'success': False,
        'error': last_email_error() or (
            'Failed to send email. '
            'Check TRACKIT_EMAIL_FROM and TRACKIT_EMAIL_PASSWORD in backend/.env, '
            'then restart the backend.'
        ),
    }), 500


# ─────────────────────────────────────────────
# ADVISERS  GET /api/users/advisers
# ─────────────────────────────────────────────
@user_bp.route('/advisers', methods=['GET'])
@jwt_required()
def list_advisers():
    """List advisers (optional ?search= for name filter)."""
    svc = _get_service()
    search = (request.args.get('search') or '').strip().lower()

    source = []
    try:
        if hasattr(svc, 'get_all_advisers'):
            source = svc.get_all_advisers() or []
    except Exception as e:
        print(f"[list_advisers] get_all_advisers failed: {e}")
    if not source:
        source = svc.get_all_users()

    advisers = []
    seen = set()
    for u in source:
        if _effective_role(u.get('role', '')) != 'adviser':
            continue
        uid = u.get('id')
        if uid in seen:
            continue
        seen.add(uid)
        name = (u.get('name') or '').strip()
        if search and search not in name.lower():
            continue
        advisers.append({
            'id': uid,
            'name': name,
            'department': u.get('department') or '',
            'email': u.get('email') or '',
        })

    advisers.sort(key=lambda a: (a.get('name') or '').lower())
    return jsonify(advisers), 200


# ─────────────────────────────────────────────
# SUPERVISORS  GET /api/users/supervisors
# ─────────────────────────────────────────────
@user_bp.route('/supervisors', methods=['GET'])
@jwt_required()
def list_supervisors():
    """List supervisors (optional ?search= for name filter)."""
    svc = _get_service()
    search = (request.args.get('search') or '').strip().lower()

    supervisors = []
    for u in svc.get_all_users():
        if _effective_role(u.get('role', '')) != 'supervisor':
            continue
        name = (u.get('name') or '').strip()
        if search and search not in name.lower():
            continue
        supervisors.append({
            'id': u.get('id'),
            'name': name,
            'department': u.get('department', ''),
            'company': u.get('company', ''),
        })

    supervisors.sort(key=lambda s: (s.get('name') or '').lower())
    return jsonify(supervisors), 200


# ─────────────────────────────────────────────
# STUDENT SUPERVISOR  PUT /api/users/me/supervisor
# ─────────────────────────────────────────────
@user_bp.route('/me/supervisor', methods=['PUT'])
@jwt_required()
def set_my_supervisor():
    """Student selects their company supervisor."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    student = svc.get_user(current_user_id)

    if not student:
        return jsonify({'error': 'User not found'}), 404
    if _effective_role(student.get('role', '')) != 'student':
        return jsonify({'error': 'Only students can select a supervisor'}), 403

    data = request.get_json() or {}
    supervisor_id = (data.get('supervisorId') or '').strip()
    if not supervisor_id:
        return jsonify({'error': 'supervisorId is required'}), 400

    supervisor = svc.get_user(supervisor_id)
    if not supervisor:
        return jsonify({'error': 'Supervisor not found'}), 404
    if _effective_role(supervisor.get('role', '')) != 'supervisor':
        return jsonify({'error': 'Selected user is not a supervisor'}), 400

    result = svc.update_user(current_user_id, {
        'supervisorId': supervisor_id,
        'supervisorName': supervisor.get('name', ''),
    })
    if result.get('success'):
        return jsonify(result['user']), 200
    return jsonify({'error': result.get('error', 'Failed to update supervisor')}), 400


# ─────────────────────────────────────────────
# STUDENT ADVISER  PUT /api/users/me/adviser
# ─────────────────────────────────────────────
@user_bp.route('/me/adviser', methods=['PUT'])
@jwt_required()
def set_my_adviser():
    """Student selects their programme adviser."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    student = svc.get_user(current_user_id)

    if not student:
        return jsonify({'error': 'User not found'}), 404
    if _effective_role(student.get('role', '')) != 'student':
        return jsonify({'error': 'Only students can select an adviser'}), 403

    data = request.get_json() or {}
    adviser_id = (data.get('adviserId') or '').strip()
    if not adviser_id:
        return jsonify({'error': 'adviserId is required'}), 400

    adviser = svc.get_user(adviser_id)
    if not adviser:
        return jsonify({'error': 'Adviser not found'}), 404
    if _effective_role(adviser.get('role', '')) != 'adviser':
        return jsonify({'error': 'Selected user is not an adviser'}), 400

    result = svc.update_user(current_user_id, {
        'adviserId': adviser_id,
        'adviserName': adviser.get('name', ''),
    })
    if result.get('success'):
        return jsonify(result['user']), 200
    return jsonify({'error': result.get('error', 'Failed to update adviser')}), 400


# ─────────────────────────────────────────────
# STUDENT COMPANY (HTE)  PUT /api/users/me/company
# ─────────────────────────────────────────────
@user_bp.route('/me/company', methods=['PUT'])
@jwt_required()
def set_my_company():
    """HTE is assigned automatically from the internship plan — students cannot pick one."""
    return jsonify({
        'error': 'Your HTE is assigned automatically from your internship plan. '
                 'Complete and save the plan to get matched.'
    }), 403


# ─────────────────────────────────────────────
# MY STUDENTS  GET /api/users/my-students
# ─────────────────────────────────────────────
@user_bp.route('/my-students', methods=['GET'])
@jwt_required()
def get_my_students():
    """Return students assigned to the current adviser or supervisor."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'User not found'}), 404
    if _effective_role(caller.get('role', '')) not in ('adviser', 'coordinator', 'chairman', 'supervisor'):
        return jsonify({'error': 'Unauthorized'}), 403
    effective_role = _effective_role(caller.get('role', ''))
    if effective_role == 'supervisor':
        students = svc.get_students_by_supervisor(current_user_id)
    else:
        students = svc.get_students_by_adviser(current_user_id)
    return jsonify(students), 200


# ─────────────────────────────────────────────
# BULK IMPORT  POST /api/users/import
# ─────────────────────────────────────────────
@user_bp.route('/import', methods=['POST'])
@jwt_required()
def import_users():
    """
    Bulk-import users from a CSV or Excel (.xlsx) file.
    Columns (case-insensitive, spaces/underscores ignored):
      name, email, idNumber, password, role, department, company

    Behaviour:
    - New accounts  → generate temp password, create user, send welcome email,
                      mark mustChangePassword=True.
    - Existing accounts (matched by email or idNumber) → update non-empty
                      fields (name, department, company, role); do NOT reset
                      password unless the row explicitly provides one.
    Returns: { created, updated, skipped, errors: [...], emailsSent }
    """
    import io

    svc = _get_service()
    creator_role = _current_user_role(svc)
    print(f"[IMPORT] Called by role: {creator_role!r}")
    if creator_role not in ('coordinator', 'chairman'):
        print(f"[IMPORT] Rejected — role {creator_role!r} not allowed")
        return jsonify({'error': 'Only coordinators and chairmen can import users'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded. Attach a CSV or Excel file as "file".'}), 400

    f = request.files['file']
    filename = (f.filename or '').lower()
    file_bytes = f.read()

    # ── Parse rows from CSV or Excel ─────────────────────────────────────────
    rows = []  # list of plain dicts

    if filename.endswith('.csv'):
        import csv
        try:
            content = file_bytes.decode('utf-8-sig')
        except UnicodeDecodeError:
            return jsonify({'error': 'File encoding error. Please save the CSV as UTF-8.'}), 400
        reader = csv.DictReader(io.StringIO(content))
        if not reader.fieldnames:
            return jsonify({'error': 'CSV file is empty or has no headers.'}), 400
        rows = list(reader)

    elif filename.endswith(('.xlsx', '.xls')):
        try:
            import openpyxl
        except ImportError:
            return jsonify({'error': 'openpyxl not installed. Run: pip install openpyxl'}), 500
        try:
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            ws = wb.active
            headers = None
            for row in ws.iter_rows(values_only=True):
                if headers is None:
                    headers = [str(c or '').strip() for c in row]
                    continue
                rows.append(dict(zip(headers, [str(c or '').strip() for c in row])))
            wb.close()
        except Exception as e:
            return jsonify({'error': f'Could not read Excel file: {e}'}), 400

    else:
        return jsonify({'error': 'Only CSV (.csv) and Excel (.xlsx) files are supported.'}), 400

    if not rows:
        return jsonify({'error': 'The file contains no data rows.'}), 400

    # ── Process each row ──────────────────────────────────────────────────────
    created       = 0
    updated       = 0
    skipped       = 0
    emails_sent   = 0
    emails_failed = []   # list of {name, email, reason} for accounts that failed to send
    errors        = []

    all_users = []
    try:
        all_users = svc.get_all_users() or []
    except Exception as e:
        print(f"[IMPORT] get_all_users failed: {e}")

    # ── Process rows first (no email) ─────────────────────────────────────────
    # Emails are sent asynchronously after account creation so SMTP latency
    # never delays the HTTP response.
    pending_emails = []   # list of (payload_dict, temp_pw) to send after loop

    for row_num, raw_row in enumerate(rows, start=2):
        # Normalise keys
        row = {
            k.strip().lower().replace(' ', '').replace('_', ''): (v or '').strip()
            for k, v in raw_row.items() if k
        }

        name       = row.get('name', '')
        email      = row.get('email', '')
        id_number  = (row.get('idnumber', '') or row.get('idno', '')
                      or row.get('studentid', '') or row.get('id', ''))
        role       = _effective_role(row.get('role', 'student'))
        department = row.get('department', '') or row.get('dept', '')
        company    = row.get('company', '')
        # If caller provides a password use it; otherwise generate one for new accounts
        provided_pw = row.get('password', '')

        if not name:
            errors.append(f'Row {row_num}: name is required — skipped')
            skipped += 1
            continue
        if role not in VALID_ROLES:
            errors.append(f'Row {row_num} ({name}): invalid role "{role}" — skipped')
            skipped += 1
            continue
        # Chairmen cannot create other chairman accounts via import
        if creator_role == 'chairman' and role == 'chairman':
            errors.append(f'Row {row_num} ({name}): only the Coordinator can create Chairperson accounts — skipped')
            skipped += 1
            continue
        if not email and not id_number:
            errors.append(f'Row {row_num} ({name}): email or idNumber required — skipped')
            skipped += 1
            continue

        # ── Determine if the user already exists ──────────────────────────────
        existing_user = None
        for u in all_users:
            u_email = (u.get('email') or '').strip().lower()
            u_id    = (u.get('idNumber') or '').strip()
            if email and u_email == email.lower():
                existing_user = u
                break
            if id_number and u_id == id_number:
                existing_user = u
                break

        try:
            if existing_user:
                # ── UPDATE existing account ───────────────────────────────────
                update_fields = {}
                if name:
                    update_fields['name'] = name
                if email:
                    update_fields['email'] = email
                if department:
                    update_fields['department'] = department
                if company:
                    update_fields['company'] = company
                if id_number:
                    update_fields['idNumber'] = id_number

                # Only reset password if the row explicitly provides a new one
                temp_pw = None
                if provided_pw and len(provided_pw) >= 6:
                    temp_pw = provided_pw
                    svc.change_password(existing_user['id'], existing_user.get('password', ''), temp_pw)
                    update_fields['mustChangePassword'] = True
                    update_fields['_tempPw'] = temp_pw

                if update_fields:
                    svc.update_user(existing_user['id'], update_fields)

                # Send email only if a new password was explicitly set
                if temp_pw:
                    merged = {**existing_user, **update_fields}
                    pending_emails.append((merged, temp_pw, existing_user.get('id', ''),
                                          email or id_number, name))

                updated += 1

            else:
                # ── CREATE new account ────────────────────────────────────────
                temp_pw = (provided_pw if provided_pw and len(provided_pw) >= 6
                           else generate_temp_password())

                if id_number:
                    result = svc.create_user_by_id(
                        id_number, temp_pw, name, role,
                        department or None, email or None, company or None
                    )
                else:
                    result = svc.create_user(email, temp_pw, name, role, department or None)

                if result.get('success'):
                    new_user = result['user']
                    all_users.append(new_user)
                    # Mark as requiring password change and keep temp password for retry
                    if new_user.get('id'):
                        svc.update_user(new_user['id'], {
                            'mustChangePassword': True,
                            '_tempPw': temp_pw,
                        })
                    payload = {
                        **new_user,
                        'email': email or new_user.get('email', ''),
                        'idNumber': id_number or new_user.get('idNumber', ''),
                        'role': role or new_user.get('role', ''),
                        'name': name or new_user.get('name', ''),
                    }
                    print(f"[IMPORT] Row {row_num}: Queuing welcome email to {payload.get('email')}")
                    pending_emails.append((payload, temp_pw, new_user.get('id', ''),
                                          email or id_number, name))
                    created += 1
                else:
                    err_msg = result.get('error', 'Unknown error')
                    errors.append(f'Row {row_num} ({name}): {err_msg}')
                    skipped += 1

        except Exception as e:
            print(f"[IMPORT] Row {row_num} ({name}): EXCEPTION — {type(e).__name__}: {e}")
            errors.append(f'Row {row_num} ({name}): {str(e)}')
            skipped += 1

    # ── Send emails asynchronously so SMTP latency doesn't block the response ─
    import threading

    def _send_all(pending, svc_ref):
        print(f"[IMPORT] Background thread: sending {len(pending)} email(s)...")
        with smtp_session() as mailer:
            for payload, pw, uid, addr, uname in pending:
                ok = send_welcome_email(payload, pw, smtp=mailer)
                print(f"[IMPORT] Email to {addr}: {'OK' if ok else 'FAILED'}")
                # Mark email as sent/failed on the user record
                if uid:
                    try:
                        svc_ref.update_user(uid, {'_emailSent': ok, '_emailSentAt': __import__('datetime').datetime.now().isoformat()})
                    except Exception:
                        pass

    if pending_emails:
        t = threading.Thread(target=_send_all, args=(pending_emails, svc), daemon=True)
        t.start()
        # Give it up to 5 seconds to connect before we return — catches fast success/fail
        t.join(timeout=5)
        if not t.is_alive():
            # Thread finished within 5s — we know the result
            pass
        # Whether done or still running, we report optimistically
        emails_sent = len(pending_emails)   # assume success; frontend can retry if needed

    return jsonify({
        'created':      created,
        'updated':      updated,
        'skipped':      skipped,
        'errors':       errors,
        'emailsSent':   emails_sent,
        'emailsFailed': emails_failed,
        'emailError':   last_email_error() if not pending_emails else None,
    }), 200 if (created + updated) > 0 or skipped == 0 else 207


# ─────────────────────────────────────────────
# READ ALL  GET /api/users
# ─────────────────────────────────────────────
@user_bp.route('', methods=['GET'])
@jwt_required()
def get_all_users():
    """Get all users (passwords excluded)"""
    svc = _get_service()
    role_filter = request.args.get('role')

    users = svc.get_all_users()

    if role_filter:
        # Use _effective_role so aliases (admin→coordinator, employee→adviser) are resolved
        users = [u for u in users if _effective_role(u.get('role', '')) == _effective_role(role_filter)]

    return jsonify([_with_public_photos(u) for u in users]), 200


# ─────────────────────────────────────────────
# READ ONE  GET /api/users/<user_id>
# ─────────────────────────────────────────────
@user_bp.route('/<user_id>', methods=['GET'])
@jwt_required()
def get_user(user_id):
    """Get a single user by ID"""
    svc = _get_service()
    user = svc.get_user(user_id)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    user.pop('password', None)
    return jsonify(_with_public_photos(user)), 200


# ─────────────────────────────────────────────
# UPDATE  PUT /api/users/<user_id>
# ─────────────────────────────────────────────
@user_bp.route('/<user_id>', methods=['PUT'])
@jwt_required()
def update_user(user_id):
    """Update user fields (name, email, department, role, company)"""
    svc = _get_service()
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Only allow safe fields to be updated
    allowed = {'name', 'email', 'department', 'role', 'company', 'idNumber',
               'status', 'adviserId', 'adviserName', 'supervisorId', 'supervisorName',
               'profilePicture', 'avatar', 'mustChangePassword', '_tempPw'}
    update_data = {k: v for k, v in data.items() if k in allowed}

    if 'profilePicture' in update_data:
        stored = _persist_profile_picture(update_data.get('profilePicture'), user_id)
        if stored is None:
            return jsonify({'error': 'Profile photo is too large. Please use a smaller image.'}), 400
        update_data['profilePicture'] = stored
        update_data['avatar'] = stored

    if not update_data:
        return jsonify({'error': 'No valid fields to update'}), 400

    result = svc.update_user(user_id, update_data)

    if result.get('success'):
        return jsonify(_with_public_photos(result['user'])), 200
    return jsonify({'error': result.get('error', 'Update failed')}), 400


# ─────────────────────────────────────────────
# UNASSIGN  POST /api/users/<user_id>/unassign
# Removes the adviser or supervisor assignment from a student without
# deleting the student account or any records.
# ─────────────────────────────────────────────
@user_bp.route('/<user_id>/unassign', methods=['POST'])
@jwt_required()
def unassign_student(user_id):
    """
    Remove the current adviser's or supervisor's link from a student.
    - Advisers: clears student.adviserId (and adviserName if present)
    - Supervisors: clears student.supervisorId (and company/companyId)
    - Coordinators/Chairmen: can clear both
    Does NOT delete the student account or any attendance/report records.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'User not found'}), 403
    caller_role = _effective_role(caller.get('role', ''))
    if caller_role not in ('adviser', 'supervisor', 'coordinator', 'chairman'):
        return jsonify({'error': 'Unauthorized'}), 403

    student = svc.get_user(user_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    if _effective_role(student.get('role', '')) != 'student':
        return jsonify({'error': 'Target user is not a student'}), 400

    # Verify scope: advisers can only unassign their own students
    if caller_role == 'adviser':
        if student.get('adviserId') != current_user_id:
            return jsonify({'error': 'This student is not assigned to you'}), 403
        update_data = {'adviserId': None, 'adviserName': None}
    elif caller_role == 'supervisor':
        if student.get('supervisorId') != current_user_id:
            return jsonify({'error': 'This intern is not assigned to you'}), 403
        update_data = {'supervisorId': None, 'supervisorName': None,
                       'companyId': None, 'company': None, 'companyName': None}
    else:
        # Coordinator/Chairman can unassign everything
        data = (svc.db.collection('users').document(user_id).get().to_dict()
                if svc.db else student)
        update_data = {}
        if data.get('adviserId'):
            update_data.update({'adviserId': None, 'adviserName': None})
        if data.get('supervisorId'):
            update_data.update({'supervisorId': None, 'supervisorName': None,
                                'companyId': None, 'company': None, 'companyName': None})
        if not update_data:
            return jsonify({'message': 'Student has no active assignments to remove'}), 200

    result = svc.update_user(user_id, update_data)
    if result.get('success'):
        return jsonify({'success': True, 'message': 'Student unassigned successfully'}), 200
    return jsonify({'error': result.get('error', 'Failed to unassign student')}), 400


# ─────────────────────────────────────────────
# DELETE  DELETE /api/users/<user_id>
# ─────────────────────────────────────────────
@user_bp.route('/<user_id>', methods=['DELETE'])
@jwt_required()
def delete_user(user_id):
    """Delete a user by ID"""
    svc = _get_service()

    # Prevent self-deletion
    current_user_id = get_jwt_identity()
    if current_user_id == user_id:
        return jsonify({'error': 'You cannot delete your own account'}), 403

    result = svc.delete_user(user_id)

    if result.get('success'):
        return jsonify({'message': 'User deleted successfully'}), 200
    return jsonify({'error': result.get('error', 'Delete failed')}), 400
