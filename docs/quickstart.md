# Fusion Memory 快速开始

这是面向新手的默认安装方式。

## 1. 安装

Linux / macOS:

```bash
cd /public/home/wwb/memory
sh install.sh
```

Windows PowerShell:

```powershell
cd C:\path\to\memory
.\install.ps1
```

安装完成后会自动进入初始化向导，依次确认：

- 数据库：默认 SQLite，本地单文件，适合 90% 本地使用；高级用户可选 Postgres/pgvector。
- Embedding：默认内置轻量 embedding；高级用户可选本地 Qwen 或 API。
- Reranker：默认内置 lexical reranker；高级用户可选本地 Qwen 或 API。
- Extractor/router：默认内置规则；高级用户可选 OpenAI-compatible API。
- Query router：默认关闭；需要复杂查询路由时再开启 API。

API key 不会写入配置文件。向导只保存环境变量名，例如
`FUSION_MEMORY_MODEL_API_KEY`。启动服务前把真实 key 放到环境变量里即可。

无人值守安装可以跳过向导：

```bash
FUSION_MEMORY_SKIP_WIZARD=1 sh install.sh
```

## 2. 启动

```bash
fusion-memory start
```

## 3. 检查状态

```bash
fusion-memory status
```

## 4. 接入 psi-agent

启动 memory 服务后，在 psi-agent 中设置：

```bash
export PSI_MEMORY_BASE_URL=http://127.0.0.1:8765
```

然后给 `psi-agent session` 加上 `--memory-enabled`。

## 5. 常见问题

- 启动失败：先运行 `fusion-memory doctor`
- 端口被占用：修改本地配置文件里的端口
- API 模型不可用：确认向导里填写的 API key 环境变量已经设置
- 想备份：运行 `fusion-memory backup`
