// Keep Harness RPC channel names unchanged; scope only browser network URLs.
(() => {
  const prefix = __HARNESS_PREFIX__;
  function scoped(value) {
    const url = new URL(value, document.baseURI);
    if (url.host === location.host && /^\/(api|open-in-app)(\/|$)/.test(url.pathname)) {
      url.pathname = prefix + url.pathname;
    }
    return url.href;
  }
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    if (input instanceof Request) {
      const url = scoped(input.url);
      return originalFetch(url === input.url ? input : new Request(url, input), init);
    }
    return originalFetch(scoped(input), init);
  };
  const OriginalWebSocket = window.WebSocket;
  window.WebSocket = class extends OriginalWebSocket {
    constructor(url, protocols) {
      super(scoped(url), ...(protocols === undefined ? [] : [protocols]));
    }
  };
  const OriginalEventSource = window.EventSource;
  window.EventSource = class extends OriginalEventSource {
    constructor(url, options) { super(scoped(url), options); }
  };

  // Queue-position status bar, shown above the message composer.
  //
  // Anchored on [data-composer-card="true"] because it's a deliberate
  // data-* hook, not a build-hashed CSS module class (those look like
  // "uV2eYG_card" and change across dsh builds) -- much more likely to
  // survive a dsh update. If a future dsh version drops this attribute the
  // bar just stops appearing; nothing else on the page depends on it.
  const QUEUE_STATUS_URL = __QUEUE_STATUS_URL__;
  const BAR_ID = 'islab-queue-status';
  const STATUS_LABEL = { queued: '排隊中', running: '執行中' };

  function ensureBar() {
    const card = document.querySelector('[data-composer-card="true"]');
    if (!card || !card.parentNode) return null;
    let bar = document.getElementById(BAR_ID);
    if (!bar) {
      bar = document.createElement('div');
      bar.id = BAR_ID;
      // Colors/radius/font lifted from dsh's own live computed styles
      // (composer card bg, label-secondary text, and the pill radius its
      // own buttons use) rather than invented, so this reads as part of
      // dsh instead of a bolted-on widget.
      bar.style.cssText = [
        'display:none', 'align-items:center', 'gap:8px',
        'margin:0 0 8px', 'padding:6px 14px',
        'font:13px/20px -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei","Helvetica Neue",Helvetica,Arial,sans-serif',
        'color:#CFD3D6', 'background:#2C2C2E',
        'border-radius:999px', 'width:fit-content',
      ].join(';');
    }
    // Only touch the DOM when actually needed: insertBefore is not a true
    // no-op even when re-inserting a node into the exact spot it already
    // occupies (it still fires a childList mutation), and this function is
    // itself called from a MutationObserver callback -- an unconditional
    // insertBefore here would retrigger the observer on its own mutation,
    // forever.
    if (bar.nextSibling !== card) {
      card.parentNode.insertBefore(bar, card);
    }
    return bar;
  }

  async function refreshBar() {
    const bar = ensureBar();
    if (!bar) return;
    try {
      const resp = await fetch(QUEUE_STATUS_URL, { cache: 'no-store' });
      const data = await resp.json();
      if (data.status === 'queued' || data.status === 'running') {
        bar.textContent = data.status === 'queued'
          ? `${STATUS_LABEL.queued} · 目前排在第 ${data.position} 位`
          : STATUS_LABEL.running;
        bar.style.display = 'flex';
      } else {
        bar.style.display = 'none';
      }
    } catch (err) {
      bar.style.display = 'none';
    }
  }

  const observer = new MutationObserver(() => ensureBar());
  window.addEventListener('DOMContentLoaded', () => {
    observer.observe(document.body, { childList: true, subtree: true });
    refreshBar();
    setInterval(refreshBar, 4000);
  });
})();
