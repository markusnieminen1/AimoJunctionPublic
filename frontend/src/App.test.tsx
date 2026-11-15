// Smoke test: the shell renders with providers + mock API, the approvals badge shows the seeded pending order,
// store-tabs has 0 external Valio hrefs & 0 placeholder tabs, TopBar opens the Company Profile & Settings menu,
// and unauthenticated users are sent to /login.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "./App";
import { createQueryClient } from "./api/queryClient";
import { endpoints } from "./api/endpoints";
import { AuthProvider } from "./auth/AuthContext";
import { clearSession, setSession } from "./auth/session";
import { CartProvider } from "./cart/CartContext";
import { I18nProvider } from "./i18n";
import { resetMockDb } from "./mocks/db";
import { setupMockServer } from "./mocks/server";

const server = setupMockServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  resetMockDb();
  clearSession();
  window.localStorage.clear();
});

function renderAt(path: string) {
  return render(
    <I18nProvider>
      <QueryClientProvider client={createQueryClient()}>
        <AuthProvider>
          <CartProvider>
            <MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
              <AppRoutes />
            </MemoryRouter>
          </CartProvider>
        </AuthProvider>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

describe("App shell", () => {
  it("redirects anonymous users to the login page", async () => {
    renderAt("/approvals");
    expect(await screen.findByRole("heading", { name: "Welcome!" })).toBeInTheDocument();
  });

  async function login() {
    const r = await endpoints.login({ email: "anna.korhonen@hotel.fi", password: "demo1234" });
    setSession({ token: r.token, principal: r.user, company: { id: "cmp_aurora", name: "Hotel Aurora Oy" } });
  }

  it("renders the portal header with the live approvals badge and opens the Company Profile & Settings menu", async () => {
    await login();
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    renderAt("/");
    const nav = screen.getAllByRole("navigation", { name: "Main" })[0]!;
    // My Aimo variant on the front page: OVERVIEW · A.I. ASSISTANT · AGENTS & PERMISSIONS · AUDIT & A2A
    expect(within(nav).getByRole("link", { name: "Agents & permissions" })).toHaveAttribute("href", "/agents");
    expect(within(nav).getByRole("link", { name: "Audit & A2A" })).toHaveAttribute("href", "/audit");
    // Approvals: desktop cluster and the mobile bell both show the seeded pending order.
    await waitFor(() => expect(screen.getByTestId("header-approvals")).toHaveTextContent("1 pending"));
    expect(screen.getByTestId("mobile-approvals")).toHaveAttribute("href", "/approvals");
    await waitFor(() => expect(within(screen.getByTestId("mobile-approvals")).getByText("1")).toBeInTheDocument());
    expect(screen.getAllByText("Hotel Aurora Oy").length).toBeGreaterThan(0);
    // No bottom thumb-bar any more.
    expect(screen.queryByRole("navigation", { name: "Quick" })).toBeNull();
    // Footer disclaimer & Junction Edition co-badges.
    expect(screen.getByTestId("footer-disclaimer")).toHaveTextContent("not an official Valio Aimo service");
    expect(screen.getByTestId("junction-edition-topbar")).toHaveTextContent("EDITION");
    expect(screen.getByTestId("junction-edition-footer")).toHaveTextContent("EDITION");

    // Client Company Profile & Settings dropdown in TopBar
    const trigger = screen.getByTestId("company-profile-menu-trigger");
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByTestId("company-profile-menu");
    expect(within(menu).getByText("Hotel Aurora Oy")).toBeInTheDocument();
    expect(within(menu).getByText("Anna Korhonen")).toBeInTheDocument();
    expect(within(menu).getByTestId("menu-company-settings")).toHaveAttribute("href", "/account");
    expect(within(menu).getByTestId("menu-agent-settings")).toHaveAttribute("href", "/agents");
    expect(within(menu).getByTestId("menu-orders-approvals")).toHaveAttribute("href", "/orders");
    expect(within(menu).getByTestId("menu-audit-a2a")).toHaveAttribute("href", "/audit");
    expect(within(menu).getByText("VALIO AIMO INTERNAL")).toBeInTheDocument();
    expect(within(menu).getByTestId("menu-aimo-admin")).toHaveAttribute("href", "/admin");
    expect(within(menu).getByTestId("menu-logout")).toBeInTheDocument();
  });

  it("picks the header variant per route (webshop lavender vs My Aimo petrol-light)", async () => {
    await login();
    const { unmount } = renderAt("/catalog");
    expect(screen.getByTestId("header-row")).toHaveAttribute("data-variant", "webshop");
    expect(screen.getByTestId("header-row")).toHaveClass("bg-lavender");
    expect(screen.getByTestId("mobile-header-row")).toHaveAttribute("data-variant", "webshop");
    expect(screen.getByRole("search")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Products" })).toHaveAttribute("aria-expanded", "false");
    unmount();
    renderAt("/agents");
    expect(screen.getByTestId("header-row")).toHaveAttribute("data-variant", "myaimo");
    expect(screen.getByTestId("header-row")).toHaveClass("bg-primary-soft");
    expect(screen.getByRole("link", { name: "My Aimo" })).toHaveAttribute("aria-current", "page");
  });

  it("renders only real internal app routes in store-tabs (0 external Valio links and 0 disabled placeholder tabs)", async () => {
    await login();
    renderAt("/orders");
    const tabs = screen.getByTestId("store-tabs");
    expect(tabs.querySelectorAll('[data-decorative="true"]')).toHaveLength(0);
    expect(tabs.querySelectorAll('[aria-disabled="true"]')).toHaveLength(0);
    expect(tabs.textContent).not.toMatch(/Not part of this demo/i);

    const hrefs = Array.from(tabs.querySelectorAll("a")).map((a) => a.getAttribute("href") ?? "");
    expect(hrefs).toEqual(["/catalog", "/", "/admin"]);
    hrefs.forEach((href) => {
      expect(href).not.toMatch(/^https?:/i);
      expect(href.toLowerCase()).not.toContain("valio");
    });
    expect(within(tabs).getByTestId("topbar-aimo-admin-link")).toHaveAttribute("href", "/admin");
    expect(within(tabs).getByTestId("topbar-aimo-admin-link")).toHaveTextContent("INTERNAL");
  });

  it("opens the mobile drawer with client groups, separated Valio Aimo Internal link, and approvals count", async () => {
    await login();
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    renderAt("/catalog");
    await waitFor(() => expect(screen.getByTestId("header-approvals")).toHaveTextContent("1 pending"));
    await user.click(screen.getByRole("button", { name: "Menu" }));
    const drawer = screen.getByRole("dialog", { name: "Menu" });
    expect(within(drawer).getByText("Delivery wholesale")).toBeInTheDocument();
    expect(within(drawer).getByRole("link", { name: /Approvals/ })).toHaveTextContent("1");
    expect(within(drawer).getByRole("link", { name: "Company profile & settings" })).toHaveAttribute("href", "/account");
    expect(within(drawer).getByTestId("drawer-aimo-admin-link")).toHaveAttribute("href", "/admin");
    expect(within(drawer).getByRole("button", { name: "Log out" })).toBeInTheDocument();
  });
});
