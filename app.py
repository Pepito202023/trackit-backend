"""
TrackIt System - Flask Backend
Employee Attendance Management System
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from datetime import datetime, timedelta
import os

# Load backend/.env so TRACKIT_EMAIL_FROM etc. are available even if cwd is not backend/
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    load_dotenv(_env_path, override=True)
except ImportError:
    pass  # python-dotenv not installed — env vars must be set manually

# Import routes
from routes.auth_routes import auth_bp, set_firebase_service as set_auth_firebase
from routes.attendance_routes import attendance_bp, set_firebase_service as set_attendance_firebase
from routes.report_routes import report_bp, set_firebase_service as set_report_firebase
from routes.document_routes import document_bp, set_firebase_service as set_document_firebase
from routes.message_routes import message_bp, set_firebase_service as set_message_firebase
from routes.ojt_routes import ojt_bp, set_firebase_service as set_ojt_firebase
from routes.face_routes import face_bp, set_firebase_service as set_face_firebase
from routes.user_routes import user_bp, set_firebase_service as set_user_firebase
from routes.task_routes import task_bp, set_firebase_service as set_task_firebase
from routes.company_routes import company_bp, set_firebase_service as set_company_firebase
from routes.evaluation_routes import evaluation_bp, set_firebase_service as set_evaluation_firebase
from routes.rules_routes import rules_bp, set_firebase_service as set_rules_firebase
from routes.application_plan_routes import application_plan_bp, set_firebase_service as set_application_plan_firebase
from services.firebase_service import FirebaseService

# Initialize Flask app
app = Flask(__name__)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'trackit-secret-key-2024')
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'trackit-jwt-secret-2024')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=24)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# Enable CORS
CORS(app, resources={
    r"/api/*": {"origins": "*", "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], "allow_headers": ["Content-Type", "Authorization"]},
    r"/uploads/*": {"origins": "*"},
})

# Initialize JWT
jwt = JWTManager(app)

# Initialize Firebase
firebase_service = FirebaseService()

# Inject shared instance into blueprints that need it
set_auth_firebase(firebase_service)
set_attendance_firebase(firebase_service)
set_user_firebase(firebase_service)
set_report_firebase(firebase_service)
set_message_firebase(firebase_service)
set_ojt_firebase(firebase_service)
set_face_firebase(firebase_service)
set_task_firebase(firebase_service)
set_document_firebase(firebase_service)
set_company_firebase(firebase_service)
set_evaluation_firebase(firebase_service)
set_rules_firebase(firebase_service)
set_application_plan_firebase(firebase_service)

# Register Blueprints
app.register_blueprint(auth_bp, url_prefix='/api/auth')
app.register_blueprint(attendance_bp, url_prefix='/api/attendance')
app.register_blueprint(report_bp, url_prefix='/api/reports')
app.register_blueprint(document_bp, url_prefix='/api/documents')
app.register_blueprint(message_bp, url_prefix='/api/messages')
app.register_blueprint(ojt_bp, url_prefix='/api/ojt')
app.register_blueprint(face_bp, url_prefix='/api/face')
app.register_blueprint(user_bp, url_prefix='/api/users')
app.register_blueprint(task_bp, url_prefix='/api/tasks')
app.register_blueprint(company_bp, url_prefix='/api/companies')
app.register_blueprint(evaluation_bp, url_prefix='/api/evaluations')
app.register_blueprint(rules_bp, url_prefix='/api/rules')
app.register_blueprint(application_plan_bp, url_prefix='/api/application-plans')

# Dashboard Route
@app.route('/api/dashboard/stats', methods=['GET'])
@jwt_required()
def get_dashboard_stats():
    """Get dashboard statistics"""
    current_user_id = get_jwt_identity()
    
    # Get stats from Firebase
    stats = firebase_service.get_dashboard_stats()
    
    return jsonify(stats)

# Employees Route
@app.route('/api/employees', methods=['GET'])
@jwt_required()
def get_employees():
    """Get all employees"""
    employees = firebase_service.get_all_employees()
    return jsonify(employees)

@app.route('/api/employees', methods=['POST'])
@jwt_required()
def add_employee():
    """Add new employee"""
    data = request.get_json()
    
    result = firebase_service.add_employee(data)
    
    if result['success']:
        return jsonify(result), 201
    else:
        return jsonify(result), 400

# Reports Route
@app.route('/api/reports/<report_type>', methods=['POST'])
@jwt_required()
def generate_report(report_type):
    """Generate attendance report"""
    data = request.get_json()
    start_date = data.get('startDate')
    end_date = data.get('endDate')
    
    report = firebase_service.generate_report(report_type, start_date, end_date)
    
    return jsonify(report)

# Public config (no login) — phones on mobile data read tunnel URL from Firestore;
# this endpoint is for local tools; tunnel URL is also in settings/general.
@app.route('/api/config/public', methods=['GET'])
def public_config():
    """Return public API base URL for clients on any network."""
    public_url = os.environ.get('PUBLIC_API_BASE_URL', '').strip()
    tunnel_file = os.path.join(os.path.dirname(__file__), 'tunnel_url.txt')
    if not public_url and os.path.isfile(tunnel_file):
        with open(tunnel_file, encoding='utf-8') as f:
            public_url = f.read().strip()
    if not public_url:
        try:
            public_url = (firebase_service.get_settings() or {}).get(
                'publicApiBaseUrl', ''
            )
        except Exception:
            public_url = ''
    return jsonify({
        'publicApiBaseUrl': public_url or None,
        'hint': 'Run START_TRACKIT.bat on the PC to enable mobile data / any Wi-Fi',
    }), 200


# Public institution branding — no JWT required (used by login/register page)
@app.route('/api/institution/branding', methods=['GET'])
def institution_branding():
    """Return public-safe institution branding fields for the login/register page."""
    try:
        s = firebase_service.get_settings() or {}
    except Exception:
        s = {}
    return jsonify({
        'institutionName':         s.get('institutionName',         'USTP Oroquieta Campus'),
        'institutionDepartment':   s.get('institutionDepartment',   'College of Technology'),
        'institutionEmail':        s.get('institutionEmail',        'ojt@ustp.edu.ph'),
        'defaultRegistrationRole': s.get('defaultRegistrationRole', 'student'),
        'institutionLogoUrl':      s.get('institutionLogoUrl',      ''),
        'institutionCampus':       s.get('institutionCampus',       ''),
    }), 200


# Health Check
@app.route('/api/health', methods=['GET'])
def health_check():
    """API health check"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'service': 'TrackIt API'
    })

