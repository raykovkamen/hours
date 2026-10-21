# 🕴️ its-all-billable-man

### *Did you forget to track your hours? Did you "work" two hours and need to bill six?*
### **Better call `/hours`!**

---

> *"Hi. I'm your timesheet. Did you know that you have rights? The Constitution says you do. And so do I.
> I believe that until proven otherwise, every hour you spent near a keyboard is a billable hour."*

Clients asking where the day went? Project manager squinting at an empty ClickUp?
Spent the afternoon "researching" with a coffee in your hand?

**Don't panic. Don't confess. Don't touch that timer.**

One command. Zero memory required. Six hours, *minimum*. It's all good, man. ⚖️

---

## 💼 What we do for you

| Your problem | Our solution |
|---|---|
| You didn't track anything | We read **every** Claude Code session you had today, in every project. We remember so you don't have to. |
| You worked 2 hours | We round that up to a respectable **6h**. Quiet days are a *private matter*. |
| You worked 2 hours on 5 clients at once | Parallel sessions **share** the clock. We're creative, not criminal. |
| A 1-minute question about another store | That's a **0.5h consultation**, my friend. Every client gets a minimum. Ask any lawyer. |
| You left Claude running while you got a sandwich | Active time. Your AI was *working*. You were *supervising*. |
| You took a 2-hour lunch | We find your longest pause between 11:00 and 15:00 and call it a **1h break**. Nobody needs to know the rest. |
| You mentioned another client's URL by accident | The session moves to that client. Mistakes happen. Check `--all` before the subpoena arrives. |

---

## 📞 How to retain our services

In Claude Code:

```
/hours
/hours 2026-10-20      # for when the client asks about last Tuesday
```

Or, if you prefer to do things yourself (respect):

```
py -3 -I extract_sessions.py [YYYY-MM-DD] [--all]
```

You'll receive a clean, professional, court-ready table:

| Time | Hours | Site |
|---|---|---|
| 09:00–12:00 | 3.0 | Los Pollos Hermanos |
| 12:30–13:30 | 1.0 | Break |
| ... | ... | ... |
| **Total** | **6h** (+1h break) | |

*Look at that. Not a single hour unaccounted for. Beautiful.*

---

## 🧾 The fine print (read it, because I won't say it twice)

- **Active time** = gaps of 15 minutes or less between session events. Longer than that, you were "thinking."
- **The day** follows your real active time, rounded to 0.5h, kept between **6h and 8h**. Below 6? Never heard of it.
- **Every site** gets at least **0.5h**. Billing increments are a time-honored tradition.
- **Parallel sessions** split the clock between the sites active at that moment. Two sessions on the same client count once. We have *some* principles.
- **Sites** come from `sites.txt` (`pattern = Site`), matched against folder names **and** prompts. First match wins. Unknown store? You get a `?` and a stern talking-to.
- **Sessions that start with a timesheet request** don't count. We don't bill clients for *making the timesheet*. That would be unethical.

---

## 🗂️ Case files

| File | Role in the operation |
|---|---|
| `extract_sessions.py` | The muscle. Reads `~/.claude/projects/*/*.jsonl` and builds the table. |
| `sites.txt` | The client list. Copy `sites.example.txt` to start. Git-ignored, because *attorney–client privilege.* |
| `skill/SKILL.md` | The `/hours` skill. `~/.claude/skills/hours` is a junction to it. |

---

## ⚠️ Legal disclaimer

This is an **estimate** based on Claude Code activity, not a stopwatch. It doesn't see calls, browser QA,
or time spent staring out the window contemplating your life choices. If a client disputes an invoice,
run `--all`, look at where every minute went, and **adjust like an honest person would.**

No scheduled jobs, no cron, no hooks. This firm only works when you call.

---

<p align="center"><b>☎️ Better call <code>/hours</code>.</b><br><i>It's all good, man.</i></p>
