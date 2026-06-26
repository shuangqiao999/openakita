import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import ForceGraph3D from "react-force-graph-3d";
import { toast } from "sonner";
import { Upload, Loader2 } from "lucide-react";

interface Props {
  serviceRunning: boolean;
  apiBaseUrl: string;
}

interface SessionItem {
  id: string;
  title: string;
  status: string;
  phase: string;
  entity_count: number;
  relation_count: number;
  agent_count: number;
  current_round: number;
  total_rounds: number;
  created_at: string;
  error?: string;
}

interface GraphData {
  nodes: Array<{ id: string; name: string; type: string; description: string }>;
  links: Array<{ source: string; target: string; relation: string; weight: number }>;
}

export function DeductionView({ serviceRunning, apiBaseUrl }: Props) {
  const { t } = useTranslation();
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [sourceMaterial, setSourceMaterial] = useState("");
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [logs, setLogs] = useState<Array<{ phase: string; message: string; timestamp: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preGoal, setPreGoal] = useState("");
  const [interventionText, setInterventionText] = useState("");
  const [sending, setSending] = useState(false);
  const logsRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchSessions = useCallback(async () => {
    try {
      const r = await fetch(`${apiBaseUrl}/api/deduction/sessions`);
      if (r.ok) setSessions(await r.json());
    } catch { /* ignore */ }
  }, [apiBaseUrl]);

  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  const fetchGraph = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${apiBaseUrl}/api/deduction/session/${sessionId}/graph`);
      if (r.ok) setGraphData(await r.json());
    } catch { /* ignore */ }
  }, [apiBaseUrl]);

  const fetchLogs = useCallback(async (sessionId: string) => {
    try {
      const r = await fetch(`${apiBaseUrl}/api/deduction/session/${sessionId}/logs`);
      if (r.ok) setLogs(await r.json());
    } catch { /* ignore */ }
  }, [apiBaseUrl]);

  const selectSession = useCallback((id: string) => {
    setSelectedId(id);
    fetchGraph(id);
    fetchLogs(id);
  }, [fetchGraph, fetchLogs]);

  const handleCreate = useCallback(async () => {
    if (!title.trim() || !sourceMaterial.trim()) return;
    setCreating(true);
    try {
      const r = await fetch(`${apiBaseUrl}/api/deduction/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title, source_material: sourceMaterial }),
      });
      if (r.ok) {
        const data = await r.json();
        setSelectedId(data.id);
        setSessions(prev => [{ id: data.id, title: data.title, status: data.status, phase: "", entity_count: 0, relation_count: 0, agent_count: 0, current_round: 0, total_rounds: 10, created_at: data.created_at }, ...prev]);
        toast.success(t("deductionEngine.sessionCreated"));
      }
    } catch { /* ignore */ }
    setCreating(false);
  }, [title, sourceMaterial, apiBaseUrl, t]);

  const handleFileUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const ext = file.name.split(".").pop()?.toLowerCase();
    const allowed = ["txt","md","json","pdf","docx","py","js","ts","rs","go","java","c","cpp","h","csv","log","yaml","yml"];
    if (!ext || !allowed.includes(ext)) {
      toast.error(t("deductionEngine.fileTypeUnsupported", { type: `.${ext}` }));
      e.target.value = "";
      return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await fetch(`${apiBaseUrl}/api/deduction/upload`, { method: "POST", body: fd });
      if (!r.ok) { const err = await r.text(); throw new Error(err); }
      const data = await r.json();
      setSourceMaterial(data.text_content);
      const titleHint = file.name.replace(/\.[^.]+$/, "").slice(0, 40);
      if (!title.trim()) setTitle(titleHint);
      toast.success(t("deductionEngine.fileParsed", { name: file.name, count: data.text_content.length }));
    } catch (err: any) {
      toast.error(t("deductionEngine.fileUploadFailed", { message: err.message }));
    }
    setUploading(false);
    e.target.value = "";
  }, [apiBaseUrl, title, t]);

  const handleStart = useCallback(async () => {
    if (!selectedId) return;
    setLoading(true);
    setLogs([]);
    try {
      const r = await fetch(`${apiBaseUrl}/api/deduction/session/${selectedId}/start`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      toast.success(t("deductionEngine.deductionStarted"));
      await fetchSessions();
      await fetchGraph(selectedId);
      await fetchLogs(selectedId);
    } catch (e: any) {
      toast.error(t("deductionEngine.deductionFailed", { message: e.message }));
    }
    setLoading(false);
  }, [selectedId, apiBaseUrl, fetchSessions, fetchGraph, fetchLogs, t]);

  const handleDelete = useCallback(async (id: string) => {
    await fetch(`${apiBaseUrl}/api/deduction/session/${id}`, { method: "DELETE" });
    if (selectedId === id) setSelectedId(null);
    fetchSessions();
  }, [apiBaseUrl, selectedId, fetchSessions]);

  const sendPreGoal = useCallback(async () => {
    if (!selectedId || !preGoal.trim()) return;
    try {
      await fetch(`${apiBaseUrl}/api/deduction/session/${selectedId}/pre-goal`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: preGoal }),
      });
      toast.success(t("deductionEngine.preGoalSet"));
      setPreGoal("");
    } catch { /* ignore */ }
  }, [apiBaseUrl, selectedId, preGoal]);

  const sendIntervention = useCallback(async () => {
    if (!selectedId || !interventionText.trim()) return;
    setSending(true);
    try {
      await fetch(`${apiBaseUrl}/api/deduction/session/${selectedId}/intervene`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: interventionText, scope: "during" }),
      });
      toast.success(t("deductionEngine.interveneInjected"));
      setInterventionText("");
      await fetchLogs(selectedId);
    } catch (err: any) {
      toast.error(t("deductionEngine.interveneFailed", { message: err.message }));
    }
    setSending(false);
  }, [apiBaseUrl, selectedId, interventionText, fetchLogs]);

  // SSE auto-refresh logs during simulation
  useEffect(() => {
    if (!selectedId || !serviceRunning) return;
    const selected = sessions.find(s => s.id === selectedId);
    if (!selected || selected.status !== "simulating") return;
    const es = new EventSource(`${apiBaseUrl}/api/deduction/session/${selectedId}/stream`);
    es.onmessage = (ev) => {
      if (ev.data === "[DONE]") { es.close(); fetchSessions(); fetchGraph(selectedId); return; }
      try {
        const d = JSON.parse(ev.data);
        setLogs(prev => [...prev.slice(-200), { phase: d.phase || d.type || "", message: d.message || "", timestamp: d.timestamp || "" }]);
        if (logsRef.current) logsRef.current.scrollTop = logsRef.current.scrollHeight;
      } catch { /* ignore */ }
    };
    es.onerror = () => { es.close(); };
    return () => es.close();
  }, [selectedId, serviceRunning, sessions, apiBaseUrl, fetchSessions, fetchGraph]);

  const selected = sessions.find(s => s.id === selectedId);

  const phaseLabel = (s: SessionItem) => {
    const map: Record<string, string> = {
      created: t("deductionEngine.statusCreated"),
      ontology_running: t("deductionEngine.statusOntologyRunning"),
      graph_running: t("deductionEngine.statusGraphRunning"),
      agents_running: t("deductionEngine.statusAgentsRunning"),
      simulating: t("deductionEngine.statusSimulating"),
      reporting: t("deductionEngine.statusReporting"),
      complete: t("deductionEngine.statusComplete"),
      failed: t("deductionEngine.statusFailed"),
      paused: t("deductionEngine.statusPaused"),
    };
    return map[s.status] || s.status;
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100%", overflow: "hidden" }}>
      {/* ── Left Panel: Sessions ── */}
      <div style={{ borderRight: "1px solid var(--line)", overflow: "auto", padding: 12 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>{t("deductionEngine.title")}</h3>

        <div className="card" style={{ marginBottom: 10 }}>
          <input
            style={{ height: 32, marginBottom: 6 }}
            placeholder={t("deductionEngine.sessionTitle")}
            value={title}
            onChange={e => setTitle(e.target.value)}
          />
          <textarea
            style={{ height: 100, fontSize: 12, marginBottom: 6 }}
            placeholder={t("deductionEngine.pasteSourceMaterial")}
            value={sourceMaterial}
            onChange={e => setSourceMaterial(e.target.value)}
          />
          <textarea
            style={{ height: 48, fontSize: 12, marginBottom: 6 }}
            placeholder={t("deductionEngine.preGoalPlaceholder")}
            value={preGoal}
            onChange={e => setPreGoal(e.target.value)}
          />
          <input
            ref={fileInputRef}
            type="file"
            accept=".txt,.md,.json,.pdf,.docx,.py,.js,.ts,.rs,.go,.java,.c,.cpp,.csv,.log,.yaml,.yml"
            onChange={handleFileUpload}
            style={{ display: "none" }}
          />
          <button
            style={{ width: "100%", height: 28, fontSize: 12, marginBottom: 6,
              background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 6, cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "center", gap: 6, color: "var(--muted)" }}
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading || !serviceRunning}
          >
            {uploading ? <Loader2 size={14} className="spinIcon" /> : <Upload size={14} />}
            {uploading ? t("deductionEngine.parsing") : t("deductionEngine.uploadDocument")}
          </button>
          <button
            className="btnPrimary"
            style={{ width: "100%", height: 32, fontSize: 13 }}
            onClick={handleCreate}
            disabled={creating || !serviceRunning}
          >
            {creating ? t("deductionEngine.creating") : t("deductionEngine.createSession")}
          </button>
        </div>

        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{t("deductionEngine.sessionList")}</div>
        {sessions.map(s => (
          <div
            key={s.id}
            className={`card ${selectedId === s.id ? "stepItemActive" : ""}`}
            style={{ padding: "8px 10px", marginBottom: 4, cursor: "pointer", fontSize: 12 }}
            onClick={() => selectSession(s.id)}
          >
            <div style={{ fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {s.title || s.id.slice(0, 8)}
            </div>
            <div style={{ color: "var(--muted)", fontSize: 11 }}>
              {phaseLabel(s)}
              {s.agent_count > 0 && ` \u00b7 ${s.agent_count} ${t("deductionEngine.agents")}`}
            </div>
            <div style={{ marginTop: 2, display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--muted2)", fontSize: 10 }}>
                {s.current_round}/{s.total_rounds} {t("deductionEngine.round")}
              </span>
              <button
                className="btnSmall btnSmallDanger"
                style={{ padding: "1px 6px", fontSize: 10 }}
                onClick={e => { e.stopPropagation(); handleDelete(s.id); }}
              >
                {t("deductionEngine.delete")}
              </button>
            </div>
          </div>
        ))}
        {sessions.length === 0 && (
          <div style={{ color: "var(--muted)", fontSize: 12, textAlign: "center", padding: 20 }}>
            {t("deductionEngine.noSessions")}
          </div>
        )}
      </div>

      {/* ── Right Panel: Graph + Controls + Logs ── */}
      <div style={{ display: "grid", gridTemplateRows: "auto 1fr auto", overflow: "hidden" }}>
        {selected ? (
          <>
            <div className="topbar" style={{ minHeight: 36, padding: "4px 12px" }}>
              <div className="topbarStatusRow">
                <span className="topbarWs">{selected.title || selected.id.slice(0, 8)}</span>
                <span className="pill">{phaseLabel(selected)}</span>
                {selected.agent_count > 0 && <span className="pill">{selected.agent_count} {t("deductionEngine.agents")}</span>}
                {selected.current_round > 0 && <span className="pill">{selected.current_round}/{selected.total_rounds} {t("deductionEngine.round")}</span>}
              </div>
              <div>
                <button
                  className="btnSmall btnSmallPrimary"
                  style={{ marginRight: 6 }}
                  onClick={handleStart}
                  disabled={loading || selected.status === "simulating"}
                >
                  {selected.status === "complete"
                    ? t("deductionEngine.restartDeduction")
                    : loading ? t("deductionEngine.running") : t("deductionEngine.startDeduction")}
                </button>
              </div>
            </div>

            {/* Graph Visualization */}
            <div style={{ background: "#0d1117", position: "relative" }}>
              {graphData && graphData.nodes.length > 0 ? (
                <ForceGraph3D
                  graphData={{
                    nodes: graphData.nodes.map(n => ({ id: n.id, name: n.name, group: n.type, desc: n.description })),
                    links: graphData.links.map(l => ({ source: l.source, target: l.target, value: l.relation })),
                  }}
                  nodeLabel={(n: any) => `${n.name}\n${n.group}`}
                  nodeColor={(n: any) => {
                    const colors: Record<string, string> = { Person: "#60a5fa", Organization: "#f59e0b", Event: "#ef4444", Concept: "#34d399", Location: "#a78bfa" };
                    return colors[n.group] || "#94a3b8";
                  }}
                  nodeVal={(n: any) => (graphData.links.filter(l => l.source === n.id || l.target === n.id).length || 1) * 2}
                  linkLabel={(l: any) => String(l.value)}
                  linkWidth={0.5}
                  backgroundColor="#0d1117"
                  width={window.innerWidth - 340}
                  height={500}
                />
              ) : (
                <div style={{ color: "#64748b", textAlign: "center", paddingTop: 200, fontSize: 14 }}>
                  {selected.status === "created"
                    ? t("deductionEngine.graphEmpty")
                    : t("deductionEngine.graphGenerating")}
                </div>
              )}
            </div>

            {/* Intervention input (only during simulation) */}
            {selected.status === "simulating" && (
              <div style={{ display: "flex", gap: 6, padding: "6px 12px", borderTop: "1px solid var(--line)", background: "var(--bg-subtle)" }}>
                <input
                  style={{ flex: 1, height: 28, fontSize: 12 }}
                  placeholder={t("deductionEngine.intervenePlaceholder")}
                  value={interventionText}
                  onChange={e => setInterventionText(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") sendIntervention(); }}
                />
                <button
                  className="btnSmall btnSmallPrimary"
                  style={{ height: 28, fontSize: 11 }}
                  onClick={sendIntervention}
                  disabled={sending || !interventionText.trim()}
                >
                  {sending ? t("deductionEngine.sendingIntervene") : t("deductionEngine.sendIntervene")}
                </button>
              </div>
            )}

            {/* Logs */}
            <div ref={logsRef} style={{ maxHeight: 160, overflow: "auto", borderTop: "1px solid var(--line)", padding: 8, fontSize: 11 }}>
              {logs.length === 0 && (
                <div style={{ color: "var(--muted)", textAlign: "center", padding: 10 }}>{t("deductionEngine.noLogs")}</div>
              )}
              {logs.map((l, i) => (
                <div key={i} style={{ padding: "1px 0", color: "var(--muted)", fontFamily: "monospace" }}>
                  <span style={{ color: "var(--brand)", marginRight: 8 }}>[{l.phase}]</span>
                  {l.message}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--muted)", fontSize: 14 }}>
            {t("deductionEngine.selectHint")}
          </div>
        )}
      </div>
    </div>
  );
}
