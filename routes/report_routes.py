"""
TrackIt System - Report Routes
Handles accomplishment reports submission and management
"""

from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
from datetime import datetime, timezone
from services.firebase_service import FirebaseService
import json
import os
import re
import uuid

report_bp = Blueprint('reports', __name__)
_firebase_service = None


def set_firebase_service(service: FirebaseService):
    global _firebase_service
    _firebase_service = service


def _get_service() -> FirebaseService:
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service


# Roles that can see all student reports in web admin
STAFF_REPORT_ROLES = {'coordinator', 'chairman', 'adviser', 'supervisor'}
ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}


def _staff_can_view_all_reports(role: str) -> bool:
    r = (role or '').lower().strip()
    effective = ROLE_ALIASES.get(r, r)
    return effective in STAFF_REPORT_ROLES or r in STAFF_REPORT_ROLES | set(ROLE_ALIASES.keys())


def _can_review_reports(role: str) -> bool:
    """Advisers, coordinators, and chairmen may approve or return accomplishment reports."""
    r = (role or '').lower().strip()
    effective = ROLE_ALIASES.get(r, r)
    return effective in {'adviser', 'coordinator', 'chairman'}


def _can_see_approval_details(role: str) -> bool:
    """Only coordinators and chairmen may see approval status, approvedBy, and approvedAt."""
    r = (role or '').lower().strip()
    effective = ROLE_ALIASES.get(r, r)
    return effective in {'coordinator', 'chairman'}


def _strip_approval_fields(report: dict) -> dict:
    """Return a copy of the report with approval-related fields removed."""
    restricted = {'approvedBy', 'approvedByName', 'approvedAt', 'reviewedBy', 'reviewedAt'}
    return {k: v for k, v in report.items() if k not in restricted}


def _save_report_image(file) -> str:
    """Save one uploaded image and return its public URL path."""
    file_ext = os.path.splitext(file.filename or '')[1] or '.jpg'
    upload_dir = os.path.join(os.path.dirname(__file__), '..', 'uploads', 'reports')
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, f"{uuid.uuid4()}{file_ext}")
    file.save(file_path)
    return f"/uploads/reports/{os.path.basename(file_path)}"


