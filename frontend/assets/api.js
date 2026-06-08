let csrfToken = sessionStorage.getItem("csrfToken") || "";
let progressTimer = null;

async function api(path, options = {}) {
  const started = performance.now();
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData) && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    options.body = JSON.stringify(options.body);
  }
  if (!["GET", "HEAD"].includes((options.method || "GET").toUpperCase()) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, {...options, headers});
  if (response.status === 401) {
    if (path !== "/api/auth/login") {
      if (location.pathname !== "/login") location.href = "/login";
      throw new Error("登录状态已失效");
    }
  }
  const data = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
  if (localStorage.getItem("performanceDebug") === "true") {
    console.debug(`[api] ${options.method || "GET"} ${path}: ${Math.round(performance.now() - started)} ms`);
  }
  if (!response.ok) throw new Error(data.detail || data || `请求失败：${response.status}`);
  return data;
}

async function ensureSession() {
  const me = await api("/api/auth/me");
  if (me.csrf_token) {
    csrfToken = me.csrf_token;
    sessionStorage.setItem("csrfToken", csrfToken);
  }
  return me;
}

function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function formatTime(value) {
  if (!value) return "暂无";
  const text = String(value).trim();
  const normalized = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(text)
    ? `${text.replace(" ", "T")}Z`
    : text;
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return "时间格式错误";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false
  }).format(date);
}

function initTheme() {
  const theme = localStorage.getItem("theme") || "dashboard";
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll("[data-theme-select]").forEach(select => {
    select.value = theme;
    select.addEventListener("change", event => {
      document.documentElement.dataset.theme = event.target.value;
      localStorage.setItem("theme", event.target.value);
      document.querySelectorAll("[data-theme-select]").forEach(item => item.value = event.target.value);
    });
  });
}

function toast(message, type = "success", duration = type === "success" ? 3000 : 6000, action = null) {
  let root = document.getElementById("toastRoot");
  if (!root) {
    root = document.createElement("div");
    root.id = "toastRoot";
    root.className = "toast-root";
    document.body.append(root);
  }
  const item = document.createElement("div");
  item.className = `toast ${type}`;
  item.innerHTML = `<span>${escapeHtml(message)}</span><div class="toast-actions">${
    action ? `<button class="toast-action" type="button">${escapeHtml(action.label)}</button>` : ""
  }<button class="toast-close" aria-label="关闭">×</button></div>`;
  root.append(item);
  const close = () => {
    item.classList.add("leaving");
    setTimeout(() => item.remove(), 180);
  };
  item.querySelector(".toast-close").addEventListener("click", close);
  item.querySelector(".toast-action")?.addEventListener("click", async () => {
    try {
      await action.run();
      close();
    } catch (error) {
      toast(error.message, "error");
    }
  });
  if (duration) setTimeout(close, duration);
}

function confirmAction({title = "确认操作", message, confirmText = "确认", danger = false}) {
  const modal = document.getElementById("confirmModal");
  modal.querySelector("[data-confirm-title]").textContent = title;
  modal.querySelector("[data-confirm-message]").textContent = message;
  const confirmButton = modal.querySelector("[data-confirm-yes]");
  confirmButton.textContent = confirmText;
  confirmButton.classList.toggle("danger", danger);
  modal.classList.add("open");
  return new Promise(resolve => {
    const finish = value => {
      modal.classList.remove("open");
      confirmButton.onclick = null;
      modal.querySelector("[data-confirm-no]").onclick = null;
      resolve(value);
    };
    confirmButton.onclick = () => finish(true);
    modal.querySelector("[data-confirm-no]").onclick = () => finish(false);
  });
}

async function logout() {
  try {
    await api("/api/auth/logout", {method: "POST"});
  } finally {
    sessionStorage.removeItem("csrfToken");
    location.href = "/login";
  }
}

async function refreshProgress() {
  const widget = document.getElementById("progressWidget");
  if (!widget || widget.dataset.hidden === "true" || document.hidden) return false;
  try {
    const data = await api("/api/jobs/progress");
    const active = data.batches.find(batch => batch.queued || batch.running || batch.blocked);
    if (!active) {
      widget.classList.remove("visible");
      return false;
    }
    widget.classList.add("visible");
    widget.querySelector("[data-progress-name]").textContent = active.name;
    widget.querySelector("[data-progress-detail]").textContent =
      `${active.success}/${active.total} 完成 · ${active.running} 运行 · ${active.failed} 失败`;
    widget.querySelector("[data-progress-bar]").style.width = `${active.percent}%`;
    scheduleProgress(3000);
    return true;
  } catch (_) {
    widget.classList.remove("visible");
    return false;
  }
}

async function scheduleProgress(delay = 0) {
  clearTimeout(progressTimer);
  progressTimer = setTimeout(refreshProgress, delay);
}

function initProgress() {
  const widget = document.getElementById("progressWidget");
  if (!widget) return;
  const hidden = localStorage.getItem("progressHidden") === "true";
  widget.dataset.hidden = String(hidden);
  widget.querySelector("[data-progress-hide]").addEventListener("click", () => {
    widget.dataset.hidden = "true";
    widget.classList.remove("visible");
    localStorage.setItem("progressHidden", "true");
  });
  document.querySelectorAll("[data-show-progress]").forEach(button => button.addEventListener("click", () => {
    widget.dataset.hidden = "false";
    localStorage.removeItem("progressHidden");
    scheduleProgress();
  }));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) scheduleProgress();
    else clearTimeout(progressTimer);
  });
  scheduleProgress();
}

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initProgress();
});
