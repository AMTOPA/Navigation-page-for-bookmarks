let bookmarks = [];
let visibleBookmarks = [];
let editing = false;
let editorInitial = null;
let searchTimer = null;
let searchSequence = 0;
let searchController = null;
let renderSignature = "";
let renderRevision = 0;
let renderFrame = 0;
let searchSyncing = false;
let aiController = null;
let groupObserver = null;
let groupPositionObserver = null;
let groupPositionFrame = 0;
let programmaticGroup = "";
let programmaticGroupTimer = null;
let aiModels = [];
let aiPanelResizeObserver = null;
const groupItems = new Map();
const preferences = {
  groupBy: localStorage.getItem("groupBy") || "category",
  sortBy: localStorage.getItem("sortBy") || "favorited_at",
  direction: localStorage.getItem("direction") || "desc",
  searchMode: localStorage.getItem("searchMode") || "off",
  searchBarMode: localStorage.getItem("searchBarMode") || "embedded",
  aiLayout: localStorage.getItem("aiLayout") === "inline" ? "inline" : "floating",
  aiModelId: localStorage.getItem("aiSearchModel") || ""
};
let groupRailCollapsed = localStorage.getItem("groupRailCollapsed") === "true";
const importanceOrder = {high: 4, medium: 3, normal: 2, low: 1};
const importanceLabel = {high: "非常重要", medium: "比较重要", normal: "一般", low: "不重要"};
const CACHE_DB = "bookmark-navigation";
const CACHE_STORE = "snapshots";

function openCache() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(CACHE_DB, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(CACHE_STORE);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function readSnapshotCache() {
  try {
    const db = await openCache();
    return await new Promise((resolve, reject) => {
      const request = db.transaction(CACHE_STORE).objectStore(CACHE_STORE).get("navigation");
      request.onsuccess = () => resolve(request.result || null);
      request.onerror = () => reject(request.error);
    });
  } catch {
    return null;
  }
}

async function writeSnapshotCache(value) {
  try {
    const db = await openCache();
    await new Promise((resolve, reject) => {
      const request = db.transaction(CACHE_STORE, "readwrite").objectStore(CACHE_STORE).put(value, "navigation");
      request.onsuccess = resolve;
      request.onerror = () => reject(request.error);
    });
  } catch {
    // Cache failure must never block navigation.
  }
}

function shanghaiParts(value) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "numeric", day: "numeric"
  }).formatToParts(new Date(value));
  return Object.fromEntries(parts.map(item => [item.type, Number(item.value)]));
}

function dateGroup(value, kind) {
  if (!value) return "未知时间";
  const date = new Date(value);
  const now = new Date();
  const days = (now - date) / 86400000;
  const current = shanghaiParts(now);
  const target = shanghaiParts(date);
  const sameDay = current.year === target.year && current.month === target.month && current.day === target.day;
  if (kind === "updated") {
    if (sameDay) return "今天更新";
    if (days <= 7) return "最近 7 天";
    if (days <= 30) return "最近 30 天";
    return "更早";
  }
  if (days <= 30) return "最近收藏";
  if (current.year === target.year && current.month === target.month) return "本月收藏";
  if (current.year === target.year) return "今年收藏";
  return "更早收藏";
}

function groupName(item) {
  switch (preferences.groupBy) {
    case "folder": return item.folder_paths?.[0] || "无文件夹";
    case "importance": return importanceLabel[item.importance] || "一般";
    case "letter": {
      const first = (item.title || "#").trim().charAt(0).toUpperCase();
      return /^[A-Z]$/.test(first) ? first : (/^[0-9]$/.test(first) ? "#" : first || "#");
    }
    case "updated": return dateGroup(item.updated_at, "updated");
    case "favorited": return dateGroup(item.favorited_at, "favorited");
    default: return item.final_category || "未分类";
  }
}

function groupNames(item) {
  if (preferences.groupBy === "folder") return item.folder_paths?.length ? item.folder_paths : ["无文件夹"];
  return [groupName(item)];
}

function sortItems(items) {
  const direction = preferences.direction === "asc" ? 1 : -1;
  return [...items].sort((a, b) => {
    const av = a[preferences.sortBy], bv = b[preferences.sortBy];
    if (preferences.sortBy === "title") return direction * (av || "").localeCompare(bv || "", "zh-CN");
    if (preferences.sortBy === "importance") return direction * ((importanceOrder[av] || 0) - (importanceOrder[bv] || 0));
    return direction * (new Date(av || 0) - new Date(bv || 0));
  });
}

