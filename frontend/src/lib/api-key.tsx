export function getApiKey(): string | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem("lg:chat:apiKey") ?? null;
  } catch {
    // Ignore unavailable browser storage. / 忽略不可用的浏览器存储。
  }

  return null;
}
