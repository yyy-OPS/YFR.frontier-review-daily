# 研域前沿综述公网部署文档

本文档面向 `https://your-domain.example` 公网部署。示例命令默认在 `frontier-review-standalone` 目录执行。

## 1. 部署架构

```text
Internet
  |
  v
https://your-domain.example
  |
  v
Nginx / Caddy / 云厂商反向代理
  |
  v
127.0.0.1:8080  ->  web 容器 Nginx 静态前端
                      /api/* 反代到 agent:8000
```

容器：

- `web`：前端静态站点，容器内 Nginx 同时代理 `/api`。
- `agent`：FastAPI 后端，负责主题配置、管理员认证、文献检索、综述生成、翻译、生图结果持久化。

持久化目录：

- `./data/frontier_review:/data/frontier_review`
- `./data/corpora:/data/corpora`

## 2. 服务器准备

最低建议：

- Linux x86_64
- 2 核 CPU / 4 GB 内存起步，500 篇文献分析建议 4 核 / 8 GB 以上
- Docker 24+
- Docker Compose v2
- 可访问外网 API

安装 Docker 后检查：

```bash
docker --version
docker compose version
```

## 3. 初始化配置

```bash
cd frontier-review-standalone
cp .env.example .env
mkdir -p data/frontier_review data/corpora
```

编辑 `.env`：

```bash
nano .env
```

生产环境至少修改：

```env
CORS_ORIGINS=https://your-domain.example
FRONTIER_REVIEW_REQUIRE_SECURE_CONFIG=true
FRONTIER_REVIEW_ENABLE_DOCS=false
FRONTIER_REVIEW_ENABLE_SCHEDULER=true
DAILY_REVIEW_ADMIN_SECRET=replace-with-a-long-random-secret
DAILY_REVIEW_ADMIN_USERNAME=admin
DAILY_REVIEW_ADMIN_PASSWORD=replace-with-a-strong-password
ADMIN_LOGIN_RATE_LIMIT_PER_MINUTE=10
TRANSLATION_RATE_LIMIT_PER_MINUTE=30
MAX_CACHED_IMAGE_BYTES=10485760

OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_API_KEY=replace-with-your-key
OPENAI_MODEL=your-model-name

DAILY_REVIEW_LITERATURE_PROVIDER=hybrid
PAPER_SEARCH_SOURCES=semantic,openalex,crossref,europepmc,hal,base,core,unpaywall
PAPER_SEARCH_VALIDATE_LINKS=true

SCIVERSE_API_TOKEN=replace-if-you-use-sciverse
PAPER_SEARCH_MCP_CORE_API_KEY=replace-if-you-use-core
PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=your-email@example.com
PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY=replace-if-you-have-one
SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS=1.15
```

不要把真实 `.env` 上传到公开仓库。

