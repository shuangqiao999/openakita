import { useEffect, useRef, useState, useCallback } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { Loader2, Plus, Minus } from "lucide-react";

interface Props {
  apiBaseUrl: string;
  refreshKey?: number;
}

type DocBrief = { id: string; name: string; status?: string };

export function KnowledgeBaseGraph({ apiBaseUrl, refreshKey = 0 }: Props) {
  const { t } = useTranslation();
  const fgRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [graphData, setGraphData] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [includeSemantic, setIncludeSemantic] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState("");
  const [docs, setDocs] = useState<DocBrief[]>([]);
  const [dimensions, setDimensions] = useState({ w: 800, h: 500 });

  const fetchGraph = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (selectedDocId) params.set("doc_id", selectedDocId);
      if (includeSemantic) params.set("include_semantic", "true");
      params.set("max_nodes", "2000");
      const res = await safeFetch(`${apiBaseUrl}/api/kb/graph?${params.toString()}`);
      const data = await res.json();
      setGraphData({ nodes: data.nodes || [], links: data.links || [] });
    } catch {
      setGraphData({ nodes: [], links: [] });
    } finally {
      setLoading(false);
    }
  }, [apiBaseUrl, selectedDocId, includeSemantic]);

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph, refreshKey]);

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
        const fg = fgRef.current as any;
        fg?.zoomToFit?.(400, 60);
      } catch { /* ignore */ }
    }, 1000);
    return () => clearTimeout(timer);
  }, [graphData, dimensions]);

  const handleNodeClick = useCallback((node: any) => {
    const fg = fgRef.current as any;
    if (!fg) return;
    const dist = 80;
    fg.cameraPosition(
      { x: (node.x || 0) + dist, y: (node.y || 0) + dist * 0.3, z: (node.z || 0) + dist },
      { x: node.x, y: node.y, z: node.z },
      600,
    );
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

  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%", color: "#94a3b8" }}>
        <Loader2 size={24} style={{ animation: "spin 1s linear infinite", marginRight: 8 }} />
        加载图谱...
      </div>
    );
  }

  return (
    <div ref={containerRef} style={{ position: "relative", width: "100%", height: "100%" }}>
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

      <div style={{
        position: "absolute", top: 10, left: 10, zIndex: 10,
        display: "flex", gap: 8, alignItems: "center",
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
      </div>

      <div style={{
        position: "absolute", top: 10, right: 10, zIndex: 10,
        display: "flex", flexDirection: "column", gap: 6,
      }}>
        <button
          onClick={handleZoomIn}
          title="放大"
          style={{
            width: 36, height: 36, borderRadius: "50%",
            background: "rgba(255,255,255,0.15)", backdropFilter: "blur(4px)",
            border: "1px solid rgba(255,255,255,0.2)", color: "#fff",
            fontSize: 18, fontWeight: 700, cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <Plus size={16} />
        </button>
        <button
          onClick={handleZoomOut}
          title="缩小"
          style={{
            width: 36, height: 36, borderRadius: "50%",
            background: "rgba(255,255,255,0.15)", backdropFilter: "blur(4px)",
            border: "1px solid rgba(255,255,255,0.2)", color: "#fff",
            fontSize: 18, fontWeight: 700, cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <Minus size={16} />
        </button>
      </div>
    </div>
  );
}
