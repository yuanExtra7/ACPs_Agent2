# Agent_：基于 ACPs 的互联智能体初版工程

本项目是一个基于 `ACPs-community` 实现的智能体原型，当前以 **Direct RPC 模式** 为主，完成了可运行的 Partner 能力、Leader 代理能力和 Human 对话入口，并已进入 **ATR 注册审核等待阶段**。

项目定位不是完整生产平台，而是一个可验证、可扩展、可逐步演进的 ACPs 智能体工程底座。

---

## 1. 项目目标与定位

### 1.1 当前目标

- 搭建一个符合 ACPs/AIP 交互模型的最小可用智能体服务。
- 支持基于文本的任务协作闭环：`start -> get/continue -> complete`。
- 支持两类使用方式：
  - 作为 Partner，被外部 Leader 通过 `/rpc` 调用；
  - 作为简化 Leader，主动代理调用远端 Partner。
- 完成可信注册所需材料准备（ACS、CLI 配置、脚本），并推进到审核流程。

### 1.2 当前边界

- 目前聚焦 **文本输入/输出**，不支持图像、语音等多模态数据项。
- 目前主通路是 **Direct RPC**，尚未接入 Group（MQ）协作模式。
- 会话记忆为进程内内存，不做持久化，服务重启后清空。

---

## 2. 项目结构（Repository Overview）

```text
Agent_/
├─ README.md                      # 本文档（项目总说明）
├─ docs/
│  ├─ plan.md                     # 阶段规划
│  └─ 需求分析.md                  # 需求说明
└─ partner/
   ├─ README.md                   # 子项目使用说明（运行与接口）
   ├─ pyproject.toml              # Python 项目配置
   ├─ requirements.txt            # 运行依赖
   ├─ requirements-dev.txt        # 开发与测试依赖
   ├─ src/partner_agent/
   │  ├─ app.py                   # FastAPI 入口，挂载 RPC/Leader/Human 路由
   │  ├─ settings.py              # 环境变量配置
   │  ├─ handlers.py              # Partner AIP 命令处理（start/get/continue/...)
   │  ├─ leader.py                # Leader 侧 RPC 调用封装
   │  ├─ leader_api.py            # /leader 路由
   │  ├─ human_api.py             # /human 页面与 /human/chat 接口
   │  ├─ chat_service.py          # 聊天路由决策、后处理、相关性判断
   │  ├─ brain.py                 # DeepSeek 兼容模型调用
   │  ├─ memory.py                # 内存会话与运行态管理
   │  └─ __init__.py
   ├─ atr/
   │  ├─ acs.json                 # ACPs 能力描述（注册材料）
   │  ├─ acps-cli.toml            # acps-cli 配置
   │  └─ .acps-cli/               # token 等本地状态文件
   ├─ scripts/
   │  ├─ atr_register.ps1         # 登录/保存草稿/提交审核/检查状态
   │  ├─ atr_issue_cert.ps1       # 审核通过后 EAB 与证书申请
   │  ├─ verify_handlers.py       # 处理器行为校验脚本
   │  ├─ test_remote_rpc.py       # 远端 RPC 联调脚本
   │  └─ test_remote_rpc_acps.py  # ACPs 风格远端联调脚本
   └─ tests/                      # 单元测试与流程测试
```

---

## 3. 当前进展（Progress）

### 3.1 已完成

- [x] 基于 `acps_sdk` 搭建 Partner RPC 服务，支持核心 AIP 命令。
- [x] 完成文本能力边界控制：
  - 非文本请求拒绝或引导补充文本；
  - 支持 `awaiting-input` 与 `awaiting-completion` 状态。
- [x] 实现 Leader 代理能力（可主动调用远端 Partner）。
- [x] 实现 Human 入口（网页 + API），可由用户直接触发本地回复或远端协作。
- [x] 增加调用可信性防护机制：
  - 任务绑定一致性检查；
  - 调用证据（call proof）校验；
  - 离题响应检测与重试。
