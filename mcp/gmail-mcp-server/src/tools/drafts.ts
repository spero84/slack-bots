import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { ResponseFormat, DraftsListResult } from "../types.js";
import { CHARACTER_LIMIT } from "../constants.js";
import {
  CreateDraftSchema,
  CreateDraftInput,
  CreateReplyDraftSchema,
  CreateReplyDraftInput,
  ListDraftsSchema,
  ListDraftsInput,
} from "../schemas/index.js";
import {
  createDraft,
  createReplyDraft,
  listDrafts,
  handleGmailError,
} from "../services/gmail.js";

/**
 * Register draft-related tools
 */
export function registerDraftTools(server: McpServer): void {
  // Create draft tool
  server.registerTool(
    "gmail_create_draft",
    {
      title: "Create Gmail Draft",
      description: `Create a new email draft (does NOT send the email).

Use this to save an email as a draft for later review and sending.

Args:
  - to (string): Recipient email address(es), comma-separated for multiple
  - subject (string): Email subject line
  - body (string): Email body content
  - cc (string, optional): CC recipients, comma-separated
  - bcc (string, optional): BCC recipients, comma-separated
  - is_html (boolean): Whether body is HTML (default: false)

Returns:
  Draft ID, message ID, and thread ID.`,
      inputSchema: CreateDraftSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async (params: CreateDraftInput) => {
      try {
        const result = await createDraft({
          to: params.to,
          subject: params.subject,
          body: params.body,
          cc: params.cc,
          bcc: params.bcc,
          isHtml: params.is_html,
        });

        const textContent = [
          "# Draft Created Successfully",
          "",
          `- **Draft ID**: ${result.id}`,
          `- **Message ID**: ${result.messageId}`,
          `- **Thread ID**: ${result.threadId}`,
          `- **To**: ${params.to}`,
          `- **Subject**: ${params.subject}`,
          "",
          "*Note: This is a draft and has NOT been sent. Go to Gmail Drafts to review and send.*",
        ].join("\n");

        return {
          content: [{ type: "text" as const, text: textContent }],
          structuredContent: result,
        };
      } catch (error) {
        return {
          content: [{ type: "text" as const, text: handleGmailError(error) }],
        };
      }
    }
  );

  // Create reply draft tool
  server.registerTool(
    "gmail_create_reply_draft",
    {
      title: "Create Gmail Reply Draft",
      description: `Create a draft reply to an existing email (does NOT send the email).

The draft will be added to the same thread as the original message.

Args:
  - message_id (string): ID of the message to reply to
  - body (string): Reply body content
  - reply_all (boolean): Reply to all recipients (default: false)
  - is_html (boolean): Whether body is HTML (default: false)

Returns:
  Draft ID, message ID, and thread ID.`,
      inputSchema: CreateReplyDraftSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async (params: CreateReplyDraftInput) => {
      try {
        const result = await createReplyDraft(
          params.message_id,
          params.body,
          params.reply_all,
          params.is_html
        );

        const textContent = [
          "# Reply Draft Created Successfully",
          "",
          `- **Draft ID**: ${result.id}`,
          `- **Message ID**: ${result.messageId}`,
          `- **Thread ID**: ${result.threadId}`,
          "",
          "*Note: This is a draft and has NOT been sent. Go to Gmail Drafts to review and send.*",
        ].join("\n");

        return {
          content: [{ type: "text" as const, text: textContent }],
          structuredContent: result,
        };
      } catch (error) {
        return {
          content: [{ type: "text" as const, text: handleGmailError(error) }],
        };
      }
    }
  );

  // List drafts tool
  server.registerTool(
    "gmail_list_drafts",
    {
      title: "List Gmail Drafts",
      description: `List all email drafts in Gmail.

Args:
  - max_results (number): Maximum drafts to return (1-100, default: 20)
  - page_token (string): Pagination token from previous response
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  List of drafts with ID, subject, and recipient.`,
      inputSchema: ListDraftsSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: true,
      },
    },
    async (params: ListDraftsInput) => {
      try {
        const result: DraftsListResult = await listDrafts(params.max_results, params.page_token);

        if (result.drafts.length === 0) {
          return {
            content: [
              {
                type: "text" as const,
                text: "No drafts found in Gmail.",
              },
            ],
          };
        }

        let textContent: string;

        if (params.response_format === ResponseFormat.MARKDOWN) {
          const lines = [
            "# Gmail Drafts",
            "",
            `**Found**: ${result.drafts.length} drafts`,
            "",
            "| Draft ID | Subject | To |",
            "|----------|---------|-----|",
          ];

          for (const draft of result.drafts) {
            lines.push(`| ${draft.id} | ${draft.subject} | ${draft.to} |`);
          }

          if (result.nextPageToken) {
            lines.push("");
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
            "\n\n... [Response truncated. Use smaller max_results.]";
        }

        return {
          content: [{ type: "text" as const, text: textContent }],
          structuredContent: result,
        };
      } catch (error) {
        return {
          content: [{ type: "text" as const, text: handleGmailError(error) }],
        };
      }
    }
  );
}
