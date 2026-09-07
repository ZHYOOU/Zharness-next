export const DEFAULT_API_URL = "http://localhost:2024";
export const DEFAULT_ASSISTANT_ID = "lead_agent";

export function resolveApiUrl(apiUrl: string): string {
  if (!apiUrl.startsWith("/")) {
    return apiUrl;
  }

  // Resolve same-origin proxy paths against the address used by the browser. / 根据浏览器实际访问地址解析同源代理路径。
  const origin =
    typeof window === "undefined"
      ? "http://localhost:2026"
      : window.location.origin;
  return new URL(apiUrl, origin).toString().replace(/\/$/, "");
}
