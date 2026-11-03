"""Report the night's backup: how many files changed and which, with a link to see the changes.

Usage: report.py <changes.txt> <retrieve folder>
changes.txt is `git diff --cached --name-status`; settings come from the environment (see backup.yml).

Always writes the report onto the run's page in GitHub (its summary). EMAIL also emails it to MAIL_TO, sent by
Salesforce itself through its Send Email action, so no mail server or mailbox is needed:
  off   no email (the default)
  self  from the backup user's own address
  org   from the organization-wide address in MAIL_FROM
"""
import datetime
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

PREFIX = "force-app/main/default/"
KIND = {"A": "added", "M": "changed", "D": "deleted", "R": "renamed", "C": "copied", "T": "changed"}
MAX_LISTED = 300
API = "v64.0"


def read_changes(path):
    rows = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                rows.append((KIND.get(parts[0][0], "changed"), parts[-1]))
    return rows


def friendly(path):
    """force-app/main/default/objects/Account/fields/Foo__c.field-meta.xml -> ('objects', 'Account / fields / Foo__c')"""
    if path.startswith("manifest/"):
        return "manifest (the list of everything)", path
    if not path.startswith(PREFIX):
        return "other", path
    parts = path[len(PREFIX):].split("/")
    parts[-1] = parts[-1].split(".")[0]
    # objects/Account/Account.object-meta.xml is the object itself: "Account", not "Account / Account".
    if len(parts) > 2 and parts[-1] == parts[-2]:
        parts.pop()
    return parts[0], " / ".join(parts[1:]) or parts[0]


def retrieve_warnings(folder):
    """Items Salesforce listed but didn't hand over, summed over the batches; and how many batches."""
    total, batches = 0, 0
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        try:
            result = json.load(open(os.path.join(folder, name), encoding="utf-8")).get("result", {})
        except (OSError, ValueError):
            continue
        messages = result.get("messages") or []
        total += len(messages if isinstance(messages, list) else [messages])
        batches += 1
    return total, batches


def cert_line(env, today=None):
    """One line when the sign-in certificate (CERT_EXPIRES, YYYY-MM-DD) expires within 30 days; else blank."""
    value = (env.get("CERT_EXPIRES") or "").strip()
    if not value:
        return ""
    try:
        expires = datetime.date.fromisoformat(value)
    except ValueError:
        return f"CERT_EXPIRES is {value!r}: it takes a date, YYYY-MM-DD."
    if expires - (today or datetime.date.today()) > datetime.timedelta(days=30):
        return ""
    return f"The sign-in certificate expires {expires.day} {expires:%b %Y}: make a new one (README)."


def build(env, rows, warnings, batches):
    """The report as (subject, html, markdown)."""
    subject, body, summary = build_report(env, rows, warnings, batches)
    cert = cert_line(env)
    if cert:
        body = f"<p><b>{html.escape(cert)}</b></p>{body}"
        summary = f"**{cert}**\n\n{summary}"
    return subject, body, summary


