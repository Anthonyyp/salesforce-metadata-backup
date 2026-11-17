"""Pull each batch into force-app. An item that breaks its batch's pull is left out, and the batch pulled again.

Usage: pull.py <batches folder> <retrieve folder> <skipped file> <skip limit, or off>
Run from the repo, after split_manifest.py, with the CLI signed in as `org`.

Salesforce's answer to each pull goes to <retrieve folder>/batch-N.json. With --json a failed pull still exits 0,
and committing it would delete that batch's files, so each answer is checked.

One broken item fails its whole batch: a content asset whose file a sandbox didn't copy, for one ("unpackaged/
contentassets/Logo.asset-meta.xml: Expected source files for type 'ContentAsset'"). Nothing is written when that
happens. When the error names a file, and the file is one item of the batch, that item is left out and the batch
pulled again, up to the skip limit for the whole run. Each one goes in <skipped file> (type, name, batch, reason, tab
between) and on the run's page as a warning. Any other failure, or one more than the limit, stops the run.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from split_manifest import read, write  # noqa: E402

# The path in the zip Salesforce hands over: unpackaged/<folder>/<file>, up to ": " or the end.
ZIP_PATH = re.compile(r"unpackaged[\\/]+([^\\/:]+)[\\/]+(.+?)(?=: |$)")


def retrieve(batch, out):
    """Pull one batch; Salesforce's answer as JSON, and whether it succeeded."""
    with open(out, "w", encoding="utf-8") as f:
        code = subprocess.run([shutil.which("sf") or "sf", "project", "retrieve", "start", "--manifest", batch,
                               "--target-org", "org", "--ignore-conflicts", "--wait", "40", "--json"],
                              stdout=f).returncode
    try:
        answer = json.load(open(out, encoding="utf-8"))
    except ValueError:
        return None, False
    return answer, code == 0 and (answer.get("result") or {}).get("status") == "Succeeded"


def error_text(answer):
    result = (answer or {}).get("result") or {}
    return " ".join(str(m) for m in ((answer or {}).get("message"), result.get("errorMessage")) if m)


def folder_types():
    """Each folder's types (lwc: LightningComponentBundle), from the CLI's own list in SF_CLI_MODULES; or none."""
    base = os.environ.get("SF_CLI_MODULES") or ""
    sdr = os.path.join("@salesforce", "source-deploy-retrieve", "lib", "src", "registry", "metadataRegistry.json")
    for path in (os.path.join(base, sdr), os.path.join(base, "@salesforce", "cli", "node_modules", sdr)):
        if base and os.path.exists(path):
            folders = {}
            for t in json.load(open(path, encoding="utf-8"))["types"].values():
                folders.setdefault(t.get("directoryName"), set()).add(t["name"])
            return folders
    return {}


def match(members, names):
    return {(t, m) for t, m in members if m in names}


def broken_item(text, members, folders=None):
    """The one (type, name) of the batch that the error's file belongs to; None if it names none, or several."""
    found = set()
    for folder, rest in ZIP_PATH.findall(text):
        items, bundles = set(), set()
        # Names can have spaces, so the path may end at any of them ("classes/Foo.cls is forceignored").
        for path in [rest] + [rest[:i] for i, c in enumerate(rest) if c == " "]:
            parts = re.split(r"[\\/]+", path)
            last = parts[-1][:-len("-meta.xml")] if parts[-1].endswith("-meta.xml") else parts[-1]
            # A file is its item (classes/Foo.cls), in a folder (reports/Sales/Foo.report) or a bundle (lwc/a/a.js).
            # A document's name keeps its extension (documents/Logos/logo.png).
            items |= {"/".join(parts[:-1] + [n]) for n in (last.rsplit(".", 1)[0], last)}
            bundles |= {parts[0]} if len(parts) > 1 else set()
        hits = (match(members, items | {unquote(n) for n in items})
                or match(members, bundles | {unquote(n) for n in bundles}))
        if len({t for t, _ in hits}) > 1:
            # The same name in more than one type: the one whose folder this is, or whose name the error gives.
            hits = ({(t, m) for t, m in hits if t in (folders or {}).get(folder, ())}
                    or {(t, m) for t, m in hits if folder.lower().startswith(t.lower())}
                    or {(t, m) for t, m in hits if re.search(rf"\b{t}\b", text)})
        found |= hits
    return found.pop() if len(found) == 1 else None


def failed(out, answer):
    print("The pull failed:")
    if answer is None:
        print(open(out, encoding="utf-8", errors="replace").read())
        return
    result = answer.get("result") or {}
    print(json.dumps({"message": answer.get("message"), "status": result.get("status"),
                      "error": result.get("errorMessage"), "messages": result.get("messages")}, indent=2))


def main():
    batches, folder, skipped_file, limit = sys.argv[1:5]
    limit = 0 if limit.strip().lower() == "off" else int(limit)
    os.makedirs(folder, exist_ok=True)
    open(skipped_file, "w").close()
    skipped, folders = 0, folder_types()
    names = sorted((n for n in os.listdir(batches) if re.fullmatch(r"batch-\d+\.xml", n)),
                   key=lambda n: int(re.sub(r"\D", "", n)))
    for name in names:
        batch = os.path.join(batches, name)
        out = os.path.join(folder, name[:-4] + ".json")
        print(f"Pulling {name}")
        while True:
            answer, ok = retrieve(batch, out)
            if ok:
                break
            members, version = read(batch)
            text = error_text(answer)
            item = broken_item(text, members, folders)
            if item is None or skipped >= limit:
                if item is not None:
                    print(f"{item[0]} {item[1]} broke the pull, and {skipped} items are already left out"
                          f" (SKIP_BROKEN is {limit}).")
                failed(out, answer)
                sys.exit(1)
            skipped += 1
            reason = " ".join(text.split())
            with open(skipped_file, "a", encoding="utf-8") as f:
                f.write(f"{item[0]}\t{item[1]}\t{name[:-4]}\t{reason}\n")
            print(f"::warning::Left out {item[0]} {item[1]}: it broke the pull of {name[:-4]}. {reason}")
            rest = [c for c in members if c != item]
            if not rest:
                os.remove(out)
                break
            write(batch, rest, version)
            print(f"Pulling {name} again without it")


if __name__ == "__main__":
    main()
