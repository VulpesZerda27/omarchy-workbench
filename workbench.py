#!/usr/bin/env python3
"""Local Workbench storage and Herdr bridge. No shell evaluation of user input."""
import argparse
import contextlib
from datetime import datetime
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse, unquote
import uuid

PLUGIN = 'io.github.vulpeszerda27.workbench'
ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('WORKBENCH_DATA_DIR', str(Path.home()/'.local/share/omarchy/workbench')))
MAX_TEXT = 60000
DESKTOP_ENV_KEYS = ('DISPLAY','WAYLAND_DISPLAY','XAUTHORITY','HYPRLAND_INSTANCE_SIGNATURE',
    'XDG_CURRENT_DESKTOP','XDG_SESSION_DESKTOP','XDG_SESSION_TYPE')


def run(argv, timeout=12, **kwargs):
    result = subprocess.run(argv, capture_output=True, timeout=timeout, **kwargs)
    if result.returncode:
        err, out = (r.decode(errors='replace') if isinstance(r, bytes) else (r or '') for r in (result.stderr, result.stdout))
        # Some tools (Herdr among them) report errors on stdout.
        raise RuntimeError((err.strip() or out.strip() or 'Command failed: '+argv[0])[:900])
    return result.stdout


def desktop_env():
    env = os.environ.copy()
    env.setdefault('XDG_RUNTIME_DIR', '/run/user/'+str(os.getuid()))
    env.setdefault('DBUS_SESSION_BUS_ADDRESS', 'unix:path='+env['XDG_RUNTIME_DIR']+'/bus')
    # Herdr can outlive its SSH login. The user manager has the current desktop
    # environment; a new window or uwsm scope still inherits the caller's values.
    try:
        session = json.loads(run(['systemctl','--user','show-environment','--output=json'],
            env=env,text=True,timeout=2))
    except (OSError,RuntimeError,subprocess.TimeoutExpired,ValueError):
        session = {}
    if isinstance(session,dict) and (session.get('WAYLAND_DISPLAY') or session.get('DISPLAY')):
        for key in DESKTOP_ENV_KEYS:
            value = session.get(key)
            if isinstance(value,str) and value:env[key] = value
            else:env.pop(key,None)  # Do not mix stale SSH/X11 credentials into this session.
    if not env.get('HYPRLAND_INSTANCE_SIGNATURE'):
        instances = json.loads(run(['hyprctl', 'instances', '-j'], env=env,text=True))
        if instances:
            env['HYPRLAND_INSTANCE_SIGNATURE'] = instances[0]['instance']
            env['WAYLAND_DISPLAY'] = instances[0]['wl_socket']
            env['XDG_SESSION_TYPE'] = 'wayland'
    return env


def herdr_env():
    # A desktop panel must not inherit an unrelated agent's pane context.
    return {k:v for k,v in os.environ.items() if not k.startswith('HERDR_')}


HERDR_MISSING = 'Herdr is not installed, so there are no agent threads here. The shelf works without it.'


def herdr_error(text):
    """Herdr's JSON error as a sentence, naming the fix for a server that isn't running."""
    try:error=json.loads(text).get('error') or {}
    except (ValueError,AttributeError):return text
    if error.get('code')=='server_not_running':return 'Herdr is not running. Start it with `herdr` to see your agent threads.'
    return error.get('message') or text


def hcall(machine, *args, timeout=12):
    prefix = ['herdr']
    if machine.startswith('local:'):
        prefix += ['--session', machine[6:]]
    elif machine != 'local':
        prefix += ['--machine', machine]
    try:out = run(prefix+list(args), timeout=timeout, text=True, env=herdr_env())
    except FileNotFoundError:raise RuntimeError(HERDR_MISSING) from None
    except RuntimeError as exc:raise RuntimeError(herdr_error(str(exc))) from None
    if not out.strip():return {}
    value = json.loads(out)
    if isinstance(value, dict) and 'error' in value:
        raise RuntimeError(str(value['error']))
    return value.get('result', value) if isinstance(value, dict) else value


def machines():
    result = [{'value':'local', 'label':'This computer'}]
    try:
        for session in hcall('local', 'session', 'list', '--json').get('sessions', []):
            if not session.get('default') and session.get('running'):
                result.append({'value':'local:'+session['name'], 'label':'This computer · '+session['name']})
        for m in hcall('local', 'machine', 'list', '--json'):
            if m.get('enabled'):
                result.append({'value':m['id'], 'label':m['label'], 'target':m['target']})
    except (RuntimeError, subprocess.TimeoutExpired, ValueError):
        pass
    return result


class Store:
    def __init__(self, directory=DATA):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.db = sqlite3.connect(self.directory/'workbench.sqlite3', timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, body TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(kind,id))')
        self.db.commit()

    def get(self, kind, key, default=None):
        row = self.db.execute('SELECT body FROM objects WHERE kind=? AND id=?', (kind,key)).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, kind):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM objects WHERE kind=? ORDER BY updated DESC', (kind,))]

    def put(self, kind, value):
        self.db.execute('INSERT INTO objects VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body,updated=excluded.updated',
                        (kind,value['id'],json.dumps(value,ensure_ascii=False),time.time()))
        self.db.commit()
        return value

    def delete(self, kind, key):
        self.db.execute('DELETE FROM objects WHERE kind=? AND id=?', (kind,key)); self.db.commit()

    def patch(self, kind, key, changes, allowed=None, create=False, expected=None):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            value = self.get(kind,key,{'id':key} if create else None)
            if value is None: raise ValueError('This item no longer exists.')
            if allowed is not None and value.get('status') not in allowed:
                raise ValueError('This capture has already been sent or is being delivered.')
            if expected is not None and any(value.get(k)!=v for k,v in expected.items()):
                raise ValueError('This capture changed while preparing delivery; review its current version.')
            # A callable computes the change from the current row inside the transaction.
            value.update(changes(value) if callable(changes) else changes)
            return self.put(kind,value)
        except Exception:
            self.db.rollback(); raise


def project_name(cwd):
    parts = Path(cwd or str(Path.home())).parts
    if 'Projects' in parts:
        i=parts.index('Projects')
        if i+1<len(parts): return parts[i+1]
    if cwd==str(Path.home()): return 'Personal'
    return Path(cwd or '').name or 'Personal'


def normalize(snapshot, machine):
    workspaces={w['workspace_id']:w for w in snapshot.get('workspaces',[])}
    threads=[]
    for p in snapshot.get('panes',[]):
        if not p.get('agent'): continue
        w=workspaces.get(p['workspace_id'],{})
        terminal=p.get('terminal_id')
        if not terminal: continue
        key=hashlib.sha256((machine+'\0'+terminal).encode()).hexdigest()[:24]
        threads.append({'id':key,'machine':machine,'terminal':terminal,'pane':p['pane_id'],
            'workspace':p['workspace_id'],'tab':p['tab_id'],
            'title':p.get('label') or p.get('terminal_title_stripped') or w.get('tokens',{}).get('topic') or w.get('label','Thread'),
            'project':project_name(p.get('cwd')),'cwd':p.get('cwd',''),
            'agent':p['agent'],'agent_session':p.get('agent_session',{}).get('value',''),
            'status':p.get('agent_status','unknown'),'focused':p['pane_id']==snapshot.get('focused_pane_id'),
            'live':True, 'seen':time.time()})
    return threads


