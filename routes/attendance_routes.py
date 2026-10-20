"""
TrackIt System - Attendance Routes
Handles check-in, check-out, and attendance record retrieval.
Accepts both multipart/form-data (with face photo) and JSON.
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta, timezone
from services.firebase_service import FirebaseService
import os
import uuid

attendance_bp = Blueprint('attendance', __name__)

# Shared service instance — injected by app.py
_firebase_service = None

# Firebase Storage bucket name (projectId.appspot.com)
STORAGE_BUCKET = os.environ.get('FIREBASE_STORAGE_BUCKET', 'login-system-292a0.appspot.com')


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


def set_firebase_service(service):
    global _firebase_service
    _firebase_service = service


def _get_service() -> FirebaseService:
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service


def _read_upload_bytes(file_storage):
    """Read a Werkzeug FileStorage fully into memory so the stream can be reused."""
    if not file_storage:
        return None
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass
    data = file_storage.read()
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass
    return data or None


def _save_face_photo_bytes(image_bytes: bytes, user_id: str, event: str, filename_hint: str = 'photo.jpg'):
    """
    Upload face photo to Firebase Storage; return the public download URL.
    """
    if not image_bytes:
        return None

    ext = os.path.splitext(filename_hint or 'photo.jpg')[1] or '.jpg'
    if ext.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
        ext = '.jpg'
    filename = f"{event}_{user_id}_{uuid.uuid4().hex[:8]}{ext}"

    url = _upload_to_firebase_storage(image_bytes, f'checkin_photos/{filename}')
    if url:
        print(f"[OK] Face photo uploaded to Firebase Storage: {url}")
        return url

    print(f"[ERROR] Could not upload face photo to Firebase Storage")
    return None


def _save_face_photo(file_storage, user_id: str, event: str):
    """Save an uploaded face photo and return a displayable URL."""
    data = _read_upload_bytes(file_storage)
    if not data:
        return None
    hint = getattr(file_storage, 'filename', None) or 'photo.jpg'
    return _save_face_photo_bytes(data, user_id, event, hint)


def _resolve_face_photo_url(parsed, current_user_id: str, event: str):
    """Persist the uploaded file; prefer a public http(s) URL for dashboards."""
    face_photo_file = parsed.get('facePhoto')
    override = (parsed.get('facePhotoUrl') or '').strip() or None
    saved = None

    print(f"[PHOTO RESOLVE] event={event} user={current_user_id} has_file={face_photo_file is not None} override_url={override}")

    if face_photo_file:
        saved = _save_face_photo(face_photo_file, current_user_id, event)
        print(f"[PHOTO RESOLVE] saved local/storage URL: {saved}")

    if override and override.startswith(('http://', 'https://')):
        print(f"[PHOTO RESOLVE] using override Firebase URL: {override}")
        return override

    final_url = saved or override
    print(f"[PHOTO RESOLVE] final URL returned: {final_url}")
    return final_url


def _absolutize_photo_url(url):
    if not url or not isinstance(url, str):
        return url
    s = url.strip()
    if not s or s in ('null', 'undefined', 'local'):
        return None
    if s.startswith(('http://', 'https://', 'data:image')):
        return s
    base = (request.host_url or 'http://127.0.0.1:5000/').rstrip('/')
    return base + (s if s.startswith('/') else '/' + s)


def _absolutize_attendance_record(record):
    if not isinstance(record, dict):
        return record
    for key in (
        'checkInFacePhotoUrl', 'checkOutFacePhotoUrl',
        'checkInPhotoUrl', 'checkOutPhotoUrl', 'facePhotoUrl',
    ):
        if record.get(key):
            record[key] = _absolutize_photo_url(record[key])
    logs = record.get('timeLogs')
    if isinstance(logs, list):
        for log in logs:
            if not isinstance(log, dict):
                continue
            for key in (
                'checkInFacePhotoUrl', 'checkOutFacePhotoUrl',
                'checkInPhotoUrl', 'checkOutPhotoUrl', 'facePhotoUrl',
            ):
                if log.get(key):
                    log[key] = _absolutize_photo_url(log[key])
    return record


def _parse_request():
    """Parse check-in/out data from multipart or JSON."""
    if request.content_type and 'multipart/form-data' in request.content_type:
        lat = request.form.get('latitude', '').strip()
        lng = request.form.get('longitude', '').strip()
        return {
            'latitude':         float(lat) if lat else None,
            'longitude':        float(lng) if lng else None,
            'address':          request.form.get('address'),
            'faceVerified':     request.form.get('faceVerified', 'false').lower() == 'true',
            'livenessVerified': request.form.get('livenessVerified', 'false').lower() == 'true',
            'locationDisabled': request.form.get('locationDisabled', 'false').lower() == 'true',
            'facePhoto':        request.files.get('facePhoto'),
            'facePhotoUrl':     request.form.get('facePhotoUrl'),  # Firebase Storage URL
        }
    else:
        data = request.get_json() or {}
        location = data.get('location') or {}
        return {
            'latitude':         location.get('latitude'),
            'longitude':        location.get('longitude'),
            'address':          location.get('address'),
            'faceVerified':     data.get('faceVerified', False),
            'livenessVerified': data.get('livenessVerified', False),
            'locationDisabled': data.get('locationDisabled', False),
            'facePhoto':        None,
            'facePhotoUrl':     data.get('facePhotoUrl'),
        }


@attendance_bp.route('/', methods=['GET'])
@jwt_required()
def get_attendance_records():
    svc = _get_service()
    current_user_id = get_jwt_identity()
    records = svc.get_attendance_records(
        user_id=current_user_id,
        date=request.args.get('date'),
        employee_id=request.args.get('employeeId'),
        start_date=request.args.get('startDate'),
        end_date=request.args.get('endDate'),
    )
    return jsonify(records), 200


@attendance_bp.route('/all', methods=['GET'])
@jwt_required()
def get_all_attendance_records():
    """Get all attendance records for admin view.
    Coordinators/admins see all students.
    Advisers see only their assigned students.
    """
    from flask_jwt_extended import get_jwt
    svc = _get_service()
    current_user_id = get_jwt_identity()
    date = request.args.get('date')
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')

    # Determine caller role
    claims = get_jwt() or {}
    role = (claims.get('role') or '').lower().strip()
    role_aliases = {'admin': 'coordinator', 'employee': 'adviser'}
    effective_role = role_aliases.get(role, role)

    # If adviser, restrict to their assigned students
    if effective_role == 'adviser':
        my_students = svc.get_students_by_adviser(current_user_id)
        student_ids = {s['id'] for s in my_students}
        all_records = []
        for sid in student_ids:
            recs = svc.get_attendance_records(
                user_id=sid,
                date=date,
                start_date=start_date,
                end_date=end_date,
            )
            all_records.extend(recs)
        records = all_records
    else:
        records = svc.get_attendance_records(
            date=date,
            start_date=start_date,
            end_date=end_date,
        )

    enriched_records = []
    for record in records:
        student = svc.get_user(record.get('userId'))
        if student:
            record['studentName'] = student.get('name', 'Unknown')
            record['idNumber'] = student.get('idNumber', '')
            record['department'] = student.get('department', '')
        enriched_records.append(_absolutize_attendance_record(record))

    return jsonify(enriched_records), 200


@attendance_bp.route('/checkin', methods=['POST'])
@jwt_required()
def record_checkin():
    svc = _get_service()
    current_user_id = get_jwt_identity()

    try:
        parsed = _parse_request()
    except Exception as e:
        print(f"[ERROR] Failed to parse check-in request: {e}")
        return jsonify({'error': f'Invalid request data: {str(e)}'}), 400

    latitude          = parsed['latitude']
    longitude         = parsed['longitude']
    address           = parsed['address']
    face_verified     = parsed['faceVerified']
    liveness_verified = parsed['livenessVerified']
    location_disabled = parsed['locationDisabled']
    face_photo_file   = parsed['facePhoto']
    face_photo_url_override = parsed.get('facePhotoUrl')  # Firebase Storage URL
    face_photo_url = _resolve_face_photo_url(parsed, current_user_id, 'checkin')

    print(f"[CHECK-IN] user={current_user_id} lat={latitude} lng={longitude} "
          f"face={face_verified} liveness={liveness_verified} "
          f"loc_disabled={location_disabled} has_photo={face_photo_file is not None} "
          f"storage_url={bool(face_photo_url_override)} saved_url={bool(face_photo_url)}")

    if not location_disabled and (latitude is None or longitude is None):
        return jsonify({'error': 'Location is required. Enable GPS or tap Skip GPS.'}), 400
    if not face_verified:
        return jsonify({'error': 'Face verification required. Please retake your photo.'}), 400
    if not liveness_verified:
        return jsonify({'error': 'Liveness check required. Please blink during capture.'}), 400

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Determine late status from OJT settings ───────────────────────────────
    try:
        settings       = svc.get_settings()
        start_time_str = settings.get('ojtStartTime', '08:00')   # e.g. "08:00"
        late_threshold = int(settings.get('lateThreshold', 15))  # minutes
        # Parse start time in the server's local timezone
        start_h, start_m = map(int, start_time_str.split(':'))
        now_local = datetime.now()
        cutoff = now_local.replace(hour=start_h, minute=start_m + late_threshold,
                                   second=0, microsecond=0)
        attendance_status = 'late' if now_local > cutoff else 'present'
    except Exception:
        attendance_status = 'present'
    # ─────────────────────────────────────────────────────────────────────────

    try:
        existing = svc.get_today_attendance(current_user_id, today)
    except Exception as e:
        print(f"[ERROR] get_today_attendance failed: {e}")
        return jsonify({'error': f'Database error: {str(e)}'}), 500

    new_log_entry = {
        'checkIn':  now_iso,
        'checkInLocation': {
            'latitude':  latitude,
            'longitude': longitude,
            'address':   address,
        },
        'faceVerified':        face_verified,
        'livenessVerified':    liveness_verified,
        'locationDisabled':    location_disabled,
    }
    if face_photo_url:
        new_log_entry['checkInFacePhotoUrl'] = face_photo_url
        print(f"[CHECK-IN] Added checkInFacePhotoUrl to log entry: {face_photo_url}")
    else:
        print(f"[CHECK-IN] WARNING: No face_photo_url available to save")

    if existing:
        # Check if there is already an open (not yet checked-out) log entry
        time_logs = existing.get('timeLogs') or []
        open_entry = next((e for e in time_logs if e.get('checkIn') and not e.get('checkOut')), None)
        if open_entry:
            return jsonify({'error': 'Already checked in. Please check out first.'}), 400

        # Append a new log entry to existing record
        time_logs.append(new_log_entry)
        update_data = {
            'timeLogs':  time_logs,
            'checkIn':   existing.get('checkIn') or now_iso,  # keep first check-in time
            'updatedAt': now_iso,
            'status':    attendance_status,
        }
        if face_photo_url:
            update_data['checkInFacePhotoUrl'] = face_photo_url
        if getattr(svc, 'db', None):
            try:
                from google.cloud.firestore import DELETE_FIELD
                update_data['checkOut'] = DELETE_FIELD
            except Exception:
                pass
        try:
            result = svc.update_attendance_record(existing['id'], update_data)
        except Exception as e:
            print(f"[ERROR] update_attendance_record failed: {e}")
            return jsonify({'error': f'Failed to update record: {str(e)}'}), 500

        if result.get('success'):
            print(f"[CHECK-IN] Re-check-in success for user={current_user_id} (log #{len(time_logs)})")
            return jsonify({'success': True, 'record': result['record']}), 201
        return jsonify({'error': result.get('error', 'Check-in failed')}), 400

    # No record yet — create fresh
    record = {
        'userId':    current_user_id,
        'date':      today,
        # Top-level fields kept for backward-compat
        'checkIn':   now_iso,
        'checkInLocation': {
            'latitude':  latitude,
            'longitude': longitude,
            'address':   address,
        },
        'faceVerified':        face_verified,
        'livenessVerified':    liveness_verified,
        'locationDisabled':    location_disabled,
        'status':    attendance_status,
        'createdAt': now_iso,
        # New: array of individual time-log pairs
        'timeLogs': [new_log_entry],
    }
    if face_photo_url:
        record['checkInFacePhotoUrl'] = face_photo_url

    try:
        result = svc.create_attendance_record(record)
    except Exception as e:
        print(f"[ERROR] create_attendance_record failed: {e}")
        return jsonify({'error': f'Failed to save record: {str(e)}'}), 500

    if result.get('success'):
        print(f"[CHECK-IN] Success for user={current_user_id}")
        return jsonify({'success': True, 'record': result['record']}), 201

    print(f"[ERROR] Check-in failed: {result.get('error')}")
    return jsonify({'error': result.get('error', 'Check-in failed')}), 400


@attendance_bp.route('/checkout', methods=['POST'])
@jwt_required()
def record_checkout():
    svc = _get_service()
    current_user_id = get_jwt_identity()

    try:
        parsed = _parse_request()
    except Exception as e:
        return jsonify({'error': f'Invalid request data: {str(e)}'}), 400

    latitude          = parsed['latitude']
    longitude         = parsed['longitude']
    address           = parsed['address']
    face_verified     = parsed['faceVerified']
    location_disabled = parsed['locationDisabled']
    face_photo_url = _resolve_face_photo_url(parsed, current_user_id, 'checkout')

    if not location_disabled and (latitude is None or longitude is None):
        return jsonify({'error': 'Location is required. Enable GPS or tap Skip GPS.'}), 400
    if not face_verified:
        return jsonify({'error': 'Face verification required. Please retake your photo.'}), 400

    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        existing = svc.get_today_attendance(current_user_id, today)
    except Exception as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500

    if not existing or not existing.get('checkIn'):
        return jsonify({'error': 'No check-in record found for today'}), 400

    check_out_time = datetime.now(timezone.utc)
    check_out_iso  = check_out_time.isoformat()

    # Find the latest open log entry and close it
    time_logs = existing.get('timeLogs') or []
    open_idx = next(
        (i for i, e in reversed(list(enumerate(time_logs)))
         if e.get('checkIn') and not e.get('checkOut')),
        None
    )

    if open_idx is None:
        return jsonify({'error': 'No active check-in found. Please check in first.'}), 400

    # Close the open entry
    entry = time_logs[open_idx]
    entry['checkOut'] = check_out_iso
    entry['checkOutLocation'] = {
        'latitude':  latitude,
        'longitude': longitude,
        'address':   address,
    }
    if face_photo_url:
        entry['checkOutFacePhotoUrl'] = face_photo_url
        print(f"[CHECK-OUT] Added checkOutFacePhotoUrl to log entry: {face_photo_url}")
    else:
        print(f"[CHECK-OUT] WARNING: No face_photo_url available to save")
    entry['checkOutFaceVerified'] = face_verified
    entry['locationDisabled']     = location_disabled
    try:
        ci_dt = datetime.fromisoformat(entry['checkIn'])
        entry['workHours'] = round(
            (check_out_time - ci_dt).total_seconds() / 3600, 2
        )
    except Exception:
        pass
    time_logs[open_idx] = entry

    # Recalculate total work hours across all completed log entries
    total_hours = sum(
        float(e.get('workHours') or 0) for e in time_logs if e.get('checkOut')
    )

    update_data = {
        'timeLogs':             time_logs,
        # Top-level checkOut = time of the most recent checkout (for backward compat)
        'checkOut':             check_out_iso,
        'checkOutLocation': {
            'latitude':  latitude,
            'longitude': longitude,
            'address':   address,
        },
        'checkOutFaceVerified': face_verified,
        'locationDisabled':     location_disabled,
        'workHours':            round(total_hours, 2),
        'updatedAt':            check_out_iso,
    }
    if face_photo_url:
        update_data['checkOutFacePhotoUrl'] = face_photo_url

    try:
        result = svc.update_attendance_record(existing['id'], update_data)
    except Exception as e:
        return jsonify({'error': f'Failed to update record: {str(e)}'}), 500

    if result.get('success'):
        return jsonify({'success': True, 'record': result['record']}), 200

    return jsonify({'error': result.get('error', 'Check-out failed')}), 400


@attendance_bp.route('/auto-checkout', methods=['POST'])
@jwt_required()
def auto_checkout():
    """Automatic checkout triggered by the client when OJT End Time is reached.
    Face verification is not required — the session is ended by the timer, not the student.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        existing = svc.get_today_attendance(current_user_id, today)
    except Exception as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500

    if not existing or not existing.get('checkIn'):
        return jsonify({'success': True, 'message': 'No active session'}), 200

    # Find open log entry
    time_logs = existing.get('timeLogs') or []
    open_idx = next(
        (i for i, e in reversed(list(enumerate(time_logs)))
         if e.get('checkIn') and not e.get('checkOut')),
        None
    )
    if open_idx is None:
        return jsonify({'success': True, 'message': 'No active session'}), 200

    check_out_time = datetime.now(timezone.utc)
    check_out_iso = check_out_time.isoformat()

    entry = time_logs[open_idx]
    entry['checkOut'] = check_out_iso
    entry['autoCheckout'] = True
    try:
        ci_dt = datetime.fromisoformat(entry['checkIn'])
        entry['workHours'] = round((check_out_time - ci_dt).total_seconds() / 3600, 2)
    except Exception:
        pass
    time_logs[open_idx] = entry

    total_hours = sum(float(e.get('workHours') or 0) for e in time_logs if e.get('checkOut'))

    update_data = {
        'timeLogs':   time_logs,
        'checkOut':   check_out_iso,
        'workHours':  round(total_hours, 2),
        'autoCheckout': True,
        'updatedAt':  check_out_iso,
    }
    try:
        result = svc.update_attendance_record(existing['id'], update_data)
    except Exception as e:
        return jsonify({'error': f'Failed to update record: {str(e)}'}), 500

    if result.get('success'):
        return jsonify({'success': True, 'record': result['record']}), 200
    return jsonify({'error': result.get('error', 'Auto-checkout failed')}), 400



