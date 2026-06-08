let adminBookmarks = [];
let adminJobs = [];
let adminLogs = [];
let healthItems = [];
let providerData = {providers: [], routes: {}};
const selected = new Set();
let adminEditorInitial = null;
let adminSearchTimer = null;
const PAGE_SIZE_OPTIONS = [10, 20, 30, 50, 100];
const listCache = new Map();
const requestControllers = new Map();
const loadedTabs = new Set();

function savedPageSize(name) {
  const value = Number(localStorage.getItem(`adminPageSize:${name}`));
  return PAGE_SIZE_OPTIONS.includes(value) ? value : 20;
}

const pages = {
  bookmarks: {page: 1, total: 0, pageSize: savedPageSize("bookmarks")},
  jobs: {page: 1, total: 0, pageSize: savedPageSize("jobs")},
  logs: {page: 1, total: 0, pageSize: savedPageSize("logs")},
  health: {page: 1, total: 0, pageSize: savedPageSize("health")}
};
const sectionTitles = {
  bookmarks: "收藏管理", import: "导入与同步", models: "AI 模型",
  search: "搜索索引", health: "链接健康", jobs: "任务中心", logs: "系统日志", account: "账号安全"
};

async function listRequest(section, url, force = false) {
  const cached = listCache.get(url);
  if (!force && cached && Date.now() - cached.time < 30_000) return cached.data;
  requestControllers.get(section)?.abort();
  const controller = new AbortController();
  requestControllers.set(section, controller);
  try {
    const data = await api(url, {signal: controller.signal});
    listCache.set(url, {time: Date.now(), data});
    return data;
  } catch (error) {
    if (error.name === "AbortError") return null;
    throw error;
  } finally {
    if (requestControllers.get(section) === controller) requestControllers.delete(section);
  }
}

function invalidateSection(section) {
  for (const key of listCache.keys()) {
    if (key.includes(`/${section}`) || (section === "bookmarks" && key.includes("/api/bookmarks"))) {
      listCache.delete(key);
    }
  }
}

function loadPageFor(name, force = false) {
  if (name === "bookmarks") return loadBookmarks(force);
  if (name === "jobs") return loadJobs(force);
  if (name === "logs") return loadLogs(force);
  if (name === "health") return loadHealth(force);
}

function syncPageSizeControls(name) {
  document.querySelectorAll(`[data-page-size-for="${name}"], #${name === "bookmarks" ? "bookmark" : name}Pager [data-page-size]`).forEach(select => {
    select.value = String(pages[name].pageSize);
  });
}

function setPageSize(name, value) {
  const state = pages[name];
  state.pageSize = Number(value);
  state.page = 1;
  localStorage.setItem(`adminPageSize:${name}`, String(state.pageSize));
  invalidateSection(name === "health" ? "health-checks" : name);
  syncPageSizeControls(name);
  loadPageFor(name, true);
}

function safeAdminTarget(item) {
  if (item.local_copy_url?.startsWith("/api/files/")) return item.local_copy_url;
  try {
    const value = new URL(item.url);
    return ["http:", "https:"].includes(value.protocol) ? item.url : "";
  } catch { return ""; }
}

