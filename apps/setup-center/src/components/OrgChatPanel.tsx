/**
 * Reusable chat panel — organization or node level.
 * Renders a scrollable message list, input box, and real-time WS progress.
 * Messages are persisted to localStorage per orgId (+ optional nodeId).
 */
import { useState, useRef, useEffect, useCallback } from "react";
import { safeFetch } from "../providers";
import { onWsEvent } from "../platform";

interface ChatMsg {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  streaming?: boolean;
}

export interface OrgChatPanelProps {
  orgId: string;
  nodeId?: string | null;
  apiBaseUrl: string;
  compact?: boolean;
  showHeader?: boolean;
  title?: string;
  onClose?: () => void;
}

const MAX_PERSISTED = 50;
const SAVE_DEBOUNCE_MS = 800;

function storageKey(orgId: string, nodeId?: string | null): string {
  return nodeId ? `orgchat_${orgId}_${nodeId}` : `orgchat_${orgId}`;
}

function loadMessages(orgId: string, nodeId?: string | null): ChatMsg[] {
  try {
    const raw = localStorage.getItem(storageKey(orgId, nodeId));
    if (!raw) return [];
    const msgs: ChatMsg[] = JSON.parse(raw);
    return msgs.filter(m => !m.streaming);
  } catch {
    return [];
  }
}

function saveMessages(orgId: string, nodeId: string | null | undefined, msgs: ChatMsg[]): void {
  try {
    const toSave = msgs
      .filter(m => !m.streaming)
      .slice(-MAX_PERSISTED)
      .map(({ streaming, ...rest }) => rest);
    localStorage.setItem(storageKey(orgId, nodeId), JSON.stringify(toSave));
  } catch { /* quota exceeded */ }
}

let _seq = 0;
function genId() { return `orgchat-${Date.now()}-${++_seq}`; }

