"""
TrackIt System - Task Routes
Tasks assigned by supervisors to students/interns.
"""

import os
import uuid
from werkzeug.utils import secure_filename
from flask import Blueprint, request, jsonify, send_from_directory
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from services.firebase_service import FirebaseService

task_bp = Blueprint('tasks', __name__)

# Shared service instance — injected by app.py
_firebase_service = None


def set_firebase_service(service):
    global _firebase_service
    _firebase_service = service


def _get_service():
    global _firebase_service
    if _firebase_service is None:
        _firebase_service = FirebaseService()
    return _firebase_service

VALID_STATUSES = {'pending', 'in-progress', 'completed'}
VALID_PRIORITIES = {'low', 'medium', 'high'}
ADMIN_ROLES = {'coordinator', 'adviser', 'supervisor', 'chairman', 'admin'}

# Configure upload folder
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '..', 'uploads', 'task_proofs')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Create upload folder if it doesn't exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _user_role(user_id: str) -> str:
    user = _get_service().get_user(user_id)
    if not user:
        return ''
    role = (user.get('role') or '').lower()
    if role == 'admin':
        return 'coordinator'
    return role


@task_bp.route('', methods=['GET'])
@task_bp.route('/', methods=['GET'])
@jwt_required()
def list_tasks():
    """Students: assigned tasks. Supervisors: tasks they created. Admins: all tasks."""
    current_user_id = get_jwt_identity()
    role = _user_role(current_user_id)

    if role == 'coordinator' or role == 'admin':
        tasks = _get_service().get_all_tasks()
    elif role in ADMIN_ROLES:
        # Supervisor/adviser: see only tasks they created
        tasks = _get_service().get_tasks_created_by(current_user_id)
    else:
        tasks = _get_service().get_tasks_for_user(current_user_id)

    return jsonify(tasks), 200


@task_bp.route('', methods=['POST'])
@task_bp.route('/', methods=['POST'])
@jwt_required()
def create_task():
    """Create a task (supervisor/admin). If assigneeId='all', creates one task per intern."""
    current_user_id = get_jwt_identity()
    role = _user_role(current_user_id)

    if role not in ADMIN_ROLES and role != 'coordinator':
        return jsonify({'error': 'Only supervisors can create tasks'}), 403

    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Task title is required'}), 400

    assignee_id = data.get('assigneeId') or data.get('assignee')
    if not assignee_id:
        return jsonify({'error': 'Assignee is required'}), 400

    priority = (data.get('priority') or 'medium').lower()
    if priority not in VALID_PRIORITIES:
        priority = 'medium'

    now_iso = datetime.now(timezone.utc).isoformat()
    base_task = {
        'title': title,
        'description': (data.get('description') or data.get('desc') or '').strip(),
        'priority': priority,
        'status': 'pending',
        'dueDate': data.get('dueDate') or data.get('due') or '',
        'createdBy': current_user_id,
        'createdAt': now_iso,
    }

    # ── Assign to all interns ────────────────────────────────────────────────
    if assignee_id == 'all':
        svc = _get_service()
        try:
            all_users = svc.get_all_users()
        except Exception as e:
            return jsonify({'error': f'Failed to fetch users: {str(e)}'}), 500

        students = [u for u in all_users if (u.get('role') or '').lower() == 'student']
        if not students:
            return jsonify({'error': 'No interns found to assign the task to'}), 400

        created = []
        for student in students:
            task = dict(base_task)
            task['assigneeId'] = student['id']
            task['assigneeName'] = student.get('name', '')
            result = svc.create_task(task)
            if result.get('success'):
                created.append(result['task'])

        if created:
            return jsonify({'success': True, 'tasks': created, 'count': len(created)}), 201
        return jsonify({'error': 'Failed to create tasks for all interns'}), 400

    # ── Assign to a specific intern ──────────────────────────────────────────
    task = dict(base_task)
    task['assigneeId'] = assignee_id
    task['assigneeName'] = data.get('assigneeName', '')

    result = _get_service().create_task(task)
    if result.get('success'):
        return jsonify({'success': True, 'task': result['task']}), 201
    return jsonify({'error': result.get('error', 'Failed to create task')}), 400


