// 类型化 API 客户端。类型由 packages/contracts/openapi.yaml 生成 (单一真源, Codex-10)。
// 生成: pnpm gen:api → src/api/schema.d.ts
import type { components } from "./schema";
import type { LlmSettings } from "./useLlmSettings";

export type Health = components["schemas"]["Health"];
export type CorpusRef = components["schemas"]["CorpusRef"];
export type OverviewResult = components["schemas"]["OverviewResult"];
export type DbSource = components["schemas"]["DbSource"];
export type SourcesResult = components["schemas"]["SourcesResult"];
export type AuthorsResult = components["schemas"]["AuthorsResult"];
export type DocumentsResult = components["schemas"]["DocumentsResult"];
export type Graph = components["schemas"]["Graph"];
export type NetworkResult = components["schemas"]["NetworkResult"];
export type SocialResult = components["schemas"]["SocialResult"];
export type PrismaRequest = components["schemas"]["PrismaRequest"];
export type PrismaResult = components["schemas"]["PrismaResult"];
export type TextResult = components["schemas"]["TextResult"];
export type ScreenResult = components["schemas"]["ScreenResult"];
export type ChatMessage = components["schemas"]["ChatMessage"];
export type CiteResult = components["schemas"]["CiteResult"];
// A4 高级图信封类型
export type AnalysisUnavailable = components["schemas"]["AnalysisUnavailable"];
export type AnalysisUnavailableReason = AnalysisUnavailable["reason"];
export type AuthorProductionEnvelope = components["schemas"]["AuthorProductionEnvelope"];
export type KeywordTrendEnvelope = components["schemas"]["KeywordTrendEnvelope"];
export type CitedRefsEnvelope = components["schemas"]["CitedRefsEnvelope"];
// A5 高级图② 信封类型
export type ThematicEnvelope = components["schemas"]["ThematicEnvelope"];
export type EvolutionEnvelope = components["schemas"]["EvolutionEnvelope"];
export type HistciteEnvelope = components["schemas"]["HistciteEnvelope"];
export type ThreeFieldEnvelope = components["schemas"]["ThreeFieldEnvelope"];

const BASE: string =
  (import.meta.env?.VITE_API_BASE as string | undefined) ?? "/api";

function initialBase(base: string): string {
  const normalized = base.replace(/\/$/, "");
  if (/^http:\/\/(localhost|127\.0\.0\.1):(8000|8010)$/.test(normalized)) {
    return "http://127.0.0.1:8011";
  }
  return normalized;
}

let activeBase = initialBase(BASE);

function candidateBases(): string[] {
  const isBrowser = typeof window !== "undefined";
  const isLocalHost = isBrowser && /^(localhost|127\.0\.0\.1)$/.test(window.location.hostname);
  if (isBrowser && !isLocalHost && BASE.startsWith("/")) {
    return Array.from(new Set([activeBase, BASE].map((x) => x.replace(/\/$/, ""))));
  }
  const bases = [
    activeBase,
    "http://127.0.0.1:8011",
    "http://localhost:8011",
    "http://127.0.0.1:8010",
    "http://localhost:8010",
    BASE,
    "http://127.0.0.1:8000",
    "http://localhost:8000",
  ].map((x) => x.replace(/\/$/, ""));
  return Array.from(new Set(bases));
}

function withBase(input: string, base: string): string {
  const normalizedBase = base.replace(/\/$/, "");
  let normalizedInput = input;
  for (const candidate of candidateBases()) {
    if (normalizedInput.startsWith(candidate)) {
      normalizedInput = normalizedInput.slice(candidate.length);
      break;
    }
  }
  return `${normalizedBase}${normalizedInput.startsWith("/") ? normalizedInput : `/${normalizedInput}`}`;
}

export function dailyReviewAssetSrc(url?: string | null, accessKey?: string): string {
  const value = (url || "").trim();
  if (!value) return "";
  if (/^(data:|blob:|https?:\/\/)/i.test(value)) return value;
  const apiPath = value.startsWith("/api/")
    ? value
    : value.startsWith("/daily-review/")
      ? `${BASE}${value}`
      : `${BASE}/${value.replace(/^\/+/, "")}`;
  const resolved = withBase(apiPath, activeBase);
  const key = accessKey?.trim();
  if (!key || !apiPath.includes("/daily-review/assets/")) return resolved;
  const separator = resolved.includes("?") ? "&" : "?";
  return `${resolved}${separator}accessKey=${encodeURIComponent(key)}`;
}

export const dailyReviewImageSrc = dailyReviewAssetSrc;