# Serve uploaded files
@app.route('/uploads/<path:filename>', methods=['GET'])
def serve_upload(filename):
    """Serve uploaded files"""
    upload_dir = os.path.join(os.path.dirname(__file__), 'uploads')
    return send_from_directory(upload_dir, filename)

# Serve web admin static files
WEB_ADMIN_DIR = os.path.join(os.path.dirname(__file__), '..', 'web_admin')

@app.route('/web_admin/', defaults={'filename': 'index.html'})
@app.route('/web_admin/<path:filename>')
def serve_web_admin(filename):
    """Serve the web admin panel"""
    return send_from_directory(WEB_ADMIN_DIR, filename)

@app.route('/', defaults={'filename': 'login.html'})
@app.route('/<path:filename>')
def serve_root(filename):
    """Serve web admin from root for convenience"""
    # Don't intercept /api routes
    if filename.startswith('api/'):
        from flask import abort
        abort(404)
    try:
        return send_from_directory(WEB_ADMIN_DIR, filename)
    except Exception:
        return send_from_directory(WEB_ADMIN_DIR, 'login.html')

# Error Handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def internal_error(error):
    return jsonify({'error': 'Internal server error'}), 500

# Run the app
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"[STARTING] TrackIt API Server on port {port}")
    try:
        from waitress import serve
        print("[OK] Serving with Waitress WSGI")
        serve(app, host='0.0.0.0', port=port, threads=8)
    except ImportError:
        print("[WARN] Waitress not installed; falling back to Flask development server")
        app.run(host='0.0.0.0', port=port, debug=False)
