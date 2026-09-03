import { Boxes, Plus } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Modal } from '../components/Modal'
import { useWorkspaces } from '../context/WorkspaceProvider'
import { WorkspaceApiError, type WorkspaceCreateInput, type WorkspaceType } from '../services/workspaceApi'
import { WorkspaceDetail } from './WorkspaceDetail'

const WORKSPACE_TYPES: WorkspaceType[] = ['personal', 'freelance', 'study', 'experiment', 'open_source']

const emptyForm: WorkspaceCreateInput = {
  name: '',
  type: 'personal',
  local_path: '',
  linked_project_id: '',
  repository_url: '',
  default_branch: '',
}

function messageOf(error: unknown): string {
  if (error instanceof WorkspaceApiError) return error.message
  if (error instanceof Error) return error.message
  return 'Erro inesperado ao criar o workspace.'
}

function CreateWorkspaceForm({ onClose }: { onClose: () => void }) {
  const { createWorkspace } = useWorkspaces()
  const [form, setForm] = useState<WorkspaceCreateInput>(emptyForm)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = <K extends keyof WorkspaceCreateInput>(key: K, value: WorkspaceCreateInput[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!form.name.trim() || !form.local_path.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await createWorkspace({
        name: form.name.trim(),
        type: form.type,
        local_path: form.local_path.trim(),
        linked_project_id: form.linked_project_id?.trim() || null,
        repository_url: form.repository_url?.trim() || null,
        default_branch: form.default_branch?.trim() || null,
      })
      onClose()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="form-grid" onSubmit={submit}>
      <label className="wide">
        Nome *
        <input autoFocus required value={form.name} onChange={(event) => set('name', event.target.value)} />
      </label>
      <label>
        Tipo
        <select value={form.type} onChange={(event) => set('type', event.target.value as WorkspaceType)}>
          {WORKSPACE_TYPES.map((type) => (
            <option key={type} value={type}>{type}</option>
          ))}
        </select>
      </label>
      <label className="wide">
        Caminho local (absoluto) *
        <input
          required
          value={form.local_path}
          placeholder="C:\\Users\\voce\\projetos\\meu-projeto"
          onChange={(event) => set('local_path', event.target.value)}
        />
      </label>
      <label>
        Projeto vinculado (opcional)
        <input
          value={form.linked_project_id ?? ''}
          onChange={(event) => set('linked_project_id', event.target.value)}
        />
      </label>
      <label>
        Branch padrão (opcional)
        <input
          value={form.default_branch ?? ''}
          onChange={(event) => set('default_branch', event.target.value)}
        />
      </label>
      <label className="wide">
        URL do repositório (opcional)
        <input
          type="url"
          placeholder="https://github.com/..."
          value={form.repository_url ?? ''}
          onChange={(event) => set('repository_url', event.target.value)}
        />
      </label>
      {error && <p className="form-error wide" role="alert">{error}</p>}
      <div className="form-actions wide">
        <button type="button" className="button secondary" onClick={onClose}>Cancelar</button>
        <button className="button primary" disabled={submitting}>
          {submitting ? 'Registrando…' : 'Registrar workspace'}
        </button>
      </div>
    </form>
  )
}

function UnavailableState() {
  return (
    <div className="empty-state">
      <Boxes size={40} />
      <h3>Disponível apenas na execução local</h3>
      <p>
        O AI Dev Workspace roda com o backend local servindo esta interface. Na versão
        hospedada, esta área fica indisponível e nenhuma chamada à API é feita.
      </p>
    </div>
  )
}

export function DevWorkspaces() {
  const { enabled, workspaces, loading, error, refresh } = useWorkspaces()
  const [creating, setCreating] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  if (!enabled) {
    return (
      <div>
        <section className="page-intro">
          <div>
            <span className="kicker">AI Dev Workspace</span>
            <h2>Dev Workspaces</h2>
            <p>Registro de workspaces para planejamento e execução assistida.</p>
          </div>
        </section>
        <UnavailableState />
      </div>
    )
  }

  const selected = workspaces.find((item) => item.id === selectedId)
  if (selected) {
    return <WorkspaceDetail workspace={selected} onBack={() => { setSelectedId(null); void refresh() }} />
  }

  return (
    <div>
      <section className="page-intro">
        <div>
          <span className="kicker">AI Dev Workspace</span>
          <h2>Dev Workspaces</h2>
          <p>Registre um diretório local como workspace de planejamento e execução.</p>
        </div>
        <button className="button primary" onClick={() => setCreating(true)}>
          <Plus size={17} /> Novo workspace
        </button>
      </section>

      {error && <p className="form-error" role="alert">{error}</p>}
      {loading && <p role="status">Carregando workspaces…</p>}

      {!loading && workspaces.length === 0 ? (
        <div className="empty-state">
          <Boxes size={40} />
          <h3>Nenhum workspace registrado</h3>
          <p>Registre um diretório local para começar.</p>
          <button className="button primary" onClick={() => setCreating(true)}>
            <Plus size={17} /> Novo workspace
          </button>
        </div>
      ) : (
        <div className="contract-grid">
          {workspaces.map((workspace) => (
            <article className="card contract-card" key={workspace.id}>
              <header>
                <div>
                  <span>{workspace.type}</span>
                  <h3>{workspace.name}</h3>
                  <p className="pre-wrap">{workspace.local_path}</p>
                </div>
                <span className={`status-pill workspace-${workspace.status}`}>{workspace.status}</span>
              </header>
              <footer>
                <button className="text-button" onClick={() => setSelectedId(workspace.id)}>
                  Abrir workspace
                </button>
              </footer>
            </article>
          ))}
        </div>
      )}

      {creating && (
        <Modal title="Novo workspace" onClose={() => setCreating(false)} size="large">
          <CreateWorkspaceForm onClose={() => setCreating(false)} />
        </Modal>
      )}
    </div>
  )
}
