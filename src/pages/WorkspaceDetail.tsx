import { ArrowLeft, GitBranch, Loader2, ShieldAlert } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Modal } from '../components/Modal'
import { useWorkspaces } from '../context/WorkspaceProvider'
import {
  getGitPreflight,
  getPurgePreview,
  purgeWorkspace,
  WorkspaceApiError,
  type GitPreflight,
  type PurgePreview,
  type Workspace,
} from '../services/workspaceApi'

type WorkspaceTab = 'overview' | 'context' | 'tasks'

const TABS: { id: WorkspaceTab; label: string; enabled: boolean }[] = [
  { id: 'overview', label: 'Visão geral', enabled: true },
  { id: 'context', label: 'Contexto', enabled: false },
  { id: 'tasks', label: 'Tarefas', enabled: false },
]

const PURGE_ROWS: { key: keyof PurgePreview; label: string }[] = [
  { key: 'workspaces', label: 'Workspaces' },
  { key: 'tasks', label: 'Tarefas' },
  { key: 'runs', label: 'Execuções' },
  { key: 'findings', label: 'Findings' },
  { key: 'manifests', label: 'Manifests' },
  { key: 'artifacts', label: 'Artefatos' },
]

function messageOf(error: unknown): string {
  if (error instanceof WorkspaceApiError) return error.message
  if (error instanceof Error) return error.message
  return 'Erro inesperado.'
}

function GitPreflightCard({ workspaceId }: { workspaceId: string }) {
  const [state, setState] = useState<
    { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: GitPreflight }
  >({ status: 'loading' })

  useEffect(() => {
    let active = true
    setState({ status: 'loading' })
    getGitPreflight(workspaceId)
      .then((data) => active && setState({ status: 'ready', data }))
      .catch((error) => active && setState({ status: 'error', message: messageOf(error) }))
    return () => {
      active = false
    }
  }, [workspaceId])

  return (
    <section className="card">
      <h3><GitBranch size={16} /> Git preflight</h3>
      {state.status === 'loading' && <p role="status">Lendo o repositório…</p>}
      {state.status === 'error' && <p className="form-error" role="alert">{state.message}</p>}
      {state.status === 'ready' && !state.data.is_git_repo && (
        <p>
          Este diretório <strong>não é um repositório Git</strong>. O workspace serve como
          contexto; a execução de tarefas fica bloqueada até um <code>git init</code> feito
          manualmente, fora do backend.
        </p>
      )}
      {state.status === 'ready' && state.data.is_git_repo && (
        <dl className="workspace-git-facts">
          <div><dt>HEAD</dt><dd>{state.data.head ? state.data.head.slice(0, 12) : 'sem commits ainda'}</dd></div>
          <div><dt>Branch</dt><dd>{state.data.branch ?? 'HEAD desanexado'}</dd></div>
          <div>
            <dt>Divergência do working tree</dt>
            <dd>
              {state.data.dirty_file_count === null
                ? 'indisponível'
                : state.data.dirty_file_count === 0
                  ? 'árvore limpa'
                  : `${state.data.dirty_file_count} arquivo(s) não commitado(s)`}
            </dd>
          </div>
        </dl>
      )}
    </section>
  )
}

