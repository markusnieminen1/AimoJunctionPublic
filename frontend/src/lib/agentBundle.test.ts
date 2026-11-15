import { describe, expect, it } from "vitest";
import { buildClaudeMcpAddCommand, buildClaudePluginZip, buildMcpJson, buildUniversalAgentPrompt, claudePluginZipFilename } from "./agentBundle";
import { crc32, createZip } from "./zip";

/** Minimal PKZIP parser that verifies local headers, CRCs, central directory, and EOCD. */
function readZipEntries(bytes: Uint8Array): Map<string, string> {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const decoder = new TextDecoder();
  const entries = new Map<string, string>();
  let offset = 0;

  while (offset + 4 <= bytes.length && view.getUint32(offset, true) === 0x04034b50) {
    const compression = view.getUint16(offset + 8, true);
    expect(compression).toBe(0);
    const expectedCrc = view.getUint32(offset + 14, true);
    const size = view.getUint32(offset + 18, true);
    const nameLen = view.getUint16(offset + 26, true);
    const extraLen = view.getUint16(offset + 28, true);
    const nameStart = offset + 30;
    const dataStart = nameStart + nameLen + extraLen;
    const name = decoder.decode(bytes.subarray(nameStart, nameStart + nameLen));
    const data = bytes.subarray(dataStart, dataStart + size);
    expect(crc32(data)).toBe(expectedCrc);
    entries.set(name, decoder.decode(data));
    offset = dataStart + size;
  }

  // Central directory starts immediately after the last local entry and ends at EOCD.
  let cdCount = 0;
  while (offset + 4 <= bytes.length && view.getUint32(offset, true) === 0x02014b50) {
    const nameLen = view.getUint16(offset + 28, true);
    const extraLen = view.getUint16(offset + 30, true);
    const commentLen = view.getUint16(offset + 32, true);
    cdCount += 1;
    offset += 46 + nameLen + extraLen + commentLen;
  }

  expect(cdCount).toBe(entries.size);
  expect(view.getUint32(offset, true)).toBe(0x06054b50);
  expect(view.getUint16(offset + 8, true)).toBe(entries.size);
  expect(view.getUint16(offset + 10, true)).toBe(entries.size);
  expect(offset + 22).toBe(bytes.length);

  return entries;
}

