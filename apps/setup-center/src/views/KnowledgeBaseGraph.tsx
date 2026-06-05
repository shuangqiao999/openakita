import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import ForceGraph3D from "react-force-graph-3d";
import { useTranslation } from "react-i18next";
import { safeFetch } from "../providers";
import { Loader2, Plus, Minus, RotateCw, X, Search, AlertTriangle, StopCircle, Eye, EyeOff } from "lucide-react";

interface Props {
  apiBaseUrl: string;
  refreshKey?: number;
}

type DocBrief = { id: string; name: string; status?: string };

// ── 类别颜色映射 ──
const CATEGORY_COLORS: Record<string, string> = {
  theory: "#1f77b4",
  clinical: "#ff7f0e",
  index: "#d62728",
  resource: "#2ca02c",
  code: "#9467bd",
  config: "#8c564b",
  data: "#e377c2",
  guide: "#17becf",
  default: "#7f7f7f",
};

const CATEGORY_KEYWORDS: { pattern: RegExp; cat: string }[] = [
  { pattern: /theory|理论|学说|原理|基础|阴阳|五行|经络/i, cat: "theory" },
  { pattern: /clinical|临床|疾病|诊断|治疗|方剂|症状|病理|case/i, cat: "clinical" },
  { pattern: /index|目录|索引|index\.|table.?of/i, cat: "index" },
  { pattern: /resource|资源|素材|图片|参考|ref/i, cat: "resource" },
  { pattern: /\.(py|js|ts|rs|go|java|cpp|c)$|code|源码|算法/i, cat: "code" },
  { pattern: /config|配置|setting|\.(yaml|json|toml|ini|cfg)/i, cat: "config" },
  { pattern: /data|数据|统计|record|统计/i, cat: "data" },
  { pattern: /guide|指南|教程|guide|tutorial|manual|手册/i, cat: "guide" },
];

function inferCategory(docName: string, fileType: string): string {
  const name = docName.toLowerCase();
  for (const { pattern, cat } of CATEGORY_KEYWORDS) {
    if (pattern.test(name)) return cat;
  }
  if (fileType === ".py" || fileType === ".js" || fileType === ".ts" || fileType === ".rs") return "code";
  if (fileType === ".yaml" || fileType === ".json" || fileType === ".toml" || fileType === ".cfg") return "config";
  return "default";
}

function getCatLabel(cat: string): string {
  const map: Record<string, string> = {
    theory: "理论", clinical: "临床", index: "索引", resource: "资源",
    code: "代码", config: "配置", data: "数据", guide: "指南", default: "其他",
  };
  return map[cat] || cat;
}

function computeDegrees(nodes: any[], links: any[]): Map<string, number> {
  const deg = new Map<string, number>();
  for (const n of nodes) deg.set(n.id, 0);
  for (const l of links) {
    const sid = typeof l.source === "object" ? l.source.id : l.source;
    const tid = typeof l.target === "object" ? l.target.id : l.target;
    deg.set(sid, (deg.get(sid) || 0) + 1);
    deg.set(tid, (deg.get(tid) || 0) + 1);
  }
  return deg;
}

// ── 主组件 ──

