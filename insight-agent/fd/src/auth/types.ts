export interface UserResponse {
	username: string;
	email: string;
	groups: string[];
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