export function OrgChatPanel({ orgId, nodeId, apiBaseUrl, compact, showHeader, title, onClose }: OrgChatPanelProps) {
  const [messages, setMessages] = useState<ChatMsg[]>(() => loadMessages(orgId, nodeId));
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout>>();

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    });
  }, []);

  useEffect(scrollToBottom, [messages, scrollToBottom]);

  useEffect(() => {
    setMessages(loadMessages(orgId, nodeId));
  }, [orgId, nodeId]);

  useEffect(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      saveMessages(orgId, nodeId, messages);
    }, SAVE_DEBOUNCE_MS);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [messages, orgId, nodeId]);

  useEffect(() => {
    const flush = () => saveMessages(orgId, nodeId, messages);
    window.addEventListener("beforeunload", flush);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") flush();
    });
    return () => window.removeEventListener("beforeunload", flush);
  }, [orgId, nodeId, messages]);

  const handleClear = useCallback(() => {
    setMessages([]);
    try { localStorage.removeItem(storageKey(orgId, nodeId)); } catch {}
  }, [orgId, nodeId]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;

    const userMsg: ChatMsg = { id: genId(), role: "user", content: text, timestamp: Date.now() };
    const placeholderId = genId();
    const placeholder: ChatMsg = {
      id: placeholderId, role: "assistant", content: "思考中...", timestamp: Date.now(), streaming: true,
    };
    setMessages(prev => [...prev, userMsg, placeholder]);
    setInput("");
    setSending(true);

    const progressLines: string[] = [];
    const pushProgress = (line: string) => {
      progressLines.push(line);
      const preview = progressLines.slice(-8).map(l => `> ${l}`).join("\n");
      setMessages(prev => prev.map(m => m.id === placeholderId ? { ...m, content: preview } : m));
    };

    const unsubProgress = onWsEvent((event, raw) => {
      const d = raw as Record<string, unknown> | null;
      if (!d || d.org_id !== orgId) return;
      const nid = (d.node_id || d.from_node || "") as string;
      const toN = (d.to_node || "") as string;
      if (event === "org:node_status") {
        const st = d.status as string;
        if (st === "busy") {
          const task = (d.current_task || "") as string;
          pushProgress(`[START] **${nid}** 开始处理${task ? `：${task.slice(0, 60)}` : ""}`);
        } else if (st === "idle") pushProgress(`[DONE] **${nid}** 完成`);
        else if (st === "error") pushProgress(`[ERR] **${nid}** 出错`);
      } else if (event === "org:task_delegated") {
        pushProgress(`[TASK] **${nid}** → **${toN}** 分配任务：${((d.task || "") as string).slice(0, 50)}`);
      } else if (event === "org:task_complete") {
        pushProgress(`[OK] **${nid}** 任务完成`);
      } else if (event === "org:blackboard_update") {
        pushProgress(`[NOTE] **${nid}** 更新黑板`);
      }
    });

    try {
      const res = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/command`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text, target_node_id: nodeId || undefined }),
      });
      const data = await res.json();
      const commandId = data.command_id as string | undefined;

      if (!commandId) {
        const resultText = data.result || data.error || JSON.stringify(data);
        setMessages(prev => prev.map(m =>
          m.id === placeholderId ? { ...m, content: resultText, streaming: false } : m
        ));
      } else {
        let resolved = false;
        const unsubDone = onWsEvent((evt, raw) => {
          const d = raw as Record<string, unknown> | null;
          if (evt !== "org:command_done" || !d || d.command_id !== commandId) return;
          resolved = true;
          const result = d.result as Record<string, unknown> | null;
          const error = d.error as string | undefined;
          const resultText = String((result && (result.result || result.error)) || error || JSON.stringify(d));
          const progressSummary = progressLines.length > 0
            ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
            : "";
          setMessages(prev => prev.map(m =>
            m.id === placeholderId ? { ...m, content: progressSummary + resultText, streaming: false } : m
          ));
        });

        let lastActivity = Date.now();
        while (!resolved) {
          await new Promise(r => setTimeout(r, 5000));
          if (resolved) break;
          try {
            const poll = await safeFetch(`${apiBaseUrl}/api/orgs/${orgId}/commands/${commandId}`);
            const pd = await poll.json();
            if (pd.status === "done" || pd.status === "error") {
              if (!resolved) {
                resolved = true;
                const resultText = pd.result?.result || pd.result?.error || pd.error || JSON.stringify(pd);
                const progressSummary = progressLines.length > 0
                  ? progressLines.map(l => `> ${l}`).join("\n") + "\n\n---\n\n"
                  : "";
                setMessages(prev => prev.map(m =>
                  m.id === placeholderId ? { ...m, content: progressSummary + resultText, streaming: false } : m
                ));
              }
            }
          } catch { /* retry */ }
          if (!resolved && Date.now() - lastActivity > 60000) {
            pushProgress("... 执行时间较长，组织仍在处理中...");
            lastActivity = Date.now();
          }
        }
        unsubDone();
      }
    } catch (e: any) {
      setMessages(prev => prev.map(m =>
        m.id === placeholderId ? { ...m, content: `发送失败: ${e.message || e}`, streaming: false, role: "system" } : m
      ));
    } finally {
      unsubProgress();
      setSending(false);
    }
  }, [input, sending, orgId, nodeId, apiBaseUrl]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="ocp-root">
      {showHeader && (
        <div className="ocp-header">
          <div className="ocp-header-info">
            <div className="ocp-header-dot" />
            <span className="ocp-header-title">{title || (nodeId ? `对话 · ${nodeId}` : "组织指挥台")}</span>
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            {messages.length > 0 && (
              <button className="ocp-close" onClick={handleClear} title="清空历史">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/>
                </svg>
              </button>
            )}
            {onClose && (
              <button className="ocp-close" onClick={onClose}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            )}
          </div>
        </div>
      )}

      <div ref={listRef} className="ocp-messages">
        {messages.length === 0 && (
          <div className="ocp-empty">
            <div className="ocp-empty-icon">
              {nodeId ? (
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
              ) : (
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.6 }}>
                  <path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/><path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/>
                </svg>
              )}
            </div>
            <div className="ocp-empty-text">
              {nodeId ? "向该节点发送指令开始对话" : "向组织发送指令，AI 团队将协作执行"}
            </div>
            <div className="ocp-empty-hint">Shift+Enter 换行，Enter 发送</div>
          </div>
        )}
        {messages.map(m => (
          <div key={m.id} className={`ocp-msg ocp-msg-${m.role} ${m.streaming ? "ocp-msg-streaming" : ""}`}>
            <div className="ocp-msg-bubble">
              {m.content}
              {m.streaming && <span className="ocp-typing">●</span>}
            </div>
          </div>
        ))}
      </div>

      {/* Non-header mode: show clear button inline */}
      {!showHeader && messages.length > 0 && (
        <div style={{ display: "flex", justifyContent: "center", padding: "2px 0", flexShrink: 0 }}>
          <button
            onClick={handleClear}
            style={{
              fontSize: 10, color: "var(--muted, #64748b)", background: "none",
              border: "none", cursor: "pointer", padding: "2px 8px", opacity: 0.6,
            }}
          >
            清空对话记录
          </button>
        </div>
      )}

      <div className={`ocp-input-area ${compact ? "ocp-compact" : ""}`}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={nodeId ? "输入指令..." : "输入组织命令..."}
          rows={1}
          className="ocp-textarea"
        />
        <button
          onClick={handleSend}
          disabled={sending || !input.trim()}
          className={`ocp-send ${sending ? "ocp-send-busy" : ""}`}
        >
          {sending ? (
            <span className="ocp-send-spinner" />
          ) : (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
            </svg>
          )}
        </button>
      </div>

      <style>{CHAT_CSS}</style>
    </div>
  );
}

const CHAT_CSS = `
.ocp-root {
  display: flex; flex-direction: column; height: 100%; overflow: hidden;
  background: var(--bg-app, #0f172a); color: var(--text, #e2e8f0);
}

/* ─── Header ─── */
.ocp-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line, rgba(51,65,85,0.5));
  background: var(--bg-subtle, rgba(15,23,42,0.6));
  backdrop-filter: blur(8px);
  flex-shrink: 0;
}
.ocp-header-info { display: flex; align-items: center; gap: 8px; }
.ocp-header-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #22c55e;
  box-shadow: 0 0 8px #22c55e80;
  animation: ocp-pulse 2s ease-in-out infinite;
}
@keyframes ocp-pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
.ocp-header-title { font-size: 13px; font-weight: 600; }
.ocp-close {
  width: 28px; height: 28px; border: none; border-radius: 6px;
  background: transparent; color: var(--muted, #64748b);
  cursor: pointer; font-size: 14px; display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.ocp-close:hover { background: rgba(239,68,68,0.1); color: #ef4444; }

/* ─── Messages ─── */
.ocp-messages {
  flex: 1; overflow-y: auto; padding: 12px;
  display: flex; flex-direction: column; gap: 8px;
}
.ocp-messages::-webkit-scrollbar { width: 4px; }
.ocp-messages::-webkit-scrollbar-thumb { background: rgba(51,65,85,0.5); border-radius: 2px; }

.ocp-empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  flex: 1; gap: 8px; text-align: center; padding: 32px 16px;
}
.ocp-empty-icon { display: flex; align-items: center; justify-content: center; color: var(--muted, #64748b); }
.ocp-empty-text { font-size: 13px; color: var(--muted, #64748b); max-width: 220px; line-height: 1.5; }
.ocp-empty-hint { font-size: 11px; color: var(--muted, #475569); opacity: 0.5; }

.ocp-msg { display: flex; }
.ocp-msg-user { justify-content: flex-end; }
.ocp-msg-assistant, .ocp-msg-system { justify-content: flex-start; }

.ocp-msg-bubble {
  max-width: 85%; padding: 10px 14px; border-radius: 12px;
  font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
}
.ocp-msg-user .ocp-msg-bubble {
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  color: #fff; border-bottom-right-radius: 4px;
}
.ocp-msg-assistant .ocp-msg-bubble {
  background: var(--bg-subtle, rgba(30,41,59,0.8));
  border: 1px solid var(--line, rgba(51,65,85,0.4));
  color: var(--text, #e2e8f0);
  border-bottom-left-radius: 4px;
}
.ocp-msg-system .ocp-msg-bubble {
  background: rgba(239,68,68,0.08);
  border: 1px solid rgba(239,68,68,0.2);
  color: #fca5a5;
  border-bottom-left-radius: 4px;
}
.ocp-msg-streaming .ocp-msg-bubble {
  border-color: rgba(99,102,241,0.3);
}
.ocp-typing {
  display: inline-block; margin-left: 4px; color: #818cf8;
  animation: ocp-typing-blink 1.2s ease-in-out infinite;
}
@keyframes ocp-typing-blink { 0%,100% { opacity: 1; } 50% { opacity: 0.2; } }

/* ─── Input ─── */
.ocp-input-area {
  padding: 10px 12px;
  border-top: 1px solid var(--line, rgba(51,65,85,0.5));
  display: flex; gap: 8px; align-items: flex-end;
  background: var(--bg-app, #0f172a);
  flex-shrink: 0;
}
.ocp-compact { padding: 8px 10px; }
.ocp-textarea {
  flex: 1; resize: none; border: 1px solid var(--line, rgba(51,65,85,0.5));
  border-radius: 10px; padding: 10px 14px;
  font-size: 13px; font-family: inherit; line-height: 1.5;
  background: var(--bg-app, #0f172a);
  color: var(--text, #e2e8f0);
  outline: none; max-height: 100px; overflow-y: auto;
  transition: border-color 0.2s;
}
.ocp-textarea:focus { border-color: #6366f1; box-shadow: 0 0 0 2px rgba(99,102,241,0.15); }
.ocp-textarea::placeholder { color: var(--muted, #64748b); }

.ocp-send {
  width: 40px; height: 40px; border: none; border-radius: 10px;
  background: linear-gradient(135deg, #3b82f6, #6366f1);
  color: #fff; cursor: pointer; flex-shrink: 0;
  display: flex; align-items: center; justify-content: center;
  transition: all 0.2s; box-shadow: 0 2px 8px rgba(99,102,241,0.3);
}
.ocp-send:hover:not(:disabled) { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(99,102,241,0.4); }
.ocp-send:disabled { opacity: 0.4; cursor: not-allowed; box-shadow: none; }
.ocp-send-busy { background: linear-gradient(135deg, #f59e0b, #f97316); }

.ocp-send-spinner {
  width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff; border-radius: 50%;
  animation: ocp-spin 0.6s linear infinite;
}
@keyframes ocp-spin { to { transform: rotate(360deg); } }
`;