export class ApiError extends Error {
  constructor(
    public code: string,
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// 网络层失败也归一为 ApiError, 调用方只需处理一种错误类型 (Codex step4-P2)
async function doFetch(input: string, init?: RequestInit): Promise<Response> {
  const isApiRequest = input.startsWith(BASE) || input.startsWith(activeBase);
  if (isApiRequest) {
    let lastError: unknown;
    for (const base of candidateBases()) {
      const url = withBase(input, base);
      try {
        const res = await fetch(url, init);
        const contentType = res.headers.get("content-type") || "";
        if (url.includes("/daily-review/") && contentType.includes("text/html")) {
          lastError = new ApiError("API_PROXY_MISROUTED", res.status, `接口被前端路由接管: ${url}`);
          continue;
        }
        if (res.status !== 404 || !url.includes("/daily-review/")) {
          activeBase = base;
          return res;
        }
        lastError = new ApiError("NOT_FOUND", 404, `接口不存在: ${url}`);
      } catch (e) {
        lastError = e;
      }
    }
    throw new ApiError(
      "NETWORK_ERROR",
      0,
      lastError instanceof Error ? lastError.message : "无法连接后端服务",
    );
  }
  try {
    return await fetch(input, init);
  } catch (e) {
    throw new ApiError("NETWORK_ERROR", 0, (e as Error).message || "网络错误");
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let body: { code?: string; message?: string } = {};
    try {
      body = await res.json();
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(body.code ?? "INTERNAL", res.status, body.message ?? res.statusText);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

export interface LlmRequestOptions {
  apiKey?: string;
  baseUrl?: string;
  model?: string;
}

export type LlmRequestInput = LlmRequestOptions | string | undefined;

export function normalizeLlmOptions(llm?: LlmRequestInput): LlmRequestOptions | undefined {
  if (!llm) return undefined;
  if (typeof llm === "string") {
    const apiKey = llm.trim();
    return apiKey ? { apiKey } : undefined;
  }
  const next: LlmRequestOptions = {
    apiKey: llm.apiKey?.trim() || undefined,
    baseUrl: llm.baseUrl?.trim() || undefined,
    model: llm.model?.trim() || undefined,
  };
  return next.apiKey || next.baseUrl || next.model ? next : undefined;
}

export function llmOptionsFromSettings(settings?: Partial<LlmSettings> | null): LlmRequestOptions {
  return normalizeLlmOptions({
    apiKey: settings?.apiKey?.trim() || undefined,
    baseUrl: settings?.baseUrl?.trim() || undefined,
    model: settings?.model?.trim() || undefined,
  }) ?? {};
}

export function buildLlmHeaders(llm?: LlmRequestInput): Record<string, string> {
  const normalized = normalizeLlmOptions(llm);
  const headers: Record<string, string> = {};
  if (normalized?.apiKey) headers["X-LLM-Key"] = normalized.apiKey;
  if (normalized?.baseUrl) headers["X-LLM-Base-URL"] = normalized.baseUrl;
  if (normalized?.model) headers["X-LLM-Model"] = normalized.model;
  return headers;
}

export interface ReviewTopicConfig {
  id: string;
  slug: string;
  name: string;
  topic: string;
  enabled: boolean;
  scheduleEnabled: boolean;
  scheduleTime: string;
  paperCount: number;
  sinceYear: number;
  freshnessBoost: "NONE" | "MILD" | "STRONG";
  includeFullText: boolean;
  includeWeb: boolean;
  privateOnly?: boolean;
  subtopicPool?: string[];
  minHighNoveltyCount?: number | null;
  maxRepeatRatio?: number;
  allowTopicDeepDive?: boolean;
  allowNoSignificantUpdate?: boolean;
}

export interface DailyReviewConfig {
  topic: string;
  scheduleEnabled: boolean;
  scheduleTime: string;
  paperCount: number;
  sinceYear: number;
  freshnessBoost: "NONE" | "MILD" | "STRONG";
  includeFullText: boolean;
  includeWeb: boolean;
  sciverseApiToken: string;
  literatureProvider: "sciverse" | "paper_search" | "hybrid";
  paperSearchSources: string[];
  sciverseTokenConfigured?: boolean;
  llmKeyConfigured?: boolean;
  translationKeyConfigured?: boolean;
  imageKeyConfigured?: boolean;
  llm: {
    baseUrl: string;
    apiKey: string;
    model: string;
    temperature: number;
    maxTokens: number;
  };
  translation: {
    baseUrl: string;
    apiKey: string;
    model: string;
    temperature: number;
    maxTokens: number;
  };
  image: {
    enabled: boolean;
    baseUrl: string;
    apiKey: string;
    model: string;
    size: string;
  };
  wechat?: {
    enabled: boolean;
    appId: string;
    appSecret: string;
    author: string;
    sourceUrlBase: string;
    autoDraft: boolean;
    coverImageUrl?: string;
    digestPrefix?: string;
  };
  wechatSecretConfigured?: boolean;
  exclusiveAccessKey: string;
  exclusiveAccessKeyConfigured?: boolean;
  literatureSearchCdk?: string;
  literatureSearchCdkConfigured?: boolean;
  literatureSearchCdks?: LiteratureSearchCdkConfig[];
  activeTopicId: string;
  topics: ReviewTopicConfig[];
}

export interface LiteratureSearchCdkConfig {
  id: string;
  name: string;
  code: string;
  enabled: boolean;
  maxUses: number;
  usedCount: number;
  expiresAt?: string | null;
  paperCountMax: number;
  literatureProvider?: "sciverse" | "paper_search" | "hybrid" | null;
  paperSearchSources?: string[];
  note?: string;
}

export interface DailyReviewPaper {
  id: string;
  title: string;
  authors: string[];
  year?: number | null;
  publishedDate?: string | null;
  venue?: string | null;
  doi?: string | null;
  url?: string | null;
  abstract?: string | null;
  citationCount?: number | null;
  fwci?: number | null;
  influentialCitationCount?: number | null;
  docId?: string | null;
  uniqueId?: string | null;
  snippet?: string | null;
  pageNo?: number | null;
  score?: number | null;
  relevanceScore?: number | null;
  qualityScore?: number | null;
  evidenceScore?: number | null;
  noveltyScore?: number | null;
  evidenceScores?: {
    relevance?: { score?: number | null; label?: string | null } | null;
    quality?: { score?: number | null; label?: string | null } | null;
    evidence?: { score?: number | null; label?: string | null } | null;
    novelty?: { score?: number | null; label?: string | null } | null;
  } | null;
  scoreTags?: string[] | null;
  seenBefore?: boolean | null;
  recentlyUsed?: boolean | null;
  usedCount?: number | null;
  source?: string | null;
  sources?: string[] | null;
  isLinkVerified?: boolean | null;
  linkStatus?: number | null;
  unpaywallOaStatus?: string | null;
  unpaywallIsOa?: boolean | null;
  pdfUrl?: string | null;
  pdfRemoteUrl?: string | null;
  pdfCode?: string | null;
  pdfSource?: string | null;
  pdfLicense?: string | null;
  pdfBytes?: number | null;
  pdfCached?: boolean | null;
  pdfAvailable?: boolean | null;
  pdfStatus?: string | null;
  openAccessPdf?: boolean | null;
  evidenceSource?: "abstract" | "fulltext" | string | null;
}

export interface LiteratureCdkPublicInfo {
  id: string;
  name: string;
  enabled: boolean;
  maxUses: number;
  usedCount: number;
  remainingUses: number;
  expiresAt?: string | null;
  paperCountMax: number;
  literatureProvider?: "sciverse" | "paper_search" | "hybrid" | null;
  paperSearchSources?: string[];
  note?: string | null;
}

export interface LiteratureOnlySearchRequest {
  topic: string;
  paperCount: number;
  sinceYear?: number | null;
  literatureProvider?: "sciverse" | "paper_search" | "hybrid" | null;
  paperSearchSources?: string[] | null;
  cdk?: string | null;
  llm?: {
    baseUrl: string;
    apiKey: string;
    model: string;
    temperature?: number;
    maxTokens?: number;
  } | null;
}

export interface LiteratureOnlySearchResult {
  ok: boolean;
  searchId?: string | null;
  sharePath?: string | null;
  createdAt?: string | null;
  topic: string;
  requested: number;
  returned: number;
  sinceYear: number;
  literatureProvider: "sciverse" | "paper_search" | "hybrid";
  paperSearchSources: string[];
  llmSearchQueries: string[];
  searchExpression: string;
  cdk?: LiteratureCdkPublicInfo | null;
  papers: DailyReviewPaper[];
}

export interface LiteratureSearchProgressItem {
  searchId: string;
  status: "queued" | "running" | "success" | "error";
  stage: string;
  message: string;
  mode: "determinate" | "indeterminate";
  detail?: string | null;
  percent: number;
  current: number;
  total?: number | null;
  startedAt?: string | null;
  updatedAt?: string | null;
  completedAt?: string | null;
  error?: string | null;
  sharePath?: string | null;
}

export interface LiteratureSearchAccepted {
  accepted: boolean;
  searchId: string;
  sharePath: string;
  progress: LiteratureSearchProgressItem;
}

export interface DailyReviewPdfResolveResult {
  ok: boolean;
  url?: string | null;
  remoteUrl?: string | null;
  code?: string | null;
  source?: string | null;
  isOpenAccess: boolean;
  oaStatus?: string | null;
  license?: string | null;
  cached?: boolean;
  bytes?: number | null;
  message: string;
  detail?: string | null;
}

export interface DailyReviewRunResult {
  runId: string;
  topicId?: string | null;
  topicSlug?: string | null;
  topicName?: string | null;
  topic: string;
  subtitle?: string | null;
  dailyDelta?: {
    mode?: "fresh_daily" | "delta_brief" | "topic_deep_dive" | "no_significant_update" | string;
    modeLabel?: string | null;
    subtitle?: string | null;
    paperCount?: number | null;
    newEvidenceCount?: number | null;
    reusedEvidenceCount?: number | null;
    highNoveltyCount?: number | null;
    repeatRatio?: number | null;
    averageNoveltyScore?: number | null;
    recentRunCount?: number | null;
  } | null;
  createdAt: string;
  query: {
    paperCount: number;
    sinceYear: number;
    freshnessBoost: "NONE" | "MILD" | "STRONG";
    includeFullText: boolean;
    includeWeb: boolean;
    literatureProvider?: "sciverse" | "paper_search" | "hybrid";
    paperSearchSources?: string[];
    llmSearchQueries?: string[];
    searchExpression?: string;
    qualityFiltered?: boolean;
  };
  papers: DailyReviewPaper[];
  fullTextFetched: number;
  reviewMarkdown: string;
  image: {
    status: string;
    prompt: string;
    url?: string | null;
  };
  sciverseTotal?: number | null;
}

export interface WechatArticleResult {
  runId: string;
  title: string;
  digest: string;
  contentHtml: string;
  contentText: string;
  coverUrl?: string | null;
  sourceUrl: string;
  articleUrl?: string | null;
  draftMediaId?: string | null;
  status: string;
  message?: string | null;
}

export interface DailyReviewRunSummary {
  runId: string;
  topicId?: string | null;
  topicSlug?: string | null;
  topicName?: string | null;
  topic: string;
  subtitle?: string | null;
  dailyMode?: string | null;
  newEvidenceCount?: number | null;
  reusedEvidenceCount?: number | null;
  createdAt: string;
  paperCount: number;
  sinceYear?: number | null;
  fullTextFetched: number;
  imageStatus?: string | null;
}

export interface ConnectionTestResult {
  ok: boolean;
  service: "llm" | "sciverse" | "paper_search" | "image" | "wechat";
  message: string;
  detail?: string | null;
}

export interface DailyReviewAdminLoginResult {
  token: string;
  username: string;
  expiresAt: string;
}

export interface DailyReviewSmokeResult {
  ok: boolean;
  topic: string;
  requested: number;
  returned: number;
  sinceYear: number;
  sciverseTotal?: number | null;
  searchExpression: string;
  withAbstractCount?: number;
  withSnippetCount?: number;
  strongDomainCount?: number;
  yearCounts: Array<[string, number]>;
  venueTop: Array<[string, number]>;
  sampleTitles: string[];
}

export interface DailyReviewProgressItem {
  topicId: string;
  topicSlug: string;
  topicName: string;
  status: "idle" | "running" | "success" | "error";
  stage: string;
  message: string;
  mode?: "determinate" | "indeterminate";
  detail?: string | null;
  percent: number;
  current: number;
  total?: number | null;
  startedAt?: string | null;
  updatedAt?: string | null;
  completedAt?: string | null;
  runId?: string | null;
  error?: string | null;
  latestRunId?: string | null;
  latestRunAt?: string | null;
  latestPaperCount?: number | null;
  draftId?: string | null;
  draftStage?: string | null;
  draftCanResume?: boolean;
}

export interface DailyReviewRunAccepted {
  accepted: boolean;
  topicId: string;
  progress: DailyReviewProgressItem;
}

export interface DailyReviewDraftSummary {
  draftId: string;
  topicId: string;
  topicSlug?: string | null;
  topicName?: string | null;
  topic: string;
  stage: string;
  status: string;
  canResume: boolean;
  createdAt: string;
  updatedAt: string;
  paperCount: number;
  fullTextFetched: number;
  error?: string | null;
}

export interface TranslateResult {
  translatedText: string;
  model: string;
}

function authHeaders(adminToken?: string): Record<string, string> {
  return adminToken ? { Authorization: `Bearer ${adminToken}` } : {};
}

export async function loginDailyReviewAdmin(username: string, password: string): Promise<DailyReviewAdminLoginResult> {
  return handle<DailyReviewAdminLoginResult>(
    await doFetch(`${BASE}/daily-review/admin/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }),
  );
}

export async function changeDailyReviewAdminPassword(
  oldPassword: string,
  newPassword: string,
  adminToken?: string,
): Promise<{ ok: boolean }> {
  return handle<{ ok: boolean }>(
    await doFetch(`${BASE}/daily-review/admin/password`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify({ oldPassword, newPassword }),
    }),
  );
}

export async function getDailyReviewConfig(adminToken?: string): Promise<DailyReviewConfig> {
  return handle<DailyReviewConfig>(
    await doFetch(`${BASE}/daily-review/config`, {
      headers: authHeaders(adminToken),
    }),
  );
}

export async function saveDailyReviewConfig(config: DailyReviewConfig, adminToken?: string): Promise<DailyReviewConfig> {
  return handle<DailyReviewConfig>(
    await doFetch(`${BASE}/daily-review/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(config),
    }),
  );
}

export async function getDailyReviewTopics(): Promise<{ items: ReviewTopicConfig[] }> {
  return handle<{ items: ReviewTopicConfig[] }>(
    await doFetch(`${BASE}/daily-review/topics`),
  );
}

function exclusiveHeaders(accessKey?: string): Record<string, string> {
  return accessKey?.trim() ? { "X-Exclusive-Review-Key": accessKey.trim() } : {};
}

export async function getExclusiveReviewTopics(accessKey: string): Promise<{ items: ReviewTopicConfig[] }> {
  return handle<{ items: ReviewTopicConfig[] }>(
    await doFetch(`${BASE}/daily-review/exclusive/topics`, {
      headers: exclusiveHeaders(accessKey),
    }),
  );
}

export async function getLatestDailyReview(topicSlug?: string): Promise<{ result: DailyReviewRunResult | null }> {
  const query = topicSlug ? `?topic=${encodeURIComponent(topicSlug)}` : "";
  return handle<{ result: DailyReviewRunResult | null }>(
    await doFetch(`${BASE}/daily-review/latest${query}`),
  );
}

export async function getLatestExclusiveReview(accessKey: string, topicSlug?: string): Promise<{ result: DailyReviewRunResult | null }> {
  const query = topicSlug ? `?topic=${encodeURIComponent(topicSlug)}` : "";
  return handle<{ result: DailyReviewRunResult | null }>(
    await doFetch(`${BASE}/daily-review/exclusive/latest${query}`, {
      headers: exclusiveHeaders(accessKey),
    }),
  );
}

export async function getDailyReviewHistory(limit = 30, topicSlug?: string): Promise<{ items: DailyReviewRunSummary[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (topicSlug) params.set("topic", topicSlug);
  return handle<{ items: DailyReviewRunSummary[] }>(
    await doFetch(`${BASE}/daily-review/history?${params.toString()}`),
  );
}

export async function getExclusiveReviewHistory(accessKey: string, limit = 30, topicSlug?: string): Promise<{ items: DailyReviewRunSummary[] }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (topicSlug) params.set("topic", topicSlug);
  return handle<{ items: DailyReviewRunSummary[] }>(
    await doFetch(`${BASE}/daily-review/exclusive/history?${params.toString()}`, {
      headers: exclusiveHeaders(accessKey),
    }),
  );
}

export async function getDailyReviewAdminRuns(limit = 80, adminToken?: string): Promise<{ items: DailyReviewRunSummary[] }> {
  return handle<{ items: DailyReviewRunSummary[] }>(
    await doFetch(`${BASE}/daily-review/admin/runs?limit=${encodeURIComponent(String(limit))}`, {
      headers: authHeaders(adminToken),
    }),
  );
}

export async function getDailyReviewRun(runId: string): Promise<{ result: DailyReviewRunResult }> {
  return handle<{ result: DailyReviewRunResult }>(
    await doFetch(`${BASE}/daily-review/runs/${enc(runId)}`),
  );
}

export async function getLiteratureCdkStatus(cdk: string): Promise<{ ok: boolean; cdk?: LiteratureCdkPublicInfo | null; message: string }> {
  return handle<{ ok: boolean; cdk?: LiteratureCdkPublicInfo | null; message: string }>(
    await doFetch(`${BASE}/daily-review/literature-cdk/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cdk }),
    }),
  );
}

export async function searchLiteratureOnly(body: LiteratureOnlySearchRequest): Promise<LiteratureOnlySearchResult> {
  return handle<LiteratureOnlySearchResult>(
    await doFetch(`${BASE}/daily-review/literature-search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function startLiteratureSearch(body: LiteratureOnlySearchRequest): Promise<LiteratureSearchAccepted> {
  return handle<LiteratureSearchAccepted>(
    await doFetch(`${BASE}/daily-review/literature-search/async`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function getLiteratureSearchProgress(searchId: string): Promise<{ progress: LiteratureSearchProgressItem }> {
  return handle<{ progress: LiteratureSearchProgressItem }>(
    await doFetch(`${BASE}/daily-review/literature-search/progress/${enc(searchId)}`),
  );
}

export async function getLiteratureSearchResult(searchId: string): Promise<{ result: LiteratureOnlySearchResult | null; progress?: LiteratureSearchProgressItem | null }> {
  return handle<{ result: LiteratureOnlySearchResult | null; progress?: LiteratureSearchProgressItem | null }>(
    await doFetch(`${BASE}/daily-review/literature-search/results/${enc(searchId)}`),
  );
}

export async function getExclusiveReviewRun(accessKey: string, runId: string): Promise<{ result: DailyReviewRunResult }> {
  return handle<{ result: DailyReviewRunResult }>(
    await doFetch(`${BASE}/daily-review/exclusive/runs/${enc(runId)}`, {
      headers: exclusiveHeaders(accessKey),
    }),
  );
}

export async function testDailyReviewLlm(config: DailyReviewConfig, adminToken?: string): Promise<ConnectionTestResult> {
  return handle<ConnectionTestResult>(
    await doFetch(`${BASE}/daily-review/test/llm`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(config),
    }),
  );
}

export async function testDailyReviewSciverse(config: DailyReviewConfig, adminToken?: string): Promise<ConnectionTestResult> {
  return handle<ConnectionTestResult>(
    await doFetch(`${BASE}/daily-review/test/sciverse`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(config),
    }),
  );
}

export async function testDailyReviewPaperSearch(config: DailyReviewConfig, adminToken?: string): Promise<ConnectionTestResult> {
  return handle<ConnectionTestResult>(
    await doFetch(`${BASE}/daily-review/test/paper-search`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(config),
    }),
  );
}

export async function testDailyReviewTranslation(config: DailyReviewConfig, adminToken?: string): Promise<ConnectionTestResult> {
  return handle<ConnectionTestResult>(
    await doFetch(`${BASE}/daily-review/test/translation`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(config),
    }),
  );
}

export async function testDailyReviewImage(config: DailyReviewConfig, adminToken?: string): Promise<ConnectionTestResult> {
  return handle<ConnectionTestResult>(
    await doFetch(`${BASE}/daily-review/test/image`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(config),
    }),
  );
}

export async function testDailyReviewWechat(config: DailyReviewConfig, adminToken?: string): Promise<ConnectionTestResult> {
  return handle<ConnectionTestResult>(
    await doFetch(`${BASE}/daily-review/test/wechat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(config),
    }),
  );
}

export async function buildWechatArticle(
  body: { runId: string; title?: string; digest?: string },
  adminToken?: string,
): Promise<WechatArticleResult> {
  return handle<WechatArticleResult>(
    await doFetch(`${BASE}/daily-review/wechat/article`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(body),
    }),
  );
}

export async function createWechatDraft(
  body: { runId: string; title?: string; digest?: string; contentHtml?: string; contentText?: string; coverUrl?: string },
  adminToken?: string,
): Promise<WechatArticleResult> {
  return handle<WechatArticleResult>(
    await doFetch(`${BASE}/daily-review/wechat/draft`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(body),
    }),
  );
}

export async function getDailyReviewProgress(adminToken?: string): Promise<{ items: DailyReviewProgressItem[] }> {
  return handle<{ items: DailyReviewProgressItem[] }>(
    await doFetch(`${BASE}/daily-review/progress`, {
      headers: authHeaders(adminToken),
    }),
  );
}

export async function getDailyReviewDrafts(adminToken?: string): Promise<{ items: DailyReviewDraftSummary[] }> {
  return handle<{ items: DailyReviewDraftSummary[] }>(
    await doFetch(`${BASE}/daily-review/drafts`, {
      headers: authHeaders(adminToken),
    }),
  );
}

export async function resumeDailyReviewDraft(draftId: string, adminToken?: string): Promise<DailyReviewRunAccepted> {
  return handle<DailyReviewRunAccepted>(
    await doFetch(`${BASE}/daily-review/drafts/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify({ draftId }),
    }),
  );
}

export async function translateDailyReviewText(
  text: string,
  context?: string,
  targetLanguage = "中文",
): Promise<TranslateResult> {
  return handle<TranslateResult>(
    await doFetch(`${BASE}/daily-review/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, context, targetLanguage }),
    }),
  );
}

export async function resolveDailyReviewPdf(
  body: { runId?: string; paperId?: string; doi?: string | null; title?: string | null; url?: string | null },
  accessKey?: string,
): Promise<DailyReviewPdfResolveResult> {
  return handle<DailyReviewPdfResolveResult>(
    await doFetch(`${BASE}/daily-review/pdf`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...exclusiveHeaders(accessKey) },
      body: JSON.stringify(body),
    }),
  );
}

export async function runDailyReview(
  body: Partial<Pick<DailyReviewConfig, "topic" | "paperCount" | "sinceYear" | "freshnessBoost" | "includeFullText" | "includeWeb">> & { topicId?: string; topicSlug?: string },
  llm?: LlmRequestInput,
  sciverseKey?: string,
  adminToken?: string,
): Promise<DailyReviewRunResult> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...buildLlmHeaders(llm),
    ...authHeaders(adminToken),
  };
  if (sciverseKey?.trim()) headers["X-Sciverse-Key"] = sciverseKey.trim();
  return handle<DailyReviewRunResult>(
    await doFetch(`${BASE}/daily-review/run`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    }),
  );
}

export async function runDailyReviewAsync(
  body: Partial<Pick<DailyReviewConfig, "topic" | "paperCount" | "sinceYear" | "freshnessBoost" | "includeFullText" | "includeWeb">> & { topicId?: string; topicSlug?: string },
  llm?: LlmRequestInput,
  sciverseKey?: string,
  adminToken?: string,
): Promise<DailyReviewRunAccepted> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...buildLlmHeaders(llm),
    ...authHeaders(adminToken),
  };
  if (sciverseKey?.trim()) headers["X-Sciverse-Key"] = sciverseKey.trim();
  return handle<DailyReviewRunAccepted>(
    await doFetch(`${BASE}/daily-review/run-async`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    }),
  );
}

export async function smokeDailyReview(
  body: { topicId?: string; topicSlug?: string; topic?: string; paperCount: number; sinceYear?: number; freshnessBoost?: DailyReviewConfig["freshnessBoost"] },
  adminToken?: string,
): Promise<DailyReviewSmokeResult> {
  return handle<DailyReviewSmokeResult>(
    await doFetch(`${BASE}/daily-review/smoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders(adminToken) },
      body: JSON.stringify(body),
    }),
  );
}

export async function getHealth(): Promise<Health> {
  return handle<Health>(await doFetch(`${BASE}/healthz`));
}

export async function createCorpus(
  projectId: string,
  file: File,
  dbsource: DbSource,
): Promise<CorpusRef> {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("dbsource", dbsource);
  return handle<CorpusRef>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus`, { method: "POST", body: fd }),
  );
}

export async function getCorpus(projectId: string, corpusId: string): Promise<CorpusRef> {
  return handle<CorpusRef>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}`),
  );
}

