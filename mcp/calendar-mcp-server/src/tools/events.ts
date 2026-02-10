import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import {
  listEventsSchema,
  getEventSchema,
  createEventSchema,
  updateEventSchema,
  deleteEventSchema,
  ListEventsInput,
  GetEventInput,
  CreateEventInput,
  UpdateEventInput,
  DeleteEventInput,
} from "../schemas/index.js";
import {
  listEvents,
  getEvent,
  createEvent,
  updateEvent,
  deleteEvent,
  handleCalendarError,
} from "../services/calendar.js";
import { CalendarEvent, ListEventsResult } from "../types.js";

/**
 * Format event to markdown
 */
function formatEventToMarkdown(event: CalendarEvent): string {
  const startStr = event.start.dateTime || event.start.date || "";
  const endStr = event.end.dateTime || event.end.date || "";

  let md = `## ${event.summary}\n\n`;
  md += `- **When**: ${startStr} ~ ${endStr}\n`;

  if (event.location) {
    md += `- **Location**: ${event.location}\n`;
  }

  if (event.description) {
    md += `- **Description**: ${event.description}\n`;
  }

  md += `- **Status**: ${event.status}\n`;

  if (event.organizer?.email) {
    md += `- **Organizer**: ${event.organizer.displayName || event.organizer.email}\n`;
  }

  if (event.attendees && event.attendees.length > 0) {
    md += `- **Attendees**:\n`;
    for (const attendee of event.attendees) {
      const status = attendee.responseStatus || "needsAction";
      const statusEmoji =
        status === "accepted" ? "✓" : status === "declined" ? "✗" : status === "tentative" ? "?" : "○";
      md += `  - ${statusEmoji} ${attendee.displayName || attendee.email} (${status})\n`;
    }
  }

  if (event.htmlLink) {
    md += `- **Link**: ${event.htmlLink}\n`;
  }

  md += `- **ID**: ${event.id}\n`;

  return md;
}

/**
 * Format events list to markdown
 */
