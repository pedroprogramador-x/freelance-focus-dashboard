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

  constructor(code: string, message: string, status: number | null = null) {
    super(message)
    this.name = 'WorkspaceApiError'
    this.code = code
    this.status = status
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
    const detail = (body ?? {}) as { code?: string; message?: string }
    throw new WorkspaceApiError(
      detail.code ?? 'error',
      detail.message ?? `HTTP ${response.status}`,
      response.status,
    )
  }

  return body as T
}

export function listWorkspaces(status?: WorkspaceStatus): Promise<Workspace[]> {
  const query = status ? `?status=${status}` : ''
  return request<Workspace[]>(`/workspaces${query}`)
}

export function createWorkspace(input: WorkspaceCreateInput): Promise<Workspace> {
  return request<Workspace>('/workspaces', { method: 'POST', body: JSON.stringify(input) })
}

export function patchWorkspaceStatus(id: string, status: WorkspaceStatus): Promise<Workspace> {
  return request<Workspace>(`/workspaces/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function getGitPreflight(id: string): Promise<GitPreflight> {
  return request<GitPreflight>(`/workspaces/${encodeURIComponent(id)}/git`)
}

export function getPurgePreview(id: string): Promise<PurgePreview> {
  return request<PurgePreview>(`/workspaces/${encodeURIComponent(id)}/purge-preview`)
}

export function purgeWorkspace(id: string, purgeToken: string): Promise<PurgeCounts> {
  return request<PurgeCounts>(`/workspaces/${encodeURIComponent(id)}/purge`, {
    method: 'POST',
    body: JSON.stringify({ purge_token: purgeToken }),
  })
}
