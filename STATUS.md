# 測試與進度報告

這份報告記錄目前 `dsh-hub`（GitHub：`drill-research-lab/dsh-hub`，本機路徑仍是 `new-DSH`）相對於實驗室內網系統規劃文件的完成度，哪些項目已經**用真實環境驗證過**、哪些**因為環境限制暫時擱置**、哪些**還沒開始**。之後每完成一段新工作，會回來更新這份報告，不會另外散落成多份文件。

最後更新：2026-09-12（**接進實驗室既有的 AdGuard + Caddy 反向代理、真實網域名稱 `dsh-hub.islab.local` 完整驗證通過**：AdGuard DNS rewrite、內部 CA 簽的憑證、Caddyfile 反代設定、Hub 本身四層都串起來，`curl https://dsh-hub.islab.local/hub/login` 拿到真實的 islab 品牌登入頁 HTML；過程中排查出兩個真實部署坑——Caddyfile 裡的 `reverse_proxy` 目標 IP 複製貼上忘了換成實際值（導致 502）、`docker port` 用來確認 Hub 實際綁定位址是排查這類問題最可靠的方法；**正式部署到 Proxmox VM 完成**：`ops/spark-lockdown/` 三支腳本、`ops/disk-quota/apply-disk-quotas.sh` 都在真實環境跑過，前者完整驗證通過，後者只差等第一個真人使用者出現才能補完最後一小段；部署過程中抓到並修好一個真實 bug（兩支腳本猜容器名稱字首猜錯，改用 compose label 查找）；這個 repo 正式建到 GitHub、接上 CI（單元測試 + 四個 Dockerfile build 檢查，都在真正的 GitHub Actions 上跑過綠燈）；磁碟配額補完 `disk_mb` 這個維度；第三節「CPU/記憶體限制」完整實作並實測完成——新容器建立時套用預設值、管理員可對已在跑的容器即時 `docker update`；排隊位置提示列的視覺樣式改用從 dsh 實際頁面（真實瀏覽器 `getComputedStyle`）量到的色票／字型／圓角重做，取代原本自己瞎猜的等寬字＋青綠色方案；之前已完成：dsh 網頁內嵌排隊位置提示列；介面重新設計：Hub 全站 + Panel 換成 islab／應用密碼與資訊安全實驗室品牌，不再出現 JupyterHub 字樣；第六節：dsh 預設 provider/模型接好，過程中修好五個 bug——帳號格式檢查、Panel session 隨重啟失效、Dispatcher 不支援 streaming、API key 核發邏輯判斷依據錯誤、以及兩次手動修 Redis 資料造成的意外副作用）。

## 這次測試環境的重要背景

這個 sandbox 原本沒有 Docker（WSL 沒接上 Docker Desktop），使用者開通後才能實際跑 `docker compose`。開通後意外發現：這個環境同時可以直接連到 `ldap-server.islab.local:636`（真實 LLDAP）與 `192.168.101.70:8888`（真實 Spark vLLM 端點）。這代表這次驗證**不是模擬**，而是對正式環境資源做的真實煙霧測試（唯一動到的是我自己建立、測完即刪除的測試容器/volume，沒有動到既有資料）。

沒有、也不會在這個 sandbox 裡取得的東西：Proxmox 主機本身、VM 的實際磁碟（Docker Desktop 用的是它自己的虛擬磁碟，不是文件裡規劃的 XFS 獨立硬碟）、任何真人使用者的登入密碼。這些邊界決定了下面哪些項目只能列為「擱置」而不是「已測試」。

---

## 一、登入與容器管理

| 項目 | 狀態 | 說明 |
|---|---|---|
| JupyterHub + DockerSpawner 架構 | ✅ 已完成、已實測 | 走完整真實流程：瀏覽器登入 → spawn container → reverse proxy 導到使用者頁面，全部成功。 |
| LDAP 登入（lldap） | ✅ 已完成、已實測（過程中抓到並修好一個擋掉真實使用者登入的 bug，見下） | 用服務帳號 `uid=islab` 直接呼叫 `LDAPAuthenticator.authenticate()`：正確密碼回傳合法 auth model，錯誤密碼回傳 `None`（被拒絕）。也對 `ou=people,dc=islab,dc=local` 做真實搜尋，撈到 39 筆使用者。 |
| 管理員群組判斷（`post_auth_hook`） | ✅ 已完成、已實測（過程中抓到並修好一個降級 bug，見下） | 用真實登入 + 真實 LDAP 群組資料驗證：服務帳號暫時被指定為 `lldap_strict_readonly` 成員時，登入後 Hub 判定 `admin: True`；把 `LDAP_ADMIN_GROUP` 改回空值、重新登入後，`admin` 正確變回 `False`。目前 `.env` 的 `LDAP_ADMIN_GROUP` 刻意留空（沒有人是管理員）——**這是設計好的入口，不是漏測**，等你在 LLDAP 建好實際管理員群組後把 DN 填進去即可生效。 |
| 容器持久化（`remove=False` + 具名 volume + `cleanup_servers=False`） | ✅ 已完成、已實測 | 真實 spawn 後檢查：`/home/demo` 確實掛在具名 volume `dsh-demo-home-<帳號>` 上，容器帶著 `cap_drop=ALL` 也能正常建立/啟動/停止/刪除。 |
| dsh 自動導向 + URL/WebSocket 改寫 | ✅ 已完成、已實測 | 真實走完 OAuth 流程後，`GET /user/<帳號>/harness/` 回 200，內容確認 `harness_bridge.js` 有被正確注入、`prefix` 值正確算成 `/user/islab/harness`。 |

### 新發現並修好的 bug：真實帳號（數字開頭）完全無法登入

**現象**：你自己用真實帳號登入時，Hub 和 Panel 兩個網頁都進不去。

**原因**：`jupyterhub-ldapauthenticator` 套件自帶一個「使用者名稱格式檢查」（`valid_username_regex`），預設值 `^[a-z][.a-z0-9_-]*$` 要求帳號必須以小寫字母開頭，在真正嘗試 LDAP 驗證之前就先擋掉。但這個 LLDAP 的真實帳號是學號/職員編號格式（例如 `61447007s`），以數字開頭，完全不符合這個預設格式，所以每次登入都直接被拒絕，連 LDAP 都還沒查就結束了。log 裡明確寫 `Illegal characters in username`。這個 bug 影響範圍很大——不只你，這個機構絕大多數帳號應該都是這種格式，沒修的話等於幾乎沒人登得進去。

**修法**（[hub/jupyterhub_config.py](hub/jupyterhub_config.py)）：把 `c.LDAPAuthenticator.valid_username_regex` 放寬成 `^[a-zA-Z0-9][.a-zA-Z0-9_-]*$`，允許數字開頭，但還是擋掉真正危險的字元（分號、括號、引號等 LDAP injection 字元）。這個檢查原本主要是防注入用的，但 ldap3 從 2.0 版起已經會自動 escape 特殊字元，所以現在主要是輸入健檢，放寬起始字元不影響原本的安全目的。

**已驗證**：用正則表達式直接測過 `61447007s`、`41173058h`、`islab` 都能通過，同時確認 `a;droptable` 這種明顯異常輸入依然被擋。沒有拿你的真實密碼幫你代測完整登入（我沒有你的密碼），修法部署後請你自己重新整理再試一次。

### 新發現並修好的 bug：Panel 重啟後，正在登入的 session 會斷（OAuth state 對不上）

**現象**：你成功登入 Hub 後進到 Panel，畫面顯示「Not allowed.」，佇列是空的；log 裡看到 `oauth state does not match. Try logging in again.`。

**原因**：`panel/app.py` 原本用 `os.urandom(32)` 當 Tornado 的 `cookie_secret`，代表**每次 panel 容器重啟，這個密鑰就換一把**，所有之前簽發的 cookie（包含 session cookie、OAuth state cookie）全部失效。今天測試過程中 panel 容器被我重建/重啟了很多次，你在某次重啟前後跨越著操作，瀏覽器裡混到用舊密鑰簽的 cookie，OAuth 交換 code 時拿新舊不一致的 state 去比對，於是對不上、整個登入流程失敗，連帶 `/api/queue` 這類需要登入的 API 都會回 403，畫面上顯示成容易誤解的「Not allowed.」——這句話原本設計只會在「服務層級權限被拒絕」時出現，不是「一般使用者」的正常狀態；一般非管理員登入成功應該是看到「Read-only view.」而不是「Not allowed.」，所以這確實是個異常訊號，不是你誤解了唯讀設計。

**修法**（[panel/app.py](panel/app.py)）：`cookie_secret` 改成從 `PANEL_API_TOKEN`（透過 `JUPYTERHUB_API_TOKEN` 環境變數）用 SHA-256 衍生出來，只要這個 token 沒換，容器重啟幾次密鑰都一樣，不會再無故把人踢出登入狀態。

