// Cliente HTTP do Orchestrator (docs/architecture/06 §2, docs/architecture/02 §3–§7).
//
// Reusa `apiRequest` de `workspaceApi.ts` — mesmo token, mesmos cabeçalhos, mesma guarda de
// modo hospedado, mesma tradução de erro. Uma segunda implementação de `fetch` aqui seria
// uma segunda chance de esquecer o `Authorization`.
//
// As rotas de E7–E11 (`/runs`, `/diff`, `/findings`, `/worktree/discard`, `/events`) **não**
// aparecem aqui: elas não existem no backend desta fase, e um cliente que as chamasse
// receberia 404. A aba de execução do Task Detail é um placeholder textual, não um painel
// desabilitado apontando para rota inexistente.

import { apiRequest, WorkspaceApiError } from './workspaceApi'

export type TaskStatus =
  | 'draft'
  | 'planning'
  | 'awaiting_approval'
  | 'approved'
  | 'executing'
  | 'needs_fix'
  | 'done'
  | 'failed'
  | 'cancelled'

export type TaskPhase = 'implementing' | 'testing' | 'auditing'
export type RiskLevel = 'low' | 'medium' | 'high'
export type ComplexityLevel = 'trivial' | 'low' | 'medium' | 'high'
export type RiskSource = 'hard_rule' | 'llm' | 'user'
export type ExecutionMode = 'claude_only' | 'orchestrated' | 'orchestrated_ruflo'

export interface TaskSummary {
  id: string
  workspace_id: string
  title: string
  status: TaskStatus
  phase: TaskPhase | null
  risk: RiskLevel
  complexity: ComplexityLevel
  risk_source: RiskSource
  execution_mode: ExecutionMode
  version: number
  created_at: string
  finished_at: string | null
}

// O `test_binding` de docs/architecture/02 §7. Os três campos são todos `null` (workspace
// sem Test Runner configurado) ou todos preenchidos — nunca um estado misto.
export interface TestBinding {
  runner: string | null
  command_hash: string | null
  policy_hash: string | null
}

export interface ProviderBinding {
  adapter: string
  adapter_version: string | null
  model: string | null
}

// Os componentes de docs/architecture/02 §7, exatamente como o backend os serializa. É o
// que permite a UI dizer **qual campo** mudou quando a aprovação é invalidada.
export interface FingerprintParts {
  v: number
  plan_hash: string
  manifest_hash: string
  rendered_context_hash: string
  base_commit: string
  developer_binding: ProviderBinding | null
  auditor_binding: ProviderBinding | null
  test_binding: TestBinding
  agents: string[]
  tool_profile_hash: string
  safety_policy_hash: string
  workflow_policy_hash: string
  execution_limits: Record<string, number>
}

export interface TaskAnalysis {
  risk: RiskLevel
  complexity: ComplexityLevel
  risk_source: RiskSource
  hard_rule: { risk: RiskLevel; complexity: ComplexityLevel; matched: string[] }
  shortcut_applied: boolean
  // `true` quando o piso de fallback de docs/architecture/03 §5 foi aplicado. Uma task
  // `medium` por fallback não é a mesma coisa que uma `medium` por análise, e a UI diz qual.
  enrichment_unavailable: boolean
  enrichment_failure: string | null
  summary: string | null
  affected_domains: string[]
  objective_terms: string[]
  acceptance_criteria: string[]
  needs_architect: boolean
  needs_researcher: boolean
}

export interface TaskPlan {
  v: number
  goal: string
  title: string
  base_commit: string
  base_branch: string | null
  analysis: TaskAnalysis
  agents: string[]
  acceptance_criteria: string[]
  execution_limits: Record<string, number>
}

export type PlanningBlocker =
  | 'workspace_archived'
  | 'workspace_not_git_repo'
  | 'repository_without_head'
  | 'git_unverifiable'
  | 'invalid_test_config'

// `TaskResponse.planning`. Só existe no DETALHE — a listagem nunca sonda git por task.
//
//   transition  allowed (draft, needs_fix) · after_reject (awaiting_approval) · forbidden
//   checked     false quando o git não foi consultado: aí `blockers` é PARCIAL, e vazio
//               não quer dizer "nada falta"
//   eligible    a ÚNICA autorização para oferecer "Planejar"
//
// É uma fotografia: `POST /plan` repete as guardas e continua sendo a autoridade.
export interface PlanningEligibility {
  transition: 'allowed' | 'after_reject' | 'forbidden'
  checked: boolean
  blockers: PlanningBlocker[]
  eligible: boolean
}

// none: sem plano · current: em vigor · historical: preservado, sem validade (rejeitado,
// replanejamento interrompido, aprovação invalidada, replanejamento em andamento) ·
// final: registro de uma task encerrada.
export type PlanStanding = 'none' | 'current' | 'historical' | 'final'

