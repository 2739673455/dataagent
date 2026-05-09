export interface UserResponse {
  username: string;
  email: string;
  roles: string[];
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

export interface PendingAuthorizationRequest {
  clientId: string;
  redirectUri: string;
  returnTo: string;
  state: string;
  codeVerifier: string;
}
