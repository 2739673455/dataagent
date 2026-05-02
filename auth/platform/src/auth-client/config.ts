import type { OAuthClientConfig } from "./types";

let _config: OAuthClientConfig | null = null;

export function configureClient(config: OAuthClientConfig): void {
	_config = Object.freeze({ ...config });
}

export function getConfig(): OAuthClientConfig {
	if (!_config) {
		throw new Error(
			"OAuth client not configured. Call configureClient() before using any @/auth-client functions.",
		);
	}
	return _config;
}
