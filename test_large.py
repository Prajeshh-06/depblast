import urllib.request
import json
import time
import os

lock_path = r'C:\Users\mrrob\Documents\AntiGrav\CYSCOM\depblast\test\package-lock.json'

if not os.path.exists(lock_path):
    print('ERROR: File not found at', lock_path)
    exit(1)

with open(lock_path, 'rb') as f:
    data = f.read()

pkg_count = len(json.loads(data).get('packages', {}))
print(f'Package count in lockfile: {pkg_count}')
print(f'File size: {len(data)/1024:.0f} KB')

boundary = b'----DepBlastBoundary'
body = (
    b'--' + boundary + b'\r\n' +
    b'Content-Disposition: form-data; name="enrich"\r\n\r\nfalse\r\n' +
    b'--' + boundary + b'\r\n' +
    b'Content-Disposition: form-data; name="file"; filename="package-lock.json"\r\n' +
    b'Content-Type: application/json\r\n\r\n' + data + b'\r\n' +
    b'--' + boundary + b'--\r\n'
)

req = urllib.request.Request(
    'http://127.0.0.1:5000/analyze',
    data=body,
    method='POST',
    headers={'Content-Type': 'multipart/form-data; boundary=----DepBlastBoundary'}
)

print('Sending request...')
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        elapsed = time.time() - t0
        result = json.loads(resp.read())
        if 'error' in result:
            print(f'ERROR from server ({elapsed:.1f}s): {result["error"]}')
        else:
            h = result.get('health', {})
            print(f'SUCCESS in {elapsed:.1f}s')
            print(f'  Total deps:   {h.get("total")}')
            print(f'  Direct:       {h.get("direct")}')
            print(f'  Chokepoints:  {h.get("chokepoint_count")}')
            print(f'  Max depth:    {h.get("max_depth")}')
            dist = h.get('risk_distribution', {})
            print(f'  Risk dist:    critical={dist.get("critical")} high={dist.get("high")} medium={dist.get("medium")} low={dist.get("low")}')
            print(f'  Enrich auto-disabled: {result.get("enrich_auto_disabled")}')
except Exception as e:
    elapsed = time.time() - t0
    print(f'FAILED after {elapsed:.1f}s: {e}')
