// Client-side generation of .mcp.json and the self-contained Claude plugin + skill .zip bundle.
// All agent_secret substitution happens purely in memory; nothing is saved to web storage or sent back to the server.

import mcpTemplateRaw from "../../../clients/skills/claude/.mcp.json.template?raw";
import gitignoreRaw from "../../../clients/skills/claude/.gitignore?raw";
import pluginJsonRaw from "../../../clients/skills/claude/.claude-plugin/plugin.json?raw";
import skillMdRaw from "../../../clients/skills/claude/SKILL.md?raw";
import apiReferenceRaw from "../../../clients/skills/claude/references/api_and_mcp_reference.md?raw";
import demoScenariosRaw from "../../../clients/skills/claude/examples/demo_scenarios.md?raw";
import mcpBridgePyRaw from "../../../clients/mcp/aimo_mcp_bridge.py?raw";
import universalPromptRaw from "../../../clients/skills/UNIVERSAL_AGENT_PROMPT.md?raw";
import { createZip } from "./zip";

export interface McpTemplateValues {
  agentId: string;
  agentSecret: string;
  origin?: string;
  bearerToken?: string;
  tokenExpiresAt?: string;
}

function replaceLiteral(source: string, search: string, replacement: string): string {
  return source.split(search).join(replacement);
}

/** Builds a ready-to-use .mcp.json from clients/skills/claude/.mcp.json.template in memory. */
export function buildMcpJson({
  agentId,
  agentSecret,
  origin = window.location.origin,
  bearerToken,
  tokenExpiresAt,
}: McpTemplateValues): string {
  const parsed = JSON.parse(mcpTemplateRaw) as {
    mcpServers?: Record<string, { env?: Record<string, string>; [k: string]: unknown }>;
  };
  const server = parsed.mcpServers?.aimoannos;
  if (server?.env) {
    server.env.AIMO_BASE_URL = origin;
    server.env.AIMO_AGENT_ID = agentId;
    server.env.AIMO_AGENT_SECRET = agentSecret;
    if (bearerToken) server.env.AIMO_BEARER_TOKEN = bearerToken;
    if (tokenExpiresAt) server.env.AIMO_TOKEN_EXPIRES_AT = tokenExpiresAt;
  }
  return `${JSON.stringify(parsed, null, 2)}\n`;
}

/** Builds a self-contained Claude plugin + MCP bridge .zip bundle in memory with embedded credentials. */
export function buildClaudePluginZip(values: McpTemplateValues): Uint8Array {
  const {
    agentId,
    agentSecret,
    origin = window.location.origin,
    bearerToken,
    tokenExpiresAt,
  } = values;

  const bundledBridgePy = (
    [
      ['"__AIMO_BUNDLED_BASE_URL__"', JSON.stringify(origin)],
      ['"__AIMO_BUNDLED_AGENT_ID__"', JSON.stringify(agentId)],
      ['"__AIMO_BUNDLED_AGENT_SECRET__"', JSON.stringify(agentSecret)],
      ['"__AIMO_BUNDLED_BEARER_TOKEN__"', JSON.stringify(bearerToken ?? "")],
      ['"__AIMO_BUNDLED_TOKEN_EXPIRES_AT__"', JSON.stringify(tokenExpiresAt ?? "")],
    ] as const
  ).reduce((acc, [sentinel, literal]) => replaceLiteral(acc, sentinel, literal), mcpBridgePyRaw);

  const bundledSkillMd = replaceLiteral(skillMdRaw, "${AIMO_BASE_URL}", origin);
  const bundledCredentialsJson = `${JSON.stringify(
    {
      AIMO_BASE_URL: origin,
      AIMO_AGENT_ID: agentId,
      AIMO_AGENT_SECRET: agentSecret,
      ...(bearerToken ? { AIMO_BEARER_TOKEN: bearerToken } : {}),
      ...(tokenExpiresAt ? { AIMO_TOKEN_EXPIRES_AT: tokenExpiresAt } : {}),
    },
    null,
    2,
  )}\n`;

  return createZip([
    { name: ".mcp.json", content: buildMcpJson(values) },
    { name: ".gitignore", content: gitignoreRaw },
    { name: ".claude-plugin/plugin.json", content: pluginJsonRaw },
    { name: "SKILL.md", content: bundledSkillMd },
    { name: "references/api_and_mcp_reference.md", content: apiReferenceRaw },
    { name: "examples/demo_scenarios.md", content: demoScenariosRaw },
    { name: "mcp/aimo_credentials.json", content: bundledCredentialsJson },
    { name: "mcp/aimo_mcp_bridge.py", content: bundledBridgePy },
  ]);
}

export function claudePluginZipFilename(agentId: string): string {
  const safeId = agentId.replace(/[^a-zA-Z0-9_-]/g, "_");
  return `aimoannos-claude-plugin-${safeId}.zip`;
}

/** Pre-fills the framework-agnostic agent prompt purely in memory (Claude MCP, LangGraph, Cursor, REST). */
export function buildUniversalAgentPrompt({
  agentId,
  agentSecret = "<AIMO_AGENT_SECRET>",
  origin = window.location.origin,
}: {
  agentId: string;
  agentSecret?: string;
  origin?: string;
}): string {
  return [
    ["{{AIMO_BASE_URL}}", origin],
    ["{{AIMO_AGENT_ID}}", agentId],
    ["{{AIMO_AGENT_SECRET}}", agentSecret],
  ].reduce((acc, [placeholder, val]) => replaceLiteral(acc, placeholder!, val!), universalPromptRaw);
}

/** One-line `claude mcp add` CLI snippet pre-bound to this exact environment & agent credential (run inside unzipped bundle). */
export function buildClaudeMcpAddCommand({
  agentId,
  agentSecret,
  origin = window.location.origin,
}: McpTemplateValues): string {
  return `claude mcp add aimoannos -e AIMO_BASE_URL=${origin} -e AIMO_AGENT_ID=${agentId} -e AIMO_AGENT_SECRET=${agentSecret} -- python3 ./mcp/aimo_mcp_bridge.py`;
}
