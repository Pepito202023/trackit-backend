"""
cleanup_invalid_assignments.py
────────────────────────────────────────────────────────────────────────────────
One-shot maintenance script: clears adviser/HTE assignment fields from student
Firestore documents that have no corresponding 'assigned' application plan.

Run from the backend/ directory (or any directory — sys.path is patched):

    python scripts/cleanup_invalid_assignments.py
    python scripts/cleanup_invalid_assignments.py --yes    # skip prompt

Requirements:
  - serviceAccountKey.json in backend/, OR
  - GOOGLE_APPLICATION_CREDENTIALS env var pointing to the credentials file.
  - firebase-admin and google-cloud-firestore packages (already in requirements.txt).

Safety:
  - Only clears fields from the 'users' collection.
  - Does NOT touch attendance, OJT hours, task, evaluation, or plan documents.
  - Prints a preview of every affected student before writing.
  - Prompts for confirmation unless --yes / -y is passed.
"""

import argparse
import os
import sys

# Allow imports from backend/ root (firebase_service, etc.) when running the
# script from any working directory.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1 import DELETE_FIELD

# ── Argument parsing ──────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(
    description='Clear invalid adviser/HTE assignments from student records.'
)
parser.add_argument(
    '--yes', '-y',
    action='store_true',
    help='Skip the confirmation prompt (for non-interactive/CI environments)',
)
args = parser.parse_args()

# ── Initialise Firebase ───────────────────────────────────────────────────────
cred_path = os.environ.get(
    'GOOGLE_APPLICATION_CREDENTIALS',
    os.path.join(os.path.dirname(__file__), '..', 'serviceAccountKey.json'),
)
cred_path = os.path.abspath(cred_path)

if not os.path.exists(cred_path):
    print(f"ERROR: Credentials file not found: {cred_path!r}")
    print(
        "Set GOOGLE_APPLICATION_CREDENTIALS or place serviceAccountKey.json "
        "in the backend/ directory."
    )
    sys.exit(1)

if not firebase_admin._apps:
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)

db = firestore.client()

# Assignment fields that should only exist when a student has an 'assigned' plan
ASSIGNMENT_FIELDS = [
    'adviserId', 'adviserName',
    'supervisorId', 'supervisorName',
    'companyId', 'companyName', 'company',
]

# ── Scan students ─────────────────────────────────────────────────────────────
print("Scanning student documents …")
students = list(db.collection('users').where('role', '==', 'student').stream())
print(f"  Found {len(students)} student document(s).")

to_fix = []
for doc in students:
    data = doc.to_dict()
    populated = {
        f: data[f]
        for f in ASSIGNMENT_FIELDS
        if data.get(f) and str(data.get(f, '')).strip()
    }
    if not populated:
        continue  # nothing to clear

    # Check if this student has at least one 'assigned' plan
    assigned_plans = list(
        db.collection('application_plans')
        .where('studentId', '==', doc.id)
        .where('status', '==', 'assigned')
        .limit(1)
        .stream()
    )
    if not assigned_plans:
        to_fix.append({
            'id': doc.id,
            'name': data.get('name', '(no name)'),
            'fields': populated,
        })

if not to_fix:
    print("\nNo students with invalid assignments found. Database is clean.")
    sys.exit(0)

print(f"\nStudents with invalid assignment fields ({len(to_fix)}):")
for item in to_fix:
    print(f"  {item['name']!r} ({item['id']}): {list(item['fields'].keys())}")

# ── Confirm before writing ────────────────────────────────────────────────────
if not args.yes:
    try:
        confirm = input(
            f"\nProceed to clear these fields for {len(to_fix)} student(s)? (y/n): "
        ).strip().lower()
    except EOFError:
        print("\nEOF received on stdin. Re-run with --yes / -y for non-interactive mode.")
        sys.exit(1)
    if confirm != 'y':
        print("Aborted — no changes made.")
        sys.exit(0)

# ── Apply cleanup ─────────────────────────────────────────────────────────────
print()
errors = 0
for item in to_fix:
    update = {f: DELETE_FIELD for f in item['fields']}
    try:
        db.collection('users').document(item['id']).update(update)
        print(f"  ✓ Cleared {list(item['fields'].keys())} for {item['name']!r} ({item['id']})")
    except Exception as e:
        print(f"  ✗ ERROR clearing {item['name']!r} ({item['id']}): {e}")
        errors += 1

if errors:
    print(f"\nDone with {errors} error(s). Review output above.")
    sys.exit(1)
else:
    print("\nDone. All invalid assignments cleared successfully.")
