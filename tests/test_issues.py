import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workbench as w
from test_workbench import Case


def remotes(text):
    return patch.object(w,'run',return_value=text)


class RepositoryTest(unittest.TestCase):
    def test_origin_url_forms_resolve_to_owner_and_name(self):
        for url in ('https://github.com/octo/garden.git','https://github.com/octo/garden',
                    'git@github.com:octo/garden.git','ssh://git@github.com/octo/garden.git',
                    'https://token@github.com/octo/garden.git'):
            with self.subTest(url=url),remotes('remote.origin.url '+url+'\n'):
                self.assertEqual(w.github_repo('/p'),'octo/garden')
    def test_ghs_default_remote_wins_over_origin(self):
        config=('remote.origin.url https://github.com/me/atlas.git\n'
                'remote.upstream.url https://github.com/team/atlas.git\nremote.upstream.gh-resolved base\n')
        with remotes(config):self.assertEqual(w.github_repo('/p'),'team/atlas')
    def test_other_hosts_and_missing_remotes_have_no_repository(self):
        with remotes('remote.origin.url https://gitlab.com/me/app.git\n'):self.assertEqual(w.github_repo('/p'),'')
        with patch.object(w,'run',side_effect=RuntimeError('')):self.assertEqual(w.github_repo('/p'),'')
    def test_issue_projects_leave_out_home(self):
        rows=[{'value':'/home/m','label':'Home','description':'~'},{'value':'/home/m/Projects/garden','label':'garden','description':'main'},
              {'value':'/home/m/Projects/notes','label':'notes','description':'not a git repo'}]
        with patch.object(w,'projects',return_value=rows),patch.object(w,'github_repo',side_effect=lambda d:'me/garden' if d.endswith('garden') else ''):
            result=w.issue_projects()
        self.assertEqual([(r['label'],r['repo'],r['description']) for r in result],
            [('garden','me/garden','me/garden'),('notes','','No GitHub repository')])


GH_ISSUE={'number':197,'title':'Open links in the app','state':'OPEN','stateReason':None,'url':'https://github.com/me/garden/issues/197',
    'labels':[{'name':'enhancement','color':'a2eeef'}],'author':{'login':'me'},'assignees':[],
    'createdAt':'2026-10-10T07:22:49Z','updatedAt':'2026-10-10T07:22:49Z','closedAt':None}


