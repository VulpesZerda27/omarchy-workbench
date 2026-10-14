import curses
from contextlib import ExitStack
import io
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import companion as c
from workbench import Store, add_item, update_brief


class FocusInputTests(unittest.TestCase):
    def window(self,keys,focused=False):
        raw=Mock()
        raw.get_wch.side_effect=keys
        raw.getdelay.return_value=750
        return c.FocusWindow(raw,focused)

    def test_focus_reports_update_state_without_becoming_escape(self):
        win=self.window(['\x1b','[','I','s','\x1b','[','O','c'])
        with self.assertRaises(curses.error):win.get_wch()
        self.assertTrue(win.focused)
        self.assertEqual(win.get_wch(),'s')
        with self.assertRaises(curses.error):win.get_wch()
        self.assertFalse(win.focused)
        self.assertEqual(win.get_wch(),'c')
        self.assertEqual(win.win.timeout.call_args.args,(750,))

    def test_plain_escape_still_returns(self):
        win=self.window(['\x1b',curses.error()])
        self.assertEqual(win.get_wch(),'\x1b')
        self.assertEqual(win.win.timeout.call_args.args,(750,))

    def test_terminfo_focus_keys_update_state(self):
        win=self.window([1001,1002,curses.KEY_DOWN])
        with patch.object(curses,'keyname',side_effect=[b'kxIN',b'kxOUT',b'KEY_DOWN']):
            with self.assertRaises(curses.error):win.get_wch()
            self.assertTrue(win.focused)
            with self.assertRaises(curses.error):win.get_wch()
            self.assertFalse(win.focused)
            self.assertEqual(win.get_wch(),curses.KEY_DOWN)

    def test_incomplete_escape_and_blocking_timeout_are_preserved(self):
        win=self.window(['\x1b','[',curses.error()])
        win.win.getdelay.return_value=-1
        with patch.object(curses,'unget_wch') as unread:
            self.assertEqual(win.get_wch(),'\x1b')
        unread.assert_called_once_with('[')
        self.assertEqual(win.win.timeout.call_args.args,(-1,))

    def test_unrelated_input_after_escape_is_preserved(self):
        win=self.window(['\x1b','[','x'])
        with patch.object(curses,'unget_wch') as unread:
            self.assertEqual(win.get_wch(),'\x1b')
        self.assertEqual([x.args[0] for x in unread.call_args_list],['x','['])

    def test_resize_after_escape_is_preserved_as_keycode(self):
        win=self.window(['\x1b',curses.KEY_RESIZE])
        with patch.object(curses,'ungetch') as unread:
            self.assertEqual(win.get_wch(),'\x1b')
        unread.assert_called_once_with(curses.KEY_RESIZE)

    def test_initial_focus_and_reporting_cleanup(self):
        for pane in ('companion','agent',None):
            with self.subTest(pane=pane), patch.object(c,'hcall',return_value={'snapshot':{'focused_pane_id':pane}}), patch.object(c.sys,'stdout',new_callable=io.StringIO) as output:
                with self.assertRaisesRegex(RuntimeError,'exit view'):
                    with c.focus_input(Mock(),'local:research','companion') as win:
                        self.assertEqual(win.focused,pane=='companion')
                        self.assertEqual(output.getvalue(),'\x1b[?1004h')
                        raise RuntimeError('exit view')
                self.assertEqual(output.getvalue(),'\x1b[?1004h\x1b[?1004l')

    def test_missing_initial_snapshot_starts_unfocused(self):
        with patch.object(c,'hcall',side_effect=RuntimeError('offline')), patch.object(c.sys,'stdout',new_callable=io.StringIO):
            with c.focus_input(Mock(),'local','companion') as win:
                self.assertFalse(win.focused)


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.thread = {'id':'thread', 'machine':'local:research', 'pane':'agent', 'cwd':'/tmp'}

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_named_session_invocation(self):
        with patch.dict(os.environ, {'HERDR_SOCKET_PATH':'/home/test/.config/herdr/sessions/research/herdr.sock',
                                     'WORKBENCH_MACHINE':'local'}, clear=True):
            self.assertEqual(c.invoking_machine(), 'local:research')

    def test_dismissal_is_rechecked_before_open(self):
        self.store.put('companion_settings', {'id':'thread','dismissed':True})
        with patch.object(c,'hcall') as call:
            c.open_for_thread(self.store,self.thread,focus=False,toggle=False)
        call.assert_not_called()

    def test_old_tui_does_not_dismiss_replacement(self):
        self.store.put('companion', {'id':'thread','pane':'replacement'})
        c.dismiss(self.store,'thread','old-pane')
        self.assertEqual(self.store.get('companion','thread')['pane'], 'replacement')
        self.assertIsNone(self.store.get('companion_settings','thread'))

    def test_launch_closes_database_on_failure(self):
        with patch.object(c,'Store',return_value=self.store), patch.object(c,'hcall',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError,'offline'):
                c.launch(machine='local')
        with self.assertRaises(sqlite3.ProgrammingError):self.store.all('thread')

    def test_markdown_opens_in_default_app(self):
        guide=Path(self.temp.name)/'guide.md'
        guide.write_text('# Workbench quick guide\nPress Enter to open.')
        item=add_item(self.store,str(guide),'Workbench quick guide',reference=True)
        with patch.object(c,'read_view') as view, patch.object(c,'open_target') as external:
            c.open_reference('window',self.store,item)
        external.assert_called_once_with(str(guide))
        view.assert_not_called()

    def test_markdown_preview_stays_in_terminal(self):
        guide=Path(self.temp.name)/'guide.md'
        guide.write_text('# Workbench quick guide\nPress Enter to open.')
        item=add_item(self.store,str(guide),'Workbench quick guide',reference=True)
        with patch.object(c,'read_view') as view, patch.object(c,'open_target') as external:
            c.preview_reference('window',self.store,item)
        view.assert_called_once_with('window','Workbench quick guide',guide.read_text())
        external.assert_not_called()

    def test_image_preview_opens_selected_shelf_item(self):
        self.store.put('thread',self.thread)
        item={'id':'image','thread':'thread','kind':'Image','target':'/tmp/image.png'}
        with patch.object(c,'summon') as shelf, patch.object(c,'open_target') as external:
            c.preview_reference('window',self.store,item)
        shelf.assert_called_once_with('shelf',thread='thread',machine='local:research',item='image')
        external.assert_not_called()

    def test_link_reference_uses_external_opener(self):
        item={'id':'ref','target':'https://example.com/guide.md','kind':'Link','title':'Guide'}
        with patch.object(c,'open_target') as external, patch.object(c,'read_view') as view:
            c.open_reference('window',self.store,item)
        external.assert_called_once_with(item['target'])
        view.assert_not_called()

    def run_panel(self,keys,focused=True,status='ready',pairs=False):
        win=Mock(focused=focused)
        win.getmaxyx.return_value=(60,80)
        events=iter(keys)
        def next_key():
            event=next(events)
            return event(win) if callable(event) else event
        win.get_wch.side_effect=next_key
        thread={**self.thread,'title':'Actual thread title','project':'Project','agent':'codex','status':status}
        with ExitStack() as stack:
            for name in ('refresh','apply_palette','dismiss'):
                stack.enter_context(patch.object(c,name))
            stack.enter_context(patch.object(c.sys,'stdout',new_callable=io.StringIO))
            # Tag each colour pair so tests can tell which one drew a string.
            stack.enter_context(patch.object(curses,'color_pair',side_effect=pair_attr) if pairs else patch.object(curses,'color_pair',return_value=0))
            opened=stack.enter_context(patch.object(c,'open_reference'))
            summoned=stack.enter_context(patch.object(c,'summon'))
            c.run_tui(win,self.store,thread,'thread','companion')
        return win,opened,summoned

    def drawn(self,win):
        return [(call.args[0],call.args[1],call.args[2],call.args[4]) for call in win.addnstr.call_args_list]

    def test_header_uses_herdr_status_dot(self):
        for status,pair in (('working',c.P_YELLOW),('blocked',c.P_RED),('idle',c.P_GREEN)):
            with self.subTest(status=status):
                win,_,_=self.run_panel(['q'],status=status,pairs=True)
                self.assertIn((1,2,'●' if status!='idle' else '○',pair_attr(pair)),self.drawn(win))

    def test_selection_band_appears_only_with_focus(self):
        add_item(self.store,'https://example.com/output','Output','thread')
        for focused in (True,False):
            with self.subTest(focused=focused):
                win,_,_=self.run_panel(['q'],focused=focused,pairs=True)
                band=[text for _,_,text,attr in self.drawn(win) if attr>>8&0xff==c.P_ROW]
                self.assertEqual(bool(band),focused)
                if focused:self.assertIn('Output',band)

    def test_general_actions_show_their_keys_at_the_right_edge(self):
        win,_,_=self.run_panel(['q'])
        for key in ('o','c'):
            self.assertIn(76,[x for _,x,text,_ in self.drawn(win) if text==key])

    def test_brief_shows_waiting_item_and_folds_empty_one(self):
        self.store.put('thread',{**self.thread,'title':'Actual thread title','project':'Project','agent':'codex','status':'ready'})
        for waiting,expected in (('Approve the release','Waiting on you'),('','Nothing waiting on you')):
            with self.subTest(waiting=waiting):
                update_brief(self.store,'thread','Shipped the fix','Run the tests',waiting)
                win,_,_=self.run_panel(['q'])
                texts=[text for _,_,text,_ in self.drawn(win)]
                self.assertIn(expected,texts)
                self.assertEqual('Approve the release' in texts,bool(waiting))

    def test_result_messages_are_marked(self):
        win,_,_=self.run_panel(['p','q'])
        self.assertIn('Select a file or link to preview.',[text for _,_,text,_ in self.drawn(win)])
        add_item(self.store,'https://example.com/output','Output','thread')
        def fail_open(win):
            c.open_reference.side_effect=RuntimeError('No default application')
            return '\n'
        win,_,_=self.run_panel([fail_open,'q'])
        self.assertIn('× No default application',[text for _,_,text,_ in self.drawn(win)])

    def test_o_opens_the_threads_shelf_like_super_alt_o(self):
        for key in ('o','s'):
            with self.subTest(key=key):
                _,opened,summoned=self.run_panel([key,'q'])
                summoned.assert_called_once_with('shelf',thread='thread',machine='local:research')
                opened.assert_not_called()

    def test_unpin_explains_when_selection_is_not_a_reference(self):
        add_item(self.store,'https://example.com/output','Output','thread')
        win,_,_=self.run_panel(['d','q'])
        self.assertTrue(any(text.startswith('Select a pinned reference') for _,_,text,_ in self.drawn(win)))

    def test_output_opens_directly_and_unpin_keeps_output(self):
        output=add_item(self.store,'https://example.com/output','Output','thread')
        pin=add_item(self.store,output['target'],'Pinned output','thread',reference=True)
        _,opened,_=self.run_panel(['d','\n','q'])
        self.assertIsNone(self.store.get('item',pin['id']))
        self.assertEqual(self.store.get('item',output['id']),output)
        self.assertEqual(opened.call_args.args[2]['id'],output['id'])

    def test_unpin_does_not_delete_output_or_general_actions(self):
        output=add_item(self.store,'https://example.com/output','Output','thread')
        self.run_panel(['d',curses.KEY_DOWN,'d',curses.KEY_DOWN,'d','q'])
        self.assertEqual(self.store.all('item'),[output])

    def test_arriving_output_preserves_selected_target(self):
        output=add_item(self.store,'https://example.com/original','Original','thread')
        def insert_output(win):
            add_item(self.store,'https://example.com/new','New output','thread')
            return curses.KEY_RESIZE
        _,opened,_=self.run_panel([insert_output,'\n','q'])
        self.assertEqual(opened.call_args.args[2]['id'],output['id'])

    def test_mouse_ignores_separator_and_opens_general_action_below_it(self):
        add_item(self.store,'https://example.com/output','Output','thread')
        mouse_events=[]
        def click_text(prefix):
            def event(win):
                row=next(call.args[0] for call in reversed(win.addnstr.call_args_list)
                         if call.args[2].lstrip('› ').startswith(prefix))
                mouse_events.append((0,3,row,0,curses.BUTTON1_CLICKED))
                return curses.KEY_MOUSE
            return event
        with patch.object(curses,'getmouse',side_effect=lambda:mouse_events.pop(0)):
            _,opened,summoned=self.run_panel([click_text('─'),click_text('Outputs & previews'),'q'])
        opened.assert_not_called()
        summoned.assert_called_once_with('shelf',thread='thread',machine='local:research')

    def test_preview_shortcut_does_not_open_external_app(self):
        item=add_item(self.store,'https://example.com/output','Output','thread')
        with patch.object(c,'preview_reference') as previewed:
            _,opened,_=self.run_panel(['p','q'])
        previewed.assert_called_once()
        self.assertEqual(previewed.call_args.args[2]['id'],item['id'])
        opened.assert_not_called()

    def test_open_failure_is_shown_in_companion(self):
        add_item(self.store,'https://example.com/output','Output','thread')
        def fail_open(win):
            c.open_reference.side_effect=RuntimeError('Could not open: no default application')
            return '\n'
        win,_,_=self.run_panel([fail_open,'q'])
        self.assertTrue(any('Could not open: no default application' in call.args[2] for call in win.addnstr.call_args_list))


