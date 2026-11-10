"""
TRACK-IT: Migrate existing Firestore user accounts to Firebase Auth.

For every `users` document that has an email but no `firebaseUid`, this script:
  1. Creates (or links) a Firebase Auth account with a random unknown password.
  2. Stores the returned UID as `firebaseUid` on the Firestore profile.
  3. Optionally emails each user a one-time setup link so they can set a
     password — WITHOUT changing their role, ID number, or any other field.

Existing passwords are NOT touched: the Firestore password hash remains valid
for the mobile app, and once the user sets a password via the setup link both
stores stay in sync (set_password_for_email syncs Firebase).

Usage:
    python scripts/migrate_firebase_auth.py            # dry-run report only
    python scripts/migrate_firebase_auth.py --apply    # create/link Auth users
    python scripts/migrate_firebase_auth.py --apply --notify   # + send setup emails
"""

import os
import sys
import secrets
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.firebase_service import FirebaseService  # noqa: E402
from services.email_service import WEB_BASE_URL, send_account_setup_email  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Actually create Auth users')
    parser.add_argument('--notify', action='store_true', help='Email setup links (requires --apply)')
    args = parser.parse_args()

    svc = FirebaseService()
    if not svc.db:
        print('[MIGRATE] Firestore not available — aborting')
        sys.exit(1)

    docs = list(svc.db.collection('users').stream())
    print(f'[MIGRATE] Scanning {len(docs)} user profiles...')

    migrated, linked, skipped, failed = [], [], [], []

    for doc in docs:
        data = doc.to_dict() or {}
        email = (data.get('email') or '').strip().lower()
        name = data.get('name') or doc.id
        role = data.get('role') or '-'

        if not email:
            skipped.append((name, role, 'no email'))
            continue
        if data.get('firebaseUid'):
            skipped.append((name, role, 'already has firebaseUid'))
            continue

        if not args.apply:
            migrated.append((name, email, role, 'would provision'))
            continue

        # Random password the user never sees — they set their own via link.
        random_pw = secrets.token_urlsafe(24)
        uid = svc.ensure_firebase_auth_user(email, random_pw, name)
        if not uid:
            failed.append((name, email, role, 'Firebase Auth provisioning failed'))
            continue

        try:
            svc.db.collection('users').document(doc.id).update({'firebaseUid': uid})
        except Exception as e:
            failed.append((name, email, role, f'firebaseUid write failed: {e}'))
            continue

        (linked if data.get('firebaseUid') else migrated).append((name, email, role, uid))

        if args.notify:
            try:
                token = svc.create_password_reset_token(email)
                if token:
                    link = f"{WEB_BASE_URL}/reset_password.html?token={token}"
                    ok = send_account_setup_email(email, name, role, data.get('idNumber') or '', link)
                    print(f'[MIGRATE] Setup email to {email}: {"OK" if ok else "FAILED"}')
            except Exception as e:
                print(f'[MIGRATE] Setup email to {email} failed: {e}')

    print('\n=== MIGRATION REPORT ===')
    print(f'Provisioned: {len(migrated)}')
    for name, email, role, uid in migrated:
        print(f'  + {name} <{email}> ({role}) -> {uid}')
    print(f'Already linked: {len(linked)}')
    print(f'Skipped: {len(skipped)}')
    for name, role, why in skipped:
        print(f'  - {name} ({role}): {why}')
    print(f'Failed: {len(failed)}')
    for name, email, role, why in failed:
        print(f'  ! {name} <{email}> ({role}): {why}')
    if not args.apply:
        print('\n(dry-run — rerun with --apply to provision Firebase Auth accounts)')


if __name__ == '__main__':
    main()
