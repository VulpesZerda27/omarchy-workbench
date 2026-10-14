import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workbench as w


class DesktopEnvironmentTest(unittest.TestCase):
    session={'DISPLAY':':1','WAYLAND_DISPLAY':'wayland-1','XDG_SESSION_TYPE':'wayland',
        'XDG_CURRENT_DESKTOP':'Hyprland','XDG_SESSION_DESKTOP':'Hyprland',
        'HYPRLAND_INSTANCE_SIGNATURE':'current-desktop'}

    def test_ssh_launch_uses_desktop_session_and_preserves_caller_context(self):
        inherited={'XDG_SESSION_TYPE':'tty','PATH':'/caller/bin','HERDR_PANE':'own-pane'}
        session={**self.session,'PATH':'/manager/bin','HERDR_PANE':'wrong-pane'}
        with patch.dict(os.environ,inherited,clear=True),patch.object(w,'run',return_value=json.dumps(session)) as run:
            env=w.desktop_env()
        for key,value in self.session.items():self.assertEqual(env[key],value)
        self.assertEqual(env['PATH'],'/caller/bin')
        self.assertEqual(env['HERDR_PANE'],'own-pane')
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'],'unix:path='+env['XDG_RUNTIME_DIR']+'/bus')
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0],['systemctl','--user','show-environment','--output=json'])

    def test_stale_desktop_values_are_replaced_even_with_inherited_signature(self):
        inherited={'DISPLAY':'localhost:10.0','WAYLAND_DISPLAY':'wayland-old',
            'HYPRLAND_INSTANCE_SIGNATURE':'old-desktop','XAUTHORITY':'/stale/ssh-auth'}
        with patch.dict(os.environ,inherited,clear=True),patch.object(w,'run',return_value=json.dumps(self.session)):
            env=w.desktop_env()
        for key,value in self.session.items():self.assertEqual(env[key],value)
        self.assertNotIn('XAUTHORITY',env)

    def test_wayland_session_does_not_keep_a_stale_x11_display(self):
        session={k:v for k,v in self.session.items() if k!='DISPLAY'}
        with patch.dict(os.environ,{'DISPLAY':'localhost:10.0'},clear=True),patch.object(w,'run',return_value=json.dumps(session)):
            env=w.desktop_env()
        self.assertNotIn('DISPLAY',env)
        self.assertEqual(env['XDG_SESSION_TYPE'],'wayland')

    def test_existing_desktop_survives_unavailable_manager(self):
        for failure in (FileNotFoundError('systemctl'),RuntimeError('no bus'),
                subprocess.TimeoutExpired('systemctl',2)):
            with self.subTest(failure=failure),patch.dict(os.environ,self.session,clear=True),patch.object(w,'run',side_effect=failure):
                env=w.desktop_env()
                for key,value in self.session.items():self.assertEqual(env[key],value)

    def test_invalid_or_nongraphical_manager_environment_keeps_existing_desktop(self):
        for output in ('invalid','[]','{}','{"XDG_SESSION_TYPE":"tty"}'):
            with self.subTest(output=output),patch.dict(os.environ,self.session,clear=True),patch.object(w,'run',return_value=output):
                env=w.desktop_env()
                for key,value in self.session.items():self.assertEqual(env[key],value)

    def test_hyprland_fallback_corrects_ssh_session_type(self):
        instances=json.dumps([{'instance':'current-desktop','wl_socket':'wayland-1'}])
        with patch.dict(os.environ,{'XDG_SESSION_TYPE':'tty'},clear=True),patch.object(w,'run',side_effect=[RuntimeError('no manager'),instances]):
            env=w.desktop_env()
        self.assertEqual(env['XDG_SESSION_TYPE'],'wayland')
        self.assertEqual(env['WAYLAND_DISPLAY'],'wayland-1')
        self.assertNotIn('DISPLAY',env)

    def test_launcher_receives_recovered_desktop_environment(self):
        process=Mock();process.wait.return_value=0
        with patch.dict(os.environ,{'XDG_SESSION_TYPE':'tty'},clear=True),patch.object(w,'run',return_value=json.dumps(self.session)),patch.object(w.subprocess,'Popen',return_value=process) as launch:
            w._launch_opening(['code','--new-window','/tmp/example.json'])
        for key,value in self.session.items():self.assertEqual(launch.call_args.kwargs['env'][key],value)


class OpeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        default_app=patch.object(w,'_default_app',return_value=None)
        default_app.start();self.addCleanup(default_app.stop)
        preferred_editor=patch.object(w,'_preferred_editor',return_value='nvim')
        preferred_editor.start();self.addCleanup(preferred_editor.stop)

    def file(self,name,content=b'example',executable=False):
        path=self.root/name
        path.write_bytes(content)
        if executable:path.chmod(0o700)
        return path

    def command(self,path):
        with patch.object(w,'_launch_opening') as launch:
            w.open_target(str(path))
        return launch.call_args.args[0]

    def test_documents_and_urls_use_desktop_defaults(self):
        for suffix in ('.md','.txt','.json','.pdf','.svg','.docx','.xlsx','.csv','.zip','.html'):
            with self.subTest(suffix=suffix):
                path=self.file('output'+suffix)
                self.assertEqual(self.command(path),['gio','open',str(path)])
        self.assertEqual(self.command('https://example.com/report?a=1&b=2'),
                         ['gio','open','https://example.com/report?a=1&b=2'])

    def test_source_and_executable_text_use_omarchy_editor(self):
        for name,content,executable in (
            ('script.py',b'print("hello")',False),('script.js',b'alert(1)',False),
            ('script.sh',b'echo hello',True),('helper',b'#!/usr/bin/python\nprint(1)',False),
            ('executable-text',b'echo hello',True),('Makefile',b'all:\n\techo hello',False)):
            with self.subTest(name=name):
                path=self.file(name,content,executable)
                self.assertEqual(self.command(path),['omarchy','launch','editor',str(path)])

    def test_vscode_source_uses_dedicated_file_window(self):
        path=self.file('script.py',b'print(1)')
        with patch.object(w,'_preferred_editor',return_value='code'),patch.object(w.shutil,'which',return_value='/usr/bin/code'):
            self.assertEqual(self.command(path),['omarchy','launch','editor','--new-window',str(path)])

    def test_missing_vscode_does_not_pass_flags_to_fallback_editor(self):
        path=self.file('script.py',b'print(1)')
        with patch.object(w,'_preferred_editor',return_value='code'),patch.object(w.shutil,'which',return_value=None):
            self.assertEqual(self.command(path),['omarchy','launch','editor',str(path)])

    def test_vscode_mime_handler_uses_dedicated_file_window(self):
        path=self.file('report with spaces:2.json',b'{"ok":true}')
        app=Mock();app.get_executable.return_value='code';app.get_commandline.return_value='code %F'
        with patch.object(w,'_default_app',return_value=app),patch.object(w.shutil,'which',return_value='/usr/bin/code'):
            self.assertEqual(self.command(path),['code','--new-window',str(path)])

    def test_other_apps_and_custom_vscode_launchers_keep_desktop_defaults(self):
        path=self.file('report.txt')
        for executable,command in (('omawrite','omawrite %f'),('code','code --profile Custom %F')):
            with self.subTest(command=command):
                app=Mock();app.get_executable.return_value=executable;app.get_commandline.return_value=command
                with patch.object(w,'_default_app',return_value=app),patch.object(w.shutil,'which',return_value='/usr/bin/'+executable):
                    self.assertEqual(self.command(path),['gio','open',str(path)])

    def test_binaries_and_launchers_reveal_containing_folder(self):
        for name,content,executable in (
            ('binary',b'\x7fELF\x00payload',True),('renamed.py',b'\x7fELF\x00payload',False),
            ('renamed.js',b'\x00payload',False),('binary.txt',b'\x7fELF\x00payload',False),
            ('app.desktop',b'[Desktop Entry]\nExec=bad',False),('app.AppImage',b'app',False),
            ('app.exe',b'MZpayload',False)):
            with self.subTest(name=name):
                path=self.file(name,content,executable)
                self.assertEqual(self.command(path),['gio','open',str(self.root)])

    def test_relative_paths_and_shell_metacharacters_stay_single_argument(self):
        path=self.file('report $(touch sentinel); `id` & stuff.py')
        previous=Path.cwd()
        try:
            os.chdir(self.root)
            self.assertEqual(self.command('./'+path.name),['omarchy','launch','editor',str(path)])
        finally:os.chdir(previous)
        self.assertFalse((self.root/'sentinel').exists())

    def test_missing_file_never_launches(self):
        with patch.object(w,'_launch_opening') as launch,self.assertRaisesRegex(ValueError,'does not exist'):
            w.open_target(str(self.root/'missing.pdf'))
        launch.assert_not_called()

    def test_launcher_failure_surfaces_stderr(self):
        with patch.object(w,'desktop_env',return_value=os.environ.copy()):
            with self.assertRaisesRegex(RuntimeError,'No application is registered'):
                w._launch_opening([sys.executable,'-c',
                    'import sys; print("No application is registered",file=sys.stderr); sys.exit(2)'])

    def test_missing_launcher_surfaces_clear_error(self):
        with patch.object(w.subprocess,'Popen',side_effect=FileNotFoundError('missing gio')):
            with self.assertRaisesRegex(RuntimeError,'Cannot open target with gio'):
                w._launch_opening(['gio','open','/tmp/example'])

    def test_launch_is_detached_and_does_not_capture_gui_pipes(self):
        process=Mock()
        process.wait.return_value=0
        with patch.object(w.subprocess,'Popen',return_value=process) as launch,patch.object(w,'desktop_env',return_value={}):
            w._launch_opening(['gio','open','/tmp/example'])
        kwargs=launch.call_args.kwargs
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stdin'],subprocess.DEVNULL)
        self.assertEqual(kwargs['stdout'],subprocess.DEVNULL)
        self.assertNotEqual(kwargs['stderr'],subprocess.PIPE)
        self.assertNotIn('shell',kwargs)

    def test_timeout_does_not_kill_launched_app(self):
        for command,is_editor in ((['gio','open','/tmp/example'],False),(['omarchy','launch','editor','/tmp/script.py'],True),(['code','--new-window','/tmp/data.json'],True)):
            with self.subTest(command=command):
                process=Mock()
                process.wait.side_effect=subprocess.TimeoutExpired(command,3)
                with patch.object(w.subprocess,'Popen',return_value=process),patch.object(w,'desktop_env',return_value={}),patch.object(w.threading,'Thread') as thread:
                    if is_editor:w._launch_opening(command)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'still running'):
                            w._launch_opening(command)
                thread.return_value.start.assert_called_once()
                process.kill.assert_not_called()
                process.terminate.assert_not_called()


if __name__=='__main__':unittest.main()
