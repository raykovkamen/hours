# 🕴️ hours

### *Forgot to start the timer? Worked on four clients at once and ClickUp is still empty at 18:00?*
### **Better call `/hours`!**

---

> *"Hi. I'm your timesheet. Did you know that you have rights? The Constitution says you do. And so do I.
> I believe that until proven otherwise, every minute you spent working is a minute that deserves
> to be on the record. Not one more. Not one less."*

Project manager squinting at an empty ClickUp? Can't remember whether the Shelly thing was before or after
the Josera thing? Spent the afternoon in Slack "aligning"?

**Don't panic. Don't guess. Don't touch that timer.**

One command. Zero memory required. Five witnesses who were there the whole time. It's all good, man. ⚖️

(For the version of this document with the jokes removed, see [`docs/TEAM-POLICY.md`](docs/TEAM-POLICY.md).
That's the one the firm actually operates under.)

---

## 👀 Our witnesses

We don't ask you what happened. We ask the people who saw it.

| Witness | Testimony | How they know which client |
|---|---|---|
| **ActivityWatch** | Which window was in front, every few seconds, with away-from-keyboard time struck from the record. Sees the admin, the theme editor, Slack, email. With its browser extension, the **address** of the tab; with its VS Code extension, the **project** you're editing. | The window title, the URL (`admin.shopify.com/store/acme-shop` names the store even when the tab title doesn't), the VS Code project. Matched against `sites.txt`. |
| **Claude Code** | Every session you had today, in every project. Gaps over 15 minutes don't count — your AI was working, but you were *supervising* elsewhere. | The project folder. Name another client in a prompt and the session follows it. Paste a ClickUp link and it follows the task. |
| **git** | Your commits. Each one vouches for up to 30 minutes of honest labour before it, capped at the previous commit. | The repository. A task id in the branch name (`feature/86cabc123-header`) or the commit subject names the task. |
| **Calendar** | Your meetings, from your calendar's secret iCal address. They count even when the laptop slept through them — that's the whole point. Declined, cancelled, all-day and "free" events don't. | The meeting title. `Josera weekly` goes to Josera; `Dentist` goes to a `?` row and a stern look. |
| **You** | `hours.py add "Josera call about the ES store" 30m` — for the call on your phone, the meeting in the kitchen. | Your words. Name the client. |

They testify onto a one-minute grid. Then:

- A minute is **on the clock** when any witness saw you working.
- Two clients claim the same minute? They **share it**. We're creative, not criminal.
- A minute nobody could place (an unmatched window title) **inherits the nearest client** within
  30 minutes. You were on Slack *about* the same thing. Probably.
- Still nobody? It goes on the table as **Unknown**. We don't make evidence disappear. We *flag* it.

---

## 🧾 What you'll be billed

- **Tracked time, rounded to 15 minutes.** Every client on the table gets at least one step. But a client
  with under 5 real minutes (`min_client_minutes`) is a passing question, not a consultation — it's noted
  under the table and its minutes are absorbed by the clients you actually worked for. We have *some* principles.
- **A 1h break, if you took one**: placed on your longest real pause (30+ min, with work before and after)
  starting between 11:00 and 15:00. Worked straight through? No break row. Took two hours? We only need
  to know about one of them.
- **Nothing padded, nothing capped — by default.** `min_hours` and `max_hours` in `config.toml` can bound
  the reported day if your firm bills that way. The footer always prints the real tracked time next to
  it, so when the subpoena arrives, the two numbers are not confused. We learned that one the hard way.

---

## 🤝 How to become a client

Three steps. No retainer. One question asked, and it's `check`.

**macOS / Linux**
```bash
git clone https://github.com/raykovkamen/hours.git ~/.claude/skills/hours
cd ~/.claude/skills/hours
cp config.example.toml config.toml && cp sites.example.txt sites.txt
python3 -I hours.py check
```

**Windows (PowerShell)**
```powershell
git clone https://github.com/raykovkamen/hours.git "$env:USERPROFILE\.claude\skills\hours"
cd "$env:USERPROFILE\.claude\skills\hours"
Copy-Item config.example.toml config.toml; Copy-Item sites.example.txt sites.txt
py -3 -I hours.py check
```

`check` tells you exactly what's missing: your repository folders (`git_roots`), your clients
(`sites.txt`), ActivityWatch, the ClickUp token. Fix the `FAIL` lines, run it again, done. Both personal
files are git-ignored. *Attorney–client privilege.*

Requires Python 3.11+, git, Claude Code. Restart Claude Code and `/hours` is ready to take your case.
Latest legal strategy: `git pull` in that folder.

