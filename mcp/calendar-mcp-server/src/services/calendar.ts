import { google, calendar_v3 } from "googleapis";
import { OAuth2Client } from "google-auth-library";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import {
  CalendarEvent,
  Calendar,
  ListEventsResult,
  CreateEventParams,
  UpdateEventParams,
  EventDateTime,
  Attendee,
} from "../types.js";
import { CALENDAR_SCOPES, TOKEN_PATH, CREDENTIALS_PATH } from "../constants.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let calendarClient: calendar_v3.Calendar | null = null;
let oAuth2Client: OAuth2Client | null = null;

/**
 * Get the credentials file path from environment or default location
 */
function getCredentialsPath(): string {
  return process.env.CALENDAR_CREDENTIALS_PATH || path.join(__dirname, "..", "..", CREDENTIALS_PATH);
}

/**
 * Get the token file path from environment or default location
 */
function getTokenPath(): string {
  return process.env.CALENDAR_TOKEN_PATH || path.join(__dirname, "..", "..", TOKEN_PATH);
}

/**
 * Initialize OAuth2 client and Calendar API
 */
export async function initializeCalendarClient(): Promise<calendar_v3.Calendar> {
  if (calendarClient) {
    return calendarClient;
  }

  const credentialsPath = getCredentialsPath();
  const tokenPath = getTokenPath();

  // Check if credentials file exists
  if (!fs.existsSync(credentialsPath)) {
    throw new Error(
      `Credentials file not found at ${credentialsPath}. ` +
        "Please download OAuth 2.0 credentials from Google Cloud Console and save as credentials.json"
    );
  }

  // Load credentials
  const credentials = JSON.parse(fs.readFileSync(credentialsPath, "utf-8"));
  const { client_id, client_secret, redirect_uris } = credentials.installed || credentials.web;

  oAuth2Client = new google.auth.OAuth2(client_id, client_secret, redirect_uris?.[0] || "urn:ietf:wg:oauth:2.0:oob");

  // Check if token exists
  if (!fs.existsSync(tokenPath)) {
    throw new Error(
      `Token file not found at ${tokenPath}. ` +
        "Please run 'npm run auth' to authenticate with Google Calendar first."
    );
  }

  // Load token
  const token = JSON.parse(fs.readFileSync(tokenPath, "utf-8"));
  oAuth2Client.setCredentials(token);

  // Handle token refresh
  oAuth2Client.on("tokens", (tokens) => {
    if (tokens.refresh_token) {
      const currentToken = JSON.parse(fs.readFileSync(tokenPath, "utf-8"));
      const updatedToken = { ...currentToken, ...tokens };
      fs.writeFileSync(tokenPath, JSON.stringify(updatedToken, null, 2));
    }
  });

  calendarClient = google.calendar({ version: "v3", auth: oAuth2Client });
  return calendarClient;
}

/**
 * Generate OAuth2 authorization URL
 */
export function getAuthUrl(): string {
  const credentialsPath = getCredentialsPath();

  if (!fs.existsSync(credentialsPath)) {
    throw new Error(
      `Credentials file not found at ${credentialsPath}. ` +
        "Please download OAuth 2.0 credentials from Google Cloud Console."
    );
  }

  const credentials = JSON.parse(fs.readFileSync(credentialsPath, "utf-8"));
  const { client_id, client_secret, redirect_uris } = credentials.installed || credentials.web;

  const auth = new google.auth.OAuth2(client_id, client_secret, redirect_uris?.[0] || "urn:ietf:wg:oauth:2.0:oob");

  return auth.generateAuthUrl({
    access_type: "offline",
    scope: CALENDAR_SCOPES,
    prompt: "consent",
  });
}

/**
 * Exchange authorization code for tokens
 */
export async function exchangeCodeForTokens(code: string): Promise<void> {
  const credentialsPath = getCredentialsPath();
  const tokenPath = getTokenPath();

  const credentials = JSON.parse(fs.readFileSync(credentialsPath, "utf-8"));
  const { client_id, client_secret, redirect_uris } = credentials.installed || credentials.web;

  const auth = new google.auth.OAuth2(client_id, client_secret, redirect_uris?.[0] || "urn:ietf:wg:oauth:2.0:oob");

  const { tokens } = await auth.getToken(code);
  fs.writeFileSync(tokenPath, JSON.stringify(tokens, null, 2));
  console.log("Token saved to", tokenPath);
}

/**
 * Convert Google Calendar API event to CalendarEvent
 */
function toCalendarEvent(event: calendar_v3.Schema$Event): CalendarEvent {
  return {
    id: event.id || "",
    summary: event.summary || "(No title)",
    description: event.description || undefined,
    location: event.location || undefined,
    start: {
      dateTime: event.start?.dateTime || undefined,
      date: event.start?.date || undefined,
      timeZone: event.start?.timeZone || undefined,
    },
    end: {
      dateTime: event.end?.dateTime || undefined,
      date: event.end?.date || undefined,
      timeZone: event.end?.timeZone || undefined,
    },
    status: event.status || "confirmed",
    htmlLink: event.htmlLink || undefined,
    creator: event.creator
      ? {
          email: event.creator.email || undefined,
          displayName: event.creator.displayName || undefined,
        }
      : undefined,
    organizer: event.organizer
      ? {
          email: event.organizer.email || undefined,
          displayName: event.organizer.displayName || undefined,
        }
      : undefined,
    attendees: event.attendees?.map((a) => ({
      email: a.email || "",
      displayName: a.displayName || undefined,
      responseStatus: a.responseStatus || undefined,
      self: a.self || undefined,
      organizer: a.organizer || undefined,
    })),
    recurrence: event.recurrence || undefined,
    recurringEventId: event.recurringEventId || undefined,
  };
}

