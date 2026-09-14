#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('managed', REPO / 'managed-launch.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
guard_spec = importlib.util.spec_from_file_location('guard', REPO / 'codex-guard.py')
guard = importlib.util.module_from_spec(guard_spec)
guard_spec.loader.exec_module(guard)

FAKE = '''#!/usr/bin/env python3
import json,os,sys,time
from pathlib import Path
args=sys.argv[1:]; root=Path(os.environ['FIXTURE']); runtime=Path(sys.argv[0]).name
state=root/(runtime+'.json')
rows=json.loads(state.read_text()) if state.exists() else []
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps([runtime,*args])+'\\n')
if args[:2]==['system','status'] or args==['info']: sys.exit(0)
if args[0] in ('list','ps'):
 if '--quiet' in args: print('\\n'.join(r['name'] for r in rows));sys.exit(0)
 if runtime=='container': print(json.dumps([{'configuration':{'id':r['name'],'labels':{'io.claude-contained.owner':r['token']}},'status':{'state':'running'}} for r in rows]))
 else:
  for r in rows: print(json.dumps({'Names':r['name'],'State':'running','Labels':'io.claude-contained.owner='+r['token']}))
elif args[0]=='run':
 env=dict(a.split('=',1) for i,a in enumerate(args) if i and args[i-1]=='-e')
 record={'name':args[args.index('--name')+1],'token':env['CC_TOKEN'],'home':env['HOST_HOME'],'runtime':runtime}
 state.write_text(json.dumps([record]))
 (root/'started').touch()
 while not (root/'release').exists(): time.sleep(.02)
 state.write_text('[]')
elif args[0]=='exec':
 if args[-2:]==['cat','/run/claude-contained/identity.json']:
  r=rows[0];print(json.dumps({k:r[k] for k in ('token','home','runtime')}))
 elif args[-1]=='--cc-check-idle' and (root/'busy').exists():
  print('Docker permits only one Codex session per folder.',file=sys.stderr);sys.exit(1)
 else: (root/'attached.json').write_text(json.dumps(args))
else: sys.exit(1)
'''


class ManagedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        for name in ('container', 'docker'):
            p = self.bin / name
            p.write_text(FAKE)
            p.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), FIXTURE=str(self.root), PATH=str(self.bin)+':'+os.environ['PATH'])
        self.env.pop('CC_TOKEN', None)
        self.children = []
        # Standalone copies also prevent the launcher's update check fetching Git.
        for name in ('claude-contained', 'claude-docked'):
            shutil.copyfile(REPO / name, self.bin / name)

    def tearDown(self):
        (self.root/'release').touch()
        for child in self.children:
            child.communicate(timeout=10)
        self.tmp.cleanup()

    def command(self, launcher, *args):
        return ['bash', str(self.bin / launcher), *map(str,args)]

    def start(self, launcher='claude-contained', *args, extra=()):
        proc = subprocess.Popen(self.command(launcher, '-t', 'codex', *args, self.project, *extra),
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(proc)
        for _ in range(200):
            if (self.root/'started').exists(): return proc
            if proc.poll() is not None: self.fail(proc.communicate())
            time.sleep(.02)
        self.fail('launcher did not start')

    def launch(self, launcher='claude-contained', *args, answer='yes\n'):
        return subprocess.run(self.command(launcher,*args),env=self.env,input=answer,capture_output=True,text=True,timeout=10)

    def test_identity_and_mount_rejection(self):
        alias = self.root/'alias';alias.symlink_to(self.project)
        self.assertEqual(m.canonical(alias),m.canonical(self.project))
        other = self.root/'other'/self.project.name;other.mkdir(parents=True)
        self.assertNotEqual(m.key_for(str(other)),m.key_for(str(self.project)))
        with self.assertRaises(RuntimeError): m.validate_mount_sources(str(self.home),[str(self.home)],[])
        with self.assertRaises(RuntimeError): m.validate_tool_args('codex',['--cd=/elsewhere'])
        for override in (['-c','sqlite_home="/x"'],['--config=sqlite_home=/x'],['-csqlite_home=/x'],['-c','history.sqlite_home=/x']):
            with self.assertRaises(RuntimeError): m.validate_tool_args('codex',override)
        m.validate_tool_args('codex',['why is CODEX_HOME or sqlite_home unset?','-c','model="gpt"'])
        m.validate_tool_args('claude',['-c','sqlite_home=/x'])

    def test_seed_ignores_dangling_symlinks(self):
        skills=self.home/'.codex/skills';skills.mkdir(parents=True)
        (skills/'dead').symlink_to('/nonexistent');(skills/'live').mkdir();(skills/'live/SKILL.md').write_text('x')
        with patch.dict(os.environ,HOME=str(self.home)):
            store=m.prepare_store(self.home/'.claude-contained/codex',str(self.project))
        self.assertTrue((store/'skills/live/SKILL.md').exists())
        self.assertFalse((store/'skills/dead').exists())

    def test_config_overrides_rejected_by_host_and_container(self):
        identity = self.root/'identity.json'
        identity.write_text(json.dumps({'home': str(self.home), 'runtime': 'docker'}))
        def guard_path(path):
            return identity if path == '/run/claude-contained/identity.json' else Path(path)
        for key in ('sqlite_home', 'codex_home'):
            for args in (['-c', key+'="/x"'], ['--config', key+'="/x"'],
                         ['--config='+key+'="/x"'], ['-c'+key+'="/x"'],
                         ['-c='+key+'="/x"']):
                with self.subTest(args=args):
                    with self.assertRaisesRegex(RuntimeError, 'overrides are not supported'):
                        m.validate_tool_args('codex', args)
                    with patch.object(guard, 'Path', side_effect=guard_path), \
                         patch.object(guard.sys, 'argv', ['codex', *args]):
                        with self.assertRaisesRegex(RuntimeError, 'state-location overrides are disabled'):
                            guard.main()

    def test_seed_copies_valid_relative_symlinks(self):
        skills = self.home/'.codex/skills'
        (skills/'real').mkdir(parents=True)
        (skills/'real/SKILL.md').write_text('skill content')
        (skills/'alias').symlink_to('real', target_is_directory=True)
        (skills/'real/copy.md').symlink_to('SKILL.md')
        (skills/'real/dead').symlink_to('missing')
        with patch.dict(os.environ, HOME=str(self.home)):
            store = m.prepare_store(self.home/'.claude-contained/codex', str(self.project))
        for name in ('real', 'alias'):
            copied = store/'skills'/name
            self.assertFalse(copied.is_symlink())
            self.assertEqual((copied/'SKILL.md').read_text(), 'skill content')
            self.assertEqual((copied/'copy.md').read_text(), 'skill content')
            self.assertFalse((copied/'copy.md').is_symlink())
            self.assertFalse((copied/'dead').is_symlink())
            self.assertFalse((copied/'dead').exists())

    def test_attach_adopts_owner_folders_unless_given_explicitly(self):
        extra=self.root/'extra';extra.mkdir()
        self.start('claude-contained','-a','Named',extra=[extra])
        for args in (['-a','Named','-t','codex'],['-t','claude',self.project]):
            result=self.launch('claude-contained',*args)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Shared folders: '+str(self.project)+' (rw), '+str(extra)+' (rw)',result.stderr)
        other=self.root/'other';other.mkdir()
        for args in ([self.project,other],[self.project,str(extra)+':ro']):
            result=self.launch('claude-contained','-t','claude',*args)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('does not share',result.stderr)

    def test_seed_copies_setup_only_and_retains_history(self):
        native=self.home/'.codex';native.mkdir()
        for name in ['auth.json','config.toml','state_5.sqlite','history.jsonl']:(native/name).write_text(name)
        base=self.home/'.claude-contained/codex'
        with patch.dict(os.environ,HOME=str(self.home)):
            store=m.prepare_store(base,str(self.project))
            self.assertTrue((store/'auth.json').exists())
            self.assertFalse((store/'state_5.sqlite').exists())
            self.assertFalse((store/'history.jsonl').exists())
            (store/'state_5.sqlite').write_text('retained')
            self.assertEqual(m.prepare_store(base,str(self.project)),store)
            self.assertEqual((store/'state_5.sqlite').read_text(),'retained')

    def test_apple_reuse_arguments_decline_options_and_mounts(self):
        self.start()
        reply=self.launch('claude-contained','-y','-t','codex',self.project,'--','resume','abc','prompt with spaces')
        self.assertEqual(reply.returncode,0,reply.stderr)
        attached=json.loads((self.root/'attached.json').read_text())
        self.assertEqual(attached[-5:],['codex','--yolo','resume','abc','prompt with spaces'])
        self.assertIn(str(self.project),attached)
        for answer in ['no\n','']:
            result=self.launch('claude-contained','-t','claude',self.project,answer=answer)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('cancelled',result.stderr)
        result=self.launch('claude-contained','-S',self.project)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('incompatible',result.stderr)
        calls=[json.loads(s) for s in (self.root/'calls.jsonl').read_text().splitlines()]
        runs=[c for c in calls if c[1]=='run']
        self.assertEqual(len(runs),1)
        mounts=[x for x in runs[0] if x.startswith('type=bind')]
        self.assertTrue(any('/codex/projects/' in x and x.endswith('/.codex') for x in mounts))
        self.assertFalse(any(x.startswith('type=bind,src='+str(self.home)+'/.codex,') for x in mounts))
        self.assertIn('type=bind,src='+str(self.home)+'/.claude-contained/shared,dst='+str(self.home)+'/.claude-contained',mounts)

    def test_docker_busy_refuses_codex_but_reuses_other_agents(self):
        self.start('claude-docked');(self.root/'busy').touch()
        result=self.launch('claude-docked','-t','codex',self.project)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('only one Codex',result.stderr)
        result=self.launch('claude-docked','-t','gemini',self.project)
        self.assertEqual(result.returncode,0,result.stderr)
        result=self.launch('claude-contained','-t','codex',self.project)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('use that runtime',result.stderr)

    def test_orphan_reuse_and_runtime_error_fail_closed(self):
        proc=self.start()
        # Killing the supervisor leaves its child launcher holding the inherited
        # lock; attachment still uses the live container's persisted identity.
        proc.kill();proc.wait()
        result=self.launch('claude-contained','-t','copilot',self.project)
        self.assertEqual(result.returncode,0,result.stderr)
        (self.root/'container.json').write_text('invalid runtime response')
        result=self.launch('claude-contained','-t','codex',self.project)
        self.assertNotEqual(result.returncode,0)

    def test_explicit_attach_uses_owner_folder_and_preserves_arguments(self):
        self.start()
        record=json.loads(next((self.home/'.claude-contained/codex/locks').glob('*.json')).read_text())
        result=self.launch('claude-contained','-a',record['name'],'-t','codex','--','resume','xyz')
        self.assertEqual(result.returncode,0,result.stderr)
        attached=json.loads((self.root/'attached.json').read_text())
        self.assertEqual(attached[-3:],['codex','resume','xyz'])
        self.assertIn(str(self.project),attached)

    def test_simultaneous_launches_create_exactly_one_container(self):
        for _ in range(2):
            self.children.append(subprocess.Popen(self.command('claude-contained','-t','codex',self.project),
                                 env=self.env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,text=True))
        for _ in range(200):
            if (self.root/'started').exists() and any(p.poll() is not None for p in self.children):break
            time.sleep(.02)
        self.assertEqual(sum(p.poll() is None for p in self.children),1)
        calls=[json.loads(s) for s in (self.root/'calls.jsonl').read_text().splitlines()]
        self.assertEqual(sum(c[1]=='run' for c in calls),1)

    def test_shared_claude_migration_preserves_old_and_native_links(self):
        parent=self.home/'.claude-contained';parent.mkdir()
        old=parent/'.claude.json';old.write_text('preserved')
        native=self.home/'.claude.json';native.symlink_to(old)
        shared=m.prepare_shared(str(self.home))
        self.assertEqual((shared/'.claude.json').read_text(),'preserved')
        self.assertEqual(native.read_text(),'preserved')
        self.assertTrue(old.is_symlink())
        self.assertEqual(m.prepare_shared(str(self.home)),shared)

    def test_custom_name_normalizes_identically_for_create_and_attach(self):
        self.start('claude-contained','-a','MyProject')
        result=self.launch('claude-contained','-a','MyProject','-t','codex','--','--version')
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__': unittest.main()
