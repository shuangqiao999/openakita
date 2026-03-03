import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface Agent {
  id: string;
  name: string;
  description: string;
  category: string;
  authorName?: string;
  downloads: number;
  avgRating?: number;
  ratingCount?: number;
  latestVersion?: string;
  tags?: string[];
  isFeatured?: boolean;
}

interface AgentStoreViewProps {
  apiBaseUrl: string;
  visible: boolean;
}

export function AgentStoreView({ apiBaseUrl, visible }: AgentStoreViewProps) {
  const { t } = useTranslation();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("downloads");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [installing, setInstalling] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  const fetchAgents = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ sort, page: String(page), limit: "20" });
      if (query) params.set("q", query);
      if (category) params.set("category", category);
      const resp = await fetch(`${apiBaseUrl}/api/hub/agents?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setAgents(data.agents || data.data || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e.message || "无法连接到 Agent Store");
      setAgents([]);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, query, category, sort, page]);

  useEffect(() => {
    if (visible) fetchAgents();
  }, [visible, fetchAgents]);

  const handleInstall = async (agentId: string) => {
    setInstalling(agentId);
    setNotice("");
    try {
      const resp = await fetch(`${apiBaseUrl}/api/hub/agents/${agentId}/install`, { method: "POST" });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setNotice(`✅ ${data.profile?.name || agentId} 安装成功！`);
    } catch (e: any) {
      setNotice(`❌ 安装失败: ${e.message}`);
    } finally {
      setInstalling(null);
    }
  };

  if (!visible) return null;

  return (
    <div>
      <div className="card" style={{ marginBottom: 16 }}>
        <h2 className="cardTitle" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          🏪 Agent Store
        </h2>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "4px 0 16px" }}>
          从 OpenAkita 社区发现并安装 Agent（需要网络连接，本地导入导出不受影响）
        </p>

        <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <input
            type="text"
            placeholder="搜索 Agent..."
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            onKeyDown={(e) => e.key === "Enter" && fetchAgents()}
            style={{ flex: 1, minWidth: 200 }}
          />
          <select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
            <option value="">所有分类</option>
            <option value="customer_service">客服</option>
            <option value="development">开发</option>
            <option value="business">商务</option>
            <option value="creative">创意</option>
            <option value="education">教育</option>
            <option value="productivity">效率</option>
            <option value="general">通用</option>
          </select>
          <select value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
            <option value="downloads">按下载量</option>
            <option value="rating">按评分</option>
            <option value="newest">最新</option>
          </select>
          <button onClick={fetchAgents} disabled={loading}>
            {loading ? "搜索中..." : "搜索"}
          </button>
        </div>

        {notice && (
          <div style={{
            padding: "8px 12px", marginBottom: 12, borderRadius: 6,
            background: notice.startsWith("✅") ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)",
            color: notice.startsWith("✅") ? "#16a34a" : "#dc2626",
            fontSize: 13,
          }}>
            {notice}
          </div>
        )}
      </div>

      {error && (
        <div className="card" style={{ textAlign: "center", padding: "24px 16px" }}>
          <p style={{ color: "#dc2626", marginBottom: 8 }}>⚠️ 无法连接到远程 Agent Store</p>
          <p style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6 }}>
            远程市场暂时不可用，这不影响本地功能。<br />
            你可以在侧栏「Agent 管理」中继续使用本地 Agent 导入导出功能，<br />
            也可以通过 <code>.akita-agent</code> 文件直接分享和导入 Agent。
          </p>
          <button onClick={fetchAgents} style={{ marginTop: 12 }}>重试连接</button>
        </div>
      )}

      {!loading && !error && agents.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: 40 }}>
          <p style={{ color: "var(--muted)", fontSize: 15 }}>暂无 Agent</p>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
        {agents.map((a) => (
          <div key={a.id} className="card" style={{ position: "relative" }}>
            {a.isFeatured && (
              <span style={{
                position: "absolute", top: 8, right: 8, fontSize: 10, padding: "2px 6px",
                background: "var(--accent)", color: "#fff", borderRadius: 4, fontWeight: 600,
              }}>
                精选
              </span>
            )}
            <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4 }}>
              {a.name}
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8, lineHeight: 1.5 }}>
              {a.description?.slice(0, 120) || "暂无描述"}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
              <span>📥 {a.downloads}</span>
              {a.avgRating != null && a.avgRating > 0 && <span>⭐ {a.avgRating.toFixed(1)}</span>}
              {a.latestVersion && <span>v{a.latestVersion}</span>}
              {a.authorName && <span>by {a.authorName}</span>}
            </div>
            {a.tags && a.tags.length > 0 && (
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 8 }}>
                {a.tags.slice(0, 4).map((tag) => (
                  <span key={tag} style={{
                    fontSize: 10, padding: "2px 6px", borderRadius: 4,
                    background: "var(--bg-hover, #f1f5f9)", color: "var(--muted)",
                  }}>
                    {tag}
                  </span>
                ))}
              </div>
            )}
            <button
              onClick={() => handleInstall(a.id)}
              disabled={installing === a.id}
              style={{ width: "100%", marginTop: 4 }}
            >
              {installing === a.id ? "安装中..." : "安装"}
            </button>
          </div>
        ))}
      </div>

      {total > 20 && (
        <div style={{ display: "flex", justifyContent: "center", gap: 8, marginTop: 16 }}>
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
          <span style={{ fontSize: 13, color: "var(--muted)", lineHeight: "32px" }}>
            第 {page} 页 / 共 {Math.ceil(total / 20)} 页
          </span>
          <button disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}
    </div>
  );
}
