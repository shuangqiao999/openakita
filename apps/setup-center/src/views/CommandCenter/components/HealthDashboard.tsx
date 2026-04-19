/**
 * 健康状态仪表盘
 */
import React, { useEffect } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useHealthStore } from "../hooks/useHealthStore";
import type { ComponentHealthStatus } from "../types";

const STATUS_BG_COLORS: Record<ComponentHealthStatus, string> = {
  healthy: "border-green-500/50 bg-green-500/5",
  warning: "border-yellow-500/50 bg-yellow-500/5",
  unhealthy: "border-red-500/50 bg-red-500/5",
  unknown: "border-gray-500/50 bg-gray-500/5",
};

const STATUS_TEXT_COLORS: Record<ComponentHealthStatus, string> = {
  healthy: "text-green-600",
  warning: "text-yellow-600",
  unhealthy: "text-red-600",
  unknown: "text-gray-600",
};

const STATUS_LABELS: Record<ComponentHealthStatus, string> = {
  healthy: "正常",
  warning: "告警",
  unhealthy: "故障",
  unknown: "未知",
};

export function HealthDashboard() {
  const components = useHealthStore((state) => state.components);
  const overallStatus = useHealthStore((state) => state.overallStatus);
  const computeOverallStatus = useHealthStore(
    (state) => state.computeOverallStatus
  );

  useEffect(() => {
    computeOverallStatus();
  }, [components, computeOverallStatus]);

  return (
    <div>
      {/* 整体状态 */}
      <Card className={`mb-4 ${STATUS_BG_COLORS[overallStatus]}`}>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-muted-foreground">系统整体健康状态</p>
              <p className={`text-2xl font-bold ${STATUS_TEXT_COLORS[overallStatus]}`}>
                {STATUS_LABELS[overallStatus]}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* 各组件状态 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Object.entries(components).map(([name, health]) => (
          <HealthCard key={name} name={name} health={health} />
        ))}
      </div>

      {Object.keys(components).length === 0 && (
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground text-center">暂无健康状态数据</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

interface HealthCardProps {
  name: string;
  health: any;
}

function HealthCard({ name, health }: HealthCardProps) {
  const displayName = getComponentDisplayName(name);

  return (
    <Card className={STATUS_BG_COLORS[health.status]}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{displayName}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-center justify-between">
          <span className={`text-lg font-bold ${STATUS_TEXT_COLORS[health.status]}`}>
            {STATUS_LABELS[health.status]}
          </span>
        </div>

        {health.message && (
          <p className="text-sm text-muted-foreground mt-2">{health.message}</p>
        )}

        {health.metrics && Object.keys(health.metrics).length > 0 && (
          <div className="mt-3 space-y-1">
            {Object.entries(health.metrics).map(([key, value]) => (
              <div key={key} className="flex justify-between text-sm">
                <span className="text-muted-foreground">{key}</span>
                <span className="font-medium">{value}</span>
              </div>
            ))}
          </div>
        )}

        {health.lastCheckedAt && (
          <p className="text-xs text-muted-foreground mt-3">
            最后检查: {new Date(health.lastCheckedAt).toLocaleTimeString()}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function getComponentDisplayName(name: string): string {
  const names: Record<string, string> = {
    commander: "指挥官",
    dispatcher: "调度台",
    memory: "记忆系统",
    llm: "LLM 连接",
    soldier_pool: "军人池",
  };
  return names[name] || name;
}
