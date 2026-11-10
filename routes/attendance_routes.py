"""
TrackIt System - Attendance Routes
Handles check-in, check-out, and attendance record retrieval.
Accepts both multipart/form-data (with face photo) and JSON.
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta, timezone
from services.firebase_service import FirebaseService
from routes.face_routes import _registered_image_bytes, _image_match_score, _parse_descriptor, _descriptor_distance
import os
import uuid
import uuid as _uuid
import urllib.parse as _urllib_parse

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
        token = str(_uuid.uuid4())
        blob.metadata = {'firebaseStorageDownloadTokens': token}
        blob.patch()
        download_url = (
            f"https://firebasestorage.googleapis.com/v0/b/{bucket.name}"
            f"/o/{_urllib_parse.quote(destination_path, safe='')}"
            f"?alt=media&token={token}"
        )
        print(f"[Storage] Uploaded {destination_path} → {download_url}")
        return download_url
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


def _hhmm(value, default_minutes: int) -> int:
    try:
        hour_text, minute_text = str(value).strip().split(':')[:2]
        return int(hour_text) * 60 + int(minute_text)
    except Exception:
        return default_minutes


def _log_session(entry: dict) -> str:
    session = str((entry or {}).get('session') or '').strip().lower()
    if session in ('morning', 'am'):
        return 'morning'
    if session in ('afternoon', 'pm'):
        return 'afternoon'
    return ''


def _is_open_log(entry: dict) -> bool:
    return bool(entry.get('checkIn')) and not entry.get('checkOut')


def _latest_open_log_index(logs: list):
    for index in range(len(logs) - 1, -1, -1):
        if _is_open_log(logs[index]):
            return index
    return None


def _entry_hours(entry: dict):
    hours = entry.get('workHours', entry.get('hours'))
    if hours is None:
        hours = FirebaseService._hours_between(entry.get('checkIn'), entry.get('checkOut'))
    try:
        return float(hours) if hours is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _completed_hours(logs: list) -> float:
    return round(sum(_entry_hours(e) for e in logs if e.get('checkOut')), 2)


def _first_checkin_time(record: dict, logs: list):
    times = [e.get('checkIn') for e in logs if e.get('checkIn')]
    if record.get('checkIn'):
        times.append(record.get('checkIn'))
    return min((t for t in times if t), key=lambda t: str(t), default=None)


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


def _compress_face_photo(image_bytes: bytes, max_dim: int = 800, quality: int = 70) -> bytes:
    """Downscale/re-encode a JPEG so base64 fallback stays well under Firestore's 1 MiB doc limit."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert('RGB')
        img.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=quality, optimize=True)
        return buf.getvalue()
    except Exception as e:
        print(f"[PHOTO] Compression failed, using original bytes: {e}")
        return image_bytes


