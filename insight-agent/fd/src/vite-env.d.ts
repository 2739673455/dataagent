/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_API_BASE_URL?: string;
  readonly VITE_AUTH_API_BASE_URL?: string;
  readonly VITE_AUTH_APP_BASE_URL?: string;
  readonly VITE_AUTH_CLIENT_ID?: string;
  readonly VITE_APP_WS_BASE_URL?: string;
}

interface ImportMeta {
	readonly env: ImportMetaEnv;
}
