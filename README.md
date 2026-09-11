# JupyterHub + LLDAP + DeepSeek Harness

登入頁 → 後端向 LLDAP 驗證帳密 → DockerSpawner 啟動個人容器 → 自動導向 `/user/帳號/harness/`。

帳號、密碼與群組由既有的 LLDAP 管理。此專案不會另外建立 LLDAP 伺服器；原本的前端 demo123 驗證已移除。

## 設定 LLDAP

```bash
cp .env.example .env
chmod 600 .env
```

在 `.env` 填入實際連線資料。以 Base DN `dc=example,dc=com` 為例：

- `LDAP_SERVER_ADDRESS`：Hub 容器可連到的 LLDAP 主機名稱或 IP，不含協定和 port。
- `LDAP_USER_SEARCH_BASE`：`ou=people,dc=example,dc=com`。
- `LDAP_USER_ATTRIBUTE` 與 `LDAP_USER_DN_ATTRIBUTE`：`uid`，以 LLDAP 使用者 ID 登入。
- `LDAP_BIND_DN`：例如 `uid=jupyterhub-service,ou=people,dc=example,dc=com`。先在 LLDAP 建立這個服務帳號，加入 `lldap_strict_readonly` 群組。
- `LDAP_BIND_PASSWORD`：該服務帳號密碼。包含 `$` 或 `#` 時用單引號包住。
- `LDAP_ALLOWED_GROUPS`：預設 `[]`，允許所有通過 LLDAP 驗證的帳號。若只允許特定群組，設成 `'["cn=jupyterhub-users,ou=groups,dc=example,dc=com"]'`，並在 LLDAP 建立群組、加入使用者。

帳號格式：`jupyterhub-ldapauthenticator` 內建的使用者名稱檢查（`valid_username_regex`）已放寬成允許數字開頭（`^[a-zA-Z0-9][.a-zA-Z0-9_-]*$`），因為學號/職員編號格式的帳號很常見；若你的 LLDAP 帳號還有其他這個正則不涵蓋的字元，需要在 [hub/jupyterhub_config.py](hub/jupyterhub_config.py) 再調整。

LLDAP 的一般 LDAP port 通常是 `3890`，Web 管理介面 port 不是 LDAP port。若使用可信任私有網路內的未加密 LDAP，設 `LDAP_TLS_STRATEGY=insecure` 與 `LDAP_SERVER_PORT=3890`；帳密會透過該連線明文傳輸。若已啟用 LDAPS，設 `on_connect` 與實際 LDAPS port（常見為 `6360`）。範本預設為 LDAPS，需與你的部署一致。

TLS 會驗證伺服器憑證；若使用私有 CA，將憑證以唯讀 volume 掛進 Hub，再把容器內路徑填入 `LDAP_CA_CERT_FILE`。

Hub 容器中的 `localhost` 是 Hub 自己。若 LLDAP 在 Docker Desktop 主機上，可使用 `host.docker.internal` 和主機公開的 LDAP port；若在另一個 Docker 網路，需讓 Hub 加入該網路後以 LLDAP 容器名稱連線。

`.env` 已加入 Git 忽略清單，也不會被複製進映像。未填 LDAP 主機或搜尋 Base DN 時，Hub 會拒絕啟動，不會退回測試登入。

## 建置與啟動

填好設定後執行：

```bash
docker compose --profile build build
docker compose up -d hub redis dispatcher panel
```

開啟 http://localhost:9000，以 LLDAP 帳號密碼登入。沿用現有對外 port 9000，容器內入口仍為 8000。使用者容器透過內部網路和網址路徑共用入口。

`redis`/`dispatcher`/`panel` 是佇列與管理面板（見下方「Dispatcher 與 Admin Panel」），不是 Hub 登入必要的一部分，但 Panel 依賴 Hub 已經在跑（要向 Hub 要 OAuth token）。`.env` 需要額外填 `PANEL_API_TOKEN`（`openssl rand -hex 32` 生成）、`SPARK_BASE_URL`（Spark 上 vLLM 的位址），否則 `docker compose` 會直接拒絕啟動。

