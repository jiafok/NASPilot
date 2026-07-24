"""Fix docker_root config and run backup test."""
import requests, json, time

r = requests.post('http://localhost:8080/api/v1/auth/login',
                  json={'username': 'admin', 'password': 'admin123'})
t = r.json()['access_token']
h = {'Authorization': f'Bearer {t}'}

# Update instance 3 config: docker_root = /app/data (parent of test)
# backup_dir stays /app/data
print("Updating docker_root to /app/data ...")
r2 = requests.put('http://localhost:8080/api/v1/plugins/4/instances/3',
                  headers=h,
                  json={'config': {
                      'docker_root': '/app/data',
                      'backup_dir': '/app/data',
                      'keep_days': 7,
                      'containers': [],
                      'max_app_size_gb': 0,
                      'schedule_enabled': False,
                  }})
print('UPDATE:', r2.status_code, r2.text[:300])

time.sleep(1)

# Run backup
print("\nRunning backup...")
r3 = requests.post('http://localhost:8080/api/v1/plugins/4/run', headers=h, timeout=300)
print('STATUS:', r3.status_code)
print('RESULT:', json.dumps(r3.json(), ensure_ascii=False, indent=2))

time.sleep(1)
# Check archive
r4 = requests.get('http://localhost:8080/api/v1/system/logs/raw?limit=200', headers=h)
print("\n=== backup logs ===")
for line in r4.text.splitlines():
    if any(k in line for k in ['Starting', 'Collecting', 'Skipping', 'Backing up', 'docker_root', 'backup']):
        print(line[:200])