describe("zip + agentBundle", () => {
  it("builds valid store-mode zip archives with CRC32", () => {
    const zip = createZip([
      { name: "hello.txt", content: "hei maailma" },
      { name: "nested/a.json", content: '{"ok":true}' },
    ]);
    const extracted = readZipEntries(zip);
    expect(extracted.get("hello.txt")).toBe("hei maailma");
    expect(extracted.get("nested/a.json")).toBe('{"ok":true}');
  });

  it("substitutes agent credentials into .mcp.json purely from arguments", () => {
    const raw = buildMcpJson({
      agentId: "agt_test01",
      agentSecret: "aks_secret_123",
      origin: "https://aimo.example.com",
    });
    const parsed = JSON.parse(raw);
    expect(parsed.mcpServers.aimoannos).toEqual({
      command: "python3",
      args: ["${CLAUDE_PLUGIN_ROOT}/mcp/aimo_mcp_bridge.py"],
      env: {
        AIMO_BASE_URL: "https://aimo.example.com",
        AIMO_AGENT_ID: "agt_test01",
        AIMO_AGENT_SECRET: "aks_secret_123",
      },
    });
  });

  it("packages the self-contained Claude plugin + MCP bridge bundle with embedded credentials", () => {
    const zip = buildClaudePluginZip({
      agentId: "agt_bakery",
      agentSecret: "aks_bakery_topsecret",
      origin: "http://127.0.0.1:8000",
    });
    const extracted = readZipEntries(zip);

    expect([...extracted.keys()].sort()).toEqual([
      ".claude-plugin/plugin.json",
      ".gitignore",
      ".mcp.json",
      "SKILL.md",
      "examples/demo_scenarios.md",
      "mcp/aimo_credentials.json",
      "mcp/aimo_mcp_bridge.py",
      "references/api_and_mcp_reference.md",
    ]);

    expect(extracted.get(".mcp.json")).toContain("aks_bakery_topsecret");
    expect(extracted.get(".mcp.json")).toContain("http://127.0.0.1:8000");
    expect(extracted.get(".gitignore")).toContain(".mcp.json");
    expect(extracted.get(".gitignore")).toContain("mcp/aimo_credentials.json");
    expect(extracted.get(".claude-plugin/plugin.json")).toContain('"aimoannos"');
    expect(extracted.get("SKILL.md")).toContain("http://127.0.0.1:8000/api/a2a");
    expect(extracted.get("SKILL.md")).not.toContain("${AIMO_BASE_URL}");

    const creds = JSON.parse(extracted.get("mcp/aimo_credentials.json")!);
    expect(creds).toEqual({
      AIMO_BASE_URL: "http://127.0.0.1:8000",
      AIMO_AGENT_ID: "agt_bakery",
      AIMO_AGENT_SECRET: "aks_bakery_topsecret",
    });

    const bridgePy = extracted.get("mcp/aimo_mcp_bridge.py")!;
    expect(bridgePy).toContain('BUNDLED_BASE_URL = "http://127.0.0.1:8000"');
    expect(bridgePy).toContain('BUNDLED_AGENT_ID = "agt_bakery"');
    expect(bridgePy).toContain('BUNDLED_AGENT_SECRET = "aks_bakery_topsecret"');
    expect(bridgePy).toContain('BUNDLED_BEARER_TOKEN = ""');
    expect(bridgePy).toContain('BUNDLED_TOKEN_EXPIRES_AT = ""');
    expect(bridgePy).not.toMatch(/__AIMO_BUNDLED_[A-Z_]+__/);
    expect(claudePluginZipFilename("agt_bakery")).toBe("aimoannos-claude-plugin-agt_bakery.zip");
  });

  it("embeds eagerly minted Bearer JWTs and preserves $ characters verbatim across .zip and .mcp.json", () => {
    const trickySecret = "aks_special_$$_$&_secret";
    const sampleBearer = ["eyJ", "hbGciOiJIUzI1NiJ9.initial_token"].join("");
    const zip = buildClaudePluginZip({
      agentId: "agt_token01",
      agentSecret: trickySecret,
      origin: "https://portal.example.com",
      bearerToken: sampleBearer,
      tokenExpiresAt: "2026-11-15T02:30:00Z",
    });
    const extracted = readZipEntries(zip);

    const mcpParsed = JSON.parse(extracted.get(".mcp.json")!);
    expect(mcpParsed.mcpServers.aimoannos.env).toEqual({
      AIMO_BASE_URL: "https://portal.example.com",
      AIMO_AGENT_ID: "agt_token01",
      AIMO_AGENT_SECRET: trickySecret,
      AIMO_BEARER_TOKEN: sampleBearer,
      AIMO_TOKEN_EXPIRES_AT: "2026-11-15T02:30:00Z",
    });

    const creds = JSON.parse(extracted.get("mcp/aimo_credentials.json")!);
    expect(creds).toEqual({
      AIMO_BASE_URL: "https://portal.example.com",
      AIMO_AGENT_ID: "agt_token01",
      AIMO_AGENT_SECRET: trickySecret,
      AIMO_BEARER_TOKEN: sampleBearer,
      AIMO_TOKEN_EXPIRES_AT: "2026-11-15T02:30:00Z",
    });

    const bridgePy = extracted.get("mcp/aimo_mcp_bridge.py")!;
    expect(bridgePy).toContain(`BUNDLED_AGENT_SECRET = ${JSON.stringify(trickySecret)}`);
    expect(bridgePy).toContain(`BUNDLED_BEARER_TOKEN = ${JSON.stringify(sampleBearer)}`);
    expect(bridgePy).toContain('BUNDLED_TOKEN_EXPIRES_AT = "2026-11-15T02:30:00Z"');
  });

  it("renders the framework-agnostic universal agent prompt and claude mcp add command with substituted credentials", () => {
    const prompt = buildUniversalAgentPrompt({
      agentId: "agt_new01",
      agentSecret: "aks_new_secret",
      origin: "https://aimo.example.com",
    });
    expect(prompt).toContain("https://aimo.example.com");
    expect(prompt).toContain("agt_new01");
    expect(prompt).toContain("aks_new_secret");
    expect(prompt).toContain("/api/agent-auth/token");
    expect(prompt).not.toContain("{{AIMO_");

    const cli = buildClaudeMcpAddCommand({
      agentId: "agt_new01",
      agentSecret: "aks_new_secret",
      origin: "https://aimo.example.com",
    });
    expect(cli).toContain("claude mcp add aimoannos");
    expect(cli).toContain("agt_new01");
    expect(cli).toContain("aks_new_secret");
    expect(cli).toContain("./mcp/aimo_mcp_bridge.py");
  });
});
