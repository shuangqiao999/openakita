/**
 * 任务状态管理
 */
import { create } from "zustand";
import type { Task, TaskQueueOverview, DagGraph } from "../types";

interface TaskStore {
  // 任务队列概览
  queueOverview: TaskQueueOverview | null;
  setQueueOverview: (overview: TaskQueueOverview) => void;

  // 活跃任务列表
  activeTasks: Task[];
  setActiveTasks: (tasks: Task[]) => void;
  updateTask: (taskId: string, updates: Partial<Task>) => void;

  // DAG 图
  dagGraph: DagGraph | null;
  setDagGraph: (graph: DagGraph) => void;

  // 选中的任务
  selectedTaskId: string | null;
  setSelectedTaskId: (taskId: string | null) => void;

  // 加载状态
  loading: boolean;
  setLoading: (loading: boolean) => void;
}

export const useTaskStore = create<TaskStore>((set) => ({
  queueOverview: null,
  setQueueOverview: (overview) => set({ queueOverview: overview }),

  activeTasks: [],
  setActiveTasks: (tasks) => set({ activeTasks: tasks }),
  updateTask: (taskId, updates) =>
    set((state) => ({
      activeTasks: state.activeTasks.map((t) =>
        t.id === taskId ? { ...t, ...updates } : t
      ),
    })),

  dagGraph: null,
  setDagGraph: (graph) => set({ dagGraph: graph }),

  selectedTaskId: null,
  setSelectedTaskId: (taskId) => set({ selectedTaskId: taskId }),

  loading: false,
  setLoading: (loading) => set({ loading }),
}));
