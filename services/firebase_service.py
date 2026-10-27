"""
TrackIt System - Firebase Service
Handles Firebase Firestore database operations
"""

import os
import json
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

try:
    from werkzeug.security import generate_password_hash, check_password_hash
    _HAS_WERKZEUG = True
except ImportError:
    _HAS_WERKZEUG = False
    print("[WARN] werkzeug.security not available — passwords will be stored as plaintext")


def _hash_password(password: str) -> str:
    """Hash a password using werkzeug's PBKDF2 scheme (backward-compatible)."""
    if _HAS_WERKZEUG:
        return generate_password_hash(password)
    return password  # fallback: plaintext


def _is_hashed(password: str) -> bool:
    """Detect whether a stored password is a werkzeug hash (vs. legacy plaintext)."""
    if not password:
        return False
    # werkzeug hashes start with 'pbkdf2:', 'scrypt:', or 'argon2:'
    return password.startswith(('pbkdf2:', 'scrypt:', 'argon2:'))


def _verify_password(stored: str, provided: str) -> bool:
    """Verify a password against either a werkzeug hash or legacy plaintext."""
    if not stored:
        return True  # Google-only accounts have no password
    if _is_hashed(stored):
        if _HAS_WERKZEUG:
            return check_password_hash(stored, provided)
        return False  # can't verify hash without werkzeug
    return stored == provided  # legacy plaintext comparison

# Firebase Admin SDK imports
import firebase_admin
from firebase_admin import credentials, firestore, auth
from firebase_admin.exceptions import FirebaseError

# Default Storage bucket — override via FIREBASE_STORAGE_BUCKET env var
_DEFAULT_STORAGE_BUCKET = os.environ.get(
    'FIREBASE_STORAGE_BUCKET', 'login-system-292a0.appspot.com'
)

