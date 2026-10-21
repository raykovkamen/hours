"""Summarise today's Claude Code sessions across all projects (local time) for the daily timesheet,
then spread them over a 6-8h day from 09:00 (12:00-13:00 break) by active time per site.

    py -3 -I extract_sessions.py [YYYY-MM-DD] [--all]

Prompts are listed only for sessions with a site marked ?; --all lists every session.
"""
import json, re, sys, glob, os
from collections import Counter
from datetime import datetime, date, timedelta

sys.stdout.reconfigure(encoding='utf-8')  # prompts contain Cyrillic; the Windows console default (cp1252) crashes on it

day = next((date.fromisoformat(a) for a in sys.argv[1:] if re.fullmatch(r'\d{4}-\d{2}-\d{2}', a)), date.today())
SHOW_ALL = '--all' in sys.argv[1:]
root = os.path.join(os.path.expanduser('~'), '.claude', 'projects')
IDLE = timedelta(minutes=15)  # gaps longer than this are not counted as active time
MIN_SLOTS, MAX_SLOTS = 12, 16  # the day is 6-8h in 0.5h steps, following real active time
TIMESHEET = ("today's hours", "hours for", "/hours")  # asking for the timesheet is not work on a site
DAY_START = datetime.combine(day, datetime.min.time()).replace(hour=9)
BREAK_START, BREAK_END = DAY_START.replace(hour=12), DAY_START.replace(hour=13)  # lunch default, moved to the real pause below

rules = []  # (pattern, site) from sites.txt (or the example until there is one), first match wins
here = os.path.dirname(os.path.abspath(__file__))
sites_file = os.path.join(here, 'sites.txt')
for line in open(sites_file if os.path.exists(sites_file) else os.path.join(here, 'sites.example.txt'), encoding='utf-8'):
    if '=' in line and not line.lstrip().startswith('#'):
        pattern, site = line.split('=', 1)
        rules.append((pattern.strip().lower(), site.strip()))

def match(text):
    text = text.lower()
    return next((site for pattern, site in rules if pattern in text), None)

out = []
sites = {}  # site -> [first start, active time], filled per prompt below
spans = []  # (start, end, site) of every active gap, across all sessions
for path in glob.glob(os.path.join(root, '*', '*.jsonl')):
    folder = os.path.basename(os.path.dirname(path))
    if folder.lower().endswith('timesheet'):
        continue
    if datetime.fromtimestamp(os.path.getmtime(path)).date() < day:
        continue
    stamps, prompts, cwd, first = [], [], None, None
    for line in open(path, encoding='utf-8', errors='replace'):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get('timestamp')
        if not t:
            continue
        local = datetime.fromisoformat(t.replace('Z', '+00:00')).astimezone()
        if local.date() != day:
            continue
        stamps.append(local)
        cwd = cwd or d.get('cwd')  # first cwd = where the work started
        if d.get('type') == 'user':
            c = d.get('message', {}).get('content')
            if first is None and isinstance(c, str) and c.strip():
                first = c.lower()
            if isinstance(c, str) and c.strip() and not c.startswith('<') and not any(k in c.lower() for k in TIMESHEET):
                prompts.append((local, c.strip()))
    if not prompts:
        continue
    stamps.sort()
    prompts.sort(key=lambda p: p[0])
    active = sum(((b - a) for a, b in zip(stamps, stamps[1:]) if b - a <= IDLE), timedelta())
    # A session opened to ask for the timesheet is about the timesheet tool, not client work: leave it out.
    if first is not None and any(k in first for k in TIMESHEET):
        continue
    one_off = len(prompts) == 1 and active < timedelta(minutes=5)

    # Site per prompt: a prompt naming a site switches the session to it until another one does;
    # before that the session counts for its folder's site. A single quick question is not work on
    # the folder's site, so it only gets a site its own text names.
    base = None if one_off else match(folder) or f"? {os.path.basename(cwd or folder)}"
    switches, current = [], base
    for t, text in prompts:
        current = match(text) or current
        switches.append((t, current or f"? one-off: {text[:64]}".replace('\n', ' ')))
    for a, b in zip(stamps, stamps[1:]):  # each active gap counts for the site of the latest prompt before it
        site = next((s for t, s in reversed(switches) if t <= a), switches[0][1])
        entry = sites.setdefault(site, [a, timedelta()])
        entry[0] = min(entry[0], a)
        if b - a <= IDLE:
            spans.append((a, b, site))
    out.append((stamps[0], folder, cwd, stamps[-1], active, prompts, switches))

