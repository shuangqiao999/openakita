import { useState, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { IconBot, IconRefresh, IconPlus, IconEdit, IconTrash } from "../icons";

type AgentProfile = {
  id: string;
  name: string;
  description: string;
  icon: string;
  color: string;
  type: string;
  skills: string[];
  skills_mode: string;
  custom_prompt: string;
};

type SkillItem = {
  name: string;
  enabled: boolean;
  name_i18n?: Record<string, string> | null;
};

const EMPTY_PROFILE: AgentProfile = {
  id: "",
  name: "",
  description: "",
  icon: "🤖",
  color: "#6b7280",
  type: "custom",
  skills: [],
  skills_mode: "all",
  custom_prompt: "",
};

const EMOJI_PRESETS = [
  "🤖", "🧠", "💡", "🎯", "📊", "🔍", "🛠️", "📝",
  "🌐", "🚀", "⚡", "🎨", "📚", "🔬", "💻", "🎵",
];

export function AgentManagerView({
  apiBaseUrl = "http://127.0.0.1:18900",
  visible = true,
  multiAgentEnabled = false,
}: {
  apiBaseUrl?: string;
  visible?: boolean;
  multiAgentEnabled?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingProfile, setEditingProfile] = useState<AgentProfile>(EMPTY_PROFILE);
  const [isCreating, setIsCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [availableSkills, setAvailableSkills] = useState<SkillItem[]>([]);
  const [emojiPickerOpen, setEmojiPickerOpen] = useState(false);
  const [toastMsg, setToastMsg] = useState<{ text: string; type: "ok" | "err" } | null>(null);

  const showToast = useCallback((text: string, type: "ok" | "err" = "ok") => {
    setToastMsg({ text, type });
    setTimeout(() => setToastMsg(null), 3500);
  }, []);

  const extractErrorMsg = (detail: unknown, fallback: string): string => {
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = detail
        .map((d: Record<string, unknown>) => typeof d?.msg === "string" ? d.msg : "")
        .filter(Boolean);
      return msgs.length ? msgs.join("; ") : fallback;
    }
    if (typeof detail === "object" && detail !== null) {
      const d = detail as Record<string, unknown>;
      if (typeof d.msg === "string") return d.msg;
      try { return JSON.stringify(detail); } catch { /* fall through */ }
    }
    return fallback;
  };

  const fetchProfiles = useCallback(async () => {
    if (!multiAgentEnabled) return;
    setLoading(true);
    try {
      const res = await fetch(`${apiBaseUrl}/api/agents/profiles`);
      if (res.ok) {
        const data = await res.json();
        setProfiles(data.profiles || []);
      }
    } catch (e) {
      console.warn("Failed to fetch profiles:", e);
    }
    setLoading(false);
  }, [apiBaseUrl, multiAgentEnabled]);

  const fetchSkills = useCallback(async () => {
    try {
      const res = await fetch(`${apiBaseUrl}/api/skills`);
      if (res.ok) {
        const data = await res.json();
        setAvailableSkills(data.skills || []);
      }
    } catch {
      /* skills endpoint may not be available */
    }
  }, [apiBaseUrl]);

  useEffect(() => {
    if (visible && multiAgentEnabled) {
      fetchProfiles();
      fetchSkills();
    }
  }, [visible, multiAgentEnabled, fetchProfiles, fetchSkills]);

  const openCreateEditor = () => {
    setEditingProfile({ ...EMPTY_PROFILE });
    setIsCreating(true);
    setEditorOpen(true);
    setEmojiPickerOpen(false);
  };

  const openEditEditor = (profile: AgentProfile) => {
    setEditingProfile({ ...profile });
    setIsCreating(false);
    setEditorOpen(true);
    setEmojiPickerOpen(false);
  };

  const closeEditor = () => {
    setEditorOpen(false);
    setEmojiPickerOpen(false);
  };

  const generateId = (name: string) =>
    name
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 32) || "custom-agent";

  const handleSave = async () => {
    if (!editingProfile.name.trim()) return;
    setSaving(true);
    try {
      const payload = {
        id: editingProfile.id,
        name: editingProfile.name,
        description: editingProfile.description,
        icon: editingProfile.icon,
        color: editingProfile.color,
        skills: editingProfile.skills,
        skills_mode: editingProfile.skills_mode,
        custom_prompt: editingProfile.custom_prompt,
      };

      const url = isCreating
        ? `${apiBaseUrl}/api/agents/profiles`
        : `${apiBaseUrl}/api/agents/profiles/${editingProfile.id}`;
      const method = isCreating ? "POST" : "PUT";

      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        closeEditor();
        fetchProfiles();
        showToast(t("agentManager.saveSuccess"), "ok");
      } else {
        const data = await res.json().catch(() => ({}));
        showToast(extractErrorMsg(data.detail, res.statusText || t("agentManager.saveFailed")), "err");
      }
    } catch (e) {
      showToast(String(e) || t("agentManager.saveFailed"), "err");
    }
    setSaving(false);
  };

  const handleDelete = async (profileId: string) => {
    try {
      const res = await fetch(`${apiBaseUrl}/api/agents/profiles/${profileId}`, { method: "DELETE" });
      if (res.ok) {
        setConfirmDeleteId(null);
        fetchProfiles();
        showToast(t("agentManager.deleteSuccess"), "ok");
      } else {
        const data = await res.json().catch(() => ({}));
        showToast(extractErrorMsg(data.detail, t("agentManager.deleteFailed")), "err");
      }
    } catch (e) {
      showToast(String(e) || t("agentManager.deleteFailed"), "err");
    }
  };

  const toggleSkill = (skillName: string) => {
    setEditingProfile((prev) => {
      const skills = prev.skills.includes(skillName)
        ? prev.skills.filter((s) => s !== skillName)
        : [...prev.skills, skillName];
      return { ...prev, skills };
    });
  };

  if (!multiAgentEnabled) {
    return (
      <div style={{ padding: 40, textAlign: "center", opacity: 0.5 }}>
        <IconBot size={48} />
        <div style={{ marginTop: 12, fontWeight: 700 }}>{t("agentManager.disabled")}</div>
      </div>
    );
  }

  return (
    <div style={{ padding: 20, position: "relative", overflow: "auto", height: "100%" }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 24 }}>
        <IconBot size={24} />
        <h2 style={{ margin: 0, fontSize: 18 }}>{t("agentManager.title")}</h2>
        <div style={{ flex: 1 }} />
        <button
          onClick={fetchProfiles}
          disabled={loading}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 12px", borderRadius: 8, border: "1px solid var(--line)",
            background: "var(--panel)", cursor: "pointer", fontSize: 13,
          }}
        >
          <IconRefresh size={14} />
          {loading ? t("dashboard.loading") : t("dashboard.refresh")}
        </button>
        <button
          onClick={openCreateEditor}
          style={{
            display: "flex", alignItems: "center", gap: 6,
            padding: "6px 14px", borderRadius: 8, border: "none",
            background: "var(--primary, #3b82f6)", color: "#fff",
            cursor: "pointer", fontSize: 13, fontWeight: 600,
          }}
        >
          <IconPlus size={14} />
          {t("agentManager.create")}
        </button>
      </div>

      {/* Agent Grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 14 }}>
        {profiles.map((agent) => {
          const isSystem = agent.type === "system";
          return (
            <div
              key={agent.id}
              style={{
                padding: 16, borderRadius: 12,
                background: "var(--panel)", border: "1px solid var(--line)",
                position: "relative", overflow: "hidden",
                transition: "box-shadow 0.2s",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.boxShadow = "0 2px 12px rgba(0,0,0,0.08)")}
              onMouseLeave={(e) => (e.currentTarget.style.boxShadow = "none")}
            >
              {/* Color bar */}
              <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: 3, background: agent.color || "var(--brand)" }} />

              {/* Badge */}
              <div style={{ position: "absolute", top: 8, right: 8 }}>
                <span
                  style={{
                    fontSize: 10, fontWeight: 600, padding: "2px 6px", borderRadius: 4,
                    background: isSystem ? "rgba(99,102,241,0.12)" : "rgba(16,185,129,0.12)",
                    color: isSystem ? "#6366f1" : "#10b981",
                  }}
                >
                  {isSystem ? t("agentManager.systemBadge") : t("agentManager.customBadge")}
                </span>
              </div>

              {/* Content */}
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8, marginTop: 4 }}>
                <span style={{ fontSize: 28, lineHeight: 1 }}>{agent.icon}</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 700, fontSize: 14, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{agent.name}</div>
                  <div style={{ fontSize: 11, opacity: 0.45, fontFamily: "monospace" }}>{agent.id}</div>
                </div>
              </div>
              <div style={{ fontSize: 12, opacity: 0.6, marginBottom: 10, minHeight: 18, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {agent.description || "—"}
              </div>

              {/* Actions */}
              {!isSystem && (
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    onClick={() => openEditEditor(agent)}
                    style={{
                      display: "flex", alignItems: "center", gap: 4,
                      padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                      background: "transparent", cursor: "pointer", fontSize: 12,
                    }}
                  >
                    <IconEdit size={12} />
                    {t("agentManager.edit")}
                  </button>
                  <button
                    onClick={() => setConfirmDeleteId(agent.id)}
                    style={{
                      display: "flex", alignItems: "center", gap: 4,
                      padding: "4px 10px", borderRadius: 6, border: "1px solid var(--line)",
                      background: "transparent", cursor: "pointer", fontSize: 12,
                      color: "#ef4444",
                    }}
                  >
                    <IconTrash size={12} />
                    {t("agentManager.delete")}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {profiles.length === 0 && !loading && (
        <div style={{ textAlign: "center", padding: 40, opacity: 0.5 }}>
          <IconBot size={40} />
          <div style={{ marginTop: 8 }}>{t("common.noData")}</div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {confirmDeleteId && (
        <div
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
            backdropFilter: "blur(4px)", WebkitBackdropFilter: "blur(4px)",
            display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
          }}
          onClick={() => setConfirmDeleteId(null)}
        >
          <div
            style={{
              background: "var(--panel)", borderRadius: 12, padding: 24,
              minWidth: 320, maxWidth: 400, boxShadow: "0 8px 32px rgba(0,0,0,0.2)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ fontSize: 15, fontWeight: 600, marginBottom: 12 }}>{t("agentManager.confirmDelete")}</div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button
                onClick={() => setConfirmDeleteId(null)}
                style={{
                  padding: "6px 14px", borderRadius: 8, border: "1px solid var(--line)",
                  background: "var(--panel)", cursor: "pointer", fontSize: 13,
                }}
              >
                {t("agentManager.cancel")}
              </button>
              <button
                onClick={() => handleDelete(confirmDeleteId)}
                style={{
                  padding: "6px 14px", borderRadius: 8, border: "none",
                  background: "#ef4444", color: "#fff", cursor: "pointer", fontSize: 13, fontWeight: 600,
                }}
              >
                {t("agentManager.delete")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Toast notification */}
      {toastMsg && (
        <div style={{
          position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)",
          padding: "10px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600, zIndex: 2000,
          background: toastMsg.type === "ok" ? "#10b981" : "#ef4444", color: "#fff",
          boxShadow: "0 4px 16px rgba(0,0,0,0.18)",
          animation: "fadeIn 0.2s ease-out",
        }}>
          {toastMsg.text}
        </div>
      )}

      {/* Editor Slide-in Panel */}
      {editorOpen && (
        <div
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
            backdropFilter: "blur(4px)", WebkitBackdropFilter: "blur(4px)",
            display: "flex", justifyContent: "flex-end", zIndex: 1000,
          }}
          onClick={closeEditor}
        >
          <div
            style={{
              width: 460, maxWidth: "90vw", height: "100%",
              background: "var(--panel)", boxShadow: "-4px 0 24px rgba(0,0,0,0.15)",
              overflowY: "auto", padding: 24,
              animation: "slideIn 0.2s ease-out",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <style>{`@keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }`}</style>

            <h3 style={{ margin: "0 0 20px 0", fontSize: 16 }}>
              {isCreating ? t("agentManager.create") : t("agentManager.edit")}
            </h3>

            {/* ID */}
            <label style={labelStyle}>{t("agentManager.id")}</label>
            <input
              value={editingProfile.id}
              onChange={(e) => setEditingProfile((p) => ({ ...p, id: e.target.value }))}
              disabled={!isCreating}
              style={{ ...inputStyle, opacity: isCreating ? 1 : 0.5, fontFamily: "monospace", fontSize: 13 }}
              placeholder="my-agent"
            />

            {/* Name */}
            <label style={labelStyle}>{t("agentManager.name")}</label>
            <input
              value={editingProfile.name}
              onChange={(e) => {
                const name = e.target.value;
                setEditingProfile((p) => ({
                  ...p,
                  name,
                  ...(isCreating && !p.id ? { id: generateId(name) } : {}),
                }));
              }}
              style={inputStyle}
              placeholder="My Agent"
            />

            {/* Description */}
            <label style={labelStyle}>{t("agentManager.description")}</label>
            <input
              value={editingProfile.description}
              onChange={(e) => setEditingProfile((p) => ({ ...p, description: e.target.value }))}
              style={inputStyle}
              placeholder="A brief description..."
            />

            {/* Icon + Color row */}
            <div style={{ display: "flex", gap: 12, marginBottom: 4 }}>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>{t("agentManager.icon")}</label>
                <div style={{ position: "relative" }}>
                  <button
                    onClick={() => setEmojiPickerOpen((v) => !v)}
                    style={{
                      ...inputStyle,
                      cursor: "pointer", fontSize: 22, textAlign: "center",
                      padding: "6px", width: "100%", display: "block",
                    }}
                  >
                    {editingProfile.icon}
                  </button>
                  {emojiPickerOpen && (
                    <div style={{
                      position: "absolute", top: "100%", left: 0, zIndex: 10,
                      background: "var(--panel)", border: "1px solid var(--line)",
                      borderRadius: 8, padding: 8, display: "flex", flexWrap: "wrap",
                      gap: 4, width: 200, boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
                    }}>
                      {EMOJI_PRESETS.map((emoji) => (
                        <button
                          key={emoji}
                          onClick={() => {
                            setEditingProfile((p) => ({ ...p, icon: emoji }));
                            setEmojiPickerOpen(false);
                          }}
                          style={{
                            width: 36, height: 36, fontSize: 20, border: "none",
                            borderRadius: 6, cursor: "pointer",
                            background: editingProfile.icon === emoji ? "var(--line)" : "transparent",
                          }}
                        >
                          {emoji}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>{t("agentManager.color")}</label>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input
                    type="color"
                    value={editingProfile.color}
                    onChange={(e) => setEditingProfile((p) => ({ ...p, color: e.target.value }))}
                    style={{ width: 40, height: 36, border: "none", cursor: "pointer", borderRadius: 6, padding: 0, background: "none" }}
                  />
                  <input
                    value={editingProfile.color}
                    onChange={(e) => setEditingProfile((p) => ({ ...p, color: e.target.value }))}
                    style={{ ...inputStyle, flex: 1, fontFamily: "monospace", fontSize: 13 }}
                  />
                </div>
              </div>
            </div>

            {/* Skills Mode */}
            <label style={labelStyle}>{t("agentManager.skills")}</label>
            <select
              value={editingProfile.skills_mode}
              onChange={(e) => setEditingProfile((p) => ({ ...p, skills_mode: e.target.value }))}
              style={{ ...inputStyle, cursor: "pointer" }}
            >
              <option value="all">{t("agentManager.skillsModeAll")}</option>
              <option value="inclusive">{t("agentManager.skillsModeInclusive")}</option>
              <option value="exclusive">{t("agentManager.skillsModeExclusive")}</option>
            </select>

            {/* Skills multi-select */}
            {editingProfile.skills_mode !== "all" && availableSkills.length > 0 && (
              <div style={{
                maxHeight: 220, overflowY: "auto", border: "1px solid var(--line)",
                borderRadius: 8, padding: 4, marginBottom: 12,
              }}>
                {availableSkills.map((skill) => {
                  const checked = editingProfile.skills.includes(skill.name);
                  return (
                    <label
                      key={skill.name}
                      style={{
                        display: "flex", alignItems: "center", gap: 8,
                        padding: "6px 10px", borderRadius: 6, cursor: "pointer",
                        fontSize: 13, lineHeight: 1.4,
                        background: checked ? "var(--primary-bg, rgba(59,130,246,0.08))" : "transparent",
                        transition: "background 0.15s",
                      }}
                      onMouseEnter={(e) => { if (!checked) e.currentTarget.style.background = "var(--hover, rgba(0,0,0,0.04))"; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = checked ? "var(--primary-bg, rgba(59,130,246,0.08))" : "transparent"; }}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSkill(skill.name)}
                        style={{ accentColor: "var(--primary, #3b82f6)", flexShrink: 0, width: 16, height: 16 }}
                      />
                      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {skill.name_i18n?.[i18n.language?.startsWith("zh") ? "zh" : i18n.language || "zh"] || skill.name}
                      </span>
                    </label>
                  );
                })}
              </div>
            )}

            {/* Custom Prompt */}
            <label style={labelStyle}>{t("agentManager.prompt")}</label>
            <textarea
              value={editingProfile.custom_prompt}
              onChange={(e) => setEditingProfile((p) => ({ ...p, custom_prompt: e.target.value }))}
              rows={6}
              style={{
                ...inputStyle, resize: "vertical", fontFamily: "inherit",
                minHeight: 100, lineHeight: 1.5,
              }}
              placeholder="Additional system prompt for this agent..."
            />

            {/* Actions */}
            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button
                onClick={closeEditor}
                style={{
                  flex: 1, padding: "8px 0", borderRadius: 8, border: "1px solid var(--line)",
                  background: "var(--panel)", cursor: "pointer", fontSize: 13,
                }}
              >
                {t("agentManager.cancel")}
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !editingProfile.name.trim()}
                style={{
                  flex: 1, padding: "8px 0", borderRadius: 8, border: "none",
                  background: "var(--primary, #3b82f6)", color: "#fff",
                  cursor: saving ? "wait" : "pointer", fontSize: 13, fontWeight: 600,
                  opacity: !editingProfile.name.trim() ? 0.5 : 1,
                }}
              >
                {saving ? t("common.loading") : t("agentManager.save")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block", fontSize: 12, fontWeight: 600, marginBottom: 4, marginTop: 12, opacity: 0.7,
};

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "8px 10px", borderRadius: 8,
  border: "1px solid var(--line)", background: "var(--bg, #fff)",
  fontSize: 13, outline: "none", boxSizing: "border-box",
};
