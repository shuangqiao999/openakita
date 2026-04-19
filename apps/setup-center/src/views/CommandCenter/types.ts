/**
 * 情报看板类型定义
 */

// 军人 Agent 状态
export type SoldierStatus = "idle" | "running" | "blocked" | "paused" | "crashed";

export interface SoldierAgent {
  id: string;
  name: string;
  status: SoldierStatus;
  currentTaskId?: string;
  currentTaskName?: string;
  progress?: number;
  stepsUsed?: number;
  maxSteps?: number;
  elapsedTime?: number;
}

// 任务状态
export type TaskStatus = "pending" | "running" | "completed" | "failed" | "blocked" | "paused";

export interface Task {
  id: string;
  name: string;
  type: string;
  status: TaskStatus;
  createdAt: string;
  startedAt?: string;
  elapsedTime?: number;
  assignedSoldierId?: string;
  currentStep?: string;
}

// 组件健康状态
export type ComponentHealthStatus = "healthy" | "warning" | "unhealthy" | "unknown";

export interface ComponentHealth {
  name: string;
  status: ComponentHealthStatus;
  message?: string;
  metrics?: Record<string, number>;
  lastCheckedAt: string;
}

// 信任等级
export type TrustLevel = 0 | 1 | 2 | 3 | 4;

export interface TrustScore {
  taskTypeId: string;
  taskTypeName: string;
  score: number;
  level: TrustLevel;
  successCount: number;
  totalCount: number;
  locked?: boolean;
  lockedLevel?: TrustLevel;
}

// WebSocket 事件类型
export type WsEventType =
  | "task_status_update"
  | "soldier_status_update"
  | "component_health_update"
  | "alert"
  | "task_queue_update";

export interface WsEvent {
  type: WsEventType;
  data: any;
  timestamp: string;
}

// 告警
export interface Alert {
  id: string;
  level: "info" | "warning" | "error" | "critical";
  message: string;
  component?: string;
  acknowledged: boolean;
  createdAt: string;
}

// 任务队列概览
export interface TaskQueueOverview {
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

// DAG 节点
export interface DagNode {
  id: string;
  name: string;
  status: TaskStatus;
  dependencies: string[];
}

export interface DagGraph {
  nodes: DagNode[];
}
