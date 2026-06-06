import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  DailyReviewConfig,
  DailyReviewRunSummary,
  WechatArticleResult,
  buildWechatArticle,
  createWechatDraft,
  getDailyReviewAdminRuns,
  getDailyReviewConfig,
  saveDailyReviewConfig,
  testDailyReviewWechat,
} from "../api/client";

const ADMIN_TOKEN_KEY = "frontier_review_admin_token";

const FALLBACK_WECHAT = {
  enabled: false,
  appId: "",
  appSecret: "",
  author: "研域前沿综述",
  sourceUrlBase: "",
  autoDraft: false,
  coverImageUrl: "",
  digestPrefix: "研域前沿综述",
};

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function maskedPlaceholder(configured?: boolean) {
  return configured ? "已配置，留空则继续使用原 AppSecret" : "AppSecret";
}

async function copyText(value: string) {
  await navigator.clipboard.writeText(value);
}

function sortRunsNewestFirst(items: DailyReviewRunSummary[]): DailyReviewRunSummary[] {
  return [...items].sort((a, b) => {
    const ta = new Date(a.createdAt || 0).getTime();
    const tb = new Date(b.createdAt || 0).getTime();
    return (Number.isNaN(tb) ? 0 : tb) - (Number.isNaN(ta) ? 0 : ta);
  });
}