def refresh(store, machine='local'):
    snapshot=hcall(machine,'api','snapshot')['snapshot']
    live=normalize(snapshot,machine)
    companion=next((c for c in store.all('companion') if c.get('pane')==snapshot.get('focused_pane_id')),None)
    if companion:
        for thread in live:
            if thread['id']==companion['id']:thread['focused']=True
    live_ids={t['id'] for t in live}
    for old in store.all('thread'):
        if old['machine']==machine and old['id'] not in live_ids and old.get('live'):
            old.update(live=False,focused=False,status='closed');store.put('thread',old)
    for thread in live: store.put('thread',thread)
    return live


def resolve_thread(store, key='', machine='local', pane=''):
    live=refresh(store,machine)
    if key:
        result=next((t for t in live if t['id']==key),None)
    elif pane:
        result=next((t for t in live if t['pane']==pane),None)
    else:
        result=next((t for t in live if t['focused']),None)
    if not result: raise ValueError('Choose a live agent thread first.')
    return result


def validate_target(target):
    target=target.strip()
    if not target: raise ValueError('Choose a file or paste a URL.')
    u=urlparse(target)
    if u.scheme in ('http','https'):
        if not u.netloc: raise ValueError('Enter a complete web address.')
        return target,'Link'
    if u.scheme=='file':
        if u.netloc not in ('','localhost'): raise ValueError('Only local file URLs are supported.')
        target=unquote(u.path)
    elif u.scheme:
        raise ValueError('Use a local file or an http/https link.')
    path=Path(target).expanduser().resolve()
    if not path.is_file(): raise ValueError('That file does not exist.')
    suffix=path.suffix.lower()
    kind='Image' if suffix in ('.png','.jpg','.jpeg','.webp','.gif','.svg') else 'PDF' if suffix=='.pdf' else 'Document' if suffix in ('.md','.txt','.rst','.docx','.odt') else 'Page' if suffix in ('.html','.htm') else 'File'
    return str(path),kind


def add_item(store, target, title='', thread='', note='', reference=False):
    target,kind=validate_target(target)
    t=store.get('thread',thread,{})
    existing=next((i for i in store.all('item') if i['target']==target and i['thread']==thread and i['reference']==reference),{})
    item={'id':existing.get('id',uuid.uuid4().hex),'target':target,'kind':kind,
          'title':title.strip() or (urlparse(target).netloc if kind=='Link' else Path(target).stem.replace('-',' ').replace('_',' ')),
          'thread':thread,'thread_title':t.get('title','Unassigned'),'project':t.get('project','General'),
          'note':note[:MAX_TEXT],'reference':reference,'created':existing.get('created',time.time()),'updated':time.time()}
    return store.put('item',item)


def public_item(item):
    item=dict(item)
    item['exists']=item['kind']=='Link' or Path(item['target']).is_file()
    item['thumbnail']=Path(item['target']).as_uri() if item['kind']=='Image' and item['exists'] else ''
    return item


def sort_entries(entries,sort='name',descending=False):
    """Folders stay above files; size and type don't apply to folders, so those keep name order."""
    by_name=lambda e:(e['name'].lower(),e['name'])
    key={'type':lambda e:(e['type'].lower(),)+by_name(e),'size':lambda e:(e['bytes'],)+by_name(e),
         'modified':lambda e:(e['modified'],)+by_name(e)}.get(sort,by_name)
    folders=sorted((e for e in entries if e['directory']),key=by_name if sort in ('type','size') else key,
                   reverse=descending and sort not in ('type','size'))
    return folders+sorted((e for e in entries if not e['directory']),key=key,reverse=descending)


def browse_files(target='~',hidden=False,sort='name',descending=False):
    folder=Path(target or '~').expanduser().resolve()
    if folder.is_file():return {'file':str(folder)}
    if not folder.is_dir():raise ValueError('That folder does not exist.')
    entries=[]
    for p in folder.iterdir():
        if not hidden and p.name.startswith('.'):continue
        try:
            directory=p.is_dir()
            if not directory and not p.is_file():continue
            stat=p.stat();size=stat.st_size
        except OSError:continue
        entries.append({'path':str(p),'name':p.name,'directory':directory,'bytes':size,'modified':stat.st_mtime,
            'type':'Folder' if directory else p.suffix[1:].upper() or 'File',
            'size':str(size)+' B' if size<1024 else str(round(size/1024))+' KB' if size<1048576 else str(round(size/1048576,1))+' MB'})
    return {'folder':str(folder),'entries':sort_entries(entries,sort,descending)}


def preview(store, key):
    item=store.get('item',key)
    if not item: raise ValueError('This output was removed from the shelf.')
    result=public_item(item);result['text']='';result['format']='plain'
    path=Path(item['target'])
    if item['kind']=='Link': result['text']=item.get('note') or item['target'];return result
    if not path.is_file(): result['text']='The original file has moved or was removed.';return result
    if item['kind']=='PDF':
        cache=store.directory/'previews';cache.mkdir(exist_ok=True)
        stem=hashlib.sha256((str(path)+str(path.stat().st_mtime_ns)).encode()).hexdigest()
        image=cache/(stem+'.png')
        if not image.exists(): run(['pdftoppm','-f','1','-singlefile','-scale-to','900','-png',str(path),str(cache/stem)],timeout=20)
        result['thumbnail']=image.as_uri()
    elif item['kind']!='Image':
        try:
            with path.open('rb') as source:data=source.read(180000)
            if b'\0' not in data:
                result['text']=data.decode('utf-8')
                result['format']='markdown' if path.suffix.lower()=='.md' else 'plain'
                if path.stat().st_size>180000: result['text']+='\n\n… Open the file to read the rest.'
        except (UnicodeDecodeError,OSError): pass
    return result


MAX_ATTACHMENTS = 20
AGENTS = ('claude', 'codex')


def draft_attachments(draft):
    """A draft's reference files, including drafts saved with a single attachment."""
    files=draft.get('attachments')
    if files is None:files=[draft.get('attachment','')]
    return [f for f in files if f]


def attachment_fields(files):
    if not isinstance(files,list) or not all(isinstance(f,str) for f in files):
        raise ValueError('Attachments must be a list of file paths.')
    files=list(dict.fromkeys(f for f in files if f))
    if len(files)>MAX_ATTACHMENTS:raise ValueError('Attach up to '+str(MAX_ATTACHMENTS)+' files to one capture.')
    # `attachment` mirrors the first file for panels from before multiple attachments.
    return {'attachments':files,'attachment':files[0] if files else ''}


def new_draft(store, thread='', text='', attachment='', attachments=None, issue=None):
    value={'id':uuid.uuid4().hex,'thread':thread,'text':text[:MAX_TEXT],
           **attachment_fields(attachments if attachments is not None else [attachment]),
           'status':'draft','created':time.time(),'updated':time.time(),
           'destination':store.get('thread',thread,{})}
    if issue:value['issue']=issue
    return store.put('draft',value)


def add_attachment(store, key, path):
    return store.patch('draft',key,lambda draft:{**attachment_fields(draft_attachments(draft)+[str(path)]),
        'updated':time.time()},allowed={'draft'})


