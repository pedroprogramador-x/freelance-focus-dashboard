// Cliente HTTP do AI Dev Workspace (docs/architecture/06 §2).
//
// - Lê o LocalSessionToken da <meta name="ff-session-token"> injetada no HTML inicial
//   pelo backend (docs/architecture/06 §1), copia para memória e **remove a meta do DOM**.
// - Monta `Authorization: Bearer <token>` em toda requisição.
// - Em HOSTED_COMMERCIAL_ONLY, nenhuma função aqui chega a chamar `fetch`: elas rejeitam
//   antes, com `WorkspaceApiError('workspace_mode_disabled')`.
//
// Persistência disjunta (docs/architecture/01 §4): nada aqui toca `localStorage`.

import { isWorkspaceModeEnabled } from '../config/appMode'

const SESSION_TOKEN_META_NAME = 'ff-session-token'

export type WorkspaceType = 'personal' | 'freelance' | 'study' | 'experiment' | 'open_source'
export type WorkspaceStatus = 'active' | 'archived'

export interface Workspace {
  id: string
  name: string
  type: WorkspaceType
  local_path: string
  linked_project_id: string | null
  repository_url: string | null
  default_branch: string | null
  status: WorkspaceStatus
  // A `TestPolicy` deste workspace (docs/architecture/04 §6), normalizada pelo backend.
  // `null` = sem Test Runner configurado, e nesse caso o `test_binding` do fingerprint tem
  // os três campos nulos (docs/architecture/02 §7).
  test_config: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export interface WorkspaceCreateInput {
  name: string
  type: WorkspaceType
  local_path: string
  linked_project_id?: string | null
  repository_url?: string | null
  default_branch?: string | null
}

export interface GitPreflight {
  is_git_repo: boolean
  head: string | null
  branch: string | null
  dirty_file_count: number | null
}

export interface PurgeCounts {
  workspaces: number
  tasks: number
  runs: number
  findings: number
  manifests: number
  artifacts: number
}

export interface PurgePreview extends PurgeCounts {
  purge_token: string
}

export class WorkspaceApiError extends Error {
  readonly code: string
  readonly status: number | null
  // Campos de diagnóstico que o corpo do erro trouxe além de `{code, message}`.
  //
  // docs/architecture/06 §2 exige que um 409 venha "sempre com o motivo e **qual campo
  // divergiu**", e o backend responde isso como dado estruturado (`diverged_fields`,
  // `guard`, `current_status`…). Descartá-lo aqui obrigaria a UI a extrair o diagnóstico
  // da mensagem em prosa — que é exatamente o que o campo estruturado existe para evitar.
  readonly details: Record<string, unknown>

  constructor(
    code: string,
    message: string,
    status: number | null = null,
    details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'WorkspaceApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

let cachedToken: string | null | undefined

function sessionToken(): string | null {
  if (cachedToken === undefined) {
    const meta =
      typeof document === 'undefined'
        ? null
        : document.querySelector<HTMLMetaElement>(`meta[name="${SESSION_TOKEN_META_NAME}"]`)
    cachedToken = meta?.content.trim() || null
    // O token vive só em memória a partir daqui (docs/architecture/06 §1).
    meta?.remove()
  }
  return cachedToken
}

// Exportado só para os testes: zera o cache do token entre casos.
export function __resetSessionTokenCache(): void {
  cachedToken = undefined
}

// Exportado para `contextApi.ts` reusar exatamente o mesmo transporte: leitura do token,
// cabeçalhos, guarda de modo hospedado e tradução de erro. Uma segunda implementação de
// `fetch` no frontend seria uma segunda chance de esquecer o `Authorization`.
export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  if (!isWorkspaceModeEnabled()) {
    throw new WorkspaceApiError(
      'workspace_mode_disabled',
      'O AI Dev Workspace só está disponível na execução local.',
    )
  }

  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')
  if (init?.body !== undefined) headers.set('Content-Type', 'application/json')
  const token = sessionToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let response: Response
  try {
    response = await fetch(`/api${path}`, { ...init, headers })
  } catch {
    throw new WorkspaceApiError('network_error', 'Não foi possível falar com o backend local.')
  }

  const raw = await response.text()
  const body: unknown = raw ? JSON.parse(raw) : null

  if (!response.ok) {
    const detail = (body ?? {}) as Record<string, unknown>
    const { code, message, ...rest } = detail
    throw new WorkspaceApiError(
      typeof code === 'string' ? code : 'error',
      typeof message === 'string' ? message : `HTTP ${response.status}`,
      response.status,
      rest,
    )
  }

  return body as T
}

export function listWorkspaces(status?: WorkspaceStatus): Promise<Workspace[]> {
  const query = status ? `?status=${status}` : ''
  return apiRequest<Workspace[]>(`/workspaces${query}`)
}

export function createWorkspace(input: WorkspaceCreateInput): Promise<Workspace> {
  return apiRequest<Workspace>('/workspaces', { method: 'POST', body: JSON.stringify(input) })
}

export function patchWorkspaceStatus(id: string, status: WorkspaceStatus): Promise<Workspace> {
  return apiRequest<Workspace>(`/workspaces/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function getGitPreflight(id: string): Promise<GitPreflight> {
  return apiRequest<GitPreflight>(`/workspaces/${encodeURIComponent(id)}/git`)
}

export function getPurgePreview(id: string): Promise<PurgePreview> {
  return apiRequest<PurgePreview>(`/workspaces/${encodeURIComponent(id)}/purge-preview`)
}

export function purgeWorkspace(id: string, purgeToken: string): Promise<PurgeCounts> {
  return apiRequest<PurgeCounts>(`/workspaces/${encodeURIComponent(id)}/purge`, {
    method: 'POST',
    body: JSON.stringify({ purge_token: purgeToken }),
  })
}
