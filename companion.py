#!/usr/bin/env python3
"""Herdr-native per-thread companion. Uses the terminal's live theme palette."""
import argparse
import curses
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
import time
import tomllib
from urllib.parse import urlparse

from workbench import Store, hcall, refresh, resolve_thread, add_item, open_target, summon, save_personal_note, preview

PLUGIN='io.github.vulpeszerda27.workbench-companion'


class FocusWindow:
    """Consume terminal focus reports in every view, including note editors."""
    def __init__(self,win,focused=False):
        self.win=win
        self.focused=focused

    def __getattr__(self,name):return getattr(self.win,name)

    def get_wch(self):
        key=self.win.get_wch()
        if isinstance(key,int):
            # ncurses exposes these as extended terminfo keys on some systems.
            name=curses.keyname(key)
            if name in (b'kxIN',b'kxOUT'):
                self.focused=name==b'kxIN'
                raise curses.error('Focus changed; redraw')
        if key!='\x1b':return key
        # Other terminfo entries leave xterm's ESC [ I / ESC [ O undecoded.
        # Keep ordinary Escape and any following user input intact.
        suffix=[];delay=self.win.getdelay();self.win.timeout(35)
        try:
            suffix.append(self.win.get_wch())
            if suffix==['[']:suffix.append(self.win.get_wch())
        except curses.error:pass
        finally:self.win.timeout(delay)
        if suffix in (['[','I'],['[','O']):
            self.focused=suffix[-1]=='I'
            raise curses.error('Focus changed; redraw')
        for ch in reversed(suffix):
            if isinstance(ch,str):curses.unget_wch(ch)
            else:curses.ungetch(ch)
        return key


@contextlib.contextmanager
def focus_input(win,machine,own):
    # Subscribe before taking the initial snapshot so later changes are queued.
    sys.stdout.write('\x1b[?1004h');sys.stdout.flush()
    try:
        try:focused=bool(own) and hcall(machine,'api','snapshot')['snapshot'].get('focused_pane_id')==own
        except (RuntimeError,subprocess.TimeoutExpired,KeyError,ValueError):focused=False
        yield FocusWindow(win,focused)
    finally:
        sys.stdout.write('\x1b[?1004l');sys.stdout.flush()


def invoking_machine():
    socket=os.environ.get('HERDR_SOCKET_PATH','')
    if socket:
        path=Path(socket)
        return 'local:'+path.parent.name if path.parent.parent.name=='sessions' else 'local'
    return os.environ.get('WORKBENCH_MACHINE','local')


