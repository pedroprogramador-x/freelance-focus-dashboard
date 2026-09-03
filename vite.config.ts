import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// O projeto não tem `@types/node`; a config só precisa de `process.env`.
declare const process: { env: Record<string, string | undefined> }

// Modo local do AI Dev Workspace (docs/architecture/06 §1, §3).
//
// - `VITE_APP_MODE=local_dev_workspace`  → build servido pelo backend FastAPI em `/`,
//   mesma origem por construção. `base` precisa ser `/` para o HTML compilado referenciar
//   `/assets/...` (que o FastAPI monta).
// - sem essa variável (build do GitHub Pages, `deploy.yml` intocado) → `base` continua
//   `/freelance-focus-dashboard/`.
//
// FLUXO DE DEV LOCAL SUPORTADO ATÉ A E11: `vite build` (com essa variável) servido pela
// FastAPI — NÃO `npm run dev` isolado. O launcher de hot-reload (FastAPI + Vite juntos,
// token compartilhado em memória) está adiado para a E11 (ver "Decisões adiadas" no
// roadmap e CLAUDE.md). O `server.proxy` abaixo já deixa o caminho pronto para esse
// launcher, mas sem ele um `npm run dev` isolado não tem token e não autentica na API.
const LOCAL_MODE = process.env.VITE_APP_MODE === 'local_dev_workspace'

const PAGES_BASE = '/freelance-focus-dashboard/'
const DEV_API_TARGET = process.env.FF_DEV_API_TARGET ?? 'http://127.0.0.1:8756'

export default defineConfig(({ command }) => ({
  plugins: [react()],
  base: command === 'build' && !LOCAL_MODE ? PAGES_BASE : '/',
  server: {
    // Serviria HTML autenticador → mesmas defesas do backend (docs/architecture/06 §1).
    host: '127.0.0.1',
    cors: false,
    allowedHosts: ['127.0.0.1', 'localhost'],
    proxy: {
      // `changeOrigin: false` preserva o `Host` do dev server (`localhost:5173`) na
      // requisição ao backend, para o `Host` e o `Origin` casarem na validação de
      // mesma origem (app/api/security.py::origin_matches_host).
      '/api': { target: DEV_API_TARGET, changeOrigin: false },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
}))
