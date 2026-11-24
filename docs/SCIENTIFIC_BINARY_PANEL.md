# V0.3.1：真实观测数据接入（独立根二元面板）

本模块不是新的 Agent，也不是通用因果发现算法。它为 V0.3 提供一个小而可复用的真实数据入口：
由领域适配器从不可变原始记录导出 `BinaryObservation`，计算描述性现象与匹配对照，再由研究者或
模型将结果登记为 `ScientificPhenomenon` / `ScientificRelationship`。

- 根 ID 表示独立采集的实验单位；同根重复、快照、随机种子和 A/A 不等于新的独立根。
- 局部谓词缺测用 null；不把未知当失败，不把局部成功当后继任务成功。
- 每个 root/condition/repeat 三元组唯一。重复记录直接报错。
- 按条件计算每根成功率，再在根间等权平均。不同条件的根集合可能不同，不能直接比较非配对均值。
- 配对只按 root 和 repeat 匹配；还必须在领域适配器核验恢复、随机数、预算、前置目标和协议一致性。
- 缺少配对时不输出总体净收益；保留未匹配计数。不得据此进行选择性剔除。
- 局部谓词为真而最终任务失败是对“该谓词足以保证本协议内任务成功”的观测反例，
  不是对任意因果机制、概率规律或跨环境普适性的证明。
- 不在此处自动估计 p 值或置信区间。独立根数与采样设计不足时，保留探索性结论。

```python
from epipilot.topic_discovery.binary_panel import (
    BinaryObservation,
    compare_paired_conditions,
    summarize_binary_panel,
)

rows = [
    BinaryObservation(root_id="r1", condition="base", repeat_id="0", success=False),
    BinaryObservation(root_id="r1", condition="repair", repeat_id="0", success=True),
]
summary = summarize_binary_panel(rows)
contrast = compare_paired_conditions(rows, treatment="repair", control="base")
```

真实领域适配器应保留原始文件路径、不可变 commit、SHA-256、逐行映射、排除/失败记录与独立性审计。
本公开仓库只保留通用代码和合成单元测试；未发表的私有研究数据、具体假说、科研档案应留在相应私有工作区。
