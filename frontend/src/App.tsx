import { QueryClientProvider } from "@tanstack/react-query";
import { Suspense, lazy, useEffect, useState, type ComponentType } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { createQueryClient } from "./api/queryClient";
import { AuthProvider } from "./auth/AuthContext";
import { RequireAuth } from "./auth/RequireAuth";
import { CartProvider } from "./cart/CartContext";
import { AppShell } from "./components/AppShell";
import { Toaster } from "./components/Toast";
import { RouteFallback } from "./components/RouteFallback";
import { I18nProvider } from "./i18n";

// Route-level code splitting: each page is its own chunk. Once the shell has rendered, the browser fetches the other
// chunks when idle, so moving between pages on a phone does not wait for the network.
type PageModule = { default: ComponentType };
function lazyPage(load: () => Promise<PageModule>) {
  const Page = lazy(load);
  return Object.assign(Page, { preload: load });
}

const LoginPage = lazyPage(() => import("./pages/LoginPage"));
const RegisterPage = lazyPage(() => import("./pages/RegisterPage"));
const DashboardPage = lazyPage(() => import("./pages/DashboardPage"));
const CatalogPage = lazyPage(() => import("./pages/CatalogPage"));
const ProductPage = lazyPage(() => import("./pages/ProductPage"));
const CartPage = lazyPage(() => import("./pages/CartPage"));
const ApprovalsPage = lazyPage(() => import("./pages/ApprovalsPage"));
const OrdersPage = lazyPage(() => import("./pages/OrdersPage"));
const OrderDetailPage = lazyPage(() => import("./pages/OrderDetailPage"));
const AgentsPage = lazyPage(() => import("./pages/AgentsPage"));
const AgentDetailPage = lazyPage(() => import("./pages/AgentDetailPage"));
const AuditPage = lazyPage(() => import("./pages/AuditPage"));
const AssistantPage = lazyPage(() => import("./pages/AssistantPage"));
const AccountPage = lazyPage(() => import("./pages/AccountPage"));
const AdminPage = lazyPage(() => import("./pages/AdminPage"));
const NotFoundPage = lazyPage(() => import("./pages/NotFoundPage"));

const PAGES = [
  DashboardPage, CatalogPage, ProductPage, CartPage, ApprovalsPage, OrdersPage, OrderDetailPage,
  AgentsPage, AgentDetailPage, AuditPage, AssistantPage, AccountPage, AdminPage, NotFoundPage, LoginPage, RegisterPage,
];

/** Fetch every page chunk once the browser is idle (no-op in tests / when already loaded). */
function usePreloadPages() {
  useEffect(() => {
    const run = () => PAGES.forEach((p) => void p.preload().catch(() => undefined));
    const w = window as Window & { requestIdleCallback?: (cb: () => void) => number; cancelIdleCallback?: (id: number) => void };
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(run);
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(run, 1500);
    return () => window.clearTimeout(id);
  }, []);
}

/** Suspense per route, below the shell: the header and navigation stay while a page chunk loads. */
function page(Page: ComponentType, variant?: "auth") {
  return (
    <Suspense fallback={<RouteFallback variant={variant} />}>
      <Page />
    </Suspense>
  );
}

/** Route table (DESIGN_VALIO §6). Every page lives in its own file under src/pages/. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={page(LoginPage, "auth")} />
      <Route path="/register" element={page(RegisterPage, "auth")} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={page(DashboardPage)} />
        <Route path="catalog" element={page(CatalogPage)} />
        <Route path="products/:id" element={page(ProductPage)} />
        <Route path="cart" element={page(CartPage)} />
        <Route path="approvals" element={page(ApprovalsPage)} />
        <Route path="orders" element={page(OrdersPage)} />
        <Route path="orders/:id" element={page(OrderDetailPage)} />
        <Route path="agents" element={page(AgentsPage)} />
        <Route path="agents/new" element={<Navigate to="/agents?create=1" replace />} />
        <Route path="agents/:id" element={page(AgentDetailPage)} />
        <Route path="audit" element={page(AuditPage)} />
        <Route path="assistant" element={page(AssistantPage)} />
        <Route path="account" element={page(AccountPage)} />
        <Route path="admin" element={page(AdminPage)} />
        <Route path="*" element={page(NotFoundPage)} />
      </Route>
    </Routes>
  );
}

export default function App() {
  const [queryClient] = useState(createQueryClient);
  usePreloadPages();
  return (
    <I18nProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <CartProvider>
            <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
              <AppRoutes />
            </BrowserRouter>
            <Toaster />
          </CartProvider>
        </AuthProvider>
      </QueryClientProvider>
    </I18nProvider>
  );
}
