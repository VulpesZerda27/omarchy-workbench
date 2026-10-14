# Publishing Workbench

Notes for releasing Workbench to other Omarchy and Herdr users. Checked against Omarchy 4.0.4 (Quattro) and Herdr 0.9.1.

## Status

The repository is ready to publish; the only steps left are the public ones below.

Done:

- Permanent plugin IDs: `io.github.vulpeszerda27.workbench` (Omarchy) and `io.github.vulpeszerda27.workbench-companion` (Herdr). Version 0.9.0, MIT license.
- `workbench setup` adds what `omarchy plugin add` cannot: the command wrapper, key bindings, Omarchy menu entries, agent instructions ([agent-instructions.md](../agent-instructions.md)), and the Herdr companion linked to the same checkout with its key. It asks before each change, writes marked blocks and keeps `.bak` copies. `workbench remove` takes out exactly what setup added. Both are covered by `tests/test_integration.py`.
- Default keys don't overlap Omarchy's: Super+Alt+O, C, I and Super+Alt+Print. Taking over Print is an opt-in question. The hints name whichever key is bound to Workbench's screenshot.
- Projects are ordered by recent activity directly (no personal script), interface copy and tests use neutral names, and the README is written for other users, with first steps and troubleshooting.
- Without Herdr, or with Herdr stopped, the panels say so in plain words and the shelf keeps working. The companion worker restarts itself after an update.
- `preview.png` is made from demo data (made-up threads, files and issues), not from a real machine.
- Checked by installing with `omarchy plugin add` into an empty home folder with Omarchy's stock config files, running setup (twice) and remove: the files parse, setup is idempotent, and remove restores `bindings.lua` and the menu file exactly. Herdr was a stand-in in that run.

Not tested: a real second machine or user account with Hyprland and Herdr running, Omarchy or Herdr versions other than the ones above, and remote Herdr machines (marked experimental in the README).

## Release steps

1. Publish a fresh history. Earlier commits contain the names of private repositories in test fixtures, so the public `main` should start from one squashed commit of the current tree. Keep the full history locally (for example as a `private-history` branch) and don't push it.
2. Force-push that `main` to `github.com/VulpesZerda27/omarchy-workbench`, then make the repository public.
3. Submit to the Omarchy marketplace with the text below (`gh issue create --repo omacom/omarchy-plugin-marketplace --title "[Plugin]: Workbench" --body-file …`). The owner must confirm every checklist line first.
4. Add the GitHub topic `herdr-plugin` so the Herdr marketplace lists the repository. Its listing is automatic and unreviewed; the README tells Herdr users to install through Omarchy.
5. Watch the submission issue for the validation and security-baseline comments and answer any questions.

### Omarchy marketplace submission

```markdown
### Repository URL

https://github.com/VulpesZerda27/omarchy-workbench

### Category

Productivity

### Tags

ai, quickshell, hyprland

### Suggest a missing tag

_No response_

### Maintainer notes

Workbench is for running Claude Code or Codex in Herdr; Herdr is needed for threads, captures and the companion. `omarchy plugin add` only clones it; `workbench setup` then adds the key bindings, menu entries, agent instructions and Herdr companion, asking before each change and keeping a backup of every file it edits. `workbench remove` takes all of it out. The default keys don't overlap Omarchy's; replacing Print is an opt-in question. The baseline will probably report `service-management`: workbench.py reads the user session's environment with `systemctl --user show-environment` (read-only) so apps open on the right display when Herdr started over SSH.

### Submission checklist

- [x] The repository is public and contains installation and removal instructions.
- [x] I have documented the plugin license and any external dependencies.
- [x] I confirm that I own or have permission to submit this plugin and its preview assets.
- [x] The plugin does not overwrite user configuration without explicit consent.
- [x] I understand that approval is for listing and is not a security review.
```

## Releasing an update later

1. Bump `version` in both `manifest.json` and `herdr-plugin.toml`.
2. Run the tests, `omarchy plugin validate .` and `herdr config check`, then push to `main`.
3. Open the marketplace's **Plugin verification** issue form, choose **Verify and publish a newer upstream commit**, and give the plugin ID, the repository URL and the full commit SHA. Until a maintainer approves it, the listing shows `Update unverified`.

Users update with `omarchy plugin update io.github.vulpeszerda27.workbench`, which updates both halves because the companion runs from the same folder. The companion's worker notices the new code and restarts on it by itself; an open companion pane picks it up when it is reopened.

## How the marketplaces work

### Omarchy

Official listing repository: <https://github.com/omacom/omarchy-plugin-marketplace>. Read its `SUBMISSION.md` and `SECURITY.md` again before submitting; the process may have changed.

- One plugin per public GitHub repository root, with `manifest.json`, a README with installation and removal instructions, and a license file. External dependencies must be documented. A root `preview.png` is optional; they resize it.
- Plugin IDs are permanent and unique across the marketplace; retired IDs can't be reused.
- Automated validation and a static security baseline run on the exact commit, then a maintainer applies `approved-and-verified`. The baseline flags `systemctl` as `service-management`, and any file whose name contains `install` or `setup` as an installer, so setup lives in `integration.py`.
- `omarchy plugin add <git-url>` only clones the repository into `~/.config/omarchy/plugins/<id>/` and validates it. It never runs install hooks, scripts or sudo. Plugins can't declare key bindings or menu entries themselves, which is why `workbench setup` exists.

### Herdr

Listing: <https://herdr.dev/docs/plugins/>.

- Any public repository with the GitHub topic `herdr-plugin` is indexed automatically, about every 30 minutes, without review.
- `herdr-plugin.toml` can sit at the root or in a subfolder. Required fields: `id`, `name`, `version`, `min_herdr_version`.
- Plugins can't declare key bindings; users add `[[keys.command]]` to `config.toml`, which setup does in a marked block.
- Startup hooks run once when the server starts, not on link or enable, so setup starts the companion worker itself. Plugins must stop their own background processes; the worker exits within a few seconds when Herdr stops, restarts or the plugin is disabled.