def capture_draft_for(store, thread, draft_id=''):
    """The draft a new screenshot joins: the one given, else the thread's open draft."""
    if draft_id:return draft_id
    return next((d['id'] for d in store.all('draft') if d['status']=='draft' and not d.get('issue') and d.get('thread','')==thread),'')


def capture_view(store, key):
    """Issue requests are drafted in the Issues view; every other draft in Capture."""
    return 'issues' if (store.get('draft',key) or {}).get('issue') else 'capture'


def draft_has_content(draft):
    # An existing issue is a complete request on its own; other drafts need a note or a file.
    return bool(draft['text'].strip() or draft_attachments(draft) or (draft.get('issue') or {}).get('number'))


def new_thread_target(value):
    if not value:return None
    if not isinstance(value,dict) or value.get('agent') not in AGENTS or not isinstance(value.get('dir'),str) or not value['dir']:
        raise ValueError('Choose a project and Claude or Codex for the new thread.')
    return {'dir':value['dir'],'agent':value['agent']}


def update_brief(store, thread_key, summary, next_step, waiting=''):
    thread=store.get('thread',thread_key)
    if not thread:raise ValueError('Choose an explicit thread ID from workbench threads.')
    fields={'summary':summary.strip(),'next':next_step.strip(),'waiting':waiting.strip()}
    if not fields['summary'] or not fields['next']:
        raise ValueError('Include a short summary and a next step.')
    if any(len(value)>800 for value in fields.values()):
        raise ValueError('Keep each part of the thread brief under 800 characters.')
    brief={**fields,'author':thread['agent'],'updated':time.time(),'agent_session':thread.get('agent_session','')}
    return store.patch('card',thread_key,{'brief':brief,'updated':time.time()},create=True)


def save_personal_note(store,thread_key,note):
    if not store.get('thread',thread_key):raise ValueError('Choose a thread first.')
    return store.patch('card',thread_key,{'note':note[:MAX_TEXT],'updated':time.time()},create=True)


def save_draft(store, request, issue=None):
    changes={k:request[k] for k in ('thread','text') if k in request}
    if len(changes.get('text',''))>MAX_TEXT: raise ValueError('Please keep a capture under 60,000 characters.')
    if 'new_thread' in request:changes['new_thread']=new_thread_target(request['new_thread'])
    if issue is not None:changes['issue']=issue
    changes['updated']=time.time()
    previous=store.get('draft',request['id'],{})
    if 'attachments' in request:changes.update(attachment_fields(request['attachments']))
    elif 'attachment' in request:
        # A panel from before multiple attachments only knows the first file; keep the rest.
        files=draft_attachments(previous)
        if not files or files[0]!=request['attachment']:files=[request['attachment']]
        changes.update(attachment_fields(files))
    if 'thread' in changes and changes['thread']!=previous.get('thread'):
        changes['destination']=store.get('thread',changes['thread'],{})
    return store.patch('draft',request['id'],changes,allowed={'draft'})


class AgentBusy(ValueError):
    pass


def check_identity(thread, live, require_ready=True):
    if not live or live['terminal']!=thread['terminal'] or live['agent']!=thread['agent']:
        raise ValueError('The original agent is no longer in this thread. Choose a different destination or start a new draft for the current conversation.')
    if thread.get('agent_session') and live.get('agent_session')!=thread['agent_session']:
        raise ValueError('A different conversation is now using this pane. Choose a different destination or start a new draft for the current conversation.')
    if require_ready and live['status'] not in ('idle','done'):
        raise AgentBusy('This agent is '+live['status']+'. Your capture is saved; send it when the agent is ready.')


def missing_attachment(draft):
    return next((Path(f).name for f in draft_attachments(draft) if not Path(f).is_file()),None)


def stage_attachments(thread, draft):
    paths=[Path(f).resolve() for f in draft_attachments(draft)]
    if not paths:return []
    missing=missing_attachment(draft)
    if missing: raise ValueError('The attachment '+missing+' is no longer available.')
    if thread['machine'].startswith('local'): return [str(p) for p in paths]
    profile=next((m for m in machines() if m['value']==thread['machine']),None)
    if not profile or not profile.get('target') or profile['target'].startswith('-'):
        raise ValueError('This remote machine is no longer configured.')
    target=profile['target']; directory='.cache/omarchy-workbench/inbox/'+draft['id']
    run(['ssh','-oBatchMode=yes','-oConnectTimeout=6',target,'umask 077; mkdir -p '+directory],timeout=10)
    staged=[]
    for n,path in enumerate(paths,1):
        suffix=path.suffix if re.fullmatch(r'\.[a-zA-Z0-9]{1,8}',path.suffix) else '.bin'
        remote=directory+'/capture'+(str(n) if len(paths)>1 else '')+suffix
        run(['scp','-q','-oBatchMode=yes','-oConnectTimeout=6',str(path),target+':'+remote],timeout=45)
        staged.append('~/'+remote)
    return staged


def attachment_prompt(paths):
    if not paths:return ''
    if len(paths)==1:return '\n\nPlease inspect the attached reference file: '+paths[0]
    return '\n\nPlease inspect the attached reference files:\n'+'\n'.join('- '+p for p in paths)


def capture_version(draft):
    return {k:draft.get(k) for k in ('thread','text','attachment','attachments','new_thread','issue','destination','updated','queue_token')}


def draft_prompt(draft, attachments):
    if draft.get('issue'):return issue_prompt(draft['issue'],draft['text'].strip(),attachments)
    return draft['text'].strip()+attachment_prompt(attachments)


def deliver(store, key, queued=False, queue_token=None):
    draft=store.get('draft',key)
    allowed={'queued'} if queued else {'draft'}
    if not draft or draft['status'] not in allowed: raise ValueError('This capture is already sent or awaiting a delivery check.')
    if queue_token is not None and draft.get('queue_token')!=queue_token:
        raise ValueError('The queued capture was cancelled or replaced.')
    if not draft_has_content(draft):raise ValueError('Add a note, URL, or screenshot first.')
    thread=draft.get('destination') or store.get('thread',draft['thread'])
    if not thread:raise ValueError('Choose a destination thread.')
    live=next((t for t in normalize(hcall(thread['machine'],'api','snapshot')['snapshot'],thread['machine']) if t['id']==thread['id']),None)
    check_identity(thread,live)
    attachments=stage_attachments(thread,draft)
    if attachments:
        # A remote upload can outlast a turn or conversation; resolve the target again.
        live=next((t for t in normalize(hcall(thread['machine'],'api','snapshot')['snapshot'],thread['machine']) if t['id']==thread['id']),None)
        check_identity(thread,live)
    # Claim before submission: concurrent clicks cannot duplicate a prompt.
    draft=store.patch('draft',key,{'status':'sending'},allowed=allowed,expected=capture_version(draft))
    prompt=draft_prompt(draft,attachments)
    try:
        hcall(thread['machine'],'agent','prompt',live['pane'],prompt,timeout=20)
    except Exception as exc:
        store.patch('draft',key,{'status':'uncertain','error':str(exc)})
        raise RuntimeError('Delivery could not be confirmed. Check the thread before making a new capture; this one will not be resent automatically.') from exc
    return store.patch('draft',key,{'status':'sent','sent':time.time()})


