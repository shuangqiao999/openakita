# 情报看板集成指南

## 概述

情报看板是作战指挥室架构的前端界面，为指挥官提供全局任务监控、系统健康检查、人工介入控制三大核心能力。

## 文件结构

```
src/views/CommandCenter/
├── index.tsx              # 主页面
├── types.ts               # TypeScript 类型定义
├── hooks/
│   ├── useTaskStore.ts    # 任务状态管理
│   ├── useHealthStore.ts  # 健康状态管理
│   ├── useSoldierStore.ts # 军人状态管理
│   └── useWebSocket.ts    # WebSocket 连接管理
└── components/
    ├── TaskOverview.tsx   # 任务队列概览卡片
    ├── SoldierPanel.tsx   # 军人 Agent 状态面板
    ├── TaskList.tsx       # 活跃任务列表
    └── HealthDashboard.tsx # 系统健康仪表盘
```

## 集成步骤

### 1. 添加路由

在 `App.tsx` 中添加情报看板路由：

```tsx
// 1. 在 lazy-loaded views 部分添加
const CommandCenterView = lazy(() => import("./views/CommandCenter").then(m => ({ default: m.CommandCenterView })));

// 2. 在 _HASH_TO_VIEW 中添加路由映射
const _HASH_TO_VIEW: Record<string, ViewId> = {
  // ... 现有路由
  "command-center": "command_center",
};

// 3. 在 _VIEW_TO_HASH 中添加反向映射
const _VIEW_TO_HASH: Record<string, string> = Object.fromEntries(
  Object.entries(_HASH_TO_VIEW).map(([k, v]) => [v, k]),
);
```

### 2. 在侧边栏添加菜单

在 `components/Sidebar.tsx` 中添加情报看板菜单项：

```tsx
// 添加菜单项
{
  id: "command_center",
  label: "情报看板",
  icon: LayoutDashboard,
}
```

### 3. 配置后端 API

确保后端提供以下 API 端点：

```typescript
// 状态查询接口
GET /api/commander/status
GET /api/dispatcher/queue
GET /api/soldiers/list
GET /api/tasks/active
GET /api/tasks/history?limit=50
GET /api/memory/stats
GET /api/llm/status
GET /api/trust/scores

// 操作接口
POST /api/tasks/{task_id}/pause
POST /api/tasks/{task_id}/resume
POST /api/tasks/{task_id}/cancel
POST /api/tasks/{task_id}/retry
POST /api/tasks/reorder
POST /api/soldiers/{soldier_id}/restart
POST /api/llm/switch
POST /api/commander/failover
POST /api/trust/update

// WebSocket 端点
WS /ws/commander/events
```

### 4. 配置 WebSocket

WebSocket 端点应该推送以下事件类型：

```typescript
type WsEventType =
  | "task_status_update"
  | "soldier_status_update"
  | "component_health_update"
  | "alert"
  | "task_queue_update";

interface WsEvent {
  type: WsEventType;
  data: any;
  timestamp: string;
}
```

## 数据格式

### 任务队列概览

```typescript
interface TaskQueueOverview {
  pending: number;
  running: number;
  completed: {
    today: number;
    week: number;
    total: number;
  };
  failed: {
    today: number;
    week: number;
    total: number;
  };
}
```

### 军人 Agent

```typescript
interface SoldierAgent {
  id: string;
  name: string;
  status: "idle" | "running" | "blocked" | "paused" | "crashed";
  currentTaskId?: string;
  currentTaskName?: string;
  progress?: number;
  stepsUsed?: number;
  maxSteps?: number;
  elapsedTime?: number;
}
```

### 任务

```typescript
interface Task {
  id: string;
  name: string;
  type: string;
  status: "pending" | "running" | "completed" | "failed" | "blocked" | "paused";
  createdAt: string;
  startedAt?: string;
  elapsedTime?: number;
  assignedSoldierId?: string;
  currentStep?: string;
}
```

### 组件健康状态

```typescript
interface ComponentHealth {
  name: string;
  status: "healthy" | "warning" | "unhealthy" | "unknown";
  message?: string;
  metrics?: Record<string, number>;
  lastCheckedAt: string;
}
```

## 自定义配置

### 修改 WebSocket URL

在 `hooks/useWebSocket.ts` 中修改默认 URL：

```typescript
const url = "ws://your-backend:port/ws/commander/events";
```

### 修改主题颜色

在各个组件中修改颜色常量，或使用 CSS 变量：

```css
:root {
  --status-healthy: #10b981;
  --status-warning: #f59e0b;
  --status-unhealthy: #ef4444;
  --status-running: #3b82f6;
  --status-pending: #9ca3af;
}
```

## 测试

### 运行开发服务器

```bash
cd apps/setup-center
npm run dev
```

### 访问情报看板

启动后，在侧边栏点击"情报看板"或访问 `#/command-center`。

## 待实现功能

### 高优先级
- [ ] DAG 依赖关系图
- [ ] 人工介入控制台
- [ ] 信任度配置面板
- [ ] 策略配置面板
- [ ] 告警通知系统
- [ ] 完整的操作按钮（暂停/恢复/终止/重试）

### 中优先级
- [ ] 任务历史记录
- [ ] 性能监控图表
- [ ] 任务筛选和搜索
- [ ] 拖拽调整面板大小
- [ ] 面板折叠/展开

### 低优先级
- [ ] 深色/浅色主题适配
- [ ] 国际化（i18n）
- [ ] 快捷键支持
- [ ] 导出报告功能

## 注意事项

1. **WebSocket 重连**：已实现自动重连机制，断开后 3 秒自动重试
2. **状态同步**：建议首次加载时通过 REST API 获取全量状态，后续通过 WebSocket 接收增量更新
3. **性能优化**：大量任务时考虑虚拟滚动
4. **错误处理**：所有 API 调用都应有错误处理和用户提示
5. **Mock 数据**：当前使用模拟数据用于演示，集成时请替换为真实 API 调用
