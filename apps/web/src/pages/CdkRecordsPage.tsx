import { useEffect, useMemo, useRef, useState } from "react";
import {
  AdminLiteratureSearchFilters,
  ApiError,
  DailyReviewConfig,
  LiteratureSearchSummary,
  getAdminLiteratureSearches,
  getDailyReviewConfig,
} from "../api/client";

const ADMIN_TOKEN_KEY = "frontier_review_admin_token";

function formatTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function sourceLabel(value?: LiteratureSearchSummary["sourceType"]) {
  if (value === "cdk") return "CDK";
  if (value === "own_llm") return "用户自带 LLM";
  return "未知";
}

function statusLabel(value?: string | null) {
  if (value === "success") return "成功";
  if (value === "running") return "运行中";
  if (value === "queued") return "排队中";
  if (value === "error") return "失败";
  return value || "未知";
}

function buildShareUrl(path: string) {
  if (typeof window === "undefined") return path;
  return `${window.location.origin}${path.startsWith("/") ? path : `/${path}`}`;
}

export function CdkRecordsPage() {
  const [adminToken] = useState(() => localStorage.getItem(ADMIN_TOKEN_KEY) || "");
  const [config, setConfig] = useState<DailyReviewConfig | null>(null);
  const [records, setRecords] = useState<LiteratureSearchSummary[]>([]);
  const [filters, setFilters] = useState<AdminLiteratureSearchFilters>({ scope: "all", limit: 300 });
  const [loading, setLoading] = useState(Boolean(adminToken));
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const requestRef = useRef(0);

  const cdks = config?.literatureSearchCdks ?? [];
  const stats = useMemo(() => {
    const cdk = records.filter((item) => item.sourceType === "cdk").length;
    const own = records.filter((item) => item.sourceType === "own_llm").length;
    const success = records.filter((item) => item.status === "success").length;
    const failed = records.filter((item) => item.status === "error").length;
    return { total: records.length, cdk, own, success, failed };
  }, [records]);

  useEffect(() => {
    document.title = "YFR 检索记录";
  }, []);

  useEffect(() => {
    if (!adminToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    getDailyReviewConfig(adminToken)
      .then((data) => {
        if (alive) setConfig(data);
      })
      .catch((e) => {
        if (alive) setError(e instanceof ApiError ? e.message : "读取管理员配置失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [adminToken]);

  function loadRecords(nextFilters = filters) {
    if (!adminToken) return;
    const requestId = ++requestRef.current;
    setRecordsLoading(true);
    setError("");
    getAdminLiteratureSearches(adminToken, nextFilters)
      .then((data) => {
        if (requestRef.current === requestId) setRecords(data.items);
      })
      .catch((e) => {
        if (requestRef.current === requestId) setError(e instanceof ApiError ? e.message : "读取检索记录失败");
      })
      .finally(() => {
        if (requestRef.current === requestId) setRecordsLoading(false);
      });
  }

  useEffect(() => {
    if (adminToken && !loading) loadRecords(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminToken, loading]);

  function patchFilters(patch: Partial<AdminLiteratureSearchFilters>) {
    setFilters((old) => {
      const next = { ...old, ...patch };
      if (patch.scope === "own_llm") next.cdkId = "";
      return next;
    });
  }

  function submitFilters() {
    loadRecords(filters);
  }

  function resetFilters() {
    const next: AdminLiteratureSearchFilters = { scope: "all", limit: 300 };
    setFilters(next);
    loadRecords(next);
  }

  async function copyRecord(item: LiteratureSearchSummary) {
    await navigator.clipboard?.writeText(buildShareUrl(item.sharePath));
    setStatus("链接已复制");
    window.setTimeout(() => setStatus(""), 1800);
  }

  if (!adminToken) {
    return (
      <main className="daily-page standalone">
        <div className="daily-loading">请先登录管理员后台，再访问检索记录。</div>
      </main>
    );
  }

  if (loading && !config) {
    return (
      <main className="daily-page standalone">
        <div className="daily-loading">正在读取检索记录配置...</div>
      </main>
    );
  }

  return (
    <main className="daily-page standalone cdk-admin-page cdk-records-page">
      <section className="daily-hero">
        <div>
          <p className="daily-kicker">YFR Admin</p>
          <h1>检索记录</h1>
          <p className="daily-subtitle">集中审计公开文献检索记录，支持按 CDK、用户自带 LLM、主题、状态、来源和时间范围筛选。</p>
        </div>
        <div className="daily-hero-metrics">
          <span><strong>{stats.total}</strong>记录</span>
          <span><strong>{stats.cdk}</strong>CDK</span>
          <span><strong>{stats.own}</strong>自带 LLM</span>
          <span><strong>{stats.success}</strong>成功</span>
          <span><strong>{stats.failed}</strong>失败</span>
        </div>
      </section>

      <section className="daily-admin-page cdk-records-layout">
        <div className="daily-admin-card cdk-record-filter-card">
          <div className="daily-panel-title">
            <span>筛选条件</span>
            <div className="cdk-actions compact">
              <button className="btn btn-ghost daily-test-btn" type="button" onClick={resetFilters}>重置</button>
              <button className="btn daily-test-btn" type="button" onClick={submitFilters} disabled={recordsLoading}>
                {recordsLoading ? "查询中..." : "查询"}
              </button>
              <a className="btn btn-ghost daily-test-btn" href="/admin/CDK">返回 CDK 管理</a>
            </div>
          </div>
          <div className="daily-grid-2 cdk-record-filter-grid">
            <label>查看范围
              <select value={filters.scope ?? "all"} onChange={(e) => patchFilters({ scope: e.target.value as AdminLiteratureSearchFilters["scope"] })}>
                <option value="all">全部检索</option>
                <option value="cdk">CDK 检索</option>
                <option value="own_llm">用户自带 LLM</option>
              </select>
            </label>
            <label>CDK
              <select
                value={filters.cdkId ?? ""}
                disabled={filters.scope === "own_llm"}
                onChange={(e) => patchFilters({ cdkId: e.target.value, scope: e.target.value ? "cdk" : filters.scope })}
              >
                <option value="">不限 CDK</option>
                {cdks.map((cdk) => <option key={cdk.id} value={cdk.id}>{cdk.name}</option>)}
              </select>
            </label>
            <label>主题关键词
              <input value={filters.q ?? ""} onChange={(e) => patchFilters({ q: e.target.value })} placeholder="输入主题、searchId、CDK 名称" />
            </label>
            <label>状态
              <select value={filters.status ?? ""} onChange={(e) => patchFilters({ status: e.target.value })}>
                <option value="">不限状态</option>
                <option value="success">成功</option>
                <option value="running">运行中</option>
                <option value="queued">排队中</option>
                <option value="error">失败</option>
                <option value="unknown">未知</option>
              </select>
            </label>
            <label>检索源
              <select value={filters.provider ?? ""} onChange={(e) => patchFilters({ provider: e.target.value })}>
                <option value="">不限来源</option>
                <option value="hybrid">Hybrid 混合检索</option>
                <option value="paper_search">Paper Search</option>
                <option value="sciverse">Sciverse</option>
              </select>
            </label>
            <label>返回数量
              <input type="number" min={1} max={1000} value={filters.limit ?? 300} onChange={(e) => patchFilters({ limit: Number(e.target.value) || 300 })} />
            </label>
            <label>起始年份从
              <input type="number" min={1900} max={2100} value={filters.sinceYearFrom ?? ""} onChange={(e) => patchFilters({ sinceYearFrom: e.target.value })} />
            </label>
            <label>起始年份到
              <input type="number" min={1900} max={2100} value={filters.sinceYearTo ?? ""} onChange={(e) => patchFilters({ sinceYearTo: e.target.value })} />
            </label>
            <label>创建时间从
              <input type="datetime-local" value={filters.createdFrom ?? ""} onChange={(e) => patchFilters({ createdFrom: e.target.value })} />
            </label>
            <label>创建时间到
              <input type="datetime-local" value={filters.createdTo ?? ""} onChange={(e) => patchFilters({ createdTo: e.target.value })} />
            </label>
          </div>
          {error && <p className="daily-status bad">{error}</p>}
          {status && <p className="daily-status ok">{status}</p>}
        </div>

        <div className="daily-admin-card cdk-record-results-card">
          <div className="daily-panel-title">
            <span>记录列表</span>
            <small>{recordsLoading ? "正在刷新..." : `共 ${records.length} 条`}</small>
          </div>
          <div className="cdk-record-table">
            <div className="cdk-record-head">
              <span>主题</span>
              <span>来源</span>
              <span>数量</span>
              <span>范围</span>
              <span>状态</span>
              <span>时间</span>
              <span>操作</span>
            </div>
            {records.map((item) => (
              <article key={item.searchId} className="cdk-record-row">
                <div className="cdk-record-topic">
                  <strong>{item.topic}</strong>
                  <small>{item.searchId}</small>
                </div>
                <div data-label="来源">
                  <span className={`cdk-source-pill ${item.sourceType || "unknown"}`}>{sourceLabel(item.sourceType)}</span>
                  {item.cdkName && <small>{item.cdkName}</small>}
                  <small>{item.literatureProvider || "-"}</small>
                </div>
                <span data-label="数量">{item.returned}/{item.requested}</span>
                <span data-label="范围">{item.sinceYear ? `${item.sinceYear} 年以来` : "-"}</span>
                <span data-label="状态"><span className={`cdk-status-pill ${item.status}`}>{statusLabel(item.status)}</span></span>
                <span data-label="时间">{formatTime(item.createdAt || item.updatedAt)}</span>
                <div className="cdk-record-actions">
                  <a className="btn btn-ghost daily-test-btn" href={item.sharePath}>查看</a>
                  <button className="btn btn-ghost daily-test-btn" type="button" onClick={() => void copyRecord(item)}>复制</button>
                </div>
              </article>
            ))}
            {!recordsLoading && !records.length && <p className="daily-hint">没有匹配的检索记录。可以放宽筛选条件后重试。</p>}
          </div>
        </div>
      </section>
    </main>
  );
}
