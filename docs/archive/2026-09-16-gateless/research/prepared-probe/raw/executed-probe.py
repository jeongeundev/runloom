import asyncio, hashlib, json, os, secrets, subprocess, time
from pathlib import Path
import httpx, uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from a2a.server.apps import A2AStarletteApplication
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCard, AgentCapabilities, AgentSkill, Part, DataPart, TaskState
from a2a.utils import new_task

ROOT=Path(__file__).parent
IMAGE='gateless-feasibility:codex-0.154.0'
AUTH=Path('/Users/kje/.codex/auth.json')
TOKEN=secrets.token_urlsafe(32)
RUNS=[]

def docker(owner, args, name=None):
    result=['docker','run','--rm','--user','501:20','--cap-drop','ALL','--security-opt','no-new-privileges','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,mode=1777','--tmpfs','/home/node:rw,nosuid,nodev,uid=501,gid=20,mode=700','--mount',f'type=bind,src={AUTH},dst=/home/node/auth.json,readonly','--mount',f'type=bind,src={ROOT/owner},dst=/workspace','--env','CODEX_HOME=/home/node','--env','HOME=/home/node']
    if name: result+=['--name',name]
    return result+[IMAGE]+args

async def run(cmd):
    proc=await asyncio.create_subprocess_exec(*cmd,stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,err=await proc.communicate()
    return proc.returncode,out.decode(),err.decode()

BOUNDARY=r'''
const fs=require('fs');
(async()=>{const own=fs.readFileSync('/workspace/owner-marker.txt','utf8').trim();
const forbidden=['/workspace/../frontend/owner-marker.txt','/workspace/../backend/owner-marker.txt','/private/tmp/gateless-feasibility/frontend/owner-marker.txt','/private/tmp/gateless-feasibility/backend/owner-marker.txt','/Users/kje/.config/gh/hosts.yml','/var/run/docker.sock','/workspace/.git/config'];
const reads=Object.fromEntries(forbidden.map(p=>{try{fs.readFileSync(p);return [p,'UNEXPECTED_READ']}catch(e){return [p,e.code]}}));
let crossWrite;try{fs.writeFileSync('/other-owner.txt','bad');crossWrite='UNEXPECTED_WRITE'}catch(e){crossWrite=e.code}
fs.writeFileSync('/workspace/write-probe.txt','own workspace writable');
const response=await fetch('https://api.github.com/repos/jeongeundev/harness-framework',{headers:{'User-Agent':'gateless-isolation-probe'}});
console.log(JSON.stringify({own,reads,crossWrite,githubPrivateWithoutCredential:response.status,githubCredentialPresent:!!(process.env.GH_TOKEN||process.env.GITHUB_TOKEN)}));
if(Object.values(reads).includes('UNEXPECTED_READ')||crossWrite==='UNEXPECTED_WRITE'||response.status!==404)process.exitCode=1;
})();
'''
PROMPTS={
'prepare':'''You are the Backend coding Agent in a disposable feasibility experiment. Work only in /workspace. Actually edit api.mjs so summarize returns {subtotal,shippingFee,total} with total null when shippingFee is null, else subtotal+shippingFee. Run node --test producer.test.mjs and report real results. Write request.json with original_work_id B1, request_id R1, target frontend, goal to fix consumer rendering for paid/free/pending shipping, result_conditions requiring passing consumer tests and actual JSON cases. This is a one-hop request proposal only; do not contact the other Agent or read other workspaces. Finally output JSON with kind=request, summary describing work. Do not spawn agents.''',
'consumer':'''You are the Frontend coding Agent. Work only in /workspace. Read contract.json supplied by the coordinator; do not access Backend repo. Actually fix view.mjs render: null shippingFee or total must display 'pending', numeric zero displays '0', other numbers String. Create cases.json containing exactly three cases with input and expected_display: subtotal 100 shippingFee 10 total 110; subtotal 100 shippingFee 0 total 100; subtotal 100 shippingFee null total null. expected_display has shipping and total strings. Create consumer.test.mjs that loads cases.json and tests render(input) using node:test and strict assertions. Run node --test consumer.test.mjs. Write result.json with kind=result and summary. Do not generate fake test success. Do not spawn agents. Final output JSON kind=result and summary.''',
'resume':'''You are the Backend coding Agent resuming original Work B1 in a NEW process. Only your /workspace is available. The coordinator supplied shared-context.json from Frontend via A2A; no Frontend repo access is needed or allowed. Read that file, create compatibility.test.mjs using node:test and strict assertions: for EACH received case, call summarize(input.subtotal,input.shippingFee) and compare its output with that received input object. Assert null vs zero is preserved. Actually run node --test producer.test.mjs compatibility.test.mjs. Write compatibility-report.md recording actual case count and outcomes and shared data provenance/digest from the file. Do not edit api.mjs or shared-context.json merely to force success. Final JSON kind=continued and summary. Do not spawn agents.'''
}

class Executor(AgentExecutor):
    def __init__(self, owner):self.owner=owner
    async def execute(self, context, event_queue):
        payload=json.loads(context.get_user_input())
        phase=payload['phase']
        if phase not in (['prepare','resume'] if self.owner=='backend' else ['consumer']):raise ValueError('owner scope mismatch')
        task=context.current_task or new_task(context.message)
        if not context.current_task:await event_queue.enqueue_event(task)
        update=TaskUpdater(event_queue,task.id,task.context_id)
        await update.update_status(TaskState.working)
        correlation={k:payload[k] for k in ('original_work_id','request_id','execution_id')}
        if phase=='consumer':(ROOT/'frontend'/'contract.json').write_text(json.dumps(payload['shared']))
        if phase=='resume':(ROOT/'backend'/'shared-context.json').write_text(json.dumps(payload['shared']))
        start=time.time()
        code,out,err=await run(docker(self.owner,['codex','exec','--ignore-user-config','--ephemeral','--skip-git-repo-check','--sandbox','danger-full-access','--disable','apps','--disable','plugins','--disable','remote_plugin','--disable','browser_use','--disable','computer_use','--disable','unbounded_connection_retries','--json',PROMPTS[phase]],'gateless-probe-'+phase))
        (ROOT/f'{phase}-cli.jsonl').write_text(out)
        (ROOT/f'{phase}-stderr.txt').write_text(err)
        checks=['node','--test']+({'prepare':['producer.test.mjs'],'consumer':['consumer.test.mjs'],'resume':['producer.test.mjs','compatibility.test.mjs']}[phase])
        testcode,testout,testerr=await run(docker(self.owner,checks))
        (ROOT/f'{phase}-tests.txt').write_text(testout+testerr)
        data={'correlation':correlation,'owner':self.owner,'phase':phase,'cli_exit':code,'test_exit':testcode,'task_id':task.id,'context_id':task.context_id,'elapsed_seconds':round(time.time()-start,2)}
        if phase=='prepare':data['request']=json.loads((ROOT/'backend'/'request.json').read_text())
        if phase=='consumer':
            raw=(ROOT/'frontend'/'cases.json').read_bytes()
            data['shared']={'cases':json.loads(raw),'sha256':hashlib.sha256(raw).hexdigest(),'source':'frontend/cases.json','request_id':'R1'}
        if phase=='resume':data['report']=(ROOT/'backend'/'compatibility-report.md').read_text()
        RUNS.append(data)
        await update.add_artifact([Part(root=DataPart(data=data))],name='correlated-result')
        await update.update_status(TaskState.completed if code==0 and testcode==0 else TaskState.failed,final=True)
    async def cancel(self,context,event_queue):
        raise NotImplementedError('cancel not claimed by this bounded probe')

class Auth(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        if request.headers.get('authorization')!='Bearer '+TOKEN:return JSONResponse({'error':'unauthorized'},status_code=401)
        return await call_next(request)

def app():
    routes=[]
    for owner in ('backend','frontend'):
        card=AgentCard(name=owner,description='Disposable real CLI feasibility endpoint',url=f'http://127.0.0.1:18765/{owner}/',version='0.1.0',protocol_version='0.3.0',capabilities=AgentCapabilities(streaming=False),default_input_modes=['text/plain'],default_output_modes=['application/json'],skills=[AgentSkill(id=owner,name=owner,description='owner scoped execution',tags=['probe'])])
        handler=DefaultRequestHandler(agent_executor=Executor(owner),task_store=InMemoryTaskStore())
        routes.append(Mount('/'+owner,app=A2AStarletteApplication(agent_card=card,http_handler=handler).build()))
    return Starlette(routes=routes,middleware=[__import__('starlette.middleware',fromlist=['Middleware']).Middleware(Auth)])

async def main():
    boundaries={}
    for owner in ('backend','frontend'):
        code,out,err=await run(docker(owner,['node','-e',BOUNDARY]))
        assert code==0,(owner,out,err)
        boundaries[owner]=json.loads(out)
    (ROOT/'boundary-results.json').write_text(json.dumps(boundaries,indent=2))
    print('BOUNDARY PASS both owners',flush=True)
    code,out,err=await run(docker('backend',['node','--test','producer.test.mjs']))
    assert code!=0,'seed must fail pending-shipping test'
    (ROOT/'producer-before.txt').write_text(out+err)
    server=uvicorn.Server(uvicorn.Config(app(),host='127.0.0.1',port=18765,log_level='warning',access_log=False))
    serving=asyncio.create_task(server.serve())
    while not server.started:await asyncio.sleep(.05)
    observations=[]
    try:
        async with httpx.AsyncClient(timeout=600,headers={'Authorization':'Bearer '+TOKEN}) as client:
            denied=await client.get('http://127.0.0.1:18765/backend/.well-known/agent-card.json',headers={'Authorization':'invalid'})
            assert denied.status_code==401
            for owner in ('backend','frontend'):
                card=(await client.get(f'http://127.0.0.1:18765/{owner}/.well-known/agent-card.json')).json()
                assert card['name']==owner
            shared=None
            for i,(owner,phase) in enumerate([('backend','prepare'),('frontend','consumer'),('backend','resume')]):
                payload={'phase':phase,'original_work_id':'B1','request_id':'R1','execution_id':f'E{i+1}'}
                if phase=='consumer':payload['shared']={'version':2,'shippingFee':'number or null','total':'subtotal+shippingFee or null','display':'pending for null, 0 for free','request':RUNS[0]['request']}
                if phase=='resume':payload['shared']=shared
                msg={'jsonrpc':'2.0','id':str(i),'method':'message/send','params':{'message':{'role':'user','messageId':f'probe-{i}','parts':[{'kind':'text','text':json.dumps(payload)}]},'configuration':{'blocking':False}}}
                reply=(await client.post(f'http://127.0.0.1:18765/{owner}/',json=msg)).json()
                assert 'result' in reply,reply
                task=reply['result'];tid=task['id'];states=[task['status']['state']]
                print('A2A SENT',owner,phase,tid,flush=True)
                deadline=time.monotonic()+540
                while task['status']['state'] not in ['completed','failed','rejected','canceled']:
                    assert time.monotonic()<deadline,'task timed out'
                    await asyncio.sleep(1)
                    reply=(await client.post(f'http://127.0.0.1:18765/{owner}/',json={'jsonrpc':'2.0','id':'poll','method':'tasks/get','params':{'id':tid}})).json()
                    task=reply['result']
                    if task['status']['state']!=states[-1]:states.append(task['status']['state'])
                observations.append({'owner':owner,'phase':phase,'task_id':tid,'states':states})
                assert task['status']['state']=='completed',task
                data=task['artifacts'][0]['parts'][0]['data']
                assert data['correlation']=={k:payload[k] for k in ('original_work_id','request_id','execution_id')}
                if phase=='consumer':shared=data['shared']
                print('A2A COMPLETED',phase,'tests',data['test_exit'],flush=True)
            other=(await client.post('http://127.0.0.1:18765/frontend/',json={'jsonrpc':'2.0','id':'isolation','method':'tasks/get','params':{'id':observations[0]['task_id']}})).json()
            assert 'error' in other,other
            summary={'sdk':'a2a-sdk 0.3.25','protocol':'0.3.0','auth_rejection':401,'cross_owner_task_lookup':'not found','observations':observations,'runs':RUNS,'boundaries':boundaries,'note':'Disposable coordinator probe, NOT Gateless product; no GitHub write/PR/check verified.'}
            (ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
            print('PROBE PASS',flush=True)
    finally:
        server.should_exit=True
        await serving

if __name__=='__main__':asyncio.run(main())