**已驗證**：登入一次、確認 `/api/queue` 正常回應 → 重啟 panel 容器（模擬真實重新部署）→ 用同一個瀏覽器 session 再打一次，**不用重新登入**依然正常回應，證實修法解決了「重啟就斷線」的問題。

### 既有 bug：首次載入 404（✅ 已修好、已用真實 spawn 重新驗證）

**現象**（修之前）：容器剛啟動、`dsh web` 還在跟本地端交換 token/cookie 的頭幾秒內，第一次打 `/user/<帳號>/harness/` 會收到 **404**，過幾秒重試就變成正常的 200。實測時真實重現兩次 404 後第三次才成功。

**原因**：`singleuser/harness_proxy.py` 的 `request_headers()` 會讀 `/tmp/dsh-proxy-{port}.cookie` 這個檔案來組 `Cookie` header 轉發給後端 dsh；但這個檔案要等 `singleuser/start_harness.py` 抓到 dsh 印出的 token URL、完成一次本地端交換後才會被寫入。在這個檔案還不存在的空窗期，request_headers 回傳空字串 Cookie，請求就這樣被轉發給尚未認得這個 session 的 dsh，於是 404。`jupyter_server_proxy` 本身的「process 就緒」判斷（`_http_ready_func`）只檢查 port 有沒有回應任何內容，跟 cookie 有沒有交換完全無關，所以它不會幫忙擋下這個空窗期的請求。

**修法**（[singleuser/harness_proxy.py](singleuser/harness_proxy.py)）：`request_headers()` 改成輪詢等待 cookie 檔案出現非空內容（每 100ms 檢查一次，最多等 15 秒），才組出 Cookie header；15 秒後還沒等到就照舊放行空 Cookie，不會無限卡住。`jupyter_server_proxy` 的 `request_headers_override` 是同步呼叫、不支援 async/await，所以這個等待會在等待期間卡住當下這個使用者自己的單人 Jupyter Server（不影響其他使用者），但只發生在該使用者自己這次 harness 程序剛啟動的空窗期，之後每次請求 cookie 檔案早就存在，完全不會等。

**已用真實環境重新驗證**：砍掉舊的測試 container 重新走一次「真實登入 → spawn → 立刻打 `/harness/`」，第一次請求變成等待 10.38 秒後拿到 **200**（不再是 404），且回傳內容確認是正確的 harness 頁面；第二次請求（cookie 已存在）幾乎瞬間回應 200。也補了 3 個對應的單元測試（`tests/test_harness_proxy.py` 的 `RequestHeadersTest`）鎖住這個行為：cookie 已存在時immediately 回傳、cookie 延遲寫入時會等到才回傳、cookie 永遠不出現時會在時限內放棄而不是卡死測試。

**使用者可感受到的差異**：第一次點進工作區，畫面會多轉個幾秒圈圈（等 dsh 啟動完成），但不會再看到 404 需要手動重新整理——用等待換掉了失敗。

### 新發現並修好的 bug：管理員被移出群組後不會自動降級

**現象**：在測試第四節 Admin Panel 時，先暫時把 `LDAP_ADMIN_GROUP` 設成服務帳號所屬的一個真實群組、重新登入確認 `admin: True`，接著把 `LDAP_ADMIN_GROUP` 改回空值、重啟 Hub、重新登入——結果 `admin` **還是 `True`**，沒有跟著降級。

**原因**：`_admin_group_post_auth_hook` 原本在「沒有設定 `LDAP_ADMIN_GROUP`」或「LDAP 查詢失敗」的分支都是直接 `return auth_model`，完全沒有寫入 `auth_model["admin"]`。JupyterHub 只有在 hook 回傳的值「非 `None`」時才會覆寫資料庫裡持久化的 `user.admin` 欄位（`jupyterhub/handlers/base.py` 的 `auth_to_user()`）；hook 沒有明確設定該欄位時，JupyterHub 就完全不碰它，等於保留上一次登入時留下的舊值，而不是重新計算。

**修法**（[hub/jupyterhub_config.py](hub/jupyterhub_config.py)）：hook 的每一個分支都明確設定 `auth_model["admin"] = True` 或 `False`，不再有「什麼都不設」的路徑——群組沒設定、LDAP 查詢失敗、使用者查無 DN，這些情況現在都明確設成 `False`（fail closed），而不是維持舊狀態。

**已重新驗證**：修好後用同一個帳號重新走一次「設定群組 → 登入拿到 admin:True → 清空群組 → 重啟 Hub → 重新登入拿到 admin:False」的完整流程，結果正確。這個 bug 如果沒抓到，會讓「群組成員異動、Hub 端自動跟著變」這個設計文件明確要求的行為失效——一旦有人被升過一次管理員，之後從群組移除也不會真的失去權限，直到資料庫被手動改掉為止。

---

## 二、Container 安全性 Hardening

| 項目 | 狀態 | 說明 |
|---|---|---|
| Docker socket proxy | ✅ 已完成、已實測 | `docker-socket-proxy` 服務與獨立 `docker-api`（`internal: true`）網路已建立。實測 `GET /networks` 回 403（正確擋掉未授權端點），`version`/`ping`/`containers`/`images`/`volumes` 皆可用。 |
| Docker API 白名單涵蓋度 | ✅ 已完成、已實測 | 透過 proxy 完整跑一次 `create_volume → create_container(帶 cap_drop/security_opt/volume) → start → inspect_container → inspect_image → stop → remove_container → remove_volume`，全部成功，證明白名單剛好覆蓋 DockerSpawner 實際會用到的 API，沒有多開也沒有少開。 |
| Capabilities（`cap-drop=ALL` + `no-new-privileges`） | ✅ 已完成、已實測 | 用真實 container 跑完整條生產路徑（`python3 /opt/demo/start_harness.py` → 啟動 `dsh web` → 完成 cookie 交換 → 印出 "DeepSeek Harness ready behind JupyterHub authentication"），全程零 `Operation not permitted`/`permission denied`。原因：Dockerfile 本來就用 `USER demo` 固定跑非 root，執行期沒有任何需要 root 再降權的步驟，所以 `cap-drop=ALL` 本來就不會踩到任何東西。**`cap_add` 目前不需要加任何項目。** |

---

## 三、Container 資源限制（CPU / 記憶體 / 磁碟）

| 項目 | 狀態 | 說明 |
|---|---|---|
| CPU / 記憶體（`docker update --cpus/--memory`） | ✅ 已完成、已實測 | 見下方完整說明。 |
| 磁碟配額（XFS project quota） | 🟡 已寫完，Redis/API 那半已實測，`xfs_quota` 那半沒測過 | 見下方完整說明。 |

### 磁碟配額：做了什麼、測了什麼、沒測什麼

`common/resource_limits.py` 的 `ResourceLimitStore` 擴充第三個維度 `disk_mb`（跟 `cpu`/`memory_mb` 同一份資料結構、同一組驗證/預設值機制），Panel 的 `PATCH .../resource-limits/<user>` 也跟著能收 `disk_mb`（可選欄位，不帶就維持原值）。

**跟 CPU/記憶體關鍵的不同**：CPU/記憶體有 Docker API 可以呼叫（`docker update`），disk 沒有——XFS project quota 是主機檔案系統層級的機制，Docker 完全不知道這件事，`docker-socket-proxy` 也沒有對應的端點可以轉發。所以 Panel（跑在 container 裡，沒有主機 root）**沒辦法「立即套用」磁碟配額**，PATCH 端點的回應會標記 `disk_quota_pending: true`，只把想要的值記進 Redis。真正的套用要靠新增的 [ops/disk-quota/apply-disk-quotas.sh](ops/disk-quota/apply-disk-quotas.sh)：這支腳本在主機上以 root 執行，列出所有 `dsh-demo-home-*` volume、查出各自的真實掛載路徑、用 `docker exec` 進 Hub 容器讀出 Redis 裡該使用者的 `disk_mb`（重用 `ResourceLimitStore.get()`，跟 Hub/Panel 讀的是同一份邏輯，不是自己重算一次），然後對主機的 XFS 檔案系統下 `xfs_quota` 指令設定 project quota。設計上預期用 systemd timer/cron 定期跑（README 有寫），不是即時的。

**已用真實環境測過的部分**（跟 CPU/記憶體同一輪，暫時把 islab 設成管理員測完復原）：
1. Panel 的 PATCH 端點正確收下 `disk_mb`、存進 Redis、回應正確標記 `disk_quota_pending: true`（不是誤導成「已套用」）。
2. 不帶 `disk_mb` 的 PATCH（只改 cpu/memory）正確保留原本的 `disk_mb` 不變——驗證過先設 20480、再打一次不帶 disk_mb 的 PATCH，讀回來確認還是 20480。
3. 超出範圍（1024~102400 MB）正確擋 400。
4. `apply-disk-quotas.sh` 唯一「能在這裡測」的部分——它讀 Redis 用的那段 Python（`docker exec` 進 Hub 容器、`ResourceLimitStore.get(user)['disk_mb']`）——直接照腳本裡一模一樣的程式碼跑過一次，確認能正確讀回剛才存的 20480。