- [x] 完成 Leader/Human 调用链修复（参考 `demo-leader` 完成闸门思路）：
  - 在 `awaiting-completion` 状态下增加自动 `complete` 收尾；
  - 新话题默认触发新 task，降低旧 task 结果复用概率；
  - 提升异常可观测性（错误信息与阶段信息更完整）。
- [x] 超时策略已上调：
  - `LEADER_CALL_TIMEOUT_SECONDS` 默认值已调为 `30`；
  - `HUMAN_TOTAL_BUDGET_SECONDS` 默认值已调为 `30`。
- [x] 测试体系已具备基础覆盖，当前测试通过。
- [x] 已准备并提交 ATR 注册材料，进入“审批等待”阶段。

### 3.2 正在进行

- [ ] 审核状态跟踪与 AIC 下发等待。
- [ ] 证书申请前配置与部署连通性准备（endpoint、TLS 拓扑一致性）。
- [ ] Partner 侧深度稳定性治理（并发竞争、回放幂等、会话记忆隔离）与补测。

### 3.3 下一阶段

- [ ] 审批通过后获取 EAB 并申请证书（CAI）。
- [ ] 完成 mTLS 对接与外网 HTTPS 暴露方案固定。
- [ ] 从“可跑通”升级到“可持续运行”（日志、监控、持久化、限流）。

---

## 4. 基本能力说明（What It Can Do）

### 4.1 Partner 能力（被调用方）

- 提供 AIP JSON-RPC 端点（默认 `/rpc`）。
- 支持命令：
  - `start`：启动任务；
  - `get`：获取任务状态；
  - `continue`：补充上下文；
  - `complete`：确认完成；
  - `cancel`：取消任务。
- 任务状态可覆盖：
  - `accepted / working / awaiting-input / awaiting-completion / completed`
  - `failed / rejected / canceled`
- 目前仅文本型产物（`TextDataItem`）。

### 4.2 Leader 能力（调用方）

- 提供 `/leader/chat` 接口。
- 可对指定 Partner RPC 地址执行任务生命周期控制。
- 支持轮询状态、按条件 continue，并在适配场景下自动 complete 收尾。
- 返回可审计信息：trace、final_state、task/session 绑定检查、call proof。

### 4.3 Human 能力（用户入口）

- 提供 `/human` 网页对话界面。
- 提供 `/human/chat` API。
- 可在“本地对话回复”和“经 Leader 调远端 Partner”之间动态路由。
- 具备会话态维护、错误恢复提示、时延预算控制与后处理逻辑。
- 对 `awaiting-completion` 场景具备任务收尾和路由纠偏能力，降低“旧回答粘连”。

---

## 5. ACPs 相关基础知识（面向项目理解）

### 5.1 核心协议关系

- **AIP（Agent Interaction Protocol）**：定义任务如何在 Leader 与 Partner 间交互。
- **ATR（Agent Trusted Registration）**：定义可信注册流程（ACS 审核 + 证书）。
- **ADP（Agent Discovery Protocol）**：定义如何发现合适智能体。

本项目当前重点落在：**AIP 已落地，ATR 在审核中，ADP 尚未接入主流程**。

### 5.2 最小任务状态机（本项目可映射）

```text
start -> awaiting-input -> continue -> awaiting-completion -> complete -> completed
```

以及异常分支：

```text
start -> rejected / failed / canceled
```

### 5.3 ACS、AIC、证书与 endpoint 的关系

- `ACS`：能力描述与对外访问地址（endpoint）声明文件。
- `AIC`：审核通过后分配的智能体身份码。
- `CAI`：基于 AIC + EAB 申请得到的证书材料。
- `endpoint`：Leader 实际访问 Partner 的地址，必须与部署现实一致。

---

## 6. 运行与开发（Quick Start）