def queue_capture(store,key,coordinator='local'):
    draft=store.get('draft',key)
    if not draft or draft['status']!='draft':raise ValueError('This capture is already queued or sent.')
    if not draft_has_content(draft):raise ValueError('Add a note, URL, or screenshot first.')
    thread=draft.get('destination') or store.get('thread',draft['thread'])
    if not thread:raise ValueError('Choose a destination thread.')
    live=next((t for t in normalize(hcall(thread['machine'],'api','snapshot')['snapshot'],thread['machine']) if t['id']==thread['id']),None)
    check_identity(thread,live,require_ready=False)
    missing=missing_attachment(draft)
    if missing:raise ValueError('The attachment '+missing+' is no longer available.')
    return store.patch('draft',key,{'status':'queued','queued':time.time(),'queue_token':uuid.uuid4().hex,
        'queue_coordinator':coordinator,'queue_error':''},allowed={'draft'},expected=capture_version(draft))


def cancel_capture(store,key):
    return store.patch('draft',key,{'status':'draft','queue_error':''},allowed={'queued'})


def git_branch(folder):
    head=(folder/'.git'/'HEAD')
    try:ref=head.read_text().strip()
    except OSError:return ''
    return ref.removeprefix('ref: refs/heads/') if ref.startswith('ref: refs/heads/') else ''


def projects():
    """Home, then every folder in ~/Projects, most recently active first.

    A repository's activity is its last commit; other folders use their own modification time.
    Git worktrees and symlinks to another listed project are skipped, so each project appears once."""
    home=Path.home();root=home/'Projects'
    rows=[{'value':str(home),'label':'Home','description':'~'}]
    try:folders=[p for p in root.iterdir() if not p.name.startswith('.') and p.is_dir()]
    except OSError:folders=[]
    found=[]
    for folder in folders:
        if (folder/'.git').is_file():continue
        if folder.is_symlink() and folder.resolve().parent==root.resolve():continue
        try:when=folder.stat().st_mtime
        except OSError:continue
        detail='not a git repo'
        if (folder/'.git').is_dir():
            try:
                stamp,ago=run(['git','-C',str(folder),'log','-1','--format=%ct%x09%cr'],timeout=5,text=True).strip().split('\t')
                when=float(stamp)
            except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired):ago='no commits'
            branch=git_branch(folder)
            detail=(branch+' · ' if branch else '')+ago
        found.append((-when,folder.name.lower(),{'value':str(folder),'label':folder.name,'description':detail}))
    return rows+[row for _,_,row in sorted(found,key=lambda f:f[:2])]


def start_thread(store, directory, agent, machine='local', timeout=25):
    """Start an agent in a fresh Herdr workspace for the project, without switching to it."""
    if agent not in AGENTS:raise ValueError('Choose Claude or Codex.')
    if machine!='local' and not machine.startswith('local:'):raise ValueError('New threads start on this computer.')
    path=Path(directory).expanduser().resolve()
    if not path.is_dir():raise ValueError('That project folder no longer exists.')
    created=hcall(machine,'workspace','create','--cwd',str(path),'--label',path.name,'--no-focus')
    pane=(created.get('root_pane') or {}).get('pane_id') if isinstance(created,dict) else None
    if not pane:raise RuntimeError('Herdr did not return the new workspace.')
    hcall(machine,'pane','run',pane,agent)
    deadline=time.monotonic()+timeout
    while True:
        thread=next((t for t in normalize(hcall(machine,'api','snapshot')['snapshot'],machine) if t['pane']==pane),None)
        if thread:return store.put('thread',thread)
        if time.monotonic()>deadline:
            raise RuntimeError(agent.title()+' did not start in the new '+path.name+' workspace. Check it in Herdr; your draft is kept.')
        time.sleep(0.5)


GITHUB_REMOTE = re.compile(r'^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$')
ISSUE_FIELDS = 'number,title,state,stateReason,labels,author,assignees,createdAt,updatedAt,closedAt,url'
ISSUE_LIMIT = 200


def github_repo(directory):
    """owner/name of a project's GitHub repository: gh's default remote, else origin, else any."""
    try:out=run(['git','-C',str(directory),'config','--get-regexp',r'^remote\..*\.(url|gh-resolved)$'],timeout=5,text=True)
    except (OSError,RuntimeError,subprocess.TimeoutExpired):return ''
    urls={};preferred=[]
    for line in out.splitlines():
        key,_,value=line.partition(' ');value=value.strip()
        name,_,field=key[len('remote.'):].rpartition('.')
        if field=='url':urls[name]=value
        elif value=='base':preferred.append(name)
        elif re.fullmatch(r'[\w.-]+/[\w.-]+',value):return value  # `gh repo set-default` chose another repository
    for name in preferred+['origin']+list(urls):
        match=GITHUB_REMOTE.match(urls.get(name,''))
        if match:return match.group(1)
    return ''


def issue_projects():
    """The projects, most recently active first, each with the repository its issues live in."""
    rows=[]
    for project in projects()[1:]:
        repo=github_repo(project['value'])
        rows.append({**project,'repo':repo,'description':repo or 'No GitHub repository'})
    return rows


def gh(*args):
    # A panel can't answer an interactive prompt; fail with gh's message instead.
    env={**os.environ,'GH_PROMPT_DISABLED':'1','GH_NO_UPDATE_NOTIFIER':'1','NO_COLOR':'1'}
    return run(['gh',*args],timeout=30,text=True,env=env)


def gh_error(exc, repo):
    text=str(exc)
    if isinstance(exc,FileNotFoundError):return 'Install the GitHub CLI (gh) to see issues.'
    if isinstance(exc,subprocess.TimeoutExpired) or 'error connecting' in text:return 'GitHub couldn’t be reached. Check the connection, then refresh.'
    if 'gh auth login' in text:return 'Sign in to GitHub first: run gh auth login in a terminal.'
    if 'disabled issues' in text:return 'Issues are turned off for '+repo+' on GitHub.'
    if 'Could not resolve to a Repository' in text:return 'GitHub can’t find '+repo+', or this account can’t see it.'
    if isinstance(exc,ValueError):return 'GitHub sent a reply Workbench couldn’t read.'
    return text.strip()[:500] or 'GitHub didn’t answer.'


def iso_time(value):
    try:return datetime.fromisoformat(value).timestamp() if value else 0
    except ValueError:return 0


def slim_issue(raw):
    return {'number':raw['number'],'title':raw.get('title',''),'state':str(raw.get('state','')).lower(),
        'reason':str(raw.get('stateReason') or '').lower(),'url':raw.get('url',''),
        'labels':[{'name':l.get('name',''),'color':'#'+(l.get('color') or '888888')} for l in raw.get('labels') or []],
        'author':(raw.get('author') or {}).get('login',''),'assignees':[a.get('login','') for a in raw.get('assignees') or []],
        'created':iso_time(raw.get('createdAt')),'updated':iso_time(raw.get('updatedAt')),'closed':iso_time(raw.get('closedAt'))}


