"""`workbench setup` and `workbench remove`: the desktop pieces outside the plugin folders.

Omarchy and Herdr install a plugin by cloning it and never run its scripts, so the command,
key bindings, menu entries, agent instructions and the Herdr companion are added here, each
after showing the change and asking. Every edit to an existing file is a marked block, and
the command wrapper carries a marker line, so running setup again updates them in place and
`remove` takes out exactly what setup added.
"""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib

HERE = Path(__file__).resolve().parent
REPO_URL = 'https://github.com/VulpesZerda27/omarchy-workbench'
OMARCHY_ID = json.loads((HERE/'manifest.json').read_text())['id']
HERDR_ID = tomllib.loads((HERE/'herdr-plugin.toml').read_text())['id']
BEGIN, END = '>>> Workbench', '<<< Workbench'
WRAPPER_MARK = '# Added by `workbench setup`; `workbench remove` deletes it.'
COMMENT = {'lua': ('-- ', ''), 'jsonc': ('// ', ''), 'toml': ('# ', ''), 'md': ('<!-- ', ' -->')}

# None of these overlap Omarchy 4's own bindings. Super+Alt+Print fills an open capture the way
# Print does; taking over Print itself is a separate, opt-in question because it replaces
# Omarchy's screenshot binding (Workbench hands the key back to it when no capture is open).
BINDINGS = [('SUPER + ALT + O', 'Workbench output shelf', 'open shelf'),
            ('SUPER + ALT + C', 'Workbench capture', 'open capture'),
            ('SUPER + ALT + I', 'Workbench GitHub issues', 'open issues'),
            ('SUPER + ALT + PRINT', 'Screenshot for a Workbench capture', 'capture screenshot')]
PRINT_KEY = ('PRINT', 'Screenshot (into an open Workbench capture)', 'print')
MENU = [('workbench', '󰆧', 'Workbench', '', 'Outputs, capture drafts and GitHub issues'),
        ('workbench.shelf', '', 'Output shelf', 'open shelf', ''),
        ('workbench.capture', '', 'Capture for a thread', 'open capture', ''),
        ('workbench.issues', '', 'GitHub issues', 'open issues', ''),
        ('workbench.screenshot', '', 'Capture a screenshot', 'capture screenshot', '')]
HERDR_KEY = 'prefix+i'
DEPENDENCIES = [('herdr', 'threads, captures and the companion'), ('gh', 'the Issues view'),
                ('voxtype', 'dictation'), ('pdftoppm', 'PDF previews'), ('wl-paste', 'pasting images'),
                ('gio', 'opening files')]


def run_cmd(argv, timeout=20):
    """Run a helper command and return (ok, output) instead of raising."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def home():
    return Path.home()


def tilde(path):
    path, h = str(path), str(home())
    return '~' + path[len(h):] if path == h or path.startswith(h + '/') else path


def read(path):
    try: return path.read_text()
    except OSError: return ''


# Marked blocks

def marker(kind, text):
    start, end = COMMENT[kind]
    return start + text + end


def block_pattern(kind):
    start, end = (re.escape(part) for part in COMMENT[kind])
    # The block's own lines, plus the blank line setup puts before it.
    return re.compile(r'(?P<gap>^[ \t]*\n)?^[ \t]*' + start + re.escape(BEGIN) + r'.*?^[ \t]*' + start + re.escape(END) + end + r'[ \t]*(?:\n|\Z)', re.M | re.S)


def find_block(text, kind):
    match = block_pattern(kind).search(text)
    return match.group(0) if match else ''


def strip_block(text, kind):
    match = block_pattern(kind).search(text)
    return text[:match.start()] + text[match.end():] if match else text


def make_block(kind, lines, indent=''):
    head = marker(kind, BEGIN + ': added by `workbench setup`; `workbench remove` takes it out.')
    return '\n'.join([indent + head, *(indent + line for line in lines), indent + marker(kind, END)]) + '\n'


def put_block(text, kind, lines):
    """Replace the Workbench block, or add one: at the end, or before a JSONC object's closing brace."""
    indent = '  ' if kind == 'jsonc' else ''
    match = block_pattern(kind).search(text)
    if match:
        return text[:match.start()] + (match.group('gap') or '') + make_block(kind, lines, indent) + text[match.end():]
    if kind != 'jsonc':
        return text.rstrip('\n') + ('\n\n' if text.strip() else '') + make_block(kind, lines)
    close = text.rstrip().rfind('}')
    if close < 0:
        return '{\n' + make_block(kind, lines, indent) + '}\n'
    before = text[:close].rstrip()
    # Omarchy's menu drops whole-line comments and trailing commas, so a comma after the last entry is enough.
    content = [line for line in before.splitlines() if line.strip() and not line.strip().startswith('//')]
    if content and not content[-1].rstrip().endswith((',', '{')):
        last = before.rfind(content[-1])
        before = before[:last] + content[-1].rstrip() + ',' + before[last + len(content[-1]):]
    return before + '\n' + make_block(kind, lines, indent) + text[close:].lstrip(' \t').rstrip('\n') + '\n'