@jwt_required()
def reset_today_attendance():
    """Clear today's attendance so the student can check in again (e.g. mistaken early checkout)."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        existing = svc.get_today_attendance(current_user_id, today)
    except Exception as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500

    if not existing:
        return jsonify({'success': True, 'message': 'No attendance record for today'}), 200

    record_id = existing.get('id')
    if not record_id:
        return jsonify({'error': 'Invalid attendance record'}), 400

    result = svc.delete_attendance_record(record_id)
    if result.get('success'):
        return jsonify({'success': True, 'message': 'Today\'s attendance cleared'}), 200

    return jsonify({'error': result.get('error', 'Failed to reset attendance')}), 400


@attendance_bp.route('/upload-checkin-photo', methods=['POST'])
@jwt_required()
def upload_checkin_photo():
    """Upload a face photo for an existing attendance record (e.g. after offline check-in)."""
    current_user_id = get_jwt_identity()
    svc = _get_service()

    event = request.form.get('event', 'checkin')  # 'checkin' or 'checkout'
    record_id = request.form.get('recordId', '').strip()

    if 'facePhoto' not in request.files:
        return jsonify({'error': 'No photo file'}), 400

    file = request.files['facePhoto']
    if not file or not file.filename:
        return jsonify({'error': 'No photo file'}), 400

    photo_url = _save_face_photo(file, current_user_id, event)
    if not photo_url:
        return jsonify({'error': 'Failed to save photo'}), 500

    # If a record ID was provided, patch it
    if record_id:
        field = 'checkInFacePhotoUrl' if event == 'checkin' else 'checkOutFacePhotoUrl'
        try:
            svc.update_attendance_record(record_id, {field: photo_url})
        except Exception:
            pass  # best-effort; URL is still returned

    return jsonify({'success': True, 'photoUrl': photo_url}), 201


@attendance_bp.route('/today', methods=['GET'])
@jwt_required()
def get_today_attendance():
    """Get today's attendance record for the current user."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    record = svc.get_today_attendance(current_user_id, today)
    return jsonify(record), 200