// 接入响应可能附带匹配统计 (路径 B)
export type IngestRef = CorpusRef & { matched?: number; unmatched?: number; extracted?: number };

// 路径 A: 主题词 → OpenAlex 检索建库
export interface TopicReq { query: string; n?: number; since?: string; withRefs?: boolean }
export async function createFromTopic(projectId: string, req: TopicReq): Promise<IngestRef> {
  return handle<IngestRef>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/from-topic`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  );
}

// 路径 B: 粘贴参考文献 → LLM 抽取 + OpenAlex 反查建库
export async function createFromRefs(
  projectId: string,
  text: string,
  withRefs = true,
  llm?: LlmRequestInput,
): Promise<IngestRef> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...buildLlmHeaders(llm),
  };
  return handle<IngestRef>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/from-refs`, {
      method: "POST",
      headers,
      body: JSON.stringify({ text, withRefs }),
    }),
  );
}

// 路径 D: 一键加载内置演示数据 (取 web 同源静态文件 → 走上传接入)
export async function loadDemo(projectId: string): Promise<CorpusRef> {
  const res = await doFetch("/demo/demo_ipo_textual_50.txt");
  if (!res.ok) throw new ApiError("DEMO_MISSING", res.status, "演示数据文件缺失");
  const blob = await res.blob();
  const file = new File([blob], "demo_ipo_textual_50.txt", { type: "text/plain" });
  return createCorpus(projectId, file, "wos");
}