**後續：正式 Proxmox VM 建好後，已經進一步驗證過（不再是純語法檢查）**：真的把資料碟格式化成 XFS、掛上 `prjquota`（`df -hT /var/lib/docker` 確認是 `xfs`），在上面跑 `sudo ./apply-disk-quotas.sh`，乾淨執行完畢、印出 `Done: 0 applied, 0 skipped.`，沒有任何錯誤——證明腳本裡查容器、進 Hub 容器讀 Redis 那整段管線（`docker ps` label 篩選、`docker exec`、`ResourceLimitStore.get()`）在真實環境下是通的。

**還差最後一小塊**：因為驗證當下還沒有真人登入、沒有任何 `dsh-demo-home-<帳號>` volume，迴圈本體實際下 `xfs_quota -x -c 'project ...'`/`limit -p bhard=...` 那兩行**一次都還沒真的執行過**（沒有東西可以疊代）。等有第一個真人登入、產生第一個使用者 volume 之後，需要再跑一次這支腳本、並用 `sudo xfs_quota -x -c 'report -p' /var/lib/docker` 確認配額真的套用上去，才算完全補完。同一輪也把[ops/spark-lockdown/](ops/spark-lockdown/) 三支腳本裡「猜容器名稱字首」的 bug 一起修掉了（原本寫死 `new-dsh-` 這個本機開發環境的字首，真實部署目錄叫 `dsh-hub`、容器名稱對不起來，會直接抓不到），改成用 `com.docker.compose.service` label 查找，兩支腳本都同步套用同一個修法，且都已經驗證過。
| 統一開關 API（`PATCH /users/{id}/resource-limits`） | ✅ 已完成、已實測 | 見下方完整說明。 |

### CPU / 記憶體限制：完整實作與實測

新增 [common/resource_limits.py](common/resource_limits.py)（`ResourceLimitStore`，Hub 跟 Panel 共用，跟 `api_keys.py`/`dispatch_queue.py` 同一種 Redis-backed 共用模組的設計）。跟併發數（第五節）同一個思路：限制值存在 Redis，不是寫死在設定檔或容器建立瞬間就定死——這樣管理員改了之後，不需要重建容器就能生效。

**兩條分開但互補的路徑**（因為 Docker 的限制天生分兩種情境）：
1. **全新建立的容器**：[hub/jupyterhub_config.py](hub/jupyterhub_config.py) 的 `pre_spawn_hook`（就是原本已經在核發 Dispatcher API key 的那個 hook，這次擴充）在容器真的要被建立之前，去 Redis 查這個使用者目前的限制值（沒設過就用 `.env` 的 `DEFAULT_CPU_CORES`/`DEFAULT_MEMORY_MB`），設進 `spawner.mem_limit`/`spawner.cpu_limit`——這兩個是 `dockerspawner` 套件原生就支援的 trait（查過套件原始碼確認：`mem_limit` 接受 `"4096M"` 這種字串、`cpu_limit` 接受浮點數核心數，套件自己會換算成 Docker 的 `CpuQuota`/`CpuPeriod`），不是我們自己刻的機制。
2. **已經在跑的容器**：环境變數和 host_config 本來就是建立瞬間定死的（跟第七節「重啟不會拿到新 key」是同一個 Docker 限制），管理員想改一個「已經在跑」的使用者的限制，必須直接對那個容器下 `docker update`——這條路徑經 Panel 完成：新增 `PATCH /services/panel/api/resource-limits/<user>`（管理員專用），存進 Redis 之後，直接組出 `docker update` 要的 JSON body（`CpuQuota`/`CpuPeriod`/`Memory`/`MemorySwap`），透過 Hub 原本就在用的同一個 `docker-socket-proxy` 打過去，容器不用重建就立即套用新的限制。使用者本人也能在 Panel 看到「你的容器目前限制」（唯讀）。

**架構筆記**：Panel 原本沒有 Docker 存取權（只有 Hub 有，走 `docker-api` 這個獨立網路 + `docker-socket-proxy`），這次讓 Panel 也加入 `docker-api` 網路、拿到同一個 proxy 的存取權——跟 Dispatcher/Hub 各自負責一塊（Hub 管 spawn、Dispatcher 管模型呼叫、Panel 管理員操作面）的既有分工一致，Panel 本來就已經是「管理員操作的入口」，這次只是多開一種它能做的操作，`docker-socket-proxy` 的白名單本身沒有放寬（一樣只開 `CONTAINERS`/`POST`，不是給 Panel 開了什麼新的 Docker API 權限，只是多一個 caller 用同一組既有白名單）。

**已用真實環境完整驗證**（過程：暫時把 `LDAP_ADMIN_GROUP` 指向 islab 所屬的真實群組來測管理員路徑，測完照樣改回空值、重啟 Hub 復原，跟第四節用的是同一套暫時授權再復原的做法）：
1. **docker-socket-proxy 是否放行 `/containers/{id}/update`**：現有白名單沒有明講這個端點，先用一個丟棄式測試容器直接對 proxy 送真實請求確認——回應是 Docker 自己的業務邏輯錯誤（`Memory limit should be smaller than already set memoryswap limit`），不是 proxy 的 403，證明端點本身是放行的；補上 `MemorySwap` 後重試拿到 200，`docker inspect` 確認真的套用了。
2. **全新 spawn 是否真的套用預設值**：這次測試過程中 islab 剛好觸發了一次全新的容器建立（`dsh-demo-islab`，建立時間跟測試時間吻合），直接 `docker inspect` 確認 `CpuQuota=200000`（= 2.0 核 × 100000）、`Memory=4294967296`（= 4096 MB）—— 剛好就是 `.env` 裡的預設值，證明 `pre_spawn_hook` 到 `dockerspawner` trait 這條路徑是通的。
3. **Panel 的 PATCH 端點，用真實管理員 session 完整走一次**：真實登入 islab（拿到 `admin: True`）→ 對一個丟棄式測試容器（`dsh-demo-resource-test-user`）呼叫 `PATCH .../resource-limits/resource-test-user`（`cpu: 1.5, memory_mb: 2048`）→ 回應 `applied_live: true` → `docker inspect` 確認容器真的變成 `CpuQuota=150000, Memory=2147483648`（2GiB）→ Redis 裡的紀錄跟審計 log 都正確寫入，`actor` 正確是 `islab`。
4. **邊界情況**：對一個沒有容器在跑的使用者呼叫 PATCH，正確回應 `applied_live: false`（不會噴錯，只是記錄下來、等下次 spawn 生效）；`cpu` 超出設定的範圍（0.25~8 核）正確被擋 400，帶明確錯誤訊息。
5. **自助查詢**：任何登入者呼叫 `GET api/resource-limits`（不帶參數）能看到自己目前的有效限制（含是否為預設值）；`?all=1` 只有管理員能用，列出所有「被管理員設過自訂值」的使用者（不是列出所有 LDAP 帳號，避免這個清單無限膨脹）。
6. **測完清理**：丟棄式測試容器直接砍掉，測試用的兩筆 Redis 限制紀錄用精準的 `DEL`/`SREM` 刪除（不是 `FLUSHDB`），`.env` 的 `LDAP_ADMIN_GROUP` 改回空值並重啟 Hub 復原、驗證 islab 的 `admin` 正確變回 `False`。全程沒有動到真人帳號 `61447007s` 的真實容器（`docker ps` 確認 uptime 全程沒中斷）。

---

## 四、Admin Panel

新增 `panel/` 服務：Tornado app，註冊成 JupyterHub Service（`hub/jupyterhub_config.py` 的 `c.JupyterHub.services`），走 Hub OAuth SSO，掛在 `/services/panel/`。

| 項目 | 狀態 | 說明 |
|---|---|---|
| SSO 登入（Hub OAuth） | ✅ 已完成、已實測 | 用 `jupyterhub.services.auth.HubOAuthenticated` + `HubOAuthCallbackHandler`（JupyterHub 官方提供、給 Tornado 用的現成 mixin，不是自己刻的 OAuth）。真實走過一次登入 → `/services/panel/` → 拿到 200 頁面。中間卡過兩個真實的設定坑，都已修好，見下。 |
| 一般使用者唯讀 | ✅ 已完成、已實測 | 非 admin 帳號打 `/api/reorder`、`/api/audit`、`/api/concurrency` 全部正確回 403；`/api/queue` 正常回傳但 `admin: false`。 |
| 管理員拖拉排序 + 併發數調整 | ✅ 已完成、已實測 | 見下方「完整流程實測」。前端排序用原生 HTML5 drag-and-drop（`panel/app.py` 內嵌的 vanilla JS，沒有另外引入前端框架）。 |
| 審計 log | ✅ 已完成、已實測 | 每次 reorder / 併發數變更都寫進 `dispatcher:audit`（Redis list），記錄 actor、action、時間、細節；`/api/audit` 只有 admin 能讀。 |
| 排隊狀況不做成 dsh plugin | ✅ 符合設計 | Panel 完全獨立於 dsh，只透過 Redis 讀寫佇列狀態，不碰 dsh 內部任何東西。 |

