"""
TrackIt System - OJT Hours Routes
Handles OJT hours tracking and computation
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone, timedelta
from services.firebase_service import FirebaseService
import re

ojt_bp = Blueprint('ojt', __name__)

_firebase_service = None

ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}
# Roles allowed to update OJT settings — coordinators and chairmen only
OJT_SETTINGS_ROLES = {'coordinator', 'chairman'}


def set_firebase_service(service: FirebaseService):
    global _firebase_service
    _firebase_service = service


def _get_service() -> FirebaseService:
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service


def _effective_role(role: str) -> str:
    r = (role or '').lower().strip()
    return ROLE_ALIASES.get(r, r)


@ojt_bp.route('/hours/<student_id>', methods=['GET'])
@jwt_required()
def get_student_ojt_hours(student_id):
    """Get OJT hours for a specific student"""
    svc = _get_service()
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')

    records = svc.get_attendance_records(
        user_id=student_id,
        start_date=start_date,
        end_date=end_date
    )

    print(f"[OJT HOURS] Student: {student_id}, Records found: {len(records)}")

    total_hours = 0
    total_days = 0
    daily_hours = []

    for record in records:
        # Use the pre-calculated workHours field (sum of all timeLogs sessions).
        # Fall back to wall-clock span only for legacy records that pre-date timeLogs.
        hours = float(record.get('workHours') or 0)
        if hours == 0 and record.get('checkIn') and record.get('checkOut'):
            try:
                ci = datetime.fromisoformat(record['checkIn'])
                co = datetime.fromisoformat(record['checkOut'])
                hours = (co - ci).total_seconds() / 3600
            except Exception:
                pass
        if hours > 0:
            total_hours += hours
            total_days += 1
            daily_hours.append({
                'date': record.get('date'),
                'hours': round(hours, 2),
                'checkIn': record.get('checkIn'),
                'checkOut': record.get('checkOut'),
            })

    student = svc.get_user(student_id)
    settings = svc.get_settings()
    required_hours = settings.get('requiredOjtHours', 600)
    progress_percentage = (total_hours / required_hours * 100) if required_hours > 0 else 0
    remaining_hours = max(0, required_hours - total_hours)

    result = {
        'studentId': student_id,
        'studentName': student.get('name', 'Unknown') if student else 'Unknown',
        'totalHours': round(total_hours, 2),
        'requiredHours': required_hours,
        'remainingHours': round(remaining_hours, 2),
        'progressPercentage': round(progress_percentage, 2),
        'totalDays': total_days,
        'averageHoursPerDay': round(total_hours / total_days, 2) if total_days > 0 else 0,
        'dailyHours': daily_hours
    }
    print(f"[OJT HOURS] Result: totalHours={result['totalHours']}, required={required_hours}, progress={result['progressPercentage']}%")
    return jsonify(result), 200


@ojt_bp.route('/hours', methods=['GET'])
@jwt_required()
def get_all_ojt_hours():
    """Get OJT hours for all students"""
    svc = _get_service()
    students = svc.get_all_students()
    settings = svc.get_settings()
    required_hours = settings.get('requiredOjtHours', 600)

    students_hours = []
    for student in students:
        student_id = student.get('id')
        records = svc.get_attendance_records(user_id=student_id)

        total_hours = 0
        for record in records:
            hours = float(record.get('workHours') or 0)
            if hours == 0 and record.get('checkIn') and record.get('checkOut'):
                try:
                    ci = datetime.fromisoformat(record['checkIn'])
                    co = datetime.fromisoformat(record['checkOut'])
                    hours = (co - ci).total_seconds() / 3600
                except Exception:
                    pass
            total_hours += hours

        progress_percentage = (total_hours / required_hours * 100) if required_hours > 0 else 0
        remaining_hours = max(0, required_hours - total_hours)

        students_hours.append({
            'studentId': student_id,
            'studentName': student.get('name', 'Unknown'),
            'email': student.get('email', ''),
            'company': student.get('company', ''),
            'totalHours': round(total_hours, 2),
            'requiredHours': required_hours,
            'remainingHours': round(remaining_hours, 2),
            'progressPercentage': round(progress_percentage, 2),
            'status': 'completed' if total_hours >= required_hours else 'in_progress'
        })

    students_hours.sort(key=lambda x: x['progressPercentage'], reverse=True)
    return jsonify(students_hours), 200


@ojt_bp.route('/hours/summary', methods=['GET'])
@jwt_required()
def get_ojt_summary():
    """Get OJT hours summary statistics"""
    svc = _get_service()
    students = svc.get_all_students()
    settings = svc.get_settings()
    required_hours = settings.get('requiredOjtHours', 600)

    total_students = len(students)
    completed_students = 0
    in_progress_students = 0
    total_hours_all = 0

    for student in students:
        student_id = student.get('id')
        records = svc.get_attendance_records(user_id=student_id)
        total_hours = 0
        for record in records:
            hours = float(record.get('workHours') or 0)
            if hours == 0 and record.get('checkIn') and record.get('checkOut'):
                try:
                    ci = datetime.fromisoformat(record['checkIn'])
                    co = datetime.fromisoformat(record['checkOut'])
                    hours = (co - ci).total_seconds() / 3600
                except Exception:
                    pass
            total_hours += hours
        total_hours_all += total_hours
        if total_hours >= required_hours:
            completed_students += 1
        else:
            in_progress_students += 1

    return jsonify({
        'totalStudents': total_students,
        'completedStudents': completed_students,
        'inProgressStudents': in_progress_students,
        'totalHoursLogged': round(total_hours_all, 2),
        'averageHoursPerStudent': round(total_hours_all / total_students, 2) if total_students > 0 else 0,
        'requiredHours': required_hours,
        'completionRate': round((completed_students / total_students * 100), 2) if total_students > 0 else 0
    }), 200


@ojt_bp.route('/settings', methods=['GET'])
@jwt_required()
def get_ojt_settings():
    """Get OJT settings"""
    svc = _get_service()
    settings = svc.get_settings()
    return jsonify(settings), 200


@ojt_bp.route('/settings', methods=['PUT'])
@jwt_required()
def update_ojt_settings():
    """Update OJT settings.

    Only coordinators and chairmen can update OJT timing/schedule settings.
    Supervisors are NOT allowed — they cannot set start time, end time, or late threshold.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json()

    user = svc.get_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    user_role = _effective_role(user.get('role', ''))

    # Supervisors cannot modify OJT schedule — coordinator-only
    if user_role == 'supervisor':
        return jsonify({'error': 'OJT schedule settings can only be changed by the Coordinator. Please contact your coordinator.'}), 403
    elif user_role not in OJT_SETTINGS_ROLES:
        return jsonify({'error': 'Only coordinators or chairmen can update OJT settings'}), 403

    result = svc.update_settings(data)
    if result['success']:
        return jsonify({'success': True, 'settings': result['settings']}), 200
    return jsonify({'error': result['error']}), 400


