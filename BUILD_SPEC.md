# 施工圖：最小版本機任務佇列

**這份文件是寫給 AI 看的。** 使用者把它交給自己的 Claude Code 或 Codex，AI 照著在使用者的專案資料夾裡建出一個可以用的最小版。使用者不需要看懂這份文件。

讀這份的 AI，先確認三件事再動手：

1. 你現在的工作目錄就是使用者要用的專案資料夾。不是家目錄、不是桌面、不是放了很多專案的共用資料夾。如果是，停下來請使用者換一個資料夾。
2. 你只能在這個資料夾裡建檔。唯一的例外是第 6 節的 hook 設定，而且改之前要先把要改的內容給使用者看、問一次。
3. 只用 Python 標準函式庫，Python 3.10 以上，Windows 與 macOS 都要能跑。不安裝套件、不連雲端、不用任何 API 金鑰。

做完要跑第 7 節的驗收，全部通過才回報。回報用使用者看得懂的話，不要貼指令。

## 0. 這個最小版做什麼、不做什麼

做的：一個本機佇列，Claude Code 與 Codex 共用。工作先登記、原子搶單、租約、同一份檔案同一時間只有一個 writer、做到一半可以接著做、結果自動注入下一句對話、子行程只用訂閱不用 API 金鑰。

不做的：暫存區寫入、整波 SHA-256 盤點、另一引擎新鮮審查、儀表板、多台電腦。這些是完整版的東西，最小版靠宣告與事後檢查，不靠強制隔離。誠實地告訴使用者這一點。

## 1. 檔案

在專案資料夾建立：

```
aiq.py          單一程式檔，所有指令都在這裡
.aiq/           資料夾，程式自己建
.aiq/tasks.db   SQLite 資料庫
.aiq/tasks/<id>/checkpoint.json   每件任務的進度檔
.aiq/tasks/<id>/result.md         每件任務的結果
.aiq/tasks/<id>/prompt.md         每次 run 時組好的完整 prompt（程式自己寫）
.aiq/tasks/<id>/engine.log        引擎子行程的輸出（程式自己寫，出事時看這裡）
.aiq/capacity.json                額度估計，使用者手填，可以不存在
```

「專案資料夾」＝ `aiq.py` 所在的資料夾，不是執行時的工作目錄；這樣掛在對話 hook 上、從別的地方呼叫，結果都一樣。

如果專案是 git repo，把 `.aiq/` 加進 `.gitignore`。

## 1a. 讓之後的對話也知道這裡有佇列（必做）

使用者不會背指令。之後他在這個資料夾開新對話時，AI 得自己知道有 `aiq.py` 可用，所以安裝完要在專案資料夾放一份給 AI 看的使用說明，並讓兩邊的 CLI 自動讀到：

1. 建立 `AIQ.md`，內容照這個範本（可以照抄）：

   ```
   # 這個資料夾有一個本機任務佇列（aiq.py）
   使用者不會用指令，他講的是意思，不是關鍵字。凡是他表達「把某件事排進去／排隊做／等一下做」「去跑／開始做／跑佇列」「做到哪了／進度」這類意思，就對到下面的指令，不要要求他照字面講。他只負責說要做什麼，該動哪些檔由你判斷：
   - 登記：先自己看他的要求與這個資料夾的內容，判斷這件事需要動哪些檔，再 python aiq.py add "標題" --prompt "要做什麼與驗收條件" --write <你判斷出來的檔案或資料夾>...
     範圍是你的工作，不是他的。登記完用一句話告訴他「排進去了，會改的是這幾個檔」，不要反過來叫他指定。只有真的判斷不出來（同名檔案很多、他的說法可以指到兩個完全不同的地方）才問一句。他主動指定時以他的為準。
   - **他人在現場、要你現在做（最常見）：你自己動手做。** 照登記的 write_scope 做，不要碰別的檔；做完 python aiq.py done <id> --result "做了什麼、怎麼驗的"。
     不要為了「跑佇列」再去啟動另一個引擎做同一件事：你本來就是引擎，那樣會多花一次額度，在 Codex 對話裡還會因為巢狀沙箱直接失敗。
   - **他明說要放著跑、不看著（「放著跑」「我先去忙」「睡了」）：** python aiq.py run --worker <你的名字> --engine <你自己是 claude 還是 codex>，讓它另外開一個引擎在背景做。任務要跑指令或測試加 --allow-all。
   - 他一句話裡同時有交辦和「你做」的意思（例如「這個你排進去做」），就 add 完直接自己動手，不要停下來等他再說一次。
   - 狀態：python aiq.py status
   - 收掉／重排：python aiq.py done|fail|requeue <id>
   做完的結果會由 hook 注入下一句對話；沒接 hook 就跑 python aiq.py hook 讀。細節看 python aiq.py --help。
   ```

