#!/usr/bin/env node
/**
 * Google Calendar OAuth Authentication Helper
 *
 * Run this script to authenticate with Google Calendar and save the access token.
 * Usage: npm run auth
 */

import * as readline from "readline";
import { getAuthUrl, exchangeCodeForTokens } from "./services/calendar.js";

async function main(): Promise<void> {
  console.log("\n=== Google Calendar OAuth Authentication ===\n");

  try {
    const authUrl = getAuthUrl();

    console.log("1. Open this URL in your browser:\n");
    console.log(authUrl);
    console.log("\n2. Sign in and authorize the application");
    console.log("3. Copy the authorization code and paste it below\n");

    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });

    rl.question("Enter the authorization code: ", async (code) => {
      rl.close();

      if (!code.trim()) {
        console.error("Error: No code provided");
        process.exit(1);
      }

      try {
        await exchangeCodeForTokens(code.trim());
        console.log("\n✓ Authentication successful!");
        console.log("You can now use the Google Calendar MCP server.\n");
        process.exit(0);
      } catch (error) {
        console.error("\nError exchanging code for tokens:", error);
        process.exit(1);
      }
    });
  } catch (error) {
    console.error("Error:", error);
    process.exit(1);
  }
}

main();
