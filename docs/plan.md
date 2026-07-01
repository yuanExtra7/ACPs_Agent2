# ACPs Partner Agent 开发计划与任务拆解

## 1. 计划目标

在尽量贴近 ACPs 官方开发指南的前提下，完成一个可注册、可调用、可验证的最小 `Partner` 智能体，实现路径严格遵循：

`最小 Direct RPC Partner -> 状态机完善 -> ACS 对齐 -> ATR 注册 -> 证书与 HTTPS/mTLS 联调`

---

## 2. 总体实施策略（对齐官方指南）

对应 `acps-docs/tutorials/agent-development.md` 的建议，采用“先最小、后增强”的路线：

1. 先实现最小 `Direct RPC`，不引入 Group/RabbitMQ；
2. 先保证 AIP 命令与状态机正确性，再做描述与注册；
3. 用 ACS 精准描述“文本聊天咨询”能力边界；
4. 通过 `acps-cli` 完成 ATR 可信注册与证书申请；
5. 先 `serverAuth`，后续再评估 `clientAuth`。

---

## 3. 里程碑与任务拆解

## M0 - 前置确认与冻结（0.5 天）

### 目标

冻结首期技术边界，避免开发中途改协议与能力范围。

### 任务

1. 冻结当前约束：
   - 仅 `Partner`；
   - 仅 `Direct RPC`；
   - 仅文本聊天；
   - `streaming=false`、`notification=false`、`messageQueue=[]`；
   - 证书首期 `serverAuth`。
2. 确认生产 endpoint（唯一待确认项）：
   - 建议：`https://113.47.5.136/rpc`。
3. 固定 provider 信息：
   - `organization=北京科技大学`。

### 产物

- 本文档（计划）冻结版本；
- `需求分析.md` 最终确认。

### 验收标准

- 所有核心参数无歧义，进入编码阶段不再变更架构方向。

---

## M1 - 最小 Partner 可运行（1 天）

### 目标

实现可运行的 FastAPI Partner，完成 AIP RPC 基础命令闭环。

### 任务

1. 创建 Partner 服务骨架（FastAPI + `acps-sdk`）。
2. 挂载 `/rpc` 路由（`add_aip_rpc_router`）。
3. 实现命令处理器：
   - `on_start`
   - `on_get`
   - `on_continue`
   - `on_complete`
   - `on_cancel`
4. 实现基础文本提取与数据项封装（统一 `TextDataItem`）。
5. 增加健康检查端点（如 `/health`）。
6. 先使用内存任务管理（`TaskManager`）。

### 产物

- 可启动的 Partner 服务；
- 本地调用脚本（或 curl/json-rpc 请求样例）。

### 验收标准

- 服务可启动；
- `/rpc` 可接收并返回合法 `TaskResult`；
- `senderRole/senderId/taskId/sessionId` 字段结构正确。

---

## M2 - 状态机与边界行为完善（1 天）

### 目标

保证状态流转严格符合 AIP 规则，尤其是幂等和终态保护。

### 任务

1. 明确并实现状态转移规则：
   - `start -> accepted/working/awaiting-input/awaiting-completion/rejected`
   - `awaiting-input + continue -> working`
   - `awaiting-completion + complete -> completed`
2. 实现非法命令处理策略：
   - 不合法状态下命令被忽略或返回稳定状态；
   - 终态不可改写。
3. 实现拒绝策略文案：
   - 非文本请求；
   - 越能力边界请求；
   - 缺关键信息请求（可返回 `awaiting-input`）。
4. 补充 `products` 与 `status.dataItems` 的一致性逻辑。

### 产物

- 完整状态机实现；
- 拒绝/等待输入策略说明。

### 验收标准

- 关键命令序列可重复执行且行为稳定；
- 终态任务不会被异常重写；
- 返回结构与 AIP 模型一致。

---

## M3 - 测试与联调验证（1 天）

### 目标

建立“可证明符合指南”的最小测试集合。

### 任务

1. 覆盖最小测试矩阵（对齐指南建议）：
   - `start` 接受/拒绝/缺参；
   - `get` 幂等；
   - `continue` 在合法状态生效；
   - `complete` 仅在 `awaiting-completion` 生效；
   - 终态不可修改。