2. 在 `CLAUDE.md` 與 `AGENTS.md` 各加一行「請先讀 AIQ.md：這個資料夾有本機任務佇列。」檔案不存在就建立；已存在就附加在最後一行，不要覆蓋或改動原有內容。Claude Code 會自動讀 CLAUDE.md，Codex 會自動讀 AGENTS.md，所以之後任何新對話都認得佇列。

## 2. 資料表

一張表就夠：

```sql
CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,      -- 短 id，例如 t-20260906-a1b2
  title         TEXT NOT NULL,
  prompt        TEXT NOT NULL,
  work_key      TEXT,                  -- 工作身分，同 key 不重複建
  engine        TEXT NOT NULL,         -- auto | claude | codex（使用者登記時指定的）
  status        TEXT NOT NULL,         -- queued | running | done | failed | needs_decision
  read_scope    TEXT NOT NULL,         -- JSON 陣列，相對路徑
  write_scope   TEXT NOT NULL,         -- JSON 陣列，相對路徑
  depends_on    TEXT NOT NULL,         -- JSON 陣列，task id
  attempt       INTEGER NOT NULL DEFAULT 0,
  lease_owner   TEXT,
  lease_until   TEXT,                  -- ISO 8601 UTC
  created_at    TEXT NOT NULL,
  finished_at   TEXT,
  result        TEXT,                  -- 結果摘要
  delivered     INTEGER NOT NULL DEFAULT 0,  -- 0 未注入對話, 1 已注入
  engine_used   TEXT                   -- 這次實際跑的是 claude 還是 codex；auto 交替時看這欄
);
```

開資料庫時設 `PRAGMA journal_mode=WAL` 與 `PRAGMA busy_timeout=5000`。

## 3. 指令

全部走 `python aiq.py <指令>`。每個指令都要有 `--json` 輸出選項。

### add：登記一件工作

```
python aiq.py add "標題" [--prompt "要做什麼，含驗收條件"] \
  --write 路徑 [路徑...] [--read 路徑...] [--depends-on ID...] \
  [--engine auto|claude|codex] [--work-key KEY]
```

規則：

- `--prompt` 不給就用標題當 prompt（小事一句標題就夠，像 T8 那樣）。

- `--write` 至少一個。路徑是相對於專案資料夾的檔案或資料夾；絕對路徑、`..` 跳出專案的、整個專案（`.`）、以及 `.aiq/` 底下的一律拒收。
- 同一個 `work_key` 已經有 `queued`、`running` 或 `done` 的任務：不新建，回傳既有的 id，並印「這件已經登記過」。最小版沒有「輸入版本」的概念：同一個 key 就是同一件工作，內容變了請換 key。
- **範圍衝突檢查**：新任務的 `write_scope` 與任何 `queued` 或 `running` 任務的 `write_scope` 或 `read_scope` 有重疊，而且兩者之間沒有 `depends_on` 關係，就拒收，印出衝突的任務 id 與路徑，並建議加 `--depends-on`。精確定義：
  - 重疊＝正規化後（統一用 `/`、去掉 `.`、解析 `..`）路徑相等，或其中一方是另一方的上層資料夾，用分段比對，不是字串前綴比對（`src/a` 與 `src/ab` 不重疊）。
  - 有 `depends_on` 關係＝新任務直接或間接依賴那件任務（走 `depends_on` 的遞移閉包）。新任務登記時還沒有任何人依賴它，所以只要算這一個方向。
- `depends_on` 指到不存在的 id 拒收。依賴的任務最後是 `failed` 或 `needs_decision`，下游會一直留在 `queued` 不被搶；上游 `requeue` 後做完，下游自然接著跑。

### claim：原子搶單

```
python aiq.py claim --worker 名字 [--lease-seconds 900]
```