function refreshGroupFilter() {
  const select = document.getElementById("groupFilter");
  const previous = select.value;
  const groups = [...new Set(bookmarks.flatMap(groupNames))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  select.innerHTML = '<option value="">全部组别</option>' +
    groups.map(name => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join("");
  select.value = groups.includes(previous) ? previous : "";
}

function render() {
  const selectedGroup = document.getElementById("groupFilter")?.value || "";
  const signature = [
    renderRevision,
    preferences.groupBy,
    preferences.sortBy,
    preferences.direction,
    selectedGroup,
    editing,
    visibleBookmarks.map(item => item.id).join(",")
  ].join("|");
  if (signature === renderSignature) return;
  renderSignature = signature;
  if (groupObserver) groupObserver.disconnect();
  groupItems.clear();
  const groups = new Map();
  visibleBookmarks.forEach(item => {
    groupNames(item).forEach(name => {
      if (selectedGroup && name !== selectedGroup) return;
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(item);
    });
  });
  const root = document.getElementById("groups");
  if (!groups.size) {
    root.innerHTML = '<div class="empty"><h2>没有找到匹配的收藏</h2><p class="muted">可以换一种描述，或开启智能/AI 搜索。</p></div>';
    renderGroupRail([]);
    return;
  }
  for (const [name, items] of groups) groupItems.set(name, sortItems(items));
  root.innerHTML = [...groups.entries()].map(([name, items]) => `
    <section class="group" id="group-${hashName(name)}" data-group="${escapeHtml(name)}">
      <h2 class="group-title">${escapeHtml(name)} <span class="count">${items.length}</span></h2>
      <div class="grid lazy-grid" data-grid="${escapeHtml(name)}"><div class="group-placeholder">正在准备卡片…</div></div>
    </section>`).join("");
  bindGroupDrops();
  groupObserver = new IntersectionObserver(entries => {
    entries.filter(entry => entry.isIntersecting).forEach(entry => {
      populateGroup(entry.target);
      groupObserver.unobserve(entry.target);
    });
  }, {rootMargin: "500px 0px"});
  document.querySelectorAll(".lazy-grid").forEach(grid => groupObserver.observe(grid));
  renderGroupRail([...groups.entries()]);
}

function scheduleRender() {
  if (renderFrame) return;
  renderFrame = requestAnimationFrame(() => {
    renderFrame = 0;
    render();
  });
}

function populateGroup(grid) {
  if (grid.dataset.loaded === "true") return;
  const items = groupItems.get(grid.dataset.grid) || [];
  grid.innerHTML = items.map(cardHtml).join("");
  grid.dataset.loaded = "true";
  bindCards(grid);
}

function hashName(value) {
  let hash = 0;
  for (const char of value) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return Math.abs(hash).toString(36);
}

function renderGroupRail(groups) {
  const outline = document.getElementById("groupOutline");
  const rail = document.getElementById("groupRail");
  rail.innerHTML = groups.map(([name, items]) =>
    `<button type="button" data-jump="${hashName(name)}" title="${escapeHtml(name)}">${escapeHtml(name)}<small>${items.length}</small></button>`
  ).join("");
  rail.querySelectorAll("[data-jump]").forEach(button => button.addEventListener("click", () => {
    jumpToGroup(button.dataset.jump);
  }));
  outline.hidden = groups.length === 0;
  applyGroupRailState();
  observeCurrentGroup();
}

function applyGroupRailState() {
  const outline = document.getElementById("groupOutline");
  const toggle = document.getElementById("groupRailToggle");
  outline.classList.toggle("collapsed", groupRailCollapsed);
  toggle.setAttribute("aria-expanded", String(!groupRailCollapsed));
  toggle.title = groupRailCollapsed ? "展开组别大纲" : "折叠组别大纲";
  toggle.textContent = groupRailCollapsed ? "‹" : "›";
}

function observeCurrentGroup() {
  if (groupPositionObserver) groupPositionObserver.disconnect();
  const groups = [...document.querySelectorAll(".group")];
  if (!groups.length) return;
  groupPositionObserver = new IntersectionObserver(scheduleActiveGroup, {
    rootMargin: "-8% 0px -82% 0px",
    threshold: [0, 0.01, 1]
  });
  groups.forEach(group => groupPositionObserver.observe(group));
  scheduleActiveGroup();
}

function groupScrollOffset() {
  const topbar = document.querySelector(".topbar")?.getBoundingClientRect().height || 0;
  const control = document.getElementById("controlPanel");
  const stickyControl = preferences.searchBarMode === "compact" && control && !control.hidden
    ? control.getBoundingClientRect().height + 8
    : 0;
  return topbar + stickyControl + 14;
}

function setActiveGroup(groupId) {
  document.querySelectorAll(".group-rail button").forEach(button =>
    button.classList.toggle("active", button.dataset.jump === groupId)
  );
  document.querySelector(`.group-rail button[data-jump="${groupId}"]`)?.scrollIntoView({
    block: "nearest"
  });
}

function updateActiveGroup() {
  groupPositionFrame = 0;
  if (programmaticGroup) {
    setActiveGroup(programmaticGroup);
    return;
  }
  const groups = [...document.querySelectorAll(".group")];
  if (!groups.length) return;
  const anchor = groupScrollOffset() + 8;
  let current = groups[0];
  for (const group of groups) {
    if (group.getBoundingClientRect().top <= anchor) current = group;
    else break;
  }
  setActiveGroup(current.id.replace("group-", ""));
}

function scheduleActiveGroup() {
  if (!groupPositionFrame) groupPositionFrame = requestAnimationFrame(updateActiveGroup);
}

function finishProgrammaticGroup() {
  clearTimeout(programmaticGroupTimer);
  programmaticGroup = "";
  scheduleActiveGroup();
}

function jumpToGroup(groupId) {
  const target = document.getElementById(`group-${groupId}`);
  if (!target) return;
  const grid = target.querySelector(".lazy-grid");
  if (grid) populateGroup(grid);
  programmaticGroup = groupId;
  setActiveGroup(groupId);
  clearTimeout(programmaticGroupTimer);
  const top = window.scrollY + target.getBoundingClientRect().top - groupScrollOffset();
  window.scrollTo({top: Math.max(0, top), behavior: "smooth"});
  programmaticGroupTimer = setTimeout(finishProgrammaticGroup, 900);
}

function cardHtml(item) {
  const target = safeTarget(item);
  const openLink = target ? `<a href="${escapeHtml(target)}" target="_blank" rel="noopener noreferrer" aria-label="打开链接" class="card-link"></a>` : "";
  return `<article class="card" data-id="${item.id}" draggable="${editing && preferences.groupBy === "category"}">
    <img class="favicon" src="${escapeHtml(item.display_image_url || "/assets/default.svg")}" alt="" loading="lazy" onerror="this.src='/assets/default.svg'">
    <div class="card-body">
      <h3>${escapeHtml(item.title || item.url)}</h3>
      <p>${escapeHtml(item.description || item.summary || item.url)}</p>
      <div class="tags">${(item.tags || []).slice(0,5).map(tag => `<span class="tag">${escapeHtml(tag)}</span>`).join("")}${item.local_copy_url ? '<span class="tag">服务器副本</span>' : ""}</div>
    </div>
    <div class="card-actions">
      <button class="button secondary edit-card" data-edit="${item.id}">编辑</button>
      <button class="button danger delete-card" data-delete="${item.id}">删除</button>
    </div>
    ${openLink}
  </article>`;
}

function safeTarget(item) {
  if (item.local_copy_url?.startsWith("/api/files/")) return item.local_copy_url;
  try {
    const value = new URL(item.url);
    return ["http:", "https:"].includes(value.protocol) ? item.url : "";
  } catch { return ""; }
}

function bindCards(root = document) {
  root.querySelectorAll("[data-edit]").forEach(button => {
    button.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      openEditor(button.dataset.edit);
    });
  });
  root.querySelectorAll("[data-delete]").forEach(button => {
    button.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      deleteBookmark(button.dataset.delete);
    });
  });
  root.querySelectorAll(".favicon").forEach(image => {
    image.addEventListener("load", scheduleActiveGroup, {once: true});
  });
  root.querySelectorAll(".card[draggable=true]").forEach(card => {
    card.addEventListener("dragstart", event => event.dataTransfer.setData("text/plain", card.dataset.id));
  });
}

