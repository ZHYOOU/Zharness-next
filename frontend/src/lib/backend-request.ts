import { getApiKey } from "@/lib/api-key";

export async function backendRequest(
  base: string,
  authScheme: string | null,
  path: string,
  init?: RequestInit,
) {
  const headers = new Headers(init?.headers);
  if (init?.body) headers.set("Content-Type", "application/json");
  const key = getApiKey();
  if (key) headers.set("X-Api-Key", key);
  const scheme = authScheme || process.env.NEXT_PUBLIC_AUTH_SCHEME;
  if (scheme) headers.set("X-Auth-Scheme", scheme);
  const response = await fetch(`${base.replace(/\/$/, "")}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(data?.error || `后端请求失败（${response.status}）`);
  }
  if (!data) throw new Error("后端返回了无效数据");
  return data;
}
