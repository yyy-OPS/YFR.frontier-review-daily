import { MouseEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  DailyReviewRunResult,
  DailyReviewRunSummary,
  DailyReviewPdfResolveResult,
  ReviewTopicConfig,
  getExclusiveReviewHistory,
  getExclusiveReviewRun,
  getExclusiveReviewTopics,
  getLatestExclusiveReview,
  getDailyReviewHistory,
  getDailyReviewRun,
  getDailyReviewTopics,
  getLatestDailyReview,
  dailyReviewAssetSrc,
  dailyReviewImageSrc,
  resolveDailyReviewPdf,
  translateDailyReviewText,
} from "../api/client";
import { renderMarkdown } from "../lib/markdown";

type Tab = "review" | "papers" | "image";
type TranslationMap = Record<string, string>;
type PdfLookupState = Record<string, { loading?: boolean; result?: DailyReviewPdfResolveResult; error?: string }>;
const EXCLUSIVE_KEY_STORAGE = "frontier_review_exclusive_access_key";
const PROJECT = {
  name: "研域前沿综述",
  englishName: "Frontier Review",
  shortName: "研域",
};

function linkCitations(md: string): string {
  return md.replace(/\[((?:\d+)(?:[\s,，、;；-]+(?:\d+))*)\]/g, (_match, group) => {
    const nums = String(group).match(/\d+/g) ?? [];
    return nums.map((n) => `<a href="#paper-${n}" class="daily-cite-ref" data-paper-ref="${n}">[${n}]</a>`).join("");
  });
}

function cleanText(value?: string | null): string {
  const div = document.createElement("div");
  div.innerHTML = value ?? "";
  return (div.textContent || div.innerText || "").replace(/\s+/g, " ").trim();
}