export async function getOverview(
  projectId: string,
  corpusId: string,
): Promise<OverviewResult> {
  return handle<OverviewResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/overview`),
  );
}

export async function getSources(projectId: string, corpusId: string): Promise<SourcesResult> {
  return handle<SourcesResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/sources`),
  );
}

export async function getAuthors(projectId: string, corpusId: string): Promise<AuthorsResult> {
  return handle<AuthorsResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/authors`),
  );
}

export async function getDocuments(projectId: string, corpusId: string): Promise<DocumentsResult> {
  return handle<DocumentsResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/documents`),
  );
}

// --- A4 高级图 (返回可用性信封; available:false 也是 HTTP 200) ---

export async function getAuthorProduction(
  projectId: string,
  corpusId: string,
): Promise<AuthorProductionEnvelope> {
  return handle<AuthorProductionEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/authors/production`),
  );
}

export async function getKeywordTrend(
  projectId: string,
  corpusId: string,
): Promise<KeywordTrendEnvelope> {
  return handle<KeywordTrendEnvelope>(
    await doFetch(
      `${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/documents/keyword-trend`,
    ),
  );
}

export async function getCitedRefs(
  projectId: string,
  corpusId: string,
): Promise<CitedRefsEnvelope> {
  return handle<CitedRefsEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/documents/cited-refs`),
  );
}

