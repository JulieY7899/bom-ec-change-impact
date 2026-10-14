const state = {
  files: [],
  status: null,
  result: null,
  report: "",
  draftItems: [],
  batchItems: [],
  selectedBatchIndex: 0,
  baseEcnSequence: 4,
  effectivityLookupSequence: 0,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function toast(message, isError = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = { error: "本地服务返回了无法解析的响应" };
  }
  if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

const pageTitles = {
  import: "数据准备",
  draft: "变更登记",
  analysis: "影响核查",
  report: "核查报告",
};

const sourceTypeLabels = {
  expanded_bom: "多层 BOM 展开",
  production_order: "生产订单",
  sales_order_item: "销售订单行",
  sales_order_header: "销售订单抬头",
  change_history: "变更历史",
  bom_header_reference: "BOM 表头参考",
  bom_item_reference: "BOM 项目参考",
  bom_view_reference: "BOM 结构参考",
  material_master_reference: "物料主数据",
  plant_master_reference: "工厂级物料主数据",
  sales_master_reference: "销售视图",
  assignment_reference: "任务清单分配",
  source_list_reference: "供应来源清单",
  planning_supply_demo: "计划备料虚拟数据",
  unrecognized: "未识别数据",
};

function formatSourceType(value) {
  return String(value || "")
    .split("+")
    .map((item) => sourceTypeLabels[item] || item)
    .join(" + ");
}

function showView(name) {
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
  $$(".view").forEach((view) => view.classList.remove("active"));
  $(`#view-${name}`).classList.add("active");
  $("#page-title").textContent = pageTitles[name];
  if (name === "draft") {
    loadMaterials();
    loadChangeLog();
  }
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN");
}

function businessText(value) {
  return String(value || "")
    .replaceAll("新组件物料与寻源验证", "新组件物料与供应准备")
    .replaceAll("新组件物料主数据与寻源验证", "新组件物料与供应准备")
    .replaceAll(
      "新组件物料与供应准备为待补证",
      "新组件物料与供应准备状态为“待补证”",
    )
    .replaceAll("按路径证据逐一确认", "按数据来源记录逐一确认")
    .replaceAll(
      "逐项关闭新组件的物料主数据、采购类型、寻源和质量证据缺口",
      "逐项补齐新组件的物料主数据、采购类型、供应来源和质量资料",
    )
    .replaceAll(
      "新组件适用性尚未验证；缺少替代、质量、主数据和配置规则证据",
      "新组件适用性尚未确认；替代、质量、物料主数据和配置规则资料待补充",
    )
    .replaceAll(
      "结论基于当前导入快照，用于影响发现，不替代正式 BOM 放行与审批",
      "核查结论基于当前导入数据，不替代正式 BOM 放行与审批",
    )
    .replace(
      /路径中出现 (\d+) 个替代组；系统只提示，不自动选料/g,
      "BOM 使用路径中出现 $1 个替代组；替代料仍需人工确认",
    )
    .replaceAll(
      "分析起点下没有找到有效的下层组件路径；请核查数据快照和字段映射",
      "变更料号下未找到有效的下层物料路径；请核查导入数据和字段对应关系",
    )
    .replaceAll("当前快照未覆盖", "当前导入数据未覆盖")
    .replaceAll("未找到工厂级采购类型证据", "未找到工厂级采购类型记录")
    .replaceAll(
      "未导入供应商来源清单或 AVL/AML 证据",
      "未导入供应商来源清单或 AVL/AML 资料",
    )
    .replaceAll("顶层BOM", "顶层成品")
    .replaceAll("起点组件", "变更料号")
    .replaceAll("分析起点", "变更料号")
    .replaceAll("末端组件", "末端物料")
    .replaceAll("下层组件", "下层物料")
    .replaceAll("数据快照", "导入数据");
}

function businessSummary(result) {
  const metrics = result.metrics || {};
  const riskLevel = result.risk?.level || "—";
  const base = result.analysis_kind === "bom_version"
    ? `${result.bom_version || "当前核查版本"} 共包含 ${result.change_item_count || 0} 个变更项目`
      + `${result.ecn ? `（${result.ecn}）` : ""}，涉及 ${metrics.top_materials || 0} 个顶层成品，`
      + `关联 ${metrics.sales_order_items || 0} 个销售订单行和 `
      + `${metrics.production_orders || 0} 个生产订单。`
    : `本项${result.change?.type_label || "变更"}计划于 `
      + `${result.change?.effective_date || "待确认日期"}生效，涉及 `
      + `${metrics.top_materials || 0} 个顶层成品，关联 `
      + `${metrics.sales_order_items || 0} 个销售订单行和 `
      + `${metrics.production_orders || 0} 个生产订单。`;
  let summary = `${base}按预设规则，当前关注等级为“${riskLevel}”。`
    + `BOM 使用关系共核查 ${metrics.upstream_paths || 0} 条成品使用路径和 `
    + `${metrics.downstream_paths || 0} 条下层展开路径。`;
  if (result.material_validation?.applicable) {
    summary += `新组件物料与供应准备状态为“${result.material_validation.status}”。`;
  }
  return summary;
}

async function refreshStatus() {
  try {
    const data = await api("/api/status");
    state.status = data;
    $("#snapshot-state").textContent = data.ready ? "分析数据已就绪" : "等待导入";
    $("#m-valid-rows").textContent = formatNumber(data.valid_bom_rows);
    $("#m-tops").textContent = formatNumber(data.top_materials);
    $("#m-versions").textContent = formatNumber(data.bom_versions);
    $("#m-components").textContent = formatNumber(data.components);
    $("#m-level").textContent = formatNumber(data.max_level);
    $("#m-excluded").textContent = formatNumber(data.excluded_bom_rows);
    $("#m-row-note").textContent = data.bom_edges
      ? `已纳入 ${formatNumber(data.bom_edges)} 条 BOM 结构`
      : "纳入核查";
    $("#m-version-note").textContent = data.multi_version_boms
      ? `${data.multi_version_boms} 个 BOM 含多个版本`
      : "按数据来源区分";
    $("#source-summary").textContent = data.source_count
      ? `${data.source_count} 个数据来源 · ${formatNumber(data.valid_bom_rows)} 条可用 BOM 明细行`
      : "尚未导入";
    renderBomVersions(data.top_bom_details || []);
    renderSources(data.sources || []);
    $("#change-log-count").textContent = `${formatNumber(data.change_log_count)} 条`;
    $("#ai-explain").classList.toggle("hidden", !data.ai_configured);
    $("#ai-status").textContent = "";
    $("#ai-status").classList.add("hidden");
    if (data.ready && !state.result) {
      const response = await fetch("/api/analysis/last");
      if (response.ok) {
        const latest = await response.json();
        state.result = latest.result;
        state.report = latest.report;
        renderAnalysis(latest.result);
        renderReport(latest.report, latest.result);
        $("#report-download").href = `/api/report.md?analysis_id=${encodeURIComponent(latest.result.analysis_id)}`;
      }
    }
    await loadChangeLog();
    await loadDraftDefaults();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadDraftDefaults() {
  try {
    const data = await api("/api/draft-defaults");
    const match = /^ECN-(\d{6})$/.exec(data.next_ecn || "");
    if (match) state.baseEcnSequence = Number(match[1]);
    if (!state.draftItems.length) {
      $("#ecn").value = data.next_ecn || "ECN-000004";
    }
    if (!$("#bom-version").value) {
      $("#bom-version").value = data.default_bom_version || "BOM-000001";
    }
    renderDraftItems();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderBomVersions(items) {
  const body = $("#bom-version-table");
  body.replaceChildren();
  const multiVersionCount = items.filter((item) => item.version_count > 1).length;
  $("#bom-version-summary").textContent = items.length
    ? `${items.length} 个顶层成品 · ${items.reduce((sum, item) => sum + item.version_count, 0)} 个数据版本`
    : "尚未导入";
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty">导入后在此显示 BOM 核查范围</td></tr>';
    return;
  }
  items.forEach((item) => {
    const row = document.createElement("tr");
    const version = document.createElement("span");
    version.className = item.version_count > 1 ? "version-badge multi" : "version-badge";
    version.textContent = `${item.version_count} 个版本`;
    const values = [
      item.bom,
      item.material,
      version,
      formatNumber(item.valid_rows),
      formatNumber(item.max_level),
      (item.sources || []).join("、") || "—",
    ];
    values.forEach((value) => {
      const cell = document.createElement("td");
      if (value instanceof Node) cell.appendChild(value);
      else cell.textContent = value;
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
  if (multiVersionCount) {
    $("#bom-version-summary").textContent += ` · ${multiVersionCount} 个顶层成品含多个版本`;
  }
}

function renderSources(sources) {
  const body = $("#source-table");
  body.replaceChildren();
  if (!sources.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">导入后在此显示数据统计</td></tr>';
    return;
  }
  sources.forEach((source) => {
    const row = document.createElement("tr");
    [source.source_id, formatSourceType(source.source_type), formatNumber(source.rows_seen),
      formatNumber(source.rows_imported), source.warning_count ? `${source.warning_count} 项` : "—"]
      .forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      });
    body.appendChild(row);
  });
}

function chooseFiles(files) {
  state.files = [...files].filter((file) => file.name.toLowerCase().endsWith(".xlsx"));
  $("#file-count").textContent = state.files.length ? `已选择 ${state.files.length} 个 XLSX 文件（名称不显示）` : "尚未选择文件";
  $("#import-files").disabled = !state.files.length;
}

async function importFiles() {
  if (!state.files.length) return;
  const progress = $("#import-progress");
  const button = $("#import-files");
  progress.classList.remove("hidden");
  button.disabled = true;
  try {
    await api("/api/snapshot/start", { method: "POST" });
    for (let index = 0; index < state.files.length; index += 1) {
      const percent = Math.round((index / state.files.length) * 100);
      $("#progress-bar").style.width = `${percent}%`;
      $("#progress-label").textContent = `解析数据源 ${index + 1}/${state.files.length}`;
      await api("/api/import/file", {
        method: "POST",
        headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
        body: state.files[index],
      });
    }
    $("#progress-bar").style.width = "96%";
    $("#progress-label").textContent = "建立业务编号索引";
    await api("/api/snapshot/finish", { method: "POST" });
    $("#progress-bar").style.width = "100%";
    $("#progress-label").textContent = "导入完成";
    state.files = [];
    $("#file-input").value = "";
    chooseFiles([]);
    await refreshStatus();
    toast("核查数据已导入，敏感文字已脱敏");
  } catch (error) {
    toast(error.message, true);
    button.disabled = false;
  }
}

async function loadDemo() {
  try {
    $("#load-demo").disabled = true;
    await api("/api/demo", { method: "POST" });
    await refreshStatus();
    toast("17 份脱敏业务数据和 3 个计划备料情景已载入");
  } catch (error) {
    toast(error.message, true);
  } finally {
    $("#load-demo").disabled = false;
  }
}

async function loadMaterials() {
  if (!state.status?.ready) return;
  try {
    const data = await api("/api/materials");
    const list = $("#material-options");
    list.replaceChildren();
    data.items.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.alias;
      option.label = `上层物料 ${item.parent_count} · 下层物料 ${item.child_count} · 顶层成品 ${item.top_count}`;
      list.appendChild(option);
    });
  } catch (error) {
    toast(error.message, true);
  }
}

function resetOriginalEffectivity(message = "输入原组件物料号后自动带出") {
  $("#old-effective-date").value = "";
  const options = $("#old-effective-date-options");
  options.replaceChildren();
  options.classList.add("hidden");
  const hint = $("#old-effective-date-hint");
  hint.textContent = message;
  hint.className = "";
}

async function loadOriginalEffectivity() {
  const type = $('input[name="change_type"]:checked').value;
  const material = $("#old-component").value.trim();
  const sequence = ++state.effectivityLookupSequence;
  if (type !== "effectivity" || !material) {
    resetOriginalEffectivity();
    return;
  }

  const hint = $("#old-effective-date-hint");
  hint.textContent = "正在查询当前 BOM 中的原生效日期…";
  hint.className = "";
  try {
    const data = await api(
      `/api/material-effectivity?material=${encodeURIComponent(material)}`,
    );
    if (sequence !== state.effectivityLookupSequence) return;
    const dateInput = $("#old-effective-date");
    const options = $("#old-effective-date-options");
    dateInput.value = data.default_date || "";
    options.replaceChildren();

    if (!data.dates.length) {
      options.classList.add("hidden");
      hint.textContent = `已找到组件，但 ${data.total_uses} 个使用位置均未提供原生效日期`;
      hint.className = "error";
      return;
    }

    data.dates.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.date;
      option.textContent = `${item.date} · ${item.usage_count} 个使用位置 · ${item.top_count} 个顶层成品`;
      options.appendChild(option);
    });
    options.value = data.default_date;
    options.classList.toggle("hidden", data.dates.length <= 1);
    if (data.ambiguous) {
      hint.textContent = `发现 ${data.dates.length} 个原生效日期，已默认带出覆盖使用位置最多的日期；请在下拉框确认`;
      hint.className = "warning";
    } else {
      hint.textContent = `已从 ${data.dates[0].usage_count} 个使用位置自动带出`;
      hint.className = "";
    }
  } catch (error) {
    if (sequence !== state.effectivityLookupSequence) return;
    resetOriginalEffectivity(error.message);
    $("#old-effective-date-hint").className = "error";
  }
}

function scheduleOriginalEffectivityLookup(delay = 350) {
  clearTimeout(scheduleOriginalEffectivityLookup.timer);
  scheduleOriginalEffectivityLookup.timer = setTimeout(
    loadOriginalEffectivity,
    delay,
  );
}

function updateChangeFields() {
  const type = $('input[name="change_type"]:checked').value;
  $("#old-component-field").classList.toggle("hidden", type === "add");
  $("#target-parent-field").classList.toggle("hidden", type !== "add");
  $("#new-component-field").classList.toggle("hidden", !["add", "replace"].includes(type));
  $("#old-quantity-field").classList.toggle("hidden", type !== "quantity");
  $("#new-quantity-field").classList.toggle("hidden", !["add", "quantity"].includes(type));
  $("#old-effective-date-field").classList.toggle("hidden", type !== "effectivity");
  $("#new-effective-date-field").classList.toggle("hidden", type !== "effectivity");
  $("#effective-date").closest("label").classList.toggle("hidden", type === "effectivity");
  if (type === "effectivity") {
    scheduleOriginalEffectivityLookup(0);
  } else {
    state.effectivityLookupSequence += 1;
    resetOriginalEffectivity();
  }
}

function numericValue(selector) {
  const value = $(selector).value;
  return value === "" ? null : Number(value);
}

function currentDraftItem() {
  const type = $('input[name="change_type"]:checked').value;
  const effectiveDate = type === "effectivity"
    ? $("#new-effective-date").value
    : $("#effective-date").value;
  return {
    change_type: type,
    ecn: $("#ecn").value.trim().toUpperCase(),
    bom_version: $("#bom-version").value.trim(),
    old_component: $("#old-component").value.trim(),
    new_component: $("#new-component").value.trim(),
    target_parent: $("#target-parent").value.trim(),
    old_quantity: numericValue("#old-quantity"),
    new_quantity: numericValue("#new-quantity"),
    effective_date: effectiveDate,
    old_effective_date: $("#old-effective-date").value,
    new_effective_date: $("#new-effective-date").value,
    note: $("#change-note").value.trim(),
    old_on_hand: numericValue("#old-on-hand"),
    old_in_transit: numericValue("#old-in-transit"),
    old_open_po: numericValue("#old-open-po"),
    old_consumable_demand: numericValue("#old-consumable-demand"),
    new_on_hand: numericValue("#new-on-hand"),
    new_in_transit: numericValue("#new-in-transit"),
    new_open_po: numericValue("#new-open-po"),
    new_required_demand: numericValue("#new-required-demand"),
    planning_plant: $("#planning-plant").value.trim(),
    storage_location: $("#storage-location").value.trim(),
  };
}

function addDraftItem(event) {
  event.preventDefault();
  if (!state.status?.ready || !state.status?.bom_edges) {
    toast("请先导入包含多层展开 BOM 的数据", true);
    showView("import");
    return;
  }
  const item = currentDraftItem();
  if (!item.ecn || !item.bom_version) {
    toast("请确认该变更项目的 ECN 参考号和核查版本", true);
    return;
  }
  if (!/^BOM-\d{6}$/.test(item.bom_version)) {
    toast("核查版本必须使用 BOM-000001 格式", true);
    return;
  }
  if (state.draftItems.some((existing) => existing.ecn === item.ecn)) {
    toast("本次变更中的 ECN 参考号不能重复", true);
    return;
  }
  if (item.change_type === "add" && (!item.target_parent || !item.new_component)) {
    toast("新增组件需要填写目标父件和新组件", true);
    return;
  }
  if (["replace", "remove", "quantity", "effectivity"].includes(item.change_type)
      && !item.old_component) {
    toast("请填写原组件", true);
    return;
  }
  if (item.change_type === "replace" && !item.new_component) {
    toast("替换组件需要填写新组件", true);
    return;
  }
  if (item.change_type === "effectivity"
      && (!item.old_effective_date || !item.new_effective_date)) {
    toast("请填写原生效日期和新生效日期", true);
    return;
  }
  if (!item.effective_date) {
    toast("请填写计划生效日期", true);
    return;
  }
  state.draftItems.push(item);
  renderDraftItems();
  $("#old-component").value = "";
  $("#new-component").value = "";
  $("#target-parent").value = "";
  resetOriginalEffectivity();
  $("#new-effective-date").value = "";
  $("#change-note").value = "";
  [
    "#old-on-hand",
    "#old-in-transit",
    "#old-open-po",
    "#old-consumable-demand",
    "#new-on-hand",
    "#new-in-transit",
    "#new-open-po",
    "#new-required-demand",
  ].forEach((selector) => { $(selector).value = ""; });
  $("#planning-plant").value = "";
  $("#storage-location").value = "";
  toast(`已加入本次变更：${item.ecn}`);
}

function renderDraftItems() {
  const host = $("#draft-item-list");
  host.replaceChildren();
  $("#draft-item-count").textContent = `${state.draftItems.length} 项`;
  $("#analyze-draft").disabled = !state.draftItems.length;
  state.draftItems.forEach((item, index) => {
    item.ecn = `ECN-${String(state.baseEcnSequence + index).padStart(6, "0")}`;
  });
  $("#ecn").value = `ECN-${String(state.baseEcnSequence + state.draftItems.length).padStart(6, "0")}`;
  if (!state.draftItems.length) {
    host.innerHTML = '<p class="empty">逐项填写变更内容后加入本次核查</p>';
    return;
  }
  const labels = {
    replace: "替换组件",
    remove: "删除组件",
    add: "新增组件",
    quantity: "修改用量",
    effectivity: "改变生效日期",
  };
  state.draftItems.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "draft-item";
    const number = document.createElement("span");
    number.className = "draft-item-index";
    number.textContent = String(index + 1).padStart(2, "0");
    const main = document.createElement("div");
    main.className = "draft-item-main";
    const target = item.target_parent || item.old_component;
    const title = document.createElement("strong");
    title.textContent = `${labels[item.change_type]} · ${item.ecn}`;
    main.appendChild(title);
    const detail = document.createElement("span");
    detail.textContent = `${target} · BOM ${item.bom_version} · ${item.effective_date}`;
    main.appendChild(detail);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "移除";
    remove.addEventListener("click", () => {
      state.draftItems.splice(index, 1);
      renderDraftItems();
    });
    row.append(number, main, remove);
    host.appendChild(row);
  });
}

