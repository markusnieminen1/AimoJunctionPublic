/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "true" starts the MSW mock API in the browser; "false" proxies /api to the live FastAPI backend. */
  readonly VITE_USE_MOCKS?: string;
  /** Optional public Telegram bot handle (without @) for deep-link fallbacks when the backend omits bot_username. */
  readonly VITE_TELEGRAM_BOT_USERNAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
