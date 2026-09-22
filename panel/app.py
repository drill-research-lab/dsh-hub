"""Queue status panel, registered as a JupyterHub Service (SSO via Hub OAuth).

Everyone who can log in to the Hub can see the queue (read-only). Only users
whose Hub user model has admin=True (see hub/jupyterhub_config.py's LDAP
admin-group post_auth_hook) can reorder it or change concurrency; both
actions are written to the shared audit log.
"""
import hashlib
import json
import os
import sys

import tornado.httpclient
import tornado.ioloop
import tornado.web
from jupyterhub.services.auth import HubOAuthCallbackHandler, HubOAuthenticated

sys.path.insert(0, "/opt/common")
from api_keys import ApiKeyStore
from dispatch_queue import Queue
from resource_limits import ResourceLimitStore, docker_update_body, DEFAULT_CPU_CORES, DEFAULT_MEMORY_MB, DEFAULT_DISK_MB

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
DEFAULT_CONCURRENCY = int(os.environ.get("DISPATCHER_CONCURRENCY", "1"))
PORT = int(os.environ.get("PORT", "8090"))
PREFIX = os.environ.get("JUPYTERHUB_SERVICE_PREFIX", "/")
# For applying a resource-limit change live to a container that's already
# running, the same way hub/jupyterhub_config.py reaches Docker -- through
# the socket proxy, never the raw socket.
DOCKER_API_URL = os.environ.get("DOCKER_API_URL", "http://docker-socket-proxy:2375")
CONTAINER_NAME_TEMPLATE = os.environ.get("CONTAINER_NAME_TEMPLATE", "dsh-demo-{username}")
# Derived, not os.urandom(32): a random secret would invalidate every
# outstanding cookie (session, XSRF, in-flight OAuth state) on every restart
# of this container. Panel gets restarted a lot (every redeploy), and a
# random secret turned that into real, reproduced login failures -- users
# mid-login when the container recycles get "oauth state does not match".
# Deriving from JUPYTERHUB_API_TOKEN (the same PANEL_API_TOKEN value from
# .env, injected under Hub's expected env var name -- see compose.yaml)
# keeps it stable across restarts without a new secret or volume to manage;
# it changes only when that token is rotated, which already invalidates the
# service's Hub registration too.
COOKIE_SECRET = hashlib.sha256(f"panel-cookie-secret:{os.environ['JUPYTERHUB_API_TOKEN']}".encode()).digest()

