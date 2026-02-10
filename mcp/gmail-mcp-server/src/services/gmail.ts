import { google, gmail_v1 } from "googleapis";
import { OAuth2Client } from "google-auth-library";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import {
  EmailMessage,
  GmailMessageResponse,
  GmailMessagePart,
  AttachmentInfo,
  SearchResult,
  SendEmailParams,
  DraftEmailParams,
  DraftResult,
  DraftsListResult,
} from "../types.js";
import { GMAIL_SCOPES, TOKEN_PATH, CREDENTIALS_PATH } from "../constants.js";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let gmailClient: gmail_v1.Gmail | null = null;
let oAuth2Client: OAuth2Client | null = null;

/**
 * Get the credentials file path from environment or default location
 */
function getCredentialsPath(): string {
  return process.env.GMAIL_CREDENTIALS_PATH || path.join(__dirname, "..", "..", CREDENTIALS_PATH);
}

/**
 * Get the token file path from environment or default location
 */
function getTokenPath(): string {
  return process.env.GMAIL_TOKEN_PATH || path.join(__dirname, "..", "..", TOKEN_PATH);
}

/**
 * Initialize OAuth2 client and Gmail API
 */
export async function initializeGmailClient(): Promise<gmail_v1.Gmail> {
  if (gmailClient) {
    return gmailClient;
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
        "Please run 'npm run auth' to authenticate with Gmail first."
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

  gmailClient = google.gmail({ version: "v1", auth: oAuth2Client });
  return gmailClient;
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
    scope: GMAIL_SCOPES,
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
 * Parse email headers to extract common fields
 */
function parseHeaders(
  headers: gmail_v1.Schema$MessagePartHeader[] | undefined
): { subject: string; from: string; to: string; date: string; messageId: string; references: string } {
  const result = { subject: "", from: "", to: "", date: "", messageId: "", references: "" };

  if (!headers) return result;

  for (const header of headers) {
    if (!header.name || !header.value) continue;

    switch (header.name.toLowerCase()) {
      case "subject":
        result.subject = header.value;
        break;
      case "from":
        result.from = header.value;
        break;
      case "to":
        result.to = header.value;
        break;
      case "date":
        result.date = header.value;
        break;
      case "message-id":
        result.messageId = header.value;
        break;
      case "references":
        result.references = header.value;
        break;
    }
  }

  return result;
}

/**
 * Decode base64url encoded string
 */
function decodeBase64Url(data: string): string {
  const base64 = data.replace(/-/g, "+").replace(/_/g, "/");
  return Buffer.from(base64, "base64").toString("utf-8");
}

/**
 * Extract body from message parts
 */
function extractBody(payload: GmailMessageResponse["payload"]): string {
  if (!payload) return "";

  // Try to get body directly
  if (payload.body?.data) {
    return decodeBase64Url(payload.body.data);
  }

  // Search in parts
  if (payload.parts) {
    // Prefer text/plain, then text/html
    const textPart = findPartByMimeType(payload.parts, "text/plain");
    if (textPart?.body?.data) {
      return decodeBase64Url(textPart.body.data);
    }

    const htmlPart = findPartByMimeType(payload.parts, "text/html");
    if (htmlPart?.body?.data) {
      // Strip HTML tags for plain text display
      const html = decodeBase64Url(htmlPart.body.data);
      return html.replace(/<[^>]*>/g, "").replace(/&nbsp;/g, " ").trim();
    }
  }

  return "";
}

/**
 * Find message part by MIME type
 */
function findPartByMimeType(
  parts: GmailMessagePart[],
  mimeType: string
): GmailMessagePart | undefined {
  for (const part of parts) {
    if (part.mimeType === mimeType) {
      return part;
    }
    if (part.parts) {
      const found = findPartByMimeType(part.parts, mimeType);
      if (found) return found;
    }
  }
  return undefined;
}

/**
 * Extract attachment information from message parts
 */
function extractAttachments(payload: GmailMessageResponse["payload"]): AttachmentInfo[] {
  const attachments: AttachmentInfo[] = [];

  function processPartForAttachments(parts: GmailMessagePart[] | undefined): void {
    if (!parts) return;

    for (const part of parts) {
      if (part.filename && part.body?.attachmentId) {
        attachments.push({
          filename: part.filename,
          mimeType: part.mimeType || "application/octet-stream",
          size: part.body.size || 0,
          attachmentId: part.body.attachmentId,
        });
      }
      if (part.parts) {
        processPartForAttachments(part.parts);
      }
    }
  }

  processPartForAttachments(payload?.parts);
  return attachments;
}

/**
 * Convert Gmail API response to EmailMessage
 */
function toEmailMessage(message: GmailMessageResponse, includeBody: boolean): EmailMessage {
  const headers = parseHeaders(message.payload?.headers as gmail_v1.Schema$MessagePartHeader[] | undefined);
  const attachments = extractAttachments(message.payload);

  return {
    id: message.id,
    threadId: message.threadId,
    snippet: message.snippet,
    subject: headers.subject,
    from: headers.from,
    to: headers.to,
    date: headers.date,
    labels: message.labelIds || [],
    ...(includeBody ? { body: extractBody(message.payload) } : {}),
    ...(attachments.length > 0 ? { attachments } : {}),
  };
}

/**
 * Search messages in Gmail
 */
export async function searchMessages(
  query: string,
  maxResults: number,
  includeBody: boolean,
  pageToken?: string
): Promise<SearchResult> {
  const gmail = await initializeGmailClient();

  const listResponse = await gmail.users.messages.list({
    userId: "me",
    q: query,
    maxResults,
    pageToken,
  });

  const messageIds = listResponse.data.messages || [];
  const messages: EmailMessage[] = [];

  // Fetch details for each message
  for (const msg of messageIds) {
    const detail = await gmail.users.messages.get({
      userId: "me",
      id: msg.id!,
      format: includeBody ? "full" : "metadata",
      metadataHeaders: ["Subject", "From", "To", "Date"],
    });

    messages.push(toEmailMessage(detail.data as GmailMessageResponse, includeBody));
  }

  return {
    total: listResponse.data.resultSizeEstimate || messages.length,
    count: messages.length,
    messages,
    nextPageToken: listResponse.data.nextPageToken || undefined,
    has_more: !!listResponse.data.nextPageToken,
  };
}

/**
 * Get a single message by ID
 */
export async function getMessage(messageId: string): Promise<EmailMessage> {
  const gmail = await initializeGmailClient();

  const response = await gmail.users.messages.get({
    userId: "me",
    id: messageId,
    format: "full",
  });

  return toEmailMessage(response.data as GmailMessageResponse, true);
}

/**
 * Get message headers for reply (Message-ID, References)
 */
export async function getMessageHeaders(messageId: string): Promise<{ messageId: string; references: string; threadId: string }> {
  const gmail = await initializeGmailClient();

  const response = await gmail.users.messages.get({
    userId: "me",
    id: messageId,
    format: "metadata",
    metadataHeaders: ["Message-ID", "References"],
  });

  const headers = parseHeaders(response.data.payload?.headers);

  return {
    messageId: headers.messageId,
    references: headers.references,
    threadId: response.data.threadId || "",
  };
}

/**
 * Create MIME message for sending
 */
function createMimeMessage(params: SendEmailParams): string {
  const contentType = params.isHtml ? "text/html" : "text/plain";

  let message = [
    `To: ${params.to}`,
    params.cc ? `Cc: ${params.cc}` : "",
    params.bcc ? `Bcc: ${params.bcc}` : "",
    `Subject: ${params.subject}`,
    `MIME-Version: 1.0`,
    `Content-Type: ${contentType}; charset="UTF-8"`,
    "",
    params.body,
  ]
    .filter(Boolean)
    .join("\r\n");

  // Encode to base64url
  return Buffer.from(message)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

/**
 * Create MIME message for draft (with reply headers)
 */
function createDraftMimeMessage(params: DraftEmailParams): string {
  const contentType = params.isHtml ? "text/html" : "text/plain";

  const lines = [
    `To: ${params.to}`,
    params.cc ? `Cc: ${params.cc}` : "",
    params.bcc ? `Bcc: ${params.bcc}` : "",
    `Subject: ${params.subject}`,
    params.inReplyTo ? `In-Reply-To: ${params.inReplyTo}` : "",
    params.references ? `References: ${params.references}` : "",
    `MIME-Version: 1.0`,
    `Content-Type: ${contentType}; charset="UTF-8"`,
    "",
    params.body,
  ];

  const message = lines.filter(Boolean).join("\r\n");

  // Encode to base64url
  return Buffer.from(message)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

/**
 * Send an email
 */
export async function sendEmail(params: SendEmailParams): Promise<{ id: string; threadId: string }> {
  const gmail = await initializeGmailClient();

  const raw = createMimeMessage(params);

  const response = await gmail.users.messages.send({
    userId: "me",
    requestBody: { raw },
  });

  return {
    id: response.data.id!,
    threadId: response.data.threadId!,
  };
}

/**
 * Reply to an email
 */
export async function replyToEmail(
  messageId: string,
  body: string,
  replyAll: boolean,
  isHtml: boolean
): Promise<{ id: string; threadId: string }> {
  const gmail = await initializeGmailClient();

  // Get original message
  const original = await getMessage(messageId);

  // Build recipients
  let to = original.from;
  let cc: string | undefined;

  if (replyAll && original.to) {
    // Add original recipients except the sender
    const otherRecipients = original.to
      .split(",")
      .map((r) => r.trim())
      .filter((r) => !r.includes(original.from));
    if (otherRecipients.length > 0) {
      cc = otherRecipients.join(", ");
    }
  }

  // Build subject with Re: prefix
  const subject = original.subject.startsWith("Re:") ? original.subject : `Re: ${original.subject}`;

  const raw = createMimeMessage({
    to,
    subject,
    body,
    cc,
    isHtml,
  });

  const response = await gmail.users.messages.send({
    userId: "me",
    requestBody: {
      raw,
      threadId: original.threadId,
    },
  });

  return {
    id: response.data.id!,
    threadId: response.data.threadId!,
  };
}

/**
 * Create a new draft
 */
export async function createDraft(params: DraftEmailParams): Promise<DraftResult> {
  const gmail = await initializeGmailClient();

  const raw = createDraftMimeMessage(params);

  const response = await gmail.users.drafts.create({
    userId: "me",
    requestBody: {
      message: {
        raw,
        threadId: params.threadId,
      },
    },
  });

  return {
    id: response.data.id!,
    messageId: response.data.message?.id || "",
    threadId: response.data.message?.threadId || "",
  };
}

/**
 * Create a draft reply to an existing message
 */
export async function createReplyDraft(
  messageId: string,
  body: string,
  replyAll: boolean,
  isHtml: boolean
): Promise<DraftResult> {
  // Get original message details
  const original = await getMessage(messageId);
  const headers = await getMessageHeaders(messageId);

  // Build recipients
  let to = original.from;
  let cc: string | undefined;

  if (replyAll && original.to) {
    // Add original recipients except the sender
    const otherRecipients = original.to
      .split(",")
      .map((r) => r.trim())
      .filter((r) => !r.includes(original.from));
    if (otherRecipients.length > 0) {
      cc = otherRecipients.join(", ");
    }
  }

  // Build subject with Re: prefix
  const subject = original.subject.startsWith("Re:") ? original.subject : `Re: ${original.subject}`;

  // Build references header
  let references = headers.references ? `${headers.references} ${headers.messageId}` : headers.messageId;

  return createDraft({
    to,
    subject,
    body,
    cc,
    isHtml,
    threadId: original.threadId,
    inReplyTo: headers.messageId,
    references,
  });
}

/**
 * List all drafts
 */
export async function listDrafts(
  maxResults: number,
  pageToken?: string
): Promise<DraftsListResult> {
  const gmail = await initializeGmailClient();

  const response = await gmail.users.drafts.list({
    userId: "me",
    maxResults,
    pageToken,
  });

  const drafts: Array<{ id: string; subject: string; to: string }> = [];

  for (const draft of response.data.drafts || []) {
    if (draft.id && draft.message?.id) {
      const detail = await gmail.users.drafts.get({
        userId: "me",
        id: draft.id,
        format: "metadata",
      });

      const headers = parseHeaders(detail.data.message?.payload?.headers);
      drafts.push({
        id: draft.id,
        subject: headers.subject || "(No Subject)",
        to: headers.to || "",
      });
    }
  }

  return {
    drafts,
    nextPageToken: response.data.nextPageToken || undefined,
  };
}

/**
 * List all labels
 */
export async function listLabels(): Promise<Array<{ id: string; name: string; type: string }>> {
  const gmail = await initializeGmailClient();

  const response = await gmail.users.labels.list({
    userId: "me",
  });

  return (response.data.labels || []).map((label) => ({
    id: label.id!,
    name: label.name!,
    type: label.type || "user",
  }));
}

/**
 * Modify labels on a message
 */
export async function modifyLabels(
  messageId: string,
  addLabels?: string[],
  removeLabels?: string[]
): Promise<string[]> {
  const gmail = await initializeGmailClient();

  const response = await gmail.users.messages.modify({
    userId: "me",
    id: messageId,
    requestBody: {
      addLabelIds: addLabels,
      removeLabelIds: removeLabels,
    },
  });

  return response.data.labelIds || [];
}

/**
 * Handle API errors
 */
export function handleGmailError(error: unknown): string {
  if (error instanceof Error) {
    const message = error.message;

    if (message.includes("invalid_grant")) {
      return "Error: Authentication expired. Please run 'npm run auth' to re-authenticate.";
    }
    if (message.includes("Request had insufficient authentication scopes")) {
      return "Error: Insufficient permissions. Please re-authenticate with required scopes.";
    }
    if (message.includes("404")) {
      return "Error: Message not found. Please check the message ID is correct.";
    }
    if (message.includes("403")) {
      return "Error: Permission denied. You may not have access to this message.";
    }
    if (message.includes("429")) {
      return "Error: Rate limit exceeded. Please wait before making more requests.";
    }

    return `Error: ${message}`;
  }

  return `Error: Unexpected error occurred: ${String(error)}`;
}
