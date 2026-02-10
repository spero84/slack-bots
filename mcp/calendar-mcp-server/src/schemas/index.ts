import { z } from "zod";

// List events schema
export const listEventsSchema = z.object({
  calendar_id: z
    .string()
    .optional()
    .default("primary")
    .describe("Calendar ID to list events from (default: primary)"),
  time_min: z
    .string()
    .optional()
    .describe("Start time in ISO 8601 format (default: now)"),
  time_max: z
    .string()
    .optional()
    .describe("End time in ISO 8601 format"),
  max_results: z
    .number()
    .int()
    .min(1)
    .max(100)
    .default(20)
    .describe("Maximum number of events to return (1-100, default: 20)"),
  query: z
    .string()
    .optional()
    .describe("Free text search query to filter events"),
  page_token: z
    .string()
    .optional()
    .describe("Page token for pagination from previous response"),
  response_format: z
    .enum(["markdown", "json"])
    .default("markdown")
    .describe("Output format: 'markdown' for human-readable or 'json' for structured data"),
});

// Get event schema
export const getEventSchema = z.object({
  event_id: z.string().min(1).describe("The ID of the event to retrieve"),
  calendar_id: z
    .string()
    .optional()
    .default("primary")
    .describe("Calendar ID (default: primary)"),
  response_format: z
    .enum(["markdown", "json"])
    .default("markdown")
    .describe("Output format: 'markdown' for human-readable or 'json' for structured data"),
});

// Create event schema
export const createEventSchema = z.object({
  summary: z.string().min(1).describe("Event title/summary"),
  description: z.string().optional().describe("Event description"),
  location: z.string().optional().describe("Event location"),
  start_time: z.string().describe("Start time in ISO 8601 format (e.g., 2026-02-10T09:00:00+09:00)"),
  end_time: z.string().describe("End time in ISO 8601 format (e.g., 2026-02-10T10:00:00+09:00)"),
  all_day: z
    .boolean()
    .optional()
    .default(false)
    .describe("If true, creates an all-day event (use date format YYYY-MM-DD)"),
  attendees: z
    .array(z.string().email())
    .optional()
    .describe("List of attendee email addresses"),
  calendar_id: z
    .string()
    .optional()
    .default("primary")
    .describe("Calendar ID to create event in (default: primary)"),
  time_zone: z
    .string()
    .optional()
    .default("Asia/Seoul")
    .describe("Time zone for the event (default: Asia/Seoul)"),
  response_format: z
    .enum(["markdown", "json"])
    .default("markdown")
    .describe("Output format: 'markdown' for human-readable or 'json' for structured data"),
});

// Update event schema
export const updateEventSchema = z.object({
  event_id: z.string().min(1).describe("The ID of the event to update"),
  calendar_id: z
    .string()
    .optional()
    .default("primary")
    .describe("Calendar ID (default: primary)"),
  summary: z.string().optional().describe("New event title/summary"),
  description: z.string().optional().describe("New event description"),
  location: z.string().optional().describe("New event location"),
  start_time: z.string().optional().describe("New start time in ISO 8601 format"),
  end_time: z.string().optional().describe("New end time in ISO 8601 format"),
  attendees: z
    .array(z.string().email())
    .optional()
    .describe("New list of attendee email addresses (replaces existing)"),
  response_format: z
    .enum(["markdown", "json"])
    .default("markdown")
    .describe("Output format: 'markdown' for human-readable or 'json' for structured data"),
});

// Delete event schema
export const deleteEventSchema = z.object({
  event_id: z.string().min(1).describe("The ID of the event to delete"),
  calendar_id: z
    .string()
    .optional()
    .default("primary")
    .describe("Calendar ID (default: primary)"),
});

// List calendars schema
export const listCalendarsSchema = z.object({
  response_format: z
    .enum(["markdown", "json"])
    .default("markdown")
    .describe("Output format: 'markdown' for human-readable or 'json' for structured data"),
});

export type ListEventsInput = z.infer<typeof listEventsSchema>;
export type GetEventInput = z.infer<typeof getEventSchema>;
export type CreateEventInput = z.infer<typeof createEventSchema>;
export type UpdateEventInput = z.infer<typeof updateEventSchema>;
export type DeleteEventInput = z.infer<typeof deleteEventSchema>;
export type ListCalendarsInput = z.infer<typeof listCalendarsSchema>;
