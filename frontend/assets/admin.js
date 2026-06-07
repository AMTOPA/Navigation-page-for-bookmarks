let adminBookmarks = [];
let providerData = {providers: [], routes: {}};
const selected = new Set();
let adminEditorInitial = null;
let adminSearchTimer = null;
const pages = {
  bookmarks: {page: 1, total: 0, pageSize: 50},
  jobs: {page: 1, total: 0, pageSize: 50},
  logs: {page: 1, total: 0, pageSize: 50}
};
const sectionTitles = {
  bookmarks: "收藏管理", import: "导入与同步", models: "AI 模型",
  search: "搜索索引", jobs: "任务中心", logs: "系统日志", account: "账号安全"
};

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
      await loadBookmarks();
    } catch (error) { toast(error.message, "error"); }
  }));
}

async function loadBookmarks() {
  const state = pages.bookmarks;
  const query = document.getElementById("adminSearch").value.trim();
  const result = await api(`/api/bookmarks?page=${state.page}&page_size=${state.pageSize}&compact=true&q=${encodeURIComponent(query)}`);
  adminBookmarks = result.items;
  state.total = result.total;
  bookmarkRows();
  renderPager("bookmarkPager", state, page => {
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

async function loadJobs() {
  const state = pages.jobs;
  const status = document.getElementById("jobStatus").value;
  const result = await api(`/api/jobs?page=${state.page}&page_size=${state.pageSize}&status=${encodeURIComponent(status)}`);
  document.getElementById("jobRows").innerHTML = result.items.map(job => `<tr>
    <td>${escapeHtml(job.job_type)}</td>
    <td class="${job.status === "failed" ? "status-failed" : ""}">${escapeHtml(job.status)}</td>
    <td>${job.attempts}/${job.max_attempts}</td><td>${escapeHtml(job.error || "")}</td>
    <td>${formatTime(job.created_at)}</td>
    <td>${["failed","blocked"].includes(job.status) ? `<button class="button secondary" data-retry="${job.id}">重试</button>` : ""}</td>
  </tr>`).join("");
  document.querySelectorAll("[data-retry]").forEach(button => button.addEventListener("click", async () => {
    try {
      await api(`/api/jobs/${button.dataset.retry}/retry`, {method:"POST"});
      toast("任务已重新排队");
      await loadJobs();
    } catch (error) { toast(error.message, "error"); }
  }));
  state.total = result.total;
  renderPager("jobPager", state, page => {
    state.page = page;
    loadJobs();
  });
}

async function loadLogs() {
  const state = pages.logs;
  const level = document.getElementById("logLevel").value;
  const result = await api(`/api/logs?page=${state.page}&page_size=${state.pageSize}&level=${encodeURIComponent(level)}`);
  document.getElementById("logRows").innerHTML = result.items.map(log => `<tr>
    <td>${escapeHtml(log.event_type)}</td><td>${escapeHtml(log.level)}</td>
    <td>${escapeHtml(log.message)}</td><td>${escapeHtml(JSON.stringify(log.details))}</td>
    <td>${formatTime(log.created_at)}</td>
  </tr>`).join("");
  state.total = result.total;
  renderPager("logPager", state, page => {
    state.page = page;
    loadLogs();
  });
}

function renderPager(id, state, onPage) {
  const pageCount = Math.max(Math.ceil(state.total / state.pageSize), 1);
  const root = document.getElementById(id);
  root.innerHTML = `<button class="button ghost" data-page="${state.page - 1}" ${state.page <= 1 ? "disabled" : ""}>上一页</button>
    <span>第 ${state.page} / ${pageCount} 页 · 共 ${state.total} 条</span>
    <button class="button ghost" data-page="${state.page + 1}" ${state.page >= pageCount ? "disabled" : ""}>下一页</button>`;
  root.querySelectorAll("[data-page]:not(:disabled)").forEach(button =>
    button.addEventListener("click", () => onPage(Number(button.dataset.page)))
  );
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
  if (tabName === "jobs") loadJobs();
  if (tabName === "logs") loadLogs();
  if (tabName === "search") loadSearchStatus();
  if (tabName === "models") loadProviders();
}

document.addEventListener("DOMContentLoaded", async () => {
  await ensureSession();
  document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => switchTab(button.dataset.tab)));
  document.getElementById("menuButton").addEventListener("click", () => document.getElementById("adminSidebar").classList.toggle("open"));
  document.getElementById("adminSearch").addEventListener("input", () => {
    clearTimeout(adminSearchTimer);
    adminSearchTimer = setTimeout(() => {
      pages.bookmarks.page = 1;
      loadBookmarks();
    }, 250);
  });
  document.getElementById("adminAdd").addEventListener("click", () => openAdminModal());
  document.getElementById("selectAll").addEventListener("change", event => {
    adminBookmarks.forEach(item => event.target.checked ? selected.add(item.id) : selected.delete(item.id));
    bookmarkRows();
  });
  document.getElementById("jobStatus").addEventListener("change", () => {
    pages.jobs.page = 1;
    loadJobs();
  });
  document.getElementById("logLevel").addEventListener("change", () => {
    pages.logs.page = 1;
    loadLogs();
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
      if (!id || Object.keys(payload).length) await api(id ? `/api/bookmarks/${id}` : "/api/bookmarks", {method:id ? "PATCH" : "POST", body:payload});
      closeAdminModal();
      toast(id ? "收藏已保存" : "链接已添加");
      await loadBookmarks();
    } catch (error) { toast(error.message, "error"); }
  });

  document.getElementById("importForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      const result = await api("/api/import/edge-html", {method:"POST", body:new FormData(event.target)});
      document.getElementById("importResult").textContent = `完成：${result.total} 条，新增 ${result.added} 条`;
      toast("Edge HTML 导入完成");
      await loadBookmarks();
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
  document.getElementById("accountForm").addEventListener("submit", async event => {
    event.preventDefault();
    try {
      await api("/api/settings/account", {method:"PUT", body:Object.fromEntries(new FormData(event.target))});
      toast("账号已更新，即将重新登录");
      setTimeout(() => { sessionStorage.removeItem("csrfToken"); location.href = "/login"; }, 900);
    } catch (error) { toast(error.message, "error"); }
  });

  const account = await api("/api/settings/account");
  document.getElementById("accountForm").elements.username.value = account.username;
  await Promise.all([loadBookmarks(), loadTokens(), loadProviders()]);
});
