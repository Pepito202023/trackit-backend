"""
TrackIt System - Rules and Regulations Routes
Supervisors can create, update, and delete rules.
All authenticated users can read rules.
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
import uuid

rules_bp = Blueprint('rules', __name__)

_firebase_service = None

ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}
EDIT_ROLES = {'supervisor', 'coordinator', 'chairman'}


def set_firebase_service(service):
    global _firebase_service
    _firebase_service = service


def _get_service():
    global _firebase_service
    if _firebase_service is None:
        from services.firebase_service import FirebaseService
        _firebase_service = FirebaseService()
    return _firebase_service


def _effective_role(role: str) -> str:
    r = (role or '').strip().lower()
    return ROLE_ALIASES.get(r, r)


# ── GET /api/rules ────────────────────────────────────────────────────────────
@rules_bp.route('', methods=['GET'])
@jwt_required()
def get_rules():
    """Get all rules — accessible by all authenticated users."""
    svc = _get_service()
    try:
        if svc.db:
            docs = svc.db.collection('rules').order_by('order').get()
            rules = []
            for doc in docs:
                d = doc.to_dict()
                d['id'] = doc.id
                rules.append(d)
            return jsonify(rules), 200
        else:
            rules = sorted(
                [dict(r, id=rid) for rid, r in getattr(svc, 'rules_db', {}).items()],
                key=lambda x: x.get('order', 0)
            )
            return jsonify(rules), 200
    except Exception as e:
        # Fallback: try without ordering (no index needed)
        try:
            if svc.db:
                docs = svc.db.collection('rules').get()
                rules = []
                for doc in docs:
                    d = doc.to_dict()
                    d['id'] = doc.id
                    rules.append(d)
                rules.sort(key=lambda x: x.get('order', 0))
                return jsonify(rules), 200
        except Exception:
            pass
        return jsonify([]), 200


# ── POST /api/rules ───────────────────────────────────────────────────────────
@rules_bp.route('', methods=['POST'])
@jwt_required()
def create_rule():
    """Create a new rule (supervisor, coordinator, or chairman only)."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'User not found'}), 404
    if _effective_role(caller.get('role', '')) not in EDIT_ROLES:
        return jsonify({'error': 'Only supervisors and coordinators can manage rules'}), 403

    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400

    rule = {
        'title': title,
        'description': description,
        'createdBy': current_user_id,
        'creatorName': caller.get('name', ''),
        'createdAt': datetime.now(timezone.utc).isoformat(),
        'order': int(datetime.now(timezone.utc).timestamp()),
    }

    try:
        if svc.db:
            doc_ref = svc.db.collection('rules').add(rule)
            rule['id'] = doc_ref[1].id
        else:
            rule_id = str(uuid.uuid4())
            rule['id'] = rule_id
            if not hasattr(svc, 'rules_db'):
                svc.rules_db = {}
            svc.rules_db[rule_id] = rule
        return jsonify({'success': True, 'rule': rule}), 201
    except Exception as e:
        return jsonify({'error': f'Failed to create rule: {str(e)}'}), 500


# ── PUT /api/rules/<rule_id> ──────────────────────────────────────────────────
@rules_bp.route('/<rule_id>', methods=['PUT'])
@jwt_required()
def update_rule(rule_id):
    """Update a rule (supervisor, coordinator, or chairman only)."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'User not found'}), 404
    if _effective_role(caller.get('role', '')) not in EDIT_ROLES:
        return jsonify({'error': 'Only supervisors and coordinators can manage rules'}), 403

    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    description = (data.get('description') or '').strip()
    if not title:
        return jsonify({'error': 'Title is required'}), 400

    update = {
        'title': title,
        'description': description,
        'updatedAt': datetime.now(timezone.utc).isoformat(),
    }

    try:
        if svc.db:
            svc.db.collection('rules').document(rule_id).update(update)
        else:
            if hasattr(svc, 'rules_db') and rule_id in svc.rules_db:
                svc.rules_db[rule_id].update(update)
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to update rule: {str(e)}'}), 500


# ── DELETE /api/rules/<rule_id> ───────────────────────────────────────────────
@rules_bp.route('/<rule_id>', methods=['DELETE'])
@jwt_required()
def delete_rule(rule_id):
    """Delete a rule (supervisor, coordinator, or chairman only)."""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    caller = svc.get_user(current_user_id)
    if not caller:
        return jsonify({'error': 'User not found'}), 404
    if _effective_role(caller.get('role', '')) not in EDIT_ROLES:
        return jsonify({'error': 'Only supervisors and coordinators can manage rules'}), 403

    try:
        if svc.db:
            svc.db.collection('rules').document(rule_id).delete()
        else:
            if hasattr(svc, 'rules_db') and rule_id in svc.rules_db:
                del svc.rules_db[rule_id]
        return jsonify({'success': True}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to delete rule: {str(e)}'}), 500
