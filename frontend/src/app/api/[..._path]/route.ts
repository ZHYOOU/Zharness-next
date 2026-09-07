import { initApiPassthrough } from "langgraph-nextjs-api-passthrough";

// Proxy requests to the configured LangGraph server. / 将请求代理到配置的 LangGraph 服务器。
// See the upstream production guide for deployment details. / 部署细节请参阅上游生产指南。

export const { GET, POST, PUT, PATCH, DELETE, OPTIONS, runtime } =
  initApiPassthrough({
    apiUrl: process.env.LANGGRAPH_API_URL ?? "remove-me", // Use an invalid fallback until configured. / 配置前使用无效回退值。
    apiKey: process.env.LANGSMITH_API_KEY ?? "remove-me", // Use an invalid fallback until configured. / 配置前使用无效回退值。
    runtime: "edge", // Run the proxy on the edge runtime. / 在 Edge 运行时中运行代理。
  });