async function deleteBookmark(id) {
  const item = bookmarks.find(value => value.id === id);
  if (!item) return;
  const confirmed = await confirmAction({
    title: "删除收藏",
    message: `“${item.title || item.url}”将被软删除，可以立即撤销或稍后在后台恢复。`,
    confirmText: "删除",
    danger: true
  });
  if (!confirmed) return;
  try {
    await api(`/api/bookmarks/${id}`, {method: "DELETE"});
    bookmarks = bookmarks.filter(value => value.id !== id);
    visibleBookmarks = visibleBookmarks.filter(value => value.id !== id);
    refreshGroupFilter();
    render();
    await writeSnapshotCache({etag: "", data: {items: bookmarks}});
    toast("收藏已删除", "success", 8000, {
      label: "撤销",
      run: async () => {
        await api(`/api/bookmarks/${id}/restore`, {method: "POST"});
        bookmarks.unshift(item);
        runLocalSearch();
        refreshGroupFilter();
        await writeSnapshotCache({etag: "", data: {items: bookmarks}});
        toast("收藏已恢复");
      }
    });
  } catch (error) {
    toast(error.message, "error");
  }
}

function bindGroupDrops() {
  document.querySelectorAll(".group").forEach(group => {
    group.addEventListener("dragover", event => {
      if (editing && preferences.groupBy === "category") {
        event.preventDefault();
        group.classList.add("drag-over");
      }
    });
    group.addEventListener("dragleave", () => group.classList.remove("drag-over"));
    group.addEventListener("drop", async event => {
      event.preventDefault();
      group.classList.remove("drag-over");
      const id = event.dataTransfer.getData("text/plain");
      try {
        const updated = await api(`/api/bookmarks/${id}`, {method:"PATCH", body:{category:group.dataset.group}});
        replaceBookmark(compactBookmark(updated));
        toast("分类已更新");
      } catch (error) {
        toast(error.message, "error");
      }
    });
  });
}