// --- A5 高级图② (返回可用性信封; available:false 也是 HTTP 200) ---

export async function getThematic(
  projectId: string,
  corpusId: string,
): Promise<ThematicEnvelope> {
  return handle<ThematicEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/conceptual/thematic`),
  );
}

export async function getEvolution(
  projectId: string,
  corpusId: string,
): Promise<EvolutionEnvelope> {
  return handle<EvolutionEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/conceptual/evolution`),
  );
}

export async function getHistcite(
  projectId: string,
  corpusId: string,
): Promise<HistciteEnvelope> {
  return handle<HistciteEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/intellectual/histcite`),
  );
}

export async function getThreeField(
  projectId: string,
  corpusId: string,
): Promise<ThreeFieldEnvelope> {
  return handle<ThreeFieldEnvelope>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/overview/threefield`),
  );
}

// 网络端点请求 top100 (A5 §4.4): 后端给到 100, 前端滑块才能真正切到 100。
export async function getConceptual(projectId: string, corpusId: string): Promise<NetworkResult> {
  return handle<NetworkResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/conceptual?limit=100`),
  );
}

export async function getIntellectual(projectId: string, corpusId: string): Promise<NetworkResult> {
  return handle<NetworkResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/intellectual?limit=100`),
  );
}

export async function getSocial(projectId: string, corpusId: string): Promise<SocialResult> {
  return handle<SocialResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/social?limit=100`),
  );
}

export async function buildPrisma(projectId: string, req: PrismaRequest): Promise<PrismaResult> {
  return handle<PrismaResult>(
    await doFetch(`${BASE}/projects/${enc(projectId)}/prisma`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    }),
  );
}

// A7: 报告格式 + 选项 (镜像 openapi ReportOptions)
export type ReportFormat = "md" | "html" | "docx";
export type ReportOptions = components["schemas"]["ReportOptions"];
export type ReportSection = NonNullable<ReportOptions["sections"]>[number];

export function reportUrl(projectId: string, corpusId: string, format: ReportFormat): string {
  return `${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/report?format=${format}`;
}

// 用 fetch 取 blob 再触发下载: 失败时能抛 ApiError 给 UI, 而非把用户带到 JSON 错误页 (Codex slice5-P2)
// A7: POST + ReportOptions (title/author/sections/可选 prismaCounts/reviewMarkdown); format 走 query。
export async function downloadReport(
  projectId: string,
  corpusId: string,
  format: ReportFormat,
  options?: ReportOptions,
): Promise<void> {
  const res = await doFetch(reportUrl(projectId, corpusId, format), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options ?? {}),
  });
  if (!res.ok) {
    await handle(res); // 抛 ApiError (含 503 PANDOC_UNAVAILABLE → UI 据此降级)
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `report.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

// --- 综述流式 (SSE over fetch; 需 POST body + X-LLM-Key 头, EventSource 做不到) ---
export type CiteSummary = { green: number; yellow: number; red: number };

export interface ReviewHandlers {
  onMeta?: (d: { template: string; chapters: string[]; docCount: number }) => void;
  onChapter?: (d: { index: number; title: string }) => void;
  onToken?: (text: string) => void;
  onCitations?: (d: { summary: CiteSummary; annotated: string }) => void;
  onDone?: (d: { chapters: number }) => void;
  onError?: (d: { code: string; message: string }) => void;
}

async function _postJson<T>(path: string, body: unknown, llm?: LlmRequestInput): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...buildLlmHeaders(llm),
  };
  return handle<T>(await doFetch(`${BASE}${path}`, { method: "POST", headers, body: JSON.stringify(body) }));
}

export function aiTranslate(p: string, text: string, direction: "en2zh" | "zh2en", llm?: LlmRequestInput) {
  return _postJson<TextResult>(`/projects/${enc(p)}/ai/translate`, { text, direction }, llm);
}
export function aiRewrite(p: string, text: string, action: string, llm?: LlmRequestInput) {
  return _postJson<TextResult>(`/projects/${enc(p)}/ai/rewrite`, { text, action }, llm);
}
export function aiSummary(p: string, text: string, llm?: LlmRequestInput) {
  return _postJson<TextResult>(`/projects/${enc(p)}/ai/summary`, { text }, llm);
}
export function aiScreen(p: string, c: string, topic: string, limit: number, llm?: LlmRequestInput) {
  return _postJson<ScreenResult>(`/projects/${enc(p)}/corpus/${enc(c)}/ai/screen`, { topic, limit }, llm);
}
export async function getCite(p: string, c: string, style: "gbt7714" | "apa" | "mla"): Promise<CiteResult> {
  return handle<CiteResult>(await doFetch(`${BASE}/projects/${enc(p)}/corpus/${enc(c)}/cite?style=${style}`));
}