切換 LDAP 使用新的 cookie secret 檔案，讓舊測試登入 cookie 失效。Hub 資料庫仍保留；同名 LDAP 帳號會對應原本同名的 Hub 身分。

## 容器行為

相同帳號連回同一個執行中的容器。登出只結束登入狀態，容器繼續運行；關閉分頁也不會停止容器。手動在 `/hub/home` 停止容器時，DockerSpawner 只會停止它，不會移除（`remove=False`），下次登入會喚醒同一個容器。使用者 home 目錄（`/home/demo`）掛載在每個帳號各自的具名 volume（`dsh-demo-home-<帳號>`），容器被停止、重建甚至手動刪除後，資料仍保留在該 volume 裡。

關閉整套系統可執行：

```bash
docker compose stop hub
```

Hub 關閉或重啟不會停止或移除它管理的使用者容器（`cleanup_servers=False`），與一般使用者登出的效果相同：容器繼續在背景運行。Hub 的資料 volume 和映像也會保留。意外中斷時，用以下指令檢查是否還有使用者容器執行：

```bash
docker ps --filter name=dsh-demo-
docker compose logs --tail=100 hub
```

LLDAP 群組或密碼變更主要在重新登入時生效；不會自動關閉既有容器或立即撤銷所有既有登入 session。

## Dispatcher 與 Admin Panel

`dispatcher/` 是排隊/轉發用的 OpenAI 相容 gatekeeper，`panel/` 是給人看/操作佇列的管理面板（JupyterHub Service，走 Hub OAuth SSO），兩者透過 `common/dispatch_queue.py` 共用同一個 Redis 當作真相來源，彼此不直接呼叫對方。

**Dispatcher**（`http://dispatcher:8080`，只在 `dsh-demo` 內部網路上，目前沒有對外開 port）：
- `POST /v1/chat/completions`：OpenAI 相容格式，轉發到 `SPARK_BASE_URL`。呼叫端必須帶 `Authorization: Bearer <API key>`，沒有或無效一律回 401。身份完全由「這把 key 實際是誰核發的」反查決定，不吃任何呼叫端自報的使用者名稱。
- `GET /v1/models`：同樣需要有效 API key，直接透傳 Spark 的 `/v1/models`。
- 併發數（同時轉發幾個請求給 Spark）存在 Redis 的 `dispatcher:concurrency`，預設值來自 `.env` 的 `DISPATCHER_CONCURRENCY`，改了立即生效，不用重啟 Dispatcher。

**身份與 API Key**（`common/api_keys.py`，設計文件第七節）：
- 使用者 container 在 spawn 時，Hub 會自動核發一把專屬 API key，透過環境變數 `DISPATCHER_API_KEY`（配 `DISPATCHER_BASE_URL`）注入到容器裡——不需要使用者自己申請。同一個使用者重新登入不會重複核發，因為容器環境變數在建立當下就固定了。這兩個環境變數已經接進 dsh 本身的預設 provider（見下方「dsh 預設 provider 與模型」），不用使用者自己設定就能用。
- 任何登入 Hub 的使用者都能在 Panel 頁面下方「My API Keys」自助核發/查看/撤銷自己的 key（給不透過 container 的個人腳本用），明文只在核發當下顯示一次。管理員能看到所有人的 key（唯讀 + 可代為撤銷）。核發/撤銷都會記進審計 log。
- Key 格式 `dsp_<48 hex>`，只存 SHA-256 hash，可撤銷、可設過期時間。

**Panel**（`http://localhost:9000/services/panel/`，登入 Hub 後即可訪問）：
- 一般使用者：唯讀，只能看目前佇列（`GET /api/queue`）、管理自己的 API key。
- 管理員（Hub 的 `admin` flag，跟 Section 一的 LDAP 管理員群組是同一套）：可以拖拉排序（`POST /api/reorder`）、調整併發數（`POST /api/concurrency`）、查看/撤銷所有人的 API key，這些動作都會寫進審計 log（`GET /api/audit`，僅管理員可讀）。
- 已經被 Dispatcher 從佇列前端撈走（開始處理）的請求，不會出現在可重排清單裡，管理員也就無法把它拉回排隊——這是刻意設計，不是漏洞。

