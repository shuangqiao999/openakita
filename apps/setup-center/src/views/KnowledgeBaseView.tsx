import { useEffect, useState, useCallback, useRef, lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { IconBook } from "../icons";
import { safeFetch } from "../providers";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel,
  AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Loader2, Upload, Search, Trash2, FileText,
  Clock, CheckCircle, XCircle, Eye, ChevronLeft, ChevronRight, Wrench,
  List, Network
} from "lucide-react";
import ReactMarkdown from "react-markdown";

const KnowledgeBaseGraph = lazy(() =>
  import("./KnowledgeBaseGraph").then(m => ({ default: m.KnowledgeBaseGraph }))
);

interface Props {
  serviceRunning: boolean;
  apiBaseUrl?: string;
}

type DocItem = {
  id: string;
  name: string;
  file_type: string;
  upload_time: number;
  total_chunks: number;
  status: string;
  error_msg?: string;
};

type SearchResult = {
  chunk_id: string;
  document_id: string;
  document_name: string;
  content: string;
  score: number;
};

type ChunkItem = {
  id: string;
  chunk_index: number;
  content: string;
  token_count: number;
};

const STATUS_CONFIG: Record<string, { labelKey: string; color: string; icon: React.ReactNode }> = {
  processing: { labelKey: "kb.statusProcessing", color: "#f59e0b", icon: <Clock size={14} /> },
  ready: { labelKey: "kb.statusReady", color: "#10b981", icon: <CheckCircle size={14} /> },
  failed: { labelKey: "kb.statusFailed", color: "#ef4444", icon: <XCircle size={14} /> },
};

