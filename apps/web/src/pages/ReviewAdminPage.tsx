import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  DailyReviewConfig,
  DailyReviewDraftSummary,
  DailyReviewProgressItem,
  ReviewTopicConfig,
  changeDailyReviewAdminPassword,
  getDailyReviewConfig,
  getDailyReviewDrafts,
  getDailyReviewProgress,
  loginDailyReviewAdmin,
  resumeDailyReviewDraft,
  runDailyReviewAsync,
  saveDailyReviewConfig,
  testDailyReviewPaperSearch,
  testDailyReviewImage,
  testDailyReviewLlm,
  testDailyReviewSciverse,
  testDailyReviewTranslation,
} from "../api/client";

const ADMIN_TOKEN_KEY = "frontier_review_admin_token";

function tenYearsAgo() {
  return new Date().getFullYear() - 10;
}

function slugify(value: string) {
  const ascii = value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return ascii || `topic-${Date.now().toString(36)}`;
}

function fieldNumber(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function maskedPlaceholder(configured?: boolean, fallback = "") {
  return configured ? "已配置，留空则继续使用原密钥" : fallback;
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 10);
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function compactRunText(value?: string | null): string {
  return (value || "").replace(/\s+/g, " ").trim();
}

function usableSecret(value: string): string | undefined {
  const secret = value.trim();
  if (!secret || secret.includes("*") || secret.includes("...")) return undefined;
  return secret;
}

function randomCdk(): string {
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  return `yfr-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function newLiteratureCdk() {
  const id = `cdk-${Date.now().toString(36)}`;
  return {
    id,
    name: "文献检索 CDK",
    code: randomCdk(),
    enabled: true,
    maxUses: 50,
    usedCount: 0,
    expiresAt: "",
    paperCountMax: 100,
    literatureProvider: null,
    paperSearchSources: [],
    note: "",
  };
}

function newTopic(): ReviewTopicConfig {
  const id = `topic-${Date.now().toString(36)}`;
  return {
    id,
    slug: id,
    name: "新主题日报",
    topic: "请输入新的科研主题",
    enabled: true,
    scheduleEnabled: true,
    scheduleTime: "08:30",
    paperCount: 80,
    sinceYear: tenYearsAgo(),
    freshnessBoost: "STRONG",
    includeFullText: true,
    includeWeb: true,
    privateOnly: false,
    subtopicPool: [],
    minHighNoveltyCount: null,
    maxRepeatRatio: 0.75,
    allowTopicDeepDive: true,
    allowNoSignificantUpdate: true,
  };
}

const EMPTY_CONFIG: DailyReviewConfig = {
  topic: "近十年人工智能在科研中的前沿进展",
  scheduleEnabled: true,
  scheduleTime: "08:30",
  paperCount: 80,
  sinceYear: tenYearsAgo(),
  freshnessBoost: "STRONG",
  includeFullText: true,
  includeWeb: true,
  sciverseApiToken: "",
  literatureProvider: "hybrid",
  paperSearchSources: ["semantic", "openalex", "crossref", "europepmc", "hal", "base", "core", "unpaywall"],
  exclusiveAccessKey: "",
  literatureSearchCdk: "",
  literatureSearchCdks: [],
  activeTopicId: "general-ai-research",
  topics: [
    {
      id: "general-ai-research",
      slug: "general-ai-research",
      name: "科研前沿日报",
      topic: "近十年人工智能在科研中的前沿进展",
      enabled: true,
      scheduleEnabled: true,
      scheduleTime: "08:30",
      paperCount: 80,
      sinceYear: tenYearsAgo(),
      freshnessBoost: "STRONG",
      includeFullText: true,
      includeWeb: true,
      privateOnly: false,
    },
  ],
  llm: { baseUrl: "https://api.openai.com/v1", apiKey: "", model: "gpt-4o-mini", temperature: 0.25, maxTokens: 24000 },
  translation: { baseUrl: "", apiKey: "", model: "", temperature: 0, maxTokens: 6000 },
  image: { enabled: false, baseUrl: "", apiKey: "", model: "", size: "1024x1024" },
  wechat: {
    enabled: false,
    appId: "",
    appSecret: "",
    author: "研域前沿综述",
    sourceUrlBase: "",
    autoDraft: false,
    coverImageUrl: "",
    digestPrefix: "研域前沿综述",
  },
};

export function ReviewAdminPage() {
  const [adminToken, setAdminToken] = useState(() => localStorage.getItem(ADMIN_TOKEN_KEY) || "");
  const [loginUser, setLoginUser] = useState("admin");
  const [loginPassword, setLoginPassword] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [config, setConfig] = useState<DailyReviewConfig>(EMPTY_CONFIG);
  const [selectedTopicId, setSelectedTopicId] = useState(EMPTY_CONFIG.activeTopicId);
  const [loading, setLoading] = useState(Boolean(adminToken));
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [connectionStatus, setConnectionStatus] = useState<Record<string, string>>({});
  const [progressItems, setProgressItems] = useState<DailyReviewProgressItem[]>([]);
  const [draftItems, setDraftItems] = useState<DailyReviewDraftSummary[]>([]);

  useEffect(() => {
    document.title = "Frontier Review Admin";
  }, []);

  useEffect(() => {
    if (!adminToken) return;
    setLoading(true);
    getDailyReviewConfig(adminToken)
      .then((data) => {
        const topics = data.topics?.length ? data.topics : EMPTY_CONFIG.topics;
        setConfig({ ...data, topics });
        setSelectedTopicId(data.activeTopicId || topics[0].id);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          localStorage.removeItem(ADMIN_TOKEN_KEY);
          setAdminToken("");
          setError("管理员会话已过期，请重新登录。");
        } else {
          setError(e instanceof ApiError ? e.message : "配置读取失败");
        }
      })
      .finally(() => setLoading(false));
  }, [adminToken]);

  const selectedTopic = useMemo(() => {
    return config.topics.find((topic) => topic.id === selectedTopicId) || config.topics[0];
  }, [config.topics, selectedTopicId]);

  const selectedProgress = useMemo(() => {
    return progressItems.find((item) => item.topicId === selectedTopicId);
  }, [progressItems, selectedTopicId]);

  const anyGenerating = progressItems.some((item) => item.status === "running");

  async function refreshProgress() {
    if (!adminToken) return;
    try {
      const [data, drafts] = await Promise.all([
        getDailyReviewProgress(adminToken),
        getDailyReviewDrafts(adminToken),
      ]);
      setProgressItems(data.items);
      setDraftItems(drafts.items);
    } catch {
      /* 进度刷新失败不打断后台操作 */
    }
  }

  useEffect(() => {
    if (!adminToken) return;
    void refreshProgress();
    const timer = window.setInterval(() => void refreshProgress(), 2000);
    return () => window.clearInterval(timer);
  }, [adminToken]);

  function updateTopic(patch: Partial<ReviewTopicConfig>) {
    if (!selectedTopic) return;
    setConfig((old) => ({
      ...old,
      activeTopicId: selectedTopic.id,
      topics: old.topics.map((topic) => topic.id === selectedTopic.id ? { ...topic, ...patch } : topic),
    }));
  }

  function addLiteratureCdk() {
    setConfig((old) => ({ ...old, literatureSearchCdks: [...(old.literatureSearchCdks ?? []), newLiteratureCdk()] }));
  }

  function updateLiteratureCdk(id: string, patch: Partial<NonNullable<DailyReviewConfig["literatureSearchCdks"]>[number]>) {
    setConfig((old) => ({
      ...old,
      literatureSearchCdks: (old.literatureSearchCdks ?? []).map((cdk) => cdk.id === id ? { ...cdk, ...patch } : cdk),
    }));
  }

  function removeLiteratureCdk(id: string) {
    setConfig((old) => ({ ...old, literatureSearchCdks: (old.literatureSearchCdks ?? []).filter((cdk) => cdk.id !== id) }));
  }

  function addTopic() {
    const topic = newTopic();
    setConfig((old) => ({ ...old, activeTopicId: topic.id, topics: [...old.topics, topic] }));
    setSelectedTopicId(topic.id);
  }

  function removeTopic() {
    if (!selectedTopic || config.topics.length <= 1) return;
    const next = config.topics.filter((topic) => topic.id !== selectedTopic.id);
    setConfig((old) => ({ ...old, activeTopicId: next[0].id, topics: next }));
    setSelectedTopicId(next[0].id);
  }

  async function handleLogin() {
    setError("");
    try {
      const login = await loginDailyReviewAdmin(loginUser, loginPassword);
      localStorage.setItem(ADMIN_TOKEN_KEY, login.token);
      setAdminToken(login.token);
      setLoginPassword("");
      setStatus(`管理员 ${login.username} 已登录`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "登录失败");
    }
  }

  async function handlePasswordChange() {
    setError("");
    try {
      await changeDailyReviewAdminPassword(oldPassword, newPassword, adminToken);
      setOldPassword("");
      setNewPassword("");
      localStorage.removeItem(ADMIN_TOKEN_KEY);
      setAdminToken("");
      setStatus("管理员密码已修改，请使用新密码重新登录。");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "密码修改失败");
    }
  }

  function handleLogout() {
    localStorage.removeItem(ADMIN_TOKEN_KEY);
    setAdminToken("");
    setStatus("已退出管理员后台");
  }

  async function handleSave() {
    setSaving(true);
    setError("");
    try {
      const saved = await saveDailyReviewConfig({ ...config, activeTopicId: selectedTopic?.id || config.activeTopicId }, adminToken);
      setConfig(saved);
      setStatus("后台配置已保存");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function testConnection(kind: "sciverse" | "paper_search" | "llm" | "translation" | "image") {
    setConnectionStatus((old) => ({ ...old, [kind]: "测试中..." }));
    try {
      const res =
        kind === "sciverse"
          ? await testDailyReviewSciverse(config, adminToken)
          : kind === "paper_search"
            ? await testDailyReviewPaperSearch(config, adminToken)
          : kind === "llm"
            ? await testDailyReviewLlm(config, adminToken)
            : kind === "translation"
              ? await testDailyReviewTranslation(config, adminToken)
              : await testDailyReviewImage(config, adminToken);
      setConnectionStatus((old) => ({
        ...old,
        [kind]: res.ok ? `连接成功：${res.detail ?? res.message}` : `连接失败：${res.detail ?? res.message}`,
      }));
    } catch (e) {
      setConnectionStatus((old) => ({ ...old, [kind]: e instanceof ApiError ? `连接失败：${e.message}` : "连接失败" }));
    }
  }

  async function handleRun() {
    if (!selectedTopic) return;
    setRunning(true);
    setError("");
    setStatus(`正在为「${selectedTopic.name}」检索 ${selectedTopic.paperCount} 篇候选文献并生成综述...`);
    try {
      const data = await runDailyReviewAsync(
        {
          topicId: selectedTopic.id,
          paperCount: selectedTopic.paperCount,
          sinceYear: selectedTopic.sinceYear,
          freshnessBoost: selectedTopic.freshnessBoost,
          includeFullText: selectedTopic.includeFullText,
          includeWeb: selectedTopic.includeWeb,
        },
        undefined,
        usableSecret(config.sciverseApiToken),
        adminToken,
      );
      setProgressItems((old) => {
        const rest = old.filter((item) => item.topicId !== data.topicId);
        return [data.progress, ...rest];
      });
      setStatus(data.accepted ? `已提交「${selectedTopic.name}」生成任务，进度会自动刷新。` : `「${selectedTopic.name}」已有生成任务正在运行。`);
      void refreshProgress();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "运行失败");
      setStatus("");
      await refreshProgress();
    } finally {
      setRunning(false);
    }
  }

  async function handleResumeDraft(draftId: string) {
    setError("");
    setStatus(`正在从暂存任务 ${draftId} 继续生成...`);
    try {
      const data = await resumeDailyReviewDraft(draftId, adminToken);
      setProgressItems((old) => {
        const rest = old.filter((item) => item.topicId !== data.topicId);
        return [data.progress, ...rest];
      });
      setStatus(data.accepted ? `已提交暂存任务 ${draftId} 继续生成。` : "该主题已有生成任务正在运行。");
      void refreshProgress();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "继续生成失败");
      await refreshProgress();
    }
  }

  if (!adminToken) {
    return (
      <main className="daily-page standalone">
        <section className="daily-hero">
          <div>
            <p className="daily-kicker">Frontier Review Admin</p>
            <h1>管理员后台</h1>
            <p className="daily-subtitle">配置多个公开主题路径、每日任务、模型、翻译和生图能力。</p>
          </div>
        </section>
        <section className="daily-admin-page">
          <div className="daily-admin-card">
            <h2>登录</h2>
            <label>账号<input value={loginUser} onChange={(e) => setLoginUser(e.target.value)} /></label>
            <label>密码<input type="password" value={loginPassword} onChange={(e) => setLoginPassword(e.target.value)} /></label>
            <button className="daily-run" onClick={() => void handleLogin()}>进入后台</button>
            {status && <p className="daily-status ok">{status}</p>}
            {error && <p className="daily-status bad">{error}</p>}
          </div>
        </section>
      </main>
    );
  }

  if (loading) {
    return <main className="daily-page standalone"><div className="daily-loading">正在读取后台配置...</div></main>;
  }

  return (
    <main className="daily-page standalone">
      <section className="daily-hero">
        <div>
          <p className="daily-kicker">Frontier Review Admin</p>
          <h1>多主题日报后台</h1>
          <p className="daily-subtitle">每个主题都有独立公开路径、年份范围、文献数量和每日更新计划。每日调度固定按北京时间执行，全局模型配置在所有主题间复用。</p>
        </div>
        <div className="daily-hero-metrics">
          <span><strong>{config.topics.length}</strong>主题</span>
          <span><strong>{selectedTopic?.paperCount ?? 0}</strong>篇/次</span>
          <span><strong>{anyGenerating ? "生成中" : "空闲"}</strong>任务状态</span>
        </div>
      </section>

      <section className="daily-admin-page">
        <div className="daily-admin-card">
          <div className="daily-panel-title">
            <span>主题路径</span>
            <button className="btn btn-ghost" onClick={handleLogout}>退出</button>
          </div>
          <div className="daily-topic-admin-list">
            {config.topics.map((topic) => (
              <button key={topic.id} className={topic.id === selectedTopic?.id ? "active" : ""} onClick={() => setSelectedTopicId(topic.id)}>
                <strong>{topic.name}{topic.privateOnly ? <em className="daily-topic-badge">专属</em> : null}</strong>
                <span>/{topic.slug} · {topic.privateOnly ? "专属访问" : topic.enabled ? "公开显示" : "已隐藏"}</span>
              </button>
            ))}
          </div>
          <button className="btn btn-ghost daily-test-btn" onClick={addTopic}>新增主题</button>
          <button className="btn btn-ghost daily-test-btn" onClick={removeTopic} disabled={config.topics.length <= 1}>删除当前主题</button>
        </div>

        <div className="daily-admin-card daily-progress-card">
          <div className="daily-panel-title">
            <span>生成进度</span>
            <button className="btn btn-ghost" onClick={() => void refreshProgress()}>刷新</button>
          </div>
          <div className="daily-progress-list">
            {progressItems.map((item) => (
              <article key={item.topicId} className={`daily-progress-item ${item.status} ${item.mode === "indeterminate" ? "indeterminate" : "determinate"}`}>
                <div className="daily-progress-head">
                  <strong>{item.topicName}</strong>
                  <span>{item.status === "running" ? "生成中" : item.status === "success" ? "已完成" : item.status === "error" ? "失败" : "空闲"}</span>
                </div>
                <div className="daily-progress-bar" aria-label={`${item.topicName} ${item.mode === "indeterminate" ? "阶段处理中" : `进度 ${item.percent}%`}`}>
                  <i style={item.mode === "indeterminate" ? undefined : { width: `${item.percent}%` }} />
                </div>
                <p className="daily-progress-message"><strong>{item.stage}</strong><span>{compactRunText(item.message)}</span></p>
                <small className="daily-progress-meta">
                  <span>{item.mode === "indeterminate" ? "等待外部服务返回" : `${item.current || 0}${item.total ? ` / ${item.total}` : ""}`}</span>
                  {item.detail && <span>{compactRunText(item.detail)}</span>}
                  <span>更新 {formatDate(item.updatedAt)}</span>
                  {item.latestRunAt && <span>最近日报 {formatDate(item.latestRunAt)}，{item.latestPaperCount ?? 0} 篇</span>}
                  {item.draftId && <span>暂存 {item.draftId}{item.draftStage ? `/${item.draftStage}` : ""}</span>}
                </small>
                {item.error && <pre className="daily-progress-error">{compactRunText(item.error)}</pre>}
                {item.draftCanResume && item.draftId && (
                  <button className="btn btn-ghost daily-test-btn" onClick={() => void handleResumeDraft(item.draftId || "")}>从暂存继续</button>
                )}
              </article>
            ))}
            {!progressItems.length && <p className="daily-hint">暂无进度记录。每日任务开始后会自动显示在这里。</p>}
          </div>
          <p className="daily-hint">进度采用可信阶段展示：检索、去重、评分等可计数阶段显示数量进度；LLM 写作和生图阶段只显示等待状态，避免伪造百分比。</p>
        </div>

        <div className="daily-admin-card daily-progress-card">
          <div className="daily-panel-title">
            <span>暂存任务</span>
            <button className="btn btn-ghost" onClick={() => void refreshProgress()}>刷新</button>
          </div>
          <div className="daily-progress-list">
            {draftItems.map((draft) => (
              <article key={draft.draftId} className={`daily-progress-item ${draft.canResume ? "error" : "running"}`}>
                <div className="daily-progress-head">
                  <strong>{draft.topicName || draft.topic}</strong>
                  <span>{draft.canResume ? "可继续" : draft.status}</span>
                </div>
                <p className="daily-progress-message"><strong>{draft.stage}</strong><span>{draft.paperCount} 篇暂存证据，全文片段 {draft.fullTextFetched} 篇</span></p>
                <small className="daily-progress-meta">
                  <span>暂存 {draft.draftId}</span>
                  <span>更新 {formatDate(draft.updatedAt)}</span>
                </small>
                {draft.error && <pre className="daily-progress-error">{compactRunText(draft.error)}</pre>}
                {draft.canResume && <button className="btn btn-ghost daily-test-btn" onClick={() => void handleResumeDraft(draft.draftId)}>调整配置后继续</button>}
              </article>
            ))}
            {!draftItems.length && <p className="daily-hint">暂无暂存任务。只有失败或等待继续的生成流程会出现在这里。</p>}
          </div>
        </div>

        {selectedTopic && (
          <div className="daily-admin-card">
            <h2>当前主题配置</h2>
            <label>显示名称<input value={selectedTopic.name} onChange={(e) => updateTopic({ name: e.target.value })} /></label>
            <label>公开路径<input value={selectedTopic.slug} onChange={(e) => updateTopic({ slug: slugify(e.target.value) })} /></label>
            <label>文献主题<textarea rows={3} value={selectedTopic.topic} onChange={(e) => updateTopic({ topic: e.target.value })} /></label>
            <div className="daily-grid-2">
              <label>文献数量<input type="number" min={20} max={500} value={selectedTopic.paperCount} onChange={(e) => updateTopic({ paperCount: fieldNumber(e.target.value, 80) })} /></label>
              <label>起始年份<input type="number" value={selectedTopic.sinceYear} onChange={(e) => updateTopic({ sinceYear: fieldNumber(e.target.value, tenYearsAgo()) })} /></label>
              <label>自动运行<select value={selectedTopic.scheduleEnabled ? "on" : "off"} onChange={(e) => updateTopic({ scheduleEnabled: e.target.value === "on" })}><option value="on">启用每日任务</option><option value="off">仅手动运行</option></select></label>
              <label>运行时间（北京时间）<input type="time" value={selectedTopic.scheduleTime} onChange={(e) => updateTopic({ scheduleTime: e.target.value })} /></label>
              <label>新鲜度<select value={selectedTopic.freshnessBoost} onChange={(e) => updateTopic({ freshnessBoost: e.target.value as ReviewTopicConfig["freshnessBoost"] })}><option value="STRONG">近 3 年强加权</option><option value="MILD">近 10 年温和加权</option><option value="NONE">不加权</option></select></label>
              <label>快捷年份<button type="button" className="btn btn-ghost daily-test-btn" onClick={() => updateTopic({ sinceYear: tenYearsAgo() })}>近十年</button></label>
            </div>
            <div className="daily-subpanel">
              <h3>Daily Delta 策略</h3>
              <div className="daily-grid-2">
                <label>新增日报最少高新颖文献<input type="number" min={1} max={selectedTopic.paperCount} placeholder="自动：目标数量的 30%" value={selectedTopic.minHighNoveltyCount ?? ""} onChange={(e) => updateTopic({ minHighNoveltyCount: e.target.value ? fieldNumber(e.target.value, 0) : null })} /></label>
                <label>最大重复比例<input type="number" min={0} max={1} step={0.05} value={selectedTopic.maxRepeatRatio ?? 0.75} onChange={(e) => updateTopic({ maxRepeatRatio: Math.max(0, Math.min(1, Number(e.target.value) || 0)) })} /></label>
              </div>
              <label>子议题池<textarea rows={4} value={(selectedTopic.subtopicPool ?? []).join("\n")} placeholder={"每行一个子议题，例如：\n数字孪生运维\n生成式结构优化\n传感器健康监测"} onChange={(e) => updateTopic({ subtopicPool: e.target.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) })} /></label>
              <div className="daily-checks">
                <label><input type="checkbox" checked={selectedTopic.allowTopicDeepDive ?? true} onChange={(e) => updateTopic({ allowTopicDeepDive: e.target.checked })} /> 新增不足时允许专题深挖</label>
                <label><input type="checkbox" checked={selectedTopic.allowNoSignificantUpdate ?? true} onChange={(e) => updateTopic({ allowNoSignificantUpdate: e.target.checked })} /> 允许生成“无显著更新”监测短报</label>
              </div>
            </div>
            <div className="daily-checks">
              <label><input type="checkbox" checked={selectedTopic.enabled} onChange={(e) => updateTopic({ enabled: e.target.checked })} /> 在公开导航目录中显示该主题</label>
              <label><input type="checkbox" checked={selectedTopic.privateOnly ?? false} onChange={(e) => updateTopic({ privateOnly: e.target.checked })} /> 管理员专属综述，不对游客公开</label>
              <label><input type="checkbox" checked={selectedTopic.includeFullText} onChange={(e) => updateTopic({ includeFullText: e.target.checked })} /> 读取可用全文片段</label>
              <label><input type="checkbox" checked={selectedTopic.includeWeb} onChange={(e) => updateTopic({ includeWeb: e.target.checked })} /> 允许联网补充策略</label>
            </div>
            <div className="daily-info-box">
              <strong>Daily Delta 会自动判断本期写作模式</strong>
              <span>新增高质量证据充足时生成“新增日报”；新增较少但有变化时生成“差异简报”；新增不足时转为“专题深挖”或“监测短报”。公开页会显示本期模式、新增数量、高新颖文献和每篇证据的相关性、质量、证据完整度、新颖度评分。</span>
            </div>
          </div>
        )}

        <div className="daily-admin-card">
          <h2>检索与综述模型</h2>
          <label>文献检索源<select value={config.literatureProvider ?? "sciverse"} onChange={(e) => setConfig((old) => ({ ...old, literatureProvider: e.target.value as DailyReviewConfig["literatureProvider"] }))}>
            <option value="sciverse">Sciverse</option>
            <option value="paper_search">Paper Search 多源</option>
            <option value="hybrid">Hybrid 混合检索</option>
          </select></label>
          <label>Paper Search 来源<input value={(config.paperSearchSources ?? []).join(",")} placeholder="semantic,openalex,crossref,europepmc,hal,base,core,unpaywall" onChange={(e) => setConfig((old) => ({ ...old, paperSearchSources: e.target.value.split(",").map((item) => item.trim()).filter(Boolean) }))} /></label>
          <label>专属综述访问密钥<input type="password" value={config.exclusiveAccessKey ?? ""} placeholder={maskedPlaceholder(config.exclusiveAccessKeyConfigured, "输入访问密钥")} onChange={(e) => setConfig((old) => ({ ...old, exclusiveAccessKey: e.target.value }))} /></label>
          <div className="daily-info-grid">
            <div>
              <strong>Hybrid 混合检索</strong>
              <span>先合并 Paper Search 多源与 Sciverse 结果，再按 DOI、标题和 uniqueId 去重，并根据相关性、质量、证据完整度、新颖度排序后交给综述模型。</span>
            </div>
            <div>
              <strong>Semantic Scholar 限速</strong>
              <span>配置环境变量后自动启用 API Key，并按官方要求控制在约 1 秒 1 次请求；被限流时按 Retry-After 重试一次。</span>
            </div>
          </div>
          <button className="btn btn-ghost daily-test-btn" onClick={() => void testConnection("paper_search")}>测试 Paper Search</button>
          {connectionStatus.paper_search && <p className={`daily-status ${connectionStatus.paper_search.startsWith("连接成功") ? "ok" : "bad"}`}>{connectionStatus.paper_search}</p>}
          <div className="daily-subpanel literature-cdk-panel">
            <div className="daily-section-head">
              <div>
                <h3>公开文献检索 CDK</h3>
                <p className="daily-hint">用户可用 CDK 借用管理员 LLM 做主题拆解；每个 CDK 可独立限制次数、有效期、最大文献数和检索源。</p>
              </div>
              <button type="button" className="btn btn-ghost daily-test-btn" onClick={addLiteratureCdk}>生成 CDK</button>
            </div>
            {(config.literatureSearchCdks ?? []).length === 0 && <p className="daily-hint">暂无 CDK。用户仍可在公开检索页填写自己的 LLM 服务。</p>}
            {(config.literatureSearchCdks ?? []).map((cdk) => (
              <div key={cdk.id} className="literature-cdk-card">
                <div className="daily-grid-2">
                  <label>名称<input value={cdk.name} onChange={(e) => updateLiteratureCdk(cdk.id, { name: e.target.value })} /></label>
                  <label>CDK<input value={cdk.code} placeholder={maskedPlaceholder(Boolean(cdk.code), "yfr-...")} onChange={(e) => updateLiteratureCdk(cdk.id, { code: e.target.value })} /></label>
                  <label>最大使用次数<input type="number" min={1} max={100000} value={cdk.maxUses} onChange={(e) => updateLiteratureCdk(cdk.id, { maxUses: fieldNumber(e.target.value, 50) })} /></label>
                  <label>已用次数<input type="number" min={0} max={cdk.maxUses} value={cdk.usedCount} onChange={(e) => updateLiteratureCdk(cdk.id, { usedCount: fieldNumber(e.target.value, 0) })} /></label>
                  <label>单次最大文献数<input type="number" min={5} max={200} value={cdk.paperCountMax} onChange={(e) => updateLiteratureCdk(cdk.id, { paperCountMax: fieldNumber(e.target.value, 100) })} /></label>
                  <label>过期时间（北京时间）<input type="datetime-local" value={(cdk.expiresAt ?? "").slice(0, 16)} onChange={(e) => updateLiteratureCdk(cdk.id, { expiresAt: e.target.value || null })} /></label>
                  <label>检索源覆盖<select value={cdk.literatureProvider ?? ""} onChange={(e) => updateLiteratureCdk(cdk.id, { literatureProvider: e.target.value ? e.target.value as DailyReviewConfig["literatureProvider"] : null })}>
                    <option value="">跟随全局配置</option>
                    <option value="sciverse">Sciverse</option>
                    <option value="paper_search">Paper Search 多源</option>
                    <option value="hybrid">Hybrid 混合检索</option>
                  </select></label>
                  <label>Paper Search 来源覆盖<input value={(cdk.paperSearchSources ?? []).join(",")} placeholder="留空则跟随全局" onChange={(e) => updateLiteratureCdk(cdk.id, { paperSearchSources: e.target.value.split(",").map((item) => item.trim()).filter(Boolean) })} /></label>
                </div>
                <label>备注<input value={cdk.note ?? ""} onChange={(e) => updateLiteratureCdk(cdk.id, { note: e.target.value })} /></label>
                <div className="daily-checks literature-cdk-actions">
                  <label><input type="checkbox" checked={cdk.enabled} onChange={(e) => updateLiteratureCdk(cdk.id, { enabled: e.target.checked })} /> 启用该 CDK</label>
                  <span>剩余 {Math.max(0, cdk.maxUses - cdk.usedCount)} 次</span>
                  <button type="button" className="btn btn-ghost daily-test-btn" onClick={() => updateLiteratureCdk(cdk.id, { enabled: false })}>立即停用</button>
                  <button type="button" className="btn btn-ghost daily-test-btn" onClick={() => updateLiteratureCdk(cdk.id, { code: randomCdk(), usedCount: 0 })}>重置 CDK</button>
                  <button type="button" className="btn btn-ghost daily-test-btn" onClick={() => removeLiteratureCdk(cdk.id)}>删除</button>
                </div>
              </div>
            ))}
          </div>
          <label>Sciverse Token<input type="password" value={config.sciverseApiToken} placeholder={maskedPlaceholder(config.sciverseTokenConfigured, "Bearer token")} onChange={(e) => setConfig((old) => ({ ...old, sciverseApiToken: e.target.value }))} /></label>
          <button className="btn btn-ghost daily-test-btn" onClick={() => void testConnection("sciverse")}>测试 Sciverse</button>
          {connectionStatus.sciverse && <p className={`daily-status ${connectionStatus.sciverse.startsWith("连接成功") ? "ok" : "bad"}`}>{connectionStatus.sciverse}</p>}
          <label>LLM Base URL<input value={config.llm.baseUrl} onChange={(e) => setConfig((old) => ({ ...old, llm: { ...old.llm, baseUrl: e.target.value } }))} /></label>
          <label>LLM API Key<input type="password" value={config.llm.apiKey} placeholder={maskedPlaceholder(config.llmKeyConfigured, "sk-...")} onChange={(e) => setConfig((old) => ({ ...old, llm: { ...old.llm, apiKey: e.target.value } }))} /></label>
          <label>LLM Model<input value={config.llm.model} onChange={(e) => setConfig((old) => ({ ...old, llm: { ...old.llm, model: e.target.value } }))} /></label>
          <label>Max Tokens<input type="number" value={config.llm.maxTokens} onChange={(e) => setConfig((old) => ({ ...old, llm: { ...old.llm, maxTokens: fieldNumber(e.target.value, 24000) } }))} /></label>
          <button className="btn btn-ghost daily-test-btn" onClick={() => void testConnection("llm")}>测试综述 LLM</button>
          {connectionStatus.llm && <p className={`daily-status ${connectionStatus.llm.startsWith("连接成功") ? "ok" : "bad"}`}>{connectionStatus.llm}</p>}
        </div>

        <div className="daily-admin-card">
          <h2>翻译模型</h2>
          <p className="daily-hint">未配置时，公开页面会提醒用户直接使用浏览器翻译。系统不会自动翻译英文内容，用户点击后才调用模型。</p>
          <label>Base URL<input value={config.translation.baseUrl} onChange={(e) => setConfig((old) => ({ ...old, translation: { ...old.translation, baseUrl: e.target.value } }))} /></label>
          <label>API Key<input type="password" value={config.translation.apiKey} placeholder={maskedPlaceholder(config.translationKeyConfigured, "sk-...")} onChange={(e) => setConfig((old) => ({ ...old, translation: { ...old.translation, apiKey: e.target.value } }))} /></label>
          <label>Model<input value={config.translation.model} onChange={(e) => setConfig((old) => ({ ...old, translation: { ...old.translation, model: e.target.value } }))} /></label>
          <button className="btn btn-ghost daily-test-btn" onClick={() => void testConnection("translation")}>测试翻译模型</button>
          {connectionStatus.translation && <p className={`daily-status ${connectionStatus.translation.startsWith("连接成功") ? "ok" : "bad"}`}>{connectionStatus.translation}</p>}
        </div>

        <div className="daily-admin-card">
          <h2>生图模型</h2>
          <label><input type="checkbox" checked={config.image.enabled} onChange={(e) => setConfig((old) => ({ ...old, image: { ...old.image, enabled: e.target.checked } }))} /> 在综述中生成“一图看懂”</label>
          <label>Base URL<input value={config.image.baseUrl} onChange={(e) => setConfig((old) => ({ ...old, image: { ...old.image, baseUrl: e.target.value } }))} /></label>
          <label>API Key<input type="password" value={config.image.apiKey} placeholder={maskedPlaceholder(config.imageKeyConfigured, "sk-...")} onChange={(e) => setConfig((old) => ({ ...old, image: { ...old.image, apiKey: e.target.value } }))} /></label>
          <label>Model<input value={config.image.model} onChange={(e) => setConfig((old) => ({ ...old, image: { ...old.image, model: e.target.value } }))} /></label>
          <label>Size<input value={config.image.size} onChange={(e) => setConfig((old) => ({ ...old, image: { ...old.image, size: e.target.value } }))} /></label>
          <button className="btn btn-ghost daily-test-btn" onClick={() => void testConnection("image")}>真实测试生图</button>
          {connectionStatus.image && <p className={`daily-status ${connectionStatus.image.startsWith("连接成功") ? "ok" : "bad"}`}>{connectionStatus.image}</p>}
        </div>

        <div className="daily-admin-card">
          <h2>管理员密码</h2>
          <label>原密码<input type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} /></label>
          <label>新密码<input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} /></label>
          <button className="btn btn-ghost daily-test-btn" onClick={() => void handlePasswordChange()}>修改管理员密码</button>
        </div>

        <div className="daily-admin-card daily-admin-actions">
          <button className="btn" onClick={() => void handleSave()} disabled={saving}>{saving ? "保存中..." : "保存配置"}</button>
          <button className="daily-run" onClick={() => void handleRun()} disabled={running || selectedProgress?.status === "running" || !selectedTopic}>
            {running || selectedProgress?.status === "running" ? `生成中 ${selectedProgress?.percent ?? 0}%` : `运行当前主题：${selectedTopic?.name ?? ""}`}
          </button>
          <a className="btn btn-ghost" href="/admin/wechat">公众号模块</a>
          <a className="btn btn-ghost" href="/admin/exclusive-review">专属综述</a>
          {selectedTopic && (
            <a className="btn btn-ghost" href={selectedTopic.privateOnly ? `/admin/exclusive-review/${selectedTopic.slug}` : `/daily-review/${selectedTopic.slug}`}>
              {selectedTopic.privateOnly ? "打开专属综述" : "打开公开路径"}
            </a>
          )}
          {status && <p className="daily-status ok">{status}</p>}
          {error && <p className="daily-status bad">{error}</p>}
        </div>
      </section>
    </main>
  );
}