### 🕵️ Retaining the first witness

ActivityWatch is optional, but without it we only have Claude and git, and they didn't see the afternoon
you spent in the Shopify admin.

```
winget install --id ActivityWatch.ActivityWatch --exact    # Windows
brew install --cask activitywatch                          # macOS
```

Start it once. It lives in the tray, starts with the system, and **never leaves your machine** —
we read it at `localhost:5600` and that's as far as it goes.

**Two extensions make the first witness twice as sharp**, both free, both from ActivityWatch:

- **Browser:** "ActivityWatch Web Watcher" from the Chrome Web Store (or Firefox Add-ons). Without it, a tab
  called "Shopify Plus - Google Chrome" is a mystery. With it, it's `admin.shopify.com/store/josera-hungary`.
  When the address and the tab title disagree, the address wins — it's the more reliable witness.
- **VS Code:** `code --install-extension ActivityWatch.aw-watcher-vscode`, then **reload VS Code**
  (Ctrl/Cmd+Shift+P → *Developer: Reload Window*) — the extension stays silent until you do. Places hours of
  editing you never committed.

`check` tells you which ones it found, and the summary line says how many minutes each one placed — or
that VS Code still needs that reload. Incognito tabs are never read.

### 📅 Calling the calendar as a witness

Google Calendar → Settings → your calendar → *Integrate calendar* → **Secret address in iCal format**.
Put it in `config.toml` as `calendar_ics = "https://…"` (or a path to an `.ics` file — Outlook works too).
It's a secret: it goes in the config, never in a chat. Add your calendar address to `my_emails` if it
isn't your git email, so the meetings you declined stay off the record.

### 🔑 The ClickUp token

Settings → Apps → API Token in ClickUp. Put it in `config.toml` under `[clickup] token`, or export
`CLICKUP_TOKEN`. It reads your own time entries and writes new ones. It is never printed, never logged,
and you never paste it into a chat. Not even to me.

---

## 📞 How to retain our services

In Claude Code:

```
/hours                 today's table
/hours 2026-10-20      for when the client asks about last Tuesday
/hours push            put it on the record in ClickUp (we show you the plan first)
/hours add "Josera call" 30m    the call no machine saw
/hours week            the whole week: hours, gaps, what's not in ClickUp yet
/hours check           is everything in order?
```

Or, if you prefer to do things yourself (respect):

```
hours.py [DAY] [--all] [--json]
hours.py push [DAY] [--dry-run] [--only Site] [--assign "Site=taskid"]
hours.py push [DAY] --create [--title "Site=…"] [--prompt "Site=…"]   # create mode, see below
hours.py add "TEXT" 30m [--at HH:MM] [DAY]
hours.py week [DAY]
hours.py check
```

Prefix with `py -3 -I` on Windows, `python3 -I` elsewhere. You'll receive a clean, professional,
court-ready table:

| Time | Hours | Site | ClickUp task |
|---|---|---|---|
| 09:00–10:30 | 1.5 | Acme | 86c9abc12 |
| 10:30–12:00 | 1.5 | Zeta | — |
| 12:00–13:00 | 1 | Break | |
| 13:00–16:00 | 3 | Acme | 86c9abc12 |
| **Total** | **6h** (+1h break) | | |

*Look at that. Every minute accounted for, and every one of them real. Beautiful.*

The **ClickUp task** column says where each row will be filed when you log on existing tasks. In create
mode the push opens the tasks itself, so there's nothing to show yet and the column quietly leaves the room.

Once a week, `/hours week` reads the whole week back to you — tracked and reported hours per day,
how much is Unknown, which days have unnamed rows, and how much of each day is **actually in ClickUp**
(read from ClickUp itself, not from our memory). Fix the gaps on Friday, not at the end of the month.

---

## 📋 Putting it on the record (`push`)

This is the only thing we do that writes anywhere, so it's done by code, not by vibes. Two ways to file,
chosen with `[clickup] mode` in `config.toml`:

### Mode `existing` — log on the tasks you already have (the default)

Every row needs a ClickUp task. We find it in this order: a task id in your branch name, in the commit
subject, a ClickUp link in a prompt, or the default on the client's line in `sites.txt`. Put the id in
your branch name (`feature/86cabc123-header`) and you'll never think about it again.

### Mode `create` — open a fresh file every day

We don't touch existing tasks at all. Each client gets **one new task per day** in its list, and the
day's time goes there:

- **Name:** what you did, nothing else — `Checkout VAT field and ERP SKU sync`. With `/hours push`, Claude
  reads the day's commits, your prompts and its own replies — the replies say what actually got done —
  and writes it; you approve it in the dry run. By hand: `--title
  "Acme=…"`, or we take your first commit subject, or the date if you didn't commit.
- **Status** *in progress*, **assigned** to you, **no description**, and the tag **`created by hours`** — so
  anyone can see (and filter) which tasks the skill opened. `task_tag = ""` if you'd rather it didn't sign its work.
- **Initial prompt** (the custom field, if the list has it): the request that started the work — the first
  prompt of the Claude session behind it, exactly as you typed or pasted it. `--prompt "Acme=…"` to
  file a different one, `prompt_field = ""` to file none.
- **The list** comes from `list:<id>` on the client's line in `sites.txt` (the number in the list's URL),
  or `[clickup] list_id` as the catch-all.
- **Push again later** and we find the same task — even if you renamed it. We look in `logs/` first; lost
  the folder, or pushing from your other laptop? We find it through today's `hours: <client>` entries
  instead. Delete it in ClickUp, and the next push opens a fresh one.

### Either way

1. **Nothing is filed half-done.** A `?` row, an `Unknown` row, a row without a task (existing mode) or
   without a list (create mode) blocks the push and tells you exactly what to fix.
2. **Everything is checked first.** A mistyped task or list id stops the push before a single entry exists.
3. **ClickUp ends up matching the table.** We read what's already logged per task today. Table has more?
   We post the difference. Table has the same? We do nothing — push at noon, push at five, push again by
   accident, nothing doubles. Table has *less* (you named a `?` row, a minute moved to another client)? We
   shorten or remove the entries **we** filed, newest first. Entries you logged by hand are your
   testimony: we never touch them, we just tell you when they add up to more than the table.
   ClickUp having a bad day (rate limits, a 503, a dropped connection)? We wait and try again.
4. **One entry per task**, starting at its first row, described `hours: <client>`, non-billable unless you
   say otherwise. Every run, dry or real, goes in `logs/push-<day>.jsonl`. Receipts.
5. **Dry run first.** `/hours push` always shows you the plan before it files anything. By hand:
   `--dry-run`. Only want one client today? `--only Acme`.

---

## 🗂️ `sites.txt` — the client list

```
pattern = Site Name | clickup_task_id list:clickup_list_id    # both optional
```

A `pattern` is a case-insensitive substring tried against folder names, repository names, window titles
and prompts. First match wins, so specific lines go above generic ones. Add store domains and admin page
titles (`acme.com = Acme`) so browser work gets placed. Unknown project folder? We name it after the folder
(`what-the-pink` → `What The Pink`), append the line, and let you fix the spelling.

---

## ⚠️ The fine print (read it, because I won't say it twice)

- This is an **estimate from evidence**, not a stopwatch. It cannot see the call you took on your phone
  or the whiteboard session in the kitchen — unless it's in your calendar or you `add` it. Like an honest
  person would.
- `--all` shows where every minute came from. Read it before you push, and *definitely* before you argue.
- The pushed entries are **your** statement of your time, the same as if you'd typed them in. We're
  counsel, not the defendant.
- Everything runs locally and reads only your own data: session logs, repositories, ActivityWatch (titles,
  URLs, VS Code projects), your calendar feed, your notes. None of it is uploaded. What reaches ClickUp: the time entry (task,
  start, duration, `hours: <client>`) and, in create mode, the new task's title and its **Initial prompt**
  field — your first prompt for that work, pasted client text included. Anyone who can see the task can
  read it. Set `prompt_field = ""` if that's more than you want on the record.
- No scheduled jobs, no cron, no hooks. This firm only works when you call.

---

## 🧰 Behind the counter

```
python -m unittest discover -s tests -t .
```

`hours.py` is the front desk. `timesheet/` is the firm: `config.py`, `sites.py`, `timeline.py` (the grid),
`allocate.py` (slots and the schedule), `report.py`, `clickup.py` (the API), `push.py` (the paperwork),
and `sources/` with one file per witness: `activitywatch.py`, `claude_sessions.py`, `git_commits.py`,
`calendar_ics.py` (with its own recurrence engine — weekly standups, last-Friday retros, moved meetings),
`manual.py`. Adding a witness — a calendar, a ticket system — is one module
and one line. A hundred and four tests cover the lot, including a fake ClickUp, a fake ActivityWatch, a real temporary
git repo, a calendar full of awkward recurring meetings and the night the clocks go back.

---

<p align="center"><b>☎️ Better call <code>/hours</code>.</b><br><i>It's all good, man.</i></p>
