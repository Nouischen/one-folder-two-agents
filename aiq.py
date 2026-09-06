#!/usr/bin/env python3
"""aiq.py：最小版本機任務佇列，Claude Code 與 Codex 共用（照 BUILD_SPEC.md 實作）。

單檔、只用標準函式庫、Python 3.10 以上、Windows 與 macOS 都能跑。
「專案資料夾」＝這支檔案所在的資料夾；所有相對路徑都以它為基準，
所以不管從哪裡執行（例如掛在對話 hook 上）結果都一樣。

    python aiq.py add "標題" --prompt "要做什麼，含驗收條件" --write 路徑 [--engine claude|codex|auto]
    python aiq.py run --worker 我           # 搶一件交給引擎做；加 --loop 做到佇列空為止
    python aiq.py hook                      # 掛在對話 hook 上，把未讀結果注入下一句話
    python aiq.py status | claim | done | fail | requeue

最小版做不到的：暫存區寫入、整波 SHA-256 盤點、另一引擎新鮮審查、儀表板、多台電腦。
寫入範圍靠宣告與事後 git 檢查，不靠強制隔離。
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AIQ_DIR = ROOT / ".aiq"
DB_PATH = AIQ_DIR / "tasks.db"
TASKS_DIR = AIQ_DIR / "tasks"
CAPACITY_PATH = AIQ_DIR / "capacity.json"

ENGINES = ("claude", "codex")
STATUSES = ("queued", "running", "done", "failed", "needs_decision")
PROMPT_INLINE_LIMIT = 8000  # prompt 超過這個長度就改成「請先讀 prompt.md」
HOOK_BATCH = 5
RESULT_PREVIEW = 800
DEFAULT_LEASE = 900
DEFAULT_TIMEOUT = 3600

# 子行程要剝掉的環境變數：
# - ANTHROPIC_*、OPENAI_API_KEY、OPENAI_BASE_URL：留著會讓 CLI 改走 API 計費，甚至拒絕啟動。
# - CLAUDE_CODE_USE_BEDROCK / _VERTEX（含在 CLAUDE_CODE_ 前綴裡）：會改走第三方雲。
# - CLAUDECODE、CLAUDE_CODE_*、CLAUDE_PID：從 Claude Code 對話裡啟動時會被繼承，
#   子 claude 看到會當成巢狀啟動而拒跑或接錯 session。訂閱登入用的 CLAUDE_CODE_OAUTH_TOKEN 保留。
STRIP_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_")
STRIP_NAMES = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "CLAUDECODE", "CLAUDE_PID"}
KEEP_NAMES = {"CLAUDE_CODE_OAUTH_TOKEN"}

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
try:
    DEFAULT_WORKER = getpass.getuser()
except Exception:  # noqa: BLE001 - 某些環境查不到使用者名稱
    DEFAULT_WORKER = "worker"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  prompt        TEXT NOT NULL,
  work_key      TEXT,
  engine        TEXT NOT NULL,
  status        TEXT NOT NULL,
  read_scope    TEXT NOT NULL,
  write_scope   TEXT NOT NULL,
  depends_on    TEXT NOT NULL,
  attempt       INTEGER NOT NULL DEFAULT 0,
  lease_owner   TEXT,
  lease_until   TEXT,
  created_at    TEXT NOT NULL,
  finished_at   TEXT,
  result        TEXT,
  delivered     INTEGER NOT NULL DEFAULT 0,
  engine_used   TEXT
)
"""

PROMPT_TEMPLATE = """你正在處理佇列任務 {id}：{title}
目標與驗收條件：
{prompt}
規則：
- 只能修改這些路徑：{write_scope}，加上你自己的紀錄夾 .aiq/tasks/{id}/（下面兩個檔就放這裡，寫它們不算違規）。其他檔案只能讀，不能寫、不能刪、不能改名。
- 每完成一個里程碑，把進度整檔覆寫到 {checkpoint}，格式 {{"done": [...], "remaining": [...], "notes": "..."}}。
- 上一次的進度（如果有）：{checkpoint_content}。已記錄為 done 的視為做完，驗證後接著做 remaining。
- 全部完成後，把結果摘要（做了什麼、怎麼驗證的、還有什麼沒做）寫到 {result}。
"""


class Usage(Exception):
    """使用者層級的錯誤：印一行、exit 2，不吐 traceback。"""