function formatEventsListToMarkdown(result: ListEventsResult): string {
  if (result.events.length === 0) {
    return "No events found.";
  }

  let md = `Found ${result.count} event(s):\n\n`;

  for (const event of result.events) {
    const startStr = event.start.dateTime || event.start.date || "";
    const time = startStr.includes("T")
      ? new Date(startStr).toLocaleString("ko-KR", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      : startStr;

    md += `### ${event.summary}\n`;
    md += `- **When**: ${time}\n`;
    if (event.location) {
      md += `- **Location**: ${event.location}\n`;
    }
    md += `- **ID**: ${event.id}\n\n`;
  }

  if (result.has_more) {
    md += `\n---\nMore events available. Use page_token: "${result.nextPageToken}" to get next page.`;
  }

  return md;
}

/**
 * Register event tools
 */
export function registerEventTools(server: McpServer): void {
  // List events tool
  server.tool(
    "calendar_list_events",
    `List upcoming events from Google Calendar.

Supports filtering by time range and text search.

Examples:
- List next 10 events: max_results=10
- Events this week: time_max=2026-02-10T23:59:59+09:00
- Search meetings: query="meeting"

Args:
  - calendar_id (string): Calendar ID (default: "primary")
  - time_min (string): Start time in ISO 8601 format (default: now)
  - time_max (string): End time in ISO 8601 format
  - max_results (number): Max events to return (1-100, default: 20)
  - query (string): Free text search query
  - page_token (string): Pagination token
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  List of calendar events with title, time, location, and attendees.`,
    listEventsSchema.shape,
    async (input: ListEventsInput) => {
      try {
        const result = await listEvents(
          input.calendar_id,
          input.time_min,
          input.time_max,
          input.max_results,
          input.page_token,
          input.query
        );

        if (input.response_format === "json") {
          return {
            content: [{ type: "text", text: JSON.stringify(result, null, 2) }],
          };
        }

        return {
          content: [{ type: "text", text: formatEventsListToMarkdown(result) }],
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleCalendarError(error) }],
          isError: true,
        };
      }
    }
  );

  // Get event tool
  server.tool(
    "calendar_get_event",
    `Get detailed information about a specific calendar event.

Args:
  - event_id (string): The event ID to retrieve
  - calendar_id (string): Calendar ID (default: "primary")
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  Full event details including description, attendees, and status.`,
    getEventSchema.shape,
    async (input: GetEventInput) => {
      try {
        const event = await getEvent(input.event_id, input.calendar_id);

        if (input.response_format === "json") {
          return {
            content: [{ type: "text", text: JSON.stringify(event, null, 2) }],
          };
        }

        return {
          content: [{ type: "text", text: formatEventToMarkdown(event) }],
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleCalendarError(error) }],
          isError: true,
        };
      }
    }
  );

  // Create event tool
  server.tool(
    "calendar_create_event",
    `Create a new calendar event.

Examples:
- Create meeting: summary="팀 미팅", start_time="2026-02-10T10:00:00+09:00", end_time="2026-02-10T11:00:00+09:00"
- All-day event: summary="휴가", start_time="2026-02-10", end_time="2026-02-11", all_day=true
- With attendees: attendees=["user@example.com"]

Args:
  - summary (string): Event title (required)
  - description (string): Event description
  - location (string): Event location
  - start_time (string): Start time in ISO 8601 format (required)
  - end_time (string): End time in ISO 8601 format (required)
  - all_day (boolean): Create as all-day event (default: false)
  - attendees (string[]): List of attendee emails
  - calendar_id (string): Calendar ID (default: "primary")
  - time_zone (string): Time zone (default: "Asia/Seoul")
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  Created event details.`,
    createEventSchema.shape,
    async (input: CreateEventInput) => {
      try {
        const params = {
          summary: input.summary,
          description: input.description,
          location: input.location,
          start: input.all_day
            ? { date: input.start_time.split("T")[0] }
            : { dateTime: input.start_time, timeZone: input.time_zone },
          end: input.all_day
            ? { date: input.end_time.split("T")[0] }
            : { dateTime: input.end_time, timeZone: input.time_zone },
          attendees: input.attendees,
          calendarId: input.calendar_id,
          timeZone: input.time_zone,
        };

        const event = await createEvent(params);

        if (input.response_format === "json") {
          return {
            content: [{ type: "text", text: JSON.stringify(event, null, 2) }],
          };
        }

        return {
          content: [
            {
              type: "text",
              text: `Event created successfully!\n\n${formatEventToMarkdown(event)}`,
            },
          ],
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleCalendarError(error) }],
          isError: true,
        };
      }
    }
  );

  // Update event tool
  server.tool(
    "calendar_update_event",
    `Update an existing calendar event.

Only provided fields will be updated. Omitted fields keep their current values.

Args:
  - event_id (string): Event ID to update (required)
  - calendar_id (string): Calendar ID (default: "primary")
  - summary (string): New event title
  - description (string): New description
  - location (string): New location
  - start_time (string): New start time in ISO 8601 format
  - end_time (string): New end time in ISO 8601 format
  - attendees (string[]): New attendee list (replaces existing)
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  Updated event details.`,
    updateEventSchema.shape,
    async (input: UpdateEventInput) => {
      try {
        const params = {
          eventId: input.event_id,
          calendarId: input.calendar_id,
          summary: input.summary,
          description: input.description,
          location: input.location,
          start: input.start_time ? { dateTime: input.start_time } : undefined,
          end: input.end_time ? { dateTime: input.end_time } : undefined,
          attendees: input.attendees,
        };

        const event = await updateEvent(params);

        if (input.response_format === "json") {
          return {
            content: [{ type: "text", text: JSON.stringify(event, null, 2) }],
          };
        }

        return {
          content: [
            {
              type: "text",
              text: `Event updated successfully!\n\n${formatEventToMarkdown(event)}`,
            },
          ],
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleCalendarError(error) }],
          isError: true,
        };
      }
    }
  );

  // Delete event tool
  server.tool(
    "calendar_delete_event",
    `Delete a calendar event.

Args:
  - event_id (string): Event ID to delete (required)
  - calendar_id (string): Calendar ID (default: "primary")

Returns:
  Confirmation of deletion.`,
    deleteEventSchema.shape,
    async (input: DeleteEventInput) => {
      try {
        await deleteEvent(input.event_id, input.calendar_id);

        return {
          content: [
            {
              type: "text",
              text: `Event deleted successfully. (ID: ${input.event_id})`,
            },
          ],
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