# Wall-clock time is shared between the sites active at that moment, so parallel sessions
# (several VS Code windows, or several sessions in one) are not counted twice; parallel
# sessions on the same site count once.
events = sorted([(a, 1, s) for a, b, s in spans] + [(b, -1, s) for a, b, s in spans], key=lambda e: (e[0], e[1]))
running, prev, pauses = Counter(), None, []
for t, step, site in events:
    live = [s for s, n in running.items() if n > 0]
    if prev and live and t > prev:
        for s in live:
            sites[s][1] += (t - prev) / len(live)
    elif prev and not live and t > prev:
        pauses.append((prev.replace(tzinfo=None), t.replace(tzinfo=None)))  # no session active
    running[site] += step
    prev = t

# Lunch: the longest pause of 30+ min that falls within 11:00-15:00, starting on the nearest
# half hour; without one it stays at 12:00. It is always shown as 1h.
window = (DAY_START.replace(hour=11), DAY_START.replace(hour=15))
lunch = max(((min(b, window[1]) - max(a, window[0]), max(a, window[0])) for a, b in pauses), default=None)
if lunch and lunch[0] >= timedelta(minutes=30):
    start = lunch[1] + timedelta(minutes=15)
    BREAK_START = min(start.replace(minute=30 if start.minute >= 30 else 0, second=0, microsecond=0), window[1] - timedelta(hours=1))
    BREAK_END = BREAK_START + timedelta(hours=1)

out.sort(key=lambda o: o[0])
print(f"Claude Code sessions on {day.isoformat()} (local time)\n")
for start, folder, cwd, end, active, prompts, switches in out:
    if not SHOW_ALL and not any(s.startswith('?') for _, s in switches):
        continue  # only sessions with an unnamed site need their prompts read
    print(f"## project folder: {folder} | cwd: {cwd}")
    print(f"   {start:%H:%M}-{end:%H:%M}, active ~{int(active.total_seconds() // 60)} min")
    shown = None
    for i, ((t, text), (_, site)) in enumerate(zip(prompts, switches)):
        if site != shown or i < 12:  # past the first 12 prompts, only show where the site changes
            tag = f"[{site}] " if site != shown else ""
            print(f"   - {t:%H:%M} {tag}{text[:200]}".replace('\n', ' '))
            shown = site
    print()
if not out:
    print("No sessions found.")
    sys.exit()

# Day length: real active time (after the parallel-session split) rounded to 0.5h, kept within 6-8h.
active_total = sum(v[1].total_seconds() for v in sites.values())
SLOTS = min(MAX_SLOTS, max(MIN_SLOTS, round(active_total / 1800)))

# Sites in the order work on them started.
order = sorted(sites, key=lambda k: sites[k][0])[:SLOTS]

# Every site gets at least one 0.5h slot; the rest is split by active time (largest remainder).
units = {k: 1 for k in order}
free = SLOTS - len(order)
total = sum(sites[k][1].total_seconds() for k in order) or 1
share = {k: free * sites[k][1].total_seconds() / total for k in order}
for k in order:
    units[k] += int(share[k])
for k in sorted(order, key=lambda k: share[k] - int(share[k]), reverse=True)[:SLOTS - sum(units.values())]:
    units[k] += 1

print("Timesheet (sites starting with ? need a name):\n")
print("| Time | Hours | Site |\n|---|---|---|")
t = DAY_START
for k in order:
    end = t + timedelta(minutes=30 * units[k])
    if t < BREAK_START < end:  # a site running into the lunch break continues after it
        print(f"| {t:%H:%M}–{BREAK_START:%H:%M} | {(BREAK_START - t).total_seconds() / 3600:.1f} | {k} |")
        t = BREAK_START
    if t == BREAK_START:
        print(f"| {BREAK_START:%H:%M}–{BREAK_END:%H:%M} | 1.0 | Break |")
        t, end = BREAK_END, end + (BREAK_END - BREAK_START)
    print(f"| {t:%H:%M}–{end:%H:%M} | {(end - t).total_seconds() / 3600:.1f} | {k} |")
    t = end
print(f"| **Total** | **{SLOTS / 2:g}h** (+1h break) | |")
print(f"\n(real active time {active_total / 3600:.1f}h, day kept within {MIN_SLOTS / 2:g}-{MAX_SLOTS / 2:g}h)")
