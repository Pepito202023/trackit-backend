"""
One-time migration script: update legacy roles in Firestore.
  admin    → coordinator
  employee → adviser

Run once from the backend folder:
    python migrate_roles.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from services.firebase_service import FirebaseService

ROLE_MAP = {
    'admin': 'coordinator',
    'employee': 'adviser',
}

def migrate():
    svc = FirebaseService()
    if not svc.db:
        print("[ERROR] Firestore not connected. Check your service account key.")
        return

    users_ref = svc.db.collection('users')
    docs = users_ref.get()
    updated = 0

    for doc in docs:
        data = doc.to_dict()
        old_role = data.get('role', '')
        new_role = ROLE_MAP.get(old_role)
        if new_role:
            doc.reference.update({'role': new_role})
            print(f"  Updated {data.get('email', doc.id)}: {old_role} → {new_role}")
            updated += 1

    print(f"\n[DONE] {updated} user(s) migrated.")

if __name__ == '__main__':
    migrate()