# What setup would add

def wrapper_path():
    return home()/'.local/bin/workbench'


def wrapper_text():
    return '#!/bin/sh\n' + WRAPPER_MARK + '\nexec python3 ' + shlex.quote(str(HERE/'workbench.py')) + ' "$@"\n'


def launcher(use_wrapper):
    """How bindings, the menu and agents call Workbench: the wrapper, or this folder directly."""
    return shlex.quote(str(wrapper_path())) if use_wrapper else 'python3 ' + shlex.quote(str(HERE/'workbench.py'))


def key_name(key):
    parts = [p.strip().upper() for p in key.split('+')]
    return '+'.join(sorted(parts[:-1]) + parts[-1:])


def bound_keys(texts):
    keys = set()
    for text in texts:
        for match in re.finditer(r'(?<![\w.])(?:o|hl)\.bind\(\s*"([^"]+)"', text):
            keys.add(key_name(match.group(1)))
    return keys


def lua_files(root):
    texts = []
    for path in sorted(root.glob('**/*.lua')) if root.is_dir() else []:
        try: texts.append((path, path.read_text()))
        except OSError: pass
    return texts


def binding_lines(launch, bindings):
    return ['o.bind(' + json.dumps(key) + ', ' + json.dumps(label) + ', ' + json.dumps(launch + ' ' + args) + ')' for key, label, args in bindings]


def menu_lines(launch):
    lines = []
    for ident, icon, label, args, about in MENU:
        entry = {'icon': icon} if icon else {}
        entry['label'] = label
        if args: entry['action'] = launch + ' ' + args
        if about: entry['description'] = about
        lines.append(json.dumps(ident) + ': ' + json.dumps(entry, ensure_ascii=False) + ',')
    return lines


def herdr_plugin():
    ok, out = run_cmd(['herdr', 'plugin', 'list', '--json'])
    if not ok: return None
    try: rows = json.loads(out)
    except ValueError: return None
    if isinstance(rows, dict): rows = (rows.get('result') or rows).get('plugins', [])
    return next((p for p in rows if isinstance(p, dict) and p.get('plugin_id') == HERDR_ID), None)


class Step:
    """One question in setup. `lines` are shown before asking; `present` means it is already in place."""
    def __init__(self, key, title, why, lines=(), notes=(), default=True, present=False, skip=''):
        self.key, self.title, self.why, self.lines, self.notes = key, title, why, list(lines), list(notes)
        self.default, self.present, self.skip = default, present, skip


def command_step():
    path, text = wrapper_path(), read(wrapper_path())
    foreign = path.exists() and WRAPPER_MARK not in text
    return Step('command', 'Command', 'Key bindings, the menu and agents run Workbench through ' + tilde(path) + '.',
        lines=wrapper_text().splitlines(), present=text == wrapper_text(), default=not foreign,
        notes=['A different ' + tilde(path) + ' exists; it is only replaced if you say so.'] if foreign else [])


