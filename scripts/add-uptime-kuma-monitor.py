#!/usr/bin/env python3
"""
Add OpenClaw to Uptime Kuma monitoring.
Run once to insert the monitor into kuma.db.
"""
import sqlite3
import sys
from datetime import datetime

DB = '/Users/davevoyles/docker-stack/uptime-kuma/data/kuma.db'
MONITOR_NAME = "OpenClaw (Health)"
MONITOR_URL = "http://192.168.1.93:8765/health"
MONITOR_METRICS_URL = "http://192.168.1.93:8765/metrics"

with sqlite3.connect(DB, timeout=10) as con:
    # Check schema
    cols = {c[1] for c in con.execute("PRAGMA table_info(monitor)").fetchall()}
    print(f"[+] Found {len(cols)} columns in monitor table")

    # Check if already exists
    existing = con.execute("SELECT id, name FROM monitor WHERE name=?", (MONITOR_NAME,)).fetchone()
    if existing:
        print(f"[!] Monitor already exists (id={existing[0]}), skipping insert.")
        sys.exit(0)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Build insert based on available columns
    base_fields = {
        "name": MONITOR_NAME,
        "type": "http",
        "url": MONITOR_URL,
        "interval": 60,
        "active": 1,
    }
    if "created_date" in cols:
        base_fields["created_date"] = now
    if "max_retries" in cols:
        base_fields["max_retries"] = 3
    elif "maxretries" in cols:
        base_fields["maxretries"] = 3
    if "accepted_statuscodes" in cols:
        base_fields["accepted_statuscodes"] = '["200-299"]'
    if "upside_down" in cols:
        base_fields["upside_down"] = 0
    if "ignore_tls" in cols:
        base_fields["ignore_tls"] = 0
    if "maxRedirects" in cols:
        base_fields["maxRedirects"] = 10
    if "dns_resolve_type" in cols:
        base_fields["dns_resolve_type"] = "A"
    if "dns_resolve_server" in cols:
        base_fields["dns_resolve_server"] = "1.1.1.1"
    if "weight" in cols:
        base_fields["weight"] = 2000

    placeholders = ",".join("?" * len(base_fields))
    col_names = ",".join(base_fields.keys())
    values = list(base_fields.values())

    result = con.execute(
        f"INSERT INTO monitor ({col_names}) VALUES ({placeholders})",
        values,
    )
    monitor_id = result.lastrowid
    con.commit()
    print(f"[+] Inserted monitor '{MONITOR_NAME}' with id={monitor_id}")
    print(f"    URL: {MONITOR_URL}")
    print(f"    Interval: 60s")

print("[+] Done. Restart Uptime Kuma or wait for its auto-reload to see the new monitor.")
