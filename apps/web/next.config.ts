import type { NextConfig } from 'next';

const internalApi = (process.env.QUANT_API_INTERNAL_URL || 'http://127.0.0.1:8100').replace(/\/+$/, '');

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${internalApi}/api/v1/:path*`
      }
    ];
  }
};

export default nextConfig;