def binding_steps(launch):
    path = home()/'.config/hypr/bindings.lua'
    if not path.exists():
        return [Step('bindings', 'Key bindings', '', skip='There is no ~/.config/hypr/bindings.lua, so no key bindings are added.')]
    block = find_block(read(path), 'lua')
    user = [strip_block(text, 'lua') if p == path else text for p, text in lua_files(home()/'.config/hypr')]
    defaults = [text for _, text in lua_files(Path(os.environ.get('OMARCHY_PATH', '/usr/share/omarchy'))/'default/hypr')]
    taken = bound_keys(user + defaults)
    free = [b for b in BINDINGS if key_name(b[0]) not in taken]
    lines = binding_lines(launch, free)
    steps = [Step('bindings', 'Key bindings', 'Open the shelf, capture and issues, and send a screenshot to a capture.',
        lines=lines, present=bool(lines) and all(l in block for l in lines),
        notes=[k + ' is already bound, so it is left alone.' for k, _, _ in BINDINGS if key_name(k) in taken],
        skip='' if lines else 'All of its keys are already bound.')]
    print_lines = ['hl.unbind("PRINT")'] + binding_lines(launch, [PRINT_KEY])
    steps.append(Step('print', 'Print key (optional)',
        'Lets Print add its screenshot to an open Workbench capture, and take Omarchy\'s usual screenshot otherwise. '
        'This replaces Omarchy\'s own Print binding; Super+Alt+Print does the same without replacing anything.',
        lines=print_lines, present=all(l in block for l in print_lines), default=False,
        skip='Your own config binds Print, so it is left alone.' if key_name('PRINT') in bound_keys(user) else ''))
    return steps


def menu_step(launch):
    path = home()/'.config/omarchy/extensions/omarchy-menu.jsonc'
    text = read(path)
    lines = menu_lines(launch)
    if re.search(r'^\s*"workbench"\s*:', strip_block(text, 'jsonc'), re.M):
        return Step('menu', 'Omarchy menu', '', skip='Your menu already has a "workbench" entry, so it is left alone.')
    return Step('menu', 'Omarchy menu', 'Adds a Workbench section to the Omarchy menu.', lines=lines,
        present=all(l in find_block(text, 'jsonc') for l in lines))


def agent_paths():
    paths = []
    for agent, folder, name, label in (('claude', '.claude', 'CLAUDE.md', 'Claude Code'), ('codex', '.codex', 'AGENTS.md', 'Codex')):
        if (home()/folder).is_dir() or shutil.which(agent):
            paths.append((agent, home()/folder/name, label))
    return paths


def agent_body(launch):
    return (HERE/'agent-instructions.md').read_text().replace('{workbench}', launch).strip().splitlines()


def agent_steps(launch):
    steps = []
    for agent, path, label in agent_paths():
        text = read(path)
        if re.search(r'workbench\S* add ', strip_block(text, 'md')):
            steps.append(Step('agents-' + agent, label + ' instructions', '', skip=tilde(path) + ' already has Workbench instructions, so it is left alone.'))
            continue
        body = agent_body(launch)
        steps.append(Step('agents-' + agent, label + ' instructions',
            'Asks ' + label + ' to put finished outputs on the shelf and keep each thread\'s brief current. Without this, the shelf and companion stay empty.',
            lines=['The Workbench sections of agent-instructions.md, added to ' + tilde(path)],
            present=make_block('md', body) in text))
    return steps


def herdr_steps():
    if not shutil.which('herdr'):
        return [Step('herdr-link', 'Herdr companion', '', skip='Herdr is not installed. Install it for threads, captures and the companion, then run setup again.')]
    plugin = herdr_plugin()
    root = Path(plugin['plugin_root']).resolve() if plugin and plugin.get('plugin_root') else None
    why = 'Links the companion to this folder, so both halves always run the same version.'
    if plugin and root != HERE:
        why = 'The companion is installed from ' + tilde(root or 'GitHub') + '. Linking it to this folder keeps both halves on the same version.'
    steps = [Step('herdr-link', 'Herdr companion', why, lines=['herdr plugin link ' + tilde(HERE)], present=root == HERE)]
    config = home()/'.config/herdr/config.toml'
    rest = strip_block(read(config), 'toml')
    lines = ['[[keys.command]]', 'key = "' + HERDR_KEY + '"', 'type = "plugin_action"', 'command = "' + HERDR_ID + '.open"', 'description = "Open the Workbench companion"']
    if HERDR_ID + '.open' in rest:
        steps.append(Step('herdr-key', 'Companion key', '', skip='Your Herdr config already binds the companion, so it is left alone.'))
    elif re.search(r'"' + re.escape(HERDR_KEY) + r'"', rest):
        steps.append(Step('herdr-key', 'Companion key', '', skip=HERDR_KEY + ' is already used in your Herdr config; bind ' + HERDR_ID + '.open to a free key yourself.'))
    else:
        steps.append(Step('herdr-key', 'Companion key', 'Your Herdr prefix, then i, opens the companion beside an agent.',
            lines=lines, present=all(l in find_block(read(config), 'toml') for l in lines)))
    return steps