INDEX_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>應用密碼與資訊安全實驗室 · 排隊系統</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Manrope:wght@700;800&family=IBM+Plex+Mono:wght@400;500;600&family=Noto+Sans+TC:wght@400;500;700&display=swap">
<style>
  :root {
    --bg: #14171C; --bg-elevated: #181B21; --border: #262B33; --border-soft: #21252C;
    --text: #DDE2E8; --text-muted: #7C8794; --text-faint: #5B6470; --accent: #5FD3A6; --accent-contrast: #0B1410;
  }
  * { box-sizing: border-box; }
  body { font-family: "Noto Sans TC", system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; background: var(--bg); color: var(--text); }
  a { color: var(--accent); }
  h1, h2 { font-family: "Manrope", "Noto Sans TC", sans-serif; font-weight: 700; }
  label, small, code, th { font-family: "IBM Plex Mono", "Noto Sans TC", monospace; }
  .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 0.2rem; }
  .brand svg { flex-shrink: 0; }
  h1 { font-size: 1.25rem; margin: 0; color: #F1F4F7; }
  #hint { color: var(--text-muted); font-size: 0.85em; }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid var(--border-soft); font-size: 0.9em; }
  th { color: var(--text-faint); font-size: 0.75em; letter-spacing: 0.04em; }
  tr[draggable="true"] { cursor: grab; }
  tr.dragging { opacity: 0.4; }
  input, button { font-family: "IBM Plex Mono", "Noto Sans TC", monospace; background: var(--bg-elevated); border: 1px solid var(--border); color: var(--text); padding: 0.4rem 0.6rem; }
  input:focus { outline: 1px solid var(--accent); border-color: var(--accent); }
  button { cursor: pointer; }
  button:hover { border-color: var(--accent); color: var(--accent); }
  #concurrency-save, #key-create { background: var(--accent); color: var(--accent-contrast); border-color: var(--accent); font-weight: 600; }
  #concurrency-save:hover, #key-create:hover { opacity: 0.88; color: var(--accent-contrast); }
  .status { padding: 0.1rem 0.5rem; border: 1px solid var(--border); font-size: 0.85em; }
  .status-queued { color: var(--text-faint); }
  .status-running { color: var(--accent); border-color: var(--accent); }
  #concurrency-form { margin-top: 1.5rem; }
  #audit { margin-top: 2rem; font-size: 0.85em; color: var(--text-muted); }
  #audit ul { list-style: none; padding: 0; }
  #audit li { padding: 0.3rem 0; border-bottom: 1px solid var(--border-soft); }
  #keys-section { margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid var(--border); }
  #keys-section p { color: var(--text-muted); font-size: 0.85em; }
  #new-key-raw { background: var(--bg-elevated); border: 1px solid var(--accent); color: var(--accent); padding: 0.6rem; margin-top: 0.5rem; font-family: "IBM Plex Mono", monospace; word-break: break-all; }
  .revoked-row { color: var(--text-faint); text-decoration: line-through; }
  #limits-section { margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid var(--border); }
  #limits-section p { color: var(--text-muted); font-size: 0.85em; }
  #limits-form label { margin-right: 1rem; }
  #limits-form input { width: 6em; }
  #limits-msg { font-size: 0.85em; margin-top: 0.5rem; min-height: 1.2em; }
  #my-limit { font-size: 0.85em; color: var(--text-muted); }
</style>
</head>
<body>
<div class="brand">
  <svg width="26" height="26" viewBox="0 0 26 26" fill="none" aria-hidden="true">
    <path d="M4 4 L4 22 M4 4 L14 4 M4 13 L11 13" stroke="#5FD3A6" stroke-width="2.2"></path>
    <path d="M22 22 L22 4 M22 22 L12 22" stroke="#5B6470" stroke-width="2.2"></path>
  </svg>
  <h1>islab 排隊佇列 <span id="who"></span></h1>
</div>
<p id="hint"></p>
<table>
  <thead><tr><th></th><th>#</th><th>使用者</th><th>模型</th><th>狀態</th><th>送出時間</th></tr></thead>
  <tbody id="rows"></tbody>
</table>
<div id="concurrency-form" hidden>
  <label>併發數 (N)： <input id="concurrency-input" type="number" min="1" style="width:4em"></label>
  <button id="concurrency-save">儲存</button>
</div>
<div id="audit" hidden>
  <h2 style="font-size:1.05rem">審計紀錄</h2>
  <ul id="audit-list"></ul>
</div>
<div id="keys-section">
  <h2 style="font-size:1.05rem">我的 API Key</h2>
  <p>給不透過 dsh container、自己寫程式呼叫 Dispatcher 的情境使用。金鑰只會在核發當下顯示一次。</p>
  <label>用途標籤： <input id="key-label" placeholder="例如：筆電腳本"></label>
  <button id="key-create">核發新金鑰</button>
  <div id="new-key-raw" hidden></div>
  <table>
    <thead><tr><th>標籤</th><th>建立時間</th><th>最後使用</th><th>狀態</th><th></th></tr></thead>
    <tbody id="keys-rows"></tbody>
  </table>
  <div id="admin-keys" hidden>
    <h2 style="font-size:1.05rem">所有 API Key（管理員）</h2>
    <table>
      <thead><tr><th>使用者</th><th>標籤</th><th>建立時間</th><th>最後使用</th><th>狀態</th><th></th></tr></thead>
      <tbody id="admin-keys-rows"></tbody>
    </table>
  </div>
</div>
<div id="limits-section">
  <h2 style="font-size:1.05rem">容器資源限制</h2>
  <p id="my-limit"></p>
  <div id="limits-admin" hidden>
    <p>CPU/記憶體套用後：已有正在跑的容器會立即用 <code>docker update</code> 生效，還沒 spawn 過的使用者會在下次啟動容器時套用。磁碟配額沒有「立即生效」——它是主機檔案系統層級的設定，Panel 只會把數值記下來，實際套用要等主機上的 <code>apply-disk-quotas.sh</code> 下一次排程執行（見 README）。</p>
    <div id="limits-form">
      <label>使用者： <input id="limit-user" placeholder="帳號"></label>
      <label>CPU (核)： <input id="limit-cpu" type="number" step="0.25" min="0.25" max="8"></label>
      <label>記憶體 (MB)： <input id="limit-memory" type="number" step="256" min="512" max="16384"></label>
      <label>磁碟 (MB)： <input id="limit-disk" type="number" step="1024" min="1024" max="102400" placeholder="留空＝不變"></label>
      <button id="limit-save">套用</button>
    </div>
    <p id="limits-msg"></p>
    <table>
      <thead><tr><th>使用者</th><th>CPU (核)</th><th>記憶體 (MB)</th><th>磁碟 (MB)</th><th>設定時間</th><th>設定者</th></tr></thead>
      <tbody id="limits-rows"></tbody>
    </table>
  </div>
</div>
<script>
const PREFIX = __PANEL_PREFIX__;
let isAdmin = false;
let dragSrc = null;

async function fetchQueue() {
  const resp = await fetch(PREFIX + 'api/queue');
  if (resp.status === 403) { document.getElementById('hint').textContent = '沒有權限存取。'; return; }
  const data = await resp.json();
  isAdmin = !!data.admin;
  document.getElementById('who').textContent = '— ' + data.user + (isAdmin ? ' ' : '');
  document.getElementById('hint').textContent = isAdmin
    ? '管理員：拖曳可調整順序。'
    : '唯讀檢視。';
  document.getElementById('concurrency-form').hidden = !isAdmin;
  document.getElementById('audit').hidden = !isAdmin;
  document.getElementById('admin-keys').hidden = !isAdmin;
  document.getElementById('limits-admin').hidden = !isAdmin;
  renderRows(data.pending);
  if (isAdmin) {
    document.getElementById('concurrency-input').value = data.concurrency;
    fetchAudit();
    fetchKeys(true);
    fetchAllLimits();
  }
}

function renderRows(pending) {
  const tbody = document.getElementById('rows');
  tbody.innerHTML = '';
  pending.forEach((item, i) => {
    const tr = document.createElement('tr');
    tr.dataset.id = item.id;
    if (isAdmin) {
      tr.draggable = true;
      tr.addEventListener('dragstart', () => { dragSrc = tr; tr.classList.add('dragging'); });
      tr.addEventListener('dragend', () => tr.classList.remove('dragging'));
      tr.addEventListener('dragover', (e) => e.preventDefault());
      tr.addEventListener('drop', (e) => {
        e.preventDefault();
        if (dragSrc && dragSrc !== tr) {
          tr.parentNode.insertBefore(dragSrc, tr);
          submitOrder();
        }
      });
    }
    const submitted = new Date(item.submitted_at * 1000).toLocaleTimeString();
    tr.innerHTML = `<td>${isAdmin ? '☰' : ''}</td><td>${i + 1}</td><td>${item.user}</td>` +
      `<td>${item.model || ''}</td><td><span class="status status-${item.status}">${item.status}</span></td>` +
      `<td>${submitted}</td>`;
    tbody.appendChild(tr);
  });
}

async function submitOrder() {
  const order = [...document.getElementById('rows').children].map(tr => tr.dataset.id);
  await fetch(PREFIX + 'api/reorder', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-XSRFToken': getXsrf()},
    body: JSON.stringify({order}),
  });
  fetchQueue();
}

