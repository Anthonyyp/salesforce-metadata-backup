"""Tests for putting each flow's running version in place. Not part of the backup; nothing here runs at night.

Run from the repo: python3 -m unittest discover .github/tests
"""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import active_flows  # noqa: E402
import report  # noqa: E402


def flow(name, active, latest, ns=None):
    return {"ApiName": name, "NamespacePrefix": ns, "ActiveVersionId": active, "LatestVersionId": latest}


class Behind(unittest.TestCase):
    def test_only_flows_running_an_older_version(self):
        rows = [flow("Same", "301a", "301a"), flow("Late", "301b", "301c"), flow("None", None, "301d")]
        self.assertEqual(list(active_flows.behind(rows, {"Same", "Late", "None"})), ["Late"])

    def test_not_a_packages_or_one_the_backup_left_out(self):
        rows = [flow("Pkg", "301a", "301b", ns="acme"), flow("Out", "301c", "301d")]
        self.assertEqual(active_flows.behind(rows, {"Pkg"}), {})


class Manifest(unittest.TestCase):
    def test_versions_by_number(self):
        xml = active_flows.manifest({"B": 2, "A": 7}, "66.0")
        self.assertIn("<members>A-7</members><members>B-2</members><name>Flow</name>", xml)
        self.assertIn("<version>66.0</version>", xml)


class PutInPlace(unittest.TestCase):
    def test_running_version_takes_the_flows_own_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pulled, flows = os.path.join(tmp, "out", "main", "default", "flows"), os.path.join(tmp, "flows")
            os.makedirs(pulled)
            os.makedirs(flows)
            open(os.path.join(pulled, "Late-1.flow-meta.xml"), "w").write("running")
            open(os.path.join(flows, "Late.flow-meta.xml"), "w").write("newest")
            done = active_flows.put_in_place(os.path.join(tmp, "out"), {"Late": 1, "Gone": 4}, flows)
            self.assertEqual(done, ["Late"])
            self.assertEqual(open(os.path.join(flows, "Late.flow-meta.xml")).read(), "running")
            self.assertEqual(sorted(os.listdir(flows)), ["Late.flow-meta.xml"])


class Report(unittest.TestCase):
    def env(self, status="success"):
        return {"JOB_STATUS": status, "RUN_URL": "u", "REPO_URL": "r", "ORG_LABEL": "Test org"}

    def test_listed_in_the_report(self):
        subject, body, summary = report.build(self.env(), [], 0, 1, flows=[("Late", "1", "2")])
        self.assertIn("1 flow runs an older version than its newest", summary)
        self.assertIn("Late (running version 1, newest 2)", body)
        self.assertNotIn("WARNING", subject)

    def test_failed_run_says_nothing_about_them(self):
        _, _, summary = report.build(self.env("failure"), [], 0, 1, flows=[("Late", "1", "2")])
        self.assertNotIn("Late", summary)

    def test_error_said(self):
        _, _, summary = report.build(self.env(), [], 0, 1, flows=[("error", "query failed", "")])
        self.assertIn("Couldn't put every flow's running version in place (query failed)", summary)


if __name__ == "__main__":
    unittest.main()
