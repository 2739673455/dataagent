export function getAppWsBaseUrl() {
	const url = new URL(window.location.origin);
	url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
	return url.toString().replace(/\/+$/, "");
}