function compactBookmark(item) {
  return prepareBookmark({
    id: item.id, url: item.url, title: item.title, description: item.description, summary: item.summary,
    display_image_url: item.display_image_url, final_category: item.final_category,
    manual_category: item.manual_category, tags: item.tags || [], importance: item.importance,
    notes: item.notes, favorited_at: item.favorited_at, updated_at: item.updated_at,
    crawl_status: item.crawl_status, ai_status: item.ai_status, local_copy_url: item.local_copy_url,
    folder_paths: (item.sources || []).filter(source => source.is_active && source.folder_path).map(source => source.folder_path)
  });
}

function replaceBookmark(item) {
  const index = bookmarks.findIndex(value => value.id === item.id);
  if (index >= 0) bookmarks[index] = item;
  else bookmarks.unshift(item);
  renderRevision += 1;
  runLocalSearch();
  writeSnapshotCache({etag: "", data: {items: bookmarks}});
}

async function openEditor(id = "") {
  const form = document.getElementById("editForm");
  form.reset();
  let item = null;
  if (id) {
    try {
      item = await api(`/api/bookmarks/${id}`);
    } catch (error) {
      return toast(error.message, "error");
    }
  }
  for (const [key, value] of Object.entries(item || {})) {
    if (form.elements[key]) form.elements[key].value = key === "tags" ? (value || []).join(", ") : (value ?? "");
  }
  if (item) form.elements.category.value = item.manual_category || item.final_category || "";
  form.elements.id.value = id;
  editorInitial = Object.fromEntries(new FormData(form));
  document.getElementById("editTitle").textContent = id ? "编辑收藏" : "新增链接";
  document.getElementById("editModal").classList.add("open");
}

async function load() {
  const cached = await readSnapshotCache();
  if (cached?.data?.items?.length) {
    bookmarks = cached.data.items.map(prepareBookmark);
    visibleBookmarks = bookmarks;
    refreshGroupFilter();
    render();
  }
  const headers = {};
  if (cached?.etag) headers["If-None-Match"] = cached.etag;
  const response = await fetch("/api/navigation/snapshot", {headers});
  if (response.status === 304) return;
  if (!response.ok) throw new Error(`加载收藏失败：${response.status}`);
  const data = await response.json();
  bookmarks = data.items.map(prepareBookmark);
  visibleBookmarks = bookmarks;
  await writeSnapshotCache({etag: response.headers.get("ETag") || "", data});
  refreshGroupFilter();
  renderRevision += 1;
  scheduleRender();
}

