# 施工圖：安裝一份兩個 AI 共用的待辦清單

這份文件寫給替使用者安裝的 AI。請使用 repo 附的 `aiq.py`，不要自行重寫另一套。使用者只要正常說明工作，選範圍、登記、領取、交接由 AI 處理。

## 0. 安裝位置與完成條件

- 裝在使用者打算長期使用的**專用工作區**（例如 `文件/AI工作區`），或單一專案。專用工作區可以有多個主題子資料夾。
- 不直接裝在家目錄、整個桌面、磁碟根目錄，或尚未確認要一起管理的雜物目錄。母工作區與子專案不要各自再裝一份來管理同一批檔案；獨立清單互相看不見。
- `aiq.py` 所在位置就是清單根目錄。兩個 AI 都從該根目錄開工作；所有 `--write`／`--read` 相對於這裡。
- Python 3.10 以上，只用標準函式庫。先找可用解譯器並跑 `--version`；Windows 記住實際完整路徑，macOS 通常是 `python3`。
- 允許下載原始碼及在專用暫存目錄跑驗收。實際安裝只改使用者指定的工作區；全域 hook 是選配，需另外取得同意。

最小版包含：共用清單、原子領取、範圍檢查、每次領取憑證、互動／背景工作的進度檔、結果讀回。沒有常駐服務、不會自己喚醒、沒有原對話定址推播、不會搬移完整聊天記錄。檔案由引擎直接修改，這是合作規則，**不是作業系統的檔案鎖**。

## 1. 安裝與更新

1. 把 repo 下載到新建的暫存位置，讀本文件。在下載副本執行第 7 節的完整測試；測試自己建立暫存工作區，不碰使用者的清單。
2. 檢查目標根目錄是否已有 `aiq.py`、`AIQ.md`、`CLAUDE.md`、`AGENTS.md`。更新前，確認舊引擎已停手，或等它完成；不可讓新舊程式同時管理清單。
3. 安裝前備份所有將修改的既有檔案。用 SQLite backup API 備份既有 `tasks.db`（不能只複製仍在使用中的資料庫主檔、漏掉 WAL）。不要刪除或清空 `.aiq/`。
4. 把下載版 `aiq.py` 複製到目標根目錄，執行 `<python> <aiq.py完整路徑> install`。
5. `install` 會建立／更新 `AIQ.md`、`CLAUDE.md`、`AGENTS.md` 的 `<!-- AIQ:BEGIN -->` 至 `<!-- AIQ:END -->` 區段，三個檔都含完整工作規則。區段外的個人規則保留；既有檔備份在 `.aiq/install-backups/`。重跑不會堆出重複規則。
6. 舊版沒有標記時，程式會指出是哪個檔。先備份，由 AI 比對內容，只移除舊 AIQ 說明或「請先讀 AIQ.md」指標，保留所有其他個人規則，再執行 `install`。不能刪除整份 CLAUDE.md／AGENTS.md，也不能把這個內部整理步驟丟回使用者。
7. 讀回三份規則，確認解譯器與根路徑正確，執行 `status --json`，確認既有任務仍在。若是全新安裝，應沒有測試任務；若是更新，不能為了「清空狀態」結掉使用者的工作。

資料庫會原地新增 `claim_token`、`started_at`、`before_state` 欄位。舊的 running 工作仍占用範圍；確認舊引擎停止後，按第 4 節重新領取。舊版 `done ID` 不帶領取憑證的用法已停止接受，必須同步更新三份規則。

## 2. 檔案與日常入口

```text
aiq.py                             單檔程式
AIQ.md / CLAUDE.md / AGENTS.md     完整共用工作規則
.aiq/tasks.db                      共用清單
.aiq/tasks/<id>/checkpoint.json    目標、已完成、待做、決策與卡點
.aiq/tasks/<id>/result.md          結果
.aiq/tasks/<id>/prompt.md          背景派工用的完整指令
.aiq/tasks/<id>/engine.log         背景引擎輸出
.aiq/capacity.json                 可選的手動額度估計
.aiq/install-backups/              規則檔備份
```

正式規則由 `aiq.py install` 產生，以程式為準，不要另外抄一份簡化流程造成差異。之後新對話會讀 CLAUDE.md／AGENTS.md，仍需讓使用者在兩個工具各開一次新對話，確認 AI 找得到這份清單。若 App 不載入專案規則，直接請它讀 AIQ.md；不要宣稱所有 App 都有 hook 支援。

## 3. 登記、領取與完成

以下 `<python>` 一律換成已確認的解譯器。路徑含空白時正確引用；Windows PowerShell 呼叫完整程式路徑要使用 `&`。使用者不用自己打這些指令。

```text
<python> aiq.py status --json
<python> aiq.py add "標題" --prompt "完整目標、背景與驗收條件" --write 子資料夾/成果.md --read 參考資料 --json
<python> aiq.py claim --id ID --worker claude --json
<python> aiq.py checkpoint ID --token TOKEN --done "完成草稿，已存檔" --remaining "核對數據" --notes "採用的決策"
<python> aiq.py done ID --token TOKEN --by claude --result "做了什麼、如何驗證、限制"
<python> aiq.py show ID --json
```

