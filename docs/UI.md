# EpiPilot 项目认知工作台

这是以项目为中心的首版 Web UI：**看懂项目 → 找到目标差距 → 检查依据 → 导出待审阅的调查交接包**。
它不是 Agent 聊天窗口，也不会把界面展示状态写回正式项目事实。

## 安装与启动

```bash
python -m pip install -e '.[web]'
python -m epipilot.web --repo /path/to/project
```

在浏览器打开 `http://127.0.0.1:8765`。也可以用 `--port 8877` 更改端口。
不需要 Node 构建，不使用外部 CDN，不需要 API key，不会调用 LLM。
仓库必须有至少一个 Git commit。可以查看任意本地 Git 项目，不限于 EpiPilot 自身。

接入现有 EpiPilot SQLite 事件库：

```bash
python -m epipilot.web \
  --repo /path/to/project \
  --events-db /path/to/project-events.sqlite \
  --project-id your-exact-aggregate-id
```

数据库通过只读 URI 打开，使用现有 canonical replay kernel；不会自动创建数据库或猜测项目 ID。
损坏、缺失、不连续或不受支持的事件流会报错，不会显示成空的成功项目。
源码地图基于已提交版本，未提交修改仅显示警告；刷新按钮会重新读取 Git HEAD 与事件状态。

## 五个页面

| 页面 | 已实现 |
| --- | --- |
| 总览 | 项目来源、目标、验收差距、候选下一步、认知边界和快照版本 |
| 项目认知 | 已提交 Markdown 文档、安全文本渲染、源码模块说明、Python 静态结构与依赖、结论筛选 |
| 目标推进 | 正式需求、任务状态、独立验证状态、用户决策/调查边界 |
| 优化机会 | 未知问题与假设形成的调查候选、来源详情、Codex/Pi/DSH 交接包选择 |
| 成果与证据 | 证据来源/范围/时间、关联实体、最近 50 条事件元数据、Markdown 简报导出 |

模块地图是静态检查，不是完整调用图或运行行为证明。文档声明、代码观察、技术假设与独立证据分别标注。
不显示未经校准的置信百分比，不用已完成任务数量冒充项目完成率。
当前模型尚未提供逐条成功标准的验证映射，所以目标验收状态明确为 `not_assessed`。
历史证据没有显式绑定当前 Git HEAD 时，界面也不会声称当前版本已通过验证。

## 检索与交接

按 `/` 或点击“检索项目”打开来源检索。它是关键词检索而非 LLM 问答；没有匹配就明确报告没有匹配。
所有来源均是纯文本，不执行 Markdown 中的 HTML、脚本或外链。

在“优化机会”选择 `codex`、`pi` 或 `dsh`，点击“导出调查交接包”，得到包含版本、来源、目标与
执行器偏好的 JSON。它明确标记 `status=proposal_only` 和 `execution_authorized=false`。
选择不会改变已运行会话、启动模型调用、修改仓库或登记正式任务；必须先由控制层补齐权限、预算、
工作区和独立验收约束。没有把尚未实现的自动运行/恢复按钮伪装成可用功能。

## 安全边界

这是**单用户本地只读工具**，不是生产级鉴权系统。CLI 仅绑定 loopback；拒绝非本地 Host、跨源请求
和所有写 HTTP 方法；静态资源使用 CSP。没有任意文件下载接口，也不会自动展开 artifacts 路径。
禁止将该服务通过反向代理、端口映射或公网隧道直接暴露给不可信用户。

Git 扫描排除 symlink/submodule、常见密钥路径及构建目录；限制候选文件数和文本大小。但这些过滤
**不等于通用脱敏**：已提交的文档、源码注释和 canonical evidence 仍可能含敏感信息，启动前应检查。
不会记录原始执行器日志、提示词或 API 密钥。不执行项目代码，不运行 shell 拼接命令。

## 验证

```bash
python -m pip install -e '.[dev,web,browser]'
ruff format --check .
ruff check .
mypy src
pytest
python -m playwright install chromium
EPIPILOT_BROWSER_TESTS=1 pytest tests/test_web_browser.py -q
```

可通过 `EPIPILOT_CHROMIUM=/path/to/chromium` 使用已有 Chromium。
浏览器测试验证五页导航、键盘操作、来源检索、交接包选择/导出、窄屏布局和数据源失效提示。
普通 pytest 明确跳过需要浏览器的测试；独立 Web UI CI 开启并执行它们。

## 后续命令层，不在本 PR 声称完成

自然语言项目解释、经授权启动实验、审批、持久会话恢复、工作区锁、任务级预算与自动研究迭代，
需要继续通过 command service 接入。UI 不能直接修改 `PASSED`，也不能用任务拖动代替验证。
后续先增加版本校验和审批/执行命令，再开放相应控件；不重写 epistemics、verifier 或 executor 端口。

## 设计依据

- [Linear 项目总览](https://linear.app/docs/project-overview)：以目标、摘要、文档和里程碑组织项目。
- [WAI-ARIA Tabs](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/)：键盘与语义化导航。
- [FastAPI StaticFiles](https://fastapi.tiangolo.com/tutorial/static-files/) 与
  [Testing](https://fastapi.tiangolo.com/tutorial/testing/)：轻量同源 API 与可测试的静态交付。
- [Starlette middleware](https://www.starlette.io/middleware/)：Host 等 HTTP 边界。
- [Playwright assertions](https://playwright.dev/python/docs/test-assertions)：实际浏览器验证。

上面的文档用于借鉴交互与实现机制，不代表这些工具验证了 EpiPilot 的功能或安全性。