def _parse_daily_entries(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _apply_uploaded_images_to_entries(daily_entries: list, files) -> str:
    """Attach uploaded images to daily entries. Returns first image URL for legacy fileUrl."""
    first_url = None
    if not daily_entries:
        return None

    for key, file in files.items():
        if not file or not getattr(file, 'filename', None):
            continue

        day_num = None
        idx = None
        if key.startswith('image_day_'):
            try:
                day_num = int(key.replace('image_day_', ''))
            except ValueError:
                continue
        elif key == 'file':
            url = _save_report_image(file)
            if first_url is None:
                first_url = url
            if daily_entries and 'imageUrl' not in daily_entries[0]:
                daily_entries[0]['imageUrl'] = url
            continue
        elif key.startswith('image_'):
            try:
                idx = int(key.split('_', 1)[1])
            except (ValueError, IndexError):
                continue
        else:
            continue

        url = _save_report_image(file)
        if first_url is None:
            first_url = url

        if day_num is not None:
            for entry in daily_entries:
                if int(entry.get('day', 0)) == day_num:
                    entry['imageUrl'] = url
                    break
        elif idx is not None and idx < len(daily_entries):
            daily_entries[idx]['imageUrl'] = url

    return first_url


def _daily_entries_from_content(content: str, image_urls_by_index: dict) -> list:
    """Build daily entry list from plain-text content (legacy reports)."""
    if not content:
        return []
    entries = []
    pattern = re.compile(r'Day\s*(\d+)\s*:\s*(.+?)(?=\n\nDay\s*\d+\s*:|$)', re.IGNORECASE | re.DOTALL)
    for match in pattern.finditer(content):
        day_num = int(match.group(1))
        description = match.group(2).strip()
        idx = day_num - 1
        entry = {'day': day_num, 'description': description}
        if idx in image_urls_by_index:
            entry['imageUrl'] = image_urls_by_index[idx]
        elif day_num == 1 and 0 in image_urls_by_index:
            entry['imageUrl'] = image_urls_by_index[0]
        entries.append(entry)
    return entries


def _resolve_user_role(current_user_id: str) -> str:
    claims = get_jwt() or {}
    role = claims.get('role')
    if role:
        r = role.lower().strip()
        return ROLE_ALIASES.get(r, r)
    current_user = _get_service().get_user(current_user_id)
    if not current_user:
        return 'student'
    r = (current_user.get('role') or 'student').lower().strip()
    return ROLE_ALIASES.get(r, r)


@report_bp.route('', methods=['GET'])
@report_bp.route('/', methods=['GET'])
@jwt_required()
def get_reports():
    """Get reports with optional filters.
    Coordinators/chairmen see all reports.
    Advisers see only reports from their assigned students.
    Students see only their own reports.
    """
    current_user_id = get_jwt_identity()
    svc = _get_service()

    status = request.args.get('status')
    student_id = request.args.get('studentId')
    start_date = request.args.get('startDate')
    end_date = request.args.get('endDate')

    user_role = _resolve_user_role(current_user_id)

    if user_role == 'adviser':
        # Adviser: only see reports from their assigned students
        my_students = svc.get_students_by_adviser(current_user_id)
        student_ids = [s['id'] for s in my_students]
        if student_id:
            # Further filter if a specific student was requested
            student_ids = [sid for sid in student_ids if sid == student_id]
        all_reports = []
        for sid in student_ids:
            reps = svc.get_reports(
                student_id=sid,
                status=status,
                start_date=start_date,
                end_date=end_date,
            )
            all_reports.extend(reps)
        reports = all_reports
    elif _staff_can_view_all_reports(user_role):
        # Coordinator / chairman / supervisor: see all
        reports = svc.get_reports(
            status=status,
            student_id=student_id,
            start_date=start_date,
            end_date=end_date,
        )
    else:
        # Student: own reports only
        reports = svc.get_reports(
            student_id=current_user_id,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

    # Strip approval details for roles that must not see them
    if not _can_see_approval_details(user_role):
        reports = [_strip_approval_fields(r) for r in reports]

    return jsonify(reports), 200


@report_bp.route('/upload-day-image', methods=['POST'])
@jwt_required()
def upload_day_image():
    """Upload a single day proof image; returns URL for use in dailyEntries."""
    if 'file' not in request.files:
        return jsonify({'error': 'No image file'}), 400
    file = request.files['file']
    if not file or not file.filename:
        return jsonify({'error': 'No image file'}), 400
    url = _save_report_image(file)
    return jsonify({'success': True, 'imageUrl': url}), 201


@report_bp.route('/submit', methods=['POST'])
@jwt_required()
def submit_report():
    """Submit accomplishment report"""
    current_user_id = get_jwt_identity()
    
    data = {}
    daily_entries = []

    # Handle multipart form data (for file uploads)
    if request.content_type and 'multipart/form-data' in request.content_type:
        title = request.form.get('title')
        content = request.form.get('content')
        week = request.form.get('week')
        company = request.form.get('company')
        start_date = request.form.get('startDate')
        end_date = request.form.get('endDate')
        daily_entries = _parse_daily_entries(request.form.get('dailyEntries'))
        file_url = _apply_uploaded_images_to_entries(daily_entries, request.files)

        if not daily_entries and file_url:
            daily_entries = _daily_entries_from_content(content, {0: file_url})
    else:
        data = request.get_json() or {}
        title = data.get('title')
        content = data.get('content')
        week = data.get('week')
        company = data.get('company')
        file_url = data.get('fileUrl')
        start_date = data.get('startDate')
        end_date = data.get('endDate')
        daily_entries = _parse_daily_entries(data.get('dailyEntries'))
        if not daily_entries and file_url and content:
            daily_entries = _daily_entries_from_content(content, {0: file_url})
        if not file_url and daily_entries:
            for entry in daily_entries:
                if entry.get('imageUrl'):
                    file_url = entry['imageUrl']
                    break

    # Validate required fields
    if not title or not content:
        return jsonify({'error': 'Title and content are required'}), 400
    
    # Create report record
    report = {
        'studentId': current_user_id,
        'title': title,
        'content': content,
        'week': week,
        'company': company,
        'startDate': start_date,
        'endDate': end_date,
        'fileUrl': file_url,
        'status': 'pending',
        'submittedAt': datetime.now(timezone.utc).isoformat(),
        'createdAt': datetime.now(timezone.utc).isoformat()
    }
    if daily_entries:
        report['dailyEntries'] = daily_entries

    # Add student name for web admin display
    student = _get_service().get_user(current_user_id)
    if student:
        report['studentName'] = student.get('name', 'Unknown')
        report['studentIdNumber'] = student.get('idNumber', '')
    
    result = _get_service().create_report(report)
    
    if result['success']:
        return jsonify({
            'success': True,
            'report': result['report']
        }), 201
    else:
        return jsonify({'error': result['error']}), 400

@report_bp.route('/<report_id>', methods=['GET'])
@jwt_required()
def get_report(report_id):
    """Get specific report. Approval details are only included for coordinator/chairman."""
    current_user_id = get_jwt_identity()
    user_role = _resolve_user_role(current_user_id)

    report = _get_service().get_report(report_id)

    if not report:
        return jsonify({'error': 'Report not found'}), 404

    # Students may only fetch their own reports
    if user_role == 'student' and report.get('studentId') != current_user_id:
        return jsonify({'error': 'Unauthorized'}), 403

    # Strip approval details for roles that must not see them
    if not _can_see_approval_details(user_role):
        report = _strip_approval_fields(report)

    return jsonify(report), 200

@report_bp.route('/<report_id>', methods=['DELETE'])
@jwt_required()
def delete_report(report_id):
    """Delete a report — owner or staff (coordinator/adviser/chairman) can delete."""
    current_user_id = get_jwt_identity()
    svc = _get_service()

    report = svc.get_report(report_id)
    if not report:
        return jsonify({'error': 'Report not found'}), 404

    # Allow: the student who owns it, or any coordinator/adviser/chairman/supervisor
    caller = svc.get_user(current_user_id)
    _role_aliases = {'admin': 'coordinator', 'employee': 'adviser'}
    raw_role = (caller.get('role', '') if caller else '').strip().lower()
    caller_role = _role_aliases.get(raw_role, raw_role)
    STAFF_ROLES = {'coordinator', 'adviser', 'chairman', 'supervisor', 'admin'}
    if report.get('studentId') != current_user_id and caller_role not in STAFF_ROLES:
        return jsonify({'error': 'Unauthorized'}), 403

    try:
        if svc.db:
            svc.db.collection('reports').document(report_id).delete()
        else:
            if hasattr(svc, 'reports_db') and report_id in svc.reports_db:
                del svc.reports_db[report_id]
        return jsonify({'success': True, 'message': 'Report deleted'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to delete report: {str(e)}'}), 500

@report_bp.route('/<report_id>/review', methods=['POST'])
@jwt_required()
def review_report(report_id):
    """Review and approve/return report (adviser, coordinator, or chairman).
    On approval, saves approvedByName and approvedAt for coordinator/chairman display.
    """
    current_user_id = get_jwt_identity()
    user_role = _resolve_user_role(current_user_id)
    if not _can_review_reports(user_role):
        return jsonify({'error': 'Only advisers, coordinators, and chairmen can approve or return reports'}), 403

    data = request.get_json()

    status = data.get('status')  # 'approved' or 'returned'
    feedback = data.get('feedback', '')

    if status not in ['approved', 'returned']:
        return jsonify({'error': 'Invalid status'}), 400

    svc = _get_service()
    reviewer = svc.get_user(current_user_id)
    reviewer_name = (reviewer.get('name', '') if reviewer else '').strip()
    # Fallback: if name is blank, use email (prevents storing an empty approvedByName)
    if not reviewer_name and reviewer:
        reviewer_name = (reviewer.get('email', '') or '').strip()

    now = datetime.now(timezone.utc).isoformat()

    update_data = {
        'status': status,
        'feedback': feedback,
        'reviewedBy': current_user_id,
        'reviewedAt': now,
        'updatedAt': now,
    }

    # Store approval details for coordinator/chairman display
    if status == 'approved':
        update_data['approvedBy'] = current_user_id
        update_data['approvedByName'] = reviewer_name
        update_data['approvedAt'] = now
    else:
        # Returning a report clears any previous approval details
        update_data['approvedBy'] = None
        update_data['approvedByName'] = None
        update_data['approvedAt'] = None

    result = svc.update_report(report_id, update_data)

    if result['success']:
        report_out = result['report']
        # Return full report only to coordinator/chairman; strip for others
        if not _can_see_approval_details(user_role):
            report_out = _strip_approval_fields(report_out)
        return jsonify({'success': True, 'report': report_out}), 200
    else:
        return jsonify({'error': result['error']}), 400

@report_bp.route('/<report_id>/download', methods=['GET'])
@jwt_required()
def download_report(report_id):
    """Download report file"""
    report = _get_service().get_report(report_id)
    
    if not report:
        return jsonify({'error': 'Report not found'}), 404
    
    file_url = report.get('fileUrl')
    if not file_url:
        return jsonify({'error': 'No file attached to this report'}), 404
    
    # In production, serve file from cloud storage
    # For now, return the file URL
    return jsonify({'fileUrl': file_url}), 200

@report_bp.route('/student/<student_id>', methods=['GET'])
@jwt_required()
def get_student_reports(student_id):
    """Get all reports for a specific student.
    Students can only access their own reports; staff can access any student's.
    """
    current_user_id = get_jwt_identity()
    user_role = _resolve_user_role(current_user_id)
    # Students may only fetch their own reports
    if current_user_id != student_id and not _staff_can_view_all_reports(user_role):
        return jsonify({'error': 'Unauthorized'}), 403
    reports = _get_service().get_reports(student_id=student_id)
    # Strip approval details for roles that must not see them
    if not _can_see_approval_details(user_role):
        reports = [_strip_approval_fields(r) for r in reports]
    return jsonify(reports), 200
