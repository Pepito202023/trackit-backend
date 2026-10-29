"""
Helper script to generate secure environment variables for Cloud Run deployment.
Run this locally to generate random secrets and prepare your Firebase JSON.
"""

import secrets
import json
import os

def generate_secret():
    """Generate a secure random secret for production."""
    return secrets.token_urlsafe(32)

def read_firebase_json():
    """Read Firebase service account key and return as single-line JSON."""
    # Try multiple possible locations
    possible_paths = [
        'serviceAccountKey.json',
        'services/login-system-292a0-firebase-adminsdk-fbsvc-e84bb03b25.json',
        '../serviceAccountKey.json',
    ]

    for path in possible_paths:
        if os.path.exists(path):
            with open(path, 'r') as f:
                return json.dumps(json.load(f), separators=(',', ':'))

    return None

def main():
    print("=" * 70)
    print("TrackIt Backend - Cloud Run Environment Variables Generator")
    print("=" * 70)
    print()

    # Generate secrets
    print("Generated Secrets:")
    print("-" * 70)
    secret_key = generate_secret()
    jwt_secret = generate_secret()

    print(f"SECRET_KEY={secret_key}")
    print(f"JWT_SECRET_KEY={jwt_secret}")
    print()

    # Read Firebase JSON
    print("Firebase Service Account JSON:")
    print("-" * 70)
    firebase_json = read_firebase_json()
    if firebase_json:
        print("[OK] Found Firebase service account key")
        print(f"FIREBASE_SERVICE_ACCOUNT_JSON={firebase_json[:100]}...")
        print()
        print("Full FIREBASE_SERVICE_ACCOUNT_JSON value (copy this):")
        print("-" * 70)
        print(firebase_json)
    else:
        print("[ERROR] Could not find Firebase service account key")
        print("   Looking for: serviceAccountKey.json or services/*.json")
        print()
        print("To manually generate:")
        print("1. Go to Firebase Console -> Project Settings -> Service Accounts")
        print("2. Click 'Generate new private key'")
        print("3. Download the JSON file")
        print("4. Run: (Get-Content 'path/to/key.json' -Raw).Replace('`r`n','').Replace('`n','')")
    print()

    # Environment variables summary
    print("=" * 70)
    print("Environment Variables for Cloud Run")
    print("=" * 70)
    print()
    print("Add these to your Cloud Run service:")
    print()
    print("1. SECRET_KEY")
    print(f"   Value: {secret_key}")
    print()
    print("2. JWT_SECRET_KEY")
    print(f"   Value: {jwt_secret}")
    print()
    print("3. GOOGLE_CLIENT_ID")
    print("   Value: 801818179102-k8urb95atjb6rsqm8mtpgnpsl7qq2tba.apps.googleusercontent.com")
    print()
    print("4. TRACKIT_WEB_URL")
    print("   Value: https://webadmin-lac.vercel.app")
    print()
    print("5. FIREBASE_STORAGE_BUCKET")
    print("   Value: login-system-292a0.appspot.com")
    print()
    print("6. FIREBASE_SERVICE_ACCOUNT_JSON")
    if firebase_json:
        print(f"   Value: {firebase_json[:100]}... (see full value above)")
    else:
        print("   Value: [Paste your Firebase JSON as single line]")
    print()
    print("7. TRACKIT_EMAIL_FROM")
    print("   Value: alexanderpepitojr6@gmail.com")
    print()
    print("8. TRACKIT_EMAIL_PASSWORD")
    print("   Value: fufczklbmnpaulyz")
    print()
    print("=" * 70)
    print("IMPORTANT SECURITY NOTES:")
    print("=" * 70)
    print()
    print("• Never commit these values to Git")
    print("• Store secrets in Cloud Run environment variables only")
    print("• Rotate secrets if compromised")
    print("• Keep your Firebase service account key secure")
    print("• Email password is a Gmail App Password (not your regular password)")
    print()

if __name__ == '__main__':
    main()
