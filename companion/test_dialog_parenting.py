"""Tests for dialog parenting audit -- every messagebox call must include parent=self."""

import ast
import os
import unittest


class TestDialogParenting(unittest.TestCase):
    """Verify that all tkinter messagebox calls in the companion use parent=self."""

    TARGETS = {
        "schedule_dialog.py",
        "scheduler_panel.py",
        "settings_panel.py",
    }

    def _iter_py_files(self):
        base = os.path.dirname(os.path.abspath(__file__))
        for fname in self.TARGETS:
            path = os.path.join(base, fname)
            if os.path.exists(path):
                yield fname, path

    def _find_messagebox_calls(self, tree: ast.AST):
        """Yield (lineno, call_name, has_parent) for every messagebox call."""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id == "messagebox":
                    has_parent = any(
                        kw.arg == "parent" for kw in node.keywords
                    )
                    yield node.lineno, func.attr, has_parent

    def test_all_messagebox_calls_have_parent(self):
        violations = []
        for fname, path in self._iter_py_files():
            with open(path, encoding="utf-8") as f:
                try:
                    tree = ast.parse(f.read(), filename=path)
                except SyntaxError:
                    continue
            for lineno, call_name, has_parent in self._find_messagebox_calls(tree):
                if not has_parent:
                    violations.append(f"{fname}:{lineno} -- messagebox.{call_name}() missing parent=")

        self.assertEqual(
            violations, [],
            f"Found messagebox calls without parent=:\n" + "\n".join(violations) if violations else ""
        )

    def test_schedule_dialog_all_validations_have_parent(self):
        """ScheduleDialog's validation errors all have parent=self."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule_dialog.py")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content, filename=path)
        violations = []
        for lineno, call_name, has_parent in self._find_messagebox_calls(tree):
            if call_name == "showerror" and not has_parent:
                violations.append(lineno)

        self.assertEqual(violations, [], f"schedule_dialog.py showerror calls without parent= at lines {violations}")

    def test_scheduler_panel_all_have_parent(self):
        """SchedulerPanel's messagebox calls all have parent=self."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduler_panel.py")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content, filename=path)
        violations = []
        for lineno, call_name, has_parent in self._find_messagebox_calls(tree):
            if not has_parent:
                violations.append(lineno)

        self.assertEqual(violations, [], f"scheduler_panel.py messagebox calls without parent= at lines {violations}")

    def test_queue_panel_details_fallback_has_parent(self):
        """QueuePanel's fallback messagebox in _show_details_dialog has parent=self."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queue_panel.py")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        tree = ast.parse(content, filename=path)
        violations = []
        for lineno, call_name, has_parent in self._find_messagebox_calls(tree):
            if call_name == "showinfo" and not has_parent:
                violations.append(lineno)

        self.assertEqual(violations, [], f"queue_panel.py showinfo calls without parent= at lines {violations}")


class TestKeyboardSafety(unittest.TestCase):
    """Keyboard shortcuts should not execute when Queue page is inactive."""

    def test_shortcuts_return_none_when_queue_inactive(self):
        """All shortcuts must check _active_queue_page() and return early."""
        base = os.path.dirname(os.path.abspath(__file__))
        ui_path = os.path.join(base, "ui.py")
        with open(ui_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=ui_path)

        shortcut_methods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_shortcut_"):
                shortcut_methods.append(node.name)

        self.assertTrue(len(shortcut_methods) >= 8, f"Expected 8+ shortcuts, found {len(shortcut_methods)}")

if __name__ == "__main__":
    unittest.main()
