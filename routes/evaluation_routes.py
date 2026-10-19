"""
TrackIt System - Evaluation Routes
Student and HTE (Host Training Establishment) Evaluations
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from services.firebase_service import FirebaseService
from typing import Dict, List, Optional
from datetime import datetime

evaluation_bp = Blueprint('evaluations', __name__)

_firebase_service = None

EVAL_TYPES = {'student', 'hte', 'hte_student'}  # hte_student = submitted by the intern themselves

# 8 required HTE criteria
HTE_CRITERIA = [
    'Adherence to Work Schedule and Punctuality',
    'Attendance and DTR Compliance',
    'Job Knowledge and Skills Application',
    'Quality of Output / Work Performance',
    'Initiative and Willingness to Learn',
    'Communication and Teamwork',
    'Professional Conduct and Attitude',
    'Overall Performance',
]


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
    return {'admin': 'coordinator', 'employee': 'adviser'}.get(r, r)


def _compute_overall_rating(average: float) -> str:
    """Convert numeric average to labelled overall rating."""
    if average >= 4.50:
        return 'Excellent (5)'
    elif average >= 3.50:
        return 'Very Good (4)'
    elif average >= 2.50:
        return 'Good (3)'
    elif average >= 1.50:
        return 'Fair (2)'
    else:
        return 'Poor (1)'


def _calc_average_and_rating(scores: dict):
    """Return (average, overallRating) from a scores dict.
    Returns (0.0, '') if no valid numeric scores are present."""
    numeric = []
    for v in scores.values():
        try:
            numeric.append(float(v))
        except (TypeError, ValueError):
            pass
    if not numeric:
        return 0.0, ''
    avg = round(sum(numeric) / len(numeric), 2)
    return avg, _compute_overall_rating(avg)


# ─────────────────────────────────────────────
# LIST CRITERIA  GET /api/evaluations/criteria
# ─────────────────────────────────────────────
@evaluation_bp.route('/criteria', methods=['GET'])
@jwt_required()
def list_criteria():
    """Get evaluation criteria for all types"""
    svc = _get_service()
    criteria = svc.get_evaluation_criteria()
    return jsonify(criteria), 200


# ─────────────────────────────────────────────
# CREATE CRITERION  POST /api/evaluations/criteria
# ─────────────────────────────────────────────
@evaluation_bp.route('/criteria', methods=['POST'])
@jwt_required()
def create_criterion():
    """Create a new evaluation criterion (coordinator only)"""
    svc = _get_service()
    current = svc.get_user(get_jwt_identity())
    if not current or _effective_role(current.get('role', '')) != 'coordinator':
        return jsonify({'error': 'Only coordinators can create criteria'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    name = data.get('name', '').strip()
    eval_type = data.get('type', '').strip().lower()
    weight = int(data.get('weight', 1))

    if not name:
        return jsonify({'error': 'Name is required'}), 400
    if eval_type not in EVAL_TYPES:
        return jsonify({'error': f'Type must be one of: {", ".join(EVAL_TYPES)}'}), 400
    if weight < 1:
        return jsonify({'error': 'Weight must be positive'}), 400

    result = svc.create_evaluation_criterion({
        'name': name,
        'type': eval_type,
        'weight': weight,
        'createdAt': datetime.now().isoformat()
    })

    if result.get('success'):
        return jsonify(result['criterion']), 201
    return jsonify({'error': result.get('error', 'Failed to create criterion')}), 400


# ─────────────────────────────────────────────
# LIST EVALUATIONS  GET /api/evaluations
# ─────────────────────────────────────────────
@evaluation_bp.route('', methods=['GET'])
@evaluation_bp.route('/', methods=['GET'])
@jwt_required()
def list_evaluations():
    """
    Get evaluations filtered by role:
      - coordinator / chairman  → all evaluations
      - supervisor              → only evaluations they submitted
      - adviser                 → only evaluations for their assigned students
      - student                 → only their own evaluations
    """
    svc = _get_service()
    current = svc.get_user(get_jwt_identity())
    role = _effective_role(current.get('role', '')) if current else ''

    eval_type = request.args.get('type')

    if role in ('coordinator', 'chairman'):
        # Full visibility — no filter by evaluator or student
        evaluations = svc.get_evaluations(type=eval_type)

    elif role == 'supervisor':
        # Only the evaluations this supervisor submitted
        evaluations = svc.get_evaluations(
            evaluator_id=get_jwt_identity(),
            type=eval_type,
        )

    elif role == 'adviser':
        # Get all students assigned to this adviser, then return their evaluations
        # (both supervisor-submitted HTE evals and student-submitted hte_student evals)
        my_students = svc.get_students_by_adviser(get_jwt_identity())
        student_ids = [s['id'] for s in my_students if s.get('id')]
        if not student_ids:
            return jsonify([]), 200
        evaluations = []
        seen = set()
        for sid in student_ids:
            for ev in svc.get_evaluations(student_id=sid, type=eval_type):
                if ev.get('id') not in seen:
                    seen.add(ev.get('id'))
                    evaluations.append(ev)
        # Always include hte_student evals for adviser's students (regardless of type filter)
        if eval_type != 'hte_student':
            for sid in student_ids:
                for ev in svc.get_evaluations(student_id=sid, type='hte_student'):
                    if ev.get('id') not in seen:
                        seen.add(ev.get('id'))
                        evaluations.append(ev)

    elif role == 'student':
        # Students can only see their own self-submitted hte_student evaluations.
        # They must NEVER see supervisor (hte) or adviser (student) evaluations.
        if eval_type and eval_type != 'hte_student':
            # Explicitly requested a forbidden type — return empty
            return jsonify([]), 200
        evaluations = svc.get_evaluations(
            student_id=get_jwt_identity(),
            type='hte_student',
        )

    else:
        return jsonify([]), 200

    return jsonify(evaluations), 200


# ─────────────────────────────────────────────
# CREATE EVALUATION  POST /api/evaluations
# ─────────────────────────────────────────────
@evaluation_bp.route('', methods=['POST'])
@evaluation_bp.route('/', methods=['POST'])
@jwt_required()
def create_evaluation():
    """
    Create a new evaluation.
      - adviser   → type 'student' (evaluating a student's performance)
      - supervisor → type 'hte'    (evaluating intern from company side)
      - student   → type 'hte_student' (self-evaluation of HTE experience)

    Supervisors may save a 'draft' (status='draft') without all criteria rated.
    Submitting a completed evaluation requires all HTE criteria to be rated.
    """
    svc = _get_service()
    current = svc.get_user(get_jwt_identity())
    role = _effective_role(current.get('role', '')) if current else ''

    if role not in ('adviser', 'supervisor', 'student'):
        return jsonify({'error': 'Only advisers, supervisors, and students can create evaluations'}), 403

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    eval_type = data.get('type', '').strip().lower()

    # Role-based type enforcement
    if role == 'adviser' and eval_type != 'student':
        return jsonify({'error': 'Advisers can only create student evaluations'}), 403
    if role == 'supervisor' and eval_type != 'hte':
        return jsonify({'error': 'Supervisors can only create HTE evaluations'}), 403
    if role == 'student' and eval_type != 'hte_student':
        return jsonify({'error': 'Students can only submit HTE self-evaluations (type: hte_student)'}), 403

    if eval_type not in EVAL_TYPES:
        return jsonify({'error': 'Invalid evaluation type'}), 400

    scores   = data.get('scores', {})
    remarks  = data.get('remarks', {})
    comments = data.get('comments', '').strip()
    status   = data.get('status', 'draft' if role == 'supervisor' else 'completed').strip().lower()
    if status not in ('draft', 'completed'):
        status = 'draft'

    # Header fields
    week_no         = data.get('weekNo', '')
    period          = data.get('period', '')
    position        = data.get('position', '')
    company         = data.get('company', '')
    department      = data.get('department', '')
    supervisor_name = data.get('supervisorName', '')
    office          = data.get('office', '')

    # For student self-evals studentId is themselves; for others it's provided
    if role == 'student':
        student_id     = get_jwt_identity()
        student        = current
        evaluator_id   = get_jwt_identity()
        evaluator_name = current.get('name', '')
    else:
        student_id = data.get('studentId', '').strip()
        if not student_id:
            return jsonify({'error': 'Student ID is required'}), 400
        student = svc.get_user(student_id)
        if not student:
            return jsonify({'error': 'Student not found'}), 404
        # Verify the supervisor is authorised to evaluate this student
        if role == 'supervisor':
            supervisor_students = svc.get_students_by_supervisor(get_jwt_identity())
            allowed_ids = {s['id'] for s in supervisor_students if s.get('id')}
            if student_id not in allowed_ids:
                return jsonify({'error': 'You are not authorised to evaluate this intern'}), 403
        evaluator_id   = get_jwt_identity()
        evaluator_name = current.get('name', '')

    # Compute average and overall rating from provided scores
    average, overall_rating = _calc_average_and_rating(scores)

    # For a 'completed' HTE evaluation all criteria must be rated
    if status == 'completed' and eval_type == 'hte':
        rated = set(k for k, v in scores.items() if v)
        missing = [c for c in HTE_CRITERIA if c not in rated]
        if missing:
            return jsonify({
                'error': 'All criteria must be rated before submitting.',
                'missing': missing,
            }), 400

    # For drafts: allow saving without a final rating
    if status == 'draft':
        overall_rating_final = overall_rating if average > 0 else ''
    else:
        overall_rating_final = overall_rating

    result = svc.create_evaluation({
        'studentId':      student_id,
        'studentName':    student.get('name', ''),
        'evaluatorId':    evaluator_id,
        'evaluatorName':  evaluator_name,
        'submittedByRole': role,
        'type':           eval_type,
        'scores':         scores,
        'remarks':        remarks,
        'average':        average,
        'overallRating':  overall_rating_final,
        'comments':       comments,
        'weekNo':         week_no,
        'period':         period,
        'position':       position,
        'company':        company,
        'department':     department,
        'supervisorName': supervisor_name,
        'office':         office,
        'status':         status,
        'createdAt':      datetime.now().isoformat(),
    })

    if result.get('success'):
        return jsonify(result['evaluation']), 201
    return jsonify({'error': result.get('error', 'Failed to create evaluation')}), 400


# ─────────────────────────────────────────────
# GET ONE EVALUATION  GET /api/evaluations/<evaluation_id>
# ─────────────────────────────────────────────
@evaluation_bp.route('/<evaluation_id>', methods=['GET'])
@jwt_required()
def get_evaluation(evaluation_id):
    """Get a single evaluation by ID"""
    svc = _get_service()
    evaluation = svc.get_evaluation(evaluation_id)

    if not evaluation:
        return jsonify({'error': 'Evaluation not found'}), 404

    return jsonify(evaluation), 200


# ─────────────────────────────────────────────
# UPDATE EVALUATION  PUT /api/evaluations/<evaluation_id>
# ─────────────────────────────────────────────
@evaluation_bp.route('/<evaluation_id>', methods=['PUT'])
@jwt_required()
def update_evaluation(evaluation_id):
    """Update an evaluation.
    - Supervisors may only update their own evaluations.
    - Supervisors may update a draft freely; submitting ('completed') requires all criteria.
    - Overall rating is always recalculated automatically when scores change.
    """
    svc = _get_service()
    current = svc.get_user(get_jwt_identity())
    role = _effective_role(current.get('role', '')) if current else ''
    data = request.get_json()

    if not data:
        return jsonify({'error': 'No data provided'}), 400

    # Fetch existing document to check ownership
    existing = svc.get_evaluation(evaluation_id)
    if not existing:
        return jsonify({'error': 'Evaluation not found'}), 404

    # Supervisors can only edit their own evaluations
    if role == 'supervisor':
        if existing.get('evaluatorId') != get_jwt_identity():
            return jsonify({'error': 'You are not authorised to edit this evaluation'}), 403

    # Students cannot update any evaluation
    if role == 'student':
        return jsonify({'error': 'Students cannot update evaluations'}), 403

    allowed = {'scores', 'remarks', 'comments', 'status', 'weekNo', 'period',
               'position', 'company', 'department', 'supervisorName', 'office'}
    update_data = {k: v for k, v in data.items() if k in allowed}

    if not update_data:
        return jsonify({'error': 'No valid fields to update'}), 400

    # Recalculate average and overall rating whenever scores change
    scores = update_data.get('scores', existing.get('scores', {}))
    average, overall_rating = _calc_average_and_rating(scores)
    update_data['average'] = average

    # Determine new status
    new_status = update_data.get('status', existing.get('status', 'draft'))

    # Enforce complete criteria when transitioning to 'completed'
    if new_status == 'completed' and existing.get('type') == 'hte':
        rated = set(k for k, v in scores.items() if v)
        missing = [c for c in HTE_CRITERIA if c not in rated]
        if missing:
            return jsonify({
                'error': 'All criteria must be rated before submitting.',
                'missing': missing,
            }), 400
        update_data['overallRating'] = overall_rating
    elif average > 0:
        update_data['overallRating'] = overall_rating
    else:
        # Draft with no scores yet — clear the overall rating
        update_data['overallRating'] = ''

    update_data['updatedAt'] = datetime.now().isoformat()

    result = svc.update_evaluation(evaluation_id, update_data)

    if result.get('success'):
        return jsonify(result['evaluation']), 200
    return jsonify({'error': result.get('error', 'Update failed')}), 400


# ─────────────────────────────────────────────
# DELETE EVALUATION  DELETE /api/evaluations/<evaluation_id>
# ─────────────────────────────────────────────
@evaluation_bp.route('/<evaluation_id>', methods=['DELETE'])
@jwt_required()
def delete_evaluation(evaluation_id):
    """Delete an evaluation.
    - Coordinators and chairmen can delete any evaluation.
    - Supervisors can delete only their own evaluations.
    """
    svc = _get_service()
    current = svc.get_user(get_jwt_identity())
    role = _effective_role(current.get('role', '')) if current else ''

    if role not in ('coordinator', 'chairman', 'supervisor'):
        return jsonify({'error': 'You are not authorised to delete evaluations'}), 403

    if role == 'supervisor':
        existing = svc.get_evaluation(evaluation_id)
        if not existing:
            return jsonify({'error': 'Evaluation not found'}), 404
        if existing.get('evaluatorId') != get_jwt_identity():
            return jsonify({'error': 'You are not authorised to delete this evaluation'}), 403

    result = svc.delete_evaluation(evaluation_id)

    if result.get('success'):
        return jsonify({'message': 'Evaluation deleted successfully'}), 200
    return jsonify({'error': result.get('error', 'Delete failed')}), 400