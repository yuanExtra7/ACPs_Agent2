# Partner Agent

最小可运行的 ACPs `Leader + Partner` 双能力实现（Direct RPC 模式）。

## 目录结构

- `src/partner_agent/`：应用源码（同时包含 partner 与 leader）
- `tests/`：测试代码（后续阶段补充）

## 本阶段能力（双能力）

- **Partner 能力**
  - 提供 `/rpc` AIP JSON-RPC 端点
  - 支持 `start/get/continue/complete/cancel`
  - 仅支持文本输入输出（`TextDataItem`）
- **Leader 能力**
  - 提供 `/leader/chat` HTTP 接口，由本服务主动调用目标 Partner 的 AIP RPC
  - 自动执行 `start -> (poll) -> continue(如需要) -> complete(可选)`
  - 返回最终状态、文本产物和完整 trace
- **用户直连页面**
  - 提供 `/human` 可视化聊天页面（简约商务风、微信式消息布局）
  - 提供 `/human/chat` 文本对话 API（供页面调用或第三方前端接入）
- **统一记忆机制（进程内）**
  - Human：基于 `session_id` 维持会话记忆
  - Partner：基于 AIP `sessionId/taskId` 维持会话记忆
  - Leader：基于 `conversation_id` 维持对目标 Partner 的会话记忆
  - 说明：当前为内存存储，服务重启后清空

## 启动方式

建议先安装依赖（在 `Agent_/partner` 目录）：

```bash
python -m pip install -r requirements.txt
```

在具备 `fastapi`、`uvicorn`、`acps_sdk` 的 Python 环境中运行：

```bash
uvicorn partner_agent.app:app --host 0.0.0.0 --port 5000
```

### 可选：接入 DeepSeek 作为“大脑”

启动前设置环境变量（PowerShell）：

```powershell
$env:DEEPSEEK_BASE_URL="https://你的DeepSeek兼容端点/v1"
$env:DEEPSEEK_API_KEY="你的apikey"
$env:DEEPSEEK_MODEL="deepseek-chat"
```

说明：
- 配置了以上变量时，Partner 会优先调用 DeepSeek 生成回复。
- 未配置或调用失败时，自动回退到本地规则回复，保证服务可用。

健康检查：

```bash
GET /health
GET /leader/health
```

用户页面访问：

```bash
GET /human
```

用户聊天 API 示例：

```bash
POST /human/chat
{
  "text": "请帮我总结这段需求文档的重点。",
  "session_id": "human-session-001",
  "rpc_url": "http://127.0.0.1:5000/rpc"
}

说明：
- 不传 `rpc_url`：走本地 Human 对话链路；
- 传 `rpc_url`：走 Leader 代理调用远端 Partner，返回真实远端结果。
```

Leader 调用示例（本机调用远端 Partner）：

```bash
POST /leader/chat
{
  "partner_rpc_url": "http://113.47.5.136:5000/rpc",
  "user_input": "请介绍一下你的能力边界",
  "continue_input": "请再补充一个示例场景",
  "leader_id": "edu.ustb.agent.leader.chat.v1",
  "conversation_id": "leader-session-001"
}
```

默认会自动 `complete`，如需只观察到 `awaiting-completion`，可传：

```json
{
  "auto_complete": false
}
```

## 运行本地校验脚本

```bash
python scripts/verify_handlers.py
pytest -q
```

## ATR 文件与脚本

- `atr/acs.json`：可提交的 ACS 初稿（已按当前需求填好）
- `atr/acps-cli.toml`：CLI 配置模板（请改成你的平台地址）
- `scripts/atr_register.ps1`：注册与提交审核脚本
- `scripts/atr_issue_cert.ps1`：审核通过后签发 `serverAuth` 证书脚本

PowerShell 示例：

```powershell
.\scripts\atr_register.ps1 -Username your_user -Password your_pass
.\scripts\atr_issue_cert.ps1 -Aic <AIC>
```
