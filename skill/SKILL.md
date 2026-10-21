---
name: hours
description: Daily timesheet — today's (or a given day's) hours per site from all Claude Code sessions. Use when the user asks for today's hours / time for today's tasks, or runs /hours [YYYY-MM-DD].
---

1. Run `py -3 -I D:\MIGRATION\timesheet\extract_sessions.py $ARGUMENTS` (the date argument is optional, YYYY-MM-DD). It reads ALL Claude Code sessions of that day across every project folder, not just this conversation.
   - It ends with a ready-made table from 09:00 with a 1h Break row, in 0.5h steps. The break is placed at the longest real pause (30+ min) between 11:00 and 15:00, or at 12:00 if there was none. The day's total follows real active time, rounded to 0.5h, within 6–8h.
   - Time is split by active time per site, with at least 0.5h per site, in the order the work started.
   - A prompt that names a site moves that session's time to the site from then on. The `[Site]` tags in the listing show where this happened.
   - Prompts are listed only for sessions with an unnamed (`?`) site. If the user questions the split, re-run with `--all` to see every session.
2. Reply with only that table. Change it only to name the sites marked `?`: name them from the session's prompts listed above the table. A `? one-off:` row is a quick question about another site, so name that site. If the prompt doesn't name a store, write `Other store (question)`; never invent a site from the topic (e.g. "Magento migration" is not a site). Site rules live in `D:\MIGRATION\timesheet\sites.txt`. If you named a site from the prompts and it will come up again, offer to add a line for it there.

Never set this up as a scheduled task, cron or hook.