class OpenListTests(unittest.TestCase):
    def item(self,ident,target,reference=False):
        return {'id':ident,'target':target,'title':ident.title(),'kind':'Link','reference':reference}

    def test_outputs_and_references_precede_general_actions(self):
        items=[self.item('pin','https://example.com/pin',True),
               self.item('new','https://example.com/new'),
               self.item('old','https://example.com/old')]
        actions=c.open_actions(items,2)
        self.assertEqual([a[2] for a in actions],['new','old','pin','o','c'])
        self.assertIn('2',actions[-1][1])

    def test_duplicate_pin_retains_output_identity_and_pinned_status(self):
        items=[self.item('pin','https://example.com/shared',True),
               self.item('output','https://example.com/shared')]
        actions=c.open_actions(items)
        self.assertEqual([a[2] for a in actions],['output','o','c'])
        self.assertIn('pinned',actions[0][1].lower())

    def test_empty_list_has_only_general_actions_without_separator(self):
        self.assertEqual([a[2] for a in c.open_actions([])],['o','c'])
        scroll,placements,separator=c.open_action_layout(2,0,0,0,8)
        self.assertEqual(scroll,0)
        self.assertEqual(placements,[(0,0),(1,2)])
        self.assertIsNone(separator)

    def test_separator_has_its_own_non_action_row(self):
        scroll,placements,separator=c.open_action_layout(4,2,0,0,9)
        self.assertEqual(scroll,0)
        self.assertEqual(placements,[(0,0),(1,2),(2,5),(3,7)])
        self.assertEqual(separator,4)
        action_rows={row+offset for _,row in placements for offset in (0,1)}
        self.assertNotIn(separator,action_rows)

    def test_scrolling_and_resize_keep_selection_visible(self):
        scroll=0
        for rows in (8,4,2,9):
            for selected in list(range(12))+list(reversed(range(12))):
                with self.subTest(rows=rows,selected=selected):
                    scroll,placements,separator=c.open_action_layout(12,10,selected,scroll,rows)
                    self.assertIn(selected,[idx for idx,_ in placements])
                    self.assertTrue(all(0<=row<rows for _,row in placements))
                    occupied=[row+offset for _,row in placements for offset in (0,1)]
                    self.assertEqual(len(occupied),len(set(occupied)))
                    if separator is not None:
                        self.assertNotIn(separator,occupied)
                        self.assertTrue(any(idx<10 for idx,_ in placements))
                        self.assertTrue(any(idx>=10 for idx,_ in placements))

    def test_shrinking_list_clamps_old_scroll(self):
        scroll,placements,separator=c.open_action_layout(2,0,1,10,6)
        self.assertIn(1,[idx for idx,_ in placements])
        self.assertLess(scroll,2)
        self.assertIsNone(separator)

    def test_general_action_details_read_naturally(self):
        link=lambda n:{'id':str(n),'target':'https://example.com/'+str(n),'title':str(n),'kind':'Link','reference':False}
        self.assertEqual(c.open_actions([])[0][1],'No outputs from this thread yet')
        self.assertEqual(c.open_actions([link(1)])[1][1],'1 file or link from this thread')
        self.assertEqual(c.open_actions([link(1),link(2)])[2][1],'2 files and links from this thread')


