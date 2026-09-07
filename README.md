# 一個資料夾、兩個 AI

**讓 Claude Code 和 Codex 共用同一個工作區，又不互相踩到的設計筆記。**

> 這裡有三樣東西：一篇設計說明（本文）、一份寫給 AI 看的施工圖（`BUILD_SPEC.md`）、一支照施工圖做出來的最小版程式（`aiq.py`，單檔、純標準函式庫）。本文把我自己電腦上跑了一個月的本機任務平台拆開講：它解決什麼問題、靠哪幾條規則做到、踩過哪些雷。完整版綁死在我的環境裡，搬不出去；方法和最小版搬得出去。

[English abstract](#english-abstract) · [授權](#授權) · 作者：陳昱傑（[Nouischen](https://github.com/Nouischen)），骨科診所院長，把 Claude Code 與 Codex 當每天的工具在用。

## 為什麼寫這份

同業問我：「我本來用 Claude Code，今天開始用 Codex，指定同一個資料夾會不會衝突？還是分開兩個資料夾好？」另一位問：「額度用完換另一個模型接手，它怎麼知道進度？每次都要寫交接手冊嗎？」

我的答案是：**同一個資料夾，讓不同模型都進來，但要立規矩。** 分開兩個資料夾，日後累積的資料就分家，你得多一個動作互相同步，那是多此一舉。這份筆記就是那套規矩。

四個問題，對應的段落：

| 你在擔心的事 | 看哪一節 |
|---|---|
| 兩個 AI 改到同一份檔案 | §3 |
| 兩邊重工、互相看不見對方做過什麼 | §2 |
| 換模型接手，進度怎麼傳、要不要寫交接手冊 | §4 |
| 額度快用完，怎麼自動換另一邊 | §5 |

## 先安裝公開的最小版

[逐步教學與一鍵複製安裝說明](https://one-folder-two-agents.pages.dev)

裝在你打算長期使用的專用工作區，或單一專案。專用工作區可以有多個主題子資料夾；兩個 AI 都從同一個根目錄開啟。不要在母工作區與子專案重複安裝、管理同一批檔案，也不要直接裝到家目錄或整個桌面。

在目標資料夾開 Claude Code 或 Codex，貼這句話：

> 請先檢查 Python 3.10 以上，把 https://github.com/Nouischen/one-folder-two-agents 下載到新建的暫存位置，讀 BUILD_SPEC.md。在下載副本跑第 7 節完整驗收，測試不要碰我的真實清單。確認目前資料夾是我要長期用的工作區後，備份既有檔案與清單，把 aiq.py 安裝或更新到這裡，再執行 install 產生兩邊共用的規則，保留個人設定與原任務。完成後用白話告訴我怎麼用。全域 hook 是選配，改之前先讓我看內容並問一次；除了下載與測試的暫存位置，不要改這個工作區以外的檔案。

你不用背指令、指定讀寫範圍或自己寫交接手冊。AI 安裝後會照共用規則工作：

- 交辦：「幫我把這份報告改短，這個你排進去做。」AI 先看清單、登記範圍、成功領取才動手。
- 換手前：「把做到哪裡存好，交給另一邊接。」原引擎保存進度並停手，另一邊開同一個資料夾說「接著做」。
- 突然中斷：接手者先讀清單和進度，確認舊引擎已停止後再接。沒保存的部分需要核對檔案，完整聊天不會自動搬過去。
- 查進度：「還有什麼沒做完的？」有無 hook 都能讀回結果；hook 只讓同資料夾下一個觸發它的對話看到未讀摘要，沒有原對話定址推播。

本次修正讓互動工作不再因固定租約到期被搶走；每次領取有獨立憑證，舊對話不能替新一輪結案；重排與領取都重查衝突。`install` 會把完整規則寫進 AIQ.md／CLAUDE.md／AGENTS.md 的標記區段，重跑保留其他個人規則及既有任務。

目前有 35 個自動化測試，使用暫存工作區與假引擎檢查安裝、升級、四行程搶單、交接、舊憑證、越界與逾時。它們不代表每個 CLI／App 或 macOS 都已實測。Windows 已測；macOS 尚未實機驗證。

幾個實際限制：

- AI 仍須遵守規則，程式不會鎖住作業系統的檔案。Git 事後檢查會提示可觀察的越界，包含原本已修改檔案的內容變更；忽略檔、巢狀 repo 與並行寫入無法完整歸因，也不會自動還原。
- 背景 `run` 會額外啟動引擎。平常在目前對話裡直接做即可，不必多開一個。預設權限不等於「不能跑任何測試」；`--allow-all` 會關閉整套權限保護，不是一般安裝或測試的必要步驟。
- 子行程會移除已知 API 環境變數，但不檢查所有自訂 CLI provider 設定；請使用已登入訂閱的 CLI。沒有常駐服務、全自動額度切換、原對話推播、隔離副本或另一引擎審查。

更新舊版也用上面同一句。先讓舊引擎停手，照 [BUILD_SPEC.md](BUILD_SPEC.md) 保留清單、更新程式與三份規則。舊版不帶 token 的結案方式已停止接受。

## 以下是作者私人完整版的設計筆記

**下方第 0–9 節描述私人完整版，不是下載 aiq.py 後全部都有的功能。** 公開最小版的實際行為、安裝與驗收以 [BUILD_SPEC.md](BUILD_SPEC.md) 及程式為準。特別是隔離副本、原對話推播、額度調度與雙引擎終審，均未包含在最小版。

## 0. 三條原則

1. **控制平面不是任何一個 AI。** Claude Code、Codex 都只是「執行器」。誰在排隊、誰搶到、做完沒，記在一個 SQLite 檔裡，兩邊讀同一份。誰都不是老闆，待辦清單才是。
2. **對話是唯一前台。** 我不靠儀表板、不記任務編號、不背指令。在原本那個對話裡說「排進去，今晚跑」，隔天結果回到同一個對話。
3. **只走訂閱，不走 API。** 兩家各訂一個月費方案，平台把兩邊的額度當成一個可調度的池。子行程啟動前把 API 金鑰類環境變數拔掉，免得 Claude Code 悄悄切成按 token 計費。

## 1. 平台長什麼樣

```
 Claude Code 對話 ─┐ 登記                          ┌─► Claude Code（headless）
                   ├──────► 本機 SQLite 待辦清單 ───────┤
 Codex 對話 ───────┘           │        │           └─► Codex（headless）
        ▲                      │        └─► 跨引擎協調器
        │                      │            （分工、暫存區寫入、整波盤點、另一引擎審查）
        └── 結果推回原對話，推不到就由 hook 在下一句話注入
```

兩個部件：

- **待辦清單（runtime）**：排序、搶單、租約、預算、把結果送回對話。單引擎的長工作只需要它。
- **協調器（coordinator）**：一份任務清單（manifest）裡有多個任務要分給兩個引擎時，它管誰能寫哪些檔、寫入先進暫存區、整波盤點、另一引擎審查。

兩個部件都是純標準函式庫的 Python，資料就是檔案。不需要伺服器、沒有雲端、沒有帳號。

## 2. 一件工作只有一個主人

先講事故。2026 年 8 月 8 日晚上 8 點 40 分，Codex 跑完一輪路由稽核；晚上 10 點，Claude 又派了一個工兵做同一件事。兩邊都沒做錯，就是互相看不見。那一次重工是真的花掉的額度。

從那天起的規矩：

- **每件工作先登記進待辦清單**，不管誰要做、不管我在不在旁邊看。我在旁邊就當場跑，我不在就交給背景；登記一定做。
- **搶單是原子的。** 搶任務用一個交易加一句條件更新：

  ```sql
  BEGIN IMMEDIATE;
  UPDATE tasks SET status='running', lease_owner=?, lease_expires=?
   WHERE id=? AND status='queued';
  ```

  測試裡真的開四個獨立的作業系統行程去搶同一件任務，永遠只有一個贏家。
- **租約有期限，只有持有人能動。** 拿到任務就拿到一張租約（誰、第幾次、何時到期）。行程當掉，租約過期，工作才會被別人接手。
- **兩種身分，別混用。** 冪等鍵（idempotency key）擋的是「同一次提交重送」。工作本身的身分是 `work_key`（專案加具體交付物）加 `input_revision`（凍結後輸入的雜湊）。同一個 `work_key` 再送一次，就算換了冪等鍵，也只能附著到既有的主人，不能另開一次執行。
- **回條先落地，資料庫才記帳。** 驗身分、寫回條檔、改資料庫，三步在同一個交易裡。當機重開後找得到合法回條就認帳，找不到就不重跑。
- **次數編號一輩子只增不減。** 手動重試也不歸零，每一次執行有自己的回條路徑，舊回條不會被當成新成果。
- **不確定能不能重做的，停下來等我。** 只有明確標記「重做安全」的工作才自動重試；會發訊息、寫遠端、發布的工作，停在「等決定」，不猜。

## 3. 同一個資料夾，誰能寫什麼

這一節回答「會不會改到同一份檔案」。

- **每個任務先宣告讀寫範圍。** 任務清單裡每個任務都有 `read_scope` 與 `write_scope`。**同一個檔案，同一時間只有一個宣告的 writer。**
- **範圍重疊又沒有先後順序，直接拒收。** 不是跑到一半才發現，是派工前就擋。
- **寫入不直接落在工作區。** writer 在一份外部複本上工作。任務完成、驗過，協調器才把差異搬回工作區（promote）。失敗或卡住的嘗試整份丟掉，工作區沒被碰過。
- **一波結束就盤點。** 任務按相依關係分成幾波平行跑。每一波結束，對整個工作區做 SHA-256 盤點。任何不在宣告範圍內的變動，包括失敗的嘗試留下的殘骸，都算越界：停掉後面的波、回滾這一波。
- **run 期間誰都不能動。** 2026 年 8 月 31 日一個 run 進行中，主對話那一側順手改了建置產物，整個 run 判定越界、安全停止。這是規矩不是 bug：run 期間整個工作區都在盤點範圍內，不只是被派工的那幾個檔。
- **換手只允許一種情況。** 任務先宣告允許換手，而且引擎明確回報 blocked、沒有任何產物，才換另一邊接手一次。做到一半失敗的不換，免得留下兩份半成品。
- **兩邊都要動手時，各寫各的檔。** 宣告 `joint_implementation`，Claude 與 Codex 各自至少一個實作席，write scope 不能重疊。寫入走 brokered 模式：模型只回「我想把哪個檔寫成什麼內容」，落筆的是協調器。

任務清單的一個任務長這樣（節錄）：

```json
{
  "id": "claude-docs",
  "engine": "claude",
  "role": "worker",
  "stage": "execution",
  "objective": "把 README 的安裝段改寫成小白步驟",
  "read_scope": ["README.md", "scripts/"],
  "write_scope": ["README.md"],
  "depends_on": ["codex-installer"],
  "verification": ["README 每一步都對得上 install.ps1 的實際行為"]
}
```

邊界要說清楚：這是**合作式圍堵**，不是作業系統層級的安全邊界。它擋的是兩個聽話的代理人不小心互踩；一個故意搗蛋、跟你同一個 Windows 帳號的程式，它擋不住。回條裡會寫明這一點。

## 4. 進度怎麼傳

這一節回答「每次都要寫交接手冊嗎」。答案是要，但不是你寫，也不是聊天記錄。

三層，由輕到重：

**第一層：Markdown 進度檔。** 只是依序交班（Claude 做完換 Codex、或同一工具開新對話），把「目標、目前成果、下一步、卡點、決策、驗證方式、相關檔案」存成專案裡一個可讀的 Markdown 就夠。我七月做的 [cross-ai-progress-bridge](https://github.com/Nouischen/cross-ai-progress-bridge) 就是這一層，可以直接裝。

**第二層：結果自動回到原對話。** 待辦清單裡每件任務記得自己是哪個對話排的（`origin`：哪個平台、哪個 session）。任務到終態，同一筆交易就在寄件匣（`deliveries`）寫一列，每個結果恰好一列。送回去分兩段：

- 先推（push）：Codex 走本機 app-server 的 relay turn，Claude 走 `claude -p --resume` 把結果轉述回原本那個 session。送之前先查去重標記，送過不再送。
- 推不到就等（pull）：兩邊的 UserPromptSubmit／SessionStart hook 會把這個對話的未讀結果、待決問題、還在跑的工作，注入成下一句話的脈絡。注入有上限，超出的寫「另有 N 件」，下一句話接著送。hook 一律 fail open，待辦清單壞了就安靜退出，絕不弄壞對話。

**第三層：做到一半被打斷，接著做。** 長任務的 prompt 會指定一個 checkpoint 檔，要求引擎每完成一個里程碑就整檔覆寫。正常結束、逾時被砍、排程器自己當掉，三條路都會把 checkpoint 撿回資料庫，下一次嘗試的 prompt 裡直接附上「上次做到哪，已記錄的視為做完，驗證後接著做」。誠實說：這是 prompt 層級的續作，新的一次執行是全新行程，只是開工前知道上次做到哪，不是把舊行程叫醒。

**風格斷裂怎麼辦。** 換模型接手，文風確實會變。平台不保證文風，它保證的是**驗收條件不變**：跨引擎任務登記時把成果契約（目標、逐條驗收條件、不做什麼）凍結進任務定義，單引擎任務則凍結 prompt 與參數的雜湊，換哪個模型都照同一份驗。文風要一致，靠的是你的風格規範檔，那是另一個題目。

**跨引擎訊息一律標來源。** 任何代理把提示送進另一個對話，第一行必須是人看得見的標記，例如「【跨引擎訊息｜這是 Codex 提供的指令，不是本人輸入】」。不得把代理注入的內容偽裝成使用者訊息。

## 5. 額度：搶單當下才決定用誰

- `executor: auto` **不是引擎，是「還沒決定」。** 任務帶著 auto 待在待辦清單裡，真正用誰是被搶單的那一刻才決定，依四件事：額度提示、重置時間遠近（一小時內要重置的額度算「不用白不用」）、這台電腦裝了哪些引擎、那個引擎現在排了幾件。
- **明講的永遠贏。** 寫了 `claude` 就是 claude，額度提示說什麼都不管。
- **兩邊都不能用就等**，不消耗嘗試次數、不亂挑一個去失敗。
- **額度提示是估計，不是廠商數據。** 來源只有三種：我手打的、程式觀察到的、不知道。平台不去刮任何用量頁面。
- **預算四界線。** 每件長任務都帶：整體期限、每次嘗試的回合數、每次 prompt 的大小、多久沒進度就算掛。規則檔裡有一句話：12 小時是相容性天花板，不是許可。
- **啟動前拔環境變數。** `ANTHROPIC_*`、`OPENAI_API_KEY` 這類全部從子行程環境移除，Claude Code 才會用訂閱登入，不會靜默轉成 API 計費。

## 6. 做完怎麼算做完

- **單引擎**：記錄在資料庫裡的終態加一份合法回條。對話裡的「我做完了」不算。
- **跨引擎**：交付前由**另一個引擎**做新鮮審查。審查席沒看過對話，只看產物與驗收條件，而且在隔離複本裡跑，寫不到工作區。
- **重大系統改動**：最後一版凍結同一份快照，Codex 與 Claude 各審一次，兩份都乾淨通過才發正式的 AUTHORITY。任一 blocker 修補都讓舊的審查作廢，要重審。
- **中斷續跑**：同一個 run id、作業系統持有的單實例鎖、只有雜湊對得上的回條才跳過，其餘重派。它不會悄悄開一個新 run 假裝是續作。

## 7. 踩過的雷

1. **互相看不見**（8 月 8 日）。兩小時內同一件事做兩次。修法是登記強制，不是「記得要看一下」。
2. **run 期間全域凍結**（8 月 31 日）。要並行施工，排在 run 前後，不要在 run 中間。
3. **隔離席吃掉登入憑證**（9 月 2 日）。審查席把登入憑證複製進即用即丟的複本，CLI 在裡面刷新 OAuth，新 token 寫進副本，副本清掉，本尊只剩死 token，我每天被迫重新登入。修法：清理前把輪替後的憑證寫回本尊。
4. **沒人喊的背景工作**（8 月 26 日）。一支背景工作掛住，我白等四小時十三分。修法：長工作一定登記進待辦清單，租約看門狗是唯一會自己喊的機制；十分鐘沒長大就當它死了。
5. **PATH 上的 python 是殼**（9 月 6 日）。Windows 的 Python install manager 接管了 `python` 與 `pythonw`，啟動後拿到的 PID 是殼的，不是真正跑程式的。排程與 hook 一律寫死解譯器完整路徑。

## 8. 想自己組一套：先看現成的

「Claude Code 與 Codex 共用一條本機待辦清單」已經是擁擠的賽道。我只讀過這些工具的說明，沒有逐一實測，列在這裡是省你搜尋的時間：

- [NEEDLE](https://github.com/jedarden/NEEDLE)：SQLite 待辦清單、原子搶單，headless 派給 Claude Code、Codex、OpenCode、Aider。
- [ORCH](https://github.com/oxgeneral/ORCH)：把 Claude Code、Codex、Cursor 當成一條有型別的任務待辦清單，帶狀態機。
- [Podiom](https://github.com/Podiom/Podiom)：自架控制平面，對話可以跨供應商續接。
- [Concord MCP](https://github.com/Get-Concord-AI/concord-mcp)：多代理認領工作、偵測編輯衝突、交換訊息。
- [foremerge](https://github.com/naw103/foremerge)：代理動手前先宣告意圖與範圍，規則引擎抓計畫衝突。
- [cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator)：AWS 出的，tmux 隔離多個 CLI 代理。
- [CLITrigger](https://github.com/HyperAITeam/CLITrigger)：網頁介面，每個代理各在一個 git worktree。
- 官方內建的：Claude Code 有 `--worktree`，一個 session 一個 git worktree。

清單來源：[awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)、[awesome-cli-coding-agents](https://github.com/bradagi/awesome-cli-coding-agents)。

我這套跟它們重疊的是待辦清單本身。比較少見的是三塊：只走訂閱的額度調度、結果自動回到原對話而不是另開儀表板、暫存區寫入加整波盤點加另一引擎審查。這三塊也是綁我環境最深的部分，所以我公開方法，不公開程式。

**最小可行版本**，如果你只想要兩成的效果：

1. 一張表：`tasks(id, status, claimed_by, lease_until, attempt, work_key, origin)`，放在一個 SQLite 檔。
2. 一條規則：搶單用交易加條件更新（§2 那四行）。
3. 一份宣告：每個任務寫 read／write scope，同一個檔案同一時間一個 writer。
4. 一個 hook：UserPromptSubmit 把未讀結果注入下一句話。
5. 一個 checkpoint 檔：長任務每個里程碑覆寫一次。
6. 啟動子行程前拔掉 API 金鑰環境變數。

## 9. 這份筆記不包含什麼

- **完整版程式碼。** 兩個部件合計約九萬六千行 Python、近兩千個測試，但接線長在我的 hook 設定、我的 Codex 模型路由、我的 skill 檔裡；公開出去沒有人裝得起來，硬拆出來的成本比重寫高。這個 repo 裡的 `aiq.py` 是照 `BUILD_SPEC.md` 重寫的最小版，不是從完整版拆出來的。
- **我的規則庫、語料、診所資料。** 那是我的身分和生意本身，別人拿去也沒用。

一句話：**方法公開、資料上鎖、迴圈搬不走。** 真正的護城河不是程式，是修正史加每天餵它的人。

## English abstract

Design notes, not software. I run a local, subscription-only task platform on my own Windows PC that lets Claude Code and OpenAI Codex share one working folder without stepping on each other. Three ideas carry it: (1) the control plane is a single SQLite queue, and both agents are merely executors that claim work atomically under leases, with a canonical work identity so the same job is never scheduled twice (execution itself stays honestly at-least-once); (2) every task declares read/write scopes, one writer per file at a time, writers work in an external staged copy that is promoted only after verification, and a whole-wave SHA-256 inventory fails any out-of-scope change; (3) results are pushed back into the originating chat session, with a hook-based pull as the fallback, so the chat stays the only front end. Engine choice for `auto` tasks happens at claim time from capacity hints and reset times; API-key environment variables are stripped so both CLIs run on their subscriptions. The full platform is welded to my environment and is not published; the method, the lessons, a list of comparable open-source tools, an AI-readable build spec (`BUILD_SPEC.md`) and a minimal single-file reference implementation (`aiq.py`) are.

## 授權

文字採 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.zh-hant)：可以轉載、改寫、拿去教，註明出處就好。程式（`aiq.py` 與 `tests/`）採 MIT，細節在 `LICENSE`。
