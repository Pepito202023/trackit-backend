"""
TrackIt System - Application Plan Routes
Handles student application plans, HTE profiles, matching rules,
and automatic HTE assignment.
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from services.firebase_service import FirebaseService

application_plan_bp = Blueprint('application_plans', __name__)

_firebase_service = None


def set_firebase_service(service: FirebaseService):
    global _firebase_service
    _firebase_service = service


def _get_service() -> FirebaseService:
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service


# ─────────────────────────────────────────────────────────────────────────────
# APPLICATION PLANS — Student CRUD
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('', methods=['GET'])
@jwt_required()
def list_application_plans():
    """
    GET /api/application-plans
    Coordinators/chairmen see all plans (optionally filtered by ?status=).
    Students only see their own plans.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    # Identify caller role
    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')

    status_filter = request.args.get('status') or None
    student_id_filter = request.args.get('studentId') or None

    if role in ('coordinator', 'chairman', 'admin'):
        # Admin views: all plans, optionally narrowed by studentId or status
        plans = svc.get_application_plans(
            student_id=student_id_filter,
            status=status_filter,
        )
    else:
        # Students see only their own plans
        plans = svc.get_application_plans(
            student_id=current_user_id,
            status=status_filter,
        )

    return jsonify(plans), 200


@application_plan_bp.route('/<plan_id>', methods=['GET'])
@jwt_required()
def get_application_plan(plan_id):
    """GET /api/application-plans/<plan_id>"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    plan = svc.get_application_plan(plan_id)
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')

    # Students can only read their own plan
    if role == 'student' and plan.get('studentId') != current_user_id:
        return jsonify({'error': 'Access denied'}), 403

    return jsonify(plan), 200


@application_plan_bp.route('', methods=['POST'])
@jwt_required()
def create_application_plan():
    """
    POST /api/application-plans
    Student submits a new application plan.
    Required fields: course, skills (list), interests (list),
                     preferredLocation, otherInfo (optional)
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    user = svc.get_user(current_user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404

    course = (data.get('course') or '').strip()
    if not course:
        return jsonify({'error': 'Course is required'}), 400

    # Check if student already has a pending/submitted plan
    existing = svc.get_application_plans(student_id=current_user_id)
    active = [p for p in existing if p.get('status') in ('pending', 'submitted', 'assigned')]
    if active:
        existing_plan = active[0]
        # If already assigned, just return the existing plan — no re-assignment needed
        if existing_plan.get('status') == 'assigned':
            existing_plan['autoMatched'] = True
            existing_plan['matchNote'] = 'Already assigned to: ' + (existing_plan.get('assignedHteName') or '')
            return jsonify(existing_plan), 200

        # For pending/submitted: update the plan with new answers then re-run auto-match
        update_fields = {
            'course': course,
            'requiredHours': int(data.get('requiredHours', 600)),
            'skills': [s.strip() for s in (data.get('skills') or []) if str(s).strip()],
            'interests': [i.strip() for i in (data.get('interests') or []) if str(i).strip()],
            'preferredLocation': (data.get('preferredLocation') or '').strip(),
            'otherInfo': (data.get('otherInfo') or '').strip(),
            'competencies': data.get('competencies') or [],
            'department': (data.get('department') or user.get('department', '')).strip(),
        }
        update_result = svc.update_application_plan(existing_plan['id'], update_fields)
        if not update_result.get('success'):
            return jsonify({'error': update_result.get('error', 'Failed to update plan')}), 400

        merged = dict(existing_plan)
        merged.update(update_fields)
        merged['id'] = existing_plan['id']

        assign_result = svc.auto_assign_plan(
            plan_id=existing_plan['id'],
            plan=merged,
            assigned_by='system',
        )
        if assign_result.get('success'):
            result_plan = assign_result['plan']
            result_plan['autoMatched'] = True
            result_plan['matchedHte'] = assign_result.get('hte')
            return jsonify(result_plan), 200
        merged['autoMatched'] = False
        merged['matchNote'] = assign_result.get(
            'error',
            'No matching HTE found. A coordinator will review your plan.',
        )

        return jsonify(merged), 200

    plan = {
        'studentId': current_user_id,
        'studentName': user.get('name', ''),
        'studentIdNumber': user.get('idNumber', ''),
        'department': (data.get('department') or user.get('department', '')).strip(),
        'course': course,
        'requiredHours': int(data.get('requiredHours', 600)),
        'skills': [s.strip() for s in (data.get('skills') or []) if str(s).strip()],
        'interests': [i.strip() for i in (data.get('interests') or []) if str(i).strip()],
        'preferredLocation': (data.get('preferredLocation') or '').strip(),
        'otherInfo': (data.get('otherInfo') or '').strip(),
        # Structured competency rows from the internship plan table
        'competencies': data.get('competencies') or [],
        'status': 'submitted',
        'assignedHteId': None,
        'assignedHteName': None,
        'assignedAt': None,
        'assignedBy': None,
        'reviewNote': None,
    }

    result = svc.create_application_plan(plan)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Failed to create plan')}), 400

    created_plan = result['plan']

    # ── Automatic matching ────────────────────────────────────────────────────
    assign_result = svc.auto_assign_plan(
        plan_id=created_plan['id'],
        plan=plan,
        assigned_by='system',
    )
    if assign_result.get('success'):
        created_plan = assign_result['plan']
        created_plan['autoMatched'] = True
        created_plan['matchedHte'] = assign_result.get('hte')
    else:
        created_plan['autoMatched'] = False
        created_plan['matchNote'] = assign_result.get(
            'error',
            'No matching HTE found. A coordinator will review your plan.',
        )

    return jsonify(created_plan), 201


@application_plan_bp.route('/<plan_id>', methods=['PUT'])
@jwt_required()
def update_application_plan(plan_id):
    """
    PUT /api/application-plans/<plan_id>
    Students can update their own pending plan.
    Coordinators/chairmen can update any plan (approve/reject/reassign).
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    plan = svc.get_application_plan(plan_id)
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')

    if role == 'student':
        # Students can only edit their own plan while it is pending/submitted
        if plan.get('studentId') != current_user_id:
            return jsonify({'error': 'Access denied'}), 403
        if plan.get('status') not in ('submitted', 'pending'):
            return jsonify({'error': 'Plan cannot be edited in its current status'}), 400
        allowed = {'course', 'skills', 'interests', 'preferredLocation', 'otherInfo',
                   'competencies', 'requiredHours', 'department'}
        update = {k: v for k, v in data.items() if k in allowed}
    else:
        # Coordinator / chairman can set status, reviewNote, assignedHteId
        update = {}
        for field in ('status', 'reviewNote', 'assignedHteId', 'assignedHteName',
                      'course', 'skills', 'interests', 'preferredLocation', 'otherInfo'):
            if field in data:
                update[field] = data[field]
        if 'status' in update:
            update['reviewedBy'] = current_user_id
            update['reviewedAt'] = __import__('datetime').datetime.now().isoformat()

    if not update:
        return jsonify({'error': 'No valid fields to update'}), 400

    result = svc.update_application_plan(plan_id, update)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Update failed')}), 400

    updated_plan = result['plan']

    # ── Re-run auto-match for student updates ─────────────────────────────────
    # When a student saves/re-saves their plan we attempt matching again so they
    # are not left pending just because they edited a field after first submit.
    if role == 'student' and updated_plan.get('status') in ('submitted', 'pending'):
        # Build a merged plan dict with the latest field values for scoring
        merged = dict(plan)
        merged.update(update)
        merged['id'] = plan_id

        assign_result = svc.auto_assign_plan(
            plan_id=plan_id,
            plan=merged,
            assigned_by='system',
        )
        if assign_result.get('success'):
            updated_plan = assign_result['plan']
            updated_plan['autoMatched'] = True
            updated_plan['matchedHte'] = assign_result.get('hte')
        else:
            updated_plan['autoMatched'] = False
            updated_plan['matchNote'] = assign_result.get(
                'error',
                'No matching HTE found. A coordinator will review your plan.',
            )

    return jsonify(updated_plan), 200


@application_plan_bp.route('/<plan_id>', methods=['DELETE'])
@jwt_required()
def delete_application_plan(plan_id):
    """
    DELETE /api/application-plans/<plan_id>
    Students withdraw their own plan (only if not yet assigned).
    Coordinators can delete any plan.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    plan = svc.get_application_plan(plan_id)
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')

    if role == 'student':
        if plan.get('studentId') != current_user_id:
            return jsonify({'error': 'Access denied'}), 403
        if plan.get('status') == 'assigned':
            return jsonify({'error': 'Cannot withdraw an already-assigned plan'}), 400

    result = svc.delete_application_plan(plan_id)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Delete failed')}), 400

    return jsonify({'success': True}), 200


# ─────────────────────────────────────────────────────────────────────────────
# MANUAL ASSIGN  POST /api/application-plans/<plan_id>/assign
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('/<plan_id>/assign', methods=['POST'])
@jwt_required()
def manual_assign(plan_id):
    """
    POST /api/application-plans/<plan_id>/assign
    Body: { "hteId": "<id>", "note": "..." }
    Coordinator/chairman manually assigns a student to an HTE.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Only coordinators or chairmen can assign HTEs'}), 403

    plan = svc.get_application_plan(plan_id)
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404

    hte_id = (data.get('hteId') or '').strip()
    if not hte_id:
        return jsonify({'error': 'hteId is required'}), 400

    result = svc.assign_student_to_hte(plan_id, hte_id, assigned_by=current_user_id)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Assignment failed')}), 400

    # Optionally add a review note
    note = (data.get('note') or '').strip()
    if note:
        svc.update_application_plan(plan_id, {'reviewNote': note})
        result['plan']['reviewNote'] = note

    return jsonify(result['plan']), 200


# ─────────────────────────────────────────────────────────────────────────────
# RE-RUN AUTO-MATCH  POST /api/application-plans/<plan_id>/auto-match
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('/<plan_id>/auto-match', methods=['POST'])
@jwt_required()
def rematch(plan_id):
    """
    POST /api/application-plans/<plan_id>/auto-match
    Coordinator triggers a fresh auto-match for a plan that was not matched.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    plan = svc.get_application_plan(plan_id)
    if not plan:
        return jsonify({'error': 'Plan not found'}), 404

    result = svc.auto_assign_plan(plan_id, plan, assigned_by=current_user_id)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Assignment failed')}), 400

    return jsonify(result['plan']), 200


# ─────────────────────────────────────────────────────────────────────────────
# HTE PROFILES — Coordinator CRUD
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('/htes', methods=['GET'])
@jwt_required()
def list_htes():
    """GET /api/application-plans/htes — list all HTE profiles."""
    svc = _get_service()
    return jsonify(svc.get_hte_profiles()), 200


@application_plan_bp.route('/htes/<hte_id>', methods=['GET'])
@jwt_required()
def get_hte(hte_id):
    """GET /api/application-plans/htes/<hte_id>"""
    svc = _get_service()
    hte = svc.get_hte_profile(hte_id)
    if not hte:
        return jsonify({'error': 'HTE not found'}), 404
    return jsonify(hte), 200


@application_plan_bp.route('/htes', methods=['POST'])
@jwt_required()
def create_hte():
    """POST /api/application-plans/htes"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'HTE name is required'}), 400

    hte = {
        'name': name,
        'location': (data.get('location') or '').strip(),
        'address': (data.get('address') or '').strip(),
        'contactPerson': (data.get('contactPerson') or '').strip(),
        'contactEmail': (data.get('contactEmail') or '').strip(),
        'contactPhone': (data.get('contactPhone') or '').strip(),
        'description': (data.get('description') or '').strip(),
        'fieldOfWork': [f.strip() for f in (data.get('fieldOfWork') or []) if str(f).strip()],
        'requiredCourses': [c.strip() for c in (data.get('requiredCourses') or []) if str(c).strip()],
        'requiredSkills': [s.strip() for s in (data.get('requiredSkills') or []) if str(s).strip()],
        'availableSlots': int(data.get('availableSlots', 0)),
        'totalSlots': int(data.get('totalSlots', 0)),
        'status': (data.get('status') or 'active').strip(),
        'adviser': (data.get('adviser') or '').strip(),
        'adviserId': (data.get('adviserId') or '').strip(),
        'createdBy': current_user_id,
    }

    result = svc.create_hte_profile(hte)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Failed to create HTE')}), 400

    return jsonify(result['hte']), 201


@application_plan_bp.route('/htes/<hte_id>', methods=['PUT'])
@jwt_required()
def update_hte(hte_id):
    """PUT /api/application-plans/htes/<hte_id>"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    data = request.get_json() or {}
    allowed = {
        'name', 'location', 'address', 'contactPerson', 'contactEmail',
        'contactPhone', 'description', 'fieldOfWork', 'requiredCourses',
        'requiredSkills', 'availableSlots', 'totalSlots', 'status',
        'adviser', 'adviserId',
    }
    update = {k: v for k, v in data.items() if k in allowed}
    if 'availableSlots' in update:
        update['availableSlots'] = int(update['availableSlots'])
    if 'totalSlots' in update:
        update['totalSlots'] = int(update['totalSlots'])

    if not update:
        return jsonify({'error': 'No valid fields to update'}), 400

    result = svc.update_hte_profile(hte_id, update)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Update failed')}), 400

    return jsonify(result['hte']), 200


@application_plan_bp.route('/htes/<hte_id>', methods=['DELETE'])
@jwt_required()
def delete_hte(hte_id):
    """DELETE /api/application-plans/htes/<hte_id>"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    result = svc.delete_hte_profile(hte_id)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Delete failed')}), 400

    return jsonify({'success': True}), 200


# ─────────────────────────────────────────────────────────────────────────────
# HTE DOCUMENT UPLOAD  POST /api/application-plans/htes/upload
# Accepts a PDF or DOCX file, extracts text, and uses NLP heuristics to parse
# HTE fields: name, location, industry/fieldOfWork, required courses, required
# skills, available positions, capacity, and any other placement requirements.
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('/htes/upload', methods=['POST'])
@jwt_required()
def upload_hte_document():
    """
    POST /api/application-plans/htes/upload
    Accepts multipart/form-data with field 'file' (PDF or DOCX).
    Extracts HTE info and creates (or updates) an HTE profile in hte_profiles.
    Returns the created/updated HTE record.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    uploaded = request.files['file']
    filename = (uploaded.filename or '').lower()
    if not (filename.endswith('.pdf') or filename.endswith('.docx') or filename.endswith('.doc')):
        return jsonify({'error': 'Only PDF and DOCX files are supported'}), 400

    file_bytes = uploaded.read()
    if not file_bytes:
        return jsonify({'error': 'Uploaded file is empty'}), 400

    # ── Extract raw text ──────────────────────────────────────────────────────
    raw_text = ''
    try:
        if filename.endswith('.pdf'):
            raw_text = _extract_pdf_text(file_bytes)
        else:
            raw_text = _extract_docx_text(file_bytes)
    except Exception as e:
        print(f"[HTE upload] Text extraction error: {e}")
        return jsonify({'error': f'Could not read file: {str(e)}'}), 400

    if not raw_text.strip():
        return jsonify({'error': 'Could not extract any text from the file. Make sure the document is not image-only.'}), 400

    print(f"[HTE upload] Extracted {len(raw_text)} chars from '{uploaded.filename}'")

    # ── Parse HTE fields from text ────────────────────────────────────────────
    parsed = _parse_hte_text(raw_text)
    print(f"[HTE upload] Parsed fields: {list(parsed.keys())}")

    # ── Override with any manually provided fields in the form ────────────────
    for field in ('name', 'location', 'address', 'contactPerson', 'contactEmail',
                  'contactPhone', 'description', 'availableSlots', 'totalSlots', 'status'):
        val = request.form.get(field, '').strip()
        if val:
            parsed[field] = val

    if not parsed.get('name'):
        return jsonify({
            'error': 'Could not extract HTE name from document. '
                     'Please provide it manually using the "name" form field.',
            'extractedText': raw_text[:500],
        }), 422

    # Cast slot counts
    for slot_field in ('availableSlots', 'totalSlots'):
        try:
            parsed[slot_field] = int(parsed.get(slot_field, 0))
        except (TypeError, ValueError):
            parsed[slot_field] = 0

    parsed.setdefault('status', 'active')
    parsed['createdBy'] = current_user_id
    parsed['sourceDocument'] = uploaded.filename

    # ── Create HTE profile ────────────────────────────────────────────────────
    result = svc.create_hte_profile(parsed)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Failed to save HTE profile')}), 400

    # ── Also sync extracted data to the matching Partner Company ─────────────
    # Find a company whose name matches the extracted HTE name (case-insensitive)
    hte_name = (parsed.get('name') or '').strip().lower()
    extracted_location = parsed.get('address') or parsed.get('location') or ''
    extracted_supervisor = parsed.get('contactPerson') or ''
    if hte_name:
        try:
            companies = svc.get_companies()
            for company in companies:
                if (company.get('name') or '').strip().lower() == hte_name:
                    sync = {}
                    if extracted_location:
                        sync['address'] = extracted_location
                        sync['contact'] = parsed.get('contactPhone') or extracted_location
                    if extracted_supervisor and not (company.get('supervisor') or '').strip():
                        sync['supervisor'] = extracted_supervisor
                    if sync:
                        svc.update_company(company['id'], sync)
                        print(f"[HTE upload] Synced location/supervisor to company '{company['name']}'")
                    break
            else:
                # No match — create a new partner company from extracted data
                svc.create_company({
                    'name': parsed.get('name', ''),
                    'address': extracted_location,
                    'contact': parsed.get('contactPhone') or extracted_location,
                    'supervisor': extracted_supervisor,
                    'description': parsed.get('description', ''),
                    'status': 'active',
                })
                print(f"[HTE upload] Created new company '{parsed.get('name')}'")
        except Exception as sync_err:
            print(f"[HTE upload] Warning: company sync failed: {sync_err}")

    return jsonify({
        'success': True,
        'hte': result['hte'],
        'extractedFields': list(parsed.keys()),
    }), 201


# ─────────────────────────────────────────────────────────────────────────────
# HTE EXCEL UPLOAD  POST /api/application-plans/htes/upload-excel
# Accepts an Excel file (.xlsx or .xls) with HTE companies and creates HTE profiles.
# Required columns: Company Name, Location, Required Skills, Adviser
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('/htes/upload-excel', methods=['POST'])
@jwt_required()
def upload_hte_excel():
    """
    POST /api/application-plans/htes/upload-excel
    Accepts multipart/form-data with field 'file' (.xlsx or .xls).
    Expected columns: Company Name, Location, Required Skills, Supervisor, Adviser
    Creates HTE profiles for each valid row.
    Returns success message with count of created/failed entries.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400

    uploaded = request.files['file']
    filename = (uploaded.filename or '').lower()
    if not (filename.endswith('.xlsx') or filename.endswith('.xls')):
        return jsonify({'error': 'Only Excel files (.xlsx or .xls) are supported'}), 400

    file_bytes = uploaded.read()
    if not file_bytes:
        return jsonify({'error': 'Uploaded file is empty'}), 400

    # ── Parse Excel file ──────────────────────────────────────────────────────
    try:
        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        sheet = wb.active
        
        # Get header row
        headers = []
        for cell in sheet[1]:
            headers.append(str(cell.value).strip() if cell.value else '')
        
        # Validate required columns
        required_columns = ['Company Name', 'Location', 'Required Skills', 'Supervisor', 'Adviser']
        missing_columns = [col for col in required_columns if col not in headers]
        if missing_columns:
            return jsonify({
                'error': f'Missing required columns: {", ".join(missing_columns)}',
                'requiredColumns': required_columns,
                'foundColumns': headers,
            }), 400
        
        # Map column names to indices
        col_indices = {col: idx for idx, col in enumerate(headers)}
        
        # Parse rows
        created = []
        failed = []
        row_num = 2  # Start from row 2 (skip header)
        
        for row in sheet.iter_rows(min_row=2, values_only=True):
            if not any(row):  # Skip empty rows
                row_num += 1
                continue
            
            try:
                company_name = str(row[col_indices['Company Name']] or '').strip()
                location = str(row[col_indices['Location']] or '').strip()
                required_skills_str = str(row[col_indices['Required Skills']] or '').strip()
                supervisor = str(row[col_indices['Supervisor']] or '').strip()
                adviser = str(row[col_indices['Adviser']] or '').strip()

                if not company_name:
                    failed.append({'row': row_num, 'error': 'Company Name is required'})
                    row_num += 1
                    continue

                # Parse skills (comma-separated)
                required_skills = [s.strip() for s in required_skills_str.split(',') if s.strip()]

                # Create HTE profile
                hte = {
                    'name': company_name,
                    'location': location,
                    'address': location,
                    'requiredSkills': required_skills,
                    'supervisor': supervisor,
                    'adviser': adviser,
                    'availableSlots': 10,  # Default slots
                    'totalSlots': 10,
                    'status': 'active',
                    'createdBy': current_user_id,
                }
                
                result = svc.create_hte_profile(hte)
                if result.get('success'):
                    created.append({
                        'row': row_num,
                        'company': company_name,
                        'id': result['hte'].get('id'),
                    })
                else:
                    failed.append({
                        'row': row_num,
                        'company': company_name,
                        'error': result.get('error', 'Unknown error'),
                    })
            except Exception as e:
                failed.append({'row': row_num, 'error': str(e)})
            
            row_num += 1
        
        return jsonify({
            'success': True,
            'message': f'Processed {len(created) + len(failed)} rows. Created {len(created)} HTE profiles.',
            'created': created,
            'failed': failed,
        }), 201
        
    except ImportError:
        return jsonify({'error': 'openpyxl library not installed. Run: pip install openpyxl'}), 500
    except Exception as e:
        print(f"[HTE Excel upload] Error: {e}")
        return jsonify({'error': f'Failed to process Excel file: {str(e)}'}), 400


# ─────────────────────────────────────────────────────────────────────────────
# Text extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_pdf_text(file_bytes: bytes) -> str:
    """Extract text from a PDF using PyMuPDF (fitz) if available, else pdfminer."""
    # Try PyMuPDF first (fastest, best layout)
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=file_bytes, filetype='pdf')
        parts = []
        for page in doc:
            parts.append(page.get_text())
        return '\n'.join(parts)
    except ImportError:
        pass

    # Fallback: pdfminer.six
    try:
        import io
        from pdfminer.high_level import extract_text as pm_extract
        return pm_extract(io.BytesIO(file_bytes))
    except ImportError:
        pass

    # Last resort: pypdf
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        return '\n'.join(page.extract_text() or '' for page in reader.pages)
    except ImportError:
        pass

    raise RuntimeError(
        'No PDF library available. Install PyMuPDF: pip install PyMuPDF'
    )


def _extract_docx_text(file_bytes: bytes) -> str:
    """Extract text from a DOCX using python-docx."""
    try:
        import io
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        return '\n'.join(p.text for p in doc.paragraphs)
    except ImportError:
        raise RuntimeError(
            'python-docx not installed. Run: pip install python-docx'
        )


def _parse_hte_text(text: str) -> dict:
    """
    Use regex + keyword heuristics to extract HTE fields from raw document text.
    Returns a dict with the fields we can populate in hte_profiles.
    """
    import re

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    full = text

    def first_match(patterns, default=''):
        for p in patterns:
            m = re.search(p, full, re.IGNORECASE | re.MULTILINE)
            if m:
                val = (m.group(1) or '').strip().rstrip('.,;')
                if val and len(val) <= 200:
                    return val
        return default

    def collect_list(patterns, limit=10):
        found = []
        for p in patterns:
            for m in re.finditer(p, full, re.IGNORECASE):
                item = m.group(1).strip().rstrip('.,;')
                if item and item not in found:
                    found.append(item)
        # Also collect bullet/numbered lines after section headers
        return found[:limit]

    def extract_section_lines(section_keywords, max_lines=8):
        """Return lines following a section header keyword."""
        results = []
        in_section = False
        header_re = re.compile(
            r'^(' + '|'.join(re.escape(k) for k in section_keywords) + r')',
            re.IGNORECASE
        )
        section_end_re = re.compile(r'^(contact|address|location|about|overview|background|requirements|qualifications|slots|capacity)', re.IGNORECASE)
        for line in lines:
            if header_re.match(line):
                in_section = True
                continue
            if in_section:
                if section_end_re.match(line) and results:
                    break
                clean = re.sub(r'^[-•*·\d.]+\s*', '', line).strip()
                if clean and len(clean) >= 3:
                    results.append(clean)
                if len(results) >= max_lines:
                    break
        return results

    hte = {}

    # ── Name ─────────────────────────────────────────────────────────────────
    name = first_match([
        r'(?:company|hte|establishment|organization|institution|employer)\s*(?:name)?\s*[:\-]\s*(.+)',
        r'(?:name\s+of\s+(?:company|hte|establishment|firm|agency))\s*[:\-]\s*(.+)',
        r'^([A-Z][A-Za-z0-9 &,.\'-]{5,60})\s*$',
    ])
    if not name and lines:
        for line in lines[:5]:
            if len(line) >= 4 and not re.match(r'^(internship|ojt|on.the.job|training|the|a |an )', line, re.IGNORECASE):
                name = line
                break
    hte['name'] = name

    # ── Location / Address ────────────────────────────────────────────────────
    # Be specific: require street/barangay/city keywords or explicit label
    location = first_match([
        r'(?:address|office\s*address|business\s*address|company\s*address|head\s*office\s*address)\s*[:\-]\s*(.+)',
        r'(?:location|head\s*office|situated\s*at|found\s*at)\s*[:\-]\s*(.+)',
        r'(?:located\s+(?:at|in))\s+([A-Za-z0-9 ,.\-#]+(?:Street|St\.|Avenue|Ave\.|Blvd|Road|Rd\.|Barangay|Brgy\.|City|Town|Province|Building|Bldg)[^.\n]{0,80})',
    ])
    # Validate: reject anything that looks like a phone number or is too short
    if location:
        phone_re = re.compile(r'^[\d\s\(\)\+\-]{7,}$')
        if phone_re.match(location.strip()) or len(location.strip()) < 5:
            location = ''
    hte['location'] = location
    hte['address'] = location

    # ── Contact Person / Supervisor ───────────────────────────────────────────
    contact_person = first_match([
        r'(?:contact\s*person|hr\s*(?:contact|manager|officer)|authorized\s*representative|signatory|officer\s*in\s*charge|oic|supervisor\s*name|company\s*representative)\s*[:\-]\s*([A-Za-z][^:\n\d\(\)]{3,60})',
        r'(?:prepared\s*by|submitted\s*by|signed\s*by)\s*[:\-]?\s*([A-Za-z][^:\n\d\(\)]{3,60})',
        r'(?:name\s+of\s+(?:supervisor|contact|representative|signatory))\s*[:\-]\s*([A-Za-z][^:\n\d\(\)]{3,60})',
    ])
    # Strip trailing titles/roles from the extracted name
    if contact_person:
        contact_person = re.sub(r'\s*[,\-]\s*(?:manager|supervisor|officer|hr|admin|director|coordinator).*$', '', contact_person, flags=re.IGNORECASE).strip()
    hte['contactPerson'] = contact_person

    hte['contactEmail'] = first_match([
        r'\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Z|a-z]{2,})\b',
    ])
    hte['contactPhone'] = first_match([
        r'(?:tel(?:ephone)?|phone|mobile|contact\s*(?:no|number)|fax)\s*[:\-]?\s*([\d\s\+\-\(\)]{7,25})',
        r'\b((?:\+?63|0)[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{4})\b',
        r'\b(0\d{2}[\s\-]?\d{3}[\s\-]?\d{4})\b',
    ])

    # ── Description / Industry ────────────────────────────────────────────────
    desc = first_match([
        r'(?:about|overview|company\s*profile|background|description)\s*[:\-]\s*(.+)',
    ])
    if not desc:
        # Use first substantive paragraph (skip short lines and phone/email lines)
        for line in lines[1:8]:
            if (len(line) >= 30 and
                    not re.search(r'[\d\(\)]{7,}', line) and
                    not re.search(r'@', line) and
                    not re.match(r'^(tel|phone|fax|contact|address|location)', line, re.IGNORECASE)):
                desc = line
                break
    hte['description'] = desc

    # ── Try to find address from context if not yet found ─────────────────────
    if not hte.get('location'):
        # Look for lines containing common Philippine address keywords
        addr_re = re.compile(
            r'.{5,}(?:Street|St\.|Avenue|Ave\.|Blvd\.?|Highway|Hwy\.?|Road|Rd\.|'
            r'Barangay|Brgy\.|Building|Bldg\.?|Purok|Sitio|'
            r'Misamis|Oroquieta|Ozamiz|Cagayan|Davao|Cebu|Manila|City|Province).{0,80}',
            re.IGNORECASE
        )
        for line in lines:
            m = addr_re.search(line)
            if m and not re.search(r'[\d]{7,}', line):  # skip if it's mostly digits
                candidate = m.group(0).strip()
                if 10 <= len(candidate) <= 200:
                    hte['location'] = candidate
                    hte['address'] = candidate
                    break

    # ── Field of Work / Industry ──────────────────────────────────────────────
    field_section = extract_section_lines(
        ['field of work', 'industry', 'sector', 'nature of business', 'type of work', 'area of work']
    )
    # Also inline patterns
    inline_fields = collect_list([
        r'(?:field\s*of\s*work|industry|sector)\s*[:\-]\s*([^.\n]{3,60})',
        r'(?:specializes\s+in|engaged\s+in|focuses\s+on)\s+([^.\n]{5,60})',
    ])
    hte['fieldOfWork'] = list(dict.fromkeys(field_section + inline_fields))[:8]

    # ── Required Courses ─────────────────────────────────────────────────────
    course_section = extract_section_lines(
        ['preferred course', 'required course', 'course preference', 'degree', 'program', 'educational requirement']
    )
    inline_courses = collect_list([
        r'(?:preferred|required|accepted)\s+(?:course|degree|program)\s*[:\-]?\s*([^.\n]{5,80})',
        r'\b(BS(?:IT|CS|EE|CE|ECE|IS|MIS|CpE|ME|IE|Arch|CivE|ChE|Psych|Acctg|BA|MM|HRM|Tourism|Nursing|Pharmacy|Education)[^\s,]*)',
    ])
    hte['requiredCourses'] = list(dict.fromkeys(course_section + inline_courses))[:10]

    # ── Required Skills ───────────────────────────────────────────────────────
    skill_section = extract_section_lines(
        ['required skill', 'skills needed', 'competencies', 'qualifications', 'skills required', 'must have']
    )
    inline_skills = collect_list([
        r'(?:skills?|competenc(?:y|ies)|qualifications?)\s*[:\-]\s*([^.\n]{3,60})',
        r'(?:proficient|knowledgeable|familiar)\s+(?:in|with)\s+([^.\n]{3,60})',
        r'(?:experience\s+in|background\s+in)\s+([^.\n]{3,60})',
    ])
    hte['requiredSkills'] = list(dict.fromkeys(skill_section + inline_skills))[:12]

    # ── Available Positions / OJT Positions ───────────────────────────────────
    position_section = extract_section_lines(
        ['available position', 'ojt position', 'internship position', 'job opening', 'vacancy', 'available slot']
    )
    inline_positions = collect_list([
        r'(?:position|job\s*title|role|opening)\s*[:\-]\s*([^.\n]{3,60})',
        r'(?:accepting|looking\s+for|need)\s+(?:interns?|students?)\s+(?:for|in)\s+([^.\n]{3,60})',
    ])
    all_positions = list(dict.fromkeys(position_section + inline_positions))[:10]
    # Store positions in fieldOfWork if not already present, and in description
    if all_positions:
        if not hte.get('fieldOfWork'):
            hte['fieldOfWork'] = all_positions
        # Append positions info to description
        pos_str = 'Positions: ' + ', '.join(all_positions)
        hte['description'] = ((hte.get('description') or '') + ' | ' + pos_str).strip(' |')

    # ── Available Slots / Capacity ────────────────────────────────────────────
    slots_str = first_match([
        r'(?:available\s*slots?|capacity|no\.?\s*of\s*(?:interns?|slots?|positions?)|number\s*of\s*(?:interns?|slots?))\s*[:\-]?\s*(\d{1,4})',
        r'(\d{1,4})\s*(?:slot|intern|position|opening)',
    ])
    try:
        hte['availableSlots'] = int(slots_str) if slots_str else 5
        hte['totalSlots'] = hte['availableSlots']
    except (ValueError, TypeError):
        hte['availableSlots'] = 5
        hte['totalSlots'] = 5

    return hte


# ─────────────────────────────────────────────────────────────────────────────
# MATCHING RULES — Coordinator CRUD
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('/matching-rules', methods=['GET'])
@jwt_required()
def get_matching_rules():
    """GET /api/application-plans/matching-rules"""
    svc = _get_service()
    return jsonify(svc.get_matching_rules()), 200


@application_plan_bp.route('/matching-rules', methods=['PUT'])
@jwt_required()
def save_matching_rules():
    """
    PUT /api/application-plans/matching-rules
    Body: { "rules": [ { "field": "minScore", "value": 2, "priority": 1 }, ... ] }
    Replaces all rules with the submitted list.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    data = request.get_json() or {}
    rules = data.get('rules') or []
    if not isinstance(rules, list):
        return jsonify({'error': 'rules must be an array'}), 400

    result = svc.save_matching_rules(rules)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Failed to save rules')}), 400

    return jsonify({'success': True, 'rules': svc.get_matching_rules()}), 200


# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSTIC — GET /api/application-plans/test-match
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('/test-match', methods=['GET'])
@jwt_required()
def test_match():
    """
    GET /api/application-plans/test-match
    Returns the candidate pool and their scores for the current user's plan.
    Useful for debugging why auto-match isn't firing.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    # Build a dummy plan from the current user's latest plan or a generic one
    plans = svc.get_application_plans(student_id=current_user_id)
    if plans:
        plan = plans[0]
    else:
        plan = {
            'course': 'bsit',
            'skills': ['network administration', 'software support'],
            'interests': ['it support'],
            'preferredLocation': '',
        }

    # Get candidates
    hte_profiles = svc.get_hte_profiles()
    companies = svc.get_companies()
    rules = svc.get_matching_rules()

    configured_threshold = 1
    for rule in rules:
        if rule.get('field') == 'minScore':
            try:
                configured_threshold = int(rule.get('value', 1))
            except Exception:
                pass

    student_course = (plan.get('course') or '').strip().lower()
    student_skills = [s.strip().lower() for s in (plan.get('skills') or []) if s.strip()]
    student_interests = [i.strip().lower() for i in (plan.get('interests') or []) if i.strip()]

    debug = {
        'plan_course': student_course,
        'plan_skills': student_skills,
        'plan_interests': student_interests,
        'configured_threshold': configured_threshold,
        'hte_profiles_count': len(hte_profiles),
        'companies_count': len(companies),
        'candidates': [],
    }

    for c in companies:
        source = 'companies'
        threshold = 1  # companies always use threshold=1
        status = (c.get('status') or 'active').strip().lower()
        slots = int(c.get('availableSlots', 999))
        score = 0

        hte_name = (c.get('name') or '').lower()
        hte_desc = (c.get('description') or '').lower()

        # slot bonus
        if slots > 0:
            score += 1

        # guaranteed base
        if student_course or student_skills or student_interests:
            score = max(score, 1)

        would_match = score >= threshold and status in ('active', '')
        debug['candidates'].append({
            'name': c.get('name'),
            'source': source,
            'status': status,
            'slots': slots,
            'score': score,
            'threshold': threshold,
            'would_match': would_match,
        })

    for h in hte_profiles:
        source = 'hte_profiles'
        threshold = configured_threshold
        status = (h.get('status') or 'active').strip().lower()
        slots = int(h.get('availableSlots', 0))
        score = 0
        if slots > 0:
            score += 1
        would_match = score >= threshold and status in ('active', '')
        debug['candidates'].append({
            'name': h.get('name'),
            'source': source,
            'status': status,
            'slots': slots,
            'score': score,
            'threshold': threshold,
            'would_match': would_match,
        })

    best = svc.auto_match_student_to_hte(plan)
    debug['best_match'] = best.get('name') if best else None

    return jsonify(debug), 200


# ─────────────────────────────────────────────────────────────────────────────
# PLAN TEMPLATES  — Coordinator CRUD
# /api/application-plans/templates
# ─────────────────────────────────────────────────────────────────────────────

@application_plan_bp.route('/templates', methods=['GET'])
@jwt_required()
def list_plan_templates():
    """GET /api/application-plans/templates — list all coordinator plan templates."""
    svc = _get_service()
    return jsonify(svc.get_plan_templates()), 200


@application_plan_bp.route('/templates/<template_id>', methods=['GET'])
@jwt_required()
def get_plan_template(template_id):
    """GET /api/application-plans/templates/<template_id>"""
    svc = _get_service()
    t = svc.get_plan_template(template_id)
    if not t:
        return jsonify({'error': 'Template not found'}), 404
    return jsonify(t), 200


@application_plan_bp.route('/templates', methods=['POST'])
@jwt_required()
def create_plan_template():
    """
    POST /api/application-plans/templates
    Coordinator creates a new internship plan template.
    Body: { title, hteId, hteName, competencies: [{skill, definition, tasks, outcomes}] }
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Template title is required'}), 400

    template = {
        'title':         title,
        'hteId':         (data.get('hteId') or '').strip(),
        'hteName':       (data.get('hteName') or '').strip(),
        'description':   (data.get('description') or '').strip(),
        'competencies':  data.get('competencies') or [],
        'columnHeaders': data.get('columnHeaders') or {},
        'categoryLabel': (data.get('categoryLabel') or '').strip(),
        'createdBy':     current_user_id,
        'createdByName': (user or {}).get('name', ''),
    }

    result = svc.create_plan_template(template)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Failed to create template')}), 400
    return jsonify(result['template']), 201


@application_plan_bp.route('/templates/<template_id>', methods=['PUT'])
@jwt_required()
def update_plan_template(template_id):
    """PUT /api/application-plans/templates/<template_id>"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    data = request.get_json() or {}
    allowed = {'title', 'hteId', 'hteName', 'description', 'competencies',
               'columnHeaders', 'categoryLabel'}
    update = {k: v for k, v in data.items() if k in allowed}
    if not update:
        return jsonify({'error': 'No valid fields to update'}), 400

    result = svc.update_plan_template(template_id, update)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Update failed')}), 400
    return jsonify(result['template']), 200


@application_plan_bp.route('/templates/<template_id>', methods=['DELETE'])
@jwt_required()
def delete_plan_template(template_id):
    """DELETE /api/application-plans/templates/<template_id>"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    role = (user or {}).get('role', 'student')
    if role not in ('coordinator', 'chairman', 'admin'):
        return jsonify({'error': 'Permission denied'}), 403

    result = svc.delete_plan_template(template_id)
    if not result.get('success'):
        return jsonify({'error': result.get('error', 'Delete failed')}), 400
    return jsonify({'success': True}), 200