def _save_face_photo_bytes(image_bytes: bytes, user_id: str, event: str, filename_hint: str = 'photo.jpg'):
    """
    Upload face photo to Firebase Storage; return the public download URL.
    Falls back to a base64 data URL so the photo is never silently lost.
    """
    if not image_bytes:
        return None

    ext = os.path.splitext(filename_hint or 'photo.jpg')[1] or '.jpg'
    if ext.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
        ext = '.jpg'
    filename = f"{event}_{user_id}_{uuid.uuid4().hex[:8]}{ext}"

    # Compress first — smaller uploads and a much smaller base64 fallback.
    compressed = _compress_face_photo(image_bytes)

    url = _upload_to_firebase_storage(compressed, f'checkin_photos/{filename}')
    if url:
        print(f"[OK] Face photo uploaded to Firebase Storage: {url}")
        return url

    # Firebase Storage is unavailable on this project (no billing/bucket).
    # Fall back to a compressed base64 data URL stored in Firestore.
    print(f"[WARN] Firebase Storage upload failed — using base64 data URL fallback")
    import base64 as _b64
    b64 = _b64.b64encode(compressed).decode('utf-8')
    data_url = f"data:image/jpeg;base64,{b64}"
    print(f"[OK] Face photo stored as base64 data URL ({len(data_url)} chars)")
    return data_url


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

    def _log_url(u):
        if not u:
            return None
        return u if len(u) < 120 else f"{u[:80]}...<{len(u)} chars>"

    print(f"[PHOTO RESOLVE] event={event} user={current_user_id} has_file={face_photo_file is not None} override_url={_log_url(override)}")

    if face_photo_file:
        saved = _save_face_photo(face_photo_file, current_user_id, event)
        print(f"[PHOTO RESOLVE] saved local/storage URL: {_log_url(saved)}")

    if override and override.startswith(('http://', 'https://')):
        print(f"[PHOTO RESOLVE] using override Firebase URL: {override}")
        return override

    final_url = saved or override
    print(f"[PHOTO RESOLVE] final URL returned: {_log_url(final_url)}")
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
        acc = request.form.get('accuracy', '').strip()
        return {
            'latitude':         float(lat) if lat else None,
            'longitude':        float(lng) if lng else None,
            'accuracy':         float(acc) if acc else None,
            'address':          request.form.get('address'),
            'faceVerified':     request.form.get('faceVerified', 'false').lower() == 'true',
            'livenessVerified': request.form.get('livenessVerified', 'false').lower() == 'true',
            'locationDisabled': request.form.get('locationDisabled', 'false').lower() == 'true',
            'facePhoto':        request.files.get('facePhoto'),
            'facePhotoUrl':     request.form.get('facePhotoUrl'),  # Firebase Storage URL
            '_faceVerifiedPresent': 'faceVerified' in request.form,
        }
    else:
        data = request.get_json() or {}
        location = data.get('location') or {}
        return {
            'latitude':         location.get('latitude'),
            'longitude':        location.get('longitude'),
            'accuracy':         location.get('accuracy'),
            'address':          location.get('address'),
            'faceVerified':     data.get('faceVerified', False),
            'livenessVerified': data.get('livenessVerified', False),
            'locationDisabled': data.get('locationDisabled', False),
            'facePhoto':        None,
            'facePhotoUrl':     data.get('facePhotoUrl'),
            '_faceVerifiedPresent': 'faceVerified' in data,
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
            # Include the student's assigned company so the frontend can filter by HTE
            record['company'] = (student.get('company') or student.get('companyName') or '').strip()
        enriched_records.append(_absolutize_attendance_record(record))

    # ── Recalculate late/present/incomplete status using CURRENT OJT settings ─
    # This corrects any records whose status was set when a supervisor had
    # incorrectly changed the OJT start time. A per-student supervisor schedule
    # overrides the global OJT settings.
    try:
        settings = svc.get_settings()
        global_morning_ci   = settings.get('ojtMorningCheckinTime', settings.get('ojtStartTime', '08:00'))
        global_afternoon_ci = settings.get('ojtAfternoonCheckinTime', '13:00')
        global_morning_co   = settings.get('ojtMorningCheckoutTime', '12:00')
        global_threshold = int(settings.get('lateThreshold', 15))
        for rec in enriched_records:
            check_in = rec.get('checkIn')
            if not check_in:
                if rec.get('status') != 'absent':
                    rec['status'] = 'absent'
                continue
            # Only recalculate present/late/incomplete — do not touch 'absent'
            if rec.get('status') == 'absent':
                continue
            try:
                # Check for incomplete (has open session)
                time_logs = rec.get('timeLogs') or []
                has_open = any(e.get('checkIn') and not e.get('checkOut') for e in time_logs)
                has_checkout = bool(rec.get('checkOut'))
                if has_open and not has_checkout:
                    rec['status'] = 'incomplete'
                    continue
                # Resolve per-student override
                stu = svc.get_user(rec.get('userId'))
                morning_ci_str = (stu.get('ojtMorningCheckinTime') if stu else None) or global_morning_ci
                morning_co_str = (stu.get('ojtMorningCheckoutTime') if stu else None) or global_morning_co
                afternoon_ci_str = (stu.get('ojtAfternoonCheckinTime') if stu else None) or global_afternoon_ci
                stu_threshold = stu.get('ojtLateThresholdMinutes') if stu else None
                if stu_threshold is None:
                    stu_threshold = stu.get('ojtLateThreshold') if stu else None
                late_threshold = int(stu_threshold if stu_threshold is not None else global_threshold)
                from datetime import datetime as _dt
                time_part = str(check_in).strip()[:5]  # "HH:MM"
                ci_h, ci_m = map(int, time_part.split(':'))
                checkin_total = ci_h * 60 + ci_m
                # Determine which session this check-in belongs to
                def _tmin(hhmm):
                    h2, m2 = map(int, hhmm.split(':'))
                    return h2 * 60 + m2
                mci_min = _tmin(morning_ci_str)
                mco_min = _tmin(morning_co_str)
                aci_min = _tmin(afternoon_ci_str)
                # Pick the correct session start for the late-threshold calculation
                if checkin_total < mco_min:
                    session_start_min = mci_min
                else:
                    session_start_min = aci_min
                cutoff_total = session_start_min + late_threshold
                rec['status'] = 'late' if checkin_total > cutoff_total else 'present'
            except Exception:
                pass  # leave status unchanged if time parse fails
    except Exception as recalc_err:
        print(f"[attendance/all] Status recalc skipped: {recalc_err}")

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
    accuracy          = parsed.get('accuracy')

    print(f"[CHECK-IN] user={current_user_id} lat={latitude} lng={longitude} "
          f"face={face_verified} liveness={liveness_verified} "
          f"loc_disabled={location_disabled} has_photo={face_photo_file is not None} "
          f"storage_url={bool(face_photo_url_override)} saved_url={bool(face_photo_url)}")

    # ── Server-side face re-verification ─────────────────────────────────────
    user_profile = svc.get_user(current_user_id)
    has_registered_face = bool(
        (user_profile or {}).get('facePhotoUrl') or
        (user_profile or {}).get('faceDescriptor') or
        (user_profile or {}).get('faceThumb')
    ) if user_profile else False

    if not has_registered_face:
        return jsonify({
            'error': 'No registered face found. Register your face in Profile before check-in.'
        }), 403

    if face_photo_file and user_profile:
        photo_bytes_for_verify = _read_upload_bytes(face_photo_file)
        if photo_bytes_for_verify:
            registered_photo = _registered_image_bytes(user_profile, current_user_id)
            if registered_photo:
                photo_score, gray_score = _image_match_score(
                    registered_photo, photo_bytes_for_verify, None, None
                )
                print(f"[CHECK-IN RE-VERIFY] user={current_user_id} "
                      f"photo={photo_score:.3f} gray={gray_score:.3f}")
                if photo_score < 0.30 or gray_score < 0.20:
                    face_verified = False
                    return jsonify({
                        'error': 'Face verification failed. The submitted photo does not match your registered face.'
                    }), 403

    # face_verified key must be present — absence means the client skipped face verification
    if not parsed.get('_faceVerifiedPresent', True):
        return jsonify({'error': 'face_verified field is required. Face verification must be completed before check-in.'}), 403

    if not location_disabled and (
        latitude is None or longitude is None or
        (latitude == 0.0 and longitude == 0.0)
    ):
        return jsonify({'error': 'Valid non-zero GPS coordinates are required for check-in.'}), 400
    if not face_verified:
        return jsonify({'error': 'Face verification required. Please retake your photo.'}), 400
    if not liveness_verified:
        return jsonify({'error': 'Liveness check required. Please blink during capture.'}), 400

    # Use Philippine Time (UTC+8) for the date field so records are stored
    # under the local calendar date, not the UTC date.
    PH_TZ_OFFSET = timedelta(hours=8)
    now_ph = datetime.now(timezone.utc) + PH_TZ_OFFSET
    today = now_ph.strftime('%Y-%m-%d')
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── Load two-session schedule ─────────────────────────────────────────────
    try:
        settings = svc.get_settings()
        student = svc.get_user(current_user_id)
        def _t(hhmm):
            h, m = map(int, hhmm.split(':'))
            return h * 60 + m

        morning_checkin_str  = (student.get('ojtMorningCheckinTime')    if student else None) or settings.get('ojtMorningCheckinTime',  '08:00')
        morning_checkout_str = (student.get('ojtMorningCheckoutTime')   if student else None) or settings.get('ojtMorningCheckoutTime', '12:00')
        afternoon_checkin_str  = (student.get('ojtAfternoonCheckinTime')  if student else None) or settings.get('ojtAfternoonCheckinTime',  '13:00')
        afternoon_checkout_str = (student.get('ojtAfternoonCheckoutTime') if student else None) or settings.get('ojtAfternoonCheckoutTime', '17:00')
        late_thresh_min = (student.get('ojtLateThresholdMinutes') if student else None)
        if late_thresh_min is None:
            late_thresh_min = (student.get('ojtLateThreshold') if student else None)
        if late_thresh_min is None:
            late_thresh_min = settings.get('lateThreshold', 15)
        late_thresh_min = int(late_thresh_min)

        now_total = now_ph.hour * 60 + now_ph.minute
        t_morning_ci  = _t(morning_checkin_str)
        t_morning_co  = _t(morning_checkout_str)
        t_afternoon_ci  = _t(afternoon_checkin_str)
        t_afternoon_co  = _t(afternoon_checkout_str)
        t_evening_cutoff = 18 * 60  # 18:00

        # Determine session
        if t_morning_co <= now_total < t_afternoon_ci:
            # Lunch break
            return jsonify({'error': 'Attendance not allowed during lunch break (12:00–1:00 PM)'}), 400
        elif t_morning_ci <= now_total < t_morning_co:
            session = 'morning'
            session_checkin_str = morning_checkin_str
        elif t_afternoon_ci <= now_total <= t_evening_cutoff:
            session = 'afternoon'
            session_checkin_str = afternoon_checkin_str
        else:
            # Outside OJT hours
            return jsonify({'error': 'No active OJT session right now'}), 400

        # Determine late status
        if session in ('morning', 'afternoon'):
            cutoff_total = _t(session_checkin_str) + late_thresh_min
            attendance_status = 'late' if now_total > cutoff_total else 'present'
        else:
            attendance_status = 'present'  # should not reach here after outside-session block above

        print(f"[CHECK-IN] session={session} now={now_ph.strftime('%H:%M')} status={attendance_status}")
    except Exception as e:
        print(f"[CHECK-IN] schedule detection failed: {e}")
        session = 'morning'
        attendance_status = 'present'
    # ─────────────────────────────────────────────────────────────────────────

    try:
        existing = svc.get_today_attendance(current_user_id, today)
    except Exception as e:
        print(f"[ERROR] get_today_attendance failed: {e}")
        return jsonify({'error': f'Database error: {str(e)}'}), 500

    try:
        settings = svc.get_settings()
        student = svc.get_user(current_user_id)
    except Exception:
        settings = {}
        student = None

    supervisor_id_val = (student.get('supervisorId') if student else None) or ''

    new_log_entry = {
        'checkIn':  now_iso,
        'session':  session,
        'status':   attendance_status,
        'date':     today,
        'student_id': current_user_id,
        'checkInLocation': {
            'latitude':  latitude,
            'longitude': longitude,
            'address':   address,
            'accuracy':  accuracy,
        },
        'faceVerified':        face_verified,
        'livenessVerified':    liveness_verified,
        'locationDisabled':    location_disabled,
    }
    if supervisor_id_val:
        new_log_entry['supervisor_id'] = supervisor_id_val
    if face_photo_url:
        new_log_entry['checkInFacePhotoUrl'] = face_photo_url
        print(f"[CHECK-IN] Added checkInFacePhotoUrl to log entry: {face_photo_url[:80]}..." if len(face_photo_url) > 120 else f"[CHECK-IN] Added checkInFacePhotoUrl to log entry: {face_photo_url}")
    else:
        print(f"[CHECK-IN] WARNING: No face_photo_url available to save")

    def _checkin_mutation(current_record: dict):
        normalized = svc.normalize_attendance_record(
            current_record, student=student, settings=settings
        )
        time_logs = normalized.get('timeLogs') or []

        same_session_logs = [
            entry for entry in time_logs
            if _log_session(entry) == session and entry.get('checkIn')
        ]
        if same_session_logs:
            if any(entry.get('checkOut') for entry in same_session_logs):
                return {'_error': 'You have already completed this session.'}
            return {
                '_error': 'Please check out first. You already have an active attendance session.'
            }

        open_index = _latest_open_log_index(time_logs)
        if open_index is not None:
            open_session = _log_session(time_logs[open_index]) or 'another'
            return {
                '_error': (
                    'Please check out first. You already have an active '
                    f'{open_session} attendance session.'
                )
            }

        time_logs.append(dict(new_log_entry))
        update_data = {
            'timeLogs':  time_logs,
            'checkIn':   _first_checkin_time(normalized, time_logs) or now_iso,
            'updatedAt': now_iso,
            'status':    attendance_status,
        }
        if session == 'morning':
            update_data['morningSession'] = 'checked_in'
        elif session == 'afternoon':
            update_data['afternoonSession'] = 'checked_in'

        has_checkin_photo = bool(current_record.get('checkInFacePhotoUrl')) or any(
            entry.get('checkInFacePhotoUrl') or entry.get('checkInPhotoUrl')
            for entry in time_logs[:-1]
        )
        if face_photo_url and not has_checkin_photo:
            update_data['checkInFacePhotoUrl'] = face_photo_url

        return update_data

    record_to_update = existing
    while True:
        if record_to_update:
            try:
                result = svc.mutate_attendance_record(
                    record_to_update['id'], _checkin_mutation
                )
            except Exception as e:
                print(f"[ERROR] mutate_attendance_record failed: {e}")
                return jsonify({'error': f'Failed to update record: {str(e)}'}), 500

            if result.get('success'):
                log_count = len((result.get('record') or {}).get('timeLogs') or [])
                print(
                    f"[CHECK-IN] Re-check-in success for user={current_user_id} "
                    f"session={session} (log #{log_count})"
                )
                return jsonify({
                    'success': True,
                    'record': result['record'],
                    'session': session,
                    'status': attendance_status,
                }), 201
            return jsonify({'error': result.get('error', 'Check-in failed')}), 400

        record = {
            'userId':    current_user_id,
            'date':      today,
            # Top-level fields kept for backward-compat
            'checkIn':   now_iso,
            'checkInLocation': {
                'latitude':  latitude,
                'longitude': longitude,
                'address':   address,
                'accuracy':  accuracy,
            },
            'faceVerified':        face_verified,
            'livenessVerified':    liveness_verified,
            'locationDisabled':    location_disabled,
            'status':    attendance_status,
            'session':   session,
            'createdAt': now_iso,
            # New: array of individual time-log pairs
            'timeLogs': [new_log_entry],
        }
        if session == 'morning':
            record['morningSession'] = 'checked_in'
        elif session == 'afternoon':
            record['afternoonSession'] = 'checked_in'
        if face_photo_url:
            record['checkInFacePhotoUrl'] = face_photo_url

        try:
            result = svc.create_attendance_record_once(record)
        except Exception as e:
            print(f"[ERROR] create_attendance_record_once failed: {e}")
            return jsonify({'error': f'Failed to save record: {str(e)}'}), 500

        if result.get('success'):
            print(f"[CHECK-IN] Success for user={current_user_id} session={session}")
            return jsonify({
                'success': True,
                'record': result['record'],
                'session': session,
                'status': attendance_status,
            }), 201

        if result.get('conflict') and result.get('record'):
            record_to_update = result['record']
            continue

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
    face_photo_file   = parsed['facePhoto']
    face_photo_url = _resolve_face_photo_url(parsed, current_user_id, 'checkout')
    accuracy          = parsed.get('accuracy')

    # ── Server-side face re-verification ─────────────────────────────────────
    user_profile = svc.get_user(current_user_id)
    has_registered_face = bool(
        (user_profile or {}).get('facePhotoUrl') or
        (user_profile or {}).get('faceDescriptor') or
        (user_profile or {}).get('faceThumb')
    ) if user_profile else False

    if not has_registered_face:
        return jsonify({
            'error': 'No registered face found. Register your face in Profile before check-in.'
        }), 403

    if face_photo_file and user_profile:
        photo_bytes_for_verify = _read_upload_bytes(face_photo_file)
        if photo_bytes_for_verify:
            registered_photo = _registered_image_bytes(user_profile, current_user_id)
            if registered_photo:
                photo_score, gray_score = _image_match_score(
                    registered_photo, photo_bytes_for_verify, None, None
                )
                print(f"[CHECK-OUT RE-VERIFY] user={current_user_id} "
                      f"photo={photo_score:.3f} gray={gray_score:.3f}")
                if photo_score < 0.30 or gray_score < 0.20:
                    face_verified = False
                    return jsonify({
                        'error': 'Face verification failed. The submitted photo does not match your registered face.'
                    }), 403

    # face_verified key must be present
    if not parsed.get('_faceVerifiedPresent', True):
        return jsonify({'error': 'face_verified field is required. Face verification must be completed before check-out.'}), 403

    if not location_disabled and (
        latitude is None or longitude is None or
        (latitude == 0.0 and longitude == 0.0)
    ):
        return jsonify({'error': 'Valid non-zero GPS coordinates are required for check-out.'}), 400
    if not face_verified:
        return jsonify({'error': 'Face verification required. Please retake your photo.'}), 400

    today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d')

    # ── Lunch block for checkout ──────────────────────────────────────────────
    try:
        _settings_co = svc.get_settings()
        _student_co  = svc.get_user(current_user_id)
        def _t2(hhmm):
            h, m = map(int, hhmm.split(':'))
            return h * 60 + m
        _mco = _t2((_student_co.get('ojtMorningCheckoutTime')   if _student_co else None) or _settings_co.get('ojtMorningCheckoutTime',  '12:00'))
        _aci = _t2((_student_co.get('ojtAfternoonCheckinTime')   if _student_co else None) or _settings_co.get('ojtAfternoonCheckinTime', '13:00'))
        _now_ph_co   = datetime.now(timezone.utc) + timedelta(hours=8)
        _now_total_co = _now_ph_co.hour * 60 + _now_ph_co.minute
        if _mco <= _now_total_co < _aci:
            return jsonify({'error': 'Attendance not allowed during lunch break (12:00–1:00 PM)'}), 400
    except Exception:
        pass  # if schedule load fails, do not block checkout
    # ─────────────────────────────────────────────────────────────────────────
    try:
        existing = svc.get_today_attendance(current_user_id, today)
    except Exception as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500

    if not existing:
        return jsonify({'error': 'You must check in before checking out.'}), 400

    try:
        settings = svc.get_settings()
        student = svc.get_user(current_user_id)
    except Exception:
        settings = {}
        student = None

    check_out_time = datetime.now(timezone.utc)
    check_out_iso  = check_out_time.isoformat()
    checkout_session = None
    session_hours = 0.0
    total_hours = 0.0

    def _checkout_mutation(current_record: dict):
        nonlocal checkout_session, session_hours, total_hours

        normalized = svc.normalize_attendance_record(
            current_record, student=student, settings=settings
        )
        time_logs = normalized.get('timeLogs') or []
        has_any_checkin = bool(normalized.get('checkIn')) or any(
            entry.get('checkIn') for entry in time_logs
        )
        if not has_any_checkin:
            return {'_error': 'You must check in before checking out.'}

        open_idx = _latest_open_log_index(time_logs)
        if open_idx is None:
            return {'_error': 'You have already completed this session.'}

        entry = time_logs[open_idx]
        checkout_session = _log_session(entry) or 'morning'
        entry['checkOut'] = check_out_iso
        entry['checkOutLocation'] = {
            'latitude':  latitude,
            'longitude': longitude,
            'address':   address,
            'accuracy':  accuracy,
        }
        if face_photo_url:
            entry['checkOutFacePhotoUrl'] = face_photo_url
            print(f"[CHECK-OUT] Added checkOutFacePhotoUrl to log entry: {face_photo_url[:80]}..." if len(face_photo_url) > 120 else f"[CHECK-OUT] Added checkOutFacePhotoUrl to log entry: {face_photo_url}")
        else:
            print(f"[CHECK-OUT] WARNING: No face_photo_url available to save")
        entry['checkOutFaceVerified'] = face_verified
        entry['locationDisabled']     = location_disabled

        session_hours = FirebaseService._hours_between(
            entry.get('checkIn'), check_out_iso
        ) or 0.0
        entry['workHours'] = session_hours
        entry['hours'] = session_hours  # alias per spec
        time_logs[open_idx] = entry

        # Total hours come only from completed sessions; an open check-in is
        # intentionally excluded until it has a check-out timestamp.
        total_hours = _completed_hours(time_logs)

        has_open = any(_is_open_log(e) for e in time_logs)
        if has_open:
            overall_status = 'incomplete'
        else:
            completed = [e for e in time_logs if e.get('checkOut')]
            if completed:
                last_status = completed[-1].get(
                    'status', normalized.get('status', 'present')
                )
                overall_status = last_status if last_status in (
                    'present', 'late', 'absent'
                ) else normalized.get('status', 'present')
            else:
                overall_status = normalized.get('status', 'present')

        update_data = {
            'timeLogs':             time_logs,
            # Top-level checkOut = time of the most recent checkout (for backward compat)
            'checkOut':             check_out_iso,
            'checkOutLocation': {
                'latitude':  latitude,
                'longitude': longitude,
                'address':   address,
                'accuracy':  accuracy,
            },
            'checkOutFaceVerified': face_verified,
            'locationDisabled':     location_disabled,
            'workHours':            total_hours,
            'status':               overall_status,
            'updatedAt':            check_out_iso,
        }
        if checkout_session == 'morning':
            update_data['morningSession'] = 'completed'
        elif checkout_session == 'afternoon':
            update_data['afternoonSession'] = 'completed'
        if face_photo_url:
            update_data['checkOutFacePhotoUrl'] = face_photo_url

        return update_data

    print(
        f"[CHECK-OUT] user={current_user_id} record={existing.get('id')} "
        f"attempting mutate"
    )
    try:
        result = svc.mutate_attendance_record(existing['id'], _checkout_mutation)
    except Exception as e:
        print(f"[CHECK-OUT] mutate raised: {type(e).__name__}: {e}")
        return jsonify({'error': f'Failed to update record: {str(e)}'}), 500

    if result.get('success'):
        return jsonify({
            'success': True,
            'record': result['record'],
            'session': checkout_session,
            'sessionHours': session_hours,
            'totalHours': total_hours,
        }), 200

    return jsonify({'error': result.get('error', 'Check-out failed')}), 400


@attendance_bp.route('/auto-checkout', methods=['POST'])
@jwt_required()
def auto_checkout():
    """Automatic checkout triggered by the client when OJT End Time is reached.
    Face verification is not required — the session is ended by the timer, not the student.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d')

    try:
        existing = svc.get_today_attendance(current_user_id, today)
    except Exception as e:
        return jsonify({'error': f'Database error: {str(e)}'}), 500

    if not existing:
        return jsonify({'success': True, 'message': 'No active session'}), 200

    try:
        settings = svc.get_settings()
        student = svc.get_user(current_user_id)
    except Exception:
        settings = {}
        student = None

    check_out_time = datetime.now(timezone.utc)
    check_out_iso = check_out_time.isoformat()

    def _auto_checkout_mutation(current_record: dict):
        normalized = svc.normalize_attendance_record(
            current_record, student=student, settings=settings
        )
        time_logs = normalized.get('timeLogs') or []
        open_idx = _latest_open_log_index(time_logs)
        if open_idx is None:
            return {'_error': 'No active session'}

        entry = time_logs[open_idx]
        session = _log_session(entry) or 'morning'
        entry['checkOut'] = check_out_iso
        entry['autoCheckout'] = True
        entry['workHours'] = FirebaseService._hours_between(
            entry.get('checkIn'), check_out_iso
        ) or 0.0
        entry['hours'] = entry['workHours']
        time_logs[open_idx] = entry

        total_hours = _completed_hours(time_logs)
        has_open = any(_is_open_log(e) for e in time_logs)
        if has_open:
            overall_status = 'incomplete'
        else:
            completed = [e for e in time_logs if e.get('checkOut')]
            last_status = completed[-1].get('status', normalized.get('status', 'present')) if completed else normalized.get('status', 'present')
            overall_status = last_status if last_status in ('present', 'late', 'absent') else normalized.get('status', 'present')

        update_data = {
            'timeLogs':   time_logs,
            'checkOut':   check_out_iso,
            'workHours':  total_hours,
            'status':     overall_status,
            'autoCheckout': True,
            'updatedAt':  check_out_iso,
        }
        if session == 'morning':
            update_data['morningSession'] = 'completed'
        elif session == 'afternoon':
            update_data['afternoonSession'] = 'completed'
        return update_data

    try:
        result = svc.mutate_attendance_record(existing['id'], _auto_checkout_mutation)
    except Exception as e:
        return jsonify({'error': f'Failed to update record: {str(e)}'}), 500

    if result.get('success'):
        return jsonify({'success': True, 'record': result['record']}), 200
    if result.get('error') == 'No active session':
        return jsonify({'success': True, 'message': 'No active session'}), 200
    return jsonify({'error': result.get('error', 'Auto-checkout failed')}), 400



@attendance_bp.route('/reset-today', methods=['POST'])
@jwt_required()
def reset_today_attendance():
    """Clear today's attendance so the student can check in again (e.g. mistaken early checkout)."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d')

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
    # Use Philippine Time (UTC+8) so the date matches the stored record date.
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d')
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


