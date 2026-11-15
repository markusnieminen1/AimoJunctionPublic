// Test helpers: render the app routes at a path with all providers, and log in the mock demo user.
import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AppRoutes } from "../App";
import { createQueryClient } from "../api/queryClient";
import { endpoints } from "../api/endpoints";
import { AuthProvider } from "../auth/AuthContext";
import { setSession } from "../auth/session";
import { CartProvider } from "../cart/CartContext";
import { I18nProvider } from "../i18n";

export function renderAt(path: string) {
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

/** Log in as the seeded mock user (Anna Korhonen, Hotel Aurora Oy). Requires the MSW node server to be listening. */
export async function loginDemo() {
  const r = await endpoints.login({ email: "anna.korhonen@hotel.fi", password: "demo1234" });
  setSession({ token: r.token, principal: r.user, company: { id: r.user.company_id, name: "Hotel Aurora Oy" } });
  return r;
}
