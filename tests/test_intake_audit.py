"""Regression coverage for physical skill identities and lexical entrypoints."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
