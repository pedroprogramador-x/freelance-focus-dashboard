// Aba *Tarefas* e o *Task Detail* de docs/architecture/06 §4.
//
// O que §4 exige desta tela, e onde cada item está:
//
// | Exigência de [06] §4                         | Onde |
// | plano e critérios de aceite                  | `PlanCard` |
// | contexto selecionado + o que foi excluído    | `ContextCard` |
// | divergência do working tree + aviso do base  | `ContextCard` |
// | agentes, perfil de capability e limites      | `BindingsCard` |
// | aprovar enviando o fingerprint completo      | `ApprovalCard` |
// | quando invalidada, **qual campo mudou**      | `ApprovalCard` → `DivergedFields` |
//
// Progresso por `phase`, diff, findings e runs são E7–E11. Eles aparecem como **um**
// parágrafo em `ExecutionPlaceholder`, e não como painéis desabilitados: um painel vazio
// promete uma tela que ainda não existe e envelhece pior do que uma frase honesta.

import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  FileText,
  Loader2,
  Play,
  ShieldAlert,
  Trash2,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Modal } from '../components/Modal'
import { messageOf } from '../utils/contextEntries'
import {
  ApprovalMismatchError,
  approveTask,
  cancelTask,
  contextUnavailableReason,
  createTask,
  getTask,
  getTaskContext,
  getTaskPurgePreview,
  isConcurrentTaskConflict,
  isPlanPreconditionRefusal,
  listTasks,
  planTask,
  purgeTask,
  rejectTask,
  type FingerprintMismatch,
  type PlanningBlocker,
  type Task,
  type TaskContext,
  type TaskPurgePreview,
  type TaskSummary,
} from '../services/tasksApi'

const TERMINAL: ReadonlySet<string> = new Set(['done', 'failed', 'cancelled'])

const STATUS_LABEL: Record<string, string> = {
  draft: 'rascunho',
  planning: 'planejando',
  awaiting_approval: 'aguardando aprovação',
  approved: 'aprovada',
  executing: 'executando',
  needs_fix: 'precisa de correção',
  done: 'concluída',
  failed: 'falhou',
  cancelled: 'cancelada',
}

const RISK_LABEL: Record<string, string> = { low: 'baixo', medium: 'médio', high: 'alto' }

const COMPLEXITY_LABEL: Record<string, string> = {
  trivial: 'trivial',
  low: 'baixa',
  medium: 'média',
  high: 'alta',
}

const RISK_SOURCE_LABEL: Record<string, string> = {
  hard_rule: 'regra determinística',
  llm: 'análise por modelo',
  user: 'definido pelo usuário',
}

const EXCLUSION_LABEL: Record<string, string> = {
  budget: 'não coube no orçamento de tokens',
  secret_policy: 'classificado como segredo',
  out_of_workspace: 'fora do workspace',
}

// Nomes de campo do `execution_fingerprint` (docs/architecture/02 §7) em português. É o que
// transforma "test_binding divergiu" em algo acionável para quem não leu o documento.
const FIELD_LABEL: Record<string, string> = {
  plan_hash: 'o plano',
  manifest_hash: 'o contexto selecionado',
  rendered_context_hash: 'o payload de contexto entregue',
  base_commit: 'o commit base',
  developer_binding: 'o adaptador/modelo do Developer',
  auditor_binding: 'o adaptador/modelo do Auditor',
  test_binding: 'o Test Runner (comando ou política)',
  agents: 'os agentes escolhidos',
  tool_profile_hash: 'o perfil de capability exigido',
  safety_policy_hash: 'a política de segurança',
  workflow_policy_hash: 'a política de workflow',
  execution_limits: 'os limites de execução',
}

const PURGE_ROWS: { key: keyof TaskPurgePreview; label: string }[] = [
  { key: 'tasks', label: 'Tarefas' },
  { key: 'runs', label: 'Execuções' },
  { key: 'findings', label: 'Findings' },
  { key: 'manifests', label: 'Manifests' },
  { key: 'artifacts', label: 'Artefatos' },
]

function shortHash(value: string | null): string {
  return value ? value.slice(0, 12) : '—'
}

// ------------------------------------------------------------------------ cartões

