export interface OAuthClientConfig {
  clientId: string;
  authApiBaseUrl: string;
  authApiPaths: {
    authorize: string;
    token: string;
    introspection: string;
  };
  baseUrl: string;
  authCallbackPath: string;
  storagePrefix: string;
  tokenStorageKey: string;
}

export interface PendingAuthorizationRequest {
  clientId: string;
  redirectUri: string;
  returnTo: string;
  state: string;
  codeVerifier: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface IntrospectionResponse {
  active: boolean;
  sub?: number;
  exp?: number;
  scope?: string[];
}