class FirebaseService:
    """Firebase service for database operations using Firestore"""
    
    def __init__(self):
        """Initialize Firebase connection"""
        self.db = None
        self._initialize_firebase()
    
    def _initialize_firebase(self):
        """Initialize Firebase Admin SDK"""
        try:
            # Check if Firebase is already initialized
            if not firebase_admin._apps:
                app_options = {'storageBucket': _DEFAULT_STORAGE_BUCKET}
                # Option 1: Load from FIREBASE_SERVICE_ACCOUNT_JSON env var (cloud hosting)
                firebase_json_str = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON', '')
                if firebase_json_str:
                    try:
                        service_account_info = json.loads(firebase_json_str)
                        cred = credentials.Certificate(service_account_info)
                        firebase_admin.initialize_app(cred, app_options)
                        print("[OK] Firebase initialized from FIREBASE_SERVICE_ACCOUNT_JSON env var")
                    except Exception as e:
                        print(f"[ERROR] Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {e}")
                        raise
                else:
                    # Option 2: Load from a local file path
                    cred_path = self._find_service_account_key()
                    if cred_path:
                        cred = credentials.Certificate(cred_path)
                        firebase_admin.initialize_app(cred, app_options)
                        print(f"[OK] Firebase initialized with service account: {cred_path}")
                    else:
                        # Option 3: Default credentials (GCP environments)
                        firebase_admin.initialize_app(options=app_options)
                        print("[OK] Firebase initialized with default credentials")
            
            # Get Firestore client
            self.db = firestore.client()
            print("[OK] Firestore client connected")
            
            # Initialize demo data if collections are empty
            self._init_demo_data_if_needed()
            
        except Exception as e:
            print(f"[ERROR] Firebase initialization error: {e}")
            print("[WARNING] Falling back to in-memory storage")
            self.db = None
            self._init_in_memory_storage()
    
    def _find_service_account_key(self) -> Optional[str]:
        """Find Firebase service account key file"""
        possible_paths = [
            os.path.join(os.path.dirname(__file__), 'login-system-292a0-firebase-adminsdk-fbsvc-e84bb03b25.json'),
            os.path.join(os.path.dirname(__file__), '..', 'serviceAccountKey.json'),
            os.path.join(os.path.dirname(__file__), 'serviceAccountKey.json'),
            'serviceAccountKey.json',
            os.environ.get('FIREBASE_CONFIG_PATH', '')
        ]
        
        for path in possible_paths:
            if path and os.path.exists(path):
                return path
        
        return None
    
    def _init_in_memory_storage(self):
        """Initialize in-memory storage as fallback, loading from JSON file if exists"""
        self.attendance_db = {}
        self.employees_db = {}
        self.reports_db = {}
        self.documents_db = {}
        self.tasks_db = {}
        self.conversations_db = {}
        self.messages_db = {}
        self.settings_db = {
            'requiredOjtHours': 600,
            'ojtStartTime': '08:00',
            'ojtEndTime': '17:00',
            'lateThreshold': 15,
            'gpsRadius': 100,
            'requireFaceVerification': True,
            'requireGpsVerification': True
        }
        self.companies_db = {}
        # Load persisted users from file
        self.users_db = self._load_users_from_file()
        if not self.users_db:
            self._init_demo_data()

    def _get_users_file_path(self):
        return os.path.join(os.path.dirname(__file__), '..', 'data', 'users.json')

    def _load_users_from_file(self):
        try:
            path = self._get_users_file_path()
            if os.path.exists(path):
                with open(path, 'r') as f:
                    data = json.load(f)
                    print(f"[OK] Loaded {len(data)} users from file")
                    return data
        except Exception as e:
            print(f"[WARNING] Could not load users file: {e}")
        return {}

    def _save_users_to_file(self):
        try:
            path = self._get_users_file_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                json.dump(self.users_db, f, indent=2)
        except Exception as e:
            print(f"[WARNING] Could not save users file: {e}")
    
    def _init_demo_data_if_needed(self):
        """Initialize demo data if Firestore collections are empty"""
        try:
            # Check if users collection exists and has data
            users_ref = self.db.collection('users').limit(1).get()
            if not list(users_ref):
                print("[INFO] Initializing demo data in Firestore...")
                self._init_firestore_demo_data()
        except Exception as e:
            print(f"[WARNING] Could not check/init demo data: {e}")
    
    def _init_firestore_demo_data(self):
        """Initialize demo data in Firestore"""
        # Demo users
        demo_users = [
            {
                'id': 'user1',
                'idNumber': 'ADMIN001',
                'email': 'admin@trackit.com',
                'password': 'admin123',
                'name': 'Admin User',
                'role': 'coordinator',
                'department': 'IT',
                'createdAt': datetime.now().isoformat()
            },
            {
                'id': 'user2',
                'idNumber': 'EMP001',
                'email': 'employee@trackit.com',
                'password': 'employee123',
                'name': 'John Doe',
                'role': 'adviser',
                'department': 'Engineering',
                'createdAt': datetime.now().isoformat()
            },
            {
                'id': 'user3',
                'idNumber': 'STU001',
                'email': 'student@trackit.com',
                'password': 'student123',
                'name': 'Student User',
                'role': 'student',
                'department': 'Computer Science',
                'createdAt': datetime.now().isoformat()
            },
            {
                'id': 'user4',
                'idNumber': 'ADMIN002',
                'email': 'alexanderpepitojr6@gmail.com',
                'password': 'admin123',
                'name': 'Alexander Pepito',
                'role': 'coordinator',
                'department': 'IT',
                'createdAt': datetime.now().isoformat()
            },
            {
                'id': 'user5',
                'idNumber': 'ADMIN003',
                'email': 'airacelbalbido@gmail.com',
                'password': 'admin123',
                'name': 'Airacel Balbido',
                'role': 'supervisor',
                'department': 'IT',
                'createdAt': datetime.now().isoformat()
            },
            {
                'id': 'user6',
                'idNumber': 'CHAIR001',
                'email': 'chairman@trackit.com',
                'password': 'chairman123',
                'name': 'Chairman User',
                'role': 'chairman',
                'department': 'Administration',
                'createdAt': datetime.now().isoformat()
            }
        ]
        
        # Demo employees
        demo_employees = [
            {
                'id': 'emp1',
                'name': 'John Doe',
                'email': 'john@trackit.com',
                'department': 'Engineering',
                'position': 'Software Engineer',
                'phone': '+1234567890',
                'status': 'active',
                'joinDate': '2024-01-15'
            },
            {
                'id': 'emp2',
                'name': 'Jane Smith',
                'email': 'jane@trackit.com',
                'department': 'Sales',
                'position': 'Sales Manager',
                'phone': '+1234567891',
                'status': 'active',
                'joinDate': '2024-02-01'
            }
        ]
        
        # Add users to Firestore
        for user in demo_users:
            self.db.collection('users').document(user['id']).set(user)
        
        # Add employees to Firestore
        for employee in demo_employees:
            self.db.collection('employees').document(employee['id']).set(employee)
        
        # Initialize settings
        settings = {
            'requiredOjtHours': 600,
            'ojtStartTime': '08:00',
            'ojtEndTime': '17:00',
            'lateThreshold': 15,
            'gpsRadius': 100,
            'requireFaceVerification': True,
            'requireGpsVerification': True
        }
        self.db.collection('settings').document('general').set(settings)
        
        print("[OK] Demo data initialized in Firestore")
    
    def _init_demo_data(self):
        """Initialize demo data in memory (fallback)"""
        self.users_db = {
            'user1': {
                'id': 'user1',
                'idNumber': 'ADMIN001',
                'email': 'admin@trackit.com',
                'password': 'admin123',
                'name': 'Admin User',
                'role': 'coordinator',
                'department': 'IT'
            },
            'user2': {
                'id': 'user2',
                'idNumber': 'EMP001',
                'email': 'employee@trackit.com',
                'password': 'employee123',
                'name': 'John Doe',
                'role': 'adviser',
                'department': 'Engineering'
            },
            'user3': {
                'id': 'user3',
                'idNumber': 'STU001',
                'email': 'student@trackit.com',
                'password': 'student123',
                'name': 'Student User',
                'role': 'student',
                'department': 'Computer Science'
            },
            'user4': {
                'id': 'user4',
                'idNumber': 'ADMIN002',
                'email': 'alexanderpepitojr6@gmail.com',
                'password': 'admin123',
                'name': 'Alexander Pepito',
                'role': 'coordinator',
                'department': 'IT'
            },
            'user5': {
                'id': 'user5',
                'idNumber': 'ADMIN003',
                'email': 'airacelbalbido@gmail.com',
                'password': 'admin123',
                'name': 'Airacel Balbido',
                'role': 'supervisor',
                'department': 'IT'
            },
            'user6': {
                'id': 'user6',
                'idNumber': 'CHAIR001',
                'email': 'chairman@trackit.com',
                'password': 'chairman123',
                'name': 'Chairman User',
                'role': 'chairman',
                'department': 'Administration'
            }
        }
        
        self.employees_db = {
            'emp1': {
                'id': 'emp1',
                'name': 'John Doe',
                'email': 'john@trackit.com',
                'department': 'Engineering',
                'position': 'Software Engineer',
                'phone': '+1234567890',
                'status': 'active',
                'joinDate': '2024-01-15'
            },
            'emp2': {
                'id': 'emp2',
                'name': 'Jane Smith',
                'email': 'jane@trackit.com',
                'department': 'Sales',
                'position': 'Sales Manager',
                'phone': '+1234567891',
                'status': 'active',
                'joinDate': '2024-02-01'
            }
        }
    
    ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}

    def _effective_role(self, role: str) -> str:
        r = (role or '').lower().strip()
        return self.ROLE_ALIASES.get(r, r)

    def _password_matches(self, user_data: Dict, password: str) -> bool:
        stored_password = user_data.get('password', '')
        if stored_password:
            return _verify_password(stored_password, password)
        return True

    def _normalize_user_role(self, user_data: Dict, doc_ref=None) -> Dict:
        stored_role = user_data.get('role', '')
        if stored_role in self.ROLE_ALIASES:
            new_role = self.ROLE_ALIASES[stored_role]
            user_data['role'] = new_role
            if doc_ref is not None:
                try:
                    doc_ref.update({'role': new_role})
                except Exception:
                    pass
        return user_data

    # Authentication Methods
    def verify_user(self, email: str, password: str, role: str = None) -> Dict:
        """Verify user credentials by email (supports multiple accounts per email)."""
        try:
            requested_role = self._effective_role(role) if role else None

            if self.db:
                users_ref = self.db.collection('users')
                candidates = []
                for user_doc in users_ref.where('email', '==', email).stream():
                    user_data = user_doc.to_dict()
                    user_data['id'] = user_doc.id
                    user_data = self._normalize_user_role(user_data, user_doc.reference)
                    if self._password_matches(user_data, password):
                        candidates.append(user_data)

                if not candidates:
                    return {'success': False, 'error': 'Invalid email or password'}

                if requested_role:
                    for user_data in candidates:
                        if self._effective_role(user_data.get('role', '')) == requested_role:
                            return {'success': True, 'user': user_data}
                    return {
                        'success': False,
                        'error': f'Access denied. No account with role "{role}" for this email.',
                    }

                return {'success': True, 'user': candidates[0]}
            else:
                if not hasattr(self, 'users_db'):
                    self._init_in_memory_storage()
                candidates = []
                for user in self.users_db.values():
                    if user.get('email') == email:
                        u = dict(user)
                        u = self._normalize_user_role(u)
                        if self._password_matches(u, password):
                            candidates.append(u)
                if not candidates:
                    return {'success': False, 'error': 'Invalid email or password'}
                if requested_role:
                    for user_data in candidates:
                        if self._effective_role(user_data.get('role', '')) == requested_role:
                            return {'success': True, 'user': user_data}
                    return {
                        'success': False,
                        'error': f'Access denied. No account with role "{role}" for this email.',
                    }
                return {'success': True, 'user': candidates[0]}
        except Exception as e:
            print(f"Error verifying user: {e}")
            return {'success': False, 'error': 'Authentication error'}
    
    def verify_user_by_id(self, id_number: str, password: str, role: str = None) -> Dict:
        """Verify user credentials by ID number"""
        ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}

        try:
            if self.db:
                users_ref = self.db.collection('users')
                query = users_ref.where('idNumber', '==', id_number).limit(1)
                results = query.get()
                
                for user_doc in results:
                    user_data = user_doc.to_dict()
                    user_data['id'] = user_doc.id
                    # Migrate legacy role
                    stored_role = user_data.get('role', '')
                    if stored_role in ROLE_ALIASES:
                        new_role = ROLE_ALIASES[stored_role]
                        user_data['role'] = new_role
                        try:
                            user_doc.reference.update({'role': new_role})
                        except Exception:
                            pass
                    # Check password (supports hashed and legacy plaintext)
                    stored_password = user_data.get('password', '')
                    if _verify_password(stored_password, password):
                        return {'success': True, 'user': user_data}
            else:
                for user in self.users_db.values():
                    if user.get('idNumber') == id_number:
                        stored_role = user.get('role', '')
                        if stored_role in ROLE_ALIASES:
                            user['role'] = ROLE_ALIASES[stored_role]
                        stored_password = user.get('password', '')
                        if _verify_password(stored_password, password):
                            return {'success': True, 'user': user}
                        if not stored_password:
                            return {'success': True, 'user': user}
            
            return {'success': False, 'error': 'Invalid ID number or password'}
        except Exception as e:
            print(f"Error verifying user: {e}")
            return {'success': False, 'error': 'Authentication error'}
    
    def create_user(self, email: str, password: str, name: str, role: str = 'employee', department: str = None) -> Dict:
        """Create new user by email (staff accounts: adviser, supervisor, chairman, coordinator etc.)"""
        try:
            # Always hash passwords before storage
            hashed_password = _hash_password(password)

            user_data = {
                'email': email,
                'password': hashed_password,
                'name': name,
                'role': role,
                'status': 'active',
                'createdAt': datetime.now().isoformat(),
            }
            if department:
                user_data['department'] = department

            if self.db:
                users_ref = self.db.collection('users')
                for doc in users_ref.where('email', '==', email).limit(10).stream():
                    if doc.to_dict().get('role') == role:
                        return {'success': False, 'error': 'An account with this email and role already exists'}
                doc_ref = self.db.collection('users').add(user_data)
                user_data['id'] = doc_ref[1].id
                safe = dict(user_data)
                safe.pop('password', None)
                return {'success': True, 'user': safe}
            else:
                if not hasattr(self, 'users_db'):
                    self._init_in_memory_storage()
                for user in self.users_db.values():
                    if user.get('email') == email and user.get('role') == role:
                        return {'success': False, 'error': 'An account with this email and role already exists'}
                user_id = f"user_{len(self.users_db) + 1}"
                new_user = dict(user_data)
                new_user['id'] = user_id
                self.users_db[user_id] = new_user
                self._save_users_to_file()
                safe = dict(new_user)
                safe.pop('password', None)
                return {'success': True, 'user': safe}
        except Exception as e:
            print(f"Error creating user: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_authorized_coordinators(self) -> List[str]:
        """Return list of authorized coordinator Gmail addresses from Firestore.

        The 'authorized_coordinators' collection is managed by the system
        administrator. Only emails listed here can self-register as coordinators
        via the web admin registration form.
        """
        if not self.db:
            return []
        try:
            docs = self.db.collection('authorized_coordinators').stream()
            return [
                doc.to_dict().get('email', '').strip().lower()
                for doc in docs
                if doc.to_dict().get('email')
            ]
        except Exception as e:
            print(f"[ERROR] get_authorized_coordinators: {e}")
            return []

    def create_user_by_id(self, id_number: str, password: str, name: str, role: str = 'employee', department: str = None, email: str = None, company: str = None) -> Dict:
        """Create new user by ID number"""
        try:
            hashed_password = _hash_password(password)

            if self.db:
                # Check if ID number already exists in Firestore
                users_ref = self.db.collection('users')
                query = users_ref.where('idNumber', '==', id_number).limit(1)
                existing = list(query.get())
                
                if existing:
                    return {
                        'success': False,
                        'error': 'ID number already registered'
                    }
                
                # Create new user in Firestore
                user_data = {
                    'idNumber': id_number,
                    'password': hashed_password,
                    'name': name,
                    'role': role,
                    'status': 'active',
                    'createdAt': datetime.now().isoformat()
                }
                
                if department:
                    user_data['department'] = department
                if email:
                    user_data['email'] = email
                if company:
                    user_data['company'] = company
                
                doc_ref = self.db.collection('users').add(user_data)
                user_data['id'] = doc_ref[1].id
                safe = dict(user_data)
                safe.pop('password', None)
                return {
                    'success': True,
                    'user': safe
                }
            else:
                # Fallback to in-memory storage
                for user in self.users_db.values():
                    if user.get('idNumber') == id_number:
                        return {
                            'success': False,
                            'error': 'ID number already registered'
                        }
                
                user_id = f"user_{len(self.users_db) + 1}"
                new_user = {
                    'id': user_id,
                    'idNumber': id_number,
                    'password': hashed_password,
                    'name': name,
                    'role': role,
                    'status': 'active',
                    'createdAt': datetime.now().isoformat()
                }
                
                if department:
                    new_user['department'] = department
                if email:
                    new_user['email'] = email
                if company:
                    new_user['company'] = company
                
                self.users_db[user_id] = new_user
                self._save_users_to_file()
                safe = dict(new_user)
                safe.pop('password', None)
                return {
                    'success': True,
                    'user': safe
                }
        except Exception as e:
            print(f"Error creating user: {e}")
            return {
                'success': False,
                'error': f'Failed to create user: {str(e)}'
            }
    
    def get_user(self, user_id: str) -> Optional[Dict]:
        """Get user by ID"""
        try:
            if self.db:
                user_doc = self.db.collection('users').document(user_id).get()
                if user_doc.exists:
                    user_data = user_doc.to_dict()
                    user_data['id'] = user_doc.id
                    # Ensure supervisor and adviser data is included
                    user_data = self._normalize_user_role(user_data)
                    # Log for debugging
                    print(f"[FirebaseService] get_user({user_id}): supervisorName={user_data.get('supervisorName')}, adviserName={user_data.get('adviserName')}")
                    return user_data
                return None
            else:
                user = self.users_db.get(user_id)
                if user:
                    user_data = self._normalize_user_role(dict(user))
                    print(f"[FirebaseService] get_user({user_id}) from local: supervisorName={user_data.get('supervisorName')}, adviserName={user_data.get('adviserName')}")
                    return user_data
                return None
        except Exception as e:
            print(f"Error getting user: {e}")
            return None

    _HEAVY_USER_FIELDS = {
        'password', 'faceData', 'facePhotoBase64', 'faceDescriptor',
        'faceEncoding', 'mlkitData', '_tempPw',
    }

    def _sanitize_user_for_list(self, user: Dict, include_photo: bool = True) -> Dict:
        """Drop secrets and oversized blobs so user-list APIs stay fast."""
        safe = dict(user)
        for key in self._HEAVY_USER_FIELDS:
            safe.pop(key, None)
        pic = safe.get('profilePicture') or safe.get('avatar') or ''
        if isinstance(pic, str) and pic.startswith('data:') and len(pic) > 80_000:
            # Keep list payloads small; dashboards fall back to initials.
            safe.pop('profilePicture', None)
            safe.pop('avatar', None)
        elif include_photo and pic:
            safe['profilePicture'] = pic
        return safe

    def get_all_users(self) -> List[Dict]:
        """Get all users"""
        try:
            if self.db:
                users = []
                for doc in self.db.collection('users').get():
                    u = doc.to_dict()
                    u['id'] = doc.id
                    u.pop('password', None)  # never expose password
                    # Normalize legacy roles (admin→coordinator, employee→adviser)
                    self._normalize_user_role(u)
                    users.append(self._sanitize_user_for_list(u))
                return users
            else:
                result = []
                for u in self.users_db.values():
                    safe = dict(u)
                    safe.pop('password', None)
                    self._normalize_user_role(safe)
                    result.append(self._sanitize_user_for_list(safe))
                return result
        except Exception as e:
            print(f"Error getting all users: {e}")
            return []

    def update_user(self, user_id: str, update_data: Dict) -> Dict:
        """Update user fields (name, email, department, role, company)"""
        try:
            # Never allow password update through this method
            update_data.pop('password', None)
            update_data['updatedAt'] = datetime.now().isoformat()

            if self.db:
                doc_ref = self.db.collection('users').document(user_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'User not found'}
                doc_ref.update(update_data)
                updated = doc_ref.get().to_dict()
                updated['id'] = user_id
                updated.pop('password', None)
                return {'success': True, 'user': updated}
            else:
                if user_id not in self.users_db:
                    return {'success': False, 'error': 'User not found'}
                self.users_db[user_id].update(update_data)
                self._save_users_to_file()
                safe = dict(self.users_db[user_id])
                safe.pop('password', None)
                return {'success': True, 'user': safe}
        except Exception as e:
            print(f"Error updating user: {e}")
            return {'success': False, 'error': str(e)}

    def delete_user(self, user_id: str) -> Dict:
        """Delete a user by ID"""
        try:
            if self.db:
                doc_ref = self.db.collection('users').document(user_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'User not found'}
                doc_ref.delete()
                return {'success': True}
            else:
                if user_id not in self.users_db:
                    return {'success': False, 'error': 'User not found'}
                del self.users_db[user_id]
                self._save_users_to_file()
                return {'success': True}
        except Exception as e:
            print(f"Error deleting user: {e}")
            return {'success': False, 'error': str(e)}

    def change_password(self, user_id: str, current_password: str, new_password: str) -> Dict:
        """Change user password — verifies current password then updates to new one."""
        try:
            if self.db:
                doc_ref = self.db.collection('users').document(user_id)
                doc = doc_ref.get()
                if not doc.exists:
                    return {'success': False, 'error': 'User not found'}
                user_data = doc.to_dict()
                stored = user_data.get('password', '')
                if stored and not _verify_password(stored, current_password):
                    return {'success': False, 'error': 'Current password is incorrect'}
                doc_ref.update({'password': _hash_password(new_password)})
                return {'success': True}
            else:
                if user_id not in self.users_db:
                    return {'success': False, 'error': 'User not found'}
                stored = self.users_db[user_id].get('password', '')
                if stored and not _verify_password(stored, current_password):
                    return {'success': False, 'error': 'Current password is incorrect'}
                self.users_db[user_id]['password'] = _hash_password(new_password)
                self._save_users_to_file()
                return {'success': True}
        except Exception as e:
            print(f"Error changing password: {e}")
            return {'success': False, 'error': str(e)}

    # ── Password reset (forgot password) ────────────────────────────────────

    def find_users_by_email(self, email: str) -> List[Dict]:
        """Return all user documents matching *email* (may be multiple roles)."""
        email = (email or '').strip().lower()
        if not email:
            return []
        try:
            if self.db:
                users = []
                for doc in self.db.collection('users').where('email', '==', email).stream():
                    u = doc.to_dict()
                    u['id'] = doc.id
                    u = self._normalize_user_role(u)
                    users.append(u)
                return users
            else:
                if not hasattr(self, 'users_db'):
                    self._init_in_memory_storage()
                return [
                    dict(u) for u in self.users_db.values()
                    if (u.get('email') or '').lower() == email
                ]
        except Exception as e:
            print(f"Error finding users by email: {e}")
            return []

    def create_password_reset_token(self, email: str) -> Optional[str]:
        """
        Create a one-time, expiring password-reset token for the given email.
        Returns the raw token (to embed in the reset link) or None on failure.
        The token is stored *hashed* (SHA-256) in the ``password_resets``
        collection so that a database leak does not expose valid tokens.
        """
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=1)

        doc_data = {
            'token_hash': token_hash,
            'email': email.lower(),
            'created_at': now.isoformat(),
            'expires_at': expires_at.isoformat(),
            'used': False,
            'used_at': None,
        }
        try:
            if self.db:
                self.db.collection('password_resets').add(doc_data)
            else:
                if not hasattr(self, '_password_resets_db'):
                    self._password_resets_db = []
                self._password_resets_db.append(doc_data)
            return raw_token
        except Exception as e:
            print(f"Error creating password reset token: {e}")
            return None

    def verify_password_reset_token(self, raw_token: str) -> Dict:
        """
        Validate a raw reset token. Returns a dict with:
          - ``valid``: True/False
          - ``email``: the email associated with the token (if valid)
          - ``reason``: human-readable explanation when invalid
        """
        token_hash = hashlib.sha256((raw_token or '').encode()).hexdigest()
        now = datetime.now(timezone.utc)
        try:
            record = None
            if self.db:
                for doc in self.db.collection('password_resets').where('token_hash', '==', token_hash).limit(1).stream():
                    record = doc.to_dict()
                    record['_doc_id'] = doc.id
                    break
            else:
                if not hasattr(self, '_password_resets_db'):
                    self._password_resets_db = []
                for entry in self._password_resets_db:
                    if entry.get('token_hash') == token_hash:
                        record = dict(entry)
                        break

            if not record:
                return {'valid': False, 'reason': 'Invalid reset link. Please request a new one.'}

            if record.get('used'):
                return {'valid': False, 'reason': 'This reset link has already been used. Please request a new one.'}

            try:
                expires_at = datetime.fromisoformat(record['expires_at'])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except Exception:
                return {'valid': False, 'reason': 'Invalid reset link.'}

            if now > expires_at:
                return {'valid': False, 'reason': 'This reset link has expired. Please request a new one.'}

            return {'valid': True, 'email': record.get('email', '')}
        except Exception as e:
            print(f"Error verifying reset token: {e}")
            return {'valid': False, 'reason': 'Could not verify reset link.'}

    def use_password_reset_token(self, raw_token: str) -> bool:
        """Mark a reset token as used (one-time enforcement)."""
        token_hash = hashlib.sha256((raw_token or '').encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        try:
            if self.db:
                for doc in self.db.collection('password_resets').where('token_hash', '==', token_hash).limit(1).stream():
                    doc.reference.update({'used': True, 'used_at': now})
                    return True
            else:
                if not hasattr(self, '_password_resets_db'):
                    self._password_resets_db = []
                for entry in self._password_resets_db:
                    if entry.get('token_hash') == token_hash:
                        entry['used'] = True
                        entry['used_at'] = now
                        return True
            return False
        except Exception as e:
            print(f"Error marking token as used: {e}")
            return False

    def set_password_for_email(self, email: str, new_password: str) -> Dict:
        """
        Set a new password for ALL user documents matching *email*.
        Used by the password-reset flow (does NOT require the current password).
        Does not change any other field (role, department, status, etc.).
        """
        email = (email or '').strip().lower()
        hashed = _hash_password(new_password)
        updated_count = 0
        try:
            if self.db:
                for doc in self.db.collection('users').where('email', '==', email).stream():
                    doc.reference.update({'password': hashed, 'updatedAt': datetime.now(timezone.utc).isoformat()})
                    updated_count += 1
            else:
                if not hasattr(self, 'users_db'):
                    self._init_in_memory_storage()
                for uid, u in self.users_db.items():
                    if (u.get('email') or '').lower() == email:
                        u['password'] = hashed
                        u['updatedAt'] = datetime.now(timezone.utc).isoformat()
                        updated_count += 1
                if updated_count:
                    self._save_users_to_file()
            if updated_count == 0:
                return {'success': False, 'error': 'No account found for this email.'}
            return {'success': True, 'updated': updated_count}
        except Exception as e:
            print(f"Error setting password for email: {e}")
            return {'success': False, 'error': str(e)}
    
    # Dashboard Methods
    def get_dashboard_stats(self) -> Dict:
        """Get dashboard statistics"""
        try:
            if self.db:
                # Count students using the same method as GET /users?role=student
                # (get_all_users normalizes roles, so this is more reliable than a raw where query)
                all_users = self.get_all_users()
                ROLE_ALIASES = {'admin': 'coordinator', 'employee': 'adviser'}
                def _eff(r): return ROLE_ALIASES.get((r or '').lower().strip(), (r or '').lower().strip())
                students = [u for u in all_users if _eff(u.get('role', '')) == 'student']
                total_employees = len(students)
                print(f"[dashboard_stats] total students: {total_employees}")
                
                # Get today's attendance
                today = datetime.now().strftime('%Y-%m-%d')
                attendance_ref = self.db.collection('attendance')
                today_attendance = list(attendance_ref.where('date', '==', today).get())
                
                present_today = len([r for r in today_attendance if r.to_dict().get('status') == 'present'])
                late_today = len([r for r in today_attendance if r.to_dict().get('status') == 'late'])
                absent_today = total_employees - present_today - late_today
                
                # Get recent attendance
                recent_attendance = []
                for att_doc in today_attendance[:5]:
                    att_data = att_doc.to_dict()
                    check_in_loc = att_data.get('checkInLocation', {})
                    address = check_in_loc.get('address', '') if check_in_loc else ''
                    recent_attendance.append({
                        'employeeName': att_data.get('employeeName', 'Unknown'),
                        'checkIn': att_data.get('checkIn', ''),
                        'checkOut': att_data.get('checkOut', ''),
                        'location': address,
                        'status': att_data.get('status', '')
                    })
                
                pending_reports = 0
                try:
                    for doc in self.db.collection('reports').where('status', '==', 'pending').get():
                        pending_reports += 1
                except Exception:
                    pending_reports = 0

                return {
                    'totalEmployees': total_employees,
                    'presentToday': present_today,
                    'absentToday': absent_today,
                    'lateArrivals': late_today,
                    'pendingReports': pending_reports,
                    'recentAttendance': recent_attendance,
                    'weeklyData': {
                        'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
                        'present': [45, 48, 42, 50, 47],
                        'absent': [5, 2, 8, 0, 3],
                        'late': [3, 5, 2, 4, 3]
                    }
                }
            else:
                # Fallback to demo data
                total_employees = len(self.employees_db)
                present_today = 5
                absent_today = total_employees - present_today
                late_arrivals = 2
                
                pending_reports = len([
                    r for r in self.reports_db.values()
                    if r.get('status') == 'pending'
                ])

                return {
                    'totalEmployees': total_employees,
                    'presentToday': present_today,
                    'absentToday': absent_today,
                    'lateArrivals': late_arrivals,
                    'pendingReports': pending_reports,
                    'recentAttendance': [
                        {
                            'employeeName': 'John Doe',
                            'checkIn': '08:30 AM',
                            'checkOut': '05:30 PM',
                            'location': 'Office',
                            'status': 'Present'
                        },
                        {
                            'employeeName': 'Jane Smith',
                            'checkIn': '09:15 AM',
                            'checkOut': '',
                            'location': 'Office',
                            'status': 'Late'
                        }
                    ],
                    'weeklyData': {
                        'labels': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
                        'present': [45, 48, 42, 50, 47],
                        'absent': [5, 2, 8, 0, 3],
                        'late': [3, 5, 2, 4, 3]
                    }
                }
        except Exception as e:
            print(f"Error getting dashboard stats: {e}")
            return {
                'totalEmployees': 0,
                'presentToday': 0,
                'absentToday': 0,
                'lateArrivals': 0,
                'recentAttendance': [],
                'weeklyData': {}
            }
    
    # Employee Methods
    def get_all_employees(self) -> List[Dict]:
        """Get all employees"""
        try:
            if self.db:
                employees_ref = self.db.collection('employees')
                employees = []
                for doc in employees_ref.get():
                    emp_data = doc.to_dict()
                    emp_data['id'] = doc.id
                    employees.append(emp_data)
                return employees
            else:
                return list(self.employees_db.values())
        except Exception as e:
            print(f"Error getting employees: {e}")
            return []
    
    def add_employee(self, data: Dict) -> Dict:
        """Add new employee"""
        try:
            if self.db:
                employee_data = {
                    'name': data.get('name'),
                    'email': data.get('email'),
                    'department': data.get('department'),
                    'position': data.get('position'),
                    'phone': data.get('phone'),
                    'status': 'active',
                    'joinDate': datetime.now().strftime('%Y-%m-%d')
                }
                
                doc_ref = self.db.collection('employees').add(employee_data)
                employee_data['id'] = doc_ref[1].id
                
                return {
                    'success': True,
                    'employee': employee_data
                }
            else:
                emp_id = f"emp{len(self.employees_db) + 1}"
                employee = {
                    'id': emp_id,
                    'name': data.get('name'),
                    'email': data.get('email'),
                    'department': data.get('department'),
                    'position': data.get('position'),
                    'phone': data.get('phone'),
                    'status': 'active',
                    'joinDate': datetime.now().strftime('%Y-%m-%d')
                }
                
                self.employees_db[emp_id] = employee
                
                return {
                    'success': True,
                    'employee': employee
                }
        except Exception as e:
            print(f"Error adding employee: {e}")
            return {
                'success': False,
                'error': 'Failed to add employee'
            }

    def update_employee(self, employee_id: str, update_data: Dict) -> Dict:
        """Update an employee record"""
        try:
            update_data['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('employees').document(employee_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Employee not found'}
                doc_ref.update(update_data)
                updated = doc_ref.get().to_dict()
                updated['id'] = employee_id
                return {'success': True, 'employee': updated}
            else:
                if employee_id not in self.employees_db:
                    return {'success': False, 'error': 'Employee not found'}
                self.employees_db[employee_id].update(update_data)
                return {'success': True, 'employee': self.employees_db[employee_id]}
        except Exception as e:
            print(f"Error updating employee: {e}")
            return {'success': False, 'error': str(e)}

    def delete_employee(self, employee_id: str) -> Dict:
        """Delete an employee by ID"""
        try:
            if self.db:
                doc_ref = self.db.collection('employees').document(employee_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Employee not found'}
                doc_ref.delete()
                return {'success': True}
            else:
                if employee_id not in self.employees_db:
                    return {'success': False, 'error': 'Employee not found'}
                del self.employees_db[employee_id]
                return {'success': True}
        except Exception as e:
            print(f"Error deleting employee: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def _strip_none(value):
        """Remove None values — Firestore rejects them."""
        if isinstance(value, dict):
            return {k: FirebaseService._strip_none(v) for k, v in value.items() if v is not None}
        if isinstance(value, list):
            return [FirebaseService._strip_none(v) for v in value]
        return value

    @staticmethod
    def hydrate_attendance_photos(record: Optional[Dict]) -> Optional[Dict]:
        """Copy face photos from timeLogs / aliases onto top-level fields for the dashboards."""
        if not isinstance(record, dict):
            return record

        def _first(*vals):
            for v in vals:
                if isinstance(v, str) and v.strip():
                    return v.strip()
            return None

        logs = record.get('timeLogs') if isinstance(record.get('timeLogs'), list) else []
        check_in = _first(
            record.get('checkInFacePhotoUrl'),
            record.get('checkInPhotoUrl'),
            record.get('facePhotoUrl'),
        )
        check_out = _first(
            record.get('checkOutFacePhotoUrl'),
            record.get('checkOutPhotoUrl'),
        )
        print(f"[PHOTO NORMALIZE] record_id={record.get('id')} initial check_in={check_in} check_out={check_out}")
        for log in logs:
            if not isinstance(log, dict):
                continue
            if not check_in:
                check_in = _first(log.get('checkInFacePhotoUrl'), log.get('facePhotoUrl'), log.get('checkInPhotoUrl'))
            candidate_out = _first(log.get('checkOutFacePhotoUrl'), log.get('checkOutPhotoUrl'))
            if candidate_out:
                check_out = candidate_out
        if check_in:
            record['checkInFacePhotoUrl'] = check_in
            print(f"[PHOTO NORMALIZE] set checkInFacePhotoUrl={check_in}")
        if check_out:
            record['checkOutFacePhotoUrl'] = check_out
            print(f"[PHOTO NORMALIZE] set checkOutFacePhotoUrl={check_out}")
        return record

    # Attendance Methods
    def get_attendance_records(self, user_id: str = None, date: str = None,
                               employee_id: str = None, start_date: str = None,
                               end_date: str = None) -> List[Dict]:
        """Get attendance records with filters"""
        try:
            if self.db:
                attendance_ref = self.db.collection('attendance')
                query = attendance_ref

                if user_id:
                    query = query.where('userId', '==', user_id)
                if employee_id:
                    query = query.where('employeeId', '==', employee_id)
                if date:
                    query = query.where('date', '==', date)

                docs = list(query.get())

                # Build a user lookup map in one batch to avoid N separate Firestore reads
                user_ids = {d.to_dict().get('userId') for d in docs if d.to_dict().get('userId')}
                user_map = {}
                for uid in user_ids:
                    u = self.get_user(uid)
                    if u:
                        user_map[uid] = u

                records = []
                for doc in docs:
                    record_data = doc.to_dict()
                    record_data['id'] = doc.id
                    self.hydrate_attendance_photos(record_data)

                    # Apply start_date / end_date filter (Firestore range query needs an index;
                    # filter in Python instead to avoid index requirement)
                    rec_date = record_data.get('date', '')
                    if start_date and rec_date < start_date:
                        continue
                    if end_date and rec_date > end_date:
                        continue

                    student_id = record_data.get('userId')
                    if student_id and student_id in user_map:
                        student = user_map[student_id]
                        record_data['employeeName'] = student.get('name', 'Unknown')

                    records.append(record_data)

                return records
            else:
                records = list(self.attendance_db.values())
                
                if user_id:
                    records = [r for r in records if r.get('userId') == user_id]
                if employee_id:
                    records = [r for r in records if r.get('employeeId') == employee_id]
                if date:
                    records = [r for r in records if r.get('date') == date]
                
                # Add student names
                for record in records:
                    self.hydrate_attendance_photos(record)
                    student_id = record.get('userId')
                    if student_id and student_id in self.users_db:
                        record['employeeName'] = self.users_db[student_id].get('name', 'Unknown')
                
                return records
        except Exception as e:
            print(f"Error getting attendance records: {e}")
            return []
    
    def get_today_attendance(self, user_id: str, date: str) -> Optional[Dict]:
        """Get today's attendance record"""
        try:
            if self.db:
                attendance_ref = self.db.collection('attendance')
                query = attendance_ref.where('userId', '==', user_id).where('date', '==', date).limit(1)
                results = query.get()
                
                for doc in results:
                    record_data = doc.to_dict()
                    record_data['id'] = doc.id
                    return self.hydrate_attendance_photos(record_data)
                return None
            else:
                for record in self.attendance_db.values():
                    if record.get('userId') == user_id and record.get('date') == date:
                        return self.hydrate_attendance_photos(record)
                return None
        except Exception as e:
            print(f"Error getting today's attendance: {e}")
            return None
    
    def get_attendance_record(self, record_id: str) -> Optional[Dict]:
        """Get specific attendance record"""
        try:
            if self.db:
                doc = self.db.collection('attendance').document(record_id).get()
                if doc.exists:
                    record_data = doc.to_dict()
                    record_data['id'] = doc.id
                    return self.hydrate_attendance_photos(record_data)
                return None
            else:
                record = self.attendance_db.get(record_id)
                return self.hydrate_attendance_photos(record) if record else None
        except Exception as e:
            print(f"Error getting attendance record: {e}")
            return None
    
    def create_attendance_record(self, record: Dict) -> Dict:
        """Create new attendance record"""
        try:
            if self.db:
                doc_ref = self.db.collection('attendance').add(self._strip_none(record))
                record['id'] = doc_ref[1].id
                
                return {
                    'success': True,
                    'record': record
                }
            else:
                record_id = f"att{len(self.attendance_db) + 1}"
                record['id'] = record_id
                
                self.attendance_db[record_id] = record
                
                return {
                    'success': True,
                    'record': record
                }
        except Exception as e:
            print(f"Error creating attendance record: {e}")
            return {
                'success': False,
                'error': 'Failed to create attendance record'
            }
    
    def update_attendance_record(self, record_id: str, update_data: Dict) -> Dict:
        """Update attendance record"""
        try:
            if self.db:
                doc_ref = self.db.collection('attendance').document(record_id)
                doc = doc_ref.get()
                
                if not doc.exists:
                    return {
                        'success': False,
                        'error': 'Record not found'
                    }
                
                doc_ref.update(self._strip_none(update_data))
                
                return {
                    'success': True,
                    'record': {**doc.to_dict(), **update_data, 'id': record_id}
                }
            else:
                if record_id not in self.attendance_db:
                    return {
                        'success': False,
                        'error': 'Record not found'
                    }
                
                record = self.attendance_db[record_id]
                record.update(update_data)
                self.attendance_db[record_id] = record
                
                return {
                    'success': True,
                    'record': record
                }
        except Exception as e:
            print(f"Error updating attendance record: {e}")
            return {
                'success': False,
                'error': 'Failed to update attendance record'
            }

    def delete_attendance_record(self, record_id: str) -> Dict:
        """Delete an attendance record by ID"""
        try:
            if self.db:
                doc_ref = self.db.collection('attendance').document(record_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Attendance record not found'}
                doc_ref.delete()
                return {'success': True}
            else:
                if record_id not in self.attendance_db:
                    return {'success': False, 'error': 'Attendance record not found'}
                del self.attendance_db[record_id]
                return {'success': True}
        except Exception as e:
            print(f"Error deleting attendance record: {e}")
            return {'success': False, 'error': str(e)}
    
    # Report Methods
    def generate_report(self, report_type: str, start_date: str, end_date: str) -> Dict:
        """Generate attendance report"""
        try:
            records = self.get_attendance_records()
            
            return {
                'type': report_type,
                'startDate': start_date,
                'endDate': end_date,
                'totalRecords': len(records),
                'data': records
            }
        except Exception as e:
            print(f"Error generating report: {e}")
            return {
                'type': report_type,
                'startDate': start_date,
                'endDate': end_date,
                'totalRecords': 0,
                'data': []
            }
    
    def get_reports(self, user_id: str = None, status: str = None,
                   student_id: str = None, start_date: str = None,
                   end_date: str = None) -> List[Dict]:
        """Get reports with filters"""
        try:
            if self.db:
                reports_ref = self.db.collection('reports')
                query = reports_ref
                
                if student_id:
                    query = query.where('studentId', '==', student_id)
                if status:
                    query = query.where('status', '==', status)
                
                reports = []
                for doc in query.get():
                    report_data = doc.to_dict()
                    report_data['id'] = doc.id
                    
                    # Get student name
                    student_id_val = report_data.get('studentId')
                    if student_id_val:
                        student = self.get_user(student_id_val)
                        if student:
                            report_data['studentName'] = student.get('name', 'Unknown')
                    
                    # Re-resolve approver name from stored UID if name is missing
                    # (backfills existing approved reports that have no approvedByName)
                    approved_by_id = report_data.get('approvedBy')
                    if approved_by_id and not (report_data.get('approvedByName') or '').strip():
                        approver = self.get_user(approved_by_id)
                        if approver and approver.get('name', '').strip():
                            report_data['approvedByName'] = approver['name']
                    
                    reports.append(report_data)
                
                return reports
            else:
                reports = list(self.reports_db.values())
                
                if student_id:
                    reports = [r for r in reports if r.get('studentId') == student_id]
                if status:
                    reports = [r for r in reports if r.get('status') == status]
                
                # Add student names for in-memory storage
                for report in reports:
                    student_id_val = report.get('studentId')
                    if student_id_val:
                        student = self.users_db.get(student_id_val)
                        if student:
                            report['studentName'] = student.get('name', 'Unknown')
                
                return reports
        except Exception as e:
            print(f"Error getting reports: {e}")
            return []
    
    def create_report(self, report: Dict) -> Dict:
        """Create new report"""
        try:
            if self.db:
                doc_ref = self.db.collection('reports').add(report)
                report['id'] = doc_ref[1].id
                
                return {
                    'success': True,
                    'report': report
                }
            else:
                report_id = f"rep{len(self.reports_db) + 1}"
                report['id'] = report_id
                
                self.reports_db[report_id] = report
                
                return {
                    'success': True,
                    'report': report
                }
        except Exception as e:
            print(f"Error creating report: {e}")
            return {
                'success': False,
                'error': 'Failed to create report'
            }
    
    def get_report(self, report_id: str) -> Optional[Dict]:
        """Get specific report"""
        try:
            if self.db:
                doc = self.db.collection('reports').document(report_id).get()
                if doc.exists:
                    report_data = doc.to_dict()
                    report_data['id'] = doc.id
                    # Re-resolve approver name from stored UID if name is missing
                    approved_by_id = report_data.get('approvedBy')
                    if approved_by_id and not (report_data.get('approvedByName') or '').strip():
                        approver = self.get_user(approved_by_id)
                        if approver and approver.get('name', '').strip():
                            report_data['approvedByName'] = approver['name']
                    return report_data
                return None
            else:
                return self.reports_db.get(report_id)
        except Exception as e:
            print(f"Error getting report: {e}")
            return None
    
    def update_report(self, report_id: str, update_data: Dict) -> Dict:
        """Update report"""
        try:
            if self.db:
                doc_ref = self.db.collection('reports').document(report_id)
                doc = doc_ref.get()
                
                if not doc.exists:
                    return {
                        'success': False,
                        'error': 'Report not found'
                    }
                
                doc_ref.update(update_data)
                
                return {
                    'success': True,
                    'report': {**doc.to_dict(), **update_data, 'id': report_id}
                }
            else:
                if report_id not in self.reports_db:
                    return {
                        'success': False,
                        'error': 'Report not found'
                    }
                
                report = self.reports_db[report_id]
                report.update(update_data)
                self.reports_db[report_id] = report
                
                return {
                    'success': True,
                    'report': report
                }
        except Exception as e:
            print(f"Error updating report: {e}")
            return {
                'success': False,
                'error': 'Failed to update report'
            }

    def delete_report(self, report_id: str) -> Dict:
        """Delete a report by ID"""
        try:
            if self.db:
                doc_ref = self.db.collection('reports').document(report_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Report not found'}
                doc_ref.delete()
                return {'success': True}
            else:
                if report_id not in self.reports_db:
                    return {'success': False, 'error': 'Report not found'}
                del self.reports_db[report_id]
                return {'success': True}
        except Exception as e:
            print(f"Error deleting report: {e}")
            return {'success': False, 'error': str(e)}
    
    # Document Methods
    def get_documents(self, user_id: str = None, category: str = None) -> List[Dict]:
        """Get documents with filters"""
        try:
            if self.db:
                docs_ref = self.db.collection('documents')
                query = docs_ref
                
                if user_id:
                    query = query.where('userId', '==', user_id)
                if category:
                    query = query.where('category', '==', category)
                
                docs = []
                for doc in query.get():
                    doc_data = doc.to_dict()
                    doc_data['id'] = doc.id
                    docs.append(doc_data)
                
                return docs
            else:
                docs = list(self.documents_db.values())
                
                if user_id:
                    docs = [d for d in docs if d.get('userId') == user_id]
                if category:
                    docs = [d for d in docs if d.get('category') == category]
                
                return docs
        except Exception as e:
            print(f"Error getting documents: {e}")
            return []
    
    def get_document(self, document_id: str) -> Optional[Dict]:
        """Get specific document"""
        try:
            if self.db:
                doc = self.db.collection('documents').document(document_id).get()
                if doc.exists:
                    doc_data = doc.to_dict()
                    doc_data['id'] = doc.id
                    return doc_data
                return None
            else:
                return self.documents_db.get(document_id)
        except Exception as e:
            print(f"Error getting document: {e}")
            return None
    
    def create_document(self, document: Dict) -> Dict:
        """Create new document"""
        try:
            if self.db:
                doc_ref = self.db.collection('documents').add(document)
                document['id'] = doc_ref[1].id
                
                return {
                    'success': True,
                    'document': document
                }
            else:
                doc_id = f"doc{len(self.documents_db) + 1}"
                document['id'] = doc_id
                
                self.documents_db[doc_id] = document
                
                return {
                    'success': True,
                    'document': document
                }
        except Exception as e:
            print(f"Error creating document: {e}")
            return {
                'success': False,
                'error': 'Failed to create document'
            }

    def update_document(self, document_id: str, update_data: Dict) -> Dict:
        """Update a document"""
        try:
            update_data['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('documents').document(document_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Document not found'}
                doc_ref.update(update_data)
                updated = doc_ref.get().to_dict()
                updated['id'] = document_id
                return {'success': True, 'document': updated}
            else:
                if document_id not in self.documents_db:
                    return {'success': False, 'error': 'Document not found'}
                self.documents_db[document_id].update(update_data)
                return {'success': True, 'document': self.documents_db[document_id]}
        except Exception as e:
            print(f"Error updating document: {e}")
            return {'success': False, 'error': str(e)}

    def delete_document(self, document_id: str) -> Dict:
        """Delete a document by ID"""
        try:
            if self.db:
                doc_ref = self.db.collection('documents').document(document_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Document not found'}
                doc_ref.delete()
                return {'success': True}
            else:
                if document_id not in self.documents_db:
                    return {'success': False, 'error': 'Document not found'}
                del self.documents_db[document_id]
                return {'success': True}
        except Exception as e:
            print(f"Error deleting document: {e}")
            return {'success': False, 'error': str(e)}
    
    # Message Methods
    def get_conversations(self, user_id: str) -> List[Dict]:
        """Get all conversations for a user"""
        try:
            if self.db:
                conversations_ref = self.db.collection('conversations')
                query = conversations_ref.where('participants', 'array_contains', user_id)
                
                conversations = []
                for doc in query.get():
                    conv_data = doc.to_dict()
                    conv_data['id'] = doc.id
                    # Resolve participant names
                    participants = conv_data.get('participants', [])
                    participant_names = {}
                    for pid in participants:
                        user = self.get_user(pid)
                        if user:
                            participant_names[pid] = user.get('name', pid)
                        else:
                            participant_names[pid] = pid
                    conv_data['participantNames'] = participant_names
                    # Set a friendly display name for the other participant
                    other_ids = [p for p in participants if p != user_id]
                    if other_ids:
                        conv_data['otherParticipantName'] = participant_names.get(other_ids[0], other_ids[0])
                        conv_data['otherParticipantId'] = other_ids[0]
                        # Include the other participant's role so the client
                        # can filter conversations by role (coordinator, adviser, etc.)
                        other_user = self.get_user(other_ids[0])
                        if other_user:
                            raw_role = other_user.get('role', '')
                            conv_data['otherParticipantRole'] = self.ROLE_ALIASES.get(
                                raw_role.lower().strip(), raw_role.lower().strip()
                            )
                            # Include profile picture so chat UI can show photos
                            pic = other_user.get('profilePicture') or other_user.get('avatar')
                            if pic:
                                conv_data['otherParticipantProfilePicture'] = pic

                    # Check for unread messages (sent by others, not yet read)
                    try:
                        msgs = self.db.collection('messages') \
                            .where('conversationId', '==', doc.id) \
                            .where('read', '==', False) \
                            .limit(20).get()
                        unread_msgs = [
                            m for m in msgs
                            if m.to_dict().get('senderId') != user_id
                        ]
                        conv_data['hasUnread'] = len(unread_msgs) > 0
                        conv_data['unreadCount'] = len(unread_msgs)
                    except Exception:
                        conv_data['hasUnread'] = False
                        conv_data['unreadCount'] = 0

                    conversations.append(conv_data)
                
                return conversations
            else:
                conversations = []
                for conv in self.conversations_db.values():
                    if user_id in conv.get('participants', []):
                        conv = dict(conv)
                        participants = conv.get('participants', [])
                        participant_names = {}
                        for pid in participants:
                            user = self.users_db.get(pid)
                            if user:
                                participant_names[pid] = user.get('name', pid)
                            else:
                                participant_names[pid] = pid
                        conv['participantNames'] = participant_names
                        other_ids = [p for p in participants if p != user_id]
                        if other_ids:
                            conv['otherParticipantName'] = participant_names.get(other_ids[0], other_ids[0])
                            conv['otherParticipantId'] = other_ids[0]
                            other_user = self.users_db.get(other_ids[0])
                            if other_user:
                                raw_role = other_user.get('role', '')
                                conv['otherParticipantRole'] = self.ROLE_ALIASES.get(
                                    raw_role.lower().strip(), raw_role.lower().strip()
                                )
                                pic = other_user.get('profilePicture') or other_user.get('avatar')
                                if pic:
                                    conv['otherParticipantProfilePicture'] = pic
                        # Unread check for in-memory fallback
                        unread_count = sum(
                            1 for m in self.messages_db.values()
                            if m.get('conversationId') == conv.get('id')
                            and not m.get('read', False)
                            and m.get('senderId') != user_id
                        )
                        conv['hasUnread'] = unread_count > 0
                        conv['unreadCount'] = unread_count
                        conversations.append(conv)
                return conversations
        except Exception as e:
            print(f"Error getting conversations: {e}")
            return []
    
    def get_messages(self, conversation_id: str, user_id: str, limit: int = 50, before: str = None) -> List[Dict]:
        """Get messages in a conversation"""
        try:
            if self.db:
                messages_ref = self.db.collection('messages')
                # Use only the equality filter to avoid requiring a composite index.
                # Sorting is done in Python after fetching so no composite index is needed.
                query = messages_ref.where('conversationId', '==', conversation_id).limit(limit * 2)

                messages = []
                for doc in query.get():
                    msg_data = doc.to_dict()
                    msg_data['id'] = doc.id
                    messages.append(msg_data)

                # Sort descending by createdAt (newest first) then take the limit
                messages.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
                return messages[:limit]
            else:
                messages = [m for m in self.messages_db.values() if m.get('conversationId') == conversation_id]
                messages.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
                return messages[:limit]
        except Exception as e:
            print(f"Error getting messages: {e}")
            return []
    
    def get_broadcast_messages(self, user_id: str, limit: int = 50) -> List[Dict]:
        """Return all broadcast messages visible to a user (single query, no N+1).

        Fetches messages of type='broadcast' from every conversation the user
        participates in, sorted newest-first.
        """
        try:
            if self.db:
                # Step 1: get all conversation IDs for this user
                conv_query = self.db.collection('conversations').where(
                    'participants', 'array_contains', user_id
                )
                conv_ids = [doc.id for doc in conv_query.stream()]
                if not conv_ids:
                    return []

                # Step 2: query messages where type='broadcast' in those conversations
                # Firestore does not support IN + equality on different fields in one
                # compound query without a composite index, so we batch by conv_ids
                # using chunks of 10 (Firestore 'in' limit).
                messages: List[Dict] = []
                seen: set = set()
                sender_name_cache: Dict[str, str] = {}
                chunk_size = 10
                for i in range(0, len(conv_ids), chunk_size):
                    chunk = conv_ids[i:i + chunk_size]
                    q = (
                        self.db.collection('messages')
                        .where('conversationId', 'in', chunk)
                        .where('type', '==', 'broadcast')
                    )
                    for doc in q.stream():
                        if doc.id in seen:
                            continue
                        seen.add(doc.id)
                        data = doc.to_dict()
                        data['id'] = doc.id
                        # Enrich with sender name (cached to avoid repeated reads)
                        sender_id = data.get('senderId', '')
                        if sender_id and sender_id not in sender_name_cache:
                            try:
                                sender_doc = self.db.collection('users').document(sender_id).get()
                                sender_name_cache[sender_id] = (
                                    sender_doc.to_dict() or {}
                                ).get('name', 'Coordinator') if sender_doc.exists else 'Coordinator'
                            except Exception:
                                sender_name_cache[sender_id] = 'Coordinator'
                        data['senderName'] = sender_name_cache.get(sender_id, 'Coordinator')
                        messages.append(data)

                messages.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
                return messages[:limit]
            else:
                # In-memory fallback: filter messages_db by type and participation
                user_conv_ids = {
                    cid for cid, c in self.conversations_db.items()
                    if user_id in (c.get('participants') or [])
                }
                msgs = []
                for m in self.messages_db.values():
                    if m.get('type') == 'broadcast' and m.get('conversationId') in user_conv_ids:
                        enriched = dict(m)
                        sender_id = m.get('senderId', '')
                        sender = self.users_db.get(sender_id) if sender_id else None
                        enriched['senderName'] = (sender or {}).get('name', 'Coordinator')
                        msgs.append(enriched)
                msgs.sort(key=lambda x: x.get('createdAt', ''), reverse=True)
                return msgs[:limit]
        except Exception as e:
            print(f"Error getting broadcast messages: {e}")
            return []

    def send_message(self, message: Dict) -> Dict:
        """Send a message"""
        try:
            if self.db:
                doc_ref = self.db.collection('messages').add(message)
                message['id'] = doc_ref[1].id
                
                # Update conversation's last message
                conv_ref = self.db.collection('conversations').document(message['conversationId'])
                conv_ref.update({
                    'lastMessage': message.get('content', ''),
                    'lastMessageAt': message.get('createdAt'),
                    'updatedAt': message.get('createdAt')
                })
                
                return {
                    'success': True,
                    'message': message
                }
            else:
                msg_id = f"msg{len(self.messages_db) + 1}"
                message['id'] = msg_id
                
                self.messages_db[msg_id] = message
                
                # Update conversation's last message
                if message['conversationId'] in self.conversations_db:
                    self.conversations_db[message['conversationId']]['lastMessage'] = message.get('content', '')
                    self.conversations_db[message['conversationId']]['lastMessageAt'] = message.get('createdAt')
                
                return {
                    'success': True,
                    'message': message
                }
        except Exception as e:
            print(f"Error sending message: {e}")
            return {
                'success': False,
                'error': 'Failed to send message'
            }
    
    def create_conversation(self, conversation: Dict) -> Dict:
        """Create a new conversation"""
        try:
            if self.db:
                doc_ref = self.db.collection('conversations').add(conversation)
                conversation['id'] = doc_ref[1].id
                
                return {
                    'success': True,
                    'conversation': conversation
                }
            else:
                conv_id = f"conv{len(self.conversations_db) + 1}"
                conversation['id'] = conv_id
                
                self.conversations_db[conv_id] = conversation
                
                return {
                    'success': True,
                    'conversation': conversation
                }
        except Exception as e:
            print(f"Error creating conversation: {e}")
            return {
                'success': False,
                'error': 'Failed to create conversation'
            }
    
    def mark_messages_read(self, conversation_id: str, user_id: str) -> Dict:
        """Mark messages as read"""
        try:
            if self.db:
                messages_ref = self.db.collection('messages')
                query = messages_ref.where('conversationId', '==', conversation_id).where('senderId', '!=', user_id).where('read', '==', False)
                
                for doc in query.get():
                    doc.reference.update({'read': True})
                
                return {'success': True}
            else:
                for msg in self.messages_db.values():
                    if msg.get('conversationId') == conversation_id and msg.get('senderId') != user_id:
                        msg['read'] = True
                
                return {'success': True}
        except Exception as e:
            print(f"Error marking messages as read: {e}")
            return {'success': False, 'error': 'Failed to mark messages as read'}

    def delete_broadcast(self, message_id: str, sender_id: str) -> Dict:
        """Delete a broadcast announcement by its message ID.

        Only the original sender (coordinator/chairman) may delete their own
        broadcast. All copies of the message that share the same broadcastGroupId
        (or just the single document if no group ID was stored) are removed so
        that recipients no longer see the announcement.
        """
        try:
            if self.db:
                # Fetch the target document first to verify ownership
                doc_ref = self.db.collection('messages').document(message_id)
                doc = doc_ref.get()
                if not doc.exists:
                    return {'success': False, 'error': 'Announcement not found'}
                data = doc.to_dict()
                if data.get('senderId') != sender_id:
                    return {'success': False, 'error': 'Forbidden'}
                if data.get('type') != 'broadcast':
                    return {'success': False, 'error': 'Not a broadcast message'}

                # Delete all sibling copies that share a broadcastGroupId if present
                group_id = data.get('broadcastGroupId')
                if group_id:
                    query = self.db.collection('messages').where('broadcastGroupId', '==', group_id)
                    for sibling in query.stream():
                        sibling.reference.delete()
                else:
                    doc_ref.delete()
                return {'success': True}
            else:
                # In-memory fallback
                msg = self.messages_db.get(message_id)
                if not msg:
                    return {'success': False, 'error': 'Announcement not found'}
                if msg.get('senderId') != sender_id:
                    return {'success': False, 'error': 'Forbidden'}
                if msg.get('type') != 'broadcast':
                    return {'success': False, 'error': 'Not a broadcast message'}
                group_id = msg.get('broadcastGroupId')
                if group_id:
                    to_delete = [k for k, v in self.messages_db.items() if v.get('broadcastGroupId') == group_id]
                    for k in to_delete:
                        del self.messages_db[k]
                else:
                    del self.messages_db[message_id]
                return {'success': True}
        except Exception as e:
            print(f"Error deleting broadcast: {e}")
            return {'success': False, 'error': str(e)}

    def get_unread_message_count(self, user_id: str) -> int:
        """Get count of unread messages"""
        try:
            if self.db:
                # Only filter by read==False in Firestore (avoids composite index).
                # The senderId check is done in Python after fetching.
                messages_ref = self.db.collection('messages')
                query = messages_ref.where('read', '==', False)

                count = 0
                for doc in query.get():
                    msg_data = doc.to_dict()
                    # Skip messages sent by this user
                    if msg_data.get('senderId') == user_id:
                        continue
                    # Check if user is a participant in the conversation
                    conv_id = msg_data.get('conversationId')
                    if not conv_id:
                        continue
                    conv_ref = self.db.collection('conversations').document(conv_id)
                    conv = conv_ref.get()
                    if conv.exists and user_id in conv.to_dict().get('participants', []):
                        count += 1

                return count
            else:
                count = 0
                for msg in self.messages_db.values():
                    if msg.get('senderId') != user_id and not msg.get('read', False):
                        conv = self.conversations_db.get(msg.get('conversationId'))
                        if conv and user_id in conv.get('participants', []):
                            count += 1
                return count
        except Exception as e:
            print(f"Error getting unread message count: {e}")
            return 0
    
    def get_all_students(self) -> List[Dict]:
        """Get all students"""
        try:
            if self.db:
                users_ref = self.db.collection('users')
                query = users_ref.where('role', '==', 'student')
                
                students = []
                for doc in query.get():
                    student_data = doc.to_dict()
                    student_data['id'] = doc.id
                    students.append(student_data)
                
                return students
            else:
                return [u for u in self.users_db.values() if u.get('role') == 'student']
        except Exception as e:
            print(f"Error getting students: {e}")
            return []

    def get_all_advisers(self) -> List[Dict]:
        """Get all advisers (includes legacy employee role)."""
        try:
            if self.db:
                advisers = []
                seen = set()
                for role_value in ('adviser', 'employee', 'Adviser'):
                    try:
                        query = self.db.collection('users').where('role', '==', role_value)
                        for doc in query.get():
                            if doc.id in seen:
                                continue
                            seen.add(doc.id)
                            adviser_data = doc.to_dict()
                            adviser_data['id'] = doc.id
                            self._normalize_user_role(adviser_data)
                            if self._effective_role(adviser_data.get('role', '')) == 'adviser':
                                advisers.append(self._sanitize_user_for_list(adviser_data))
                    except Exception:
                        continue
                if not advisers:
                    for u in self.get_all_users():
                        if self._effective_role(u.get('role', '')) == 'adviser':
                            advisers.append(u)
                return advisers
            else:
                return [
                    u for u in self.users_db.values()
                    if self._effective_role(u.get('role', '')) == 'adviser'
                ]
        except Exception as e:
            print(f"Error getting advisers: {e}")
            return []
    
    def get_or_create_conversation(self, user1_id: str, user2_id: str) -> Optional[Dict]:
        """Get or create conversation between two users"""
        try:
            if self.db:
                conversations_ref = self.db.collection('conversations')
                query = conversations_ref.where('participants', 'array_contains', user1_id)
                
                for doc in query.get():
                    conv_data = doc.to_dict()
                    if user2_id in conv_data.get('participants', []):
                        conv_data['id'] = doc.id
                        return conv_data
                
                # Create new conversation
                conversation = {
                    'participants': [user1_id, user2_id],
                    'subject': '',
                    'lastMessage': None,
                    'lastMessageAt': None,
                    'createdAt': datetime.now().isoformat(),
                    'updatedAt': datetime.now().isoformat()
                }
                
                doc_ref = self.db.collection('conversations').add(conversation)
                conversation['id'] = doc_ref[1].id
                
                return conversation
            else:
                # Check if conversation exists
                for conv in self.conversations_db.values():
                    if user1_id in conv.get('participants', []) and user2_id in conv.get('participants', []):
                        return conv
                
                # Create new conversation
                conv_id = f"conv{len(self.conversations_db) + 1}"
                conversation = {
                    'id': conv_id,
                    'participants': [user1_id, user2_id],
                    'subject': '',
                    'lastMessage': None,
                    'lastMessageAt': None,
                    'createdAt': datetime.now().isoformat(),
                    'updatedAt': datetime.now().isoformat()
                }
                
                self.conversations_db[conv_id] = conversation
                
                return conversation
        except Exception as e:
            print(f"Error getting or creating conversation: {e}")
            return None
    
    # Settings Methods
    DEFAULT_SETTINGS = {
        'requiredOjtHours': 600,
        'ojtStartTime': '08:00',
        'ojtEndTime': '17:00',
        'lateThreshold': 15,
        'gpsRadius': 100,
        'requireFaceVerification': True,
        'requireGpsVerification': True,
        'academicYear': '2024-2025',
        'semester': '2nd Semester',
        'institutionName': 'USTP Oroquieta Campus',
        'institutionDepartment': 'College of Technology',
        'institutionEmail': 'ojt@ustp.edu.ph',
        'institutionCampus': '',
        'institutionLogoUrl': '',
        'defaultRegistrationRole': 'student',
    }

    def get_settings(self) -> Dict:
        """Get settings, merging defaults for any missing keys"""
        try:
            if self.db:
                settings_doc = self.db.collection('settings').document('general').get()
                stored = settings_doc.to_dict() if settings_doc.exists else {}
                # Merge defaults so OJT keys are always present
                merged = {**self.DEFAULT_SETTINGS, **stored}
                return merged
            else:
                return {**self.DEFAULT_SETTINGS, **self.settings_db}
        except Exception as e:
            print(f"Error getting settings: {e}")
            return dict(self.DEFAULT_SETTINGS)
    
    def update_settings(self, settings: Dict) -> Dict:
        """Update settings"""
        try:
            if self.db:
                doc_ref = self.db.collection('settings').document('general')
                try:
                    doc_ref.update(settings)
                except Exception as update_err:
                    if 'NOT_FOUND' in str(update_err) or '404' in str(update_err):
                        doc_ref.set(settings, merge=True)
                    else:
                        raise
                return {
                    'success': True,
                    'settings': settings
                }
            else:
                self.settings_db.update(settings)
                return {
                    'success': True,
                    'settings': self.settings_db
                }
        except Exception as e:
            print(f"Error updating settings: {e}")
            return {
                'success': False,
                'error': 'Failed to update settings'
            }

    # Task Methods
    def get_tasks_for_user(self, user_id: str) -> List[Dict]:
        """Get tasks assigned to a student/intern."""
        try:
            if self.db:
                query = self.db.collection('tasks').where('assigneeId', '==', user_id)
                tasks = []
                for doc in query.stream():
                    data = doc.to_dict()
                    data['id'] = doc.id
                    tasks.append(data)
                tasks.sort(key=lambda t: t.get('dueDate') or '', reverse=False)
                return tasks
            tasks = [
                t for t in self.tasks_db.values()
                if t.get('assigneeId') == user_id
            ]
            tasks.sort(key=lambda t: t.get('dueDate') or '')
            return tasks
        except Exception as e:
            print(f"Error getting tasks: {e}")
            return []

    def get_students_by_adviser(self, adviser_id: str) -> List[Dict]:
        """Return all students whose adviserId matches the given adviser."""
        try:
            if self.db:
                students = []
                for doc in self.db.collection('users').where('adviserId', '==', adviser_id).stream():
                    u = doc.to_dict()
                    u['id'] = doc.id
                    u.pop('password', None)
                    if (u.get('role') or '').lower() == 'student':
                        students.append(u)
                return students
            return [
                dict(u) for u in self.users_db.values()
                if u.get('adviserId') == adviser_id
                and (u.get('role') or '').lower() == 'student'
            ]
        except Exception as e:
            print(f"Error getting students by adviser: {e}")
            return []

    def get_students_by_supervisor(self, supervisor_id: str) -> List[Dict]:
        """Return all students whose supervisorId matches the given supervisor."""
        try:
            if self.db:
                students = []
                for doc in self.db.collection('users').where('supervisorId', '==', supervisor_id).stream():
                    u = doc.to_dict()
                    u['id'] = doc.id
                    u.pop('password', None)
                    if (u.get('role') or '').lower() == 'student':
                        students.append(u)
                return students
            return [
                dict(u) for u in self.users_db.values()
                if u.get('supervisorId') == supervisor_id
                and (u.get('role') or '').lower() == 'student'
            ]
        except Exception as e:
            print(f"Error getting students by supervisor: {e}")
            return []
    def get_tasks_created_by(self, user_id: str) -> List[Dict]:
        """Get tasks created by a specific supervisor."""
        try:
            if self.db:
                query = self.db.collection('tasks').where('createdBy', '==', user_id)
                tasks = []
                for doc in query.stream():
                    data = doc.to_dict()
                    data['id'] = doc.id
                    tasks.append(data)
                tasks.sort(key=lambda t: t.get('createdAt') or '', reverse=True)
                return tasks
            return [
                t for t in self.tasks_db.values()
                if t.get('createdBy') == user_id
            ]
        except Exception as e:
            print(f"Error getting tasks created by {user_id}: {e}")
            return []

    def get_all_tasks(self) -> List[Dict]:
        """Get all tasks (supervisor/admin)."""
        try:
            if self.db:
                tasks = []
                for doc in self.db.collection('tasks').stream():
                    data = doc.to_dict()
                    data['id'] = doc.id
                    tasks.append(data)
                return tasks
            return list(self.tasks_db.values())
        except Exception as e:
            print(f"Error getting all tasks: {e}")
            return []

    def get_task(self, task_id: str) -> Optional[Dict]:
        try:
            if self.db:
                doc = self.db.collection('tasks').document(task_id).get()
                if doc.exists:
                    data = doc.to_dict()
                    data['id'] = doc.id
                    return data
                return None
            return self.tasks_db.get(task_id)
        except Exception as e:
            print(f"Error getting task: {e}")
            return None

    def create_task(self, task: Dict) -> Dict:
        try:
            task.setdefault('status', 'pending')
            task.setdefault('createdAt', datetime.now().isoformat())
            if self.db:
                _, ref = self.db.collection('tasks').add(task)
                task['id'] = ref.id
                return {'success': True, 'task': task}
            task_id = f"task{len(self.tasks_db) + 1}"
            task['id'] = task_id
            self.tasks_db[task_id] = task
            return {'success': True, 'task': task}
        except Exception as e:
            print(f"Error creating task: {e}")
            return {'success': False, 'error': 'Failed to create task'}

    def update_task(self, task_id: str, update_data: Dict) -> Dict:
        try:
            update_data['updatedAt'] = datetime.now().isoformat()
            print(f"[FirebaseService] Updating task {task_id} with data: {update_data}")
            if self.db:
                ref = self.db.collection('tasks').document(task_id)
                if not ref.get().exists:
                    print(f"[FirebaseService] Task {task_id} not found")
                    return {'success': False, 'error': 'Task not found'}
                ref.update(update_data)
                data = ref.get().to_dict()
                data['id'] = task_id
                print(f"[FirebaseService] Task {task_id} updated successfully")
                if 'proofPhotoUrls' in data:
                    print(f"[FirebaseService] Proof photos in task: {data['proofPhotoUrls']}")
                return {'success': True, 'task': data}
            if task_id not in self.tasks_db:
                return {'success': False, 'error': 'Task not found'}
            self.tasks_db[task_id].update(update_data)
            return {'success': True, 'task': self.tasks_db[task_id]}
        except Exception as e:
            print(f"Error updating task: {e}")
            return {'success': False, 'error': str(e)}

    def delete_task(self, task_id: str) -> Dict:
        try:
            if self.db:
                ref = self.db.collection('tasks').document(task_id)
                if not ref.get().exists:
                    return {'success': False, 'error': 'Task not found'}
                ref.delete()
                return {'success': True}
            if task_id not in self.tasks_db:
                return {'success': False, 'error': 'Task not found'}
            del self.tasks_db[task_id]
            return {'success': True}
        except Exception as e:
            print(f"Error deleting task: {e}")
            return {'success': False, 'error': str(e)}

    # Company Methods
    def get_companies(self) -> List[Dict]:
        """Get all partner companies with live intern counts."""
        try:
            if self.db:
                companies_ref = self.db.collection('companies')
                companies = []
                for doc in companies_ref.get():
                    company_data = doc.to_dict()
                    company_data['id'] = doc.id
                    companies.append(company_data)

                # ── Count active interns per company ──────────────────────
                # Students store companyId (doc ID) and/or company/companyName
                # (name string) on their user record.  We count by ID first,
                # then fall back to name matching so both assignment paths work.
                try:
                    counts_by_id: dict = {}
                    counts_by_name: dict = {}
                    student_docs = (
                        self.db.collection('users')
                        .where('role', '==', 'student')
                        .get()
                    )
                    for sdoc in student_docs:
                        d = sdoc.to_dict()
                        cid = (d.get('companyId') or '').strip()
                        cname = (
                            d.get('company') or d.get('companyName') or ''
                        ).strip().lower()
                        if cid:
                            counts_by_id[cid] = counts_by_id.get(cid, 0) + 1
                        if cname:
                            counts_by_name[cname] = (
                                counts_by_name.get(cname, 0) + 1
                            )

                    for company in companies:
                        cid = company.get('id', '')
                        cname = (company.get('name') or '').strip().lower()
                        # Prefer an exact ID match; fall back to name match
                        company['internCount'] = counts_by_id.get(
                            cid, counts_by_name.get(cname, 0)
                        )
                except Exception as count_err:
                    print(f"[get_companies] Warning: could not count interns: {count_err}")
                    # Non-fatal – return companies without counts rather than failing
                    for company in companies:
                        company.setdefault('internCount', 0)

                return companies

            # ── In-memory fallback (dev / test) ───────────────────────────
            return list(self.companies_db.values()) if hasattr(self, 'companies_db') else []
        except Exception as e:
            print(f"Error getting companies: {e}")
            return []

    def get_company(self, company_id: str) -> Optional[Dict]:
        """Get a single company by ID"""
        try:
            if self.db:
                doc = self.db.collection('companies').document(company_id).get()
                if doc.exists:
                    company_data = doc.to_dict()
                    company_data['id'] = doc.id
                    return company_data
                return None
            return self.companies_db.get(company_id) if hasattr(self, 'companies_db') else None
        except Exception as e:
            print(f"Error getting company: {e}")
            return None

    def create_company(self, company: Dict) -> Dict:
        """Create a new company"""
        try:
            company['createdAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('companies').add(company)
                company['id'] = doc_ref[1].id
                return {'success': True, 'company': company}
            if not hasattr(self, 'companies_db'):
                self.companies_db = {}
            company_id = f"comp{len(self.companies_db) + 1}"
            company['id'] = company_id
            self.companies_db[company_id] = company
            return {'success': True, 'company': company}
        except Exception as e:
            print(f"Error creating company: {e}")
            return {'success': False, 'error': 'Failed to create company'}

    def update_company(self, company_id: str, update_data: Dict) -> Dict:
        """Update company fields"""
        try:
            update_data['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('companies').document(company_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Company not found'}
                doc_ref.update(update_data)
                updated = doc_ref.get().to_dict()
                updated['id'] = company_id
                return {'success': True, 'company': updated}
            if not hasattr(self, 'companies_db'):
                return {'success': False, 'error': 'Company not found'}
            if company_id not in self.companies_db:
                return {'success': False, 'error': 'Company not found'}
            self.companies_db[company_id].update(update_data)
            return {'success': True, 'company': self.companies_db[company_id]}
        except Exception as e:
            print(f"Error updating company: {e}")
            return {'success': False, 'error': str(e)}

    def delete_company(self, company_id: str) -> Dict:
        """Delete a company by ID"""
        try:
            deleted = False
            if self.db:
                # 1. Try exact ID match in companies collection
                doc_ref = self.db.collection('companies').document(company_id)
                if doc_ref.get().exists:
                    doc_ref.delete()
                    deleted = True
                    print(f"[delete_company] Deleted from companies: {company_id}")

                # 2. Try exact ID match in hte_profiles collection
                if not deleted:
                    hte_ref = self.db.collection('hte_profiles').document(company_id)
                    if hte_ref.get().exists:
                        hte_ref.delete()
                        deleted = True
                        print(f"[delete_company] Deleted from hte_profiles: {company_id}")

                # 3. Scan hte_profiles — match by id field stored inside doc
                if not deleted:
                    for doc in self.db.collection('hte_profiles').get():
                        d = doc.to_dict()
                        if d.get('id') == company_id:
                            self.db.collection('hte_profiles').document(doc.id).delete()
                            deleted = True
                            print(f"[delete_company] Deleted hte_profiles by inner id field: {doc.id}")
                            break

                # 4. Scan companies — match by id field stored inside doc
                if not deleted:
                    for doc in self.db.collection('companies').get():
                        d = doc.to_dict()
                        if d.get('id') == company_id:
                            self.db.collection('companies').document(doc.id).delete()
                            deleted = True
                            print(f"[delete_company] Deleted companies by inner id field: {doc.id}")
                            break

            if deleted:
                return {'success': True}

            # 5. Local in-memory fallback
            if hasattr(self, 'companies_db') and company_id in self.companies_db:
                del self.companies_db[company_id]
                return {'success': True}

            return {'success': False, 'error': 'Company not found'}
        except Exception as e:
            print(f"Error deleting company: {e}")
            return {'success': False, 'error': str(e)}

    def delete_company_by_name(self, name: str) -> Dict:
        """Delete a company/HTE profile by name (case-insensitive) from both collections."""
        needle = (name or '').strip().lower()
        if not needle:
            return {'success': False, 'error': 'Name is required'}
        try:
            deleted = False
            if self.db:
                for collection in ('companies', 'hte_profiles'):
                    for doc in self.db.collection(collection).get():
                        d = doc.to_dict()
                        stored = (d.get('name') or '').strip().lower()
                        if stored == needle:
                            self.db.collection(collection).document(doc.id).delete()
                            deleted = True
                            print(f"[delete_company_by_name] Deleted '{name}' from {collection}: {doc.id}")
                            # Keep scanning — could exist in both collections
            if deleted:
                return {'success': True}
            return {'success': False, 'error': f'No company named "{name}" found'}
        except Exception as e:
            print(f"Error deleting company by name: {e}")
            return {'success': False, 'error': str(e)}

    # Evaluation Methods
    def get_evaluation_criteria(self) -> List[Dict]:
        """Get all evaluation criteria"""
        try:
            if self.db:
                criteria = []
                for doc in self.db.collection('evaluation_criteria').get():
                    c = doc.to_dict()
                    c['id'] = doc.id
                    criteria.append(c)
                return criteria
            return []
        except Exception as e:
            print(f"Error getting evaluation criteria: {e}")
            return []

    def create_evaluation_criterion(self, criterion: Dict) -> Dict:
        """Create a new evaluation criterion"""
        try:
            if self.db:
                doc_ref = self.db.collection('evaluation_criteria').add(criterion)
                criterion['id'] = doc_ref[1].id
                return {'success': True, 'criterion': criterion}
            return {'success': False, 'error': 'No database connection'}
        except Exception as e:
            print(f"Error creating criterion: {e}")
            return {'success': False, 'error': str(e)}

    def get_evaluations(self, evaluator_id: str = None, student_id: str = None, type: str = None) -> List[Dict]:
        """Get evaluations with filters. Falls back to a full scan if a composite index is missing."""
        try:
            if not self.db:
                return []

            docs = []
            try:
                query = self.db.collection('evaluations')
                if evaluator_id:
                    query = query.where('evaluatorId', '==', evaluator_id)
                if student_id:
                    query = query.where('studentId', '==', student_id)
                if type:
                    query = query.where('type', '==', type)
                docs = list(query.get())
            except Exception as qe:
                print(f"[WARNING] evaluation query failed ({qe}); scanning collection")
                docs = list(self.db.collection('evaluations').get())

            evaluations = []
            type_norm = (type or '').strip().lower()
            for doc in docs:
                e = doc.to_dict() or {}
                e['id'] = doc.id
                if evaluator_id and e.get('evaluatorId') != evaluator_id:
                    continue
                if student_id and e.get('studentId') != student_id:
                    continue
                if type_norm and str(e.get('type') or '').strip().lower() != type_norm:
                    continue
                evaluations.append(e)
            evaluations.sort(key=lambda x: str(x.get('createdAt') or ''), reverse=True)
            return evaluations
        except Exception as e:
            print(f"Error getting evaluations: {e}")
            return []

    def get_evaluation(self, evaluation_id: str) -> Optional[Dict]:
        """Get a single evaluation by ID"""
        try:
            if self.db:
                doc = self.db.collection('evaluations').document(evaluation_id).get()
                if doc.exists:
                    e = doc.to_dict()
                    e['id'] = doc.id
                    return e
                return None
            return None
        except Exception as e:
            print(f"Error getting evaluation: {e}")
            return None

    def create_evaluation(self, evaluation: Dict) -> Dict:
        """Create a new evaluation"""
        try:
            if self.db:
                doc_ref = self.db.collection('evaluations').add(self._strip_none(evaluation))
                evaluation['id'] = doc_ref[1].id
                return {'success': True, 'evaluation': evaluation}
            return {'success': False, 'error': 'No database connection'}
        except Exception as e:
            print(f"Error creating evaluation: {e}")
            return {'success': False, 'error': str(e)}

    def update_evaluation(self, evaluation_id: str, update_data: Dict) -> Dict:
        """Update an evaluation"""
        try:
            if self.db:
                doc_ref = self.db.collection('evaluations').document(evaluation_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Evaluation not found'}
                doc_ref.update(update_data)
                updated = doc_ref.get().to_dict()
                updated['id'] = evaluation_id
                return {'success': True, 'evaluation': updated}
            return {'success': False, 'error': 'No database connection'}
        except Exception as e:
            print(f"Error updating evaluation: {e}")
            return {'success': False, 'error': str(e)}

    def delete_evaluation(self, evaluation_id: str) -> Dict:
        """Delete an evaluation by ID"""
        try:
            if self.db:
                doc_ref = self.db.collection('evaluations').document(evaluation_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Evaluation not found'}
                doc_ref.delete()
                return {'success': True}
            return {'success': False, 'error': 'No database connection'}
        except Exception as e:
            print(f"Error deleting evaluation: {e}")
            return {'success': False, 'error': str(e)}

    # ── Announcement Attachment Methods ────────────────────────────────────────

    def save_announcement_attachment(self, metadata: Dict) -> Dict:
        """Persist attachment metadata to the announcement_attachments collection."""
        try:
            if self.db:
                attachment_id = metadata.get('id')
                self.db.collection('announcement_attachments').document(attachment_id).set(metadata)
                return {'success': True}
            # In-memory fallback
            if not hasattr(self, '_attachments_db'):
                self._attachments_db = {}
            self._attachments_db[metadata['id']] = metadata
            return {'success': True}
        except Exception as e:
            print(f"Error saving announcement attachment: {e}")
            raise

    def get_announcement_attachment(self, attachment_id: str) -> Optional[Dict]:
        """Fetch a single attachment metadata document by ID."""
        try:
            if self.db:
                doc = self.db.collection('announcement_attachments').document(attachment_id).get()
                if doc.exists:
                    data = doc.to_dict()
                    data['id'] = doc.id
                    return data
                return None
            if not hasattr(self, '_attachments_db'):
                self._attachments_db = {}
            return self._attachments_db.get(attachment_id)
        except Exception as e:
            print(f"Error fetching announcement attachment: {e}")
            return None

    def get_announcement_attachments(self, attachment_ids: list) -> list:
        """Fetch multiple attachment metadata documents by ID list."""
        results = []
        for aid in attachment_ids:
            doc = self.get_announcement_attachment(aid)
            if doc:
                results.append(doc)
        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Application Plan Methods
    # ──────────────────────────────────────────────────────────────────────────

    def get_application_plans(self, student_id: str = None, status: str = None) -> List[Dict]:
        """Get application plans with optional filters."""
        try:
            if self.db:
                query = self.db.collection('application_plans')
                if student_id:
                    query = query.where('studentId', '==', student_id)
                if status:
                    query = query.where('status', '==', status)
                plans = []
                for doc in query.get():
                    d = doc.to_dict()
                    d['id'] = doc.id
                    plans.append(d)
                return plans
            # in-memory fallback
            if not hasattr(self, '_plans_db'):
                self._plans_db = {}
            plans = list(self._plans_db.values())
            if student_id:
                plans = [p for p in plans if p.get('studentId') == student_id]
            if status:
                plans = [p for p in plans if p.get('status') == status]
            return plans
        except Exception as e:
            print(f"Error getting application plans: {e}")
            return []

    def get_application_plan(self, plan_id: str) -> Optional[Dict]:
        """Get a single application plan by ID."""
        try:
            if self.db:
                doc = self.db.collection('application_plans').document(plan_id).get()
                if doc.exists:
                    d = doc.to_dict()
                    d['id'] = doc.id
                    return d
                return None
            if not hasattr(self, '_plans_db'):
                self._plans_db = {}
            return self._plans_db.get(plan_id)
        except Exception as e:
            print(f"Error getting application plan: {e}")
            return None

    def create_application_plan(self, plan: Dict) -> Dict:
        """Create a new application plan."""
        try:
            plan['createdAt'] = datetime.now().isoformat()
            plan['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('application_plans').add(plan)
                plan['id'] = doc_ref[1].id
                return {'success': True, 'plan': plan}
            if not hasattr(self, '_plans_db'):
                self._plans_db = {}
            import uuid
            plan_id = str(uuid.uuid4())
            plan['id'] = plan_id
            self._plans_db[plan_id] = plan
            return {'success': True, 'plan': plan}
        except Exception as e:
            print(f"Error creating application plan: {e}")
            return {'success': False, 'error': str(e)}

    def update_application_plan(self, plan_id: str, update_data: Dict) -> Dict:
        """Update an application plan."""
        try:
            update_data['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('application_plans').document(plan_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Plan not found'}
                doc_ref.update(update_data)
                updated = doc_ref.get().to_dict()
                updated['id'] = plan_id
                return {'success': True, 'plan': updated}
            if not hasattr(self, '_plans_db') or plan_id not in self._plans_db:
                return {'success': False, 'error': 'Plan not found'}
            self._plans_db[plan_id].update(update_data)
            return {'success': True, 'plan': self._plans_db[plan_id]}
        except Exception as e:
            print(f"Error updating application plan: {e}")
            return {'success': False, 'error': str(e)}

    def delete_application_plan(self, plan_id: str) -> Dict:
        """Delete an application plan."""
        try:
            if self.db:
                doc_ref = self.db.collection('application_plans').document(plan_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Plan not found'}
                doc_ref.delete()
                return {'success': True}
            if not hasattr(self, '_plans_db') or plan_id not in self._plans_db:
                return {'success': False, 'error': 'Plan not found'}
            del self._plans_db[plan_id]
            return {'success': True}
        except Exception as e:
            print(f"Error deleting application plan: {e}")
            return {'success': False, 'error': str(e)}

    # ──────────────────────────────────────────────────────────────────────────
    # HTE (Host Training Establishment) Extended Methods
    # ──────────────────────────────────────────────────────────────────────────

    def get_hte_profiles(self) -> List[Dict]:
        """Get all HTE profiles (richer than companies — includes slots/skills/requirements)."""
        try:
            if self.db:
                profiles = []
                for doc in self.db.collection('hte_profiles').get():
                    d = doc.to_dict()
                    d['id'] = doc.id
                    profiles.append(d)
                return profiles
            if not hasattr(self, '_hte_db'):
                self._hte_db = {}
            return list(self._hte_db.values())
        except Exception as e:
            print(f"Error getting HTE profiles: {e}")
            return []

    def get_hte_profile(self, hte_id: str) -> Optional[Dict]:
        """Get a single HTE profile."""
        try:
            if self.db:
                doc = self.db.collection('hte_profiles').document(hte_id).get()
                if doc.exists:
                    d = doc.to_dict()
                    d['id'] = doc.id
                    return d
                return None
            if not hasattr(self, '_hte_db'):
                self._hte_db = {}
            return self._hte_db.get(hte_id)
        except Exception as e:
            print(f"Error getting HTE profile: {e}")
            return None

    def create_hte_profile(self, hte: Dict) -> Dict:
        """Create a new HTE profile."""
        try:
            hte['createdAt'] = datetime.now().isoformat()
            hte['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('hte_profiles').add(hte)
                hte['id'] = doc_ref[1].id
                return {'success': True, 'hte': hte}
            if not hasattr(self, '_hte_db'):
                self._hte_db = {}
            import uuid
            hte_id = str(uuid.uuid4())
            hte['id'] = hte_id
            self._hte_db[hte_id] = hte
            return {'success': True, 'hte': hte}
        except Exception as e:
            print(f"Error creating HTE profile: {e}")
            return {'success': False, 'error': str(e)}

    def update_hte_profile(self, hte_id: str, update_data: Dict) -> Dict:
        """Update an HTE profile."""
        try:
            update_data['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('hte_profiles').document(hte_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'HTE not found'}
                doc_ref.update(update_data)
                updated = doc_ref.get().to_dict()
                updated['id'] = hte_id
                return {'success': True, 'hte': updated}
            if not hasattr(self, '_hte_db') or hte_id not in self._hte_db:
                return {'success': False, 'error': 'HTE not found'}
            self._hte_db[hte_id].update(update_data)
            return {'success': True, 'hte': self._hte_db[hte_id]}
        except Exception as e:
            print(f"Error updating HTE profile: {e}")
            return {'success': False, 'error': str(e)}

    def delete_hte_profile(self, hte_id: str) -> Dict:
        """Delete an HTE profile."""
        try:
            if self.db:
                doc_ref = self.db.collection('hte_profiles').document(hte_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'HTE not found'}
                doc_ref.delete()
                return {'success': True}
            if not hasattr(self, '_hte_db') or hte_id not in self._hte_db:
                return {'success': False, 'error': 'HTE not found'}
            del self._hte_db[hte_id]
            return {'success': True}
        except Exception as e:
            print(f"Error deleting HTE profile: {e}")
            return {'success': False, 'error': str(e)}

    # ──────────────────────────────────────────────────────────────────────────
    # Matching Rules Methods
    # ──────────────────────────────────────────────────────────────────────────

    def get_matching_rules(self) -> List[Dict]:
        """Get all matching/assignment rules."""
        try:
            if self.db:
                rules = []
                for doc in self.db.collection('matching_rules').get():
                    d = doc.to_dict()
                    d['id'] = doc.id
                    rules.append(d)
                # Sort by priority ascending
                rules.sort(key=lambda x: x.get('priority', 99))
                return rules
            if not hasattr(self, '_rules_match_db'):
                self._rules_match_db = {}
            return sorted(self._rules_match_db.values(), key=lambda x: x.get('priority', 99))
        except Exception as e:
            print(f"Error getting matching rules: {e}")
            return []

    def save_matching_rules(self, rules: List[Dict]) -> Dict:
        """Replace all matching rules with a new set."""
        try:
            if self.db:
                col_ref = self.db.collection('matching_rules')
                # Delete existing rules
                for doc in col_ref.get():
                    doc.reference.delete()
                # Write new rules
                for rule in rules:
                    rule.pop('id', None)
                    rule['updatedAt'] = datetime.now().isoformat()
                    col_ref.add(rule)
                return {'success': True}
            if not hasattr(self, '_rules_match_db'):
                self._rules_match_db = {}
            self._rules_match_db.clear()
            import uuid
            for rule in rules:
                rid = rule.get('id') or str(uuid.uuid4())
                rule['id'] = rid
                self._rules_match_db[rid] = rule
            return {'success': True}
        except Exception as e:
            print(f"Error saving matching rules: {e}")
            return {'success': False, 'error': str(e)}

    # ──────────────────────────────────────────────────────────────────────────
    # Auto-Matching Logic
    # ──────────────────────────────────────────────────────────────────────────

    def _auto_assign_supervisor(self, student_id: str, hte_id: str = None, hte: Dict = None) -> None:
        """
        Automatically assign a supervisor to a student based on the assigned HTE.
        Uses the supervisor field from the HTE profile or company record.
        Skips if the student already has a supervisor assigned.
        """
        # Skip if student already has a supervisor
        student = self.get_user(student_id)
        if not student:
            return
        if student.get('supervisorId') or student.get('supervisorName'):
            print(f"[auto-supervisor] Student {student_id} already has supervisor — skipping")
            return

        # Get HTE data if not provided
        if not hte:
            if hte_id:
                hte = self.get_hte_profile(hte_id)
                if not hte:
                    hte = self.get_company(hte_id)
            if not hte:
                print(f"[auto-supervisor] No HTE found for student {student_id}")
                return

        # Get supervisor from HTE
        supervisor_name = (hte.get('supervisor') or
                          hte.get('contactPerson') or
                          hte.get('supervisorName') or '').strip()

        if not supervisor_name:
            print(f"[auto-supervisor] No supervisor found in HTE '{hte.get('name')}' for student {student_id}")
            return

        # Find the supervisor user record
        supervisor = None
        try:
            if self.db:
                for doc in self.db.collection('users').where('role', '==', 'supervisor').get():
                    d = doc.to_dict()
                    d['id'] = doc.id
                    if d.get('name', '').strip().lower() == supervisor_name.strip().lower():
                        supervisor = d
                        break
            else:
                for u in (self.users_db or {}).values():
                    if u.get('role') == 'supervisor' and u.get('name', '').strip().lower() == supervisor_name.strip().lower():
                        supervisor = dict(u)
                        break
        except Exception as e:
            print(f"[auto-supervisor] Error finding supervisor by name: {e}")

        if supervisor:
            print(f"[auto-supervisor] Assigning HTE supervisor {supervisor.get('name')} to student {student_id}")
            self.update_user(student_id, {
                'supervisorId':   supervisor.get('id'),
                'supervisorName': supervisor.get('name'),
            })
        else:
            print(f"[auto-supervisor] Supervisor '{supervisor_name}' not found in users for student {student_id}")

    def _auto_assign_adviser(self, student_id: str, hte_id: str = None) -> None:
        """
        Automatically assign an adviser to a student.
        If hte_id is provided, use the adviser assigned to that HTE company.
        Otherwise, fall back to load-balancing (least-loaded active adviser).
        Skips if the student already has an adviser assigned.
        """
        # Skip if student already has an adviser
        student = self.get_user(student_id)
        if not student:
            return
        if student.get('adviserId') or student.get('adviserName'):
            print(f"[auto-adviser] Student {student_id} already has adviser — skipping")
            return

        # Try to get adviser from the assigned HTE
        if hte_id:
            hte = self.get_hte_profile(hte_id)
            if not hte:
                hte = self.get_company(hte_id)
            
            if hte:
                # Check if HTE has an assigned adviser
                adviser_name = hte.get('adviser') or hte.get('adviserName') or hte.get('assignedAdviser')
                adviser_id = hte.get('adviserId') or hte.get('assignedAdviserId')
                
                if adviser_name:
                    # Find the adviser user record
                    adviser = None
                    try:
                        if self.db:
                            for doc in self.db.collection('users').where('role', '==', 'adviser').get():
                                d = doc.to_dict()
                                d['id'] = doc.id
                                if d.get('name', '').strip().lower() == adviser_name.strip().lower():
                                    adviser = d
                                    break
                        else:
                            for u in (self.users_db or {}).values():
                                if u.get('role') == 'adviser' and u.get('name', '').strip().lower() == adviser_name.strip().lower():
                                    adviser = dict(u)
                                    break
                    except Exception as e:
                        print(f"[auto-adviser] Error finding adviser by name: {e}")
                    
                    if adviser:
                        print(f"[auto-adviser] Assigning HTE adviser {adviser.get('name')} to student {student_id}")
                        self.update_user(student_id, {
                            'adviserId':   adviser.get('id', adviser_id),
                            'adviserName': adviser.get('name', adviser_name),
                        })
                        return
                    else:
                        print(f"[auto-adviser] HTE adviser '{adviser_name}' not found in users, falling back to load-balancing")

        # Fallback: load-balanced adviser assignment
        # Fetch all advisers
        advisers: List[Dict] = []
        try:
            if self.db:
                for doc in self.db.collection('users').where('role', '==', 'adviser').get():
                    d = doc.to_dict()
                    d['id'] = doc.id
                    if (d.get('status') or 'active') == 'active':
                        advisers.append(d)
            else:
                for u in (self.users_db or {}).values():
                    if u.get('role') == 'adviser' and (u.get('status') or 'active') == 'active':
                        advisers.append(dict(u))
        except Exception as e:
            print(f"[auto-adviser] Error fetching advisers: {e}")
            return

        if not advisers:
            print("[auto-adviser] No active advisers found")
            return

        # Count how many students each adviser currently has
        load: Dict[str, int] = {a['id']: 0 for a in advisers}
        try:
            if self.db:
                for doc in self.db.collection('users').where('role', '==', 'student').get():
                    aid = doc.to_dict().get('adviserId')
                    if aid and aid in load:
                        load[aid] += 1
            else:
                for u in (self.users_db or {}).values():
                    if u.get('role') == 'student':
                        aid = u.get('adviserId')
                        if aid and aid in load:
                            load[aid] += 1
        except Exception as e:
            print(f"[auto-adviser] Error counting loads: {e}")

        # Pick the adviser with the minimum load
        best_adviser = min(advisers, key=lambda a: load.get(a['id'], 0))
        print(f"[auto-adviser] Assigning load-balanced adviser {best_adviser.get('name')} (load={load.get(best_adviser['id'], 0)}) to student {student_id}")

        self.update_user(student_id, {
            'adviserId':   best_adviser['id'],
            'adviserName': best_adviser.get('name', ''),
        })

    def auto_match_student_to_hte(self, plan: Dict) -> Optional[Dict]:
        """
        Match a student's internship plan to the best available HTE/company.

        Strategy (in order of priority):
        1.  Score structured hte_profiles against the plan using field matching.
        2.  For the 'companies' collection (partner companies added via web admin),
            any active company is a valid candidate — score based on content
            relevance, but guarantee at least score=1 for any active company
            so they always qualify.
        3.  If multiple candidates tie, prefer the one whose name best matches
            student keywords.
        4.  FALLBACK: if nothing matches via scoring (all empty fields, etc.),
            return the first active company as a guaranteed fallback so the
            student is always assigned.
        """
        try:
            # ── Build candidate pool ──────────────────────────────────────
            candidates = []
            seen_names = set()

            # hte_profiles first (rich structured data)
            for h in self.get_hte_profiles():
                h = dict(h)
                h['_source'] = 'hte_profiles'
                candidates.append(h)
                seen_names.add((h.get('name') or '').strip().lower())

            # partner companies (basic data, always valid candidates)
            for c in self.get_companies():
                name_key = (c.get('name') or '').strip().lower()
                if name_key in seen_names:
                    continue  # skip duplicate already in hte_profiles
                normalised = {
                    'id':              c.get('id', ''),
                    'name':            c.get('name', ''),
                    'location':        c.get('address', ''),
                    'address':         c.get('address', ''),
                    'status':          c.get('status', 'active'),
                    'description':     c.get('description', ''),
                    'requiredCourses': c.get('requiredCourses') or [],
                    'requiredSkills':  c.get('requiredSkills') or [],
                    'fieldOfWork':     c.get('fieldOfWork') or [],
                    'availableSlots':  c.get('availableSlots', 999),
                    'totalSlots':      c.get('totalSlots', 999),
                    '_source':         'companies',
                }
                candidates.append(normalised)
                seen_names.add(name_key)

            if not candidates:
                print("[auto-match] No candidates in hte_profiles or companies")
                return None

            print(f"[auto-match] Total candidates: {len(candidates)}")

            # ── Student profile ───────────────────────────────────────────
            student_course    = (plan.get('course') or '').strip().lower()
            student_skills    = [s.strip().lower() for s in (plan.get('skills') or []) if s.strip()]
            student_location  = (plan.get('preferredLocation') or '').strip().lower()
            student_interests = [i.strip().lower() for i in (plan.get('interests') or []) if i.strip()]

            has_any_content = bool(student_course or student_skills or student_interests)

            # ── Score each candidate ─────────────────────────────────────
            scored = []

            for hte in candidates:
                status = (hte.get('status') or 'active').strip().lower()
                if status not in ('active', ''):
                    print(f"[auto-match] Skipping '{hte.get('name')}' — status='{status}'")
                    continue

                source = hte.get('_source', 'hte_profiles')
                raw_slots = hte.get('availableSlots', None)
                try:
                    slots = int(raw_slots) if raw_slots is not None else 999
                except (TypeError, ValueError):
                    slots = 999
                # Skip HTEs that explicitly have no remaining slots
                if source == 'hte_profiles' and raw_slots is not None and slots <= 0:
                    print(f"[auto-match] Skipping '{hte.get('name')}' — no slots")
                    continue
                score  = 0

                hte_name = (hte.get('name') or '').lower()
                hte_desc = (hte.get('description') or '').lower()
                hte_text = f"{hte_name} {hte_desc}"

                # ── Criterion 1: Course ──────────────────────────────────
                hte_courses = [x.strip().lower() for x in (hte.get('requiredCourses') or []) if x.strip()]
                if student_course:
                    if hte_courses:
                        if student_course in hte_courses:
                            score += 2  # exact match worth more
                    else:
                        words = [w for w in student_course.split() if len(w) >= 2]
                        if any(w in hte_text for w in words):
                            score += 1

                # ── Criterion 2: Skills ──────────────────────────────────
                hte_skills = [x.strip().lower() for x in (hte.get('requiredSkills') or []) if x.strip()]
                if student_skills:
                    if hte_skills:
                        if any(s in hte_skills for s in student_skills):
                            score += 2
                    else:
                        if any(s in hte_text for s in student_skills):
                            score += 1

                # ── Criterion 3: Location ────────────────────────────────
                hte_loc = (hte.get('location') or hte.get('address') or '').lower()
                if student_location and hte_loc:
                    if student_location in hte_loc or hte_loc in student_location:
                        score += 1
                    else:
                        lw = [w for w in student_location.split() if len(w) >= 3]
                        if lw and any(w in hte_loc for w in lw):
                            score += 1

                # ── Criterion 4: Interests / field of work ───────────────
                hte_fields = [x.strip().lower() for x in (hte.get('fieldOfWork') or []) if x.strip()]
                if student_interests:
                    if hte_fields:
                        if any(i in hte_fields for i in student_interests):
                            score += 2
                    else:
                        if any(i in hte_text for i in student_interests):
                            score += 1

                # ── Criterion 5: Slots ───────────────────────────────────
                if slots > 0:
                    score += 1

                # ── Guaranteed minimum for companies ────────────────────
                # Any active partner company is always a valid candidate.
                # This ensures students are never left unassigned just because
                # companies lack structured metadata fields.
                if source == 'companies' and has_any_content:
                    score = max(score, 1)

                # ── Name keyword bonus ───────────────────────────────────
                keywords = []
                if student_course:
                    keywords += [w for w in student_course.split() if len(w) >= 2]
                for sk in student_skills:
                    keywords += [w for w in sk.split() if len(w) >= 3]
                for it in student_interests:
                    keywords += [w for w in it.split() if len(w) >= 3]

                if keywords and any(kw in hte_name for kw in keywords):
                    score += 1

                print(f"[auto-match] '{hte.get('name')}' source={source} score={score}")
                scored.append((score, hte))

            if not scored:
                print("[auto-match] No active candidates after filtering")
                return None

            # Sort by score descending, then slots descending
            scored.sort(key=lambda x: (x[0], int(x[1].get('availableSlots', 999))), reverse=True)

            # Best match: highest score >= 1
            best_score, best_hte = scored[0]
            if best_score >= 1:
                print(f"[auto-match] Winner: '{best_hte.get('name')}' score={best_score}")
                return best_hte

            # ── FALLBACK: just use first active company/hte ──────────────
            # If every candidate scored 0 (completely empty plan), pick
            # the first active one rather than returning None.
            print("[auto-match] All scores=0, using fallback: first active candidate")
            for _, hte in scored:
                return hte

            return None

        except Exception as e:
            print(f"[auto-match] ERROR: {e}")
            import traceback
            traceback.print_exc()
            return None

    def ranked_hte_matches(self, plan: Dict) -> List[Dict]:
        """Return active HTE/company candidates ordered best-first."""
        best = self.auto_match_student_to_hte(plan)
        # auto_match_student_to_hte already picks a winner; rebuild a try-order
        # by re-running candidate collection so assignment can fall through.
        try:
            winner = best
            ordered = []
            seen = set()
            if winner and winner.get('id'):
                ordered.append(winner)
                seen.add(winner.get('id'))
            for h in self.get_hte_profiles():
                hid = h.get('id')
                if hid and hid not in seen:
                    status = (h.get('status') or 'active').strip().lower()
                    if status in ('active', ''):
                        ordered.append(h)
                        seen.add(hid)
            for c in self.get_companies():
                cid = c.get('id')
                if cid and cid not in seen:
                    status = (c.get('status') or 'active').strip().lower()
                    if status in ('active', ''):
                        ordered.append(c)
                        seen.add(cid)
            return ordered
        except Exception as e:
            print(f"[auto-match] ranked_hte_matches error: {e}")
            return [best] if best else []

    def auto_assign_plan(self, plan_id: str, plan: Dict, assigned_by: str = 'system') -> Dict:
        """Match then assign, trying the next HTE if the top pick cannot be assigned."""
        candidates = self.ranked_hte_matches(plan)
        if not candidates:
            return {'success': False, 'error': 'No matching HTE found'}
        last_error = 'No matching HTE found'
        for hte in candidates:
            hte_id = (hte or {}).get('id')
            if not hte_id:
                continue
            result = self.assign_student_to_hte(plan_id, hte_id, assigned_by=assigned_by)
            if result.get('success'):
                return result
            last_error = result.get('error', last_error)
            print(f"[auto-match] Could not assign '{hte.get('name')}': {last_error}")
        return {'success': False, 'error': last_error}

    def assign_student_to_hte(self, plan_id: str, hte_id: str, assigned_by: str = 'system') -> Dict:
        """
        Assign a student to an HTE or partner company:
        1. Try hte_profiles first; fall back to companies collection.
        2. Decrement availableSlots if the HTE tracks slots.
        3. Update the application plan with assignedHteId, status=assigned.
        4. Update the student's user record (companyId / companyName) so the
           rest of the app (check-in, profile) knows which company they're at.
        5. Return the updated plan.
        """
        try:
            # ── Resolve the HTE from either collection ────────────────────
            hte = self.get_hte_profile(hte_id)
            source = 'hte_profiles'
            if not hte:
                hte = self.get_company(hte_id)
                source = 'companies'
            if not hte:
                return {'success': False, 'error': 'HTE not found'}

            hte_name = hte.get('name', '')

            # ── Slot management (only when the HTE explicitly tracks slots) ─
            if source == 'hte_profiles' and 'availableSlots' in hte and hte.get('availableSlots') is not None:
                try:
                    slots = int(hte.get('availableSlots'))
                except (TypeError, ValueError):
                    slots = None
                if slots is not None:
                    if slots <= 0:
                        return {'success': False, 'error': 'No available slots in this HTE'}
                    slot_result = self.update_hte_profile(hte_id, {'availableSlots': slots - 1})
                    if not slot_result.get('success'):
                        return slot_result
            # ── Capacity check for companies collection ───────────────────
            elif source == 'companies':
                capacity = hte.get('internCapacity') or hte.get('totalSlots') or 0
                try:
                    capacity = int(capacity)
                except (TypeError, ValueError):
                    capacity = 0
                if capacity > 0:
                    # Count current interns for this company
                    try:
                        occupied = 0
                        cmp_name = (hte.get('name') or '').strip().lower()
                        if self.db:
                            for doc in (self.db.collection('users')
                                        .where('role', '==', 'student').get()):
                                d = doc.to_dict()
                                if (d.get('companyId') == hte_id or
                                        (d.get('company') or '').strip().lower() == cmp_name):
                                    occupied += 1
                        else:
                            for u in (self.users_db or {}).values():
                                if u.get('role') == 'student' and (
                                        u.get('companyId') == hte_id or
                                        (u.get('company') or '').strip().lower() == cmp_name):
                                    occupied += 1
                        if occupied >= capacity:
                            return {'success': False, 'error': f'No available slots — {hte_name} is at full capacity ({capacity})'}
                        # Update available slots after successful assignment (done below)
                        new_available = max(0, capacity - occupied - 1)
                        new_status = 'full' if new_available == 0 else 'active'
                        self.update_company(hte_id, {
                            'availableSlots': new_available,
                            'status': new_status,
                        })
                    except Exception as slot_err:
                        print(f"[assign] Warning: company capacity check failed: {slot_err}")

            # ── Update the application plan ───────────────────────────────
            plan_update = {
                'status': 'assigned',
                'assignedHteId': hte_id,
                'assignedHteName': hte_name,
                'assignedAt': datetime.now().isoformat(),
                'assignedBy': assigned_by,
            }
            plan_result = self.update_application_plan(plan_id, plan_update)
            if not plan_result.get('success'):
                return plan_result

            updated_plan = plan_result['plan']

            # ── Get supervisor and adviser info for the plan response ───────
            supervisor_name = hte.get('supervisor') or hte.get('contactPerson') or ''
            adviser_name = hte.get('adviser') or ''

            if supervisor_name:
                updated_plan['supervisorName'] = supervisor_name
            if adviser_name:
                updated_plan['adviserName'] = adviser_name

            # ── Sync company assignment to the student's user record ───────
            student_id = updated_plan.get('studentId', '')
            if student_id:
                try:
                    self.update_user(student_id, {
                        'companyId':   hte_id,
                        'companyName': hte_name,
                        'company':     hte_name,
                    })
                except Exception as e:
                    # Non-fatal: plan is already assigned; log and continue
                    print(f"[assign] Warning: could not update user companyId: {e}")

            # ── Auto-assign a supervisor (use HTE's supervisor if available) ─
            if student_id:
                try:
                    self._auto_assign_supervisor(student_id, hte_id=hte_id, hte=hte)
                except Exception as se:
                    print(f"[assign] Warning: auto supervisor assignment failed: {se}")

            # ── Auto-assign an adviser (use HTE's adviser if available, otherwise load-balanced) ─
            if student_id:
                try:
                    self._auto_assign_adviser(student_id, hte_id=hte_id)
                except Exception as ae:
                    print(f"[assign] Warning: auto adviser assignment failed: {ae}")

            return {'success': True, 'plan': updated_plan, 'hte': hte}
        except Exception as e:
            print(f"Error assigning student to HTE: {e}")
            return {'success': False, 'error': str(e)}

    # ──────────────────────────────────────────────────────────────────────────
    # Plan Template Methods  (coordinator-created internship plan templates)
    # ──────────────────────────────────────────────────────────────────────────

    def get_plan_templates(self) -> List[Dict]:
        """Get all coordinator-created plan templates."""
        try:
            if self.db:
                templates = []
                for doc in self.db.collection('plan_templates').order_by(
                    'createdAt', direction='DESCENDING' if False else 'ASCENDING'
                ).get():
                    d = doc.to_dict()
                    d['id'] = doc.id
                    templates.append(d)
                return templates
            if not hasattr(self, '_plan_templates_db'):
                self._plan_templates_db = {}
            return sorted(self._plan_templates_db.values(),
                          key=lambda x: x.get('createdAt', ''))
        except Exception as e:
            # Fallback without ordering if index missing
            try:
                if self.db:
                    templates = []
                    for doc in self.db.collection('plan_templates').get():
                        d = doc.to_dict()
                        d['id'] = doc.id
                        templates.append(d)
                    return templates
            except Exception:
                pass
            print(f"Error getting plan templates: {e}")
            return []

    def get_plan_template(self, template_id: str) -> Optional[Dict]:
        """Get a single plan template by ID."""
        try:
            if self.db:
                doc = self.db.collection('plan_templates').document(template_id).get()
                if doc.exists:
                    d = doc.to_dict()
                    d['id'] = doc.id
                    return d
                return None
            if not hasattr(self, '_plan_templates_db'):
                self._plan_templates_db = {}
            return self._plan_templates_db.get(template_id)
        except Exception as e:
            print(f"Error getting plan template: {e}")
            return None

    def create_plan_template(self, template: Dict) -> Dict:
        """Create a new plan template."""
        try:
            template['createdAt'] = datetime.now().isoformat()
            template['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('plan_templates').add(template)
                template['id'] = doc_ref[1].id
                return {'success': True, 'template': template}
            if not hasattr(self, '_plan_templates_db'):
                self._plan_templates_db = {}
            import uuid
            tid = str(uuid.uuid4())
            template['id'] = tid
            self._plan_templates_db[tid] = template
            return {'success': True, 'template': template}
        except Exception as e:
            print(f"Error creating plan template: {e}")
            return {'success': False, 'error': str(e)}

    def update_plan_template(self, template_id: str, update_data: Dict) -> Dict:
        """Update a plan template."""
        try:
            update_data['updatedAt'] = datetime.now().isoformat()
            if self.db:
                doc_ref = self.db.collection('plan_templates').document(template_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Template not found'}
                doc_ref.update(update_data)
                updated = doc_ref.get().to_dict()
                updated['id'] = template_id
                return {'success': True, 'template': updated}
            if not hasattr(self, '_plan_templates_db') or template_id not in self._plan_templates_db:
                return {'success': False, 'error': 'Template not found'}
            self._plan_templates_db[template_id].update(update_data)
            return {'success': True, 'template': self._plan_templates_db[template_id]}
        except Exception as e:
            print(f"Error updating plan template: {e}")
            return {'success': False, 'error': str(e)}

    def delete_plan_template(self, template_id: str) -> Dict:
        """Delete a plan template."""
        try:
            if self.db:
                doc_ref = self.db.collection('plan_templates').document(template_id)
                if not doc_ref.get().exists:
                    return {'success': False, 'error': 'Template not found'}
                doc_ref.delete()
                return {'success': True}
            if not hasattr(self, '_plan_templates_db') or template_id not in self._plan_templates_db:
                return {'success': False, 'error': 'Template not found'}
            del self._plan_templates_db[template_id]
            return {'success': True}
        except Exception as e:
            print(f"Error deleting plan template: {e}")
            return {'success': False, 'error': str(e)}
