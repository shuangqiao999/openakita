/**
 * 活跃任务列表
 */
import React from "react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useTaskStore } from "../hooks/useTaskStore";
import type { TaskStatus } from "../types";

const STATUS_COLORS: Record<TaskStatus, string> = {
  pending: "bg-gray-400",
  running: "bg-blue-500",
  completed: "bg-green-500",
  failed: "bg-red-500",
  blocked: "bg-yellow-500",
  paused: "bg-orange-500",
};

const STATUS_LABELS: Record<TaskStatus, string> = {
  pending: "等待中",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  blocked: "阻塞",
  paused: "暂停",
};

export function TaskList() {
  const activeTasks = useTaskStore((state) => state.activeTasks);
  const selectedTaskId = useTaskStore((state) => state.selectedTaskId);
  const setSelectedTaskId = useTaskStore((state) => state.setSelectedTaskId);

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>任务ID</TableHead>
            <TableHead>任务名称</TableHead>
            <TableHead>类型</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>执行者</TableHead>
            <TableHead>耗时</TableHead>
            <TableHead>操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {activeTasks.map((task) => (
            <TableRow
              key={task.id}
              className={
                selectedTaskId === task.id ? "bg-primary/5" : undefined
              }
            >
              <TableCell className="font-mono text-sm">{task.id}</TableCell>
              <TableCell className="font-medium">{task.name}</TableCell>
              <TableCell>
                <Badge variant="secondary">{task.type}</Badge>
              </TableCell>
              <TableCell>
                <div className="flex items-center gap-2">
                  <div
                    className={`w-2 h-2 rounded-full ${STATUS_COLORS[task.status]}`}
                  />
                  <span>{STATUS_LABELS[task.status]}</span>
                </div>
              </TableCell>
              <TableCell>{task.assignedSoldierId || "-"}</TableCell>
              <TableCell>
                {task.elapsedTime ? formatDuration(task.elapsedTime) : "-"}
              </TableCell>
              <TableCell>
                <div className="flex gap-1">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => setSelectedTaskId(task.id)}
                  >
                    详情
                  </Button>
                  {task.status === "running" && (
                    <Button size="sm" variant="destructive">
                      终止
                    </Button>
                  )}
                  {task.status === "paused" && (
                    <Button size="sm" variant="secondary">
                      恢复
                    </Button>
                  )}
                  {task.status === "failed" && (
                    <Button size="sm" variant="secondary">
                      重试
                    </Button>
                  )}
                </div>
              </TableCell>
            </TableRow>
          ))}

          {activeTasks.length === 0 && (
            <TableRow>
              <TableCell colSpan={7} className="text-center py-8">
                <p className="text-muted-foreground">暂无活跃任务</p>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
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
