// Gmail API Base URL
export const GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1";

// Character limit for responses
export const CHARACTER_LIMIT = 25000;

// Default pagination limit
export const DEFAULT_LIMIT = 20;
export const MAX_LIMIT = 100;

// Gmail API Scopes
export const GMAIL_SCOPES = [
  "https://www.googleapis.com/auth/gmail.readonly",
  "https://www.googleapis.com/auth/gmail.send",
  "https://www.googleapis.com/auth/gmail.compose",
  "https://www.googleapis.com/auth/gmail.modify",
];

// Token file path
export const TOKEN_PATH = "gmail_token.json";
export const CREDENTIALS_PATH = "credentials.json";
