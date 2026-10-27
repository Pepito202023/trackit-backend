"""
TrackIt System - Company CRUD Routes
Partner companies for OJT (Host Training Establishments)
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from services.firebase_service import FirebaseService

company_bp = Blueprint('companies', __name__)

_firebase_service = None


def set_firebase_service(service: FirebaseService):
    global _firebase_service
    _firebase_service = service


def _get_service() -> FirebaseService:
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service


# ─────────────────────────────────────────────
# LIST  GET /api/companies[?search=<name>]
# ─────────────────────────────────────────────
@company_bp.route('', methods=['GET'])
@jwt_required()
def list_companies():
    """
    Get all partner companies.
    Optional query param ?search=<name> filters results to companies whose
    name contains the search string (case-insensitive substring match).
    """
    svc = _get_service()
    companies = list(svc.get_companies() or [])

    # Include HTE profiles uploaded by coordinators/chairpersons so the
    # mobile app sees the same establishments as the web admin.
    try:
        seen = {(c.get('name') or '').strip().lower() for c in companies if c.get('name')}
        hte_profiles = svc.get_hte_profiles() or []
        print(f"[list_companies] Found {len(hte_profiles)} HTE profiles")
        for h in hte_profiles:
            name = (h.get('name') or '').strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            # Debug: print HTE profile details
            print(f"[list_companies] Adding HTE: {name}, skills: {h.get('requiredSkills')}, adviser: {h.get('adviser')}")
            companies.append({
                'id': h.get('id'),
                'name': name,
                'address': h.get('address') or h.get('location') or '',
                'location': h.get('location') or h.get('address') or '',
                'supervisor': h.get('supervisor') or h.get('contactPerson') or '',
                'contact': h.get('contact') or h.get('contactPhone') or h.get('contactEmail') or '',
                'description': h.get('description') or '',
                'status': h.get('status') or 'active',
                'internCount': h.get('internCount') or 0,
                'requiredSkills': h.get('requiredSkills') or [],
                'adviser': h.get('adviser') or h.get('adviserName') or '',
                'source': 'hte_profiles',
            })
    except Exception as e:
        print(f"[list_companies] HTE merge skipped: {e}")

    search = (request.args.get('search') or '').strip().lower()
    if search:
        companies = [
            c for c in companies
            if search in (c.get('name') or '').strip().lower()
        ]

    return jsonify(companies), 200


# ─────────────────────────────────────────────
# LOOKUP BY NAME  GET /api/companies/by-name/<name>
# ─────────────────────────────────────────────
@company_bp.route('/by-name/<path:company_name>', methods=['GET'])
@jwt_required()
def get_company_by_name(company_name):
    """
    GET /api/companies/by-name/<company_name>
    Finds a company whose name matches company_name using:
      1. Exact case-insensitive match
      2. Substring / partial match (the stored name contains company_name)
      3. Reverse partial match (company_name contains the stored name)
    Returns the first match or 404.
    This endpoint allows the Flutter client to resolve a company when only
    the display name is known (e.g. "NEXEN" → "NEXEN Innovation Technologies Inc.").
    """
    svc = _get_service()
    needle = company_name.strip().lower()
    if not needle:
        return jsonify({'error': 'Company name is required'}), 400

    companies = svc.get_companies()

    # Pass 1: exact case-insensitive match
    for c in companies:
        if (c.get('name') or '').strip().lower() == needle:
            return jsonify(c), 200

    # Pass 2: stored name contains the search term
    for c in companies:
        haystack = (c.get('name') or '').strip().lower()
        if needle in haystack:
            return jsonify(c), 200

    # Pass 3: search term contains the stored name (e.g. needle is the full name,
    # stored is an abbreviation)
    for c in companies:
        haystack = (c.get('name') or '').strip().lower()
        if haystack and haystack in needle:
            return jsonify(c), 200

    return jsonify({'error': 'Company not found'}), 404


# ─────────────────────────────────────────────
# CREATE  POST /api/companies
# ─────────────────────────────────────────────
@company_bp.route('', methods=['POST'])
@jwt_required()
def create_company():
    """Create a new company"""
    svc = _get_service()
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    name = data.get('name', '').strip()
    address = data.get('address', '').strip()
    supervisor = data.get('supervisor', '').strip()
    contact = data.get('contact', '').strip()
    status = data.get('status', 'active').strip()
    description = data.get('description', '').strip()
    logo = data.get('logo', '').strip()
    document_data = data.get('document', '').strip()
    document_name = data.get('documentName', '').strip()
    document_type = data.get('documentType', '').strip()
    adviser = data.get('adviser', '').strip()
    adviser_id = data.get('adviserId', '').strip()
    required_skills = data.get('requiredSkills', [])

    if not name:
        return jsonify({'error': 'Company name is required'}), 400

    result = svc.create_company({
        'name': name,
        'address': address,
        'supervisor': supervisor,
        'contact': contact,
        'status': status,
        'description': description,
        'logo': logo,
        'document': document_data,
        'documentName': document_name,
        'documentType': document_type,
        'adviser': adviser,
        'adviserId': adviser_id,
        'requiredSkills': required_skills,
    })

    if result.get('success'):
        return jsonify(result['company']), 201
    return jsonify({'error': result.get('error', 'Failed to create company')}), 400


# ─────────────────────────────────────────────
# READ ONE  GET /api/companies/<company_id>
# ─────────────────────────────────────────────
@company_bp.route('/<company_id>', methods=['GET'])
@jwt_required()
def get_company(company_id):
    """Get a single company by ID"""
    svc = _get_service()
    company = svc.get_company(company_id)

    if not company:
        return jsonify({'error': 'Company not found'}), 404

    return jsonify(company), 200


# ─────────────────────────────────────────────
# UPDATE  PUT /api/companies/<company_id>
# ─────────────────────────────────────────────
@company_bp.route('/<company_id>', methods=['PUT'])
@jwt_required()
def update_company(company_id):
    """Update company fields"""
    svc = _get_service()
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    allowed = {'name', 'address', 'supervisor', 'contact', 'contactPhone', 'status', 'description', 'logo',
               'document', 'documentName', 'documentType',
               'internCapacity', 'totalSlots', 'availableSlots', 'adviser', 'adviserId', 'requiredSkills'}
    update_data = {k: v for k, v in data.items() if k in allowed}

    # Cast slot/capacity fields to int
    for int_field in ('internCapacity', 'totalSlots', 'availableSlots'):
        if int_field in update_data:
            try:
                update_data[int_field] = int(update_data[int_field])
            except (TypeError, ValueError):
                return jsonify({'error': f'{int_field} must be a number'}), 400

    if not update_data:
        return jsonify({'error': 'No valid fields to update'}), 400

    result = svc.update_company(company_id, update_data)

    if result.get('success'):
        return jsonify(result['company']), 200
    return jsonify({'error': result.get('error', 'Update failed')}), 400


# ─────────────────────────────────────────────
# DELETE  DELETE /api/companies/<company_id>
# ─────────────────────────────────────────────
@company_bp.route('/<company_id>', methods=['DELETE'])
@jwt_required()
def delete_company(company_id):
    """Delete a company by ID (also accepts ?name=<name> to delete by name as fallback)"""
    svc = _get_service()

    # Try by ID first
    result = svc.delete_company(company_id)

    # If ID-based delete failed, try name-based fallback
    if not result.get('success'):
        name_hint = (request.args.get('name') or '').strip()
        if name_hint:
            result = svc.delete_company_by_name(name_hint)

    if result.get('success'):
        return jsonify({'message': 'Company deleted successfully'}), 200
    return jsonify({'error': result.get('error', 'Delete failed')}), 400


# ─────────────────────────────────────────────────────────────────────────────
# CAPACITY  GET /api/companies/<company_id>/capacity
# ─────────────────────────────────────────────────────────────────────────────
@company_bp.route('/<company_id>/capacity', methods=['GET'])
@jwt_required()
def get_company_capacity(company_id):
    """
    Get current intern capacity stats for a company.
    Returns: { internCapacity, internCount, occupied, available, isFull }
    """
    svc = _get_service()
    company = svc.get_company(company_id)
    if not company:
        return jsonify({'error': 'Company not found'}), 404

    capacity = company.get('internCapacity') or company.get('totalSlots') or 0
    try:
        capacity = int(capacity)
    except (TypeError, ValueError):
        capacity = 0

    # Count active interns currently assigned to this company
    try:
        occupied = 0
        if svc.db:
            for doc in (svc.db.collection('users')
                        .where('role', '==', 'student').get()):
                d = doc.to_dict()
                if (d.get('companyId') == company_id or
                        (d.get('company') or '').strip().lower() ==
                        (company.get('name') or '').strip().lower()):
                    occupied += 1
        else:
            for u in (svc.users_db or {}).values():
                if u.get('role') == 'student' and (
                        u.get('companyId') == company_id or
                        (u.get('company') or '').strip().lower() ==
                        (company.get('name') or '').strip().lower()):
                    occupied += 1
    except Exception as e:
        print(f"[capacity] Warning: could not count interns: {e}")
        occupied = company.get('internCount', 0)

    available = max(0, capacity - occupied) if capacity > 0 else None
    is_full = (capacity > 0 and occupied >= capacity)

    return jsonify({
        'companyId':     company_id,
        'companyName':   company.get('name', ''),
        'internCapacity': capacity,
        'internCount':   occupied,
        'occupied':      occupied,
        'available':     available,
        'isFull':        is_full,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# SET CAPACITY  PUT /api/companies/<company_id>/capacity
# Supervisor-only: set how many interns their company can take
# ─────────────────────────────────────────────────────────────────────────────
@company_bp.route('/<company_id>/capacity', methods=['PUT'])
@jwt_required()
def set_company_capacity(company_id):
    """
    Set the intern capacity for a company.
    Only the supervisor assigned to that company (or coordinator/chairman) may call this.
    Body: { "internCapacity": <int> }
    Validation: capacity must be >= current active intern count.
    """
    from flask_jwt_extended import get_jwt_identity
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    company = svc.get_company(company_id)
    if not company:
        return jsonify({'error': 'Company not found'}), 404

    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'Caller not found'}), 403

    role = (caller.get('role') or '').lower().strip()
    ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}
    role = ROLE_ALIASES.get(role, role)

    # Supervisors: must be assigned to this company
    if role == 'supervisor':
        caller_company = (caller.get('company') or '').strip().lower()
        cmp_name = (company.get('name') or '').strip().lower()
        cmp_id   = company.get('id', '')
        if (caller_company != cmp_name and
                caller.get('companyId', '') != cmp_id):
            return jsonify({'error': 'You can only manage the capacity of your own company'}), 403
    elif role not in ('coordinator', 'chairman'):
        return jsonify({'error': 'Only supervisors, coordinators, or chairpersons can set company capacity'}), 403

    try:
        new_capacity = int(data.get('internCapacity', 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'internCapacity must be a positive integer'}), 400

    if new_capacity < 0:
        return jsonify({'error': 'internCapacity cannot be negative'}), 400

    # Count current active interns — capacity cannot be set below this
    try:
        occupied = 0
        if svc.db:
            for doc in (svc.db.collection('users')
                        .where('role', '==', 'student').get()):
                d = doc.to_dict()
                if (d.get('companyId') == company_id or
                        (d.get('company') or '').strip().lower() ==
                        (company.get('name') or '').strip().lower()):
                    occupied += 1
        else:
            for u in (svc.users_db or {}).values():
                if u.get('role') == 'student' and (
                        u.get('companyId') == company_id or
                        (u.get('company') or '').strip().lower() ==
                        (company.get('name') or '').strip().lower()):
                    occupied += 1
    except Exception:
        occupied = 0

    if new_capacity > 0 and new_capacity < occupied:
        return jsonify({
            'error': (
                f'Cannot set capacity to {new_capacity} — '
                f'the company currently has {occupied} active intern(s). '
                'Increase the capacity or remove interns first.'
            )
        }), 400

    available = max(0, new_capacity - occupied) if new_capacity > 0 else None
    new_status = 'full' if (new_capacity > 0 and occupied >= new_capacity) else 'active'

    result = svc.update_company(company_id, {
        'internCapacity': new_capacity,
        'totalSlots':     new_capacity,
        'availableSlots': available if available is not None else new_capacity,
        'status': new_status,
    })

    if result.get('success'):
        return jsonify({
            'success':       True,
            'internCapacity': new_capacity,
            'occupied':      occupied,
            'available':     available,
            'isFull':        new_status == 'full',
            'status':        new_status,
        }), 200
    return jsonify({'error': result.get('error', 'Failed to update capacity')}), 400


# ─────────────────────────────────────────────────────────────────────────────
# ASSIGN SUPERVISOR  POST /api/companies/<company_id>/assign-supervisor
# Links a Supervisor user account to a company and vice versa.
# ─────────────────────────────────────────────────────────────────────────────
@company_bp.route('/<company_id>/assign-supervisor', methods=['POST'])
@jwt_required()
def assign_supervisor(company_id):
    """
    POST /api/companies/<company_id>/assign-supervisor
    Body: { "supervisorId": "<user_id>", "supervisorName": "<name>" }
    Links the company to the supervisor's user account:
      - Updates company.supervisorId + company.supervisor (name)
      - Updates supervisor user's company + companyId + companyName fields
    Only coordinators and chairpersons may call this.
    """
    from flask_jwt_extended import get_jwt_identity
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'Caller not found'}), 403
    role = (caller.get('role') or '').lower().strip()
    role = {'admin': 'coordinator', 'employee': 'adviser'}.get(role, role)
    if role not in ('coordinator', 'chairman'):
        return jsonify({'error': 'Only coordinators or chairpersons can assign supervisors'}), 403

    company = svc.get_company(company_id)
    if not company:
        return jsonify({'error': 'Company not found'}), 404

    supervisor_id   = (data.get('supervisorId') or '').strip()
    supervisor_name = (data.get('supervisorName') or '').strip()

    if not supervisor_id:
        return jsonify({'error': 'supervisorId is required'}), 400

    supervisor = svc.get_user(supervisor_id)
    if not supervisor:
        return jsonify({'error': 'Supervisor user not found'}), 404
    sup_role = (supervisor.get('role') or '').lower().strip()
    sup_role = {'admin': 'coordinator', 'employee': 'adviser'}.get(sup_role, sup_role)
    if sup_role != 'supervisor':
        return jsonify({'error': 'Selected user is not a Supervisor'}), 400

    if not supervisor_name:
        supervisor_name = supervisor.get('name', '')

    # Update company record
    company_update = {
        'supervisorId':   supervisor_id,
        'supervisor':     supervisor_name,
    }
    cmp_result = svc.update_company(company_id, company_update)
    if not cmp_result.get('success'):
        return jsonify({'error': cmp_result.get('error', 'Failed to update company')}), 400

    # Update supervisor's user record so their dashboard shows the company
    svc.update_user(supervisor_id, {
        'companyId':   company_id,
        'companyName': company.get('name', ''),
        'company':     company.get('name', ''),
    })

    print(f"[assign-supervisor] Company '{company.get('name')}' linked to supervisor '{supervisor_name}' ({supervisor_id})")
    return jsonify({
        'success':        True,
        'companyId':      company_id,
        'companyName':    company.get('name', ''),
        'supervisorId':   supervisor_id,
        'supervisorName': supervisor_name,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# REASSIGN STUDENT  POST /api/reassign-student
# ─────────────────────────────────────────────────────────────────────────────
@company_bp.route('/reassign-student', methods=['POST'])
@jwt_required()
def reassign_student():
    """
    Reassign a student from one company to another.
    Body: { "studentId": "<student_id>", "currentCompanyId": "<current_company_id>", "newCompanyId": "<new_company_id>" }
    Only coordinators and chairpersons can reassign students.
    Updates:
      - Student's companyId, companyName, and company fields
      - Old company's internCount (decrement)
      - New company's internCount (increment)
    Preserves all other student data (attendance, OJT hours, tasks, evaluations, adviser, supervisor).
    """
    from flask_jwt_extended import get_jwt_identity
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'Caller not found'}), 403
    role = (caller.get('role') or '').lower().strip()
    role = {'admin': 'coordinator', 'employee': 'adviser'}.get(role, role)
    if role not in ('coordinator', 'chairman'):
        return jsonify({'error': 'Only coordinators or chairpersons can reassign students'}), 403

    student_id = data.get('studentId', '').strip()
    current_company_id = data.get('currentCompanyId', '').strip()
    new_company_id = data.get('newCompanyId', '').strip()

    if not student_id or not current_company_id or not new_company_id:
        return jsonify({'error': 'studentId, currentCompanyId, and newCompanyId are required'}), 400

    if current_company_id == new_company_id:
        return jsonify({'error': 'New company must be different from current company'}), 400

    student = svc.get_user(student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404

    current_company = svc.get_company(current_company_id)
    new_company = svc.get_company(new_company_id)

    if not current_company or not new_company:
        return jsonify({'error': 'One or both companies not found'}), 404

    # Update student's company assignment
    student_update = {
        'companyId': new_company_id,
        'companyName': new_company.get('name', ''),
        'company': new_company.get('name', ''),
    }

    # Update supervisor from new company
    new_supervisor = new_company.get('supervisor') or new_company.get('contactPerson') or ''
    if new_supervisor:
        student_update['supervisorName'] = new_supervisor
        # Try to find supervisor user ID
        try:
            for doc in svc.db.collection('users').where('role', '==', 'supervisor').get():
                d = doc.to_dict()
                if d.get('name', '').strip().lower() == new_supervisor.strip().lower():
                    student_update['supervisorId'] = doc.id
                    break
        except:
            pass

    # Update adviser from new company
    new_adviser = new_company.get('adviser') or ''
    if new_adviser:
        student_update['adviserName'] = new_adviser
        # Try to find adviser user ID
        try:
            for doc in svc.db.collection('users').where('role', '==', 'adviser').get():
                d = doc.to_dict()
                if d.get('name', '').strip().lower() == new_adviser.strip().lower():
                    student_update['adviserId'] = doc.id
                    break
        except:
            pass

    svc.update_user(student_id, student_update)

    # Update old company's intern count
    old_intern_count = current_company.get('internCount', 0)
    if old_intern_count > 0:
        svc.update_company(current_company_id, {'internCount': old_intern_count - 1})

    # Update new company's intern count
    new_intern_count = new_company.get('internCount', 0)
    svc.update_company(new_company_id, {'internCount': new_intern_count + 1})

    print(f"[reassign-student] Student '{student.get('name')}' ({student_id}) reassigned from '{current_company.get('name')}' to '{new_company.get('name')}'")
    return jsonify({
        'success': True,
        'studentId': student_id,
        'studentName': student.get('name'),
        'previousCompany': current_company.get('name'),
        'newCompany': new_company.get('name'),
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# REMOVE COMPANY ASSIGNMENT  POST /api/remove-company-assignment
# ─────────────────────────────────────────────────────────────────────────────
@company_bp.route('/remove-company-assignment', methods=['POST'])
@jwt_required()
def remove_company_assignment():
    """
    Remove a student's company assignment (make them unassigned).
    Body: { "studentId": "<student_id>", "companyId": "<company_id>" }
    Only coordinators and chairpersons can remove assignments.
    Updates:
      - Student's companyId, companyName, and company fields (cleared)
      - Company's internCount (decrement)
    Preserves all other student data.
    """
    from flask_jwt_extended import get_jwt_identity
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'Caller not found'}), 403
    role = (caller.get('role') or '').lower().strip()
    role = {'admin': 'coordinator', 'employee': 'adviser'}.get(role, role)
    if role not in ('coordinator', 'chairman'):
        return jsonify({'error': 'Only coordinators or chairpersons can remove assignments'}), 403

    student_id = data.get('studentId', '').strip()
    company_id = data.get('companyId', '').strip()

    if not student_id or not company_id:
        return jsonify({'error': 'studentId and companyId are required'}), 400

    student = svc.get_user(student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404

    company = svc.get_company(company_id)
    if not company:
        return jsonify({'error': 'Company not found'}), 404

    # Clear student's company assignment
    student_update = {
        'companyId': '',
        'companyName': '',
        'company': '',
    }
    svc.update_user(student_id, student_update)

    # Update company's intern count
    intern_count = company.get('internCount', 0)
    if intern_count > 0:
        svc.update_company(company_id, {'internCount': intern_count - 1})

    print(f"[remove-company-assignment] Student '{student.get('name')}' ({student_id}) removed from '{company.get('name')}'")
    return jsonify({
        'success': True,
        'studentId': student_id,
        'studentName': student.get('name'),
        'previousCompany': company.get('name'),
    }), 200
