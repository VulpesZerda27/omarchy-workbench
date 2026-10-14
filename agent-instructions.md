# Workbench output shelf

When you create a finished deliverable for the user (a report, document, image, export, or usable preview), register its existing file or URL with `{workbench} add TARGET --title 'Descriptive title'`. Do not register routine source files, logs, temporary tests, or configuration edits.

Before the first registration in a conversation, run `{workbench} threads` and match the thread by its topic and project against this conversation. When the match is clear, supply its ID with `--thread ID` on every registration. Do not infer this from whichever thread is focused. Shared agent processes can carry stale environment values. If the match is unclear, omit `--thread` and leave the output unassigned rather than attaching it to an unrelated thread.

Keep normal deliverable links in your reply too. Shelf registration is local bookkeeping; do not submit captures to agents unless the user requests it.

# Workbench companion brief

The user relies on the agent to keep the Workbench companion current, with no manual note-taking. For the clearly matched thread, update its brief at meaningful milestones during substantial work and before your final handoff:

`{workbench} brief --thread ID --summary 'What changed or what we learned' --next 'The next useful step' --waiting 'A real decision or action needed from the user'`

Use one concise, plain-language sentence per field. Omit `--waiting` when nothing is needed; do not invent a blocker or a task for the user. Describe actual progress and limitations. Replace stale status instead of accumulating a log. If the work is complete, say so and make the next step an appropriate test or use step, or say no further work is planned. The command preserves any separate personal note.

Use the same explicit thread-matching rule as the shelf. If the thread cannot be identified confidently, skip the update. Never use another thread's inherited environment or send another agent a prompt merely to update a brief.
