import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import integration as i

BINDINGS='-- My bindings\no.bind("SUPER + B", "Browser", { launch = "chromium" })\n'
MENU='{\n  // Extend the Omarchy menu.\n  // "personal": {"label":"Personal"},\n  "work": {"label": "Work", "action": "work"}\n}\n'
DEFAULTS='o.bind("PRINT", "Screenshot", "omarchy-capture-screenshot")\no.bind("SUPER + ALT + K", "Tmux keybindings", "x")\n'


def menu_items(text):
    """Parse the menu the way Omarchy does: drop whole-line comments and trailing commas."""
    text=re.sub(r'^\s*//[^\n]*(\n|$)','',text,flags=re.M)
    return json.loads(re.sub(r',(\s*[}\]])',r'\1',text))


class Desktop(unittest.TestCase):
    """A throwaway home folder with Omarchy's config files and no Herdr unless a test adds it."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'home';self.defaults=Path(self.tmp.name)/'omarchy'
        (self.defaults/'default/hypr/bindings').mkdir(parents=True)
        (self.defaults/'default/hypr/bindings/utilities.lua').write_text(DEFAULTS)
        self.bindings=self.write('.config/hypr/bindings.lua',BINDINGS)
        self.menu=self.write('.config/omarchy/extensions/omarchy-menu.jsonc',MENU)
        (self.home/'.claude').mkdir()
        self.claude=self.home/'.claude/CLAUDE.md'
        self.commands=[];self.herdr={'plugins':[]}
        self.programs={'gh','voxtype','pdftoppm','wl-paste','gio'}
        env={k:v for k,v in os.environ.items() if k!='HYPRLAND_INSTANCE_SIGNATURE'};env['OMARCHY_PATH']=str(self.defaults)
        for p in (patch.object(i,'home',return_value=self.home),patch.dict(os.environ,env,clear=True),
                  patch.object(i.shutil,'which',side_effect=lambda name:'/usr/bin/'+name if name in self.programs else None),
                  patch.object(i,'run_cmd',side_effect=self.run_cmd)):
            p.start();self.addCleanup(p.stop)
    def write(self,relative,text):
        path=self.home/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);return path
    def run_cmd(self,argv,timeout=20):
        self.commands.append(argv)
        if argv[:4]==['herdr','plugin','list','--json']:return True,json.dumps({'id':'cli','result':self.herdr})
        if argv[:3]==['herdr','plugin','link']:
            self.herdr['plugins']=[{'plugin_id':i.HERDR_ID,'plugin_root':argv[3],'source':{'kind':'local'}}]
        if argv[:3]==['herdr','plugin','unlink']:self.herdr['plugins']=[]
        return True,''
    def setup(self,**kw):
        lines=[];code=i.setup(out=lines.append,**kw);return code,'\n'.join(lines)
    def remove(self):
        lines=[];code=i.remove(yes=True,out=lines.append);return code,'\n'.join(lines)
    def files(self):
        return {p:p.read_text() for p in self.home.rglob('*') if p.is_file() and '.bak.' not in p.name}


class SetupTest(Desktop):
    def test_setup_adds_marked_pieces_and_remove_takes_them_out(self):
        before=self.files()
        code,out=self.setup(yes=True)
        self.assertEqual(code,0,out)
        wrapper=self.home/'.local/bin/workbench'
        self.assertIn(str(i.HERE/'workbench.py'),wrapper.read_text());self.assertTrue(os.access(wrapper,os.X_OK))
        bindings=self.bindings.read_text()
        self.assertTrue(bindings.startswith(BINDINGS))
        for key in ('SUPER + ALT + O','SUPER + ALT + C','SUPER + ALT + I','SUPER + ALT + PRINT'):self.assertIn('o.bind("'+key+'"',bindings)
        self.assertIn('"'+str(wrapper)+' open shelf"',bindings)
        self.assertNotIn('"PRINT"',bindings)  # Print stays Omarchy's unless chosen
        items=menu_items(self.menu.read_text())
        self.assertEqual(items['work'],{'label':'Work','action':'work'})
        self.assertEqual(items['workbench.capture']['action'],str(wrapper)+' open capture')
        claude=self.claude.read_text()
        self.assertIn(str(wrapper)+' add TARGET',claude);self.assertNotIn('{workbench}',claude)
        self.assertIn('Herdr is not installed',out);self.assertIn('Skipped (opt-in).',out)
        # Running it again finds everything in place.
        code,out=self.setup(yes=True)
        self.assertIn('Nothing needed changing',out);self.assertEqual(out.count('Already set up'),4)
        code,out=self.remove()
        self.assertEqual(code,0,out)
        after=self.files()
        self.assertEqual(after[self.bindings],BINDINGS)
        self.assertNotIn(wrapper,after);self.assertNotIn(self.claude,after)
        self.assertEqual(menu_items(after[self.menu]),menu_items(MENU))
        self.assertEqual(set(after),set(before))
        self.assertFalse(list(self.claude.parent.glob('CLAUDE.md.bak.*')))  # nothing of the user's to keep
        self.assertIn('omarchy plugin remove '+i.OMARCHY_ID,out)

    def test_print_is_offered_but_off_by_default(self):
        steps={s.key:s for s in i.binding_steps('wb')}
        self.assertFalse(steps['print'].default);self.assertEqual(steps['print'].skip,'')
        with patch('builtins.input',side_effect=['y','y','y','n','y']),patch.object(i.sys.stdin,'isatty',return_value=True):
            code,out=self.setup()
        bindings=self.bindings.read_text()
        self.assertIn('hl.unbind("PRINT")',bindings);self.assertIn('o.bind("PRINT"',bindings)
        self.assertIn('o.bind("SUPER + ALT + O"',bindings)
        self.assertFalse(menu_items(self.menu.read_text()).get('workbench'))  # answered no

    def test_existing_keys_entries_and_instructions_are_left_alone(self):
        self.bindings.write_text(BINDINGS+'o.bind("ALT + SUPER + O", "Mine", "x")\nhl.unbind("PRINT")\no.bind("PRINT", "Mine", "y")\n')
        (self.defaults/'default/hypr/bindings/tiling.lua').write_text('o.bind("SUPER + ALT + C", "Default", "z")\n')
        self.menu.write_text('{\n  "workbench": {"label":"Mine"},\n}\n')
        self.claude.write_text('Use workbench add for outputs.\n')
        wrapper=self.write('.local/bin/workbench','#!/bin/sh\necho mine\n')
        before=self.files()
        code,out=self.setup(yes=True)
        after=self.files()
        self.assertEqual(after[wrapper],before[wrapper])
        self.assertEqual(after[self.menu],before[self.menu]);self.assertEqual(after[self.claude],before[self.claude])
        block=i.find_block(after[self.bindings],'lua')
        self.assertNotIn('SUPER + ALT + O"',block);self.assertNotIn('SUPER + ALT + C"',block);self.assertNotIn('PRINT"',block.replace('SUPER + ALT + PRINT',''))
        self.assertIn('o.bind("SUPER + ALT + I"',block)
        # A wrapper that isn't ours is not used, so bindings call this folder directly.
        self.assertIn('python3 '+str(i.HERE/'workbench.py')+' open issues',block)
        self.assertIn('SUPER + ALT + O is already bound',out);self.assertIn('already has Workbench instructions',out)

    def test_dry_run_and_non_terminal_change_nothing(self):
        before=self.files()
        code,out=self.setup(dry_run=True)
        self.assertEqual(code,0);self.assertIn('Dry run',out);self.assertEqual(self.files(),before)
        with patch.object(i.sys.stdin,'isatty',return_value=False):
            self.assertEqual(self.setup()[0],2);self.assertEqual(i.remove(out=lambda _:None),2)
        self.assertEqual(self.files(),before)

    def test_herdr_companion_is_linked_bound_and_started(self):
        self.programs.add('herdr')
        code,out=self.setup(yes=True)
        self.assertEqual(code,0,out)
        self.assertIn(['herdr','plugin','link',str(i.HERE)],self.commands)
        self.assertIn(['herdr','server','reload-config'],self.commands)
        self.assertIn(['herdr','plugin','action','invoke',i.HERDR_ID+'.start-automation'],self.commands)
        config=(self.home/'.config/herdr/config.toml').read_text()
        self.assertIn('command = "'+i.HERDR_ID+'.open"',config);self.assertIn('key = "prefix+i"',config)
        self.commands.clear()
        code,out=self.setup(yes=True)
        self.assertNotIn(['herdr','plugin','link',str(i.HERE)],self.commands)
        code,out=self.remove()
        self.assertIn(['herdr','plugin','unlink',i.HERDR_ID],self.commands)
        self.assertFalse((self.home/'.config/herdr/config.toml').exists())

    def test_companion_installed_elsewhere_is_relinked_and_a_used_key_is_skipped(self):
        self.programs.add('herdr')
        self.herdr['plugins']=[{'plugin_id':i.HERDR_ID,'plugin_root':'/elsewhere','source':{'kind':'github'}}]
        self.write('.config/herdr/config.toml','[[keys.command]]\nkey = "prefix+i"\ntype = "plugin_action"\ncommand = "other.thing"\n')
        code,out=self.setup(yes=True)
        self.assertIn(['herdr','plugin','uninstall',i.HERDR_ID],self.commands)
        self.assertIn(['herdr','plugin','link',str(i.HERE)],self.commands)
        self.assertIn('prefix+i is already used',out)
        self.assertNotIn(i.HERDR_ID,(self.home/'.config/herdr/config.toml').read_text())


class BlockTest(unittest.TestCase):
    def test_blocks_replace_in_place_and_strip_cleanly(self):
        for kind,text in (('lua','A\n'),('toml','x = 1\n'),('md','# Notes\n')):
            added=i.put_block(text,kind,['one'])
            replaced=i.put_block(added+'tail\n',kind,['two'])
            self.assertEqual(replaced.count(i.BEGIN),1);self.assertIn('two',replaced);self.assertNotIn('one',replaced)
            self.assertTrue(replaced.endswith('tail\n'))
            self.assertEqual(i.strip_block(added,kind),text)
    def test_menu_entries_go_inside_the_object(self):
        for text in ('{\n  "a": {"label":"A"}\n}\n','{\n  "a": {"label":"A"},\n  // note\n}','{}\n',''):
            items=menu_items(i.put_block(text,'jsonc',['"workbench": {"label":"Workbench"},']))
            self.assertEqual(items['workbench'],{'label':'Workbench'})
            if '"a"' in text:self.assertEqual(items['a'],{'label':'A'})
    def test_key_names_ignore_modifier_order_and_unbinds(self):
        keys=i.bound_keys(['o.bind("ALT + SUPER + o", "x", "y")\nhl.unbind("PRINT")\nhl.bind("SUPER + PRINT", "c")\n'])
        self.assertEqual(keys,{i.key_name('SUPER + ALT + O'),i.key_name('SUPER + PRINT')})


if __name__=='__main__':unittest.main()