# ─────────────────────────────────────────────────────────────────────────────
# DELETE ATTENDANCE RECORD  DELETE /api/attendance/<record_id>
# Allows coordinators, chairmen, advisers, and supervisors to delete
# an attendance record from the DTR.
# ─────────────────────────────────────────────────────────────────────────────
@attendance_bp.route('/<record_id>', methods=['DELETE'])
@jwt_required()
def delete_attendance_record_by_id(record_id):
    """
    DELETE /api/attendance/<record_id>
    Deletes a specific attendance record.
    Allowed roles: coordinator, chairman, adviser, supervisor.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'Caller not found'}), 403

    role = (caller.get('role') or '').lower().strip()
    role_aliases = {'admin': 'coordinator', 'employee': 'adviser'}
    role = role_aliases.get(role, role)

    allowed_roles = {'coordinator', 'chairman', 'adviser', 'supervisor'}
    if role not in allowed_roles:
        return jsonify({'error': 'Only coordinators, chairmen, advisers, or supervisors can delete attendance records'}), 403

    record = svc.get_attendance_record(record_id)
    if not record:
        return jsonify({'error': 'Record not found'}), 404

    # Supervisors can only delete records for students assigned to their company
    if role == 'supervisor':
        caller_company = (caller.get('company') or caller.get('companyName') or '').strip().lower()
        if caller_company:
            student = svc.get_user(record.get('userId', ''))
            student_company = (student.get('company') or student.get('companyName') or '').strip().lower() if student else ''
            if student_company and student_company != caller_company:
                return jsonify({'error': 'You can only delete records for your own interns'}), 403

    result = svc.delete_attendance_record(record_id)
    if result.get('success'):
        print(f"[delete-attendance] Record {record_id} deleted by {caller.get('name')} ({role})")
        return jsonify({'success': True}), 200
    return jsonify({'error': result.get('error', 'Delete failed')}), 400