async function analyzeDraft() {
  if (!state.draftItems.length) return;
  const submit = $("#analyze-draft");
  submit.disabled = true;
  try {
    const data = await api("/api/analyze-batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes: state.draftItems }),
    });
    state.batchItems = data.groups;
    state.selectedBatchIndex = 0;
    selectBatchItem(0);
    state.draftItems = [];
    renderDraftItems();
    await loadChangeLog();
    $("#bom-version").value = "";
    await loadDraftDefaults();
    showView("analysis");
    toast(`${data.items.length} 个变更项目已记录，并生成 ${data.groups.length} 份核查版本报告`);
  } catch (error) {
    toast(error.message, true);
  } finally {
    submit.disabled = !state.draftItems.length;
  }
}

function selectBatchItem(index) {
  const item = state.batchItems[index];
  if (!item) return;
  state.selectedBatchIndex = index;
  state.result = item.result;
  state.report = item.report;
  renderBatchSwitcher();
  renderAnalysis(item.result);
  renderReport(item.report, item.result);
  $("#report-download").href = `/api/report.md?analysis_id=${encodeURIComponent(item.result.analysis_id)}`;
}

function renderBatchSwitcher() {
  const host = $("#batch-switcher");
  host.replaceChildren();
  host.classList.toggle("hidden", state.batchItems.length <= 1);
  state.batchItems.forEach((item, index) => {
    const button = document.createElement("button");
    button.className = `batch-item ${index === state.selectedBatchIndex ? "active" : ""}`;
    button.textContent = `${index + 1}. ${item.result.bom_version} · ${item.result.change_item_count} 项变更`;
    button.addEventListener("click", () => selectBatchItem(index));
    host.appendChild(button);
  });
}

