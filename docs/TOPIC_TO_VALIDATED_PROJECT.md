# Topic-to-Validated-Project：初始框架

## 1. 目标与边界

本模块面向计算机科学研究选题，输入一个宽泛主题，逐步形成经过文献审查、可证伪性检查和最小实验设计的候选研究课题。

目标流程：

1. Topic Profiling：明确主题、研究范围、目标会议与资源约束。
2. Literature Landscape：检索并整理代表性论文、研究主张、假设和局限。
3. Candidate Generation：从失败模式、隐含假设、机制和跨领域迁移等角度生成候选假说。
4. Prior-Art Audit：检索最近邻工作，逐项比较问题、方法、机制、场景和洞见。
5. Feasibility & Experiment Design：检查数据、基线、预算和可证伪条件。
6. Minimal Experiment：执行最小区分实验，并由独立验证器检查结果。

最终目标是输出具有原创性证据和初步实证可行性的 Research Project，而不是自动承诺 Oral 录用。

## 2. 与现有 EpiPilot 模块的关系

本模块位于独立命名空间 \`epipilot.topic_discovery\`，不复用或修改现有 Problem Exploration、Scientific Discovery Core、ResearchBench-T1 或其事件协议。

这样做是有意的：

- 现有 Research 模块主要处理已建立研究项目中的 Unknown、Claim、Proposal、实验和 Evidence。
- Topic Discovery 处理更早期的主题级探索、文献先例、候选研究假说和原创性审查。
- 通过人工审核后，未来可以把选定课题显式转换为现有 Research 项目；在转换接口实现之前，不自动写入现有 Claim、Evidence 或事件流。

本模块复用仓库已有的 Python、Pydantic、Ruff、mypy 和 pytest 工程规范，但不依赖旧 ResearchState 或其存储实现。

## 3. 精简架构

第一版只保留一个确定性 Controller 和三个逻辑角色：

| 角色 | 职责 | 第一版状态 |
|---|---|---|
| Researcher | 文献地图、研究空白和候选假说 | 数据契约先行 |
| Challenger | 最近邻检索、创新差异和反对证据 | 数据契约先行 |
| Experiment Designer | 可行性、最小实验和否证条件 | 数据契约先行 |
| Controller | 状态、准入条件、预算与人工审核 | 通过纯函数检查准备度 |

Researcher、Challenger 和 Experiment Designer 是逻辑职责，不要求各自运行独立 Agent。第一版不引入多 Agent 消息总线、异步调度、向量数据库或专用图数据库。

实验执行器和独立验证器作为后续可替换接口。本次不接入 Coding Agent，不运行外部命令，也不产生模型/API 费用。

## 4. 核心数据对象

- TopicSpec：研究主题、范围、目标会议、文献时间范围和预算上限。
- PaperReference：可追溯的论文或技术来源。
- ResearchGap：由来源支持、同时保留反证的候选研究空白。
- IdeaCandidate：研究问题、可证伪假说、机制、贡献和替代解释。
- NoveltyAudit：检索范围、最近邻工作、逐项差异、重叠风险和未解决问题。
- ExperimentDesign：数据/环境、基线、控制变量、预测、指标、否证规则、留出条件和成本。
- DiscoveryWorkspace：上述对象的轻量聚合，不是第二套长期事件数据库。
- ExperimentReadiness：确定性派生报告，只判断是否具备进入人工实验审查的必要材料。

所有模型拒绝未声明字段。工作区引用必须指向已登记的来源、研究空白或候选想法。

## 5. 原创性审查原则

NoveltyAudit 的 verdict 只有三种：

- \`insufficient_search\`：检索范围或证据不足，不能判断。
- \`overlap_found\`：发现可能覆盖核心主张的先例，需要淘汰或重构。
- \`provisionally_distinct\`：完成记录在案的检索和最近邻比较后，暂未发现覆盖核心主张的工作。

\`provisionally_distinct\` 不是“绝对原创”的证明。它必须有检索平台、检索式、已检查论文、最近邻工作和具体差异记录。差异维度包括 problem、method、mechanism、scenario 和 insight。

检索覆盖不足、关键最近邻缺失或仍有阻断性重叠问题时，不得进入实验准备度通过状态。非阻断性重叠风险仍需在报告中披露。

## 6. 最小实验准备度

实验设计必须至少明确：

- 要检验的假说预测；
- 至少一个有意义的替代预测；
- 数据集或环境；
- 至少一个基线；
- 控制变量；
- 主要指标及方向；
- 可执行协议；
- 否证规则；
- 未参与方案选择的留出条件；
- 估算成本和成本单位。

\`assess_experiment_readiness\` 只检查材料是否齐备、引用是否一致、原创性审查是否暂时通过、预算是否有界且可覆盖。它不证明假说正确、不计算统计功效、不授予执行权限，也不把实验执行者的自述当作证据。

## 7. 研究状态与回退

逻辑生命周期：

\`topic_defined → landscape_built → ideas_generated → novelty_audited → experiment_designed → experiment_run → independently_verified → human_review\`

以下情况应回退或停止：

- 发现最近邻工作覆盖核心贡献：返回候选重构或淘汰。
- 检索不足：补充检索，不得把“没搜到”写成原创。
- 无法设计区分性实验：重写假说或停止。
- 预算超限：缩小实验或淘汰，不得静默超支。
- 实验无效或证据不足：保留失败记录，不得提升为支持性结论。

第一版实现核心数据模型、实验准备度检查、公开文献搜索、跨来源去重、引用边整理和显式 PDF 获取。它仍不持久化完整生命周期状态；Agent 编排、自动主张抽取和实验执行将在后续阶段分别设计与验收。

## 8. 第一版验收范围

- [x] 独立命名空间与类型化数据契约。
- [x] 论文、研究空白、想法、审查和实验设计之间的引用完整性。
- [x] 最近邻审查的最低证据要求。
- [x] 实验准备度和预算检查。
- [x] 单元测试覆盖有效、重叠、检索不足、引用错误和超预算场景。
- [x] OpenAlex、Semantic Scholar、arXiv、OpenReview 公开搜索适配器。
- [x] 论文元数据、provider-scoped ID、provider-specific citation metrics 和引用关系。
- [x] 轻量 Research Landscape 汇总与 JSON CLI 输出。
- [x] 显式 allowlist 的 OA PDF 下载；PyMuPDF 文本抽取为可选依赖。
- [x] Claim / Assumption / Limitation 抽取结果的数据契约、提示词构造和逐条原文引用校验。
- [ ] LLM Provider 接入、抽取结果的人工复核 UI/CLI 和跨论文主张综合。
- [ ] LLM Provider / Agent 适配器。
- [ ] 持久化工作区与完整 CLI 生命周期。
- [ ] 实验执行和独立验证集成。
- [ ] 专家盲评 Benchmark。

## 9. 后续实施顺序

1. 文献搜索适配器：优先支持 OpenAlex、Semantic Scholar、arXiv、OpenReview 等公开来源；保存检索时间和查询。
2. 论文阅读与主张抽取：先做结构化抽取和人工确认，不建设大型知识图谱。
3. 候选生成与对抗审查：先独立生成，再由 Challenger 攻击，避免早期讨论造成候选同质化。
4. 实验设计与冻结：把协议、基线、预算、指标和否证规则固定后再执行。
5. 执行与独立验证：使用可替换 Coding Agent，实验结果必须绑定代码版本、配置、原始产物和独立验证记录。
6. Benchmark：在相同模型、检索权限和预算下，与 Direct LLM、LLM+RAG 和文献驱动生成基线比较。

## 10. 设计参考

本框架借鉴了文献驱动研究想法生成、研究想法新颖性评价、Agentic Scientific Discovery 和开放式机器学习科研评测等方向的已有工作，包括 SciMON、ResearchAgent、Chain of Ideas、Google Co-Scientist、The AI Scientist-v2、RINoBench、AlphaIdeaBench 与 MLR-Bench。

这些工作用于确定能力边界和评价维度，不意味着本项目复现了它们，也不意味着本框架已经优于它们。后续若形成系统级研究主张，必须对最近邻方法进行版本化文献审计，并以等预算实验验证。


## 11. V0.1 Literature Discovery 使用方法

安装开发依赖：

~~~bash
python -m pip install -e '.[dev]'
~~~

执行主题文献检索：

~~~bash
epidiscover search \
  --query "embodied skill generalization" \
  --research-area "Embodied AI" \
  --year-from 2022 \
  --year-to 2026 \
  --limit 20 \
  --output .topic-discovery/embodied-skills.json
~~~

也可以只选择某些来源：

~~~bash
epidiscover search \
  --query "contact-aware manipulation" \
  --provider openalex \
  --provider arxiv \
  --limit 30
~~~

支持的来源：

| Provider | 公共接口 | 主要元数据 |
|---|---|---|
| OpenAlex | \`/works?search=...\` | DOI、摘要、作者、venue、引用数、参考文献、OA PDF |
| Semantic Scholar | \`/graph/v1/paper/search\` | 摘要、作者、DOI/arXiv ID、引用数、参考文献、OA PDF |
| arXiv | Atom API | arXiv ID、摘要、作者、分类、PDF |
| OpenReview | API v2 \`/notes/search\` | 公开投稿标题、摘要、作者、venue、PDF |

可选环境变量：

- \`OPENALEX_API_KEY\`：提高 OpenAlex API 额度；无 key 也可以使用公共接口。
- \`SEMANTIC_SCHOLAR_API_KEY\`：通过 \`x-api-key\` 请求头发送。

检索结果写入一个 JSON 文件，包含原始 provider pages、去重后的论文记录、provider 错误和 Research Landscape。不同 provider 的引用数保留为独立指标，**不会混合成一个貌似可比的引用分数**。

## 12. 去重、引用与来源边界

- DOI 归一化优先，其次使用 arXiv ID，再使用带 provider 命名空间的持久 ID；只有完全没有持久标识符时才回退到严格的规范化标题 + 年份。
- 不使用模糊标题相似度自动合并，避免把不同版本、同名工作或不同论文错误合并。
- Provider ID 必须带来源命名空间，例如 OpenAlex ID 不等于 Semantic Scholar paperId。
- Citation edge 只在当前结果集中能够解析目标论文时连接到本地 paper ID；其余保留 provider-scoped target identifier，并计入 unresolved references。
- OpenAlex 与 Semantic Scholar 的 citation counts 各自保留，不进行跨数据库数值比较。
- arXiv 和 OpenReview 的排序、年份过滤与其他 provider 不完全相同；跨来源搜索结果不是严格的统一排名。

## 13. 显式获取开放 PDF

PDF 下载不在普通搜索中自动发生。调用方必须提供允许访问的 HTTPS host：

~~~python
from pathlib import Path

from epipilot.topic_discovery.fulltext import (
    download_open_access_pdf,
    extract_pdf_text,
)

artifact = download_open_access_pdf(
    selected_paper,
    Path(".topic-discovery/fulltext"),
    allowed_hosts=["arxiv.org", "openreview.net"],
)
artifact = extract_pdf_text(artifact)
~~~

PyMuPDF 文本抽取需要额外安装：

~~~bash
python -m pip install -e '.[pdf]'
~~~

下载器会检查 PDF magic bytes、大小上限、HTTPS、显式 host allowlist 和重定向后的最终 host；不允许仅凭论文元数据中的任意 URL 下载文件。机构仓库或出版社域名需要调用者显式加入 allowlist。PDF 和文本均按内容哈希记录。

## 14. Claim / Assumption / Limitation 抽取边界

\`PaperAnalysis\`、\`ExtractedStatement\` 和 \`StatementKind\` 已定义结构化输出：

- claim：论文明确主张或贡献；
- assumption：方法或理论成立所依赖的假设；
- limitation：作者明确说明或证据支持的局限。

每条抽取必须带有章节/页码等 evidence locator 和可逐字核验的短 supporting quote。当前仅实现数据契约，**尚未实现 LLM 抽取、自动判断主张真伪或自动将抽取内容提升为研究事实**。后续抽取器应保留原文位置并允许人工复核。

## 15. API 参考

- OpenAlex API: https://help.openalex.org/api/
- OpenAlex Works search: https://help.openalex.org/api/searching/
- Semantic Scholar Academic Graph API: https://api.semanticscholar.org/api-docs/
- arXiv API User Manual: https://info.arxiv.org/help/api/user-manual.html
- OpenReview API v2: https://docs.openreview.net/reference/api-v2/openapi-definition

Provider 接口仅面向公开文献元数据和公开内容。受限论文、私有投稿、审稿人信息和需要登录的内容不会被绕过。


### 15.1 结构化主张抽取（当前为模型无关接口）

从已下载并提取的文本文件读取内容：

~~~python
from pathlib import Path

from epipilot.topic_discovery.analysis import (
    build_paper_analysis_prompt,
    validate_paper_analysis,
)

text = Path("paper.txt").read_text(encoding="utf-8")
prompt = build_paper_analysis_prompt(paper, text)
# 将 prompt 交给调用方已经配置好的 LLM；本模块不隐式发起模型请求。
analysis = validate_paper_analysis(paper, model_json, text)
~~~

验证器检查 paper ID、源文本 SHA-256，以及每条 claim/assumption/limitation 的 supporting quote 是否确实出现在对应文本中。它不能证明抽取语义完全正确，也不替代人工复核；\`reviewed_by_human\` 默认保持 false。


## 16. V0.2：Research Idea Discovery

V0.2 在 V0.1 文献检索和逐篇证据抽取之上，增加跨论文综合、候选课题生成和主动 prior-art challenge。它仍然是 model-agnostic：模块生成可审计 prompt 并校验模型响应，不隐式调用外部 LLM。

### 16.1 Research Synthesis

先为每条已验证的 Claim / Assumption / Limitation 建立 paper-qualified evidence ID：

\`<paper_id>::<statement_id>\`

同一篇论文中的 \`claim-1\` 与另一篇论文中的 \`claim-1\` 不会发生 ID 冲突。Evidence index 同时保存原文 quote、evidence locator 和来源论文 ID。

CLI：

~~~bash
epidiscover synthesis-prompt \
  --topic .topic-discovery/topic.json \
  --papers .topic-discovery/papers.json \
  --analyses .topic-discovery/analyses.json \
  --output .topic-discovery/synthesis_prompt.md
~~~

其中 \`papers.json\` 和 \`analyses.json\` 是对应 Pydantic records 的 JSON 数组。将生成的 prompt 交给已配置的 LLM，保存返回的 JSON 后，使用：

~~~python
from pathlib import Path

from epipilot.topic_discovery.synthesis import validate_research_synthesis

synthesis = validate_research_synthesis(
    topic,
    papers,
    analyses,
    model_response_json,
)
~~~

验证器要求 topic ID、论文 ID 集合、源文本 SHA-256 和完整 evidence index 与输入完全一致；theme 和 gap 只能引用已存在的 evidence IDs。Gap 的 supporting/counterevidence source IDs 必须与其 statement-level evidence 对应。

### 16.2 Diverse Idea Generation

从经过验证的 synthesis 生成 5–8 个候选课题：

~~~bash
epidiscover ideas-prompt \
  --topic .topic-discovery/topic.json \
  --synthesis .topic-discovery/synthesis.json \
  --target-count 6 \
  --output .topic-discovery/ideas_prompt.md
~~~

响应由 \`validate_idea_batch(topic, synthesis, raw_json)\` 校验。每个候选必须：
- 关联一个已存在的 research gap；
- 至少引用一条 gap 支持证据；
- 反例引用只能来自该 gap 已登记的 counterevidence；
- 提供研究问题、机制、预期贡献、替代解释和可证伪条件；
- 声明 problem / method / mechanism / scenario / insight 中的创新维度。

批次至少覆盖三个创新维度。验证器还会对标题和 proposed mechanism 做词项 Jaccard 近重复检查（默认阈值 0.82）。这只是重复候选的快速筛查，不是语义多样性或原创性证明。

### 16.3 Prior-Art Challenger

对单个候选进行主动反向检索：

~~~bash
epidiscover challenge \
  --idea .topic-discovery/idea-1.json \
  --year-from 2022 \
  --year-to 2026 \
  --limit 20 \
  --top-k 12 \
  --output .topic-discovery/idea-1-prior-art.json \
  --prompt-output .topic-discovery/idea-1-challenger.md
~~~

默认使用四个公开来源。查询从候选标题、研究问题、机制和预期贡献构造；每个来源、每个查询最多读取三页。结果按 DOI / arXiv / provider-scoped ID 去重，再用 \`weighted_field_coverage_v1\` 排序。

排序分数来自标题、摘要和关键词的加权词项覆盖度，**不是 embedding 相似度、语义相似度或 novelty score**。报告保留查询、来源覆盖、失败记录、原始论文元数据、匹配词项和覆盖警告。

将 challenger prompt 交给 LLM 后，用以下接口校验：

~~~python
from epipilot.topic_discovery.prior_art import (
    attach_prior_art_review,
    validate_prior_art_review,
    confirm_prior_art_review,
)

review = validate_prior_art_review(idea, challenge_report, model_response_json)

# 必须在人工逐篇检查后，显式确认才能进入现有 NoveltyAudit gate。
audit = confirm_prior_art_review(review, human_approved=True)

# 或直接将论文和 audit 接入已有 DiscoveryWorkspace。
workspace = attach_prior_art_review(
    workspace,
    challenge_report,
    review,
    human_approved=True,
)
~~~

校验器检查 challenge/idea ID、查询和 provider coverage、检索论文 ID、最近邻 ID，以及每条比较的 supporting quote 是否出现在对应摘要或调用方提供的全文中。模型不能将自己标记为 human-reviewed。只有显式人工确认后才生成现有 \`NoveltyAudit\`；provisionally distinct 的比较必须保留原文 quote。

### 16.4 V0.2 的证据边界

- Research Synthesis 是跨论文证据组织，不会自动证明某个 gap 真实存在。
- Candidate diversity 是结构和词项层面的初筛，不代表研究思想具有实质差异。
- Prior-art lexical ranking 可能漏掉同义表述、跨领域术语和未被索引的工作；必须检查原文和反例。
- 少于两个成功 provider 或少于五篇唯一论文时，系统会给出覆盖警告；不能据此给出 provisionally distinct 结论。
- 模型输出的 novelty verdict 只是待审意见；人工确认之前不能进入 experiment-readiness gate。
- V0.2 不自动调用 LLM、不自动提交论文、不自动执行实验，也不承诺生成 Oral / Best Paper 级选题。

## 17. V0.2 验收目标

| 验收项 | 通过条件 |
|---|---|
| Evidence lineage | 每条综合结论可回溯到 paper ID、statement ID、原文 quote |
| Synthesis integrity | 源文本哈希、证据索引和来源集合完全匹配 |
| Idea batch | 生成 5–8 个候选，覆盖至少三个创新维度 |
| Candidate quality contract | 每个候选有支持证据、替代解释和可证伪条件 |
| Diversity check | 不存在超过阈值的词项近重复候选 |
| Prior-art coverage | 保存多查询、多来源结果、分页和失败记录 |
| Adversarial review | 最近邻比较有原文 quote，覆盖不足时拒绝 distinct verdict |
| Human gate | 只有显式人工确认才能转为 NoveltyAudit |

V0.2 的完成标志是得到一批**可追溯、可反驳、可淘汰**的候选课题，而不是直接自动宣布某个课题具有顶会级创新性。下一阶段才是 V0.3：为优先候选设计最小区分性实验、强基线、反事实控制、统计判据和预算上限。


## 18. V0.3：Principle-Driven Scientific Discovery

V0.3 在 V0.1/V0.2 的文献和原创性审查能力之上，增加一条以科学关系为中心的发现协议。它不替换现有 \`DiscoveryWorkspace\`，而是通过 \`ScientificDiscoveryProject.discovery_workspace\` 将科学发现层接入原有研究工作区。

### 18.1 五个核心科学对象

| 对象 | 研究含义 | 必须具备的证据 |
|---|---|---|
| \`ScientificPhenomenon\` | 值得解释的观测现象 | 操作化变量、观测条件、来源和数据哈希 |
| \`ScientificRelationship\` | 变量之间的候选关系 | 现象 ID、变量 ID、支持证据、反证、假设和适用条件 |
| \`ScientificHypothesis\` | 对关系的竞争性解释 | 机制、前瞻预测、竞争解释和证伪条件 |
| \`DiscriminativeExperiment\` | 区分竞争假说的实验 | 干预、对照、采样单位、独立性单位、预测似然和分析计划 |
| \`ValidatedPrinciple\` | 经分级证据支持的科学原理 | 支持证据、反证、适用范围、边界、预测后果和人工审查 |

辅助对象包括 \`ScientificVariable\`、\`EvidenceReference\`、\`ExperimentResult\` 和 \`HypothesisBeliefUpdate\`。所有跨对象引用由 \`ScientificDiscoveryProject\` 校验。

### 18.2 关系成熟度不能混为一谈

\`RelationshipMaturity\` 明确区分：

- \`descriptive\`：仅记录观察到的模式。
- \`observationally_supported\`：有观测数据支持，但不能据此声称因果。
- \`intervention_supported\`：有干预实验支持。
- \`mechanistically_supported\`：同时有干预证据和机制分析。
- \`independently_replicated\`：有多个研究及独立复现证据。
- \`cross_environment_replicated\`：至少两个独立研究、两个环境中的复现证据。

因果成熟度必须有干预证据；机制成熟度必须同时有干预和机制分析；跨环境复现不能只靠同一环境中的重复采样。以上是证据门槛，不是自动证明某个关系为普适定律。

### 18.3 科学关系发现与假说生成

先把观测数据整理为带操作化定义的变量、现象和证据引用，再生成关系候选：

~~~bash
epidiscover scientific-relationships-prompt \
  --project .topic-discovery/scientific_project.json \
  --output .topic-discovery/relationships_prompt.md
~~~

模型响应必须通过 \`validate_scientific_relationships(project, raw_json)\`。验证器只允许复用项目中已经登记的现象、变量和原始证据，不允许模型自行编造证据来源。

从通过验证的关系生成竞争性假说：

~~~bash
epidiscover scientific-hypotheses-prompt \
  --project .topic-discovery/scientific_project.json \
  --output .topic-discovery/hypotheses_prompt.md
~~~

每个假说必须给出至少一个前瞻预测、竞争解释、适用范围和证伪条件。用 \`validate_scientific_hypotheses(project, raw_json)\` 检查关系和证据引用。

### 18.4 区分性实验与预期信息增益

生成实验设计：

~~~bash
epidiscover scientific-experiment-prompt \
  --project .topic-discovery/scientific_project.json \
  --hypothesis-id hypothesis-contact \
  --hypothesis-id hypothesis-feature \
  --output .topic-discovery/experiment_prompt.md
~~~

实验对每个竞争假说给出相同 outcome space 上的类别概率分布。系统据此计算预期信息增益：

\[
\operatorname{EIG}(e)
=
H(H)-\sum_o P(o\mid e)H(H\mid o,e)
\]

并按实验成本归一化：

\[
\operatorname{Score}(e)=\frac{\operatorname{EIG}(e)}{\operatorname{Cost}(e)}
\]

排序命令：

~~~bash
epidiscover scientific-score-experiments \
  --experiments .topic-discovery/experiment_candidates.json \
  --budget 100 \
  --budget-unit GPU-hour \
  --output .topic-discovery/experiment_scores.json
~~~

这一步选择的是在给定假说先验和预测似然下，预期最能区分假说的实验。它不是实验效果保证，也不是统计功效计算；错误的假说先验或概率预测会导致错误排序。

### 18.5 实验结果与假说更新

执行实验后，保存 \`ExperimentResult\`，其中包含观测类别计数、结果证据、数据哈希、样本量和协议偏差。然后运行：

~~~bash
epidiscover scientific-update-beliefs \
  --experiment .topic-discovery/experiment.json \
  --result .topic-discovery/experiment_result.json \
  --output .topic-discovery/hypothesis_update.json
~~~

当前 V0.3 的贝叶斯更新使用实验中声明的类别似然，并假设在声明的 independence unit 内观测条件独立。若同一物理根下的多个 rollout 相关，不能把它们伪装成独立样本；需要先按根聚合，或为聚类/序贯数据实现匹配的似然模型。

如果观测结果在所有假说下的概率都为零，更新器会报错，而不是强行把概率分配给某个假说。这通常表示假说集合遗漏了真实机制、实验记录错误或预测模型失配。

### 18.6 科学实验准备度

~~~bash
epidiscover scientific-readiness \
  --project .topic-discovery/scientific_project.json \
  --experiment-id experiment-contact-delay \
  --output .topic-discovery/scientific_readiness.json
~~~

准备度检查至少要求：
- 已有证据支持的现象和关系；
- 实验能够区分至少两个竞争假说；
- 预期信息增益大于零；
- 明确主题预算、成本单位和实验成本。

通过仅代表可以进入人工实验审核，不代表因果有效、统计功效充分或假说成立。

### 18.7 Validated Principle 的人工门槛

\`ValidatedPrinciple\` 的证据等级为：

- \`observational\`
- \`intervention_supported\`
- \`mechanistically_supported\`
- \`independently_replicated\`
- \`cross_environment_replicated\`

每个等级有对应证据要求。因果原理必须具有干预证据；机制原理必须同时具有干预和机制分析；独立复现需要多个研究；跨环境复现需要多个研究和多个环境。

原则记录默认不是人工审查状态。必须显式调用：

~~~python
from epipilot.topic_discovery.scientific_discovery import confirm_validated_principle

principle = confirm_validated_principle(
    principle_candidate,
    human_approved=True,
)
~~~

未经过人工确认的原则不能注册到 \`ScientificDiscoveryProject.principles\`。反证、适用范围、边界条件和未解决局限必须随原则一起保留。

### 18.8 V0.3 当前边界与下一轮验收

V0.3 当前实现的是结构化科学发现协议、模型无关提示词、证据引用校验、预期信息增益排序和基于声明似然的贝叶斯更新。它**尚未**实现：

- 原始实验数据的通用自动导入、清洗和变量识别；
- 自动因果发现算法或自动符号回归；
- 面向任意数据结构的统计功效分析；
- 真实机器人/仿真执行器的通用适配；
- 自动判断一条规律是否足够普适或具有 ICLR Oral 潜力。

这些能力必须通过具体领域的实验适配器和独立验证逐步增加，不能由 LLM 自我宣称完成。

V0.3 的验收重点是：系统能否把真实观测整理成可追溯现象，提出可验证的变量关系和竞争假说，选择具有正信息增益的区分性实验，并在结果返回后合理更新假说。第一验证领域设为具身技能能力边界和物理干预。