**已知限制**：撤銷一個 container 的自動核發 key 後，該 container 不會自動拿到新 key（環境變數不會被 `docker start` 更新），需要重建容器或手動改容器內設定。

## dsh 預設 provider 與模型

已安裝的 dsh 版本（`0.1.5-rc.2`）內部是「profile + plugin patch-list」架構，不是單純一份扁平的 `providers:` 設定檔；`settings.yaml`（`@deepseek-ai/dsh-settings-file`）是使用者個人偏好存放處，**不支援環境變數代入**，只能塞字面值。真正能用環境變數指定 API key、不把明碼寫進檔案的，是 `@deepseek-ai/dsh-llm-pi-ai` 套件的 `providers:` 設定裡的 `apiKeyEnv` 欄位。

- [singleuser/dispatcher-provider.yaml](singleuser/dispatcher-provider.yaml)：一份 `--patch` 覆蓋檔，把 `lab-dispatcher` 加進 `llm-pi-ai` 的 provider 清單（`apiKeyEnv: DISPATCHER_API_KEY`、`baseURL: !!js process.env.DISPATCHER_BASE_URL`，都是即時解析，不是寫死值），同時把 `agent-default-model` 改成這個 provider 的 `deepseek-v4-flash-0731`，做成新使用者不用自己設定就有預設模型可用。
- [singleuser/start_harness.py](singleuser/start_harness.py) 第一次啟動時把這份檔案複製一份到 `$DSH_HOME/patches/dispatcher-provider.yaml`（使用者自己的持久化 volume 裡），已存在就不覆蓋——想加自己的其他 provider（例如自己的 OpenAI 帳號），直接編輯這份複製出來的檔案即可，image 更新不會蓋掉你的修改。
- `dsh web` 啟動時會自動帶上 `--patch $DSH_HOME/patches/dispatcher-provider.yaml`。

已經用 `dsh --profile headless` 真實打過一輪模型驗證這個機制確實有效（見 [STATUS.md](STATUS.md)）；沒有做瀏覽器自動化去驗證 dsh 網頁介面本身「按送出」的完整體驗，理論上走同一套底層機制，但實際操作介面沒有自動化測過。

啟動整套堆疊前，`.env` 需要：

```
PANEL_API_TOKEN=<openssl rand -hex 32 生成的值>
SPARK_BASE_URL=http://192.168.101.70:8888/v1
DISPATCHER_CONCURRENCY=1
```

`PANEL_API_TOKEN` 是 Hub 與 Panel 之間的共用密鑰（Hub 用它驗證 Panel 的身份），不是給使用者看的東西，外洩需要重新生成並同時更新 `.env`、重建 `hub` 與 `panel`。

## Container 安全性 Hardening

**Docker socket proxy**：Hub 不再直接掛載 `/var/run/docker.sock`，改成透過 `docker-socket-proxy`（`tecnativa/docker-socket-proxy`）服務中轉，Hub 用 `DOCKER_HOST=tcp://docker-socket-proxy:2375` 連線。proxy 只開放 DockerSpawner 實際會呼叫的端點——依原始碼比對出來的是 `create_container`/`start`/`stop`/`remove_container`/`inspect_container`/`inspect_image`/`create_volume`，對應 compose.yaml 裡的 `POST`/`CONTAINERS`/`IMAGES`/`VOLUMES`/`PING`/`VERSION`，其餘全部顯式設成 `0`。proxy 只加入獨立的 `docker-api`（`internal: true`）網路，跟使用者 container 所在的 `dsh-demo` 網路完全隔開，避免使用者 container 在網路上直接連到它。

**Capabilities**：使用者 container 透過 `c.DockerSpawner.extra_host_config` 設定 `cap_drop: ["ALL"]` 與 `security_opt: ["no-new-privileges:true"]`，`cap_add` 目前刻意留空。