# Running setup

def ask(question, default, yes):
    if yes: return default
    try: reply = input(question + (' [Y/n] ' if default else ' [y/N] ')).strip().lower()
    except EOFError: return default
    return default if not reply else reply in ('y', 'yes')


def decide(step, yes, dry_run, out):
    """Show a step and return whether it is wanted (already present counts as wanted)."""
    out('\n' + step.title + (': ' + step.why if step.why else ''))
    for note in step.notes: out('  ' + note)
    if step.skip:
        out('  ' + step.skip)
        return False
    if step.present:
        out('  Already set up.')
        return True
    for line in step.lines: out('    ' + line)
    if dry_run:
        out('  Would ' + ('apply' if step.default else 'skip') + ' by default.')
        return step.default
    if yes:
        out('  ' + ('Applying.' if step.default else 'Skipped (opt-in).'))
    return ask('  Apply?', step.default, yes)


def write(path, text):
    """Write a file, keeping a timestamped copy of the previous version next to it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = None
    if path.exists():
        saved = path.with_name(path.name + '.bak.' + str(int(time.time())))
        shutil.copy2(path, saved)
    path.write_text(text)
    return saved


def plugin_folder_note():
    installed = home()/'.config/omarchy/plugins'/OMARCHY_ID
    if not installed.exists():
        return 'The Omarchy plugin is not installed here. Install it with: omarchy plugin add ' + REPO_URL + '.git --enable'
    if installed.resolve() != HERE:
        return 'This folder is not the installed Omarchy plugin. Run setup from ' + tilde(installed) + ' instead.'
    return ''


def dependency_note():
    missing = [(name, use) for name, use in DEPENDENCIES if not shutil.which(name)]
    if not missing: return 'All dependencies are installed.'
    return 'Not installed: ' + '; '.join(name + ' (' + use + ')' for name, use in missing) + '.'


def setup(yes=False, dry_run=False, out=print):
    if not (yes or dry_run or sys.stdin.isatty()):
        out('Run `workbench setup` in a terminal so it can ask before each change, or pass --yes to accept the defaults.')
        return 2
    out('Workbench setup for ' + tilde(HERE))
    note = plugin_folder_note()
    if note: out(note)
    out(dependency_note())
    command = command_step()
    use_wrapper = decide(command, yes, dry_run, out)
    launch = launcher(use_wrapper)
    steps = binding_steps(launch) + [menu_step(launch)] + agent_steps(launch) + herdr_steps()
    wanted = {step.key for step in steps if decide(step, yes, dry_run, out)}
    if use_wrapper: wanted.add('command')
    if dry_run:
        out('\nDry run: nothing was changed.')
        return 0
    return apply(wanted, launch, steps, out)


def file_edits(wanted, launch, steps):
    """The new contents of each file setup touches, built only from the wanted steps."""
    by_key = {step.key: step for step in steps}
    edits = []
    if 'command' in wanted and read(wrapper_path()) != wrapper_text():
        edits.append((wrapper_path(), wrapper_text()))
    bindings = home()/'.config/hypr/bindings.lua'
    lines = (by_key['bindings'].lines if 'bindings' in wanted else []) + (by_key['print'].lines if 'print' in wanted else [])
    if lines:
        edits.append((bindings, put_block(read(bindings), 'lua', lines)))
    if 'menu' in wanted:
        menu = home()/'.config/omarchy/extensions/omarchy-menu.jsonc'
        edits.append((menu, put_block(read(menu) or '{\n}\n', 'jsonc', by_key['menu'].lines)))
    for agent, path, _ in agent_paths():
        if 'agents-' + agent in wanted:
            edits.append((path, put_block(read(path), 'md', agent_body(launch))))
    if 'herdr-key' in wanted:
        config = home()/'.config/herdr/config.toml'
        edits.append((config, put_block(read(config), 'toml', by_key['herdr-key'].lines)))
    return [(path, text) for path, text in edits if read(path) != text]


def apply(wanted, launch, steps, out):
    changed, problems = [], []
    by_key = {step.key: step for step in steps}
    for path, text in file_edits(wanted, launch, steps):
        saved = write(path, text)
        if path == wrapper_path(): path.chmod(0o755)
        changed.append(tilde(path) + (' (previous version: ' + saved.name + ')' if saved else ''))
        if path.suffix == '.lua' and os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'):
            run_cmd(['hyprctl', 'reload'])
            ok, errors = run_cmd(['hyprctl', 'configerrors'])
            if ok and errors.strip() and 'no errors' not in errors.lower():
                problems.append('Hyprland reports config errors:\n' + errors)
    if 'herdr-link' in wanted and not by_key['herdr-link'].present:
        plugin = herdr_plugin()
        if plugin:
            local = (plugin.get('source') or {}).get('kind') == 'local'
            run_cmd(['herdr', 'plugin', 'unlink' if local else 'uninstall', HERDR_ID])
        ok, output = run_cmd(['herdr', 'plugin', 'link', str(HERE)])
        (changed if ok else problems).append('herdr plugin link ' + tilde(HERE) + ('' if ok else ': ' + output[-300:]))
    if wanted & {'herdr-link', 'herdr-key'} and shutil.which('herdr'):
        ok, output = run_cmd(['herdr', 'config', 'check'])
        if not ok: problems.append('herdr config check: ' + output[-300:])
        if run_cmd(['herdr', 'api', 'snapshot'])[0]:
            run_cmd(['herdr', 'server', 'reload-config'])
            run_cmd(['herdr', 'plugin', 'action', 'invoke', HERDR_ID + '.start-automation'])
    out('')
    out('Changed:\n' + '\n'.join('  ' + c for c in changed) if changed else 'Nothing needed changing.')
    if problems:
        out('Problems:\n' + '\n'.join('  ' + p for p in problems))
        return 1
    return 0


# Running remove

def remove_plan():
    """(description, path, new text or None to delete) for everything setup added that is still here."""
    plan = []
    if WRAPPER_MARK in read(wrapper_path()):
        plan.append((tilde(wrapper_path()), wrapper_path(), None))
    for path, kind in ((home()/'.config/hypr/bindings.lua', 'lua'), (home()/'.config/omarchy/extensions/omarchy-menu.jsonc', 'jsonc'),
                       (home()/'.claude/CLAUDE.md', 'md'), (home()/'.codex/AGENTS.md', 'md'), (home()/'.config/herdr/config.toml', 'toml')):
        text = read(path)
        if find_block(text, kind):
            plan.append(('The Workbench block in ' + tilde(path), path, strip_block(text, kind)))
    return plan


def linked_companion():
    if not shutil.which('herdr'): return False
    plugin = herdr_plugin()
    return bool(plugin and plugin.get('plugin_root') and Path(plugin['plugin_root']).resolve() == HERE)


def remove(yes=False, out=print):
    if not (yes or sys.stdin.isatty()):
        out('Run `workbench remove` in a terminal so it can confirm first, or pass --yes.')
        return 2
    plan, unlink = remove_plan(), linked_companion()
    if not plan and not unlink:
        out('Nothing that `workbench setup` added is left.')
    else:
        out('This removes what `workbench setup` added:')
        for what, _, _ in plan: out('  ' + what)
        if unlink: out('  The Herdr companion link (herdr plugin unlink ' + HERDR_ID + ')')
        if not ask('Remove them?', yes, yes):
            out('Nothing was changed.')
            return 0
        problems = []
        for what, path, text in plan:
            if text is None: path.unlink(missing_ok=True)
            elif not text.strip(): path.unlink()  # Only setup's block was in it, so setup created it.
            else: write(path, text)
            if path.suffix == '.lua' and os.environ.get('HYPRLAND_INSTANCE_SIGNATURE'): run_cmd(['hyprctl', 'reload'])
        if unlink:
            ok, output = run_cmd(['herdr', 'plugin', 'unlink', HERDR_ID])
            if not ok: problems.append('herdr plugin unlink: ' + output[-300:])
        if shutil.which('herdr') and run_cmd(['herdr', 'api', 'snapshot'])[0]:
            run_cmd(['herdr', 'server', 'reload-config'])
        out('Removed. Edited files keep their previous version next to them as .bak files.')
        if problems: out('Problems:\n' + '\n'.join('  ' + p for p in problems))
    out('Your outputs, notes and drafts stay in ' + tilde(home()/'.local/share/omarchy/workbench') + '; delete that folder to remove them too.')
    out('To uninstall the plugin: omarchy plugin remove ' + OMARCHY_ID)
    return 0