async function loadChangeLog() {
  try {
    const data = await api("/api/change-log");
    const body = $("#change-log-table");
    body.replaceChildren();
    $("#change-log-count").textContent = `${data.items.length} 条`;
    if (!data.items.length) {
      body.innerHTML = '<tr><td colspan="8" class="empty">尚无变更日志</td></tr>';
      return;
    }
    data.items.forEach((item) => {
      const row = document.createElement("tr");
      const created = item.created_at ? item.created_at.slice(0, 16).replace("T", " ") : "—";
      [
        created,
        `${item.draft_id} / ${item.ecn}`,
        item.bom_version,
        item.change_type_label,
        item.target_material,
        `↑ ${item.upstream_paths} / ↓ ${item.downstream_paths}`,
        item.material_validation_status,
        item.risk_level,
      ].forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      });
      body.appendChild(row);
    });
  } catch (error) {
    toast(error.message, true);
  }
}

function renderAnalysis(result) {
  $("#analysis-empty").classList.add("hidden");
  $("#analysis-result").classList.remove("hidden");
  $("#result-title").textContent = result.analysis_kind === "bom_version"
    ? `${result.bom_version} · 综合核查 · ${result.change_item_count} 项变更`
    : `${result.change.type_label} · ${result.ecn || result.analysis_id} · BOM ${result.bom_version || "—"}`;
  $("#result-summary").textContent = businessSummary(result);
  const risk = $("#risk-badge");
  risk.className = `risk-badge ${result.risk.level === "高" ? "high" : result.risk.level === "中" ? "medium" : "low"}`;
  risk.querySelector("strong").textContent = result.risk.level;
  const m = result.metrics;
  $("#r-up-direct").textContent = formatNumber(m.direct_parents);
  $("#r-up-all").textContent = formatNumber(m.all_parents);
  $("#r-up-paths").textContent = formatNumber(m.upstream_paths);
  $("#r-down-direct").textContent = formatNumber(m.direct_children);
  $("#r-down-all").textContent = formatNumber(m.all_children);
  $("#r-down-paths").textContent = formatNumber(m.downstream_paths);
  $("#r-tops").textContent = formatNumber(m.top_materials);
  $("#r-sales").textContent = formatNumber(m.sales_order_items);
  $("#r-production").textContent = formatNumber(m.production_orders);
  $("#upstream-path-count").textContent = `${m.upstream_paths} 条`;
  $("#downstream-path-count").textContent = `${m.downstream_paths} 条`;

  const riskReasons = $("#risk-reasons");
  riskReasons.replaceChildren();
  result.risk.reasons.forEach((reason) => {
    const li = document.createElement("li");
    li.textContent = businessText(reason);
    riskReasons.appendChild(li);
  });

  const topTags = $("#top-tags");
  topTags.replaceChildren();
  result.top_materials.forEach((material) => {
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = material;
    topTags.appendChild(tag);
  });
  renderMaterialValidation(result.material_validation);

  renderBomTree(
    "#upstream-path-list",
    result.directions?.upstream?.paths || [],
    "upstream",
    result,
    "当前生效日期下未找到涉及成品的完整使用路径",
  );
  renderBomTree(
    "#downstream-path-list",
    result.directions?.downstream?.paths || [],
    "downstream",
    result,
    "当前生效日期下未找到涉及下层物料的完整展开路径",
  );

  renderPlanningImpact(result.planning_impact);
  renderManufacturingImpact(result.manufacturing_impact);
  renderOrderTable("#tab-production", result.production_orders, "production");
  renderOrderTable("#tab-sales", result.sales_orders, "sales");
  renderWarnings(result.warnings);
}

