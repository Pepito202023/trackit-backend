"""
TrackIt System - Document Routes
Handles document management (uploads, storage, retrieval)
"""

from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from services.firebase_service import FirebaseService
import os
import uuid

document_bp = Blueprint('documents', __name__)

# Allowed file extensions and maximum upload size (10 MB)
_ALLOWED_EXTENSIONS = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.txt', '.csv', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.zip',
}
_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

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

@document_bp.route('/', methods=['GET'])
@jwt_required()
def get_documents():
    """Get all documents for current user"""
    current_user_id = get_jwt_identity()
    
    # Get query parameters
    category = request.args.get('category')
    
    documents = _get_service().get_documents(
        user_id=current_user_id,
        category=category
    )
    
    return jsonify(documents), 200

@document_bp.route('/upload', methods=['POST'])
@jwt_required()
def upload_document():
    """Upload a document"""
    current_user_id = get_jwt_identity()
    
    # Handle multipart form data
    if request.content_type and 'multipart/form-data' in request.content_type:
        name = request.form.get('name')
        description = request.form.get('description')
        category = request.form.get('category', 'General')
        
        file_url = None
        file_size = '0 KB'
        
        if 'file' in request.files:
            file = request.files['file']
            if file.filename:
                # Validate extension
                file_ext = os.path.splitext(file.filename)[1].lower()
                if file_ext not in _ALLOWED_EXTENSIONS:
                    return jsonify({'error': f'File type "{file_ext}" is not allowed. Permitted: {", ".join(sorted(_ALLOWED_EXTENSIONS))}'}), 400

                # Validate size (read into memory to check, then seek back)
                file.seek(0, 2)  # seek to end
                file_length = file.tell()
                file.seek(0)     # seek back to start
                if file_length > _MAX_FILE_SIZE:
                    return jsonify({'error': f'File size exceeds the 10 MB limit ({file_length // (1024*1024)} MB uploaded)'}), 400

                # Generate unique filename
                filename = f"{uuid.uuid4()}{file_ext}"

                # Save file to local storage
                upload_dir = os.path.join(os.path.dirname(__file__), '..', 'uploads', 'documents', current_user_id)
                os.makedirs(upload_dir, exist_ok=True)
                file_path = os.path.join(upload_dir, filename)
                file.save(file_path)

                # Get file size
                file_size = f"{os.path.getsize(file_path) / 1024:.1f} KB"
                file_url = f"/uploads/documents/{current_user_id}/{filename}"
    else:
        # Handle JSON data
        data = request.get_json()
        name = data.get('name')
        description = data.get('description')
        category = data.get('category', 'General')
        file_url = data.get('fileUrl')
        file_size = data.get('size', '0 KB')
    
    if not name:
        return jsonify({'error': 'Document name is required'}), 400
    
    # Create document record
    document = {
        'userId': current_user_id,
        'name': name,
        'description': description,
        'category': category,
        'fileUrl': file_url,
        'size': file_size,
        'createdAt': datetime.now(timezone.utc).isoformat()
    }
    
    result = _get_service().create_document(document)
    
    if result['success']:
        return jsonify({
            'success': True,
            'document': result['document']
        }), 201
    else:
        return jsonify({'error': result['error']}), 400

@document_bp.route('/<document_id>', methods=['GET'])
@jwt_required()
def get_document(document_id):
    """Get specific document"""
    current_user_id = get_jwt_identity()
    
    document = _get_service().get_document(document_id)
    
    if not document:
        return jsonify({'error': 'Document not found'}), 404
    
    return jsonify(document), 200

@document_bp.route('/<document_id>', methods=['DELETE'])
@jwt_required()
def delete_document(document_id):
    """Delete a document"""
    current_user_id = get_jwt_identity()
    
    document = _get_service().get_document(document_id)
    
    if not document:
        return jsonify({'error': 'Document not found'}), 404
    
    # Check if user owns this document
    if document.get('userId') != current_user_id:
        return jsonify({'error': 'Unauthorized'}), 403
    
    # Delete the file if it exists locally
    file_url = document.get('fileUrl', '')
    if file_url and file_url.startswith('/uploads/'):
        file_path = os.path.join(os.path.dirname(__file__), '..', file_url.lstrip('/'))
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
    
    # Delete from database
    try:
        svc = _get_service()
        if svc.db:
            svc.db.collection('documents').document(document_id).delete()
        else:
            if hasattr(svc, 'documents_db') and document_id in svc.documents_db:
                del svc.documents_db[document_id]
        return jsonify({'success': True, 'message': 'Document deleted'}), 200
    except Exception as e:
        return jsonify({'error': f'Failed to delete document: {str(e)}'}), 500