import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import ReactECharts from "echarts-for-react";
import ReactDiffViewer from "react-diff-viewer-continued";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { safeFetch } from "@/providers";

interface Props {
  serviceRunning: boolean;
  apiBaseUrl?: string;
}

type TabId = "dashboard" | "experiments" | "skills" | "patterns" | "prompts" | "approvals";

export function EvolutionView({ serviceRunning, apiBaseUrl = "" }: Props) {
  const { t } = useTranslation();
  const API = apiBaseUrl;
  const [tab, setTab] = useState<TabId>("dashboard");

  if (!serviceRunning) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
        <div className="mt-3 font-semibold">{t("evolution.title")}</div>
        <div className="mt-1 text-xs opacity-50">{t("evolution.serviceNotRunning")}</div>
      </div>
    );
  }

  const tabs: { id: TabId; label: string }[] = [
    { id: "dashboard", label: t("evolution.tabDashboard") },
    { id: "experiments", label: t("evolution.tabExperiments") },
    { id: "skills", label: t("evolution.tabSkills") },
    { id: "patterns", label: t("evolution.tabPatterns") },
    { id: "prompts", label: t("evolution.tabPrompts") },
    { id: "approvals", label: t("evolution.tabApprovals") },
  ];

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-6 py-5">
      <div className="flex items-center gap-2 border-b border-border pb-2">
        {tabs.map((item) => (
          <button
            key={item.id}
            className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
              tab === item.id
                ? "bg-primary text-primary-foreground font-medium"
                : "text-muted-foreground hover:bg-muted"
            }`}
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === "dashboard" && <DashboardTab api={API} />}
        {tab === "experiments" && <ExperimentsTab api={API} />}
        {tab === "skills" && <SkillsTab api={API} />}
        {tab === "patterns" && <PatternsTab api={API} />}
        {tab === "prompts" && <PromptsTab api={API} />}
        {tab === "approvals" && <ApprovalsTab api={API} />}
      </div>
    </div>
  );
}

function DashboardTab({ api }: { api: string }) {
  const { t } = useTranslation();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await safeFetch(`${api}/api/evolution/dashboard`);
      setData(await res.json());
    } catch { /* ignore */ } finally { setLoading(false); }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="text-center py-10 text-muted-foreground">{t("evolution.loading")}</div>;
  if (!data) return <div className="text-center py-10 text-muted-foreground">{t("evolution.noData")}</div>;

  const bm = data.baseline_metrics || {};
  const benchmarks = data.recent_benchmarks || [];
  const experiments = data.recent_experiments || [];

  const chartOption = {
    tooltip: { trigger: "axis" as const },
    legend: { data: [t("evolution.successRate"), t("evolution.avgTokens"), t("evolution.efficiency")], textStyle: { color: "var(--foreground)" } },
    xAxis: { type: "category" as const, data: benchmarks.map((_: any, i: number) => `#${benchmarks.length - i}`).reverse() },
    yAxis: [
      { type: "value" as const, name: "%", max: 100 },
      { type: "value" as const, name: "tokens", position: "right" as const },
    ],
    series: [
      { name: t("evolution.successRate"), type: "line", data: [...benchmarks].reverse().map((b: any) => ((b.metrics?.success_rate || 0) * 100).toFixed(1)), smooth: true },
      { name: t("evolution.efficiency"), type: "line", data: [...benchmarks].reverse().map((b: any) => (b.metrics?.efficiency_score || 0).toFixed(1)), smooth: true },
      { name: t("evolution.avgTokens"), type: "bar", yAxisIndex: 1, data: [...benchmarks].reverse().map((b: any) => Math.round(b.metrics?.avg_tokens || 0)), itemStyle: { opacity: 0.4 } },
    ],
    backgroundColor: "transparent",
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: t("evolution.healthScore"), value: data.health_score?.toFixed(1) || "—", color: "var(--foreground)" },
          { label: t("evolution.successRate"), value: `${((bm.success_rate || 0) * 100).toFixed(0)}%`, color: "#22c55e" },
          { label: t("evolution.avgTokens"), value: Math.round(bm.avg_tokens || 0).toLocaleString(), color: "var(--foreground)" },
          { label: t("evolution.avgTime"), value: `${(bm.avg_time || 0).toFixed(1)}s`, color: "var(--foreground)" },
        ].map((item, i) => (
          <Card key={i} className="shadow-sm">
            <CardContent className="p-4 text-center">
              <div className="text-2xl font-bold" style={{ color: item.color }}>{item.value}</div>
              <div className="text-xs text-muted-foreground mt-1">{item.label}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {benchmarks.length > 0 && (
        <Card className="shadow-sm">
          <CardContent className="p-4">
            <div className="text-sm font-medium mb-2">{t("evolution.benchmarkTrend")}</div>
            <ReactECharts option={chartOption} style={{ height: 280 }} />
          </CardContent>
        </Card>
      )}

      {experiments.length > 0 && (
        <Card className="shadow-sm">
          <CardContent className="p-4">
            <div className="text-sm font-medium mb-2">{t("evolution.recentExperiments")}</div>
            {experiments.map((exp: any, i: number) => (
              <div key={i} className="flex items-center gap-2 py-1.5 border-b border-border/50 last:border-0">
                <Badge variant={exp.action === "keep" ? "default" : "secondary"}>
                  {exp.action === "keep" ? t("evolution.kept") : exp.action === "discard" ? t("evolution.discarded") : t("evolution.error")}
                </Badge>
                <span className="text-sm flex-1 truncate">{exp.description || "—"}</span>
                {exp.delta?.success_rate != null && (
                  <span className={`text-xs ${exp.delta.success_rate > 0 ? "text-green-500" : "text-red-400"}`}>
                    {exp.delta.success_rate > 0 ? "+" : ""}{(exp.delta.success_rate * 100).toFixed(1)}%
                  </span>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {benchmarks.length === 0 && experiments.length === 0 && (
        <div className="text-center py-10 text-muted-foreground text-sm">{t("evolution.noEvolutionData")}</div>
      )}
    </div>
  );
}

function ExperimentsTab({ api }: { api: string }) {
  const { t } = useTranslation();
  const [experiments, setExperiments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("all");
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = filter !== "all" ? `?status=${filter}` : "";
      const res = await safeFetch(`${api}/api/evolution/experiments${params}`);
      const data = await res.json();
      setExperiments(data.experiments || []);
    } catch { /* ignore */ } finally { setLoading(false); }
  }, [api, filter]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        {["all", "keep", "discard", "error"].map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-2.5 py-1 text-xs rounded ${filter === f ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}>
            {f === "all" ? t("evolution.filterAll") : f === "keep" ? t("evolution.kept") : f === "discard" ? t("evolution.discarded") : t("evolution.error")}
          </button>
        ))}
        <Button variant="outline" size="sm" onClick={load} className="ml-auto">{t("evolution.refresh")}</Button>
      </div>
      {loading ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.loading")}</div>
      ) : experiments.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.noData")}</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[140px]">{t("evolution.time")}</TableHead>
              <TableHead>{t("evolution.description")}</TableHead>
              <TableHead className="w-[80px]">{t("evolution.status")}</TableHead>
              <TableHead className="w-[80px]">Δ</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {experiments.map((exp, i) => (
              <TableRow key={i} className="cursor-pointer" onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}>
                <TableCell className="text-xs">{exp._timestamp || "—"}</TableCell>
                <TableCell className="text-sm">{exp.description || "—"}</TableCell>
                <TableCell>
                  <Badge variant={exp.action === "keep" ? "default" : "secondary"}>
                    {exp.action === "keep" ? t("evolution.kept") : exp.action === "discard" ? t("evolution.discarded") : t("evolution.error")}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">
                  {exp.delta?.success_rate != null ? `${(exp.delta.success_rate * 100).toFixed(1)}%` : "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function SkillsTab({ api }: { api: string }) {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await safeFetch(`${api}/api/evolution/skills`);
      const data = await res.json();
      setSkills(data.skills || []);
    } catch { /* ignore */ } finally { setLoading(false); }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const toggleSkill = async (name: string, enabled: boolean) => {
    try {
      await safeFetch(`${api}/api/evolution/skills/${encodeURIComponent(name)}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      setSkills((prev) => prev.map((s) => s.name === name ? { ...s, enabled } : s));
    } catch (e: any) { toast.error(e.message); }
  };

  const doDelete = async () => {
    if (!deleteTarget) return;
    try {
      await safeFetch(`${api}/api/evolution/skills/${encodeURIComponent(deleteTarget)}`, { method: "DELETE" });
      setSkills((prev) => prev.filter((s) => s.name !== deleteTarget));
      toast.success(t("evolution.skillDeleted"));
    } catch (e: any) { toast.error(e.message); }
    setDeleteTarget(null);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">{t("evolution.autoSkillsHint")}</div>
        <Button variant="outline" size="sm" onClick={load}>{t("evolution.refresh")}</Button>
      </div>
      {loading ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.loading")}</div>
      ) : skills.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.noAutoSkills")}</div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {skills.map((s) => (
            <Card key={s.name} className="shadow-sm">
              <CardContent className="p-4 flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm">{s.name}</span>
                  <Switch checked={s.enabled} onCheckedChange={(v) => toggleSkill(s.name, v)} />
                </div>
                <div className="text-xs text-muted-foreground line-clamp-2">{s.description}</div>
                <div className="flex items-center justify-between mt-1">
                  <span className="text-xs opacity-50">{s.created_at || ""}</span>
                  <Button variant="ghost" size="sm" className="text-destructive text-xs h-6"
                    onClick={() => setDeleteTarget(s.name)}>
                    {t("evolution.delete")}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <AlertDialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("evolution.confirmDelete")}</AlertDialogTitle>
            <AlertDialogDescription>{t("evolution.confirmDeleteDesc", { name: deleteTarget })}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("evolution.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={doDelete}>{t("evolution.delete")}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function PatternsTab({ api }: { api: string }) {
  const { t } = useTranslation();
  const [patterns, setPatterns] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await safeFetch(`${api}/api/evolution/patterns`);
      const data = await res.json();
      setPatterns(data.patterns || []);
    } catch { /* ignore */ } finally { setLoading(false); }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const togglePattern = async (idx: number, enabled: boolean) => {
    try {
      await safeFetch(`${api}/api/evolution/patterns/${idx}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      setPatterns((prev) => prev.map((p) => p._index === idx ? { ...p, enabled } : p));
    } catch (e: any) { toast.error(e.message); }
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">{t("evolution.patternsHint")}</div>
        <Button variant="outline" size="sm" onClick={load}>{t("evolution.refresh")}</Button>
      </div>
      {loading ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.loading")}</div>
      ) : patterns.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.noPatterns")}</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[100px]">{t("evolution.category")}</TableHead>
              <TableHead>{t("evolution.pattern")}</TableHead>
              <TableHead className="w-[80px]">{t("evolution.confidence")}</TableHead>
              <TableHead className="w-[60px]">{t("evolution.evidence")}</TableHead>
              <TableHead className="w-[60px]">{t("evolution.inject")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {patterns.map((p) => (
              <TableRow key={p._index}>
                <TableCell><Badge variant="outline">{p.category}</Badge></TableCell>
                <TableCell className="text-sm">{p.pattern}</TableCell>
                <TableCell className="text-xs">{((p.confidence || 0) * 100).toFixed(0)}%</TableCell>
                <TableCell className="text-xs">{p.evidence_count || 0}</TableCell>
                <TableCell>
                  <Switch checked={p.enabled !== false} onCheckedChange={(v) => togglePattern(p._index, v)} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function PromptsTab({ api }: { api: string }) {
  const { t } = useTranslation();
  const [variants, setVariants] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [diffTarget, setDiffTarget] = useState<any>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await safeFetch(`${api}/api/evolution/prompts`);
      const data = await res.json();
      setVariants(data.variants || []);
    } catch { /* ignore */ } finally { setLoading(false); }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">{t("evolution.promptsHint")}</div>
        <Button variant="outline" size="sm" onClick={load}>{t("evolution.refresh")}</Button>
      </div>
      {loading ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.loading")}</div>
      ) : variants.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.noPrompts")}</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[140px]">{t("evolution.time")}</TableHead>
              <TableHead>{t("evolution.hypothesis")}</TableHead>
              <TableHead className="w-[100px]">{t("evolution.section")}</TableHead>
              <TableHead className="w-[80px]">{t("evolution.status")}</TableHead>
              <TableHead className="w-[80px]">{t("evolution.efficiency")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {variants.map((v, i) => (
              <TableRow key={i} className="cursor-pointer" onClick={() => setDiffTarget(diffTarget?._file === v._file ? null : v)}>
                <TableCell className="text-xs">{v._file?.split("_adopted")[0]?.split("_rejected")[0] || "—"}</TableCell>
                <TableCell className="text-sm truncate max-w-[300px]">{v.hypothesis || "—"}</TableCell>
                <TableCell className="text-xs">{v.section || "—"}</TableCell>
                <TableCell>
                  <Badge variant={v.adopted ? "default" : "secondary"}>
                    {v.adopted ? t("evolution.adopted") : t("evolution.rejected")}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">{v.metrics?.efficiency_score?.toFixed(1) || "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {diffTarget && (
        <Card className="shadow-sm mt-2">
          <CardContent className="p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium">{t("evolution.diffView")}: {diffTarget.section}</span>
              <Button variant="ghost" size="sm" onClick={() => setDiffTarget(null)}>✕</Button>
            </div>
            <div className="text-xs text-muted-foreground mb-2">{diffTarget.hypothesis}</div>
            <div className="border rounded overflow-hidden text-xs">
              <ReactDiffViewer
                oldValue={diffTarget.original || `[Original: ${diffTarget.original_length || "?"} chars]`}
                newValue={diffTarget.proposed || `[Proposed: ${diffTarget.proposed_length || "?"} chars]`}
                splitView={true}
                useDarkTheme={document.documentElement.classList.contains("dark")}
              />
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ApprovalsTab({ api }: { api: string }) {
  const { t } = useTranslation();
  const [approvals, setApprovals] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<string>("pending");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [rejectId, setRejectId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [pendingCount, setPendingCount] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = filter !== "all" ? `?status=${filter}` : "";
      const res = await safeFetch(`${api}/api/evolution/approvals${params}`);
      const data = await res.json();
      setApprovals(data.approvals || []);
      setPendingCount(data.pending_count || 0);
    } catch { /* ignore */ } finally { setLoading(false); }
  }, [api, filter]);

  useEffect(() => { load(); }, [load]);

  const doApprove = async (id: string) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      const res = await safeFetch(`${api}/api/evolution/approvals/${id}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "approve" }),
      });
      const data = await res.json();
      toast.success(data.message || t("evolution.approvalApproved"));
      load();
    } catch (e: any) { toast.error(e.message); } finally { setSubmitting(false); }
  };

  const doReject = async () => {
    if (!rejectId || submitting) return;
    setSubmitting(true);
    try {
      await safeFetch(`${api}/api/evolution/approvals/${rejectId}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "reject", reason: rejectReason }),
      });
      toast.success(t("evolution.approvalRejected"));
      setRejectId(null);
      setRejectReason("");
      load();
    } catch (e: any) { toast.error(e.message); } finally { setSubmitting(false); }
  };

  const riskColor = (level: string) => {
    if (level === "high") return "text-red-500";
    if (level === "medium") return "text-yellow-500";
    return "text-green-500";
  };

  const statusBadge = (status: string) => {
    if (status === "pending") return <Badge variant="outline" className="border-yellow-500 text-yellow-500">{t("evolution.pending")}</Badge>;
    if (status === "applied") return <Badge variant="default">{t("evolution.applied")}</Badge>;
    if (status === "approved") return <Badge variant="outline" className="border-blue-500 text-blue-500">{t("evolution.approvedNotApplied")}</Badge>;
    return <Badge variant="secondary">{t("evolution.rejected")}</Badge>;
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        {["pending", "all", "approved", "applied", "rejected"].map((f) => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-2.5 py-1 text-xs rounded ${filter === f ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}>
            {f === "pending" ? `${t("evolution.pending")} (${pendingCount})` : f === "all" ? t("evolution.filterAll") : f === "approved" ? t("evolution.approvedNotApplied") : f === "applied" ? t("evolution.applied") : t("evolution.rejected")}
          </button>
        ))}
        <Button variant="outline" size="sm" onClick={load} className="ml-auto">{t("evolution.refresh")}</Button>
      </div>

      {loading ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.loading")}</div>
      ) : approvals.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground">{t("evolution.noApprovals")}</div>
      ) : (
        <div className="flex flex-col gap-3">
          {approvals.map((a) => (
            <Card key={a.id} className="shadow-sm">
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      {statusBadge(a.status)}
                      <Badge variant="outline" className={riskColor(a.risk_level)}>
                        {a.risk_level === "high" ? t("evolution.riskHigh") : a.risk_level === "medium" ? t("evolution.riskMedium") : t("evolution.riskLow")}
                      </Badge>
                      <span className="text-xs text-muted-foreground">{a.source} / {a.agent_role}</span>
                    </div>
                    <div className="font-medium text-sm">{a.title || a.description?.slice(0, 80) || "—"}</div>
                    <div className="text-xs text-muted-foreground mt-1">
                      {t("evolution.targetFile")}: {a.target_file || "—"} · {a.created_at?.slice(0, 16) || ""}
                    </div>
                  </div>
                  {(a.status === "pending" || a.status === "approved") && (
                    <div className="flex gap-1.5 shrink-0">
                      <Button size="sm" variant="default" disabled={submitting} onClick={() => doApprove(a.id)}>
                        {t("evolution.approve")}
                      </Button>
                      <Button size="sm" variant="outline" className="text-destructive" disabled={submitting} onClick={() => { setRejectId(a.id); setRejectReason(""); }}>
                        {t("evolution.reject")}
                      </Button>
                    </div>
                  )}
                </div>

                {a.hypothesis && (
                  <div className="text-xs text-muted-foreground mt-2 bg-muted/50 rounded px-2 py-1">
                    {a.hypothesis}
                  </div>
                )}

                <button className="text-xs text-primary mt-2 underline" onClick={() => setExpandedId(expandedId === a.id ? null : a.id)}>
                  {expandedId === a.id ? t("evolution.collapse") : t("evolution.viewDiff")}
                </button>

                {expandedId === a.id && a.original_content && a.proposed_content && (
                  <div className="mt-2 border rounded overflow-hidden text-xs">
                    <ReactDiffViewer
                      oldValue={a.original_content}
                      newValue={a.proposed_content}
                      splitView={true}
                      useDarkTheme={document.documentElement.classList.contains("dark")}
                    />
                  </div>
                )}

                {a.reject_reason && (
                  <div className="text-xs text-destructive mt-2">
                    {t("evolution.rejectReason")}: {a.reject_reason}
                  </div>
                )}
                {a.apply_error && (
                  <div className="text-xs text-yellow-500 mt-2">
                    {a.apply_error}
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <AlertDialog open={!!rejectId} onOpenChange={() => setRejectId(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("evolution.rejectTitle")}</AlertDialogTitle>
            <AlertDialogDescription>{t("evolution.rejectDesc")}</AlertDialogDescription>
          </AlertDialogHeader>
          <textarea
            className="w-full border rounded p-2 text-sm min-h-[80px] bg-background"
            placeholder={t("evolution.rejectReasonPlaceholder")}
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
          />
          <AlertDialogFooter>
            <AlertDialogCancel>{t("evolution.cancel")}</AlertDialogCancel>
            <AlertDialogAction onClick={doReject} disabled={submitting} className="bg-destructive text-destructive-foreground">
              {t("evolution.reject")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
