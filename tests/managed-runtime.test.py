#!/usr/bin/env python3
"""Opt-in real-runtime integration. Only fresh HOME/project, no real credentials.
Requires claude-contained-state-test image with the current guard/entrypoint.
Run: python3 tests/managed-runtime.test.py container|docker
"""
import json
import os
from pathlib import Path
import pty
import select
import signal
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

repo = Path(__file__).resolve().parent.parent
runtime = sys.argv[1]
launcher = 'claude-contained' if runtime == 'container' else 'claude-docked'
root = Path(tempfile.mkdtemp(prefix='cc-managed-' + runtime + '-', dir='/private/tmp'))
home, project, bin_dir = root/'home', root/'project', root/'bin'
for p in (home,project,bin_dir): p.mkdir()
real_runtime = shutil.which(runtime)
# Isolate launcher HOME while retaining access to the user's runtime service.
wrapper = bin_dir/runtime
wrapper.write_text('#!/bin/sh\nexport HOME='+shlex.quote(os.environ['HOME'])+'\nexec '+shlex.quote(real_runtime)+' "$@"\n')
wrapper.chmod(0o755)
shutil.copyfile(repo/launcher,bin_dir/launcher)
for name in ('history_probe.py','mock_model.py'):
    shutil.copyfile(repo/'investigations/codex-history'/name,project/name)
env = dict(os.environ,HOME=str(home),PATH=str(bin_dir)+':'+os.environ['PATH'],
           CLAUDE_CONTAINED_IMAGE='claude-contained-state-test:latest')
env.pop('CC_TOKEN',None)

# Runs as dev through the real guard and the exact managed-home mount.
(project/'exercise.py').write_text(r'''
import json,os,subprocess,sys,threading,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from history_probe import AppServer
from mock_model import MockModel
root=Path.cwd(); home=Path(os.environ['TEST_HOME']); state=home/'.codex'
(root/'home').mkdir(exist_ok=True)
if not (root/'codex').is_symlink(): (root/'codex').symlink_to(state)
runtime=sys.argv[1];phase=sys.argv[2]
if phase=='write':
 (state/'config.toml').write_text('model="probe-model"\nmodel_provider="probe"\ncheck_for_update_on_startup=false\n[model_providers.probe]\nname="Offline"\nbase_url="http://127.0.0.1:1/v1"\nwire_api="responses"\nrequires_openai_auth=false\nrequest_max_retries=0\nstream_max_retries=0\n')
 barrier=threading.Barrier(2) if runtime=='container' else None
 model=MockModel(root,lambda body: (barrier.wait(20),None)[1] if barrier else None)
 expected={}
 try:
  for mode in ('legacy','paginated'):
   a=AppServer(root,mode+'-a',binary='/usr/local/bin/codex')
   if runtime=='docker':
    busy=subprocess.run(['/usr/local/bin/codex','--version'],capture_output=True,text=True)
    assert busy.returncode!=0 and 'only one Codex' in busy.stderr,busy
   servers=[a] if runtime=='docker' else [a,AppServer(root,mode+'-b',binary='/usr/local/bin/codex')]
   try:
    ids=[s.request('thread/start',{'cwd':str(root),'historyMode':mode,'approvalPolicy':'never','sandbox':'read-only'})['result']['thread']['id'] for s in servers]
    def turn(i,n):
     prompt=f'Integration {mode} client {i} turn {n}'
     s=servers[i];response=s.request('turn/start',{'threadId':ids[i],'input':[{'type':'text','text':prompt}]})
     assert 'error' not in response,response
     while True:
      e=s.receive()
      if e.get('method')=='turn/completed':
       assert e['params']['turn']['status']=='completed',e
       return prompt
    with ThreadPoolExecutor(2) as pool:
     for n in range(3):
      futures=[pool.submit(turn,i,n) for i in range(len(servers))]
      for i,f in enumerate(futures): expected.setdefault(ids[i],[]).append(f.result())
   finally:
    for s in servers:s.close()
 finally:model.close()
 (root/'expected.json').write_text(json.dumps(expected))
else:
 expected=json.loads((root/'expected.json').read_text())
 s=AppServer(root,'verify',binary='/usr/local/bin/codex')
 try:
  for tid,prompts in expected.items():
   response=s.request('thread/read',{'threadId':tid,'includeTurns':True})
   turns=response['result']['thread']['turns'];assert len(turns)==len(prompts),response
   for turn,prompt in zip(turns,prompts):
    assert prompt in json.dumps(turn) and 'Synthetic completed turn.' in json.dumps(turn),turn
   assert 'error' not in s.request('thread/resume',{'threadId':tid})
 finally:s.close()
 print('Retained all acknowledged turns and resumed after container replacement.',flush=True)
print(phase+' passed as UID '+str(os.getuid()),flush=True)
''')

