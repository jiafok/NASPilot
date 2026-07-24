"""Run docker_backup and show result + logs."""
import requests, json, time

r = requests.post('http://localhost:8080/api/v1/auth/login',
                  json={'username': 'admin', 'password': 'admin123'})
t = r.json()['access_token']
h = {'Authorization': f'Bearer {t}'}

# Run docker_backup plugin (plugin id=4)
print("Triggering docker_backup run...")
r2 = requests.post('http://localhost:8080/api/v1/plugins/4/run', headers=h, timeout=300)
print('STATUS:', r2.status_code)
print('RESULT:', json.dumps(r2.json(), ensure_ascii=False, indent=2))

# Get recent logs
time.sleep(1)
r3 = requests.get('http://localhost:8080/api/v1/system/logs/raw?limit=300', headers=h)
print("\n=== docker_backup logs ===")
for line in r3.text.splitlines():
    if 'docker_backup' in line.lower() or 'backup' in line.lower() or 'Skipping' in line or 'Collecting' in line or 'Backing up' in line or 'Starting' in line:
        print(line[:200])
