import { z } from "zod";
import { ResponseFormat } from "../types.js";
import { DEFAULT_LIMIT, MAX_LIMIT } from "../constants.js";

// Common pagination schema
export const PaginationSchema = z.object({
  max_results: z
    .number()
    .int()
    .min(1)
    .max(MAX_LIMIT)
    .default(DEFAULT_LIMIT)
    .describe("Maximum number of messages to return (1-100)"),
  page_token: z
    .string()
    .optional()
    .describe("Page token for pagination from previous response"),
});

// Common response format schema
export const ResponseFormatSchema = z.object({
  response_format: z
    .nativeEnum(ResponseFormat)
    .default(ResponseFormat.MARKDOWN)
    .describe("Output format: 'markdown' for human-readable or 'json' for structured data"),
});

// Search messages schema
export const SearchMessagesSchema = z
  .object({
    query: z
      .string()
      .min(1, "Query is required")
      .max(500, "Query must not exceed 500 characters")
      .describe(
        "Gmail search query (e.g., 'from:user@example.com', 'subject:meeting', 'newer_than:7d')"
      ),
    include_body: z
      .boolean()
      .default(false)
      .describe("Whether to include full email body in results"),
  })
  .merge(PaginationSchema)
  .merge(ResponseFormatSchema)
  .strict();

export type SearchMessagesInput = z.infer<typeof SearchMessagesSchema>;

// Get message schema
export const GetMessageSchema = z
  .object({
    message_id: z
      .string()
      .min(1, "Message ID is required")
      .describe("The ID of the message to retrieve"),
    include_attachments: z
      .boolean()
      .default(true)
      .describe("Whether to include attachment information"),
  })
  .merge(ResponseFormatSchema)
  .strict();

export type GetMessageInput = z.infer<typeof GetMessageSchema>;

// Send message schema
export const SendMessageSchema = z
  .object({
    to: z
      .string()
      .min(1, "Recipient is required")
      .describe("Recipient email address(es), comma-separated for multiple"),
    subject: z
      .string()
      .min(1, "Subject is required")
      .max(998, "Subject must not exceed 998 characters")
      .describe("Email subject line"),
    body: z
      .string()
      .min(1, "Body is required")
      .describe("Email body content"),
    cc: z
      .string()
      .optional()
      .describe("CC recipient email address(es), comma-separated"),
    bcc: z
      .string()
      .optional()
      .describe("BCC recipient email address(es), comma-separated"),
    is_html: z
      .boolean()
      .default(false)
      .describe("Whether the body is HTML formatted"),
  })
  .strict();

export type SendMessageInput = z.infer<typeof SendMessageSchema>;

// Reply to message schema
export const ReplyMessageSchema = z
  .object({
    message_id: z
      .string()
      .min(1, "Message ID is required")
      .describe("The ID of the message to reply to"),
    body: z
      .string()
      .min(1, "Body is required")
      .describe("Reply body content"),
    reply_all: z
      .boolean()
      .default(false)
      .describe("Whether to reply to all recipients"),
    is_html: z
      .boolean()
      .default(false)
      .describe("Whether the body is HTML formatted"),
  })
  .strict();

export type ReplyMessageInput = z.infer<typeof ReplyMessageSchema>;

// Create draft schema
export const CreateDraftSchema = z
  .object({
    to: z
      .string()
      .min(1, "Recipient is required")
      .describe("Recipient email address(es), comma-separated for multiple"),
    subject: z
      .string()
      .min(1, "Subject is required")
      .max(998, "Subject must not exceed 998 characters")
      .describe("Email subject line"),
    body: z
      .string()
      .min(1, "Body is required")
      .describe("Email body content"),
    cc: z
      .string()
      .optional()
      .describe("CC recipient email address(es), comma-separated"),
    bcc: z
      .string()
      .optional()
      .describe("BCC recipient email address(es), comma-separated"),
    is_html: z
      .boolean()
      .default(false)
      .describe("Whether the body is HTML formatted"),
  })
  .strict();

export type CreateDraftInput = z.infer<typeof CreateDraftSchema>;

// Create reply draft schema
export const CreateReplyDraftSchema = z
  .object({
    message_id: z
      .string()
      .min(1, "Message ID is required")
      .describe("The ID of the message to reply to"),
    body: z
      .string()
      .min(1, "Body is required")
      .describe("Reply body content"),
    reply_all: z
      .boolean()
      .default(false)
      .describe("Whether to reply to all recipients"),
    is_html: z
      .boolean()
      .default(false)
      .describe("Whether the body is HTML formatted"),
  })
  .strict();

export type CreateReplyDraftInput = z.infer<typeof CreateReplyDraftSchema>;

// List drafts schema
export const ListDraftsSchema = z
  .object({})
  .merge(PaginationSchema)
  .merge(ResponseFormatSchema)
  .strict();

export type ListDraftsInput = z.infer<typeof ListDraftsSchema>;

// Get thread schema
export const GetThreadSchema = z
  .object({
    thread_id: z
      .string()
      .min(1, "Thread ID is required")
      .describe("The ID of the thread to retrieve"),
  })
  .merge(ResponseFormatSchema)
  .strict();

export type GetThreadInput = z.infer<typeof GetThreadSchema>;

// List labels schema
export const ListLabelsSchema = z
  .object({})
  .merge(ResponseFormatSchema)
  .strict();

export type ListLabelsInput = z.infer<typeof ListLabelsSchema>;

// Modify labels schema
export const ModifyLabelsSchema = z
  .object({
    message_id: z
      .string()
      .min(1, "Message ID is required")
      .describe("The ID of the message to modify"),
    add_labels: z
      .array(z.string())
      .optional()
      .describe("Label IDs to add to the message"),
    remove_labels: z
      .array(z.string())
      .optional()
      .describe("Label IDs to remove from the message"),
  })
  .strict();

export type ModifyLabelsInput = z.infer<typeof ModifyLabelsSchema>;