**架構筆記（跟文件字面描述不同、但有理由的判斷）**：文件說「Panel、Dispatcher 建議註冊成 JupyterHub 的 Service」，這裡只把 **Panel** 註冊成 Hub Service，**Dispatcher 沒有**。原因：Panel 是給人看的網頁，需要 Hub 的 SSO/OAuth；Dispatcher 是給程式（使用者 container 裡的 dsh）打的 API，本來就該直接連（文件六也是這樣寫 `baseURL: http://dispatcher.internal:PORT/v1`），硬套 Hub OAuth 對一個機器對機器的 API 沒有意義，反而是不必要的複雜度。Panel 和 Dispatcher 都不直接碰彼此，共用同一個 Redis 當作真相來源——Dispatcher 只從佇列尾端 pop、Panel 只重排/讀取佇列，兩者互不衝突。

**踩過的兩個真實設定坑（都已修好）**：
1. `oauth2/authorize` 一直 403「not allowed to access JupyterHub service panel」——JupyterHub 2.0 的 RBAC 預設 `user` 角色只有 `self` scope，不包含存取任何 service 的權限。修法：`c.JupyterHub.load_roles` 幫 `user` 角色加上 `access:services!service=panel`（順便保留 `self`，因為 `load_roles` 是整份取代 scope 清單，不是合併）。
2. 加上權限後卡在 Hub 的「Authorize access」確認頁，用程式化的 `requests` 打不過去——這是 JupyterHub 對一般 OAuth client 的預設行為。因為 Panel 是這個部署自己的一部分、不是第三方 app，設定 `oauth_no_confirm: True` 跳過這個確認頁，login 後直接可用。

**完整流程實測**（用服務帳號 `islab`，暫時把 `LDAP_ADMIN_GROUP` 指向它所屬的一個真實群組來測 admin 路徑，測完立刻改回空值並重啟 Hub 復原，見上面的降級 bug 記錄）：
- 3 個真實請求丟進 Dispatcher，0.4 秒後查 `/api/queue`：正確看到 2 筆 `status: queued`（第 3 筆已經被 worker 撈走變成 `running`，正確地沒有出現在可重排清單裡）。
- 呼叫 `/api/reorder` 把這兩筆順序對調 → 200，回傳新順序正確。
- 呼叫 `/api/concurrency` 把併發數從 1 改成 2 → 200，Dispatcher 立即套用（不用重啟），改完再測試性地改回 1。
- 3 個背景請求全部跑完後查 `/api/audit`：正確依序記錄 `reorder`、`set_concurrency(2)`、`set_concurrency(1)`，actor 都正確是 `islab`。

---

## 五、模型呼叫架構（排隊 / Dispatcher）

新增 `dispatcher/` 服務（Tornado app）+ `common/dispatch_queue.py`（Panel 與 Dispatcher 共用的 Redis 佇列邏輯）+ `redis` compose 服務（`redis:7-alpine`，開 `--appendonly yes` 做持久化，對應文件第八節「Redis 單點故障」風險裡要求的持久化設定）。

| 項目 | 狀態 | 說明 |
|---|---|---|
| Spark vLLM 端點連通性 | ✅ 已確認可連 | `192.168.101.70:8888` 從這個環境可直接連通，`GET /v1/models` 回傳 `deepseek-v4-flash-0731`，`max_model_len: 384000`，確認就是文件說的正式端點，不是模擬。 |
| OpenAI 相容轉發（`/v1/chat/completions`） | ✅ 已完成、已實測 | 真實丟一個請求給 Dispatcher，Dispatcher 排進佇列、轉發給 Spark、拿到真實模型回應（帶 `reasoning` 欄位）並原樣回傳給呼叫端，全程 0.64 秒。 |
| FIFO 排隊語意 | ✅ 已完成、已實測 | 併發數設 1 時丟 3 個真實請求進去，用 Redis 裡記的 `started_at`/`finished_at` 直接檢查：三筆完全不重疊，後一筆的 `started_at` 都晚於前一筆的 `finished_at`（誤差在 0.08~0.1 秒的 worker loop 輪詢間隔內），確認嚴格序列化。 |
| 併發數可即時調整（不用重開 Dispatcher） | ✅ 已完成、已實測 | 併發數不是寫死的常數，是每次 worker loop 要撈下一筆前都重新讀 Redis 的 `dispatcher:concurrency`。實測把它從 1 改成 2 後，兩個真實請求的 `started_at` 只差 0.0007 秒、執行區間完全重疊，證實真的併發執行；改完馬上又測過改回 1 會恢復嚴格序列化。 |
| Admin 重排不能拉回已送出的請求 | ✅ 已完成、已實測（設計 + 真實 Redis race 測試雙重驗證） | 用 `WATCH`/`MULTI`/`EXEC` 樂觀鎖重寫佇列順序：若 admin 送出重排的同時，某筆請求已經被 Dispatcher `LPOP` 走，重排結果會自動不包含那一筆（不會被拉回佇列），已經在 [tests/test_dispatch_queue.py](tests/test_dispatch_queue.py) 的 `test_reorder_cannot_recall_an_already_dequeued_item` 鎖住這個行為，也在裸 Redis 上手動注入過真實的併發修改來驗證重試邏輯正確（見下方「怎麼驗證的」）。 |
| 身份歸屬 | ✅ 已完成、已實測 | **已改用第七節的真實 API key 機制**（見下），不再信任任何自報的 header。identity 一律從「這把 key 實際是誰的」反查得到，使用者自己的 container 沒辦法謊報成別人。 |

**怎麼驗證的**：
- `common/dispatch_queue.py` 的 reorder 演算法先用一支獨立腳本對著真實 Redis container 測過，特別測了「重排進行到一半時，另一個連線把佇列最前面的項目 `LPOP` 掉」這個真實併發場景，確認 `WatchError` 重試邏輯會抓到衝突、重算一次、最後結果正確（用了 2 次嘗試）。
- `tests/test_dispatch_queue.py`（`IsolatedAsyncioTestCase`，共 6 個測試：FIFO、reorder、reorder 不能拉回已送出項目、reorder 不會漏掉客端沒列出的項目、併發數種子值與可設定、審計 log）在有 `redis` 套件跟真實 Redis 可連時全過；這個 repo 的 `.venv` 本身沒有 pip、裝不了 `redis` 套件，所以在 `python -m unittest discover -s tests` 裡會顯示 `skipped`，是預期行為，不是漏測——我另外用一個裝了 `redis` 套件、接到真實 Redis container 的一次性容器完整跑過這 6 個測試，全部通過。
- 完整壓力測試：3 個真實 model 請求同時丟進去，中途邊跑邊查 panel 的即時佇列畫面、邊做 reorder、邊調併發數，最後核對 Redis 裡每筆請求的 `started_at`/`finished_at` 時間戳記，行為完全符合預期（見第四節「完整流程實測」）。

---

## 六、防止繞過排隊系統

| 項目 | 狀態 | 說明 |
|---|---|---|
| settings.yaml 多 provider 並存設計 | ✅ 已完成、已實測 | 見下方完整說明——這是這次真正做的部分。 |
| 防火牆雙開關腳本（鎖死 Spark 只准 Dispatcher 連） | ⬜ 尚未開始，但**判斷已更正**：不一定需要 Spark 主機權限，見下 | 目前 Dispatcher 和使用者 container 都在同一個 `dsh-demo` 網路上，沒有防火牆規則擋著使用者 container 直接打 Spark 原生端點——現在還是能繞過。 |

**架構判斷更正（這次跟你討論後釐清，還沒動手實作）**：原本以為這段一定要有 Spark 主機本身的存取權限才能做，跟第三節磁碟配額同類——重新查了一次網路拓樸後發現這個假設是錯的。`dispatcher` 跟所有使用者 container（`dsh-demo-<帳號>`）現在是同一個 Docker bridge 網路（`dsh-demo`，`172.20.0.0/16`），對外連到 Spark（`192.168.101.70`）時都會經過這台 Docker host 的 NAT，在 Spark 那端看起來是同一個來源 IP——**代表在 Spark 主機那邊做防火牆，沒辦法用來源 IP 分辨「這是 Dispatcher 的請求」還是「使用者 container 自己繞過去的請求」，兩者外觀一樣**。真正能分辨兩者的地方，是這台 Docker host 自己：封包被 NAT 之前，Dispatcher（`172.20.0.2`）跟各個使用者 container 的內部 IP 不同，可以在這裡用 `iptables`（`DOCKER-USER` chain，Docker 官方文件建議的自訂規則掛載點，不會被 Docker 自己的動態規則覆蓋掉）精準擋掉「使用者 container 直接打 Spark 的 IP:port」、只放行 Dispatcher 的內部 IP。

