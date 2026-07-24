import requests, json

r = requests.post('http://localhost:8080/api/v1/auth/login',
                  json={'username': 'admin', 'password': 'admin123'})
t = r.json()['access_token']
h = {'Authorization': f'Bearer {t}'}

# PT RSS plugin instances
r2 = requests.get('http://localhost:8080/api/v1/plugins/1/instances', headers=h)
if r2.status_code == 200:
    insts = r2.json()
    for i in insts:
        cfg = i.get('config', {})
        print(f"inst id={i['id']} plugin={i['plugin_id']} name={i.get('name','?')} "
              f"enabled={i['enabled']} "
              f"cron='{cfg.get('schedule_cron','N/A')}' "
              f"sched_en={cfg.get('schedule_enabled', False)}")
else:
    print(f"GET instances failed: {r2.status_code} {r2.text[:200]}")

# Also check all plugins to find PT RSS
r3 = requests.get('http://localhost:8080/api/v1/plugins', headers=h)
if r3.status_code == 200:
    plugins = r3.json()
    for p in plugins:
        print(f"plugin id={p['id']} slug={p['slug']} name={p['name']}")
