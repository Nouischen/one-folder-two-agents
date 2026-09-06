"""純函式層的檢查，不起子行程：--model 要傳到對的旗標；.cmd 殼只給指標句；prompt 模板要把任務自己的紀錄夾列為例外。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiq  # noqa: E402


class ArgvAndPromptUnitTests(unittest.TestCase):
    def test_model_flag_goes_to_each_cli(self):
        claude = aiq.build_argv("claude", "claude", "t-1", "p", False, "opus")
        codex = aiq.build_argv("codex", "codex", "t-1", "p", False, "gpt-x")
        self.assertEqual(claude[claude.index("--model") + 1], "opus")
        self.assertEqual(codex[codex.index("-m") + 1], "gpt-x")
        self.assertNotIn("--model", aiq.build_argv("claude", "claude", "t-1", "p", False))
        self.assertNotIn("-m", aiq.build_argv("codex", "codex", "t-1", "p", False))

    def test_permission_flags_default_and_allow_all(self):
        self.assertIn("acceptEdits", aiq.build_argv("claude", "claude", "t-1", "p", False))
        self.assertIn("--dangerously-skip-permissions", aiq.build_argv("claude", "claude", "t-1", "p", True))
        self.assertIn("workspace-write", aiq.build_argv("codex", "codex", "t-1", "p", False))
        self.assertIn("--skip-git-repo-check", aiq.build_argv("codex", "codex", "t-1", "p", False))

    def test_cmd_shell_gets_pointer_line_only(self):
        multiline = "第一行" + chr(10) + "第二行"
        argv = aiq.build_argv("claude", "C:/x/claude.CMD", "t-9", multiline, False)
        self.assertIn(".aiq/tasks/t-9/prompt.md", argv[2])
        self.assertNotIn(chr(10), argv[2])
        plain = aiq.build_argv("claude", "/usr/local/bin/claude", "t-9", multiline, False)
        self.assertEqual(plain[2], multiline)

    def test_prompt_template_exempts_task_record_folder(self):
        text = aiq.PROMPT_TEMPLATE
        self.assertIn(".aiq/tasks/{id}/", text)
        self.assertIn("{checkpoint}", text)
        self.assertIn("{result}", text)

    def test_scope_overlap_is_segment_wise(self):
        self.assertTrue(aiq.overlaps("src", "src/a.py"))
        self.assertTrue(aiq.overlaps("src/a.py", "src"))
        self.assertTrue(aiq.overlaps("src/a.py", "src/a.py"))
        self.assertFalse(aiq.overlaps("src/a", "src/ab"))
        self.assertFalse(aiq.overlaps("docs", "src"))

    def test_no_engine_installed_returns_none(self):
        nothing = {"claude": None, "codex": None}
        self.assertIsNone(aiq.pick_engine(None, {"engine": "auto"}, nothing))
        self.assertIsNone(aiq.pick_engine(None, {"engine": "claude"}, nothing))
        self.assertIsNone(aiq.pick_engine(None, {"engine": "codex"}, {"claude": "claude", "codex": None}))
        self.assertEqual(aiq.pick_engine(None, {"engine": "claude"}, {"claude": "claude", "codex": None}), "claude")

    def test_prefer_engine_only_overrides_auto_tasks(self):
        both = {"claude": "claude", "codex": "codex"}
        self.assertEqual(aiq.pick_engine(None, {"engine": "auto"}, both, "codex"), "codex")
        self.assertEqual(aiq.pick_engine(None, {"engine": "claude"}, both, "codex"), "claude")
        # 偏好的引擎這台電腦沒裝時，回到原本的選法（用假 con，因為那條路會查資料庫）
        class _Con:
            def execute(self, *a):
                class _R:
                    def fetchone(self_inner):
                        return None
                return _R()
        self.assertEqual(
            aiq.pick_engine(_Con(), {"engine": "auto"}, {"claude": "claude", "codex": None}, "codex"),
            "claude")

    def test_env_strip_rules(self):
        self.assertTrue(aiq.is_stripped("ANTHROPIC_API_KEY"))
        self.assertTrue(aiq.is_stripped("CLAUDECODE"))
        self.assertTrue(aiq.is_stripped("CLAUDE_CODE_SESSION_ID"))
        self.assertTrue(aiq.is_stripped("OPENAI_API_KEY"))
        self.assertFalse(aiq.is_stripped("CLAUDE_CODE_OAUTH_TOKEN"))
        self.assertFalse(aiq.is_stripped("PATH"))


if __name__ == "__main__":
    unittest.main()
