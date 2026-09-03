/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Modo da aplicação, fixado em build time (ver src/config/appMode.ts).
  readonly VITE_APP_MODE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
