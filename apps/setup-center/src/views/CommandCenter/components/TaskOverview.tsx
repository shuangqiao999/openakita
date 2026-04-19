/**
 * 任务队列概览卡片
 */
import React from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useTaskStore } from "../hooks/useTaskStore";

export function TaskOverview() {
  const queueOverview = useTaskStore((state) => state.queueOverview);

  if (!queueOverview) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>任务队列概览</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground">加载中...</p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <StatCard
        title="等待中"
        value={queueOverview.pending}
        color="text-yellow-500"
        bgColor="bg-yellow-500/10"
      />
      <StatCard
        title="执行中"
        value={queueOverview.running}
        color="text-blue-500"
        bgColor="bg-blue-500/10"
      />
      <StatCard
        title="今日完成"
        value={queueOverview.completed.today}
        color="text-green-500"
        bgColor="bg-green-500/10"
        subValue={`本周 ${queueOverview.completed.week} / 总计 ${queueOverview.completed.total}`}
      />
      <StatCard
        title="今日失败"
        value={queueOverview.failed.today}
        color="text-red-500"
        bgColor="bg-red-500/10"
        subValue={`本周 ${queueOverview.failed.week} / 总计 ${queueOverview.failed.total}`}
      />
    </div>
  );
}

interface StatCardProps {
  title: string;
  value: number;
  color: string;
  bgColor: string;
  subValue?: string;
}

function StatCard({ title, value, color, bgColor, subValue }: StatCardProps) {
  return (
    <Card className={`${bgColor} border-transparent`}>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className={`text-2xl font-bold ${color}`}>{value}</div>
        {subValue && (
          <p className="text-xs text-muted-foreground mt-1">{subValue}</p>
        )}
      </CardContent>
    </Card>
  );
}