def build_report(env, rows, warnings, batches):
    org = env.get("ORG_LABEL") or "Salesforce"
    run_url, repo_url = env["RUN_URL"], env["REPO_URL"]
    deleted, had = int(env.get("DELETED") or 0), int(env.get("HAD") or 0)
    if env.get("JOB_STATUS") != "success":
        if env.get("BLOCKED") == "true":
            why = (f"Not committed: {deleted:,} of {had:,} files would have been deleted, over"
                   f" BLOCK_DELETED_PERCENT ({env.get('BLOCK_DELETED_PERCENT')}). Usually the backup user lost access.")
            return (f"{org} metadata backup BLOCKED: {deleted:,} files would be deleted",
                    f'<p>{html.escape(why)} <a href="{run_url}">The run</a>.</p>', f"**{why}**")
        return (f"{org} metadata backup FAILED",
                f'<p>The backup didn\'t finish. <a href="{run_url}">The run\'s log</a> says why.</p>',
                f"**The backup didn't finish.** The steps above say why.")
    n = len(rows)
    commit_url = f"{repo_url}/commit/{env.get('SHA', '')}"
    groups = defaultdict(list)
    for kind, path in rows:
        folder, name = friendly(path)
        groups[folder].append((kind, name))
    h, md = [], []
    for folder in sorted(groups):
        h.append(f'<h3 style="margin:16px 0 4px">{html.escape(folder)} ({len(groups[folder])})</h3><ul style="margin:0">')
        md.append(f"\n**{folder}** ({len(groups[folder])})\n")
        # A first backup, or a big release, would be thousands of lines: then counts per folder only.
        for kind, name in ([] if n > MAX_LISTED else sorted(groups[folder], key=lambda r: r[1])):
            h.append(f"<li><b>{kind}</b>: {html.escape(name)}</li>")
            md.append(f"- {kind}: `{name}`")
        h.append("</ul>")
    if n:
        lead = f"{n} file{'s' if n != 1 else ''} changed in {org}'s setup since the last backup."
        link_h = f'<p><a href="{commit_url}">See every change, line by line</a></p>'
        link_md = f"[See every change, line by line]({commit_url})"
    else:
        lead, link_h, link_md = f"Nothing changed in {org}'s setup since the last backup.", "", ""
    note = f"Pulled in {batches} batch{'es' if batches != 1 else ''}."
    if warnings:
        note += (f" {warnings} items listed but not handed over (normal: Salesforce's built-in items for features"
                 " that aren't turned on, and anything the backup user can't see, such as private dashboards).")
    kept = int(env.get("KEPT") or 0)
    if kept:
        note += (f" Kept {kept:,} item{'s' if kept != 1 else ''} the org listed but didn't hand over, as they were"
                 " (listed means not deleted).")
    subject = f"{org} metadata backup: {n} file{'s' if n != 1 else ''} changed"
    if env.get("ALERT") == "true":
        # Over ALERT_DELETED_PERCENT: say it first, since lost access looks exactly like this.
        subject = f"{org} metadata backup: WARNING, {deleted:,} files deleted"
        types = env.get("ALERT_TYPES") or ""
        by_type = f" By type: {types}." if types else ""
        lead = (f"WARNING: {deleted:,} of {had:,} files deleted.{by_type} If nobody removed that much, check the"
                f" backup user's access: once it's fixed, the next run puts them back. {lead}")
    body = (f"<p>{html.escape(lead)}</p>{link_h}{''.join(h)}"
            f'<p style="color:#666">{html.escape(note)}</p>'
            f'<p style="color:#666">The backup: <a href="{repo_url}">{repo_url}</a></p>')
    summary = "\n".join([f"**{lead}**", "", link_md] + md + ["", note])
    return subject, body, summary


def send_by_salesforce(env, mode, subject, body):
    """Salesforce's standard Send Email action, through the CLI's signed-in org (alias `org`): as the backup user
    (EMAIL=self), or from the organization-wide address in MAIL_FROM (EMAIL=org)."""
    inputs = {"emailAddresses": env["MAIL_TO"], "emailSubject": subject, "emailBody": body, "sendRichBody": True}
    if mode == "org":
        inputs.update(senderType="OrgWideEmailAddress", senderAddress=env["MAIL_FROM"])
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"inputs": [inputs]}, f)
    try:
        out = subprocess.run([shutil.which("sf") or "sf", "api", "request", "rest",
                              f"services/data/{API}/actions/standard/emailSimple", "--method", "POST",
                              "--body", "@" + f.name, "--target-org", env.get("SF_ALIAS") or "org"],
                             capture_output=True, text=True)
    finally:
        os.unlink(f.name)
    try:
        result = json.loads(out.stdout)[0]
    except (ValueError, IndexError, KeyError):
        sys.exit(f"Salesforce wouldn't send the email: {(out.stdout or out.stderr)[:500]}")
    if not result.get("isSuccess"):
        sys.exit(f"Salesforce wouldn't send the email: {result.get('errors') or result}")


def main():
    env = os.environ
    mode = (env.get("EMAIL") or "off").strip().lower()
    if mode not in ("off", "self", "org"):
        sys.exit(f"EMAIL is {mode!r}: it takes off, self or org")
    if mode != "off" and not env.get("MAIL_TO"):
        sys.exit(f"EMAIL is {mode} but MAIL_TO is blank: who should it go to?")
    if mode == "org" and not env.get("MAIL_FROM"):
        sys.exit("EMAIL is org but MAIL_FROM is blank: which organization-wide address sends it?")
    warnings, batches = retrieve_warnings(sys.argv[2])
    subject, body, summary = build(env, read_changes(sys.argv[1]), warnings, batches)
    body = f'<div style="font-family:Arial,sans-serif;font-size:14px">{body}</div>'
    if cert_line(env):
        print(f"::warning::{cert_line(env)}")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(f"## {subject}\n\n{summary}\n")
    if mode == "off":
        print(f"{subject} (EMAIL is off)")
        return
    if env.get("JOB_STATUS") != "success":
        print(f"{subject} (no email: GitHub emails you when a run fails)")
        return
    send_by_salesforce(env, mode, subject, body)
    print(f"Emailed {env['MAIL_TO']}: {subject}")


if __name__ == "__main__":
    main()