export interface Task extends TaskSummary {
  goal: string
  agents: string[]
  plan: TaskPlan | null
  plan_hash: string | null
  planning_base_commit: string | null
  base_commit: string | null
  approved_manifest_id: string | null
  // O que `POST /plan` aceitaria AGORA, derivado pelo backend das mesmas funções de guarda
  // que o planejamento aplica (E6-CONS4, docs/architecture/06 adendo). A tela não
  // reimplementa nenhuma dessas guardas: ela lê este campo.
  planning: PlanningEligibility
  // Se o `plan` exibido — e o manifest, quando existe, que pertence a ele — está em vigor.
  plan_standing: PlanStanding
  // O fingerprint **vigente**: candidato enquanto `approved_at` é `null`, aprovado depois.
  // É o valor que `approveTask` devolve ao backend.
  execution_fingerprint: string | null
  execution_fingerprint_parts: FingerprintParts | null
  approved_at: string | null
  // Onde a task está no fluxo de aprovação, derivado pelo backend das colunas de
  // docs/architecture/02 §3. NÃO é o `status` da máquina de estados — é a distinção que
  // `awaiting_approval` sozinho não faz (E6-AUD2-004):
  //
  //   pending          → há plano vigente; o botão útil é Aprovar
  //   requires_replan  → a guarda de entrada invalidou a aprovação porque o HEAD mudou.
  //                      O `plan` abaixo é HISTÓRICO: aprovar responde 409 e planejar
  //                      também, porque awaiting_approval → planning não é aresta.
  //                      O caminho é Rejeitar (volta a draft) e então Planejar.
  approval_state: 'not_planned' | 'pending' | 'approved' | 'requires_replan'
  attempts: number
  fix_rounds: number
  cancel_requested: boolean
  result_summary: string | null
  started_at: string | null
}

export interface ManifestEntry {
  entry_id: string
  domain: string
  title: string
  content_hash: string
  state: string
  stale_reason: string | null
}

export interface ExcludedItem {
  path_or_entry: string
  reason: 'budget' | 'secret_policy' | 'out_of_workspace'
}

export interface TaskContext {
  id: string
  task_id: string
  git_head: string
  base_branch: string | null
  entries: ManifestEntry[]
  source_files: { path: string; blob_sha: string }[]
  working_tree_divergence: {
    dirty_file_count: number | null
    covered: { path: string; kind: string }[]
  }
  derived: { kind: string; hash: string; item_count: number }[]
  excluded: ExcludedItem[]
  rendered_context_hash: string
  rendered_context_ref: string
  renderer_version: string
  approx_tokens: number
  total_chars: number
  manifest_hash: string
  created_at: string
}

export interface TaskPurgePreview {
  workspaces: number
  tasks: number
  runs: number
  findings: number
  manifests: number
  artifacts: number
  benchmark_protected: boolean
  purge_token: string
}

// O corpo de um 409 de fingerprint divergente (docs/architecture/06 §2: "sempre com o
// motivo e qual campo divergiu"). `WorkspaceApiError` carrega só `code`/`message`, então
// os campos extras são lidos aqui, do erro tipado.
export interface FingerprintMismatch {
  expected_fingerprint: string
  received_fingerprint: string
  diverged_fields: string[]
}

export class ApprovalMismatchError extends WorkspaceApiError {
  readonly mismatch: FingerprintMismatch

  constructor(message: string, mismatch: FingerprintMismatch) {
    super('approval_fingerprint_mismatch', message, 409)
    this.name = 'ApprovalMismatchError'
    this.mismatch = mismatch
  }
}

export function listTasks(workspaceId: string, status?: TaskStatus): Promise<TaskSummary[]> {
  const query = status ? `?status=${status}` : ''
  return apiRequest<TaskSummary[]>(
    `/workspaces/${encodeURIComponent(workspaceId)}/tasks${query}`,
  )
}