也就是說：**達成「防止繞過排隊系統」這個目標，不需要 Spark 主機權限，該做的地方是這台 Docker host。** Spark 主機那邊頂多是「多一層保險」（只允許這台 Docker host 的 LAN IP 連進 8888），但那個粒度分辨不出 Dispatcher 跟使用者 container，不是必要的第一道防線。

**原本以為可以在這裡直接寫直接測，後來查證發現也不行**：這個判斷斷再更正一次——查了才發現這個 sandbox 其實是 Docker Desktop 的 WSL2 整合環境，這個 shell 裡沒有 root（`sudo` 需要密碼、進不去），而且真正的 `dockerd`、真正的 `dsh-demo` bridge 網路，其實跑在 Docker Desktop 自己另一個獨立的 VM 裡（`docker context ls` 顯示有一個透過 Windows named pipe 連的 `desktop-linux` context；這個 WSL distro 本身找不到 `dockerd` 行程、也看不到 `dsh-demo` 的 bridge 網路介面）——代表就算有 root，在這裡下的 `iptables` 規則也不會作用在真正的容器流量上，兩者根本不在同一個網路 namespace。這點我一開始沒查證就講「這裡能測」，是錯的，後來自己發現並更正。

**做法**：[ops/spark-lockdown/](ops/spark-lockdown/) 三支腳本（`spark-lockdown-enable.sh` / `disable.sh` / `status.sh`），用 `DOCKER-USER` chain（Docker 官方文件建議的自訂規則掛載點，不會被 Docker 自己動態管理的規則洗掉）：先動態查出 Dispatcher 容器在 `dsh-demo` 網路上目前的 IP，插入一條「只允許這個 IP 打 Spark」的 ACCEPT 規則，再插入一條「整個 `dsh-demo` 子網段打 Spark 一律 DROP」的規則（順序很重要，ACCEPT 必須排在 DROP 前面）。`enable` 是冪等的（每次先呼叫 `disable --quiet` 清掉舊規則再重插，處理 Dispatcher 容器重建後 IP 換掉的情況）。

**✅ 已在正式 Proxmox VM 上用真實環境完整驗證過**（不再是「寫了沒測」）：
1. `sudo ./spark-lockdown-enable.sh` 在真正的原生 `dockerd` + root 權限下執行成功，`status.sh` 顯示規則正確（`ACCEPT` 排在 `DROP` 前面，只放行 Dispatcher 當時的 IP）。
2. 從一個丟棄式測試 container（跟使用者 container 同一個 `dsh-demo` 網路）直接打 Spark：`curl: (28) Connection timed out`——確認真的被擋。
3. 從 Dispatcher 容器本身打 Spark：拿到真實的 `200`——確認沒有誤傷合法流量。
4. 整套堆疊重建（`docker compose up -d --build`）後 Dispatcher 換了新 IP，重跑一次 `enable.sh`：正確清掉舊規則、插入新 IP 的規則（`status.sh` 顯示剛好 2 條，不是疊加成 4 條），證明冪等重跑邏輯是對的。
5. 過程中抓到並修好一個真實 bug：容器名稱解析邏輯原本猜的字首（`new-dsh-`，來自我開發時的本機環境）在真實部署（目錄叫 `dsh-hub`，容器變成 `dsh-hub-dispatcher-1`）完全兜不起來，會直接抓不到容器。改成用 Docker Compose 自己打的 `com.docker.compose.service` label 查找，不管專案叫什麼名字都能用——已經在本機不同命名的專案上驗證過這個查找邏輯本身是對的，也在真實部署上驗證過修好之後確實抓對。

### dsh 預設 provider + 預設模型：完整實作與實測

原本以為這就是設計文件說的「透過 `settings.yaml` 設定供應方」，動手查了才發現：目前安裝的 dsh 版本（`0.1.5-rc.2`）內部已經改版成「profile + plugin patch-list」架構，`settings.yaml`（`@deepseek-ai/dsh-settings-file` 套件）只是使用者個人偏好的其中一個命名空間存放處，**不支援環境變數代入**（官方文件明講：`${env:VAR}` 是「deferred」功能，目前只能塞字面值）。真正能達到「用環境變數指定 API key，不把明碼寫進設定檔」這個目標的，是另一層——透過 `dsh-llm-pi-ai` 套件的 `providers:` 設定加一個自訂 provider，裡面的 `apiKeyEnv` 才是即時解析環境變數、不落地到檔案的正確機制。設計文件對「文件字面上是 settings.yaml」這件事猜錯了實際檔案名稱，但「用 apiKeyEnv 存取金鑰、不寫死明碼」這個精神完全正確，最後做出來的效果和文件的目標一致。

**做法**：
- 新增 [singleuser/dispatcher-provider.yaml](singleuser/dispatcher-provider.yaml)：一份 `--patch` 覆蓋檔，內容是幫 `llm-pi-ai` 套件的 `providers` 加一筆 `lab-dispatcher`（`apiKeyEnv: DISPATCHER_API_KEY`、`baseURL: !!js process.env.DISPATCHER_BASE_URL`——用 dsh 設定檔本來就支援的 `!!js` 動態求值語法，不是寫死字串，Dispatcher 網址換了也不用重新 bake image），並把 `agent-default-model` 套件的預設值改成這個 provider 的 `deepseek-v4-flash-0731`。
- [singleuser/start_harness.py](singleuser/start_harness.py) 新增 `ensure_dispatcher_patch()`：容器第一次啟動時，把這份設定從 image 內建路徑複製一份到 `$DSH_HOME/patches/dispatcher-provider.yaml`（使用者的持久化 volume 裡），**已存在就不覆蓋**——完全對應文件六原本要求的「volume 內不存在該設定檔才從 image 複製，已存在則不覆蓋」規則。`dsh web` 啟動指令加上 `--patch $DSH_HOME/patches/dispatcher-provider.yaml`。

**過程中發現並修好一個更關鍵的 bug：Dispatcher 完全不支援 streaming**。第一次接上真實 dsh 測試（`dsh --profile headless`）就直接失敗，錯誤是 `TRANSPORT: Stream ended without finish_reason`——因為 dsh 預設會用 `stream: true` 跟後端要 SSE 逐字回應，但 Dispatcher 原本的實作是等 Spark 回完整個回應才一次性回傳一個 JSON blob。這不是設定問題，是 Dispatcher 本身的架構缺陷，如果沒抓到，等於 Dispatcher 做出來的東西實際上不能跟真正的 dsh 搭配使用（只有我自己手動 curl 不帶 `stream:true` 的測試才會過，掩蓋了這個問題）。已重寫 [dispatcher/app.py](dispatcher/app.py) 的轉發邏輯：用 Tornado 的 `streaming_callback` 即時把 Spark 傳回的每個 chunk 原樣轉發給呼叫端（不管呼叫端要不要 streaming，統一走 chunked transfer encoding，一般 JSON 呼叫端一樣能正常組裝出完整回應，不需要 Dispatcher 自己判斷分流）。

**已用真實流程完整驗證**：
1. 先用 `dsh --profile headless --patch <file> "回覆 OK"` 直接測（繞開 UI，最直接的驗證方式），拿到真實模型回應、exit code 0。
2. 確認舊的非 streaming 手動測試（curl 風格）沒有回歸壞掉。
3. 透過**真實 Hub 登入 → 真實 spawn 全新容器**（不是 `docker run` 手動起的）驗證：`$DSH_HOME/patches/dispatcher-provider.yaml` 正確被種入、容器內執行的 `dsh` 行程指令列確認帶著 `--patch` 參數、harness 頁面正常回 200。
4. 測試「使用者自己編輯過設定檔」的情境：手動在種好的檔案裡加一行標記文字、重啟容器，確認標記還在——證明「已存在就不覆蓋」這條規則是真的生效，不是理論上而已。

**還沒驗證的部分**：`dsh --profile headless` 走的模型呼叫路徑跟 `dsh web`（真正給使用者用的網頁介面）是同一套底層 provider/patch 機制，理論上會有一樣的行為，但我沒有實際操作瀏覽器介面去點「送出訊息」按鈕做端到端驗證（那需要瀏覽器自動化，這次沒做）。

### 上線後又抓到並修好的 bug：真實使用者的 container 完全沒拿到 API key

**現象**：真人帳號登入後，dsh 網頁介面顯示 `MISSING_CREDENTIAL`：`DISPATCHER_API_KEY` 沒有設定。

**原因**：`_inject_dispatcher_api_key`（`pre_spawn_hook`）原本用「Redis 裡這個使用者是否已經有一把 key」來判斷要不要重新核發——這個判斷依據是錯的。當時我為了修另一個問題（誤跑 `FLUSHDB` 後手動把使用者原本的 key 寫回 Redis），Redis 裡就有了這個使用者的 key 紀錄，但**這是一個全新建立的 container**，環境變數是空的。Hook 看到 Redis 有紀錄就以為「不用重發」，於是只設定了 `DISPATCHER_BASE_URL`，`DISPATCHER_API_KEY` 完全沒有寫進去。