2. 增加一条端到端脚本：
   - `start -> (awaiting-input?) -> continue -> awaiting-completion -> complete`。
3. 输出联调记录（输入、状态变化、输出样例）。

### 产物

- 基础单元/集成测试；
- 一条可复现联调脚本。

### 验收标准

- 测试全部通过；
- 能稳定复现完整任务生命周期。

---

## M4 - ACS 编写与校验（0.5~1 天）

### 目标

产出与真实实现一致的 ACS，避免“描述与实现不一致”导致注册问题。

### 任务

1. 编写 `acs.json`（实体 Agent）：
   - `protocolVersion=02.01`
   - `provider.organization=北京科技大学`
   - `securitySchemes` 声明 `mutualTLS`
   - `endPoints` 指向最终 `/rpc`
   - `capabilities` 关闭 streaming/notification/messageQueue
   - `defaultInputModes/defaultOutputModes` 仅文本相关
   - `skills`：实现为单技能，描述可拆分多个文本能力标签与示例
2. 添加证书配置：
   - `certificate.altNames.ip=["113.47.5.136"]`
   - （可选）`requestedValidity` 按平台策略填写。
3. 本地做 ACS 结构校验并修正。

### 产物

- `acs.json`（可提交版本）。

### 验收标准

- ACS 可通过本地校验；
- 字段含义与实际服务能力逐项一致。

---

## M5 - ATR 注册与证书申请（0.5~1 天）

### 目标

完成可信注册闭环，获取可用于上线的 `serverAuth` 证书。

### 任务

1. 准备 `acps-cli.toml` 配置。
2. 执行命令链（用户侧）：
   - `auth login`
   - `agent save --acs-file ...`
   - `agent submit --agent-id ...`
   - `agent check/sync --acs-file ...`
   - `cert eab fetch --aic ...`
   - `cert issue --aic ... --eab-file ... --usage serverAuth`
3. 保存证书产物并登记路径。

### 产物

- 审核通过后的 AIC；
- `serverAuth` 证书、私钥、trust bundle。

### 验收标准

- 证书签发成功；
- 本地可读取证书并用于服务配置。

---

## M6 - HTTPS/mTLS 上线联调（1 天）

### 目标

将 Partner 切换到生产可接入形态，验证可被外部按 HTTPS/mTLS 调用。

### 任务

1. 服务器部署 Partner 服务。
2. 配置 TLS（绑定 `serverAuth` 证书）。
3. 完成 RPC 端到端验证：
   - 访问 `https://113.47.5.136/rpc`；
   - 验证状态机完整交互。
4. （若平台要求）补充 mTLS 客户端证书验证策略。

### 产物

- 可公网访问的 HTTPS Partner 服务；
- 联调通过记录。

### 验收标准

- 外部调用成功；
- 关键命令与状态返回正常；
- 无证书主机名校验异常。

---

## 4. 任务清单（可执行视图）

1. 冻结 endpoint 与 ACS 参数；
2. 实现 Partner `/rpc`；
3. 完善状态机与拒绝策略；
4. 编写并通过测试；
5. 生成并校验 `acs.json`；
6. 执行 ATR 命令链并拿证；
7. 部署 HTTPS 并做线上联调。

---

## 5. 风险与应对

1. **风险：endpoint 最终地址临时变化**
   - 应对：在 M0 冻结 URL，避免注册后返工。
2. **风险：serverAuth 证书无 IP SAN 导致 TLS 校验失败**
   - 应对：在 ACS 中强制写 `certificate.altNames.ip`。
3. **风险：实现与 ACS 描述不一致导致审核/联调失败**
   - 应对：M4 阶段做逐项对照检查清单。
4. **风险：把复杂 demo 结构过早引入导致进度失控**
   - 应对：严格遵循最小 Direct RPC 路线，首期不引入 Group/LLM 编排框架。

---

## 6. 完成定义（Definition of Done）

满足以下条件视为首期完成：

1. Partner 服务支持 AIP 五命令并通过状态机测试；
2. ACS 与实现一致，且包含 IP SAN 配置；
3. ATR 流程完成并成功签发 `serverAuth` 证书；
4. HTTPS endpoint 可被外部成功调用并返回合规 `TaskResult`；
5. 文档齐备：需求分析、开发计划、联调记录。
