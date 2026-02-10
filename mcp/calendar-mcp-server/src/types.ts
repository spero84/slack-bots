// Response format enum
export enum ResponseFormat {
  MARKDOWN = "markdown",
  JSON = "json",
}

// Calendar event interface
export interface CalendarEvent {
  [key: string]: unknown;
  id: string;
  summary: string;
  description?: string;
  location?: string;
  start: EventDateTime;
  end: EventDateTime;
  status: string;
  htmlLink?: string;
  creator?: {
    email?: string;
    displayName?: string;
  };
  organizer?: {
    email?: string;
    displayName?: string;
  };
  attendees?: Attendee[];
  recurrence?: string[];
  recurringEventId?: string;
}

// Event date/time
export interface EventDateTime {
  dateTime?: string;
  date?: string;
  timeZone?: string;
}

// Attendee interface
export interface Attendee {
  email: string;
  displayName?: string;
  responseStatus?: string;
  self?: boolean;
  organizer?: boolean;
}

// Calendar interface
export interface Calendar {
  id: string;
  summary: string;
  description?: string;
  timeZone?: string;
  primary?: boolean;
  accessRole?: string;
  backgroundColor?: string;
  foregroundColor?: string;
}

// List events result
export interface ListEventsResult {
  [key: string]: unknown;
  total: number;
  count: number;
  events: CalendarEvent[];
  nextPageToken?: string;
  has_more: boolean;
}

// Create event params
export interface CreateEventParams {
  summary: string;
  description?: string;
  location?: string;
  start: EventDateTime;
  end: EventDateTime;
  attendees?: string[];
  calendarId?: string;
  timeZone?: string;
}

// Update event params
export interface UpdateEventParams {
  eventId: string;
  calendarId?: string;
  summary?: string;
  description?: string;
  location?: string;
  start?: EventDateTime;
  end?: EventDateTime;
  attendees?: string[];
}
