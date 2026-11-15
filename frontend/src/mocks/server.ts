// MSW for Node (Vitest). Usage: `const server = setupMockServer(); beforeAll(() => server.listen())`.
import { setupServer } from "msw/node";
import { handlers } from "./handlers";

export function setupMockServer() {
  return setupServer(...handlers);
}
