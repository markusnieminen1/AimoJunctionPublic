// Assistant conversation store: a tiny external store (like the toast store) persisted to sessionStorage,
// so navigating away and back keeps the conversation, and a reply that arrives after the page unmounted is not lost.
// It holds what the API returned, verbatim; it never computes prices, totals or policy.
import { useSyncExternalStore } from "react";
import type { ApiError } from "../api/client";
import type { AssistantAction, Order } from "../api/types";

export const ASSISTANT_STORAGE_KEY = "aimoannos.assistant";

/** Error shape kept in the stream (plain data, so it survives sessionStorage). */
export type ChatError = { status: number; code: string; message: string; request_id: string | null };

export type UserMessage = {
  id: string;
  role: "user";
  text: string;
  status: "pending" | "sent" | "error";
  error?: ChatError;
};

export type AssistantMessageMeta = {
  assuranceScore?: number | null;
  assuranceThreshold?: number;
  autoExecuted?: boolean;
  fallbackUsed?: boolean;
  processingWarnings?: string[];
};

export type AssistantMessage = {
  id: string;
  role: "assistant";
  text: string;
  actions: AssistantAction[];
  /** Clarification answers by action index (the option the user picked). */
  answers?: Record<number, string>;
  /** Orders placed from a quote action, by action index (so a re-mount does not offer Place order again). */
  placed?: Record<number, Order>;
  assuranceScore?: number | null;
  assuranceThreshold?: number;
  autoExecuted?: boolean;
  fallbackUsed?: boolean;
  processingWarnings?: string[];
};

export type ChatMessage = UserMessage | AssistantMessage;

export type ChatState = { owner: string | null; conversationId: string | null; messages: ChatMessage[] };

const EMPTY: ChatState = { owner: null, conversationId: null, messages: [] };

let state: ChatState = EMPTY;
let loaded = false;
const listeners = new Set<() => void>();

function storage(): Storage | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

function load(): ChatState {
  try {
    const raw = storage()?.getItem(ASSISTANT_STORAGE_KEY);
    if (!raw) return EMPTY;
    const v = JSON.parse(raw) as Partial<ChatState>;
    if (!Array.isArray(v.messages)) return EMPTY;
    // A request that was in flight when the tab reloaded never got its reply: show it as interrupted (retryable).
    const messages = v.messages.map((m): ChatMessage =>
      m.role === "user" && m.status === "pending"
        ? { ...m, status: "error", error: { status: 0, code: "INTERRUPTED", message: "The page was reloaded before the reply arrived.", request_id: null } }
        : m,
    );
    return { owner: typeof v.owner === "string" ? v.owner : null, conversationId: typeof v.conversationId === "string" ? v.conversationId : null, messages };
  } catch {
    return EMPTY;
  }
}

function ensureLoaded() {
  if (!loaded) {
    state = load();
    loaded = true;
  }
}

function set(next: ChatState) {
  state = next;
  try {
    if (next.messages.length === 0 && !next.conversationId) storage()?.removeItem(ASSISTANT_STORAGE_KEY);
    else storage()?.setItem(ASSISTANT_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* quota / private mode: keep in memory only */
  }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

function snapshot(): ChatState {
  ensureLoaded();
  return state;
}

let seq = 0;
const newMsgId = (p: string) => `${p}_${Date.now().toString(36)}_${(seq++).toString(36)}`;

export function toChatError(e: ApiError): ChatError {
  return { status: e.status, code: e.code, message: e.message, request_id: e.request_id };
}

export const chatStore = {
  get: snapshot,
  /** Bind the conversation to the signed-in principal; a different principal starts fresh. */
  claim(owner: string) {
    ensureLoaded();
    if (state.owner !== owner) set({ owner, conversationId: null, messages: [] });
  },
  addUser(text: string): string {
    ensureLoaded();
    const id = newMsgId("u");
    set({ ...state, messages: [...state.messages, { id, role: "user", text, status: "pending" }] });
    return id;
  },
  /** Mark a failed user message as pending again (Retry). */
  retrying(id: string) {
    ensureLoaded();
    set({ ...state, messages: state.messages.map((m) => (m.id === id && m.role === "user" ? { ...m, status: "pending", error: undefined } : m)) });
  },
  received(userMsgId: string, conversationId: string, reply: string, actions: AssistantAction[], meta?: AssistantMessageMeta) {
    ensureLoaded();
    // The user cleared the conversation while this was in flight: drop the late reply.
    if (!state.messages.some((m) => m.id === userMsgId)) return;
    const messages = state.messages.map((m) => (m.id === userMsgId && m.role === "user" ? { ...m, status: "sent" as const, error: undefined } : m));
    const idx = messages.findIndex((m) => m.id === userMsgId);
    const reply_: AssistantMessage = {
      id: newMsgId("a"),
      role: "assistant",
      text: reply,
      actions: Array.isArray(actions) ? actions : [],
      ...(meta ?? {}),
    };
    messages.splice(idx + 1, 0, reply_);
    set({ ...state, conversationId, messages });
  },
  failed(userMsgId: string, error: ChatError) {
    ensureLoaded();
    set({ ...state, messages: state.messages.map((m) => (m.id === userMsgId && m.role === "user" ? { ...m, status: "error", error } : m)) });
  },
  answer(assistantMsgId: string, actionIndex: number, option: string) {
    ensureLoaded();
    set({
      ...state,
      messages: state.messages.map((m) => (m.id === assistantMsgId && m.role === "assistant" ? { ...m, answers: { ...m.answers, [actionIndex]: option } } : m)),
    });
  },
  placed(assistantMsgId: string, actionIndex: number, order: Order) {
    ensureLoaded();
    set({
      ...state,
      messages: state.messages.map((m) => (m.id === assistantMsgId && m.role === "assistant" ? { ...m, placed: { ...m.placed, [actionIndex]: order } } : m)),
    });
  },
  /** "New conversation": forget messages and the conversation_id (the server keeps its own history). */
  reset() {
    ensureLoaded();
    set({ owner: state.owner, conversationId: null, messages: [] });
  },
};

/** Tests: drop in-memory state so the next read reloads from (cleared) sessionStorage. */
export function resetChatStoreForTests() {
  state = EMPTY;
  loaded = false;
}

export function useChatState(): ChatState {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}