## 4. 一键启动

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
docker compose logs -f agent
```

本机验证：

```bash
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8080/
```

## 5. Zeabur 单服务部署

Zeabur 的 Git 自动识别如果显示 `Provider: static`，通常表示它选中了前端目录或只识别到了静态站点。研域前沿综述不是纯静态项目，必须同时运行后端 API。

推荐方式：

1. 新建 Zeabur Service。
2. 选择 Git 仓库。
3. Root Directory 选择 `frontier-review-standalone`。
4. Provider 选择 `Dockerfile` / `Docker`，不要选择 `Static`。
5. Dockerfile 使用根目录下的 `Dockerfile`。
6. 配置环境变量，参考 `.env.example`。
7. 绑定域名 `your-domain.example`。

根级 `Dockerfile` 会构建 React 前端，并在同一个容器内启动：

- Nginx：监听 Zeabur 注入的 `$PORT`。
- FastAPI：监听容器内部 `127.0.0.1:8000`。
- `/api/*`：由 Nginx 反向代理到 FastAPI。

因此 Zeabur 上不需要单独暴露后端端口，也不需要让浏览器访问 `localhost`。

## 6. 反向代理

### Nginx 示例

```nginx
server {
    listen 80;
    server_name your-domain.example;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.example;

    ssl_certificate /etc/letsencrypt/live/your-domain.example/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.example/privkey.pem;

    client_max_body_size 50m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
    }
}
```

### Caddy 示例

```caddyfile
your-domain.example {
    reverse_proxy 127.0.0.1:8080
}
```

## 7. 环境变量说明

### 站点与安全

| 变量 | 是否必填 | 说明 |
| --- | --- | --- |
| `CORS_ORIGINS` | 是 | 允许访问后端的前端域名，公网建议 `https://your-domain.example`。 |
| `FRONTIER_REVIEW_REQUIRE_SECURE_CONFIG` | 是 | 公网建议 `true`，缺少强管理员密码、强签名密钥或 CORS 使用 `*` 时拒绝启动。 |
| `FRONTIER_REVIEW_ALLOW_INSECURE_DEFAULTS` | 否 | 仅本地调试可设为 `true`，公网不要开启。 |
| `FRONTIER_REVIEW_ENABLE_DOCS` | 否 | 是否开放 FastAPI `/docs`、`/redoc`、`/openapi.json`，公网建议 `false`。 |
| `FRONTIER_REVIEW_ENABLE_SCHEDULER` | 否 | 是否启用北京时间每日调度，公网默认 `true`；本地测试可设为 `false` 避免消耗额度。 |
| `DAILY_REVIEW_ADMIN_SECRET` | 是 | 管理员 JWT/会话签名密钥，必须使用随机长字符串。 |
| `DAILY_REVIEW_ADMIN_USERNAME` | 是 | 初始管理员账号。 |
| `DAILY_REVIEW_ADMIN_PASSWORD` | 是 | 初始管理员密码。 |
| `ADMIN_LOGIN_RATE_LIMIT_PER_MINUTE` | 否 | 管理员登录每 IP 每分钟尝试次数，默认 `10`。 |
| `TRANSLATION_RATE_LIMIT_PER_MINUTE` | 否 | 公开翻译接口每 IP 每分钟请求次数，默认 `30`。 |
| `MAX_CACHED_IMAGE_BYTES` | 否 | 后端缓存远程图片最大字节数，默认 `10485760`。 |
| `FRONTIER_REVIEW_DATA_DIR` | 是 | 后端数据目录，容器内默认 `/data/frontier_review`。 |
| `BIBLIOCN_CORPORA_DIR` | 否 | 全文/语料目录，容器内默认 `/data/corpora`。 |

`FRONTIER_REVIEW_DATA_DIR` 中会保存 `daily_review_config.json`、`paper_index.jsonl`、`topic_daily_state.json`、`daily_delta_runs.jsonl`、分文件日报和图片缓存。`paper_index.jsonl` 是 Daily Delta 的核心索引，用于判断同一主题每天哪些文献是新增、复用或最近已使用。

新版日报存储结构：

```text
/data/frontier_review/runs/{topicId}/{runId}.json
/data/frontier_review/daily_review_runs_index.jsonl
/data/frontier_review/daily_review_runs.jsonl
```

其中 `runs/{topicId}/{runId}.json` 保存每期完整日报，`daily_review_runs_index.jsonl` 保存轻量历史索引。`daily_review_runs.jsonl` 是旧版兼容文件，已有数据仍可读取，新生成日报也会继续写入以便回滚。长期多主题运行时，管理员人工检查应优先查看 `runs/` 目录。

### LLM

| 变量 | 是否必填 | 说明 |
| --- | --- | --- |
| `OPENAI_BASE_URL` | 是 | OpenAI 兼容接口地址，需包含 `/v1`。 |
| `OPENAI_API_KEY` | 是 | LLM API Key。 |
| `OPENAI_MODEL` | 是 | 综述撰写与检索词扩展模型。 |

翻译模型、生图模型等可在管理员后台进一步配置。未单独配置翻译模型时，系统会提示用户使用浏览器翻译或复用已配置 LLM 能力。

### 文献检索

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DAILY_REVIEW_LITERATURE_PROVIDER` | `hybrid` | `sciverse`、`paper_search`、`hybrid` 三选一。 |
| `PAPER_SEARCH_SOURCES` | `semantic,openalex,crossref,europepmc,hal,base,core,unpaywall` | 启用的 Paper Search HTTP 适配源。 |
| `PAPER_SEARCH_VALIDATE_LINKS` | `true` | 是否真实校验 DOI/URL 可达性。 |
| `SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS` | `1.15` | Semantic Scholar 进程级限速间隔。官方审批邮件要求当前 key 全端点累计 1 request/second，默认值会低于该阈值。 |
| `SCIVERSE_API_TOKEN` | 空 | Sciverse API Token。 |
| `SCIVERSE_BASE_URL` | `https://api.sciverse.space` | Sciverse API 地址。 |

### Paper Search MCP 兼容变量

| 变量 | 当前状态 | 说明 |
| --- | --- | --- |
| `PAPER_SEARCH_MCP_CORE_API_KEY` / `CORE_API_KEY` | 已使用 | CORE 检索 API Key。 |
| `PAPER_SEARCH_MCP_SEMANTIC_SCHOLAR_API_KEY` / `SEMANTIC_SCHOLAR_API_KEY` | 已使用 | Semantic Scholar API Key，请求通过 `x-api-key` 头发送，并受 `SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS` 限速。 |
| `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL` / `UNPAYWALL_EMAIL` | 已使用 | Unpaywall DOI 查询邮箱。 |
| `OPENALEX_MAILTO` | 已使用 | OpenAlex polite pool 邮箱；未配置时会复用 Unpaywall 邮箱。 |
| `PAPER_SEARCH_MCP_ZENODO_ACCESS_TOKEN` | 已读取，待适配 | 预留给 Zenodo 检索适配器。 |
| `PAPER_SEARCH_MCP_GOOGLE_SCHOLAR_PROXY_URL` | 已读取，待适配 | 预留给 Google Scholar 代理适配器。 |
| `PAPER_SEARCH_MCP_ACM_API_KEY` | 已读取，待适配 | 预留给 ACM 适配器。 |
| `PAPER_SEARCH_MCP_IEEE_API_KEY` | 已读取，待适配 | 预留给 IEEE 适配器。 |

说明：公网部署版不直接启动本地 MCP Server，而是使用后端内置 HTTP 适配器读取这些变量调用公开 API。因此只要 `.env` 配置完整，容器部署即可使用已实现的 Paper Search 能力。

### 微信公众号模块

| 变量 | 是否必填 | 说明 |
| --- | --- | --- |
| `WECHAT_APP_ID` | 否 | 公众号 AppID，也可在 `/admin/wechat` 后台配置。 |
| `WECHAT_APP_SECRET` | 否 | 公众号 AppSecret，也可在 `/admin/wechat` 后台配置。 |

公众号模块入口为 `/admin/wechat`，隶属于管理员权限。未登录时不会展示公众号配置、往期日报列表或创建草稿按钮。

当前模块支持：

- 测试 stable token 与草稿接口。
- 从当天或往期 研域前沿综述日报生成公众号图文稿。
- 复制 Markdown / HTML。
- 上传封面永久素材并创建公众号草稿。

当前模块不自动发布文章。创建草稿成功后，系统提示管理员前往公众号后台发布。服务器公网出口 IP 必须加入微信公众号后台 IP 白名单。

## 8. 管理员配置流程

1. 部署后访问公网域名。
2. 进入管理员登录页。
3. 使用 `.env` 中的初始账号密码登录。
4. 修改管理员密码。
5. 配置主题：
   - 主题名称，例如“土木工程智能结构设计”。
   - 公开路径，例如 `/daily-review/civil-smart-structures`。
   - 每日生成时间，系统统一按北京时间执行。
   - 文献数量，例如 80、200、500。
   - 年份范围，例如近十年。
   - 是否显示/隐藏该主题。
6. 配置 LLM、翻译模型、生图模型。
7. 点击测试连接，确认 LLM 与 Sciverse/Paper Search 可用。
8. 手动生成一次主题日报，检查文献证据、综述、图片和往期记录。

## 9. 冒烟测试清单

部署后建议依次检查：

```bash
docker compose ps
curl http://127.0.0.1:8000/healthz
curl -I http://127.0.0.1:8080/
```

浏览器检查：

- 访问 `https://your-domain.example/` 能进入公开主题页。
- 公开页不出现后端地址、管理员路径提示或 `Failed to fetch`。
- 管理员可登录。
- LLM 测试连接返回成功。
- Sciverse/Paper Search 测试连接返回成功。
- 手动生成主题时，管理员后台出现真实阶段进度。
- 生成完成后公开页能查看当期综述和往期综述。
- 文献证据中的 DOI/URL 可点击。
- Markdown 和 LaTeX 公式正常渲染。
- “一图看懂”能显示、点击放大、导出带水印图片。
- `/admin/wechat` 未登录时只显示登录提示。
- 登录后可选择当天或往期日报生成公众号稿。
- 公众号稿包含首屏标题、彩色标签、精选证据卡、检索来源和阅读原文入口。
- 创建草稿前确认公众号 AppID/AppSecret 已配置，且服务器出口 IP 已加入白名单。

## 10. 备份与迁移

备份：

```bash
tar -czf yfr-data-backup.tgz .env data
```

迁移：

```bash
tar -xzf yfr-data-backup.tgz
docker compose up -d --build
```

## 11. 更新与回滚

更新：

```bash
docker compose pull
docker compose up -d --build
```

回滚时恢复上一份代码目录和 `data` 备份，再执行：

```bash
docker compose up -d --build
```

## 12. 常见问题

### 前端显示 Failed to fetch

优先检查：

- `CORS_ORIGINS` 是否包含公网域名。
- 反向代理是否转发到 `127.0.0.1:8080`。
- 前端是否使用 `/api`，不要让浏览器请求服务器上的 `localhost:8000`。
- `agent` 容器是否健康：`docker compose ps`。

### 检索结果质量不够

建议：

- 保持 `DAILY_REVIEW_LITERATURE_PROVIDER=hybrid`。
- 填写 `PAPER_SEARCH_MCP_CORE_API_KEY`。
- 填写 `PAPER_SEARCH_MCP_UNPAYWALL_EMAIL`。
- 主题配置中使用明确研究对象、技术路线和应用场景。
- 文献数量很大时开启 DOI/URL 校验，但要接受更长运行时间。

### 500 篇文献运行慢

这是预期行为。500 篇会触发更多检索、去重、链接校验、LLM 分批证据整理和综述生成。建议生产服务器至少 4 核 / 8 GB，并把反向代理超时设置为 900 秒以上。

## 开放获取 PDF 查询

公开文献证据页提供 `POST /api/daily-review/pdf`，前端按钮为“获取 PDF 原文（测试）”。该接口只查找合法开放获取 PDF，不绕过出版社付费墙，不把 PDF 保存到服务器。

可用来源包括 Semantic Scholar `openAccessPdf`、Unpaywall、CORE、Europe PMC、HAL、BASE、Sciverse `access_oa_url` 以及文献记录中已有的开放仓储 URL。部署时建议配置：

```env
PAPER_SEARCH_MCP_UNPAYWALL_EMAIL=your-email@example.com
UNPAYWALL_EMAIL=your-email@example.com
PAPER_SEARCH_MCP_CORE_API_KEY=replace-if-you-use-core
CORE_API_KEY=replace-if-you-use-core
```

如果某篇文献不是开放获取，接口会返回“未找到合法开放 PDF”，用户仍可通过 DOI 或出版社页面查看访问选项。

