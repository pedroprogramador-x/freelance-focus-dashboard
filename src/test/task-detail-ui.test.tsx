// Task Detail (docs/architecture/06 §4) — o que a tela precisa mostrar, como asserção.
//
// O caso central é o último: quando a aprovação é invalidada, a UI tem de dizer **qual
// campo mudou**, em português, e não repetir a mensagem genérica do backend.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TaskDetail } from '../pages/WorkspaceTasks'
import { __resetSessionTokenCache } from '../services/workspaceApi'
import type { PlanningEligibility, Task, TaskContext } from '../services/tasksApi'

// `raw` responde um corpo textual cru (JSON inválido); `network` simula `fetch` rejeitado.
type Route = { status?: number; body?: unknown; raw?: string; network?: boolean }
type Router = (method: string, path: string, body: unknown) => Route

const fakeResponse = (status: number, body: unknown, raw?: string) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => raw ?? (body == null ? '' : JSON.stringify(body)),
})

function installRouter(router: Router) {
  const spy = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const path = url.replace(/^\/api/, '')
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(init.body as string) : undefined
    const { status = 200, body: payload = null, raw, network } = router(method, path, body)
    if (network) throw new TypeError('Failed to fetch')
    return fakeResponse(status, payload, raw) as unknown as Response
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

const FINGERPRINT = 'a'.repeat(64)

// `planning` exatamente nas formas que o backend emite (api/tests/test_e6_cons4.py).
const ELIGIBLE: PlanningEligibility = {
  transition: 'allowed',
  checked: true,
  blockers: [],
  eligible: true,
}
const FORBIDDEN: PlanningEligibility = {
  transition: 'forbidden',
  checked: false,
  blockers: [],
  eligible: false,
}
// `awaiting_approval` com plano vigente: replanejar não é o próximo passo, git adiado.
const AFTER_REJECT_DEFERRED: PlanningEligibility = {
  transition: 'after_reject',
  checked: false,
  blockers: [],
  eligible: false,
}
const AFTER_REJECT_CHECKED: PlanningEligibility = { ...AFTER_REJECT_DEFERRED, checked: true }
const blocked = (...blockers: PlanningEligibility['blockers']): PlanningEligibility => ({
  transition: 'allowed',
  checked: true,
  blockers,
  eligible: false,
})

const taskFixture = (over: Partial<Task> = {}): Task => ({
  id: 't1',
  workspace_id: 'w1',
  title: 'Ajustar o util',
  status: 'awaiting_approval',
  phase: null,
  risk: 'medium',
  complexity: 'medium',
  risk_source: 'hard_rule',
  execution_mode: 'orchestrated',
  version: 3,
  created_at: '2026-09-12T10:00:00+00:00',
  finished_at: null,
  goal: 'melhorar o util',
  agents: ['developer'],
  plan: {
    v: 1,
    goal: 'melhorar o util',
    title: 'Ajustar o util',
    base_commit: 'b'.repeat(40),
    base_branch: 'main',
    analysis: {
      risk: 'medium',
      complexity: 'medium',
      risk_source: 'hard_rule',
      hard_rule: { risk: 'low', complexity: 'low', matched: ['structure:crosses_modules'] },
      shortcut_applied: false,
      enrichment_unavailable: true,
      enrichment_failure: 'no_enrichment_port',
      summary: null,
      affected_domains: [],
      objective_terms: [],
      acceptance_criteria: [],
      needs_architect: false,
      needs_researcher: false,
    },
    agents: ['developer'],
    acceptance_criteria: [],
    execution_limits: { max_attempts: 2, max_fix_rounds: 2 },
  },
  plan_hash: 'c'.repeat(64),
  planning_base_commit: 'b'.repeat(40),
  base_commit: 'b'.repeat(40),
  approved_manifest_id: 'm1',
  planning: AFTER_REJECT_DEFERRED,
  plan_standing: 'current',
  execution_fingerprint: FINGERPRINT,
  execution_fingerprint_parts: {
    v: 1,
    plan_hash: 'c'.repeat(64),
    manifest_hash: 'd'.repeat(64),
    rendered_context_hash: 'e'.repeat(64),
    base_commit: 'b'.repeat(40),
    developer_binding: null,
    auditor_binding: null,
    test_binding: { runner: null, command_hash: null, policy_hash: null },
    agents: ['developer'],
    tool_profile_hash: 'f'.repeat(64),
    safety_policy_hash: '1'.repeat(64),
    workflow_policy_hash: '2'.repeat(64),
    execution_limits: { max_attempts: 2, max_fix_rounds: 2 },
  },
  approved_at: null,
  approval_state: 'pending',
  attempts: 0,
  fix_rounds: 0,
  cancel_requested: false,
  result_summary: null,
  started_at: null,
  ...over,
})

const contextFixture = (over: Partial<TaskContext> = {}): TaskContext => ({
  id: 'm1',
  task_id: 't1',
  git_head: 'b'.repeat(40),
  base_branch: 'main',
  entries: [
    {
      entry_id: 'e1',
      domain: 'objective',
      title: 'Objetivo do projeto',
      content_hash: '9'.repeat(64),
      state: 'fresh',
      stale_reason: null,
    },
  ],
  source_files: [],
  working_tree_divergence: { dirty_file_count: 2, covered: [] },
  derived: [],
  excluded: [{ path_or_entry: '.env', reason: 'secret_policy' }],
  rendered_context_hash: 'e'.repeat(64),
  rendered_context_ref: `artifacts/${'e'.repeat(64)}.json`,
  renderer_version: 'v1',
  approx_tokens: 120,
  total_chars: 480,
  manifest_hash: 'd'.repeat(64),
  created_at: '2026-09-12T10:05:00+00:00',
  ...over,
})

// Corpos de `GET /context` copiados da API REAL (reprodução E6-CONS4, contra banco e repos
// temporários; só o id foi trocado por `t1`). `api/tests/test_e6_cons4.py` fixa a mesma
// forma do lado do backend — os dois lados não podem se afastar sem um deles quebrar.
const NOT_PLANNED_404: Route = {
  status: 404,
  body: {
    code: 'task_not_found',
    message: "a task 't1' ainda não tem contexto congelado",
    reason: 'not_planned',
  },
}
const APPROVAL_INVALIDATED_404: Route = {
  status: 404,
  body: {
    code: 'task_not_found',
    message:
      "a task 't1' teve a aprovação invalidada porque o `HEAD` divergiu do " +
      '`planning_base_commit` congelado; o contexto daquele plano não vale mais',
    reason: 'approval_invalidated',
  },
}
const TASK_MISSING_404: Route = {
  status: 404,
  body: { code: 'task_not_found', message: "task 't1' não encontrada" },
}

// Como `routerFor`, mas com a resposta de `/context` explícita — para cenários em que o
// backend real NÃO devolve manifest.
function routerWith(
  task: Task | (() => Task),
  context: Route,
  overrides: Router = () => ({ status: 404 }),
): Router {
  return (method, path, body) => {
    if (method === 'GET' && path === '/tasks/t1') {
      return { body: typeof task === 'function' ? task() : task }
    }
    if (method === 'GET' && path === '/tasks/t1/context') return context
    return overrides(method, path, body)
  }
}

const calls = (spy: ReturnType<typeof installRouter>, method: string, path: string) =>
  spy.mock.calls.filter(([input, init]) => {
    const url = typeof input === 'string' ? input : input.toString()
    return url === `/api${path}` && (init?.method ?? 'GET').toUpperCase() === method
  }).length

function routerFor(task: Task, overrides: Router = () => ({ status: 404 })): Router {
  return (method, path, body) => {
    if (method === 'GET' && path === '/tasks/t1') return { body: task }
    if (method === 'GET' && path === '/tasks/t1/context') return { body: contextFixture() }
    return overrides(method, path, body)
  }
}

beforeEach(() => {
  // Sem isto, `apiRequest` recusa toda chamada com `workspace_mode_disabled` — a guarda de
  // docs/architecture/06 §3 que impede o build do Pages de falar com localhost.
  vi.stubEnv('VITE_APP_MODE', 'local_dev_workspace')
  __resetSessionTokenCache()
  document.head.innerHTML = '<meta name="ff-session-token" content="token-de-teste">'
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('Task Detail', () => {
  it('mostra o plano, a classificação e as regras que dispararam', async () => {
    installRouter(routerFor(taskFixture()))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('task-status')).toBeInTheDocument())
    expect(screen.getByTestId('task-risk')).toHaveTextContent('médio')
    expect(screen.getByText('structure:crosses_modules')).toBeInTheDocument()
  })

  it('avisa quando o piso de fallback foi aplicado por falta de enriquecimento', async () => {
    installRouter(routerFor(taskFixture()))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() =>
      expect(screen.getByText(/piso conservador foi aplicado/i)).toBeInTheDocument(),
    )
  })

  it('mostra o contexto selecionado, o excluído e o aviso do commit congelado', async () => {
    installRouter(routerFor(taskFixture()))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('excluded-list')).toBeInTheDocument())
    expect(screen.getByText('Objetivo do projeto')).toBeInTheDocument()
    expect(screen.getByText(/classificado como segredo/i)).toBeInTheDocument()
    expect(screen.getByText(/2 arquivo\(s\) não commitado\(s\)/i)).toBeInTheDocument()
    expect(screen.getByText(/não entram/i)).toBeInTheDocument()
  })

  it('mostra os agentes, o perfil de capability e o test_binding nulo', async () => {
    installRouter(routerFor(taskFixture()))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('agents-list')).toHaveTextContent('developer'))
    expect(screen.getByTestId('test-binding-null')).toBeInTheDocument()
    expect(screen.getByText(/requerido e aprovado/i)).toBeInTheDocument()
    expect(screen.getByText(/nulo explícito/i)).toBeInTheDocument()
  })

  it('aprova enviando o execution_fingerprint completo', async () => {
    const enviados: unknown[] = []
    const spy = installRouter(
      routerFor(taskFixture(), (method, path, body) => {
        if (method === 'POST' && path === '/tasks/t1/approve') {
          enviados.push(body)
          return { body: taskFixture({
            status: 'approved',
            approved_at: '2026-09-12T11:00:00Z',
            approval_state: 'approved',
          }) }
        }
        return { status: 404 }
      }),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)
    await waitFor(() => expect(screen.getByText('Aprovar')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Aprovar'))

    await waitFor(() => expect(enviados).toHaveLength(1))
    expect(enviados[0]).toEqual({ execution_fingerprint: FINGERPRINT })
    expect(spy).toHaveBeenCalled()
  })

  it('quando a aprovação é invalidada, diz qual campo mudou', async () => {
    installRouter(
      routerFor(taskFixture(), (method, path) => {
        if (method === 'POST' && path === '/tasks/t1/approve') {
          return {
            status: 409,
            body: {
              code: 'approval_fingerprint_mismatch',
              message: 'a aprovação foi invalidada',
              expected_fingerprint: '7'.repeat(64),
              received_fingerprint: FINGERPRINT,
              diverged_fields: ['test_binding', 'execution_limits'],
            },
          }
        }
        return { status: 404 }
      }),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)
    await waitFor(() => expect(screen.getByText('Aprovar')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Aprovar'))

    const panel = await screen.findByTestId('diverged-fields')
    expect(panel).toHaveTextContent('o Test Runner (comando ou política)')
    expect(panel).toHaveTextContent('os limites de execução')
    expect(panel).toHaveTextContent('7777777')
  })

  it('não constrói painéis de diff, runs ou findings — só o placeholder', async () => {
    installRouter(routerFor(taskFixture()))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('execution-placeholder')).toBeInTheDocument())
    expect(screen.queryByText(/^Diff$/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Findings$/)).not.toBeInTheDocument()
    expect(screen.queryByText(/^Runs$/)).not.toBeInTheDocument()
  })

  it('recusa a purga de tarefa não terminal', async () => {
    installRouter(routerFor(taskFixture()))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('purge-blocked')).toBeInTheDocument())
  })

  it('oferece a prévia de purga quando a tarefa é terminal', async () => {
    installRouter(
      routerFor(taskFixture({ status: 'cancelled', planning: FORBIDDEN, plan_standing: 'final' })),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() =>
      expect(screen.getByText('Carregar prévia da purga')).toBeInTheDocument(),
    )
    expect(screen.queryByTestId('purge-blocked')).not.toBeInTheDocument()
  })

  it('em draft oferece planejar, e não aprovar', async () => {
    const draft = taskFixture({
      status: 'draft',
      plan: null,
      execution_fingerprint: null,
      approved_manifest_id: null,
      approval_state: 'not_planned',
      planning: ELIGIBLE,
      plan_standing: 'none',
    })
    installRouter(routerWith(draft, NOT_PLANNED_404))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByText('Planejar')).toBeInTheDocument())
    expect(screen.queryByText('Aprovar')).not.toBeInTheDocument()
  })

  // E6-AUD2-004. A auditoria encontrou a tela mostrando "aguardando aprovação", "Aprovar"
  // desabilitado e nenhum botão Planejar — sem dizer por quê nem o que fazer. O caminho
  // real, comprovado, é rejeitar (volta a draft) e então planejar.
  it('quando o HEAD invalidou a aprovação, marca o plano como histórico', async () => {
    installRouter(
      routerWith(
        taskFixture({
          status: 'awaiting_approval',
          approval_state: 'requires_replan',
          approved_at: null,
          execution_fingerprint: null,
          approved_manifest_id: null,
          planning: AFTER_REJECT_CHECKED,
          plan_standing: 'historical',
        }),
        APPROVAL_INVALIDATED_404,
      ),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('stale-plan-notice')).toBeInTheDocument())
    expect(screen.getByTestId('requires-replan')).toBeInTheDocument()
    expect(screen.getByTestId('stale-plan-notice')).toHaveTextContent(/histórico/i)
    // E6-CONS5-003: a explicação específica de HEAD só é segura quando `transition` é
    // `after_reject` (a assinatura da guarda de entrada) — este é exatamente esse caso, e
    // ela deve continuar aparecendo aqui.
    expect(screen.getByTestId('stale-plan-notice')).toHaveTextContent(/head/i)
  })

  it('em requires_replan oferece rejeitar e replanejar, nunca aprovar', async () => {
    installRouter(
      routerWith(
        taskFixture({
          status: 'awaiting_approval',
          approval_state: 'requires_replan',
          approved_at: null,
          execution_fingerprint: null,
          approved_manifest_id: null,
          planning: AFTER_REJECT_CHECKED,
          plan_standing: 'historical',
        }),
        APPROVAL_INVALIDATED_404,
      ),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() =>
      expect(screen.getByText('Rejeitar e replanejar')).toBeInTheDocument(),
    )
    // Aprovar responde 409 neste subestado, e Planejar também: nenhum dos dois é oferecido.
    expect(screen.queryByText('Aprovar')).not.toBeInTheDocument()
    expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
  })

  it('com plano vigente o aviso de plano histórico não aparece', async () => {
    installRouter(routerFor(taskFixture()))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByText('Aprovar')).toBeInTheDocument())
    expect(screen.queryByTestId('stale-plan-notice')).not.toBeInTheDocument()
    expect(screen.queryByTestId('requires-replan')).not.toBeInTheDocument()
    expect(screen.getByText('Rejeitar')).toBeInTheDocument()
  })

  // E6-CONS-001. A auditoria consolidada encontrou: task criada e cancelada ainda em
  // `draft` nunca teve contexto congelado; abrir o contexto respondia `404
  // task_not_found` mandando rodar `POST /plan`, que responde `409 invalid_transition`
  // porque `cancelled` é terminal — a tela oferecia uma ação impossível. `canPlan` já
  // exclui `cancelled` (linha 451), então o botão "Planejar" nunca apareceu; o defeito
  // era só o texto dentro do `ContextCard`.
  it('cancelada antes de planejar mostra estado vazio, sem instruir POST /plan', async () => {
    const task = taskFixture({
      status: 'cancelled',
      approval_state: 'not_planned',
      plan: null,
      execution_fingerprint: null,
      approved_manifest_id: null,
      planning: FORBIDDEN,
      plan_standing: 'none',
    })
    installRouter((method, path) => {
      if (method === 'GET' && path === '/tasks/t1') return { body: task }
      if (method === 'GET' && path === '/tasks/t1/context') {
        return {
          status: 404,
          body: {
            code: 'task_not_found',
            message:
              "a task 't1' foi encerrada (cancelled) sem ter congelado contexto; estados " +
              'terminais são imutáveis ([ADR-0008] regra 3)',
            reason: 'terminal_without_context',
          },
        }
      }
      return { status: 404 }
    })

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    const nota = await screen.findByText(/cancelada antes de ser planejada/i)
    expect(nota).toHaveTextContent(/nenhum contexto foi congelado/i)
    expect(nota).toHaveTextContent(/não há planejamento a retomar/i)

    // Não é um erro para alertar, e não instrui a ação que o backend recusaria.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(/POST.*plan/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
  })

  it('cancelada depois de planejar continua mostrando o contexto congelado', async () => {
    installRouter(
      routerFor(
        taskFixture({
          status: 'cancelled',
          approval_state: 'not_planned',
          planning: FORBIDDEN,
          plan_standing: 'final',
        }),
      ),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByText('Objetivo do projeto')).toBeInTheDocument())
    expect(screen.queryByText(/cancelada antes de ser planejada/i)).not.toBeInTheDocument()
    // Registro final, não "entregue" nem histórico.
    expect(screen.getByText('Contexto registrado (registro final)')).toBeInTheDocument()
    expect(screen.getByTestId('final-plan-notice')).toBeInTheDocument()
    expect(screen.queryByTestId('context-historical')).not.toBeInTheDocument()
  })

  // Até E6-CONS4 este teste usava um 404 sem `reason` para representar "draft sem plano",
  // e fixava o alerta — foi exatamente isso que fez a ausência NORMAL de contexto parecer
  // falha (E6-CONS4-003). Hoje o 404 sem `reason` só existe para task inexistente, e é esse
  // o corpo real abaixo: ele continua sendo um erro de verdade.
  it('404 sem reason (task inexistente) continua um alerta de erro comum', async () => {
    installRouter(routerWith(taskFixture(), TASK_MISSING_404))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent(/não encontrada/i)
  })

  // E6-CONS2-001. A auditoria consolidada (rodada 2) encontrou: a correção de
  // E6-CONS-001 associava `terminal_without_context` **sempre** a "cancelada" — mas
  // uma falha inesperada durante `POST /plan` também deixa a task terminal
  // (`failed`) sem manifest, com o mesmo `reason`. Apresentar isso como cancelamento
  // esconde a falha real. A mensagem precisa vir do `status` confiável da task, não
  // de uma projeção fixa do `reason`.
  it('falha de planejamento não é apresentada como cancelamento', async () => {
    const task = taskFixture({
      status: 'failed',
      approval_state: 'not_planned',
      plan: null,
      execution_fingerprint: null,
      approved_manifest_id: null,
      planning: FORBIDDEN,
      plan_standing: 'none',
    })
    installRouter((method, path) => {
      if (method === 'GET' && path === '/tasks/t1') return { body: task }
      if (method === 'GET' && path === '/tasks/t1/context') {
        return {
          status: 404,
          body: {
            code: 'task_not_found',
            message:
              "a task 't1' foi encerrada (failed) sem ter congelado contexto; estados " +
              'terminais são imutáveis ([ADR-0008] regra 3)',
            reason: 'terminal_without_context',
          },
        }
      }
      return { status: 404 }
    })

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    const nota = await screen.findByText(/falhou antes de ter um contexto congelado/i)
    expect(nota).toBeInTheDocument()
    expect(screen.queryByText(/cancelada/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
  })

  // E6-CONS2-002. `planning` é um estado durável ([02] §3, E6-AUD-003) sem manifest
  // ainda — a mensagem antiga de "sem manifest" instruía `POST /plan`, que responde
  // `409` porque `planning → planning` não é aresta. A tela precisa de um estado
  // próprio de "planejamento em andamento", distinto de erro e de "não planejada".
  it('planning em andamento não instrui novo POST /plan', async () => {
    const task = taskFixture({
      status: 'planning',
      approval_state: 'not_planned',
      plan: null,
      execution_fingerprint: null,
      approved_manifest_id: null,
      planning: FORBIDDEN,
      plan_standing: 'none',
    })
    installRouter((method, path) => {
      if (method === 'GET' && path === '/tasks/t1') return { body: task }
      if (method === 'GET' && path === '/tasks/t1/context') {
        return {
          status: 404,
          body: {
            code: 'task_not_found',
            message: "a task 't1' está planejando; o contexto ainda não foi congelado",
            reason: 'planning_in_progress',
          },
        }
      }
      return { status: 404 }
    })

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    const status = await screen.findByText(/planejamento em andamento/i)
    expect(status).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    // `\bcancelada\b` (singular), não `/cancelada/i`: a seção de purga sempre menciona
    // "canceladas" (plural, elegibilidade) para uma task não-terminal como esta.
    expect(screen.queryByText(/\bcancelada\b/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
  })

  it('reason ausente ou desconhecido continua caindo no alerta de erro comum', async () => {
    const task = taskFixture({
      status: 'cancelled',
      approval_state: 'not_planned',
      planning: FORBIDDEN,
      plan_standing: 'final',
    })
    installRouter((method, path) => {
      if (method === 'GET' && path === '/tasks/t1') return { body: task }
      if (method === 'GET' && path === '/tasks/t1/context') {
        return {
          status: 404,
          body: {
            code: 'task_not_found',
            message: 'motivo não classificado pela API',
            reason: 'algum_motivo_futuro_desconhecido',
          },
        }
      }
      return { status: 404 }
    })

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    const alerta = await screen.findByRole('alert')
    expect(alerta).toHaveTextContent(/motivo não classificado/i)
  })

  // E6-CONS3-003 + E6-CONS4-004. Um replan que falha de forma recuperável devolve a task a
  // `draft` preservando `approved_manifest_id`: `GET /context` responde `200` com o
  // manifest anterior. A tela o mostra — mas como HISTÓRICO, nunca como "Contexto
  // entregue", e dizendo que um novo planejamento é necessário (decisão D2-A).
  it('draft com manifesto preservado mostra o contexto marcado como histórico', async () => {
    installRouter(
      routerFor(
        taskFixture({
          status: 'draft',
          approval_state: 'not_planned',
          planning: ELIGIBLE,
          plan_standing: 'historical',
        }),
      ),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByText('Objetivo do projeto')).toBeInTheDocument())
    expect(screen.getByText('Contexto do plano anterior (histórico)')).toBeInTheDocument()
    expect(screen.queryByText('Contexto entregue')).not.toBeInTheDocument()
    expect(screen.getByTestId('context-historical')).toHaveTextContent(
      /novo planejamento é necessário/i,
    )
    // O plano e o fingerprint preservados também não são "vigentes".
    expect(screen.getByTestId('stale-plan-notice')).toHaveTextContent(/não está mais em vigor/i)
    expect(screen.getByTestId('fingerprint-label')).toHaveTextContent(/plano anterior/i)
    expect(screen.queryByText('Fingerprint vigente')).not.toBeInTheDocument()
  })

  it('com plano vigente o contexto é "entregue" e o fingerprint é vigente', async () => {
    installRouter(routerFor(taskFixture()))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByText('Contexto entregue')).toBeInTheDocument())
    expect(screen.getByTestId('fingerprint-label')).toHaveTextContent('Fingerprint vigente')
    expect(screen.queryByTestId('context-historical')).not.toBeInTheDocument()
  })

  // plan → reject é fluxo NORMAL da E6: o plano fica como registro, o candidato sai, e não
  // há manifest. Plano preservado ≠ manifest preservado.
  it('após reject o plano preservado é histórico e o contexto é a ausência normal', async () => {
    const rejeitada = taskFixture({
      status: 'draft',
      approval_state: 'not_planned',
      approved_manifest_id: null,
      execution_fingerprint: null,
      execution_fingerprint_parts: null,
      planning: ELIGIBLE,
      plan_standing: 'historical',
    })
    installRouter(routerWith(rejeitada, NOT_PLANNED_404))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('context-empty')).toBeInTheDocument())
    expect(screen.getByTestId('stale-plan-notice')).toHaveTextContent(/histórico/i)
    // Sem manifest não há nada a marcar como histórico no contexto.
    expect(screen.queryByTestId('context-historical')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('Planejar')).toBeInTheDocument()
  })

  // E6-CONS4-003. O 404 REAL de uma draft recém-criada — com `reason: not_planned` — é o
  // estado vazio normal, não uma falha da aplicação.
  it('draft sem manifesto com o 404 real mostra estado vazio, sem role=alert', async () => {
    const nova = taskFixture({
      status: 'draft',
      approval_state: 'not_planned',
      plan: null,
      execution_fingerprint: null,
      execution_fingerprint_parts: null,
      approved_manifest_id: null,
      planning: ELIGIBLE,
      plan_standing: 'none',
    })
    installRouter(routerWith(nova, NOT_PLANNED_404))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    const vazio = await screen.findByTestId('context-empty')
    expect(vazio).toHaveTextContent(/ainda não tem contexto congelado/i)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByText('Planejar')).toBeInTheDocument()
  })

  it.each([
    ['401', { status: 401, body: { code: 'unauthorized', message: 'credencial ausente' } }],
    ['500', { status: 500, body: { code: 'internal_error', message: 'falha interna' } }],
    ['unavailable_in_state', {
      status: 404,
      body: {
        code: 'task_not_found',
        message: "a task 't1' não tem contexto congelado disponível no estado atual",
        reason: 'unavailable_in_state',
      },
    }],
    ['rede', { network: true }],
    ['JSON inválido', { status: 502, raw: '<html>bad gateway</html>' }],
  ] as [string, Route][])('%s em /context continua sendo erro real', async (_rotulo, route) => {
    installRouter(routerWith(taskFixture(), route))

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.queryByTestId('context-empty')).not.toBeInTheDocument()
  })

  // E6-CONS4-001. Aprovação invalidada pelo HEAD, e o workspace foi arquivado depois.
  // Rejeitar é possível AGORA; planejar depois exige reativar. A tela diz as duas coisas
  // e não promete a segunda.
  it('requires_replan em workspace arquivado: rejeitar agora, reativar antes de planejar', async () => {
    installRouter(
      routerWith(
        taskFixture({
          status: 'awaiting_approval',
          approval_state: 'requires_replan',
          approved_at: null,
          execution_fingerprint: null,
          approved_manifest_id: null,
          planning: {
            transition: 'after_reject',
            checked: true,
            blockers: ['workspace_archived'],
            eligible: false,
          },
          plan_standing: 'historical',
        }),
        APPROVAL_INVALIDATED_404,
      ),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    const bloqueios = await screen.findByTestId('plan-blockers')
    expect(bloqueios).toHaveTextContent(/Rejeitar é possível agora/i)
    expect(bloqueios).toHaveTextContent(/Reativar/)
    // O botão rejeita — e só isso. Não se chama "e replanejar" quando replanejar está bloqueado.
    expect(screen.getByText('Rejeitar')).toBeInTheDocument()
    expect(screen.queryByText('Rejeitar e replanejar')).not.toBeInTheDocument()
    expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
    expect(screen.queryByText('Aprovar')).not.toBeInTheDocument()
    expect(screen.getByTestId('requires-replan')).not.toHaveTextContent(/planeje de novo/i)
    expect(screen.getByTestId('context-empty')).toBeInTheDocument()
  })

  // E6-CONS4-001 (lado draft) e E6-CONS4-002. Cada pré-condição reportada pelo backend
  // tira o "Planejar" e explica o que falta — sem a tela reimplementar a guarda.
  it.each([
    ['workspace_archived', /Reativar/],
    ['workspace_not_git_repo', /não é um repositório git/i],
    ['repository_without_head', /nenhum commit/i],
    ['invalid_test_config', /Test Runner/i],
  ] as [PlanningEligibility['blockers'][number], RegExp][])(
    'draft bloqueada por %s não oferece Planejar',
    async (blocker, texto) => {
      installRouter(
        routerWith(
          taskFixture({
            status: 'draft',
            approval_state: 'not_planned',
            plan: null,
            execution_fingerprint: null,
            approved_manifest_id: null,
            planning: blocked(blocker),
            plan_standing: 'none',
          }),
          NOT_PLANNED_404,
        ),
      )

      render(<TaskDetail taskId="t1" onBack={() => {}} />)

      const bloqueios = await screen.findByTestId('plan-blockers')
      expect(bloqueios).toHaveTextContent(texto)
      expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
      expect(screen.queryByText('Verificar de novo')).not.toBeInTheDocument()
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    },
  )

  it('git não verificável oferece "Verificar de novo", que relê a task uma vez', async () => {
    const spy = installRouter(
      routerWith(
        taskFixture({
          status: 'draft',
          approval_state: 'not_planned',
          plan: null,
          execution_fingerprint: null,
          approved_manifest_id: null,
          planning: blocked('git_unverifiable'),
          plan_standing: 'none',
        }),
        NOT_PLANNED_404,
      ),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    fireEvent.click(await screen.findByText('Verificar de novo'))
    await waitFor(() => expect(calls(spy, 'GET', '/tasks/t1')).toBe(2))
    expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
  })

  // `checked = false` com lista vazia é verificação ADIADA, nunca elegibilidade — mesmo que
  // uma resposta malformada dissesse `eligible: true`.
  it.each([
    ['adiada', { transition: 'allowed', checked: false, blockers: [], eligible: false }],
    ['contraditória', { transition: 'allowed', checked: false, blockers: [], eligible: true }],
    ['after_reject', { transition: 'after_reject', checked: true, blockers: [], eligible: true }],
  ] as [string, PlanningEligibility][])(
    'elegibilidade %s não habilita Planejar',
    async (_rotulo, planning) => {
      installRouter(
        routerWith(
          taskFixture({ status: 'draft', approval_state: 'not_planned', planning }),
          NOT_PLANNED_404,
        ),
      )

      render(<TaskDetail taskId="t1" onBack={() => {}} />)

      await screen.findByTestId('context-empty')
      expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
    },
  )

  // Mudança entre o GET e o POST: a elegibilidade era positiva, o workspace foi arquivado
  // em outra aba, e `POST /plan` recusou. A tela relê a task e deixa de oferecer Planejar.
  it('409 de pré-condição no POST /plan relê a task e atualiza a elegibilidade', async () => {
    let arquivado = false
    const tarefa = () =>
      taskFixture({
        status: 'draft',
        approval_state: 'not_planned',
        plan: null,
        execution_fingerprint: null,
        approved_manifest_id: null,
        planning: arquivado ? blocked('workspace_archived') : ELIGIBLE,
        plan_standing: 'none',
      })
    const spy = installRouter(
      routerWith(tarefa, NOT_PLANNED_404, (method, path) => {
        if (method === 'POST' && path === '/tasks/t1/plan') {
          arquivado = true
          return {
            status: 409,
            body: {
              code: 'workspace_not_plannable',
              message: "o workspace 'ws' está arquivado ([02] §1)",
              reason: 'workspace_archived',
            },
          }
        }
        return { status: 404 }
      }),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    fireEvent.click(await screen.findByText('Planejar'))

    await waitFor(() => expect(screen.getByTestId('plan-blockers')).toBeInTheDocument())
    expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/arquivado/i)
    expect(calls(spy, 'GET', '/tasks/t1')).toBe(2)
  })

  it.each([
    ['falha interna', 500, 'internal_error'],
    ['autorização', 401, 'unauthorized'],
  ] as [string, number, string][])(
    '%s no POST /plan não vira bloqueio de elegibilidade nem relê a task',
    async (_rotulo, status, code) => {
      const spy = installRouter(
        routerWith(
          taskFixture({
            status: 'draft',
            approval_state: 'not_planned',
            plan: null,
            execution_fingerprint: null,
            approved_manifest_id: null,
            planning: ELIGIBLE,
            plan_standing: 'none',
          }),
          NOT_PLANNED_404,
          (method, path) =>
            method === 'POST' && path === '/tasks/t1/plan'
              ? { status, body: { code, message: `recusa: ${code}` } }
              : { status: 404 },
        ),
      )

      render(<TaskDetail taskId="t1" onBack={() => {}} />)

      fireEvent.click(await screen.findByText('Planejar'))

      expect(await screen.findByRole('alert')).toHaveTextContent(code)
      expect(calls(spy, 'GET', '/tasks/t1')).toBe(1)
      expect(screen.queryByTestId('plan-blockers')).not.toBeInTheDocument()
    },
  )

  // E6-CONS5-002. Achado: uma perda de CAS deixava o botão "Planejar" obsoleto na tela —
  // só um GET acontecia (o inicial), e o 409 virava só uma mensagem. `concurrent_task_update`
  // não é uma pré-condição de planejamento (não está em `PLAN_REFUSALS`), mas ainda é motivo
  // de releitura: outra escrita venceu a corrida, e o campo que mudou pode ser qualquer um.
  // O GET pós-conflito devolve uma task DIFERENTE do GET inicial, de propósito — é o que
  // prova que a tela leu de novo, e não que ficou mostrando a resposta antiga.
  it('conflito concorrente no POST /plan relê a task uma vez, sem perder o alerta', async () => {
    let conflitou = false
    const tarefa = () =>
      taskFixture({
        status: 'draft',
        approval_state: 'not_planned',
        plan: null,
        execution_fingerprint: null,
        approved_manifest_id: null,
        planning: conflitou ? blocked('invalid_test_config') : ELIGIBLE,
        plan_standing: 'none',
      })
    const spy = installRouter(
      routerWith(tarefa, NOT_PLANNED_404, (method, path) => {
        if (method === 'POST' && path === '/tasks/t1/plan') {
          conflitou = true
          return {
            status: 409,
            body: {
              code: 'concurrent_task_update',
              message: "a task 't1' perdeu uma corrida de escrita ([02] §4)",
            },
          }
        }
        return { status: 404 }
      }),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    fireEvent.click(await screen.findByText('Planejar'))

    // O refresh troca a elegibilidade mostrada: o botão obsoleto some, o bloqueio novo
    // aparece, e o alerta do conflito original continua — não é substituído nem duplicado.
    await waitFor(() => expect(screen.getByTestId('plan-blockers')).toBeInTheDocument())
    expect(screen.queryByText('Planejar')).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/corrida/i)
    expect(calls(spy, 'GET', '/tasks/t1')).toBe(2)
  })

  // O controle de reativação existente vive no cabeçalho do workspace. Quando ele muda o
  // status, a tela relê a task UMA vez — sem polling e sem consulta por renderização.
  it('mudar o status do workspace relê a task uma vez, e só uma', async () => {
    let ativo = false
    const spy = installRouter(
      routerWith(
        () =>
          taskFixture({
            status: 'draft',
            approval_state: 'not_planned',
            plan: null,
            execution_fingerprint: null,
            approved_manifest_id: null,
            planning: ativo ? ELIGIBLE : blocked('workspace_archived'),
            plan_standing: 'none',
          }),
        NOT_PLANNED_404,
      ),
    )

    const { rerender } = render(
      <TaskDetail taskId="t1" onBack={() => {}} workspaceStatus="archived" />,
    )
    await screen.findByTestId('plan-blockers')
    rerender(<TaskDetail taskId="t1" onBack={() => {}} workspaceStatus="archived" />)
    expect(calls(spy, 'GET', '/tasks/t1')).toBe(1)

    ativo = true
    rerender(<TaskDetail taskId="t1" onBack={() => {}} workspaceStatus="active" />)

    expect(await screen.findByText('Planejar')).toBeInTheDocument()
    expect(calls(spy, 'GET', '/tasks/t1')).toBe(2)
    expect(calls(spy, 'GET', '/tasks/t1/context')).toBe(1)
  })

  // E6-CONS3-002. `needs_fix` inconsistente pode vir `requires_replan`, mas o backend diz
  // `transition: allowed` — planejar é direto, e `reject` responderia 409. Nada aqui manda
  // rejeitar.
  it('needs_fix com requires_replan oferece planejar direto, nunca rejeitar', async () => {
    installRouter(
      routerWith(
        taskFixture({
          status: 'needs_fix',
          approval_state: 'requires_replan',
          approved_at: null,
          approved_manifest_id: null,
          execution_fingerprint: null,
          planning: ELIGIBLE,
          plan_standing: 'historical',
        }),
        NOT_PLANNED_404,
      ),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByText('Planejar')).toBeInTheDocument())
    expect(screen.queryByTestId('requires-replan')).not.toBeInTheDocument()
    expect(screen.queryByText('Rejeitar')).not.toBeInTheDocument()
    expect(screen.queryByText('Rejeitar e replanejar')).not.toBeInTheDocument()
    expect(document.body).not.toHaveTextContent(/rejeite/i)
  })

  // E6-CONS5-003. Achado: `PlanStandingNotice` afirmava "o HEAD do repositório mudou" só
  // por `approval_state === 'requires_replan'` — mas essa combinação também aparece aqui,
  // em `needs_fix` sem manifesto, onde `planning.transition` é `allowed` (não
  // `after_reject`) e não há nenhuma evidência de que o HEAD tenha mudado. A auditoria só
  // alcançou este estado por escrita direta no banco (`test_e6_cons5.py`); mesmo assim, a
  // frase específica de HEAD não pode aparecer sem a assinatura da guarda de entrada.
  it('needs_fix com requires_replan não afirma mudança de HEAD', async () => {
    installRouter(
      routerWith(
        taskFixture({
          status: 'needs_fix',
          approval_state: 'requires_replan',
          approved_at: null,
          approved_manifest_id: null,
          execution_fingerprint: null,
          planning: ELIGIBLE,
          plan_standing: 'historical',
        }),
        NOT_PLANNED_404,
      ),
    )

    render(<TaskDetail taskId="t1" onBack={() => {}} />)

    await waitFor(() => expect(screen.getByTestId('stale-plan-notice')).toBeInTheDocument())
    expect(screen.getByTestId('stale-plan-notice')).not.toHaveTextContent(/head/i)
    expect(screen.getByTestId('stale-plan-notice')).toHaveTextContent(/histórico/i)
  })
})