class LayoutHelperTests(unittest.TestCase):
    def test_hint_groups_start_their_own_rows(self):
        rows=c.fit_hints(c.MAIN_HINTS,70)
        self.assertEqual([[key for key,_ in row] for row in rows],[['↑↓','↵','p','tab'],['o','c','?','q']])

    def test_narrow_hints_wrap_then_drop_but_never_overflow(self):
        for width in (12,26,40):
            with self.subTest(width=width):
                rows=c.fit_hints(c.MAIN_HINTS,width)
                self.assertLessEqual(len(rows),2)
                self.assertEqual(rows[0][0],('↑↓','choose'))
                for row in rows:
                    self.assertLessEqual(sum(len(k)+1+len(l) for k,l in row)+2*(len(row)-1),width)

    def test_status_glyphs_follow_herdr(self):
        self.assertEqual(c.state_glyph('working'),('●',c.P_YELLOW))
        self.assertEqual(c.state_glyph('blocked'),('●',c.P_RED))
        self.assertEqual(c.state_glyph('done'),('●',c.P_GREEN))
        self.assertEqual(c.state_glyph('idle'),('○',c.P_GREEN))
        self.assertEqual(c.state_glyph('closed'),('○',c.P_MUTED))
        self.assertEqual(c.state_glyph('unknown'),('·',c.P_MUTED))

    def test_brief_lines(self):
        brief={'summary':'Fixed it','next':'Ship it','waiting':''}
        lines=c.brief_lines(brief,40)
        self.assertEqual(lines[0],('What changed','label'))
        self.assertEqual(lines[-1],('Nothing waiting on you','muted'))
        waiting=c.brief_lines({**brief,'waiting':'Pick a name'},40)
        self.assertEqual(waiting[-2:],[('Waiting on you','waiting'),('Pick a name','')])
        self.assertTrue(all(style=='muted' for _,style in c.brief_lines({},40)))

    def test_item_meta(self):
        now=time.time()
        link={'kind':'Link','target':'https://docs.example.com/a','updated':now-30}
        self.assertEqual(c.item_meta(link,pinned=True,now=now),'link · docs.example.com · just now · pinned')
        with tempfile.NamedTemporaryFile(suffix='.md') as handle:
            self.assertEqual(c.item_meta({'kind':'Document','target':handle.name}),'document')
        self.assertEqual(c.item_meta({'kind':'PDF','target':'/missing/report.pdf'}),'pdf · missing')

    def test_open_summary_counts_unique_targets(self):
        items=[{'target':'a','reference':False},{'target':'a','reference':True},{'target':'b','reference':False}]
        self.assertEqual(c.open_summary(items),'2 outputs · 1 pinned')
        self.assertEqual(c.open_summary(items[:1]),'1 output')
        self.assertEqual(c.open_summary([]),'')

    def test_when_is_glanceable(self):
        now=time.mktime((2026,10,12,15,0,0,0,0,-1))
        self.assertEqual(c.when(now-20,now),'just now')
        self.assertEqual(c.when(now-300,now),'5m ago')
        self.assertEqual(c.when(now-3*3600,now),'12:00')
        self.assertEqual(c.when(now-3*86400,now),time.strftime('%a %H:%M',time.localtime(now-3*86400)))
        self.assertEqual(c.when(now-40*86400,now),'2 Sep')


class ReadViewTests(unittest.TestCase):
    def render(self,text,headings=()):
        win=Mock();win.getmaxyx.return_value=(30,60);win.get_wch.return_value='q'
        with patch.object(curses,'color_pair',side_effect=pair_attr):
            c.read_view(win,'Title',text,headings=headings)
        return {call.args[2]:call.args[4] for call in win.addnstr.call_args_list}

    def test_companion_texts_style_headings_and_keys(self):
        drawn=self.render('OPTIONAL\ne  Keep a personal note.',c.HELP_HEADINGS)
        self.assertEqual(drawn['OPTIONAL'],pair_attr(c.P_ACCENT)|curses.A_BOLD)
        self.assertEqual(drawn['e'],pair_attr(c.P_ACCENT)|curses.A_BOLD)

    def test_previewed_files_render_verbatim(self):
        drawn=self.render('OPTIONAL\ne  Keep a personal note.')
        self.assertEqual(drawn['OPTIONAL'],0)
        self.assertNotIn('e',drawn)


def pair_attr(pair):
    return pair<<8


if __name__=='__main__':unittest.main()
