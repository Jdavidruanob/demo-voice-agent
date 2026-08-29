import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Evita que Turbopack intente inferir la raíz del monorepo subiendo hasta
  // encontrar otro package-lock.json fuera de este repo (hay uno en el
  // directorio del usuario, ajeno a este proyecto).
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
