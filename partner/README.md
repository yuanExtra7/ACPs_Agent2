# Partner Agent

最小可运行的 ACPs Partner 实现（Direct RPC 模式）。

## 目录结构

- `src/partner_agent/`：应用源码
- `tests/`：测试代码（后续阶段补充）

## 本阶段能力

- 提供 `/rpc` AIP JSON-RPC 端点
- 支持 `start/get/continue/complete/cancel`
- 仅支持文本输入输出（`TextDataItem`）

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
```

## 运行本地校验脚本

```bash
python scripts/verify_handlers.py
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