- 寫任何工作檔之前先看清單，按讀寫範圍比對，不能只看標題。範圍由 AI 自己判斷；同一件未完成工作優先使用原 ID。
- `--read` 不需要時可省略；`--write` 至少一項。拒絕絕對路徑、跳出根目錄、整個根目錄 `.`、`.aiq/` 與 `.git/`。
- `add` 在同一 SQLite 寫入交易內檢查衝突並登記。寫／寫、寫／讀、讀／寫重疊都算衝突；相等或祖先／子路徑才算，`src/a` 不會撞 `src/ab`。大小寫採保守不區分比對。
- 相依工作可以 `--depends-on ID` 排在後面；只有所有前置任務 done 才能領取。`claim` 仍會在交易內重查目前 running 的衝突。
- 同一個 `--work-key` 已有 queued／running／done，就回傳原 ID。它沒有輸入版本概念；新的工作內容要換 key。
- **claimed 非 null 才能動手。** 回傳包含 `claim_token`、attempt、範圍、進度路徑和 instructions。保存本次 token；done、fail、checkpoint、release 都核對它，舊 attempt 的憑證不能修改新 attempt。
- `claim` 預設不設到期時間。相容選項 `--lease-seconds N` 只是記錄提醒時間，**不會自動解除 running**；時間經過不代表舊引擎停手。
- `done` 只表示持有人宣告完成並通過可用的事後檢查，不代表另一個 AI 已審查；結果摘要不可空白。請讀回輸出的 status，needs_decision 不能說成完成。
- 工作無法完成：`fail ID --token TOKEN --result "已做的事與卡點"`。取消尚未領取的工作：`cancel ID --result "取消原因"`，它不能中斷 running。

## 4. 中斷與換手

任務登記時就建立初始 checkpoint。互動式工作與背景工作都要在每個里程碑更新它，不能等到額度全用完才想起來寫。

**原引擎還能回應：** 停止寫入及其子行程，保存成果後執行：

```text
<python> aiq.py release ID --token TOKEN --done "完成並驗證的項目" --remaining "下一步" --notes "決策、檔案、卡點"
```

release 原子檢查持有人、保存進度，再交回 queued。成功後舊引擎必須停手。接手者 claim 同一 ID、讀進度並核對現有成果，接著做剩餘工作。

**原引擎已中斷，不能 release：** 先 show、讀 checkpoint 和實際檔案。確認舊對話／背景行程已停止，才執行：

```text
<python> aiq.py requeue ID --stopped --attempt 目前次數
<python> aiq.py claim --id ID --worker codex --json
```

`--stopped` 是操作方對「已停手」的確認，程式不能跨 App 自動證明這件事。證據不足才問使用者舊對話是否已停止，不能只看時間。attempt 不吻合會拒絕，避免解除後來別人的領取。requeue 也會重查其他 queued／running 的讀寫衝突。

failed／needs_decision 重試前先檢查檔案與副作用，確認適合續作才 `requeue ID`。發布、寄信等外部動作結果不明時不可盲目重跑。沒有保存的進度不會憑空回來；checkpoint 是摘要，不是完整聊天記錄。

## 5. 背景執行與範圍檢查

使用者在對話裡叫你現在做，直接 claim 後自己做。`run` 會額外啟動一個 headless 引擎，只在使用者需要時使用；最小版沒有常駐服務或內建排程。

```text
<python> aiq.py run --worker 名字 --engine claude --timeout-seconds 3600
<python> aiq.py run --worker 名字 --dry-run --json
```

`--loop` 連做，到清單空或非 done 結果就停。`--model` 可指定模型，不填沿用 CLI 設定。任務明定的 engine 優先；auto 才看這輪 --engine、手動 capacity.json 比例、最後在可用引擎間交替。容量是使用者估計，不會讀取供應商的實際餘額；不會自動替額度耗盡的半成品換引擎。