function PurgePanel({ workspace, onPurged }: { workspace: Workspace; onPurged: () => void }) {
  const [preview, setPreview] = useState<PurgePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)

  const loadPreview = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      setPreview(await getPurgePreview(workspace.id))
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy(false)
    }
  }, [workspace.id])

  const confirmPurge = useCallback(async () => {
    if (!preview) return
    setBusy(true)
    setError(null)
    try {
      await purgeWorkspace(workspace.id, preview.purge_token)
      setConfirming(false)
      onPurged()
    } catch (caught) {
      setError(messageOf(caught))
      setConfirming(false)
      setPreview(null) // token já foi consumido; força nova prévia
    } finally {
      setBusy(false)
    }
  }, [preview, workspace.id, onPurged])

  return (
    <section className="card workspace-purge">
      <h3><ShieldAlert size={16} /> Purga destrutiva</h3>
      <p>
        A purga remove as linhas do workspace no banco. Não desfaz e não toca o repositório
        no disco. Exige a prévia abaixo, visível nesta tela, antes de habilitar o botão.
      </p>
      <div className="form-actions">
        <button type="button" className="button secondary" onClick={loadPreview} disabled={busy}>
          {busy && !confirming ? <Loader2 size={15} className="spin" /> : null} Carregar prévia da purga
        </button>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
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
          Purgar workspace
        </button>
      </div>
      {confirming && preview && (
        <Modal title="Confirmar purga" onClose={() => setConfirming(false)}>
          <p className="confirm-copy">
            Purgar <strong>{workspace.name}</strong> remove {preview.workspaces} workspace,
            {' '}{preview.tasks} tarefas, {preview.runs} execuções, {preview.findings} findings,
            {' '}{preview.manifests} manifests e {preview.artifacts} artefatos. Esta ação não
            pode ser desfeita.
          </p>
          <div className="form-actions">
            <button type="button" className="button secondary" onClick={() => setConfirming(false)}>
              Cancelar
            </button>
            <button type="button" className="button danger" onClick={confirmPurge} disabled={busy}>
              {busy ? 'Purgando…' : 'Purgar definitivamente'}
            </button>
          </div>
        </Modal>
      )}
    </section>
  )
}

export function WorkspaceDetail({ workspace, onBack }: { workspace: Workspace; onBack: () => void }) {
  const { updateStatus } = useWorkspaces()
  const [tab, setTab] = useState<WorkspaceTab>('overview')
  const [statusBusy, setStatusBusy] = useState(false)
  const [statusError, setStatusError] = useState<string | null>(null)

  const toggleArchive = useCallback(async () => {
    setStatusBusy(true)
    setStatusError(null)
    try {
      await updateStatus(workspace.id, workspace.status === 'active' ? 'archived' : 'active')
    } catch (caught) {
      setStatusError(messageOf(caught))
    } finally {
      setStatusBusy(false)
    }
  }, [updateStatus, workspace.id, workspace.status])

  return (
    <div className="project-detail">
      <button className="text-button back-button" onClick={onBack}>
        <ArrowLeft size={16} /> Voltar para workspaces
      </button>

      <section className="project-detail-hero card">
        <div>
          <span className="kicker">{workspace.type}</span>
          <h2>{workspace.name}</h2>
          <p className="pre-wrap">{workspace.local_path}</p>
        </div>
        <div className="project-detail-actions">
          <span className={`status-pill workspace-${workspace.status}`}>{workspace.status}</span>
          <button className="button secondary" onClick={toggleArchive} disabled={statusBusy}>
            {workspace.status === 'active' ? 'Arquivar' : 'Reativar'}
          </button>
        </div>
      </section>
      {statusError && <p className="form-error" role="alert">{statusError}</p>}

      <div className="project-tabs" role="tablist" aria-label="Áreas do workspace">
        {TABS.map((item) => (
          <button
            key={item.id}
            role="tab"
            aria-selected={tab === item.id}
            aria-disabled={!item.enabled}
            disabled={!item.enabled}
            className={tab === item.id ? 'active' : ''}
            onClick={() => item.enabled && setTab(item.id)}
            title={item.enabled ? undefined : 'Disponível em fase futura'}
          >
            {item.label}
            {!item.enabled && <small> · fase futura</small>}
          </button>
        ))}
      </div>

      <section role="tabpanel">
        <div className="workspace-overview-grid">
          <GitPreflightCard workspaceId={workspace.id} />
          {workspace.status === 'archived' ? (
            <PurgePanel workspace={workspace} onPurged={onBack} />
          ) : (
            <section className="card">
              <h3><ShieldAlert size={16} /> Purga destrutiva</h3>
              <p>Arquive o workspace antes de poder purgá-lo.</p>
            </section>
          )}
        </div>
      </section>
    </div>
  )
}
