/** Next.js configuration. / Next.js 配置。 @type {import('next').NextConfig} */
const nextConfig = {
  // Allow browser access from the WSL VM IP so the dev server hydrates when
  // reached through the Nginx gateway. Update this if the WSL IP changes.
  // / 允许通过 WSL 虚拟机 IP 访问，使开发服务器在经 Nginx 网关访问时也能正常水合。若 WSL IP 变化请更新。
  allowedDevOrigins: ["172.17.250.57", "172.17.250.*"],
  experimental: {
    serverActions: {
      bodySizeLimit: "10mb",
    },
  },
};

export default nextConfig;