function getXsrf() {
  const m = document.cookie.match(/_xsrf=([^;]+)/);
  return m ? m[1] : '';
}

async function fetchAudit() {
  const resp = await fetch(PREFIX + 'api/audit');
  if (!resp.ok) return;
  const data = await resp.json();
  const list = document.getElementById('audit-list');
  list.innerHTML = '';
  data.audit.slice().reverse().forEach(e => {
    const li = document.createElement('li');
    const when = new Date(e.at * 1000).toLocaleString();
    li.textContent = `${when} — ${e.actor} ${e.action} ${JSON.stringify(e.details)}`;
    list.appendChild(li);
  });
}

document.getElementById('concurrency-save').addEventListener('click', async () => {
  const value = document.getElementById('concurrency-input').value;
  await fetch(PREFIX + 'api/concurrency', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-XSRFToken': getXsrf()},
    body: JSON.stringify({value: Number(value)}),
  });
  fetchQueue();
});

function fmtTime(value) {
  return value ? new Date(Number(value) * 1000).toLocaleString() : '—';
}

function keyStatus(k) {
  if (k.revoked === '1') return 'revoked';
  if (k.expires_at && Number(k.expires_at) < Date.now() / 1000) return 'expired';
  return 'active';
}

const STATUS_LABEL = { active: '使用中', revoked: '已撤銷', expired: '已過期' };

