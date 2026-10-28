"""
One-time fix: Update the Firestore user record for alexanderpepitojr6@gmail.com
so the name is "Alexander Pepito" instead of a placeholder like "Coordinator".

Run from the backend/ folder:
    python fix_coordinator_name.py
"""
import sys
import os

# Make sure we can import the app's services
sys.path.insert(0, os.path.dirname(__file__))

# Load .env
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

from services.firebase_service import FirebaseService

TARGET_EMAIL = 'alexanderpepitojr6@gmail.com'
CORRECT_NAME = 'Alexander Pepito'

print(f"[FIX] Connecting to Firebase...")
svc = FirebaseService()

if not svc.db:
    print("[ERROR] Could not connect to Firestore. Check serviceAccountKey.json exists.")
    sys.exit(1)

print(f"[FIX] Looking up users with email: {TARGET_EMAIL}")
users_ref = svc.db.collection('users')
docs = users_ref.where('email', '==', TARGET_EMAIL).stream()

found = 0
for doc in docs:
    data = doc.to_dict()
    current_name = data.get('name', '')
    print(f"  Found doc id={doc.id!r}  name={current_name!r}  role={data.get('role')!r}")
    if current_name != CORRECT_NAME:
        doc.reference.update({'name': CORRECT_NAME})
        print(f"  [OK] Updated name: {current_name!r} → {CORRECT_NAME!r}")
    else:
        print(f"  [OK] Name is already correct, no change needed.")
    found += 1

if found == 0:
    # Try case-insensitive search by fetching all and filtering
    print(f"[FIX] Exact query returned 0 results. Scanning all users...")
    all_docs = svc.db.collection('users').stream()
    for doc in all_docs:
        data = doc.to_dict()
        stored_email = (data.get('email') or '').strip().lower()
        if stored_email == TARGET_EMAIL.lower():
            current_name = data.get('name', '')
            print(f"  Found doc id={doc.id!r}  name={current_name!r}  role={data.get('role')!r}")
            if current_name != CORRECT_NAME:
                doc.reference.update({'name': CORRECT_NAME})
                print(f"  [OK] Updated name: {current_name!r} → {CORRECT_NAME!r}")
            else:
                print(f"  [OK] Name is already correct.")
            found += 1

if found == 0:
    print(f"[ERROR] No user found with email {TARGET_EMAIL!r}. Check Firestore manually.")
else:
    print(f"\n[DONE] Fixed {found} record(s). Restart the backend and log in again.")