**修法**（[hub/jupyterhub_config.py](hub/jupyterhub_config.py) / [common/api_keys.py](common/api_keys.py)）：判斷依據改成直接查 Docker（`spawner.get_object()`）——這個 container 是不是真的已經存在，而不是問 Redis「這個使用者是否曾經有過 key」。真的是全新建立的 container 才核發新 key（同時撤銷該使用者舊的自動核發 key，避免累積用不到的孤兒 key）；container 本來就存在（只是重啟）就完全不動環境變數，因為 Docker 本來就不會讓 `docker start` 套用新的 `-e` 值。移除了原本錯誤的 `get_or_issue_auto_key()`，改成 `revoke_auto_keys()` + `issue()` 兩步驟。

**已重新驗證**（第一次驗證方法有誤、修正後重測過）：真實走一次「刪除 container → 明確觸發 `/hub/spawn` 重新建立」，確認新 container 同時拿到 `DISPATCHER_BASE_URL` 和 `DISPATCHER_API_KEY`、真實模型呼叫成功；舊 key 在 Redis 裡正確標記 `revoked`，實際打 Dispatcher 回 401；新 key 正確可用回 200。對應的單元測試也從「測錯誤假設的 `get_or_issue_auto_key`」改成測 `revoke_auto_keys` 的三個情境（只撤銷 auto 標籤的 key、沒有 key 時是 no-op、不影響其他使用者），全過。

**這次事故也是一個提醒**：手動修補 Redis 資料（即使是為了修另一個問題）可能製造出「Redis 狀態」和「Docker 真實狀態」不一致的情況，而且不容易在當下發現——之後如果還需要手動介入 Redis，要更謹慎考慮這類副作用。

---

## 七、Dispatcher 身份驗證與 API Key 管理

新增 `common/api_keys.py`（Hub、Dispatcher、Panel 三方共用），把第五節記錄的「只信任自報 header」缺口整個換掉。設計上跟文件的雙身份來源不太一樣，但更安全也更簡單，見下方「跟文件設計的差異」。

| 項目 | 狀態 | 說明 |
|---|---|---|
| API key 生成/儲存 | ✅ 已完成、已實測 | `dsp_` + 32 bytes 隨機 hex，只存 SHA-256 hash（`dispatcher:apikeys:hash:<hash> -> key_id`），明文從不落地。metadata（`user`/`label`/`created_at`/`created_by`/`last_used_at`/`revoked`/`expires_at`）存在另一個 Redis hash。10 個單元測試（[tests/test_api_keys.py](tests/test_api_keys.py)）覆蓋核發、驗證、撤銷、過期、依使用者列出、`last_used_at` 更新，跟 `test_dispatch_queue.py` 一樣需要真實 Redis，在這個 sandbox 的 `.venv` 會 skip，另外用裝了 `redis` 套件的容器接真實 Redis 跑過全部通過。 |
| container 自動核發（取代 `JUPYTERHUB_USER` 信任） | ✅ 已完成、已實測 | Hub 的 `c.Spawner.pre_spawn_hook` 在每次 spawn 前檢查該使用者是否已有有效的 auto key，沒有就核發一把，透過 `spawner.environment` 注入 `DISPATCHER_API_KEY`、`DISPATCHER_BASE_URL` 到即將建立的 container。真實 spawn 一個全新容器後，直接進容器內用注入的 key 打 Dispatcher，拿到真實模型回應；Redis 裡查證這筆請求正確歸屬到 `islab`（不是自報的，是從 key 反查出來的）。 |
| 核發的冪等性 | ✅ 已完成、已實測 | 同一使用者重新登入（容器沒被砍掉，只是重啟）不會核發第二把 key——因為 container 的環境變數在**建立時**就固定了，重新 `docker start` 不會套用新的環境變數，重複核發只會留下用不到的孤兒 key。實測「登入兩次」確認 Redis 裡該使用者的 key 集合前後都只有一把，容器裡的 `DISPATCHER_API_KEY` 值也沒變。 |
| Dispatcher 驗證邏輯 | ✅ 已完成、已實測 | `Authorization: Bearer <key>` 缺漏或無效一律 401（實測用假 key 打，正確被擋）；用真實核發的 key 打，正確轉發並在佇列裡標記正確的 owner。`/v1/models` 現在也要求有效 key，不再公開。 |
| Panel「API Key 管理」 | ✅ 已完成、已實測 | 任何登入的使用者都能在自己的頁面自助核發/查看/撤銷**自己的** key（明文只在核發當下顯示一次，之後只顯示 metadata）；管理員額外能看到`所有人的 key`（`?all=1`）。實測完整流程：islab 自助核發一把新 key → 直接用它打 Dispatcher 成功 → 撤銷 → 再打變成 401；期間也用暫時的 admin 權限確認「所有 key」清單能看到別人（這裡測試上只有 islab 一個真人帳號可用，「別人」指的是 auto key 和手動 key 兩筆不同記錄）核發/撤銷都寫進審計 log。 |

**跟設計文件字面描述的差異（判斷後的取捨，不是隨便簡化）**：文件第七節原本設計是「兩種身份來源」——(A) container 內建 `JUPYTERHUB_USER`，因為在 docker 內部網路上「天生可信」；(B) 核發的 API key，給非 container 呼叫用。實際動手做時發現：如果只是讓 container 在 header 裡自報 `JUPYTERHUB_USER` 字串，使用者自己在自己的 container 裡（他們本來就有完整的 shell/程式執行權限）可以輕易把這個字串改成任何值去冒充別人，`X-Dispatcher-User` 這個舊實作就是活生生的例子。所以這裡把兩種來源**統一成同一套機制**：container 由 Hub 在 spawn 時自動核發一把真正的 key（使用者依然不用手動申請，體驗上等於原本的「天生可信」），外部呼叫則是手動申請同一種 key——身份永遠由「這把 key 實際屬於誰」反查決定，不是由呼叫端自己宣稱決定。少了一套機制，也堵住了原本設計裡「使用者能改自己的環境變數」這個現實漏洞。

**這次過程中撞到的一個小坑**：一開始把 hook 設成 `c.Spawner.pre_spawn_start`，結果 JupyterHub 完全不認得這個設定名稱（只是印警告、靜默跳過，不會讓啟動失敗），導致 key 完全沒被注入而不自知，直到我真的進容器裡 `env | grep DISPATCHER` 才發現是空的。正確的設定名稱是 `pre_spawn_hook`（`pre_spawn_start` 是內部方法名，不是 traitlets config 名稱）。修好後重新驗證過一次完整流程。

**還沒做的部分**（第一項這裡更新掉：已經在第六節做完了，不再是待辦）：
- ~~dsh 本身的 settings.yaml 還沒有配置去讀這兩個環境變數~~ ——**已完成**，見第六節「dsh 預設 provider + 預設模型」，`dispatcher-provider.yaml` 這份 patch 檔已經讓 dsh 真的用 `DISPATCHER_API_KEY`/`DISPATCHER_BASE_URL` 送出帶 key 的請求，早就不是「放著等用」的狀態。
- 撤銷 container 的 auto key 後，該 container 不會自動拿到新 key（環境變數建立後不會變），使用者要嘛整台容器重建、要嘛手動在容器內設新的環境變數/改設定檔。這是「remove=False 長效容器」跟「key 可撤銷」兩個設計目標本來就有的張力，不是 bug，還沒有更好的解法，先記錄，不算待辦事項。

---

## dsh 網頁內嵌排隊位置提示列

不屬於原始規劃文件的章節，是你提出的新想法：既然 dsh 的網頁可以被 iframe 嵌入（設計文件已查證過這件事），能不能把「目前排第幾位」也顯示在 dsh 對話輸入框上方。

**做法**：不是把它做成 dsh plugin（維持設計文件「不做成 dsh plugin」的原則），而是延用既有的代理層注入機制（`harness_proxy.py` 原本就會攔截 dsh 回傳的 HTML、注入 `harness_bridge.js`），多注入一段小工具列。關鍵的安全考量：瀏覽器端的 JS 不能直接拿 `DISPATCHER_API_KEY` 去問 Dispatcher（放進瀏覽器可見的程式碼等於外洩金鑰），所以做法是：