- 背景 prompt 會說明任務已被 run 領取，子引擎不再 add／claim／done 同一件，只在宣告範圍寫檔，更新自己的 checkpoint 和 result.md。
- Claude 預設 `--permission-mode acceptEdits`；Codex 預設 `--sandbox workspace-write`。這**不表示所有指令或測試都不能跑**，實際可用操作受各 CLI、專案與全域權限影響。缺權限先讀 engine.log，改由目前互動對話執行，或僅授權必要操作。
- 進階 `--allow-all` 會傳 `--dangerously-skip-permissions`／`--dangerously-bypass-approvals-and-sandbox`，跳過整套權限保護。不得把它當成安裝、一般驗收或「跑任何測試」的預設要求。[Claude 權限說明](https://code.claude.com/docs/en/permissions)、[Codex sandbox 定義](https://github.com/openai/codex/blob/main/codex-rs/prompts/templates/permissions/sandbox_mode/workspace_write.md)。
- 子行程移除 ANTHROPIC_*、OPENAI_API_KEY、OPENAI_BASE_URL 及巢狀 Claude 標記，保留 CLAUDE_CODE_OAUTH_TOKEN；仍需確認 CLI 已登入訂閱，並未被其他自訂 provider 設定改走 API。程式不檢查所有 CLI 設定來源，不能保證任意環境都不會 API 計費。
- Windows npm 的 .cmd／.bat 啟動器只收到 ASCII 的 prompt.md 指標，避免換行截斷與 `%VAR%` 展開。完整指令保存在任務資料夾。
- 成功需 exit code 0 且非空 result.md；否則 needs_decision。舊結果在新背景 attempt 前清掉，不會當成本次成果。
- 逾時會停止子行程樹，保存 checkpoint，進 needs_decision 等待檢查，不會自動重跑半成品。停止失敗或狀態不明則保留 running；不能宣稱它已停手。
- 外層 CLI／App 的沙箱有時會讓巢狀引擎起不來。直接在目前對話 claim 後做即可；不把巢狀啟動失敗解讀成必須全開權限。

互動 claim→done 與背景 run 都做 Git 事後檢查：比對新出現的變動，並對執行前已 dirty 的檔案比對內容雜湊。其他同時執行任務的合法範圍會排除，以減少誤報。**它不能可靠歸因每個並行寫入、忽略檔、Git 內部操作或巢狀 repository 的全部變動，也不會還原檔案。** 非 Git 工作區會明講未做檢查。`git init` 可啟用基礎檢查，仍需正常備份或 commit。

## 6. 結果與選配 hook

`show ID` 隨時讀結果；未接 hook 也能完整使用清單。`hook` 每次最多讀 5 件未讀終態摘要並標示已讀，其他結果留到下一次。錯誤時安靜 exit 0，不建立新資料庫。

Hook 只按工作目錄篩選：**同資料夾下一個觸發它的對話**會收到，不保證回原本那個對話。已讀後可用 show 再查；不把 `delivered` 說成使用者已看過或精確一次送達。

需要自動提示時，先讓使用者看具體設定片段，取得一次同意才改；無人值守安裝就標「hook 未接，核心安裝已完成」，不擋其他交付。

- Claude Code：附加到 `~/.claude/settings.json` 的 `hooks.UserPromptSubmit`。備份並保留其他 hook，timeout 10 秒。
- Codex CLI：依當場 CLI 支援的 `~/.codex/hooks.json` 結構附加 UserPromptSubmit command，設定完整 Python 與 aiq.py 路徑、timeout 10 秒；請使用者以 `/hooks` 檢視並信任。不要把 CLI hook 的實測範圍推論成所有 Codex App 都支援。
- command 的引號須符合實際執行 shell，路徑有空白／中文都要測。不要假設 POSIX `python` 或 PowerShell 的 `&` 在所有 hook host 都可用。
- 接好後在目標資料夾與無關資料夾各試一次，確認只在目標範圍提示。避免重複追加同一條 hook。

## 7. 驗收：在下載副本跑，不碰使用者的真實清單

```text
<python> -B -m unittest discover -s tests -v
```

每個測試自行複製 aiq.py 到獨立暫存工作區；用假的引擎程式，不花模型額度。完整 suite 必須通過，不能只挑前七個函式。T1–T7 是七組驗收：

| 編號 | 檢查與通過條件 |
|---|---|
| T1 | 四個獨立行程同時領取，只能一個成功；其他人沒有 claimed |
| T2 | 登記、重新排隊、再次領取都不允許讀寫衝突；有相依的工作可依序完成 |
| T3 | 相同 work-key 不重複建單；重裝保留個人規則、原任務，標記只出現一份 |
| T4 | 超過提醒時間不搶走仍在做的工作；確認停手後可接手，attempt 增加，舊 token 不能結案或改進度；交接保留 checkpoint |
| T5 | 持有人結案後 hook 可讀；第二次不重複；非零退出／空結果不能 done |
| T6 | dry-run 不啟動引擎，環境移除 API 變數；互動與背景越界檢查、逾時停止子行程 |
| T7 | 只在測試暫存副本破壞資料庫，hook 安靜退出；舊資料庫升級保留任務及 running 占用 |

測試期間絕不能破壞、清空、刪除、或把使用者原清單的工作標成完成來「收乾淨」。測試使用自己的暫存目錄即可。

T8 是另選的真實引擎測試，會用訂閱額度。取得同意後，在另一個新暫存工作區建立只寫 hello.txt 的任務，由已安裝且登入的引擎跑，給至少 300 秒；核對檔案、done 與 hook。沒有跑或被權限阻擋就如實回報，不得用假引擎的測試冒充。

## 8. 完成回報、移除

完成用三段白話說明：裝在哪裡、以後正常交辦即可；T1–T7 結果及 T8 有沒有真跑；已知限制與 hook 是否接好。更新時補一句「既有任務與個人規則有保留」。

移除前先確認所有引擎停手並備份。只移除三個規則檔內 AIQ:BEGIN／AIQ:END 區段，保留區段外文字；舊版無標記時僅移除核對過的 AIQ 段落，不依一句指標猜整份檔案可刪。再移除對應的 aiq.py 與 .aiq（內含全部進度與結果），以及確實指向此安裝的 hook。操作前列出精確範圍供使用者確認；同意後再刪，不能把別的專案 hook 一起拿掉。