def issue_list(store, directory, state='open', refresh=True):
    """A project's issues, newest first. The last list stays available when GitHub can't be reached."""
    if state not in ('open','closed'):raise ValueError('Show open or closed issues.')
    repo=github_repo(directory)
    result={'dir':directory,'repo':repo,'state':state,'issues':[],'fetched':0,'error':''}
    if not repo:return result
    key=repo+'#'+state;cached=store.get('issues',key)
    if cached:result.update(issues=cached['issues'],fetched=cached['fetched'])
    if refresh or not cached:
        try:rows=json.loads(gh('issue','list','-R',repo,'--state',state,'--limit',str(ISSUE_LIMIT),'--json',ISSUE_FIELDS))
        except (OSError,RuntimeError,subprocess.TimeoutExpired,ValueError) as exc:
            result['error']=gh_error(exc,repo);return result
        issues=[slim_issue(r) for r in rows]
        if state=='closed':issues.sort(key=lambda i:i['closed'],reverse=True)
        cached=store.put('issues',{'id':key,'issues':issues,'fetched':time.time()})
        result.update(issues=issues,fetched=cached['fetched'])
    return result


def safe_markdown(text):
    """GitHub Markdown for a local preview. Images become links, so the preview never loads remote content."""
    text=re.sub(r'<!--.*?-->','',text or '',flags=re.S)
    text=re.sub(r'<img\b[^>]*?\bsrc=["\']([^"\']+)["\'][^>]*>',lambda m:'[Image]('+m.group(1)+')',text,flags=re.I)
    text=re.sub(r'<img\b[^>]*>','',text,flags=re.I)
    text=re.sub(r'!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?[^)]*\)',lambda m:'['+(m.group(1).strip() or 'Image')+']('+m.group(2)+')',text)
    return text.strip()


def issue_markdown(raw):
    parts=[safe_markdown(raw.get('body')) or '*No description provided.*']
    for comment in raw.get('comments') or []:
        when=iso_time(comment.get('createdAt'))
        heading='**'+((comment.get('author') or {}).get('login') or 'ghost')+'**'+(' · '+datetime.fromtimestamp(when).strftime('%-d %b %Y') if when else '')
        parts.append('---\n\n'+heading+'\n\n'+safe_markdown(comment.get('body')))
    return '\n\n'.join(parts)


def issue_detail(store, directory, number, updated=0):
    """One issue with its comments. Unchanged issues come from the cache without asking GitHub."""
    repo=github_repo(directory)
    if not repo:raise ValueError(Path(directory).name+' has no GitHub repository.')
    key=repo+'#'+str(int(number));cached=store.get('issue',key)
    if cached and updated and cached.get('updated')==updated:return {**cached,'error':''}
    try:raw=json.loads(gh('issue','view',str(int(number)),'-R',repo,'--json',ISSUE_FIELDS+',body,comments,milestone'))
    except (OSError,RuntimeError,subprocess.TimeoutExpired,ValueError) as exc:
        if cached:return {**cached,'error':gh_error(exc,repo)}
        raise RuntimeError(gh_error(exc,repo)) from exc
    issue=store.put('issue',{**slim_issue(raw),'id':key,'repo':repo,'comments':len(raw.get('comments') or []),
        'milestone':(raw.get('milestone') or {}).get('title',''),'markdown':issue_markdown(raw)})
    return {**issue,'error':''}


def issue_target(value):
    """A project and, unless the request is for a new issue, one of its issues. The repository always comes from the project."""
    if not isinstance(value,dict) or not value.get('dir'):raise ValueError('Choose a project first.')
    path=Path(str(value['dir'])).expanduser()
    if not path.is_dir():raise ValueError('That project folder no longer exists.')
    repo=github_repo(path)
    if not repo:raise ValueError(path.name+' has no GitHub repository.')
    try:number=int(value.get('number') or 0)
    except (TypeError,ValueError):number=-1
    if number<0:raise ValueError('Choose an issue.')
    return {'dir':str(path),'repo':repo,'number':number,'title':str(value.get('title') or '')[:300] if number else ''}


def issue_draft(store, target):
    """The open draft for this issue, or for a new issue in this project, created on first use."""
    store.db.execute('BEGIN IMMEDIATE')
    try:
        for draft in store.all('draft'):
            issue=draft.get('issue') or {}
            if draft['status']=='draft' and issue.get('dir')==target['dir'] and issue.get('number')==target['number']:
                store.db.commit();return draft
        return new_draft(store,issue=target)
    except Exception:
        store.db.rollback();raise


def save_issue_draft(store, request):
    target=issue_target(request.get('issue'))
    changes={k:request[k] for k in ('text','attachments') if k in request}
    return save_draft(store,{**changes,'id':issue_draft(store,target)['id']},issue=target)


def issue_prompt(issue, text, attachments):
    repo,number,project=issue['repo'],issue['number'],Path(issue['dir']).name
    if number:
        lines=['Implement '+repo+'#'+str(number)+(': '+issue['title'] if issue.get('title') else ''),'',
            'Issue: https://github.com/'+repo+'/issues/'+str(number),
            'Read it with `gh issue view '+str(number)+' -R '+repo+' --comments` and check its claims against the code before changing anything. '
            'Follow this project’s agent instructions for branches, tests, commits and pull requests, and reference #'+str(number)+' in your commits. '
            'If the issue leaves a product or design decision open, ask me before deciding it. '
            'When you finish, summarise what changed and how you verified it.']
        if text:lines+=['','Additional instructions:',text]
    else:
        summary=text.splitlines()[0].strip() if text else 'from the attached files'
        lines=['Write a GitHub issue for '+project+': '+(summary[:77]+'…' if len(summary)>80 else summary),'',
            'Turn the request below into a complete issue in '+repo+' and file it.',
            '- Read the project’s agent instructions and the code the request touches, so the issue is specific and accurate.',
            '- Check the existing issues (`gh issue list -R '+repo+' --search …`). If one already covers this, tell me instead of filing a duplicate.',
            '- Write a clear title and a body with the problem or goal and why it matters, what you found in the code, a proposed approach, and acceptance criteria. '
            'Follow the repository’s issue templates and conventions, and only use labels that already exist (`gh label list -R '+repo+'`).',
            '- File it with `gh issue create -R '+repo+'` and reply with its number and link.',
            '','Don’t change any code or project files. If the request is ambiguous in a way that would change the issue, ask me before filing it.']
        if attachments:lines.append('The attached files are for your reference; describe what matters in them, since they aren’t uploaded to GitHub.')
        lines+=['','Request:',text or '(See the attached files.)']
    return '\n'.join(lines)+attachment_prompt(attachments)


def hand_over_issue(store, request):
    """Start Claude or Codex in the project's own workspace; the issue request becomes its first message."""
    agent=request.get('agent')
    if agent not in AGENTS:raise ValueError('Choose Claude or Codex.')
    draft=save_issue_draft(store,request)
    if not draft_has_content(draft):raise ValueError('Describe the issue first.')
    missing=missing_attachment(draft)
    if missing:raise ValueError('The attachment '+missing+' is no longer available.')
    thread=start_thread(store,draft['issue']['dir'],agent)
    store.patch('draft',draft['id'],{'thread':thread['id'],'destination':thread,'started_thread':agent},allowed={'draft'})
    from automation import start, queue_coordinator
    coordinator=queue_coordinator(thread['machine'])
    start(machine=coordinator)
    return {'draft':queue_capture(store,draft['id'],coordinator=coordinator),'thread':thread}