export function KnowledgeBaseGraph({ apiBaseUrl, refreshKey = 0 }: Props) {
  const { t } = useTranslation();
  const fgRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [rawGraphData, setRawGraphData] = useState<{ nodes: any[]; links: any[]; meta?: any }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [similarityThreshold, setSimilarityThreshold] = useState(0.6);
  const [selectedDocId, setSelectedDocId] = useState("");
  const [docs, setDocs] = useState<DocBrief[]>([]);
  const [dimensions, setDimensions] = useState({ w: 0, h: 0 });
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
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ── P0: 度过滤 ──
  const [minDegree, setMinDegree] = useState(0);

  // ── P1: 类别过滤 ──
  const [activeCategories, setActiveCategories] = useState<Set<string>>(new Set());

  // ── P0: 点击高亮 ──
  const [highlightNode, setHighlightNode] = useState<{ id: string; neighbors: Set<string> } | null>(null);

  // ── P1: 搜索 ──
  const [searchQuery, setSearchQuery] = useState("");
  const [searchMatchIds, setSearchMatchIds] = useState<Set<string>>(new Set());

  // ── P1: 隐藏孤立节点 ──
  const [hideIsolated, setHideIsolated] = useState(false);

  // ── P2: 停止模拟 ──
  const [simRunning, setSimRunning] = useState(true);

  // ── 为每个节点附加类别 ──
  const enrichedNodes = useMemo(() => {
    return rawGraphData.nodes.map(n => ({
      ...n,
      category: inferCategory(n.doc_name || "", n.type || ""),
    }));
  }, [rawGraphData.nodes]);

  // ── 计算度数 ──
  const degreeMap = useMemo(() => computeDegrees(enrichedNodes, rawGraphData.links), [enrichedNodes, rawGraphData.links]);

  // ── 提取所有类别 ──
  const allCategories = useMemo(() => {
    const cats = new Set<string>();
    for (const n of enrichedNodes) cats.add(n.category);
    return Array.from(cats).sort();
  }, [enrichedNodes]);

  // 批量 mode：无任何过滤时直接用全图数据
  const filteredGraphData = useMemo(() => {
    if (enrichedNodes.length === 0) return { nodes: [], links: [] };

    const catFilterActive = activeCategories.size > 0;
    const degFilterActive = minDegree > 0;
    const searchActive = searchQuery.trim().length > 0;
    const anyFilter = catFilterActive || degFilterActive || searchActive || hideIsolated;

    if (!anyFilter) return rawGraphData;

    // 先按度和孤立节点过滤
    let keepIds = new Set<string>();
    for (const n of enrichedNodes) {
      const deg = degreeMap.get(n.id) || 0;
      if (deg < minDegree) continue;
      if (hideIsolated && deg === 0) continue;
      keepIds.add(n.id);
    }

    // 类别过滤
    if (catFilterActive) {
      const catIds = new Set<string>();
      for (const n of enrichedNodes) {
        if (activeCategories.has(n.category) && keepIds.has(n.id)) catIds.add(n.id);
      }
      keepIds = catIds;
    }

    // 搜索过滤：保留匹配节点 + 1-hop 邻居
    if (searchActive) {
      const q = searchQuery.trim().toLowerCase();
      const matchIds = new Set<string>();
      for (const n of enrichedNodes) {
        if (n.name?.toLowerCase().includes(q)) matchIds.add(n.id);
      }
      setSearchMatchIds(matchIds);
      // 扩展 1-hop
      const expanded = new Set(matchIds);
      for (const l of rawGraphData.links) {
        const sid = typeof l.source === "object" ? l.source.id : l.source;
        const tid = typeof l.target === "object" ? l.target.id : l.target;
        if (matchIds.has(sid)) expanded.add(tid);
        if (matchIds.has(tid)) expanded.add(sid);
      }
      keepIds = new Set([...keepIds].filter(id => expanded.has(id)));
    }

    const filteredNodes = enrichedNodes.filter(n => keepIds.has(n.id));
    const filteredNodeIds = new Set(filteredNodes.map(n => n.id));
    const filteredLinks = rawGraphData.links.filter(l => {
      const sid = typeof l.source === "object" ? l.source.id : l.source;
      const tid = typeof l.target === "object" ? l.target.id : l.target;
      return filteredNodeIds.has(sid) && filteredNodeIds.has(tid);
    });

    return { nodes: filteredNodes, links: filteredLinks };
  }, [enrichedNodes, rawGraphData, activeCategories, minDegree, searchQuery, hideIsolated, degreeMap]);

  const fetchGraph = useCallback(async (silent = false) => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    if (!silent) setLoading(true);
    try {
      const params = new URLSearchParams();
      if (selectedDocId) params.set("doc_id", selectedDocId);
      params.set("include_semantic", "true");
      params.set("similarity_threshold", String(similarityThreshold));
      const res = await safeFetch(`${apiBaseUrl}/api/kb/graph?${params.toString()}`, {
        signal: controller.signal,
      });
      const data = await res.json();
      setRawGraphData({ nodes: data.nodes || [], links: data.links || [], meta: data.meta });
      setHighlightNode(null);
      setSearchMatchIds(new Set());
      setSearchQuery("");

      const meta = data.meta || {};
      const warnings: string[] = [];
      if (meta.truncated && meta.total_candidates > meta.max_nodes) {
        warnings.push(t("kb.graph.truncationMsg", { totalCandidates: meta.total_candidates, maxNodes: meta.max_nodes }));
      }
      if (meta.semantic_incomplete) {
        warnings.push(t("kb.semanticTimeout"));
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
      if (e.name !== "AbortError" && !silent) {
        setRawGraphData({ nodes: [], links: [] });
      }
    } finally {
      if (controller.signal.aborted) return;
      if (!silent) setLoading(false);
    }
  }, [apiBaseUrl, selectedDocId, similarityThreshold]);

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
    let cancelled = false;
    safeFetch(`${apiBaseUrl}/api/kb/documents?limit=200`)
      .then(r => r.json())
      .then(d => { if (!cancelled) setDocs((d.documents || []).filter((dd: DocBrief) => dd.status === "ready")); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [apiBaseUrl]);

  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
      try { (fgRef.current as any)?._destructor?.(); } catch { /* ignore */ }
    };
  }, []);

  useEffect(() => {
    if (!(rawGraphData.meta?.semantic_pending)) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return;
    }
    if (pollRef.current) return;
    pollRef.current = setInterval(() => {
      fetchGraph(true);
    }, 3000);
    return () => {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [rawGraphData.meta?.semantic_pending, fetchGraph]);

  useEffect(() => {
    if (loading || !rawGraphData.nodes.length) return;
    const el = containerRef.current?.querySelector("canvas");
    if (!el) return;
    const onContextLost = () => setWebglError(true);
    el.addEventListener("webglcontextlost", onContextLost);
    return () => el.removeEventListener("webglcontextlost", onContextLost);
  }, [loading, rawGraphData.nodes.length]);

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
  }, [loading, rawGraphData.nodes.length]);

  useEffect(() => {
    if (!filteredGraphData.nodes.length || dimensions.w <= 0 || dimensions.h <= 0) return;
    const timer = setTimeout(() => {
      try {
        (fgRef.current as any)?.zoomToFit?.(400, 60);
      } catch { /* ignore */ }
    }, 200);
    return () => clearTimeout(timer);
  }, [filteredGraphData, dimensions]);

  // ── P0: 点击高亮邻居 ──
  const handleNodeClick = useCallback((node: any, event?: MouseEvent) => {
    const fg = fgRef.current as any;
    if (!fg) return;

    const dist = 80;
    fg.cameraPosition(
      { x: (node.x || 0) + dist, y: (node.y || 0) + dist * 0.3, z: (node.z || 0) + dist },
      { x: node.x, y: node.y, z: node.z },
      600,
    );

    const neighbors = new Set<string>();
    for (const l of rawGraphData.links) {
      const sid = typeof l.source === "object" ? l.source.id : l.source;
      const tid = typeof l.target === "object" ? l.target.id : l.target;
      if (sid === node.id) neighbors.add(tid);
      if (tid === node.id) neighbors.add(sid);
    }
    setHighlightNode({ id: node.id, neighbors });

    setSelectedNode(node);
    if (event) setPreviewPos({ x: event.clientX, y: event.clientY });
    setShowPreview(true);
    if (previewTimerRef.current) clearTimeout(previewTimerRef.current);
    previewTimerRef.current = setTimeout(() => setShowPreview(false), 5000);
  }, [rawGraphData.links]);

  // ── 点击空白清除高亮 ──
  const handleBackgroundClick = useCallback(() => {
    setHighlightNode(null);
  }, []);

  // ── 动态 nodeColor ──
  const nodeColorFn = useCallback((node: any) => {
    if (highlightNode) {
      if (node.id === highlightNode.id) return "#fbbf24";
      if (highlightNode.neighbors.has(node.id)) return "#f59e0b";
      return "rgba(100,100,120,0.15)";
    }
    if (searchMatchIds.size > 0) {
      if (searchMatchIds.has(node.id)) return "#fbbf24";
      return "rgba(100,100,120,0.15)";
    }
    return CATEGORY_COLORS[node.category] || CATEGORY_COLORS.default;
  }, [highlightNode, searchMatchIds]);

  // ── 动态 linkColor ──
  const linkColorFn = useCallback((link: any) => {
    if (highlightNode) {
      const sid = typeof link.source === "object" ? link.source.id : link.source;
      const tid = typeof link.target === "object" ? link.target.id : link.target;
      if (sid === highlightNode.id || tid === highlightNode.id) return "rgba(251,191,36,0.6)";
      return "rgba(100,100,120,0.04)";
    }
    if (searchMatchIds.size > 0) {
      const sid = typeof link.source === "object" ? link.source.id : link.source;
      const tid = typeof link.target === "object" ? link.target.id : link.target;
      if (searchMatchIds.has(sid) || searchMatchIds.has(tid)) return "rgba(251,191,36,0.5)";
      return "rgba(100,100,120,0.04)";
    }
    return "rgba(255,255,255,0.3)";
  }, [highlightNode, searchMatchIds]);

  // ── 动态 nodeVal ──
  const nodeValFn = useCallback((node: any) => {
    if (highlightNode && node.id === highlightNode.id) return 5;
    return 2;
  }, [highlightNode]);

  // ── 缩放控制 ──
  const handleZoomIn = () => {
    const fg = fgRef.current as any;
    if (!fg) return;
    const pos = fg.cameraPosition();
    fg.cameraPosition(
      { x: pos.x, y: pos.y, z: Math.max(20, pos.z * 0.8) },
      { x: 0, y: 0, z: 0 },
      300,
    );
  };

  const handleZoomOut = () => {
    const fg = fgRef.current as any;
    if (!fg) return;
    const pos = fg.cameraPosition();
    fg.cameraPosition(
      { x: pos.x, y: pos.y, z: pos.z * 1.25 },
      { x: 0, y: 0, z: 0 },
      300,
    );
  };

  const handleResetView = () => {
    try {
      (fgRef.current as any)?.zoomToFit?.(400, 60);
    } catch { /* ignore */ }
  };

  const handleStopSim = () => {
    const fg = fgRef.current as any;
    if (!fg) return;
    try {
      const sim = fg.d3Force?.();
      if (sim) {
        sim.stop();
        setSimRunning(false);
      }
    } catch { /* ignore */ }
  };

  const handleResumeSim = () => {
    const fg = fgRef.current as any;
    if (!fg) return;
    try {
      const sim = fg.d3Force?.();
      if (sim) {
        sim.alpha(0.3).restart();
        setSimRunning(true);
      }
    } catch { /* ignore */ }
  };

  const dismissTruncation = () => {
    setShowTruncation(false);
    if (rawGraphData.meta?.total_candidates) {
      const key = `kb_graph_warning_${rawGraphData.meta.total_candidates}_${rawGraphData.meta.semantic_incomplete ? 1 : 0}`;
      localStorage.setItem(key, "1");
    }
  };

  const filteredDocs = docs.filter(d =>
    !docSearch || d.name.toLowerCase().includes(docSearch.toLowerCase())
  );

  const graphData = filteredGraphData;

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

  if (!loading && rawGraphData.nodes.length === 0) {
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
          nodeLabel="name"
          nodeRelSize={4}
          nodeColor={nodeColorFn}
          nodeVal={nodeValFn}
          linkWidth={(l: any) => Math.max(0.3, (l.value || 1) * 1.2)}
          linkColor={linkColorFn}
          linkOpacity={0.5}
          onNodeClick={handleNodeClick as any}
          onBackgroundClick={handleBackgroundClick}
          enablePointerInteraction={true}
          d3AlphaDecay={0.1}
          warmupTicks={10}
          cooldownTicks={20}
          onEngineStop={() => setSimRunning(false)}
          onEngineTick={() => setSimRunning(true)}
        />
      )}

      {/* ── 截断告警 ── */}
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

      {/* ── 节点/边数 ── */}
      <div style={{
        position: "absolute", top: 10, left: 10, zIndex: 10,
        background: "rgba(0,0,0,0.55)", borderRadius: 8, padding: "6px 10px",
        color: "#64748b", fontSize: 11,
      }}>
        {t("kb.graph.graphNodeCount", { nodes: graphData.nodes.length })} · {t("kb.graph.graphEdgeCount", { edges: graphData.links.length })}
      </div>

      {/* ── 控制面板 ── */}
      <div style={{
        position: "absolute", top: 48, left: 10, zIndex: 10,
        display: "flex", gap: 8, alignItems: "flex-start", flexWrap: "wrap",
        maxWidth: "calc(100% - 60px)",
      }}>
        <div style={{
          background: "rgba(0,0,0,0.5)", borderRadius: 8, padding: "8px 10px",
          display: "flex", flexDirection: "column", gap: 6, minWidth: 200,
        }}>
          {/* 文档下拉 */}
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
                {selectedDocId ? docs.find(d => d.id === selectedDocId)?.name || "" : t("kb.graph.allDocsWithCount", { count: rawGraphData.nodes.length })}
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
                    {t("kb.graph.allDocsWithCount", { count: rawGraphData.nodes.length })}
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

          {/* 相似度阈值 */}
          <label style={{ color: "#e2e8f0", fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <span>{t("kb.graph.threshold")} {similarityThreshold.toFixed(2)}</span>
            <input
              type="range" min="0.5" max="0.95" step="0.05"
              value={similarityThreshold}
              onChange={e => setSimilarityThreshold(parseFloat(e.target.value))}
              style={{ width: 60, cursor: "pointer", accentColor: "#3b82f6" }}
            />
          </label>

          {/* ── P0: 度过滤滑块 ── */}
          <label style={{ color: "#e2e8f0", fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
            <span>最小度</span>
            <input
              type="range" min="0" max="20" step="1"
              value={minDegree}
              onChange={e => { setMinDegree(parseInt(e.target.value)); setHighlightNode(null); }}
              style={{ width: 60, cursor: "pointer", accentColor: "#3b82f6" }}
            />
            <span style={{ opacity: 0.7, minWidth: 20 }}>≥{minDegree}</span>
          </label>
        </div>

        {/* ── P1: 类别过滤 + 搜索 + 孤立 ── */}
        <div style={{
          background: "rgba(0,0,0,0.5)", borderRadius: 8, padding: "8px 10px",
          display: "flex", flexDirection: "column", gap: 5, minWidth: 180,
        }}>
          {/* 搜索框 */}
          <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
            <input
              value={searchQuery}
              onChange={e => { setSearchQuery(e.target.value); if (!e.target.value) setSearchMatchIds(new Set()); }}
              placeholder="搜索节点名称..."
              style={{
                flex: 1, padding: "4px 8px", border: "1px solid #334155", borderRadius: 4,
                background: "#0f172a", color: "#e2e8f0", fontSize: 11, outline: "none",
              }}
              onKeyDown={e => { if (e.key === "Escape") { setSearchQuery(""); setSearchMatchIds(new Set()); } }}
            />
            {searchQuery && (
              <button
                onClick={() => { setSearchQuery(""); setSearchMatchIds(new Set()); }}
                style={{ background: "none", border: "none", color: "#94a3b8", cursor: "pointer", padding: 2 }}
                title="清除搜索"
              >
                <X size={12} />
              </button>
            )}
          </div>

          {/* 类别多选 */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {allCategories.map(cat => {
              const selected = activeCategories.has(cat);
              const color = CATEGORY_COLORS[cat] || CATEGORY_COLORS.default;
              return (
                <label
                  key={cat}
                  onClick={() => {
                    const next = new Set(activeCategories);
                    selected ? next.delete(cat) : next.add(cat);
                    setActiveCategories(next);
                    setHighlightNode(null);
                  }}
                  style={{
                    display: "flex", alignItems: "center", gap: 3, padding: "2px 6px", borderRadius: 4,
                    cursor: "pointer", fontSize: 10, fontWeight: selected ? 600 : 400,
                    background: selected ? `${color}44` : "#1e293b",
                    color: selected ? color : "#94a3b8",
                    border: selected ? `1px solid ${color}` : "1px solid #334155",
                    userSelect: "none",
                  }}
                >
                  <span style={{
                    width: 8, height: 8, borderRadius: "50%", background: color,
                    flexShrink: 0,
                  }} />
                  {getCatLabel(cat)}
                </label>
              );
            })}
            {activeCategories.size > 0 && (
              <button
                onClick={() => setActiveCategories(new Set())}
                style={{ fontSize: 10, color: "#f59e0b", background: "none", border: "none", cursor: "pointer", padding: "2px 4px" }}
              >
                清除
              </button>
            )}
          </div>

          {/* 折叠孤立节点 + 高亮清除 */}
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <label style={{ color: "#e2e8f0", fontSize: 11, display: "flex", alignItems: "center", gap: 4, cursor: "pointer", userSelect: "none" }}
              onClick={() => { setHideIsolated(v => !v); setHighlightNode(null); }}
            >
              {hideIsolated ? <Eye size={12} /> : <EyeOff size={12} />}
              隐藏孤立节点
            </label>
            {highlightNode && (
              <button
                onClick={() => setHighlightNode(null)}
                style={{ fontSize: 10, color: "#fbbf24", background: "none", border: "none", cursor: "pointer", padding: "2px 4px" }}
              >
                清除高亮
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── 图例 ── */}
      <div style={{
        position: "absolute", bottom: 48, left: 10, zIndex: 10,
        background: "rgba(0,0,0,0.5)", borderRadius: 8, padding: "6px 8px",
        display: "flex", flexWrap: "wrap", gap: 8,
      }}>
        {allCategories.map(cat => (
          <span key={cat} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 10, color: "#94a3b8" }}>
            <span style={{
              width: 10, height: 10, borderRadius: "50%",
              background: CATEGORY_COLORS[cat] || CATEGORY_COLORS.default,
            }} />
            {getCatLabel(cat)}
          </span>
        ))}
      </div>

      {/* ── 缩放/停止按钮 ── */}
      <div style={{
        position: "absolute", top: 10, right: 10, zIndex: 10,
        display: "flex", flexDirection: "column", gap: 6,
      }}>
        <button onClick={handleZoomIn} title={t("kb.graph.zoomIn")} style={zoomBtnStyle}><Plus size={16} /></button>
        <button onClick={handleZoomOut} title={t("kb.graph.zoomOut")} style={zoomBtnStyle}><Minus size={16} /></button>
        <button onClick={handleResetView} title={t("kb.graph.resetView")} style={zoomBtnStyle}><RotateCw size={14} /></button>
        <button
          onClick={simRunning ? handleStopSim : handleResumeSim}
          title={simRunning ? "停止布局" : "恢复布局"}
          style={{ ...zoomBtnStyle, background: simRunning ? "rgba(255,255,255,0.15)" : "rgba(251,191,36,0.4)" }}
        >
          <StopCircle size={14} />
        </button>
      </div>

      {/* ── 节点预览弹窗 ── */}
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
            <span style={{
              marginLeft: 6, display: "inline-block", padding: "1px 5px", borderRadius: 3,
              fontSize: 9, color: CATEGORY_COLORS[selectedNode.category] || CATEGORY_COLORS.default,
              background: "rgba(255,255,255,0.06)",
            }}>
              {getCatLabel(selectedNode.category)}
            </span>
            <span style={{ marginLeft: 4, opacity: 0.6 }}>度:{degreeMap.get(selectedNode.id) || 0}</span>
          </div>
          <div style={{ lineHeight: 1.5, maxHeight: 120, overflow: "auto" }}>
            {selectedNode.content?.slice(0, 200) || selectedNode.name || t("kb.graph.noContent")}
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
