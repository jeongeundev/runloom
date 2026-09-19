import hashlib, json, subprocess
from pathlib import Path
from probe import docker
root=Path(__file__).parent
raw=(root/'frontend/cases.json').read_bytes()
p=root/'backend/shared-context.json'
original=p.read_bytes()
data=json.loads(original)
assert data['cases']==json.loads(raw)
assert data['sha256']==hashlib.sha256(raw).hexdigest()
assert not (root/'backend/view.mjs').exists()
try:
 bad=json.loads(original)
 bad['cases'][0]['input']['total']=999
 p.write_text(json.dumps(bad))
 r=subprocess.run(docker('backend',['node','--test','compatibility.test.mjs']),capture_output=True,text=True)
 assert r.returncode!=0,'compatibility tests must reject inconsistent received data'
 (root/'shared-negative-tests.txt').write_text(r.stdout+r.stderr)
finally:
 p.write_bytes(original)
r=subprocess.run(docker('backend',['node','--test','producer.test.mjs','compatibility.test.mjs']),capture_output=True,text=True)
assert r.returncode==0,r.stdout+r.stderr
(root/'shared-restored-tests.txt').write_text(r.stdout+r.stderr)
(root/'shared-verification.json').write_text(json.dumps({'fixture_digest_matches':True,'cases_match_frontend_bytes':True,'backend_does_not_contain_frontend_source':True,'tampered_total_rejected':True,'restored_tests_exit':r.returncode},indent=2))
print('Shared content/digest verified; wrong total rejected; restored 6 tests pass')
