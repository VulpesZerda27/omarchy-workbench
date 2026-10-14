#!/usr/bin/env python3
"""Herdr plugin lifecycle: open companions and deliver explicitly queued captures."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from workbench import Store, hcall, normalize, process_capture_queue
from companion import launch, PLUGIN


def session_context(machine=None,socket_path=''):
    base=Path.home()/'.config/herdr'
    if machine:
        path=base/'herdr.sock' if machine=='local' else base/'sessions'/machine.removeprefix('local:')/'herdr.sock'
    else:
        path=Path(socket_path or os.environ.get('HERDR_SOCKET_PATH') or base/'herdr.sock')
        machine='local:'+path.parent.name if path.parent.parent.name=='sessions' else 'local'
    return machine,Path(socket_path) if socket_path else path


def identity(path):
    st=path.stat();return [st.st_dev,st.st_ino]


def queue_coordinator(destination):
    if destination=='local' or destination.startswith('local:'):return destination
    base=Path.home()/'.config/herdr'
    candidates=[base/'herdr.sock',*sorted((base/'sessions').glob('*/herdr.sock'))]
    for path in candidates:
        if path.is_socket() and enabled(path):return session_context(socket_path=str(path))[0]
    raise ValueError('Open a local Herdr session with Workbench Companion enabled to queue a remote capture.')


def enabled(path):
    config=path.parent.parent.parent if path.parent.parent.name=='sessions' else path.parent
    try:
        return any(p['plugin_id']==PLUGIN and p.get('enabled') for p in json.loads((config/'plugins.json').read_text()))
    except (OSError,ValueError):return True  # A registry rewrite should not stop a running worker.


def start(machine=None,socket_path=''):
    machine,path=session_context(machine,socket_path)
    if not enabled(path):raise ValueError('Enable Workbench Companion in Herdr to queue captures.')
    store=Store();current=store.get('automation',machine,{})
    try:
        if current.get('server')==identity(path) and time.time()-current.get('heartbeat',0)<15:
            return current
        with (store.directory/'automation.log').open('a') as log:
            proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'watch','--machine',machine,'--socket',str(path)],
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
        return {'pid':proc.pid,'machine':machine}
    finally:store.db.close()


class Companions:
    def __init__(self):
        self.present=set();self.retry_at={}

    def sync(self,store,machine,snapshot):
        live=normalize(snapshot,machine)
        panes={p['pane_id'] for p in snapshot.get('panes',[])}
        for thread in live:store.put('thread',thread)
        for thread in live:
            key=thread['id'];existing=store.get('companion',key,{})
            if existing.get('pane') in panes:
                self.present.add(key);continue
            if key in self.present:
                # A user can hide a companion without fighting the background worker.
                store.put('companion_settings',{'id':key,'dismissed':True})
                self.present.discard(key)
            if store.get('companion_settings',key,{}).get('dismissed'):continue
            if time.monotonic()<self.retry_at.get(key,0):continue
            try:
                launch(key,focus=False,toggle=False,machine=machine)
                self.present.add(key)
            except Exception as exc:
                self.retry_at[key]=time.monotonic()+30
                print('Companion retry deferred:',key,str(exc),flush=True)
        self.present.intersection_update(t['id'] for t in live)
        return live


class SourceWatch:
    """Notices an update to Workbench's code, so the worker can restart itself on the new version.

    `omarchy plugin update` replaces several files; waiting for one quiet check after the last
    change avoids starting while only some of them are new."""
    FILES=('automation.py','companion.py','workbench.py')

    def __init__(self,folder=None):
        self.folder=Path(folder or Path(__file__).resolve().parent)
        self.started=self.stamp();self.pending=None

    def stamp(self):
        stamps=[]
        for name in self.FILES:
            try:stamps.append((self.folder/name).stat().st_mtime_ns)
            except OSError:stamps.append(0)
        return stamps

    def changed(self):
        now=self.stamp()
        if now==self.started:self.pending=None;return False
        ready=now==self.pending;self.pending=now
        return ready


def watch(machine,socket_path):
    """Run the worker loop; returns True when it stopped because its code was updated."""
    store=Store();machine,path=session_context(machine,socket_path)
    server=identity(path)
    token=hashlib.sha256(str(path).encode()).hexdigest()[:16]
    with (store.directory/('automation-'+token+'.lock')).open('a') as lock:
        # Allow an old server's worker to notice a restart and release its lock.
        while enabled(path):
            if not path.exists() or identity(path)!=server:store.db.close();return
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                current=store.get('automation',machine,{})
                if current.get('server')==server and time.time()-current.get('heartbeat',0)<15:store.db.close();return
                time.sleep(.5)
        else:store.db.close();return
        tracker=Companions();ready_counts={};misses=0;last_error='';remote_errors={};sources=SourceWatch();updated=False
        try:
            while enabled(path):
                if sources.changed():updated=True;break
                try:
                    if identity(path)!=server:break
                    snapshot=hcall(machine,'api','snapshot')['snapshot']
                    store.put('automation',{'id':machine,'pid':os.getpid(),'server':server,'heartbeat':time.time()})
                    live=tracker.sync(store,machine,snapshot)
                    process_capture_queue(store,machine,live,ready_counts)
                    # Each reviewed remote capture names the local session servicing it.
                    remotes={d.get('destination',{}).get('machine','') for d in store.all('draft')
                        if d['status']=='queued' and d.get('queue_coordinator','local')==machine}
                    for remote in remotes:
                        if not remote or remote=='local' or remote.startswith('local:'):continue
                        try:
                            remote_token=hashlib.sha256(remote.encode()).hexdigest()[:16]
                            with (store.directory/('queue-'+remote_token+'.lock')).open('a') as remote_lock:
                                try:fcntl.flock(remote_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                                except BlockingIOError:continue
                                remote_live=normalize(hcall(remote,'api','snapshot')['snapshot'],remote)
                                process_capture_queue(store,remote,remote_live,ready_counts,coordinator=machine)
                            remote_errors.pop(remote,None)
                        except Exception as exc:
                            # No submission occurred when the read failed; keep the queue saved.
                            if remote_errors.get(remote)!=str(exc):
                                print('Queued remote capture waiting:',remote,str(exc),flush=True)
                                remote_errors[remote]=str(exc)
                    misses=0;last_error=''
                except FileNotFoundError:
                    misses+=1
                    if misses>=3:break
                except Exception as exc:
                    if str(exc)!=last_error:print('Workbench automation:',str(exc),flush=True);last_error=str(exc)
                time.sleep(2)
        finally:
            if store.get('automation',machine,{}).get('pid')==os.getpid():store.delete('automation',machine)
            store.db.close()
    return updated


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['start','watch'])
    parser.add_argument('--machine',default=None);parser.add_argument('--socket',default='')
    args=parser.parse_args()
    if args.command=='start':print(json.dumps(start(args.machine,args.socket)))
    elif watch(args.machine,args.socket):
        # Updated in place (omarchy plugin update): carry on with the new code.
        print('Workbench automation: restarting with the updated code',flush=True)
        os.execv(sys.executable,[sys.executable,str(Path(__file__).resolve()),'watch','--machine',args.machine or 'local','--socket',args.socket])


if __name__=='__main__':main()
