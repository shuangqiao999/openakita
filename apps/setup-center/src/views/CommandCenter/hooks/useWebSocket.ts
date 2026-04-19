/**
 * WebSocket 连接管理
 */
import { useEffect, useRef, useCallback } from "react";
import { useTaskStore } from "./useTaskStore";
import { useHealthStore } from "./useHealthStore";
import { useSoldierStore } from "./useSoldierStore";
import type { WsEvent } from "../types";

interface UseWebSocketOptions {
  enabled?: boolean;
  url?: string;
}

export function useWebSocket(options: UseWebSocketOptions = {}) {
  const { enabled = true, url = "ws://127.0.0.1:18900/ws/commander/events" } = options;

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setQueueOverview = useTaskStore((state) => state.setQueueOverview);
  const setActiveTasks = useTaskStore((state) => state.setActiveTasks);
  const updateTask = useTaskStore((state) => state.updateTask);

  const setComponents = useHealthStore((state) => state.setComponents);
  const updateComponent = useHealthStore((state) => state.updateComponent);
  const addAlert = useHealthStore((state) => state.addAlert);

  const setSoldiers = useSoldierStore((state) => state.setSoldiers);
  const updateSoldier = useSoldierStore((state) => state.updateSoldier);

  const connect = useCallback(() => {
    if (!enabled) return;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("[CommandCenter] WebSocket connected");
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current);
          reconnectTimeoutRef.current = null;
        }
      };

      ws.onmessage = (event) => {
        try {
          const data: WsEvent = JSON.parse(event.data);
          handleWsEvent(data);
        } catch (e) {
          console.error("[CommandCenter] Failed to parse WebSocket message", e);
        }
      };

      ws.onclose = () => {
        console.log("[CommandCenter] WebSocket disconnected, reconnecting...");
        scheduleReconnect();
      };

      ws.onerror = (error) => {
        console.error("[CommandCenter] WebSocket error", error);
      };
    } catch (e) {
      console.error("[CommandCenter] Failed to connect WebSocket", e);
      scheduleReconnect();
    }
  }, [enabled, url]);

  const scheduleReconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    reconnectTimeoutRef.current = setTimeout(connect, 3000);
  }, [connect]);

  const handleWsEvent = useCallback(
    (event: WsEvent) => {
      switch (event.type) {
        case "task_queue_update":
          setQueueOverview(event.data);
          break;
        case "task_status_update":
          if (Array.isArray(event.data)) {
            setActiveTasks(event.data);
          } else {
            updateTask(event.data.id, event.data);
          }
          break;
        case "soldier_status_update":
          if (Array.isArray(event.data)) {
            setSoldiers(event.data);
          } else {
            updateSoldier(event.data.id, event.data);
          }
          break;
        case "component_health_update":
          if (Array.isArray(event.data)) {
            const components: Record<string, any> = {};
            event.data.forEach((c: any) => {
              components[c.name] = c;
            });
            setComponents(components);
          } else {
            updateComponent(event.data.name, event.data);
          }
          break;
        case "alert":
          addAlert(event.data);
          break;
        default:
          console.log("[CommandCenter] Unknown WebSocket event type", event.type);
      }
    },
    [
      setQueueOverview,
      setActiveTasks,
      updateTask,
      setComponents,
      updateComponent,
      addAlert,
      setSoldiers,
      updateSoldier,
    ]
  );

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (enabled) {
      connect();
    }
    return disconnect;
  }, [enabled, connect, disconnect]);

  return {
    connected: wsRef.current?.readyState === WebSocket.OPEN,
    reconnect: connect,
    disconnect,
  };
}