children=[]
def start(args):
    master,slave=pty.openpty()
    p=subprocess.Popen(['bash',str(bin_dir/launcher),*args],env=env,stdin=slave,stdout=slave,stderr=slave,start_new_session=True)
    os.close(slave)
    os.set_blocking(master, False)
    item={'p':p,'fd':master,'log':bytearray()};children.append(item)
    return item

def pump(item):
    if select.select([item['fd']],[],[],.05)[0]:
        try:item['log'].extend(os.read(item['fd'],65536))
        except OSError:pass

def wait_exit(item,timeout=40):
    deadline=time.monotonic()+timeout
    while item['p'].poll() is None and time.monotonic()<deadline:pump(item)
    assert item['p'].poll() is not None,item['log'].decode(errors='replace')
    pump(item)
    return item['p'].returncode

def start_shell():
    item=start(['-s',str(project)])
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        pump(item)
        if item['p'].poll() is not None:raise RuntimeError(item['log'].decode(errors='replace'))
        records=list((home/'.claude-contained/codex/locks').glob('*.json'))
        if records:
            r=json.loads(records[0].read_text())
            p=subprocess.run([real_runtime,'exec',r['name'],'cat','/run/claude-contained/identity.json'],capture_output=True,text=True)
            if p.returncode==0:return item,r
    raise RuntimeError('Startup timed out: '+item['log'].decode(errors='replace'))

try:
    item,record=start_shell()
    attached=start(['-t','codex',str(project),'--','--version'])
    deadline=time.monotonic()+20
    while b'[y/N]' not in attached['log'] and time.monotonic()<deadline:pump(attached)
    os.write(attached['fd'],b'yes\n')
    assert wait_exit(attached)==0,attached['log'].decode(errors='replace')
    print(runtime+': real launcher attach passed',flush=True)
    def exercise(phase):
        p=subprocess.run([real_runtime,'exec','-u','dev','-w',str(project),'-e','TEST_HOME='+str(home),record['name'],
                          'python3',str(project/'exercise.py'),runtime,phase],capture_output=True,text=True,timeout=100)
        print(p.stdout,flush=True)
        assert p.returncode==0,p.stderr
    exercise('write')
    # Abrupt container death after acknowledged turns must preserve history.
    subprocess.run([real_runtime,'kill',record['name']],check=True,capture_output=True)
    wait_exit(item)
    item,record=start_shell()
    exercise('verify')
    # Wrapper death must leave a running container owning the store.
    # Keep PID 1 alive after terminal disconnect. Otherwise Docker can correctly
    # exit its idle shell, leaving no orphan to attach to (a fresh launch is then
    # legitimate and has no reuse prompt).
    marker=project/'orphan-ready'
    os.write(item['fd'],("trap '' HUP; touch "+shlex.quote(str(marker))+"; sleep 300\n").encode())
    deadline=time.monotonic()+10
    while not marker.exists() and time.monotonic()<deadline:pump(item)
    assert marker.exists(),item['log'].decode(errors='replace')
    os.killpg(item['p'].pid,signal.SIGKILL);item['p'].wait()
    orphan=start(['-t','codex',str(project),'--','--version'])
    deadline=time.monotonic()+20
    while b'[y/N]' not in orphan['log'] and time.monotonic()<deadline and orphan['p'].poll() is None:pump(orphan)
    assert b'[y/N]' in orphan['log'], orphan['log'].decode(errors='replace')
    os.write(orphan['fd'],b'yes\n')
    assert wait_exit(orphan)==0,orphan['log'].decode(errors='replace')
    print(runtime+': orphan attachment passed',flush=True)
    try: os.write(item['fd'],b'exit\n')
    except OSError: pass  # Supervisor death can detach/close the original PTY.
    print('PASS; disposable evidence: '+str(root),flush=True)
finally:
    if 'record' in locals():
        subprocess.run([real_runtime,'stop',record['name']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
        subprocess.run([real_runtime,'rm',record['name']],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
    for i,item in enumerate(children):
        if item['p'].poll() is None:
            item['p'].terminate()
            try:item['p'].wait(timeout=10)
            except subprocess.TimeoutExpired:item['p'].kill()
        (root/f'launcher-{i}.log').write_bytes(item['log'])
        os.close(item['fd'])