class IssueListTest(Case):
    def setUp(self):
        super().setUp()
        self.project=Path(self.tmp.name)/'garden';self.project.mkdir()
        patcher=patch.object(w,'github_repo',side_effect=lambda d:'me/garden' if str(d).endswith('garden') else '')
        patcher.start();self.addCleanup(patcher.stop)
    def test_lists_open_issues_through_gh_without_prompts(self):
        with patch.object(w,'run',return_value=json.dumps([GH_ISSUE])) as run:
            result=w.issue_list(self.store,str(self.project))
        argv=run.call_args.args[0]
        self.assertEqual(argv[:6],['gh','issue','list','-R','me/garden','--state']);self.assertEqual(argv[6],'open')
        self.assertEqual(run.call_args.kwargs['env']['GH_PROMPT_DISABLED'],'1')
        issue=result['issues'][0]
        self.assertEqual((issue['number'],issue['state'],issue['labels'][0]['color'],issue['author']),(197,'open','#a2eeef','me'))
        self.assertGreater(issue['updated'],0);self.assertEqual(result['error'],'')
    def test_cached_list_returns_without_asking_github(self):
        with patch.object(w,'run',return_value=json.dumps([GH_ISSUE])):w.issue_list(self.store,str(self.project))
        with patch.object(w,'run') as run:
            result=w.issue_list(self.store,str(self.project),refresh=False)
        run.assert_not_called();self.assertEqual(result['issues'][0]['number'],197)
    def test_failed_refresh_keeps_the_last_list_and_explains(self):
        with patch.object(w,'run',return_value=json.dumps([GH_ISSUE])):w.issue_list(self.store,str(self.project))
        with patch.object(w,'run',side_effect=RuntimeError('error connecting to api.github.com')):
            result=w.issue_list(self.store,str(self.project))
        self.assertEqual(len(result['issues']),1);self.assertIn('couldn’t be reached',result['error'])
    def test_gh_errors_read_as_next_steps(self):
        cases={RuntimeError("the 'me/garden' repository has disabled issues"):'turned off',
               RuntimeError('To get started with GitHub CLI, please run:  gh auth login'):'gh auth login',
               FileNotFoundError('gh'):'Install the GitHub CLI',subprocess.TimeoutExpired('gh',30):'couldn’t be reached'}
        for exc,expected in cases.items():
            with self.subTest(exc=exc),patch.object(w,'run',side_effect=exc):
                self.assertIn(expected,w.issue_list(self.store,str(self.project))['error'])
    def test_project_without_repository_lists_nothing(self):
        other=Path(self.tmp.name)/'notes';other.mkdir()
        with patch.object(w,'run') as run:result=w.issue_list(self.store,str(other))
        run.assert_not_called();self.assertEqual((result['repo'],result['issues']),('',[]))
    def test_closed_issues_show_most_recently_closed_first(self):
        early={**GH_ISSUE,'number':1,'state':'CLOSED','closedAt':'2026-01-01T00:00:00Z'}
        late={**GH_ISSUE,'number':2,'state':'CLOSED','closedAt':'2026-09-01T00:00:00Z'}
        with patch.object(w,'run',return_value=json.dumps([early,late])):
            result=w.issue_list(self.store,str(self.project),'closed')
        self.assertEqual([i['number'] for i in result['issues']],[2,1])
    def test_detail_renders_comments_and_never_loads_images(self):
        raw={**GH_ISSUE,'body':'<!-- template -->Broken\n\n<img width="300" src="https://github.com/user-attachments/assets/a1">\n\n![screen](https://x.test/s.png "t")',
             'comments':[{'author':{'login':'ana'},'createdAt':'2026-10-11T10:00:00Z','body':'Seen it ![](https://x.test/b.png)'}],'milestone':None}
        with patch.object(w,'run',return_value=json.dumps(raw)):
            detail=w.issue_detail(self.store,str(self.project),197)
        text=detail['markdown']
        self.assertNotIn('<img',text);self.assertNotIn('![',text);self.assertNotIn('template',text)
        self.assertIn('[Image](https://github.com/user-attachments/assets/a1)',text);self.assertIn('[screen](https://x.test/s.png)',text)
        self.assertIn('**ana** · 11 Oct 2026',text);self.assertEqual(detail['comments'],1)
    def test_unchanged_issue_comes_from_the_cache(self):
        raw={**GH_ISSUE,'body':'Body','comments':[],'milestone':None}
        with patch.object(w,'run',return_value=json.dumps(raw)):first=w.issue_detail(self.store,str(self.project),197)
        with patch.object(w,'run') as run:
            self.assertEqual(w.issue_detail(self.store,str(self.project),197,first['updated'])['markdown'],'Body')
        run.assert_not_called()