@contextlib.contextmanager
def thread_lock(store,key):
    with (store.directory/('companion-'+key+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def dismiss(store,key,own=''):
    with thread_lock(store,key):
        existing=store.get('companion',key,{})
        # An older TUI must not remove a replacement panel's registration.
        if own and existing.get('pane') not in (None,own):return
        store.put('companion_settings',{'id':key,'dismissed':True})
        store.delete('companion',key)


def launch(thread_key='', *, focus=True, toggle=True, machine=''):
    store=Store()
    try:
        machine=machine or store.get('thread',thread_key,{}).get('machine') or invoking_machine()
        if machine!='local' and not machine.startswith('local:'):
            raise ValueError('The companion is installed on this computer. Remote threads can still use the desktop shelf and capture panel.')
        snapshot=hcall(machine,'api','snapshot')['snapshot']
        focused=snapshot.get('focused_pane_id')
        if not thread_key:
            match=next((x for x in store.all('companion') if x.get('pane')==focused and
                store.get('thread',x['id'],{}).get('machine')==machine),None)
            if match: thread_key=match['id']
            else:thread_key=resolve_thread(store,machine=machine,pane=focused)['id']
        thread=resolve_thread(store,thread_key,machine)
        # The manual shortcut and auto-open worker can notice the same thread together.
        with thread_lock(store,thread_key):
            return open_for_thread(store,thread,focus=focus,toggle=toggle)
    finally:store.db.close()


def open_for_thread(store,thread,*,focus=True,toggle=True):
    thread_key=thread['id'];machine=thread['machine']
    # Recheck inside launch's lock: a user may have dismissed while we waited.
    if not focus and store.get('companion_settings',thread_key,{}).get('dismissed'):return
    snapshot=hcall(machine,'api','snapshot')['snapshot']
    focused=snapshot.get('focused_pane_id')
    existing=store.get('companion',thread_key)
    if existing and any(p['pane_id']==existing['pane'] for p in snapshot.get('panes',[])):
        if toggle and focused==existing['pane']:
            store.put('companion_settings',{'id':thread_key,'dismissed':True})
            hcall(machine,'plugin','pane','close',existing['pane'])
            store.delete('companion',thread_key)
        elif focus:hcall(machine,'plugin','pane','focus',existing['pane'])
        return
    if focus:store.put('companion_settings',{'id':thread_key,'dismissed':False})
    result=hcall(machine,'plugin','pane','open','--plugin',PLUGIN,'--entrypoint','companion',
        '--placement','split','--direction','right',
        '--target-pane',thread['pane'],'--cwd',thread['cwd'],
        '--env','WORKBENCH_THREAD='+thread_key,'--env','WORKBENCH_MACHINE='+machine,'--focus' if focus else '--no-focus')
    # API versions wrap the newly opened pane differently. Locate the returned handle.
    def pane_id(value):
        if isinstance(value,dict):
            if value.get('pane_id'):return value['pane_id']
            for v in value.values():
                hit=pane_id(v)
                if hit:return hit
        return ''
    pane=pane_id(result)
    if pane:
        store.put('companion',{'id':thread_key,'pane':pane})
        # Herdr opens plugin splits at 50/50; give the conversation 68%.
        hcall(machine,'pane','resize','--pane',pane,'--direction','right','--amount','0.18')


def put(win,y,x,text,attr=0,width=None):
    h,w=win.getmaxyx()
    if y<0 or y>=h or x>=w:return
    text=''.join(ch if ch.isprintable() else ' ' for ch in str(text))
    try:win.addnstr(y,x,text,max(0,min(width if width is not None else w-x-1,w-x-1)),attr)
    except curses.error:pass


# Palette slots redefined from the Omarchy theme, and the colour pairs drawn with them.
ACCENT,MUTED,ROW_BG,ROW_FG=16,17,18,19
P_ACCENT,P_MUTED,P_ROW,P_GREEN,P_YELLOW,P_RED,P_ROW_ACCENT=range(1,8)
# Herdr's colour-dot status indicators, in the terminal colours its sidebar uses.
STATES={'working':('●',P_YELLOW),'blocked':('●',P_RED),'done':('●',P_GREEN),
    'idle':('○',P_GREEN),'closed':('○',P_MUTED)}


def state_glyph(status):return STATES.get(status,('·',P_MUTED))


def apply_palette():
    for pair,color in ((P_GREEN,curses.COLOR_GREEN),(P_YELLOW,curses.COLOR_YELLOW),(P_RED,curses.COLOR_RED)):
        try:curses.init_pair(pair,color,-1)
        except curses.error:pass
    try:
        colors=tomllib.loads((Path.home()/'.local/state/omarchy/current/theme/colors.toml').read_text())
        fg=colors['foreground']
        # Herdr highlights rows with these selection colours (its active_row_bg).
        slots={ACCENT:colors['accent'],MUTED:colors.get('muted') or colors.get('dark_foreground') or fg,
            ROW_BG:colors.get('selection_background') or colors.get('selection') or colors['accent'],
            ROW_FG:colors.get('selection_foreground') or colors.get('bright_foreground') or fg}
        for n,value in slots.items():
            hexcolor=value.lstrip('#')
            curses.init_color(n,*[int(hexcolor[i:i+2],16)*1000//255 for i in (0,2,4)])
        curses.init_pair(P_ACCENT,ACCENT,-1);curses.init_pair(P_MUTED,MUTED,-1)
        curses.init_pair(P_ROW,ROW_FG,ROW_BG);curses.init_pair(P_ROW_ACCENT,ACCENT,ROW_BG)
    except (OSError,KeyError,ValueError,AttributeError,curses.error):
        curses.init_pair(P_ACCENT,curses.COLOR_YELLOW,-1)
        # Bright black is the theme's muted tone in Omarchy terminal palettes.
        curses.init_pair(P_MUTED,8 if curses.COLORS>=16 else curses.COLOR_WHITE,-1)
        curses.init_pair(P_ROW,curses.COLOR_BLACK,curses.COLOR_YELLOW)
        curses.init_pair(P_ROW_ACCENT,curses.COLOR_BLACK,curses.COLOR_YELLOW)


def when(ts,now=None):
    """Glanceable time for brief and output metadata."""
    now=now or time.time();age=now-ts;t=time.localtime(ts)
    if age<60:return 'just now'
    if age<3600:return str(int(age//60))+'m ago'
    if time.localtime(now)[:3]==t[:3]:return time.strftime('%H:%M',t)
    if age<6*86400:return time.strftime('%a %H:%M',t)
    return time.strftime('%-d %b',t)


def fit_hints(groups,width,lines=2):
    """Lay out (key,label) hints in rows. Each group starts a row; overflow wraps, then drops."""
    rows=[]
    for group in groups:
        row=[];used=0
        for key,label in group:
            size=len(key)+1+len(label)
            if row and used+2+size>width:rows.append(row);row=[];used=0
            if size<=width:used+=(2 if row else 0)+size;row.append((key,label))
        if row:rows.append(row)
    return rows[:lines]


def put_hint_row(win,y,x,row):
    """Herdr-style hints: the key in the text colour, its label muted."""
    for key,label in row:
        put(win,y,x,key,curses.A_BOLD);x+=len(key)+1
        put(win,y,x,label,curses.color_pair(P_MUTED));x+=len(label)+2
    return x


def put_footer(win,groups,lines=2):
    """Draw hints against the bottom edge and return the first row they use."""
    h,w=win.getmaxyx();rows=fit_hints(groups,max(10,w-4),lines)
    for n,row in enumerate(rows):put_hint_row(win,h-len(rows)+n,2,row)
    return h-len(rows)


def put_section(win,y,title,meta=''):
    """A Herdr sidebar heading: lowercase and bold, with muted detail on the right."""
    h,w=win.getmaxyx();put(win,y,2,title,curses.A_BOLD)
    if meta and 2+len(title)+2+len(meta)<=w-2:put(win,y,w-2-len(meta),meta,curses.color_pair(P_MUTED))


def edit(win,title,initial='',multiline=True,on_save=None):
    lines=initial.split('\n') or [''];row=len(lines)-1;col=len(lines[row]);top=0
    curses.curs_set(1);win.timeout(250)
    changed=False
    try:
        while True:
            h,w=win.getmaxyx();win.erase()
            put(win,1,2,title,curses.color_pair(P_ACCENT)|curses.A_BOLD)
            if on_save:
                put(win,2,2,'Saved as you type',curses.color_pair(P_MUTED))
                put_hint_row(win,2,21,(('esc/ctrl+s','return'),))
            else:put_hint_row(win,2,2,(('↵','accept'),('esc','cancel')))
            put(win,3,2,'─'*max(1,w-4),curses.color_pair(P_MUTED))
            usable=max(1,h-7)
            if row<top:top=row
            if row>=top+usable:top=row-usable+1
            xscroll=max(0,col-max(1,w-7))
            for n,line in enumerate(lines[top:top+usable]):put(win,4+n,2,line[xscroll:],width=w-4)
            try:win.move(4+row-top,2+col-xscroll)
            except curses.error:pass
            win.refresh()
            try:k=win.get_wch()
            except curses.error:continue
            if k in ('\x1b','\x13'):
                return '\n'.join(lines) if on_save or k=='\x13' else None
            if k in ('\n','\r',curses.KEY_ENTER):
                if not multiline:return '\n'.join(lines)
                lines.insert(row+1,lines[row][col:]);lines[row]=lines[row][:col];row+=1;col=0;changed=True
            elif k in (curses.KEY_BACKSPACE,'\x7f','\b'):
                if col:lines[row]=lines[row][:col-1]+lines[row][col:];col-=1;changed=True
                elif row:
                    col=len(lines[row-1]);lines[row-1]+=lines[row];lines.pop(row);row-=1;changed=True
            elif k==curses.KEY_DC:
                if col<len(lines[row]):lines[row]=lines[row][:col]+lines[row][col+1:];changed=True
                elif row<len(lines)-1:lines[row]+=lines.pop(row+1);changed=True
            elif k==curses.KEY_LEFT:
                if col:col-=1
                elif row:row-=1;col=len(lines[row])
            elif k==curses.KEY_RIGHT:
                if col<len(lines[row]):col+=1
                elif row<len(lines)-1:row+=1;col=0
            elif k==curses.KEY_UP:row=max(0,row-1);col=min(col,len(lines[row]))
            elif k==curses.KEY_DOWN:row=min(len(lines)-1,row+1);col=min(col,len(lines[row]))
            elif k in (curses.KEY_HOME,'\x01'):col=0
            elif k in (curses.KEY_END,'\x05'):col=len(lines[row])
            elif k=='\x15':lines[row]=lines[row][col:];col=0;changed=True
            elif isinstance(k,str) and k.isprintable():lines[row]=lines[row][:col]+k+lines[row][col:];col+=len(k);changed=True
            if changed and on_save:
                on_save('\n'.join(lines)[:60000]);changed=False
    finally:curses.curs_set(0);win.timeout(750)


BRIEF_LABELS=('What changed','Next','Waiting on you')
EMPTY_BRIEF='Your agent keeps a short brief here as work progresses: what changed, what comes next, and anything waiting on you. No manual notes needed.'


def current_brief(card,thread):
    brief=card.get('brief',{})
    # A brief from an earlier conversation in the same pane is not this thread's status.
    if brief.get('agent_session') and thread.get('agent_session') and brief['agent_session']!=thread['agent_session']:
        return {}
    return brief


def brief_text(card,thread):
    brief=current_brief(card,thread)
    if not brief:return EMPTY_BRIEF
    return '\n\n'.join(label+'\n'+text for label,text in
        zip(BRIEF_LABELS,(brief['summary'],brief['next'],brief.get('waiting') or 'Nothing at the moment.')))


KEY_LINE=re.compile(r'^(\S{1,3})  (?=\S)')


def read_view(win,title,text,actions=(),headings=()):
    """Read longer briefs or help without accidentally editing the agent's note.

    Only the companion's own texts pass headings; previewed files render verbatim."""
    offset=0
    while True:
        h,w=win.getmaxyx();win.erase();lines=[]
        for line in text.splitlines():
            key=KEY_LINE.match(line) if headings else None
            heading=line in headings
            for n,part in enumerate(textwrap.wrap(line,max(10,w-4),subsequent_indent='   ' if key else '') or ['']):
                lines.append((part,heading,key.group(1) if key and n==0 else ''))
        groups=[[('↑↓','scroll'),('esc','back')]+([('e','personal note'),('a','pin a reference')] if actions else [])]
        rows=fit_hints(groups,max(10,w-10));count=max(1,h-4-len(rows)-1)
        offset=max(0,min(offset,max(0,len(lines)-count)))
        put(win,1,2,title,curses.color_pair(P_ACCENT)|curses.A_BOLD)
        put(win,2,2,'─'*max(1,w-4),curses.color_pair(P_MUTED))
        for n,(line,heading,key) in enumerate(lines[offset:offset+count]):
            put(win,3+n,2,line,curses.color_pair(P_ACCENT)|curses.A_BOLD if heading else 0)
            if key:put(win,3+n,2,key,curses.color_pair(P_ACCENT)|curses.A_BOLD)
        for n,row in enumerate(rows):put_hint_row(win,h-len(rows)+n,2,row)
        if len(lines)>count:
            position=str(round(100*offset/max(1,len(lines)-count)))+'%'
            put(win,h-len(rows),w-2-len(position),position,curses.color_pair(P_MUTED))
        win.refresh()
        try:k=win.get_wch()
        except curses.error:continue
        if k in ('q','\x1b'):return None
        if k in actions:return k
        if k in ('j',curses.KEY_DOWN):offset+=1
        elif k in ('k',curses.KEY_UP):offset-=1
        elif k in (' ',curses.KEY_NPAGE):offset+=count
        elif k==curses.KEY_PPAGE:offset-=count


HELP='''This panel is your thread at a glance.

THREAD BRIEF
The agent maintains what changed, the next step, and anything waiting on you. It updates at meaningful milestones and before handing work back. You do not need to fill it in.

OPEN
The thread's outputs come first, newest first, then pinned files and links. Enter or a click opens the system's default app. Source files open in your chosen Omarchy editor; executable apps and launchers reveal their folder.
p  Preview the selected item. Text stays here; images and PDFs open on the shelf.
o  Outputs & previews: the shelf, filtered to files and links from this thread.
c  Send a note or screenshot: a draft for this agent. Write, attach, paste or dictate, then review before sending.
The o and c actions stay at the bottom of the list, below a separator when there are files or links.

OPTIONAL
e  Keep a personal note, separate from the agent's brief.
a  Pin a useful file or link.
d  Unpin the selected reference; keep the original.
b  Read the full brief.

MOVING AROUND
Tab returns to the agent. Herdr's pane keys (prefix, then h or l by default) move between the agent and this panel, and your Herdr prefix, then i, opens it again after you close it. q closes this panel.

Existing agent sessions that have not loaded the new instructions may need one reminder: "Read the Workbench instructions and keep this thread's brief updated."'''
HELP_HEADINGS=frozenset(('THREAD BRIEF','OPEN','OPTIONAL','MOVING AROUND'))


def open_reference(win,store,item):
    open_target(item['target'])


def preview_reference(win,store,item):
    if item['kind'] not in ('Image','PDF'):
        content=preview(store,item['id'])
        if not content['exists']:raise ValueError('The original file has moved or was removed.')
        if content['text'] or (item['kind']!='Link' and Path(item['target']).stat().st_size==0):
            read_view(win,item['title'],content['text'] or '(Empty file)')
            return
    thread=store.get('thread',item['thread'],{})
    summon('shelf',thread=item['thread'],machine=thread.get('machine','local'),item=item['id'])


def item_meta(item,pinned=False,now=None):
    parts=[item['kind'].lower()]
    if item['kind']=='Link':parts.append(urlparse(item['target']).netloc)
    elif not Path(item['target']).exists():parts.append('missing')
    if item.get('updated'):parts.append(when(item['updated'],now))
    if pinned:parts.append('pinned')
    return ' · '.join(p for p in parts if p)


def open_actions(items,draft_count=0):
    outputs=[i for i in items if not i['reference']]
    refs=[i for i in items if i['reference']]
    pinned={i['target'] for i in refs}
    actions=[];seen=set()
    for item in outputs+refs:
        if item['target'] in seen:continue
        seen.add(item['target'])
        actions.append((item['title'],item_meta(item,item['target'] in pinned),item['id']))
    count=len(outputs)
    return actions+[
        ('Outputs & previews',('1 file or link from this thread' if count==1 else
            str(count)+' files and links from this thread' if count else 'No outputs from this thread yet'),'o'),
        ('Send a note or screenshot','Review a draft before sending'+(' · '+str(draft_count)+' saved' if draft_count else ''),'c')]


MAIN_HINTS=((('↑↓','choose'),('↵','open'),('p','preview'),('tab','agent')),
    (('o','outputs'),('c','capture'),('?','help'),('q','close')))


def wrap_lines(text,width):
    return [part for line in text.splitlines() for part in textwrap.wrap(line,width) or ['']]


def brief_lines(brief,width):
    """The brief as (text, style) rows; an empty waiting field folds into one muted line."""
    if not brief:return [(line,'muted') for line in wrap_lines(EMPTY_BRIEF,width)]
    lines=[]
    for label,text in zip(BRIEF_LABELS,(brief['summary'],brief['next'])):
        lines+=[(label,'label')]+[(line,'') for line in wrap_lines(text,width)]+[('','')]
    waiting=(brief.get('waiting') or '').strip()
    if not waiting:return lines+[('Nothing waiting on you','muted')]
    return lines+[(BRIEF_LABELS[2],'waiting')]+[(line,'') for line in wrap_lines(waiting,width)]


def put_meta(win,y,x,meta,attr,flag=True):
    """Row detail; a moved or deleted original is flagged in red outside the selection."""
    for n,part in enumerate(meta.split(' · ')):
        if n:put(win,y,x,' · ',attr);x+=3
        put(win,y,x,part,curses.color_pair(P_RED) if flag and part=='missing' else attr);x+=len(part)


def open_summary(items):
    outputs=len({i['target'] for i in items if not i['reference']});pinned=len({i['target'] for i in items if i['reference']})
    return ' · '.join(part for part in (
        str(outputs)+(' output' if outputs==1 else ' outputs') if outputs else '',
        str(pinned)+' pinned' if pinned else '') if part)


def open_action_layout(count,link_count,selected,scroll,rows):
    """Fit selectable rows and the non-interactive divider into the viewport."""
    rows=max(2,rows);scroll=max(0,min(scroll,selected,count-1))
    while True:
        placements=[];separator=None;y=0
        for idx in range(scroll,count):
            divided=idx==link_count and idx>scroll
            if y+2+int(divided)>rows:break
            if divided:separator=y;y+=1
            placements.append((idx,y));y+=2
        if any(idx==selected for idx,_ in placements):return scroll,placements,separator
        scroll+=1


def tui(win):
    key=os.environ.get('WORKBENCH_THREAD','');store=Store()
    try:
        thread=store.get('thread',key)
        if not thread:raise ValueError('This companion needs a thread. Open it through Herdr.')
        own=os.environ.get('HERDR_PANE_ID','')
        if own:store.put('companion',{'id':key,'pane':own})
        curses.start_color();curses.use_default_colors();curses.curs_set(0);apply_palette()
        curses.mousemask(curses.ALL_MOUSE_EVENTS);curses.mouseinterval(180)
        curses.set_escdelay(35)
        win.keypad(True);win.timeout(750)
        with focus_input(win,thread['machine'],own) as win:
            run_tui(win,store,thread,key,own)
    finally:store.db.close()


def run_tui(win,store,thread,key,own):
    selected=0;scroll=0;status=None;last_refresh=0;theme_mtime=0;actions=[];by_id={}
    while True:
        now=time.time()
        if now-last_refresh>6:
            try:refresh(store,thread['machine']);thread=store.get('thread',key,thread)
            except Exception:pass
            title=''.join(ch for ch in thread['title'] if ch.isprintable())
            sys.stdout.write('\x1b]0;'+title+'\x07')
            sys.stdout.flush()
            last_refresh=now
        try:
            m=(Path.home()/'.local/state/omarchy/current/theme/colors.toml').stat().st_mtime_ns
            if m!=theme_mtime:apply_palette();theme_mtime=m
        except OSError:pass
        card=store.get('card',key,{})
        selected_id=actions[selected][2] if actions else None
        selected_target=by_id.get(selected_id,{}).get('target')
        items=[i for i in store.all('item') if i['thread']==key]
        by_id={i['id']:i for i in items}
        refs_by_target={i['target']:i for i in items if i['reference']}
        drafts=[d for d in store.all('draft') if d['thread']==key and d['status']=='draft' and not d.get('issue')]
        actions=open_actions(items,len(drafts))
        selected=next((n for n,a in enumerate(actions) if a[2]==selected_id or
            selected_target and by_id.get(a[2],{}).get('target')==selected_target),min(selected,len(actions)-1))
        h,w=win.getmaxyx();win.erase()
        accent=curses.color_pair(P_ACCENT);dim=curses.color_pair(P_MUTED);bold=curses.A_BOLD
        glyph,pair=state_glyph(thread['status'])
        put(win,1,2,glyph,curses.color_pair(pair));put(win,1,4,thread['project'],bold)
        put(win,1,4+len(thread['project']),' · '+thread['agent']+' · '+thread['status'],dim)
        y=3
        for line in textwrap.wrap(thread['title'] or thread['project'],max(10,w-4))[:3]:put(win,y,2,line,accent|bold);y+=1
        y+=1
        brief=current_brief(card,thread);note=brief_text(card,thread)
        note_start=y
        put_section(win,y,'thread brief',brief.get('author','agent')+' · '+when(brief['updated'],now) if brief else 'kept by your agent');y+=2
        note_lines=brief_lines(brief,max(10,w-4))
        note_limit=max(0,min(24,h-y-14-(2 if card.get('note') else 0)))
        for text,style in note_lines[:note_limit]:
            if style=='waiting':put(win,y,2,'●',curses.color_pair(P_RED));put(win,y,4,text,accent|bold)
            else:put(win,y,2,text,{'label':accent,'muted':dim}.get(style,0))
            y+=1
        if len(note_lines)>note_limit:put(win,y,2,'…',dim);put_hint_row(win,y,4,(('b','read the full brief'),));y+=1
        note_end=y
        if card.get('note'):
            y+=1;put(win,y,2,'Personal note saved',dim);put_hint_row(win,y,23,(('e','read or edit'),));y+=1
        y+=1;put_section(win,y,'open',open_summary(items));y+=2
        action_start=y
        footer_top=h-len(fit_hints(MAIN_HINTS,max(10,w-4)))
        scroll,placements,separator=open_action_layout(len(actions),len(actions)-2,selected,scroll,footer_top-3-y)
        if separator is not None:put(win,y+separator,3,'─'*max(1,w-6),dim)
        for idx,row in placements:
            title,meta,ident=actions[idx]
            # Herdr marks the current row with its active-row colours across both lines.
            active=win.focused and idx==selected;band=curses.color_pair(P_ROW);keyed=ident in ('o','c')
            if active:
                for line in (0,1):put(win,y+row+line,1,' '*max(1,w-3),band)
            put(win,y+row,3,title,band|bold if active else 0,width=w-6-(2 if keyed else 0))
            if meta:put_meta(win,y+row+1,3,meta,band if active else dim,flag=not active)
            if keyed:put(win,y+row,w-4,ident,(curses.color_pair(P_ROW_ACCENT) if active else accent)|bold)
        if status:
            kind,text=status
            mark,attr={'error':('× ',curses.color_pair(P_RED)),'ok':('✓ ',curses.color_pair(P_GREEN))}.get(kind,('',accent))
            for n,line in enumerate(textwrap.wrap(mark+text,max(10,w-4))[:2]):put(win,footer_top-2+n,2,line,attr)
        put_footer(win,MAIN_HINTS)
        win.refresh()
        try:k=win.get_wch()
        except curses.error:continue
        status=None
        if k==curses.KEY_MOUSE:
            try:_,mx,my,_,buttons=curses.getmouse()
            except curses.error:continue
            if buttons & curses.BUTTON4_PRESSED:selected=max(0,selected-1);continue
            if buttons & getattr(curses,'BUTTON5_PRESSED',0):selected=min(len(actions)-1,selected+1);continue
            if not buttons & (curses.BUTTON1_CLICKED|curses.BUTTON1_DOUBLE_CLICKED):continue
            if note_start<=my<=note_end:k='b'
            else:
                hit=next((idx for idx,row in placements if action_start+row<=my<action_start+row+2),None)
                if hit is None:continue
                selected=hit;k='\n'
        if k in ('q','\x1b'):break
        if k in ('j',curses.KEY_DOWN):selected=min(selected+1,len(actions)-1);continue
        if k in ('k',curses.KEY_UP):selected=max(0,selected-1);continue
        if k=='\t':
            try:
                hcall(thread['machine'],'agent','focus',thread['pane'])
                # Herdr's agent-focus API does not emit a focus-lost report.
                win.focused=False
            except Exception as e:status=('error',str(e))
            continue
        command=actions[selected][2] if k in ('\n','\r',curses.KEY_ENTER) else k
        try:
            if command=='?':command=read_view(win,'Companion · what is here',HELP,actions=('e','a'),headings=HELP_HEADINGS)
            # o and c mirror Super+Alt+O/C; s is the shelf's earlier key.
            if command in ('o','s'):summon('shelf',thread=key,machine=thread['machine'])
            elif command=='c':summon('capture',thread=key,machine=thread['machine'])
            elif command=='b':read_view(win,'Thread brief',note,headings=BRIEF_LABELS)
            elif command=='p':
                item=by_id.get(actions[selected][2])
                if item:preview_reference(win,store,item)
                else:status=('info','Select a file or link to preview.')
            elif command=='e':
                edit(win,'Your personal note · optional',card.get('note',''),on_save=lambda note:save_personal_note(store,key,note))
                status=('ok','Personal note saved; agent brief kept')
            elif command=='a':
                target=edit(win,'Reference · paste a file path or URL',multiline=False)
                if target:
                    title=edit(win,'Reference title (optional)',multiline=False)
                    if title is not None:add_item(store,target,title,key,reference=True);status=('ok','Reference added')
            elif command=='d':
                item=by_id.get(actions[selected][2])
                ref=refs_by_target.get(item['target']) if item else None
                if ref:store.delete('item',ref['id']);status=('ok','Reference removed; original kept')
                else:status=('info','Select a pinned reference to unpin it. Outputs stay on the shelf.')
            elif command in by_id:
                open_reference(win,store,by_id[command])
        except Exception as exc:status=('error',str(exc))
    dismiss(store,key,own)


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['open','tui']);p.add_argument('--thread',default='');a=p.parse_args()
    if a.command=='open':launch(a.thread)
    else:
        curses.wrapper(tui)
        own=os.environ.get('HERDR_PANE_ID')
        if own:
            try:hcall(os.environ.get('WORKBENCH_MACHINE','local'),'plugin','pane','close',own)
            except Exception:pass

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(str(exc),file=__import__('sys').stderr)
        raise SystemExit(1)
