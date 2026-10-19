"""
Seed Authorized Coordinator Gmail Addresses
============================================
Run this script once to populate the 'authorized_coordinators' collection
in Firestore with Gmail addresses that are allowed to self-register as
Coordinators through the Track-It Web Admin registration form.

Usage:
    cd backend
    python scripts/seed_authorized_coordinators.py

Edit the AUTHORIZED_EMAILS list below before running.
"""

import sys
import os
from datetime import datetime

# Allow imports from the backend root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env so Firebase credentials are available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))
except ImportError:
    pass

from services.firebase_service import FirebaseService

# ─────────────────────────────────────────────────────────────────────────────
# ADD AUTHORIZED COORDINATOR GMAIL ADDRESSES HERE
# Only these addresses will be allowed to register via the web admin form.
# ─────────────────────────────────────────────────────────────────────────────
AUTHORIZED_EMAILS = [
    # "coordinator@gmail.com",
    # "ojt.coordinator@gmail.com",
]
# ─────────────────────────────────────────────────────────────────────────────


def add_email(db, email: str):
    """Add a single email to authorized_coordinators if not already present."""
    email = email.strip().lower()
    if not email or not email.endswith('@gmail.com'):
        print(f"  [SKIP] {email!r} — not a valid Gmail address")
        return

    col = db.collection('authorized_coordinators')
    existing = list(col.where('email', '==', email).limit(1).stream())
    if existing:
        print(f"  [SKIP] {email} — already in authorized_coordinators")
        return

    col.add({
        'email': email,
        'addedAt': datetime.utcnow().isoformat(),
        'addedBy': 'seed_script',
    })
    print(f"  [ADD]  {email}")


def remove_email(db, email: str):
    """Remove an email from authorized_coordinators."""
    email = email.strip().lower()
    col = db.collection('authorized_coordinators')
    docs = list(col.where('email', '==', email).stream())
    if not docs:
        print(f"  [SKIP] {email} — not found")
        return
    for doc in docs:
        doc.reference.delete()
    print(f"  [DEL]  {email}")


def list_emails(db):
    """Print all currently authorized emails."""
    docs = list(db.collection('authorized_coordinators').stream())
    if not docs:
        print("  (no authorized coordinators)")
        return
    for doc in docs:
        d = doc.to_dict()
        print(f"  - {d.get('email')}  (added: {d.get('addedAt', 'unknown')})")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Manage authorized coordinator Gmail addresses')
    parser.add_argument('--list', action='store_true', help='List all authorized emails')
    parser.add_argument('--add', metavar='EMAIL', help='Add a single email')
    parser.add_argument('--remove', metavar='EMAIL', help='Remove a single email')
    args = parser.parse_args()

    print("[*] Connecting to Firebase...")
    svc = FirebaseService()
    if not svc.db:
        print("[ERROR] Firestore not connected. Check your service account credentials.")
        sys.exit(1)

    if args.list:
        print("[*] Authorized coordinator emails:")
        list_emails(svc.db)
        return

    if args.add:
        print(f"[*] Adding {args.add!r}...")
        add_email(svc.db, args.add)
        return

    if args.remove:
        print(f"[*] Removing {args.remove!r}...")
        remove_email(svc.db, args.remove)
        return

    # Default: seed from AUTHORIZED_EMAILS list above
    if not AUTHORIZED_EMAILS:
        print("[WARN] AUTHORIZED_EMAILS list is empty.")
        print("       Edit the list in this script and re-run, or use:")
        print("         python scripts/seed_authorized_coordinators.py --add your@gmail.com")
        return

    print(f"[*] Seeding {len(AUTHORIZED_EMAILS)} email(s) into authorized_coordinators...")
    for email in AUTHORIZED_EMAILS:
        add_email(svc.db, email)

    print("\n[*] Current authorized coordinator emails:")
    list_emails(svc.db)
    print("\nDone.")


if __name__ == '__main__':
    main()
