#!/usr/bin/env python3
"""Print recent event source counts from the local ledger API."""
import json
import urllib.request
from collections import Counter

data = json.loads(urllib.request.urlopen("http://localhost:5000/api/events", timeout=10).read())
events = data.get("events") or []
counts = Counter(e.get("source") for e in events[:50])
print("last50_sources", dict(counts))
for e in events[:15]:
    print(e.get("source"), e.get("title", "")[:55], e.get("timestamp", "")[-8:])
