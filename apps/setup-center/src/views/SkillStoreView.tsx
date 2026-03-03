import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

interface Skill {
  id: string;
  name: string;
  description: string;
  category: string;
  trustLevel: "official" | "certified" | "community";
  authorName?: string;
  installCount: number;
  avgRating?: number;
  ratingCount?: number;
  version?: string;
  githubStars?: number;
  sourceRepo?: string;
}

interface SkillStoreViewProps {
  apiBaseUrl: string;
  visible: boolean;
}

const TRUST_BADGE: Record<string, { label: string; color: string; bg: string }> = {
  official: { label: "🏛️ 官方", color: "#1d4ed8", bg: "rgba(59,130,246,0.1)" },
  certified: { label: "✅ 认证", color: "#15803d", bg: "rgba(34,197,94,0.1)" },
  community: { label: "🌐 社区", color: "#6b7280", bg: "rgba(107,114,128,0.1)" },
};

export function SkillStoreView({ apiBaseUrl, visible }: SkillStoreViewProps) {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [trustLevel, setTrustLevel] = useState("");
  const [sort, setSort] = useState("installs");
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [installing, setInstalling] = useState<string | null>(null);
  const [notice, setNotice] = useState("");

  const fetchSkills = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ sort, page: String(page), limit: "20" });
      if (query) params.set("q", query);
      if (category) params.set("category", category);
      if (trustLevel) params.set("trust_level", trustLevel);
      const resp = await fetch(`${apiBaseUrl}/api/hub/skills?${params}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      setSkills(data.skills || data.data || []);
      setTotal(data.total || 0);
    } catch (e: any) {
      setError(e.message || "无法连接到 Skill Store");
      setSkills([]);
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, query, category, trustLevel, sort, page]);

  useEffect(() => {
    if (visible) fetchSkills();
  }, [visible, fetchSkills]);

  const handleInstall = async (skillId: string) => {
    setInstalling(skillId);
    setNotice("");
    try {
      const resp = await fetch(`${apiBaseUrl}/api/hub/skills/${skillId}/install`, { method: "POST" });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      setNotice(`✅ ${data.skill_name || skillId} 安装成功！`);
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
          🧩 Skill Store
        </h2>
        <p style={{ color: "var(--muted)", fontSize: 13, margin: "4px 0 16px" }}>
          从 OpenAkita 社区发现并安装技能（需要网络连接，本地技能管理和 skills.sh 市场不受影响）
        </p>

        <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <input
            type="text"
            placeholder="搜索 Skill..."
            value={query}
            onChange={(e) => { setQuery(e.target.value); setPage(1); }}
            onKeyDown={(e) => e.key === "Enter" && fetchSkills()}
            style={{ flex: 1, minWidth: 200 }}
          />
          <select value={trustLevel} onChange={(e) => { setTrustLevel(e.target.value); setPage(1); }}>
            <option value="">所有等级</option>
            <option value="official">官方</option>
            <option value="certified">认证</option>
            <option value="community">社区</option>
          </select>
          <select value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
            <option value="">所有分类</option>
            <option value="general">通用</option>
            <option value="development">开发</option>
            <option value="productivity">效率</option>
            <option value="data">数据</option>
            <option value="creative">创意</option>
            <option value="communication">通信</option>
          </select>
          <select value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
            <option value="installs">按安装量</option>
            <option value="rating">按评分</option>
            <option value="newest">最新</option>
            <option value="stars">GitHub Stars</option>
          </select>
          <button onClick={fetchSkills} disabled={loading}>
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
          <p style={{ color: "#dc2626", marginBottom: 8 }}>⚠️ 无法连接到远程 Skill Store</p>
          <p style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.6 }}>
            远程市场暂时不可用，这不影响本地功能。<br />
            你可以在侧栏「技能管理」中继续管理本地技能，<br />
            也可以通过「技能管理 → 浏览市场」从 skills.sh 搜索安装社区技能。
          </p>
          <button onClick={fetchSkills} style={{ marginTop: 12 }}>重试连接</button>
        </div>
      )}

      {!loading && !error && skills.length === 0 && (
        <div className="card" style={{ textAlign: "center", padding: 40 }}>
          <p style={{ color: "var(--muted)", fontSize: 15 }}>暂无 Skill</p>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
        {skills.map((s) => {
          const badge = TRUST_BADGE[s.trustLevel] || TRUST_BADGE.community;
          return (
            <div key={s.id} className="card" style={{ position: "relative" }}>
              <span style={{
                position: "absolute", top: 8, right: 8, fontSize: 10, padding: "2px 6px",
                background: badge.bg, color: badge.color, borderRadius: 4, fontWeight: 600,
              }}>
                {badge.label}
              </span>
              <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 4, fontFamily: "monospace" }}>
                {s.name}
              </div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8, lineHeight: 1.5 }}>
                {s.description?.slice(0, 120) || "暂无描述"}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
                <span>📥 {s.installCount}</span>
                {s.avgRating != null && s.avgRating > 0 && <span>⭐ {s.avgRating.toFixed(1)}</span>}
                {s.githubStars != null && s.githubStars > 0 && <span>★ {s.githubStars}</span>}
                {s.version && <span>v{s.version}</span>}
                {s.authorName && <span>by {s.authorName}</span>}
              </div>
              {s.sourceRepo && (
                <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 8 }}>
                  <a
                    href={`https://github.com/${s.sourceRepo}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{ color: "var(--accent, #5B8DEF)", textDecoration: "none" }}
                  >
                    {s.sourceRepo}
                  </a>
                </div>
              )}
              <button
                onClick={() => handleInstall(s.id)}
                disabled={installing === s.id}
                style={{ width: "100%", marginTop: 4 }}
              >
                {installing === s.id ? "安装中..." : "安装"}
              </button>
            </div>
          );
        })}
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
