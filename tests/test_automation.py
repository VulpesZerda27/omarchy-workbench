"""Lifecycle tests use isolated stores and never contact a running Herdr server."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import automation as automation
import companion as companion
import workbench as w


class AutomationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = w.Store(temporary.name)
        self.addCleanup(self.store.db.close)
        self.raw = {
            'focused_pane_id': 'w1:p1',
            'workspaces': [{'workspace_id': 'w1', 'label': 'Project'}],
            'panes': [{
                'pane_id': 'w1:p1', 'workspace_id': 'w1', 'tab_id': 'w1:t1',
                'terminal_id': 'term-a', 'agent': 'codex', 'agent_status': 'idle',
                'label': 'Thread A', 'cwd': '/tmp/project',
                'agent_session': {'value': 'conversation-a'},
            }],
        }
        self.thread = w.normalize(self.raw, 'local')[0]
        self.store.put('thread', self.thread)
        self.ready = {}
        # Guard every imported API reference, including unintended calls.
        for module in (w, automation, companion):
            guard = patch.object(module, 'hcall', side_effect=AssertionError('Unexpected Herdr API call'))
            guard.start()
            self.addCleanup(guard.stop)

    def queue(self, text='Feedback', thread=None):
        draft = w.new_draft(self.store, (thread or self.thread)['id'], text)
        with patch.object(w, 'hcall', return_value={'snapshot': self.raw}):
            return w.queue_capture(self.store, draft['id'])

    def poll(self, snapshot=None):
        w.process_capture_queue(self.store, 'local', w.normalize(snapshot or self.raw, 'local'), self.ready)

    def test_busy_blocked_and_unknown_captures_wait_and_reset_readiness(self):
        draft = self.queue()
        for status in ('working', 'blocked', 'unknown'):
            with self.subTest(status=status):
                self.poll()
                raw = copy.deepcopy(self.raw)
                raw['panes'][0]['agent_status'] = status
                self.poll(raw)
                self.poll(raw)
                self.assertEqual(self.store.get('draft', draft['id'])['status'], 'queued')
                self.assertEqual(self.ready, {})
                self.poll()
                self.assertEqual(self.store.get('draft', draft['id'])['status'], 'queued')
                self.ready.clear()

    def test_two_ready_polls_deliver_once_for_idle_and_done(self):
        for status in ('idle', 'done'):
            with self.subTest(status=status):
                self.raw['panes'][0]['agent_status'] = status
                draft = self.queue()
                with patch.object(w, 'hcall', side_effect=[{'snapshot': self.raw}, {}]) as api:
                    self.poll()
                    api.assert_not_called()
                    self.poll()
                    self.assertEqual(self.store.get('draft', draft['id'])['status'], 'sent')
                    for _ in range(4):
                        self.poll()
                    self.assertEqual(api.call_count, 2)
                    self.assertEqual(api.call_args.args, ('local', 'agent', 'prompt', 'w1:p1', 'Feedback'))

    def test_cancel_before_second_poll_keeps_capture_editable(self):
        draft = self.queue()
        self.poll()
        w.cancel_capture(self.store, draft['id'])
        self.poll()
        self.poll()
        self.assertEqual(self.store.get('draft', draft['id'])['status'], 'draft')
        self.assertEqual(w.save_draft(self.store, {'id': draft['id'], 'text': 'Revised'})['text'], 'Revised')

    def test_cancel_during_final_snapshot_prevents_dispatch(self):
        draft = self.queue()
        self.poll()
        def snapshot_and_cancel(*args, **kwargs):
            self.assertEqual(args, ('local', 'api', 'snapshot'))
            w.cancel_capture(self.store, draft['id'])
            return {'snapshot': self.raw}
        with patch.object(w, 'hcall', side_effect=snapshot_and_cancel) as api:
            self.poll()
            self.assertEqual(api.call_count, 1)
        self.assertEqual(self.store.get('draft', draft['id'])['status'], 'draft')

    def test_changed_identity_stops_queue_without_dispatch(self):
        for changed in ('conversation', 'agent', 'terminal', 'closed'):
            with self.subTest(changed=changed):
                draft = self.queue()
                raw = copy.deepcopy(self.raw)
                if changed == 'conversation':
                    raw['panes'][0]['agent_session']['value'] = 'other-conversation'
                elif changed == 'agent':
                    raw['panes'][0]['agent'] = 'claude'
                elif changed == 'terminal':
                    raw['panes'][0]['terminal_id'] = 'replacement-terminal'
                else:
                    raw['panes'] = []
                self.poll(raw)
                saved = self.store.get('draft', draft['id'])
                self.assertEqual(saved['status'], 'draft')
                self.assertIn('Queue stopped:', saved['queue_error'])
                self.assertEqual(saved['text'], 'Feedback')

    def test_final_snapshot_rechecks_identity(self):
        draft = self.queue()
        self.poll()
        raw = copy.deepcopy(self.raw)
        raw['panes'][0]['agent_session']['value'] = 'replacement'
        with patch.object(w, 'hcall', return_value={'snapshot': raw}) as api:
            self.poll()
            self.assertEqual(api.call_count, 1)
        self.assertEqual(self.store.get('draft', draft['id'])['status'], 'draft')

    def test_uncertain_submission_is_never_retried(self):
        draft = self.queue()
        with patch.object(w, 'hcall', side_effect=[{'snapshot': self.raw}, RuntimeError('Lost connection')]) as api:
            for _ in range(6):
                self.poll()
            self.assertEqual(api.call_count, 2)
        self.assertEqual(self.store.get('draft', draft['id'])['status'], 'uncertain')

    def test_oldest_queue_entry_goes_first_and_one_per_thread_per_poll(self):
        # Creation order deliberately differs from queue order.
        first = self.queue('Created first, queued second')
        second = self.queue('Created second, queued first')
        self.store.patch('draft', first['id'], {'queued': 20})
        self.store.patch('draft', second['id'], {'queued': 10})
        with patch.object(w, 'hcall', side_effect=[{'snapshot': self.raw}, {}, {'snapshot': self.raw}, {}]) as api:
            self.poll()
            self.poll()
            self.assertEqual(api.call_count, 2)
            self.assertEqual(api.call_args.args[-1], second['text'])
            self.assertEqual(self.store.get('draft', first['id'])['status'], 'queued')
            self.poll()
            self.assertEqual(api.call_count, 2)
            self.poll()
            self.assertEqual(api.call_count, 4)
            self.assertEqual(api.call_args.args[-1], first['text'])

    def test_cancel_edit_requeue_during_snapshot_preserves_new_queue(self):
        draft = self.queue()
        self.poll()
        replacement = {}
        def snapshot_and_replace(*args, **kwargs):
            self.assertEqual(args, ('local', 'api', 'snapshot'))
            w.cancel_capture(self.store, draft['id'])
            w.save_draft(self.store, {'id': draft['id'], 'text': 'New capture'})
            with patch.object(w, 'hcall', return_value={'snapshot': self.raw}):
                replacement.update(w.queue_capture(self.store, draft['id']))
            return {'snapshot': self.raw}
        with patch.object(w, 'hcall', side_effect=snapshot_and_replace) as api:
            self.poll()
            self.assertEqual(api.call_count, 1)
        saved = self.store.get('draft', draft['id'])
        self.assertEqual(saved['status'], 'queued')
        self.assertEqual(saved['text'], 'New capture')
        self.assertNotEqual(saved['queue_token'], draft['queue_token'])
        self.assertEqual(saved['queue_token'], replacement['queue_token'])
        with patch.object(w, 'hcall', side_effect=[{'snapshot': self.raw}, {}]) as api:
            self.poll()
            api.assert_not_called()
            self.poll()
            self.assertEqual(api.call_args.args[-1], 'New capture')

    def test_requeue_requires_two_new_ready_observations(self):
        draft = self.queue()
        self.poll()
        w.cancel_capture(self.store, draft['id'])
        with patch.object(w, 'hcall', return_value={'snapshot': self.raw}):
            replacement = w.queue_capture(self.store, draft['id'])
        self.assertNotEqual(replacement['queue_token'], draft['queue_token'])
        with patch.object(w, 'hcall', side_effect=[{'snapshot': self.raw}, {}]) as api:
            self.poll()
            api.assert_not_called()
            self.poll()
            self.assertEqual(self.store.get('draft', draft['id'])['status'], 'sent')

    def test_changed_payload_during_direct_send_or_queue_is_not_claimed(self):
        for action in (w.deliver, w.queue_capture):
            with self.subTest(action=action.__name__):
                draft = w.new_draft(self.store, self.thread['id'], 'Original')
                def snapshot_and_edit(*args, **kwargs):
                    self.assertEqual(args, ('local', 'api', 'snapshot'))
                    w.save_draft(self.store, {'id': draft['id'], 'text': 'Edited concurrently'})
                    return {'snapshot': self.raw}
                with patch.object(w, 'hcall', side_effect=snapshot_and_edit) as api:
                    with self.assertRaises(ValueError):
                        action(self.store, draft['id'])
                    self.assertEqual(api.call_count, 1)
                saved = self.store.get('draft', draft['id'])
                self.assertEqual(saved['status'], 'draft')
                self.assertEqual(saved['text'], 'Edited concurrently')

    def test_identity_or_busy_status_after_attachment_staging_prevents_prompt(self):
        attachment = self.store.directory / 'reference.txt'
        attachment.write_text('Reference')
        for change in ('conversation', 'working', 'blocked', 'unknown'):
            with self.subTest(change=change):
                draft = w.new_draft(self.store, self.thread['id'], 'Reference note', str(attachment))
                with patch.object(w, 'hcall', return_value={'snapshot': self.raw}):
                    w.queue_capture(self.store, draft['id'])
                self.poll()
                raw = copy.deepcopy(self.raw)
                def stage_and_change(*args):
                    if change == 'conversation':
                        raw['panes'][0]['agent_session']['value'] = 'replacement'
                    else:
                        raw['panes'][0]['agent_status'] = change
                    return [str(attachment)]
                def snapshot_only(*args, **kwargs):
                    self.assertEqual(args, ('local', 'api', 'snapshot'))
                    return {'snapshot': copy.deepcopy(raw)}
                with patch.object(w, 'stage_attachments', side_effect=stage_and_change) as stage, patch.object(w, 'hcall', side_effect=snapshot_only) as api:
                    self.poll()
                    stage.assert_called_once()
                    self.assertEqual(api.call_count, 2)
                saved = self.store.get('draft', draft['id'])
                self.assertEqual(saved['status'], 'draft' if change == 'conversation' else 'queued')
                if change != 'conversation':
                    w.cancel_capture(self.store, draft['id'])

    def test_final_snapshot_busy_status_keeps_queue_for_later(self):
        draft = self.queue()
        self.poll()
        raw = copy.deepcopy(self.raw)
        raw['panes'][0]['agent_status'] = 'working'
        with patch.object(w, 'hcall', return_value={'snapshot': raw}) as api:
            self.poll()
            self.assertEqual(api.call_count, 1)
        self.assertEqual(self.store.get('draft', draft['id'])['status'], 'queued')
        with patch.object(w, 'hcall', side_effect=[{'snapshot': self.raw}, {}]) as api:
            self.poll()
            api.assert_not_called()
            self.poll()
            self.assertEqual(self.store.get('draft', draft['id'])['status'], 'sent')

    def test_named_session_queue_starts_its_destination_worker(self):
        thread = w.normalize(self.raw, 'local:project-session')[0]
        self.store.put('thread', thread)
        draft = w.new_draft(self.store, thread['id'], 'Feedback')
        with patch.object(automation, 'start') as start, patch.object(w, 'hcall', return_value={'snapshot': self.raw}):
            result = w.rpc({'op': 'draft-queue', 'id': draft['id']}, self.store)
            start.assert_called_once_with(machine='local:project-session')
        self.assertEqual(result['status'], 'queued')

    def test_remote_coordinator_only_claims_its_own_queue(self):
        remote = 'remote-server'
        thread = w.normalize(self.raw, remote)[0]
        self.store.put('thread', thread)
        other = w.new_draft(self.store, thread['id'], 'Other coordinator capture')
        owned = w.new_draft(self.store, thread['id'], 'Owned capture')
        with patch.object(w, 'hcall', return_value={'snapshot': self.raw}):
            other = w.queue_capture(self.store, other['id'], coordinator='local:two')
            w.queue_capture(self.store, owned['id'], coordinator='local:one')
        self.store.patch('draft', other['id'], {'queued': 10})
        self.store.patch('draft', owned['id'], {'queued': 20})
        before = self.store.get('draft', other['id'])
        live = w.normalize(self.raw, remote)
        with patch.object(w, 'hcall', side_effect=[{'snapshot': self.raw}, {}]) as api:
            for _ in range(4):
                w.process_capture_queue(self.store, remote, live, self.ready, coordinator='local:one')
            self.assertEqual(api.call_count, 2)
            self.assertEqual(api.call_args.args, (remote, 'agent', 'prompt', 'w1:p1', 'Owned capture'))
        self.assertEqual(self.store.get('draft', owned['id'])['status'], 'sent')
        self.assertEqual(self.store.get('draft', other['id']), before)
        self.assertNotIn((other['id'], other['queue_token']), self.ready)

    def test_other_machine_queue_is_not_processed(self):
        draft = self.queue()
        destination = {**draft['destination'], 'machine': 'remote'}
        self.store.patch('draft', draft['id'], {'destination': destination})
        for _ in range(3):
            self.poll()
        self.assertEqual(self.store.get('draft', draft['id'])['status'], 'queued')
        self.assertEqual(self.ready, {})

    def test_new_agent_opens_without_focus_or_toggle(self):
        tracker = automation.Companions()
        with patch.object(automation, 'launch') as launch:
            tracker.sync(self.store, 'local', self.raw)
            launch.assert_called_once_with(self.thread['id'], focus=False, toggle=False, machine='local')

    def test_non_agent_and_unidentified_terminal_do_not_open(self):
        raw = copy.deepcopy(self.raw)
        raw['panes'][0]['agent'] = ''
        unidentified = copy.deepcopy(self.raw['panes'][0])
        unidentified.pop('terminal_id')
        raw['panes'].append(unidentified)
        with patch.object(automation, 'launch') as launch:
            automation.Companions().sync(self.store, 'local', raw)
            launch.assert_not_called()

    def test_existing_companion_is_not_duplicated(self):
        self.store.put('companion', {'id': self.thread['id'], 'pane': 'w1:companion'})
        raw = copy.deepcopy(self.raw)
        raw['panes'].append({'pane_id': 'w1:companion'})
        with patch.object(automation, 'launch') as launch:
            tracker = automation.Companions()
            tracker.sync(self.store, 'local', raw)
            tracker.sync(self.store, 'local', raw)
            launch.assert_not_called()

    def test_manually_closed_companion_stays_dismissed_after_worker_restart(self):
        self.store.put('companion', {'id': self.thread['id'], 'pane': 'w1:companion'})
        raw = copy.deepcopy(self.raw)
        raw['panes'].append({'pane_id': 'w1:companion'})
        with patch.object(automation, 'launch') as launch:
            tracker = automation.Companions()
            tracker.sync(self.store, 'local', raw)
            tracker.sync(self.store, 'local', self.raw)
            automation.Companions().sync(self.store, 'local', self.raw)
            launch.assert_not_called()
        self.assertTrue(self.store.get('companion_settings', self.thread['id'])['dismissed'])

    def test_manual_toggle_records_dismissal(self):
        self.store.put('companion', {'id': self.thread['id'], 'pane': 'w1:companion'})
        raw = copy.deepcopy(self.raw)
        raw['panes'].append({'pane_id': 'w1:companion'})
        raw['focused_pane_id'] = 'w1:companion'
        with patch.object(companion, 'hcall', side_effect=[{'snapshot': raw}, {}]) as api:
            companion.open_for_thread(self.store, self.thread)
            self.assertEqual(api.call_args.args, ('local', 'plugin', 'pane', 'close', 'w1:companion'))
        with patch.object(automation, 'launch') as launch:
            automation.Companions().sync(self.store, 'local', self.raw)
            launch.assert_not_called()


class SourceWatchTest(unittest.TestCase):
    def test_restarts_once_an_update_has_settled(self):
        import os
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        files = [Path(folder.name) / name for name in automation.SourceWatch.FILES]
        for f in files: f.write_text('v1'); os.utime(f, ns=(1, 1))
        sources = automation.SourceWatch(folder.name)
        self.assertFalse(sources.changed())
        os.utime(files[0], ns=(2, 2))
        self.assertFalse(sources.changed())  # the update may still be landing
        os.utime(files[2], ns=(3, 3))
        self.assertFalse(sources.changed())
        self.assertTrue(sources.changed())   # quiet for one check: restart
        unchanged = automation.SourceWatch(folder.name)
        self.assertFalse(unchanged.changed()); self.assertFalse(unchanged.changed())


if __name__ == '__main__':
    unittest.main()
