import { useMemo, useState } from "react";
import {
  ApiError,
  LiteratureCdkPublicInfo,
  LiteratureOnlySearchResult,
  getLiteratureCdkStatus,
  searchLiteratureOnly,
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

export function LiteratureSearchPage() {
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

  const visibleLimit = useMemo(() => cdkInfo?.paperCountMax ?? 200, [cdkInfo]);

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
      const res = await searchLiteratureOnly({
        topic: cleanTopic,
        paperCount: Math.max(5, Math.min(visibleLimit, paperCount)),
        sinceYear,
        cdk: authMode === "cdk" ? cdk.trim() : null,
        llm: authMode === "own" ? { baseUrl: baseUrl.trim(), apiKey: apiKey.trim(), model: model.trim(), temperature: 0, maxTokens: 1000 } : null,
      });
      setResult(res);
      if (res.cdk) setCdkInfo(res.cdk);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "文献检索失败。");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="literature-page">
      <section className="literature-hero">
        <div>
          <p className="literature-kicker">YFR · 研域前沿综述</p>
          <h1>只检索文献</h1>
          <p>输入一个研究主题，系统会用 LLM 拆解中英文检索式，再合并 Hybrid 多源文献结果，返回去重后的高质量证据列表。</p>
          <div className="literature-links">
            <a href="/daily-review">查看前沿日报</a>
            <a href="https://yfr.yangy.cn" target="_blank" rel="noreferrer">yfr.yangy.cn</a>
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
          {!result && (
            <div className="literature-empty">
              <strong>等待检索</strong>
              <span>结果会展示检索式、扩展查询、来源、DOI、摘要和三项质量分数。</span>
            </div>
          )}
          {result && (
            <>
              <div className="literature-result-head">
                <div>
                  <p className="literature-kicker">检索完成</p>
                  <h2>{result.topic}</h2>
                  <span>{result.returned}/{result.requested} 篇 · {result.sinceYear} 年以来 · {result.literatureProvider}</span>
                </div>
                <a href="#literature-paper-list">跳到文献列表</a>
              </div>
              <div className="literature-query-box">
                <strong>LLM 扩展检索式</strong>
                <div>{result.llmSearchQueries.length ? result.llmSearchQueries.map((item) => <span key={item}>{item}</span>) : <span>未生成扩展检索式</span>}</div>
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