export function WechatAdminPage() {
  const [adminToken] = useState(() => localStorage.getItem(ADMIN_TOKEN_KEY) || "");
  const [config, setConfig] = useState<DailyReviewConfig | null>(null);
  const [runs, setRuns] = useState<DailyReviewRunSummary[]>([]);
  const [runId, setRunId] = useState("");
  const [article, setArticle] = useState<WechatArticleResult | null>(null);
  const [loading, setLoading] = useState(Boolean(adminToken));
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    document.title = "研域前沿综述公众号模块";
  }, []);

  useEffect(() => {
    if (!adminToken) return;
    setLoading(true);
    Promise.all([getDailyReviewConfig(adminToken), getDailyReviewAdminRuns(80, adminToken)])
      .then(([cfg, history]) => {
        const sortedRuns = sortRunsNewestFirst(history.items);
        setConfig({ ...cfg, wechat: { ...FALLBACK_WECHAT, ...cfg.wechat } });
        setRuns(sortedRuns);
        setRunId(sortedRuns[0]?.runId || "");
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "读取公众号模块失败"))
      .finally(() => setLoading(false));
  }, [adminToken]);

  const selectedRun = useMemo(() => runs.find((item) => item.runId === runId), [runs, runId]);

  function updateWechat(patch: Partial<NonNullable<DailyReviewConfig["wechat"]>>) {
    setConfig((old) => old ? { ...old, wechat: { ...FALLBACK_WECHAT, ...old.wechat, ...patch } } : old);
  }

  async function handleSave() {
    if (!config) return;
    setSaving(true);
    setError("");
    try {
      const saved = await saveDailyReviewConfig(config, adminToken);
      setConfig({ ...saved, wechat: { ...FALLBACK_WECHAT, ...saved.wechat } });
      setStatus("公众号配置已保存");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    if (!config) return;
    setBusy(true);
    setError("");
    setStatus("正在真实测试公众号连接...");
    try {
      const result = await testDailyReviewWechat(config, adminToken);
      setStatus(result.ok ? `连接成功：${result.detail ?? result.message}` : `连接失败：${result.detail ?? result.message}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "公众号连接测试失败");
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function handleBuild() {
    if (!runId) return;
    setBusy(true);
    setError("");
    setStatus("正在生成公众号图文稿...");
    try {
      const data = await buildWechatArticle({ runId }, adminToken);
      setArticle(data);
      setStatus("公众号稿已生成，可预览、复制或创建草稿。");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "生成公众号稿失败");
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateDraft() {
    if (!article) return;
    setBusy(true);
    setError("");
    setStatus("正在上传封面并创建公众号草稿...");
    try {
      const data = await createWechatDraft(
        {
          runId: article.runId,
          title: article.title,
          digest: article.digest,
          contentHtml: article.contentHtml,
          contentText: article.contentText,
          coverUrl: article.coverUrl || undefined,
        },
        adminToken,
      );
      setArticle(data);
      setStatus(data.message || "草稿已创建，请前往公众号后台发布。");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "创建草稿失败");
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  if (!adminToken) {
    return (
      <main className="daily-page standalone">
        <section className="daily-hero"><div><p className="daily-kicker">研域前沿综述 WeChat Desk</p><h1>公众号模块</h1><p className="daily-subtitle">请先登录管理员后台，再进入公众号模块。</p></div></section>
      </main>
    );
  }

  if (loading || !config) {
    return <main className="daily-page standalone"><div className="daily-loading">正在读取公众号模块...</div></main>;
  }

  return (
    <main className="daily-page standalone wechat-admin">
      <section className="daily-hero wechat-hero">
        <div>
          <p className="daily-kicker">研域前沿综述 WeChat Desk</p>
          <h1>公众号图文编辑台</h1>
          <p className="daily-subtitle">把当天或往期平台日报转成更抓眼球的科研前沿图文：少一点堆字，多一点色块、证据卡和可追溯入口。</p>
        </div>
        <div className="daily-hero-metrics">
          <span><strong>{runs.length}</strong>可选日报</span>
          <span><strong>{config.wechat?.enabled ? "启用" : "未启用"}</strong>公众号</span>
          <span><strong>草稿</strong>不自动发布</span>
        </div>
      </section>

      <section className="wechat-workbench">
        <div className="daily-admin-card wechat-control">
          <div className="daily-panel-title">
            <span>公众号账号</span>
            <a className="btn btn-ghost" href="/admin">返回后台</a>
          </div>
          <p className="daily-hint">当前权限支持自动创建草稿，不支持自动发布。创建成功后请进入公众号后台人工检查并发布。</p>
          <label><input type="checkbox" checked={config.wechat?.enabled ?? false} onChange={(e) => updateWechat({ enabled: e.target.checked })} /> 启用公众号模块</label>
          <label>AppID<input value={config.wechat?.appId ?? ""} onChange={(e) => updateWechat({ appId: e.target.value })} /></label>
          <label>AppSecret<input type="password" value={config.wechat?.appSecret ?? ""} placeholder={maskedPlaceholder(config.wechatSecretConfigured)} onChange={(e) => updateWechat({ appSecret: e.target.value })} /></label>
          <div className="daily-grid-2">
            <label>作者<input value={config.wechat?.author ?? ""} onChange={(e) => updateWechat({ author: e.target.value })} /></label>
            <label>摘要前缀<input value={config.wechat?.digestPrefix ?? ""} onChange={(e) => updateWechat({ digestPrefix: e.target.value })} /></label>
          </div>
          <label>平台域名<input value={config.wechat?.sourceUrlBase ?? ""} onChange={(e) => updateWechat({ sourceUrlBase: e.target.value })} /></label>
          <label>固定封面 URL<input value={config.wechat?.coverImageUrl ?? ""} placeholder="留空则使用本期一图看懂或本地占位封面" onChange={(e) => updateWechat({ coverImageUrl: e.target.value })} /></label>
          <div className="wechat-actions">
            <button className="btn" disabled={saving} onClick={() => void handleSave()}>{saving ? "保存中..." : "保存配置"}</button>
            <button className="btn btn-ghost" disabled={busy} onClick={() => void handleTest()}>测试微信连接</button>
          </div>
        </div>

        <div className="daily-admin-card wechat-control">
          <div className="daily-panel-title"><span>选择日报</span><button className="btn btn-ghost" onClick={() => void handleBuild()} disabled={busy || !runId}>生成公众号稿</button></div>
          <p className="daily-hint">可以选择当天日报，也可以把往期日报重新包装成“方向回顾”“证据再读”或专题推文。完整证据仍回到平台原文。</p>
          <div className="wechat-run-list">
            {runs.map((run) => (
              <button key={run.runId} className={run.runId === runId ? "active" : ""} onClick={() => { setRunId(run.runId); setArticle(null); }}>
                <strong>{run.topicName || run.topic}</strong>
                <span>{run.subtitle || run.dailyMode || "滚动日报"} · {formatDate(run.createdAt)} · {run.paperCount} 篇</span>
              </button>
            ))}
            {!runs.length && <p className="daily-hint">暂无已生成日报。请先在主题后台生成平台日报。</p>}
          </div>
          {selectedRun && (
            <div className="wechat-selected-run">
              <strong>{selectedRun.topicName || selectedRun.topic}</strong>
              <span>{selectedRun.subtitle || "未生成动态小标题"}</span>
            </div>
          )}
        </div>

        <div className="daily-admin-card wechat-preview-card">
          <div className="daily-panel-title">
            <span>公众号预览</span>
            <div className="wechat-actions">
              <button className="btn btn-ghost" disabled={!article} onClick={() => article && void copyText(article.contentText).then(() => setStatus("Markdown 已复制"))}>复制 Markdown</button>
              <button className="btn btn-ghost" disabled={!article} onClick={() => article && void copyText(article.contentHtml).then(() => setStatus("HTML 已复制"))}>复制 HTML</button>
              <button className="daily-run" disabled={busy || !article} onClick={() => void handleCreateDraft()}>创建公众号草稿</button>
            </div>
          </div>
          {article ? (
            <>
              <div className="wechat-article-meta">
                <span>{article.title}</span>
                <small>{article.digest}</small>
                <small>阅读原文：{article.sourceUrl}</small>
                {article.draftMediaId && <strong>草稿 media_id：{article.draftMediaId}</strong>}
              </div>
              <iframe className="wechat-preview-frame" title="公众号图文预览" srcDoc={article.contentHtml} />
            </>
          ) : (
            <div className="wechat-empty-preview">
              <strong>等待生成公众号稿</strong>
              <span>生成后会看到封面式首屏、关键数字、精选证据卡、检索来源和阅读原文入口。</span>
            </div>
          )}
        </div>
      </section>

      {status && <p className="daily-status ok">{status}</p>}
      {error && <p className="daily-status bad">{error}</p>}
    </main>
  );
}
