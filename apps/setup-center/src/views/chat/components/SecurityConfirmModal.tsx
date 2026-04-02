import { useRef, useEffect, useCallback } from "react";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../../../providers";
import { getAccessToken } from "../../../platform/auth";
import { IS_TAURI, logger } from "../../../platform";
import { IconShield, IconAlertCircle } from "../../../icons";

export function SecurityConfirmModal({
  data, apiBase, onClose, timerRef, setData,
  onAllow, onDeny, sessionTrustInfo,
}: {
  data: { tool: string; args: Record<string, unknown>; reason: string; riskLevel: string; needsSandbox: boolean; toolId?: string; countdown: number };
  apiBase: string;
  onClose: () => void;
  timerRef: React.MutableRefObject<ReturnType<typeof setInterval> | null>;
  setData: React.Dispatch<React.SetStateAction<typeof data | null>>;
  onAllow?: (toolName: string) => void;
  onDeny?: (toolName: string) => void;
  sessionTrustInfo?: { toolAllows: number; globalAllows: number; isEscalated: boolean };
}) {
  const { t } = useTranslation();
  const pausedRef = useRef(false);

  useEffect(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      if (pausedRef.current) return;
      setData((prev) => {
        if (!prev) return prev;
        if (prev.countdown <= 1) {
          clearInterval(timerRef.current!);
          timerRef.current = null;
          logger.info("Chat.Security", "confirm.timeout", { confirmId: prev.toolId });
          handleDecision("allow");
          return null;
        }
        return { ...prev, countdown: prev.countdown - 1 };
      });
    }, 1000);
    return () => {
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDecision = useCallback(async (decision: "allow" | "deny" | "sandbox") => {
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    logger.info("Chat.Security", "confirm.decision", { confirmId: data.toolId, decision });
    try {
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (!IS_TAURI) {
        const token = getAccessToken();
        if (token) headers["Authorization"] = `Bearer ${token}`;
      }
      await safeFetch(`${apiBase}/api/chat/security-confirm`, {
        method: "POST",
        headers,
        body: JSON.stringify({ confirm_id: data.toolId || "", decision }),
      });
    } catch (err) {
      console.error("[SecurityConfirm] decision failed:", err);
    }
    if (decision === "allow" || decision === "sandbox") {
      onAllow?.(data.tool);
    } else {
      onDeny?.(data.tool);
    }
    onClose();
  }, [apiBase, data.toolId, data.tool, onClose, onAllow, onDeny, timerRef]);

  const riskColors: Record<string, string> = {
    critical: "#ef4444",
    high: "#f59e0b",
    medium: "#3b82f6",
    low: "#10b981",
  };
  const riskColor = riskColors[data.riskLevel] || riskColors.medium;

  const trustHint = sessionTrustInfo && sessionTrustInfo.toolAllows > 0
    ? t("chat.securityTrustHint", {
        count: sessionTrustInfo.toolAllows,
        defaultValue: `本次会话中此工具已被允许 ${sessionTrustInfo.toolAllows} 次`,
      })
    : null;

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 99999,
        background: "rgba(0,0,0,0.55)", backdropFilter: "blur(8px)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={(e) => { if (e.target === e.currentTarget) { pausedRef.current = !pausedRef.current; } }}
    >
      <div style={{
        background: "var(--panel)", borderRadius: 16, padding: "24px 28px",
        maxWidth: 480, width: "90%",
        border: `2px solid ${riskColor}`,
        boxShadow: `0 8px 32px rgba(0,0,0,0.25), 0 0 0 1px ${riskColor}33`,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
          <IconShield size={24} style={{ color: riskColor }} />
          <div>
            <div style={{ fontWeight: 700, fontSize: 16 }}>{t("chat.securityConfirmTitle", "安全确认")}</div>
            <div style={{ fontSize: 12, opacity: 0.6 }}>
              {t("chat.securityRiskLevel", "风险等级")}: <span style={{ color: riskColor, fontWeight: 700, textTransform: "uppercase" }}>{data.riskLevel}</span>
            </div>
          </div>
        </div>

        <div style={{ padding: "12px 14px", background: `${riskColor}08`, border: `1px solid ${riskColor}22`, borderRadius: 10, marginBottom: 12 }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
            <IconAlertCircle size={16} style={{ color: riskColor, marginTop: 2, flexShrink: 0 }} />
            <div style={{ fontSize: 13, lineHeight: 1.5 }}>{data.reason}</div>
          </div>
        </div>

        <div style={{ fontSize: 13, marginBottom: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{t("chat.securityTool", "工具")}: <code>{data.tool}</code></div>
          <pre style={{ margin: 0, fontSize: 11, maxHeight: 120, overflow: "auto", padding: "8px 10px", borderRadius: 8, background: "var(--panel2)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {JSON.stringify(data.args, null, 2)}
          </pre>
        </div>

        {trustHint && (
          <div style={{ fontSize: 12, color: "#10b981", marginBottom: 10, padding: "4px 8px", background: "#10b98110", borderRadius: 6 }}>
            {trustHint}
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button
            onClick={() => handleDecision("deny")}
            style={{
              padding: "8px 20px", borderRadius: 8, cursor: "pointer",
              background: "transparent", border: "1px solid var(--line)", color: "var(--text)",
              fontSize: 13, fontWeight: 600,
            }}
          >
            {t("chat.securityDeny", "拒绝")}
          </button>
          {data.needsSandbox && (
            <button
              onClick={() => handleDecision("sandbox")}
              style={{
                padding: "8px 20px", borderRadius: 8, cursor: "pointer",
                background: "#3b82f622", border: "1px solid #3b82f644", color: "#3b82f6",
                fontSize: 13, fontWeight: 600,
              }}
            >
              {t("chat.securitySandbox", "沙箱运行")}
            </button>
          )}
          <button
            onClick={() => handleDecision("allow")}
            style={{
              padding: "8px 20px", borderRadius: 8, cursor: "pointer",
              background: riskColor, border: "none", color: "#fff",
              fontSize: 13, fontWeight: 700,
            }}
          >
            {t("chat.securityAllow", "允许")} ({data.countdown}s)
          </button>
        </div>
      </div>
    </div>
  );
}
