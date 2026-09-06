"""aiq.py 驗收測試（BUILD_SPEC.md 第 7 節 T1～T7 ＋幾個邊界）。

全部在暫存資料夾裡跑：每個測試把 aiq.py 複製到一個新的 temp 專案夾，用子行程呼叫，
不碰 repo 本身的 .aiq。引擎用假的 claude／codex 殼（一支小 Python 腳本），不花額度。

    C:\\Python314\\python.exe -B -m unittest tests.test_aiq -v
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
AIQ_SRC = HERE.parent / "aiq.py"
PY = sys.executable
IS_WIN = os.name == "nt"

FAKE_ENGINE = r'''
import os, re, sys, time, pathlib
text = " ".join(sys.argv[1:])
root = pathlib.Path.cwd()
m = re.search(r"t-\d{8}-[0-9a-f]{4}", text)
tdir = root / ".aiq" / "tasks" / (m.group(0) if m else "unknown")
tdir.mkdir(parents=True, exist_ok=True)
(tdir / "seen_args.txt").write_text(text, encoding="utf-8")
mode = os.environ.get("AIQ_FAKE_MODE", "write")
if mode == "sleep":
    beat = tdir / "heartbeat.txt"
    for i in range(300):
        beat.write_text(str(i), encoding="utf-8")
        time.sleep(0.2)
    sys.exit(0)
for w in os.environ.get("AIQ_FAKE_WRITE", "").split(";"):
    if w:
        p = root / w
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("hi\n", encoding="utf-8")
if mode == "stray":
    (root / "stray.txt").write_text("oops", encoding="utf-8")
if mode in ("write", "stray"):
    (tdir / "result.md").write_text("fake done: " + mode, encoding="utf-8")
sys.exit(0)
'''


class AiqCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="aiq-test-"))
        self.script = self.tmp / "aiq.py"
        shutil.copy(AIQ_SRC, self.script)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_"))
                    and k.upper() not in ("CLAUDECODE", "OPENAI_API_KEY", "OPENAI_BASE_URL", "CLAUDE_PID")}
        self.env.pop("AIQ_FAKE_MODE", None)
        self.env.pop("AIQ_FAKE_WRITE", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers ----
    def aiq(self, *args, env=None, cwd=None, timeout=120):
        r = subprocess.run([PY, "-B", str(self.script), *args], cwd=str(cwd or self.tmp),
                           capture_output=True, env=env or self.env, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")

    def add(self, title, *extra, write=("out.txt",), env=None):
        rc, out, err = self.aiq("add", title, "--prompt", "做 " + title, "--write", *write, *extra,
                                "--json", env=env)
        self.assertEqual(rc, 0, f"add 失敗: {out} {err}")
        return json.loads(out)["id"]

    def db(self):
        con = sqlite3.connect(self.tmp / ".aiq" / "tasks.db")
        con.row_factory = sqlite3.Row
        return con

    def row(self, task_id):
        return self.db().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def install_fake_engines(self, *names):
        """把假的 claude／codex 放到 PATH 最前面；Windows 用 .cmd 殼（跟 npm 裝的一樣）。"""
        fakebin = self.tmp / "fakebin"
        fakebin.mkdir(exist_ok=True)
        script = fakebin / "fake_engine.py"
        script.write_text(FAKE_ENGINE, encoding="utf-8")
        for name in names:
            if IS_WIN:
                (fakebin / f"{name}.cmd").write_text(
                    f'@echo off\r\n"{PY}" "{script}" %*\r\n', encoding="ascii")
            else:
                sh = fakebin / name
                sh.write_text(f'#!/bin/sh\nexec "{PY}" "{script}" "$@"\n', encoding="utf-8")
                sh.chmod(sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        env = dict(self.env)
        env["PATH"] = str(fakebin) + os.pathsep + env.get("PATH", "")
        if IS_WIN:
            env["PATHEXT"] = env.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
        return env

    # ---- T1 ----
    def test_t1_four_processes_race_for_one_task(self):
        task_id = self.add("race")
        deadline = time.time() + 2.5  # 四個行程各自睡到同一個時刻再一起搶
        wrapper = ("import sys, time, runpy; time.sleep(max(0.0, {d} - time.time())); "
                   "sys.argv = ['aiq.py', 'claim', '--worker', sys.argv[1], '--json']; "
                   "runpy.run_path('aiq.py', run_name='__main__')").format(d=deadline)
        procs = [subprocess.Popen([PY, "-B", "-c", wrapper, f"w{n}"], cwd=str(self.tmp), env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for n in range(1, 5)]
        results = {}
        for n, p in enumerate(procs, 1):
            out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, err.decode("utf-8", "replace"))
            results[f"w{n}"] = json.loads(out.decode("utf-8", "replace"))["claimed"]
        winners = [w for w, c in results.items() if c is not None]
        losers = [w for w, c in results.items() if c is None]
        print(f"\n[T1] 4 processes raced for {task_id}: winner={winners} got-nothing={losers}")
        self.assertEqual(len(winners), 1)
        self.assertEqual(len(losers), 3)
        row = self.row(task_id)
        self.assertEqual((row["status"], row["lease_owner"], row["attempt"]), ("running", winners[0], 1))

    # ---- T2 ----
    def test_t2_scope_conflict_rejected_with_id_and_path(self):
        a = self.add("A", write=("src/a.py",))
        rc, out, err = self.aiq("add", "B", "--prompt", "x", "--write", "src/")
        self.assertEqual(rc, 2)
        self.assertIn(a, out)
        self.assertIn("src/a.py", out)
        self.assertIn("--depends-on", out)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
        # 有 depends_on 就放行；讀取範圍也算衝突
        self.add("C", "--depends-on", a, write=("src/",))
        rc, out, _ = self.aiq("add", "D", "--prompt", "x", "--write", "docs/x.md", "--read", "docs")
        self.assertEqual(rc, 0)
        rc, out, _ = self.aiq("add", "E", "--prompt", "x", "--write", "docs/y.md")
        self.assertEqual(rc, 2, "新任務的 write 撞到既有任務的 read 也要拒收")
        self.assertIn("read_scope", out)

    def test_t2b_bad_paths_rejected(self):
        for bad in ("../x.txt", "/etc/passwd", "C:/Windows/x", ".", ".aiq/tasks.db", "a/../../b"):
            rc, out, err = self.aiq("add", "bad", "--prompt", "x", "--write", bad)
            self.assertEqual(rc, 2, bad)
            self.assertIn("拒收", err)
        rc, out, err = self.aiq("add", "dep", "--prompt", "x", "--write", "z.txt", "--depends-on", "t-nope")
        self.assertEqual(rc, 2)
        self.assertNotIn("Traceback", err)

    # ---- T3 ----
    def test_t3_same_work_key_returns_same_id(self):
        first = self.add("k1", "--work-key", "job-1", write=("k.txt",))
        rc, out, _ = self.aiq("add", "k1 again", "--prompt", "x", "--write", "other.txt", "--work-key", "job-1")
        self.assertEqual(rc, 0)
        self.assertIn("已經登記過", out)
        self.assertIn(first, out)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)

    # ---- T4 ----
    def test_t4_expired_lease_is_recovered_and_reclaimed(self):
        task_id = self.add("lease")
        rc, out, _ = self.aiq("claim", "--worker", "w1", "--lease-seconds", "1", "--json")
        self.assertEqual(json.loads(out)["claimed"]["attempt"], 1)
        time.sleep(2)
        rc, out, _ = self.aiq("claim", "--worker", "w2", "--lease-seconds", "60")
        self.assertEqual(rc, 0)
        self.assertIn(f"回收了 {task_id}", out)
        row = self.row(task_id)
        self.assertEqual((row["status"], row["lease_owner"], row["attempt"]), ("running", "w2", 2))

    def test_t4b_claim_respects_depends_on_order(self):
        a = self.add("first", write=("a.txt",))
        b = self.add("second", "--depends-on", a, write=("b.txt",))
        rc, out, _ = self.aiq("claim", "--worker", "w", "--json")
        self.assertEqual(json.loads(out)["claimed"]["id"], a)
        rc, out, _ = self.aiq("claim", "--worker", "w", "--json")
        self.assertIsNone(json.loads(out)["claimed"], "B 要等 A done 才能搶")
        self.aiq("done", a, "--result", "ok")
        rc, out, _ = self.aiq("claim", "--worker", "w", "--json")
        self.assertEqual(json.loads(out)["claimed"]["id"], b)

    # ---- T5 ----
    def test_t5_hook_prints_once(self):
        task_id = self.add("hook me")
        rc, out, _ = self.aiq("done", task_id, "--result", "結果內容 123")
        self.assertEqual(rc, 0)
        rc, out, err = self.aiq("hook")
        self.assertEqual(rc, 0)
        self.assertIn("【佇列結果】hook me（done）", out)
        self.assertIn("結果內容 123", out)
        rc, out, err = self.aiq("hook")
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertEqual(self.row(task_id)["delivered"], 1)

    def test_t5b_hook_batches_five_and_reports_rest(self):
        for i in range(7):
            self.aiq("done", self.add(f"job{i}", write=(f"f{i}.txt",)), "--result", f"r{i}")
        rc, out, _ = self.aiq("hook")
        self.assertEqual(out.count("【佇列結果】"), 5)
        self.assertIn("另有 2 件", out)
        rc, out, _ = self.aiq("hook")
        self.assertEqual(out.count("【佇列結果】"), 2)
        self.assertNotIn("另有", out)

    # ---- T6 ----
    def test_t6_dry_run_reports_stripped_env(self):
        env = self.install_fake_engines("claude", "codex")
        env["ANTHROPIC_API_KEY"] = "x"
        env["CLAUDECODE"] = "1"
        env["OPENAI_API_KEY"] = "y"
        task_id = self.add("dry", "--engine", "claude", env=env)
        rc, out, err = self.aiq("run", "--worker", "w", "--dry-run", env=env)
        self.assertEqual(rc, 0, err)
        self.assertIn("stripped: true", out)
        self.assertIn("ANTHROPIC_API_KEY", out)
        self.assertIn("CLAUDECODE", out)
        self.assertIn("acceptEdits", out)
        self.assertEqual(self.row(task_id)["status"], "queued", "dry-run 後要放回佇列")
        # auto 引擎：capacity.json 數字大的贏
        (self.tmp / ".aiq" / "capacity.json").write_text('{"claude": 0.2, "codex": 0.9}', encoding="utf-8")
        self.aiq("done", task_id, "--result", "x")
        self.add("auto", write=("auto.txt",), env=env)
        rc, out, _ = self.aiq("run", "--worker", "w", "--dry-run", "--json", env=env)
        info = json.loads(out)
        self.assertEqual(info["engine"], "codex")
        self.assertTrue({"ANTHROPIC_API_KEY", "CLAUDECODE", "OPENAI_API_KEY"} <= set(info["removed_env"]))

    # ---- T7 ----
    def test_t7_hook_survives_corrupt_db(self):
        self.add("x")
        (self.tmp / ".aiq" / "tasks.db").write_bytes(b"\x00garbage\xff" * 100)
        rc, out, err = self.aiq("hook")
        self.assertEqual((rc, out, err), (0, "", ""))
        (self.tmp / ".aiq" / "tasks.db").unlink()

    def test_t7b_hook_without_aiq_dir_or_with_bad_dir(self):
        rc, out, err = self.aiq("hook")
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertFalse((self.tmp / ".aiq").exists(), "hook 不准自己建 .aiq")
        (self.tmp / ".aiq").write_text("not a dir", encoding="utf-8")
        rc, out, err = self.aiq("hook")
        self.assertEqual((rc, out, err), (0, "", ""))
        (self.tmp / ".aiq").unlink()
        rc, out, err = self.aiq("hook", cwd=tempfile.gettempdir())  # 從別的資料夾呼叫也一樣
        self.assertEqual((rc, out, err), (0, "", ""))

    # ---- 假引擎跑完整流程 ----
    def test_run_with_fake_engine_marks_done_and_hook_delivers(self):
        env = self.install_fake_engines("claude")
        env["AIQ_FAKE_WRITE"] = "hello.txt"
        task_id = self.add("fake run", "--engine", "claude", write=("hello.txt",), env=env)
        rc, out, err = self.aiq("run", "--worker", "w", "--timeout-seconds", "60", env=env)
        self.assertEqual(rc, 0, err)
        self.assertIn("done", out)
        row = self.row(task_id)
        self.assertEqual((row["status"], row["engine_used"], row["result"]), ("done", "claude", "fake done: write"))
        self.assertEqual((self.tmp / "hello.txt").read_text(encoding="utf-8"), "hi\n")
        self.assertTrue((self.tmp / ".aiq" / "tasks" / task_id / "prompt.md").exists())
        seen = (self.tmp / ".aiq" / "tasks" / task_id / "seen_args.txt").read_text(encoding="utf-8")
        self.assertIn(task_id, seen)
        rc, out, _ = self.aiq("hook")
        self.assertIn("【佇列結果】fake run（done）", out)
        rc, out, _ = self.aiq("run", "--worker", "w", env=env)
        self.assertIn("沒有可搶的", out)

    def test_run_without_result_file_needs_decision(self):
        env = self.install_fake_engines("codex")
        env["AIQ_FAKE_MODE"] = "noresult"
        task_id = self.add("no result", "--engine", "codex", env=env)
        rc, out, _ = self.aiq("run", "--worker", "w", env=env)
        row = self.row(task_id)
        self.assertEqual(row["status"], "needs_decision")
        self.assertIn("沒有寫結果檔", row["result"])
        rc, out, _ = self.aiq("requeue", task_id)
        self.assertEqual(self.row(task_id)["status"], "queued")

    @unittest.skipIf(shutil.which("git") is None, "沒有 git")
    def test_run_detects_out_of_scope_change_in_git_repo(self):
        subprocess.run(["git", "init", "-q"], cwd=str(self.tmp), check=True, capture_output=True)
        env = self.install_fake_engines("claude")
        env["AIQ_FAKE_MODE"] = "stray"
        env["AIQ_FAKE_WRITE"] = "ok.txt"
        task_id = self.add("stray", "--engine", "claude", write=("ok.txt",), env=env)
        self.assertIn(".aiq/", (self.tmp / ".gitignore").read_text(encoding="utf-8"))
        rc, out, _ = self.aiq("run", "--worker", "w", env=env)
        row = self.row(task_id)
        self.assertEqual(row["status"], "needs_decision")
        self.assertIn("越界改動：stray.txt", row["result"])
        self.assertNotIn("ok.txt", row["result"])

    def test_run_timeout_kills_tree_and_requeues(self):
        env = self.install_fake_engines("claude")
        env["AIQ_FAKE_MODE"] = "sleep"
        task_id = self.add("slow", "--engine", "claude", env=env)
        started = time.monotonic()
        rc, out, _ = self.aiq("run", "--worker", "w", "--timeout-seconds", "2", env=env)
        self.assertLess(time.monotonic() - started, 40)
        self.assertIn("逾時", out)
        self.assertEqual(self.row(task_id)["status"], "queued")
        beat = self.tmp / ".aiq" / "tasks" / task_id / "heartbeat.txt"
        self.assertTrue(beat.exists())
        time.sleep(1.0)
        first = beat.stat().st_mtime
        time.sleep(1.5)
        self.assertEqual(beat.stat().st_mtime, first, "子行程沒被殺掉，還在跳心跳")

    def test_status_and_help(self):
        a = self.add("one", write=("1.txt",))
        rc, out, _ = self.aiq("status")
        self.assertIn("queued=1", out)
        self.assertIn(a, out)
        rc, out, _ = self.aiq("status", "--json")
        self.assertEqual(json.loads(out)["counts"]["queued"], 1)
        for cmd in ([], ["add"], ["claim"], ["run"], ["hook"], ["status"], ["done"], ["fail"], ["requeue"]):
            rc, out, err = self.aiq(*cmd, "--help")
            self.assertEqual(rc, 0, f"{cmd} --help 失敗: {err}")
            self.assertIn("usage", out)


if __name__ == "__main__":
    unittest.main()
