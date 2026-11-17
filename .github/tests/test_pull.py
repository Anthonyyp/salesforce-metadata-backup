"""Tests for leaving out an item that breaks a pull. Not part of the backup; nothing here runs at night.

Run from the repo: python3 -m unittest discover .github/tests
fixtures/ holds the CLI's answers, as `sf project retrieve start --json` gives them: a pull broken by a content
asset without its file (a real one, renamed), one broken by a forceignored class, a success, an expired sign-in and
a failure on Salesforce's side. The keep test needs the Salesforce CLI's modules (SF_CLI_MODULES, as in the
workflow); without them it's skipped.
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
import pull  # noqa: E402
import report  # noqa: E402
from split_manifest import read, write  # noqa: E402


def fixture(name):
    return json.load(open(os.path.join(HERE, "fixtures", name), encoding="utf-8"))


class BrokenItem(unittest.TestCase):
    def test_content_asset(self):
        members = [("ContentAsset", "Banner"), ("ContentAsset", "Logo"), ("StaticResource", "Logo")]
        text = pull.error_text(fixture("broken-content-asset.json"))
        self.assertEqual(pull.broken_item(text, members), ("ContentAsset", "Logo"))

    def test_forceignored_class(self):
        members = [("ApexClass", "Foo"), ("ApexClass", "FooTest"), ("CustomObject", "Foo__c")]
        text = pull.error_text(fixture("forceignored-class.json"))
        self.assertEqual(pull.broken_item(text, members), ("ApexClass", "Foo"))

    def test_names_with_spaces_and_folders(self):
        members = [("Layout", "Account-Account Layout"), ("Report", "Sales"), ("Report", "Sales/Pipeline")]
        self.assertEqual(pull.broken_item("unpackaged/layouts/Account-Account Layout.layout: bad", members),
                         ("Layout", "Account-Account Layout"))
        self.assertEqual(pull.broken_item("unpackaged/reports/Sales/Pipeline.report: bad", members),
                         ("Report", "Sales/Pipeline"))
        documents = [("Document", "Logos"), ("Document", "Logos/logo.png")]
        self.assertEqual(pull.broken_item("unpackaged/documents/Logos/logo.png-meta.xml: bad", documents),
                         ("Document", "Logos/logo.png"))
        # The same name in two types, told apart by the CLI's list of folders; without it, by nothing: no guess.
        bundles = [("LightningComponentBundle", "card"), ("ApexClass", "card")]
        folders = {"lwc": {"LightningComponentBundle"}, "classes": {"ApexClass"}}
        self.assertEqual(pull.broken_item("unpackaged/lwc/card/card.js: bad", bundles, folders),
                         ("LightningComponentBundle", "card"))
        self.assertIsNone(pull.broken_item("unpackaged/lwc/card/card.js: bad", bundles))

    @unittest.skipUnless(os.environ.get("SF_CLI_MODULES"), "needs SF_CLI_MODULES")
    def test_cli_folders(self):
        folders = pull.folder_types()
        self.assertIn("ContentAsset", folders["contentassets"])
        self.assertIn("LightningComponentBundle", folders["lwc"])

    def test_not_in_the_batch(self):
        text = pull.error_text(fixture("broken-content-asset.json"))
        self.assertIsNone(pull.broken_item(text, [("ContentAsset", "Banner")]))

    def test_no_item_named(self):
        for name in ("auth-failure.json", "server-failure.json"):
            self.assertIsNone(pull.broken_item(pull.error_text(fixture(name)), [("ApexClass", "Foo")]), name)


class Pull(unittest.TestCase):
    """pull.main against a fake CLI: each batch's answer depends on what's still in it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.batches = os.path.join(self.dir, "batches")
        self.retrieve = os.path.join(self.dir, "retrieve")
        self.skipped = os.path.join(self.dir, "skipped.txt")
        os.makedirs(self.batches)
        write(os.path.join(self.batches, "batch-1.xml"), [("ApexClass", "Foo"), ("ApexClass", "Bar")], "66.0")
        write(os.path.join(self.batches, "batch-2.xml"),
              [("ContentAsset", "Logo"), ("ContentAsset", "Banner"), ("StaticResource", "Logo")], "66.0")
        self.calls = []
        self.real = pull.retrieve
        pull.retrieve = self.fake

    def tearDown(self):
        pull.retrieve = self.real
        shutil.rmtree(self.dir)

    broken = {("ContentAsset", "Logo"): "broken-content-asset.json", ("ApexClass", "Foo"): "forceignored-class.json"}
    always = None  # a fixture every pull answers with

    def fake(self, batch, out):
        members, _ = read(batch)
        self.calls.append((os.path.basename(batch), len(members)))
        name = self.always or next((f for item, f in self.broken.items() if item in members), "succeeded.json")
        answer = fixture(name)
        json.dump(answer, open(out, "w", encoding="utf-8"))
        return answer, answer.get("result", {}).get("status") == "Succeeded" and "exitCode" not in answer

    def run_main(self, limit):
        sys.argv = ["pull.py", self.batches, self.retrieve, self.skipped, limit]
        with redirect_stdout(io.StringIO()) as out:
            try:
                pull.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def skipped_rows(self):
        return [line.split("\t") for line in open(self.skipped, encoding="utf-8").read().splitlines()]

    def test_success(self):
        self.broken = {}
        code, _ = self.run_main("5")
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [("batch-1.xml", 2), ("batch-2.xml", 3)])
        self.assertEqual(self.skipped_rows(), [])

    def test_leaves_out_what_breaks(self):
        code, out = self.run_main("5")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.calls, [("batch-1.xml", 2), ("batch-1.xml", 1), ("batch-2.xml", 3), ("batch-2.xml", 2)])
        rows = self.skipped_rows()
        self.assertEqual([r[:3] for r in rows], [["ApexClass", "Foo", "batch-1"], ["ContentAsset", "Logo", "batch-2"]])
        self.assertIn("Expected source files for type 'ContentAsset'", rows[1][3])
        self.assertIn("::warning::Left out ContentAsset Logo", out)
        # The static resource of the same name stays in.
        self.assertEqual(read(os.path.join(self.batches, "batch-2.xml"))[0],
                         [("ContentAsset", "Banner"), ("StaticResource", "Logo")])
        self.assertEqual(sorted(os.listdir(self.retrieve)), ["batch-1.json", "batch-2.json"])
        self.assertEqual(fixture("succeeded.json"), json.load(open(os.path.join(self.retrieve, "batch-2.json"))))

    def test_limit(self):
        code, out = self.run_main("1")
        self.assertEqual(code, 1)
        self.assertEqual([r[:2] for r in self.skipped_rows()], [["ApexClass", "Foo"]])
        self.assertIn("ContentAsset Logo broke the pull, and 1 items are already left out", out)
        self.assertIn("The pull failed:", out)

    def test_off(self):
        code, out = self.run_main("off")
        self.assertEqual(code, 1)
        self.assertEqual(self.calls, [("batch-1.xml", 2)])
        self.assertEqual(self.skipped_rows(), [])

    def test_a_bad_setting(self):
        code, _ = self.run_main("five")
        self.assertEqual(code, "SKIP_BROKEN is 'five': it takes a number, or off")
        self.assertEqual(self.calls, [])

    def test_failures_not_from_an_item_stop_the_run(self):
        for name in ("auth-failure.json", "server-failure.json"):
            self.calls, self.always = [], name
            code, out = self.run_main("5")
            self.assertEqual(code, 1, name)
            self.assertEqual(self.calls, [("batch-1.xml", 2)], name)
            self.assertEqual(self.skipped_rows(), [], name)
            self.assertIn("The pull failed:", out)

    def test_a_batch_of_one_broken_item(self):
        write(os.path.join(self.batches, "batch-1.xml"), [("ApexClass", "Foo")], "66.0")
        os.remove(os.path.join(self.batches, "batch-2.xml"))
        code, _ = self.run_main("5")
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.retrieve), [])


