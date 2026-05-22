import { useEffect, useRef, useState, useCallback } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { Loader2, Plus, Minus, RotateCw, X } from "lucide-react";

interface Props {
  apiBaseUrl: string;
  refreshKey?: number;
}

type DocBrief = { id: string; name: string; status?: string };

const TRUNCATION_DISMISSED_KEY = "kb_graph_truncation_dismissed";

export function KnowledgeBaseGraph({ apiBaseUrl, refreshKey = 0 }: Props) {
  const { t } = useTranslation();
  const fgRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [graphData, setGraphData] = useState<{ nodes: any[]; links: any[]; meta?: any }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [includeSemantic, setIncludeSemantic] = useState(false);
  const [similarityThreshold, setSimilarityThreshold] = useState(0.75);
  const [selectedDocId, setSelectedDocId] = useState("");
  const [docs, setDocs] = useState<DocBrief[]>([]);
  const [dimensions, setDimensions] = useState({ w: 800, h: 500 });
  const [showTruncation, setShowTruncation] = useState(false);
  const [truncationMsg, setTruncationMsg] = useState("");
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [previewPos, setPreviewPos] = useState({ x: 0, y: 0 });
  const [showPreview, setShowPreview] = useState(false);
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const fetchGraph = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (selectedDocId) params.set("doc_id", selectedDocId);
      if (includeSemantic) {
        params.set("include_semantic", "true");
        params.set("similarity_threshold", String(similarityThreshold));
      }
      params.set("max_nodes", "2000");
      const res = await safeFetch(`${apiBaseUrl}/api/kb/graph?${params.toString()}`, {
        signal: controller.signal,
      });
      const data = await res.json();
      setGraphData({ nodes: data.nodes || [], links: data.links || [], meta: data.meta });

      const meta = data.meta || {};
      if (meta.truncated && meta.total_candidates > meta.max_nodes) {
        const key = `${TRUNCATION_DISMISSED_KEY}_${meta.total_candidates}`;
        if (localStorage.getItem(key) !== "1") {
          setTruncationMsg(`知识库共 ${meta.total_candidates} 个节点，当前仅显示前 ${meta.max_nodes} 个，请使用文档过滤缩小范围。`);
          setShowTruncation(true);
        }
      } else {
        setShowTruncation(false);
      }
    } catch (e: any) {
      if (e.name !== "AbortError") {
        setGraphData({ nodes: [], links: [] });
      }
    } finally {
      if (controller.signal.aborted) return;
      setLoading(false);
    }
  }, [apiBaseUrl, selectedDocId, includeSemantic, similarityThreshold]);

  const fetchGraphDebounced = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(fetchGraph, 300);
  }, [fetchGraph]);

  useEffect(() => {
    fetchGraphDebounced();
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [fetchGraphDebounced, refreshKey]);

  useEffect(() => {
    if (!apiBaseUrl) return;
    safeFetch(`${apiBaseUrl}/api/kb/documents?limit=200`)
      .then(r => r.json())
      .then(d => setDocs((d.documents || []).filter((dd: DocBrief) => dd.status === "ready")))
      .catch(() => {});
  }, [apiBaseUrl]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        setDimensions({ w: entry.contentRect.width, h: entry.contentRect.height });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!graphData.nodes.length || dimensions.w <= 0 || dimensions.h <= 0) return;
    const timer = setTimeout(() => {
      try {
        (fgRef.current as any)?.zoomToFit?.(400, 60);
      } catch { /* ignore */ }
    }, 1000);
    return () => clearTimeout(timer);
  }, [graphData, dimensions]);

  const handleNodeClick = useCallback((node: any, event?: MouseEvent) => {
    const fg = fgRef.current as any;
    if (!fg) return;
    const dist = 80;
    fg.cameraPosition(
      { x: (node.x || 0) + dist, y: (node.y || 0) + dist * 0.3, z: (node.z || 0) + dist },
      { x: node.x, y: node.y, z: node.z },
      600,
    );
    setSelectedNode(node);
    if (event) {
      setPreviewPos({ x: event.clientX, y: event.clientY });
    }
    setShowPreview(true);
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => setShowPreview(false), 5000);
  }, []);

  const handleZoomIn = () => {
    const fg = fgRef.current as any;
    if (!fg) return;
    const pos = fg.cameraPosition();
    fg.cameraPosition({ x: pos.x, y: pos.y, z: Math.max(20, pos.z * 0.8) }, pos, 300);
  };

  const handleZoomOut = () => {
    const fg = fgRef.current as any;
    if (!fg) return;
    const pos = fg.cameraPosition();
    fg.cameraPosition({ x: pos.x, y: pos.y, z: pos.z * 1.25 }, pos, 300);
  };

  const handleResetView = () => {
    try {
      (fgRef.current as any)?.zoomToFit?.(400, 60);
    } catch { /* ignore */ }
  };

  const dismissTruncation = () => {
    setShowTruncation(false);
    if (graphData.meta?.total_candidates) {
      const key = `${TRUNCATION_DISMISSED_KEY}_${graphData.meta.total_candidates}`;
      localStorage.setItem(key, "1");
    }
  };

  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%", color: "#94a3b8" }}>
        <Loader2 size={24} style={{ animation: "spin 1s linear infinite", marginRight: 8 }} />
        加载图谱...
      </div>
    );
  }

  return (
    <div ref={containerRef} style={{ position: "relative", width: "100%", height: "100%", overflow: "hidden" }}>
      {dimensions.w > 0 && dimensions.h > 0 && (
        <ForceGraph3D
          ref={fgRef}
          graphData={graphData}
          width={dimensions.w}
          height={dimensions.h}
          backgroundColor="#111827"
          nodeLabel={(node: any) =>
            `[${node.doc_name || ""}] ${(node.content || node.name || "").slice(0, 100)}`
          }
          nodeAutoColorBy="group"
          nodeVal={2}
          linkWidth={(l: any) => Math.max(0.3, (l.value || 1) * 1.2)}
          linkOpacity={0.5}
          onNodeClick={handleNodeClick as any}
          enablePointerInteraction={true}
          d3AlphaDecay={0.05}
          warmupTicks={30}
          cooldownTicks={50}
        />
      )}

      {showTruncation && (
        <div style={{
          position: "absolute", bottom: 10, left: "50%", transform: "translateX(-50%)", zIndex: 10,
          background: "rgba(239, 68, 68, 0.9)", color: "#fff", borderRadius: 8,
          padding: "8px 16px", fontSize: 12, display: "flex", alignItems: "center", gap: 10,
          maxWidth: "90%",
        }}>
          <span>{truncationMsg}</span>
          <button
            onClick={dismissTruncation}
            style={{ background: "none", border: "none", color: "#fff", cursor: "pointer", padding: 0 }}
          >
            <X size={14} />
          </button>
        </div>
      )}

      <div style={{
        position: "absolute", top: 10, left: 10, zIndex: 10,
        display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
        background: "rgba(0,0,0,0.5)", borderRadius: 8, padding: "6px 10px",
      }}>
        <select
          value={selectedDocId}
          onChange={e => setSelectedDocId(e.target.value)}
          style={{
            background: "#1e293b", color: "#e2e8f0", border: "1px solid #334155",
            borderRadius: 4, padding: "4px 8px", fontSize: 12,
          }}
        >
          <option value="">所有文档 ({graphData.nodes.length} 节点)</option>
          {docs.map(d => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
        <label style={{ color: "#e2e8f0", fontSize: 12, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={includeSemantic}
            onChange={e => setIncludeSemantic(e.target.checked)}
            style={{ cursor: "pointer" }}
          />
          语义边
        </label>
        {includeSemantic && (
          <label style={{ color: "#e2e8f0", fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <span>阈值 {similarityThreshold.toFixed(2)}</span>
            <input
              type="range"
              min="0.5"
              max="0.95"
              step="0.05"
              value={similarityThreshold}
              onChange={e => setSimilarityThreshold(parseFloat(e.target.value))}
              style={{ width: 60, cursor: "pointer", accentColor: "#3b82f6" }}
            />
          </label>
        )}
      </div>

      <div style={{
        position: "absolute", top: 10, right: 10, zIndex: 10,
        display: "flex", flexDirection: "column", gap: 6,
      }}>
        <button onClick={handleZoomIn} title="放大" style={zoomBtnStyle}><Plus size={16} /></button>
        <button onClick={handleZoomOut} title="缩小" style={zoomBtnStyle}><Minus size={16} /></button>
        <button onClick={handleResetView} title="重置视图" style={zoomBtnStyle}><RotateCw size={14} /></button>
      </div>

      {showPreview && selectedNode && (
        <div style={{
          position: "fixed", left: Math.min(previewPos.x + 12, window.innerWidth - 280),
          top: Math.min(previewPos.y + 12, window.innerHeight - 180),
          zIndex: 9999, background: "rgba(15,23,42,0.95)", color: "#e2e8f0",
          borderRadius: 8, padding: 12, maxWidth: 280, fontSize: 12,
          boxShadow: "0 4px 20px rgba(0,0,0,0.5)", border: "1px solid #334155",
          pointerEvents: "auto",
        }}>
          <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>
            {selectedNode.doc_name} · #{selectedNode.chunk_index ?? ""}
          </div>
          <div style={{ lineHeight: 1.5, maxHeight: 120, overflow: "auto" }}>
            {selectedNode.content?.slice(0, 200) || selectedNode.name || "(无内容)"}
          </div>
          <button
            onClick={() => setShowPreview(false)}
            style={{
              position: "absolute", top: 4, right: 4, background: "none", border: "none",
              color: "#94a3b8", cursor: "pointer", padding: 2,
            }}
          >
            <X size={12} />
          </button>
        </div>
      )}
    </div>
  );
}

const zoomBtnStyle: React.CSSProperties = {
  width: 36, height: 36, borderRadius: "50%",
  background: "rgba(255,255,255,0.15)", backdropFilter: "blur(4px)",
  border: "1px solid rgba(255,255,255,0.2)", color: "#fff",
  fontSize: 18, fontWeight: 700, cursor: "pointer",
  display: "flex", alignItems: "center", justifyContent: "center",
};
