import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { clearSession } from "../auth/session";
import { resetMockClock } from "../mocks/clock";
import { resetMockDb } from "../mocks/db";
import { setupMockServer } from "../mocks/server";
import { loginDemo, renderAt } from "../test/renderApp";

const server = setupMockServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe("EN / FI i18n switcher", () => {
  beforeEach(() => {
    window.localStorage.clear();
    clearSession();
    resetMockDb();
    resetMockClock();
    document.documentElement.lang = "en";
  });

  it("defaults to English and switches live between EN and FI across the shell and dashboard", async () => {
    await loginDemo();
    const user = userEvent.setup();
    renderAt("/");

    const switcher = await screen.findByTestId("language-switcher");
    expect(switcher).toHaveTextContent("EN");
    expect(document.documentElement.lang).toBe("en");
    expect((await screen.findAllByText("Overview")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Agents & permissions").length).toBeGreaterThan(0);

    await user.click(switcher);
    await user.click(screen.getByRole("menuitemradio", { name: "Suomi (FI)" }));

    await waitFor(() => expect(screen.getByTestId("language-switcher")).toHaveTextContent("FI"));
    expect(document.documentElement.lang).toBe("fi");
    expect(window.localStorage.getItem("aimoannos.locale")).toBe("fi");
    expect(screen.getAllByText("Etusivu").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Agentit ja oikeudet").length).toBeGreaterThan(0);

    await user.click(screen.getByTestId("language-switcher"));
    await user.click(screen.getByRole("menuitemradio", { name: "English (EN)" }));

    await waitFor(() => expect(screen.getByTestId("language-switcher")).toHaveTextContent("EN"));
    expect(document.documentElement.lang).toBe("en");
    expect(window.localStorage.getItem("aimoannos.locale")).toBe("en");
    expect(screen.getAllByText("Overview").length).toBeGreaterThan(0);
  });

  it("reads persisted FI locale on initial render", async () => {
    window.localStorage.setItem("aimoannos.locale", "fi");
    await loginDemo();
    renderAt("/catalog");

    const switcher = await screen.findByTestId("language-switcher");
    expect(switcher).toHaveTextContent("FI");
    expect(document.documentElement.lang).toBe("fi");
    expect(await screen.findByRole("heading", { name: "Tuotteet" })).toBeInTheDocument();
  });
});