1. [common/dispatch_queue.py](common/dispatch_queue.py) 新增 `status_for_user()`：查某個使用者目前是「執行中」、「排隊中（第幾位）」還是「閒置」，只回自己的狀態，不是整份佇列。
2. [dispatcher/app.py](dispatcher/app.py) 新增 `GET /v1/queue/position`，一樣用既有的 API key 機制驗證身份。
3. [singleuser/queue_status_proxy.py](singleuser/queue_status_proxy.py)：容器內新增一個極簡的本機小伺服器，用容器自己（伺服器端）持有的 `DISPATCHER_API_KEY` 去問 Dispatcher，只把 `{status, position}` 轉發給瀏覽器——瀏覽器全程看不到金鑰本身。這支透過 [singleuser/jupyter_server_config.py](singleuser/jupyter_server_config.py) 用既有的 `jupyter_server_proxy` 機制掛載，跟現有的 `harness_bridge.js`/`harness_proxy.py` 同一套防護（Jupyter 本身的登入驗證）。
4. [singleuser/harness_bridge.js](singleuser/harness_bridge.js)：新增一段輪詢（每 4 秒）+ `MutationObserver`，把提示列插在 `[data-composer-card="true"]`（你實際打開瀏覽器開發者工具、複製 dsh 輸入框外層容器的 HTML 給我確認的真實選擇器——這是刻意選的 `data-*` 屬性，不是 dsh 建置後產生的雜湊 class 名稱，換版存活機率高很多）正上方；閒置時自動隱藏，不會一直佔位。

**已用真實請求驗證**：用兩個真實核發的 key（islab 本人 + 一個測試用的合成帳號）模擬「別人佔用唯一併發名額、islab 自己的請求在後面排隊」的情境，實測輪詢 `queue-status` 端點：對方在跑的時候正確回報 `{"status": "queued", "position": 1}`，換 islab 的請求開始跑之後變成 `{"status": "running"}`，跑完後變回 `{"status": "idle"}`，全程透過真實的 `/user/<帳號>/queue-status/` 代理路徑（跟瀏覽器實際會打的路徑一樣），沒有走捷徑。

**視覺效果：已用真實瀏覽器操作驗證過會出現，樣式已校正過一輪**。第一次你實際在瀏覽器看到提示列有跳出來，但反映樣式（等寬字 IBM Plex Mono、青綠色、有邊框）跟 dsh 本身風格不搭——這組顏色/字型當初是我自己編的，沒有比對過 dsh 實際的樣式。修法：請你在瀏覽器 devtools 對 `[data-composer-card="true"]` 跟它的送出按鈕做 `getComputedStyle()`，量到 dsh 真實用的數值：卡片背景 `#2C2C2E`、主文字 `#F9FAFB`、系統字型堆疊（`-apple-system, "Segoe UI", "PingFang SC"...`，不是等寬字）、按鈕圓角 `999px`（藥丸形）。另外從 dsh 打包的 CSS 檔案裡挖到一個有寫死數值的次要文字色 `#CFD3D6`（`--dsh-boot-label-secondary` 深色主題版）可以用在狀態提示這種不需要搶眼的文字上。[singleuser/harness_bridge.js](singleuser/harness_bridge.js) 的提示列樣式已經照這組真實數值重做（背景貼齊卡片、文字用次要色、系統字型、藥丸圓角），透過 `docker cp` 直接更新進正在跑的容器測試（`harness_proxy.py` 每次回應都即時讀檔案內容，不需要重建 image/重啟容器就能生效）。

第一次寫的版本還有個潛在的無限迴圈風險（`MutationObserver` 的 callback 無條件呼叫 `insertBefore`，而 `insertBefore` 就算目標位置沒變也會觸發一次 DOM mutation，等於自己觸發自己），已經在測試前就發現並修好，改成只在提示列真的不在正確位置時才動 DOM。

**還沒完全確認**：新樣式（第三輪）改完後你還沒回報外觀是否滿意——上面兩輪的手動排隊模擬（decoy 佔位請求）都已經驗證過後端邏輯正確（`queued`→`running`→`idle` 狀態正確流轉），只差你這邊視覺上點頭。新樣式已經直接改在 repo 原始檔（[singleuser/harness_bridge.js](singleuser/harness_bridge.js)），下次重建 image 就會自動帶到；同時也用 `docker cp` 把同一份檔案熱更新進你目前正在跑的 container，不用重建就能立刻看到效果。

---

## 介面重新設計：islab（應用密碼與資訊安全實驗室）品牌，移除 JupyterHub 字樣

不屬於原始規劃文件的章節，是這次額外做的視覺改版工作。先用 Claude Design 畫布出了三個視覺方向（學術書卷 / 系統實驗室 / 師大紫）給你選，你選了「系統實驗室」（深色、等寬字、青綠主色），但標題字型改用「師大紫」方向的 Manrope，並確認品牌全名是「應用密碼與資訊安全實驗室」（紀博文教授的實驗室，NTNU CSIE）。

**做了什麼**：
- [hub/templates/page.html](hub/templates/page.html)：JupyterHub 所有頁面共用的基礎範本，改了網站標題、meta 描述、導覽列 logo（換成 islab 圖示 + wordmark）、整站 CSS（深色配色、Manrope 標題字、IBM Plex Mono 標籤字、Noto Sans TC 中文內文、隱藏了原本的淺色/深色切換按鈕，因為品牌固定用深色）。因為其他每個頁面（登入、首頁、admin、token…）都是 `{% extends "page.html" %}` 出去的，這一份檔案的改動會自動套用到全部頁面，不用每頁重改。
- [hub/templates/login.html](hub/templates/login.html)：整個重寫登入區塊，加上 islab 品牌識別區（圖示 + wordmark + 「國立臺灣師範大學資訊工程學系 / 應用密碼與資訊安全實驗室」副標），欄位文字全部換成中文（帳號/密碼/登入），並把原本的「JupyterHub」不安全連線警告文字也換掉。
- [hub/templates/admin.html](hub/templates/admin.html)：頁尾「JupyterHub {version}」換成「islab · {version}」。
- [panel/app.py](panel/app.py)：Panel 的配色、字型、標題、所有中英夾雜的按鈕/表格文字全部換成跟 Hub 一致的風格與中文標籤。

**JupyterHub 字樣清除範圍（老實列出做了哪些、沒做哪些）**：
- ✅ 已清乾淨：網站標題、meta 說明、導覽列 logo、登入頁全部文字、admin 頁尾。
- ⚠️ 刻意不動、有說明原因的殘留：
  - `<noscript>JupyterHub requires JavaScript</noscript>`（`page.html`）——只有瀏覽器關閉 JavaScript 時才看得到，這段文字不在任何具名的 Jinja block 裡，要移除得整個重寫 `<body>` 結構，風險大於效益，先留著。
  - `home.html` 裡 `<h1 class="visually-hidden">JupyterHub home page</h1>` ——螢幕報讀器專用的無障礙文字，一般使用者肉眼看不到；它一樣不在獨立 block 裡，要拿掉得整段重寫首頁的具名伺服器管理區塊（`.new-server-name`/`.stop-server`/`.start-server` 這些 JS 依賴的 class），複製錯一個字就可能弄壞「新增/刪除具名伺服器」功能，評估後決定不動。
  - `token.html`／`oauth.html` 裡幾處提到「JupyterHub API」「JupyterHub documentation」「JupyterHub account」的技術性文字——這些是指真正的上游 JupyterHub REST API/官方文件（技術上仍然正確，這系統底層真的是 JupyterHub），沒有換成品牌文字；`admin.html` 主要內容是一個 React SPA（`admin-react.js`），沒有進一步深入改它內部的文字/樣式。

**已驗證**：真實登入（含錯誤密碼會被拒絕，畫面文字/樣式仍正常顯示）、首頁、Panel 頁面都用真實瀏覽器流程測過，確認樣式與品牌文字正確顯示、原本的功能（登入表單送出、xsrf、密碼錯誤提示）都沒有壞掉。你自己那個帳號的 container（`61447007s`）完全沒被動到——這次改的都是 Hub/Panel 的呈現層，不影響 dsh 使用者容器本身的網頁。

---

## 八、已知風險與設計提醒 / 九、尚待確認事項——這次解決掉的項目

**思考強度相容性測試（`developer` 角色）：✅ 已解決**。這是文件「九、尚待確認」裡列的唯一一條待辦。直接對正式 Spark vLLM 端點送一個帶 `role: "developer"` 訊息的 `/v1/chat/completions` 請求（`max_tokens=5`，控制成本）：

```
POST http://192.168.101.70:8888/v1/chat/completions
messages: [{role: "developer", ...}, {role: "user", content: "Say OK."}]
```

回應是正常的 `200`，且 `message` 裡還帶了 `reasoning` 欄位（內容是模型的思考過程摘要），代表這個 vLLM 部署：
1. 完全接受 `developer` 角色，不會報錯。
2. 原生支援 reasoning/思考過程輸出，跟 dsh 的 `reasoningEfforts` 機制相容。

**結論：dsh 該模型設定不需要加 `compat: supportsDeveloperRole: false`，用預設的 `developer` 角色即可。**

