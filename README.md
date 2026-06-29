# 研域前沿综述

研域前沿综述是一个可自部署的多主题前沿文献监测与综述生成系统，面向研究人员、工程技术团队和科研内容运营者。系统支持管理员配置多个研究主题，定时或手动触发多源文献检索、证据筛选、去重融合、质量评分、Daily Delta 判断、LLM 综述撰写、一图看懂、开放 PDF 线索检测、翻译和微信公众号草稿生成。

本项目基于 [niuniu-869/Biblio_Agent](https://github.com/niuniu-869/Biblio_Agent) 的思想和部分工程基础继续扩展，并参考 [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp) 的多源论文检索思路，重点面向“多主题长期滚动前沿监测”和“可溯源综述生产”场景进行工程化改造。感谢相关开源项目作者和社区贡献者。

[English README](./README.en.md)

## 项目定位

本项目不是一次性问答工具，也不是严格 PRISMA 系统综述工具。它更接近一个可部署的“前沿文献雷达”：

- 每个主题可以长期滚动更新。
- 每期综述保留完整文献证据和来源字段。
- 综述写作基于检索到的候选证据，而不是让 LLM 凭空发挥。
- Daily Delta 会根据历史索引判断本期是新增日报、差异简报、专题深挖还是监测短报。
- 管理员可控制主题、模型、检索源、调度、专属访问和公众号草稿。

## 主要功能

- 多主题管理：每个主题拥有独立路径、独立历史、独立 Daily Delta 判断参数。
- 公开综述与专属综述：主题可设置为公开或管理员专属；专属综述使用独立访问密钥访问，游客不可见。
- Hybrid 文献检索：支持 Sciverse 与 Paper Search HTTP 适配源合并检索，参考 paper-search-mcp 的多源检索组织方式。
- 多源字段标准化：对 Semantic Scholar、OpenAlex、Crossref、Europe PMC、HAL、BASE、CORE、Unpaywall、Sciverse 等来源做字段对齐。
- 文献去重融合：按 DOI、标题、uniqueId、docId 等字段去重，并融合来源、摘要、PDF 线索和引用指标。
- 只检索文献入口：公开 `/literature-search` 页面支持用户输入主题和文献数量，只返回检索与评分后的文献证据，不生成完整综述。
- 管理员 CDK：管理员可为公开文献检索生成 CDK，独立控制启停、最大使用次数、过期时间、单次最大文献数和可用检索源。
- 证据质量评分：每篇证据展示相关性、文献质量、证据完整度和新颖度。
- Daily Delta 策略：根据本主题历史文献索引自动判断本期写作模式。
- 动态小标题：结合主题、子议题池、近期历史和候选文献生成本期小标题。
- LLM 综述撰写：通过 OpenAI 兼容接口生成结构化中文综述。
- 文献证据页：支持 DOI 跳转、来源标签、摘要/全文片段标记、Markdown 与 LaTeX 公式渲染、文献目录跳转。
- 一图看懂：生成绘图提示词，调用生图模型或生成本地占位图，并缓存图片。
- 综述图片导出：前端支持导出带水印的综述图片。
- 翻译功能：标题、摘要和证据内容支持 LLM 翻译；未配置翻译模型时提示使用浏览器翻译。
- 开放 PDF 检测：仅检测合法开放获取 PDF，不绕过付费墙。
- 微信公众号模块：将平台日报转换为适合公众号阅读的科研图文，并创建微信公众号草稿。
- 失败暂存与继续：检索和证据增强后的中间状态会保存到 drafts，LLM 或生图失败后可继续。
- 北京时间调度：定时任务统一基于北京时间。
- 北京时间调度：默认一次只运行一个主题，降低 LLM 上游拥塞风险；如确有需要，可通过环境变量调整并发数。

## 技术栈

- 前端：React、TypeScript、Vite
- 后端：FastAPI、Python
- 部署：Dockerfile 一体化构建，Nginx 托管前端静态资源，FastAPI 提供 API
- 持久化：文件系统目录持久化
- LLM：OpenAI 兼容接口
- 文献源：Sciverse、Paper Search HTTP 适配器、多种公开学术 API
- 公众号：微信公众号服务端接口，支持创建草稿

## 目录结构

```text
.
├── apps/web/                  # React 前端
├── services/agent/app/         # FastAPI 后端
├── services/agent/requirements.txt
├── Dockerfile                  # 一体化部署入口
├── docker-compose.yml          # Docker Compose 示例
├── zeabur-start.sh             # 容器启动脚本
├── .env.example                # 环境变量模板
├── DEPLOYMENT.md               # 部署补充说明
├── README.md                   # 中文说明
├── README.en.md                # English README
├── NOTICE.md                   # 来源与致谢
└── LICENSE                     # AGPL-3.0-only
```

## 快速部署

### Dockerfile 一体化部署

```bash
cp .env.example .env
mkdir -p data/frontier_review data/corpora
docker build -t frontier-review .
docker run -d \
  --name frontier-review \
  --env-file .env \
  -p 8080:8080 \
  -v "$(pwd)/data/frontier_review:/data/frontier_review" \
  -v "$(pwd)/data/corpora:/data/corpora" \
  frontier-review
```

访问：

```text
http://localhost:8080/
http://localhost:8080/api/healthz
```

### Docker Compose

```bash
cp .env.example .env
mkdir -p data/frontier_review data/corpora
docker compose up -d --build
```

如果云平台将本项目识别为 static provider，说明它只启动了前端静态站点，后端 API 不会运行。请改用 Dockerfile / Docker provider。

## 环境变量

复制 `.env.example` 为 `.env`，至少配置：

```env
CORS_ORIGINS=https://your-domain.example
FRONTIER_REVIEW_REQUIRE_SECURE_CONFIG=true
FRONTIER_REVIEW_ALLOW_INSECURE_DEFAULTS=false
FRONTIER_REVIEW_ENABLE_DOCS=false
FRONTIER_REVIEW_ENABLE_SCHEDULER=true

DAILY_REVIEW_ADMIN_SECRET=replace-with-a-long-random-secret
DAILY_REVIEW_ADMIN_USERNAME=admin
DAILY_REVIEW_ADMIN_PASSWORD=replace-with-a-strong-password

FRONTIER_REVIEW_DATA_DIR=/data/frontier_review
BIBLIOCN_CORPORA_DIR=/data/corpora

OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
OPENAI_API_KEY=replace-with-your-key
OPENAI_MODEL=your-model-name
LLM_MAX_RETRIES=3
LLM_TIMEOUT_SECONDS=900
LLM_CONNECT_TIMEOUT_SECONDS=20
LLM_COMPLETE_USE_STREAM=true

DAILY_REVIEW_LITERATURE_PROVIDER=hybrid
PAPER_SEARCH_SOURCES=semantic,openalex,crossref,europepmc,hal,base,core,unpaywall
PAPER_SEARCH_VALIDATE_LINKS=true
DAILY_REVIEW_SCHEDULER_CONCURRENCY=1

UNPAYWALL_EMAIL=your-email@example.com
OPENALEX_MAILTO=your-email@example.com
```

更多变量见 [.env.example](./.env.example)。

## 持久化目录

推荐部署时挂载以下目录：

```text
/data/frontier_review
/data/corpora
```

主要数据：

```text
/data/frontier_review/config.json
/data/frontier_review/admin_auth.json
/data/frontier_review/runs/{topicId}/{runId}.json
/data/frontier_review/daily_review_runs_index.jsonl
/data/frontier_review/paper_index.jsonl
/data/frontier_review/daily_delta_runs.jsonl
/data/frontier_review/drafts/{topicId}/{draftId}.json
/data/frontier_review/assets/images/
/data/frontier_review/assets/pdfs/
/data/frontier_review/wechat_token.json
```

说明：

- `runs/{topicId}/{runId}.json` 保存每期正式日报完整数据。
- `daily_review_runs_index.jsonl` 是轻量历史索引。
- `paper_index.jsonl` 用于 Daily Delta 历史文献判断。
- `drafts/{topicId}/{draftId}.json` 保存失败或待继续的暂存任务。
- 只有完整流程结束后，系统才写入正式日报和 Daily Delta 历史索引。

## 管理员后台

管理员后台用于配置主题、模型、检索源、调度时间、公众号信息和 Daily Delta 策略。

典型流程：

1. 访问 `/admin`。
2. 使用管理员账号登录。
3. 修改默认管理员密码和安全配置。
4. 新增或编辑主题。
5. 配置主题名称、路径、主题描述、文献数量、年份范围、公开/专属、定时更新。
6. 配置 Daily Delta 参数：子议题池、最少高新颖文献数、最大重复比例、是否允许专题深挖、是否允许监测短报。
7. 配置 LLM、翻译模型、生图模型和文献源。
8. 使用测试连接按钮确认 LLM、Sciverse、Paper Search、翻译、图片、微信等服务可用。
9. 手动运行主题或等待定时任务触发。

### 专属综述

- 管理员可配置“专属综述访问密钥”。
- 主题可勾选“管理员专属综述，不对游客公开”。
- 专属主题不会出现在 `/daily-review` 公开目录中，公开 latest/history/run API 也不会返回该主题内容。
- 专属综述入口为 `/admin/exclusive-review`。
- 专属综述和公开综述共用同一套生成逻辑：检索、去重、评分、综述、图片、PDF、翻译均一致。

## Daily Delta 模式

每个主题独立维护历史文献索引。系统根据本主题历史记录计算：

- `newEvidenceCount`：本主题首次出现的文献数量。
- `reusedEvidenceCount`：本主题历史中已出现的文献数量。
- `highNoveltyCount`：新颖度评分较高的文献数量。
- `repeatRatio`：重复文献比例。
- `averageNoveltyScore`：平均新颖度。

模式判定：

```text
highNoveltyCount >= minHighNoveltyCount
且 repeatRatio <= maxRepeatRatio
=> fresh_daily 新增日报

否则，如果 highNoveltyCount >= 3
或 newEvidenceCount >= max(5, paperCount * 0.15)
=> delta_brief 差异简报

否则，如果 allowTopicDeepDive = true
且 paperCount >= 20
=> topic_deep_dive 专题深挖

否则
=> no_significant_update 监测短报
```

这些参数是主题级配置。

## 微信公众号模块

公众号模块位于 `/admin/wechat`，需要管理员鉴权。

功能：

- 选择已生成的日报，默认按生成时间倒序排列。
- 将平台综述转为适合公众号阅读的 HTML 图文。
- 自动生成程序化微信封面，不依赖 AI 生图。
- 公众号封面适配微信消息列表 `2.35:1` 和转发卡片/公众号主页 `1:1`。
- 将正文中的本地图像上传为微信可访问图片。
- 创建微信公众号草稿。
- “阅读原文”指向单篇日报永久路径。

权限要求：

- 需要微信公众号 AppID 和 AppSecret。
- 服务器公网出口 IP 必须加入微信公众号后台 IP 白名单。
- 需要具备草稿创建接口权限。
- 项目默认只创建草稿，不自动发布。

## 开放 PDF 检测

系统只检测合法开放获取 PDF，不绕过付费墙。可用线索包括：

- Semantic Scholar `openAccessPdf`
- Unpaywall `best_oa_location.url_for_pdf`
- CORE `downloadUrl`
- Europe PMC full text URL
- HAL / BASE 开放仓储 URL
- Sciverse 开放访问 URL
- 文献记录中已有的开放 PDF URL

如果成功缓存 PDF，前端文献卡片会显示 PDF 下载入口。未找到合法开放 PDF 时会显示未发现可缓存 PDF。

## 本地验证

```bash
python -B -m py_compile services/agent/app/daily_review.py services/agent/app/paper_search_client.py services/agent/app/config.py services/agent/app/main.py services/agent/app/llm.py
pnpm --dir apps/web build
```

## 开源目标与贡献方向

开源这个项目的主要目标，是让更多人一起完善“可溯源 AI 文献综述”的工作流。欢迎贡献：

- 更强的综述提示词与证据约束策略。
- 更好的文献检索、去重、质量评分和字段标准化逻辑。
- 更多公开学术 API 适配器。
- 更可靠的全文片段获取和开放 PDF 检测。
- 更好的 Daily Delta 策略。
- 更美观的前端交互和文献证据阅读体验。
- 微信公众号同步、排版、封面和草稿工作流。
- 部署脚本、安全审计、测试用例和文档。

## 安全注意事项

- 不要将 `.env`、真实 API Key、微信公众号 AppSecret、管理员密码提交到公开仓库。
- 生产环境必须设置 `FRONTIER_REVIEW_REQUIRE_SECURE_CONFIG=true`。
- 生产环境建议关闭 API 文档：`FRONTIER_REVIEW_ENABLE_DOCS=false`。
- 管理员密码应使用强密码。
- `DAILY_REVIEW_ADMIN_SECRET` 应使用长随机字符串。
- CORS 只允许实际公网域名。
- 使用外部文献 API、LLM API、生图 API、微信公众号 API 时，应遵守对应平台的服务条款、速率限制和数据使用规范。

## 来源、致谢与社区

- 本项目基于 [niuniu-869/Biblio_Agent](https://github.com/niuniu-869/Biblio_Agent) 继续扩展和工程化改造。
- 文献多源检索能力参考 [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp) 的实现思路。
- 感谢 [LINUX DO](https://linux.do/) 社区对开源项目交流、反馈和推广的支持。
- 作者主页：[violetreay on LINUX DO](https://linux.do/u/violetreay/summary)。

## 开源协议

本项目采用 [GNU Affero General Public License v3.0 only](./LICENSE)。
