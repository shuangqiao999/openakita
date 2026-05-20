import { useRef, useCallback, forwardRef, useImperativeHandle } from "react";
import type { ChatMessage, MdModules, ChatDisplayMode } from "../utils/chatTypes";
import { MessageBubble } from "./MessageBubble";
import { FlatMessageItem } from "./FlatMessageItem";
import { Virtuoso, VirtuosoHandle } from "react-virtuoso";

export interface MessageListHandle {
  scrollToIndex: (index: number, align?: "start" | "center" | "end") => void;
  scrollToBottom: (behavior?: "auto" | "smooth") => void;
  forceFollow: () => void;
  cancelFollow: () => void;
  isAtBottom: () => boolean;
  saveScrollPosition: () => void;
  restoreScrollPosition: () => void;
}

export interface MessageListProps {
  messages: ChatMessage[];
  displayMode: ChatDisplayMode;
  showChain: boolean;
  apiBaseUrl?: string;
  mdModules?: MdModules | null;
  isStreaming: boolean;
  searchHighlight?: string;
  conversationId?: string;
  httpApiBase?: () => string;
  onPlanStepAction?: (action: "skip" | "retry", stepIdx: number, description: string) => void;
  onAskAnswer?: (msgId: string, answer: string) => void;
  onRetry?: (msgId: string) => void;
  onEdit?: (msgId: string) => void;
  onRegenerate?: (msgId: string) => void;
  onRewind?: (msgId: string) => void;
  onFork?: (msgId: string) => void;
  onSaveMemory?: (msgId: string) => void;
  onSkipStep?: () => void;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
  onAtBottomChange?: (atBottom: boolean) => void;
  onLoadOlder?: () => void;
  hasMoreBefore?: boolean;
  loadingOlder?: boolean;
}

export const MessageList = forwardRef<MessageListHandle, MessageListProps>(function MessageList(
  {
    messages,
    displayMode,
    showChain,
    apiBaseUrl,
    mdModules,
    isStreaming,
    onAskAnswer,
    onRetry,
    onEdit,
    onRegenerate,
    onRewind,
    onFork,
    onSaveMemory,
    onSkipStep,
    onImagePreview,
    onAtBottomChange,
    conversationId,
    httpApiBase,
    onPlanStepAction,
    onLoadOlder,
    hasMoreBefore = false,
    loadingOlder = false,
  },
  ref,
) {
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const atBottomRef = useRef(true);
  const forceFollowRef = useRef(false);

  const scrollToBottom = useCallback((behavior: "auto" | "smooth" = "auto") => {
    virtuosoRef.current?.scrollToIndex({
      index: messages.length - 1,
      align: "end",
      behavior,
    });
  }, [messages.length]);

  useImperativeHandle(ref, () => ({
    scrollToIndex: (index: number, align: "start" | "center" | "end" = "center") => {
      virtuosoRef.current?.scrollToIndex({ index, align, behavior: "smooth" });
    },
    scrollToBottom,
    forceFollow: () => {
      forceFollowRef.current = true;
      virtuosoRef.current?.scrollToIndex({
        index: messages.length - 1,
        align: "end",
        behavior: "smooth",
      });
    },
    cancelFollow: () => {
      forceFollowRef.current = false;
    },
    isAtBottom: () => atBottomRef.current,
    saveScrollPosition: () => {},
    restoreScrollPosition: () => {},
  }), [scrollToBottom, messages.length]);

  const handleAtBottomStateChange = useCallback(
    (atBottom: boolean) => {
      atBottomRef.current = atBottom;
      onAtBottomChange?.(atBottom);
      if (!atBottom) {
        forceFollowRef.current = false;
      }
    },
    [onAtBottomChange],
  );

  const followOutput = useCallback(
    (isAtBottom: boolean) => {
      if (forceFollowRef.current) return "smooth" as const;
      if (isStreaming && isAtBottom) return "smooth" as const;
      return false;
    },
    [isStreaming],
  );

  const handleStartReached = useCallback(() => {
    if (hasMoreBefore && !loadingOlder) {
      onLoadOlder?.();
    }
  }, [hasMoreBefore, loadingOlder, onLoadOlder]);

  const computeItemKey = useCallback((_index: number, msg: ChatMessage) => msg.id, []);

  const itemContent = useCallback(
    (index: number, msg: ChatMessage) => {
      const isLast = index === messages.length - 1;
      const Component = displayMode === "flat" ? FlatMessageItem : MessageBubble;
      return (
        <div style={{ paddingTop: 4, paddingBottom: 4 }}>
          <Component
            msg={msg}
            isLast={isLast}
            apiBaseUrl={apiBaseUrl}
            showChain={showChain}
            mdModules={mdModules}
            onAskAnswer={onAskAnswer}
            onRetry={onRetry}
            onEdit={onEdit}
            onRegenerate={onRegenerate}
            onRewind={onRewind}
            onSkipStep={onSkipStep}
            onImagePreview={onImagePreview}
            conversationId={conversationId}
            httpApiBase={httpApiBase}
            onPlanStepAction={onPlanStepAction}
          />
        </div>
      );
    },
    [
      messages.length, displayMode, apiBaseUrl, showChain, mdModules,
      onAskAnswer, onRetry, onEdit, onRegenerate, onRewind, onSkipStep, onImagePreview,
      conversationId, httpApiBase, onPlanStepAction,
    ],
  );

  const Header = useCallback(() => {
    if (hasMoreBefore) {
      return (
        <div style={{ display: "flex", justifyContent: "center", padding: "12px 0 8px" }}>
          <button
            type="button"
            onClick={onLoadOlder}
            disabled={loadingOlder}
            style={{
              border: "1px solid var(--border)",
              background: "var(--surface)",
              color: "var(--text-muted)",
              borderRadius: 999,
              padding: "6px 14px",
              fontSize: 12,
              cursor: loadingOlder ? "default" : "pointer",
              opacity: loadingOlder ? 0.7 : 1,
            }}
          >
            {loadingOlder ? "正在加载更早消息..." : "加载更早消息"}
          </button>
        </div>
      );
    }
    return null;
  }, [hasMoreBefore, loadingOlder, onLoadOlder]);

  const Footer = useCallback(() => <div style={{ height: 24 }} />, []);

  if (messages.length === 0) return null;

  return (
    <Virtuoso
      ref={virtuosoRef}
      data={messages}
      computeItemKey={computeItemKey}
      itemContent={itemContent}
      followOutput={followOutput}
      atBottomStateChange={handleAtBottomStateChange}
      startReached={handleStartReached}
      components={{ Header, Footer }}
      style={{ flex: 1, minHeight: 0 }}
      increaseViewportBy={{ top: 400, bottom: 400 }}
    />
  );
});
