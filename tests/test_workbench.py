import copy
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workbench as w

class Case(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=w.Store(self.tmp.name)
        self.addCleanup(self.store.db.close)
        self.raw={'focused_pane_id':'w1:p1','workspaces':[{'workspace_id':'w1','label':'garden'}],
            'panes':[{'pane_id':'w1:p1','workspace_id':'w1','tab_id':'w1:t1','terminal_id':'term-a',
            'agent':'codex','agent_status':'idle','label':'A thread','cwd':'/home/user/Projects/garden','agent_session':{'value':'conversation-a'}}]}
        self.thread=w.normalize(self.raw,'local')[0];self.store.put('thread',self.thread)
        self.file=Path(self.tmp.name)/'brief with spaces.md';self.file.write_text('# Hello')
    def draft(self):return w.new_draft(self.store,self.thread['id'],'Feedback')

class WorkbenchTest(Case):
    def test_thread_identity_survives_rename_and_pane_move(self):
        raw=copy.deepcopy(self.raw);raw['panes'][0].update(label='Changed',pane_id='w2:p8',workspace_id='w2')
        self.assertEqual(w.normalize(raw,'local')[0]['id'],self.thread['id'])
    def test_machine_ids_cannot_collide(self):
        self.assertNotEqual(w.normalize(self.raw,'remote')[0]['id'],self.thread['id'])
    def test_new_terminal_does_not_reuse_thread_notes(self):
        raw=copy.deepcopy(self.raw);raw['panes'][0]['terminal_id']='term-b'
        self.assertNotEqual(w.normalize(raw,'local')[0]['id'],self.thread['id'])
    def test_registry_is_idempotent_and_original_is_never_deleted(self):
        a=w.add_item(self.store,str(self.file),thread=self.thread['id'])
        b=w.add_item(self.store,str(self.file),title='Updated',thread=self.thread['id'])
        self.assertEqual(a['id'],b['id']);self.assertEqual(len(self.store.all('item')),1)
        self.store.delete('item',a['id']);self.assertTrue(self.file.exists())
    def test_notes_and_drafts_survive_reopening_store(self):
        self.store.put('card',{'id':self.thread['id'],'note':'Next step'});draft=self.draft()
        other=w.Store(self.tmp.name)
        self.addCleanup(other.db.close)
        self.assertEqual(other.get('draft',draft['id'])['text'],'Feedback')
        self.assertEqual(other.get('card',self.thread['id'])['note'],'Next step')
    def test_unsafe_url_schemes_are_rejected(self):
        for url in ['javascript:alert(1)','data:text/html,x','ssh://host','file://remote/etc/passwd']:
            with self.subTest(url=url),self.assertRaises(ValueError):w.validate_target(url)
    def test_preview_missing_file_is_clear(self):
        item=w.add_item(self.store,str(self.file));self.file.unlink()
        self.assertFalse(w.preview(self.store,item['id'])['exists'])
    def test_busy_and_blocked_agents_never_receive_input(self):
        draft=self.draft()
        for status in ['working','blocked','unknown']:
            raw=copy.deepcopy(self.raw);raw['panes'][0]['agent_status']=status
            with patch.object(w,'hcall',return_value={'snapshot':raw}) as call:
                with self.assertRaises(ValueError):w.deliver(self.store,draft['id'])
                self.assertEqual(call.call_count,1)
            self.assertEqual(self.store.get('draft',draft['id'])['status'],'draft')
    def test_changed_conversation_rejected_even_after_registry_refresh(self):
        draft=self.draft();raw=copy.deepcopy(self.raw);raw['panes'][0]['agent_session']['value']='conversation-b'
        self.store.put('thread',w.normalize(raw,'local')[0])
        w.save_draft(self.store,{'id':draft['id'],'text':'Edited after refresh','thread':draft['thread']})
        with patch.object(w,'hcall',return_value={'snapshot':raw}):
            with self.assertRaisesRegex(ValueError,'different conversation'):w.deliver(self.store,draft['id'])
    def test_delivery_exact_once(self):
        draft=self.draft()
        with patch.object(w,'hcall',side_effect=[{'snapshot':self.raw},{}]) as call:
            self.assertEqual(w.deliver(self.store,draft['id'])['status'],'sent')
            self.assertEqual(call.call_args.args,('local','agent','prompt','w1:p1','Feedback'))
            with self.assertRaises(ValueError):w.deliver(self.store,draft['id'])
            self.assertEqual(call.call_count,2)
    def test_uncertain_submission_is_not_retried(self):
        draft=self.draft()
        with patch.object(w,'hcall',side_effect=[{'snapshot':self.raw},RuntimeError('connection lost')]) as call:
            with self.assertRaises(RuntimeError):w.deliver(self.store,draft['id'])
            self.assertEqual(self.store.get('draft',draft['id'])['status'],'uncertain')
            with self.assertRaises(ValueError):w.deliver(self.store,draft['id'])
            self.assertEqual(call.call_count,2)
    def test_only_one_store_can_claim_draft(self):
        draft=self.draft();other=w.Store(self.tmp.name)
        self.addCleanup(other.db.close)
        self.store.patch('draft',draft['id'],{'status':'sending'},allowed={'draft'})
        with self.assertRaises(ValueError):other.patch('draft',draft['id'],{'status':'sending'},allowed={'draft'})
    def test_shell_text_is_passed_as_data(self):
        draft=self.draft();text='$(touch /tmp/nope); `whoami`\nquote " and unicode ä'
        w.save_draft(self.store,{'id':draft['id'],'text':text})
        with patch.object(w,'hcall',side_effect=[{'snapshot':self.raw},{}]) as call:
            w.deliver(self.store,draft['id']);self.assertEqual(call.call_args.args[-1],text)
    def test_cli_registration_does_not_guess_focused_thread(self):
        # No pane context -> explicitly unassigned, rather than linking to a user's unrelated focus.
        item=w.add_item(self.store,str(self.file));self.assertEqual(item['thread'],'')
    def test_delivery_follows_same_agent_after_pane_move(self):
        draft=self.draft();raw=copy.deepcopy(self.raw);raw['panes'][0]['pane_id']='w2:p8'
        with patch.object(w,'hcall',side_effect=[{'snapshot':raw},{}]) as call:
            w.deliver(self.store,draft['id'])
            self.assertEqual(call.call_args.args[3],'w2:p8')
    def test_shared_app_server_does_not_claim_inherited_pane(self):
        with patch.object(w,'Store',return_value=self.store),patch.object(w.sys,'argv',['workbench','add',str(self.file)]),patch.dict(w.os.environ,{'HERDR_PANE_ID':'wrong-pane'}),patch.object(w,'resolve_thread') as resolve,contextlib.redirect_stdout(io.StringIO()):
            w.main();resolve.assert_not_called()
        self.assertEqual(self.store.all('item')[0]['thread'],'')
    def test_voice_output_goes_to_owned_draft_without_agent_input(self):
        draft=self.draft();other=self.draft()
        path=Path(self.tmp.name)/'dictation.txt';path.write_text('My spoken feedback')
        self.store.put('voice',{'id':'active','draft':draft['id'],'path':str(path)})
        with patch.object(w,'run',return_value='{}') as run,patch.object(w,'hcall') as agent:
            result=w.voice_stop(self.store)
            self.assertEqual(result['draft']['text'],'Feedback\nMy spoken feedback')
            self.assertEqual(self.store.get('draft',other['id'])['text'],'Feedback')
            self.assertIn('--wait-file',run.call_args.args[0]);agent.assert_not_called()
        self.assertIsNone(self.store.get('voice','active'))
    def test_voice_failure_releases_recording_state(self):
        draft=self.draft();self.store.put('voice',{'id':'active','draft':draft['id'],'path':'missing'})
        with patch.object(w,'run',side_effect=RuntimeError('No speech')):
            with self.assertRaises(RuntimeError):w.voice_stop(self.store)
        self.assertIsNone(self.store.get('voice','active'))
        self.assertEqual(self.store.get('draft',draft['id'])['text'],'Feedback')
    def test_clipboard_image_attaches_without_replacing_note(self):
        draft=self.draft()
        with patch.object(w,'run',side_effect=['image/png\n',b'PNG reference']),patch.object(w,'desktop_env',return_value={}):
            result=w.rpc({'op':'draft-clipboard','id':draft['id']},self.store)
        self.assertEqual(result['text'],'Feedback')
        self.assertEqual(Path(result['attachments'][0]).read_bytes(),b'PNG reference')
    def test_latest_saved_draft_is_used_when_switching(self):
        draft=self.draft();w.save_draft(self.store,{'id':draft['id'],'text':'Latest edit'})
        self.assertEqual(w.rpc({'op':'draft-get','id':draft['id']},self.store)['text'],'Latest edit')
    def test_file_browser_lists_folders_first_and_can_choose_file(self):
        folder=Path(self.tmp.name)/'z folder';folder.mkdir()
        hidden=Path(self.tmp.name)/'.hidden';hidden.write_text('private')
        listing=w.browse_files(self.tmp.name)['entries']
        self.assertEqual(listing[0]['path'],str(folder))
        self.assertNotIn('.hidden',[e['name'] for e in listing])
        self.assertIn('.hidden',[e['name'] for e in w.browse_files(self.tmp.name,True)['entries']])
        self.assertEqual(w.browse_files(str(self.file)),{'file':str(self.file)})
    def test_file_browser_sorts_files_and_keeps_folders_first(self):
        import os
        root=Path(self.tmp.name)/'sorting';root.mkdir()
        for name,size,age in [('b old.png',300,300),('a big.pdf',900,200),('c new.md',10,100),('d plain',50,400)]:
            f=root/name;f.write_bytes(b'x'*size);os.utime(f,(1000-age,1000-age))
        for name,age in [('Older',50),('newer',10)]:
            (root/name).mkdir();os.utime(root/name,(2000-age,2000-age))
        names=lambda sort,descending=False:[e['name'] for e in w.rpc({'op':'browse','path':str(root),'sort':sort,'descending':descending},self.store)['entries']]
        self.assertEqual(names('name'),['newer','Older','a big.pdf','b old.png','c new.md','d plain'])
        self.assertEqual(names('name',True),['Older','newer','d plain','c new.md','b old.png','a big.pdf'])
        self.assertEqual(names('modified',True),['newer','Older','c new.md','a big.pdf','b old.png','d plain'])
        self.assertEqual(names('size',True),['newer','Older','a big.pdf','b old.png','d plain','c new.md'])
        self.assertEqual(names('type'),['newer','Older','d plain','c new.md','a big.pdf','b old.png'])
        self.assertEqual(names('unknown'),names('name'))
        entry=w.browse_files(str(root))['entries'][2]
        self.assertEqual((entry['type'],entry['bytes'],entry['modified'],entry['size']),('PDF',900,800,'900 B'))
    def test_empty_success_response_is_not_a_failed_delivery(self):
        with patch.object(w,'run',return_value=''):
            self.assertEqual(w.hcall('local','agent','prompt','w1:p1','note'),{})
    def test_agent_brief_and_personal_note_preserve_each_other(self):
        key=self.thread['id']
        w.save_personal_note(self.store,key,'My own reminder')
        other=w.Store(self.tmp.name);self.addCleanup(other.db.close)
        w.update_brief(other,key,'Finished the report','Review the preview')
        self.assertEqual(self.store.get('card',key)['note'],'My own reminder')
        w.rpc({'op':'card','id':key,'note':'Edited personal reminder'},self.store)
        self.assertEqual(other.get('card',key)['brief']['summary'],'Finished the report')
        w.update_brief(other,key,'Updated report','No further work planned')
        self.assertEqual(self.store.get('card',key)['note'],'Edited personal reminder')
        self.assertEqual(self.store.get('card',key)['brief']['waiting'],'')
    def test_agent_brief_requires_known_explicit_thread(self):
        for key in ('','missing-thread'):
            with self.assertRaises(ValueError):w.update_brief(self.store,key,'Done','Review')
        self.assertEqual(self.store.all('card'),[])
    def test_agent_brief_does_not_write_to_other_threads(self):
        self.store.put('card',{'id':'another-thread','note':'Other note','brief':{'summary':'Other work'}})
        w.update_brief(self.store,self.thread['id'],'Done','Review')
        self.assertEqual(self.store.get('card','another-thread')['brief']['summary'],'Other work')
    def test_invalid_brief_keeps_previous_content(self):
        key=self.thread['id'];before=w.update_brief(self.store,key,'Useful summary','A real next step')
        for summary,next_step in [('', 'Next'),('Summary',''),('x'*801,'Next')]:
            with self.assertRaises(ValueError):w.update_brief(self.store,key,summary,next_step)
            self.assertEqual(self.store.get('card',key),before)
    def test_old_conversation_brief_is_not_shown_as_current(self):
        from companion import brief_text
        card=w.update_brief(self.store,self.thread['id'],'Old task summary','Next')
        thread={**self.thread,'agent_session':'new-conversation'}
        self.assertNotIn('Old task summary',brief_text(card,thread))

class AttachmentTest(Case):
    def files(self,*names):
        paths=[Path(self.tmp.name)/name for name in names]
        for p in paths:p.write_bytes(b'PNG')
        return [str(p) for p in paths]
    def test_every_attachment_reaches_the_first_message(self):
        one,two=self.files('one.png','two.png')
        draft=w.new_draft(self.store,self.thread['id'],'Compare these',attachments=[one,two])
        with patch.object(w,'hcall',side_effect=[{'snapshot':self.raw},{'snapshot':self.raw},{}]) as call:
            w.deliver(self.store,draft['id'])
        self.assertEqual(call.call_args.args[-1],'Compare these\n\nPlease inspect the attached reference files:\n- '+one+'\n- '+two)
    def test_single_attachment_keeps_its_wording_and_old_drafts_still_send(self):
        one,=self.files('one.png')
        draft=self.draft();self.store.patch('draft',draft['id'],{'attachment':one})
        legacy=self.store.get('draft',draft['id']);legacy.pop('attachments');self.store.put('draft',legacy)
        with patch.object(w,'hcall',side_effect=[{'snapshot':self.raw},{'snapshot':self.raw},{}]) as call:
            w.deliver(self.store,draft['id'])
        self.assertEqual(call.call_args.args[-1],'Feedback\n\nPlease inspect the attached reference file: '+one)
    def test_attaching_appends_without_duplicates(self):
        one,two=self.files('one.png','two.png');draft=self.draft()
        for path in (one,two,one):w.rpc({'op':'draft-attach','id':draft['id'],'target':path},self.store)
        saved=self.store.get('draft',draft['id'])
        self.assertEqual(saved['attachments'],[one,two]);self.assertEqual(saved['attachment'],one)
    def test_attachments_can_be_removed_individually(self):
        one,two=self.files('one.png','two.png');draft=w.new_draft(self.store,self.thread['id'],attachments=[one,two])
        w.save_draft(self.store,{'id':draft['id'],'attachments':[two]})
        self.assertEqual(self.store.get('draft',draft['id'])['attachments'],[two])
    def test_earlier_panel_saving_first_attachment_keeps_the_others(self):
        one,two,other=self.files('one.png','two.png','other.png');draft=w.new_draft(self.store,self.thread['id'],attachments=[one,two])
        w.save_draft(self.store,{'id':draft['id'],'text':'Edited','attachment':one})
        self.assertEqual(self.store.get('draft',draft['id'])['attachments'],[one,two])
        w.save_draft(self.store,{'id':draft['id'],'attachment':other})
        self.assertEqual(self.store.get('draft',draft['id'])['attachments'],[other])
        w.save_draft(self.store,{'id':draft['id'],'attachment':''})
        self.assertEqual(self.store.get('draft',draft['id'])['attachments'],[])
    def test_missing_attachment_blocks_queueing_and_sending(self):
        one,two=self.files('one.png','two.png');draft=w.new_draft(self.store,self.thread['id'],attachments=[one,two])
        Path(two).unlink()
        with patch.object(w,'hcall',return_value={'snapshot':self.raw}):
            with self.assertRaisesRegex(ValueError,'two.png'):w.queue_capture(self.store,draft['id'])
            with self.assertRaisesRegex(ValueError,'two.png'):w.deliver(self.store,draft['id'])
        self.assertEqual(self.store.get('draft',draft['id'])['status'],'draft')
    def test_screenshots_collect_in_the_threads_open_draft(self):
        older=self.draft();newer=self.draft()
        self.assertEqual(w.capture_draft_for(self.store,self.thread['id']),newer['id'])
        self.assertEqual(w.capture_draft_for(self.store,self.thread['id'],older['id']),older['id'])
        self.assertEqual(w.capture_draft_for(self.store,'another-thread'),'')
        self.store.patch('draft',newer['id'],{'status':'sent'});self.store.patch('draft',older['id'],{'status':'queued'})
        self.assertEqual(w.capture_draft_for(self.store,self.thread['id']),'')
    def test_attachment_count_is_limited(self):
        with self.assertRaisesRegex(ValueError,'up to'):w.attachment_fields(['/tmp/f'+str(n) for n in range(w.MAX_ATTACHMENTS+1)])


class ScreenshotTest(Case):
    def shot(self):
        source=Path(self.tmp.name)/'screenshot-2026-10-12_17-00-00.png';source.write_bytes(b'PNG')
        return source
    def test_print_goes_to_an_open_capture(self):
        reply=json.dumps({'open':True,'draft':'d1','thread':self.thread['id']})
        with patch.object(w,'run',return_value=reply+'\n') as run,patch.object(w,'desktop_env',return_value={}):
            self.assertEqual(w.print_target(),{'open':True,'draft':'d1','thread':self.thread['id']})
        self.assertEqual(run.call_args.args[0],['omarchy-shell','shell','call','io.github.vulpeszerda27.workbench','printTarget','-'])
    def test_print_falls_back_when_capture_is_closed_or_workbench_missing(self):
        for reply in ('{"open": false}','unknown','',RuntimeError('no shell')):
            with self.subTest(reply=reply),patch.object(w,'desktop_env',return_value={}):
                with patch.object(w,'run',side_effect=reply if isinstance(reply,Exception) else None,return_value=reply):
                    self.assertIsNone(w.print_target())
    def test_print_command_runs_omarchys_screenshot_without_a_capture(self):
        umasks=[]
        def handover(*args):
            umasks.append(w.os.umask(0o022));raise SystemExit(0)
        before=w.os.umask(0o022);w.os.umask(before)
        with patch.object(w,'Store',return_value=self.store),patch.object(w.sys,'argv',['workbench','print']),patch.object(w,'print_target',return_value=None),patch.object(w.os,'execvp',side_effect=handover) as execvp:
            with self.assertRaises(SystemExit):w.main()
        w.os.umask(before)
        execvp.assert_called_once_with('omarchy-capture-screenshot',['omarchy-capture-screenshot'])
        self.assertEqual(umasks,[before])
    def test_screenshot_key_command_fills_an_open_capture_like_print(self):
        draft=self.draft();target={'open':True,'draft':draft['id'],'thread':self.thread['id']}
        with patch.object(w,'Store',return_value=self.store),patch.object(w.sys,'argv',['workbench','capture','screenshot']),\
             patch.object(w,'print_target',return_value=target),patch.object(w,'capture_screenshot') as capture:
            self.assertEqual(w.main(),0)
        capture.assert_called_once_with(self.store,self.thread['id'],draft['id'])
        with patch.object(w,'Store',return_value=self.store),patch.object(w.sys,'argv',['workbench','capture','screenshot','--thread',self.thread['id']]),\
             patch.object(w,'print_target') as lookup,patch.object(w,'capture_screenshot') as capture:
            w.main()
        lookup.assert_not_called();capture.assert_called_once_with(self.store,self.thread['id'],'')
    def test_hints_name_the_key_bound_to_workbench_screenshots(self):
        binds=[{'modmask':64,'key':'PRINT','description':'Color picker'},
               {'modmask':72,'key':'PRINT','description':'Screenshot for a Workbench capture'},
               {'modmask':0,'key':'PRINT','description':'Screenshot (into an open Workbench capture)'}]
        for listed,expected in ((binds,'Print'),(binds[:2],'Super+Alt+Print'),(binds[:1],''),('not json',''),(RuntimeError('no hyprland'),'')):
            with self.subTest(expected=expected),patch.object(w,'desktop_env',return_value={}):
                fail=listed if isinstance(listed,Exception) else None
                reply='' if fail else listed if isinstance(listed,str) else json.dumps(listed)
                with patch.object(w,'run',side_effect=fail,return_value=reply):
                    self.assertEqual(w.screenshot_key(),expected)
    def test_print_command_adds_to_the_open_draft(self):
        draft=self.draft()
        with patch.object(w,'Store',return_value=self.store),patch.object(w.sys,'argv',['workbench','print']),patch.object(w,'print_target',return_value={'open':True,'draft':draft['id'],'thread':self.thread['id']}),patch.object(w,'capture_screenshot') as capture:
            w.main()
        capture.assert_called_once_with(self.store,self.thread['id'],draft['id'])
    def test_screenshot_uses_omarchy_capture_and_keeps_it_private(self):
        source=self.shot();draft=self.draft()
        with patch.object(w,'run',side_effect=['',str(source)+'\n','']) as run,patch.object(w,'desktop_env',return_value={}),patch.object(w.time,'sleep'),patch.object(w,'summon') as summon:
            result=w.capture_screenshot(self.store,self.thread['id'],draft['id'])
        self.assertEqual(run.call_args_list[1].args[0],['omarchy-capture-screenshot','smart','save'])
        saved=Path(result['attachments'][0])
        self.assertEqual(saved.parent,self.store.directory);self.assertTrue(saved.is_file());self.assertFalse(source.exists())
        summon.assert_called_once_with('capture',draft=draft['id'])
    def test_cancelled_screenshot_returns_to_the_draft(self):
        draft=self.draft()
        with patch.object(w,'run',side_effect=['','']),patch.object(w,'desktop_env',return_value={}),patch.object(w.time,'sleep'),patch.object(w,'summon') as summon:
            self.assertIsNone(w.capture_screenshot(self.store,self.thread['id'],draft['id']))
        summon.assert_called_once_with('capture',draft=draft['id'])
        self.assertEqual(self.store.get('draft',draft['id'])['attachments'],[])
    def test_pasting_a_copied_image_attaches_it(self):
        draft=self.draft()
        with patch.object(w,'run',side_effect=['image/jpeg\nimage/png\n',b'PNG data']),patch.object(w,'desktop_env',return_value={}):
            saved=w.rpc({'op':'draft-paste-image','id':draft['id']},self.store)
        path=Path(saved['attachments'][0]);self.assertEqual(path.suffix,'.png');self.assertEqual(path.read_bytes(),b'PNG data')
    def test_pasting_text_or_nothing_leaves_attachments_alone(self):
        draft=self.draft()
        for types in ('text/plain;charset=utf-8\nimage/png\n','text/html\n',RuntimeError('No selection')):
            with self.subTest(types=types),patch.object(w,'desktop_env',return_value={}):
                with patch.object(w,'run',side_effect=[types] if not isinstance(types,Exception) else types):
                    self.assertEqual(w.rpc({'op':'draft-paste-image','id':draft['id']},self.store),{})
        self.assertEqual(self.store.get('draft',draft['id'])['attachments'],[])


class NewThreadTest(Case):
    def setUp(self):
        super().setUp()
        self.project=Path(self.tmp.name)/'harbor';self.project.mkdir()
        self.started={'focused_pane_id':'w1:p1','workspaces':self.raw['workspaces']+[{'workspace_id':'w9','label':'harbor'}],
            'panes':self.raw['panes']+[{'pane_id':'w9:p1','workspace_id':'w9','tab_id':'w9:t1','terminal_id':'term-new',
            'agent':'claude','agent_status':'unknown','label':'harbor','cwd':str(self.project),'agent_session':{'value':''}}]}
    def herdr(self,starting_polls=1):
        calls=[];pending=[starting_polls]
        def call(machine,*args,**kwargs):
            calls.append(args)
            if args[:2]==('workspace','create'):return {'root_pane':{'pane_id':'w9:p1'}}
            if args[:2]==('pane','run'):return {}
            if args==('api','snapshot'):
                if pending[0]>0:pending[0]-=1;return {'snapshot':self.raw}
                return {'snapshot':self.started}
            raise AssertionError(args)
        return call,calls
    def test_starts_in_a_new_workspace_without_switching(self):
        call,calls=self.herdr()
        with patch.object(w,'hcall',side_effect=call),patch.object(w.time,'sleep'):
            thread=w.start_thread(self.store,str(self.project),'claude')
        self.assertEqual(calls[0],('workspace','create','--cwd',str(self.project),'--label','harbor','--no-focus'))
        self.assertEqual(calls[1],('pane','run','w9:p1','claude'))
        self.assertEqual((thread['pane'],thread['agent']),('w9:p1','claude'))
        self.assertEqual(self.store.get('thread',thread['id'])['project'],'harbor')
    def test_rejects_unknown_agents_remote_machines_and_missing_folders(self):
        with patch.object(w,'hcall') as call:
            with self.assertRaisesRegex(ValueError,'Claude or Codex'):w.start_thread(self.store,str(self.project),'bash')
            with self.assertRaisesRegex(ValueError,'this computer'):w.start_thread(self.store,str(self.project),'claude','server')
            with self.assertRaisesRegex(ValueError,'no longer exists'):w.start_thread(self.store,str(self.project/'gone'),'claude')
        call.assert_not_called()
    def test_agent_that_never_appears_reports_and_keeps_draft(self):
        call,_=self.herdr(starting_polls=10**6)
        with patch.object(w,'hcall',side_effect=call),patch.object(w.time,'sleep'),patch.object(w.time,'monotonic',side_effect=[0,0,99]):
            with self.assertRaisesRegex(RuntimeError,'did not start'):w.start_thread(self.store,str(self.project),'codex')
    def test_start_thread_queues_the_note_as_first_message(self):
        call,_=self.herdr();draft=w.new_draft(self.store,'','Fix the footer')
        request={'op':'draft-start-thread','id':draft['id'],'text':'Fix the footer','new_thread':{'dir':str(self.project),'agent':'claude'}}
        with patch.object(w,'hcall',side_effect=call),patch.object(w.time,'sleep'),patch('automation.start') as start:
            result=w.rpc(request,self.store)
        saved=result['draft'];start.assert_called_once_with(machine='local')
        self.assertEqual(saved['status'],'queued');self.assertEqual(saved['thread'],result['thread']['id'])
        self.assertEqual((saved['started_thread'],saved['new_thread']),('claude',None))
    def test_start_thread_needs_content_and_a_project(self):
        draft=w.new_draft(self.store,'','')
        with patch.object(w,'start_thread') as start:
            with self.assertRaisesRegex(ValueError,'Add a note'):
                w.rpc({'op':'draft-start-thread','id':draft['id'],'new_thread':{'dir':str(self.project),'agent':'claude'}},self.store)
            with self.assertRaisesRegex(ValueError,'Choose a project'):
                w.rpc({'op':'draft-start-thread','id':draft['id'],'text':'Note','new_thread':None},self.store)
        start.assert_not_called()
    def test_new_thread_target_is_validated_when_saved(self):
        draft=w.new_draft(self.store,'','Note')
        with self.assertRaises(ValueError):w.save_draft(self.store,{'id':draft['id'],'new_thread':{'dir':str(self.project),'agent':'vim'}})
        w.save_draft(self.store,{'id':draft['id'],'new_thread':{'dir':str(self.project),'agent':'codex','extra':1}})
        self.assertEqual(self.store.get('draft',draft['id'])['new_thread'],{'dir':str(self.project),'agent':'codex'})
    def test_projects_are_ordered_by_recent_activity_with_home_first(self):
        import os,subprocess
        home=Path(self.tmp.name)/'home';root=home/'Projects';root.mkdir(parents=True)
        env=dict(os.environ,GIT_AUTHOR_NAME='T',GIT_AUTHOR_EMAIL='t@example.com',GIT_COMMITTER_NAME='T',GIT_COMMITTER_EMAIL='t@example.com')
        def repo(name,stamp,branch='main'):
            path=root/name;path.mkdir()
            subprocess.run(['git','init','-q','-b',branch,str(path)],check=True,env=env)
            subprocess.run(['git','-C',str(path),'commit','-q','--allow-empty','-m','x'],check=True,
                env=dict(env,GIT_AUTHOR_DATE=f'@{stamp} +0000',GIT_COMMITTER_DATE=f'@{stamp} +0000'))
        now=int(time.time())
        repo('older',now-3*86400);repo('recent',now-7200,'feature/menu')
        (root/'notes').mkdir();os.utime(root/'notes',(now-10800,now-10800))
        (root/'worktree').mkdir();(root/'worktree'/'.git').write_text('gitdir: elsewhere')
        (root/'.hidden').mkdir();(root/'alias').symlink_to(root/'older');(root/'README.md').write_text('not a folder')
        with patch.object(w.Path,'home',return_value=home):rows=w.projects()
        self.assertEqual([r['label'] for r in rows],['Home','recent','notes','older'])
        self.assertEqual(rows[0],{'value':str(home),'label':'Home','description':'~'})
        self.assertEqual(rows[1],{'value':str(root/'recent'),'label':'recent','description':'feature/menu · 2 hours ago'})
        self.assertEqual(rows[2]['description'],'not a git repo')
        self.assertEqual(rows[3]['description'],'main · 3 days ago')
        with patch.object(w.Path,'home',return_value=Path(self.tmp.name)/'nobody'):
            self.assertEqual(w.projects(),[{'value':str(Path(self.tmp.name)/'nobody'),'label':'Home','description':'~'}])

if __name__=='__main__':unittest.main()
