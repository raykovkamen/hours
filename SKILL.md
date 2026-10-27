---
name: hours
description: Daily timesheet — today's (or a given day's) hours per client from local evidence (ActivityWatch with browser URLs and VS Code, Claude Code sessions, git commits, calendar meetings, manual notes). Use when the user asks for today's hours / time for today's tasks, or runs /hours [YYYY-MM-DD]. `/hours push [YYYY-MM-DD]` logs the table into ClickUp; `/hours add "Client call" 30m` notes unseen time; `/hours week` shows the week; `/hours check` verifies the installation.
---

All commands run from this skill's base directory (shown above) with `py -3 -I hours.py …` on Windows and `python3 -I hours.py …` on macOS/Linux. `$ARGUMENTS` is `[push|add|week|check] …`; a date (`YYYY-MM-DD`) defaults to today.

## `/hours [DAY]` — the table

Run `hours.py [DAY]`. It merges, on a one-minute grid: ActivityWatch (window in front minus AFK time; with the browser extension also the active tab's URL, with the VS Code extension the edited project), all Claude Code sessions of the day across every project folder, the user's git commits, calendar meetings (if `calendar_ics` is set) and manual notes. It prints a source summary, the evidence that still needs a client name, and the table. `README.md` here documents the attribution rules if a split needs explaining.

What the output means:
- The table starts at the configured workday start with one `Break` row, in the configured step (default 15 min). Each client is one row (a client with several tasks has one row per task), split only around the break. The footer states the real tracked time per client and whether day bounds from `config.toml` were applied.
- `ClickUp task` comes from a task id in the branch name or commit subject, a ClickUp link in a prompt, or the client's default in `sites.txt`; `—` when none.
- A row starting with `?` is evidence nobody could name: a session started in a container folder (`D:\ERP`) that never names a client, a one-off question, a repository not in `sites.txt`, a `? meeting:` whose title names no client, or a `? note:`. The listing shows what is behind it.
- `Unknown` is real active time (usually unmatched windows) that no client could inherit. Leave it unless the user reassigns it.
- The ActivityWatch section lists window titles, URLs (host and path) and VS Code projects that matched no rule, with minutes. Offer a `sites.txt` pattern for any that clearly belong to a client — for Shopify admin URLs the store handle (`admin.shopify.com/store/<handle>`) is the best pattern. If the summary says an extension is not installed, mention it once; never install anything yourself unless asked.
- `--all` lists every session, commit, meeting and note; use it when the user questions the split.

Reply with only the table. Change it only to name `?` rows:
- Name them from the evidence listed above the table. A `? one-off:` row is a quick question about another client; name that client. If nothing names a client, write `Other client (question)`. Never infer a client from the topic ("Magento migration" is not a client). An internal meeting or note belongs to the company itself if `sites.txt` has a rule for it.
- When a `?` row is named to a client that already has a row, merge them into one row (add the hours, keep the earlier start) and shift the following rows so the table stays contiguous.
- If the client will come up again, offer to add a rule to `sites.txt`. If `sites.txt` does not exist the script uses `sites.example.txt` and says so; offer to create it.

## `/hours add "TEXT" DURATION [--at HH:MM] [DAY]` — time no source can see

For a phone call, a meeting without a calendar entry, work away from the computer. Run `hours.py add "TEXT" DURATION` with the user's words; TEXT should name the client ("Josera call about the ES store"), DURATION is `30m`, `1h`, `1h30m`. Without `--at` the note ends now (today) — for another day `--at` is required. Show the script's one-line confirmation. Notes live in `logs/manual-<day>.jsonl`; to correct one, edit or delete its line there.

## `/hours week [DAY]` — the week

Run `hours.py week [DAY]` and show its tables: per day the tracked and reported hours, Unknown minutes, unnamed rows and how much of the reported time is already in ClickUp (read from ClickUp when a token is set); then tracked hours per client. Point out days with unnamed rows, a lot of Unknown, or time missing from ClickUp — those are the days to fix while the user still remembers them.

## `/hours push [DAY]` — write to ClickUp

Only when the user said `push` (or "log it to ClickUp", "push the hours"). Plain `/hours` never writes.

In `create` mode (`[clickup] mode = "create"` in config.toml, or `--create`) the push makes one new "in progress" task per client per day in the client's list (`list:<id>` in sites.txt) and logs the time there, instead of using existing tasks; a row is then blocked only when its client has no list. Use `--create` only when the user asks for new tasks or the config already says so.

**Titles in create mode.** Before the dry run, run `hours.py [DAY] --json` and read `evidence` (per client, in time order: `commit` subjects, the user's `prompt`s, Claude's `reply` texts, `meeting` titles and `note`s). Claude's replies say what was actually done and are usually the best material; prompts alone are often too short ("do it"). For every client in the table write one task title that says what was done, e.g. `Checkout VAT field and ERP SKU sync`: English, at most 60 characters, the outcome rather than the conversation, no prices, credentials, personal data or pasted client text. Evidence is for your understanding only; never copy prompts or replies into a title. Pass the titles as `--title "Site=…"` on both the dry run and the real push. Without `--title` the script uses the day's first commit subject, or the date when there were none. The task name is the title alone (no client prefix) and the task has no description. Its "Initial prompt" custom field (`[clickup] prompt_field`) is filled with the first prompt of the earliest Claude session behind the client's work that day, as the user wrote it, pasted text included; the dry run shows its start. Pass `--prompt "Site=…"` only if the user wants a different one. A later push the same day finds the task through the local push log (`logs/`), so a changed title never creates a second task or renames the first; if the task was deleted in ClickUp, a new one is created.

1. Run `hours.py push [DAY] --dry-run`. It prints the table and either the plan (what would be posted per task, what is already logged) or `Push blocked` with the rows to resolve.
2. If blocked: resolve every reason with the user. A `?` row needs a client (as above; add the rule to `sites.txt` or re-run after the fix). A row without a task needs a task id: ask the user, or look up the client's recent tasks with the ClickUp connector (`clickup_get_time_entries` for the last 14 days) and offer them. Pass the answers as `--assign "Site=taskid"` (one per client) and offer to store them as defaults in `sites.txt`. Repeat the dry run until it is clean. `--only Site` pushes just that client's rows.
3. Show the user the dry-run plan and run `hours.py push [DAY]` with the same options. The script validates every task, posts only the difference to what is already logged, and appends to `logs/push-<day>.jsonl`.
4. Reply with the table and the script's "Posted" section verbatim (task name, start, minutes, entry id, skipped tasks). If the script exits non-zero, show its message and stop; do not retry with different options on your own.

The push needs a ClickUp personal token in `config.toml` (`[clickup] token`) or `CLICKUP_TOKEN`. If it is missing the script says so; tell the user where to put it (README, "ClickUp token"). Never ask the user to paste the token — or the calendar's secret iCal address — into the chat.

## `/hours check` — verify the installation

Run `hours.py check` and show its output. Every `FAIL` line says what to fix and every `tip` line names an optional improvement (browser extension, VS Code extension, calendar); help the user with them (copy the example files, set `git_roots`, start ActivityWatch, add the token, set `calendar_ics`).

Never set this up as a scheduled task, cron or hook.
