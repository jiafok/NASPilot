"""Check docker_backup plugin instance config and test dir structure."""
import requests, json

r = requests.post('http://localhost:8080/api/v1/auth/login',
                  json={'username': 'admin', 'password': 'admin123'})
t = r.json()['access_token']
h = {'Authorization': f'Bearer {t}'}

# Get docker_backup plugin instance config
r2 = requests.get('http://localhost:8080/api/v1/plugins/4/instances', headers=h)
if r2.status_code == 200:
    insts = r2.json()
    for i in insts:
        cfg = i.get('config', {})
        print(f"Instance id={i['id']} enabled={i['enabled']}")
        print(f"  docker_root = '{cfg.get('docker_root', 'NOT SET')}'")
        print(f"  backup_dir  = '{cfg.get('backup_dir', 'NOT SET')}'")
        print(f"  containers  = {cfg.get('containers', 'NOT SET')}")
        print(f"  keep_days   = {cfg.get('keep_days', 'NOT SET')}")

# Check what files exist in the data dir (mounted as /app/data)
r3 = requests.get('http://localhost:8080/api/v1/system/logs/raw?limit=50',
                  headers=h)
print("\n=== Recent logs (docker_backup related) ===")
for line in r3.text.splitlines():
    if 'docker_backup' in line.lower() or 'backup' in line.lower() or 'Skipping' in line or 'Collecting' in line or 'Backing up' in line:
        print(line[:200])
