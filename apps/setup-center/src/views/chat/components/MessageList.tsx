import { useRef, useCallback, forwardRef, useImperativeHandle } from "react";
import { Virtuoso, VirtuosoHandle } from "react-virtuoso";
import type { ChatMessage, MdModules, ChatDisplayMode } from "../utils/chatTypes";
import { MessageBubble } from "./MessageBubble";
import { FlatMessageItem } from "./FlatMessageItem";

export interface MessageListHandle {
  scrollToIndex: (index: number, align?: "start" | "center" | "end") => void;
  scrollToBottom: (behavior?: "auto" | "smooth") => void;
  /** Force followOutput to return true on the next new-item append, even if user scrolled up. */
  forceFollow: () => void;
}

export interface MessageListProps {
  messages: ChatMessage[];
  displayMode: ChatDisplayMode;
  showChain: boolean;
  apiBaseUrl?: string;
  mdModules?: MdModules | null;
  isStreaming: boolean;
  onAskAnswer?: (msgId: string, answer: string) => void;
  onRetry?: (msgId: string) => void;
  onEdit?: (msgId: string) => void;
  onRegenerate?: (msgId: string) => void;
  onRewind?: (msgId: string) => void;
  onFork?: (msgId: string) => void;
  onSaveMemory?: (msgId: string) => void;
  onSkipStep?: () => void;
  onImagePreview?: (displayUrl: string, downloadUrl: string, name: string) => void;
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
  },
  ref,
) {
  const virtuosoRef = useRef<VirtuosoHandle>(null);
  const forceFollowRef = useRef(false);

  useImperativeHandle(ref, () => ({
    scrollToIndex: (index: number, align: "start" | "center" | "end" = "center") => {
      virtuosoRef.current?.scrollToIndex({ index, align, behavior: "smooth" });
    },
    scrollToBottom: (behavior: "auto" | "smooth" = "smooth") => {
      virtuosoRef.current?.scrollToIndex({ index: "LAST", align: "end", behavior });
    },
    forceFollow: () => { forceFollowRef.current = true; },
  }), []);

  const followOutput = useCallback((isAtBottom: boolean) => {
    if (forceFollowRef.current) {
      forceFollowRef.current = false;
      return "auto";
    }
    if (isAtBottom) return isStreaming ? "auto" : "smooth";
    return false;
  }, [isStreaming]);

  const itemContent = useCallback((index: number, msg: ChatMessage) => {
    const isLast = index === messages.length - 1;
    const Component = displayMode === "flat" ? FlatMessageItem : MessageBubble;
    return (
      <div data-msg-idx={index}>
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
          onFork={onFork}
          onSaveMemory={onSaveMemory}
          onSkipStep={onSkipStep}
          onImagePreview={onImagePreview}
        />
      </div>
    );
  }, [
    messages.length, displayMode, apiBaseUrl, showChain, mdModules,
    onAskAnswer, onRetry, onEdit, onRegenerate, onRewind, onFork, onSaveMemory, onSkipStep, onImagePreview,
  ]);

  return (
    <Virtuoso
      ref={virtuosoRef}
      data={messages}
      followOutput={followOutput}
      initialTopMostItemIndex={Math.max(0, messages.length - 1)}
      atBottomThreshold={80}
      increaseViewportBy={{ top: 400, bottom: 200 }}
      itemContent={itemContent}
      style={{ flex: 1, minHeight: 0 }}
    />
  );
});