function normalizedSearchText(item) {
  return [
    item.title, item.url, item.final_category, ...(item.tags || []),
    item.description, item.summary, item.notes, ...(item.folder_paths || [])
  ].filter(Boolean).join(" ").toLocaleLowerCase("zh-CN");
}

function prepareBookmark(item) {
  Object.defineProperty(item, "_searchText", {
    configurable: true,
    enumerable: false,
    writable: true,
    value: normalizedSearchText(item),
  });
  return item;
}

function runLocalSearch() {
  const term = document.getElementById("search").value.trim().toLocaleLowerCase("zh-CN");
  const status = document.getElementById("searchStatus");
  visibleBookmarks = term
    ? bookmarks.filter(item => (item._searchText || normalizedSearchText(item)).includes(term))
    : bookmarks;
  status.hidden = !term;
  if (term) status.textContent = `即时关键词结果 · ${visibleBookmarks.length} 条`;
  scheduleRender();
}

async function runSearch() {
  const term = document.getElementById("search").value.trim();
  runLocalSearch();
  if (!term || preferences.searchMode !== "smart") return;
  const status = document.getElementById("searchStatus");
  const sequence = ++searchSequence;
  if (searchController) searchController.abort();
  searchController = new AbortController();
  status.hidden = false;
  status.textContent = `即时结果 ${visibleBookmarks.length} 条 · 正在补充智能结果…`;
  const group = document.getElementById("groupFilter").value;
  const serverGroupBy = ["category", "folder", "importance"].includes(preferences.groupBy) ? preferences.groupBy : "";
  const serverGroup = serverGroupBy ? group : "";
  try {
    const result = await api(
      `/api/search?q=${encodeURIComponent(term)}&mode=semantic&limit=50&group_by=${encodeURIComponent(serverGroupBy)}&group=${encodeURIComponent(serverGroup)}`,
      {signal: searchController.signal}
    );
    if (sequence !== searchSequence) return;
    visibleBookmarks = result.items.map(prepareBookmark);
    status.textContent = `智能搜索结果 · ${result.items.length} 条${result.query_cache_hit ? " · 已使用持久缓存" : ""}`;
    if (result.fallback_error) status.textContent += " · Embedding 暂不可用，已降级";
    render();
  } catch (error) {
    if (sequence !== searchSequence) return;
    if (error.name === "AbortError") return;
    toast(error.message, "error");
    status.textContent = "智能搜索不可用，已保留即时关键词结果";
  }
}

function setSearchMode(mode) {
  preferences.searchMode = mode;
  localStorage.setItem("searchMode", mode);
  document.querySelectorAll("[data-search-mode]").forEach(button =>
    button.setAttribute("aria-pressed", String(button.dataset.searchMode === mode))
  );
  const panel = document.getElementById("aiSearchPanel");
  panel.hidden = mode !== "ai";
  document.body.classList.toggle("ai-floating-open", mode === "ai" && preferences.aiLayout === "floating");
  if (mode === "ai") {
    syncSearchInputs(document.getElementById("search").value, "search");
    restoreAiPanelGeometry();
  } else {
    setAiMinimized(false);
  }
  runSearch();
}

function applySearchBarMode(mode) {
  preferences.searchBarMode = mode;
  localStorage.setItem("searchBarMode", mode);
  document.getElementById("searchBarMode").value = mode;
  document.body.dataset.searchBar = mode;
  document.getElementById("searchOrb").hidden = mode !== "circle";
  scheduleActiveGroup();
}

function applyAiLayout(layout) {
  const panel = document.getElementById("aiSearchPanel");
  if (preferences.aiLayout === "floating") saveAiPanelGeometry();
  preferences.aiLayout = layout;
  localStorage.setItem("aiLayout", layout);
  document.getElementById("aiLayout").value = layout;
  document.body.dataset.aiLayout = layout;
  document.body.classList.toggle("ai-floating-open", preferences.searchMode === "ai" && layout === "floating");
  setAiMinimized(false);
  clearAiPanelGeometryStyles();
  if (layout === "floating") requestAnimationFrame(restoreAiPanelGeometry);
  else if (preferences.searchMode === "ai") panel.scrollIntoView({block: "nearest"});
}