class Report(unittest.TestCase):
    env = {"RUN_URL": "https://github.com/o/r/actions/runs/1", "REPO_URL": "https://github.com/o/r",
           "JOB_STATUS": "success", "SHA": "abc", "ORG_LABEL": "Acme"}

    def test_skipped(self):
        skipped = [("ContentAsset", "Logo", "Metadata API request failed: ... Expected source files")]
        subject, body, summary = report.build(self.env, [], 0, 2, skipped)
        self.assertEqual(subject, "Acme metadata backup: WARNING, 1 item left out")
        self.assertIn("ContentAsset <b>Logo</b>: Metadata API request failed", body)
        self.assertIn("- ContentAsset `Logo`: Metadata API request failed", summary)

    def test_nothing_skipped(self):
        subject, body, _ = report.build(self.env, [], 0, 2, [])
        self.assertEqual(subject, "Acme metadata backup: 0 files changed")
        self.assertNotIn("left out", body)

    def test_read_skipped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("ContentAsset\tLogo\tbatch-4\tExpected source files\n")
        try:
            self.assertEqual(report.read_skipped(f.name), [("ContentAsset", "Logo", "Expected source files")])
        finally:
            os.unlink(f.name)
        self.assertEqual(report.read_skipped(None), [])


@unittest.skipUnless(os.environ.get("SF_CLI_MODULES") and shutil.which("node") and shutil.which("git"),
                     "needs SF_CLI_MODULES (the Salesforce CLI's node_modules), node and git")
class Keep(unittest.TestCase):
    """keep_undelivered.js puts back the last backup's files for an item left out."""

    def test_keeps_left_out_items(self):
        repo = tempfile.mkdtemp()
        try:
            git = lambda *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True)
            git("init", "-q")
            shutil.copy(os.path.join(HERE, "..", "..", "sfdx-project.json"), repo)
            assets = os.path.join(repo, "force-app", "main", "default", "contentassets")
            os.makedirs(assets)
            for name in ("Logo", "Banner"):
                open(os.path.join(assets, f"{name}.asset"), "w").write("png")
                open(os.path.join(assets, f"{name}.asset-meta.xml"), "w").write(
                    '<?xml version="1.0" encoding="UTF-8"?>\n<ContentAsset xmlns="http://soap.sforce.com/2006/04/'
                    f'metadata">\n    <masterLabel>{name}</masterLabel>\n</ContentAsset>\n')
            git("add", "-A")
            git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "backup")
            # Tonight's pull: both gone from force-app, Logo left out because it broke the pull.
            shutil.rmtree(os.path.join(repo, "force-app"))
            os.makedirs(os.path.join(repo, "retrieve"))
            skipped = os.path.join(repo, "skipped.txt")
            open(skipped, "w").write("ContentAsset\tLogo\tbatch-2\tExpected source files\n")
            out = subprocess.run(["node", os.path.abspath(os.path.join(SCRIPTS, "keep_undelivered.js")), "retrieve",
                                  skipped], cwd=repo, check=True, capture_output=True, text=True).stdout
            self.assertEqual(out.strip(), "1")
            self.assertEqual(sorted(os.listdir(assets)), ["Logo.asset", "Logo.asset-meta.xml"])
        finally:
            shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