function renderMaterialValidation(validation) {
  const host = $("#material-validation");
  host.replaceChildren();
  if (!validation?.applicable) {
    host.classList.add("hidden");
    return;
  }
  host.classList.remove("hidden");
  host.appendChild(reportElement("strong", "", `新组件物料与供应准备 · ${businessText(validation.material)}`));
  const status = reportElement("span", "validation-status", businessText(validation.status));
  status.classList.add(
    validation.status === "通过" ? "pass" : validation.status === "不通过" ? "fail" : "pending",
  );
  host.appendChild(status);
  const checks = reportElement("div", "validation-checks");
  validation.checks.forEach((item) => {
    const row = reportElement("div", "validation-check");
    const label = reportElement("span", "", `${businessText(item.label)} · ${businessText(item.detail)}`);
    const value = reportElement("b", "", businessText(item.status));
    row.append(label, value);
    checks.appendChild(row);
  });
  host.appendChild(checks);
}

function appendMetricCards(host, items) {
  const grid = reportElement("div", "operational-metrics");
  items.forEach(([label, value, note = ""]) => {
    const card = reportElement("div", "operational-metric");
    card.append(
      reportElement("span", "", label),
      reportElement("strong", "", value),
    );
    if (note) card.appendChild(reportElement("small", "", note));
    grid.appendChild(card);
  });
  host.appendChild(grid);
}

function renderPlanningImpact(impact) {
  const host = $("#tab-planning");
  host.replaceChildren();
  if (!impact) {
    host.appendChild(reportElement("p", "empty", "尚未生成计划与备料影响"));
    return;
  }
  const head = reportElement("div", "operational-head planning");
  const text = reportElement("div");
  text.append(
    reportElement("p", "kicker", "ME 决策结论 · 计划与备料"),
    reportElement("h3", "", "计划切换建议"),
    reportElement("p", "", impact.rationale || ""),
  );
  const decision = reportElement("div", "operational-decision");
  decision.append(
    reportElement("span", "", "建议结论"),
    reportElement("strong", "", impact.recommendation || "待补供应数据"),
  );
  head.append(text, decision);
  host.appendChild(head);

  const scope = impact.scope || {};
  const scopeCard = reportElement("div", "scenario-card");
  scopeCard.appendChild(reportElement("h4", "", "本次结论适用范围"));
  scopeCard.appendChild(reportElement(
    "p",
    "muted",
    `${scope.material || "本次ECN变更料号"} · 工厂：${scope.plant || "待补"} · 库位：${scope.storage_location || "待补"}`,
  ));
  host.appendChild(scopeCard);

  const metrics = impact.metrics || {};
  appendMetricCards(host, [
    ["受影响生产订单", formatNumber(metrics.affected_production_orders || 0), "只含已释放/WIP/完工未交付"],
    ["需关注生产订单", formatNumber(metrics.active_production_orders || 0), "用于ME识别"],
    ["待交付/未完工成品", formatNumber(metrics.remaining_production_quantity || 0), "成品数量，不等同组件需求"],
    ["关联销售订单行", formatNumber(metrics.linked_sales_items || 0), `其中 ${formatNumber(metrics.pending_sales_items || 0)} 行待确认`],
  ]);

  const coverage = reportElement("div", "operational-section");
  coverage.appendChild(reportElement("h4", "", "结论依据与缺口"));
  const coverageRows = reportElement("div", "coverage-list");
  (impact.coverage || []).forEach((item) => {
    const row = reportElement("div", "coverage-row");
    const main = reportElement("div");
    main.append(
      reportElement("strong", "", item.item),
      reportElement("small", "", item.detail),
    );
    const status = reportElement("span", "coverage-status", item.status);
    status.classList.add(
      item.status === "已具备"
        ? "ready"
        : ["人工补充", "虚拟数据"].includes(item.status)
        ? "manual"
        : "missing",
    );
    row.append(main, status);
    coverageRows.appendChild(row);
  });
  coverage.appendChild(coverageRows);
  host.appendChild(coverage);

  const scenario = impact.scenario || {};
  const scenarioCard = reportElement("div", "scenario-card");
  scenarioCard.appendChild(reportElement("h4", "", "备料情景测算"));
  if (scenario.scenario_id) {
    scenarioCard.appendChild(reportElement(
      "p",
      "muted",
      `${scenario.scenario_id} · ${scenario.origin || "导入情景"} · 数据截止：${scenario.data_date || "待补"}`,
    ));
  }
  if (scenario.items) {
    scenario.items.forEach((item) => {
      scenarioCard.appendChild(reportElement(
        "div",
        "scenario-line",
        `${item.ecn}${item.scenario_id ? ` · ${item.scenario_id}` : ""} · ${item.recommendation} · ${item.rationale}`,
      ));
    });
  } else if (!scenario.provided) {
    scenarioCard.appendChild(reportElement(
      "p",
      "muted",
      "未补充可用库存、在途、Open PO和切换前后组件需求；当前只能给出数据缺口结论。",
    ));
  } else {
    const values = [
      ["旧料总暴露", scenario.old_exposure],
      ["旧料预计剩余", scenario.old_excess],
      ["新料可用供应", scenario.new_supply],
      ["新料缺口", scenario.new_gap],
    ];
    appendMetricCards(scenarioCard, values.map(([label, value]) => [
      label,
      value === null || value === undefined ? "待补" : formatNumber(value),
    ]));
  }
  scenarioCard.appendChild(reportElement("p", "boundary-text", impact.boundary || ""));
  host.appendChild(scenarioCard);
}

