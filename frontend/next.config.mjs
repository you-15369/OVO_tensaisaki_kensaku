/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // ブラウザからは同一オリジンの /api/* を叩き、Next.js が FastAPI へ中継する
  // （Basic 認証の Authorization ヘッダーもそのまま転送される）
  async rewrites() {
    const target = (process.env.API_PROXY_TARGET ?? "http://localhost:8000").replace(/\/$/, "");
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
