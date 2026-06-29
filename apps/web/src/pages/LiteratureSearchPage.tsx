import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  LiteratureCdkPublicInfo,
  LiteratureOnlySearchResult,
  LiteratureSearchProgressItem,
  getLiteratureCdkStatus,
  getLiteratureSearchProgress,
  getLiteratureSearchResult,
  startLiteratureSearch,
} from "../api/client";

type AuthMode = "cdk" | "own";

function currentSinceYear() {
  return new Date().getFullYear() - 10;
}

function scoreText(value?: number | null) {
  return typeof value === "number" ? String(value) : "-";
}

function sourceText(value?: string[] | null) {
  return value?.length ? value.join(" / ") : "unknown";
}

function statusLabel(status?: LiteratureSearchProgressItem["status"]) {
  if (status === "queued") return "排队";
  if (status === "running") return "检索中";
  if (status === "success") return "已完成";
  if (status === "error") return "失败";
  return "等待";
}

function formatTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function LiteratureSearchPage() {
  const navigate = useNavigate();
  const { searchId } = useParams();
  const [topic, setTopic] = useState("土木工程智能结构设计");
  const [paperCount, setPaperCount] = useState(50);
  const [sinceYear, setSinceYear] = useState(currentSinceYear());
  const [authMode, setAuthMode] = useState<AuthMode>("cdk");
  const [cdk, setCdk] = useState("");
  const [cdkInfo, setCdkInfo] = useState<LiteratureCdkPublicInfo | null>(null);
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("gpt-4o-mini");
  const [loading, setLoading] = useState(false);
  const [checkingCdk, setCheckingCdk] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<LiteratureOnlySearchResult | null>(null);
  const [activeSearchId, setActiveSearchId] = useState(searchId || "");
  const [progress, setProgress] = useState<LiteratureSearchProgressItem | null>(null);
  const [logs, setLogs] = useState<string[]>([]);

  const visibleLimit = useMemo(() => cdkInfo?.paperCountMax ?? 200, [cdkInfo]);
  const shareUrl = useMemo(() => {
    const id = result?.searchId || progress?.searchId || activeSearchId;
    if (!id || typeof window === "undefined") return "";
    return `${window.location.origin}/literature-search/${id}`;
  }, [activeSearchId, progress?.searchId, result?.searchId]);

  function appendLog(item: LiteratureSearchProgressItem) {
    const text = `${formatTime(item.updatedAt)} · ${item.stage} · ${item.message}`;
    setLogs((old) => (old[old.length - 1] === text ? old : [...old.slice(-20), text]));
  }

  useEffect(() => {
    if (!searchId) return;
    setActiveSearchId(searchId);
    setLoading(true);
    setError("");
    getLiteratureSearchResult(searchId)
      .then((data) => {
        setResult(data.result);
        if (data.progress) {
          setProgress(data.progress);
          appendLog(data.progress);
        }
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "读取检索记录失败。"))
      .finally(() => setLoading(false));
  }, [searchId]);

  useEffect(() => {
    if (!activeSearchId || result) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      getLiteratureSearchProgress(activeSearchId)
        .then((data) => {
          if (cancelled) return;
          setProgress(data.progress);
          appendLog(data.progress);
          if (data.progress.status === "success") {
            return getLiteratureSearchResult(activeSearchId).then((stored) => {
              if (!cancelled) {
                setResult(stored.result);
                setLoading(false);
              }
            });
          }
          if (data.progress.status === "error") {
            setLoading(false);
            setError(data.progress.error || data.progress.message || "检索失败。");
          }
        })
        .catch(() => {
          /* 轮询偶发失败不打断任务。 */
        });
    }, 1600);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeSearchId, result]);

  async function handleCheckCdk() {
    setError("");
    setCdkInfo(null);
    if (!cdk.trim()) {
      setError("请输入 CDK。");
      return;
    }
    setCheckingCdk(true);
    try {
      const res = await getLiteratureCdkStatus(cdk.trim());
      if (!res.ok || !res.cdk) {
        setError(res.message || "CDK 不可用。");
        return;
      }
      setCdkInfo(res.cdk);
      if (paperCount > res.cdk.paperCountMax) setPaperCount(res.cdk.paperCountMax);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "CDK 校验失败。");
    } finally {
      setCheckingCdk(false);
    }
  }

  async function handleSearch() {
    setError("");
    setResult(null);
    setProgress(null);
    setLogs([]);
    const cleanTopic = topic.trim();
    if (cleanTopic.length < 2) {
      setError("请输入更明确的文献主题。");
      return;
    }
    if (authMode === "cdk" && !cdk.trim()) {
      setError("请输入管理员提供的 CDK，或切换为自己的 LLM 服务。");
      return;
    }
    if (authMode === "own" && (!apiKey.trim() || !baseUrl.trim() || !model.trim())) {
      setError("使用自己的 LLM 时，需要填写 Base URL、API Key 和模型名。");
      return;
    }
    setLoading(true);
    try {
      const accepted = await startLiteratureSearch({
        topic: cleanTopic,
        paperCount: Math.max(5, Math.min(visibleLimit, paperCount)),
        sinceYear,
        cdk: authMode === "cdk" ? cdk.trim() : null,
        llm: authMode === "own" ? { baseUrl: baseUrl.trim(), apiKey: apiKey.trim(), model: model.trim(), temperature: 0, maxTokens: 1000 } : null,
      });
      setActiveSearchId(accepted.searchId);
      setProgress(accepted.progress);
      appendLog(accepted.progress);
      navigate(accepted.sharePath, { replace: false });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "文献检索失败。");
      setLoading(false);
    }
  }

  return (
    <main className="literature-page">
      <section className="literature-hero">
        <div>
          <p className="literature-kicker">YFR · 研域前沿综述</p>
          <h1>检索文献</h1>
          <p>输入研究主题，系统会调用 LLM 做主题拆解，再合并 Hybrid 多源文献结果，返回去重后的高质量证据列表。每次检索都会生成独立路径，便于回看和分享。</p>
          <div className="literature-links">
            <a href="/daily-review">查看前沿日报</a>
            <a href="https://github.com/yyy-OPS/YFR.frontier-review-daily" target="_blank" rel="noreferrer">GitHub 开源地址</a>
          </div>
        </div>
        <div className="literature-hero-metrics">
          <strong>质量优先</strong>
          <span>DOI / 标题 / uniqueId 去重</span>
          <span>相关性 · 质量 · 证据完整度评分</span>
          <span>支持管理员 CDK 或自带 LLM</span>
        </div>
      </section>

      <section className="literature-workbench">
        <aside className="literature-form">
          <label>文献主题<textarea rows={4} value={topic} onChange={(e) => setTopic(e.target.value)} /></label>
          <div className="literature-grid">
            <label>文献数量<input type="number" min={5} max={visibleLimit} value={paperCount} onChange={(e) => setPaperCount(Number(e.target.value) || 50)} /></label>
            <label>起始年份<input type="number" min={1900} max={new Date().getFullYear()} value={sinceYear} onChange={(e) => setSinceYear(Number(e.target.value) || currentSinceYear())} /></label>
          </div>
          <div className="literature-auth-switch" role="tablist" aria-label="LLM 使用方式">
            <button type="button" className={authMode === "cdk" ? "active" : ""} onClick={() => setAuthMode("cdk")}>管理员 CDK</button>
            <button type="button" className={authMode === "own" ? "active" : ""} onClick={() => setAuthMode("own")}>自己的 LLM</button>
          </div>
          {authMode === "cdk" ? (
            <div className="literature-auth-box">
              <label>CDK<input type="password" value={cdk} onChange={(e) => setCdk(e.target.value)} placeholder="输入管理员提供的 CDK" /></label>
              <button type="button" className="btn btn-ghost" onClick={() => void handleCheckCdk()} disabled={checkingCdk}>{checkingCdk ? "校验中..." : "查看 CDK 状态"}</button>
              {cdkInfo && (
                <div className="literature-cdk-info">
                  <strong>{cdkInfo.name}</strong>
                  <span>剩余 {cdkInfo.remainingUses}/{cdkInfo.maxUses} 次 · 单次最多 {cdkInfo.paperCountMax} 篇</span>
                  <span>有效期：{cdkInfo.expiresAt || "长期有效"}</span>
                  <span>检索源：{cdkInfo.literatureProvider || "跟随管理员全局配置"}</span>
                  {cdkInfo.note && <span>{cdkInfo.note}</span>}
                </div>
              )}
            </div>
          ) : (
            <div className="literature-auth-box">
              <label>Base URL<input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>
              <label>API Key<input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-..." /></label>
              <label>模型名<input value={model} onChange={(e) => setModel(e.target.value)} /></label>
            </div>
          )}
          {error && <p className="literature-error">{error}</p>}
          <button className="literature-run" type="button" onClick={() => void handleSearch()} disabled={loading}>
            {loading ? "正在检索与评分..." : "开始检索文献"}
          </button>
        </aside>

        <section className="literature-results">
          <div className={`literature-progress-panel ${progress?.mode === "indeterminate" ? "indeterminate" : ""}`}>
            <div className="literature-progress-head">
              <div>
                <strong>{progress ? statusLabel(progress.status) : "等待检索"}</strong>
                <span>{progress?.stage || "准备"}</span>
              </div>
              {shareUrl && <button type="button" className="btn btn-ghost" onClick={() => navigator.clipboard?.writeText(shareUrl)}>复制分享路径</button>}
            </div>
            <div className="literature-progress-bar" aria-label={progress ? `${progress.stage} ${progress.percent}%` : "等待检索"}>
              <i style={progress?.mode === "indeterminate" ? undefined : { width: `${progress?.percent ?? 0}%` }} />
            </div>
            <p>{progress?.message || "输入主题后开始检索，进度会在这里实时刷新。"}</p>
            <small>{progress ? `${progress.current || 0}${progress.total ? ` / ${progress.total}` : ""} · 更新 ${formatTime(progress.updatedAt)}${progress.detail ? ` · ${progress.detail}` : ""}` : "尚未创建检索任务"}</small>
            {logs.length > 0 && (
              <div className="literature-log-list">
                {logs.map((item) => <span key={item}>{item}</span>)}
              </div>
            )}
          </div>

          {!result && (
            <div className="literature-empty">
              <strong>{loading ? "检索进行中" : "等待检索"}</strong>
              <span>{loading ? "系统正在合并多源文献、去重、验证链接并计算证据分数。" : "结果会展示来源、DOI、摘要、PDF 状态和三项质量分数。"}</span>
            </div>
          )}
          {result && (
            <>
              <div className="literature-result-head">
                <div>
                  <p className="literature-kicker">检索完成</p>
                  <h2>{result.topic}</h2>
                  <span>{result.returned}/{result.requested} 篇 · {result.sinceYear} 年以来 · {result.literatureProvider}</span>
                  {shareUrl && <code>{shareUrl}</code>}
                </div>
                <a href="#literature-paper-list">跳到文献列表</a>
              </div>
              <div id="literature-paper-list" className="literature-paper-list">
                {result.papers.map((paper, index) => (
                  <article key={paper.id || `${paper.title}-${index}`} className="literature-paper-card">
                    <div className="literature-paper-top">
                      <span>[{index + 1}]</span>
                      <div>
                        <h3>{paper.title}</h3>
                        <p>{paper.authors?.slice(0, 6).join(", ") || "Unknown authors"} · {paper.year || "n.d."} · {paper.venue || "Unknown venue"}</p>
                      </div>
                    </div>
                    <p className="literature-abstract">{paper.abstract || "暂无摘要"}</p>
                    <div className="literature-score-row">
                      <span>相关 {scoreText(paper.relevanceScore)}</span>
                      <span>质量 {scoreText(paper.qualityScore)}</span>
                      <span>证据 {scoreText(paper.evidenceScore)}</span>
                      <span>来源 {sourceText(paper.sources)}</span>
                      <span>{paper.pdfAvailable || paper.pdfCached || paper.pdfUrl ? "有开放 PDF" : "未缓存 PDF"}</span>
                    </div>
                    <div className="literature-paper-links">
                      {paper.doi && <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">DOI</a>}
                      {paper.url && <a href={paper.url} target="_blank" rel="noreferrer">原始链接</a>}
                      {paper.pdfUrl && <a href={paper.pdfUrl} target="_blank" rel="noreferrer">PDF</a>}
                    </div>
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
      </section>
    </main>
  );
}
