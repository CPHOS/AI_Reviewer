# AI Reviewer

CPHOS 物理竞赛题目 AI 审核工具。自动解析 LaTeX 题目文件，对每个评分点进行数学正确性、物理合理性与难度评估，输出结构化审核报告。支持本地文件审核与远程题库服务器两种运行模式。

## 功能特性

- LaTeX 题目文件自动解析（四级小问 + 多解法 + 评分点提取）
- 逐评分点 AI 审核：正确性、合理性、计算/思维难度
- 三维难度评分 + 综合总结
- Markdown + JSON 双格式报告
- 多文件并发审核
- 远程题库服务器对接：手动搜索审题 + 自动轮询新题 + server 侧有界并发审核

## 快速开始

### 环境要求

- Python ≥ 3.12
- [uv](https://docs.astral.sh/uv/) 包管理器

### 安装

```bash
git clone <repo-url>
cd AI_Reviewer
cp .env.example .env
# 编辑 .env，填入 API Key 等配置
uv sync
```

## 使用方式

### Local 模式 — 本地文件审核

```bash
# 审核单个题目
ai-reviewer local problem.tex

# 审核多个题目
ai-reviewer local orbit.tex penning_trap.tex stick.tex

# 并发审核（最多 3 个同时运行）
ai-reviewer local orbit.tex penning_trap.tex stick.tex -j 3

# 指定输出目录
ai-reviewer local problem.tex -o reports/
```

每个审核任务自动分配唯一 `task_id`（格式 `{文件名}_{8位hex}`），输出文件以此命名：
- `output/orbit_a1b2c3d4.md` — Markdown 报告
- `output/orbit_a1b2c3d4.json` — JSON 结构化数据

### Server 模式 — 题库服务器

连接远程 CPHOS 题库服务器，进入交互式命令行：

```bash
ai-reviewer server
ai-reviewer server --auto-on # 连接成功后立即开启自动轮询模式
ai-reviewer server --auto-on --auto-updated-after 2026-04-11T08:00:00+08:00 # 指定 auto 模式检查起点，默认为服务启动时间
```

登录成功后可使用以下命令：

| 命令 | 说明 |
|------|------|
| `search <关键词>` | 搜索题目 |
| `review <序号\|UUID>` | 审核指定题目（序号为最近搜索结果编号） |
| `auto on` | 开启自动轮询模式 |
| `auto off` | 关闭自动轮询模式 |
| `status` | 查看服务状态 |
| `help` | 显示帮助 |
| `quit` / `exit` | 退出 |

**自动模式**：后台定时轮询题库，自动审核 `status=none` 且在服务启动后有更新的新题目。已审核过的老题目不会重复处理。
手动 `review` 与自动轮询共用同一并发上限，由 `QB_MAX_CONCURRENT_REVIEWS` 控制。
可选启动参数 `--auto-updated-after` 可手动指定 auto 的检查起点；未指定时默认使用服务启动时间。

审核完成后会自动：
1. 生成本地报告（.md + .json）
2. 回写题库：按难度标签存在性执行 upsert（存在则 `update_question_difficulty`，不存在则 `create_question_difficulty`），并调用 `update_question_status(..., "reviewed")`；`notes` 上传内容与本次生成的 Markdown 报告全文一致

## 配置

复制 `.env.example` 为 `.env` 并填入配置：

### LLM 配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_PROVIDER` | LLM 服务商 | `openrouter` |
| `OPENROUTER_API_KEY` | API 密钥（多个用逗号分隔，失败时轮询） | — |
| `OPENROUTER_BASE_URL` | API 地址 | `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | 模型名称 | `anthropic/claude-sonnet-4` |
| `LLM_TEMPERATURE` | 温度参数 | `0.3` |
| `LLM_MAX_TOKENS` | 最大 token 数 | `4096` |
| `LLM_MAX_RETRIES` | 单个 Key 最大重试次数 | `3` |
| `LLM_RETRY_INTERVAL` | 重试间隔（秒） | `2` |
| `OUTPUT_DIR` | 报告输出目录 | `output` |

### 题库服务器配置（server 模式）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `QB_URL` | 题库 API 地址 | — |
| `QB_USERNAME` | Bot 账号用户名 | — |
| `QB_PASSWORD` | Bot 账号密码 | — |
| `QB_POLL_INTERVAL` | 自动轮询间隔（秒） | `600` |
| `QB_MAX_CONCURRENT_REVIEWS` | server 模式最多同时维持的审核任务数 | `1` |

## 输出报告

### Markdown 报告

1. **概览表格** — 题目、总分、三维难度
2. **评分点总览表** — 编号、类型、分值、正确性、合理性、难度
3. **细致审核** — 按层级展开，每个子题附小结
4. **综合评估** — 总结文字
5. **元信息** — 使用模型、token 用量、生成时间、总耗时

### JSON 报告

```json
{
  "meta": {
    "model": "openrouter/anthropic/claude-sonnet-4",
    "prompt_tokens": 12345,
    "completion_tokens": 6789,
    "total_tokens": 19134,
    "timestamp": "2026-04-06T12:00:00+08:00",
    "elapsed_seconds": 45.2
  },
  "title": "...",
  "total_score": 40,
  "difficulty": { ... },
  "summary": "...",
  "node_reviews": [ ... ]
}
```

## Docker 部署

### 自动构建

推送到 `main` 分支或打 `v*` tag 时，GitHub Actions 会自动构建镜像并推送到 `ghcr.io/cphos/ai-reviewer`。

### 生产环境部署

```bash
# 1. 拉取镜像
docker pull ghcr.io/cphos/ai-reviewer:latest

# 2. 准备配置，编辑 .env，填入 API Key 等配置
mkdir ai-reviewer && cd ai-reviewer
curl -O https://raw.githubusercontent.com/cphos/AI_Reviewer/main/.env.example

# 3. 从仓库复制 docker-compose.yml
curl -O https://raw.githubusercontent.com/cphos/AI_Reviewer/main/docker-compose.yml

# 4. 启动
docker compose up -d

# 查看日志
docker compose logs -f
```

默认以 `server --auto-on` 模式运行。如需自定义启动命令：

```yaml
# docker-compose.yml 中覆盖 command
command: ["server", "--auto-on", "--auto-updated-after", "2026-04-11T08:00:00+08:00"]
```

### 本地构建

```bash
docker build -t ai-reviewer .
docker run --rm --env-file .env -v ./output:/app/output ai-reviewer server --auto-on
```

## 开发

```bash
uv sync
uv run pytest
```

详见 [DEVELOPMENT.md](DEVELOPMENT.md)。

## License

[AGPL v3.0](LICENSE)
