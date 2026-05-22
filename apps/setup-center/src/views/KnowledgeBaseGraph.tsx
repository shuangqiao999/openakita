import { useEffect, useRef, useState, useCallback } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { Loader2, Plus, Minus, RotateCw, X, Search, AlertTriangle } from "lucide-react";

interface Props {
  apiBaseUrl: string;
  refreshKey?: number;
}

type DocBrief = { id: string; name: string; status?: string };

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
  const [webglError, setWebglError] = useState(false);
  const [docSearch, setDocSearch] = useState("");
  const [showDocDropdown, setShowDocDropdown] = useState(false);
  const previewTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const docDropdownRef = useRef<HTMLDivElement>(null);

  const fetchGraph = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    const timeoutId = setTimeout(() => controller.abort(), 30_000);
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
      const warnings: string[] = [];
      if (meta.truncated && meta.total_candidates > meta.max_nodes) {
        warnings.push(t("kb.graph.truncationMsg", { totalCandidates: meta.total_candidates, maxNodes: meta.max_nodes }));
      }
      if (meta.semantic_incomplete) {
        warnings.push("语义边计算超时，跨文档关联可能不完整");
      }
      if (warnings.length) {
        const key = `kb_graph_warning_${meta.total_candidates ?? 0}_${meta.semantic_incomplete ? 1 : 0}`;
        if (localStorage.getItem(key) !== "1") {
          setTruncationMsg(warnings.join(" "));
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
      clearTimeout(timeoutId);
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
    const onContextLost = () => setWebglError(true);
    const el = containerRef.current;
    if (el) {
      el.addEventListener("webglcontextlost", onContextLost);
      return () => el.removeEventListener("webglcontextlost", onContextLost);
    }
  }, []);

  useEffect(() => {
    const onOutsideClick = (e: MouseEvent) => {
      if (docDropdownRef.current && !docDropdownRef.current.contains(e.target as Node)) {
        setShowDocDropdown(false);
      }
    };
    document.addEventListener("mousedown", onOutsideClick);
    return () => document.removeEventListener("mousedown", onOutsideClick);
  }, []);

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
      const key = `kb_graph_warning_${graphData.meta.total_candidates}_${graphData.meta.semantic_incomplete ? 1 : 0}`;
      localStorage.setItem(key, "1");
    }
  };

  const filteredDocs = docs.filter(d =>
    !docSearch || d.name.toLowerCase().includes(docSearch.toLowerCase())
  );

  if (webglError) {
    return (
      <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", height: "100%", color: "#f59e0b", gap: 8 }}>
        <AlertTriangle size={32} />
        <span style={{ fontSize: 13 }}>{t("kb.graph.webglError")}</span>
        <span style={{ fontSize: 11, color: "#94a3b8" }}>{t("kb.graph.webglHint")}</span>
      </div>
    );
  }

  if (loading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%", color: "#94a3b8" }}>
        <Loader2 size={24} style={{ animation: "spin 1s linear infinite", marginRight: 8 }} />
        {t("kb.graph.loading")}
      </div>
    );
  }

  if (!loading && graphData.nodes.length === 0) {
    return (
      <div style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", height: "100%", color: "#94a3b8", gap: 8 }}>
        <Search size={32} style={{ opacity: 0.5 }} />
        <span style={{ fontSize: 13 }}>{t("kb.graph.emptyTitle")}</span>
        <span style={{ fontSize: 11 }}>{t("kb.graph.emptyHint")}</span>
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
        <div ref={docDropdownRef} style={{ position: "relative" }}>
          <div
            onClick={() => setShowDocDropdown(v => !v)}
            style={{
              background: "#1e293b", color: "#e2e8f0", border: "1px solid #334155",
              borderRadius: 4, padding: "4px 8px", fontSize: 12, cursor: "pointer",
              minWidth: 140, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 4,
            }}
          >
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {selectedDocId ? docs.find(d => d.id === selectedDocId)?.name || "" : t("kb.graph.allDocsWithCount", { count: graphData.nodes.length })}
            </span>
            <span style={{ fontSize: 10, opacity: 0.5 }}>▼</span>
          </div>
          {showDocDropdown && (
            <div style={{
              position: "absolute", top: "100%", left: 0, zIndex: 20,
              background: "#1e293b", border: "1px solid #334155", borderRadius: 4,
              marginTop: 4, maxHeight: 200, overflow: "hidden", width: 220,
            }}>
              <input
                value={docSearch}
                onChange={e => setDocSearch(e.target.value)}
                placeholder={t("kb.graph.searchDocs")}
                autoFocus
                style={{
                  width: "100%", padding: "6px 8px", border: "none", borderBottom: "1px solid #334155",
                  background: "#0f172a", color: "#e2e8f0", fontSize: 11, outline: "none",
                  boxSizing: "border-box",
                }}
                onClick={e => e.stopPropagation()}
              />
              <div style={{ overflow: "auto", maxHeight: 160 }}>
                <div
                  onClick={() => { setSelectedDocId(""); setShowDocDropdown(false); setDocSearch(""); }}
                  style={{ padding: "5px 8px", fontSize: 11, cursor: "pointer", color: selectedDocId ? "#94a3b8" : "#3b82f6" }}
                >
                  {t("kb.graph.allDocsWithCount", { count: graphData.nodes.length })}
                </div>
                {filteredDocs.map(d => (
                  <div
                    key={d.id}
                    onClick={() => { setSelectedDocId(d.id); setShowDocDropdown(false); setDocSearch(""); }}
                    style={{
                      padding: "5px 8px", fontSize: 11, cursor: "pointer",
                      color: selectedDocId === d.id ? "#3b82f6" : "#e2e8f0",
                      background: selectedDocId === d.id ? "rgba(59,130,246,0.1)" : "transparent",
                    }}
                  >
                    {d.name}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <label style={{ color: "#e2e8f0", fontSize: 12, display: "flex", alignItems: "center", gap: 4, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={includeSemantic}
            onChange={e => setIncludeSemantic(e.target.checked)}
            style={{ cursor: "pointer" }}
          />
          {t("kb.graph.semanticEdges")}
        </label>
        {includeSemantic && (
          <label style={{ color: "#e2e8f0", fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <span>{t("kb.graph.threshold")} {similarityThreshold.toFixed(2)}</span>
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
        <button onClick={handleZoomIn} title={t("kb.graph.zoomIn")} style={zoomBtnStyle}><Plus size={16} /></button>
        <button onClick={handleZoomOut} title={t("kb.graph.zoomOut")} style={zoomBtnStyle}><Minus size={16} /></button>
        <button onClick={handleResetView} title={t("kb.graph.resetView")} style={zoomBtnStyle}><RotateCw size={14} /></button>
      </div>

      <div style={{
        position: "absolute", bottom: 10, left: 10, zIndex: 10,
        background: "rgba(0,0,0,0.55)", borderRadius: 8, padding: "6px 10px",
        color: "#94a3b8", fontSize: 10, display: "flex", gap: 12,
      }}>
        {[
          [t("kb.graph.legendNode"), ""],
          [t("kb.graph.legendSeqEdge"), ""],
          [t("kb.graph.legendSimEdge"), ""],
        ].map(([label]) => (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span>{label}</span>
          </div>
        ))}
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
