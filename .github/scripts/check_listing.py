"""Fail if listing the org hit an error for any metadata type.

Usage: check_listing.py <since, in ms since the epoch>
Run after `sf project generate manifest --from-org` with SF_LOG_LEVEL=debug.

That command lists the org one type at a time and, when a listing fails, leaves the type out of the manifest
without saying so: the error goes only to the CLI's debug log (~/.sf/sf-*.log). Left alone, the pull then succeeds
without that type and the commit records all of it as deleted. This reads the log and stops the run instead.

Two kinds of error are normal and ignored: INVALID_TYPE (a type this org or API version doesn't have) and the
StandardValueSet lookups the CLI tries by name. If the log has no listing lines at all, the CLI's logging has
changed, and the run stops rather than trust a check it can't make.
"""
import glob
import json
import os
import re
import sys

since = int(sys.argv[1]) if len(sys.argv) > 1 else 0
listed, errors = set(), []
for path in sorted(glob.glob(os.path.expanduser("~/.sf/sf-*.log"))):
    for line in open(path, encoding="utf-8", errors="replace"):
        if "ConnectionResolver" not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("time", 0) < since or not str(entry.get("name", "")).endswith("ConnectionResolver"):
            continue
        msg = str(entry.get("msg", ""))
        m = re.match(r"listMetadata for \{ type: '([^']+)'", msg)
        if m:
            listed.add(m.group(1))
            continue
        m = re.match(r"\[([^\]]+)\] (.*)", msg, re.S)
        if m:
            errors.append((m.group(1), m.group(2).strip()))

if not listed:
    sys.exit("Couldn't check the listing: the CLI's log has no listing lines (has the CLI changed?). Nothing pulled.")
# Only errors against a type that was listed count; StandardValueSet lookups are logged by value set name.
real = [(t, e) for t, e in errors if t in listed and not e.startswith("INVALID_TYPE")]
if real:
    print(f"Listing the org failed for {len(real)} type(s), so they'd be missing from tonight's backup:")
    for t, e in real:
        print(f"  {t}: {e[:300]}")
    sys.exit(1)
print(f"Listed {len(listed)} types without errors.")
