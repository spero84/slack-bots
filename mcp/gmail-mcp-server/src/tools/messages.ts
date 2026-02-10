import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { ResponseFormat, EmailMessage } from "../types.js";
import { CHARACTER_LIMIT } from "../constants.js";
import {
  SearchMessagesSchema,
  SearchMessagesInput,
  GetMessageSchema,
  GetMessageInput,
  SendMessageSchema,
  SendMessageInput,
  ReplyMessageSchema,
  ReplyMessageInput,
} from "../schemas/index.js";
import {
  searchMessages,
  getMessage,
  sendEmail,
  replyToEmail,
  handleGmailError,
} from "../services/gmail.js";

/**
 * Format email message to markdown
 */
function formatEmailToMarkdown(email: EmailMessage, includeBody: boolean): string {
  const lines: string[] = [
    `## ${email.subject || "(No Subject)"}`,
    "",
    `- **From**: ${email.from}`,
    `- **To**: ${email.to}`,
    `- **Date**: ${email.date}`,
    `- **ID**: ${email.id}`,
  ];

  if (email.labels.length > 0) {
    lines.push(`- **Labels**: ${email.labels.join(", ")}`);
  }

  if (email.attachments && email.attachments.length > 0) {
    lines.push(`- **Attachments**: ${email.attachments.map((a) => a.filename).join(", ")}`);
  }

  lines.push("");

  if (includeBody && email.body) {
    lines.push("### Content");
    lines.push("");
    lines.push(email.body);
  } else {
    lines.push(`> ${email.snippet}`);
  }

  return lines.join("\n");
}

/**
 * Register message-related tools
 */
export function registerMessageTools(server: McpServer): void {
  // Search messages tool
  server.registerTool(
    "gmail_search_messages",
    {
      title: "Search Gmail Messages",
      description: `Search for emails in Gmail using Gmail search syntax.

Supports Gmail search operators:
- from:user@example.com - Messages from specific sender
- to:user@example.com - Messages to specific recipient
- subject:keyword - Messages with keyword in subject
- has:attachment - Messages with attachments
- is:unread / is:read - Unread or read messages
- is:starred - Starred messages
- newer_than:7d / older_than:30d - Date filters (d=days, m=months, y=years)
- after:2024/01/01 / before:2024/12/31 - Specific date range
- label:inbox / label:sent - Messages with specific label
- filename:pdf - Messages with specific attachment type

Examples:
- "from:boss@company.com newer_than:7d" - Recent emails from boss
- "subject:meeting has:attachment" - Meeting emails with attachments
- "인력 소싱 newer_than:7d" - Staffing related emails from last 7 days

Args:
  - query (string): Gmail search query
  - max_results (number): Maximum messages to return (1-100, default: 20)
  - include_body (boolean): Include full email body (default: false)
  - page_token (string): Pagination token from previous response
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  List of matching emails with subject, sender, date, and snippet/body.`,
      inputSchema: SearchMessagesSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: true,
      },
    },
    async (params: SearchMessagesInput) => {
      try {
        const result = await searchMessages(
          params.query,
          params.max_results,
          params.include_body,
          params.page_token
        );

        if (result.messages.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: `No messages found matching query: "${params.query}"`,
              },
            ],
          };
        }

        let textContent: string;

        if (params.response_format === ResponseFormat.MARKDOWN) {
          const lines = [
            `# Gmail Search Results`,
            "",
            `**Query**: ${params.query}`,
            `**Found**: ${result.total} messages (showing ${result.count})`,
            "",
          ];

          for (const email of result.messages) {
            lines.push(formatEmailToMarkdown(email, params.include_body));
            lines.push("");
            lines.push("---");
            lines.push("");
          }

          if (result.has_more) {
            lines.push(`*More results available. Use page_token: "${result.nextPageToken}"*`);
          }

          textContent = lines.join("\n");
        } else {
          textContent = JSON.stringify(result, null, 2);
        }

        // Truncate if too long
        if (textContent.length > CHARACTER_LIMIT) {
          textContent =
            textContent.substring(0, CHARACTER_LIMIT) +
            "\n\n... [Response truncated. Use smaller max_results or add filters.]";
        }

        return {
          content: [{ type: "text", text: textContent }],
          structuredContent: result,
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleGmailError(error) }],
        };
      }
    }
  );

  // Get single message tool
  server.registerTool(
    "gmail_get_message",
    {
      title: "Get Gmail Message",
      description: `Get a specific email message by ID with full content.

Use this to read the complete email including full body text and attachment information.

Args:
  - message_id (string): The Gmail message ID
  - include_attachments (boolean): Include attachment info (default: true)
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  Full email message with subject, sender, recipients, date, body, and attachments.`,
      inputSchema: GetMessageSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: true,
      },
    },
    async (params: GetMessageInput) => {
      try {
        const email = await getMessage(params.message_id);

        let textContent: string;

        if (params.response_format === ResponseFormat.MARKDOWN) {
          textContent = formatEmailToMarkdown(email, true);
        } else {
          textContent = JSON.stringify(email, null, 2);
        }

        return {
          content: [{ type: "text", text: textContent }],
          structuredContent: email,
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleGmailError(error) }],
        };
      }
    }
  );

  // Send message tool
  server.registerTool(
    "gmail_send_message",
    {
      title: "Send Gmail Message",
      description: `Send a new email message.

Args:
  - to (string): Recipient email address(es), comma-separated for multiple
  - subject (string): Email subject line
  - body (string): Email body content
  - cc (string, optional): CC recipients, comma-separated
  - bcc (string, optional): BCC recipients, comma-separated
  - is_html (boolean): Whether body is HTML (default: false)

Returns:
  Confirmation with message ID and thread ID.`,
      inputSchema: SendMessageSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async (params: SendMessageInput) => {
      try {
        const result = await sendEmail({
          to: params.to,
          subject: params.subject,
          body: params.body,
          cc: params.cc,
          bcc: params.bcc,
          isHtml: params.is_html,
        });

        const textContent = [
          "# Email Sent Successfully",
          "",
          `- **Message ID**: ${result.id}`,
          `- **Thread ID**: ${result.threadId}`,
          `- **To**: ${params.to}`,
          `- **Subject**: ${params.subject}`,
        ].join("\n");

        return {
          content: [{ type: "text", text: textContent }],
          structuredContent: result,
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleGmailError(error) }],
        };
      }
    }
  );

  // Reply to message tool
  server.registerTool(
    "gmail_reply_message",
    {
      title: "Reply to Gmail Message",
      description: `Reply to an existing email message.

The reply will be added to the same thread as the original message.

Args:
  - message_id (string): ID of the message to reply to
  - body (string): Reply body content
  - reply_all (boolean): Reply to all recipients (default: false)
  - is_html (boolean): Whether body is HTML (default: false)

Returns:
  Confirmation with message ID and thread ID.`,
      inputSchema: ReplyMessageSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async (params: ReplyMessageInput) => {
      try {
        const result = await replyToEmail(
          params.message_id,
          params.body,
          params.reply_all,
          params.is_html
        );

        const textContent = [
          "# Reply Sent Successfully",
          "",
          `- **Message ID**: ${result.id}`,
          `- **Thread ID**: ${result.threadId}`,
        ].join("\n");

        return {
          content: [{ type: "text", text: textContent }],
          structuredContent: result,
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleGmailError(error) }],
        };
      }
    }
  );
}