function renderKeyRow(k, withUser) {
  const tr = document.createElement('tr');
  const status = keyStatus(k);
  if (status !== 'active') tr.className = 'revoked-row';
  const label = STATUS_LABEL[status] || status;
  const cells = withUser ? [k.user, k.label, fmtTime(k.created_at), fmtTime(k.last_used_at), label] : [k.label, fmtTime(k.created_at), fmtTime(k.last_used_at), label];
  tr.innerHTML = cells.map(c => `<td>${c || ''}</td>`).join('');
  const actionTd = document.createElement('td');
  if (status === 'active') {
    const btn = document.createElement('button');
    btn.textContent = '撤銷';
    btn.addEventListener('click', () => revokeKey(k.id));
    actionTd.appendChild(btn);
  }
  tr.appendChild(actionTd);
  return tr;
}

async function fetchKeys(alsoAdmin) {
  const resp = await fetch(PREFIX + 'api/keys');
  if (!resp.ok) return;
  const data = await resp.json();
  const tbody = document.getElementById('keys-rows');
  tbody.innerHTML = '';
  data.keys.forEach(k => tbody.appendChild(renderKeyRow(k, false)));

  if (alsoAdmin) {
    const aresp = await fetch(PREFIX + 'api/keys?all=1');
    if (!aresp.ok) return;
    const adata = await aresp.json();
    const atbody = document.getElementById('admin-keys-rows');
    atbody.innerHTML = '';
    adata.keys.forEach(k => atbody.appendChild(renderKeyRow(k, true)));
  }
}

async function revokeKey(keyId) {
  await fetch(PREFIX + 'api/keys/' + keyId + '/revoke', {
    method: 'POST',
    headers: {'X-XSRFToken': getXsrf()},
  });
  fetchKeys(isAdmin);
}

document.getElementById('key-create').addEventListener('click', async () => {
  const label = document.getElementById('key-label').value;
  const resp = await fetch(PREFIX + 'api/keys', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-XSRFToken': getXsrf()},
    body: JSON.stringify({label}),
  });
  const data = await resp.json();
  const box = document.getElementById('new-key-raw');
  box.hidden = false;
  box.textContent = '新的金鑰（只顯示這一次，請立即複製）：' + data.key;
  document.getElementById('key-label').value = '';
  fetchKeys(isAdmin);
});