# ---------------------------------------------------------------- 小工具

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def rel(path: Path) -> str:
    """專案內相對路徑，一律用 / 分隔，方便放進 prompt 與輸出。"""
    return path.relative_to(ROOT).as_posix()


def task_dir(task_id: str) -> Path:
    return TASKS_DIR / task_id


def task_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("read_scope", "write_scope", "depends_on"):
        d[key] = json.loads(d[key])
    d["checkpoint_path"] = rel(task_dir(d["id"]) / "checkpoint.json")
    d["result_path"] = rel(task_dir(d["id"]) / "result.md")
    return d


def out(a, payload, text: str) -> None:  # --json 印 JSON，否則印人看的文字
    print(dumps(payload) if a.json else text)


def say(a, text: str) -> None:  # 只在非 --json 模式印的進度訊息
    if not a.json:
        print(text, flush=True)


def open_db() -> sqlite3.Connection:
    AIQ_DIR.mkdir(exist_ok=True)
    TASKS_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)  # 交易自己下 BEGIN
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(SCHEMA)
    return con


def fetch(con, task_id: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()


def new_id(con) -> str:
    while True:
        cand = f"t-{utcnow():%Y%m%d}-{secrets.token_hex(2)}"
        if fetch(con, cand) is None:
            return cand


def ensure_gitignore() -> None:
    """專案是 git repo 就把 .aiq/ 加進 .gitignore（已有就不動）。"""
    if not (ROOT / ".git").exists():
        return
    path = ROOT / ".gitignore"
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if any(line.strip() in (".aiq", ".aiq/", "/.aiq", "/.aiq/") for line in text.splitlines()):
            return
        with path.open("a", encoding="utf-8") as fh:
            fh.write(("" if not text or text.endswith("\n") else "\n") + ".aiq/\n")
    except (OSError, UnicodeError):
        pass


# ---------------------------------------------------------------- 範圍

def norm_scope(raw: str) -> str:
    """把使用者給的路徑正規化成專案內相對路徑；絕對路徑、跳出專案、.aiq 一律拒收。"""
    text = raw.strip().replace("\\", "/")
    if not text or text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        raise Usage(f"拒收路徑 {raw!r}：要用專案內的相對路徑")
    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise Usage(f"拒收路徑 {raw!r}：跳出專案資料夾")
            parts.pop()
        else:
            parts.append(part)
    if not parts:
        raise Usage(f"拒收路徑 {raw!r}：不能把整個專案資料夾當範圍")
    if parts[0] == ".aiq":
        raise Usage(f"拒收路徑 {raw!r}：.aiq 是佇列自己的資料夾")
    return "/".join(parts)


def overlaps(a: str, b: str) -> bool:
    """同一路徑，或一方是另一方的上層資料夾。

    比對前先 casefold：Windows 與 macOS 的檔案系統預設不分大小寫，
    `report.md` 與 `REPORT.MD` 是同一個檔。不這樣做的話，換個大小寫
    重新登記就能繞過「同一個檔同一時間只有一個 writer」這條保證。
    """
    pa, pb = a.casefold().split("/"), b.casefold().split("/")
    n = min(len(pa), len(pb))
    return pa[:n] == pb[:n]


def dep_closure(con, ids) -> set[str]:
    """depends_on 的遞移閉包：新任務依賴 A、A 依賴 B，則與 B 也算有關係。"""
    seen: set[str] = set()
    todo = list(ids)
    while todo:
        cur = todo.pop()
        if cur in seen:
            continue
        seen.add(cur)
        row = fetch(con, cur)
        if row is not None:
            todo.extend(json.loads(row["depends_on"]))
    return seen


def cmd_add(a) -> int:
    con = open_db()
    writes = [norm_scope(p) for p in a.write]
    reads = [norm_scope(p) for p in a.read]
    deps = list(dict.fromkeys(a.depends_on))
    # 檢查與寫入必須在同一個交易裡：兩個對話同時登記同一個檔時，
    # 「先查再寫」中間的空檔會讓雙方都查到沒衝突、然後都寫進去。
    con.execute("BEGIN IMMEDIATE")
    try:
        rc = _add_locked(con, a, writes, reads, deps)
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    return rc


def _add_locked(con, a, writes: list[str], reads: list[str], deps: list[str]) -> int:
    for dep in deps:
        if fetch(con, dep) is None:
            raise Usage(f"--depends-on {dep}：沒有這件任務")
    if a.work_key:
        row = con.execute(
            "SELECT * FROM tasks WHERE work_key=? AND status IN ('queued','running','done') "
            "ORDER BY created_at DESC LIMIT 1", (a.work_key,)).fetchone()
        if row is not None:
            out(a, {"ok": True, "id": row["id"], "existing": True, "status": row["status"]},
                f"這件已經登記過：{row['id']}（{row['title']}，{row['status']}）")
            return 0
    related = dep_closure(con, deps)
    conflicts = []
    for t in con.execute("SELECT * FROM tasks WHERE status IN ('queued','running')").fetchall():
        if t["id"] in related:
            continue
        # 我的 write vs 對方的 write/read，以及我的 read vs 對方的 write。
        # 只查前者的話，「先登記 writer 再登記 reader」會被放行，reader 讀到寫到一半的檔。
        pairs = [(writes, "write_scope"), (writes, "read_scope"), (reads, "write_scope")]
        for mine_list, kind in pairs:
            for theirs in json.loads(t[kind]):
                for mine in mine_list:
                    if overlaps(mine, theirs):
                        conflicts.append({"id": t["id"], "status": t["status"], "kind": kind,
                                          "theirs": theirs, "mine": mine})
    if conflicts:
        lines = ["範圍衝突，拒收："]
        for c in conflicts:
            lines.append(f"  {c['id']}（{c['status']}）的 {c['kind']} {c['theirs']} "
                         f"與你的 --write {c['mine']} 重疊")
        lines.append("要排在它後面就加 --depends-on " + " ".join(sorted({c["id"] for c in conflicts})))
        out(a, {"ok": False, "error": "scope_conflict", "conflicts": conflicts}, "\n".join(lines))
        return 2
    task_id = new_id(con)
    con.execute(
        "INSERT INTO tasks (id,title,prompt,work_key,engine,status,read_scope,write_scope,depends_on,created_at) "
        "VALUES (?,?,?,?,?,'queued',?,?,?,?)",
        (task_id, a.title, a.prompt or a.title, a.work_key, a.engine, dumps(reads), dumps(writes),
         dumps(deps), iso(utcnow())))
    task_dir(task_id).mkdir(parents=True, exist_ok=True)
    ensure_gitignore()
    out(a, {"ok": True, "id": task_id, "existing": False, "task": task_dict(fetch(con, task_id))},
        f"已登記 {task_id}：{a.title}（engine={a.engine}，write={', '.join(writes)}）")
    return 0


def recover_expired(con) -> list[str]:
    """租約過期的 running 任務改回 queued，attempt 不動。"""
    now = iso(utcnow())
    ids = [r["id"] for r in con.execute(
        "SELECT id FROM tasks WHERE status='running' AND lease_until < ?", (now,)).fetchall()]
    for task_id in ids:
        con.execute("UPDATE tasks SET status='queued', lease_owner=NULL, lease_until=NULL "
                    "WHERE id=? AND status='running' AND lease_until < ?", (task_id, now))
    return ids


def claim_one(con, worker: str, lease_seconds: int, only_id: str | None = None) -> sqlite3.Row | None:
    """在 BEGIN IMMEDIATE 交易裡挑一件可做的，用條件 UPDATE 搶；更新到 0 列就重選。

    only_id 指定時只搶那一件：對話裡的 AI 要自己動手做之前，先用它把任務變成
    running，另一邊才看得出來「這件有人在做」，不會兩邊同時做同一件。
    """
    for _ in range(20):
        con.execute("BEGIN IMMEDIATE")
        try:
            done = {r["id"] for r in con.execute("SELECT id FROM tasks WHERE status='done'").fetchall()}
            pick = None
            for r in con.execute("SELECT id, depends_on FROM tasks WHERE status='queued' "
                                 "ORDER BY created_at, id").fetchall():
                if only_id is not None and r["id"] != only_id:
                    continue
                if all(dep in done for dep in json.loads(r["depends_on"])):
                    pick = r["id"]
                    break
            if pick is None:
                con.execute("COMMIT")
                return None
            until = iso(utcnow() + timedelta(seconds=lease_seconds))
            cur = con.execute(
                "UPDATE tasks SET status='running', lease_owner=?, lease_until=?, attempt=attempt+1 "
                "WHERE id=? AND status='queued'", (worker, until, pick))
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        if cur.rowcount == 1:
            return fetch(con, pick)
    return None


def cmd_claim(a) -> int:
    con = open_db()
    recovered = recover_expired(con)
    row = claim_one(con, a.worker, a.lease_seconds, a.id)
    claimed = task_dict(row) if row is not None else None
    if a.json:
        print(dumps({"claimed": claimed, "recovered": recovered}))
        return 0
    for task_id in recovered:
        print(f"回收了 {task_id}")
    print("沒有可搶的" if claimed is None else json.dumps(claimed, ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------- run

def child_env() -> tuple[dict[str, str], list[str]]:
    """複製目前環境，剝掉會轉成 API 計費或造成巢狀啟動誤判的變數。回傳 (env, 被剝掉的名字)。"""
    env: dict[str, str] = {}
    removed: list[str] = []
    for key, value in os.environ.items():
        if is_stripped(key):
            removed.append(key)
        else:
            env[key] = value
    return env, sorted(removed)


def is_stripped(key: str) -> bool:
    up = key.upper()
    return up not in KEEP_NAMES and (up in STRIP_NAMES or up.startswith(STRIP_PREFIXES))


def find_engines() -> dict[str, str | None]:
    return {name: shutil.which(name) for name in ENGINES}


def pick_engine(con, task: sqlite3.Row, found: dict[str, str | None],
                prefer: str | None = None) -> str | None:
    """任務指定的就用它；auto 看 prefer、再看 capacity.json，都沒有就跟上一件 done 的交替。

    prefer 來自 ``run --engine``：使用者換到另一個引擎的對話通常就是為了用那邊的額度，
    所以那一輪的 auto 任務優先跑在它身上。任務自己寫死的引擎仍然贏過 prefer。
    """
    avail = [e for e in ENGINES if found[e]]
    if task["engine"] in ENGINES:
        return task["engine"] if found[task["engine"]] else None
    if not avail:
        return None
    if prefer in avail:
        return prefer
    if CAPACITY_PATH.exists():
        try:
            cap = json.loads(CAPACITY_PATH.read_text(encoding="utf-8"))
            return max(avail, key=lambda e: float(cap.get(e, 0)))
        except (ValueError, TypeError, OSError, AttributeError):
            pass
    last = con.execute("SELECT engine_used FROM tasks WHERE status='done' AND engine_used IS NOT NULL "
                       "ORDER BY finished_at DESC LIMIT 1").fetchone()
    prefer = "codex" if last is not None and last[0] == "claude" else "claude"
    return prefer if prefer in avail else avail[0]


def build_prompt(task: sqlite3.Row) -> str:
    tdir = task_dir(task["id"])
    checkpoint = tdir / "checkpoint.json"
    content = "（無）"
    if checkpoint.exists():
        content = checkpoint.read_text(encoding="utf-8", errors="replace").strip() or "（無）"
    return PROMPT_TEMPLATE.format(
        id=task["id"], title=task["title"], prompt=task["prompt"],
        write_scope="、".join(json.loads(task["write_scope"])),
        checkpoint=rel(checkpoint), checkpoint_content=content, result=rel(tdir / "result.md"))


def build_argv(engine: str, exe: str, task_id: str, prompt: str, allow_all: bool,
               model: str | None = None) -> list[str]:
    """組子行程命令列。prompt 全文永遠先寫在 .aiq/tasks/<id>/prompt.md；命令列只在兩種情況改放一行
    「請先讀那個檔」：prompt 超過 PROMPT_INLINE_LIMIT，或 exe 是 Windows 的 .cmd/.bat 殼（npm 裝的
    claude、codex 都是）——那層 cmd.exe 會把參數在第一個換行截斷、展開 %VAR%，多行 prompt 直接被吃掉。
    model 不給就用各 CLI 自己的預設（使用者的設定檔）。"""
    text = prompt
    if len(prompt) > PROMPT_INLINE_LIMIT or exe.lower().endswith((".cmd", ".bat")):
        text = (f"Read the UTF-8 file .aiq/tasks/{task_id}/prompt.md first, "
                "then follow the instructions in it exactly.")
    if engine == "claude":
        perm = ["--dangerously-skip-permissions"] if allow_all else ["--permission-mode", "acceptEdits"]
        pick = ["--model", model] if model else []
        return [exe, "-p", text, "--output-format", "text", *perm, *pick]
    perm = ["--dangerously-bypass-approvals-and-sandbox"] if allow_all else ["--sandbox", "workspace-write"]
    pick = ["-m", model] if model else []
    return [exe, "exec", "--skip-git-repo-check", *perm, *pick, text]


def git_status() -> set[str] | None:
    """在 git repo 裡就回傳目前有變動的檔案（相對於專案資料夾）；不是 repo 或沒裝 git 回 None。"""
    kw = dict(cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
              stdin=subprocess.DEVNULL, timeout=60, creationflags=NO_WINDOW)
    try:
        top = subprocess.run(["git", "rev-parse", "--show-prefix"], **kw)
        if top.returncode != 0:
            return None
        st = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "-z", "--", "."], **kw)
    except (OSError, subprocess.SubprocessError):
        return None
    if st.returncode != 0:
        return None
    prefix = top.stdout.strip()
    paths: set[str] = set()
    skip = False
    for entry in st.stdout.split("\0"):
        if skip:  # rename / copy 的下一格是舊路徑
            skip = False
            continue
        if len(entry) < 4:
            continue
        skip = entry[0] in "RC"
        path = entry[3:]
        if path.startswith(prefix):
            paths.add(path[len(prefix):])
    return paths


def dirty_hashes(paths: set[str] | None) -> dict[str, str]:
    """把「本來就有未存檔改動」的檔案內容雜湊起來。

    只靠 git status 的路徑集合比對會有一個洞：本來就 dirty 的檔已經在 before 裡，
    引擎再把它整份覆寫，after - before 是空的，於是越界完全抓不到、任務還顯示 done。
    """
    out: dict[str, str] = {}
    for rel_path in sorted(paths or ()):
        if rel_path.startswith(".aiq/"):
            continue
        try:
            out[rel_path] = hashlib.sha256((ROOT / rel_path).read_bytes()).hexdigest()
        except OSError:
            out[rel_path] = "missing"
    return out


def strays(before: set[str] | None, after: set[str] | None, write_scope: list[str],
           before_hashes: dict[str, str] | None = None) -> list[str]:
    """這次執行動到、但不在 write_scope 也不是 .aiq/ 的檔案。"""
    if before is None or after is None:
        return []
    def out_of_scope(p: str) -> bool:
        return not p.startswith(".aiq/") and not any(overlaps(p, w) for w in write_scope)
    bad = {p for p in after - before if out_of_scope(p)}
    for rel_path, old_hash in (before_hashes or {}).items():
        if not out_of_scope(rel_path):
            continue
        if dirty_hashes({rel_path}).get(rel_path) != old_hash:
            bad.add(rel_path)
    return sorted(bad)


def kill_tree(proc: subprocess.Popen) -> None:
    """殺掉子行程整棵樹：Windows 用 taskkill /T /F，其他平台 killpg（子行程開在自己的 session）。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30,
                           creationflags=NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


def requeue_task(con, task_id: str) -> None:
    con.execute("UPDATE tasks SET status='queued', lease_owner=NULL, lease_until=NULL WHERE id=?", (task_id,))


def peer_write_scopes(con, task_id: str, started_iso: str) -> list[str]:
    """這次執行期間，其他任務合法宣告過的 write_scope。

    兩個 AI 真的同時跑時（各開一個 run），A 結束時的 git 快照會看到 B 剛寫出來的檔案。
    那不是 A 越界——add 的範圍衝突檢查已經保證同時存在的任務 write_scope 不重疊——
    所以把「當時也在跑、或在這之後才結束」的其他任務的宣告範圍排除掉，否則會誤報。
    """
    rows = con.execute(
        "SELECT write_scope FROM tasks WHERE id<>? AND (status='running' OR finished_at>=?)",
        (task_id, started_iso)).fetchall()
    scopes: list[str] = []
    for r in rows:
        scopes.extend(json.loads(r["write_scope"]))
    return scopes


def finalize(con, row: sqlite3.Row, engine: str, rc: int, before: set[str] | None,
             started_iso: str, before_hashes: dict[str, str] | None = None) -> tuple[str, str]:
    """子行程結束後判定 done / needs_decision，並寫回資料庫。"""
    task_id = row["id"]
    tdir = task_dir(task_id)
    result_path = tdir / "result.md"
    if result_path.exists():
        status = "done"
        result = result_path.read_text(encoding="utf-8", errors="replace").strip()
    else:
        status = "needs_decision"
        result = f"引擎結束但沒有寫結果檔（exit code {rc}，輸出見 {rel(tdir / 'engine.log')}）"
    allowed = json.loads(row["write_scope"]) + peer_write_scopes(con, task_id, started_iso)
    bad = strays(before, git_status(), allowed, before_hashes)
    if bad:
        status = "needs_decision"
        result += "\n\n越界改動：" + "、".join(bad) + "，請使用者檢查"
    elif before is None:
        result += ("\n\n（這個資料夾不是 git repo，這次沒有做越界檢查："
                   "引擎有沒有改到範圍外的檔，程式無法判斷。）")
    cur = con.execute(
        "UPDATE tasks SET status=?, result=?, finished_at=?, delivered=0, engine_used=?, "
        "lease_owner=NULL, lease_until=NULL WHERE id=? AND status='running' AND lease_owner=?",
        (status, result, iso(utcnow()), engine, task_id, row["lease_owner"]))
    if cur.rowcount != 1:
        result = "（租約已被別的 worker 接手，這次的結果沒有寫回資料庫）\n" + result
    return status, result


def run_once(a) -> str:
    """做一件。回傳 empty / no-engine / dry-run / timeout / done / needs_decision。"""
    con = open_db()
    recovered = recover_expired(con)
    for task_id in recovered:
        say(a, f"回收了 {task_id}")
    row = claim_one(con, a.worker, a.timeout_seconds + 60)  # 租約蓋過整段執行，做到一半不會被搶走
    if row is None:
        out(a, {"claimed": None, "recovered": recovered}, "沒有可搶的")
        return "empty"
    task_id = row["id"]
    found = find_engines()
    engine = pick_engine(con, row, found, a.engine)
    if engine is None:
        requeue_task(con, task_id)
        msg = ("這台電腦沒有 claude 也沒有 codex" if not any(found.values())
               else f"這台電腦沒有 {row['engine']}，任務已放回佇列")
        out(a, {"id": task_id, "status": "queued", "error": "no_engine", "message": msg}, msg)
        return "no-engine"
    tdir = task_dir(task_id)
    tdir.mkdir(parents=True, exist_ok=True)
    prompt = build_prompt(row)
    (tdir / "prompt.md").write_text(prompt, encoding="utf-8")
    argv = build_argv(engine, found[engine], task_id, prompt, a.allow_all, a.model)
    env, removed = child_env()
    stripped = not any(is_stripped(k) for k in env)
    if a.dry_run:
        requeue_task(con, task_id)
        info = {"id": task_id, "engine": engine, "command": argv, "cwd": str(ROOT), "stripped": stripped,
                "removed_env": removed, "prompt_file": rel(tdir / "prompt.md"), "status": "queued"}
        out(a, info, "\n".join([
            f"dry-run：不啟動引擎，任務 {task_id} 已放回佇列",
            f"command: {subprocess.list2cmdline(argv)}", f"cwd: {ROOT}",
            f"stripped: {str(stripped).lower()}", f"removed_env: {', '.join(removed) or '(none)'}",
            f"prompt: {info['prompt_file']}"]))
        return "dry-run"
    result_path = tdir / "result.md"
    if result_path.exists():
        result_path.unlink()  # 舊結果不算數，免得引擎秒退還被判 done
    log_path = tdir / "engine.log"
    before = git_status()
    before_hashes = dirty_hashes(before)
    if before is None:
        say(a, "提醒：這個資料夾不是 git repo，做完不會檢查引擎有沒有改到範圍外的檔。"
               "要這層保護，先在這個資料夾 git init。")
    started_iso = iso(utcnow())
    say(a, f"{task_id} 交給 {engine}（attempt {row['attempt']}），引擎輸出寫在 {rel(log_path)}")
    started = time.monotonic()
    popen_kw: dict = {"cwd": str(ROOT), "env": env, "stdin": subprocess.DEVNULL, "stderr": subprocess.STDOUT}
    if os.name == "nt":
        popen_kw["creationflags"] = NO_WINDOW
    else:
        popen_kw["start_new_session"] = True
    with log_path.open("ab") as log:
        log.write(f"=== {iso(utcnow())} attempt {row['attempt']} {engine}: "
                  f"{subprocess.list2cmdline(argv)}\n".encode("utf-8"))
        log.flush()
        try:
            proc = subprocess.Popen(argv, stdout=log, **popen_kw)
        except OSError as exc:
            requeue_task(con, task_id)
            raise Usage(f"啟動 {engine} 失敗：{exc}") from exc
        try:
            rc = proc.wait(timeout=a.timeout_seconds)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            requeue_task(con, task_id)
            out(a, {"id": task_id, "status": "queued", "error": "timeout", "engine": engine},
                f"{task_id} 逾時（{a.timeout_seconds}s），已殺掉子行程並放回佇列，checkpoint 保留")
            return "timeout"
        except KeyboardInterrupt:
            kill_tree(proc)
            requeue_task(con, task_id)
            say(a, f"中斷：{task_id} 已放回佇列")
            raise
    elapsed = round(time.monotonic() - started, 1)
    status, result = finalize(con, row, engine, rc, before, started_iso, before_hashes)
    out(a, {"id": task_id, "status": status, "engine": engine, "rc": rc, "seconds": elapsed, "result": result},
        f"{task_id} → {status}（{engine}，rc={rc}，{elapsed}s）\n{result[:RESULT_PREVIEW]}")
    return status


def cmd_run(a) -> int:
    while True:
        outcome = run_once(a)
        if not a.loop or outcome not in ("done", "needs_decision"):
            return 0


# ---------------------------------------------------------------- hook / status / done / fail / requeue

def cmd_hook(a) -> int:
    """把未讀結果印出來給對話用。永遠 exit 0、任何錯誤都吞掉；不建立 .aiq、不建資料庫。"""
    try:
        if not DB_PATH.is_file():
            return 0
        # 只在對話的工作目錄就在這個專案裡時才出聲。hook 常常掛在使用者層設定
        # （全機器生效），沒有這道判斷的話，你在別的專案講一句話，這裡的結果
        # 就會被灌進那個對話並標成已送達，原本那個對話反而再也看不到。
        here = Path.cwd().resolve()
        if here != ROOT and ROOT not in here.parents:
            return 0
        con = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        rows = con.execute(
            "SELECT id, title, status, result FROM tasks WHERE delivered=0 "
            "AND status IN ('done','failed','needs_decision') ORDER BY finished_at, created_at").fetchall()
        if not rows:
            return 0
        items = [{"id": r["id"], "title": r["title"], "status": r["status"],
                  "result": (r["result"] or "")[:RESULT_PREVIEW]} for r in rows[:HOOK_BATCH]]
        rest = len(rows) - len(items)
        if a.json:
            print(dumps({"results": items, "remaining": rest}))
        else:
            for it in items:
                print(f"【佇列結果】{it['title']}（{it['status']}）")
                print(it["result"])
            if rest > 0:
                print(f"另有 {rest} 件，下一句話接著送")
        sys.stdout.flush()
        con.executemany("UPDATE tasks SET delivered=1 WHERE id=?", [(it["id"],) for it in items])
        con.close()
    except BaseException:  # noqa: BLE001 - 這個指令絕對不能弄壞使用者的對話
        pass
    return 0


def cmd_status(a) -> int:
    con = open_db()
    rows = con.execute("SELECT * FROM tasks ORDER BY created_at, id").fetchall()
    counts = {s: 0 for s in STATUSES}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    if a.json:
        print(dumps({"counts": counts, "tasks": [task_dict(r) for r in rows]}))
        return 0
    print("  ".join(f"{k}={v}" for k, v in counts.items()))
    for r in rows:
        used = f"→{r['engine_used']}" if r["engine_used"] and r["engine_used"] != r["engine"] else ""
        print(f"{r['id']}  {r['status']:<14}  {r['engine'] + used:<13}  {r['title']}")
    return 0


def cmd_finish(a) -> int:
    """done / fail：手動結案，人或 AI 都能用。"""
    con = open_db()
    if fetch(con, a.id) is None:
        raise Usage(f"沒有這件任務：{a.id}")
    status = "done" if a.cmd == "done" else "failed"
    con.execute("UPDATE tasks SET status=?, result=?, finished_at=?, delivered=0, lease_owner=NULL, "
                "lease_until=NULL, engine_used=COALESCE(?, engine_used) WHERE id=?",
                (status, a.result, iso(utcnow()), a.by, a.id))
    out(a, {"ok": True, "id": a.id, "status": status}, f"{a.id} → {status}")
    return 0


def cmd_show(a) -> int:
    """把一件任務的完整內容與結果再印一次；hook 送過之後也還讀得到。"""
    con = open_db()
    row = fetch(con, a.id)
    if row is None:
        raise Usage(f"沒有這件任務：{a.id}")
    d = task_dict(row)
    if a.json:
        print(dumps(d))
        return 0
    print(f"{d['id']}  {d['status']}  engine={d['engine']}"
          f"{'→' + d['engine_used'] if d['engine_used'] else ''}")
    print(f"標題：{d['title']}")
    print(f"會改的檔：{'、'.join(d['write_scope']) or '(無)'}")
    if d["read_scope"]:
        print(f"會讀的檔：{'、'.join(d['read_scope'])}")
    print(f"要做什麼：{d['prompt']}")
    print("結果：\n" + (d["result"] or "(還沒有結果)"))
    return 0


def cmd_requeue(a) -> int:
    con = open_db()
    row = fetch(con, a.id)
    if row is None:
        raise Usage(f"沒有這件任務：{a.id}")
    if row["status"] not in ("needs_decision", "failed"):
        raise Usage(f"{a.id} 目前是 {row['status']}，只有 needs_decision 或 failed 能 requeue")
    con.execute("UPDATE tasks SET status='queued', lease_owner=NULL, lease_until=NULL, finished_at=NULL "
                "WHERE id=?", (a.id,))
    out(a, {"ok": True, "id": a.id, "status": "queued"}, f"{a.id} → queued")
    return 0


# ---------------------------------------------------------------- 命令列

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aiq.py", description="最小版本機任務佇列（Claude Code 與 Codex 共用）")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="指令")

    def add_cmd(name: str, func, help_text: str):
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.add_argument("--json", action="store_true", help="以 JSON 輸出")
        sp.set_defaults(func=func)
        return sp

    sp = add_cmd("add", cmd_add, "登記一件工作")
    sp.add_argument("title", help="標題")
    sp.add_argument("--prompt", help="要做什麼，含驗收條件；不給就用標題")
    sp.add_argument("--write", nargs="+", required=True, metavar="PATH", help="允許修改的相對路徑，至少一個")
    sp.add_argument("--read", nargs="+", default=[], metavar="PATH", help="會讀的相對路徑")
    sp.add_argument("--depends-on", nargs="+", default=[], metavar="ID", help="要等這些任務 done 才開始")
    sp.add_argument("--engine", choices=("auto", "claude", "codex"), default="auto")
    sp.add_argument("--work-key", help="工作身分；同 key 已有 queued/running/done 就不重複建")
    sp = add_cmd("claim", cmd_claim, "原子搶單（只搶不執行）；自己要動手做之前先用它占住")
    sp.add_argument("--worker", default=DEFAULT_WORKER, help="搶單者名字")
    sp.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE)
    sp.add_argument("--id", help="只搶這一件（不給就搶最早那件可做的）")
    sp = add_cmd("run", cmd_run, "搶一件並交給引擎做")
    sp.add_argument("--worker", default=DEFAULT_WORKER, help="搶單者名字")
    sp.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT)
    sp.add_argument("--dry-run", action="store_true", help="只印出將要執行的指令，不啟動")
    sp.add_argument("--loop", action="store_true", help="連做到佇列空為止")
    sp.add_argument("--allow-all", action="store_true",
                    help="引擎跳過所有權限確認（claude --dangerously-skip-permissions／codex 全開）；"
                         "預設只自動接受專案內的檔案編修")
    sp.add_argument("--model", help="指定模型名稱（claude --model／codex -m）；不給就用 CLI 自己的預設")
    sp.add_argument("--engine", choices=ENGINES,
                    help="這一輪的 auto 任務優先跑在這個引擎上（換引擎接手時用，任務自己指定的仍然優先）")
    add_cmd("hook", cmd_hook, "把未讀結果印出來給對話（永遠 exit 0）")
    add_cmd("status", cmd_status, "各狀態計數＋每件一行")
    for name in ("done", "fail"):
        sp = add_cmd(name, cmd_finish, f"手動把任務標成 {'done' if name == 'done' else 'failed'}")
        sp.add_argument("id")
        sp.add_argument("--result", default="（手動結案）", help="結果摘要")
        sp.add_argument("--by", choices=ENGINES, help="是誰做的（對話裡自己做完就填自己）")
    sp = add_cmd("requeue", cmd_requeue, "needs_decision 或 failed 改回 queued")
    sp.add_argument("id")
    sp = add_cmd("show", cmd_show, "把一件任務的內容與結果再印一次")
    sp.add_argument("id")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # 管線或 hook 下 Windows 預設 cp950，中文會炸
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Usage as exc:
        if args.json:
            print(dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"錯誤：{exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"資料庫錯誤：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
