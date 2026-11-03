"""Count tonight's deletions against the backup, in all and per type; print step outputs for GitHub.

Usage: deletions.py <changes.txt> <alert percent, or off>
changes.txt is `git diff --cached --name-status`; the backup is what HEAD holds under force-app.

Prints deleted=, had= and, when it's over the alert percent, alert=true and alert_types= (a type being a folder under
force-app/main/default). A type counts when it had at least 10 files and lost more than the percent, or when it had
any files and lost them all. A first backup (HEAD holds nothing yet) never alerts.
"""
import subprocess
import sys
from collections import Counter

PREFIX = "force-app/main/default/"
changes, alert = sys.argv[1], sys.argv[2].strip().lower()

had_files = subprocess.run(["git", "ls-tree", "-r", "HEAD", "--name-only", "--", "force-app"],
                           capture_output=True, text=True).stdout.split("\n")
deleted_files = [line.split("\t", 1)[1] for line in open(changes, encoding="utf-8")
                 if line.startswith("D\t") and line.split("\t", 1)[1].strip().startswith("force-app/")]


def kind(path):
    return path[len(PREFIX):].split("/")[0] if path.startswith(PREFIX) else "other"


had = Counter(kind(f) for f in had_files if f.startswith("force-app/"))
gone = Counter(kind(f.strip()) for f in deleted_files)
total_had, total_gone = sum(had.values()), sum(gone.values())
print(f"deleted={total_gone}")
print(f"had={total_had}")

if alert.isdigit() and total_had:
    pct = int(alert)
    types = sorted(t for t, n in gone.items()
                   if n == had[t] or (had[t] >= 10 and n * 100 > had[t] * pct))
    if types or total_gone * 100 > total_had * pct:
        print("alert=true")
        print("alert_types=" + ", ".join(f"{t} ({gone[t]:,} of {had[t]:,})" for t in types))
