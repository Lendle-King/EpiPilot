const $ = (id) => document.getElementById(id);
const labels = {
  static_inspection: "静态代码依据", goal: "正式目标", success_criterion: "验收标准 · 未评估",
  hard_constraint: "硬约束", forbidden_action: "禁止事项", soft_preference: "偏好", budget: "预算",
  proposed: "待规划", ready: "待执行", running: "执行中", agent_reported_done: "Agent 报告完成",
  verifying: "独立验证中", passed: "历史验证通过", failed: "失败", blocked: "阻塞",
  superseded: "已被替代", cancelled: "已取消", invalidated: "已失效", needs_review: "证据待复核",
  technical_unknown: "待调查问题", observed_gap: "已观察到的缺口",
  independent_evidence: "独立证据 · 适用范围有限", unverified_observation: "未验证观察",
  hypothesis_proposed: "待验证假设", hypothesis_active: "验证中的假设",
  hypothesis_supported: "有支持证据", hypothesis_refuted: "已有反对证据",
  hypothesis_inconclusive: "尚无定论", hypothesis_superseded: "已被替代的假设",
  scoped_fact: "范围内结论", historical_fact: "历史结论", decision_recorded: "已记录决策",
};
const eventLabels = {
  requirement_added: "登记项目要求", decision_made: "记录决策", unknown_registered: "登记认知缺口",
  hypothesis_created: "记录技术假设", evidence_recorded: "记录证据", task_created: "创建任务",
  task_started: "开始执行尝试", task_status_changed: "任务状态变化", context_compiled: "编译任务上下文",
  executor_observation_recorded: "记录执行器观察", verification_passed: "独立验证通过",
  verification_failed: "独立验证失败", task_superseded: "任务被替代", plan_version_created: "更新推进计划",
};
let view = null;
let selectedBackend = "codex";
let refreshing = false;
let synchronized = false;
let searchSequence = 0;

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}
function button(text, handler, className = "") {
  const node = el("button", className, text);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
function tag(status) {
  const positive = ["passed", "independent_evidence", "scoped_fact"].includes(status);
  return el("span", `tag ${positive ? "positive" : ""}`, labels[status] || status);
}
function heading(title, subtitle) {
  const node = el("div", "page-heading");
  node.append(el("h1", "", title), el("p", "muted", subtitle));
  return node;
}
function block(title, subtitle = "") {
  const node = el("section", "block");
  const head = el("div", "block-heading");
  head.append(el("h2", "", title));
  if (subtitle) head.append(el("p", "muted", subtitle));
  node.append(head);
  return node;
}
function empty(title, description) {
  const node = el("div", "empty-state");
  node.append(el("h3", "", title), el("p", "muted", description));
  return node;
}
function cards() {
  if (!view) return [];
  return [...view.goals, ...view.requirements, ...view.tasks, ...view.knowledge,
    ...view.opportunities, ...view.decisions, ...view.evidence];
}
function card(item, proposal = false) {
  const node = el("article", "item-card");
  node.append(tag(item.status), el("h3", "", item.title));
  if (item.summary) node.append(el("p", "muted", item.summary));
  const actions = el("div", "card-actions");
  actions.append(button("查看依据 ↗", () => openDetail(item), "text-button"));
  if (proposal) {
    const action = button("导出调查交接包", () => exportProposal(item.id));
    action.dataset.proposal = "true";
    action.disabled = !synchronized;
    actions.append(action);
  }
  node.append(actions);
  return node;
}
function list(container, items, description, proposal = false) {
  if (!items.length) container.append(empty("尚无记录", description));
  else items.forEach((item) => container.append(card(item, proposal)));
}
function openDetail(item) {
  if ($("search-dialog").open) $("search-dialog").close();
  $("detail-title").textContent = item.title;
  const body = $("detail-body");
  body.replaceChildren(tag(item.status), el("p", "", item.summary));
  const details = el("dl", "details-list");
  Object.entries(item.details).forEach(([key, value]) => details.append(el("dt", "", key), el("dd", "", value)));
  body.append(details, el("h3", "", "来源与适用性"));
  if (!item.refs.length) body.append(el("p", "muted", "未提供来源，不得据此做正式验收。"));
  item.refs.forEach((ref) => {
    const source = el("div", "source-ref");
    source.append(el("strong", "", ref.kind), el("p", "mono", ref.location),
      el("p", "muted", `版本：${ref.revision || "未绑定当前源码版本"}`));
    if (ref.scope) source.append(el("p", "muted", `范围：${ref.scope}`));
    if (ref.recorded_at) source.append(el("p", "muted", `记录于：${ref.recorded_at}`));
    body.append(source);
  });
  item.related_ids.forEach((id) => {
    const related = cards().find((candidate) => candidate.id === id);
    if (related) body.append(button(`关联：${related.title}`, () => openDetail(related), "related-link"));
  });
  if (!$("detail-dialog").open) $("detail-dialog").showModal();
}
function renderOverview() {
  const panel = $("panel-overview");
  panel.replaceChildren(heading("先看懂项目，再决定下一步。", "以目标和可信结果为中心，而不是以 Agent 活动量为中心。"));
  const hero = el("section", "hero");
  hero.append(el("span", "eyebrow", "当前项目结论"), el("h2", "", view.summary));
  hero.append(el("p", "", view.source_mode === "events"
    ? "正式项目状态来自只读事件回放，源码认知来自当前已提交版本；两者不自动具有相同的适用范围。"
    : "当前为真实仓库认知模式。接入 EpiPilot 事件库后，目标、任务、假设与证据将显示在同一工作区。"));
  hero.append(button("查看项目认知 →", () => activate("understanding"), "hero-action"));
  panel.append(hero);
  const metrics = el("div", "metrics");
  [["目标验收", "未评估", "不从任务数量推断"], ["已扫描源码", String(view.repository.modules.length), "静态地图，不代表测试通过"],
    ["历史验证任务", `${view.verified_task_count} / ${view.tasks.length}`, "不是项目完成百分比"],
    ["待调查机会", String(view.opportunities.length), "不自动加入当前目标"]].forEach(([label, value, note]) => {
    const metric = el("div", "metric");
    metric.append(el("span", "muted", label), el("strong", "", value), el("small", "muted", note));
    metrics.append(metric);
  });
  panel.append(metrics);
  const columns = el("div", "columns");
  const goal = block("当前目标与验收差距", "项目可以完成当前目标，同时保留未来优化空间。");
  list(goal, [...view.goals, ...view.requirements.filter((item) => item.status === "success_criterion")],
    "未接入正式目标或验收标准。没有数据不等于项目完成。请使用 --events-db 与 --project-id 连接现有事件库。");
  const next = block("下一步为什么值得做", "这是基于状态的候选行动，不是已授权的执行。");
  list(next, view.next_steps, "暂无可由当前记录支持的下一步。先补齐目标、约束与证据。");
  columns.append(goal, next);
  panel.append(columns);
  const boundaries = block("认知边界", "明确知道哪些还不知道，也是项目认知的一部分。");
  [...view.warnings, ...view.repository.warnings].forEach((warning) => boundaries.append(el("p", "notice", warning)));
  panel.append(boundaries);
}
function renderMarkdown(text) {
  const container = el("div", "document-text");
  let code = null;
  text.split("\n").forEach((line) => {
    if (line.startsWith("```")) {
      if (code) code = null;
      else { code = el("pre", "code-block"); container.append(code); }
    } else if (code) code.textContent += `${line}\n`;
    else if (/^#{1,4}\s/.test(line)) container.append(el("h3", "", line.replace(/^#+\s/, "")));
    else if (line.trim()) container.append(el("p", "", line));
  });
  return container;
}
function renderUnderstanding() {
  const panel = $("panel-understanding");
  panel.replaceChildren(heading("项目认知", "读懂项目主线，展开关键模块，再检查每个解释的依据。"));
  const documents = block("项目主线与设计文档", "文档是设计声明，不自动等同于实际实现或运行结果。");
  if (view.repository.documents.length) {
    const label = el("label", "control-label", "选择已提交文档");
    const selector = el("select");
    selector.id = "document-selector";
    label.htmlFor = selector.id;
    const content = el("div", "document-wrap");
    view.repository.documents.forEach((document, index) => {
      const option = el("option", "", document.path);
      option.value = String(index);
      selector.append(option);
    });
    const update = () => {
      const document = view.repository.documents[Number(selector.value)];
      content.replaceChildren(el("p", "source-note", `文档声明 · ${document.path} @ ${document.revision.slice(0, 12)}`), renderMarkdown(document.text));
      if (document.truncated) content.append(el("p", "notice", "文档展示已截断，并非完整内容。"));
    };
    selector.addEventListener("change", update);
    documents.append(label, selector, content);
    update();
  } else documents.append(empty("尚未发现项目文档", "此结果受扫描范围限制；先从源码地图了解结构。"));
  panel.append(documents);
  const knowledge = block("模块与结论", "静态依赖不是调用图；假设不是事实。点击查看职责、结构和来源。");
  const label = el("label", "control-label", "筛选模块与结论");
  const filter = el("input");
  filter.type = "search"; filter.id = "knowledge-filter"; filter.placeholder = "输入路径、职责或关键词";
  label.htmlFor = filter.id;
  const grid = el("div", "card-grid");
  const update = () => {
    const query = filter.value.toLowerCase();
    grid.replaceChildren();
    list(grid, view.knowledge.filter((item) => `${item.title} ${item.summary}`.toLowerCase().includes(query)), "没有匹配的源码或结论。");
  };
  filter.addEventListener("input", update);
  knowledge.append(label, filter, grid); update(); panel.append(knowledge);
}
function executorChooser() {
  const container = el("div", "executor-choice");
  const label = el("label", "control-label", "交接包执行器");
  const selector = el("select");
  selector.id = "backend-choice"; label.htmlFor = selector.id;
  view.backends.forEach((backend) => {
    const option = el("option", "", `${backend.name} · ${backend.executable_available ? "本机可找到命令" : "本机未检测到命令"}`);
    option.value = backend.name; selector.append(option);
  });
  selector.value = selectedBackend;
  selector.addEventListener("change", () => { selectedBackend = selector.value; });
  container.append(label, selector, el("p", "muted", "选择只写入待审阅的任务交接包，不会启动执行器或迁移会话。"));
  return container;
}
function renderDelivery() {
  const panel = $("panel-delivery");
  panel.replaceChildren(heading("目标推进", "展示结果缺口与验证状态。已验证、已合并、已部署是不同的事情。"));
  const flow = el("div", "lifecycle", "待执行 → 执行中 → Agent 报告完成 → 独立验证 → 已验证 / 失败");
  panel.append(flow);
  const requirements = block("验收与约束", "当前版本没有逐项需求证明映射，因此不会显示虚假的已验收标记。");
  list(requirements, view.requirements, "没有接入需求数据。此工作台不会根据 README 自动猜测验收标准。");
  panel.append(requirements);
  const tasks = block("推进任务", "执行器报告完成仍需经过独立验证。");
  list(tasks, view.tasks, "尚无任务记录。先在 EpiPilot 控制层登记并执行项目，再连接其事件数据库。");
  panel.append(tasks);
  const decisions = block("需要了解的决策", "用户决策与技术调查分开；此处仅查看，不自动批准。");
  list(decisions, view.decisions, "没有用户决策或已记录决策。"); panel.append(decisions);
}
function renderOpportunities() {
  const panel = $("panel-opportunities");
  panel.replaceChildren(heading("优化机会", "先验证是否值得做，再决定是否实施；发现机会不等于扩张当前目标。"));
  panel.append(executorChooser());
  const note = el("div", "notice", "未知问题与技术假设是调查候选，不是已证实的缺陷。当前不生成未经测量的收益数字，也不自动安排实现。");
  panel.append(note);
  const grid = el("div", "card-grid");
  list(grid, view.opportunities, "暂无有依据的机会。不要用通用优化建议填充空白。", true);
  panel.append(grid);
}
function renderEvidence() {
  const panel = $("panel-evidence");
  panel.replaceChildren(heading("成果与证据", "从结果追溯到来源、范围与记录时间，而不是相信一段完成声明。"));
  const evidence = block("证据档案", "历史证据是否适用于当前版本，需要显式版本关联或重新验证。");
  list(evidence, view.evidence, "尚无独立证据。没有执行记录时，不会制造测试结果或指标。");
  panel.append(evidence);
  const history = block("认知与推进事件", "展示最近 50 条事件元数据，不暴露原始提示词、执行器响应或私有日志。");
  if (!view.changes.length) history.append(empty("尚无事件记录", "仓库认知模式不会构造虚假的实验时间线。"));
  [...view.changes].reverse().forEach((event) => {
    const row = el("div", "event-row");
    row.append(el("span", "event-number mono", `#${event.version}`),
      el("strong", "", eventLabels[event.type] || event.type),
      el("time", "muted", new Date(event.occurred_at).toLocaleString("zh-CN")));
    history.append(row);
  });
  panel.append(history);
}
function activate(name) {
  document.querySelectorAll("[data-tab]").forEach((tab) => {
    const active = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    $(`panel-${tab.dataset.tab}`).hidden = !active;
  });
}
async function request(path) {
  const response = await fetch(path, {cache: "no-store"});
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try { const data = await response.json(); if (typeof data.detail === "string") message = data.detail; } catch { /* Do not expose raw error bodies. */ }
    throw new Error(message);
  }
  return response;
}
function showError(message) {
  $("error").hidden = false;
  $("error").textContent = message;
}
async function refresh() {
  if (refreshing) return;
  refreshing = true; $("refresh").disabled = true;
  try {
    const next = await (await request("/api/project")).json();
    view = next; synchronized = true;
    $("error").hidden = true;
    $("project-name").textContent = view.name;
    $("source-label").textContent = view.source_mode === "events" ? "真实仓库 + 正式事件回放" : "真实仓库认知 · 未连接执行事件";
    $("revision").textContent = `${view.repository.branch} @ ${view.repository.revision.slice(0, 12)}`;
    $("dirty").textContent = view.repository.dirty ? "存在未提交修改" : "工作区干净";
    $("event-version").textContent = `事件版本 ${view.event_version}`;
    $("sync-time").textContent = `快照 ${new Date(view.captured_at).toLocaleTimeString("zh-CN")} · 手动刷新`;
    renderOverview(); renderUnderstanding(); renderDelivery(); renderOpportunities(); renderEvidence();
    $("content").hidden = false; $("content").classList.remove("stale");
  } catch (error) {
    synchronized = false;
    showError(`${error.message} ${view ? "当前保留上次成功快照，并非最新状态。" : "尚未加载项目，请检查服务端配置。"}`);
    $("content").classList.add("stale");
    document.querySelectorAll("[data-proposal]").forEach((node) => { node.disabled = true; });
  } finally { $("loading").hidden = true; refreshing = false; $("refresh").disabled = false; }
}
function download(text, filename, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const anchor = el("a"); anchor.href = url; anchor.download = filename;
  document.body.append(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function exportProposal(id) {
  if (!synchronized) return;
  try {
    const proposal = await (await request(`/api/proposals/${encodeURIComponent(id)}?backend=${selectedBackend}`)).json();
    download(JSON.stringify(proposal, null, 2), "epipilot-task-proposal.json", "application/json");
  } catch (error) { showError(error.message); }
}
$("export-report").addEventListener("click", async () => {
  try { download(await (await request("/api/report")).text(), "epipilot-project-report.md", "text/markdown"); }
  catch (error) { showError(error.message); }
});
$("refresh").addEventListener("click", refresh);
$("close-detail").addEventListener("click", () => $("detail-dialog").close());
$("close-search").addEventListener("click", () => $("search-dialog").close());
function openSearch() { $("search-dialog").showModal(); $("search-query").focus(); }
$("open-search").addEventListener("click", openSearch);
$("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const sequence = ++searchSequence;
  const query = $("search-query").value.trim();
  if (!query) return;
  $("search-results").replaceChildren(el("p", "muted", "正在检索…"));
  try {
    const result = await (await request(`/api/search?q=${encodeURIComponent(query)}`)).json();
    if (sequence !== searchSequence) return;
    $("search-results").replaceChildren();
    list($("search-results"), result.results, "未找到匹配来源。检索采用关键词匹配，不会生成没有依据的答案。");
  } catch (error) { if (sequence === searchSequence) $("search-results").replaceChildren(el("p", "notice", error.message)); }
});
const tabs = [...document.querySelectorAll("[data-tab]")];
tabs.forEach((tab) => tab.addEventListener("click", () => activate(tab.dataset.tab)));
function setOrientation() { $("tabs").setAttribute("aria-orientation", window.innerWidth < 800 ? "horizontal" : "vertical"); }
setOrientation(); window.addEventListener("resize", setOrientation);
$("tabs").addEventListener("keydown", (event) => {
  const index = tabs.indexOf(document.activeElement);
  if (index < 0) return;
  const vertical = $("tabs").getAttribute("aria-orientation") === "vertical";
  const previous = vertical ? "ArrowUp" : "ArrowLeft";
  const next = vertical ? "ArrowDown" : "ArrowRight";
  let target = null;
  if (event.key === previous) target = (index + tabs.length - 1) % tabs.length;
  if (event.key === next) target = (index + 1) % tabs.length;
  if (event.key === "Home") target = 0;
  if (event.key === "End") target = tabs.length - 1;
  if (target !== null) { event.preventDefault(); activate(tabs[target].dataset.tab); tabs[target].focus(); }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)
      && !$("detail-dialog").open && !$("search-dialog").open) { event.preventDefault(); openSearch(); }
});
refresh();
