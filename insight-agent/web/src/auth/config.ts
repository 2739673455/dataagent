export interface OAuthClientConfig {
  clientId: string;
  authApiBaseUrl: string;
  authWebBaseUrl: string;
  authApiPaths: {
    authorize: string;
    token: string;
    introspection: string;
    logout: string;
    userinfo: string;
  };
  baseUrl: string;
  authCallbackPath: string;
  storagePrefix: string;
  tokenStorageKey: string;
}

let _config: OAuthClientConfig | null = null;

export function configureClient(config: OAuthClientConfig): void {
  _config = Object.freeze({ ...config });
}

export function getConfig(): OAuthClientConfig {
  if (!_config) {
    throw new Error(
      "OAuth client not configured. Call configureClient() before using any auth functions."
    );
  }
  return _config;
}