def process_capture_queue(store,machine,live,ready_counts,coordinator=None):
    """Two ready observations, then one capture per thread. Never answer an approval."""
    by_id={t['id']:t for t in live};visited=set();drafts=store.all('draft')
    active={(d['id'],d.get('queue_token')) for d in drafts if d['status']=='queued'}
    for attempt in list(ready_counts):
        if attempt not in active:ready_counts.pop(attempt,None)
    def stop(draft,exc):
        try:store.patch('draft',draft['id'],{'status':'draft','queue_error':'Queue stopped: '+str(exc)},
            allowed={'queued'},expected={'queue_token':draft.get('queue_token')})
        except ValueError:pass  # Cancellation or a replacement belongs to the user.
    for draft in sorted(drafts,key=lambda d:d.get('queued',d['created'])):
        if draft['status']!='queued':continue
        if coordinator is not None and draft.get('queue_coordinator','local')!=coordinator:continue
        destination=draft.get('destination') or store.get('thread',draft['thread'],{})
        if destination.get('machine')!=machine or draft['thread'] in visited:continue
        visited.add(draft['thread']);key=draft['id'];target=by_id.get(draft['thread'])
        attempt=(key,draft.get('queue_token'))
        try:check_identity(destination,target,require_ready=False)
        except ValueError as exc:
            stop(draft,exc);ready_counts.pop(attempt,None);continue
        if target['status'] not in ('idle','done'):
            ready_counts.pop(attempt,None);continue
        ready_counts[attempt]=ready_counts.get(attempt,0)+1
        if ready_counts[attempt]<2:continue
        ready_counts.pop(attempt,None)
        try:deliver(store,key,queued=True,queue_token=draft.get('queue_token'))
        except AgentBusy:continue
        except Exception as exc:
            # An uncertain delivery is already marked by deliver and must not retry.
            stop(draft,exc)


SOURCE_SUFFIXES = frozenset(('.sh','.bash','.zsh','.fish','.py','.pyw','.js','.mjs','.cjs',
    '.jsx','.ts','.tsx','.rb','.pl','.pm','.php','.lua','.r','.go','.rs','.c','.h','.cc',
    '.cpp','.cxx','.hpp','.java','.kt','.kts','.swift','.cs','.scala','.ex','.exs','.erl',
    '.hs','.clj','.sql','.css','.scss','.sass','.less','.vue','.svelte','.ps1','.bat','.cmd'))
LAUNCHER_SUFFIXES = frozenset(('.desktop','.appimage','.exe','.com','.msi'))


def _preferred_editor():
    try:return (Path.home()/'.local/state/omarchy/defaults/editor').read_text().splitlines()[0].strip() or 'nvim'
    except (OSError,IndexError):return 'nvim'


def _default_app(target):
    # GIO resolves inherited MIME defaults too, without parsing localized CLI output.
    try:
        from gi.repository import Gio, GLib
    except ImportError:return None
    try:return Gio.File.new_for_path(target).query_default_handler(None)
    except GLib.Error:return None


def _is_vscode(executable):
    return bool(executable and Path(executable).name in ('code','code-insiders') and shutil.which(executable))


def _opening_command(target,kind):
    if kind=='Link':return ['gio','open',target]
    path=Path(target)
    if path.suffix.lower() in LAUNCHER_SUFFIXES:
        return ['gio','open',str(path.parent)]
    try:
        with path.open('rb') as source:sample=source.read(8192)
    except OSError as exc:
        raise RuntimeError(f'Cannot read {path.name}: {exc}') from exc
    # Inspect bytes as well as names: a renamed binary must never become a script.
    binary=(sample.startswith((b'\x7fELF',b'MZ')) or
            any(byte<32 and byte not in (9,10,12,13) for byte in sample))
    source_file=path.suffix.lower() in SOURCE_SUFFIXES or path.name in ('Makefile','Dockerfile','Justfile')
    if binary and (source_file or os.access(path,os.X_OK) or sample.startswith((b'\x7fELF',b'MZ'))):
        return ['gio','open',str(path.parent)]
    if not binary and (source_file or sample.startswith(b'#!') or os.access(path,os.X_OK)):
        return ['omarchy','launch','editor']+(['--new-window'] if _is_vscode(_preferred_editor()) else [])+[target]
    app=_default_app(target)
    if app and _is_vscode(app.get_executable()):
        # Keep custom desktop wrappers/profiles intact; only specialize the stock launch.
        try:command=shlex.split(app.get_commandline() or '')
        except ValueError:command=[]
        if len(command)==2 and command[0]==app.get_executable() and command[1] in ('%f','%F','%u','%U'):
            return [app.get_executable(),'--new-window',target]
    return ['gio','open',target]


def _launch_opening(command):
    editor=command[0]=='omarchy' or Path(command[0]).name in ('code','code-insiders')
    # GUI descendants can inherit stderr. A regular file avoids communicate()
    # waiting on an inherited pipe until the user eventually closes the app.
    with tempfile.TemporaryFile() as errors:
        try:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                stderr=errors,env=desktop_env(),start_new_session=True)
        except OSError as exc:
            raise RuntimeError(f'Cannot open target with {command[0]}: {exc}') from exc
        try:
            status=process.wait(timeout=0.5 if editor else 3)
        except subprocess.TimeoutExpired:
            # Some editors stay alive with their window. Reap them without
            # blocking the companion or terminating the newly opened app.
            threading.Thread(target=process.wait,daemon=True).start()
            if editor:return
            raise RuntimeError('The default-app launcher is still running. Check the desktop before trying again.')
        if status:
            errors.seek(0)
            detail=errors.read(4096).decode('utf-8',errors='replace').strip()
            raise RuntimeError(f'Could not open target: {detail or f"{command[0]} exited with status {status}"}')


def open_target(target):
    target,kind=validate_target(target)
    _launch_opening(_opening_command(target,kind))