export function KnowledgeBaseView({ serviceRunning, apiBaseUrl = "" }: Props) {
  const { t } = useTranslation();
  const [documents, setDocuments] = useState<DocItem[]>([]);
  const [totalDocs, setTotalDocs] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DocItem | null>(null);
  const [viewChunks, setViewChunks] = useState<{ docName: string; chunks: ChunkItem[] } | null>(null);
  const [viewChunksLoading, setViewChunksLoading] = useState(false);
  const [kbReady, setKbReady] = useState<boolean | null>(null);
  const [activeTab, setActiveTab] = useState<"manage" | "graph">("manage");
  const [graphRefreshKey, setGraphRefreshKey] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const pageSize = 20;

  const loadDocuments = useCallback(async () => {
    if (!serviceRunning) return;
    setLoading(true);
    try {
      const offset = page * pageSize;
      const res = await safeFetch(`${apiBaseUrl}/api/kb/documents?limit=${pageSize}&offset=${offset}`);
      const data = await res.json();
      setDocuments(data.documents || []);
      setTotalDocs(data.total ?? 0);
    } catch {
      toast.error(t("kb.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [serviceRunning, page, apiBaseUrl, t]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  useEffect(() => {
    if (!serviceRunning) return;
    pollingRef.current = setInterval(() => {
      const hasProcessing = documents.some(d => d.status === "processing");
      if (hasProcessing) {
        loadDocuments();
      }
    }, 5000);
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [serviceRunning, documents, loadDocuments]);

  useEffect(() => {
    if (!serviceRunning) {
      setKbReady(null);
      return;
    }
    safeFetch(`${apiBaseUrl}/api/kb/ready`)
      .then(r => r.json())
      .then(d => setKbReady(d.ready === true))
      .catch(() => setKbReady(false));
  }, [serviceRunning, apiBaseUrl]);

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const allowedExts = [".pdf", ".docx", ".md", ".txt", ".markdown"];
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!allowedExts.includes(ext)) {
      toast.error(`不支持的文件类型: ${ext}`);
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      toast.error("文件超过 20MB 限制");
      return;
    }

    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await safeFetch(`${apiBaseUrl}/api/kb/upload`, {
        method: "POST",
        body: formData,
      });
      await res.json();
      toast.success("文件上传成功，正在后台处理...");
      setPage(0);
      loadDocuments();
      setGraphRefreshKey(k => k + 1);
    } catch {
      toast.error(t("kb.uploadFailed"));
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await safeFetch(`${apiBaseUrl}/api/kb/documents/${deleteTarget.id}`, {
        method: "DELETE",
      });
      setDeleteTarget(null);
      loadDocuments();
      setGraphRefreshKey(k => k + 1);
    } catch {
      toast.error(t("kb.deleteFailed"));
    }
  };

  const handleRepair = async (doc: DocItem) => {
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/kb/repair/${doc.id}`, { method: "POST" });
      const data = await res.json();
      if (data.status === "ok") {
        toast.success(`${t("kb.repairSuccess")} (${data.chunks} chunks)`);
      } else {
        toast.error(data.reason || t("kb.repairFailed"));
      }
      loadDocuments();
      setGraphRefreshKey(k => k + 1);
    } catch {
      toast.error(t("kb.repairFailed"));
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/kb/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: searchQuery.trim(), top_k: 10 }),
      });
      const data = await res.json();
      setSearchResults(data.results || []);
    } catch {
      toast.error(t("kb.searchFailed"));
    } finally {
      setSearching(false);
    }
  };

  const handleViewChunks = async (doc: DocItem) => {
    setViewChunksLoading(true);
    try {
      const res = await safeFetch(`${apiBaseUrl}/api/kb/status/${doc.id}`);
      const data = await res.json();
      setViewChunks({
        docName: doc.name,
        chunks: data.chunks || [],
      });
    } catch {
      toast.error(t("kb.loadFailed"));
    } finally {
      setViewChunksLoading(false);
    }
  };

  const formatTime = (ts: number) => {
    const d = new Date(ts * 1000);
    return d.toLocaleString();
  };

  const formatScore = (score: number) => `${(score * 100).toFixed(0)}%`;

  const totalPages = Math.ceil(totalDocs / pageSize);

  return (
    <div className="viewContainer" style={{ padding: 24, maxWidth: 900, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 20 }}>
        <IconBook size={24} />
        <h2 style={{ margin: 0 }}>{t("kb.title")}</h2>
        <div style={{ flex: 1 }} />
        <ToggleGroup type="single" value={activeTab} onValueChange={v => { if (v) setActiveTab(v as "manage" | "graph"); }}>
          <ToggleGroupItem value="manage"><List size={14} /> {t("kb.tabManage")}</ToggleGroupItem>
          <ToggleGroupItem value="graph"><Network size={14} /> {t("kb.tabGraph")}</ToggleGroupItem>
        </ToggleGroup>
      </div>
      <p style={{ color: "#94a3b8", fontSize: 13, marginTop: 0, marginBottom: 24 }}>
        {t("kb.description")}
      </p>

      {activeTab === "graph" && (
        <div style={{ height: "calc(100vh - 200px)", minHeight: 500 }}>
          <Suspense fallback={
            <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: "100%", color: "#94a3b8" }}>
              <Loader2 size={24} style={{ animation: "spin 1s linear infinite", marginRight: 8 }} />
              加载图谱组件...
            </div>
          }>
            <KnowledgeBaseGraph apiBaseUrl={apiBaseUrl} refreshKey={graphRefreshKey} />
          </Suspense>
        </div>
      )}

      {activeTab === "manage" && (
        <>
      {kbReady === null && (
        <div style={{ textAlign: "center", padding: 12, marginBottom: 16 }}>
          <Loader2 size={18} style={{ animation: "spin 1s linear infinite", color: "#94a3b8" }} />
          <span style={{ color: "#94a3b8", fontSize: 12, marginLeft: 8 }}>检查服务状态...</span>
        </div>
      )}

      <Card style={{ marginBottom: 16 }}>
        <CardContent style={{ padding: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx,.md,.txt,.markdown"
              onChange={handleUpload}
              style={{ display: "none" }}
            />
            <Button
              variant="outline"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading || !serviceRunning || kbReady !== true}
            >
              {uploading ? (
                <Loader2 size={14} style={{ animation: "spin 1s linear infinite", marginRight: 6 }} />
              ) : (
                <Upload size={14} style={{ marginRight: 6 }} />
              )}
              {uploading ? t("kb.uploading") : t("kb.upload")}
            </Button>
            <span style={{ color: "#64748b", fontSize: 12 }}>
              {t("kb.uploadHint")}
            </span>
          </div>
        </CardContent>
      </Card>

      <Card style={{ marginBottom: 16 }}>
        <CardContent style={{ padding: 20 }}>
          <div style={{ display: "flex", gap: 8 }}>
            <Input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder={kbReady === true ? t("kb.searchPlaceholder") : "嵌入模型未配置，无法搜索"}
              onKeyDown={e => { if (e.key === "Enter" && kbReady === true) handleSearch(); }}
              disabled={kbReady !== true}
              style={{ flex: 1 }}
            />
            <Button onClick={handleSearch} disabled={searching || !searchQuery.trim() || kbReady !== true}>
              {searching ? <Loader2 size={14} style={{ animation: "spin 1s linear infinite" }} /> : <Search size={14} />}
            </Button>
          </div>

          {searchResults.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <div style={{ fontSize: 13, color: "#64748b", marginBottom: 8 }}>
                {searchResults.length} 条结果
              </div>
              {searchResults.map((r, i) => (
                <Card key={i} style={{ marginBottom: 8 }}>
                  <CardContent style={{ padding: 12 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                      <Badge variant="secondary" style={{ fontSize: 11 }}>
                        {r.document_name}
                      </Badge>
                      <span style={{ fontSize: 11, color: "#64748b" }}>
                        {t("kb.score")}: {formatScore(r.score)}
                      </span>
                    </div>
                    <div style={{ fontSize: 13, lineHeight: 1.6, color: "#334155", maxHeight: 150, overflow: "auto" }}>
                      <ReactMarkdown>{r.content}</ReactMarkdown>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}

          {searchResults.length === 0 && searchQuery.trim() && !searching && (
            <div style={{ marginTop: 16, textAlign: "center", color: "#94a3b8", fontSize: 13 }}>
              {t("kb.noResults")}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent style={{ padding: 20 }}>
          {loading ? (
            <div style={{ textAlign: "center", padding: 40 }}>
              <Loader2 size={24} style={{ animation: "spin 1s linear infinite", color: "#94a3b8" }} />
            </div>
          ) : documents.length === 0 ? (
            <div style={{ textAlign: "center", padding: 40, color: "#94a3b8", fontSize: 13 }}>
              {t("kb.noDocuments")}
            </div>
          ) : (
            <>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid #e2e8f0" }}>
                    <th style={thStyle}>{t("kb.name")}</th>
                    <th style={thStyle}>{t("kb.type")}</th>
                    <th style={thStyle}>{t("kb.time")}</th>
                    <th style={thStyle}>{t("kb.chunks")}</th>
                    <th style={thStyle}>{t("kb.status")}</th>
                    <th style={thStyle}>{t("kb.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {documents.map(doc => {
                    const statusConfig = STATUS_CONFIG[doc.status] || STATUS_CONFIG.processing;
                    return (
                      <tr key={doc.id} style={{ borderBottom: "1px solid #f1f5f9" }}>
                        <td style={tdStyle}>
                          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                            <FileText size={14} style={{ color: "#94a3b8" }} />
                            <span style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {doc.name}
                            </span>
                          </div>
                        </td>
                        <td style={tdStyle}>
                          <Badge variant="outline" style={{ fontSize: 10 }}>{doc.file_type?.toUpperCase()}</Badge>
                        </td>
                        <td style={{ ...tdStyle, fontSize: 12, color: "#64748b" }}>{formatTime(doc.upload_time)}</td>
                        <td style={{ ...tdStyle, textAlign: "center" }}>{doc.total_chunks}</td>
                        <td style={tdStyle}>
                          <span style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: statusConfig.color }}>
                            {statusConfig.icon}
                            {t(statusConfig.labelKey)}
                            {doc.status === "processing" && (
                              <Loader2 size={12} style={{ animation: "spin 1s linear infinite", marginLeft: 2 }} />
                            )}
                          </span>
                          {doc.error_msg && (
                            <div style={{ fontSize: 10, color: "#ef4444", marginTop: 2, maxWidth: 150 }}>
                              {doc.error_msg.length > 60 ? doc.error_msg.slice(0, 60) + "..." : doc.error_msg}
                            </div>
                          )}
                        </td>
                        <td style={tdStyle}>
                          <div style={{ display: "flex", gap: 4 }}>
                            {doc.status === "ready" && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleViewChunks(doc)}
                                disabled={viewChunksLoading}
                              >
                                <Eye size={14} />
                              </Button>
                            )}
                            {(doc.status === "failed") && (
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleRepair(doc)}
                                title={t("kb.repair")}
                              >
                                <Wrench size={14} />
                              </Button>
                            )}
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setDeleteTarget(doc)}
                              style={{ color: "#ef4444" }}
                            >
                              <Trash2 size={14} />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>

              {totalPages > 1 && (
                <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 12, marginTop: 16 }}>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page === 0}
                    onClick={() => setPage(p => p - 1)}
                  >
                    <ChevronLeft size={14} />
                  </Button>
                  <span style={{ fontSize: 13, color: "#64748b" }}>
                    {page + 1} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={page >= totalPages - 1}
                    onClick={() => setPage(p => p + 1)}
                  >
                    <ChevronRight size={14} />
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {viewChunks && (
        <AlertDialog open onOpenChange={() => setViewChunks(null)}>
          <AlertDialogContent style={{ maxWidth: 700, maxHeight: "80vh" }}>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("kb.chunksTitle")} — {viewChunks.docName}</AlertDialogTitle>
            </AlertDialogHeader>
            <div style={{ overflow: "auto", maxHeight: "60vh" }}>
              {viewChunks.chunks.map((chunk, i) => (
                <Card key={chunk.id || i} style={{ marginBottom: 8 }}>
                  <CardContent style={{ padding: 12 }}>
                    <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>
                      #{chunk.chunk_index + 1} ~{chunk.token_count} tokens
                    </div>
                    <div style={{ fontSize: 13, lineHeight: 1.6 }}>
                      <ReactMarkdown>{chunk.content.length > 500 ? chunk.content.slice(0, 500) + "..." : chunk.content}</ReactMarkdown>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel onClick={() => setViewChunks(null)}>关闭</AlertDialogCancel>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}

      <AlertDialog open={deleteTarget !== null} onOpenChange={() => setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("kb.deleteTitle")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t("kb.deleteConfirm")}
              <br /><br />
              <strong>{deleteTarget?.name}</strong>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => setDeleteTarget(null)}>取消</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete} style={{ background: "#ef4444", color: "white" }}>
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
        </>
      )}
    </div>
  );
}

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "10px 8px",
  fontSize: 12,
  fontWeight: 600,
  color: "#64748b",
};

const tdStyle: React.CSSProperties = {
  padding: "10px 8px",
  fontSize: 13,
  verticalAlign: "middle",
};