@attendance_bp.route('/summary', methods=['GET'])
@jwt_required()
def get_attendance_summary():
    svc = _get_service()
    current_user_id = get_jwt_identity()
    end_date   = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=30)
    records = svc.get_attendance_records(
        user_id=current_user_id,
        start_date=start_date.strftime('%Y-%m-%d'),
        end_date=end_date.strftime('%Y-%m-%d'),
    )
    print(f"[ATTENDANCE SUMMARY] User: {current_user_id}, Records found: {len(records)}")
    total_days   = len(records)
    present_days = sum(1 for r in records if r.get('status') in ('present', 'late') or r.get('checkIn'))
    late_days    = sum(1 for r in records if r.get('status') == 'late')
    absent_days  = total_days - present_days
    total_hours  = sum(float(r.get('workHours') or 0) for r in records)
    result = {
        'totalDays':      total_days,
        'presentDays':    present_days,
        'lateDays':       late_days,
        'absentDays':     absent_days,
        'totalHours':     round(total_hours, 2),
        'averageHours':   round(total_hours / present_days, 2) if present_days else 0,
        'attendanceRate': round(present_days / total_days * 100, 2) if total_days else 0,
    }
    print(f"[ATTENDANCE SUMMARY] Result: {result}")
    return jsonify(result), 200


@attendance_bp.route('/<record_id>', methods=['GET'])
@jwt_required()
def get_attendance_record(record_id):
    svc = _get_service()
    current_user_id = get_jwt_identity()
    record = svc.get_attendance_record(record_id)
    if not record:
        return jsonify({'error': 'Record not found'}), 404
    if record.get('userId') != current_user_id:
        return jsonify({'error': 'Unauthorized'}), 403
    return jsonify(record), 200