// "Rejeitar agora" é uma ação desta tela só quando o backend diz que o caminho para
// replanejar passa por `reject` (`planning.transition === 'after_reject'`) e a aprovação
// foi invalidada. `needs_fix` inconsistente também pode vir `requires_replan`, mas ali
// `transition` é `allowed` — planejar é direto, e `reject` responderia 409 (E6-CONS3-002).
function replanRequiresReject(task: Task): boolean {
  return task.approval_state === 'requires_replan' && task.planning.transition === 'after_reject'
}

// O plano preservado, marcado pelo que ele é (E6-CONS4-004, D2-A). O backend decide o
// `plan_standing`; aqui só se escolhe o texto. Nenhum destes avisos promete uma ação: o
// que dá para fazer agora sai de `planning`, no `ApprovalCard`.
//
// docs/architecture/02 §6: a execução nunca salta para o HEAD novo, e a base congelada não
// é substituída em silêncio (E6-AUD2-004) — daí o texto próprio de `requires_replan`.
function PlanStandingNotice({ task }: { task: Task }) {
  if (task.plan_standing === 'final') {
    return (
      <p className="form-hint" role="note" data-testid="final-plan-notice">
        Registro final: a tarefa foi encerrada com este plano.
      </p>
    )
  }
  if (task.plan_standing !== 'historical') return null
  // E6-CONS5-003: `approval_state === 'requires_replan'` sozinho não prova que o `HEAD`
  // mudou — a mesma combinação de colunas também aparece (defensivamente, hoje só por
  // escrita direta no banco) num `needs_fix` sem manifesto, onde `planning.transition` é
  // `allowed`, não `after_reject`. A explicação específica de HEAD só é segura quando
  // `replanRequiresReject` também é verdadeiro: essa é a assinatura de
  // `_invalidate_approval_on_entry` (a guarda de entrada), a única causa comprovada.
  if (replanRequiresReject(task)) {
    return (
      <p className="form-error" role="status" data-testid="stale-plan-notice">
        <AlertTriangle size={14} /> Este plano é <strong>histórico</strong>: o `HEAD` do
        repositório mudou depois que ele foi aprovado, então a aprovação foi invalidada e a
        base congelada não vale mais. Ele não pode ser aprovado como está.
      </p>
    )
  }
  if (task.status === 'planning') {
    return (
      <p className="form-hint" role="status" data-testid="stale-plan-notice">
        Este é o plano <strong>anterior</strong>: um novo planejamento está em andamento e vai
        substituí-lo.
      </p>
    )
  }
  return (
    <p className="form-hint" role="status" data-testid="stale-plan-notice">
      Este plano é <strong>histórico</strong>: ele não está mais em vigor (foi rejeitado, ou o
      replanejamento não chegou ao fim). Um novo planejamento é necessário.
    </p>
  )
}

