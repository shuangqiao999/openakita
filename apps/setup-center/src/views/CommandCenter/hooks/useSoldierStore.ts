/**
 * 军人 Agent 状态管理
 */
import { create } from "zustand";
import type { SoldierAgent } from "../types";

interface SoldierStore {
  soldiers: SoldierAgent[];
  setSoldiers: (soldiers: SoldierAgent[]) => void;
  updateSoldier: (soldierId: string, updates: Partial<SoldierAgent>) => void;

  selectedSoldierId: string | null;
  setSelectedSoldierId: (soldierId: string | null) => void;
}

export const useSoldierStore = create<SoldierStore>((set) => ({
  soldiers: [],
  setSoldiers: (soldiers) => set({ soldiers }),
  updateSoldier: (soldierId, updates) =>
    set((state) => ({
      soldiers: state.soldiers.map((s) =>
        s.id === soldierId ? { ...s, ...updates } : s
      ),
    })),

  selectedSoldierId: null,
  setSelectedSoldierId: (soldierId) => set({ selectedSoldierId: soldierId }),
}));
