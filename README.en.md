# Frontier Review

Frontier Review is a self-hostable multi-topic literature monitoring and review-generation system for researchers, engineering teams, and research-content operators. Administrators can configure multiple research topics, schedule or manually trigger multi-source literature search, evidence filtering, deduplication, field merging, quality scoring, Daily Delta classification, LLM-based review writing, infographic generation, open-access PDF detection, translation, and WeChat Official Account draft creation.

This project is extended and engineered from the ideas and part of the technical foundation of [niuniu-869/Biblio_Agent](https://github.com/niuniu-869/Biblio_Agent), and it also references the multi-source paper search approach of [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp). It focuses on long-running multi-topic frontier monitoring and traceable AI-assisted review production. Thanks to the related open-source authors and contributors.

[中文 README](./README.md)

## What This Project Is

This is not a one-shot Q&A tool, and it is not a strict PRISMA systematic-review platform. It is closer to a deployable literature radar:

- Each topic can be monitored continuously.
- Every issue keeps evidence records and source metadata.
- The review is generated from retrieved evidence rather than free-form model hallucination.
- Daily Delta classifies each issue as a fresh daily review, delta brief, topic deep dive, or monitoring note based on topic history.
- Administrators control topics, models, sources, schedules, private access, and WeChat draft workflows.

## Features

- Multi-topic management with independent routes, histories, and Daily Delta thresholds.
- Public and private reviews. Private topics are hidden from public routes and require an access key.
- Hybrid literature retrieval from Sciverse and Paper Search HTTP adapters, inspired in part by paper-search-mcp's multi-source retrieval workflow.
- Field normalization across Semantic Scholar, OpenAlex, Crossref, Europe PMC, HAL, BASE, CORE, Unpaywall, Sciverse, and related sources.
- DOI/title/uniqueId/docId deduplication with metadata merging.
- Evidence scoring for relevance, quality, completeness, and novelty.
- Daily Delta mode selection based on per-topic historical indexes.
- Dynamic subtitles generated from the topic, subtopic pool, recent history, and evidence.
- LLM-based structured Chinese review writing through OpenAI-compatible APIs.
- Evidence page with DOI links, source labels, abstract/full-text markers, Markdown and LaTeX rendering, and paper directory jumps.
- Infographic prompt generation, image generation, local fallback image, and cached image display.
- Review image export with watermark.
- LLM translation for paper titles, abstracts, and evidence content.
- Legal open-access PDF detection. The system does not bypass paywalls.
- WeChat Official Account module for turning reviews into draft articles.
- Draft persistence and resume support when LLM/image generation fails.
- Beijing-time scheduling and configurable scheduler concurrency.

## Tech Stack

- Frontend: React, TypeScript, Vite
- Backend: FastAPI, Python
- Deployment: Dockerfile, Nginx for frontend static files, FastAPI for APIs
- Persistence: filesystem-based storage
- LLM: OpenAI-compatible APIs
- Literature sources: Sciverse, Paper Search HTTP adapters, and public scholarly APIs
- WeChat: Official Account server APIs for draft creation

## Directory Structure

```text
.
├── apps/web/                  # React frontend
├── services/agent/app/         # FastAPI backend
├── services/agent/requirements.txt
├── Dockerfile
├── docker-compose.yml
├── zeabur-start.sh
├── .env.example
├── DEPLOYMENT.md
├── README.md
├── README.en.md
├── NOTICE.md
└── LICENSE
```

## Quick Start

### Dockerfile

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

Open:

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

If a cloud platform detects this repository as a static provider, the backend API will not run. Use Docker / Dockerfile deployment instead.

## Environment Variables

Copy `.env.example` to `.env`. At minimum, configure:

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

DAILY_REVIEW_LITERATURE_PROVIDER=hybrid
PAPER_SEARCH_SOURCES=semantic,openalex,crossref,europepmc,hal,base,core,unpaywall
PAPER_SEARCH_VALIDATE_LINKS=true

UNPAYWALL_EMAIL=your-email@example.com
OPENALEX_MAILTO=your-email@example.com
```

See [.env.example](./.env.example) for the full template.

## Persistence

Recommended mounted directories:

```text
/data/frontier_review
/data/corpora
```

Important files:

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

## Admin Console

The admin console configures topics, models, sources, schedules, WeChat settings, and Daily Delta rules.

Typical workflow:

1. Visit `/admin`.
2. Log in with the administrator account.
3. Change the default password and security settings.
4. Add or edit topics.
5. Configure topic name, route, query description, paper count, year range, public/private visibility, and schedule.
6. Configure Daily Delta thresholds.
7. Configure LLM, translation, image generation, and literature sources.
8. Test each external service.
9. Run manually or wait for scheduled jobs.

### Private Reviews

- Administrators can configure a private-review access key.
- Topics can be marked as administrator-only.
- Private topics are not returned by public topic/latest/history/run APIs.
- Private review entry: `/admin/exclusive-review`.
- Private reviews and public reviews share the same generation pipeline: search, deduplication, scoring, review writing, images, PDFs, and translation.

## Contributing

This project is open sourced to invite collaboration on traceable AI-assisted literature reviews. Contributions are welcome in:

- Better review prompts and evidence constraints.
- Literature retrieval, deduplication, quality scoring, and field normalization.
- Additional scholarly API adapters.
- Full-text snippet extraction and open-access PDF detection.
- Daily Delta strategies.
- Frontend reading experience and evidence navigation.
- WeChat article synchronization, formatting, covers, and draft workflow.
- Deployment scripts, security review, tests, and documentation.

## Security Notes

- Never commit `.env`, real API keys, WeChat AppSecret, or admin passwords.
- Use `FRONTIER_REVIEW_REQUIRE_SECURE_CONFIG=true` in production.
- Disable API docs in production with `FRONTIER_REVIEW_ENABLE_DOCS=false`.
- Use a strong admin password.
- Use a long random `DAILY_REVIEW_ADMIN_SECRET`.
- Restrict CORS to your real frontend domains.
- Follow the terms, rate limits, and data policies of all external literature, LLM, image, and WeChat APIs.

## Attribution and Community

- This project is extended from [niuniu-869/Biblio_Agent](https://github.com/niuniu-869/Biblio_Agent).
- Its multi-source literature retrieval workflow also references [openags/paper-search-mcp](https://github.com/openags/paper-search-mcp).
- Thanks to the [LINUX DO](https://linux.do/) community for open-source discussion, feedback, and project sharing.
- Author profile: [violetreay on LINUX DO](https://linux.do/u/violetreay/summary).

## License

This project is licensed under the [GNU Affero General Public License v3.0 only](./LICENSE).
