// Response format enum
export enum ResponseFormat {
  MARKDOWN = "markdown",
  JSON = "json",
}

// Email message interface
export interface EmailMessage {
  [key: string]: unknown;
  id: string;
  threadId: string;
  snippet: string;
  subject: string;
  from: string;
  to: string;
  date: string;
  labels: string[];
  body?: string;
  attachments?: AttachmentInfo[];
}

// Attachment info interface
export interface AttachmentInfo {
  filename: string;
  mimeType: string;
  size: number;
  attachmentId: string;
}

// Search result interface
export interface SearchResult {
  [key: string]: unknown;
  total: number;
  count: number;
  messages: EmailMessage[];
  nextPageToken?: string;
  has_more: boolean;
}

// Send email params
export interface SendEmailParams {
  to: string;
  subject: string;
  body: string;
  cc?: string;
  bcc?: string;
  isHtml?: boolean;
}

// Draft email params
export interface DraftEmailParams {
  to: string;
  subject: string;
  body: string;
  cc?: string;
  bcc?: string;
  isHtml?: boolean;
  threadId?: string;
  inReplyTo?: string;
  references?: string;
}

// Draft result interface
export interface DraftResult {
  [key: string]: unknown;
  id: string;
  messageId: string;
  threadId: string;
}

// Drafts list result interface
export interface DraftsListResult {
  [key: string]: unknown;
  drafts: Array<{ id: string; subject: string; to: string }>;
  nextPageToken?: string;
}

// Gmail API response types
export interface GmailMessageResponse {
  id: string;
  threadId: string;
  snippet: string;
  labelIds?: string[];
  payload?: {
    headers?: Array<{ name: string; value: string }>;
    mimeType?: string;
    body?: { data?: string; size?: number };
    parts?: GmailMessagePart[];
  };
  internalDate?: string;
}

export interface GmailMessagePart {
  mimeType?: string;
  filename?: string;
  body?: { data?: string; size?: number; attachmentId?: string };
  parts?: GmailMessagePart[];
}

export interface GmailListResponse {
  messages?: Array<{ id: string; threadId: string }>;
  nextPageToken?: string;
  resultSizeEstimate?: number;
}

// Thread result interface
export interface ThreadResult {
  [key: string]: unknown;
  threadId: string;
  messageCount: number;
  messages: EmailMessage[];
}
