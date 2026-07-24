"""Check duplicate log lines in the actual log file."""
import requests

r = requests.post('http://localhost:8080/api/v1/auth/login',
                  json={'username': 'admin', 'password': 'admin123'})
t = r.json()['access_token']

r2 = requests.get('http://localhost:8080/api/v1/system/logs/raw?limit=100000',
                  headers={'Authorization': f'Bearer {t}'})
lines = r2.text.splitlines()
print(f"Total lines in log file: {len(lines)}")

# Count exact duplicates of each message
from collections import Counter
c = Counter()
for line in lines:
    # Extract the message part after "—"
    if ' — ' in line:
        msg = line.split(' — ', 1)[1]
    else:
        msg = line
    c[msg] += 1

print("\n=== Most duplicated messages ===")
for msg, cnt in c.most_common(15):
    if cnt > 1:
        print(f"{cnt}x | {msg[:120]}")

# Specifically look at 15:00 section
print("\n=== Lines at 15:00 ===")
for l in lines:
    if '15:00:0' in l:
        print(l[:150])