function renderManufacturingImpact(impact) {
  const host = $("#tab-manufacturing");
  host.replaceChildren();
  if (!impact) {
    host.appendChild(reportElement("p", "empty", "尚未生成制造执行影响"));
    return;
  }
  const head = reportElement("div", "operational-head manufacturing");
  const text = reportElement("div");
  text.append(
    reportElement("p", "kicker", "ME 决策结论 · 制造识别"),
    reportElement("h3", "", "制造影响对象"),
    reportElement(
      "p",
      "",
      "只识别已释放生产订单、WIP 和完工未交付订单；不判断返工路径或质量放行。",
    ),
  );
  const decision = reportElement("div", "operational-decision");
  const recommendation = impact.recommended_disposition || {};
  const batchRecommendations = impact.recommended_dispositions || [];
  decision.append(
    reportElement("span", "", "ECN级候选建议"),
    reportElement(
      "strong",
      "",
      batchRecommendations.length
        ? "按 ECN 分别建议"
        : `${recommendation.code || "UNCONFIRMED"} · ${recommendation.name || "未确认"}`,
    ),
  );
  head.append(text, decision);
  host.appendChild(head);

  const recommendationCard = reportElement("div", "scenario-card");
  recommendationCard.appendChild(reportElement(
    "h4", "", "建议依据",
  ));
  recommendationCard.appendChild(reportElement(
    "p",
    "",
    `${recommendation.evidence_level || "不足"} · ${recommendation.rationale || "当前数据不足，需ME补充信息。"}`,
  ));
  recommendationCard.appendChild(reportElement(
    "p",
    "boundary-text",
    "候选 Code 只用于影响分析和ME沟通，不代表正式处置或MES写回。",
  ));
  host.appendChild(recommendationCard);

  const stageCounts = impact.stage_counts || {};
  appendMetricCards(host, [
    ["WIP/生产中", formatNumber(stageCounts["WIP/生产中"] || 0)],
    ["完工未交付", formatNumber(stageCounts["完工未交付"] || 0)],
    ["需确认对象", formatNumber(impact.review_object_count || 0)],
    ["数据缺口", formatNumber((impact.data_gaps || []).length)],
  ]);

  const objectSection = reportElement("div", "operational-section");
  const objectHeading = reportElement("div", "section-heading-inline");
  objectHeading.append(
    reportElement("h4", "", "需要ME确认的生产对象"),
    reportElement("span", "", `${(impact.review_objects || []).length} 条`),
  );
  objectSection.appendChild(objectHeading);
  if (!(impact.review_objects || []).length) {
    objectSection.appendChild(reportElement("p", "muted", "当前没有匹配到开放或完工未交付生产订单。"));
  } else {
    const wrap = reportElement("div", "table-wrap");
    const table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>生产订单</th><th>成品</th><th>工厂</th><th>对象阶段</th><th>待确认事项</th></tr></thead>";
    const body = document.createElement("tbody");
    (impact.review_objects || []).forEach((item) => {
      const row = document.createElement("tr");
      [
        item.production_order,
        item.material,
        item.plant,
        item.stage,
        item.review_reason,
      ].forEach((value) => row.appendChild(reportElement("td", "", value ?? "—")));
      body.appendChild(row);
    });
    table.appendChild(body);
    wrap.appendChild(table);
    objectSection.appendChild(wrap);
  }
  host.appendChild(objectSection);

  const gaps = reportElement("div", "scenario-card");
  gaps.appendChild(reportElement("h4", "", "当前无法自动判断的内容"));
  (impact.data_gaps || []).forEach((item) => {
    gaps.appendChild(reportElement("div", "scenario-line", item));
  });
  host.appendChild(gaps);

  /* Batch results use the same card structure but provide one recommendation per ECN. */
  if (batchRecommendations.length) {
    const recommendationCard = reportElement("div", "scenario-card");
    recommendationCard.appendChild(reportElement("h4", "", "各 ECN 候选建议"));
    batchRecommendations.forEach((item) => {
      recommendationCard.appendChild(reportElement(
        "div",
        "scenario-line",
        `${item.ecn} · ${item.code || "UNCONFIRMED"} · ${item.name || "未确认"} · ${item.evidence_level || "不足"}：${item.rationale || ""}`,
      ));
    });
    host.appendChild(recommendationCard);
  }
  host.appendChild(reportElement("p", "boundary-text", impact.boundary || ""));
}

function pathNodeDetails(path) {
  return path.node_details
    || (path.nodes || []).map((alias) => ({ alias, role: "路径节点" }));
}

function isChangedTreeNode(node) {
  return /起点|变更料号|拟新增/.test(node.role || "");
}

function treeDisplayOrder(details, direction) {
  if (direction !== "upstream") return details;
  const topIndex = details.findIndex((detail) =>
    ["顶层BOM", "顶层成品"].includes(detail.role));
  const startIndex = details.findIndex((detail) => isChangedTreeNode(detail));
  if (topIndex >= 0 && startIndex >= 0) {
    return topIndex < startIndex ? details : [...details].reverse();
  }
  return [...details].reverse();
}

function buildBomForest(pathItems, direction) {
  const root = { children: new Map() };
  pathItems.forEach((path) => {
    let parent = root;
    const details = pathNodeDetails(path);
    const displayOrder = treeDisplayOrder(details, direction);
    displayOrder.forEach((detail) => {
      const key = `${detail.alias}\u0000${detail.role || ""}`;
      if (!parent.children.has(key)) {
        parent.children.set(key, {
          alias: detail.alias,
          role: detail.role || "路径节点",
          changed: false,
          pathIds: new Set(),
          ecns: new Set(),
          topMaterials: new Set(),
          children: new Map(),
        });
      }
      const node = parent.children.get(key);
      node.changed ||= isChangedTreeNode(detail);
      node.pathIds.add(path.path_id);
      if (path.ecn) node.ecns.add(path.ecn);
      if (path.top_material) node.topMaterials.add(path.top_material);
      parent = node;
    });
  });
  return [...root.children.values()];
}

function treeChangeLabel(result) {
  const change = result?.change || {};
  if (change.type === "replace") {
    return `替换：${change.old_component || "原组件"} → ${change.new_component || "新组件"}`;
  }
  if (change.type === "add") {
    return `新增：${change.new_component || "新组件"} · 目标父件 ${change.target_parent || "—"}`;
  }
  if (change.type === "remove") return `删除：${change.old_component || "原组件"}`;
  if (change.type === "quantity") return `用量变更：${change.old_component || "原组件"}`;
  if (change.type === "effectivity") return `生效日期变更：${change.old_component || "原组件"}`;
  return result?.analysis_kind === "bom_version"
    ? `${result.change_item_count || 0} 项变更 · ${result.ecn || ""}`
    : "变更影响链路";
}