async function loadAiModels() {
  const select = document.getElementById("aiModel");
  try {
    const data = await api("/api/ai-search/models");
    aiModels = data.models || [];
    const available = new Set(aiModels.map(model => model.id));
    const selected = available.has(preferences.aiModelId)
      ? preferences.aiModelId
      : (available.has(data.default_model_id) ? data.default_model_id : aiModels[0]?.id || "");
    preferences.aiModelId = selected;
    if (selected) localStorage.setItem("aiSearchModel", selected);
    else localStorage.removeItem("aiSearchModel");
    select.innerHTML = aiModels.length
      ? aiModels.map(model => `<option value="${model.id}">${escapeHtml(model.provider_name)} · ${escapeHtml(model.display_name)}${model.is_default ? "（默认）" : ""}</option>`).join("")
      : '<option value="">没有可用模型</option>';
    select.value = selected;
    select.disabled = !aiModels.length;
  } catch (error) {
    select.innerHTML = '<option value="">模型加载失败</option>';
    select.disabled = true;
    toast(error.message, "error");
  }
}

function restoreAiPanelGeometry() {
  const panel = document.getElementById("aiSearchPanel");
  if (preferences.aiLayout !== "floating" || window.innerWidth <= 850) {
    clearAiPanelGeometryStyles();
    return;
  }
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem("aiPanelGeometry") || "{}");
  } catch {
    localStorage.removeItem("aiPanelGeometry");
  }
  const validSaved = Number(saved.width) >= 520 && Number(saved.height) >= 360;
  if (!validSaved && Object.keys(saved).length) localStorage.removeItem("aiPanelGeometry");
  const width = Math.min(Math.max(validSaved ? saved.width : 760, 520), window.innerWidth - 32);
  const height = Math.min(Math.max(validSaved ? saved.height : Math.round(window.innerHeight * 0.7), 360), window.innerHeight - 32);
  const left = Math.min(Math.max(saved.left ?? window.innerWidth - width - 24, 16), window.innerWidth - width - 16);
  const top = Math.min(Math.max(saved.top ?? 92, 16), window.innerHeight - height - 16);
  Object.assign(panel.style, {left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px`});
}

function saveAiPanelGeometry() {
  if (preferences.aiLayout !== "floating" || window.innerWidth <= 850) return;
  const rect = document.getElementById("aiSearchPanel").getBoundingClientRect();
  if (rect.width < 300 || rect.height < 200) return;
  localStorage.setItem("aiPanelGeometry", JSON.stringify({
    left: Math.round(rect.left),
    top: Math.round(rect.top),
    width: Math.round(rect.width),
    height: Math.round(rect.height)
  }));
}

function clearAiPanelGeometryStyles() {
  const panel = document.getElementById("aiSearchPanel");
  for (const property of ["left", "right", "top", "bottom", "width", "height"]) {
    panel.style.removeProperty(property);
  }
}

function resetAiPanelGeometry() {
  localStorage.removeItem("aiPanelGeometry");
  clearAiPanelGeometryStyles();
  restoreAiPanelGeometry();
  toast("AI 助手位置已重置");
}

function setAiMinimized(minimized) {
  const panel = document.getElementById("aiSearchPanel");
  const dock = document.getElementById("aiRestoreDock");
  if (minimized && preferences.aiLayout === "floating") saveAiPanelGeometry();
  panel.hidden = minimized || preferences.searchMode !== "ai";
  dock.hidden = !minimized || preferences.searchMode !== "ai" || preferences.aiLayout !== "floating";
  document.body.classList.toggle("ai-floating-open", !minimized && preferences.searchMode === "ai" && preferences.aiLayout === "floating");
}

function syncSearchInputs(value, source) {
  if (searchSyncing) return;
  searchSyncing = true;
  if (source !== "search") document.getElementById("search").value = value;
  if (source !== "ai") document.getElementById("aiQuery").value = value;
  searchSyncing = false;
}

function initAiFloatingWindow() {
  const panel = document.getElementById("aiSearchPanel");
  const handle = document.getElementById("aiPanelHandle");
  handle.addEventListener("pointerdown", event => {
    if (
      preferences.aiLayout !== "floating"
      || window.innerWidth <= 850
      || event.target.closest("button, select, label")
    ) return;
    const rect = panel.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    handle.setPointerCapture(event.pointerId);
    panel.classList.add("dragging");
    const move = moveEvent => {
      const left = Math.min(
        Math.max(rect.left + moveEvent.clientX - startX, 8),
        window.innerWidth - panel.offsetWidth - 8
      );
      const top = Math.min(
        Math.max(rect.top + moveEvent.clientY - startY, 8),
        window.innerHeight - 72
      );
      panel.style.left = `${left}px`;
      panel.style.top = `${top}px`;
    };
    const end = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      panel.classList.remove("dragging");
      saveAiPanelGeometry();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  });
  document.getElementById("aiMinimize").addEventListener("click", () => setAiMinimized(true));
  document.getElementById("aiRestoreDock").addEventListener("click", () => {
    setAiMinimized(false);
    restoreAiPanelGeometry();
  });
  document.getElementById("aiResetPosition").addEventListener("click", resetAiPanelGeometry);
  if ("ResizeObserver" in window) {
    aiPanelResizeObserver = new ResizeObserver(() => {
      clearTimeout(aiPanelResizeObserver.saveTimer);
      aiPanelResizeObserver.saveTimer = setTimeout(saveAiPanelGeometry, 150);
    });
    aiPanelResizeObserver.observe(panel);
  }
  window.addEventListener("resize", restoreAiPanelGeometry);
}

function parseSseChunk(buffer, onEvent) {
  const blocks = buffer.split("\n\n");
  const pending = blocks.pop() || "";
  for (const block of blocks) {
    let event = "message";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) data += line.slice(5).trim();
    }
    if (data) onEvent(event, JSON.parse(data));
  }
  return pending;
}

async function askAi() {
  const query = document.getElementById("aiQuery").value.trim();
  if (!query) return toast("请先输入想找的内容", "error");
  if (aiController) aiController.abort();
  aiController = new AbortController();
  const answer = document.getElementById("aiAnswer");
  const askButton = document.getElementById("askAi");
  const stopButton = document.getElementById("stopAi");
  answer.textContent = "";
  askButton.disabled = true;
  stopButton.disabled = false;
  try {
    const requestPayload = {
      query,
      context: document.getElementById("aiContext").value,
      model_id: document.getElementById("aiModel").value || null,
      group_by: ["category", "folder", "importance"].includes(preferences.groupBy) ? preferences.groupBy : "",
      group: ["category", "folder", "importance"].includes(preferences.groupBy) ? document.getElementById("groupFilter").value : "",
      limit: 30
    };
    const request = () => fetch("/api/ai-search/stream", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": csrfToken},
      body: JSON.stringify(requestPayload),
      signal: aiController.signal
    });
    let response = await request();
    if (response.status === 400 && requestPayload.model_id) {
      requestPayload.model_id = null;
      preferences.aiModelId = "";
      localStorage.removeItem("aiSearchModel");
      await loadAiModels();
      toast("所选模型已不可用，已回退到默认模型", "error");
      response = await request();
    }
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || `AI 搜索失败：${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    const handle = (event, data) => {
      if (event === "results") {
        visibleBookmarks = data.items.map(prepareBookmark);
        render();
        document.getElementById("searchStatus").hidden = false;
        document.getElementById("searchStatus").textContent =
          `AI 检索候选 · ${data.items.length} 条${data.answer_cache_hit ? " · 已使用回答缓存" : ""}`;
        document.getElementById("aiSuggestions").innerHTML = (data.suggestions || []).map(value =>
          `<button type="button" data-suggestion="${escapeHtml(value)}">${escapeHtml(value)}</button>`
        ).join("");
        bindSuggestions();
      } else if (event === "token") {
        answer.textContent += data.text;
      } else if (event === "error") {
        toast(data.message, "error");
      }
    };
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      buffer = parseSseChunk(buffer, handle);
    }
  } catch (error) {
    if (error.name !== "AbortError") toast(error.message, "error");
  } finally {
    aiController = null;
    askButton.disabled = false;
    stopButton.disabled = true;
  }
}

