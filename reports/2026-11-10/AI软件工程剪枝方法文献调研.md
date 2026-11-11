# AI for 软件工程：剪枝、选择性扩展与执行控制文献

检索日期：2026-11-10。收录 26 篇经 arXiv 原始页面核验的论文，以 2024—2026 年为主，另含 1 篇 2023 年基础工作。检索范围不是穷尽式系统综述。

**与你当前 B 组最直接的路线是：SWE-Verifier 的选择性采样、JevCoder 的动作类型引导、SWE-PRM 的错误分类反馈，以及 AgentDiet/SWE-Pruner 的上下文裁剪。** DARS、StopLoss、EET 分别补足关键节点重采样、失败早停、经验驱动收尾的相关工作。

下面区分作者报告的发现与针对本实验的迁移判断。不同论文的模型、基准子集、预算和发布时间不同，数字不可横向当作排行榜；没有把 Verified、Lite、旧 Pro 子集等同于当前 Pro V2。

## 1. “剪枝”需要按被裁掉的对象分类

| 类别 | 实际减少什么 | 是否能省掉候选生成本身 | 代表工作 |
| --- | --- | --- | --- |
| 选择性扩展 | 没必要产生的额外动作或轨迹 | 可以，但门控也要计费 | SWE-Verifier、DARS |
| 动作/轨迹纠偏 | 错误方向、重复行为、无效后续步骤 | 间接减少 | JevCoder、SWE-PRM、RepairAgent |
| 终止/重启 | 成功后冗余收尾、失败轨迹剩余部分 | 减少未来调用 | EET、StopLoss、FailFast-RestartSmart |
| 候选过滤/合并 | 已生成补丁、动作或执行结果的冗余 | 通常不省已经支付的生成费 | DARS 的终局去重、CodeMonkeys、LEVER |
| 上下文裁剪 | 重复、过期、不相关的历史或工具输出 | 不减少候选数，但缩短请求 | AgentDiet、SWE-Pruner、AttnCompress |
| 规则/经验筛选 | 无效、过期或有害的指导内容 | 间接影响 | Guardrails Beat Guidance、STAIR、Cortex |

MCTS 的选择性展开不自动等同于硬剪枝；提示里写一条“不要做 X”也不等同于程序保证排除了 X。应记录每篇方法真正改变了哪一步。

## 2. 最值得先读的 10 篇

这 10 篇除核对 arXiv 元数据、摘要外，还核对了方法正文或相关消融段落。其他论文在第 3 节作为摘要级筛选，不冒称已逐篇精读全文。

### P01 · SWE-Verifier：与你的动态分支门控最接近

