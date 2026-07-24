import requests, json

r = requests.post('http://localhost:8080/api/v1/auth/login',
                  json={'username': 'admin', 'password': 'admin123'})
t = r.json()['access_token']
h = {'Authorization': f'Bearer {t}'}

# Run alist_upload plugin (plugin id=2)
r2 = requests.post('http://localhost:8080/api/v1/plugins/2/run', headers=h)
print('STATUS:', r2.status_code)
print('RESULT:', json.dumps(r2.json(), ensure_ascii=False, indent=2))