**已實機驗證**（詳細過程見 [STATUS.md](STATUS.md)）：真的 `docker compose up` 起過這一套堆疊，並透過 Hub 的真實 LDAP 登入流程完整 spawn 過一個使用者 container。確認 proxy 只放行需要的端點（`GET /networks` 回 403，其餘允許的端點皆正常）；確認 `cap-drop=ALL` + `no-new-privileges:true` 下，容器仍能完整跑完 dsh 啟動、cookie 交換、頁面渲染全流程，沒有任何 `Operation not permitted` / `permission denied`，`cap_add` 目前不需要加任何東西。若之後 dsh 版本更新引入需要額外權限的新功能，仍照下列流程補：

1. 用 `docker compose up -d` 啟動整套堆疊，正常走一次登入流程。
2. 若容器能建立但 dsh 執行某些操作時噴 `Operation not permitted` / `permission denied`，對照文件的候選 capability 清單，逐一加回 `cap_add`，不要整批開回 `cap_drop: []`。

實測過程中也發現並修好一個既有（非本次改動引入）的競速小 bug：容器剛啟動、`dsh web` 還沒完成 token/cookie 交換的頭幾秒內，第一次載入 `/harness/` 原本會吃到 404。現在 `request_headers()` 會等 cookie 就緒才放行請求（最多等 15 秒），第一次載入會多轉幾秒圈圈，但不會再需要手動重新整理。細節見 [STATUS.md](STATUS.md)。

## 防止繞過排隊系統（鎖死 Spark，只准 Dispatcher 連）

使用者的 container 目前跟 Dispatcher 在同一個 Docker 網路（`dsh-demo`），因為 dsh 要能打到 Dispatcher（`http://dispatcher:8080/v1`）。但這也代表使用者可以在自己的 container 裡直接對 `SPARK_BASE_URL` 的真實 IP:port 發請求，完全繞過 Dispatcher 的排隊與 API key 驗證。

[ops/spark-lockdown/](ops/spark-lockdown/) 有三支腳本處理這個問題：`spark-lockdown-enable.sh`、`spark-lockdown-disable.sh`、`spark-lockdown-status.sh`，邏輯是在 `DOCKER-USER` iptables chain（Docker 官方文件建議的自訂規則掛載點，不會被 Docker 自己的動態規則清掉）插入規則：只放行 Dispatcher 容器目前的 IP 打 Spark，其餘整個 `dsh-demo` 子網段一律擋掉。

**這三支腳本沒有在這個 repo 的開發環境裡實際測試過**——這裡是 Docker Desktop 的 WSL2 整合，真正的 `dockerd` 和 `dsh-demo` bridge 網路其實跑在 Docker Desktop 自己另一個獨立的 VM 裡，這個殼層既沒有 root、也碰不到那個網路 namespace，沒辦法驗證規則是否真的生效。需要在真正跑 `dockerd` 本身、且有 root 權限的 Linux 主機上（例如正式的 Proxmox VM）執行並驗證，執行前請先讀過腳本開頭的註解。

```bash
sudo ./ops/spark-lockdown/spark-lockdown-enable.sh   # 啟用：只放行 Dispatcher
sudo ./ops/spark-lockdown/spark-lockdown-status.sh   # 查目前狀態
sudo ./ops/spark-lockdown/spark-lockdown-disable.sh  # 停用、恢復原狀
```

Dispatcher 容器如果被重建（IP 可能換掉），要重新執行一次 `enable`。

## 驗證

1. 正確 LLDAP 帳密可登入並進入個人頁面。
2. 錯誤密碼與不存在帳號被後端拒絕，直接送登入 POST 也不能繞過。
3. 若設定群組限制，群組外帳號不能登入。
4. 兩個 LLDAP 帳號取得各自獨立的 Harness 工作區。
5. 登出再登入同帳號，仍連回原本的容器。

設定檢查不等同真實 LDAP 連線測試；需提供實際 LLDAP 位址與帳號才可完成端到端驗證。

## 參考

