import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

// React.lazy page chunks can take >1s to import under 15-worker Vitest contention.
configure({ asyncUtilTimeout: 3000 });

// Vitest runs without globals, so Testing Library's auto-cleanup is not registered: do it here.
afterEach(() => cleanup());

// jsdom has no layout: window.scrollTo logs "Not implemented". The shell scrolls to the top on every route change.
window.scrollTo = (() => undefined) as typeof window.scrollTo;
