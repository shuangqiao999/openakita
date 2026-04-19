/**
 * 健康状态管理
 */
import { create } from "zustand";
import type { ComponentHealth, Alert } from "../types";

interface HealthStore {
  // 组件健康状态
  components: Record<string, ComponentHealth>;
  setComponents: (components: Record<string, ComponentHealth>) => void;
  updateComponent: (name: string, updates: Partial<ComponentHealth>) => void;

  // 告警列表
  alerts: Alert[];
  setAlerts: (alerts: Alert[]) => void;
  addAlert: (alert: Alert) => void;
  acknowledgeAlert: (alertId: string) => void;

  // 整体健康状态
  overallStatus: "healthy" | "warning" | "unhealthy" | "unknown";
  computeOverallStatus: () => void;
}

export const useHealthStore = create<HealthStore>((set, get) => ({
  components: {},
  setComponents: (components) => set({ components }),
  updateComponent: (name, updates) =>
    set((state) => ({
      components: {
        ...state.components,
        [name]: { ...state.components[name], ...updates },
      },
    })),

  alerts: [],
  setAlerts: (alerts) => set({ alerts }),
  addAlert: (alert) =>
    set((state) => ({ alerts: [alert, ...state.alerts] })),
  acknowledgeAlert: (alertId) =>
    set((state) => ({
      alerts: state.alerts.map((a) =>
        a.id === alertId ? { ...a, acknowledged: true } : a
      ),
    })),

  overallStatus: "unknown" as const,
  computeOverallStatus: () => {
    const { components } = get();
    const statuses = Object.values(components).map((c) => c.status);

    if (statuses.length === 0) {
      set({ overallStatus: "unknown" });
      return;
    }

    if (statuses.some((s) => s === "unhealthy")) {
      set({ overallStatus: "unhealthy" });
    } else if (statuses.some((s) => s === "warning")) {
      set({ overallStatus: "warning" });
    } else {
      set({ overallStatus: "healthy" });
    }
  },
}));