// ============================================================
// W1: 文献库统计端点 (Task 5)
// ============================================================

export type LibraryStats = components["schemas"]["LibraryStats"];
export type ProjectLibraryStats = components["schemas"]["ProjectLibraryStats"];
export type OcrBreakdown = components["schemas"]["OcrBreakdown"];
export type InclusionBreakdown = components["schemas"]["InclusionBreakdown"];

export async function getLibraryStats(): Promise<LibraryStats> {
  return handle<LibraryStats>(await doFetch(`${BASE}/library/stats`));
}

export async function getProjectLibraryStats(pid: number): Promise<ProjectLibraryStats> {
  return handle<ProjectLibraryStats>(await doFetch(`${BASE}/projects/${pid}/library/stats`));
}

// ============================================================
// 项目管理 & SLR Agent 端点类型与函数 (P1-9)
// ============================================================

export interface Project {
  id: number;
  name: string;
  createdAt: string;
}

/**
 * M2: 项目当前 active corpus 摘要。
 * corpusId   — Postgres DB corpus.id（整数），物化/stale 重算时使用。
 * rCorpusId  — R 字符串 ID，调分析端点时透传；status != ready 时为 null。
 * stale      — 当前 included 集合与本 corpus 的 contentHash 不同 → 需重算。
 */
export interface ActiveCorpus {
  corpusId: number;
  rCorpusId: string | null;
  status: "parsing" | "ready" | "failed";
  documentCount: number;
  contentHash: string;
  stale: boolean;
}

export interface ProjectDetail {
  id: number;
  name: string;
  researchQuestion?: string;
  description?: string;
  paperCount: number;
  includedCount: number;
  /** M2: 项目当前 active corpus（最新 ready corpus；无则 null） */
  activeCorpus?: ActiveCorpus | null;
}

/**
 * M2: POST /projects/{pid}/corpus/materialize 响应体。
 * corpusId/rCorpusId 同 ActiveCorpus；rCorpusId 在 parsing/failed 时为 null。
 */
export interface CorpusMaterializeResult {
  corpusId: number;
  rCorpusId: string | null;
  status: "parsing" | "ready" | "failed";
  documentCount: number;
  contentHash: string;
}

export type InclusionStatus = "candidate" | "included" | "excluded" | "maybe";

export type ProjectPaperItem = components["schemas"]["ProjectPaperItem"];

// 作者既可能是纯字符串, 也可能是 CSL-JSON 对象 ({literal} 或 {family,given})
export type Creator = string | { family?: string; given?: string; literal?: string };

// PaperExtractionDto 和 PaperDetail 使用生成类型（消除手写漂移，B-fix）
export type PaperExtractionDto = components["schemas"]["PaperExtractionDto"];
export type PaperDetail = components["schemas"]["PaperDetail"];

export interface RunRef {
  runId: string;
  projectId: number;
  status: string;
}

export interface RunDetail {
  runId: string;
  status: string;
  roundsLog?: unknown[];
  finalOutput?: string;
  evidenceRefs?: unknown[];
}

// M2: 物化语料端点 — 从项目 included 论文构建 R 分析语料（幂等）
export async function materializeCorpus(pid: number): Promise<CorpusMaterializeResult> {
  return handle<CorpusMaterializeResult>(
    await doFetch(`${BASE}/projects/${pid}/corpus/materialize`, { method: "POST" }),
  );
}

export async function listProjects(): Promise<{ projects: Project[] }> {
  return handle<{ projects: Project[] }>(await doFetch(`${BASE}/projects`));
}

export async function createProject(body: {
  name: string;
  researchQuestion?: string;
  description?: string;
}): Promise<Project> {
  return handle<Project>(
    await doFetch(`${BASE}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function getProject(pid: number): Promise<ProjectDetail> {
  return handle<ProjectDetail>(await doFetch(`${BASE}/projects/${pid}`));
}

export async function getProjectPapers(pid: number): Promise<{ papers: ProjectPaperItem[] }> {
  return handle<{ papers: ProjectPaperItem[] }>(await doFetch(`${BASE}/projects/${pid}/papers`));
}

export async function getPaperDetail(pid: number, paperId: number): Promise<PaperDetail> {
  return handle<PaperDetail>(await doFetch(`${BASE}/projects/${pid}/papers/${paperId}`));
}

export async function patchInclusion(
  pid: number,
  paperId: number,
  body: { inclusionStatus: InclusionStatus; exclusionReason?: string; screeningScore?: number },
): Promise<ProjectPaperItem> {
  return handle<ProjectPaperItem>(
    await doFetch(`${BASE}/projects/${pid}/papers/${paperId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

// ---- M1: 文献导入端点 ----
export interface ImportResult {
  imported: number;
  skipped: number;
  failed: Array<{ name: string; reason: string }>;
  paperIds: number[];
}

export async function importPapers(
  pid: number,
  files: File[],
  defaultStatus: "candidate" | "included" | "excluded" | "maybe" = "candidate",
): Promise<ImportResult> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("default_status", defaultStatus);
  return handle<ImportResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/import`, { method: "POST", body: fd }),
  );
}

export async function listRuns(pid: number): Promise<{ runs: RunRef[] }> {
  return handle<{ runs: RunRef[] }>(await doFetch(`${BASE}/projects/${pid}/agent/runs`));
}

export async function createRun(
  pid: number,
  body: { prompt: string; autoConfirm?: boolean },
  llm?: LlmRequestInput,
): Promise<RunRef> {
  return handle<RunRef>(
    await doFetch(`${BASE}/projects/${pid}/agent/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...buildLlmHeaders(llm) },
      body: JSON.stringify(body),
    }),
  );
}

export async function getRun(pid: number, rid: string): Promise<RunDetail> {
  return handle<RunDetail>(await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}`));
}

// ============================================================
// P2-4: 检索候选 + from-search 入库
// ============================================================

/**
 * 来自 Agent SearchTool 的单条候选文献，字段与后端 SearchTool emit 事件一致。
 * candidate_id 是前端勾选/去重用的本地 ID（openalexId 或 hash）。
 */
export interface SearchCandidate {
  candidate_id: string;
  openalexId?: string | null;
  title: string;
  authors?: string[];
  year?: number | null;
  doi?: string | null;
  containerTitle?: string | null;
  url?: string | null;
  publicationDate?: string | null;
  abstract?: string | null;
  citedByCount?: number | null;
  source?: string | null;
  docId?: string | null;
  uniqueId?: string | null;
  snippet?: string | null;
  pageNo?: number | null;
  score?: number | null;
}

export type FromSearchResult = components["schemas"]["FromSearchResult"];

/**
 * POST /projects/{pid}/papers/from-search
 * 把选中候选批量入库。defaultStatus 控制 inclusion 状态。
 */
export async function addPapersFromSearch(
  pid: number,
  candidates: SearchCandidate[],
  defaultStatus: "candidate" | "included" = "candidate",
): Promise<FromSearchResult> {
  // 映射 SearchCandidate → FromSearchCandidate（schema 字段对齐）
  const mapped = candidates.map((c) => ({
    title: c.title,
    doi: c.doi ?? undefined,
    authors: c.authors ?? undefined,
    year: c.year ?? undefined,
    abstract: c.abstract ?? undefined,
    containerTitle: c.containerTitle ?? undefined,
    url: c.url ?? undefined,
    openalexId: c.openalexId ?? undefined,
    source: c.source ?? undefined,
    docId: c.docId ?? undefined,
    uniqueId: c.uniqueId ?? undefined,
    snippet: c.snippet ?? undefined,
    pageNo: c.pageNo ?? undefined,
    score: c.score ?? undefined,
  }));
  return handle<FromSearchResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/from-search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ candidates: mapped, defaultStatus }),
    }),
  );
}

// P3-T2: 元数据补全端点
export type BackfillMetadataResult = components["schemas"]["BackfillMetadataResult"];

export async function backfillMetadata(
  pid: number,
  opts: { limit?: number; onlyMissing?: boolean } = {},
  llm?: LlmRequestInput,
): Promise<BackfillMetadataResult> {
  return handle<BackfillMetadataResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/backfill-metadata`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...buildLlmHeaders(llm) },
      body: JSON.stringify({ limit: opts.limit ?? 20, onlyMissing: opts.onlyMissing ?? true }),
    }),
  );
}

