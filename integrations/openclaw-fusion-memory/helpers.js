export const DEFAULT_BASE_URL = "http://127.0.0.1:8765";
export const DEFAULT_TIMEOUT_MS = 1500;
const MIN_TIMEOUT_MS = 100;
const MAX_TIMEOUT_MS = 2000;

export function normalizeBaseUrl(value) {
  return String(value || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

export function normalizeTimeoutMs(value) {
  const timeoutMs = Number(value);
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
    return DEFAULT_TIMEOUT_MS;
  }
  return Math.min(Math.max(timeoutMs, MIN_TIMEOUT_MS), MAX_TIMEOUT_MS);
}

export function safeFailure(_error) {
  return {
    content: [
      {
        type: "text",
        text: "Fusion Memory is not available. Continue without memory, then run fusion-memory doctor.",
      },
    ],
  };
}