async function fetchMyLimit() {
  const resp = await fetch(PREFIX + 'api/resource-limits');
  if (!resp.ok) return;
  const data = await resp.json();
  document.getElementById('my-limit').textContent =
    `你的容器目前限制：CPU ${data.cpu} 核、記憶體 ${data.memory_mb} MB、磁碟 ${data.disk_mb} MB` +
    (data.is_default ? '（預設值）' : '（管理員自訂）');
}

async function fetchAllLimits() {
  const resp = await fetch(PREFIX + 'api/resource-limits?all=1');
  if (!resp.ok) return;
  const data = await resp.json();
  const tbody = document.getElementById('limits-rows');
  tbody.innerHTML = '';
  data.limits.forEach(l => {
    const tr = document.createElement('tr');
    const when = l.updated_at ? new Date(Number(l.updated_at) * 1000).toLocaleString() : '—';
    tr.innerHTML = `<td>${l.user}</td><td>${l.cpu}</td><td>${l.memory_mb}</td><td>${l.disk_mb}</td><td>${when}</td><td>${l.updated_by || ''}</td>`;
    tbody.appendChild(tr);
  });
}

document.getElementById('limit-save').addEventListener('click', async () => {
  const user = document.getElementById('limit-user').value.trim();
  const cpu = Number(document.getElementById('limit-cpu').value);
  const memory_mb = Number(document.getElementById('limit-memory').value);
  const diskRaw = document.getElementById('limit-disk').value;
  const msg = document.getElementById('limits-msg');
  if (!user) { msg.textContent = '請輸入使用者帳號。'; return; }
  const body = {cpu, memory_mb};
  if (diskRaw !== '') body.disk_mb = Number(diskRaw);
  const resp = await fetch(PREFIX + 'api/resource-limits/' + encodeURIComponent(user), {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json', 'X-XSRFToken': getXsrf()},
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) {
    msg.textContent = '失敗：' + (data.error || resp.status);
    return;
  }
  const cpuMemMsg = data.applied_live
    ? `CPU/記憶體已儲存並立即套用到 ${user} 目前正在跑的容器。`
    : `CPU/記憶體已儲存，${user} 目前沒有正在跑的容器，下次啟動時套用。`;
  msg.textContent = cpuMemMsg + (diskRaw !== '' ? ` 磁碟配額已記錄，等主機端下次排程套用。` : '');
  document.getElementById('limit-disk').value = '';
  fetchAllLimits();
});

// Sequenced, not fired in parallel: on first load there's no session
// cookie yet, so each of these independently protected calls used to
// kick off its own OAuth handshake at the same time, all racing to set
// the same service-panel-oauth-state cookie and stomping on each other
// -- every login attempt failed with "oauth state does not match" (see
// STATUS.md). Awaiting the first call lets it finish establishing the
// session cookie before the next ones fire, so they just reuse it.
(async () => {
  await fetchQueue();
  await fetchKeys(false);
  await fetchMyLimit();
  setInterval(fetchQueue, 4000);
})();
</script>
</body>
</html>
""".replace("__PANEL_PREFIX__", json.dumps(PREFIX))


class BaseHandler(HubOAuthenticated, tornado.web.RequestHandler):
    def initialize(self, queue, api_keys, resource_limits):
        self.queue = queue
        self.api_keys = api_keys
        self.resource_limits = resource_limits

    def check_xsrf_cookie(self):
        return self.hub_auth.check_xsrf_cookie(self)


class IndexHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.finish(INDEX_HTML)


class QueueApiHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        pending = await self.queue.list_pending()
        concurrency = await self.queue.get_concurrency(DEFAULT_CONCURRENCY)
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({
            "user": self.current_user["name"],
            "admin": bool(self.current_user.get("admin")),
            "pending": pending,
            "concurrency": concurrency,
        }))


class ReorderApiHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self):
        if not self.current_user.get("admin"):
            raise tornado.web.HTTPError(403, "admin only")
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            raise tornado.web.HTTPError(400, "invalid JSON body")
        order = body.get("order")
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            raise tornado.web.HTTPError(400, "order must be a list of request ids")
        new_order = await self.queue.reorder(order, actor=self.current_user["name"])
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({"order": new_order}))


class ConcurrencyApiHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self):
        if not self.current_user.get("admin"):
            raise tornado.web.HTTPError(403, "admin only")
        try:
            body = json.loads(self.request.body or b"{}")
            value = int(body["value"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise tornado.web.HTTPError(400, "value must be an integer")
        new_value = await self.queue.set_concurrency(value, actor=self.current_user["name"])
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({"concurrency": new_value}))


class AuditApiHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        if not self.current_user.get("admin"):
            raise tornado.web.HTTPError(403, "admin only")
        entries = await self.queue.list_audit()
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({"audit": entries}))


class ApiKeysApiHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        username = self.current_user["name"]
        if self.get_query_argument("all", None) and self.current_user.get("admin"):
            keys = await self.api_keys.list_all()
        else:
            keys = await self.api_keys.list_for_user(username)
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({"keys": keys}))

    @tornado.web.authenticated
    async def post(self):
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            raise tornado.web.HTTPError(400, "invalid JSON body")
        label = str(body.get("label") or "").strip()[:200]
        username = self.current_user["name"]
        raw_key, meta = await self.api_keys.issue(username, label=label, issued_by=username)
        await self.queue.audit(username, "issue_api_key", {"key_id": meta["id"], "label": label})
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({"key": raw_key, "meta": meta}))


class ApiKeyRevokeApiHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self, key_id):
        meta = await self.api_keys.get(key_id)
        if not meta:
            raise tornado.web.HTTPError(404, "no such key")
        username = self.current_user["name"]
        if meta["user"] != username and not self.current_user.get("admin"):
            raise tornado.web.HTTPError(403, "not your key")
        await self.api_keys.revoke(key_id)
        await self.queue.audit(username, "revoke_api_key", {"key_id": key_id, "owner": meta["user"]})
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps({"revoked": key_id}))


class ResourceLimitsApiHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        if self.get_query_argument("all", None):
            if not self.current_user.get("admin"):
                raise tornado.web.HTTPError(403, "admin only")
            limits = await self.resource_limits.list_all()
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps({"limits": limits}))
            return
        limit = await self.resource_limits.get(self.current_user["name"])
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps(limit))


class ResourceLimitPatchApiHandler(BaseHandler):
    @tornado.web.authenticated
    async def patch(self, target_user):
        if not self.current_user.get("admin"):
            raise tornado.web.HTTPError(403, "admin only")
        try:
            body = json.loads(self.request.body or b"{}")
            cpu = float(body["cpu"])
            memory_mb = int(body["memory_mb"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise tornado.web.HTTPError(400, "cpu (float) and memory_mb (int) are required")
        # disk_mb is optional: most callers are only changing cpu/memory, and
        # unlike those, disk has no live-apply -- see the class docstring --
        # so there is no urgency pushing every caller to always resend it.
        # Missing means "leave disk_mb where it already effectively is".
        current = await self.resource_limits.get(target_user)
        try:
            disk_mb = int(body["disk_mb"]) if "disk_mb" in body else current["disk_mb"]
        except (TypeError, ValueError):
            raise tornado.web.HTTPError(400, "disk_mb must be an integer")
        actor = self.current_user["name"]
        try:
            limit = await self.resource_limits.set(target_user, cpu, memory_mb, disk_mb, actor=actor)
        except ValueError as exc:
            self.set_status(400)
            self.set_header("Content-Type", "application/json")
            self.finish(json.dumps({"error": str(exc)}))
            return
        applied_live = await self._apply_live(target_user, cpu, memory_mb)
        await self.queue.audit(
            actor, "set_resource_limit",
            {"user": target_user, "cpu": cpu, "memory_mb": memory_mb, "disk_mb": disk_mb, "applied_live": applied_live},
        )
        self.set_header("Content-Type", "application/json")
        # cpu/memory take effect per applied_live above; disk_mb is only
        # ever recorded here -- ops/disk-quota/apply-disk-quotas.sh picks it
        # up on its own schedule, there is no "live" for it to report.
        self.finish(json.dumps({**limit, "applied_live": applied_live, "disk_quota_pending": True}))

    async def _apply_live(self, user, cpu, memory_mb):
        """Best-effort `docker update` on a container that's already
        running, through the same socket proxy Hub uses to spawn. A user
        with no running container yet (never spawned, or currently stopped)
        just gets the limit recorded for their next spawn -- see
        hub/jupyterhub_config.py's pre_spawn_hook -- so a 404 here is a
        normal, expected outcome, not an error to surface."""
        name = CONTAINER_NAME_TEMPLATE.format(username=user)
        client = tornado.httpclient.AsyncHTTPClient()
        try:
            await client.fetch(
                f"{DOCKER_API_URL}/containers/{name}/update",
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps(docker_update_body(cpu, memory_mb)),
            )
            return True
        except tornado.httpclient.HTTPClientError as exc:
            if exc.code == 404:
                return False
            raise


def make_app(queue, api_keys, resource_limits):
    settings = {"cookie_secret": COOKIE_SECRET}
    handler_kwargs = dict(queue=queue, api_keys=api_keys, resource_limits=resource_limits)
    return tornado.web.Application(
        [
            (PREFIX + "oauth_callback", HubOAuthCallbackHandler),
            (PREFIX + "?", IndexHandler, handler_kwargs),
            (PREFIX + "api/queue", QueueApiHandler, handler_kwargs),
            (PREFIX + "api/reorder", ReorderApiHandler, handler_kwargs),
            (PREFIX + "api/concurrency", ConcurrencyApiHandler, handler_kwargs),
            (PREFIX + "api/audit", AuditApiHandler, handler_kwargs),
            (PREFIX + "api/keys", ApiKeysApiHandler, handler_kwargs),
            (PREFIX + r"api/keys/([0-9a-f]+)/revoke", ApiKeyRevokeApiHandler, handler_kwargs),
            (PREFIX + "api/resource-limits", ResourceLimitsApiHandler, handler_kwargs),
            (PREFIX + r"api/resource-limits/([^/]+)", ResourceLimitPatchApiHandler, handler_kwargs),
        ],
        **settings,
    )


def main():
    queue = Queue(REDIS_URL)
    api_keys = ApiKeyStore(queue.redis)
    resource_limits = ResourceLimitStore(
        queue.redis,
        default_cpu=float(os.environ.get("DEFAULT_CPU_CORES", DEFAULT_CPU_CORES)),
        default_memory_mb=int(os.environ.get("DEFAULT_MEMORY_MB", DEFAULT_MEMORY_MB)),
        default_disk_mb=int(os.environ.get("DEFAULT_DISK_MB", DEFAULT_DISK_MB)),
    )
    app = make_app(queue, api_keys, resource_limits)
    # xheaders=True: trust X-Forwarded-Proto/-For/-Host from the reverse
    # proxy chain (Caddy -> Hub -> jupyter-server-proxy -> here). Without
    # it, Tornado sees this connection as plain http (its own listener has
    # no TLS) and builds OAuth redirect/callback URLs with the wrong scheme
    # -- browser is on https, so the state cookie set under Tornado's
    # (wrong) http assumption never round-trips correctly, and every OAuth
    # handshake fails with "oauth state does not match" (see STATUS.md).
    app.listen(PORT, xheaders=True)
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