- [LLDAP 服務整合設定](https://github.com/lldap/lldap#general-configuration-guide)
- [JupyterHub LDAPAuthenticator](https://github.com/jupyterhub/ldapauthenticator)

## 目前的 LLDAP 連線

已將提供的設定轉換到本機 `.env`：`ldaps://ldap-server.islab.local:636` 對應主機 `ldap-server.islab.local`、port `636`、`LDAP_TLS_STRATEGY=on_connect`；不再額外使用 StartTLS。使用者搜尋範圍為 `ou=people,dc=islab,dc=local`，以 `uid` 搜尋，等同 `(uid={username})`。

只需自行填入 `.env` 的 `LDAP_BIND_PASSWORD`。管理員群組對應已經實作：`post_auth_hook` 會在每次登入後查詢 `LDAP_ADMIN_GROUP`（獨立於 `LDAP_ALLOWED_GROUPS`，只影響 admin flag，不影響能不能登入），是該群組成員就取得 JupyterHub 管理員權限。目前 `LDAP_ADMIN_GROUP` 留空，等於沒有人是管理員——這是刻意留下的入口，之後在 LLDAP 建立實際的管理員群組（例如 `cn=jupyterhub-admins,ou=groups,dc=islab,dc=local`）後，把該 DN 填進 `.env` 即可生效，不需要改程式。若日後要限制登入群組，使用 `LDAP_ALLOWED_GROUPS` 填寫完整群組 DN，這與管理員群組是各自獨立的設定。

填入密碼後，執行 `docker compose build hub` 與 `docker compose up -d hub`，再開啟 http://localhost:9000。伺服器用的是 NTNU CSIE islab 內部私有 CA（`CN=islab.local`），不能只在主機上信任憑證：CA 憑證已放在 `hub/certs/islab-ca.crt`（透過 compose.yaml 唯讀掛進 Hub 的 `/etc/jupyterhub-certs/`），`.env` 的 `LDAP_CA_CERT_FILE` 已指向這個路徑。已實測 Hub 能用這張憑證跟真實 LLDAP 完成 TLS 交握與 bind/search（見 [STATUS.md](STATUS.md)）。若之後 CA 憑證輪替，用新憑證覆蓋 `hub/certs/islab-ca.crt` 即可，不需要改程式或重建 image（bind mount 是即時生效的）。

## DeepSeek Harness 使用者映像

根目錄 `Dockerfile` 已將簡單網頁換成官方 `@deepseek-ai/dsh`，採已確認 GitHub release 與 npm 套件皆存在的 `0.1.5-rc.2`（預覽版）。建置參數 `DSH_VERSION` 可指定新版；若要每次重新查 npm latest，可使用 `--build-arg DSH_VERSION=latest --no-cache`。

```bash
docker compose --profile build build user-image
docker compose build hub
# LDAP 恢復且 .env 密碼填好後再啟動：
docker compose up -d hub
```

登入後進入 `/user/帳號/harness/`。Jupyter Server Proxy 啟動 Harness、代理 HTTP 與 WebSocket；Harness 僅監聽容器內 loopback，不額外對主機開 port。啟動 token 由容器內程式交換成 session cookie，不需要使用者手動輸入。子路徑適配會重寫 Harness 的 API、plugin 與資源 URL，升級版本後應重新驗證這些路徑。

每個使用者的 Harness 設定與會話位於容器內 `/home/demo/.dsh`。尚未掛載持久化 volume，因此登出可保留，但停止並移除容器會刪除這些資料。更新映像不會更新既有容器，需在 Hub 手動停止舊容器，再登入啟動新容器；若有資料，先備份。

第一次使用需在 Harness 設定模型供應商與 API key；映像不含共用 API key，也不會將 LDAP bind 密碼傳給使用者容器。LLDAP 尚未恢復時，不需要啟動 Hub。

官方專案：https://github.com/deepseek-ai/deepseek-harness

### 本次驗證結果

本機獨立 Jupyter 代理搭配 Harness CLI `0.1.5-rc.1` 已通過瀏覽器測試：首頁、General 設定資料、WebSocket `/api/remote.mux` 均正常，無失敗 HTTP 請求與前端錯誤。路徑重寫單元測試通過。未連線 LDAP、未送出模型請求；Docker 建置因目前 WSL Docker 整合不可用而未完成。測試服務已關閉。

Dockerfile 後續已更新至 `0.1.5-rc.2`，並確認該 npm 版本可下載；此版本尚未重新完成 Docker 建置與瀏覽器測試。