第八節其餘風險，現在 Dispatcher/佇列已經做出來後的最新狀態：
- **Redis 單點故障**：`redis:7-alpine` 已開 `--appendonly yes`（AOF 持久化），資料存在 `redis-data` 具名 volume；還沒有另外做監控/告警。
- **Admin 重排的 race condition**：已解決，見第五節。
- **長任務佔用併發名額 / 多分頁衝突 / 主機重開機續跑**：跟 Dispatcher 本身無關，維持原狀待評估。

---

## 這次測試動到的東西 / 需要你知道的殘留狀態

- `hub/certs/islab-ca.crt`：NTNU CSIE islab 的私有 CA 憑證（用 `openssl verify` 比對過確認是正確簽發的那張）。`.env` 的 `LDAP_CA_CERT_FILE` 已指向它。
- 新增了 `PANEL_API_TOKEN`（隨機生成）、`SPARK_BASE_URL`、`DISPATCHER_CONCURRENCY` 到 `.env`/`.env.example`。
- **你（真人帳號 `61447007s`）目前有一個真的在跑的 container**（`dsh-demo-61447007s`），是你自己真實登入 spawn 出來的，我完全沒有動它——所有測試都是用服務帳號 `islab` 或直接 `docker run` 做的獨立測試容器，測完就清掉，不會混到你的東西。
- **`FLUSHDB` 這個習慣捅了兩次同樣的簍子**：第一次已經記錄過（清測試資料時連你 container 正在用的 key 一起清掉）。做「dsh 網頁排隊提示列」這個功能清測試資料時，**同一個錯誤又發生了一次**——又跑了一次 `FLUSHDB`，又把你的 key 清掉了。兩次都是當下立刻發現、從你的 container 讀出實際使用中的 key 值手動寫回 Redis、驗證過 `verify()` 能正確認出是你，**你不需要做任何事，key 目前是有效的**。但這代表光是「提醒自己小心」沒有用，之後不會再用 `FLUSHDB` 清測試資料，改成只刪測試用的特定 key（用前綴或 id 精準刪除），從根本上避免這個風險再發生第三次。
- `dsh-demo-user:local`、`new-dsh-hub`、`new-dsh-dispatcher`、`new-dsh-panel` 四個 image 都已經用最新程式碼重新 build 過。你自己的 container 目前還是**跑舊 image**（remove=False，不會自動換），代表它還沒有這次新增的 `--patch` 預設 provider 設定；下次你的 container 被重建（例如手動在 `/hub/home` 停止並移除，或整個環境重置）才會拿到新版。如果你想現在就用新的預設模型設定，需要重建你的 container（會遺失容器內非 volume 部分的狀態，`/home/demo` 底下的資料不會丟）。
- 目前背景執行中的容器：`hub`、`docker-socket-proxy`、`redis`、`dispatcher`、`panel`，加上你自己的 `dsh-demo-61447007s`。Redis 現在只有你那把 key 加上 `dispatcher:concurrency=1`，其餘測試資料都清乾淨了。

---

## 待查、未確認的觀察（不是已修好的 bug，只是記錄下來）

- **上一輪「Panel 每次輪詢都重新走完整 OAuth」的猜測，回頭查證後判斷是誤判**：實際去翻 `new-dsh-hub-1` 的 log 才發現，17:37–17:38 那組「每 20~40 秒重複一次 authorize→token」的紀錄，來源是 `islab@127.0.0.1`——也就是我自己先前測試第四節 Admin Panel 時寫的 Python 測試腳本在跑，不是真實瀏覽器的 Panel 頁面在做輪詢（Panel 前端 `fetchQueue()` 的輪詢間隔是 4 秒，跟這組紀錄的 20~40 秒間隔對不上）。所以這條待辦可以拿掉了，不是真的問題。
- **另外查到一個不同的、範圍更小的現象，深入查證後推翻了自己原本的推測**：真人帳號 `61447007s` 的 log 裡，`GET /hub/api/oauth2/authorize?...redirect_uri=/user/61447007s/oauth_callback`（單人 Jupyter Server 自己的 Hub OAuth，`jupyter_server_proxy` 走 harness/queue-status 代理用的那層驗證，跟 Panel 無關）偶爾（3 小時內 5 次，間隔幾分鐘到快 40 分鐘，不密集）會被導回 `/hub/login` 而不是完成交換。我原本猜是「per-server OAuth token 預設 1 小時過期，剛好跟背景輪詢撞期」，直接去翻 JupyterHub 5.5.2 原始碼（`app.py` 的 `oauth_token_expires_in` trait）查證後發現這個猜測是錯的：預設值其實是跟著 `cookie_max_age_days`（我們沒覆寫，預設 14 天）走，換算下來是 14 天，不是 1 小時——我一開始把 `oauth/provider.py` 裡一段 docstring 範例寫的 `'expires_in': 3600` 誤當成真正預設值，那只是說明文件裡展示 token 格式的範例數字。14 天的時間尺度跟這幾次事件幾分鐘到幾十分鐘的間隔完全對不上，這個理論已撤回。

  重新看這幾筆 log：actor 欄位完全空白（`(@172.20.0.1)`，代表請求本身就沒帶任何 Hub session cookie，不是「舊 cookie 剛好過期」），比較吻合「開新分頁、瀏覽器重開、或頁面第一次載入、Hub 登入重導向還沒完成就有背景請求先打出去」這種正常情境，而不是週期性的 token 失效。**目前沒有證據支持這是一個持續性 bug**，先降級成「已查證、看起來是正常現象」，不再列為需要深入的待辦；如果之後你真的在瀏覽器上感覺到提示列會規律性地閃一下消失（不是只在開新分頁/重新整理當下），再跟我說，我會需要瀏覽器端即時對照 cookie 狀態才查得下去，光看 server log 到這裡已經是極限。

---

## 下一步建議

**這個 repo 已經正式部署到正式的 Proxmox VM 上了**（`hellman` 這台主機，8 vCPU / 40GB RAM / 300GB XFS+prjquota 資料碟，規格怎麼算的見 README「VM 規格建議」），不再只是本機 sandbox 的紙上驗證。目前真正還沒完成的項目：

**已完成、全部在真實 Proxmox VM 上驗證過：**
1. 「三、CPU/記憶體限制」——完成並實測。
2. 排隊提示列——後端邏輯、DOM 注入、視覺樣式三塊都完成並確認過外觀。
3. 「六、防止繞過排隊系統」——[ops/spark-lockdown/](ops/spark-lockdown/) 在正式 VM 上真實跑過：Dispatcher 打 Spark 拿到 200，丟棄式測試 container 打 Spark 逾時失敗，整套堆疊重建後重跑 `enable.sh` 也正確處理了 Dispatcher 換 IP 的情況。過程中還抓到並修好一個真實 bug（容器名稱解析邏輯猜錯字首，改用 compose label 查找）。
4. 「三、磁碟配額」的 Redis/容器/腳本管線——在真的 XFS + prjquota 磁碟上跑過 `apply-disk-quotas.sh`，乾淨執行無誤。

**現在唯一剩下、卡在「還沒有人真的登入過」的事**：
5. 反向代理（AdGuard + Caddy + 內部 CA 憑證）已經接好並驗證通過（`curl https://dsh-hub.islab.local/hub/login` 拿到真實的登入頁），代表現在真的可以拿瀏覽器打開這個網址、輸入 LLDAP 帳密登入了。
6. 磁碟配額腳本裡實際下 `xfs_quota` 指令那幾行，因為驗證當下還沒有使用者 volume 存在，一次都還沒真的執行過（見上方「三、Container 資源限制」章節的說明）。**只要完成第 5 項（真人登入一次）**，就會自動產生第一個 `dsh-demo-home-<帳號>` volume，這時候重跑一次 `apply-disk-quotas.sh`、用 `xfs_quota -x -c 'report -p' /var/lib/docker` 確認配額真的生效，整個第三節就完全收尾了。

**理論上的長期考量、不影響現在的正確性：**
7. 磁碟配額本身沒有問題，但 Proxmox VM 的虛擬磁碟終究不是文件原始設想的「獨立實體硬碟」，如果日後正式上線發現效能瓶頸，可能要重新評估；目前判斷不影響功能正確性，只是效能上的理論差異，先不列為待辦。

**已完成、僅剩你確認/決定的：**
8. Panel/Hub 介面重新設計——已完成全站，品牌換成 islab／應用密碼與資訊安全實驗室；殘留幾處刻意不動的技術性文字/無障礙標籤（原因見上方「介面重新設計」章節），除非你覺得需要更深入處理，否則這項算完成。
9. 舊的本機測試環境（這個 sandbox）跟正式 VM 是兩份獨立的部署，本機那邊要不要繼續留著純粹看你需不需要，不影響正式環境。

**待查、非 bug 的觀察**：Panel 疑似每次輪詢佇列都重新走一次完整 OAuth handshake（見上方「待查、未確認的觀察」），還沒深入查，如果你操作時覺得卡頓再跟我說。

等你指示，或是有其他優先順序。
