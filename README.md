# big-supervise-big

同一模型家族的 **Decisions 接口监督生成接口**，用于 SWE-bench Pro V2 的步级代码修复。

默认主模型 `openai/gpt-6-luna`，监督模型 `openai/gpt-6-luna-decisions`。每一步从相同历史连续生成 3 个候选，监督器一次 Choice 三选一，**只执行选中动作，只继承选中历史**。附带 `single` 模式作为无监督对照。

这是[原“小监督大”实现](https://github.com/Kunyanli230/small-supervise-big-and-big-supervise-small/blob/7e8a2e3884e6c5d1417efd49adc8638c5f00dcd3/src/d3study/study.py)的托管 API 迁移。保留原选择语义和监督准则，使用固定 `mini-swe-agent==2.4.6` 的 run loop、工具解析、observation 格式和提交协议。没有 D3 模型服务、Ollama、GPU 部署、训练或历史实验结果。来源与差异见 [实现对照](docs/IMPLEMENTATION.md)，三组完整实验设计见 [实验计划](实验计划.md)。

## 当前验证范围

本仓库提供实际 API 客户端、步级 agent、V2 Docker 任务适配、独立镜像补丁重评与离线测试。离线测试不调用付费模型。**当前没有真实 Luna/Decisions 运行结果，也没有据此报告任何 benchmark 成功率。** Docker 与模型接口的首次实际联调仍需在运行环境完成。

此实现按 V2 的核心隔离要求执行：宿主机调模型，agent 容器断网；隐藏 verifier 仅复制到全新评测容器，使用官方原样脚本。它是直接 Docker 适配，不是 Harbor/Modal 官方榜单提交器；不声称未经联调便与官方榜单运行完全等价。

## 安装与离线检查

需要 Python 3.11+；真实任务需要 Docker Linux containers、Git 和 `OPENROUTER_API_KEY`。宿主不需要 GPU。

```bash
git clone https://github.com/Kunyanli230/big-supervise-big.git
cd big-supervise-big
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run bsb validate-config --config configs/luna.step_choice.json
```

也可 `python -m pip install -e '.[dev]'` 后直接使用 `bsb`。源码固定依赖由 `uv.lock` 记录；构建时不下载模型权重。

## 接口预检

在宿主环境设置 `OPENROUTER_API_KEY`，例如通过现有 secret manager；不把密钥写入配置或 Git。PowerShell 可用 `$env:OPENROUTER_API_KEY`，Bash 可用 `export OPENROUTER_API_KEY`。

```bash
uv run bsb doctor --config configs/luna.step_choice.json
```

`doctor` 检查 Models API、价格/能力和 Docker，不推理、不启动任务。正式调用使用以下两个独立 schema：

- 生成：`POST https://openrouter.ai/api/v1/chat/completions`，原生 `bash` tool call。
- 监督：`POST https://openrouter.ai/api/alpha/decisions`，`state` 与 `questions.next_action={type:choice, criteria:...}`。

依据：[OpenRouter Decisions](https://openrouter.ai/docs/guides/community/multimodal-decisions)、[Luna](https://openrouter.ai/openai/gpt-6-luna)、[Luna Decisions](https://openrouter.ai/openai/gpt-6-luna-decisions)。不混用 OpenAI 直连 Decisions 的另一套 schema。

配置严格校验同家族 ID：`supervisor == generator + '-decisions'`；不自动换模型。生成不发送旧配置的 `temperature/top_p`。默认仅路由 OpenAI，禁止 fallback；所有实际模型/provider 回执均保存。

## 准备固定的 SWE-bench Pro V2

```bash
uv run bsb fetch-v2 --dest datasets/SWE-bench_Pro-os
```

固定官方提交 `66f92766bba642462d4bbe5479e83f91f9211862`。运行时要求 `v2/` 未修改。Agent 只读取 `instruction.md` 和 `task.toml`，完整问题、requirements 与 interfaces 均进入上下文；不读取 `solution/` 或 `tests/`。

先预拉取某一题镜像（将 `INSTANCE_ID` 替换为实验计划中的实际 ID）：

```bash
docker pull ghcr.io/scaleapi/swe-bench_pro-v2:INSTANCE_ID
```

每次生成记录不可变 image ID，独立评测严格复用该 image ID。agent 新容器使用 `--network none`，不挂宿主目录、不传宿主密钥。原始任务仓库和隐藏 verifier 不会挂入 agent 容器。

## 运行监督组 B 与单次对照

以下命令会实际调用模型并消耗 API 额度。每次使用新的输出目录。

```bash
uv run bsb run --config configs/luna.step_choice.json \
  --checkout datasets/SWE-bench_Pro-os --instance-id INSTANCE_ID \
  --arm B --output runs/INSTANCE_ID/B

uv run bsb run --config configs/luna.single.json \
  --checkout datasets/SWE-bench_Pro-os --instance-id INSTANCE_ID \
  --arm A_r1 --output runs/INSTANCE_ID/A_r1

uv run bsb alias-c --source runs/INSTANCE_ID/A_r1 --output runs/INSTANCE_ID/C
```

PowerShell 将命令写在一行，或用反引号续行。`single` 的一条轨迹仍可多步读写与测试；它不是只有一次 API 调用。

生成 A_r2/A_r3 时复制 single 配置，分别预先设置 seed=`2026110502` / `2026110503`，使用对应 `--arm` 和独立目录。禁止复制其他轨迹的历史、补丁或容器。C 的正式口径直接引用预先指定的 A_r1，无需再生成或评测一次。

本仓库聚焦实验计划第 2 部分的纯净实现，提供单轨迹原语与汇总，不自动启动 30 题队列；整轮全局预算和调度由调用方管理。单轨迹预算、步数、上下文与限时在配置内执行。

## 独立评测

所有候选补丁先封存，选择确定后再评分。

```bash
uv run bsb grade --checkout datasets/SWE-bench_Pro-os --output runs/INSTANCE_ID/B
uv run bsb grade --checkout datasets/SWE-bench_Pro-os --output runs/INSTANCE_ID/A_r1
uv run bsb summarize --runs runs
```

评测新建容器，应用封存补丁，再复制固定官方 `tests/` 到 `/tests`，原样执行 `bash /tests/test.sh`；允许 verifier 准备依赖的网络访问。`grading/grade.json` 是独立评分记录，代码从不把评分回传给生成器或监督器。评测入口不读取模型密钥，也不调用模型。

参考 V2 官方 [README](https://github.com/scaleapi/SWE-bench_Pro-os/blob/66f92766bba642462d4bbe5479e83f91f9211862/v2/README.md)。评测脚本缺输出、超时或容器异常时 `resolved=null`；无效提交补丁单列 `invalid_patch`。不会把基础设施异常伪造为已执行测试后的模型失败。

每个运行的 `grading/` 目录不可覆盖。需要基础设施重评时，保留旧目录并在审计记录中说明原因后将其改为另一个归档名，再对同一封存补丁执行 `grade`；不要重新生成补丁。若重评结论冲突，人工审计后再进入研究主表。

## 预算、失败与恢复

- 每条轨迹默认最多 250 步、50 分钟；B 每步 3 次生成和 1 次 Choice，single 每步一次生成。工具在容器内用 `timeout` 限时；API 等待时长受配置的 socket timeout 约束，deadline 在各调用前后检查，不能保证中断操作系统或网络中不可取消的阻塞。
- 依据运行前 Models API 的最高输入/cache-write/输出价预留每次请求费用。输入使用“序列化 UTF-8 字节数＋8192 framing”保守估计，**不是精确 Luna token 计数**；这可能比真实 token 窗口更早停止。实际 token 和账单从 API 回执保留。
- 已返回但没有可靠费用的成功响应保存为 unknown 并停止；429/5xx/网络超时最多重试两次，未知账单按预留额占住预算。已知费用与不确定费用分开，不把缺失费用当 0。
- 无效候选不免费重采样。三个都无效时走 Mini 的格式反馈步骤；连续 3 步格式错误按原 Mini 规则结束。监督器选中无效候选时，回退到有效候选中概率最高的一项并记录原因。
- 无概率、未知选项或非法概率分布均终止为 `selector_error`，不静默执行第一个候选。
- 正常提交保存原生提交补丁；预算/时间/格式/接口等退出尽力保存当前工作树 diff（含未跟踪文件），之后独立评测。
- 每次调用、每个候选、选择及动作开始/结束写入 fsync 日志；动作前保存轨迹 checkpoint。输出目录存在时拒绝重新 `run`，不自动重新执行状态未知的动作。本版本不提供跨进程 reattach/自动恢复；封存的补丁可独立评分，未封存运行保留审计后明确另建版本。

运行目录内包含 `manifest.json`、`catalog.json`、`events.jsonl`、`trajectory.json`、`model.patch`、`patch-seal.json`、`result.json`，以及独立的 `grading/`。`result.json` 的停止原因与 `grading/grade.json` 的成功标签是两件事。

`summarize` 计算 A 三条任一成功的 Oracle Pass@3、B 监督 Pass@1 和 C=A_r1。未知分数保持未知，重复 cell 报错；C 引用不再次累计实际费用。A 的事后上界不能称为线上选择器效果。
