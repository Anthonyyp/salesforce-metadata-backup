"""Split the org's manifest into batches small enough for one pull each.

Usage: split_manifest.py <package.xml> <out dir> <limit>
Salesforce hands over at most 10,000 files in one pull. But a profile or permission set only comes back with the
permissions for what's pulled with it, so everything except a few standalone types stays together in batch 1.
Only the standalone types (reports, dashboards, email templates, documents), which no profile or permission set
lists, are split off. Each component is weighed by the files it usually makes (a class is two, a Lightning
component a bundle of them). Writes batch-1.xml, batch-2.xml... and prints how many.
"""
import os
import sys
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
WEIGHT = {
    "ApexClass": 2, "ApexTrigger": 2, "ApexPage": 2, "ApexComponent": 2, "StaticResource": 2,
    "LightningComponentBundle": 10, "AuraDefinitionBundle": 10, "ExperienceBundle": 50, "WaveTemplateBundle": 20,
}
STANDALONE = {"Report", "Dashboard", "EmailTemplate", "Document", "Letterhead"}  # not ReportType: translations carry its labels


def read(path):
    root = ET.parse(path).getroot()
    version = root.findtext(f"{{{NS}}}version")
    members = []
    for t in root.findall(f"{{{NS}}}types"):
        name = t.findtext(f"{{{NS}}}name")
        members += [(name, m.text) for m in t.findall(f"{{{NS}}}members")]
    return members, version


def write(path, members, version):
    by_type = {}
    for t, m in members:
        by_type.setdefault(t, []).append(m)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<Package xmlns="{NS}">']
    for t in sorted(by_type):
        lines.append("    <types>")
        lines += [f"        <members>{escape(m)}</members>" for m in by_type[t]]
        lines += [f"        <name>{t}</name>", "    </types>"]
    lines += [f"    <version>{version}</version>", "</Package>", ""]
    open(path, "w", encoding="utf-8").write("\n".join(lines))


def escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    src, out, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
    members, version = read(src)
    core = [c for c in members if c[0] not in STANDALONE]
    batches, current = [], core
    weight = sum(WEIGHT.get(t, 1) for t, _ in core)
    if weight > limit:
        print(f"Warning: batch 1 is {weight}, over {limit}, and can't be split without emptying the profiles")
    for t, m in (c for c in members if c[0] in STANDALONE):
        w = WEIGHT.get(t, 1)
        if current and weight + w > limit:
            batches.append(current)
            current, weight = [], 0
        current.append((t, m))
        weight += w
    if current:
        batches.append(current)
    os.makedirs(out, exist_ok=True)
    for i, b in enumerate(batches, 1):
        write(os.path.join(out, f"batch-{i}.xml"), b, version)
    print(f"{len(members)} components in {len(batches)} batch{'es' if len(batches) != 1 else ''}")


if __name__ == "__main__":
    main()
