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

第一版只实现核心数据模型和实验准备度检查，不持久化上述生命周期状态；状态存储、搜索适配器、Agent 编排和实验执行将在后续阶段分别设计与验收。

## 8. 第一版验收范围

- [x] 独立命名空间与类型化数据契约。
- [x] 论文、研究空白、想法、审查和实验设计之间的引用完整性。
- [x] 最近邻审查的最低证据要求。
- [x] 实验准备度和预算检查。
- [x] 单元测试覆盖有效、重叠、检索不足、引用错误和超预算场景。
- [ ] 学术搜索 API 适配器。
- [ ] 全文解析和主张抽取。
- [ ] LLM Provider / Agent 适配器。
- [ ] CLI 与持久化。
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