> 详细参数可参考 `partner/README.md`，这里给出最短路径。

### 6.1 安装依赖

在 `Agent_/partner` 下执行：

```bash
python -m pip install -r requirements.txt
```

### 6.2 启动服务

```bash
uvicorn partner_agent.app:app --host 0.0.0.0 --port 5000
```

### 6.3 主要入口

- 健康检查：`GET /health`
- Leader 健康：`GET /leader/health`
- Partner RPC：`POST /rpc`
- Leader 代理：`POST /leader/chat`
- Human 页面：`GET /human`
- Human API：`POST /human/chat`

### 6.4 测试

```bash
pytest -q
```

---

## 7. ATR 注册与证书流程（当前状态说明）

### 7.1 当前状态

- 已准备：
  - `partner/atr/acs.json`
  - `partner/atr/acps-cli.toml`
  - 注册与证书脚本
- 已执行注册提交流程，当前处于 **平台审核等待**。

### 7.2 审批前后动作

- 审批前：
  - 反复 `agent check` 查看状态；
  - 保持 ACS 字段与当前服务能力一致；
  - 准备好部署侧 TLS/端口拓扑。
- 审批后：
  - 获取 AIC；
  - 拉取 EAB；
  - 申请证书；
  - 完成 mTLS 或网关 TLS 配置后再对外发布。

---

## 8. 当前待解决问题（Open Issues）

### 8.1 部署与网络层

- `ACS` 中 endpoint 与真实部署端口/协议需要严格一致。
- 需明确最终 TLS 架构：
  - 应用直挂 443；
  - 或网关（Nginx/Caddy）终止 TLS 后转发到应用端口。
- 外网可达性、证书链完整性与域名/IP 兼容性仍需专项验证。

### 8.2 协议与安全

- 是否仅需 `serverAuth`，或需补充 `clientAuth`，取决于平台 mTLS 校验策略。
- 任务级鉴权、请求签名、调用限流、重放防护尚未完整工程化。

### 8.3 工程化能力

- 记忆持久化（数据库/缓存）尚未落地。
- 结构化日志、链路追踪、指标监控、告警仍需建设。
- 高并发与故障注入场景测试不足（尤其是 Partner 侧任务并发与幂等）。

### 8.4 互联能力扩展

- 目前尚未接入 ADP 动态发现（仍偏手工指定 RPC 地址）。
- Group/MQ 协作模式尚未纳入主链路。
- 多 Partner 并发编排与结果聚合能力仍在规划阶段。

### 8.5 当前重点技术债（Partner）

- `start` 任务创建时机、并发访问下的状态一致性仍需强化。
- 会话级记忆在多 task 并发时存在潜在串话题风险。
- `/rpc` 协议层的回放幂等与异常路径集成测试仍需补齐。

---

## 9. 建议演进路线（Roadmap）

```text
阶段1: 单体可运行（已完成）
  Direct RPC + 文本状态机 + 基础测试

阶段2: 可信接入（进行中）
  ATR审核通过 -> EAB -> 证书 -> TLS/端点对齐

阶段3: 稳定化
  持久化会话/任务 + 可观测性 + 错误恢复增强

阶段4: 互联升级
  Discovery接入 + 多Partner编排 + Group模式

阶段5: 生产化
  安全策略完善 + 自动化部署 + 压测与容量规划
```

---

## 10. 参考资料

- ACPs 项目总览：`ACPs-community/README.md`
- 智能体开发指南：`ACPs-community/acps-docs/tutorials/agent-development.md`
- 本项目子模块说明：`Agent_/partner/README.md`

---

## 11. 备注

本仓库处于“快速迭代 + 联调验证”阶段，文档将随注册进展、证书接入、部署架构调整持续更新。建议每次对外联调或发布前，先检查：

- `ACS` 声明是否与当前运行事实一致；
- API 行为是否与测试结果一致；
- 安全配置是否达到目标环境要求。