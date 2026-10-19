"""Diagnostic: check Google OAuth config consistency"""
import sys, os, re
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')

client_id = os.environ.get('GOOGLE_CLIENT_ID', 'NOT SET')
print('=== Backend GOOGLE_CLIENT_ID ===')
print(client_id)

with open('../web_admin/login.html', 'r', encoding='utf-8') as f:
    content = f.read()

matches = re.findall(r'data-client_id=["\']([^"\']+)["\']', content)
print('\n=== login.html data-client_id ===')
for m in matches:
    print(m)
    print('MATCH:', m == client_id)

print('\n=== Checking Google tokeninfo for audience ===')
print('The aud field in the token must exactly equal GOOGLE_CLIENT_ID')
print('If they differ, backend returns 401 "audience mismatch"')

# Check if 127.0.0.1:5502 is in Google Cloud Console authorized origins
# (we can't check this directly, but we can note the requirement)
print('\n=== OAuth Origins Checklist ===')
print('Google Cloud Console must have these Authorized JavaScript Origins:')
print('  http://127.0.0.1:5502')
print('  http://localhost:5502')
print('If missing, Google will reject the sign-in before we even get a token')

# Check the actual error path in auth_routes
with open('routes/auth_routes.py', 'r', encoding='utf-8') as f:
    routes = f.read()

# Find what error message is sent when audience mismatches
aud_section = routes[routes.find('aud ='):routes.find('aud =')+300]
print('\n=== Audience check code ===')
print(aud_section)