export function createTask(
  workspaceId: string,
  input: { title: string; goal: string },
): Promise<Task> {
  return apiRequest<Task>(`/workspaces/${encodeURIComponent(workspaceId)}/tasks`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function getTask(taskId: string): Promise<Task> {
  return apiRequest<Task>(`/tasks/${encodeURIComponent(taskId)}`)
}

export function planTask(taskId: string, candidatePaths: string[] = []): Promise<Task> {
  return apiRequest<Task>(`/tasks/${encodeURIComponent(taskId)}/plan`, {
    method: 'POST',
    body: JSON.stringify({ candidate_paths: candidatePaths }),
  })
}

// `approve` envia o `execution_fingerprint` completo (ADR-0008 regra 5) — nunca um
// `confirm: true`. É o que vincula a aprovação ao que o humano de fato leu.
export async function approveTask(taskId: string, fingerprint: string): Promise<Task> {
  try {
    return await apiRequest<Task>(`/tasks/${encodeURIComponent(taskId)}/approve`, {
      method: 'POST',
      body: JSON.stringify({ execution_fingerprint: fingerprint }),
    })
  } catch (caught) {
    throw enrichApprovalError(caught)
  }
}

// Promove o 409 genérico a um erro tipado, lendo `details` — os campos que o backend
// manda além de `{code, message}` (docs/architecture/06 §2).
function enrichApprovalError(caught: unknown): unknown {
  if (!(caught instanceof WorkspaceApiError)) return caught
  if (caught.code !== 'approval_fingerprint_mismatch') return caught

  const { expected_fingerprint, received_fingerprint, diverged_fields } = caught.details

  return new ApprovalMismatchError(caught.message, {
    expected_fingerprint: typeof expected_fingerprint === 'string' ? expected_fingerprint : '',
    received_fingerprint: typeof received_fingerprint === 'string' ? received_fingerprint : '',
    diverged_fields: Array.isArray(diverged_fields) ? (diverged_fields as string[]) : [],
  })
}

export function rejectTask(taskId: string, note?: string): Promise<Task> {
  return apiRequest<Task>(`/tasks/${encodeURIComponent(taskId)}/reject`, {
    method: 'POST',
    body: JSON.stringify({ note: note ?? null }),
  })
}

export function cancelTask(taskId: string, note?: string): Promise<Task> {
  return apiRequest<Task>(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
    body: JSON.stringify({ note: note ?? null }),
  })
}

export function getTaskContext(taskId: string): Promise<TaskContext> {
  return apiRequest<TaskContext>(`/tasks/${encodeURIComponent(taskId)}/context`)
}

// `GET .../context` reaproveita o código `task_not_found` (404) tanto para "a task não
// existe" quanto para "a task existe, mas não tem manifest". Desde E6-CONS4 TODO 404 do
// segundo tipo traz `reason` (docs/architecture/06, adendo); a AUSÊNCIA de `reason` é o
// que identifica a task inexistente — um erro real. Ler `reason` de `details`, em vez de
// inspecionar o texto de `message`, é o mesmo idioma de `enrichApprovalError`.
//
//   not_planned               draft/needs_fix sem contexto — estado vazio NORMAL
//   approval_invalidated      a guarda de entrada invalidou a aprovação (HEAD divergiu)
//   planning_in_progress      replanejamento em curso
//   terminal_without_context  encerrada sem nunca ter congelado contexto
//   unavailable_in_state      combinação inconsistente — tratada como erro real
export function contextUnavailableReason(caught: unknown): string | null {
  if (!(caught instanceof WorkspaceApiError)) return null
  if (caught.code !== 'task_not_found') return null
  const { reason } = caught.details
  return typeof reason === 'string' ? reason : null
}

// As recusas de `POST /plan` que significam "a pré-condição mudou desde a última leitura":
// a tela recarrega a task para mostrar a elegibilidade nova. Autorização (401/403) e
// falhas inesperadas (5xx) NÃO entram — cada uma continua com o tratamento próprio, e
// nenhuma vira "bloqueio de elegibilidade". A perda de CAS (`concurrent_task_update`) é um
// motivo de releitura diferente — ver `isConcurrentTaskConflict` — porque não é uma
// pré-condição de planejamento: é "outra escrita venceu a corrida", e o campo que mudou
// pode ser qualquer um, não só `planning`.
const PLAN_REFUSALS: ReadonlySet<string> = new Set([
  'workspace_not_plannable',
  'invalid_test_config',
  'invalid_transition',
])

export function isPlanPreconditionRefusal(caught: unknown): boolean {
  return (
    caught instanceof WorkspaceApiError &&
    (caught.status === 409 || caught.status === 422) &&
    PLAN_REFUSALS.has(caught.code)
  )
}

// E6-CONS5-002: uma perda de *compare-and-set* (`409 concurrent_task_update`,
// docs/architecture/02 §4) — outra escrita venceu a corrida entre a leitura que a tela
// mostra e este `POST`. O estado observado pode ter mudado por qualquer campo; a tela relê
// a task **uma vez** para não continuar oferecendo uma ação que a leitura antiga autorizava
// mas que já não vale. Autorização (401/403) e falhas de servidor (5xx) não são conflito
// de escrita e não entram aqui — cada uma continua só com a mensagem.
export function isConcurrentTaskConflict(caught: unknown): boolean {
  return (
    caught instanceof WorkspaceApiError &&
    caught.status === 409 &&
    caught.code === 'concurrent_task_update'
  )
}

export function getTaskPurgePreview(taskId: string): Promise<TaskPurgePreview> {
  return apiRequest<TaskPurgePreview>(`/tasks/${encodeURIComponent(taskId)}/purge-preview`)
}

export function purgeTask(taskId: string, purgeToken: string): Promise<TaskPurgePreview> {
  return apiRequest<TaskPurgePreview>(`/tasks/${encodeURIComponent(taskId)}/purge`, {
    method: 'POST',
    body: JSON.stringify({ purge_token: purgeToken }),
  })
}
