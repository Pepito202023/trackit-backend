"""
TrackIt System - Message Routes
Handles real-time messaging between students and coordinators
"""

from flask import Blueprint, request, jsonify, send_from_directory
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from services.firebase_service import FirebaseService
import os
import uuid

message_bp = Blueprint('messages', __name__)

_firebase_service = None

ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}

# Allowed MIME types for announcement attachments
ALLOWED_MIMES = {
    'application/pdf',
    'image/jpeg', 'image/png', 'image/gif',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
}
ALLOWED_EXTS = {'pdf', 'jpg', 'jpeg', 'png', 'gif', 'doc', 'docx', 'xls', 'xlsx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ANNOUNCEMENTS_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), '..', 'uploads', 'announcements')


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


@message_bp.route('/conversations', methods=['GET'])
@jwt_required()
def get_conversations():
    """Get all conversations for current user"""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    conversations = svc.get_conversations(current_user_id)
    return jsonify(conversations), 200


@message_bp.route('/conversations/<conversation_id>/messages', methods=['GET'])
@jwt_required()
def get_messages(conversation_id):
    """Get messages in a conversation"""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    limit = request.args.get('limit', 50, type=int)
    before = request.args.get('before')
    messages = svc.get_messages(
        conversation_id=conversation_id,
        user_id=current_user_id,
        limit=limit,
        before=before
    )
    return jsonify(messages), 200


@message_bp.route('/conversations/<conversation_id>/messages', methods=['POST'])
@jwt_required()
def send_message(conversation_id):
    """Send a message in a conversation (text and/or file attachment)."""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    # ── Multipart upload (file attachment) ───────────────────────────────────
    if request.content_type and 'multipart/form-data' in request.content_type:
        content = (request.form.get('content') or '').strip()
        message_type = request.form.get('type', 'text')
        file = request.files.get('file')

        if not content and not file:
            return jsonify({'error': 'Message content or a file is required'}), 400

        attachment = None
        if file and file.filename:
            original_name = file.filename
            ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''
            if ext not in ALLOWED_EXTS:
                return jsonify({'error': f'File type not allowed: {original_name}'}), 400
            raw = file.read()
            if len(raw) > MAX_FILE_SIZE:
                return jsonify({'error': f'File too large ({len(raw) // (1024*1024):.1f} MB). Max 10 MB.'}), 400

            ext_mime_map = {
                'pdf': 'application/pdf', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'png': 'image/png', 'gif': 'image/gif', 'doc': 'application/msword',
                'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'xls': 'application/vnd.ms-excel',
                'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            }
            mime = file.content_type or ext_mime_map.get(ext, 'application/octet-stream')

            msg_upload_dir = os.path.join(os.path.dirname(__file__), '..', 'uploads', 'messages')
            os.makedirs(msg_upload_dir, exist_ok=True)
            stored_name = f'{uuid.uuid4().hex}.{ext}'
            with open(os.path.join(msg_upload_dir, stored_name), 'wb') as out:
                out.write(raw)

            attachment = {
                'id': uuid.uuid4().hex,
                'originalFilename': original_name,
                'storedFilename': stored_name,
                'fileUrl': f'/uploads/messages/{stored_name}',
                'mimeType': mime,
                'sizeBytes': len(raw),
            }
            if not content:
                content = original_name  # fallback preview text in conversation list

        message = {
            'conversationId': conversation_id,
            'senderId': current_user_id,
            'content': content,
            'type': 'file' if attachment else message_type,
            'read': False,
            'createdAt': datetime.now(timezone.utc).isoformat(),
        }
        if attachment:
            message['attachment'] = attachment

        result = svc.send_message(message)
        if result['success']:
            return jsonify({'success': True, 'message': result['message']}), 201
        return jsonify({'error': result['error']}), 400

    # ── JSON body (plain text) ────────────────────────────────────────────────
    data = request.get_json() or {}
    content = (data.get('content') or '').strip()
    message_type = data.get('type', 'text')

    if not content:
        return jsonify({'error': 'Message content is required'}), 400

    message = {
        'conversationId': conversation_id,
        'senderId': current_user_id,
        'content': content,
        'type': message_type,
        'read': False,
        'createdAt': datetime.now(timezone.utc).isoformat()
    }

    result = svc.send_message(message)
    if result['success']:
        return jsonify({'success': True, 'message': result['message']}), 201
    return jsonify({'error': result['error']}), 400


@message_bp.route('/conversations', methods=['POST'])
@jwt_required()
def create_conversation():
    """Get or create a direct conversation with another user"""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json() or {}

    participant_id = data.get('participantId')
    if not participant_id:
        return jsonify({'error': 'Participant ID is required'}), 400
    if participant_id == current_user_id:
        return jsonify({'error': 'Cannot start a conversation with yourself'}), 400

    try:
        conversation = svc.get_or_create_conversation(current_user_id, participant_id)
        if conversation:
            return jsonify({'success': True, 'conversation': conversation}), 201
        return jsonify({'error': 'Failed to create conversation'}), 400
    except Exception as e:
        print(f"[ERROR] create_conversation: {e}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500


@message_bp.route('/conversations/<conversation_id>/read', methods=['POST'])
@jwt_required()
def mark_as_read(conversation_id):
    """Mark messages as read"""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    result = svc.mark_messages_read(conversation_id, current_user_id)
    if result['success']:
        return jsonify({'success': True}), 200
    return jsonify({'error': result['error']}), 400


@message_bp.route('/unread/count', methods=['GET'])
@jwt_required()
def get_unread_count():
    """Get count of unread messages"""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    count = svc.get_unread_message_count(current_user_id)
    return jsonify({'count': count}), 200


@message_bp.route('/broadcast/upload', methods=['POST'])
@jwt_required()
def upload_announcement_attachments():
    """Upload files for an announcement (coordinator/chairman only)"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    if not user:
        return jsonify({'error': 'Unauthorized'}), 403
    user_role = _effective_role(user.get('role', ''))
    if user_role not in {'coordinator', 'chairman', 'adviser', 'supervisor'}:
        return jsonify({'error': 'Unauthorized'}), 403

    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No files provided'}), 400
    if len(files) > 10:
        return jsonify({'error': 'Maximum 10 files per upload'}), 400

    os.makedirs(ANNOUNCEMENTS_UPLOAD_DIR, exist_ok=True)

    results = []
    for f in files:
        original_name = f.filename or 'file'
        ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''

        # Validate extension
        if ext not in ALLOWED_EXTS:
            return jsonify({'error': f'File type not allowed: {original_name}'}), 400

        # Read content to check size
        content = f.read()
        if len(content) > MAX_FILE_SIZE:
            size_mb = len(content) / (1024 * 1024)
            return jsonify({'error': f'File too large: {original_name} ({size_mb:.2f} MB). Maximum is 10 MB.'}), 400

        # Validate MIME type
        mime = f.content_type or ''
        if mime not in ALLOWED_MIMES:
            # Fallback: derive from extension
            ext_mime_map = {
                'pdf': 'application/pdf', 'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'png': 'image/png', 'gif': 'image/gif', 'doc': 'application/msword',
                'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'xls': 'application/vnd.ms-excel',
                'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            }
            mime = ext_mime_map.get(ext, '')
            if not mime:
                return jsonify({'error': f'File type not allowed: {original_name}'}), 400

        # Save to disk
        stored_name = f'{uuid.uuid4().hex}.{ext}'
        file_path = os.path.join(ANNOUNCEMENTS_UPLOAD_DIR, stored_name)
        with open(file_path, 'wb') as out:
            out.write(content)

        # Persist metadata to Firestore
        attachment_id = uuid.uuid4().hex
        file_url = f'/uploads/announcements/{stored_name}'
        metadata = {
            'id': attachment_id,
            'originalFilename': original_name,
            'storedFilename': stored_name,
            'fileUrl': file_url,
            'mimeType': mime,
            'sizeBytes': len(content),
            'uploadedBy': current_user_id,
            'uploadedAt': datetime.now(timezone.utc).isoformat(),
        }
        try:
            svc.save_announcement_attachment(metadata)
        except Exception as e:
            # Rollback: delete the stored file
            try:
                os.remove(file_path)
            except OSError:
                pass
            return jsonify({'error': 'Failed to persist attachment metadata'}), 500

        results.append({
            'id': attachment_id,
            'filename': original_name,
            'fileUrl': file_url,
            'mimeType': mime,
            'size': len(content),
        })

    return jsonify(results), 200


@message_bp.route('/attachments/<attachment_id>', methods=['GET'])
@jwt_required()
def download_attachment(attachment_id):
    """Download an announcement attachment"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    if not user:
        return jsonify({'error': 'Unauthorized'}), 403
    user_role = _effective_role(user.get('role', ''))
    if user_role not in {'student', 'coordinator', 'chairman', 'adviser', 'supervisor'}:
        return jsonify({'error': 'Unauthorized'}), 403

    attachment = svc.get_announcement_attachment(attachment_id)
    if not attachment:
        return jsonify({'error': 'Attachment not found'}), 404

    stored_name = attachment.get('storedFilename', '')
    file_path = os.path.join(ANNOUNCEMENTS_UPLOAD_DIR, stored_name)
    if not os.path.isfile(file_path):
        return jsonify({'error': 'File is no longer available'}), 404

    from flask import send_file
    response = send_file(
        file_path,
        mimetype=attachment.get('mimeType', 'application/octet-stream'),
        as_attachment=True,
        download_name=attachment.get('originalFilename', stored_name),
    )
    response.headers['X-Attachment-Name'] = attachment.get('originalFilename', stored_name)
    response.headers['X-Attachment-Mime'] = attachment.get('mimeType', '')
    response.headers['X-Attachment-Size'] = str(attachment.get('sizeBytes', 0))
    return response


@message_bp.route('/broadcast/<message_id>', methods=['DELETE'])
@jwt_required()
def delete_broadcast(message_id):
    """Delete a broadcast announcement (sender only)"""
    svc = _get_service()
    current_user_id = get_jwt_identity()

    user = svc.get_user(current_user_id)
    if not user:
        return jsonify({'error': 'Unauthorized'}), 403
    user_role = _effective_role(user.get('role', ''))
    if user_role not in {'coordinator', 'chairman', 'adviser', 'supervisor'}:
        return jsonify({'error': 'Unauthorized'}), 403

    result = svc.delete_broadcast(message_id, current_user_id)
    if result['success']:
        return jsonify({'success': True}), 200
    if result.get('error') == 'Forbidden':
        return jsonify({'error': 'You can only delete your own announcements'}), 403
    if result.get('error') == 'Announcement not found':
        return jsonify({'error': 'Announcement not found'}), 404
    return jsonify({'error': result.get('error', 'Failed to delete')}), 400


@message_bp.route('/broadcasts', methods=['GET'])
@jwt_required()
def get_broadcasts():
    """Return all broadcast announcements visible to the current user.

    Instead of forcing the client to loop through every conversation and fetch
    messages one by one (N+1), this endpoint queries the messages collection
    directly for type='broadcast' messages where the current user is a
    participant, returning them in a single call sorted newest-first.
    """
    svc = _get_service()
    current_user_id = get_jwt_identity()
    limit = request.args.get('limit', 50, type=int)

    broadcasts = svc.get_broadcast_messages(current_user_id, limit=limit)
    return jsonify(broadcasts), 200


@message_bp.route('/broadcast', methods=['POST'])
@jwt_required()
def broadcast_message():
    """Broadcast message to students and/or advisers (coordinator only)"""
    svc = _get_service()
    current_user_id = get_jwt_identity()
    data = request.get_json()

    user = svc.get_user(current_user_id)
    if not user:
        return jsonify({'error': 'Unauthorized'}), 403

    user_role = _effective_role(user.get('role', ''))
    if user_role not in {'coordinator', 'chairman', 'adviser', 'supervisor'}:
        return jsonify({'error': 'Unauthorized'}), 403

    content = data.get('content')
    subject = data.get('subject', 'Announcement')
    target = data.get('target', 'all_students')  # all_students | all_advisers | all
    attachment_ids_raw = data.get('attachmentIds') or []

    if not content:
        return jsonify({'error': 'Message content is required'}), 400

    # Validate attachmentIds
    if len(attachment_ids_raw) > 10:
        return jsonify({'error': 'Maximum 10 attachments per announcement'}), 400

    # Deduplicate
    seen = set()
    attachment_ids = []
    for aid in attachment_ids_raw:
        if aid not in seen:
            seen.add(aid)
            attachment_ids.append(aid)

    # Verify all IDs exist
    if attachment_ids:
        missing = []
        for aid in attachment_ids:
            if not svc.get_announcement_attachment(aid):
                missing.append(aid)
        if missing:
            return jsonify({'error': f'Attachment IDs not found: {missing}'}), 400

    recipients = []
    if target in ('all_students', 'all'):
        recipients += svc.get_all_students()
    if target in ('all_advisers', 'all'):
        recipients += svc.get_all_advisers()

    sent_count = 0
    seen_ids = set()
    for recipient in recipients:
        rid = recipient.get('id')
        if not rid or rid == current_user_id or rid in seen_ids:
            continue
        seen_ids.add(rid)
        conversation = svc.get_or_create_conversation(current_user_id, rid)
        if conversation:
            message = {
                'conversationId': conversation.get('id'),
                'senderId': current_user_id,
                'content': content,
                'type': 'broadcast',
                'subject': subject,
                'attachmentIds': attachment_ids,
                'read': False,
                'createdAt': datetime.now(timezone.utc).isoformat()
            }
            svc.send_message(message)
            sent_count += 1

    return jsonify({'success': True, 'sentTo': sent_count}), 200