function bookmarkRows() {
  document.getElementById("bookmarkRows").innerHTML = adminBookmarks.map(item => `<tr>
    <td><input type="checkbox" data-select="${item.id}" ${selected.has(item.id) ? "checked" : ""}></td>
    <td><strong>${escapeHtml(item.title)}</strong><br>${safeAdminTarget(item) ? `<a href="${escapeHtml(safeAdminTarget(item))}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.url)}</a>` : escapeHtml(item.url)}</td>
    <td>${escapeHtml(item.final_category)}<br>${(item.tags || []).map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join(" ")}</td>
    <td>${(item.folder_paths || []).map(path => escapeHtml(path || "根目录")).join("<br>") || "无文件夹"}</td>
    <td>抓取：${escapeHtml(item.crawl_status)}<br>AI：${escapeHtml(item.ai_status)}</td>
    <td><button class="button secondary" data-edit="${item.id}">编辑</button> <button class="button danger" data-delete="${item.id}">删除</button></td>
  </tr>`).join("");
  document.querySelectorAll("[data-select]").forEach(box => box.addEventListener("change", () => box.checked ? selected.add(box.dataset.select) : selected.delete(box.dataset.select)));
  document.querySelectorAll("[data-edit]").forEach(button => button.addEventListener("click", () => openAdminModal(button.dataset.edit)));
  document.querySelectorAll("[data-delete]").forEach(button => button.addEventListener("click", async () => {
    const confirmed = await confirmAction({title:"删除收藏", message:"这条收藏将被软删除，之后仍可恢复。", confirmText:"删除", danger:true});
    if (!confirmed) return;
    try {
      await api(`/api/bookmarks/${button.dataset.delete}`, {method:"DELETE"});
      toast("收藏已删除");
      adminBookmarks = adminBookmarks.filter(item => item.id !== button.dataset.delete);
      pages.bookmarks.total = Math.max(0, pages.bookmarks.total - 1);
      selected.delete(button.dataset.delete);
      invalidateSection("bookmarks");
      if (!adminBookmarks.length && pages.bookmarks.page > 1) {
        pages.bookmarks.page -= 1;
        await loadBookmarks(true);
      } else {
        bookmarkRows();
        renderPager("bookmarkPager", "bookmarks", pages.bookmarks, page => {
          pages.bookmarks.page = page;
          loadBookmarks();
        });
      }
    } catch (error) { toast(error.message, "error"); }
  }));
}

async function loadBookmarks(force = false) {
  const state = pages.bookmarks;
  const query = document.getElementById("adminSearch").value.trim();
  const result = await listRequest("bookmarks", `/api/bookmarks?page=${state.page}&page_size=${state.pageSize}&compact=true&q=${encodeURIComponent(query)}`, force);
  if (!result) return;
  adminBookmarks = result.items;
  state.total = result.total;
  bookmarkRows();
  renderPager("bookmarkPager", "bookmarks", state, page => {
    state.page = page;
    loadBookmarks();
  });
}

async function openAdminModal(id = "") {
  let item = null;
  if (id) {
    try {
      item = await api(`/api/bookmarks/${id}`);
    } catch (error) {
      return toast(error.message, "error");
    }
  }
  const form = document.getElementById("adminForm");
  form.reset();
  Object.entries(item || {}).forEach(([key,value]) => {
    if (form.elements[key]) form.elements[key].value = key === "tags" ? (value || []).join(", ") : (value ?? "");
  });
  if (item) form.elements.category.value = item.manual_category || item.final_category || "";
  form.elements.id.value = id;
  document.getElementById("adminModal").classList.add("open");
  adminEditorInitial = Object.fromEntries(new FormData(form));
}

function closeAdminModal() {
  document.getElementById("adminModal").classList.remove("open");
}

async function runBatch(action) {
  const ids = [...selected];
  if (!ids.length) return toast("请先选择收藏", "error");
  if (action === "delete") {
    const confirmed = await confirmAction({
      title: "批量删除",
      message: `确认软删除选中的 ${ids.length} 条收藏？`,
      confirmText: "批量删除",
      danger: true
    });
    if (!confirmed) return;
  }
  try {
    await api("/api/bookmarks/batch", {method:"POST", body:{ids, action, confirm: action === "delete"}});
    selected.clear();
    toast(action === "delete" ? "批量删除完成" : "任务已加入后台队列");
    await loadBookmarks();
    refreshProgress();
  } catch (error) { toast(error.message, "error"); }
}

function jobRows() {
  document.getElementById("jobRows").innerHTML = adminJobs.map(job => `<tr>
    <td>${escapeHtml(job.job_type)}</td>
    <td class="${job.status === "failed" ? "status-failed" : ""}">${escapeHtml(job.status)}</td>
    <td>${job.attempts}/${job.max_attempts}</td><td>${escapeHtml(job.error || "")}</td>
    <td>${formatTime(job.created_at)}</td>
    <td>${["failed","blocked"].includes(job.status) ? `<button class="button secondary" data-retry="${job.id}">重试</button>` : ""}</td>
  </tr>`).join("");
  document.querySelectorAll("[data-retry]").forEach(button => button.addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${button.dataset.retry}/retry`, {method:"POST"});
      const job = adminJobs.find(item => item.id === button.dataset.retry);
      if (job) Object.assign(job, {status:"queued", attempts:0, error:""});
      invalidateSection("jobs");
      jobRows();
      toast("任务已重新排队");
      refreshProgress();
    } catch (error) { toast(error.message, "error"); }
  }));
}

