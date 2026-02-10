import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { listCalendarsSchema, ListCalendarsInput } from "../schemas/index.js";
import { listCalendars, handleCalendarError } from "../services/calendar.js";
import { Calendar } from "../types.js";

/**
 * Format calendars list to markdown
 */
function formatCalendarsToMarkdown(calendars: Calendar[]): string {
  if (calendars.length === 0) {
    return "No calendars found.";
  }

  let md = `Found ${calendars.length} calendar(s):\n\n`;

  for (const cal of calendars) {
    md += `### ${cal.summary}${cal.primary ? " (Primary)" : ""}\n`;
    md += `- **ID**: ${cal.id}\n`;
    if (cal.description) {
      md += `- **Description**: ${cal.description}\n`;
    }
    md += `- **Access**: ${cal.accessRole}\n`;
    if (cal.timeZone) {
      md += `- **Time Zone**: ${cal.timeZone}\n`;
    }
    md += "\n";
  }

  return md;
}

/**
 * Register calendar list tool
 */
export function registerCalendarTools(server: McpServer): void {
  server.tool(
    "calendar_list_calendars",
    `List all calendars accessible to the user.

Returns available calendars with their IDs, which can be used in other calendar tools.

Args:
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  List of calendars with ID, name, description, and access role.`,
    listCalendarsSchema.shape,
    async (input: ListCalendarsInput) => {
      try {
        const calendars = await listCalendars();

        if (input.response_format === "json") {
          return {
            content: [{ type: "text", text: JSON.stringify(calendars, null, 2) }],
          };
        }

        return {
          content: [{ type: "text", text: formatCalendarsToMarkdown(calendars) }],
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleCalendarError(error) }],
          isError: true,
        };
      }
    }
  );
}
