# Time tracking with `hours` — team policy

This is how the development team records time in ClickUp. It is short on purpose; the tool's own
README explains the mechanics.

## What the tool is

`hours` reconstructs a developer's day from evidence on their own machine (which window was in front,
Claude Code sessions, git commits) and turns it into a per-client table that can be pushed into ClickUp
as time entries. It replaces starting and stopping timers. It does not replace judgement: the table is
an estimate from evidence, and the developer confirms it before it is pushed.

## Daily routine

1. At the end of the day (or whenever convenient) run `/hours` in Claude Code, or `hours.py` directly.
2. Read the table. Fix anything that is wrong:
   - a `?` row: name the client (the listing shows the prompts or commits behind it);
   - an `Unknown` row: assign it, or add a `sites.txt` pattern for the window title that caused it;
   - time on the wrong client: adjust the rule that caused it, or edit the row.
3. Make sure every row has a ClickUp task. Prefer putting the task id in your branch name
   (`feature/86cabc123-short-name`) so the tool picks it up on its own; otherwise set a default task per
   client in `sites.txt` or pass `--assign`.
4. Push: `/hours push` (Claude runs a dry run first and shows it) or `hours.py push --dry-run` then
   `hours.py push`. Pushing again later the same day only adds the difference.

Pushed entries are the developer's statement of their time, exactly as a manual entry would be. The
tool records what it pushed in `logs/` on the developer's machine.

## What is reported

- The table reports **tracked time**: minutes some source saw you working, rounded to 15 minutes, with
  every client getting at least one step. Meetings, calls and reading that happen away from the
  computer are not seen; add them in ClickUp by hand or configure the day bounds consciously.
- `config.toml` can pad or cap the reported day (`min_hours`, `max_hours`). The default is off. If a
  team or client agreement requires a fixed day length, the developer sets the bounds and the table
  footer still shows the real tracked time, so the two numbers are never confused.

## Privacy

- ActivityWatch records window titles and application names on the developer's own machine. Nothing is
  uploaded; `hours` reads it locally. Titles that matched no client are shown only in the developer's own
  terminal so a rule can be added.
- What reaches ClickUp: each time entry (task, start time, duration, the text `hours: <client>`). In
  create mode (`[clickup] mode = "create"`) also a new task per client per day, named after the work, and
  its **Initial prompt** field: the developer's first Claude prompt for that work, including any pasted
  client text. It is visible to everyone with access to the task. Developers who do not want that set
  `prompt_field = ""`. Without an explicit title the first commit subject of the day becomes the task name.
  Window titles and all other prompts never leave the machine.
- Developers install ActivityWatch themselves, knowing the above. It is optional: without it the day is
  built from Claude sessions and commits only, and the listing says so.

## Responsibilities

- **Developer**: keeps `sites.txt` current, reviews the table before pushing, corrects attribution
  mistakes in ClickUp when they slip through.
- **Lead**: owns this policy and the shared `sites.example.txt` conventions; reviews ClickUp time
  weekly as before.