async function loadJobs(force = false) {
  const state = pages.jobs;
  const status = document.getElementById("jobStatus").value;
  const result = await listRequest("jobs", `/api/jobs?page=${state.page}&page_size=${state.pageSize}&status=${encodeURIComponent(status)}`, force);
  if (!result) return;
  adminJobs = result.items;
  jobRows();
  state.total = result.total;
  renderPager("jobPager", "jobs", state, page => {
    state.page = page;
    loadJobs();
  });
}

async function loadLogs(force = false) {
  const state = pages.logs;
  const level = document.getElementById("logLevel").value;
  const result = await listRequest("logs", `/api/logs?page=${state.page}&page_size=${state.pageSize}&level=${encodeURIComponent(level)}`, force);
  if (!result) return;
  adminLogs = result.items;
  document.getElementById("logRows").innerHTML = adminLogs.map(log => `<tr>
    <td>${escapeHtml(log.event_type)}</td><td>${escapeHtml(log.level)}</td>
    <td>${escapeHtml(log.message)}</td><td>${escapeHtml(JSON.stringify(log.details))}</td>
    <td>${formatTime(log.created_at)}</td>
  </tr>`).join("");
  state.total = result.total;
  renderPager("logPager", "logs", state, page => {
    state.page = page;
    loadLogs();
  });
}

function healthRows() {
  const labels = {ok:"正常", redirected:"重定向", failed:"失败", timeout:"超时", unsupported:"不支持", pending:"待检查"};
  document.getElementById("healthRows").innerHTML = healthItems.map(item => `<tr>
    <td><strong>${escapeHtml(item.title || item.url)}</strong><br><a href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(item.url)}</a></td>
    <td class="status-${escapeHtml(item.status)}">${escapeHtml(labels[item.status] || item.status)}</td>
    <td>${item.http_status ?? "—"}</td>
    <td>${item.latency_ms == null ? "—" : `${item.latency_ms} ms`}</td>
    <td>${item.error ? escapeHtml(item.error) : escapeHtml(item.final_url || "—")}</td>
    <td>${formatTime(item.checked_at)}</td>
    <td><button class="button secondary" data-check-health="${item.bookmark_id}">重新检查</button></td>
  </tr>`).join("") || '<tr><td colspan="7" class="muted">暂无检查记录。可以先检查选中收藏或全部收藏。</td></tr>';
  document.querySelectorAll("[data-check-health]").forEach(button => button.addEventListener("click", async () => {
    try {
      await api(`/api/health-checks/${button.dataset.checkHealth}/run`, {method:"POST"});
      toast("健康检查已加入队列");
      invalidateSection("health-checks");
      refreshProgress();
    } catch (error) { toast(error.message, "error"); }
  }));
}

async function loadHealth(force = false) {
  const state = pages.health;
  const status = document.getElementById("healthStatus").value;
  const result = await listRequest("health-checks", `/api/health-checks?page=${state.page}&page_size=${state.pageSize}&status=${encodeURIComponent(status)}`, force);
  if (!result) return;
  healthItems = result.items;
  state.total = result.total;
  healthRows();
  renderPager("healthPager", "health", state, page => {
    state.page = page;
    loadHealth();
  });
}

async function loadHealthSettings() {
  const settings = await api("/api/settings/health-check");
  const form = document.getElementById("healthSettingsForm");
  form.elements.enabled.checked = settings.enabled;
  form.elements.interval_days.value = settings.interval_days;
  document.getElementById("healthLastRun").textContent = settings.last_scheduled_at
    ? `上次自动排队：${formatTime(settings.last_scheduled_at)}`
    : "尚未自动运行";
}

