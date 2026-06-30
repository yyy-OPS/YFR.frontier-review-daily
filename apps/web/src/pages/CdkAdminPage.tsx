import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  DailyReviewConfig,
  LiteratureSearchCdkConfig,
  LiteratureSearchSummary,
  getAdminLiteratureSearches,
  getDailyReviewConfig,
  saveDailyReviewConfig,
} from "../api/client";

const ADMIN_TOKEN_KEY = "frontier_review_admin_token";

function fieldNumber(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function randomCdk(): string {
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  return `yfr-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

function newCdk(index = 1): LiteratureSearchCdkConfig {
  return {
    id: `cdk-${Date.now().toString(36)}-${index}`,
    name: `文献检索 CDK ${index}`,
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

function formatTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function cdkUsageText(cdk: LiteratureSearchCdkConfig) {
  return `${Math.max(0, cdk.maxUses - cdk.usedCount)} / ${cdk.maxUses}`;
}

export function CdkAdminPage() {
  const [adminToken] = useState(() => localStorage.getItem(ADMIN_TOKEN_KEY) || "");
  const [config, setConfig] = useState<DailyReviewConfig | null>(null);
  const [records, setRecords] = useState<LiteratureSearchSummary[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [expandedId, setExpandedId] = useState<string>("");
  const [activeCdkId, setActiveCdkId] = useState<string>("");
  const [batchCount, setBatchCount] = useState(5);
  const [batchMaxUses, setBatchMaxUses] = useState(50);
  const [batchPaperMax, setBatchPaperMax] = useState(100);
  const [batchExpiresAt, setBatchExpiresAt] = useState("");
  const [batchNote, setBatchNote] = useState("");
  const [loading, setLoading] = useState(Boolean(adminToken));
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const recordsRequestRef = useRef(0);

  const cdks = config?.literatureSearchCdks ?? [];
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const selectedCdks = cdks.filter((item) => selectedSet.has(item.id));
  const cdkIdsSignature = useMemo(() => cdks.map((item) => item.id).join(","), [cdks]);

  useEffect(() => {
    document.title = "YFR CDK 管理";
  }, []);

  useEffect(() => {
    if (!adminToken) return;
    let alive = true;
    setLoading(true);
    setError("");
    getDailyReviewConfig(adminToken)
      .then((data) => {
        if (!alive) return;
        setConfig(data);
        setActiveCdkId((old) => old || data.literatureSearchCdks?.[0]?.id || "");
      })
      .catch((e) => {
        if (alive) setError(e instanceof ApiError ? e.message : "读取 CDK 配置失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [adminToken]);

  useEffect(() => {
    if (!activeCdkId || !cdkIdsSignature) return;
    if (!cdks.some((item) => item.id === activeCdkId)) {
      setActiveCdkId(cdks[0]?.id || "");
    }
  }, [activeCdkId, cdks, cdkIdsSignature]);

  useEffect(() => {
    if (!adminToken || !activeCdkId) {
      setRecords([]);
      return;
    }
    const requestId = ++recordsRequestRef.current;
    setRecordsLoading(true);
    getAdminLiteratureSearches(adminToken, activeCdkId, 120)
      .then((data) => {
        if (recordsRequestRef.current === requestId) setRecords(data.items);
      })
      .catch((e) => {
        if (recordsRequestRef.current === requestId) setError(e instanceof ApiError ? e.message : "读取文献检索记录失败");
      })
      .finally(() => {
        if (recordsRequestRef.current === requestId) setRecordsLoading(false);
      });
  }, [adminToken, activeCdkId]);

  function updateCdk(id: string, patch: Partial<LiteratureSearchCdkConfig>) {
    setConfig((old) =>
      old
        ? {
            ...old,
            literatureSearchCdks: (old.literatureSearchCdks ?? []).map((item) => (item.id === id ? { ...item, ...patch } : item)),
          }
        : old,
    );
  }

  function addBatch() {
    const next = Array.from({ length: Math.max(1, Math.min(200, batchCount)) }, (_, index) => ({
      ...newCdk(index + 1),
      maxUses: batchMaxUses,
      paperCountMax: batchPaperMax,
      expiresAt: batchExpiresAt || null,
      note: batchNote,
    }));
    setConfig((old) => (old ? { ...old, literatureSearchCdks: [...(old.literatureSearchCdks ?? []), ...next] } : old));
    setSelectedIds(next.map((item) => item.id));
    setExpandedId(next[0]?.id ?? "");
    setActiveCdkId(next[0]?.id ?? activeCdkId);
    setStatus(`已生成 ${next.length} 个 CDK，保存后生效。`);
  }

  function removeSelected() {
    setConfig((old) => (old ? { ...old, literatureSearchCdks: (old.literatureSearchCdks ?? []).filter((item) => !selectedSet.has(item.id)) } : old));
    setSelectedIds([]);
    setExpandedId("");
  }

  function patchSelected(patch: Partial<LiteratureSearchCdkConfig>) {
    setConfig((old) =>
      old
        ? {
            ...old,
            literatureSearchCdks: (old.literatureSearchCdks ?? []).map((item) => (selectedSet.has(item.id) ? { ...item, ...patch } : item)),
          }
        : old,
    );
  }

  async function copySelected() {
    const text = (selectedCdks.length ? selectedCdks : cdks)
      .map((item) => `${item.name}\t${item.code}\t剩余 ${cdkUsageText(item)}\t单次最多 ${item.paperCountMax} 篇`)
      .join("\n");
    await navigator.clipboard?.writeText(text);
    setStatus(`已复制 ${selectedCdks.length || cdks.length} 个 CDK。`);
  }

  async function save() {
    if (!config) return;
    setSaving(true);
    setError("");
    try {
      const saved = await saveDailyReviewConfig(config, adminToken, "cdks");
      setConfig(saved);
      setStatus("CDK 配置已保存。");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "保存 CDK 配置失败");
    } finally {
      setSaving(false);
    }
  }

  if (!adminToken) {
    return (
      <main className="daily-page standalone">
        <div className="daily-loading">请先登录管理员后台，再访问 CDK 管理页。</div>
      </main>
    );
  }

  if (loading && !config) {
    return (
      <main className="daily-page standalone">
        <div className="daily-loading">正在读取 CDK 配置...</div>
      </main>
    );
  }

  return (
    <main className="daily-page standalone cdk-admin-page">
      <section className="daily-hero">
        <div>
          <p className="daily-kicker">YFR Admin</p>
          <h1>CDK 管理</h1>
          <p className="daily-subtitle">集中管理公开文献检索 CDK。记录只在切换当前 CDK 时加载，避免编辑配置时重复请求。</p>
        </div>
        <div className="daily-hero-metrics">
          <span><strong>{cdks.length}</strong>CDK</span>
          <span><strong>{cdks.filter((item) => item.enabled).length}</strong>启用</span>
          <span><strong>{records.length}</strong>记录</span>
        </div>
      </section>

      <section className="daily-admin-page cdk-admin-layout">
        <div className="daily-admin-card">
          <h2>批量生成</h2>
          <div className="daily-grid-2">
            <label>生成数量<input type="number" min={1} max={200} value={batchCount} onChange={(e) => setBatchCount(fieldNumber(e.target.value, 5))} /></label>
            <label>最大使用次数<input type="number" min={1} max={100000} value={batchMaxUses} onChange={(e) => setBatchMaxUses(fieldNumber(e.target.value, 50))} /></label>
            <label>单次最大文献数<input type="number" min={5} max={200} value={batchPaperMax} onChange={(e) => setBatchPaperMax(fieldNumber(e.target.value, 100))} /></label>
            <label>过期时间（北京时间）<input type="datetime-local" value={batchExpiresAt} onChange={(e) => setBatchExpiresAt(e.target.value)} /></label>
          </div>
          <label>批量备注<input value={batchNote} onChange={(e) => setBatchNote(e.target.value)} placeholder="例如：测试用户 / 社区体验 / 内部试用" /></label>
          <div className="cdk-actions">
            <button className="btn" type="button" onClick={addBatch}>批量生成</button>
            <button className="btn btn-ghost" type="button" onClick={() => void copySelected()} disabled={!cdks.length}>批量复制</button>
            <button className="btn btn-ghost" type="button" onClick={() => void save()} disabled={!config || saving}>{saving ? "保存中..." : "保存配置"}</button>
            <a className="btn btn-ghost" href="/admin/CDK/records">检索记录</a>
            <a className="btn btn-ghost" href="/admin">返回主管理员后台</a>
          </div>
          {status && <p className="daily-status ok">{status}</p>}
          {error && <p className="daily-status bad">{error}</p>}
        </div>

        <div className="daily-admin-card cdk-bulk-card">
          <h2>批量修改</h2>
          <p className="daily-hint">先勾选 CDK，再批量启停、重置次数或调整限制。</p>
          <div className="cdk-actions">
            <button className="btn btn-ghost" type="button" onClick={() => patchSelected({ enabled: true })} disabled={!selectedIds.length}>启用所选</button>
            <button className="btn btn-ghost" type="button" onClick={() => patchSelected({ enabled: false })} disabled={!selectedIds.length}>停用所选</button>
            <button className="btn btn-ghost" type="button" onClick={() => patchSelected({ usedCount: 0 })} disabled={!selectedIds.length}>重置次数</button>
            <button className="btn btn-ghost" type="button" onClick={removeSelected} disabled={!selectedIds.length}>删除所选</button>
          </div>
          <div className="daily-grid-2">
            <label>使用次数改为<input type="number" min={1} max={100000} onBlur={(e) => e.target.value && patchSelected({ maxUses: fieldNumber(e.target.value, 50) })} /></label>
            <label>文献上限改为<input type="number" min={5} max={200} onBlur={(e) => e.target.value && patchSelected({ paperCountMax: fieldNumber(e.target.value, 100) })} /></label>
            <label>过期时间改为<input type="datetime-local" onBlur={(e) => patchSelected({ expiresAt: e.target.value || null })} /></label>
            <label>检索源改为<select onChange={(e) => patchSelected({ literatureProvider: e.target.value ? (e.target.value as LiteratureSearchCdkConfig["literatureProvider"]) : null })} defaultValue="">
              <option value="">跟随全局配置</option>
              <option value="sciverse">Sciverse</option>
              <option value="paper_search">Paper Search 多源</option>
              <option value="hybrid">Hybrid 混合检索</option>
            </select></label>
          </div>
        </div>

        <div className="daily-admin-card cdk-table-card">
          <div className="daily-panel-title">
            <span>CDK 列表</span>
            <label className="cdk-select-all"><input type="checkbox" checked={cdks.length > 0 && selectedIds.length === cdks.length} onChange={(e) => setSelectedIds(e.target.checked ? cdks.map((item) => item.id) : [])} /> 全选</label>
          </div>
          <div className="cdk-table compact">
            {cdks.map((cdk) => {
              const expanded = expandedId === cdk.id;
              return (
                <article key={cdk.id} className={`cdk-row ${expanded ? "expanded" : ""}`}>
                  <label className="cdk-check"><input type="checkbox" checked={selectedSet.has(cdk.id)} onChange={(e) => setSelectedIds((old) => (e.target.checked ? [...old, cdk.id] : old.filter((id) => id !== cdk.id)))} /></label>
                  <div className="cdk-row-main">
                    <button type="button" className="cdk-summary" onClick={() => setActiveCdkId(cdk.id)}>
                      <strong>{cdk.name}</strong>
                      <span>{cdk.enabled ? "启用" : "停用"} · 剩余 {cdkUsageText(cdk)} · 单次 {cdk.paperCountMax} 篇</span>
                    </button>
                    <div className="cdk-actions compact">
                      <button type="button" className="btn btn-ghost daily-test-btn" onClick={() => setExpandedId(expanded ? "" : cdk.id)}>{expanded ? "收起编辑" : "编辑"}</button>
                      <button type="button" className="btn btn-ghost daily-test-btn" onClick={() => setActiveCdkId(cdk.id)}>查看记录</button>
                      <button type="button" className="btn btn-ghost daily-test-btn" onClick={() => updateCdk(cdk.id, { enabled: !cdk.enabled })}>{cdk.enabled ? "停用" : "启用"}</button>
                    </div>
                    {expanded && (
                      <div className="cdk-edit-panel">
                        <div className="daily-grid-2">
                          <label>名称<input value={cdk.name} onChange={(e) => updateCdk(cdk.id, { name: e.target.value })} /></label>
                          <label>CDK<input value={cdk.code} onChange={(e) => updateCdk(cdk.id, { code: e.target.value })} /></label>
                          <label>最大使用次数<input type="number" min={1} max={100000} value={cdk.maxUses} onChange={(e) => updateCdk(cdk.id, { maxUses: fieldNumber(e.target.value, 50) })} /></label>
                          <label>已用次数<input type="number" min={0} max={cdk.maxUses} value={cdk.usedCount} onChange={(e) => updateCdk(cdk.id, { usedCount: fieldNumber(e.target.value, 0) })} /></label>
                          <label>单次最大文献数<input type="number" min={5} max={200} value={cdk.paperCountMax} onChange={(e) => updateCdk(cdk.id, { paperCountMax: fieldNumber(e.target.value, 100) })} /></label>
                          <label>过期时间（北京时间）<input type="datetime-local" value={(cdk.expiresAt ?? "").slice(0, 16)} onChange={(e) => updateCdk(cdk.id, { expiresAt: e.target.value || null })} /></label>
                        </div>
                        <label>备注<input value={cdk.note ?? ""} onChange={(e) => updateCdk(cdk.id, { note: e.target.value })} /></label>
                        <div className="daily-checks literature-cdk-actions">
                          <label><input type="checkbox" checked={cdk.enabled} onChange={(e) => updateCdk(cdk.id, { enabled: e.target.checked })} /> 启用</label>
                          <button type="button" className="btn btn-ghost daily-test-btn" onClick={() => updateCdk(cdk.id, { code: randomCdk(), usedCount: 0 })}>重置 CDK</button>
                        </div>
                      </div>
                    )}
                  </div>
                </article>
              );
            })}
            {!cdks.length && <p className="daily-hint">暂无 CDK。可批量生成后保存配置。</p>}
          </div>
        </div>

        <div className="daily-admin-card cdk-record-card">
          <div className="daily-panel-title">
            <span>CDK 生成的文献检索</span>
            <select value={activeCdkId} onChange={(e) => setActiveCdkId(e.target.value)}>
              <option value="">选择 CDK</option>
              {cdks.map((cdk) => <option key={cdk.id} value={cdk.id}>{cdk.name}</option>)}
            </select>
          </div>
          {recordsLoading && <p className="daily-hint">正在读取当前 CDK 的检索记录...</p>}
          <div className="cdk-record-list">
            {records.map((item) => (
              <a key={item.searchId} className="cdk-record-item" href={item.sharePath}>
                <strong>{item.topic}</strong>
                <span>{item.returned}/{item.requested} 篇 · {item.sinceYear || "-"} 年以来 · {item.literatureProvider || "-"} · {formatTime(item.createdAt)}</span>
              </a>
            ))}
            {!recordsLoading && !records.length && <p className="daily-hint">该 CDK 暂无文献检索记录。新的检索完成后会自动出现在这里。</p>}
          </div>
        </div>
      </section>
    </main>
  );
}
