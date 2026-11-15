// TanStack Query client with a global error → toast handler.
// Opt out per call with `meta: { silent: true }` (the hooks accept `{ silent: true }`) when a page renders errors inline.
import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { isApiError } from "./client";
import { toast } from "../components/Toast";

declare module "@tanstack/react-query" {
  interface Register {
    queryMeta: { silent?: boolean };
    mutationMeta: { silent?: boolean };
  }
}

function shouldToast(error: unknown, silent: boolean | undefined): boolean {
  if (silent) return false;
  // 401s redirect to /login instead.
  if (isApiError(error) && error.status === 401) return false;
  return true;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (shouldToast(error, query.meta?.silent)) toast.error(error);
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _vars, _ctx, mutation) => {
        if (shouldToast(error, mutation.meta?.silent)) toast.error(error);
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        retry: (count, error) => {
          // Retry only network/5xx once; 4xx are final.
          if (isApiError(error) && error.status >= 400 && error.status < 500) return false;
          return count < 1;
        },
      },
      mutations: { retry: false },
    },
  });
}
