/**
 * 军人 Agent 状态面板
 */
import React from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useSoldierStore } from "../hooks/useSoldierStore";
import type { SoldierStatus } from "../types";

const STATUS_COLORS: Record<SoldierStatus, string> = {
  idle: "bg-gray-400",
  running: "bg-blue-500 animate-pulse",
  blocked: "bg-yellow-500",
  paused: "bg-orange-500",
  crashed: "bg-red-500",
};

const STATUS_LABELS: Record<SoldierStatus, string> = {
  idle: "空闲",
  running: "执行中",
  blocked: "阻塞",
  paused: "暂停",
  crashed: "已崩溃",
};

export function SoldierPanel() {
  const soldiers = useSoldierStore((state) => state.soldiers);
  const selectedSoldierId = useSoldierStore((state) => state.selectedSoldierId);
  const setSelectedSoldierId = useSoldierStore(
    (state) => state.setSelectedSoldierId
  );

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>军人 Agent</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {soldiers.map((soldier) => (
          <div
            key={soldier.id}
            className={`p-3 rounded-lg border cursor-pointer transition-colors ${
              selectedSoldierId === soldier.id
                ? "border-primary bg-primary/5"
                : "border-border hover:border-primary/50"
            }`}
            onClick={() => setSelectedSoldierId(soldier.id)}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div
                  className={`w-3 h-3 rounded-full ${STATUS_COLORS[soldier.status]}`}
                />
                <span className="font-medium">{soldier.name}</span>
              </div>
              <span className="text-xs text-muted-foreground">
                {STATUS_LABELS[soldier.status]}
              </span>
            </div>

            {soldier.currentTaskName && (
              <div className="mt-2">
                <p className="text-sm text-muted-foreground truncate">
                  {soldier.currentTaskName}
                </p>
                <div className="flex items-center gap-2 mt-1">
                  <div className="flex-1 h-1.5 bg-secondary rounded-full overflow-hidden">
                    <div
                      className="h-full bg-primary transition-all duration-300"
                      style={{ width: `${soldier.progress || 0}%` }}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {soldier.stepsUsed || 0}/{soldier.maxSteps || 10}
                  </span>
                </div>
                {soldier.elapsedTime && (
                  <p className="text-xs text-muted-foreground mt-1">
                    已耗时: {formatDuration(soldier.elapsedTime)}
                  </p>
                )}
              </div>
            )}

            {selectedSoldierId === soldier.id && (
              <div className="flex gap-2 mt-3">
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={soldier.status !== "running"}
                >
                  暂停
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={soldier.status !== "paused"}
                >
                  恢复
                </Button>
                <Button size="sm" variant="destructive">
                  终止
                </Button>
              </div>
            )}
          </div>
        ))}

        {soldiers.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-4">
            暂无军人 Agent
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (mins > 0) {
    return `${mins}分${secs}秒`;
  }
  return `${secs}秒`;
}
