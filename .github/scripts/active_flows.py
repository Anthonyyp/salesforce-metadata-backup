"""Put each flow's running version in the backup, where the pull put its newest.

Usage: active_flows.py <work folder> <list file>
Run from the repo after pull.py, with the CLI signed in as `org`.

Pulling a flow hands over its newest version, running or not: save a new version without activating it and the
backup holds the draft, not what the org runs. This asks the org which flows run an older version than their newest,
pulls that version by number (Name-3), and puts it in the flow's own file. Flows that run nothing keep their newest.

<list file> gets one line a flow, tab between: name, the running version, the newest; the report lists them. If
anything here fails, the newest versions stay, the run carries on, and the list holds one line: "error", tab, why.
"""
import json
import os
import shutil
import subprocess
import sys

FLOWS = os.path.join("force-app", "main", "default", "flows")
# Where the running versions are pulled to: the CLI only writes inside the project. Deleted before the commit.
PULLED = ".running-flows"
SF = shutil.which("sf") or "sf"
ORG = os.environ.get("SF_TARGET_ORG") or "org"  # the workflow signs in as `org`; set it to try this elsewhere


def query(soql):
    """The records of one SOQL query, as the backup user."""
    out = subprocess.run([SF, "data", "query", "--target-org", ORG, "--query", soql, "--json"],
                         capture_output=True, text=True)
    answer = json.loads(out.stdout or "{}")
    if out.returncode != 0 or answer.get("status") != 0:
        raise RuntimeError(answer.get("message") or out.stderr.strip() or "the query failed")
    return answer["result"]["records"]


def behind(definitions, have):
    """Flows whose running version isn't the newest, and that the backup has (not a package's, not left out)."""
    return {d["ApiName"]: d for d in definitions
            if not d.get("NamespacePrefix") and d.get("ActiveVersionId")
            and d["ActiveVersionId"] != d.get("LatestVersionId") and d["ApiName"] in have}


def manifest(versions, api_version):
    members = "".join(f"<members>{name}-{n}</members>" for name, n in sorted(versions.items()))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<Package xmlns="http://soap.sforce.com/2006/04/metadata">'
            f"<types>{members}<name>Flow</name></types><version>{api_version}</version></Package>\n")


def put_in_place(pulled, versions, flows=FLOWS):
    """Each pulled Name-N file (anywhere under pulled) over Name's own file. Returns the names put in place."""
    files = {f: os.path.join(d, f) for d, _, fs in os.walk(pulled) for f in fs}
    done = []
    for name, n in versions.items():
        src = files.get(f"{name}-{n}.flow-meta.xml")
        if src:
            shutil.copyfile(src, os.path.join(flows, f"{name}.flow-meta.xml"))
            done.append(name)
    return done


def api_version(path=os.path.join("manifest", "package.xml")):
    """The API version the night's pull used, from its manifest."""
    try:
        text = open(path, encoding="utf-8").read()
        return text.split("<version>")[1].split("</version>")[0].strip()
    except (OSError, IndexError):
        return "64.0"


def main():
    work, listing = sys.argv[1], sys.argv[2]
    have = {f[:-len(".flow-meta.xml")] for f in os.listdir(FLOWS)} if os.path.isdir(FLOWS) else set()
    lines = []
    try:
        late = behind(query("SELECT ApiName, NamespacePrefix, ActiveVersionId, LatestVersionId"
                            " FROM FlowDefinitionView WHERE IsActive = true"), have)
        if late:
            ids = [d["ActiveVersionId"] for d in late.values()] + [d["LatestVersionId"] for d in late.values()]
            number = {}
            for i in range(0, len(ids), 200):
                quoted = ",".join(f"'{x}'" for x in ids[i:i + 200])
                for v in query(f"SELECT DurableId, VersionNumber FROM FlowVersionView WHERE DurableId IN ({quoted})"):
                    number[v["DurableId"]] = v["VersionNumber"]
            running = {name: number[d["ActiveVersionId"]] for name, d in late.items()}
            os.makedirs(work, exist_ok=True)
            package = os.path.join(work, "package.xml")
            with open(package, "w", encoding="utf-8") as f:
                f.write(manifest(running, api_version()))
            out = subprocess.run([SF, "project", "retrieve", "start", "--manifest", package, "--target-org", ORG,
                                  "--output-dir", PULLED, "--wait", "20", "--json"], capture_output=True, text=True)
            answer = json.loads(out.stdout or "{}")
            if out.returncode != 0 or (answer.get("result") or {}).get("status") != "Succeeded":
                raise RuntimeError(answer.get("message") or "the pull of the running versions failed")
            done = set(put_in_place(PULLED, running))
            missing = sorted(set(running) - done)
            if missing:
                raise RuntimeError(f"no running version handed over for {', '.join(missing)}")
            for name, d in sorted(late.items()):
                lines.append(f"{name}\t{running[name]}\t{number.get(d['LatestVersionId'], '?')}")
            k = len(lines)
            print(f"{k} flow{'s run' if k != 1 else ' runs'} an older version than {'their' if k != 1 else 'its'}"
                  " newest; the backup holds the running one.")
        else:
            print("Every flow runs its newest version.")
    except Exception as e:  # The newest versions are still there: say so, don't stop the night.
        lines = [f"error\t{e}"]
        print(f"::warning::Couldn't put every running flow version in place ({e}). Those flows hold their newest"
              " version tonight.")
    finally:  # Never committed: it's inside the repo only because the CLI writes nowhere else.
        shutil.rmtree(PULLED, ignore_errors=True)
    with open(listing, "w", encoding="utf-8") as f:
        f.write("".join(line + "\n" for line in lines))


if __name__ == "__main__":
    main()