function normalizeEvidenceText(value?: string | null, fallback = ""): string {
  const source = (value || "").trim() || fallback;
  if (!source) return "";
  const htmlAware = source
    .replace(/<\s*sub[^>]*>([\s\S]*?)<\s*\/\s*sub\s*>/gi, "_($1)")
    .replace(/<\s*sup[^>]*>([\s\S]*?)<\s*\/\s*sup\s*>/gi, "^($1)")
    .replace(/<\s*br\s*\/?\s*>/gi, "\n");
  const div = document.createElement("div");
  div.innerHTML = htmlAware;
  return (div.textContent || div.innerText || "")
    .replace(/\\mathrm\{([^{}]+)\}/g, "$1")
    .replace(/\\text\{([^{}]+)\}/g, "$1")
    .replace(/\\rm\s+([A-Za-z]+)/g, "$1")
    .replace(/\\([,;:!])/g, "$1")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function renderEvidenceMarkdown(value?: string | null, fallback = ""): string {
  return renderMarkdown(normalizeEvidenceText(value, fallback));
}

function scoreLabelText(label?: string | null): string {
  if (label === "high") return "高";
  if (label === "medium") return "中";
  if (label === "low") return "低";
  return "待评";
}

function scoreClass(score?: number | null): string {
  if (typeof score !== "number") return "unknown";
  if (score >= 80) return "high";
  if (score >= 55) return "medium";
  return "low";
}

function evidenceScoreItems(paper: DailyReviewRunResult["papers"][number]) {
  const scores = paper.evidenceScores;
  const items = [
    { key: "relevance", label: "相关", score: paper.relevanceScore ?? scores?.relevance?.score, tag: scores?.relevance?.label },
    { key: "quality", label: "质量", score: paper.qualityScore ?? scores?.quality?.score, tag: scores?.quality?.label },
    { key: "evidence", label: "证据", score: paper.evidenceScore ?? scores?.evidence?.score, tag: scores?.evidence?.label },
    { key: "novelty", label: "新颖", score: paper.noveltyScore ?? scores?.novelty?.score, tag: scores?.novelty?.label },
  ];
  return items.filter((item) => typeof item.score === "number");
}

function shortEvidenceTitle(value?: string | null): string {
  const title = cleanText(normalizeEvidenceText(value, "未命名文献"));
  return title.length > 78 ? `${title.slice(0, 78)}...` : title;
}

function primarySourceLabel(paper: DailyReviewRunResult["papers"][number]): string {
  const sources = paper.sources?.filter(Boolean) ?? [];
  if (sources.length > 0) return sources.slice(0, 2).join(" / ");
  return cleanText(paper.source) || "unknown";
}

function deltaModeText(mode?: string | null): string {
  if (mode === "fresh_daily") return "新增日报";
  if (mode === "delta_brief") return "差异简报";
  if (mode === "topic_deep_dive") return "专题深挖";
  if (mode === "no_significant_update") return "监测短报";
  return "滚动日报";
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function filePart(value?: string | null): string {
  return (value || "daily-review")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 72) || "daily-review";
}

function imageStatusText(status?: string | null): string {
  if (status === "generated") return "AI 图片已生成";
  if (status === "fallback") return "当前为占位图，需重新生成正式图片";
  if (status === "prompt-only") return "仅生成提示词，占位图展示";
  return "图片状态未知";
}

function formatBytes(value?: number | null): string {
  if (!value || value <= 0) return "";
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

function pdfEvidenceStatus(paper: DailyReviewRunResult["papers"][number]): { label: string; className: string; detail: string } {
  const source = cleanText(paper.pdfSource);
  const bytes = formatBytes(paper.pdfBytes);
  if (paper.pdfAvailable && paper.pdfUrl) {
    return {
      label: "有 PDF 原文",
      className: "ok",
      detail: [paper.pdfCode ? `编码 ${paper.pdfCode}` : "", bytes, source ? `来源 ${source}` : ""].filter(Boolean).join(" / "),
    };
  }
  if (paper.openAccessPdf || paper.pdfRemoteUrl) {
    return {
      label: "有开放原文线索",
      className: "hint",
      detail: [source ? `来源 ${source}` : "", paper.pdfLicense ? cleanText(paper.pdfLicense) : ""].filter(Boolean).join(" / "),
    };
  }
  if (paper.pdfAvailable === false) {
    return {
      label: "未发现可缓存 PDF",
      className: "bad",
      detail: cleanText(paper.pdfStatus) || "已检测公开来源，未找到可合法缓存的 PDF。",
    };
  }
  return {
    label: "尚未检测开放 PDF",
    className: "pending",
    detail: "旧日报或该批次尚未执行 PDF 预缓存，可点击按钮即时检测。",
  };
}

async function saveCanvasAsPng(canvas: HTMLCanvasElement, filename: string): Promise<void> {
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((value) => {
      if (value) resolve(value);
      else reject(new Error("图片导出失败"));
    }, "image/png");
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function DailyReviewPage({ exclusive = false }: { exclusive?: boolean }) {
  const { topicSlug, runId } = useParams();
  const navigate = useNavigate();
  const reviewExportRef = useRef<HTMLDivElement>(null);
  const [exclusiveKey, setExclusiveKey] = useState(() => sessionStorage.getItem(EXCLUSIVE_KEY_STORAGE) || "");
  const [exclusiveInput, setExclusiveInput] = useState("");
  const [exclusiveChecking, setExclusiveChecking] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [result, setResult] = useState<DailyReviewRunResult | null>(null);
  const [topics, setTopics] = useState<ReviewTopicConfig[]>([]);
  const [history, setHistory] = useState<DailyReviewRunSummary[]>([]);
  const [tab, setTab] = useState<Tab>("review");
  const [translations, setTranslations] = useState<TranslationMap>({});
  const [translatingKey, setTranslatingKey] = useState("");
  const [pdfLookups, setPdfLookups] = useState<PdfLookupState>({});
  const [imagePreviewOpen, setImagePreviewOpen] = useState(false);
  const [exportingReview, setExportingReview] = useState(false);

  useEffect(() => {
    document.title = `${PROJECT.shortName} · ${PROJECT.name}`;
  }, []);

  useEffect(() => {
    if (exclusive) document.title = `${PROJECT.shortName} · 专属综述`;
  }, [exclusive]);

  useEffect(() => {
    let alive = true;
    setError("");
    if (exclusive && !exclusiveKey) {
      setLoading(false);
      setTopics([]);
      setResult(null);
      setHistory([]);
      return () => {
        alive = false;
      };
    }
    setLoading(true);
    (exclusive ? getExclusiveReviewTopics(exclusiveKey) : getDailyReviewTopics())
      .then(async (topicList) => {
        if (!alive) return;
        const visibleTopics = topicList.items;
        setTopics(visibleTopics);
        if (!topicSlug || !visibleTopics.some((topic) => topic.slug === topicSlug)) {
          setResult(null);
          setHistory([]);
          return;
        }
        const [runData, list] = await Promise.all([
          exclusive
            ? (runId ? getExclusiveReviewRun(exclusiveKey, runId) : getLatestExclusiveReview(exclusiveKey, topicSlug))
            : (runId ? getDailyReviewRun(runId) : getLatestDailyReview(topicSlug)),
          exclusive ? getExclusiveReviewHistory(exclusiveKey, 50, topicSlug) : getDailyReviewHistory(50, topicSlug),
        ]);
        if (!alive) return;
        setResult(runId ? runData.result : runData.result);
        setHistory(list.items);
        setTranslations({});
        setPdfLookups({});
        setImagePreviewOpen(false);
      })
      .catch((e) => {
        if (exclusive && e instanceof ApiError && e.code === "EXCLUSIVE_ACCESS_REQUIRED") {
          sessionStorage.removeItem(EXCLUSIVE_KEY_STORAGE);
          if (alive) {
            setExclusiveKey("");
            setExclusiveInput("");
          }
        }
        if (alive) setError(e instanceof ApiError ? e.message : "综述读取失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [topicSlug, runId, exclusive, exclusiveKey]);

  const activeTopic = useMemo(() => {
    if (result?.topicSlug) return topics.find((t) => t.slug === result.topicSlug);
    if (topicSlug) return topics.find((t) => t.slug === topicSlug);
    return topics[0];
  }, [result, topicSlug, topics]);

  const reviewBasePath = exclusive ? "/admin/exclusive-review" : "/daily-review";

  useEffect(() => {
    if (exclusive && exclusiveKey && !topicSlug && topics[0]?.slug) {
      navigate(`${reviewBasePath}/${topics[0].slug}`, { replace: true });
    }
  }, [exclusive, exclusiveKey, topicSlug, topics, navigate, reviewBasePath]);

  const evidenceStats = useMemo(() => {
    if (!result) return null;
    const withDoi = result.papers.filter((p) => p.doi).length;
    const withAbstract = result.papers.filter((p) => cleanText(p.abstract).length > 20).length;
    const withFullText = result.papers.filter((p) => p.evidenceSource === "fulltext").length;
    return { total: result.papers.length, withDoi, withAbstract, withFullText };
  }, [result]);

  const paperDirectoryItems = useMemo(() => {
    if (!result) return [];
    return result.papers.map((paper, index) => {
      const relevance = paper.relevanceScore ?? paper.evidenceScores?.relevance?.score;
      const quality = paper.qualityScore ?? paper.evidenceScores?.quality?.score;
      return {
        id: paper.id,
        index: index + 1,
        title: shortEvidenceTitle(paper.title),
        year: paper.year || "n.d.",
        source: primarySourceLabel(paper),
        relevance: typeof relevance === "number" ? relevance : null,
        quality: typeof quality === "number" ? quality : null,
        hasPdf: Boolean(paper.pdfAvailable && paper.pdfUrl),
        hasFullText: paper.evidenceSource === "fulltext",
      };
    });
  }, [result]);

  const reviewHtml = useMemo(() => {
    if (!result) return "";
    return renderMarkdown(linkCitations(result.reviewMarkdown));
  }, [result]);

  const assetAccessKey = exclusive ? exclusiveKey : undefined;
  const imageSrc = useMemo(() => dailyReviewImageSrc(result?.image.url, assetAccessKey), [result?.image.url, assetAccessKey]);

  useEffect(() => {
    if (!imagePreviewOpen) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setImagePreviewOpen(false);
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [imagePreviewOpen]);

  function jumpToPaper(n: number) {
    setTab("papers");
    window.setTimeout(() => {
      document.getElementById(`paper-${n}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 80);
  }

  function handleReviewClick(e: MouseEvent<HTMLElement>) {
    const target = e.target as HTMLElement;
    const ref = target.closest("[data-paper-ref]") as HTMLElement | null;
    if (!ref) return;
    e.preventDefault();
    const n = Number(ref.dataset.paperRef);
    if (Number.isFinite(n)) jumpToPaper(n);
  }

  async function loadRun(runId: string) {
    setError("");
    setLoading(true);
    try {
      const data = await getDailyReviewRun(runId);
      setResult(data.result);
      setTab("review");
      setTranslations({});
      setPdfLookups({});
      setImagePreviewOpen(false);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "往期日报读取失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleTranslate(key: string, text: string, context: string) {
    setTranslatingKey(key);
    try {
      const data = await translateDailyReviewText(text, context, "中文");
      setTranslations((old) => ({ ...old, [key]: data.translatedText }));
    } catch (e) {
      setTranslations((old) => ({
        ...old,
        [key]: e instanceof ApiError && e.code === "TRANSLATION_NOT_CONFIGURED"
          ? "翻译模型未配置，请直接使用浏览器翻译。"
          : "翻译失败，请稍后重试。",
      }));
    } finally {
      setTranslatingKey("");
    }
  }

  async function handleResolvePdf(paper: DailyReviewRunResult["papers"][number]) {
    if (!result) return;
    const key = paper.id;
    setPdfLookups((old) => ({ ...old, [key]: { loading: true } }));
    try {
      const data = await resolveDailyReviewPdf({
        runId: result.runId,
        paperId: paper.id,
        doi: paper.doi,
        title: paper.title,
        url: paper.url,
      }, assetAccessKey);
      setPdfLookups((old) => ({ ...old, [key]: { result: data } }));
      setResult((current) => {
        if (!current || current.runId !== result.runId || !data.ok) return current;
        return {
          ...current,
          papers: current.papers.map((item) => item.id === paper.id
            ? {
                ...item,
                pdfAvailable: true,
                pdfUrl: data.url ?? item.pdfUrl,
                pdfRemoteUrl: data.remoteUrl ?? item.pdfRemoteUrl,
                pdfCode: data.code ?? item.pdfCode,
                pdfSource: data.source ?? item.pdfSource,
                pdfLicense: data.license ?? item.pdfLicense,
                pdfBytes: data.bytes ?? item.pdfBytes,
                pdfCached: data.cached ?? item.pdfCached,
                openAccessPdf: true,
                pdfStatus: data.message ?? item.pdfStatus,
              }
            : item),
        };
      });
    } catch (e) {
      setPdfLookups((old) => ({
        ...old,
        [key]: { error: e instanceof ApiError ? e.message : "开放 PDF 查询失败，请稍后再试。" },
      }));
    }
  }

  async function exportReviewImage() {
    const target = reviewExportRef.current;
    if (!target || !result) return;
    setError("");
    setExportingReview(true);
    try {
      await document.fonts?.ready;
      const maxScale = Math.max(
        1,
        Math.min(2, 28000 / Math.max(target.scrollHeight, 1), 14000 / Math.max(target.scrollWidth, 1)),
      );
      const { default: html2canvas } = await import("html2canvas");
      const canvas = await html2canvas(target, {
        backgroundColor: "#fffdf8",
        logging: false,
        scale: maxScale,
        useCORS: true,
        windowWidth: target.scrollWidth,
        windowHeight: target.scrollHeight,
      });
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      await saveCanvasAsPng(canvas, `${PROJECT.shortName}-${filePart(activeTopic?.slug || topicSlug)}-${stamp}.png`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "综述图片导出失败");
    } finally {
      setExportingReview(false);
    }
  }

  async function handleExclusiveEnter() {
    const key = exclusiveInput.trim();
    if (!key || exclusiveChecking) return;
    setExclusiveChecking(true);
    setError("");
    try {
      const topicList = await getExclusiveReviewTopics(key);
      sessionStorage.setItem(EXCLUSIVE_KEY_STORAGE, key);
      setExclusiveKey(key);
      setTopics(topicList.items);
      if (topicList.items[0]?.slug) {
        navigate(`/admin/exclusive-review/${topicList.items[0].slug}`, { replace: true });
      }
    } catch (e) {
      sessionStorage.removeItem(EXCLUSIVE_KEY_STORAGE);
      setExclusiveKey("");
      setError(e instanceof ApiError ? e.message : "专属综述访问密钥验证失败");
    } finally {
      setExclusiveChecking(false);
    }
  }

  if (exclusive && !exclusiveKey) {
    return (
      <main className="daily-page standalone exclusive-review-gate">
        <section className="daily-hero">
          <div>
            <p className="daily-kicker">{PROJECT.shortName} · Exclusive Review</p>
            <h1>专属综述</h1>
            <p className="daily-subtitle">请输入管理员配置的专属综述访问密钥。验证通过后可查看管理员专属主题、往期综述和文献证据。</p>
          </div>
        </section>
        <section className="daily-admin-card exclusive-key-card">
          <label>访问密钥<input type="password" value={exclusiveInput} onChange={(e) => setExclusiveInput(e.target.value)} onKeyDown={(e) => {
            if (e.key === "Enter" && exclusiveInput.trim()) {
              void handleExclusiveEnter();
            }
          }} /></label>
          <button className="daily-run" type="button" disabled={!exclusiveInput.trim() || exclusiveChecking} onClick={() => void handleExclusiveEnter()}>
            {exclusiveChecking ? "正在验证..." : "进入专属综述"}
          </button>
          {error && <p className="daily-status bad">{error}</p>}
          <p className="daily-hint">该入口不使用管理员登录态；只校验专属综述访问密钥。请勿公开分享访问密钥。</p>
        </section>
      </main>
    );
  }

  if (loading && !result) {
    return <main className="daily-page standalone"><div className="daily-loading">正在加载 {PROJECT.shortName} · {PROJECT.name}...</div></main>;
  }

  if (topicSlug && !topics.some((topic) => topic.slug === topicSlug)) {
    return <Navigate to={exclusive ? "/admin/exclusive-review" : "/daily-review"} replace />;
  }

  if (!topicSlug) {
    return (
      <main className="daily-page standalone">
        <section className="daily-hero">
          <div>
            <p className="daily-kicker">{PROJECT.shortName} · {PROJECT.englishName}</p>
            <h1>{exclusive ? "专属综述" : PROJECT.name}</h1>
            <p className="daily-subtitle">
              {exclusive
                ? "这里只展示管理员设置为专属的主题。点击主题卡片进入对应路径，查看当期综述、文献证据和该主题的往期日报。"
                : "这里只展示已开放的公开主题。点击主题卡片进入对应路径，查看当期综述、文献证据和该主题的往期日报。"}
            </p>
          </div>
          <div className="daily-hero-metrics" aria-label="主题概览">
            <span><strong>{topics.length}</strong>公开主题</span>
            <span><strong>实时</strong>读取后台配置</span>
            <span><strong>多源</strong>证据追踪</span>
          </div>
        </section>
        {error && <p className="daily-status bad">{error}</p>}
        <section className="daily-topic-directory">
          {topics.map((topic) => (
            <Link key={topic.id} className="daily-topic-card" to={`${reviewBasePath}/${topic.slug}`}>
              <span className="daily-topic-path">/{topic.slug}</span>
              <h2>{topic.name}</h2>
              <p>{topic.topic}</p>
              <div>
                <span>{topic.sinceYear} 起</span>
                <span>{topic.paperCount} 篇/期</span>
                <span>{topic.scheduleEnabled ? `每日 ${topic.scheduleTime}` : "手动更新"}</span>
              </div>
            </Link>
          ))}
          {!topics.length && (
            <div className="daily-empty">
              <h3>{exclusive ? "暂无专属主题" : "暂无公开主题"}</h3>
              <p>{exclusive ? "密钥验证通过，但当前没有启用的专属主题。请在管理员后台将主题设置为专属并启用。" : "当前还没有可浏览的公开主题。主题开放后会自动显示在这里。"}</p>
            </div>
          )}
        </section>
        <div className="daily-public-links" aria-label="YFR 项目链接">
          <a className="daily-public-brand" href="https://yfr.yangy.cn/" target="_blank" rel="noreferrer">YFR · 研域前沿综述</a>
          <a href="/literature-search">检索文献</a>
          <a href="https://github.com/yyy-OPS/YFR.frontier-review-daily" target="_blank" rel="noreferrer">GitHub 开源地址</a>
          <span>基于 <a href="https://github.com/niuniu-869/Biblio_Agent" target="_blank" rel="noreferrer">niuniu-869/Biblio_Agent</a> 开源项目扩展</span>
        </div>
      </main>
    );
  }

  return (
    <main className="daily-page standalone">
      <section className="daily-hero">
        <div>
          <p className="daily-kicker">{PROJECT.shortName} · {PROJECT.name}</p>
          <h1>{activeTopic?.name || "每日科研前沿综述平台"}</h1>
          <p className="daily-subtitle">
            每个公开路径对应一个独立主题，支持每日更新、年份范围和文献数量配置。每一期日报都会持久化保存，可随时回看。
          </p>
        </div>
        <div className="daily-hero-metrics" aria-label="运行概览">
          <span><strong>{result?.papers.length ?? 0}</strong>篇证据</span>
          <span><strong>{result?.query.sinceYear ?? activeTopic?.sinceYear ?? "-"}</strong>起</span>
          <span><strong>{history.length}</strong>期日报</span>
        </div>
      </section>

      {error && <p className="daily-status bad">{error}</p>}

      <section className="daily-public-layout">
        <aside className="daily-history-panel">
          <div className="daily-panel-title">
            <span>主题路径</span>
            <small>{topics.length} 个</small>
          </div>
          <div className="daily-topic-links">
            {topics.map((topic) => (
              <Link key={topic.id} className={topic.slug === topicSlug || topic.id === result?.topicId ? "active" : ""} to={`${reviewBasePath}/${topic.slug}`}>
                <strong>{topic.name}</strong>
                <span>/{topic.slug}</span>
              </Link>
            ))}
          </div>

          <div className="daily-panel-title daily-history-title">
            <span>往期日报</span>
            <small>{history.length} 期</small>
          </div>
          <div className="daily-history-list">
            {history.map((item) => (
              <Link key={item.runId} className={item.runId === result?.runId ? "active" : ""} to={`${reviewBasePath}/${item.topicSlug || topicSlug}/${item.runId}`}>
                <strong>{item.topicName || item.topic || "未命名主题"}</strong>
                {item.subtitle && <em>{cleanText(item.subtitle)}</em>}
                <span>{formatDate(item.createdAt)} / {deltaModeText(item.dailyMode)} / 新增 {item.newEvidenceCount ?? "-"} / {item.paperCount} 篇</span>
              </Link>
            ))}
            {!history.length && <p className="daily-hint">暂无历史日报，首期生成后会显示在这里。</p>}
          </div>
        </aside>

        <section className="daily-output">
          <div className="daily-output-head">
            <div>
              <p className="daily-kicker">{PROJECT.shortName} Review Output</p>
              <h2>{result ? cleanText(result.topic) : "暂无综述结果"}</h2>
              {result?.dailyDelta && (
                <div className="daily-delta-strip" aria-label="Daily Delta 状态">
                  <strong>{result.dailyDelta.modeLabel || deltaModeText(result.dailyDelta.mode)}</strong>
                  <span>{cleanText(result.dailyDelta.subtitle || result.subtitle || "")}</span>
                </div>
              )}
              {result && <p className="daily-hint">生成时间：{formatDate(result.createdAt)} / Run ID: {result.runId}</p>}
            </div>
            {evidenceStats && (
              <div className="daily-mini-stats">
                <span>{evidenceStats.total} 篇</span>
                <span>{evidenceStats.withAbstract} 摘要</span>
                <span>{evidenceStats.withFullText} 全文片段</span>
                {result?.dailyDelta && <span>{result.dailyDelta.newEvidenceCount ?? 0} 新增</span>}
                {result?.dailyDelta && <span>{result.dailyDelta.highNoveltyCount ?? 0} 高新颖</span>}
              </div>
            )}
          </div>

          <div className="daily-tabs" role="tablist">
            <button className={tab === "review" ? "active" : ""} onClick={() => setTab("review")}>综述</button>
            <button className={tab === "papers" ? "active" : ""} onClick={() => setTab("papers")}>文献证据</button>
            <button className={tab === "image" ? "active" : ""} onClick={() => setTab("image")}>一图看懂</button>
          </div>

          {!result && (
            <div className="daily-empty">
              <h3>该主题日报尚未发布</h3>
              <p>日报生成后，这里会展示完整综述、文献证据和一图看懂内容。</p>
            </div>
          )}

          {result && tab === "review" && (
            <div className="daily-review-export-area">
              <div className="daily-review-toolbar">
                <span>PNG 导出将保留 Markdown、表格和公式排版</span>
                <button className="daily-export-btn" type="button" onClick={() => void exportReviewImage()} disabled={exportingReview}>
                  {exportingReview ? "正在导出..." : "导出综述图片"}
                </button>
              </div>
              <div className="daily-review-export-shell" ref={reviewExportRef}>
                <div className="daily-review-export-brand">
                  <span>{PROJECT.shortName}</span>
                  <strong>{PROJECT.name}</strong>
                  <em>{PROJECT.englishName}</em>
                </div>
                <article className="daily-review markdown" onClick={handleReviewClick} dangerouslySetInnerHTML={{ __html: reviewHtml }} />
                <div className="daily-review-watermark">
                  <strong>{PROJECT.shortName}</strong>
                  <span>{PROJECT.name} · {PROJECT.englishName}</span>
                </div>
              </div>
            </div>
          )}

          {result && tab === "papers" && (
            <div className="daily-paper-list">
              {paperDirectoryItems.length > 0 && (
                <nav id="paper-directory" className="daily-paper-directory" aria-label="文献证据目录">
                  <div className="daily-paper-directory-head">
                    <div>
                      <strong>文献证据目录</strong>
                      <span>点击编号或标题直达对应证据卡</span>
                    </div>
                    <em>{paperDirectoryItems.length} 篇</em>
                  </div>
                  <div className="daily-paper-directory-grid">
                    {paperDirectoryItems.map((item) => (
                      <a className="daily-paper-directory-item" href={`#paper-${item.index}`} key={item.id}>
                        <span className="daily-paper-directory-no">[{item.index}]</span>
                        <span className="daily-paper-directory-title">{item.title}</span>
                        <span className="daily-paper-directory-meta">
                          {item.year} · {item.source}
                          {item.relevance !== null ? ` · 相关 ${item.relevance}` : ""}
                          {item.quality !== null ? ` · 质量 ${item.quality}` : ""}
                          {item.hasFullText ? " · 全文片段" : ""}
                          {item.hasPdf ? " · PDF" : ""}
                        </span>
                      </a>
                    ))}
                  </div>
                </nav>
              )}
              {result.papers.map((paper, index) => {
                const titleSource = normalizeEvidenceText(paper.title, "未命名文献");
                const abstractSource = normalizeEvidenceText(paper.abstract || paper.snippet);
                const title = cleanText(titleSource);
                const abstract = cleanText(abstractSource);
                const venue = cleanText(paper.venue);
                const snippet = cleanText(paper.snippet);
                const displayAbstract = abstractSource || "暂无摘要";
                const titleKey = `${paper.id}:title`;
                const abstractKey = `${paper.id}:abstract`;
                const source = paper.evidenceSource === "fulltext" ? "全文片段+摘要" : snippet ? "检索片段+摘要/元数据" : "摘要/元数据";
                const searchSources = paper.sources?.length ? paper.sources.join(" / ") : cleanText(paper.source);
                const scoreItems = evidenceScoreItems(paper);
                const pdfState = pdfLookups[paper.id];
                const pdfStatus = pdfEvidenceStatus(paper);
                return (
                  <article id={`paper-${index + 1}`} key={paper.id} className="daily-paper">
                    <div className="daily-paper-nav">
                      <span className="daily-paper-index">[{index + 1}]</span>
                      <a href="#paper-directory">目录</a>
                    </div>
                    <div>
                      <div className="daily-paper-title-row">
                        <div className="daily-paper-title-markdown markdown" dangerouslySetInnerHTML={{ __html: renderEvidenceMarkdown(titleSource) }} />
                        <button className="daily-inline-btn" onClick={() => void handleTranslate(titleKey, title, "paper title")} disabled={translatingKey === titleKey}>
                          {translatingKey === titleKey ? "翻译中..." : "翻译标题"}
                        </button>
                      </div>
                      {translations[titleKey] && <div className="daily-translation markdown" dangerouslySetInnerHTML={{ __html: renderEvidenceMarkdown(translations[titleKey]) }} />}
                      <p>{paper.authors?.slice(0, 6).map((name) => cleanText(name)).join(", ") || "作者未知"} / {paper.year || "n.d."} / {venue || "来源未知"}</p>
                      {scoreItems.length > 0 && (
                        <div className="daily-score-grid" aria-label="文献证据评分">
                          {scoreItems.map((item) => {
                            const score = Number(item.score ?? 0);
                            const tone = scoreClass(score);
                            return (
                              <div className={`daily-score-pill ${tone}`} key={item.key}>
                                <div className="daily-score-head">
                                  <span>{item.label}</span>
                                  <strong>{score}</strong>
                                  <em>{scoreLabelText(item.tag ?? tone)}</em>
                                </div>
                                <div className="daily-score-track">
                                  <span style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                      <div className="daily-abstract markdown" dangerouslySetInnerHTML={{ __html: renderEvidenceMarkdown(displayAbstract) }} />
                      <button className="daily-inline-btn" onClick={() => void handleTranslate(abstractKey, abstract || title, "paper abstract")} disabled={translatingKey === abstractKey}>
                        {translatingKey === abstractKey ? "翻译中..." : "翻译摘要"}
                      </button>
                      {translations[abstractKey] && <div className="daily-translation markdown" dangerouslySetInnerHTML={{ __html: renderEvidenceMarkdown(translations[abstractKey]) }} />}
                      <div className="daily-paper-meta">
                        <span>{source}</span>
                        {searchSources && <span>来源 {searchSources}</span>}
                        {typeof paper.noveltyScore === "number" && <span>{paper.seenBefore ? "历史延续" : "首次入库"}</span>}
                        <span>被引 {paper.citationCount ?? 0}</span>
                        <span>FWCI {paper.fwci ?? "n/a"}</span>
                        <span className={`daily-pdf-status ${pdfStatus.className}`}>原文：{pdfStatus.label}</span>
                        {paper.doi && <a href={`https://doi.org/${paper.doi}`} target="_blank" rel="noreferrer">DOI</a>}
                      </div>
                      <div className="daily-pdf-tools">
                        <span className={`daily-pdf-note ${pdfStatus.className}`}>{pdfStatus.detail}</span>
                        {paper.pdfAvailable && paper.pdfUrl && (
                          <a className="daily-pdf-link" href={dailyReviewAssetSrc(paper.pdfUrl, assetAccessKey)} target="_blank" rel="noreferrer">
                            下载本地 PDF
                          </a>
                        )}
                        <button className="daily-inline-btn" type="button" onClick={() => void handleResolvePdf(paper)} disabled={Boolean(pdfState?.loading)}>
                          {pdfState?.loading ? "检测开放 PDF..." : paper.pdfAvailable && paper.pdfUrl ? "重新检测开放 PDF" : "检测开放 PDF"}
                        </button>
                        {pdfState?.result?.ok && pdfState.result.url && (
                          <a className="daily-pdf-link" href={dailyReviewAssetSrc(pdfState.result.url, assetAccessKey)} target="_blank" rel="noreferrer">
                            打开开放 PDF
                          </a>
                        )}
                        {pdfState?.result && (
                          <span className={pdfState.result.ok ? "daily-pdf-note ok" : "daily-pdf-note"}>
                            {pdfState.result.message}
                            {pdfState.result.code ? ` 编码 ${pdfState.result.code}` : ""}
                            {pdfState.result.cached ? " / 已复用本地缓存" : pdfState.result.ok ? " / 已下载到本地" : ""}
                            {pdfState.result.bytes ? ` / ${formatBytes(pdfState.result.bytes)}` : ""}
                            {pdfState.result.source ? ` 来源 ${pdfState.result.source}` : ""}
                            {pdfState.result.license ? ` / ${pdfState.result.license}` : ""}
                            {pdfState.result.oaStatus ? ` / OA ${pdfState.result.oaStatus}` : ""}
                          </span>
                        )}
                        {pdfState?.error && <span className="daily-pdf-note bad">{pdfState.error}</span>}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          {result && tab === "image" && (
            <div className="daily-image-panel">
              <div className={`daily-image-status ${result.image.status === "generated" ? "ok" : "warn"}`}>
                {imageStatusText(result.image.status)}
              </div>
              {imageSrc && (
                <button
                  type="button"
                  className="daily-image-frame"
                  onClick={() => setImagePreviewOpen(true)}
                  aria-label="放大查看一图看懂综述信息图"
                >
                  <img src={imageSrc} alt="一图看懂综述信息图" />
                  <span>点击放大</span>
                </button>
              )}
              <h3>绘图模型提示词</h3>
              <textarea readOnly value={result.image.prompt} rows={9} />
            </div>
          )}
        </section>
      </section>
      {imagePreviewOpen && imageSrc && (
        <div className="daily-image-lightbox" role="dialog" aria-modal="true" aria-label="一图看懂图片预览" onClick={() => setImagePreviewOpen(false)}>
          <div className="daily-image-lightbox-inner" onClick={(e) => e.stopPropagation()}>
            <button type="button" className="daily-lightbox-close" onClick={() => setImagePreviewOpen(false)} aria-label="关闭图片预览">
              ×
            </button>
            <img src={imageSrc} alt="一图看懂综述信息图放大预览" />
          </div>
        </div>
      )}
    </main>
  );
}