[SWE-VERIFIER: Selective Verification for Cost-Efficient Coding Agents](https://arxiv.org/abs/2610.30284)，Kaijian Zou 等，2026-10-21，v1。[方法正文 §3.1–3.2](https://arxiv.org/html/2610.30284v1#S3)。

- **机制：**先生成一个动作，由 gate 评分；只有分数低于阈值时才追加候选并排序。因此是“一个候选后、其余候选前”的门控，不是看到状态就零候选门控。
- **去重：**训练数据构建中按动作 Jaccard 相似度过滤，再回放动作并去除重复非空观测；这不是已证明安全的线上语义等价判定。
- **证据：**作者报告验证触发率低于 4%；一个设置的解题率 75.47%→77.20%，费用为单轨迹的 1.09 倍。省钱主要是相对全步骤验证，不是相对单轨迹免费增益。
- **迁移判断：**这是当前 B 必须比较的直接相关工作。训练过的 4B verifier 分数不能直接替换成未校准的 Luna Decisions 概率并沿用阈值。

### P02 · DARS：只在关键动作处分叉，并利用先前尝试反馈

[DARS: Dynamic Action Re-Sampling to Enhance Coding Agent Performance by Adaptive Tree Traversal](https://arxiv.org/abs/2503.14269)，Vaibhav Aggarwal 等，2025-03-18，v1。[正文 §3.2–3.3](https://arxiv.org/html/2503.14269v1#S3.SS2)。

- **机制：**先深度执行一条轨迹，保留关键节点；结束后从选定节点重新展开。重点考虑 create、append、edit、submit，并限制各类展开次数；最终合并产生相同清理后补丁的轨迹。
- **证据：**SWE-bench Lite 的论文结果包含 47% Pass@1；不是 Pro V2 上的结果。
- **迁移判断：**支持“不要每步盲目分叉”，也支持带历史反馈的替代方案生成。完整复现需要恢复仓库/环境状态，代价明显大于当前单活跃轨迹的三选一。

### P03 · JevCoder：概率裁判应指导动作类别，而非只阻止动作

[JevCoder: When Does Decision Delegation Pay Off in Code Repair?](https://arxiv.org/abs/2610.19596)，Jiahao Zhang 等，2026-10-14，v1。[正文 §5.2 与附录 D.2](https://arxiv.org/html/2610.19596v1#S5.SS2)。

- **机制：**概率裁判识别当前修复状态，程序将答案映射为动作类型建议；具体命令仍由修复模型生成，并结合相关代码推荐及提交控制。
- **重要负结果：**72 道 Pro Verified 对照里，命令阻止策略成功 28 题，对照 30 题，未满足其修复损失标准；减少重复不保证保持成功率。
- **迁移判断：**最贴近“Decisions 选方向、Luna 生成动作”的接口分工。优先比较动作类别建议与命令硬阻止，不应仅把后者包装成可靠剪枝。论文 Pro Verified 不等于当前 Pro V2。

### P04 · SWE-PRM：按错误分类纠偏，比泛泛批评或规定动作更有依据

[When Agents go Astray: Course-Correcting SWE Agents with PRMs](https://arxiv.org/abs/2509.02360)，Shubham Gandhi 等，2025-09-02；核对 v2。[正文 §3.2](https://arxiv.org/html/2509.02360v2#S3.SS2)。

- **机制：**间隔读取最近若干步骤，依据任务偏离、推理/流程错误等分类输出自然语言纠偏，不必每步先生成三份动作。
- **证据：**作者报告闭源 PRM 设置在 SWE-bench Verified 上由 40.0%→50.6%；不同反馈方式效果不同，分类引导优于无分类或明确规定下一动作的变体。
- **迁移判断：**可把错误分类转成 Decisions 的有限选项，再映射为纠偏模板。但原模型能生成反馈文本，Decisions 适配不是原论文完整复现。

### P05 · EET：经验驱动的生成/选择早停

[EET: Experience-Driven Early Termination for Cost-Efficient Software Engineering Agents](https://arxiv.org/abs/2601.05777)，Yaoqi Guo 等，2026-01-09；核对 v2，arXiv 标注 ACL 2026 Findings。[正文 §3](https://arxiv.org/html/2601.05777v2#S3)。

- **机制：**从历史修复提取结构化经验；在修改、测试等里程碑检查是否还需迭代，也控制是否继续生成更多候选补丁。
- **证据：**三个 SE agent 的 Verified 实验报告费用降低 19%–55%，平均 32%，解题率损失至多 0.2%。均是作者对应配置的结果。
- **迁移判断：**很适合你的补丁查看、测试和收尾开销。应区分“已经足够好而停止”与“预计会失败而放弃”；经验库需要跨任务隔离，置信阈值需要验证。

### P06 · StopLoss：先证明监测足够便宜，再做失败早停

[StopLoss: Teaching Agents to Predict Failure and Stop Early](https://arxiv.org/abs/2610.25525)，Jiarong Liang 等，2026-10-18，v1。[正文 §2–3、§5.3](https://arxiv.org/html/2610.25525v1#S2)。

- **机制：**小模型由终局结果学习短窗口失败预测；按检查点监测，用成功轨迹校准阈值，单次前向读取分数后决定停止。
- **证据：**作者在 Verified 报告每保留成功任务成本下降 4.5%、保留 99% 成功运行；Pro 结果限于旧版公开 Python 子集，不是全部 Pro V2。
- **迁移判断：**本研究应学习其短窗口、监测费用和误杀成功任务的联合核算。其阈值是经验校准，论文明确不保证分布变化后仍成立；高价 Decisions 不自动有同样收益。

### P07 · Guardrails Beat Guidance：负向约束有依据，但不支持任意动态黑名单

[Guardrails Beat Guidance: A Large-Scale Study of Rules, Skills, and Persistent Configuration for Coding Agents](https://arxiv.org/abs/2604.11088)，Xing Zhang 等，2026-04-13；核对 v2。v1 的标题不同。[正文 §6.4](https://arxiv.org/html/2604.11088v2#S6.SS4)。

- **机制/证据：**研究持久规则文件及规则消融。在其分析中，有益个体规则是负向约束，有害个体规则是正向指令；随机规则与精挑规则的收益也相近，存在上下文启动效应的解释。
- **边界：**个体规则消融使用 35 个有区分性的任务；不是“所有否定句都有效”的定律。
- **迁移判断：**支持测试“避免无关重构”等边界，但不支持从一次 build 失败推出“禁止再改该文件”。静态规则实验也不能证明历史自动生成的硬约束一定正确。

### P08 · STAIR：先检索阶段策略，再让代理具体修复

[Reusing Past Repairs Through Hierarchical Trajectory Abstraction for Coding Agents](https://arxiv.org/abs/2607.29658)，Yisen Xu 等，2026-07-31，v1。[正文 III-B–III-D](https://arxiv.org/html/2607.29658v1#S3.SS3)。

- **机制：**把历史轨迹抽象为不同粒度的过程知识，围绕定位、规划、执行验证三个阶段检索并适配计划。
- **证据：**论文报告计划也能迁移到 mini-SWE-agent v2，Pass@1 从 75.8%→81.0%；原始轨迹直接迁移不如抽象知识。
- **迁移判断：**支持策略前置引导，而不是换 seed。它不是三分支门控，也不是严格负向剪枝；阶段知识能提供可选策略，但新任务相关性和历史泄漏需要控制。

### P09 · AgentDiet：裁掉无用、重复、过期的历史

[Reducing Cost of LLM Agents with Trajectory Reduction](https://arxiv.org/abs/2509.23586)，Yuan-An Xiao 等，2025-09-28；核对 v2，arXiv 标注 FSE 2026。[方法 §2.3–3](https://arxiv.org/html/2509.23586v2#S3)。

- **机制：**通过低成本反思模块识别历史浪费，采用滑动窗口控制裁剪开销；不是持续把完整历史交给另一个大模型重写。
- **证据：**作者报告输入减少 39.9%–59.7%，总计算费用减少 21.1%–35.9%，在其评测中保持代理表现。
- **迁移判断：**对 Decisions 长历史输入有直接启发；但删除或重写前缀可能影响主模型缓存。先只裁剪监督器视图并保留完整原始日志，单独测有损信息与真实费用。

### P10 · SWE-Pruner：由当前信息需求驱动工具输出剪枝

[SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents](https://arxiv.org/abs/2601.16746)，Yuhang Wang 等，2026-01-23；核对 v4。[正文 §3.2–3.4](https://arxiv.org/html/2601.16746v4#S3.SS2)。

- **机制：**代理为读取工具提供当前关注问题，0.6B skimmer 根据目标筛选相关代码行；没有关注问题时可返回完整输出。
- **证据：**作者报告多轮代理任务 token 减少 23%–54%；更高的单轮压缩倍数不宜挪用到仓库修复实验。
- **迁移判断：**对大段 cat/grep 输出有用；完整方法需要额外模型，不是纯提示词技巧。移植到托管 Luna 可以复用外部筛选器，但需计入部署与推理费用，并测是否丢失精确待编辑代码。

## 3. 扩展文献：另外 16 篇

本节已核对原始页面标题、年份与摘要。机制适用性是基于这些内容的筛选判断，未对其完整实验和代码作审计。

| ID | 论文与首次提交 | 方法要点、对本项目的意义与限制 |
| --- | --- | --- |
| P11 | [Fail-Fast, Restart-Smart: Early Failure Prediction and Restart for SWE Agentic Tasks](https://arxiv.org/abs/2608.03222)，2026-08-04 | 0.6B 监测器预测失败，重启时把旧 diff 作为可选 overlay，丢弃旧提示历史。可借鉴“保留产物、丢弃错误思路”；训练用了终局及中间 fail-to-pass 监督，不能在新测试时偷读隐藏 verifier。 |
| P12 | [SWE-Search: Enhancing Software Agents with Monte Carlo Tree Search and Iterative Refinement](https://arxiv.org/abs/2410.20285)，2024-10-26 | MCTS 加数值/文本价值反馈，支持回溯和替代轨迹。适合搜索背景与强对照，但包含更多状态分支与评估，不是廉价三选一的直接替代。 |
| P13 | [SE-Agent: Self-Evolution Trajectory Optimization in Multi-Step Reasoning with LLM-Based Agents](https://arxiv.org/abs/2508.02085)，2025-08-04 | 通过修订、重组、精炼既有轨迹增加搜索多样性，在 Verified 评测。支持跨轨迹利用反馈，代价是先有轨迹并支付额外优化，不能称为生成前零成本剪枝。 |
| P14 | [SWE-Pruner Pro: The Coder LLM Already Knows What to Prune](https://arxiv.org/abs/2607.18213)，2026-07-20 | 用 agent 内部表征加小 head 做逐行保留判断。直接复现需要可访问的内部表示；闭源 Luna API 不提供这种接入条件。 |
| P15 | [AttnCompress: Dynamic Attention-Guided Trajectory Compression for Software Engineering Agents](https://arxiv.org/abs/2609.08318)，2026-09-08 | 结构化分块、代理注意力相关性及滚动窗口，动态保留/召回历史。属于上下文压缩，非候选门控；代理模型的计算与证据召回失败都要计入。 |
| P16 | [Skip the Compressor: Coding Agents Fix More Issues When They Pick Whole Files](https://arxiv.org/abs/2610.21073)，2026-10-15 | 在相同上下文预算下选择完整文件，与压缩器/检索器比较，在 SWE-rebench 上报告更高修复率。重要反证：少 token 不必然更好，切坏精确代码会损害 patch 应用。 |
| P17 | [RepairAgent: An Autonomous, LLM-Based Agent for Program Repair](https://arxiv.org/abs/2403.17134)，2024-03-25 | 有限状态机指导信息收集、修复与验证工具，在 Defects4J 评测。支持阶段级工具约束，未直接证明自适应 1/3 分支；可作为不新增裁判调用的规则控制基线。 |
| P18 | [Agentless: Demystifying LLM-based Software Engineering Agents](https://arxiv.org/abs/2407.01489)，2024-07-01 | 定位、修复、验证三阶段约束问题空间，避免完全自由的工具规划。用于比较阶段化缩小搜索与自由代理；终局补丁验证不等于前置动作门控。 |
| P19 | [CodeMonkeys: Scaling Test-Time Compute for Software Engineering](https://arxiv.org/abs/2501.14723)，2025-01-24 | 多轮补丁/测试联合生成，多轨迹并行，再用生成测试及选择轨迹筛选。是串行/并行计算分配与后置选择的对照，不能算省掉已经生成的候选。 |
| P20 | [SWE-Shepherd: Advancing PRMs for Reinforcing Code Agents](https://arxiv.org/abs/2604.10493)，2026-04-12 | 由动作级轨迹奖励训练 PRM，评价中间动作。与逐步评分直接相关；摘要也强调中间奖励与终局成功未必一致，应先核对权重、训练和预算再复现。 |
| P21 | [SWE-Router: Routing in Multi-turn Agentic Software Engineering Tasks](https://arxiv.org/abs/2607.00053)，2026-06-30 | 先由便宜模型探索几轮，再基于部分轨迹决定继续还是升级强模型。体现“看到实际交互后再分配资源”；其研究对象是模型路由，直接采用会改变同模型实验约束。 |
| P22 | [ToolTree: Efficient LLM Agent Tool Planning via Dual-Feedback Monte Carlo Tree Search and Bidirectional Pruning](https://arxiv.org/abs/2603.12740)，2026-03-13 | 在工具执行前后双向评估和剪枝。方法很贴近前置判定＋后置反馈，但原评测是通用工具规划，不应说成已在 SWE-bench Pro V2 证明有效。 |
| P23 | [PrefixGuard: Online Failure Warning and Trace-Grounded Diagnosis for LLM Agents](https://arxiv.org/abs/2605.06455)，2026-05-07 | 学习事件、GRU 预测失败风险，DFA 表达紧凑诊断路径。适合错误状态机背景；预警 AUPRC 高不直接等于闭环修复更好或总成本降低。 |
| P24 | [LEVER: Learning to Verify Language-to-Code Generation with Execution](https://arxiv.org/abs/2302.08468)，2023-02-16 | 用执行结果验证候选，并合并相同执行结果的概率质量。属于程序生成基础工作，非仓库代理；一次观测相同不保证 bash 副作用或后续模型历史等价。 |
| P25 | [Cortex: Measurement-Gated Evolution and Pruning of Coding-Agent Skills and Rules](https://arxiv.org/abs/2610.14216)，2026-10-09 | 用重复的有/无规则实验决定保留或删除规则，适合验证“避坑记忆”是否真有收益。主要实验含专门构造仓库，强模型/真实项目结果较弱，不能当作广泛有效性证明。 |
| P26 | [BAGEN: Are LLM Agents Budget-Aware?](https://arxiv.org/abs/2606.00198)，2026-05-29 | 将剩余预算估计变成主动控制信号，并研究校准。是预算感知背景；“预算内能否完成”不等于“最终是否能成功”，两种停止目标要分开。 |

## 4. 对你提出的三种机制，文献分别支持到什么程度

### 4.1 动态分支门控：已有直接研究，建议从两个明确基线开始

- **状态/阶段门控：**参考 DARS 的关键动作，以及 RepairAgent 的阶段结构，先建立不额外调用模型的规则基线。
- **候选后门控：**复用 SWE-Verifier 的“一份动作→是否追加候选”结构，避免门控面对完全抽象状态时缺少信息。

两者检查时机不同：前者省判定费用但可能粗糙，后者多读一份具体动作但更有依据。本文建议在同预算下直接比较，不预设哪一种更好。训练 verifier 的触发率不能作为 Luna Decisions 的默认目标值。

### 4.2 策略前置引导：证据支持分层/类型引导，尚不支持强制三个互斥方向

JevCoder 提供“概率判断映射到动作类型”的直接范例；STAIR 支持从抽象过程知识生成阶段计划；SWE-PRM 支持根据错误类型给恢复提示。它们的共同启发是给 Policy 有用的状态与方向信息，而非代写每一个命令。

这不证明三个策略前缀就能保证语义多样性，更不证明必须互斥。不同根因假设可能需要先读取同一个文件，强行分开反而浪费。作为本文迁移建议，优先用单个阶段指令，只有确实存在竞争假设时才展开组合。

### 4.3 负向约束：需要同时读支持证据与失败证据

Guardrails Beat Guidance 支持研究有边界的负向规则；JevCoder 命令阻止的负结果说明强制阻止可能损害成功；Cortex 提醒规则需要配对重复评测，而非从一次成功就积累永久规则。

因此建议把记录写成“状态条件＋失败证据＋应避免的重复模式＋失效条件”，作为可撤销指导。这里的条件化规则是针对本项目的设计建议，不能冒称三篇论文已经共同证明该具体方案有效。

## 5. 与现有实验结合后的优先级

依据此前本地账单分析，Decisions 约占 B 已知 API 费用的一半。因而只做 action 去重，不一定能处理主要费用来源。以下是本文的研究优先级判断：

| 优先级 | 建议实验 | 相关工作与必要对照 |
| --- | --- | --- |
| 1 | 一候选后按需追加 vs 阶段规则门控 vs 当前固定三候选 | SWE-Verifier、DARS、RepairAgent |
| 2 | 完整历史选择器 vs 最近窗口/阶段状态选择器 | AgentDiet、SWE-Pruner；同时保留原始证据，测试丢失信息的代价 |
| 3 | 不干预 vs 动作类型提示 vs 直接命令阻止 | JevCoder、SWE-PRM；必须计入成功退化和所有监督费用 |
| 4 | 普通结束 vs 成功收尾提醒 vs 失败早停 | EET、StopLoss；不要把成功停止与失败放弃合成一个标签 |
| 5 | 无记忆 vs 条件化避坑记录 vs 持久黑名单 | Guardrails Beat Guidance、Cortex；不把黑名单设为默认正确 |

不能把“动态门控”“动作类型指导”“失败反馈”本身作为已确认的全新贡献。你当前更值得研究的差异点可能是：**同家族生成/Decisions 接口，在缓存计价不对称的情况下，如何联合选择检查时机、监督视图与分支预算。** 这是潜在问题定位，不是本轮检索已证明的首创性结论。

## 6. 阅读顺序与检索记录

时间有限先读 P01、P03、P04、P05、P09；它们分别覆盖追加采样、前置方向、纠偏、收尾和历史输入。然后用 P02 与 P06 补齐回溯和失败早停，再读 P07 检查负向约束的证据边界。

检索词覆盖 `selective verification`、`dynamic action re-sampling`、`trajectory reduction`、`context pruning`、`early termination`、`failure prediction`、`negative constraints`、`coding agent rules`。使用 arXiv 原始页面核对，并从 StopLoss、SWE-Verifier 等正文的参考文献继续追溯；检索摘要和当前版本标题不一致时以实际打开的 arXiv 页面为准。

排除了神经网络权重剪枝、与程序生成无关的稀疏化、临床编码中“coding”的同名干扰，以及未核验原始论文的二手概述。通用工具/语言代理论文只作为邻近方法，未当成 SWE 基准实验证据。

本次仅形成文献材料，没有修改运行代码、访问 VM 的隐藏测试内容或启动新的模型实验。