@task_bp.route('/<task_id>', methods=['PATCH'])
@jwt_required()
def update_task(task_id):
    """Update task status (assignee) or full task (supervisor)."""
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    print(f"[Task Routes] PATCH /{task_id} from user {current_user_id}")
    print(f"[Task Routes] Request data: {data}")

    task = _get_service().get_task(task_id)
    if not task:
        print(f"[Task Routes] Task {task_id} not found")
        return jsonify({'error': 'Task not found'}), 404

    role = _user_role(current_user_id)
    is_admin = role in ADMIN_ROLES or role == 'coordinator'

    if not is_admin and task.get('assigneeId') != current_user_id:
        print(f"[Task Routes] Unauthorized: user {current_user_id} (role: {role}) cannot update task {task_id}")
        return jsonify({'error': 'Unauthorized'}), 403

    update_data = {}
    if is_admin:
        for key in ('title', 'description', 'priority', 'dueDate', 'assigneeId', 'assigneeName'):
            if key in data:
                update_data[key] = data[key]
        if 'desc' in data:
            update_data['description'] = data['desc']

    if 'status' in data:
        status = data['status'].lower()
        if status not in VALID_STATUSES:
            return jsonify({'error': 'Invalid status'}), 400
        update_data['status'] = status
        print(f"[Task Routes] Status change: {task.get('status')} -> {status}")

    # Store proof photos when task is marked completed
    if 'proofPhotoUrls' in data and isinstance(data['proofPhotoUrls'], list):
        urls = [u for u in data['proofPhotoUrls'] if isinstance(u, str) and u.startswith('http')][:5]
        if urls:
            update_data['proofPhotoUrls'] = urls
            update_data['completedAt'] = datetime.now(timezone.utc).isoformat()
            print(f"[Task Routes] Updated task {task_id} with {len(urls)} proof photos")
        else:
            print(f"[Task Routes] No valid proof photo URLs provided for task {task_id}")

    if not update_data:
        return jsonify({'error': 'No valid fields to update'}), 400

    print(f"[Task Routes] Updating task {task_id} with: {update_data}")
    result = _get_service().update_task(task_id, update_data)
    if result['success']:
        print(f"[Task Routes] Task {task_id} updated successfully")
        return jsonify({'success': True, 'task': result['task']}), 200
    print(f"[Task Routes] Task update failed: {result.get('error')}")
    return jsonify({'error': result.get('error', 'Update failed')}), 400


@task_bp.route('/<task_id>', methods=['DELETE'])
@jwt_required()
def delete_task(task_id):
    """Delete a task (supervisor/admin)."""
    current_user_id = get_jwt_identity()
    role = _user_role(current_user_id)

    if role not in ADMIN_ROLES and role != 'coordinator':
        return jsonify({'error': 'Unauthorized'}), 403

    result = _get_service().delete_task(task_id)
    if result['success']:
        return jsonify({'success': True}), 200
    return jsonify({'error': result.get('error', 'Delete failed')}), 400


@task_bp.route('/<task_id>/upload-proof', methods=['POST'])
@jwt_required()
def upload_task_proof(task_id):
    """Upload proof photo for a task (student only)."""
    current_user_id = get_jwt_identity()

    # Verify task exists and belongs to current user
    task = _get_service().get_task(task_id)
    if not task:
        return jsonify({'error': 'Task not found'}), 404

    if task.get('assigneeId') != current_user_id:
        return jsonify({'error': 'Unauthorized'}), 403

    # Check if file is present
    if 'photo' not in request.files:
        return jsonify({'error': 'No photo file provided'}), 400

    file = request.files['photo']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: png, jpg, jpeg, gif, webp'}), 400

    # Check file size
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return jsonify({'error': f'File too large. Maximum size: {MAX_FILE_SIZE // (1024*1024)}MB'}), 400

    # Generate unique filename
    file_ext = file.filename.rsplit('.', 1)[1].lower()
    unique_filename = f"{task_id}_{current_user_id}_{uuid.uuid4().hex}.{file_ext}"
    file_path = os.path.join(UPLOAD_FOLDER, unique_filename)

    # Save file
    try:
        file.save(file_path)
        print(f"[Task Routes] Proof photo saved: {unique_filename}")
    except Exception as e:
        print(f"[Task Routes] Error saving file: {e}")
        return jsonify({'error': 'Failed to save photo'}), 500

    # Generate public URL
    # Note: In production, this should be a proper CDN or storage URL
    # For now, we'll use the backend's public URL
    from flask import url_for
    photo_url = f"/uploads/task_proofs/{unique_filename}"

    # Update task with proof photo URL and mark as completed
    update_data = {
        'status': 'completed',
        'proofPhotoUrls': [photo_url],
        'completedAt': datetime.now(timezone.utc).isoformat()
    }

    result = _get_service().update_task(task_id, update_data)
    if result['success']:
        print(f"[Task Routes] Task {task_id} marked completed with proof photo")
        return jsonify({
            'success': True,
            'photoUrl': photo_url,
            'task': result['task']
        }), 200

    return jsonify({'error': result.get('error', 'Failed to update task')}), 400
