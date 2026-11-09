# 第 2 部分实现对照与验收边界

源方法：`Kunyanli230/small-supervise-big-and-big-supervise-small`，提交 `7e8a2e3884e6c5d1417efd49adc8638c5f00dcd3`，`src/d3study/study.py` 的 `StepD3VerifierAgent.query()`。

底层依赖：`mini-swe-agent==2.4.6`，其中 `DefaultAgent` 负责 run/execute/save，`parse_toolcall_actions` 负责原生 bash 工具解析，`format_toolcall_observation_messages` 负责 tool-call ID 配对，`DockerEnvironment._check_finished` 保留精确提交标记。Mini 源码：[SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)，MIT；安装包保留原版权与许可证。本项目没有复制或重新发布 Mini 源码。

## 映射

| 原方法 | 此仓库 |
| --- | --- |
| 3 次冻结历史采样 | `agent.SupervisedAgent.query()`，每个候选 deep copy 同一个前缀 |
| `_extract_thought_and_action` | `Policy.query()` + Mini native parser，`visible_thought()` 只取实际可见文本 |
| `_format_shared_prefix` | `agent.shared_prefix()`，只含已选动作与观测 |
| 原始 `INSTRUCTIONS` | `prompts.INSTRUCTIONS` 原文保留 |
| D3 `choose(state, criteria, instructions)` | `agent.Decisions.choose()`，OpenRouter Choice |
| 无效选择的概率回退 | 有效候选中取最大概率，单列 fallback |
| 只执行 selected message | `DefaultAgent.step()` → `execute_actions()`，其余候选只进 sidecar |
| 原 Mini 格式反馈 | 三候选全无效时抛 `FormatError`，下一步计入正常预算 |
| 原生提交检测 | Mini Docker 的 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 检测 |
| Verified loader / grader | 固定 V2 `instruction.md` / `task.toml` / 全新容器的官方 verifier |
| 源 D3 / Ollama 身份校验 | 同家族模型配置校验，Models API、provider、价格与每请求回执 |

未把原实现中的终局选择函数、D3 模型权重、Ollama digest 校验、GPU 内存恢复或云项目脚本带入此仓库。也没有依赖完整 SWE-Verifier 包或修改其底层框架。

## 有意的运行差异

1. V2 完整说明替代 Verified 短问题字段；提示中的工作目录由镜像探测为 `/app` 或 `/testbed`，未沿用写死 `/testbed` 的旧模板。
2. 输入界限采用保守字节估算，原代码原生 D3 token counter 不适用于托管 Luna；在回执中保留实际用量。
3. 限时/异常终止尽力抓取当前工作树，原代码的空提交不作为唯一恢复方式。
4. 本轮不启用“异常自动跳题记失败”的旧策略。生成状态与评分状态分离。
5. 重试收窄为传输错误、429、5xx，最多两次；不保证托管 API 幂等，也不声称未知超时免费。
6. 不做额外 LLM 摘要、候选执行预览、人工挑选、回溯或终局裁判。
7. `single` 只移除候选扩展和 Choice，保留同一原生 agent/tool/submission 路径。

## 可验证的边界

离线测试覆盖实际 HTTP payload/response 适配与 Mini run loop 的组合，包括工具调用配对、跨轮隔离、无效候选回退、监督器异常、预算/上下文/时间停止、费用缺失与重试、任务版本/补丁封存、失败时保存补丁、独立评分与 C 引用统计。

真实 API 可用性、具体 provider 对 Choice routing 参数的支持、真实模型工具输出、V2 镜像内依赖与官方 verifier 的执行，需要首次部署完成 live smoke。未完成这些检查前，不能称为已完成真实实验或已有解题结果。离线测试结果不能代替该步骤。

此版本有持久 checkpoint，但对不确定的动作状态采用拒绝自动恢复；安全的自动 reattach 和 30 题全局调度不属于本次纯净单轨迹实现。