function renderPager(id, name, state, onPage) {
  const pageCount = Math.max(Math.ceil(state.total / state.pageSize), 1);
  if (state.page > pageCount) state.page = pageCount;
  const start = state.total ? (state.page - 1) * state.pageSize + 1 : 0;
  const end = Math.min(state.page * state.pageSize, state.total);
  const root = document.getElementById(id);
  root.innerHTML = `<button class="button ghost" data-page="${state.page - 1}" ${state.page <= 1 ? "disabled" : ""}>上一页</button>
    <span>${start}-${end} / ${state.total} · 第 ${state.page}/${pageCount} 页</span>
    <label>每页 <select data-page-size>${PAGE_SIZE_OPTIONS.map(value => `<option value="${value}" ${value === state.pageSize ? "selected" : ""}>${value}</option>`).join("")}</select></label>
    <button class="button ghost" data-page="${state.page + 1}" ${state.page >= pageCount ? "disabled" : ""}>下一页</button>`;
  root.querySelectorAll("[data-page]:not(:disabled)").forEach(button =>
    button.addEventListener("click", () => onPage(Number(button.dataset.page)))
  );
  root.querySelector("[data-page-size]").addEventListener("change", event => {
    setPageSize(name, event.target.value);
  });
  syncPageSizeControls(name);
}

async function loadTokens() {
  const tokens = await api("/api/settings/tokens");
  document.getElementById("tokenList").innerHTML = tokens.map(token => `<div class="token-row">
    <span><strong>${escapeHtml(token.name)}</strong><br><small>${escapeHtml(token.token_prefix)} · ${token.revoked_at ? "已撤销" : `最后使用：${formatTime(token.last_used_at)}`}</small></span>
    ${token.revoked_at ? "" : `<button class="button danger" data-revoke="${token.id}">撤销</button>`}
  </div>`).join("");
  document.querySelectorAll("[data-revoke]").forEach(button => button.addEventListener("click", async () => {
    const confirmed = await confirmAction({title:"撤销 Token", message:"撤销后使用该 Token 的同步客户端将立即失效。", danger:true});
    if (!confirmed) return;
    await api(`/api/settings/tokens/${button.dataset.revoke}`, {method:"DELETE"});
    toast("Token 已撤销");
    await loadTokens();
  }));
}

function modelOptions(capability = "") {
  return ['<option value="">继承默认模型</option>', ...providerData.providers.flatMap(provider =>
    provider.models
      .filter(model => model.enabled && (!capability || model.capabilities.includes(capability)))
      .map(model => `<option value="${model.id}">${escapeHtml(provider.name)} / ${escapeHtml(model.display_name)}</option>`)
  )].join("");
}

function renderProviders() {
  document.getElementById("providerList").innerHTML = providerData.providers.map(provider => `<article class="panel stack provider-card">
    <div class="provider-head"><div><span class="eyebrow">${provider.enabled ? "ENABLED" : "DISABLED"}</span><h2>${escapeHtml(provider.name)}</h2><p class="muted">${escapeHtml(provider.base_url)} · Key ${provider.api_key_configured ? "已配置" : "未配置"}</p></div><div><button class="button secondary" data-edit-provider="${provider.id}">编辑</button> <button class="button danger" data-delete-provider="${provider.id}">删除</button></div></div>
    <div class="model-list">${provider.models.map(model => `<div class="model-row"><span><strong>${escapeHtml(model.display_name)}</strong><br><small>${escapeHtml(model.model_name)}</small><br>${model.capabilities.map(value => `<i class="capability">${escapeHtml(value)}</i>`).join("")}</span><span><button class="button secondary" data-edit-model="${model.id}">编辑</button> <button class="button danger" data-delete-model="${model.id}">删除</button></span></div>`).join("") || '<p class="muted">暂无模型</p>'}</div>
  </article>`).join("");
  const routeForm = document.getElementById("routeForm");
  for (const [task, capability] of Object.entries({default:"chat", classification:"classification", summary:"summary", embedding:"embedding", ai_search:"chat"})) {
    routeForm.elements[task].innerHTML = modelOptions(capability);
    routeForm.elements[task].value = providerData.routes[task] || "";
  }
  const providerSelect = document.getElementById("modelForm").elements.provider_id;
  providerSelect.innerHTML = providerData.providers.map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join("");
  bindProviderActions();
}

function findModel(id) {
  for (const provider of providerData.providers) {
    const model = provider.models.find(item => item.id === id);
    if (model) return {...model, provider_id: provider.id};
  }
  return null;
}