class IssueHandoffTest(Case):
    def setUp(self):
        super().setUp()
        self.project=Path(self.tmp.name)/'garden';self.project.mkdir()
        patcher=patch.object(w,'github_repo',side_effect=lambda d:'me/garden' if str(d).endswith('garden') else '')
        patcher.start();self.addCleanup(patcher.stop)
    def issue(self,number=197,title='Open links in the app'):return {'dir':str(self.project),'number':number,'title':title}
    def test_typing_keeps_one_draft_per_issue(self):
        a=w.save_issue_draft(self.store,{'issue':self.issue(),'text':'Start with Android'})
        b=w.save_issue_draft(self.store,{'issue':self.issue(),'text':'Start with Android only'})
        c=w.save_issue_draft(self.store,{'issue':self.issue(0),'text':'New idea'})
        self.assertEqual(a['id'],b['id']);self.assertNotEqual(a['id'],c['id'])
        self.assertEqual(b['issue'],{'dir':str(self.project),'repo':'me/garden','number':197,'title':'Open links in the app'})
        self.assertEqual(c['issue']['title'],'')
    def test_repository_comes_from_the_project_not_the_panel(self):
        draft=w.save_issue_draft(self.store,{'issue':{**self.issue(),'repo':'someone/else'},'text':'x'})
        self.assertEqual(draft['issue']['repo'],'me/garden')
        other=Path(self.tmp.name)/'notes';other.mkdir()
        with self.assertRaisesRegex(ValueError,'no GitHub repository'):w.save_issue_draft(self.store,{'issue':{'dir':str(other)},'text':'x'})
    def test_issue_drafts_stay_out_of_capture(self):
        draft=w.save_issue_draft(self.store,{'issue':self.issue(0),'text':'New idea'})
        self.assertEqual(w.capture_draft_for(self.store,''),'')
        self.assertEqual(w.capture_view(self.store,draft['id']),'issues')
        self.assertEqual(w.capture_view(self.store,self.draft()['id']),'capture')
    def test_implementation_prompt_names_the_issue_and_adds_instructions(self):
        prompt=w.issue_prompt(w.issue_target(self.issue()),'Android only for now',['/tmp/shot.png'])
        first,*rest=prompt.splitlines()
        self.assertEqual(first,'Implement me/garden#197: Open links in the app')
        self.assertIn('gh issue view 197 -R me/garden --comments',prompt)
        self.assertIn('Additional instructions:\nAndroid only for now',prompt)
        self.assertTrue(prompt.endswith('Please inspect the attached reference file: /tmp/shot.png'))
        self.assertNotIn('Additional instructions',w.issue_prompt(w.issue_target(self.issue()),'',[]))
    def test_new_issue_prompt_files_in_the_projects_repository(self):
        prompt=w.issue_prompt(w.issue_target(self.issue(0)),'The invite page jumps on iOS\nWhen the keyboard opens…',[])
        self.assertTrue(prompt.startswith('Write a GitHub issue for garden: The invite page jumps on iOS\n'))
        self.assertIn('gh issue create -R me/garden',prompt);self.assertIn('Don’t change any code',prompt)
        self.assertTrue(prompt.endswith('Request:\nThe invite page jumps on iOS\nWhen the keyboard opens…'))
    def test_new_issue_needs_a_description_but_an_existing_issue_does_not(self):
        with patch.object(w,'start_thread') as start:
            with self.assertRaisesRegex(ValueError,'Describe the issue'):
                w.hand_over_issue(self.store,{'issue':self.issue(0),'text':'  ','agent':'claude'})
            with self.assertRaisesRegex(ValueError,'Claude or Codex'):
                w.hand_over_issue(self.store,{'issue':self.issue(),'text':'','agent':'bash'})
        start.assert_not_called()
    def test_handoff_starts_the_agent_in_the_project_and_queues_the_request(self):
        started={**self.thread,'id':'t-new','pane':'w9:p1','cwd':str(self.project)}
        with patch.object(w,'start_thread',return_value=started) as start,patch('automation.start'),\
             patch.object(w,'hcall',return_value={'snapshot':self.raw}),patch.object(w,'normalize',return_value=[started]):
            result=w.hand_over_issue(self.store,{'issue':self.issue(),'text':'','agent':'codex'})
        start.assert_called_once_with(self.store,str(self.project),'codex')
        draft=result['draft']
        self.assertEqual((draft['status'],draft['thread'],draft['started_thread']),('queued','t-new','codex'))
        # Delivery composes the issue prompt; the draft keeps only what was typed.
        with patch.object(w,'hcall',side_effect=[{'snapshot':self.raw},{}]) as call,patch.object(w,'normalize',return_value=[{**started,'status':'idle'}]):
            w.deliver(self.store,draft['id'],queued=True,queue_token=draft['queue_token'])
        self.assertTrue(call.call_args.args[4].startswith('Implement me/garden#197: Open links in the app'))
        self.assertEqual(self.store.get('draft',draft['id'])['text'],'')
    def test_print_creates_the_issue_draft_for_its_screenshot(self):
        target={'open':True,'draft':'','thread':'','issue':self.issue(0)}
        with patch.object(w,'Store',return_value=self.store),patch.object(w.sys,'argv',['workbench','print']),\
             patch.object(w,'print_target',return_value=target),patch.object(w,'capture_screenshot') as capture:
            w.main()
        key=capture.call_args.args[2]
        self.assertEqual(self.store.get('draft',key)['issue']['number'],0)
    def test_settings_merge_instead_of_replacing(self):
        w.rpc({'op':'settings','value':{'issue_project':'/p/garden'}},self.store)
        w.rpc({'op':'settings','value':{'other':1}},self.store)
        self.assertEqual(self.store.get('settings','ui'),{'id':'ui','issue_project':'/p/garden','other':1})


if __name__=='__main__':unittest.main()