function createTreeNode(node, depth, reportMode, direction) {
  const item = document.createElement("li");
  item.className = "bom-tree-item";
  const hasChildren = node.children.size > 0;
  const content = document.createElement(hasChildren ? "details" : "div");
  content.className = `bom-tree-branch ${hasChildren ? "" : "leaf"}`;
  if (hasChildren && (direction === "upstream" || depth < 2 || node.changed)) {
    content.open = true;
  }

  const row = document.createElement(hasChildren ? "summary" : "div");
  row.className = "bom-tree-row";
  const card = document.createElement("span");
  const isTop = ["顶层BOM", "顶层成品"].includes(node.role);
  const isLeaf = /末端组件|末端物料/.test(node.role);
  card.className = [
    "bom-tree-node",
    node.changed ? "changed" : "impacted",
    isTop ? "top-bom" : "",
    isLeaf ? "terminal" : "",
  ].filter(Boolean).join(" ");

  const material = document.createElement("b");
  material.textContent = node.alias;
  const role = document.createElement("small");
  const businessRole = isTop
    ? "顶层成品"
    : isChangedTreeNode(node)
      ? (/拟新增/.test(node.role) ? "拟新增物料" : "变更料号")
      : String(node.role || "")
        .replace("末端组件", "末端物料")
        .replace("下层组件", "下层物料");
  role.textContent = direction === "upstream"
    ? `${businessRole} · L${depth}`
    : businessRole;
  card.append(material, role);
  if (node.changed) {
    const badge = document.createElement("em");
    badge.textContent = "变更料号";
    card.appendChild(badge);
  }

  const metrics = document.createElement("span");
  metrics.className = "bom-tree-node-meta";
  metrics.textContent = `${node.pathIds.size} 条关联路径`;
  if (node.ecns.size) {
    const ecn = document.createElement("small");
    ecn.textContent = [...node.ecns].slice(0, 3).join("、")
      + (node.ecns.size > 3 ? ` 等 ${node.ecns.size} 个 ECN` : "");
    metrics.appendChild(ecn);
  }
  row.append(card, metrics);
  content.appendChild(row);

  if (hasChildren) {
    const children = document.createElement("ul");
    children.className = "bom-tree-children";
    [...node.children.values()]
      .sort((left, right) => Number(right.changed) - Number(left.changed)
        || left.alias.localeCompare(right.alias, "zh-CN"))
      .forEach((child) => children.appendChild(
        createTreeNode(child, depth + 1, reportMode, direction),
      ));
    content.appendChild(children);
  }
  item.appendChild(content);
  return item;
}

function appendTreeLegend(host, result, direction, reportMode) {
  const legend = document.createElement("div");
  legend.className = `bom-tree-legend ${reportMode ? "report" : ""}`;
  const directionText = direction === "upstream"
    ? "展示顺序：顶层成品 ↓ 各级父件 ↓ 变更料号"
    : "展示顺序：变更料号 → 下层物料 → 末端物料";
  const change = document.createElement("strong");
  change.textContent = treeChangeLabel(result);
  const route = document.createElement("span");
  route.textContent = directionText;
  const keys = document.createElement("span");
  keys.className = "bom-tree-legend-keys";
  keys.innerHTML = '<i class="changed"></i>变更料号 <i class="impacted"></i>关联料号 <i class="line"></i>BOM 使用路径';
  legend.append(change, route, keys);
  host.appendChild(legend);
}

function appendPathEvidenceIndex(host, pathItems, reportMode, direction) {
  const details = document.createElement("details");
  details.className = `tree-evidence-index ${reportMode ? "report" : ""}`;
  const summary = document.createElement("summary");
  summary.textContent = `查看 ${pathItems.length} 条路径编号与数据来源`;
  details.appendChild(summary);
  const list = document.createElement("div");
  list.className = "tree-evidence-records";
  pathItems.forEach((path) => {
    const row = document.createElement("div");
    const displayPath = treeDisplayOrder(pathNodeDetails(path), direction)
      .map((node) => node.alias)
      .join(" → ");
    const refs = (path.evidence || []).map((item) =>
      item.source_row ? `${item.source_id} 第 ${item.source_row} 行` : item.source_id)
      .join("；");
    row.textContent = `${path.path_id} · ${displayPath} · ${refs || "无行级来源记录"}`;
    list.appendChild(row);
  });
  details.appendChild(list);
  host.appendChild(details);
}

function renderBomTree(selectorOrHost, pathItems, direction, result, emptyMessage, reportMode = false) {
  const host = typeof selectorOrHost === "string" ? $(selectorOrHost) : selectorOrHost;
  host.replaceChildren();
  if (!pathItems.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = emptyMessage;
    host.appendChild(empty);
    return;
  }
  appendTreeLegend(host, result, direction, reportMode);
  const viewport = document.createElement("div");
  viewport.className = `bom-tree-viewport ${reportMode ? "report" : ""}`;
  const forest = document.createElement("ul");
  forest.className = "bom-tree-root";
  buildBomForest(pathItems, direction).forEach((node) =>
    forest.appendChild(createTreeNode(node, 0, reportMode, direction)));
  viewport.appendChild(forest);
  host.appendChild(viewport);
  appendPathEvidenceIndex(host, pathItems, reportMode, direction);
}