function bindProviderActions() {
  document.querySelectorAll("[data-edit-provider]").forEach(button => button.addEventListener("click", () => openProvider(button.dataset.editProvider)));
  document.querySelectorAll("[data-edit-model]").forEach(button => button.addEventListener("click", () => openModel(button.dataset.editModel)));
  document.querySelectorAll("[data-delete-provider]").forEach(button => button.addEventListener("click", async () => {
    if (!await confirmAction({title:"删除供应商", message:"只有未被任务路由和搜索索引使用的供应商才能删除。", danger:true})) return;
    try { await api(`/api/settings/providers/${button.dataset.deleteProvider}`, {method:"DELETE"}); toast("供应商已删除"); await loadProviders(); }
    catch (error) { toast(error.message, "error"); }
  }));
  document.querySelectorAll("[data-delete-model]").forEach(button => button.addEventListener("click", async () => {
    if (!await confirmAction({title:"删除模型", message:"只有未被任务路由和搜索索引使用的模型才能删除。", danger:true})) return;
    try { await api(`/api/settings/models/${button.dataset.deleteModel}`, {method:"DELETE"}); toast("模型已删除"); await loadProviders(); }
    catch (error) { toast(error.message, "error"); }
  }));
}

async function loadProviders() {
  providerData = await api("/api/settings/providers");
  renderProviders();
}

function openProvider(id = "") {
  const provider = providerData.providers.find(item => item.id === id);
  const form = document.getElementById("providerForm");
  form.reset();
  form.elements.id.value = id;
  if (provider) {
    form.elements.name.value = provider.name;
    form.elements.base_url.value = provider.base_url;
    form.elements.enabled.checked = provider.enabled;
    form.elements.api_key.placeholder = provider.api_key_configured ? "已配置，留空表示不修改" : "输入 API Key";
  }
  document.getElementById("providerModal").classList.add("open");
}

function openModel(id = "") {
  const model = findModel(id);
  const form = document.getElementById("modelForm");
  form.reset();
  form.elements.id.value = id;
  if (model) {
    form.elements.provider_id.value = model.provider_id;
    form.elements.provider_id.disabled = true;
    form.elements.display_name.value = model.display_name;
    form.elements.model_name.value = model.model_name;
    form.elements.enabled.checked = model.enabled;
    form.querySelectorAll('[name="capability"]').forEach(box => box.checked = model.capabilities.includes(box.value));
  } else {
    form.elements.provider_id.disabled = false;
  }
  document.getElementById("modelModal").classList.add("open");
}

async function testCurrentModel() {
  const form = document.getElementById("modelForm");
  const capability = [...form.querySelectorAll('[name="capability"]:checked')].map(box => box.value)[0] || "chat";
  const payload = {
    provider_id: form.elements.provider_id.value,
    model_id: form.elements.id.value || null,
    model_name: form.elements.model_name.value,
    capability
  };
  try {
    const result = await api("/api/settings/models/test", {method:"POST", body:payload});
    toast(`连接成功，耗时 ${result.latency_ms} ms${result.dimensions ? `，维度 ${result.dimensions}` : ""}`);
  } catch (error) { toast(error.message, "error"); }
}

async function loadSearchStatus() {
  const status = await api("/api/search/status");
  document.getElementById("searchIndexStatus").innerHTML = [
    ["收藏总数", status.total], ["全文索引", status.fts_indexed], ["已建立向量", status.indexed],
    ["向量待更新", status.embedding_configured ? status.pending : "未配置模型"],
    ["失败", status.failed], ["Embedding 模型", status.model_name || "未配置"]
  ].map(([label,value]) => {
    const statusClass = label === "已建立向量" ? "metric-success" : label === "向量待更新" ? "metric-warning" : label === "失败" ? "metric-danger" : "";
    return `<div class="metric ${statusClass}"><span class="muted">${label}</span><strong>${escapeHtml(value)}</strong></div>`;
  }).join("");
}

