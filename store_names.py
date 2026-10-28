"""List Shopify admin store handles seen in ActivityWatch that match no sites.txt pattern,
with the store name taken from the admin tab title. Usage: store_names.py [YYYY-MM-DD]"""
import collections
import datetime as dt
import json
import pathlib
import re
import sys
import urllib.request

BASE = "http://localhost:5600/api/0"
HERE = pathlib.Path(__file__).parent


def patterns():
    path = HERE / "sites.txt"
    if not path.exists():
        path = HERE / "sites.example.txt"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            out.append(line.split("=", 1)[0].strip().lower())
    return out


def main():
    day = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today()
    start, end = day.isoformat(), (day + dt.timedelta(days=1)).isoformat()
    buckets = json.load(urllib.request.urlopen(f"{BASE}/buckets/"))
    known = patterns()
    names = collections.defaultdict(collections.Counter)
    seconds = collections.Counter()
    for bucket in buckets:
        if "web" not in bucket:
            continue
        url = f"{BASE}/buckets/{bucket}/events?start={start}T00:00:00&end={end}T00:00:00&limit=-1"
        for event in json.load(urllib.request.urlopen(url)):
            m = re.search(r"admin\.shopify\.com/store/([^/?#]+)", event["data"].get("url", ""))
            if not m:
                continue
            handle = m.group(1).lower()
            if any(p in handle for p in known):
                continue
            seconds[handle] += event["duration"]
            title = event["data"].get("title", "")
            name = re.split(r"\s+[··|–-]\s+", title)[0].strip()
            if name and not name.lower().startswith("shopify"):
                names[handle][name] += 1
    if not seconds:
        print("No unmatched Shopify stores.")
    for handle, secs in seconds.most_common():
        name = names[handle].most_common(1)[0][0] if names[handle] else "?"
        print(f"{handle}\t{name}\t{secs / 60:.0f} min")


if __name__ == "__main__":
    main()
