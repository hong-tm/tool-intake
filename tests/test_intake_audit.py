"""Regression coverage for physical skill identities and lexical entrypoints."""

import contextlib
import base64
import datetime as dt
from email.message import Message
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, patch


SPEC = importlib.util.spec_from_file_location(
    "intake_audit", Path(__file__).resolve().parents[1] / "scripts/intake_audit.py"
)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class InventoryTests(unittest.TestCase):
    base = Path()
    root = Path()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / "skills"
        self.root.mkdir()

    def make_skill(self, directory, name="shared"):
        directory.mkdir(parents=True)
        path = directory / "SKILL.md"
        path.write_text(
            f"---\nname: {name}\ndescription: canonical inventory regression\n---\n",
            encoding="utf-8",
        )
        return path

    def cli(self, *args):
        output = io.StringIO()
        with patch.object(audit, "USER_ROOTS", []), patch.object(
            audit, "find_project_roots", return_value=[]
        ), contextlib.redirect_stdout(output):
            result = audit.main(list(args))
        self.assertEqual(result, 0)
        return output.getvalue()

    def test_two_aliases_share_one_physical_skill(self):
        # Given two entry directories pointing at one physical skill.
        physical = self.make_skill(self.base / "physical")
        for name in ("a", "b"):
            (self.root / name).symlink_to(physical.parent, target_is_directory=True)
        # When the same root is also supplied twice.
        skills, notes = audit.inventory([self.root, self.root])
        # Then the first alias survives and distinct entrypoints are retained once.
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["canonical_path"], str(physical))
        self.assertEqual(skills[0]["path"], str(self.root / "a/SKILL.md"))
        self.assertEqual(skills[0]["root"], str(self.root))
        self.assertEqual(skills[0]["entrypoints"], [
            {"path": str(self.root / name / "SKILL.md"), "root": str(self.root)}
            for name in ("a", "b")
        ])
        self.assertEqual(notes, [])

    def test_identical_same_name_copies_remain_distinct(self):
        # Given two files with identical contents but separate realpaths.
        paths = [self.make_skill(self.root / name) for name in ("a", "b")]
        # When inventory is collected.
        skills, notes = audit.inventory([self.root])
        # Then both identities survive and collision provenance names both files.
        self.assertEqual([s["canonical_path"] for s in skills], list(map(str, paths)))
        self.assertEqual(len(notes), 1)
        self.assertIn("collision", notes[0])
        for path in paths:
            self.assertIn(str(path), notes[0])
        groups = audit.same_name_groups(skills)
        self.assertEqual(groups, [{
            "name": "shared", "copies": [
                {"canonical_path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in paths
            ], "content_identical": True, "equality_scope": "SKILL.md",
        }])
        # Equality ignores other assets but detects a changed SKILL.md body.
        (paths[0].parent / "asset.txt").write_text("different asset", encoding="utf-8")
        self.assertTrue(audit.same_name_groups(audit.inventory([self.root])[0])[0]["content_identical"])
        with paths[1].open("a", encoding="utf-8") as stream:
            stream.write("changed body\n")
        self.assertFalse(audit.same_name_groups(audit.inventory([self.root])[0])[0]["content_identical"])

    def test_broken_and_cyclic_entry_symlinks_are_reported(self):
        # Given invalid directory and SKILL.md symlinks beside a valid skill.
        valid = self.make_skill(self.root / "valid")
        (self.root / "dangling").symlink_to("missing")
        (self.root / "cycle-a").symlink_to("cycle-b")
        (self.root / "cycle-b").symlink_to("cycle-a")
        for name, target in (("file-broken", "missing"), ("file-cycle", "SKILL.md")):
            directory = self.root / name
            directory.mkdir()
            (directory / "SKILL.md").symlink_to(target)
        # When the inventory follows links.
        skills, notes = audit.inventory([self.root])
        # Then invalid paths have diagnostics without losing valid skills.
        self.assertEqual([s["canonical_path"] for s in skills], [str(valid)])
        self.assertEqual(len(notes), 5)
        for path in ("dangling", "cycle-a", "cycle-b", "file-broken/SKILL.md", "file-cycle/SKILL.md"):
            self.assertTrue(any(str(self.root / path) in note for note in notes))

    def test_root_aliases_preserve_first_discovery_and_lexical_dedup(self):
        # Given user, project and explicit roots sharing one directory.
        physical = self.make_skill(self.root / "one")
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        # When repeated lexical roots are collected then inventoried.
        with patch.object(audit, "USER_ROOTS", [str(alias)]), patch.object(
            audit, "find_project_roots", return_value=[self.root]
        ):
            roots, root_notes = audit.collect_roots([str(alias), str(self.root)])
        skills, notes = audit.inventory(roots)
        # Then both root aliases remain but identical lexical roots do not repeat.
        self.assertEqual(roots, [alias, self.root])
        self.assertEqual(root_notes + notes, [])
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["path"], str(alias / "one/SKILL.md"))
        self.assertEqual(skills[0]["root"], str(alias))
        self.assertEqual(skills[0]["canonical_path"], str(physical))
        self.assertEqual(len(skills[0]["entrypoints"]), 2)

    def test_invalid_root_links_are_reported_without_crashing(self):
        # Given dangling and cyclic root aliases, including an auto-discovered one.
        broken = self.base / "broken"
        broken.symlink_to("missing")
        cycle = self.base / "cycle"
        cycle.symlink_to("cycle")
        # When roots are collected.
        with patch.object(audit, "USER_ROOTS", [str(cycle)]), patch.object(
            audit, "find_project_roots", return_value=[]
        ):
            roots, notes = audit.collect_roots([str(broken), str(cycle), str(self.root)])
        # Then only the valid root survives and both invalid paths are diagnosed.
        self.assertEqual(roots, [self.root])
        self.assertEqual(len(notes), 2)
        for path in (broken, cycle):
            self.assertTrue(any(str(path) in note for note in notes))

    def test_skill_file_symlink_uses_resolved_file_identity(self):
        # Given distinct directories whose SKILL.md files share one target.
        physical = self.make_skill(self.base / "physical")
        for name in ("a", "b"):
            directory = self.root / name
            directory.mkdir()
            (directory / "SKILL.md").symlink_to(physical)
        # When inventory resolves the file, not merely its parent.
        skills, notes = audit.inventory([self.root])
        # Then there is one identity and two entrypoints.
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["canonical_path"], str(physical))
        self.assertEqual(len(skills[0]["entrypoints"]), 2)
        self.assertEqual(notes, [])
        self.assertEqual(audit.same_name_groups(skills), [])

    def test_cli_json_and_markdown_counts_include_alias_provenance(self):
        # Given two skill entry aliases visible through two root aliases.
        physical = self.make_skill(self.base / "physical")
        for name in ("a", "b"):
            (self.root / name).symlink_to(physical.parent, target_is_directory=True)
        alias = self.base / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        args = ("--roots", str(self.root), "--roots", str(alias), "--keywords", "canonical")
        before = physical.read_bytes()
        # When both CLI output modes render the inventory.
        report = json.loads(self.cli(*args, "--json"))
        markdown = self.cli(*args)
        # Then JSON retains existing fields and counts physical skills per root.
        self.assertEqual(report["inventory_roots"], [
            {"root": str(root), "count": 1, "entrypoint_count": 2}
            for root in (self.root, alias)
        ])
        self.assertEqual(len(report["skills"]), 1)
        self.assertEqual(len(report["overlaps"]), 1)
        self.assertEqual(len(report["skills"][0]["entrypoints"]), 4)
        self.assertTrue({"name", "description", "path", "root"} <= report["skills"][0].keys())
        self.assertIn("Total distinct skills: 1", markdown)
        self.assertIn("Total entrypoints: 4", markdown)
        for root in (self.root, alias):
            self.assertIn(f"{root} (1 unique skill(s), 2 entrypoint(s))", markdown)
            for name in ("a", "b"):
                self.assertIn(str(root / name / "SKILL.md"), markdown)
        self.assertIn(str(physical), markdown)
        self.assertEqual(physical.read_bytes(), before)


