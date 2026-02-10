import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { ResponseFormat } from "../types.js";
import {
  ListLabelsSchema,
  ListLabelsInput,
  ModifyLabelsSchema,
  ModifyLabelsInput,
} from "../schemas/index.js";
import { listLabels, modifyLabels, handleGmailError } from "../services/gmail.js";

/**
 * Register label-related tools
 */
export function registerLabelTools(server: McpServer): void {
  // List labels tool
  server.registerTool(
    "gmail_list_labels",
    {
      title: "List Gmail Labels",
      description: `List all labels in the Gmail account.

Returns both system labels (INBOX, SENT, SPAM, etc.) and user-created labels.

Args:
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  List of all labels with their IDs, names, and types.`,
      inputSchema: ListLabelsSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (params: ListLabelsInput) => {
      try {
        const labels = await listLabels();

        let textContent: string;

        if (params.response_format === ResponseFormat.MARKDOWN) {
          const systemLabels = labels.filter((l) => l.type === "system");
          const userLabels = labels.filter((l) => l.type === "user");

          const lines = ["# Gmail Labels", ""];

          if (systemLabels.length > 0) {
            lines.push("## System Labels");
            lines.push("");
            for (const label of systemLabels) {
              lines.push(`- **${label.name}** (ID: ${label.id})`);
            }
            lines.push("");
          }

          if (userLabels.length > 0) {
            lines.push("## User Labels");
            lines.push("");
            for (const label of userLabels) {
              lines.push(`- **${label.name}** (ID: ${label.id})`);
            }
          }

          textContent = lines.join("\n");
        } else {
          textContent = JSON.stringify({ labels }, null, 2);
        }

        return {
          content: [{ type: "text", text: textContent }],
          structuredContent: { labels },
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleGmailError(error) }],
        };
      }
    }
  );

  // Modify labels tool
  server.registerTool(
    "gmail_modify_labels",
    {
      title: "Modify Message Labels",
      description: `Add or remove labels from a message.

Use this to organize emails by adding/removing labels, marking as read/unread,
archiving (remove INBOX label), or starring messages.

Common label IDs:
- INBOX - Inbox
- UNREAD - Mark as unread
- STARRED - Starred
- IMPORTANT - Important
- SPAM - Spam
- TRASH - Trash

Args:
  - message_id (string): ID of the message to modify
  - add_labels (string[]): Label IDs to add
  - remove_labels (string[]): Label IDs to remove

Returns:
  Updated list of labels on the message.`,
      inputSchema: ModifyLabelsSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async (params: ModifyLabelsInput) => {
      try {
        const updatedLabels = await modifyLabels(
          params.message_id,
          params.add_labels,
          params.remove_labels
        );

        const textContent = [
          "# Labels Modified Successfully",
          "",
          `**Message ID**: ${params.message_id}`,
          "",
          "**Current Labels**:",
          updatedLabels.map((l) => `- ${l}`).join("\n"),
        ].join("\n");

        return {
          content: [{ type: "text", text: textContent }],
          structuredContent: { message_id: params.message_id, labels: updatedLabels },
        };
      } catch (error) {
        return {
          content: [{ type: "text", text: handleGmailError(error) }],
        };
      }
    }
  );
}
