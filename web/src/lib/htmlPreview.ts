const BLOCKED_ELEMENTS = [
  "script",
  "iframe",
  "object",
  "embed",
  "link",
  "base",
  "form",
  "input",
  "button",
  "video",
  "audio",
  "source",
  "track",
  "portal",
].join(",");

const URL_ATTRIBUTES = new Set([
  "action",
  "background",
  "cite",
  "data",
  "formaction",
  "href",
  "ping",
  "poster",
  "src",
  "srcdoc",
  "srcset",
  "xlink:href",
]);

const NETWORK_CSS_PATTERN = /(?:@import|url\s*\(|image-set\s*\()/i;
const PREVIEW_CSP = [
  "default-src 'none'",
  "script-src 'none'",
  "connect-src 'none'",
  "img-src data:",
  "font-src data:",
  "style-src 'unsafe-inline'",
  "media-src 'none'",
  "object-src 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "form-action 'none'",
  "base-uri 'none'",
  "navigate-to 'none'",
].join("; ");

export function sanitizeHtmlForPreview(source: string): string {
  const document = new DOMParser().parseFromString(source, "text/html");
  document.querySelectorAll(BLOCKED_ELEMENTS).forEach((element) => {
    element.remove();
  });
  document.querySelectorAll("meta[http-equiv]").forEach((element) => {
    element.remove();
  });

  document.querySelectorAll("*").forEach((element) => {
    for (const attribute of Array.from(element.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || URL_ATTRIBUTES.has(name)) {
        element.removeAttribute(attribute.name);
        continue;
      }
      if (name === "style" && NETWORK_CSS_PATTERN.test(attribute.value)) {
        element.removeAttribute(attribute.name);
      }
    }
  });

  document.querySelectorAll("style").forEach((element) => {
    if (NETWORK_CSS_PATTERN.test(element.textContent ?? "")) element.remove();
  });

  const policy = document.createElement("meta");
  policy.httpEquiv = "Content-Security-Policy";
  policy.content = PREVIEW_CSP;
  document.head.prepend(policy);
  return `<!doctype html>\n${document.documentElement.outerHTML}`;
}