/**
 * List events from a calendar
 */
export async function listEvents(
  calendarId: string = "primary",
  timeMin?: string,
  timeMax?: string,
  maxResults: number = 20,
  pageToken?: string,
  query?: string
): Promise<ListEventsResult> {
  const calendar = await initializeCalendarClient();

  const params: calendar_v3.Params$Resource$Events$List = {
    calendarId,
    maxResults,
    singleEvents: true,
    orderBy: "startTime",
    pageToken,
  };

  // Set time range - default to today onwards
  if (timeMin) {
    params.timeMin = timeMin;
  } else {
    params.timeMin = new Date().toISOString();
  }

  if (timeMax) {
    params.timeMax = timeMax;
  }

  if (query) {
    params.q = query;
  }

  const response = await calendar.events.list(params);

  const events = (response.data.items || []).map(toCalendarEvent);

  return {
    total: events.length,
    count: events.length,
    events,
    nextPageToken: response.data.nextPageToken || undefined,
    has_more: !!response.data.nextPageToken,
  };
}

/**
 * Get a single event by ID
 */
export async function getEvent(
  eventId: string,
  calendarId: string = "primary"
): Promise<CalendarEvent> {
  const calendar = await initializeCalendarClient();

  const response = await calendar.events.get({
    calendarId,
    eventId,
  });

  return toCalendarEvent(response.data);
}

/**
 * Create a new event
 */
export async function createEvent(params: CreateEventParams): Promise<CalendarEvent> {
  const calendar = await initializeCalendarClient();

  const eventBody: calendar_v3.Schema$Event = {
    summary: params.summary,
    description: params.description,
    location: params.location,
    start: params.start,
    end: params.end,
  };

  if (params.attendees && params.attendees.length > 0) {
    eventBody.attendees = params.attendees.map((email) => ({ email }));
  }

  const response = await calendar.events.insert({
    calendarId: params.calendarId || "primary",
    requestBody: eventBody,
    sendUpdates: params.attendees && params.attendees.length > 0 ? "all" : "none",
  });

  return toCalendarEvent(response.data);
}

/**
 * Update an existing event
 */
export async function updateEvent(params: UpdateEventParams): Promise<CalendarEvent> {
  const calendar = await initializeCalendarClient();
  const calendarId = params.calendarId || "primary";

  // Get existing event first
  const existing = await calendar.events.get({
    calendarId,
    eventId: params.eventId,
  });

  const eventBody: calendar_v3.Schema$Event = {
    ...existing.data,
    summary: params.summary ?? existing.data.summary,
    description: params.description ?? existing.data.description,
    location: params.location ?? existing.data.location,
  };

  if (params.start) {
    eventBody.start = params.start;
  }
  if (params.end) {
    eventBody.end = params.end;
  }
  if (params.attendees) {
    eventBody.attendees = params.attendees.map((email) => ({ email }));
  }

  const response = await calendar.events.update({
    calendarId,
    eventId: params.eventId,
    requestBody: eventBody,
    sendUpdates: "all",
  });

  return toCalendarEvent(response.data);
}

/**
 * Delete an event
 */
export async function deleteEvent(
  eventId: string,
  calendarId: string = "primary"
): Promise<void> {
  const calendar = await initializeCalendarClient();

  await calendar.events.delete({
    calendarId,
    eventId,
    sendUpdates: "all",
  });
}

/**
 * List all calendars
 */
export async function listCalendars(): Promise<Calendar[]> {
  const calendar = await initializeCalendarClient();

  const response = await calendar.calendarList.list();

  return (response.data.items || []).map((cal) => ({
    id: cal.id || "",
    summary: cal.summary || "",
    description: cal.description || undefined,
    timeZone: cal.timeZone || undefined,
    primary: cal.primary || false,
    accessRole: cal.accessRole || undefined,
    backgroundColor: cal.backgroundColor || undefined,
    foregroundColor: cal.foregroundColor || undefined,
  }));
}

/**
 * Handle API errors
 */
export function handleCalendarError(error: unknown): string {
  if (error instanceof Error) {
    const message = error.message;

    if (message.includes("invalid_grant")) {
      return "Error: Authentication expired. Please run 'npm run auth' to re-authenticate.";
    }
    if (message.includes("Request had insufficient authentication scopes")) {
      return "Error: Insufficient permissions. Please re-authenticate with required scopes.";
    }
    if (message.includes("404")) {
      return "Error: Event or calendar not found. Please check the ID is correct.";
    }
    if (message.includes("403")) {
      return "Error: Permission denied. You may not have access to this calendar.";
    }
    if (message.includes("429")) {
      return "Error: Rate limit exceeded. Please wait before making more requests.";
    }

    return `Error: ${message}`;
  }

  return `Error: Unexpected error occurred: ${String(error)}`;
}