- 先回收過期租約：`status='running' AND lease_until < now` 的任務改回 `queued`，`attempt` 不動，印一行「回收了 t-xxx」。
- 然後在一個 `BEGIN IMMEDIATE` 交易裡，選出一件 `queued`、所有 `depends_on` 都是 `done` 的任務（`created_at` 最早的），用一句 `UPDATE ... WHERE id=? AND status='queued'` 改成 `running`、填 `lease_owner`、`lease_until`、`attempt+1`。更新到 0 列就代表被別人搶走，重選一次或回傳「沒有可搶的」。
- 成功時印出任務 JSON，含 `checkpoint` 路徑與 `result` 路徑。

### run：搶一件並交給引擎做

```
python aiq.py run --worker 名字 [--engine claude|codex] [--timeout-seconds 3600] [--dry-run] [--allow-all] [--model 名稱] [--loop]
```

`--model` 不給就用各 CLI 自己的預設模型（使用者設定檔裡的那個）。給了就傳給 `claude --model` 或 `codex -m`。使用者的 Codex 設定檔若指定了目前 CLI 版本跑不了的模型，引擎會秒退、任務進 `needs_decision`，`engine.log` 裡會有原因；這時用 `--model` 指定一個能跑的。

1. 呼叫 claim，租約長度＝`timeout-seconds + 60`（要蓋過整段執行，做到一半才不會被別的 worker 當過期搶走）。沒有可搶的就結束，exit 0。
2. 決定引擎：任務寫明 `claude` 或 `codex` 就用它。`auto` 的話先看這一輪的 `--engine`（換引擎接手時，接手的那一邊會傳自己），再讀 `.aiq/capacity.json`（格式 `{"claude": 0.7, "codex": 0.3}`，數字是使用者估的剩餘比例），選數字大的；檔案不存在就交替使用（看上一件 done 的任務的 `engine_used`）。只在這台電腦找得到的引擎裡選（用 `shutil.which`）。兩個都找不到：任務改回 `queued`，印「這台電腦沒有 claude 也沒有 codex」，exit 0。
3. 組 prompt，固定模板：

   ```
   你正在處理佇列任務 <id>：<title>
   目標與驗收條件：
   <prompt>
   規則：
   - 只能修改這些路徑：<write_scope>，加上你自己的紀錄夾 .aiq/tasks/<id>/（下面兩個檔就放這裡，寫它們不算違規）。其他檔案只能讀，不能寫、不能刪、不能改名。
   - 每完成一個里程碑，把進度整檔覆寫到 <checkpoint 路徑>，格式 {"done": [...], "remaining": [...], "notes": "..."}。
   - 上一次的進度（如果有）：<checkpoint 內容>。已記錄為 done 的視為做完，驗證後接著做 remaining。
   - 全部完成後，把結果摘要（做了什麼、怎麼驗證的、還有什麼沒做）寫到 <result 路徑>。
   ```

4. 啟動子行程。Claude 用 `claude -p <prompt> --output-format text --permission-mode acceptEdits`，Codex 用 `codex exec --skip-git-repo-check --sandbox workspace-write <prompt>`。這兩個權限旗標不能省：headless 模式沒人按同意，不給旗標連寫一個檔都會被擋；`acceptEdits`／`workspace-write` 只放行專案內的檔案編修，要讓引擎也能跑指令就下 `run --allow-all`（改成 `--dangerously-skip-permissions`／`--dangerously-bypass-approvals-and-sandbox`）。工作目錄是專案資料夾，引擎的輸出寫到 `.aiq/tasks/<id>/engine.log`。**環境變數**：複製目前環境，然後刪掉所有 `ANTHROPIC_` 開頭的變數，以及 `OPENAI_API_KEY`、`OPENAI_BASE_URL`。這樣兩個 CLI 才會用使用者登入的訂閱，不會轉成 API 計費。巢狀啟動相關的變數也要剝，見第 4 節。
5. `--dry-run`：不啟動，印出將要執行的指令、工作目錄、以及環境變數裡是否還有上述金鑰（印 `stripped: true/false`），然後把任務改回 `queued`。
6. 逾時：殺掉子行程整棵樹（Windows 用 `taskkill /T /F`，其他用 `killpg`，所以子行程要開在自己的 session），任務改回 `queued`，checkpoint 保留。
7. 結束後：
   - `result.md` 存在 → 讀進 `result` 欄，`status='done'`，`finished_at`、`delivered=0`。（啟動前要先刪掉上一次留下的 `result.md`，不然引擎秒退也會被當成 done。）
   - 不存在 → `status='needs_decision'`，`result` 寫「引擎結束但沒有寫結果檔」。
   - 如果專案是 git repo：啟動前後各跑一次 `git status --porcelain`，只看這次執行**新出現**的變動裡、不在 `write_scope` 也不是 `.aiq/` 的檔案。有的話 `status='needs_decision'`，`result` 加一段「越界改動：<清單>，請使用者檢查」。這是最小版唯一的越界檢查（使用者自己本來就沒 commit 的改動不算）。

