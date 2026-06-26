import { useState, useEffect, useCallback, useRef } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { toast } from "sonner";

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
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [sourceMaterial, setSourceMaterial] = useState("");
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [logs, setLogs] = useState<Array<{ phase: string; message: string; timestamp: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const logsRef = useRef<HTMLDivElement>(null);

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
        toast.success("推演会话已创建");
      }
    } catch { /* ignore */ }
    setCreating(false);
  }, [title, sourceMaterial, apiBaseUrl]);

  const handleStart = useCallback(async () => {
    if (!selectedId) return;
    setLoading(true);
    setLogs([]);
    try {
      const r = await fetch(`${apiBaseUrl}/api/deduction/session/${selectedId}/start`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      toast.success("推演启动成功");
      await fetchSessions();
      await fetchGraph(selectedId);
      await fetchLogs(selectedId);
    } catch (e: any) {
      toast.error(`推演失败: ${e.message}`);
    }
    setLoading(false);
  }, [selectedId, apiBaseUrl, fetchSessions, fetchGraph, fetchLogs]);

  const handleDelete = useCallback(async (id: string) => {
    await fetch(`${apiBaseUrl}/api/deduction/session/${id}`, { method: "DELETE" });
    if (selectedId === id) setSelectedId(null);
    fetchSessions();
  }, [apiBaseUrl, selectedId, fetchSessions]);

  const selected = sessions.find(s => s.id === selectedId);

  const phaseLabel = (s: SessionItem) => {
    const map: Record<string, string> = {
      created: "已创建", ontology_running: "本体生成中", graph_running: "图谱构建中",
      agents_running: "智能体生成中", simulating: "模拟中", reporting: "报告生成中",
      complete: "已完成", failed: "失败", paused: "已暂停",
    };
    return map[s.status] || s.status;
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "320px 1fr", height: "100%", overflow: "hidden" }}>
      {/* ── Left Panel: Sessions ── */}
      <div style={{ borderRight: "1px solid var(--line)", overflow: "auto", padding: 12 }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 15 }}>推演引擎</h3>

        <div className="card" style={{ marginBottom: 10 }}>
          <input
            style={{ height: 32, marginBottom: 6 }}
            placeholder="会话标题"
            value={title}
            onChange={e => setTitle(e.target.value)}
          />
          <textarea
            style={{ height: 100, fontSize: 12, marginBottom: 6 }}
            placeholder="粘贴种子材料（新闻、报告、小说片段等）..."
            value={sourceMaterial}
            onChange={e => setSourceMaterial(e.target.value)}
          />
          <button
            className="btnPrimary"
            style={{ width: "100%", height: 32, fontSize: 13 }}
            onClick={handleCreate}
            disabled={creating || !serviceRunning}
          >
            {creating ? "创建中..." : "创建推演会话"}
          </button>
        </div>

        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>会话列表</div>
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
              {s.agent_count > 0 && ` · ${s.agent_count} 智能体`}
            </div>
            <div style={{ marginTop: 2, display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: "var(--muted2)", fontSize: 10 }}>
                {s.current_round}/{s.total_rounds} 轮
              </span>
              <button
                className="btnSmall btnSmallDanger"
                style={{ padding: "1px 6px", fontSize: 10 }}
                onClick={e => { e.stopPropagation(); handleDelete(s.id); }}
              >
                删除
              </button>
            </div>
          </div>
        ))}
        {sessions.length === 0 && (
          <div style={{ color: "var(--muted)", fontSize: 12, textAlign: "center", padding: 20 }}>
            暂无推演会话
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
                {selected.agent_count > 0 && <span className="pill">{selected.agent_count} 智能体</span>}
                {selected.current_round > 0 && <span className="pill">{selected.current_round}/{selected.total_rounds} 轮</span>}
              </div>
              <div>
                <button
                  className="btnSmall btnSmallPrimary"
                  style={{ marginRight: 6 }}
                  onClick={handleStart}
                  disabled={loading || selected.status === "simulating"}
                >
                  {selected.status === "complete" ? "重新推演" : loading ? "运行中..." : "启动推演"}
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
                  {selected.status === "created" ? "启动推演后可查看动态知识图谱" : "图谱生成中..."}
                </div>
              )}
            </div>

            {/* Logs */}
            <div ref={logsRef} style={{ maxHeight: 160, overflow: "auto", borderTop: "1px solid var(--line)", padding: 8, fontSize: 11 }}>
              {logs.length === 0 && (
                <div style={{ color: "var(--muted)", textAlign: "center", padding: 10 }}>暂无日志</div>
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
            选择或创建一个推演会话以开始
          </div>
        )}
      </div>
    </div>
  );
}
