// M2: register validation + API errors inline, register → dashboard onboarding, login → back to `next`.
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clearSession } from "../auth/session";
import { safeNext } from "../auth/safeNext";
import { resetMockDb } from "../mocks/db";
import { setupMockServer } from "../mocks/server";
import { renderAt } from "../test/renderApp";
import { validateRegister } from "./RegisterPage";

const server = setupMockServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  resetMockDb();
  clearSession();
  window.localStorage.clear();
});

describe("validateRegister", () => {
  it("requires fields and checks formats", () => {
    const e = validateRegister({ company_name: " ", business_id: "123", user_name: "", email: "nope", password: "short" });
    expect(Object.keys(e).sort()).toEqual(["business_id", "company_name", "email", "password", "user_name"]);
    expect(validateRegister({ company_name: "A Oy", business_id: "", user_name: "A", email: "a@b.fi", password: "12345678" })).toEqual({});
    expect(validateRegister({ company_name: "A Oy", business_id: "1234567-8", user_name: "A", email: "a@b.fi", password: "12345678" })).toEqual({});
  });
});

describe("RegisterPage", () => {
  it("shows inline validation errors and does not call the API", async () => {
    const user = userEvent.setup();
    renderAt("/register");
    await user.click(await screen.findByRole("button", { name: "Create account" }));
    expect(screen.getByText("Company name is required")).toBeInTheDocument();
    expect(screen.getByText("Your name is required")).toBeInTheDocument();
    expect(screen.getByText("Email is required")).toBeInTheDocument();
    expect(screen.getByText("Password is required")).toBeInTheDocument();
    expect(screen.getByLabelText("Company name")).toHaveAttribute("aria-invalid", "true");
  });

  it("renders EMAIL_TAKEN inline with code and request id", async () => {
    const user = userEvent.setup();
    renderAt("/register");
    await user.type(screen.getByLabelText("Company name"), "Test Kitchen Oy");
    await user.type(screen.getByLabelText("Your name"), "Test Person");
    await user.type(screen.getByLabelText("Email"), "anna.korhonen@hotel.fi");
    await user.type(screen.getByLabelText("Password"), "longenough");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("EMAIL_TAKEN")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/req_/);
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
  });

  it("registers and lands on the dashboard with the create-agent call to action", async () => {
    const user = userEvent.setup();
    renderAt("/register");
    await user.type(screen.getByLabelText("Company name"), "Fresh Kitchen Oy");
    await user.type(screen.getByLabelText(/Business ID/), "1234567-8");
    await user.type(screen.getByLabelText("Your name"), "Mika Virtanen");
    await user.type(screen.getByLabelText("Email"), "mika@fresh.test");
    await user.type(screen.getByLabelText("Password"), "longenough");
    await user.click(screen.getByRole("button", { name: "Create account" }));
    expect(await screen.findByText("Create your first purchasing agent")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Create agent/ })).toHaveAttribute("href", "/agents?create=1");
  });
});

describe("LoginPage", () => {
  it("shows wrong-credential errors inline", async () => {
    const user = userEvent.setup();
    renderAt("/login");
    await user.type(await screen.findByLabelText("Email"), "anna.korhonen@hotel.fi");
    await user.type(screen.getByLabelText("Password"), "wrongpass");
    await user.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByText("Wrong email or password")).toBeInTheDocument();
    expect(screen.getByText("UNAUTHENTICATED")).toBeInTheDocument();
  });

  it("returns to the originally requested page after login", async () => {
    const user = userEvent.setup();
    renderAt("/agents");
    await screen.findByRole("heading", { name: "Welcome!" });
    await user.type(screen.getByLabelText("Email"), "anna.korhonen@hotel.fi");
    await user.type(screen.getByLabelText("Password"), "demo1234");
    await user.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(screen.getByRole("heading", { level: 1, name: "Agents & permissions" })).toBeInTheDocument());
  });

  it("renders quick-fill demo persona chips from /api/auth/demo-personas and fills credentials on click", async () => {
    const user = userEvent.setup();
    renderAt("/login");
    const chip = await screen.findByTestId("demo-persona-mikko.laine@restaurant.fi");
    expect(screen.getByTestId("demo-persona-anna.korhonen@hotel.fi")).toBeInTheDocument();
    expect(screen.getByTestId("demo-persona-sari.virtanen@festival.fi")).toBeInTheDocument();
    await user.click(chip);
    expect(screen.getByLabelText("Email")).toHaveValue("mikko.laine@restaurant.fi");
    expect(screen.getByLabelText("Password")).toHaveValue("demo1234");
  });

  it("groups the 3 generic demo personas under Reference Kitchen Personas and signs Mikko in with 1 click", async () => {
    const user = userEvent.setup();
    renderAt("/login");

    const panel = await screen.findByTestId("demo-personas-panel");
    expect(within(panel).queryByTestId("persona-group-judge")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("persona-group-founder")).not.toBeInTheDocument();

    const demoGroup = await within(panel).findByTestId("persona-group-demo");
    expect(demoGroup).toHaveTextContent("Reference Kitchen Personas");
    for (const email of [
      "anna.korhonen@hotel.fi",
      "mikko.laine@restaurant.fi",
      "sari.virtanen@festival.fi",
    ]) {
      expect(within(demoGroup).getByTestId(`demo-persona-${email}`)).toBeInTheDocument();
      expect(within(demoGroup).getByTestId(`demo-persona-signin-${email}`)).toHaveTextContent("Sign in →");
    }
    expect(within(demoGroup).getByText("Anna Korhonen")).toBeInTheDocument();
    expect(within(demoGroup).getByText("Hotel Aurora Oy")).toBeInTheDocument();
    expect(within(demoGroup).getByText("Mikko Laine")).toBeInTheDocument();
    expect(within(demoGroup).getByText("Bistro Satama Oy")).toBeInTheDocument();
    expect(within(demoGroup).getByText("Sari Virtanen")).toBeInTheDocument();
    expect(within(demoGroup).getByText("Nordic Campus Catering Oy")).toBeInTheDocument();

    const roleTag = within(demoGroup).getByText("Head Chef");
    expect(roleTag.className).toContain("rounded-none");

    // Ensure valid HTML (zero nested <button> inside <button>)
    expect(panel.querySelectorAll("button button").length).toBe(0);

    // 1-click sign-in enters Mikko's isolated company environment directly without typing
    await user.click(within(demoGroup).getByTestId("demo-persona-signin-mikko.laine@restaurant.fi"));
    expect(await screen.findByRole("heading", { level: 1, name: "Welcome to My Aimo, Mikko!" })).toBeInTheDocument();
    expect(screen.getByTestId("company-profile-menu-trigger")).toHaveTextContent("Bistro Satama Oy");
  });

  it("only accepts relative next targets", () => {
    expect(safeNext("/orders/1?x=1")).toBe("/orders/1?x=1");
    expect(safeNext("//evil.example")).toBe("/");
    expect(safeNext("https://evil.example")).toBe("/");
    expect(safeNext("/login")).toBe("/");
    expect(safeNext(null)).toBe("/");
  });
});