### hook：把未讀結果注入下一句對話

```
python aiq.py hook
```

- 印出所有 `delivered=0` 且 `status` 是 `done`、`failed`、`needs_decision` 的任務，最多 5 件，每件格式：

  ```
  【佇列結果】<title>（<status>）
  <result 前 800 字>
  ```

  超過 5 件加一行「另有 N 件，下一句話接著送」。印完把這幾件改成 `delivered=1`。
- 沒有東西就什麼都不印。
- **永遠 exit 0，任何錯誤都吞掉**：資料庫壞了、檔案不在、權限不對，都安靜結束。這個指令會掛在對話的 hook 上，它絕對不能弄壞使用者的對話。

### status、done、fail、requeue

```
python aiq.py status                  各狀態計數＋每件一行（id、狀態、引擎、標題）
python aiq.py done ID --result "..."  手動結案（人或 AI 都能用）
python aiq.py fail ID --result "..."
python aiq.py requeue ID              needs_decision 或 failed 改回 queued
```

## 4. 引擎注意事項

- 先用 `shutil.which("claude")` 與 `shutil.which("codex")` 確認裝了哪個。Windows 上 `claude` 可能是 `claude.cmd`，`which` 會處理。
- 子行程一律 `stdin=subprocess.DEVNULL`。互動式 CLI 沒關 stdin 會掛住。
- `result.md` 與 `engine.log` 的內容是引擎自由寫的，還會套用使用者自己的全域規則（例如 CLAUDE.md、AGENTS.md 要求的回報格式）。aiq.py 只保證「有沒有這個檔」，不保證格式；要給別的程式解析，自己另外加規範。
- 不要把 prompt 放在命令列以外的地方傳（例如用 stdin 餵），兩個 CLI 的 `-p` 與 `exec` 都收命令列參數。完整 prompt 每次 run 都先寫進 `.aiq/tasks/<id>/prompt.md`；prompt 太長（超過 8000 字）時命令列只放一行「請先讀 <路徑> 再開始」。
- **Windows 的 `.cmd` 殼會吃掉 prompt。** npm 裝的 `claude.cmd`、`codex.cmd` 中間隔了一層 cmd.exe，參數會在第一個換行被截斷、`%VAR%` 會被展開（實測：多行 prompt 只剩第一行）。所以 `shutil.which` 找到的是 `.cmd`／`.bat` 時，命令列一律只放那一行純 ASCII 的「請先讀 prompt.md」，不放全文。
- **從 Claude Code 對話裡啟動時要剝掉巢狀標記。** 對話環境裡有 `CLAUDECODE`、`CLAUDE_PID` 與一堆 `CLAUDE_CODE_*`（session id、messaging socket 等），子 `claude` 繼承到會被當成巢狀啟動而拒跑或接錯 session。子行程環境除了第 3 節說的金鑰以外，還要刪掉 `CLAUDECODE`、`CLAUDE_PID` 與所有 `CLAUDE_CODE_` 開頭的變數；只有訂閱登入用的 `CLAUDE_CODE_OAUTH_TOKEN` 保留。`CLAUDE_CODE_USE_BEDROCK`／`_VERTEX` 也順便被這條剝掉，正好。

## 5. 執行模式

**兩種做法，別搞混。** 使用者在對話裡要你現在做，你就自己做完再 `done` 記帳——你本身就是引擎，再開一個只是多花一次額度。`run` 是給「他不在旁邊、要背景做」用的，它會另外啟動一個 headless 引擎。

⚠ 在 Codex 的對話裡執行 `run` 且任務又要交給 codex 時，內層 codex 會因為外層沙箱而起不來（`engine.log` 出現 `os error 5`、`failed to initialize in-process app-server client`）。所以在 Codex 對話裡優先自己動手，或讓任務交給 claude。