// P3-T3/T4: 结构化抽取端点
export type ExtractStructuredResult = components["schemas"]["ExtractStructuredResult"];

export async function extractStructured(
  pid: number,
  opts: { limit?: number; reextract?: boolean } = {},
  llm?: LlmRequestInput,
): Promise<ExtractStructuredResult> {
  return handle<ExtractStructuredResult>(
    await doFetch(`${BASE}/projects/${pid}/papers/extract-structured`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...buildLlmHeaders(llm) },
      body: JSON.stringify({ limit: opts.limit ?? 15, reextract: opts.reextract ?? false }),
    }),
  );
}

// P2-3: 写确认端点。awaiting_confirmation 时由 UI 调用 → 后端在同一条已打开的流上继续发后续事件。
export async function confirmRun(
  pid: number,
  rid: string,
  body: { toolCallId: string; decision: "approve" | "reject" },
): Promise<{ status: string }> {
  return handle<{ status: string }>(
    await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

// P2-3: 可验证运行日志 (runlog/v1)。返回 RunLog JSON, 由 UI 序列化后触发浏览器下载。
export async function getRunLog(pid: number, rid: string): Promise<unknown> {
  return handle<unknown>(await doFetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}/runlog`));
}

// ============================================================
// M4: 工件 (Artifact) CRUD
// ============================================================

export interface ArtifactItem {
  id: number;
  projectId: number;
  runId?: number | null;
  type: string; // review|analysis|extraction|paperset
  title: string;
  sourceEventSeq?: number | null;
  contentRef?: string | null;
  pinned: boolean;
  userAnnotation?: string | null;
  order: number;
  createdAt?: string | null;
}

export interface ArtifactCreateBody {
  type?: string;
  title?: string;
  runId?: number | null;
  sourceEventSeq?: number | null;
  contentRef?: string | null;
  pinned?: boolean;
  userAnnotation?: string | null;
  order?: number;
}

export interface ArtifactPatchBody {
  title?: string;
  pinned?: boolean;
  userAnnotation?: string | null;
  order?: number;
}

export async function listArtifacts(
  pid: number,
  pinned?: boolean,
): Promise<{ artifacts: ArtifactItem[] }> {
  const q = pinned !== undefined ? `?pinned=${pinned}` : "";
  return handle<{ artifacts: ArtifactItem[] }>(
    await doFetch(`${BASE}/projects/${pid}/artifacts${q}`),
  );
}

export async function createArtifact(
  pid: number,
  body: ArtifactCreateBody,
): Promise<ArtifactItem> {
  return handle<ArtifactItem>(
    await doFetch(`${BASE}/projects/${pid}/artifacts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function patchArtifact(
  pid: number,
  aid: number,
  body: ArtifactPatchBody,
): Promise<ArtifactItem> {
  return handle<ArtifactItem>(
    await doFetch(`${BASE}/projects/${pid}/artifacts/${aid}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function deleteArtifact(pid: number, aid: number): Promise<void> {
  const res = await doFetch(`${BASE}/projects/${pid}/artifacts/${aid}`, { method: "DELETE" });
  if (!res.ok) {
    await handle(res);
  }
}

// ============================================================
// Agent Run SSE 流事件类型 (P1-10)
// ============================================================

export interface AgentRunStartEvent {
  type: "run_start";
  max_rounds: number;
  model: string;
  seq: number;
}

export interface AgentLlmStartEvent {
  type: "llm_start";
  round: number;
  is_final: boolean;
  context_tokens: number;
  seq: number;
}

export interface AgentToolCall {
  id: string;
  name: string;
  args_preview: string;
}

export interface AgentToolsStartEvent {
  type: "tools_start";
  round: number;
  thinking: string;
  tool_calls: AgentToolCall[];
  seq: number;
}

export interface AgentToolResult {
  tool_id: string;
  action: string;
  success: boolean;
  summary: string;
  data_source?: string;
  error?: string;
}

export interface AgentRoundCompleteEvent {
  type: "round_complete";
  round: number;
  thinking: string;
  tool_calls: AgentToolCall[];
  tool_results: AgentToolResult[];
  is_final: boolean;
  seq: number;
}

export interface AgentRunCompleteEvent {
  type: "run_complete";
  status: string;
  final_output: string;
  seq: number;
}

export interface AgentErrorEvent {
  type: "error";
  error: string;
  seq: number;
}

// P3-1: 运行生命周期事件。paused/resumed 为非终态信息事件（SSE 不据此关流）；
// cancelled 为终态（SSE 收到即关流）。
export interface AgentPausedEvent {
  type: "paused";
  status: string;
  seq: number;
}

export interface AgentResumedEvent {
  type: "resumed";
  status: string;
  seq: number;
}

export interface AgentCancelledEvent {
  type: "cancelled";
  status: string;
  seq: number;
}

// P2-3: 写工具需确认时发出此信号。非终态——SSE 流保持打开, 等用户批准/拒绝。
export interface AgentToolConfirmRequiredEvent {
  type: "tool_confirm_required";
  toolCallId: string;
  toolId: string;
  action: string;
  argsPreview: string;
  seq: number;
}

// P2-4: SearchTool emit 的检索候选事件。非终态——SSE 流继续。
export interface AgentSearchResultsEvent {
  type: "search_results";
  candidates: SearchCandidate[];
  query: string;
  seq: number;
}

export type AgentSseEvent =
  | AgentRunStartEvent
  | AgentLlmStartEvent
  | AgentToolsStartEvent
  | AgentRoundCompleteEvent
  | AgentRunCompleteEvent
  | AgentErrorEvent
  | AgentPausedEvent
  | AgentResumedEvent
  | AgentCancelledEvent
  | AgentToolConfirmRequiredEvent
  | AgentSearchResultsEvent;

export interface AgentRunHandlers {
  onRunStart?: (d: AgentRunStartEvent) => void;
  onLlmStart?: (d: AgentLlmStartEvent) => void;
  onToolsStart?: (d: AgentToolsStartEvent) => void;
  onRoundComplete?: (d: AgentRoundCompleteEvent) => void;
  onRunComplete?: (d: AgentRunCompleteEvent) => void;
  onError?: (d: AgentErrorEvent) => void;
  onPaused?: (d: AgentPausedEvent) => void;
  onResumed?: (d: AgentResumedEvent) => void;
  onCancelled?: (d: AgentCancelledEvent) => void;
  onToolConfirmRequired?: (d: AgentToolConfirmRequiredEvent) => void;
  /** P2-4: 收到检索候选事件（非终态，流继续）*/
  onSearchResults?: (d: AgentSearchResultsEvent) => void;
}

export async function streamAgentRun(
  pid: number,
  rid: string,
  opts: { lastEventId?: number; signal?: AbortSignal },
  handlers: AgentRunHandlers,
): Promise<void> {
  const headers: Record<string, string> = {};
  if (opts.lastEventId !== undefined) {
    headers["Last-Event-ID"] = String(opts.lastEventId);
  }
  let res: Response;
  try {
    res = await fetch(`${BASE}/projects/${pid}/agent/runs/${enc(rid)}/events`, {
      headers,
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof Error && e.name === "AbortError") throw e;
    throw new ApiError("NETWORK_ERROR", 0, (e as Error).message || "网络错误");
  }

  // 修复2: 先检查 res.ok。非 2xx（如 404 RUN_NOT_FOUND）走 onError，不当 SSE 处理。
  if (!res.ok) {
    let body: { code?: string; message?: string } = {};
    try {
      body = await res.json();
    } catch {
      /* 非 JSON 错误体 */
    }
    const errEvt: AgentErrorEvent = {
      type: "error",
      error: body.message ?? res.statusText,
      seq: -1,
    };
    handlers.onError?.(errEvt);
    return;
  }

  if (!res.body) {
    const errEvt: AgentErrorEvent = { type: "error", error: "响应无 body", seq: -1 };
    handlers.onError?.(errEvt);
    return;
  }

  // 修复2/3: 跟踪终态事件和最大 seq
  let receivedTerminal = false;
  let lastSeq = opts.lastEventId ?? -1;

  try {
    await consumeSse(res, (event, data) => {
      // 忽略心跳注释帧（consumeSse 已过滤无 data 的帧，此处防御性过滤空 event）
      if (!data) return;

      let parsed: AgentSseEvent;
      try {
        parsed = JSON.parse(data) as AgentSseEvent;
      } catch {
        return;
      }

      // 修复2: 用 data 里的 seq 记录最大 lastEventId，供潜在重连
      if (typeof parsed.seq === "number" && parsed.seq > lastSeq) {
        lastSeq = parsed.seq;
      }

      switch (event) {
        case "run_start":
          handlers.onRunStart?.(parsed as AgentRunStartEvent);
          break;
        case "llm_start":
          handlers.onLlmStart?.(parsed as AgentLlmStartEvent);
          break;
        case "tools_start":
          handlers.onToolsStart?.(parsed as AgentToolsStartEvent);
          break;
        case "round_complete":
          handlers.onRoundComplete?.(parsed as AgentRoundCompleteEvent);
          break;
        case "run_complete":
          receivedTerminal = true;
          handlers.onRunComplete?.(parsed as AgentRunCompleteEvent);
          break;
        case "error":
          receivedTerminal = true;
          handlers.onError?.(parsed as AgentErrorEvent);
          break;
        // P3-1: paused/resumed 非终态（流保持打开，等 resume）；cancelled 终态（关流）。
        case "paused":
          handlers.onPaused?.(parsed as AgentPausedEvent);
          break;
        case "resumed":
          handlers.onResumed?.(parsed as AgentResumedEvent);
          break;
        case "cancelled":
          receivedTerminal = true;
          handlers.onCancelled?.(parsed as AgentCancelledEvent);
          break;
        // P2-3: 写确认信号为非终态——不改 receivedTerminal, 流保持打开等待 confirm。
        case "tool_confirm_required":
          handlers.onToolConfirmRequired?.(parsed as AgentToolConfirmRequiredEvent);
          break;
        // P2-4: 检索候选事件——非终态，流继续；候选渲染在 AgentChat 侧处理。
        case "search_results":
          handlers.onSearchResults?.(parsed as AgentSearchResultsEvent);
          break;
      }
    });
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof Error && e.name === "AbortError") throw e;
    throw new ApiError("STREAM_ERROR", 0, (e as Error)?.message || "流中断");
  }

  // 修复3: 流结束但从未收到终态事件 → 通知 UI 连接中断
  if (!receivedTerminal) {
    const errEvt: AgentErrorEvent = {
      type: "error",
      error: "连接中断，运行可能未完成",
      seq: lastSeq + 1,
    };
    handlers.onError?.(errEvt);
  }
}

// ============================================================

// 通用 SSE 帧读取 (chat/review 共用)
// onFrame(event, data, id?) — id 由 "id:" 行解析，保持现有调用兼容
async function consumeSse(res: Response, onFrame: (event: string, data: string, id?: string) => void): Promise<void> {
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let event = "message";
      let frameId: string | undefined;
      const dl: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dl.push(line.slice(5).trim());
        else if (line.startsWith("id:")) frameId = line.slice(3).trim();
      }
      if (dl.length) onFrame(event, dl.join("\n"), frameId);
    }
  }
}

export async function streamChat(
  projectId: string,
  corpusId: string,
  req: { query: string; history: ChatMessage[] },
  opts: LlmRequestInput,
  handlers: { onToken?: (t: string) => void; onDone?: () => void; onError?: (d: { code: string; message: string }) => void },
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...buildLlmHeaders(opts) };
  const res = await doFetch(
    `${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/ai/chat`,
    { method: "POST", headers, body: JSON.stringify(req) },
  );
  if (!res.ok || !res.body) {
    await handle(res);
    return;
  }
  let captured: { code: string; message: string } | null = null;
  try {
    await consumeSse(res, (event, data) => {
      let parsed: Record<string, unknown> = {};
      try {
        parsed = JSON.parse(data);
      } catch {
        parsed = {};
      }
      if (event === "token") handlers.onToken?.((parsed as { text: string }).text ?? "");
      else if (event === "done") handlers.onDone?.();
      else if (event === "error") {
        captured = parsed as { code: string; message: string };
        handlers.onError?.(captured);
      }
    });
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError("STREAM_ERROR", 0, (e as Error)?.message || "流中断");
  }
  if (captured) throw new ApiError((captured as { code: string }).code, 0, (captured as { message: string }).message);
}

function dispatchSse(event: string, data: string, h: ReviewHandlers) {
  let parsed: unknown = {};
  try {
    parsed = JSON.parse(data);
  } catch {
    parsed = {};
  }
  switch (event) {
    case "meta": h.onMeta?.(parsed as never); break;
    case "chapter": h.onChapter?.(parsed as never); break;
    case "token": h.onToken?.((parsed as { text: string }).text ?? ""); break;
    case "citations": h.onCitations?.(parsed as never); break;
    case "done": h.onDone?.(parsed as never); break;
    case "error": h.onError?.(parsed as never); break;
  }
}

export async function streamReview(
  projectId: string,
  corpusId: string,
  req: { type: string; topic: string },
  opts: (LlmRequestOptions & { signal?: AbortSignal }) | ({ signal?: AbortSignal } & { apiKey?: string; baseUrl?: string; model?: string }) | string | undefined,
  handlers: ReviewHandlers,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...buildLlmHeaders(opts) };
  const signal = typeof opts === "string" ? undefined : opts?.signal;
  const res = await doFetch(
    `${BASE}/projects/${enc(projectId)}/corpus/${enc(corpusId)}/review`,
    { method: "POST", headers, body: JSON.stringify(req), signal },
  );
  if (!res.ok || !res.body) {
    await handle(res); // 非 200 → 抛 ApiError
    return;
  }
  // 捕获 error 事件: 仍回调给 UI, 但流结束后让 streamReview reject (Codex slice2-P2)
  let captured: { code: string; message: string } | null = null;
  const wrapped: ReviewHandlers = {
    ...handlers,
    onError: (d) => {
      captured = d;
      handlers.onError?.(d);
    },
  };
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) dispatchSse(event, dataLines.join("\n"), wrapped);
    }
  }
  if (captured) {
    throw new ApiError((captured as { code: string }).code, 0, (captured as { message: string }).message);
  }
}