function renderOrderTable(selector, items, kind) {
  const host = $(selector);
  host.replaceChildren();
  if (!items.length) {
    host.innerHTML = '<p class="empty">没有发现关联订单</p>';
    return;
  }
  const columns = kind === "production"
    ? [["evidence_id", "来源记录号"], ["production_order", "生产订单"], ["material", "成品"], ["plant", "工厂"], ["order_quantity", "订单数量"], ["confirmed_quantity", "确认产量"], ["linked_sales_order", "销售订单"], ["status", "状态"]]
    : [["evidence_id", "来源记录号"], ["sales_order", "销售订单"], ["item", "项目"], ["material", "成品"], ["plant", "工厂"], ["order_quantity", "订单数量"], ["configuration", "配置"], ["requested_date", "需求日期"]];
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  columns.forEach(([, label]) => {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  const tbody = document.createElement("tbody");
  items.forEach((item) => {
    const row = document.createElement("tr");
    columns.forEach(([key]) => {
      const td = document.createElement("td");
      td.textContent = item[key] ?? "—";
      row.appendChild(td);
    });
    tbody.appendChild(row);
  });
  table.append(thead, tbody);
  wrap.appendChild(table);
  host.appendChild(wrap);
}

function renderWarnings(warnings) {
  const host = $("#tab-warnings");
  host.replaceChildren();
  const list = document.createElement("div");
  list.className = "warning-list";
  warnings.forEach((warning) => {
    const item = document.createElement("div");
    item.className = `warning-item ${warning.severity === "重要" ? "important" : ""}`;
    [warning.warning_id, warning.severity, warning.message].forEach((value) => {
      const span = document.createElement("span");
      span.textContent = value === warning.message ? businessText(value) : value;
      item.appendChild(span);
    });
    list.appendChild(item);
  });
  host.appendChild(list);
}

function reportElement(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function reportSection(title, number) {
  const section = reportElement("section", "report-section");
  const heading = reportElement("div", "report-section-heading");
  heading.append(
    reportElement("span", "report-section-number", number),
    reportElement("h3", "", title),
  );
  section.appendChild(heading);
  return section;
}

function renderReport(report, result = state.result) {
  $("#report-empty").classList.add("hidden");
  $("#report-result").classList.remove("hidden");
  const host = $("#report-content");
  host.replaceChildren();
  if (!result) {
    host.appendChild(reportElement("pre", "report-fallback", report));
    return;
  }

  const cover = reportElement("header", "report-cover");
  const coverMain = reportElement("div");
  coverMain.append(
    reportElement("p", "kicker", "工程变更影响核查"),
    reportElement("h1", "", "BOM 工程变更影响核查报告"),
  );
  const meta = reportElement("div", "report-meta");
  [
    ["报告编号", result.analysis_id || "—"],
    ["核查批次", result.draft_id || "—"],
    ["ECN 参考号", result.ecn || "—"],
    ["核查版本", result.bom_version || "—"],
    ["核查日期", result.calculated_on || "—"],
    ["计划生效", result.change.effective_date || "—"],
    ["变更类型", result.change.type_label],
    ["核查范围", "涉及成品及全部下层物料"],
    ["数据口径", "当前本机脱敏数据"],
  ].forEach(([label, value]) => {
    const item = reportElement("div");
    item.append(reportElement("span", "", label), reportElement("strong", "", value));
    meta.appendChild(item);
  });
  coverMain.appendChild(meta);
  const risk = reportElement("div", `report-risk risk-${result.risk.level}`);
  risk.append(
    reportElement("span", "", "关注等级"),
    reportElement("strong", "", result.risk.level),
  );
  cover.append(coverMain, risk);
  host.appendChild(cover);

  const notice = reportElement(
    "div",
    "report-notice",
    "本报告用于正式工程变更前的影响核查，不执行审批、发布或 ERP 写回。",
  );
  host.appendChild(notice);

  const summary = reportSection("变更影响概览", "01");
  summary.appendChild(reportElement("p", "report-lead", businessSummary(result)));
  const metrics = reportElement("div", "report-metrics");
  const m = result.metrics;
  [
    ["直接父件", m.direct_parents],
    ["全部上层物料", m.all_parents],
    ["成品关联路径", m.upstream_paths],
    ["直接下层物料", m.direct_children],
    ["全部下层物料", m.all_children],
    ["下层展开路径", m.downstream_paths],
    ["涉及顶层成品", m.top_materials],
    ["关联销售订单行", m.sales_order_items],
    ["关联生产订单", m.production_orders],
  ].forEach(([label, value]) => {
    const card = reportElement("div", "report-metric");
    card.append(reportElement("strong", "", formatNumber(value)), reportElement("span", "", label));
    metrics.appendChild(card);
  });
  summary.appendChild(metrics);
  host.appendChild(summary);

  const scope = reportSection("BOM 使用关系与关联范围", "02");
  [
    ["upstream", "涉及成品与上层物料 · 顶层成品 → 各级父件 → 变更料号", m.upstream_paths],
    ["downstream", "涉及下层物料 · 变更料号 → 末端物料", m.downstream_paths],
  ].forEach(([directionKey, label, pathCount]) => {
    const directionBlock = reportElement("div", "report-direction-block");
    const scopeIntro = reportElement("div", "report-scope-intro");
    scopeIntro.append(
      reportElement("strong", "", label),
      reportElement("span", "", `${pathCount} 条关联路径 · 使用源数据物料号`),
    );
    directionBlock.appendChild(scopeIntro);
    const directionPaths = result.directions?.[directionKey]?.paths || [];
    const treeHost = reportElement("div", "report-bom-tree");
    renderBomTree(
      treeHost,
      directionPaths,
      directionKey,
      result,
      "当前生效日期下未找到该范围的完整使用路径",
      true,
    );
    directionBlock.appendChild(treeHost);
    scope.appendChild(directionBlock);
  });
  host.appendChild(scope);

  const riskSection = reportSection("重点关注事项与业务关联", "03");
  const riskLayout = reportElement("div", "report-two-column");
  const reasonCard = reportElement("div", "report-card");
  reasonCard.appendChild(reportElement("h4", "", "重点关注原因"));
  const reasons = reportElement("ul", "report-list");
  result.risk.reasons.forEach((reason) =>
    reasons.appendChild(reportElement("li", "", businessText(reason))));
  reasonCard.appendChild(reasons);
  const bomCard = reportElement("div", "report-card");
  bomCard.appendChild(reportElement("h4", "", "涉及顶层成品"));
  const tags = reportElement("div", "report-tags");
  result.top_materials.forEach((item) => tags.appendChild(reportElement("span", "", item)));
  if (!result.top_materials.length) tags.appendChild(reportElement("span", "muted", "未识别"));
  bomCard.appendChild(tags);
  riskLayout.append(reasonCard, bomCard);
  riskSection.appendChild(riskLayout);

  if (result.material_validation?.applicable) {
    const validationCard = reportElement("div", "report-card report-validation-card");
    const heading = reportElement("div", "report-card-heading");
    heading.append(
      reportElement("h4", "", `新组件物料与供应准备 · ${businessText(result.material_validation.material)}`),
      reportElement("span", "", businessText(result.material_validation.status)),
    );
    validationCard.appendChild(heading);
    const records = reportElement("div", "report-records");
    result.material_validation.checks.forEach((item) => {
      records.appendChild(reportElement(
        "div",
        "report-record",
        `${businessText(item.label)}：${businessText(item.status)} · ${businessText(item.detail)}`,
      ));
    });
    validationCard.appendChild(records);
    riskSection.appendChild(validationCard);
  }

  const orderGrid = reportElement("div", "report-two-column report-orders");
  [
    ["生产订单", result.production_orders, (item) =>
      `${item.production_order} · ${item.material} · ${item.status}`],
    ["销售订单", result.sales_orders, (item) =>
      `${item.sales_order} / ${item.item} · ${item.material} · ${item.requested_date || "日期未知"}`],
  ].forEach(([title, items, formatter]) => {
    const card = reportElement("div", "report-card");
    const heading = reportElement("div", "report-card-heading");
    heading.append(reportElement("h4", "", title), reportElement("span", "", `${items.length} 条`));
    card.appendChild(heading);
    const records = reportElement("div", "report-records");
    if (!items.length) records.appendChild(reportElement("p", "muted", "没有发现关联记录"));
    items.forEach((item) => records.appendChild(reportElement("div", "report-record", formatter(item))));
    card.appendChild(records);
    orderGrid.appendChild(card);
  });
  riskSection.appendChild(orderGrid);
  host.appendChild(riskSection);

  const planningImpact = result.planning_impact || {};
  const planningSection = reportSection("计划与备料影响", "04");
  const planningLead = reportElement("div", "report-card report-operational-summary");
  const planningHeading = reportElement("div", "report-card-heading");
  planningHeading.append(
    reportElement("h4", "", "备料建议（初步）"),
    reportElement("span", "", planningImpact.recommendation || "待补供应数据"),
  );
  planningLead.append(
    planningHeading,
    reportElement("p", "report-lead", planningImpact.rationale || ""),
  );
  planningSection.appendChild(planningLead);
  const pm = planningImpact.metrics || {};
  const planningMetrics = reportElement("div", "report-metrics operational-report-metrics");
  [
    ["受影响生产订单", pm.affected_production_orders || 0],
    ["需关注生产订单", pm.active_production_orders || 0],
    ["待交付/未完工成品", pm.remaining_production_quantity || 0],
    ["关联销售订单行", pm.linked_sales_items || pm.affected_sales_items || 0],
  ].forEach(([label, value]) => {
    const card = reportElement("div", "report-metric");
    card.append(
      reportElement("strong", "", formatNumber(value)),
      reportElement("span", "", label),
    );
    planningMetrics.appendChild(card);
  });
  planningSection.appendChild(planningMetrics);
  const coverageRecords = reportElement("div", "report-records report-coverage");
  (planningImpact.coverage || []).forEach((item) => {
    coverageRecords.appendChild(reportElement(
      "div",
      "report-record",
      `${item.item}：${item.status} · ${item.detail}`,
    ));
  });
  planningSection.appendChild(coverageRecords);
  planningSection.appendChild(reportElement(
    "p",
    "boundary-text",
    planningImpact.boundary || "",
  ));
  host.appendChild(planningSection);

  const manufacturingImpact = result.manufacturing_impact || {};
  const manufacturingSection = reportSection("制造执行影响", "05");
  const manufacturingLead = reportElement("div", "report-card report-operational-summary");
  const manufacturingHeading = reportElement("div", "report-card-heading");
  const manufacturingRecommendation = manufacturingImpact.recommended_disposition || {};
  const manufacturingRecommendations = manufacturingImpact.recommended_dispositions || [];
  manufacturingHeading.append(
    reportElement("h4", "", "制造影响对象"),
    reportElement("span", "", manufacturingRecommendations.length
      ? "按 ECN 分别建议"
      : `${manufacturingRecommendation.code || "UNCONFIRMED"} · ${manufacturingRecommendation.name || "未确认"}`),
  );
  manufacturingLead.append(
    manufacturingHeading,
    reportElement(
      "p",
      "report-lead",
      manufacturingRecommendations.length
        ? manufacturingRecommendations.map((item) => `${item.ecn}：${item.code || "UNCONFIRMED"} · ${item.evidence_level || "不足"}`).join("；")
        : `${manufacturingRecommendation.evidence_level || "不足"}：${manufacturingRecommendation.rationale || "当前数据不足，需ME补充信息。"}`,
    ),
  );
  manufacturingSection.appendChild(manufacturingLead);
  const manufacturingRecords = reportElement("div", "report-records");
  (manufacturingImpact.review_objects || []).slice(0, 80).forEach((item) => {
    manufacturingRecords.appendChild(reportElement(
      "div",
      "report-record",
      `${item.ecn ? `${item.ecn} · ` : ""}${item.production_order} · `
        + `${item.material} · ${item.stage} · ${item.review_reason}`,
    ));
  });
  if ((manufacturingImpact.review_objects || []).length > 80) {
    manufacturingRecords.appendChild(reportElement(
      "p",
      "muted",
      `其余 ${(manufacturingImpact.review_objects || []).length - 80} 条对象请在结果页查看。`,
    ));
  }
  manufacturingSection.appendChild(manufacturingRecords);
  (manufacturingImpact.data_gaps || []).forEach((item) => {
    manufacturingSection.appendChild(reportElement("p", "muted", `数据缺口：${item}`));
  });
  manufacturingSection.appendChild(reportElement(
    "p",
    "boundary-text",
    manufacturingImpact.boundary || "",
  ));
  host.appendChild(manufacturingSection);

  const checklist = reportSection("分工与待办事项", "06");
  const checklistGrid = reportElement("div", "report-checklists");
  Object.entries(result.checklists || {}).forEach(([role, items]) => {
    const card = reportElement("div", "report-card");
    card.appendChild(reportElement("h4", "", role));
    items.forEach((item) => {
      const row = reportElement("div", "report-check");
      row.append(reportElement("span", "", "□"), reportElement("p", "", businessText(item)));
      card.appendChild(row);
    });
    checklistGrid.appendChild(card);
  });
  checklist.appendChild(checklistGrid);
  host.appendChild(checklist);

  const warnings = reportSection("待确认事项与数据来源", "07");
  const warningList = reportElement("div", "report-warning-list");
  result.warnings.forEach((warning) => {
    const item = reportElement(
      "div",
      `report-warning ${warning.severity === "重要" ? "important" : ""}`,
    );
    item.append(
      reportElement("strong", "", `${warning.warning_id} · ${warning.severity}`),
      reportElement("p", "", businessText(warning.message)),
    );
    warningList.appendChild(item);
  });
  warnings.appendChild(warningList);
  const evidence = reportElement("details", "report-evidence");
  const evidenceSummary = reportElement("summary", "", `展开数据来源明细（${result.evidence.edge_count} 条 BOM 关系）`);
  const evidenceList = reportElement("div", "report-records");
  result.paths.forEach((path) => {
    const refs = path.evidence.map((item) =>
      item.source_row ? `${item.source_id} 第 ${item.source_row} 行` : item.source_id).join("；");
    evidenceList.appendChild(reportElement("div", "report-record", `${path.path_id}：${refs || "无行级来源记录"}`));
  });
  evidence.append(evidenceSummary, evidenceList);
  warnings.appendChild(evidence);
  host.appendChild(warnings);

  if (result.ai_explanation) {
    const ai = reportSection("补充说明", "08");
    ai.appendChild(reportElement("p", "report-lead", result.ai_explanation));
    ai.appendChild(reportElement(
      "p",
      "muted",
      "本段文字根据脱敏核查结果辅助生成，不参与影响范围和关注等级判定。",
    ));
    host.appendChild(ai);
  }

  host.appendChild(reportElement(
    "footer",
    "report-limit",
    "使用说明：可用库存、在途、Open PO和组件需求可人工补充用于情景测算；库位、实际领料、工序和 MES 状态仍需补充。正式审批、发布和 ERP 维护仍需按业务流程完成。",
  ));
}

async function aiExplain() {
  const button = $("#ai-explain");
  button.disabled = true;
  $("#ai-status").classList.remove("hidden");
  $("#ai-status").textContent = "正在根据脱敏核查结果生成补充说明…";
  try {
    const data = await api("/api/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ analysis_id: state.result?.analysis_id || "" }),
    });
    state.report = data.report;
    state.result.ai_explanation = data.text;
    renderReport(data.report, state.result);
    $("#ai-status").textContent = "补充说明已生成；该段文字不参与影响范围和关注等级判定。";
    toast("补充说明已加入报告");
  } catch (error) {
    $("#ai-status").textContent = error.message;
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function setup() {
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => showView(button.dataset.view)));
  $$("[data-jump]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.jump)));
  $("#choose-files").addEventListener("click", () => $("#file-input").click());
  $("#file-input").addEventListener("change", (event) => chooseFiles(event.target.files));
  $("#import-files").addEventListener("click", importFiles);
  $("#load-demo").addEventListener("click", loadDemo);
  $("#change-form").addEventListener("submit", addDraftItem);
  $("#analyze-draft").addEventListener("click", analyzeDraft);
  $$('input[name="change_type"]').forEach((input) => input.addEventListener("change", updateChangeFields));
  $("#old-component").addEventListener("input", () => scheduleOriginalEffectivityLookup());
  $("#old-component").addEventListener("change", () => scheduleOriginalEffectivityLookup(0));
  $("#old-effective-date-options").addEventListener("change", (event) => {
    $("#old-effective-date").value = event.target.value;
  });
  $("#ai-explain").addEventListener("click", aiExplain);

  const drop = $("#drop-zone");
  ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.remove("dragover");
  }));
  drop.addEventListener("drop", (event) => chooseFiles(event.dataTransfer.files));

  $$(".tab").forEach((tab) => tab.addEventListener("click", () => {
    $$(".tab").forEach((item) => item.classList.toggle("active", item === tab));
    $$(".tab-panel").forEach((panel) => panel.classList.remove("active"));
    $(`#tab-${tab.dataset.tab}`).classList.add("active");
  }));

  const today = new Date();
  today.setDate(today.getDate() + 30);
  $("#effective-date").value = today.toISOString().slice(0, 10);
  $("#new-effective-date").value = today.toISOString().slice(0, 10);
  updateChangeFields();
  renderDraftItems();
  refreshStatus();

  const updateTopbarScrollState = () => {
    const topbar = $(".topbar");
    if (topbar) topbar.classList.toggle("scrolled", window.scrollY > 8);
  };
  updateTopbarScrollState();
  window.addEventListener("scroll", updateTopbarScrollState, { passive: true });
}

document.addEventListener("DOMContentLoaded", setup);
