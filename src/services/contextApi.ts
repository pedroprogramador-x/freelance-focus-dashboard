// Cliente HTTP do Context Registry (docs/architecture/06 §2, docs/architecture/03).
//
// Reusa `apiRequest` de `workspaceApi.ts` — mesmo token, mesmos cabeçalhos, mesma guarda de
// modo hospedado, mesma tradução de erro. Uma segunda implementação de `fetch` aqui seria
// uma segunda chance de esquecer o `Authorization`.
//
// Persistência disjunta (ADR-0002): nada aqui toca `localStorage`. O seed de planejamento
// é **copiado** para o formato anônimo que o backend aceita e enviado uma vez; não há
// sincronização de volta.

import { apiRequest } from './workspaceApi'

export type ContextDomain =
  | 'objective'
  | 'architecture'
  | 'stack'
  | 'requirements'
  | 'modules'
  | 'decisions'
  | 'risks'
  | 'contracts'

export type ContextState = 'fresh' | 'stale' | 'unknown'
export type StaleReason = 'sources_changed' | 'working_tree'
export type ContextOrigin = 'manual' | 'imported_planning' | 'generated'

export interface ContextEntry {
  id: string
  workspace_id: string
  domain: ContextDomain
  title: string
  body: string
  structured: Record<string, unknown> | null
  tags: string[]
  source_refs: string[]
  content_hash: string
  source_hash: string | null
  source_hash_commit: string | null
  state: ContextState
  stale_reason: StaleReason | null
  origin: ContextOrigin
  last_verified_at: string | null
  last_verified_commit: string | null
  created_at: string
  updated_at: string
}

export interface ContextEntryCreateInput {
  domain: ContextDomain
  title: string
  body: string
  structured?: Record<string, unknown> | null
  tags?: string[]
  source_refs?: string[]
}

// Campo ausente ≠ campo nulo (docs/architecture/03 §3): omitir `source_refs` preserva o
// baseline; enviar `[]` o zera. Por isso o tipo é `Partial` de verdade, e quem chama monta
// o objeto só com o que mudou.
export type ContextEntryUpdateInput = Partial<{
  title: string
  body: string
  structured: Record<string, unknown> | null
  tags: string[]
  source_refs: string[]
}>

export interface CoveredDivergence {
  path: string
  kind: string
  entry_id: string
}

export interface WorkingTreeDivergence {
  dirty_file_count: number | null
  covered: CoveredDivergence[]
}

export interface ContextVerifyResult {
  verification_commit: string | null
  entries: ContextEntry[]
  working_tree_divergence: WorkingTreeDivergence
}

// Contrato de import: `snake_case` e **anônimo**. O backend não sabe de onde veio
// (docs/architecture/03 §1); a tradução do modelo comercial acontece aqui, do lado do
// frontend, e é a única ponte entre as duas persistências.
export interface PlanningImportInput {
  problem?: string
  objective?: string
  functional_requirements?: string[]
  non_functional_requirements?: string[]
  stack?: string[]
  architecture?: string
  technical_decisions?: { title: string; decision?: string; reason?: string }[]
  risks?: { description: string; mitigation?: string }[]
}

export interface ContextImportResult {
  created: number
  entries: ContextEntry[]
}

const base = (workspaceId: string) => `/workspaces/${encodeURIComponent(workspaceId)}/context`

export function listContextEntries(workspaceId: string): Promise<ContextEntry[]> {
  return apiRequest<ContextEntry[]>(base(workspaceId))
}

export function createContextEntry(
  workspaceId: string,
  input: ContextEntryCreateInput,
): Promise<ContextEntry> {
  return apiRequest<ContextEntry>(base(workspaceId), {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function updateContextEntry(
  entryId: string,
  input: ContextEntryUpdateInput,
): Promise<ContextEntry> {
  return apiRequest<ContextEntry>(`/context/${encodeURIComponent(entryId)}`, {
    method: 'PATCH',
    body: JSON.stringify(input),
  })
}

export function deleteContextEntry(entryId: string): Promise<null> {
  return apiRequest<null>(`/context/${encodeURIComponent(entryId)}`, { method: 'DELETE' })
}

export function verifyContext(workspaceId: string): Promise<ContextVerifyResult> {
  return apiRequest<ContextVerifyResult>(`${base(workspaceId)}/verify`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}

export function importPlanning(
  workspaceId: string,
  seed: PlanningImportInput,
): Promise<ContextImportResult> {
  return apiRequest<ContextImportResult>(`${base(workspaceId)}/import`, {
    method: 'POST',
    body: JSON.stringify(seed),
  })
}
