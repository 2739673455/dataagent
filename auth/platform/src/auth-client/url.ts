// 去除开头斜杠
function rmLeadingSlash(value: string): string {
	return value.replace(/^\/+/, "");
}

// 补上尾部斜杠
function ensureTrailingSlash(value: string): string {
	return value.endsWith("/") ? value : `${value}/`;
}

// 连接基础地址与路径
export function joinUrl(baseUrl: string, pathname: string): string {
	const isAbsoluteHttpUrl = /^https?:\/\//.test(baseUrl);

	const normalizedBaseUrl = ensureTrailingSlash(baseUrl);
	const normalizedPathname = rmLeadingSlash(pathname);

	return isAbsoluteHttpUrl
		? new URL(normalizedPathname, normalizedBaseUrl).toString()
		: `${normalizedBaseUrl}${normalizedPathname}`;
}
