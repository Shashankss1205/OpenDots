import json
from pathlib import Path
import tempfile
import unittest

from opendots.agents import CodexAgent, workspace_snapshot
from opendots.config import Target, validate_config
from opendots.tools import ToolRegistry, digest


class WorkspaceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))

    def snapshot(self, **limits):
        entries, truncated = workspace_snapshot(self.root, limits)
        self.assertLessEqual(len(json.dumps(entries).encode()), limits.get("max_bytes", 128000))
        self.assertLessEqual(len(entries), limits.get("max_files", 1000))
        return {entry["path"]: entry for entry in entries}, truncated

    def test_docs_and_examples_cannot_hide_source_inventory(self):
        # Each individual document fits the per-file cap; together they used to
        # consume the entire snapshot before os.walk reached the package.
        for index in range(12):
            self.write(f"docs/{index:02}.md", "documentation\n" * 2000)
            self.write(f"examples/{index:02}.py", "# example\n" * 2500)
        self.write("opendots/terminal.py", "def main():\n    print('terminal')\n")
        self.write("opendots/engine.py", "# runtime\n")
        entries, truncated = self.snapshot()
        self.assertFalse(truncated)
        self.assertEqual(len(entries), 26)
        self.assertEqual(entries["opendots/terminal.py"]["content"], "def main():\n    print('terminal')\n")
        self.assertEqual(entries["opendots/engine.py"]["sha256"], digest("# runtime\n"))
        self.assertTrue(any(entry.get("content_omitted") for entry in entries.values()))

    def test_oversized_json_content_does_not_hide_later_small_file(self):
        self.write("a.py", '"\\' * 100)
        self.write("z.py", "pass\n")
        entries, truncated = self.snapshot(max_bytes=300)
        self.assertFalse(truncated)
        self.assertTrue(entries["a.py"]["content_omitted"])
        self.assertEqual(entries["z.py"]["content"], "pass\n")

    def test_directory_and_file_caps_prioritize_source_over_examples(self):
        for index in range(10):
            self.write(f"examples/{index:02}/sample.py", "example")
        self.write("package/terminal.py", "source")
        entries, truncated = self.snapshot(max_files=3)
        self.assertTrue(truncated)
        self.assertEqual(entries["package/terminal.py"]["content"], "source")

    def test_owner_can_prioritize_docs_and_nested_packages(self):
        self.write("docs/guide.md", "guide" * 20)
        self.write("a.py", "source" * 20)
        entries, _ = self.snapshot(max_bytes=330, priority_paths=["docs/*"])
        self.assertIn("content", entries["docs/guide.md"])
        self.assertTrue(entries["a.py"]["content_omitted"])
        for index in range(5):
            self.write(f"aaa/{index}/test.py", "other")
        self.write("packages/app/terminal.py", "selected")
        entries, truncated = self.snapshot(max_files=4, priority_paths=["packages/app/*"])
        self.assertTrue(truncated)
        self.assertEqual(entries["packages/app/terminal.py"]["content"], "selected")

    def test_omitted_contents_can_be_read_in_a_follow_up(self):
        content = "# large source\n" * 100
        self.write("package/terminal.py", content)
        entries, truncated = self.snapshot(max_file_bytes=10)
        self.assertFalse(truncated)
        self.assertEqual(entries["package/terminal.py"], {"path": "package/terminal.py", "content_omitted": True})
        target = Target("test", "Test", "Update terminal", self.root, (), {"read_file": "auto"})
        agent = CodexAgent(context_limits={"max_file_bytes": 10})
        prompt = agent.prompt(target, {"type": "issue", "payload": {}}, {})
        self.assertIn("Never guess an existing filename", prompt)
        self.assertIn("outcome=needs_follow_up", prompt)
        result = ToolRegistry().execute(target, {"tool": "read_file", "args": {"path": entries["package/terminal.py"]["path"]}})
        self.assertEqual(result["content"], content)
        self.assertEqual(result["sha256"], digest(content))

    def test_unicode_crlf_hashes_and_exact_byte_bounds(self):
        self.write("package/terminal.py", "# café 🚀\r\nprint('hi')\r\n")
        entries, _ = self.snapshot()
        normalized = "# café 🚀\nprint('hi')\n"
        self.assertEqual(entries["package/terminal.py"]["content"], normalized)
        self.assertEqual(entries["package/terminal.py"]["sha256"], digest(normalized))
        for budget in (2, 40, 80, 150, 300):
            with self.subTest(budget=budget):
                self.snapshot(max_bytes=budget)

    def test_hidden_excluded_and_symlink_files_are_not_exposed(self):
        for relative in (".env", ".claude/settings.json", "pkg/node_modules/lib/index.js", "pkg/__pycache__/x.pyc", "private/token.txt"):
            self.write(relative, "secret")
        self.write("pkg/main.py", "safe")
        (self.root / "alias.py").symlink_to(self.root / ".env")
        (self.root / "alias-dir").symlink_to(self.root / "private", target_is_directory=True)
        entries, _ = self.snapshot(exclude=["node_modules", "__pycache__", "private"])
        self.assertEqual(set(entries), {"pkg/main.py"})

    def test_binary_files_remain_in_inventory_without_contents(self):
        (self.root / "image.bin").write_bytes(b"\x00\xff")
        self.write("app.py", "pass")
        entries, _ = self.snapshot()
        self.assertTrue(entries["image.bin"]["content_omitted"])
        self.assertEqual(entries["app.py"]["content"], "pass")

    def test_context_configuration_validation(self):
        validate_config({"targets": [], "context_limits": {"priority_paths": ["docs/*"]}})
        for limits in ({"priority_paths": "docs/*"}, {"priority_paths": [1]}, {"max_bytes": 1}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                validate_config({"targets": [], "context_limits": limits})
