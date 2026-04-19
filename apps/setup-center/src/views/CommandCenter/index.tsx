/**
 * 情报看板主页面
 */
import React, { useEffect } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AlertTriangle, CheckCircle2, LayoutDashboard, Activity, Users } from "lucide-react";
import { TaskOverview } from "./components/TaskOverview";
import { SoldierPanel } from "./components/SoldierPanel";
import { TaskList } from "./components/TaskList";
import { HealthDashboard } from "./components/HealthDashboard";
import { useWebSocket } from "./hooks/useWebSocket";
import { useTaskStore } from "./hooks/useTaskStore";
import { useSoldierStore } from "./hooks/useSoldierStore";
import { useHealthStore } from "./hooks/useHealthStore";

export function CommandCenterView() {
  // WebSocket 连接
  const { connected } = useWebSocket({ enabled: true });

  // 模拟数据加载（实际项目中应该通过 API 或 WebSocket 加载）
  const setQueueOverview = useTaskStore((state) => state.setQueueOverview);
  const setActiveTasks = useTaskStore((state) => state.setActiveTasks);
  const setSoldiers = useSoldierStore((state) => state.setSoldiers);
  const setComponents = useHealthStore((state) => state.setComponents);

  useEffect(() => {
    // 模拟初始数据加载
    loadMockData();
  }, []);

  const loadMockData = () => {
    // 任务队列概览
    setQueueOverview({
      pending: 5,
      running: 3,
      completed: { today: 12, week: 87, total: 1247 },
      failed: { today: 1, week: 5, total: 42 },
    });

    // 活跃任务
    setActiveTasks([
      {
        id: "task_001",
        name: "编写项目文档",
        type: "documentation",
        status: "running",
        createdAt: new Date(Date.now() - 3600000).toISOString(),
        startedAt: new Date(Date.now() - 1800000).toISOString(),
        elapsedTime: 1800,
        assignedSoldierId: "soldier_1",
        currentStep: "正在生成目录结构",
      },
      {
        id: "task_002",
        name: "代码审查",
        type: "code_review",
        status: "running",
        createdAt: new Date(Date.now() - 7200000).toISOString(),
        startedAt: new Date(Date.now() - 3600000).toISOString(),
        elapsedTime: 3600,
        assignedSoldierId: "soldier_2",
        currentStep: "分析 PR #42",
      },
      {
        id: "task_003",
        name: "测试新功能",
        type: "testing",
        status: "pending",
        createdAt: new Date(Date.now() - 1800000).toISOString(),
      },
    ]);

    // 军人 Agent
    setSoldiers([
      {
        id: "soldier_1",
        name: "军人一号",
        status: "running",
        currentTaskId: "task_001",
        currentTaskName: "编写项目文档",
        progress: 65,
        stepsUsed: 7,
        maxSteps: 10,
        elapsedTime: 1800,
      },
      {
        id: "soldier_2",
        name: "军人二号",
        status: "running",
        currentTaskId: "task_002",
        currentTaskName: "代码审查",
        progress: 40,
        stepsUsed: 4,
        maxSteps: 10,
        elapsedTime: 3600,
      },
      {
        id: "soldier_3",
        name: "军人三号",
        status: "idle",
      },
    ]);

    // 组件健康状态
    setComponents({
      commander: {
        name: "commander",
        status: "healthy",
        message: "运行正常",
        metrics: {
          运行时长: "2h 34m",
          心跳状态: "正常",
        },
        lastCheckedAt: new Date().toISOString(),
      },
      dispatcher: {
        name: "dispatcher",
        status: "healthy",
        message: "运行正常",
        metrics: {
          队列长度: 5,
          平均等待时间: "12s",
        },
        lastCheckedAt: new Date().toISOString(),
      },
      memory: {
        name: "memory",
        status: "warning",
        message: "查询延迟略高",
        metrics: {
          记忆条目: 1247,
          查询延迟: "420ms",
        },
        lastCheckedAt: new Date().toISOString(),
      },
      llm: {
        name: "llm",
        status: "healthy",
        message: "运行正常",
        metrics: {
          当前提供商: "Anthropic",
          今日调用: 247,
          错误率: "0.2%",
        },
        lastCheckedAt: new Date().toISOString(),
      },
    });
  };

  return (
    <div className="flex flex-col h-full">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between border-b px-4 py-2">
        <div className="flex items-center gap-2">
          <LayoutDashboard className="h-5 w-5" />
          <h1 className="text-lg font-semibold">情报看板</h1>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            {connected ? (
              <CheckCircle2 className="h-4 w-4 text-green-500" />
            ) : (
              <AlertTriangle className="h-4 w-4 text-yellow-500" />
            )}
            <span className="text-sm text-muted-foreground">
              {connected ? "已连接" : "断开连接"}
            </span>
          </div>
        </div>
      </div>

      {/* 主要内容 */}
      <div className="flex-1 overflow-auto p-4">
        {/* 任务队列概览 */}
        <div className="mb-6">
          <TaskOverview />
        </div>

        {/* Tabs */}
        <Tabs defaultValue="monitoring">
          <TabsList>
            <TabsTrigger value="monitoring">
              <Activity className="h-4 w-4 mr-2" />
              任务监控
            </TabsTrigger>
            <TabsTrigger value="health">
              <CheckCircle2 className="h-4 w-4 mr-2" />
              系统健康
            </TabsTrigger>
            <TabsTrigger value="soldiers">
              <Users className="h-4 w-4 mr-2" />
              军人管理
            </TabsTrigger>
          </TabsList>

          <TabsContent value="monitoring" className="mt-4">
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
              {/* 左侧军人面板 */}
              <div className="lg:col-span-1">
                <SoldierPanel />
              </div>

              {/* 右侧任务列表 */}
              <div className="lg:col-span-3">
                <Card>
                  <CardContent className="pt-6">
                    <TaskList />
                  </CardContent>
                </Card>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="health" className="mt-4">
            <HealthDashboard />
          </TabsContent>

          <TabsContent value="soldiers" className="mt-4">
            <Card>
              <CardContent className="pt-6">
                <p className="text-muted-foreground">军人管理界面待实现</p>
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
