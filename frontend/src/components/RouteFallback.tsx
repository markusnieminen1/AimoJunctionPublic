// Suspense fallback while a route's code chunk loads (App.tsx lazy pages). Neutral skeleton, no layout jump.
import { Skeleton } from "./Skeleton";

export function RouteFallback({ variant }: { variant?: "auth" }) {
  if (variant === "auth") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas p-4" aria-busy="true" aria-label="Loading page">
        <div className="w-full max-w-sm space-y-3 rounded-lg border border-line bg-white p-5">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-4" aria-busy="true" aria-label="Loading page">
      <Skeleton className="h-7 w-56" />
      <Skeleton className="h-4 w-80 max-w-full" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
      <Skeleton className="h-64 w-full" />
    </div>
  );
}