def _build_two_session_schedule(sched, settings):
    """Build the full two-session schedule response dict from a raw sched doc + global settings fallback."""
    morning_checkin  = sched.get('morningCheckinTime')  or settings.get('ojtMorningCheckinTime',  '08:00')
    morning_checkout = sched.get('morningCheckoutTime') or settings.get('ojtMorningCheckoutTime', '12:00')
    afternoon_checkin  = sched.get('afternoonCheckinTime')  or settings.get('ojtAfternoonCheckinTime',  '13:00')
    afternoon_checkout = sched.get('afternoonCheckoutTime') or settings.get('ojtAfternoonCheckoutTime', '17:00')
    late_threshold_min = sched.get('lateThresholdMinutes')
    if late_threshold_min is None:
        late_threshold_min = settings.get('lateThreshold', 15)
    return {
        'morningCheckinTime':    morning_checkin,
        'morningCheckoutTime':   morning_checkout,
        'afternoonCheckinTime':  afternoon_checkin,
        'afternoonCheckoutTime': afternoon_checkout,
        'lateThresholdMinutes':  int(late_threshold_min),
        # Backward-compat
        'startTime':    morning_checkin,
        'endTime':      afternoon_checkout,
        'lateThreshold': int(late_threshold_min),
    }


@ojt_bp.route('/supervisor-schedule', methods=['PUT'])
@jwt_required()
def update_supervisor_schedule():
    """Set the OJT schedule for the authenticated supervisor's assigned students.

    Only supervisors may call this. Each assigned student's user record is
    updated with the two-session schedule fields so attendance uses the
    per-student override; global OJT settings are left untouched.
    """
    svc = _get_service()
    caller_id = get_jwt_identity()

    user = svc.get_user(caller_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if _effective_role(user.get('role', '')) != 'supervisor':
        return jsonify({'error': 'Only supervisors can set a supervisor OJT schedule'}), 403

    data = request.get_json() or {}

    # New two-session fields (with defaults)
    morning_checkin    = (data.get('morningCheckinTime')    or data.get('startTime')  or '08:00').strip()
    morning_checkout   = (data.get('morningCheckoutTime')   or '12:00').strip()
    afternoon_checkin  = (data.get('afternoonCheckinTime')  or '13:00').strip()
    afternoon_checkout = (data.get('afternoonCheckoutTime') or data.get('endTime')   or '17:00').strip()
    late_threshold_min = data.get('lateThresholdMinutes', data.get('lateThreshold', 15))

    time_fields = [morning_checkin, morning_checkout, afternoon_checkin, afternoon_checkout]
    for tf in time_fields:
        if not re.match(r'^\d{2}:\d{2}$', tf):
            return jsonify({'error': 'All time fields must be in HH:MM format'}), 400
    try:
        late_threshold_min = int(late_threshold_min)
    except (ValueError, TypeError):
        return jsonify({'error': 'lateThresholdMinutes must be a non-negative integer'}), 400
    if late_threshold_min < 0:
        return jsonify({'error': 'lateThresholdMinutes must be a non-negative integer'}), 400

    save_doc = {
        'morningCheckinTime':    morning_checkin,
        'morningCheckoutTime':   morning_checkout,
        'afternoonCheckinTime':  afternoon_checkin,
        'afternoonCheckoutTime': afternoon_checkout,
        'lateThresholdMinutes':  late_threshold_min,
        # Backward-compat legacy fields
        'startTime':    morning_checkin,
        'endTime':      afternoon_checkout,
        'lateThreshold': late_threshold_min,
    }

    result = svc.save_supervisor_schedule(caller_id, save_doc)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Failed to save schedule')}), 500

    # Propagate the schedule to each assigned student's user record
    for student in svc.get_students_by_supervisor(caller_id):
        svc.update_user(student['id'], {
            # New two-session fields
            'ojtMorningCheckinTime':    morning_checkin,
            'ojtMorningCheckoutTime':   morning_checkout,
            'ojtAfternoonCheckinTime':  afternoon_checkin,
            'ojtAfternoonCheckoutTime': afternoon_checkout,
            'ojtLateThresholdMinutes':  late_threshold_min,
            # Backward-compat legacy fields
            'ojtStartTime':     morning_checkin,
            'ojtEndTime':       afternoon_checkout,
            'ojtLateThreshold': late_threshold_min,
        })

    settings = svc.get_settings()
    return jsonify(_build_two_session_schedule(save_doc, settings)), 200


@ojt_bp.route('/supervisor-schedule', methods=['GET'])
@jwt_required()
def get_supervisor_schedule_route():
    """Get the authenticated supervisor's OJT schedule, falling back to global settings."""
    svc = _get_service()
    caller_id = get_jwt_identity()

    user = svc.get_user(caller_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    if _effective_role(user.get('role', '')) != 'supervisor':
        return jsonify({'error': 'Only supervisors can view a supervisor OJT schedule'}), 403

    sched = svc.get_supervisor_schedule(caller_id)
    settings = svc.get_settings()
    return jsonify(_build_two_session_schedule(sched, settings)), 200


@ojt_bp.route('/schedule/<supervisor_id>', methods=['GET'])
@jwt_required()
def get_schedule_by_supervisor(supervisor_id):
    """Get the saved schedule for a given supervisor (public to authenticated users).

    Used by Flutter to fetch the schedule via the student's assigned supervisorId.
    Returns defaults if no schedule has been saved yet.
    """
    svc = _get_service()
    sched = svc.get_supervisor_schedule(supervisor_id)
    settings = svc.get_settings()
    return jsonify(_build_two_session_schedule(sched, settings)), 200


@ojt_bp.route('/student-schedule/<student_id>', methods=['GET'])
@jwt_required()
def get_student_schedule(student_id):
    """Get the effective OJT schedule for a student (per-student override or global).

    Allowed for: the student themselves, their supervisor, their adviser,
    coordinators, and chairmen.
    """
    svc = _get_service()
    caller_id = get_jwt_identity()

    caller = svc.get_user(caller_id)
    if not caller:
        return jsonify({'error': 'User not found'}), 404

    student = svc.get_user(student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404

    role = _effective_role(caller.get('role', ''))
    allowed = (
        caller_id == student_id
        or role in {'coordinator', 'chairman'}
        or (role == 'adviser' and student.get('adviserId') == caller_id)
        or (role == 'supervisor' and student.get('supervisorId') == caller_id)
    )
    if not allowed:
        return jsonify({'error': 'Not authorized to view this student schedule'}), 403

    settings = svc.get_settings()

    # Build schedule from per-student fields first, then supervisor schedule, then global defaults
    sched = {
        'morningCheckinTime':    student.get('ojtMorningCheckinTime')    or student.get('ojtStartTime'),
        'morningCheckoutTime':   student.get('ojtMorningCheckoutTime'),
        'afternoonCheckinTime':  student.get('ojtAfternoonCheckinTime'),
        'afternoonCheckoutTime': student.get('ojtAfternoonCheckoutTime') or student.get('ojtEndTime'),
        'lateThresholdMinutes':  student.get('ojtLateThresholdMinutes')  or student.get('ojtLateThreshold'),
    }

    # If per-student fields are missing, use the supervisor's saved schedule
    has_any = any(v for v in sched.values() if v)
    if not has_any:
        supervisor_id = student.get('supervisorId') or student.get('supervisor_id')
        if supervisor_id:
            sup_sched = svc.get_supervisor_schedule(supervisor_id)
            if sup_sched:
                sched = sup_sched

    return jsonify(_build_two_session_schedule(sched, settings)), 200


@ojt_bp.route('/total-hours', methods=['PUT'])
@jwt_required()
def set_total_hours():
    """Set total required hours (coordinator/chairman)"""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json()

    user = svc.get_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    user_role = _effective_role(user.get('role', ''))
    if user_role not in {'coordinator', 'chairman'}:
        return jsonify({'error': 'Access denied'}), 403

    total_hours = data.get('totalHours')
    if total_hours is None:
        return jsonify({'error': 'totalHours is required'}), 400

    try:
        total_hours = float(total_hours)
        if total_hours < 0:
            return jsonify({'error': 'totalHours must be positive'}), 400
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid totalHours value'}), 400

    result = svc.update_settings({'totalHours': total_hours})
    if result['success']:
        return jsonify({'success': True, 'totalHours': total_hours, 'message': 'Total hours updated successfully'}), 200
    return jsonify({'error': result['error']}), 400


@ojt_bp.route('/total-hours/<student_id>', methods=['GET'])
@jwt_required()
def get_student_total_hours(student_id):
    """Get a student's remaining total hours"""
    svc = _get_service()
    settings = svc.get_settings()
    total_hours = settings.get('totalHours', 600)

    records = svc.get_attendance_records(user_id=student_id)
    worked_hours = 0
    for record in records:
        hours = float(record.get('workHours') or 0)
        if hours == 0 and record.get('checkIn') and record.get('checkOut'):
            try:
                ci = datetime.fromisoformat(record['checkIn'])
                co = datetime.fromisoformat(record['checkOut'])
                hours = (co - ci).total_seconds() / 3600
            except Exception:
                pass
        worked_hours += hours

    remaining_hours = max(0, total_hours - worked_hours)
    progress_percentage = (worked_hours / total_hours * 100) if total_hours > 0 else 0

    return jsonify({
        'studentId': student_id,
        'totalHours': total_hours,
        'workedHours': round(worked_hours, 2),
        'remainingHours': round(remaining_hours, 2),
        'progressPercentage': round(progress_percentage, 2)
    }), 200


@ojt_bp.route('/export/<student_id>', methods=['GET'])
@jwt_required()
def export_ojt_report(student_id):
    """Export OJT hours report for a student"""
    svc = _get_service()
    student = svc.get_user(student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404

    records = svc.get_attendance_records(user_id=student_id)
    total_hours = 0
    daily_records = []

    for record in records:
        hours = float(record.get('workHours') or 0)
        if hours == 0 and record.get('checkIn') and record.get('checkOut'):
            try:
                ci = datetime.fromisoformat(record['checkIn'])
                co = datetime.fromisoformat(record['checkOut'])
                hours = (co - ci).total_seconds() / 3600
            except Exception:
                pass
        if hours > 0:
            total_hours += hours
            daily_records.append({
                'date': record.get('date'),
                'checkIn': record.get('checkIn'),
                'checkOut': record.get('checkOut'),
                'hours': round(hours, 2),
                'location': record.get('checkInLocation', {}).get('address', ''),
                'status': record.get('status', ''),
            })

    settings = svc.get_settings()
    required_hours = settings.get('requiredOjtHours', 600)

    return jsonify({
        'student': {
            'id': student_id,
            'name': student.get('name'),
            'email': student.get('email'),
            'company': student.get('company', ''),
            'department': student.get('department', '')
        },
        'summary': {
            'totalHours': round(total_hours, 2),
            'requiredHours': required_hours,
            'remainingHours': round(max(0, required_hours - total_hours), 2),
            'progressPercentage': round((total_hours / required_hours * 100), 2) if required_hours > 0 else 0,
            'totalDays': len(daily_records)
        },
        'dailyRecords': daily_records,
        'generatedAt': datetime.now(timezone.utc).isoformat()
    }), 200