class EvidenceTests(InventoryTests):
    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = audit.main([
                "--only-roots", "--roots", str(self.root),
                "--as-of", "2026-10-14", *args,
            ])
        return status, output.getvalue()

    def test_full_tail_keywords_file_once_and_source_integrity(self):
        self.make_skill(self.root / "tail", "tailkeyword")
        target = self.base / "candidate.md"
        content = ("普通文本 " * 600 + "tailkeyword sudo").encode("utf-8")
        target.write_bytes(content)
        original = Path.read_bytes
        reads = []

        def read(path):
            reads.append(path)
            return original(path)

        with patch.object(Path, "read_bytes", read):
            status, output = self.invoke(str(target), "--json", "--strict")
        report = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(reads.count(target), 1)
        up = report["upstream"]
        self.assertNotIn("tailkeyword", up["readme_head"])
        self.assertIn("tailkeyword", report["keywords"])
        self.assertEqual(report["overlaps"][0]["name"], "tailkeyword")
        self.assertEqual(up["evidence_sources"], [{
            "source": str(target), "utf8_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }])
        self.assertEqual(up["readme_bytes"], len(content))
        self.assertIn("root/sudo required", up["red_flags"])
        self.assertNotIn(content.decode(), output)
        self.assertEqual(report["collection_status"], "complete")

    def test_multiple_local_documents_keep_separate_integrity(self):
        target = self.base / "candidate"
        target.mkdir()
        contents = {"README.md": b"hello", "SKILL.md": "文档 tailkeyword".encode()}
        for name, content in contents.items():
            (target / name).write_bytes(content)
        up = audit.local_target(target)
        self.assertEqual(up["readme_bytes"], sum(map(len, contents.values())))
        self.assertEqual(up["evidence_sources"], [
            {"source": str(target / name), "utf8_bytes": len(content),
             "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in contents.items()
        ])

    def test_repeated_explicit_inputs_are_byte_identical(self):
        self.make_skill(self.root / "one")
        target = self.base / "README.md"
        target.write_text("canonical regression", encoding="utf-8")
        with patch.object(audit, "find_project_roots", side_effect=AssertionError("auto discovery")):
            first = self.invoke(str(target), "--json")
            second = self.invoke(str(target), "--json")
        self.assertEqual(first, second)
        report = json.loads(first[1])
        self.assertEqual(report["generated"], "2026-10-14")
        self.assertEqual(report["schema_version"], 2)

    def test_argparse_rejects_invalid_flags_with_exit_two(self):
        cases = [
            ["--only-roots"], ["--top", "0"], ["--top", "-1"],
            ["--as-of", "2026-02-30"], ["--as-of", "20261014"],
            ["--as-of", "2026-1-14"], ["--roots", "a", "b", "c"],
        ]
        for args in cases:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    audit.main(args)
                self.assertEqual(raised.exception.code, 2)

    def test_http_readme_full_tail_hash_and_fixed_push_age(self):
        self.make_skill(self.root / "tail", "tailkeyword")
        content = ("ordinary " * 400 + "tailkeyword").encode()
        payloads = [
            {"pushed_at": "2026-10-10T23:00:00Z"},
            {"content": base64.encodebytes(content).decode(), "download_url": "https://example.org/README.md"},
        ]
        responses = []
        for payload in payloads:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            responses.append(response)
        with patch.object(audit.urllib.request, "urlopen", side_effect=responses) as fetch:
            status, output = self.invoke("https://github.com/owner/candidate", "--json", "--strict")
        report = json.loads(output)
        self.assertEqual(status, 0)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(report["upstream"]["days_since_push"], 4)
        self.assertEqual(report["upstream"]["evidence_sources"][0]["sha256"], hashlib.sha256(content).hexdigest())
        self.assertIn("tailkeyword", report["keywords"])

    def test_http_failure_and_skipped_fetch_are_incomplete(self):
        self.make_skill(self.root / "one")
        url = "https://github.com/owner/candidate"
        error = urllib.error.HTTPError(url, 503, "unavailable", Message(), None)
        with patch.object(audit.urllib.request, "urlopen", side_effect=error):
            status, output = self.invoke(url, "--strict")
        self.assertEqual(status, 1)
        self.assertIn("HTTP 503", output)
        self.assertNotIn("none detected", output)
        with patch.object(audit.urllib.request, "urlopen", side_effect=AssertionError("network forbidden")):
            status, output = self.invoke(url, "--no-network", "--json", "--strict")
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)["collection_status"], "partial")
        self.assertEqual(json.loads(output)["upstream"]["evidence_sources"], [])

    def test_strict_inventory_missing_empty_and_complete_statuses(self):
        self.make_skill(self.root / "one")
        status, output = self.invoke("--json", "--strict")
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)["collection_status"], "inventory_only")
        missing = str(self.base / "missing")
        self.assertEqual(self.invoke(missing, "--json")[0], 0)
        status, output = self.invoke(missing, "--strict")
        self.assertEqual(status, 1)
        self.assertNotIn("none detected", output)
        target = self.base / "empty.md"
        target.write_bytes(b"")
        self.assertEqual(self.invoke(str(target), "--strict")[0], 1)
        target.write_text("the and for", encoding="utf-8")
        status, output = self.invoke(str(target), "--strict")
        self.assertEqual(status, 0)
        self.assertIn("none detected", output)
        (self.root / "broken").symlink_to("missing")
        status, output = self.invoke("--strict", "--json")
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output)["collection_status"], "partial")
        self.assertEqual(self.invoke("--json")[0], 0)

    def test_empty_root_does_not_mark_evidence_partial(self):
        # Given a populated root beside a real root that holds no skills.
        self.make_skill(self.root / "one")
        empty = self.base / "empty-root"
        empty.mkdir()
        # When strict mode audits both roots.
        status, output = self.invoke("--json", "--strict", "--roots", str(empty))
        report = json.loads(output)
        # Then the empty root is reported by count and never fails the run.
        self.assertEqual(status, 0)
        self.assertEqual(report["collection_status"], "inventory_only")
        self.assertEqual(report["notes"], [])
        self.assertEqual(
            [entry for entry in report["inventory_roots"] if entry["root"] == str(empty)],
            [{"root": str(empty), "count": 0, "entrypoint_count": 0}],
        )

    def test_rarity_weights_order_matches_for_agent_review(self):
        # Given a large inventory whose skills all share one word, plus one skill
        # holding an extra rare word.
        for index in range(20):
            self.make_skill(self.root / f"filler{index}", f"filler{index}")
            (self.root / f"filler{index}" / "SKILL.md").write_text(
                f"---\nname: filler{index}\ndescription: sharedword\n---\n", encoding="utf-8"
            )
        self.make_skill(self.root / "specialist", "specialist")
        (self.root / "specialist" / "SKILL.md").write_text(
            "---\nname: specialist\ndescription: sharedword rarekeyword\n---\n", encoding="utf-8"
        )
        skills, _ = audit.inventory([self.root])
        weights = audit.idf_weights(skills)
        # When a candidate shares both the universal and the rare word.
        target = self.base / "candidate.md"
        target.write_text("sharedword rarekeyword", encoding="utf-8")
        status, output = self.invoke(str(target), "--json", "--strict")
        report = json.loads(output)
        # Then a word every skill shares weighs far less than a rare one.
        self.assertLess(weights["sharedword"], weights["rarekeyword"] / 10)
        self.assertEqual(status, 0)
        self.assertEqual(report["overlaps"][0]["name"], "specialist")
        self.assertEqual(report["overlaps"][0]["matched"], ["rarekeyword", "sharedword"])
        self.assertEqual(report["overlaps"][0]["matched_count"], 2)
        filler = next(item for item in report["overlaps"] if item["name"] == "filler0")
        self.assertEqual(filler["matched"], ["sharedword"])
        self.assertGreater(report["overlaps"][0]["score"], filler["score"])
        self.assertEqual(report["agent_review"], {"status": "required", "decision": None})
        for row in report["overlaps"]:
            self.assertNotIn("strength", row)
            self.assertTrue(Path(row["canonical_path"]).is_file())

    def test_agent_review_stays_required_with_missing_or_unmatched_evidence(self):
        self.make_skill(self.root / "one")
        candidate = self.base / "candidate.md"
        candidate.write_text("zzzzunique", encoding="utf-8")
        for target, expected_status in ((candidate, "complete"), (self.base / "missing", "partial")):
            with self.subTest(target=target):
                _, output = self.invoke(str(target), "--json")
                report = json.loads(output)
                self.assertEqual(report["collection_status"], expected_status)
                self.assertEqual(report["overlaps"], [])
                self.assertEqual(report["agent_review"], {"status": "required", "decision": None})
        _, output = self.invoke("--json")
        self.assertEqual(json.loads(output)["agent_review"], {"status": "not_requested", "decision": None})

    def test_match_lists_stay_short(self):
        # Given a candidate that shares far more words than a reader needs.
        for index in range(12):
            self.make_skill(self.root / f"wordy{index}", f"wordy{index}")
            (self.root / f"wordy{index}" / "SKILL.md").write_text(
                f"---\nname: wordy{index}\ndescription: {' '.join(f'word{index}x{n}' for n in range(12))}\n---\n",
                encoding="utf-8",
            )
        target = self.base / "candidate.md"
        target.write_text(" ".join(f"word3x{n}" for n in range(12)), encoding="utf-8")
        status, output = self.invoke(str(target), "--json")
        row = next(item for item in json.loads(output)["overlaps"] if item["name"] == "wordy3")
        # Then the row keeps the count while showing a bounded token list.
        self.assertEqual(row["matched_count"], 12)
        self.assertEqual(len(row["matched"]), 8)

    def test_default_date_uses_utc(self):
        with patch.object(audit.dt, "datetime") as clock:
            clock.now.return_value.date.return_value = dt.date(2026, 10, 14)
            with contextlib.redirect_stdout(io.StringIO()):
                audit.main(["--only-roots", "--roots", str(self.root), "--json"])
        clock.now.assert_called_once_with(dt.timezone.utc)


if __name__ == "__main__":
    unittest.main()