最小版沒有常駐服務。要跑佇列時，AI 或使用者執行：

```
python aiq.py run --worker 我
```

一次做一件。要連做就寫個迴圈：`python aiq.py run --worker 我 --loop`，做到佇列空為止。要背景跑就交給作業系統排程（Windows 工作排程器、macOS launchd），這份施工圖不包含排程設定。

## 6. 把 hook 接上對話（唯一會改到專案外的步驟）

先把要加的內容印給使用者看，問一次「要不要我幫你加」，同意才改。當下沒有人能回答（無人值守、自動化流程）就只印出建議片段、標明「尚未套用，等你確認」，停在這一步，不要自己猜他會同意。

Claude Code：在 `~/.claude/settings.json` 的 `hooks.UserPromptSubmit` 加一個 command hook，指令是 `python <aiq.py 的絕對路徑> hook`，timeout 10 秒。已有其他 hook 就附加，不要覆蓋。改之前把原檔複製一份 `settings.json.bak-<日期>`。

Codex：在 `~/.codex/hooks.json` 的 `UserPromptSubmit` 加同樣的指令。

接好之後，使用者在同一個資料夾開的任何 Claude Code 或 Codex 對話，每說一句話，未讀的佇列結果就會出現在 AI 的脈絡裡，AI 應該主動轉述。

## 7. 驗收：全部要跑，全部要過

| 編號 | 測什麼 | 通過條件 |
|---|---|---|
| T1 | 四個行程同時 `claim` 同一件任務（用 `subprocess` 同時啟動 4 個 `python aiq.py claim --worker wN --json`） | 恰好 1 個拿到，其餘 3 個回「沒有可搶的」 |
| T2 | `add` 兩件任務，`--write src/a.py` 與 `--write src/`，沒有 depends_on | 第二件被拒收，訊息指出衝突的 id 與路徑 |
| T3 | 同一個 `--work-key` `add` 兩次 | 回同一個 id，資料庫只有一列 |
| T4 | `claim --lease-seconds 1`，等 2 秒，再 `claim` | 第二次搶得到，`attempt` 從 1 變 2 |
| T5 | 手動 `done` 一件，跑 `hook` 兩次 | 第一次印出結果，第二次什麼都不印，exit 都是 0 |
| T6 | 環境變數先設 `ANTHROPIC_API_KEY=x`，跑 `run --dry-run` | 輸出 `stripped: true` |
| T7 | 先把 `.aiq/tasks.db` 備份一份，再把它改成壞檔（例如寫入亂碼），跑 `hook` | 沒有輸出、沒有 traceback、exit 0；測完用備份覆寫還原，不要直接刪掉資料庫本身（裡面可能已經有真實任務）；還原後那個備份檔可留可刪 |
| T8 | 真跑一次（要先問使用者同意，會用一次額度）：`add "建立 hello.txt，內容一行 hi" --write hello.txt --engine claude`（或 codex），然後 `run`。自訂 `--timeout-seconds` 就給寬一點（300 以上），一次真跑常要 60 到 120 秒 | `hello.txt` 存在且內容正確、任務 `done`、`hook` 印得出結果 |

T1 到 T7 不花額度，自己跑完貼結果。T8 要使用者同意。

兩個容易踩的地方：

- `claim` 是先進先出。T8 開始前先用 `status` 確認佇列裡沒有其他 `queued` 的任務（前面測試留下的先 `done` 或 `fail` 掉），不然 `run` 會先去做更早登記的那件，結果對不起來。
- 越界改動檢查只在專案是 git repo 時有作用。專案資料夾不是 git repo，T8 這類真跑就完全沒有「引擎有沒有亂改別的檔」這層保護，純靠 prompt 裡的範圍宣告；回報時要講清楚。

## 8. 回報格式

驗收過了之後，用三段白話回報：

1. 建了什麼、放在哪裡、怎麼用（三句話以內，不要給指令；告訴他用自己的話講就好，AIQ.md 會讓之後的對話認得佇列）。
2. 驗收結果（T1 到 T7 各一行過或不過；T8 有跑就加一行）。
3. 這個最小版做不到什麼（第 0 節那句），以及 hook 有沒有接上。
