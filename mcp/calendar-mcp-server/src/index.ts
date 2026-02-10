#!/usr/bin/env node
/**
 * Google Calendar MCP Server
 *
 * MCP server for Google Calendar API integration - list, create, update, and delete events.
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { registerEventTools } from "./tools/events.js";
import { registerCalendarTools } from "./tools/calendars.js";

// Create MCP server instance
const server = new McpServer({
  name: "calendar-mcp-server",
  version: "1.0.0",
});

// Register all tools
registerEventTools(server);
registerCalendarTools(server);

// Main function
async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("Google Calendar MCP server running via stdio");
}

main().catch((error) => {
  console.error("Server error:", error);
  process.exit(1);
});
