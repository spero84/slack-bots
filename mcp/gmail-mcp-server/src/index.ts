#!/usr/bin/env node
/**
 * Gmail MCP Server
 *
 * MCP server for Gmail API integration - search, read, send emails, and manage drafts.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { registerMessageTools } from "./tools/messages.js";
import { registerLabelTools } from "./tools/labels.js";
import { registerDraftTools } from "./tools/drafts.js";

// Create MCP server instance
const server = new McpServer({
  name: "gmail-mcp-server",
  version: "1.0.0",
});

// Register all tools
registerMessageTools(server);
registerLabelTools(server);
registerDraftTools(server);

// Main function
async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Gmail MCP server running via stdio");
}

main().catch((error) => {
  console.error("Server error:", error);
  process.exit(1);
});