function bindSuggestions() {
  document.querySelectorAll("[data-suggestion]").forEach(button => button.addEventListener("click", () => {
    const input = document.getElementById("search");
    input.value = `${input.value.trim()} ${button.dataset.suggestion}`.trim();
    syncSearchInputs(input.value, "search");
    askAi();
  }));
}

document.addEventListener("DOMContentLoaded", async () => {
  await ensureSession();
  window.addEventListener("scroll", scheduleActiveGroup, {passive: true});
  window.addEventListener("scrollend", finishProgrammaticGroup);
  for (const key of ["groupBy", "sortBy", "direction"]) {
    document.getElementById(key).value = preferences[key];
    document.getElementById(key).addEventListener("change", event => {
      preferences[key] = event.target.value;
      localStorage.setItem(key, event.target.value);
      if (key === "groupBy") refreshGroupFilter();
      document.getElementById("dragNotice").hidden = !editing || preferences.groupBy === "category";
      render();
    });
  }
  document.getElementById("groupFilter").addEventListener("change", runSearch);
  document.getElementById("search").addEventListener("input", event => {
    syncSearchInputs(event.target.value, "search");
    clearTimeout(searchTimer);
    runLocalSearch();
    if (preferences.searchMode === "smart") searchTimer = setTimeout(runSearch, 300);
  });
  document.getElementById("aiQuery").addEventListener("input", event => {
    syncSearchInputs(event.target.value, "ai");
    clearTimeout(searchTimer);
    runLocalSearch();
    if (preferences.searchMode === "smart") searchTimer = setTimeout(runSearch, 300);
  });
  document.getElementById("aiQuery").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      askAi();
    }
  });
  document.querySelectorAll("[data-search-mode]").forEach(button =>
    button.addEventListener("click", () => setSearchMode(button.dataset.searchMode))
  );
  document.getElementById("searchBarMode").addEventListener("change", event => applySearchBarMode(event.target.value));
  document.getElementById("groupRailToggle").addEventListener("click", () => {
    groupRailCollapsed = !groupRailCollapsed;
    localStorage.setItem("groupRailCollapsed", String(groupRailCollapsed));
    applyGroupRailState();
  });
  document.getElementById("searchOrb").addEventListener("click", () => {
    applySearchBarMode("compact");
    document.getElementById("search").focus();
  });
  document.getElementById("aiLayout").addEventListener("change", event => applyAiLayout(event.target.value));
  document.getElementById("aiModel").addEventListener("change", event => {
    preferences.aiModelId = event.target.value;
    localStorage.setItem("aiSearchModel", event.target.value);
  });
  document.getElementById("askAi").addEventListener("click", askAi);
  document.getElementById("stopAi").addEventListener("click", () => aiController?.abort());
  document.getElementById("editMode").addEventListener("click", event => {
    editing = !editing;
    document.body.classList.toggle("editing", editing);
    event.target.textContent = editing ? "关闭编辑" : "开启编辑";
    document.getElementById("dragNotice").hidden = !editing || preferences.groupBy === "category";
    render();
  });
  document.getElementById("addButton").addEventListener("click", () => openEditor());
  document.querySelectorAll("[data-close]").forEach(button => button.addEventListener("click", () => document.getElementById("editModal").classList.remove("open")));
  document.getElementById("editForm").addEventListener("submit", async event => {
    event.preventDefault();
    const button = event.submitter;
    const values = Object.fromEntries(new FormData(event.target));
    const id = values.id;
    delete values.id;
    values.tags = values.tags.split(",").map(value => value.trim()).filter(Boolean);
    let payload = values;
    if (id) {
      payload = {};
      for (const [key, value] of Object.entries(values)) {
        const initialValue = key === "tags"
          ? (editorInitial.tags || "").split(",").map(item => item.trim()).filter(Boolean)
          : editorInitial[key];
        if (JSON.stringify(value) !== JSON.stringify(initialValue)) payload[key] = value;
      }
    }
    button.disabled = true;
    try {
      let updated = null;
      if (!id || Object.keys(payload).length) {
        updated = await api(id ? `/api/bookmarks/${id}` : "/api/bookmarks", {method:id ? "PATCH" : "POST", body:payload});
      }
      document.getElementById("editModal").classList.remove("open");
      if (updated) replaceBookmark(compactBookmark(updated));
      toast(id ? "收藏已保存" : "链接已添加，后台将自动补全信息");
    } catch (error) {
      toast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  });
  applySearchBarMode(preferences.searchBarMode);
  applyAiLayout(preferences.aiLayout);
  initAiFloatingWindow();
  await loadAiModels();
  setSearchMode(preferences.searchMode);
  try {
    await load();
  } catch (error) {
    toast(error.message, "error");
  }
});