function PlanCard({ task }: { task: Task }) {
  const plan = task.plan
  const analysis = plan?.analysis

  return (
    <section className="card">
      <h3>
        <FileText size={16} /> Plano
      </h3>
      {!plan && <p>Esta tarefa ainda não foi planejada.</p>}
      {plan && <PlanStandingNotice task={task} />}
      {plan && (
        <>
          <p className="pre-wrap">{plan.goal}</p>

          <h4>Critérios de aceite</h4>
          {plan.acceptance_criteria.length === 0 ? (
            <p>
              Nenhum critério de aceite foi proposto: a porta de enriquecimento por modelo
              só existe a partir da E8. Até lá os critérios são responsabilidade de quem
              escreve o objetivo.
            </p>
          ) : (
            <ul>
              {plan.acceptance_criteria.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}

          <h4>Classificação</h4>
          <dl className="workspace-git-facts">
            <div>
              <dt>Risco</dt>
              <dd data-testid="task-risk">{RISK_LABEL[task.risk] ?? task.risk}</dd>
            </div>
            <div>
              <dt>Complexidade</dt>
              <dd>{COMPLEXITY_LABEL[task.complexity] ?? task.complexity}</dd>
            </div>
            <div>
              <dt>Fonte do risco</dt>
              <dd>{RISK_SOURCE_LABEL[task.risk_source] ?? task.risk_source}</dd>
            </div>
          </dl>

          {analysis?.enrichment_unavailable && (
            <p className="form-hint" role="note">
              O enriquecimento por modelo não estava disponível, então o piso conservador foi
              aplicado: risco e complexidade valem ao menos <strong>médio</strong>. A fonte
              do risco continua sendo a regra determinística — não houve segunda opinião.
            </p>
          )}

          {analysis && analysis.hard_rule.matched.length > 0 && (
            <>
              <h4>Regras que dispararam</h4>
              <ul>
                {analysis.hard_rule.matched.map((rule) => (
                  <li key={rule}>
                    <code>{rule}</code>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  )
}

// A mensagem de "sem contexto congelado" depende do `status` **confiável** da task, não
// de inspecionar a mensagem do backend (E6-CONS2-001, E6-CONS2-002: `reason ===
// 'terminal_without_context'` sozinho não diz se foi cancelamento ou falha — a projeção
// fixa "foi cancelada" já apresentou uma falha real como cancelamento uma vez).
function unavailableMessage(status: string): string {
  if (status === 'cancelled') {
    return (
      'Esta tarefa foi cancelada antes de ser planejada — nenhum contexto foi congelado, e ' +
      'não há planejamento a retomar.'
    )
  }
  if (status === 'failed') {
    return (
      'Esta tarefa falhou antes de ter um contexto congelado — nenhum contexto foi ' +
      'congelado, e não há planejamento a retomar.'
    )
  }
  // Defensivo: os outros estados terminais (`done`) sempre têm manifest pelas guardas
  // atuais — ver a matriz em `context_route` — mas o `reason` não presume isso para sempre.
  return 'Esta tarefa foi encerrada sem que um contexto fosse congelado.'
}

// Os `reason` de ausência que são estados normais — uma frase de descrição, sem `role=alert`.
// Tudo o que não está aqui (404 sem `reason` = task inexistente, `unavailable_in_state`,
// `reason` desconhecido, 401, 5xx, rede, JSON inválido) continua sendo erro real.
const EMPTY_CONTEXT_MESSAGE: ReadonlyMap<string, string> = new Map([
  ['not_planned', 'Esta tarefa ainda não tem contexto congelado.'],
  [
    'approval_invalidated',
    'O contexto do plano anterior não vale mais: a aprovação foi invalidada porque o `HEAD` ' +
      'mudou. Um novo planejamento vai congelar outro.',
  ],
])

// O manifest pertence ao mesmo plano que `plan_standing` descreve (docs/architecture/06,
// adendo E6-CONS4). O título só diz "entregue" quando esse plano está em vigor.
const CONTEXT_TITLE: Record<string, string> = {
  current: 'Contexto entregue',
  historical: 'Contexto do plano anterior (histórico)',
  final: 'Contexto registrado (registro final)',
  none: 'Contexto registrado',
}

function ContextCard({
  taskId,
  status,
  planStanding,
}: {
  taskId: string
  status: string
  planStanding: string
}) {
  const [state, setState] = useState<
    | { status: 'loading' }
    | { status: 'planning' }
    | { status: 'empty'; message: string }
    | { status: 'unavailable'; message: string }
    | { status: 'error'; message: string }
    | { status: 'ready'; data: TaskContext }
  >({ status: 'loading' })

  // Sempre consulta a API, mesmo em `draft` (E6-CONS3-003): um replan que falha de forma
  // recuperável devolve a task a `draft` sem limpar `approved_manifest_id`, e `/context`
  // responde `200` com o manifest histórico. Presumir "draft = sem contexto" escondia
  // esse manifest — `status` não é confiável para decidir isso, só a resposta da API é.
  useEffect(() => {
    let active = true
    setState({ status: 'loading' })
    getTaskContext(taskId)
      .then((data) => active && setState({ status: 'ready', data }))
      .catch((error) => {
        if (!active) return
        const reason = contextUnavailableReason(error)
        // `planning_in_progress` (E6-CONS2-002): a task está no estado durável
        // `planning`, entre o commit de entrada e o de saída ([02] §3) — sem manifest
        // ainda, mas `POST /plan` responderia `409` (`planning → planning` não é
        // aresta). Não é um erro nem "ainda não planejada": é planejamento em curso.
        if (reason === 'planning_in_progress') {
          setState({ status: 'planning' })
          return
        }
        // `terminal_without_context` (E6-CONS-001, generalizado por E6-CONS2-001): a
        // task chegou a um estado terminal sem nunca ter congelado contexto — não só
        // `cancelled`. Não é uma falha para alertar, e a mensagem usa o `status`
        // confiável da task, nunca o texto da API, para não apresentar uma falha real
        // como cancelamento (ou vice-versa).
        if (reason === 'terminal_without_context') {
          setState({ status: 'unavailable', message: unavailableMessage(status) })
          return
        }
        // E6-CONS4-003: a ausência normal de contexto não é falha da aplicação.
        const empty = reason === null ? undefined : EMPTY_CONTEXT_MESSAGE.get(reason)
        if (empty !== undefined) {
          setState({ status: 'empty', message: empty })
          return
        }
        setState({ status: 'error', message: messageOf(error) })
      })
    return () => {
      active = false
    }
  }, [taskId, status])

  const title =
    state.status === 'ready' ? (CONTEXT_TITLE[planStanding] ?? CONTEXT_TITLE.none) : 'Contexto'

  return (
    <section className="card">
      <h3>{title}</h3>
      {state.status === 'loading' && <p role="status">Lendo o manifest…</p>}
      {state.status === 'planning' && (
        <p role="status">Planejamento em andamento — o contexto ainda não foi congelado.</p>
      )}
      {state.status === 'empty' && (
        <p className="form-hint" role="note" data-testid="context-empty">
          {state.message}
        </p>
      )}
      {state.status === 'ready' && planStanding === 'historical' && (
        <p className="form-error" role="status" data-testid="context-historical">
          <AlertTriangle size={14} /> Este manifest pertence ao plano anterior e não está em
          vigor. Um novo planejamento é necessário para congelar o contexto atual.
        </p>
      )}
      {state.status === 'unavailable' && (
        <p className="form-hint" role="note">
          {state.message}
        </p>
      )}
      {state.status === 'error' && (
        <p className="form-error" role="alert">
          {state.message}
        </p>
      )}
      {state.status === 'ready' && (
        <>
          <dl className="workspace-git-facts">
            <div>
              <dt>Commit base</dt>
              <dd>
                <code>{shortHash(state.data.git_head)}</code>
              </dd>
            </div>
            <div>
              <dt>Branch</dt>
              <dd>{state.data.base_branch ?? 'HEAD desanexado'}</dd>
            </div>
            <div>
              <dt>Tokens aproximados</dt>
              <dd>{state.data.approx_tokens}</dd>
            </div>
          </dl>

          <h4>Entradas selecionadas ({state.data.entries.length})</h4>
          {state.data.entries.length === 0 ? (
            <p>Nenhuma entrada do registry foi selecionada.</p>
          ) : (
            <ul>
              {state.data.entries.map((entry) => (
                <li key={entry.entry_id}>
                  <strong>{entry.title}</strong> <small>({entry.domain})</small>{' '}
                  <span className={`status-pill context-${entry.state}`}>{entry.state}</span>
                </li>
              ))}
            </ul>
          )}

          <h4>Excluído ({state.data.excluded.length})</h4>
          {state.data.excluded.length === 0 ? (
            <p>Nada foi excluído.</p>
          ) : (
            <ul data-testid="excluded-list">
              {state.data.excluded.map((item) => (
                <li key={`${item.reason}:${item.path_or_entry}`}>
                  <code>{item.path_or_entry}</code> — {EXCLUSION_LABEL[item.reason] ?? item.reason}
                </li>
              ))}
            </ul>
          )}

          <h4>Divergência do working tree</h4>
          <p>
            {state.data.working_tree_divergence.dirty_file_count === null
              ? 'Indisponível.'
              : state.data.working_tree_divergence.dirty_file_count === 0
                ? 'Árvore limpa.'
                : `${state.data.working_tree_divergence.dirty_file_count} arquivo(s) não commitado(s).`}
          </p>
          <p className="form-hint" role="note">
            A execução usa o commit base <strong>congelado</strong> acima. Alterações não
            commitadas <strong>não entram</strong> — a worktree nasce daquele SHA.
          </p>
        </>
      )}
    </section>
  )
}

function BindingsCard({ task }: { task: Task }) {
  const parts = task.execution_fingerprint_parts

  return (
    <section className="card">
      <h3>Agentes, perfil e limites</h3>

      <h4>Agentes</h4>
      {task.agents.length === 0 ? (
        <p>Ainda não decididos — planeje a tarefa.</p>
      ) : (
        <ul data-testid="agents-list">
          {task.agents.map((agent) => (
            <li key={agent}>{agent}</li>
          ))}
        </ul>
      )}

      {parts && (
        <>
          <h4>Perfil de capability exigido</h4>
          <p>
            <code>{shortHash(parts.tool_profile_hash)}</code> — perfil{' '}
            <strong>requerido e aprovado</strong> para a execução futura. Ele é declarado
            agora e <strong>provado</strong> só a partir da E7, quando o adaptador existir;
            sem prova, a execução é recusada.
          </p>

          <h4>Test Runner</h4>
          {parts.test_binding.runner === null ? (
            <p data-testid="test-binding-null">
              Nenhum Test Runner configurado para este workspace. Os três campos do{' '}
              <code>test_binding</code> ficam nulos, e configurá-lo depois invalida esta
              aprovação.
            </p>
          ) : (
            <dl className="workspace-git-facts">
              <div>
                <dt>Runner</dt>
                <dd>{parts.test_binding.runner}</dd>
              </div>
              <div>
                <dt>Comando</dt>
                <dd>
                  <code>{shortHash(parts.test_binding.command_hash)}</code>
                </dd>
              </div>
              <div>
                <dt>Política</dt>
                <dd>
                  <code>{shortHash(parts.test_binding.policy_hash)}</code>
                </dd>
              </div>
            </dl>
          )}

          <h4>Auditor</h4>
          <p>
            {parts.auditor_binding === null
              ? 'Ausente nesta fase — registrado como nulo explícito no fingerprint.'
              : `${parts.auditor_binding.adapter} · ${parts.auditor_binding.model ?? 'modelo padrão'}`}
          </p>

          <h4>Limites</h4>
          <dl className="workspace-git-facts">
            {Object.entries(parts.execution_limits).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </section>
  )
}

function DivergedFields({ mismatch }: { mismatch: FingerprintMismatch }) {
  return (
    <div className="form-error" role="alert" data-testid="diverged-fields">
      <p>
        <AlertTriangle size={15} /> A aprovação foi invalidada porque algo mudou desde que
        você leu este plano:
      </p>
      <ul>
        {mismatch.diverged_fields.map((field) => (
          <li key={field}>{FIELD_LABEL[field] ?? field}</li>
        ))}
      </ul>
      <p>
        O fingerprint vigente é <code>{shortHash(mismatch.expected_fingerprint)}</code>.
        Releia o plano e aprove de novo.
      </p>
    </div>
  )
}

// O texto de cada pré-condição que `planning.blockers` pode trazer. É só apresentação: quem
// decide se o bloqueio existe é o backend. A reativação aponta para o controle que já
// existe no cabeçalho do workspace — não há um segundo fluxo aqui.
const BLOCKER_TEXT: Record<PlanningBlocker, string> = {
  workspace_archived: 'o workspace está arquivado — reative-o pelo botão "Reativar" no topo do workspace',
  workspace_not_git_repo:
    'o diretório do workspace não é um repositório git (o backend nunca executa `git init`)',
  repository_without_head: 'o repositório ainda não tem nenhum commit',
  git_unverifiable: 'não foi possível consultar o git agora',
  invalid_test_config: 'a configuração do Test Runner deste workspace está malformada',
}

function PlanBlockers({
  blockers,
  lead,
  onRecheck,
}: {
  blockers: PlanningBlocker[]
  lead: string
  onRecheck: () => void
}) {
  return (
    <div className="form-hint" role="note" data-testid="plan-blockers">
      <p>{lead}</p>
      <ul>
        {blockers.map((blocker) => (
          <li key={blocker} data-blocker={blocker}>
            {BLOCKER_TEXT[blocker] ?? blocker}
          </li>
        ))}
      </ul>
      {/* Uma nova leitura só quando o humano pede — nunca em loop nem por renderização. */}
      {blockers.includes('git_unverifiable') && (
        <button type="button" className="button secondary" onClick={onRecheck}>
          Verificar de novo
        </button>
      )}
    </div>
  )
}

// "Vigente" só com o plano em vigor (E6-CONS4-004): um fingerprint preservado de um plano
// histórico não autoriza nada.
const FINGERPRINT_LABEL: Record<string, string> = {
  current: 'Fingerprint vigente',
  historical: 'Fingerprint do plano anterior (sem validade)',
  final: 'Fingerprint do registro final',
  none: 'Fingerprint',
}

function ApprovalCard({ task, onChanged }: { task: Task; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mismatch, setMismatch] = useState<FingerprintMismatch | null>(null)
  const [note, setNote] = useState('')

  const run = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true)
      setError(null)
      setMismatch(null)
      try {
        await action()
        onChanged()
      } catch (caught) {
        if (caught instanceof ApprovalMismatchError) {
          setMismatch(caught.mismatch)
          onChanged() // a task continua aguardando, com o fingerprint novo
        } else {
          setError(messageOf(caught))
          // A elegibilidade mostrada é uma fotografia (E6-CONS4): se `POST /plan` recusou
          // por pré-condição, ou se uma perda de CAS (E6-CONS5-002) diz que outra escrita
          // venceu a corrida, o estado pode ter mudado desde a leitura — relê a task, uma
          // vez, para não continuar exibindo uma ação obsoleta. Autorização e 5xx ficam só
          // com a mensagem: nenhum dos dois diz que o estado mudou.
          if (isPlanPreconditionRefusal(caught) || isConcurrentTaskConflict(caught)) onChanged()
        }
      } finally {
        setBusy(false)
      }
    },
    [onChanged],
  )

  // Tudo o que diz respeito a planejar sai de `task.planning`, que o backend deriva das
  // mesmas funções de guarda que `POST /plan` aplica (E6-CONS4). Nada aqui reimplementa
  // arquivamento, git, `HEAD` ou `test_config`.
  const { planning } = task
  // `eligible` é a única autorização; `checked` é conferido de novo por defesa: uma
  // verificação adiada (`checked = false`, lista vazia) nunca vira "Planejar".
  const canPlan = planning.eligible && planning.checked && planning.transition === 'allowed'
  const planBlockers = planning.transition === 'allowed' ? planning.blockers : []
  // `requires_replan` em `awaiting_approval`: aprovar responde 409, e planejar também
  // (awaiting_approval → planning não é aresta). Rejeitar é a ação de AGORA; planejar vem
  // depois, e só se as pré-condições estiverem satisfeitas (E6-AUD2-004, E6-CONS4-001).
  const needsReplan = replanRequiresReject(task)
  const replanBlockers = needsReplan && planning.checked ? planning.blockers : []
  const canDecide = task.status === 'awaiting_approval'
  const canApprove = canDecide && !needsReplan
  const canCancel = !TERMINAL.has(task.status)
  const fingerprintLabel = FINGERPRINT_LABEL[task.plan_standing] ?? 'Fingerprint'

  return (
    <section className="card">
      <h3>
        <CheckCircle2 size={16} /> Aprovação
      </h3>

      <dl className="workspace-git-facts">
        <div>
          <dt data-testid="fingerprint-label">{fingerprintLabel}</dt>
          <dd>
            <code data-testid="fingerprint">{shortHash(task.execution_fingerprint)}</code>
          </dd>
        </div>
        <div>
          <dt>Aprovada em</dt>
          <dd>{task.approved_at ? new Date(task.approved_at).toLocaleString('pt-BR') : '—'}</dd>
        </div>
      </dl>

      {needsReplan && (
        <p className="form-error" role="status" data-testid="requires-replan">
          <AlertTriangle size={14} /> A aprovação foi invalidada porque o `HEAD` divergiu da
          base congelada: não há como aprovar nem replanejar a partir daqui. Rejeitar volta a
          tarefa para rascunho, de onde um novo plano pode ser feito.
        </p>
      )}
      {needsReplan && replanBlockers.length > 0 && (
        <PlanBlockers
          blockers={replanBlockers}
          lead="Rejeitar é possível agora, mas o novo planejamento ainda exigirá:"
          onRecheck={onChanged}
        />
      )}
      {planBlockers.length > 0 && (
        <PlanBlockers
          blockers={planBlockers}
          lead="Planejar não está disponível agora:"
          onRecheck={onChanged}
        />
      )}
      {mismatch && <DivergedFields mismatch={mismatch} />}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      {canDecide && (
        <label className="field">
          <span>Nota (opcional, usada na rejeição)</span>
          <textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} />
        </label>
      )}

      <div className="form-actions">
        {canPlan && (
          <button
            type="button"
            className="button"
            disabled={busy}
            onClick={() => run(() => planTask(task.id))}
          >
            {busy ? <Loader2 size={15} className="spin" /> : null} Planejar
          </button>
        )}
        {canApprove && (
          <button
            type="button"
            className="button"
            disabled={busy || !task.execution_fingerprint}
            onClick={() => run(() => approveTask(task.id, task.execution_fingerprint ?? ''))}
          >
            Aprovar
          </button>
        )}
        {canDecide && (
          <button
            type="button"
            className={needsReplan ? 'button' : 'button secondary'}
            disabled={busy}
            onClick={() => run(() => rejectTask(task.id, note || undefined))}
          >
            {needsReplan && replanBlockers.length === 0 && planning.checked
              ? 'Rejeitar e replanejar'
              : 'Rejeitar'}
          </button>
        )}
        {canCancel && (
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={() => run(() => cancelTask(task.id, note || undefined))}
          >
            Cancelar tarefa
          </button>
        )}
      </div>
    </section>
  )
}

function ExecutionPlaceholder() {
  return (
    <section className="card" data-testid="execution-placeholder">
      <h3>
        <Play size={16} /> Execução
      </h3>
      <p>
        Execução mediada, testes, diff e auditoria chegam nas fases seguintes. Esta fase
        planeja e aprova — nenhum arquivo do seu repositório é escrito.
      </p>
    </section>
  )
}

function TaskPurgePanel({ task, onPurged }: { task: Task; onPurged: () => void }) {
  const [preview, setPreview] = useState<TaskPurgePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

  const loadPreview = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      setPreview(await getTaskPurgePreview(task.id))
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy(false)
    }
  }, [task.id])

  const confirm = useCallback(async () => {
    if (!preview) return
    setBusy(true)
    setError(null)
    try {
      await purgeTask(task.id, preview.purge_token)
      setConfirming(false)
      onPurged()
    } catch (caught) {
      setError(messageOf(caught))
      setConfirming(false)
      setPreview(null) // token consumido; força nova prévia
    } finally {
      setBusy(false)
    }
  }, [preview, task.id, onPurged])

  if (!TERMINAL.has(task.status)) {
    return (
      <section className="card">
        <h3>
          <ShieldAlert size={16} /> Purga destrutiva
        </h3>
        <p data-testid="purge-blocked">
          Só tarefas concluídas, com falha ou canceladas podem ser purgadas.
        </p>
      </section>
    )
  }

  return (
    <section className="card workspace-purge">
      <h3>
        <ShieldAlert size={16} /> Purga destrutiva
      </h3>
      <p>
        Remove as linhas desta tarefa no banco. Não desfaz e não toca o repositório no disco.
      </p>
      <div className="form-actions">
        <button type="button" className="button secondary" onClick={loadPreview} disabled={busy}>
          Carregar prévia da purga
        </button>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {preview?.benchmark_protected && (
        <p className="form-error" role="alert">
          Esta tarefa pertence a um grupo de benchmark com avaliação registrada. A purga será
          recusada.
        </p>
      )}
      {preview && (
        <table className="workspace-purge-preview">
          <caption>Será removido</caption>
          <tbody>
            {PURGE_ROWS.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.label}</th>
                <td>{preview[row.key] as number}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="form-actions">
        <button
          type="button"
          className="button danger"
          disabled={!preview || busy}
          onClick={() => setConfirming(true)}
        >
          <Trash2 size={15} /> Purgar tarefa
        </button>
      </div>
      {confirming && preview && (
        <Modal title="Confirmar purga da tarefa" onClose={() => setConfirming(false)}>
          <p className="confirm-copy">
            Purgar <strong>{task.title}</strong> remove {preview.tasks} tarefa,{' '}
            {preview.runs} execuções, {preview.findings} findings e {preview.manifests}{' '}
            manifests. Esta ação não pode ser desfeita.
          </p>
          <div className="form-actions">
            <button
              type="button"
              className="button secondary"
              onClick={() => setConfirming(false)}
            >
              Cancelar
            </button>
            <button type="button" className="button danger" onClick={confirm} disabled={busy}>
              {busy ? 'Purgando…' : 'Purgar definitivamente'}
            </button>
          </div>
        </Modal>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------- Task Detail

export function TaskDetail({
  taskId,
  onBack,
  workspaceStatus,
}: {
  taskId: string
  onBack: () => void
  // O status do workspace no cabeçalho, quando esta tela vive dentro dele. Reativar ou
  // arquivar ali muda `planning` desta task, e a tela relê a task UMA vez quando ele muda
  // — sem polling e sem consulta por renderização (E6-CONS4).
  workspaceStatus?: string
}) {
  const [state, setState] = useState<
    | { status: 'loading' }
    | { status: 'error'; message: string }
    | { status: 'ready'; task: Task }
  >({ status: 'loading' })

  const reload = useCallback(() => {
    let active = true
    getTask(taskId)
      .then((task) => active && setState({ status: 'ready', task }))
      .catch((error) => active && setState({ status: 'error', message: messageOf(error) }))
    return () => {
      active = false
    }
  }, [taskId])

  useEffect(() => reload(), [reload, workspaceStatus])

  if (state.status === 'loading') return <p role="status">Carregando tarefa…</p>
  if (state.status === 'error')
    return (
      <p className="form-error" role="alert">
        {state.message}
      </p>
    )

  const { task } = state

  return (
    <div className="task-detail">
      <button className="text-button back-button" onClick={onBack}>
        <ArrowLeft size={16} /> Voltar para tarefas
      </button>

      <section className="project-detail-hero card">
        <div>
          <span className="kicker">{task.execution_mode}</span>
          <h2>{task.title}</h2>
        </div>
        <div className="project-detail-actions">
          <span className={`status-pill task-${task.status}`} data-testid="task-status">
            {STATUS_LABEL[task.status] ?? task.status}
          </span>
        </div>
      </section>

      <div className="workspace-overview-grid">
        <PlanCard task={task} />
        <ContextCard taskId={task.id} status={task.status} planStanding={task.plan_standing} />
        <BindingsCard task={task} />
        <ApprovalCard task={task} onChanged={reload} />
        <ExecutionPlaceholder />
        <TaskPurgePanel task={task} onPurged={onBack} />
      </div>
    </div>
  )
}

// ------------------------------------------------------------------------ aba Tarefas

export function TasksTab({
  workspaceId,
  workspaceStatus,
}: {
  workspaceId: string
  workspaceStatus?: string
}) {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [goal, setGoal] = useState('')
  const [creating, setCreating] = useState(false)

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setTasks(await listTasks(workspaceId))
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setLoading(false)
    }
  }, [workspaceId])

  useEffect(() => {
    void reload()
  }, [reload])

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault()
      if (!title.trim() || !goal.trim()) return
      setCreating(true)
      setError(null)
      try {
        const created = await createTask(workspaceId, { title, goal })
        setTitle('')
        setGoal('')
        await reload()
        setSelected(created.id)
      } catch (caught) {
        setError(messageOf(caught))
      } finally {
        setCreating(false)
      }
    },
    [workspaceId, title, goal, reload],
  )

  if (selected) {
    return (
      <TaskDetail
        taskId={selected}
        workspaceStatus={workspaceStatus}
        onBack={() => {
          setSelected(null)
          void reload()
        }}
      />
    )
  }

  return (
    <div className="workspace-tasks">
      <section className="card">
        <h3>Nova tarefa</h3>
        <form onSubmit={submit}>
          <label className="field">
            <span>Título</span>
            <input value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label className="field">
            <span>Objetivo</span>
            <textarea value={goal} onChange={(event) => setGoal(event.target.value)} rows={3} />
          </label>
          <div className="form-actions">
            <button type="submit" className="button" disabled={creating}>
              Criar tarefa
            </button>
          </div>
        </form>
      </section>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {loading && <p role="status">Carregando tarefas…</p>}

      {!loading && tasks.length === 0 && <p>Nenhuma tarefa ainda.</p>}

      {tasks.length > 0 && (
        <ul className="task-list">
          {tasks.map((task) => (
            <li key={task.id}>
              <button type="button" className="text-button" onClick={() => setSelected(task.id)}>
                <strong>{task.title}</strong>{' '}
                <span className={`status-pill task-${task.status}`}>
                  {STATUS_LABEL[task.status] ?? task.status}
                </span>{' '}
                <small>risco {RISK_LABEL[task.risk] ?? task.risk}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
