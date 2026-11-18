"use strict";

function setupWorkflow() {
  const form = $("#workflow-form");
  const prepareButton = $("#workflow-prepare");
  const runButton = $("#workflow-run");
  const preflight = $("#workflow-preflight");
  const output = $("#workflow-output");
  const openPreparation = $("#workflow-open-preparation");
  const dataPlan = $("#workflow-data-plan");
  const planStatus = $("#workflow-data-status");
  const planFields = $("#workflow-data-fields");
  const planSources = $("#workflow-data-sources");
  const planIssues = $("#workflow-data-issues");
  let prepared = null;
  let latestPayload = null;
  let handedOff = false;
  let draftRevision = 0;

  const post = (endpoint, payload) => api(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  function element(tag, content) {
    const node = document.createElement(tag);
    node.textContent = content;
    return node;
  }

  function refreshFields() {
    const change = form.elements.change_type.value;
    const strict = form.elements.analysis_mode.value === "engineering";
    form.querySelectorAll("[data-wf-changes]").forEach((label) => {
      label.hidden = Boolean(change) && !label.dataset.wfChanges.split(" ").includes(change);
    });
    form.querySelectorAll("[data-wf-engineering]").forEach((label) => {
      label.hidden = !strict;
    });
  }

  function invalidateDraft(message = "参数有变化，请重新预检。") {
    prepared = null;
    latestPayload = null;
    handedOff = false;
    draftRevision++;
    runButton.disabled = true;
    openPreparation.classList.add("hidden");
    prepareButton.disabled = false;
    dataPlan.classList.add("hidden");
    preflight.textContent = message;
    refreshFields();
  }

  const appliedFocus = setupWorkflowDialogue(form, post, element, invalidateDraft);

  function review(data) {
    prepared = data;
    runButton.disabled = !data.ready;
    const { snapshot, issues } = data.context;
    preflight.replaceChildren(element("p", snapshot.id
      ? "当前导入有 " + snapshot.source_count + " 个来源、"
        + snapshot.expanded_bom_rows + " 条展开 BOM、"
        + snapshot.engineering_versions + " 个明确工程版本。"
      : "等待导入可核查的 BOM 数据。"));
    const list = document.createElement("ul");
    for (const issue of issues) {
      list.append(element("li", (issue.level === "review" ? "待核 · " : "需补充 · ") + issue.message));
    }
    if (issues.length) preflight.append(list);
    if (data.ready) preflight.append(element("p", "请核对上方参数，确认后运行。"));
    openPreparation.classList.remove("hidden");
    if (handedOff) showDataPlan(data);
  }

  function showDataPlan(data) {
    const { fields, sources } = data.data_preparation;
    planStatus.textContent = data.ready
      ? "本次参数已通过当前快照的预检。下方列有需要补充或人工核实的证据。"
      : "请按下列范围准备资料或补充参数。成功导入后会自动更新预检。";
    planFields.replaceChildren(...fields.flatMap(({ label, value }) =>
      [element("dt", label), element("dd", value)]));
    planSources.replaceChildren(...sources.map(({ label, status, detail }) => {
      const row = element("li", "");
      row.append(element("strong", label + " · " + status), element("div", detail));
      return row;
    }));
    planIssues.replaceChildren(...data.context.issues.map(({ message, level }) =>
      element("li", (level === "review" ? "待核 · " : "需补充 · ") + message)));
    dataPlan.classList.remove("hidden");
  }

  openPreparation.addEventListener("click", () => {
    if (!prepared) return;
    handedOff = true;
    showDataPlan(prepared);
    showView("import");
    $("#workflow-data-title").focus();
  });

  $("#workflow-back-to-draft").addEventListener("click", () => {
    showView("workflow");
    $("#workflow-draft").open = true;
    $("#workflow-draft summary").focus();
  });

  form.addEventListener("input", () => invalidateDraft());
  document.addEventListener("bom-snapshot-replaced", () => {
    prepared = null;
    draftRevision++;
    runButton.disabled = true;
    openPreparation.classList.add("hidden");
    output.classList.add("hidden");
    if (!latestPayload) {
      preflight.textContent = "导入快照已更新。";
      return;
    }
    preflight.textContent = "正在用新快照重新预检…";
    if (handedOff) planStatus.textContent = preflight.textContent;
    runPreflight();
  });

  async function runPreflight() {
    const revision = ++draftRevision;
    prepareButton.disabled = true;
    runButton.disabled = true;
    openPreparation.classList.add("hidden");
    prepared = null;
    try {
      const data = await post("/api/workflow/preflight", latestPayload);
      if (revision === draftRevision) review(data);
    } catch (error) {
      if (revision === draftRevision) {
        preflight.textContent = error.message;
        if (handedOff) planStatus.textContent = "自动预检未完成 · " + error.message;
      }
    } finally {
      if (revision === draftRevision) prepareButton.disabled = false;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const { role, ...draft } = Object.fromEntries(new FormData(form));
    const supplied = Object.fromEntries(Object.entries(draft).filter(([name, value]) =>
      value !== "" && !form.elements[name].closest("label").hidden));
    latestPayload = { role, draft: supplied, focus: appliedFocus() };
    runPreflight();
  });

  let displayedRun = "";

  function showResult(data) {
    displayedRun = data.run_id;
    const steps = $("#workflow-steps");
    const answer = $("#workflow-answer");
    steps.replaceChildren(...data.steps.map((step) => {
      const row = document.createElement("li");
      const status = element("span", step.status);
      status.className = "workflow-step-status";
      const detail = element("span", step.detail);
      detail.className = "muted workflow-step-detail";
      row.append(element("strong", step.name), status, detail);
      return row;
    }));
    answer.replaceChildren(element("h3", data.answer.role + "核查"));
    for (const fact of data.answer.facts) answer.append(element("p", fact));
    for (const action of data.answer.review_actions) answer.append(element("p", "待业务复核 · " + action));
    $("#workflow-report").textContent = data.report;
    $("#workflow-download").href = "/api/workflow/report.md?run_id=" + encodeURIComponent(data.run_id);
    output.classList.remove("hidden");
  }

  runButton.addEventListener("click", async () => {
    if (!prepared?.ready) return;
    const { context, confirmation_token } = prepared;
    const revision = draftRevision;
    runButton.disabled = true;
    try {
      const data = await post("/api/workflow/run", {
        context, confirmation_token, confirmed: true, include_analysis: true,
      });
      if (revision !== draftRevision) return;
      state.batchItems = [data.analysis];
      selectBatchItem(0);
      showResult(data);
      preflight.textContent = "核查已完成 · " + data.run_id;
    } catch (error) {
      preflight.textContent = error.message;
    }
  });

  $("#workflow-open-analysis").addEventListener("click", async () => {
    const run = displayedRun;
    try {
      const item = await api("/api/analysis/by-id?analysis_id=" + encodeURIComponent(run));
      if (run !== displayedRun || output.classList.contains("hidden")) return;
      state.batchItems = [item];
      selectBatchItem(0);
      showView("analysis");
    } catch (error) {
      toast(error.message, true);
    }
  });

  refreshFields();
  setupEngineeringKnowledge(post, element);
  const revision = draftRevision;
  api("/api/workflow/run").then((data) => {
    if (data.run_id && revision === draftRevision) showResult(data);
  }).catch((error) => {
    if (revision === draftRevision) preflight.textContent = error.message;
  });
}


function setupEngineeringKnowledge(post, element) {
  const panel = $("#engineering-knowledge");
  const library = $("#workflow-library");
  const pptInput = $("#workflow-ppt");
  const previewButton = $("#workflow-preview");
  const indexForm = $("#workflow-index");
  const excerpts = $("#workflow-excerpts");
  const approved = $("#workflow-approved");
  const knowledgeStatus = $("#workflow-knowledge-status");
  const references = $("#engineering-knowledge-references");
  const search = $("#engineering-knowledge-search");
  let activeId = "";
  let preview = null;

  const endpoint = (path, id) => "/api/engineering-knowledge" + path + "?analysis_id=" + encodeURIComponent(id);

  function reset() {
    activeId = "";
    panel.classList.add("hidden");
    panel.querySelector("details").open = false;
    clearPreview();
    pptInput.value = "";
    knowledgeStatus.textContent = "";
    references.replaceChildren();
  }

  function isCurrent(id) {
    return id && id === activeId && state.result?.analysis_id === id;
  }

  async function showLibrary() {
    const id = activeId;
    try {
      const data = await api(endpoint("", id));
      if (!isCurrent(id)) return;
      library.textContent = data.documents + " 份文稿 · " + data.excerpts + " 页已审核摘录";
      panel.classList.remove("hidden");
    } catch (error) {
      if (isCurrent(id)) reset();
    }
  }

  function refresh() {
    const id = state.result?.analysis_id;
    if (id === activeId) return;
    reset();
    if (!state.result?.engineering_knowledge_available || !$("#view-analysis").classList.contains("active")) return;
    activeId = id;
    showLibrary();
  }

  document.addEventListener("bom-analysis-view", refresh);
  document.addEventListener("bom-analysis-rendered", refresh);
  document.addEventListener("bom-snapshot-replaced", reset);

  search.addEventListener("click", async () => {
    const id = activeId;
    search.disabled = true;
    references.textContent = "正在检索已审核的工程知识…";
    try {
      const data = await api(endpoint("/references", id));
      if (!isCurrent(id)) return;
      references.replaceChildren();
      for (const hit of data.matches) {
        const block = element("div", "");
        block.className = "workflow-excerpt";
        const text = element("p", hit.text);
        text.className = "workflow-knowledge-text";
        block.append(element("strong", hit.source_id + " 第 " + hit.slide + " 页"), text);
        references.append(block);
      }
      if (!data.matches.length) references.textContent = data.status === "empty"
        ? "本机工程知识库还没有已审核的摘录。" : "本次没有检索到相关页。";
    } catch (error) {
      if (isCurrent(id)) references.textContent = error.message;
    } finally {
      search.disabled = false;
    }
  });

  function clearPreview() {
    preview = null;
    excerpts.replaceChildren();
    indexForm.classList.add("hidden");
    indexForm.reset();
  }

  pptInput.addEventListener("change", () => {
    clearPreview();
    knowledgeStatus.textContent = "";
  });

  previewButton.addEventListener("click", async () => {
    clearPreview();
    const file = pptInput.files[0];
    const id = activeId;
    if (!file || !file.name.toLowerCase().endsWith(".pptx")) {
      knowledgeStatus.textContent = "请选择一个 .pptx 文件。";
      return;
    }
    previewButton.disabled = true;
    knowledgeStatus.textContent = "正在本机提取文本…";
    try {
      const data = await api(endpoint("/preview", id), { method: "POST", body: file });
      if (!isCurrent(id) || file !== pptInput.files[0]) return;
      preview = data;
      for (const item of data.excerpts) {
        const block = document.createElement("div");
        block.className = "workflow-excerpt";
        block.dataset.slide = item.slide;
        const label = element("label", "索引第 " + item.slide + " 页" + (item.truncated ? "（预览已截断）" : ""));
        label.className = "workflow-check";
        const checked = document.createElement("input");
        checked.type = "checkbox";
        label.prepend(checked);
        const textarea = document.createElement("textarea");
        textarea.value = item.text;
        textarea.rows = 3;
        textarea.maxLength = 1200;
        textarea.setAttribute("aria-label", "第 " + item.slide + " 页摘录");
        block.append(label, textarea);
        excerpts.append(block);
      }
      indexForm.classList.remove("hidden");
      knowledgeStatus.textContent = "请编辑并勾选可进入知识库的摘录。";
    } catch (error) {
      if (isCurrent(id)) knowledgeStatus.textContent = error.message;
    } finally {
      previewButton.disabled = false;
    }
  });

  indexForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!preview) return;
    const picked = [...excerpts.children]
      .filter((block) => block.querySelector("input").checked)
      .map((block) => ({ slide: Number(block.dataset.slide), text: block.querySelector("textarea").value }));
    if (!picked.length) {
      knowledgeStatus.textContent = "请选择至少一页。";
      return;
    }
    const id = activeId;
    const { digest, slides, page_count, review_token } = preview;
    const save = $("#workflow-save");
    save.disabled = true;
    try {
      const data = await post(endpoint("/index", id), {
        digest, slides, page_count, review_token, approved: approved.checked, excerpts: picked,
      });
      if (!isCurrent(id)) return;
      references.replaceChildren();
      knowledgeStatus.textContent = "已加入 " + data.indexed + " 页 · " + data.source_id;
      clearPreview();
      pptInput.value = "";
      showLibrary();
    } catch (error) {
      if (isCurrent(id)) knowledgeStatus.textContent = error.message;
    } finally {
      save.disabled = false;
    }
  });

}