def focus_thread(store,key):
    t=store.get('thread',key)
    if not t: raise ValueError('Choose a thread.')
    live=resolve_thread(store,key,t['machine'])
    hcall(t['machine'],'agent','focus',live['pane'])
    env=desktop_env()
    if t['machine'].startswith('local'):
        windows=json.loads(run(['hyprctl','clients','-j'],env=env,text=True))
        candidates=[w for w in windows if w.get('class')=='local.herdr']
        if not candidates:
            pids={int(p) for p in run(['pgrep','-x','herdr'],text=True).split()}
            ancestors=set()
            for pid in pids:
                for _ in range(6):
                    try:
                        pid=int(next(line.split()[1] for line in Path('/proc/'+str(pid)+'/status').read_text().splitlines() if line.startswith('PPid:')))
                    except (OSError,StopIteration):break
                    ancestors.add(pid)
            candidates=[w for w in windows if w.get('pid') in ancestors]
        if candidates:
            address=candidates[0]['address']
            run(['hyprctl','dispatch','hl.dsp.focus({ window = '+json.dumps('address:'+address)+' })'],env=env,text=True)
    else:
        m=next((m for m in machines() if m['value']==t['machine']),None)
        if m:subprocess.Popen(['uwsm-app','--','foot','herdr','--remote',m['target']],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return {'message':'Thread opened'}


def voice_start(store,draft_id):
    draft=store.get('draft',draft_id)
    if not draft or draft['status']!='draft':raise ValueError('Choose an unsent draft first.')
    status=json.loads(run(['voxtype','status','--format','json'],text=True))
    if status.get('class')!='idle':raise ValueError('Dictation is already active. Finish that recording first.')
    path=store.directory/('voice-'+draft_id+'.txt')
    path.unlink(missing_ok=True)
    run(['voxtype','record','start','--file='+str(path),'--no-auto-submit','--no-smart-auto-submit','--no-osd'],text=True)
    store.put('voice',{'id':'active','draft':draft_id,'path':str(path)})
    return {'recording':True}


def voice_stop(store):
    active=store.get('voice','active')
    if not active:raise ValueError('Workbench has no active recording.')
    try:
        run(['voxtype','record','stop','--wait','--json','--timeout','100','--wait-file',active['path']],timeout=110,text=True)
        text=Path(active['path']).read_text().strip()
        draft=store.get('draft',active['draft'])
        if draft and draft['status']=='draft':
            store.patch('draft',draft['id'],{'text':(draft['text']+'\n'+text).strip()[:MAX_TEXT]})
        return {'draft':store.get('draft',active['draft']),'transcript':text}
    finally:
        store.delete('voice','active')


def snapshot(store,machine,refresh_live=True):
    error=''
    if refresh_live:
        try:refresh(store,machine)
        except Exception as exc:error=str(exc)[:500]
    threads=store.all('thread')
    threads.sort(key=lambda t:(not t.get('live'),not t.get('focused'),t['title'].lower()))
    return {'threads':threads,'items':[public_item(i) for i in store.all('item')],
            'cards':store.all('card'),'drafts':store.all('draft'),'machines':machines(),
            'connection_error':error,'voice':store.get('voice','active'),
            'settings':store.get('settings','ui',{}),'screenshot_key':screenshot_key()}


def rpc(req,store=None):
    store=store or Store();op=req.get('op')
    if op=='snapshot':return snapshot(store,req.get('machine','local'),req.get('refresh',True))
    if op=='preview':return preview(store,req['id'])
    if op=='browse':return browse_files(req.get('path','~'),req.get('hidden',False),req.get('sort','name'),req.get('descending',False))
    if op=='add':return add_item(store,req['target'],req.get('title',''),req.get('thread',''),req.get('note',''),req.get('reference',False))
    if op=='remove':
        kind=req['kind']
        if kind not in ('item','draft'):raise ValueError('Unsupported removal.')
        store.delete(kind,req['id']);return {}
    if op=='card':
        return save_personal_note(store,req['id'],req['note'])
    if op=='settings':return store.patch('settings','ui',dict(req.get('value',{}),id='ui'),create=True)
    if op=='draft-new':return new_draft(store,req.get('thread',''),req.get('text',''))
    if op=='draft-get':
        draft=store.get('draft',req['id'])
        if not draft:raise ValueError('This draft no longer exists.')
        return draft
    if op=='draft-save':return save_draft(store,req)
    if op=='draft-send':
        save_draft(store,req)
        return deliver(store,req['id'])
    if op=='draft-queue':
        draft=save_draft(store,req)
        from automation import start, queue_coordinator
        destination=draft.get('destination') or store.get('thread',draft['thread'],{})
        coordinator=queue_coordinator(destination.get('machine','local'))
        start(machine=coordinator)
        return queue_capture(store,req['id'],coordinator=coordinator)
    if op=='draft-start-thread':
        # The note becomes the new agent's first message, through the same reviewed queue.
        draft=save_draft(store,req)
        if draft['status']!='draft' or not (draft['text'].strip() or draft_attachments(draft)):
            raise ValueError('Add a note, URL, or screenshot first.')
        target=draft.get('new_thread') or {}
        if not target:raise ValueError('Choose a project for the new thread.')
        missing=missing_attachment(draft)
        if missing:raise ValueError('The attachment '+missing+' is no longer available.')
        thread=start_thread(store,target['dir'],target['agent'],req.get('machine','local'))
        store.patch('draft',draft['id'],{'thread':thread['id'],'destination':thread,'new_thread':None,
            'started_thread':target['agent']},allowed={'draft'})
        from automation import start, queue_coordinator
        coordinator=queue_coordinator(thread['machine'])
        start(machine=coordinator)
        return {'draft':queue_capture(store,draft['id'],coordinator=coordinator),'thread':thread}
    if op=='projects':return projects()
    if op=='issue-projects':return issue_projects()
    if op=='issues':return issue_list(store,req['dir'],req.get('state','open'),req.get('refresh',True))
    if op=='issue':return issue_detail(store,req['dir'],req['number'],req.get('updated',0))
    if op=='issue-draft-save':return save_issue_draft(store,req)
    if op=='issue-handoff':return hand_over_issue(store,req)
    if op=='draft-paste-image':return paste_image(store,req['id'])
    if op=='draft-unqueue':return cancel_capture(store,req['id'])
    if op=='draft-attach':
        target,kind=validate_target(req['target'])
        if kind=='Link':raise ValueError('Paste a link into the note field.')
        return add_attachment(store,req['id'],target)
    if op=='draft-clipboard':
        types=run(['wl-paste','--list-types'],env=desktop_env(),text=True)
        if 'image/png' in types:
            path=store.directory/('capture-'+uuid.uuid4().hex+'.png')
            path.write_bytes(run(['wl-paste','--type','image/png'],env=desktop_env()))
            return add_attachment(store,req['id'],path)
        text=run(['wl-paste','--no-newline'],env=desktop_env(),text=True)
        d=store.get('draft',req['id'])
        return store.patch('draft',req['id'],{'text':(d['text']+'\n'+text).strip()[:MAX_TEXT]},allowed={'draft'})
    if op=='open':open_target(req['target']);return {}
    if op=='copy':
        run(['wl-copy','--',req['text']],env=desktop_env());return {}
    if op=='focus':return focus_thread(store,req['id'])
    if op=='voice-start':return voice_start(store,req['id'])
    if op=='voice-stop':return voice_stop(store)
    raise ValueError('Unknown Workbench action: '+str(op))


def summon(view='shelf',**payload):
    run(['omarchy-shell','shell','summon',PLUGIN,json.dumps({'view':view,**payload})],env=desktop_env(),text=True)


def take_screenshot(store):
    """Omarchy's own picker and capture, saved quietly, then kept in Workbench's private folder."""
    out=run(['omarchy-capture-screenshot','smart','save'],timeout=180,env=desktop_env(),text=True).strip()
    source=Path(out.splitlines()[-1]) if out else None
    if not source or not source.is_file():return None  # cancelled
    path=store.directory/('capture-'+uuid.uuid4().hex+(source.suffix or '.png'))
    shutil.move(str(source),str(path))
    return path


def capture_screenshot(store, thread, draft_id=''):
    """Step the panel aside, take a screenshot and add it to the capture draft it belongs to."""
    run(['omarchy-shell','shell','hide',PLUGIN],env=desktop_env(),text=True)
    time.sleep(.16)
    try:path=take_screenshot(store)
    except (RuntimeError,subprocess.TimeoutExpired):path=None
    if not path:
        if draft_id:summon(capture_view(store,draft_id),draft=draft_id)
        return None
    # Repeated screenshots collect in the thread's open draft until it is sent.
    joined=capture_draft_for(store,thread,draft_id)
    draft=add_attachment(store,joined,path) if joined else new_draft(store,thread,attachments=[str(path)])
    summon(capture_view(store,draft['id']),draft=draft['id'])
    return draft


def screenshot_into(store, target):
    """Add a screenshot to the capture or issue request open in Workbench."""
    draft=target.get('draft','')
    # An issue request gets its draft on the first screenshot, as typing would create it.
    if not draft and target.get('issue'):draft=issue_draft(store,issue_target(target['issue']))['id']
    return capture_screenshot(store,target.get('thread',''),draft)


MODIFIERS = [(64,'Super'),(4,'Ctrl'),(8,'Alt'),(1,'Shift')]


def screenshot_key():
    """The key Hyprland runs Workbench's screenshot with, as the hints name it ('' when none is bound).
    Lua bindings only expose their description, so this looks for "Workbench capture" there,
    preferring the binding that fills an open capture."""
    try:binds=json.loads(run(['hyprctl','binds','-j'],env=desktop_env(),timeout=3,text=True))
    except (OSError,RuntimeError,ValueError,subprocess.TimeoutExpired):return ''
    found=[b for b in binds if isinstance(b,dict) and 'Workbench capture' in str(b.get('description',''))]
    if not found:return ''
    best=min(found,key=lambda b:('into an open' not in b['description'],bin(b.get('modmask',0)).count('1')))
    key=str(best.get('key',''));mods=[name for bit,name in MODIFIERS if best.get('modmask',0)&bit]
    return '+'.join(mods+[key.title() if key.isupper() else key])


def print_target():
    """Where the Print key's screenshot goes: the open Workbench capture, or None for Omarchy's."""
    try:out=run(['omarchy-shell','shell','call',PLUGIN,'printTarget','-'],env=desktop_env(),timeout=3,text=True).strip()
    except (OSError,RuntimeError,subprocess.TimeoutExpired):return None
    try:value=json.loads(out)
    except ValueError:return None  # "unknown" when Workbench isn't loaded
    return value if isinstance(value,dict) and value.get('open') else None


CLIPBOARD_IMAGES = {'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif'}


def paste_image(store, key):
    """Ctrl+V in a capture adds a copied image; copied text is pasted by the editor itself."""
    try:types=run(['wl-paste','--list-types'],env=desktop_env(),text=True).split()
    except (OSError,RuntimeError,subprocess.TimeoutExpired):return {}  # nothing copied
    # Apps such as LibreOffice offer a picture of copied text too; text wins.
    if any(t.startswith('text/plain') or t in ('UTF8_STRING','STRING','TEXT') for t in types):return {}
    kind=next((t for t in CLIPBOARD_IMAGES if t in types),None)
    if not kind:return {}
    path=store.directory/('capture-'+uuid.uuid4().hex+CLIPBOARD_IMAGES[kind])
    path.write_bytes(run(['wl-paste','--type',kind],env=desktop_env()))
    return add_attachment(store,key,path)


def main():
    inherited_umask=os.umask(0o077)
    parser=argparse.ArgumentParser(prog='workbench',description='Workbench: an output shelf, reviewed captures and an issue desk for your coding agents')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('rpc');p.add_argument('request',nargs='?')
    p=sub.add_parser('open',help='Open the shelf, capture, issues or the companion');p.add_argument('view',choices=['shelf','companion','capture','issues'],default='shelf',nargs='?')
    p=sub.add_parser('add',help='Register a finished file or link on the shelf');p.add_argument('target');p.add_argument('--title',default='');p.add_argument('--thread',default='');p.add_argument('--pane',default='');p.add_argument('--machine',default='local');p.add_argument('--reference',action='store_true');p.add_argument('--note',default='')
    p=sub.add_parser('capture',help='Start a capture for a thread from a screenshot, the clipboard or a note');p.add_argument('kind',choices=['screenshot','clipboard','note']);p.add_argument('--thread',default='');p.add_argument('--draft',default='')
    p=sub.add_parser('brief',help='Update the agent-written companion brief; requires an explicit thread')
    p.add_argument('--thread',required=True);p.add_argument('--summary',required=True);p.add_argument('--next',required=True,dest='next_step');p.add_argument('--waiting',default='')
    sub.add_parser('threads',help='List the agent threads, with the IDs that add and brief take')
    sub.add_parser('print',help='Print key: add a screenshot to the open Workbench capture, otherwise take an Omarchy screenshot')
    p=sub.add_parser('setup',help='Add the command, key bindings, menu entries, agent instructions and Herdr companion, asking first')
    p.add_argument('--yes',action='store_true',help='Accept the default answers without asking');p.add_argument('--dry-run',action='store_true',help='Show what would change')
    p=sub.add_parser('remove',help='Take out everything setup added; your outputs and drafts stay')
    p.add_argument('--yes',action='store_true',help='Remove without asking')
    a=parser.parse_args()
    if a.command in ('setup','remove'):
        os.umask(inherited_umask)
        import integration
        return integration.setup(a.yes,a.dry_run) if a.command=='setup' else integration.remove(a.yes)
    store=Store()
    if a.command=='rpc':
        try:print(json.dumps({'ok':True,'data':rpc(json.loads(a.request or sys.stdin.read()),store)},ensure_ascii=False))
        except Exception as exc:print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 1
    elif a.command=='open':
        if a.view=='companion':
            from companion import launch
            launch()
        else:summon(a.view)
    elif a.command=='threads':print(json.dumps(snapshot(store,'local')['threads'],indent=2,ensure_ascii=False))
    elif a.command=='print':
        target=print_target()
        if not target:
            # Hand over to Omarchy's screenshot exactly as the old binding ran it.
            store.db.close();os.umask(inherited_umask)
            os.execvp('omarchy-capture-screenshot',['omarchy-capture-screenshot'])
        screenshot_into(store,target)
    elif a.command=='brief':
        print(json.dumps(update_brief(store,a.thread,a.summary,a.next_step,a.waiting),ensure_ascii=False))
    elif a.command=='add':
        thread=a.thread
        if not thread:
            # Shared agent processes can carry another conversation's environment.
            # Only an explicitly chosen pane or thread may associate a deliverable.
            pane=a.pane
            if pane:
                try:thread=resolve_thread(store,machine=a.machine,pane=pane)['id']
                except (ValueError,RuntimeError):pass
        print(json.dumps(add_item(store,a.target,a.title,thread,a.note,a.reference),ensure_ascii=False))
    elif a.command=='capture':
        # The screenshot key fills an open capture as Print does, so it can stand in for Print.
        target=print_target() if a.kind=='screenshot' and not (a.thread or a.draft) else None
        if target:
            screenshot_into(store,target)
            return 0
        thread=a.thread
        if not thread:
            try:thread=resolve_thread(store)['id']
            except Exception:pass
        if a.kind=='screenshot':
            capture_screenshot(store,thread,a.draft)
            return 0
        else:
            draft=new_draft(store,thread)
            if a.kind=='clipboard':draft=rpc({'op':'draft-clipboard','id':draft['id']},store)
        summon('capture',draft=draft['id'])
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
