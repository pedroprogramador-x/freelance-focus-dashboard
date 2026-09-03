// Modo da aplicação, fixado em build time (docs/architecture/06 §3).
//
// - HOSTED_COMMERCIAL_ONLY: build do GitHub Pages. Fluxo comercial completo; a área de
//   AI Dev Workspace mostra "disponível apenas na execução local" e **não faz nenhuma
//   chamada de rede à API** (docs/architecture/01 §4, docs/architecture/06 §3).
// - LOCAL_DEV_WORKSPACE: build servido pelo backend local. Workspace Registry ativo.
//
// O build do backend passa `VITE_APP_MODE=local_dev_workspace`; o `deploy.yml` do Pages
// roda `npm run build` sem essa variável, então o default é o modo hospedado.

export type AppMode = 'LOCAL_DEV_WORKSPACE' | 'HOSTED_COMMERCIAL_ONLY'

export function getAppMode(): AppMode {
  return import.meta.env.VITE_APP_MODE === 'local_dev_workspace'
    ? 'LOCAL_DEV_WORKSPACE'
    : 'HOSTED_COMMERCIAL_ONLY'
}

export function isWorkspaceModeEnabled(): boolean {
  return getAppMode() === 'LOCAL_DEV_WORKSPACE'
}