function setupWorkflowDialogue(form, post, element, invalidateDraft) {
  const composer = $("#workflow-dialogue");
  const message = $("#workflow-message");
  const log = $("#workflow-dialogue-log");
  const send = $("#workflow-send");
  const reset = $("#workflow-new-dialogue");
  const status = $("#workflow-dialogue-status");
  const transfer = $("#workflow-transfer");
  const list = $("#workflow-collected-fields");
  const apply = $("#workflow-apply");
  const applied = $("#workflow-transfer-status");
  const greeting = log.firstElementChild.textContent;
  const messages = [];
  let collection = null;
  let focus = [];

  function append(text, author) {
    const row = element("div", "");
    row.className = "workflow-message" + (author === "你" ? " workflow-message-user" : "");
    row.append(element("strong", author), element("p", text));
    log.append(row);
    log.scrollTop = log.scrollHeight;
  }

  function transferRows() {
    if (!collection) return [];
    const rows = [...collection.rows];
    if (collection.cleared.includes("new_component") && form.elements.new_component.value) {
      rows.push({ field: "new_component", label: "新组件", display: "清空，待确认替代件" });
    }
    const scopeChanges = ["product", "plant", "engineering_version"].some((field) =>
      collection.fields[field] && form.elements[field].value
      && collection.fields[field] !== form.elements[field].value);
    if (!collection.fields.target_bom_row && form.elements.target_bom_row.value
      && (scopeChanges || collection.cleared.includes("target_bom_row"))) {
      rows.push({ field: "target_bom_row", label: "目标 BOM 行号", display: "清空，按新范围补充" });
    }
    return rows;
  }

  function renderTransfer() {
    const rows = transferRows();
    transfer.classList.toggle("hidden", !rows.length);
    list.replaceChildren();
    for (const { field, label, display } of rows) {
      const input = form.elements[field];
      const value = collection.fields[field];
      const previous = input.selectedOptions?.[0]?.text || input.value;
      const change = input.value && input.value !== String(value ?? "");
      list.append(element("dt", label), element("dd", change ? previous + " → " + display : display));
    }
  }

  composer.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = message.value.trim();
    if (!text || send.disabled) return;
    if (messages.length >= 30) {
      status.textContent = "这一轮对话已满 30 条，可以先填入核查表，或开始新对话。";
      return;
    }
    send.disabled = true;
    reset.disabled = true;
    apply.disabled = true;
    status.textContent = "正在整理这条消息…";
    try {
      const next = await post("/api/workflow/dialogue", { messages: [...messages, text] });
      messages.push(text);
      append(text, "你");
      append(next.reply, "核查助手");
      collection = next;
      message.value = "";
      applied.textContent = "";
      status.textContent = "";
      renderTransfer();
      if (next.rows.length) invalidateDraft("对话已更新，请核对待填字段。");
    } catch (error) {
      status.textContent = error.message;
    } finally {
      send.disabled = false;
      reset.disabled = !messages.length;
      apply.disabled = false;
      message.focus();
    }
  });

  message.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });

  apply.addEventListener("click", () => {
    const rows = transferRows();
    if (!rows.length) return;
    for (const { field } of rows) form.elements[field].value = collection.fields[field] ?? "";
    focus = collection.focus;
    invalidateDraft("对话字段已填入，核对后请预检。");
    $("#workflow-draft").open = true;
    $("#workflow-draft").querySelector("summary").focus();
    applied.textContent = "已同步 " + rows.length + " 项到核查表。";
    renderTransfer();
  });

  reset.addEventListener("click", () => {
    messages.length = 0;
    collection = null;
    log.replaceChildren();
    append(greeting, "核查助手");
    reset.disabled = true;
    status.textContent = "已开始新对话，核查表中的内容可继续编辑。";
    applied.textContent = "";
    renderTransfer();
    message.focus();
  });
  form.addEventListener("input", renderTransfer);
  return () => focus;
}

document.addEventListener("DOMContentLoaded", setupWorkflow);