function switchTab(tabName) {
  document.querySelectorAll(".tab-content").forEach(tab => tab.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.tab === tabName));
  document.getElementById(`tab-${tabName}`).classList.add("active");
  document.getElementById("sectionTitle").textContent = sectionTitles[tabName];
  document.getElementById("adminSidebar").classList.remove("open");
  const firstLoad = !loadedTabs.has(tabName);
  loadedTabs.add(tabName);
  if (tabName === "bookmarks") loadBookmarks();
  if (tabName === "import" && firstLoad) loadTokens();
  if (tabName === "jobs") loadJobs();
  if (tabName === "logs") loadLogs();
  if (tabName === "search" && firstLoad) loadSearchStatus();
  if (tabName === "models" && firstLoad) loadProviders();
  if (tabName === "health") {
    loadHealth();
    if (firstLoad) loadHealthSettings();
  }
  if (tabName === "account" && firstLoad) {
    api("/api/settings/account").then(account => {
      document.getElementById("accountForm").elements.username.value = account.username;
    }).catch(error => toast(error.message, "error"));
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await ensureSession();
  document.querySelectorAll("[data-page-size-for]").forEach(select => {
    const name = select.dataset.pageSizeFor;
    select.innerHTML = PAGE_SIZE_OPTIONS.map(value => `<option value="${value}">${value}</option>`).join("");
    select.value = String(pages[name].pageSize);
    select.addEventListener("change", event => setPageSize(name, event.target.value));
  });
  document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  document.getElementById("menuButton").addEventListener("click", () => document.getElementById("adminSidebar").classList.toggle("open"));
  document.getElementById("adminSearch").addEventListener("input", () => {
    clearTimeout(adminSearchTimer);
    adminSearchTimer = setTimeout(() => {
      pages.bookmarks.page = 1;
      loadBookmarks(true);
    }, 250);
  });
  document.getElementById("adminAdd").addEventListener("click", () => openAdminModal());
  document.getElementById("selectAll").addEventListener("change", event => {
    adminBookmarks.forEach(item => event.target.checked ? selected.add(item.id) : selected.delete(item.id));
    bookmarkRows();
  });
  document.getElementById("jobStatus").addEventListener("change", () => {
    pages.jobs.page = 1;
    invalidateSection("jobs");
    loadJobs(true);
  });
  document.getElementById("logLevel").addEventListener("change", () => {
    pages.logs.page = 1;
    invalidateSection("logs");
    loadLogs(true);
  });
  document.getElementById("healthStatus").addEventListener("change", () => {
    pages.health.page = 1;
    invalidateSection("health-checks");
    loadHealth(true);
  });
  document.querySelectorAll("[data-batch]").forEach(button => button.addEventListener("click", () => runBatch(button.dataset.batch)));
  document.querySelectorAll("[data-close-modal]").forEach(button => button.addEventListener("click", () => button.closest(".modal").classList.remove("open")));
  document.getElementById("addProvider").addEventListener("click", () => openProvider());
  document.getElementById("addModel").addEventListener("click", () => openModel());
  document.getElementById("testModel").addEventListener("click", testCurrentModel);
  document.getElementById("testProvider").addEventListener("click", async () => {
    const form = document.getElementById("providerForm");
    try {
      const result = await api("/api/settings/providers/test", {
        method: "POST",
        body: {
          provider_id: form.elements.id.value || null,
          base_url: form.elements.base_url.value,
          api_key: form.elements.api_key.value
        }
      });
      toast(`供应商连接成功，耗时 ${result.latency_ms} ms，可见 ${result.models} 个模型`);
    } catch (error) { toast(error.message, "error"); }
  });

  document.getElementById("adminForm").addEventListener("submit", async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    const id = values.id;
    delete values.id;
    values.tags = values.tags.split(",").map(value => value.trim()).filter(Boolean);
    let payload = values;
    if (id) {
      payload = {};
      for (const [key, value] of Object.entries(values)) {
        const initialValue = key === "tags" ? (adminEditorInitial.tags || "").split(",").map(item => item.trim()).filter(Boolean) : adminEditorInitial[key];
        if (JSON.stringify(value) !== JSON.stringify(initialValue)) payload[key] = value;
      }
    }
    try {
      const updated = (!id || Object.keys(payload).length)
        ? await api(id ? `/api/bookmarks/${id}` : "/api/bookmarks", {method:id ? "PATCH" : "POST", body:payload})
        : null;
      closeAdminModal();
      toast(id ? "收藏已保存" : "链接已添加");
      invalidateSection("bookmarks");
      if (id && updated) {
        const index = adminBookmarks.findIndex(item => item.id === id);
        if (index >= 0) {
          adminBookmarks[index] = {
            ...adminBookmarks[index],
            ...updated,
            folder_paths: (updated.sources || []).filter(source => source.is_active && source.folder_path).map(source => source.folder_path)
          };
          bookmarkRows();
        }
      } else if (!id) {
        await loadBookmarks(true);
      }
    } catch (error) { toast(error.message, "error"); }
  });

  document.getElementById("importForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const result = await api("/api/import/edge-html", {method:"POST", body:new FormData(event.target)});
      document.getElementById("importResult").textContent = `完成：${result.total} 条，新增 ${result.added} 条`;
      toast("Edge HTML 导入完成");
      invalidateSection("bookmarks");
      await loadBookmarks(true);
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("tokenForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const result = await api("/api/settings/tokens", {method:"POST", body:{name:new FormData(event.target).get("name")}});
      document.getElementById("tokenResult").innerHTML = `<div class="notice">请立即保存，仅显示一次：<code>${escapeHtml(result.token)}</code></div>`;
      event.target.reset();
      toast("Token 已生成");
      await loadTokens();
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("providerForm").addEventListener("submit", async event => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.target));
    const id = values.id;
    delete values.id;
    values.enabled = event.target.elements.enabled.checked;
    if (!values.api_key) delete values.api_key;
    try {
      await api(id ? `/api/settings/providers/${id}` : "/api/settings/providers", {method:id ? "PATCH" : "POST", body:values});
      document.getElementById("providerModal").classList.remove("open");
      toast("供应商配置已保存");
      await loadProviders();
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("modelForm").addEventListener("submit", async event => {
    event.preventDefault();
    const id = event.target.elements.id.value;
    const body = {
      provider_id: event.target.elements.provider_id.value,
      display_name: event.target.elements.display_name.value,
      model_name: event.target.elements.model_name.value,
      capabilities: [...event.target.querySelectorAll('[name="capability"]:checked')].map(box => box.value),
      enabled: event.target.elements.enabled.checked
    };
    if (id) delete body.provider_id;
    try {
      await api(id ? `/api/settings/models/${id}` : "/api/settings/models", {method:id ? "PATCH" : "POST", body});
      document.getElementById("modelModal").classList.remove("open");
      toast("模型配置已保存");
      await loadProviders();
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("routeForm").addEventListener("submit", async event => {
    event.preventDefault();
    const routes = Object.fromEntries(new FormData(event.target));
    try {
      const result = await api("/api/settings/routes", {method:"PUT", body:{routes}});
      toast(result.reindex_batch_id ? "路由已保存，新的语义索引正在后台构建" : "任务路由已保存");
      refreshProgress();
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("rebuildSearch").addEventListener("click", async () => {
    if (!await confirmAction({title:"重建搜索索引", message:"将为全部有效收藏重新检查索引。未变化的全文数据会直接复用。"})) return;
    try {
      const result = await api("/api/search/rebuild", {method:"POST"});
      toast(`已加入 ${result.count} 个索引任务`);
      refreshProgress();
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("runSelectedHealth").addEventListener("click", async () => {
    const ids = [...selected];
    if (!ids.length) return toast("请先在收藏管理中选择要检查的收藏", "error");
    try {
      const result = await api("/api/health-checks/run", {method:"POST", body:ids});
      toast(`已加入 ${result.count} 个健康检查任务`);
      invalidateSection("health-checks");
      refreshProgress();
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("runAllHealth").addEventListener("click", async () => {
    if (!await confirmAction({title:"检查全部链接", message:"将逐个访问全部公网收藏地址，只检查可达性，不抓取页面正文。"})) return;
    try {
      const result = await api("/api/health-checks/run", {method:"POST", body:[]});
      toast(`已加入 ${result.count} 个健康检查任务`);
      invalidateSection("health-checks");
      refreshProgress();
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("healthSettingsForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      await api("/api/settings/health-check", {
        method:"PUT",
        body:{
          enabled:event.target.elements.enabled.checked,
          interval_days:Number(event.target.elements.interval_days.value)
        }
      });
      toast("链接健康检查设置已保存");
      await loadHealthSettings();
    } catch (error) { toast(error.message, "error"); }
  });
  document.getElementById("accountForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      await api("/api/settings/account", {method:"PUT", body:Object.fromEntries(new FormData(event.target))});
      toast("账号已更新，即将重新登录");
      setTimeout(() => { sessionStorage.removeItem("csrfToken"); location.href = "/login"; }, 900);
    } catch (error) { toast(error.message, "error"); }
  });

  loadedTabs.add("bookmarks");
  await loadBookmarks();
});
