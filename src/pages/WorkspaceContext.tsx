// Aba **Contexto** do workspace — o Context Registry (docs/architecture/03).
//
// O editor de entradas é modelado no `PlanningEditor` de `ProjectDetail.tsx`: mesmas seções
// em card, mesma lista editável de strings, mesmo indicador de "alterações não salvas" e o
// mesmo `UnsavedChangesModal` para sair sem salvar (docs/architecture/06, tabela de reuso).
//
// A regra que a tela precisa transmitir sem ambiguidade (docs/architecture/03 §3):
//
// - editar título/corpo/tags/estruturado **não** muda o estado de frescor da entrada;
// - só reapontar `source_refs` reconfirma a linha de base;
// - `Verificar` recalcula o veredito e **nunca** cura uma entrada `stale`.
//
// Por isso o formulário separa visualmente o bloco de `source_refs` do resto, e o botão de
// verificar diz o que faz e o que não faz.

import { AlertTriangle, CircleHelp, Download, Plus, RefreshCw, Save, ShieldCheck, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Modal, UnsavedChangesModal } from '../components/Modal'
import { useOptionalApp } from '../context/AppContext'
import {
  createContextEntry,
  deleteContextEntry,
  importPlanning,
  listContextEntries,
  updateContextEntry,
  verifyContext,
  type ContextDomain,
  type ContextEntry,
  type ContextVerifyResult,
  type PlanningImportInput,
} from '../services/contextApi'
import {
  buildEntryPatch,
  emptyEntryForm,
  entryFormOf,
  isFormDirty,
  messageOf,
  parseStructured,
  sameList,
  stateHint,
  stateLabel,
  stateTone,
  type EntryForm,
} from '../utils/contextEntries'
import { describePlanningSeed, toPlanningImport, totalSeedEntries } from '../utils/planningSeed'
import type { ProjectPlanning } from '../types'

const DOMAINS: { id: ContextDomain; label: string }[] = [
  { id: 'objective', label: 'Objetivo' },
  { id: 'architecture', label: 'Arquitetura' },
  { id: 'stack', label: 'Stack' },
  { id: 'requirements', label: 'Requisitos' },
  { id: 'modules', label: 'Módulos' },
  { id: 'decisions', label: 'Decisões' },
  { id: 'risks', label: 'Riscos' },
  { id: 'contracts', label: 'Contratos' },
]

const DOMAIN_LABEL = new Map(DOMAINS.map((item) => [item.id, item.label]))

// ------------------------------------------------------------------ indicador de estado

// Os quatro estados de docs/architecture/03 §3, cada um com rótulo, tom e explicação
// próprios. A lógica vive em `utils/contextEntries.ts`, testada isoladamente: é ela que
// garante que `stale` nunca apareça sem dizer **qual** dos dois motivos.
export function ContextStateBadge({ entry }: { entry: Pick<ContextEntry, 'state' | 'stale_reason'> }) {
  const tone = stateTone(entry)
  const Icon = entry.state === 'fresh' ? ShieldCheck : entry.state === 'unknown' ? CircleHelp : AlertTriangle
  return (
    <span className={`context-state context-state-${tone}`} title={stateHint(entry)}>
      <Icon size={14} aria-hidden="true" />
      {stateLabel(entry)}
    </span>
  )
}

// ------------------------------------------------------------------------- lista editável

// Mesma forma do `EditableStringList` do `PlanningEditor`, com rótulo e ajuda por campo:
// `source_refs` precisa de explicação que uma lista de requisitos não precisa.
function StringListField({
  legend,
  help,
  items,
  onChange,
  placeholder,
}: {
  legend: string
  help: string
  items: string[]
  onChange: (items: string[]) => void
  placeholder: string
}) {
  const [draft, setDraft] = useState('')
  const pending = draft.trim()

  const add = () => {
    if (!pending || items.includes(pending)) return
    onChange([...items, pending])
    setDraft('')
  }

  return (
    <fieldset className="planning-section card context-list-field">
      <legend>{legend}</legend>
      <p className="context-field-help">{help}</p>
      <div className="stack-entry">
        <input
          aria-label={legend}
          placeholder={placeholder}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              add()
            }
          }}
        />
        <button type="button" className="button secondary compact" onClick={add}>
          Adicionar
        </button>
      </div>
      {items.length === 0 ? (
        <p className="planning-empty">Nenhum item.</p>
      ) : (
        <div className="tag-list">
          {items.map((item) => (
            <span key={item}>
              {item}
              <button
                type="button"
                aria-label={`Remover ${item}`}
                onClick={() => onChange(items.filter((current) => current !== item))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </fieldset>
  )
}

// ------------------------------------------------------------------------------- editor

function ContextEntryEditor({
  workspaceId,
  entry,
  onSaved,
  onClose,
}: {
  workspaceId: string
  entry: ContextEntry | null
  onSaved: (saved: ContextEntry) => void
  onClose: () => void
}) {
  const baseline = useMemo(() => (entry ? entryFormOf(entry) : emptyEntryForm()), [entry])
  const [form, setForm] = useState<EntryForm>(baseline)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmingExit, setConfirmingExit] = useState(false)

  const dirty = isFormDirty(form, baseline)
  const set = <K extends keyof EntryForm>(key: K, value: EntryForm[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  const requestClose = () => (dirty ? setConfirmingExit(true) : onClose())

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const structured = parseStructured(form.structuredText)
    if (!structured.ok) {
      setError(structured.error)
      return
    }

    setBusy(true)
    setError(null)
    try {
      if (!entry) {
        onSaved(
          await createContextEntry(workspaceId, {
            domain: form.domain,
            title: form.title,
            body: form.body,
            structured: structured.value,
            tags: form.tags,
            source_refs: form.sourceRefs,
          }),
        )
      } else {
        // Campo ausente ≠ campo nulo (docs/architecture/03 §3): `source_refs` só vai no
        // corpo quando **mudou** — ver `buildEntryPatch`.
        onSaved(await updateContextEntry(entry.id, buildEntryPatch(form, baseline, structured.value)))
      }
      onClose()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy(false)
    }
  }

  const refsChanged = !sameList(form.sourceRefs, baseline.sourceRefs)

  return (
    <Modal title={entry ? 'Editar entrada de contexto' : 'Nova entrada de contexto'} size="large" onClose={requestClose}>
      <form className="planning-form context-entry-form" onSubmit={(event) => void submit(event)}>
        <section className="planning-section card">
          <label className="sr-label">
            Domínio *
            <select
              value={form.domain}
              disabled={entry !== null}
              onChange={(event) => set('domain', event.target.value as ContextDomain)}
            >
              {DOMAINS.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          {entry && <p className="context-field-help">O domínio não muda depois de criada: trocá-lo é criar outra entrada.</p>}
          <label className="sr-label">
            Título *
            <input required value={form.title} onChange={(event) => set('title', event.target.value)} />
          </label>
          <label className="sr-label">
            Conteúdo *
            <textarea
              required
              className="architecture-input"
              value={form.body}
              onChange={(event) => set('body', event.target.value)}
            />
          </label>
        </section>

        <StringListField
          legend="Tags"
          help="Rótulos livres para busca e filtro. Não entram no hash de conteúdo."
          items={form.tags}
          onChange={(items) => set('tags', items)}
          placeholder="Ex.: backend"
        />

        <fieldset className="planning-section card context-structured">
          <legend>Estruturado (JSON)</legend>
          <p className="context-field-help">
            Metadado tipado por domínio — <code>{'{decision, reason, date}'}</code> para decisões,{' '}
            <code>{'{mitigation}'}</code> para riscos. Deixe em branco se não se aplica. Entra no hash de
            conteúdo, porque é conteúdo autoral.
          </p>
          <label className="sr-label">
            Conteúdo estruturado
            <textarea
              className="architecture-input"
              placeholder='{"mitigation": "..."}'
              value={form.structuredText}
              onChange={(event) => set('structuredText', event.target.value)}
            />
          </label>
        </fieldset>

        <StringListField
          legend="Fontes (source_refs)"
          help="Caminhos ou globs relativos à raiz do workspace: src/app.py, src/**, docs/0?-*.md. Alterar esta lista reconfirma a linha de base contra a qual o código é comparado — editar o texto acima não."
          items={form.sourceRefs}
          onChange={(items) => set('sourceRefs', items)}
          placeholder="Ex.: src/**"
        />
        {entry && refsChanged && (
          <p className="context-baseline-warning" role="status">
            As fontes mudaram: salvar vai reconfirmar a linha de base no commit atual e recalcular o
            estado desta entrada.
          </p>
        )}

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}

        <div className="sticky-save">
          {dirty && (
            <span className="unsaved-indicator" role="status">
              ● Alterações não salvas
            </span>
          )}
          <button type="button" className="button secondary" onClick={requestClose} disabled={busy}>
            Cancelar
          </button>
          <button className="button primary" disabled={busy}>
            <Save size={16} /> {busy ? 'Salvando…' : 'Salvar entrada'}
          </button>
        </div>
      </form>
      {confirmingExit && (
        <UnsavedChangesModal onStay={() => setConfirmingExit(false)} onDiscard={onClose} />
      )}
    </Modal>
  )
}

// ------------------------------------------------------------------------------ import

// Uma origem de seed: o planejamento comercial e o rótulo com que o usuário o reconhece.
export interface PlanningChoice {
  planning: ProjectPlanning
  label: string
}

function ImportPanel({
  workspaceId,
  plannings,
  onImported,
  onClose,
}: {
  workspaceId: string
  plannings: PlanningChoice[]
  onImported: (created: number) => void
  onClose: () => void
}) {
  const [selectedId, setSelectedId] = useState(plannings[0]?.planning.id ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const chosen = plannings.find((item) => item.planning.id === selectedId)
  const seed: PlanningImportInput | null = chosen ? toPlanningImport(chosen.planning) : null
  const rows = seed ? describePlanningSeed(seed) : []
  const total = totalSeedEntries(rows)

  const confirm = async () => {
    if (!seed) return
    setBusy(true)
    setError(null)
    try {
      const result = await importPlanning(workspaceId, seed)
      onImported(result.created)
      onClose()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Importar planejamento" onClose={onClose}>
      {plannings.length === 0 ? (
        <p className="confirm-copy">
          Nenhum planejamento técnico disponível para importar. Preencha o planejamento de um projeto
          na área de Projetos primeiro.
        </p>
      ) : (
        <>
          <label className="sr-label">
            Planejamento de origem
            <select value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
              {plannings.map((item) => (
                <option key={item.planning.id} value={item.planning.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>

          <table className="context-import-preview">
            <caption>Prévia — será criado</caption>
            <tbody>
              {rows.map((row) => (
                <tr key={row.domain}>
                  <th scope="row">{row.label}</th>
                  <td>{row.count}</td>
                </tr>
              ))}
              <tr className="context-import-total">
                <th scope="row">Total de entradas</th>
                <td>{total}</td>
              </tr>
            </tbody>
          </table>

          <p className="context-field-help">
            A importação <strong>sempre cria entradas novas</strong> e nunca sobrescreve as
            existentes: os dois modelos evoluem de forma independente e não há sincronização de
            volta. Importar de novo produz um segundo conjunto.
          </p>
        </>
      )}

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="form-actions">
        <button type="button" className="button secondary" onClick={onClose} disabled={busy}>
          Cancelar
        </button>
        <button
          type="button"
          className="button primary"
          onClick={() => void confirm()}
          disabled={busy || total === 0}
        >
          {busy ? 'Importando…' : `Importar ${total} entrada(s)`}
        </button>
      </div>
    </Modal>
  )
}

// --------------------------------------------------------------------------------- aba

export function ContextTab({
  workspaceId,
  plannings: providedPlannings,
}: {
  workspaceId: string
  plannings?: PlanningChoice[]
}) {
  // `plannings` é a **única** entrada desta tela vinda do domínio comercial, e por isso é
  // um parâmetro explícito com origem padrão no contexto do app. Injetá-la deixa a fronteira
  // do ADR-0002 visível na assinatura, em vez de escondida num `useContext` no meio do
  // componente, e permite exercitar a importação sem montar o estado comercial inteiro.
  const app = useOptionalApp()
  const [entries, setEntries] = useState<ContextEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [verification, setVerification] = useState<ContextVerifyResult | null>(null)
  const [editing, setEditing] = useState<{ entry: ContextEntry | null } | null>(null)
  const [importing, setImporting] = useState(false)
  const [deleting, setDeleting] = useState<ContextEntry | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      setEntries(await listContextEntries(workspaceId))
    } catch (caught) {
      setError(messageOf(caught))
      setEntries([])
    }
  }, [workspaceId])

  useEffect(() => {
    void load()
  }, [load])

  const plannings = useMemo<PlanningChoice[]>(() => {
    if (providedPlannings) return providedPlannings
    if (!app) return []
    return app.data.projectPlannings.map((planning) => {
      const project = app.data.projects.find((item) => item.id === planning.projectId)
      return { planning, label: project ? project.name : `Planejamento ${planning.id.slice(0, 8)}` }
    })
  }, [app, providedPlannings])

  const runVerify = async () => {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const result = await verifyContext(workspaceId)
      setVerification(result)
      setEntries(result.entries)
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy(false)
    }
  }

  const confirmDelete = async () => {
    if (!deleting) return
    setBusy(true)
    try {
      await deleteContextEntry(deleting.id)
      setDeleting(null)
      await load()
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setBusy(false)
    }
  }

  const upsert = (saved: ContextEntry) => {
    setEntries((current) => {
      const list = current ?? []
      const index = list.findIndex((item) => item.id === saved.id)
      if (index < 0) return [...list, saved]
      const next = [...list]
      next[index] = saved
      return next
    })
    // Um `verify` anterior descreve um commit que pode não ser mais o atual; mantê-lo na
    // tela depois de uma escrita daria a impressão de que a divergência mostrada ainda vale.
    setVerification(null)
  }

  const covered = verification?.working_tree_divergence.covered ?? []

  return (
    <section className="workspace-context">
      <header className="context-toolbar">
        <div>
          <h3>Contexto do workspace</h3>
          <p>
            O conhecimento que os agentes recebem. Cada entrada declara quais arquivos descreve, e o
            estado diz se essa descrição ainda confere com o código.
          </p>
        </div>
        <div className="form-actions">
          <button type="button" className="button secondary" onClick={() => setImporting(true)} disabled={busy}>
            <Download size={15} /> Importar planejamento
          </button>
          <button type="button" className="button secondary" onClick={() => void runVerify()} disabled={busy}>
            <RefreshCw size={15} /> {busy ? 'Verificando…' : 'Verificar contexto'}
          </button>
          <button type="button" className="button primary" onClick={() => setEditing({ entry: null })} disabled={busy}>
            <Plus size={15} /> Nova entrada
          </button>
        </div>
      </header>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="context-notice" role="status">
          {notice}
        </p>
      )}

      {verification && (
        <section className="card context-verification" role="status">
          <h4>Última verificação</h4>
          <dl className="workspace-git-facts">
            <div>
              <dt>Commit verificado</dt>
              <dd>{verification.verification_commit ? verification.verification_commit.slice(0, 12) : 'sem commit (não é repositório Git)'}</dd>
            </div>
            <div>
              <dt>Divergência do working tree</dt>
              <dd>
                {verification.working_tree_divergence.dirty_file_count === null
                  ? 'indisponível'
                  : `${verification.working_tree_divergence.dirty_file_count} arquivo(s) divergente(s) no workspace`}
              </dd>
            </div>
            <div>
              <dt>Cobertos por alguma entrada</dt>
              <dd>{covered.length === 0 ? 'nenhum' : `${covered.length} caminho(s)`}</dd>
            </div>
          </dl>
          {covered.length > 0 && (
            <ul className="context-covered-list">
              {covered.map((item) => (
                <li key={`${item.entry_id}:${item.path}:${item.kind}`}>
                  <code>{item.path}</code> <small>{item.kind}</small>
                </li>
              ))}
            </ul>
          )}
          <p className="context-field-help">
            Verificar recalcula o veredito de cada entrada. Ele nunca move a linha de base: uma
            entrada desatualizada continua desatualizada até o código voltar ao que era, ou até as
            fontes serem reapontadas.
          </p>
        </section>
      )}

      {entries === null && <p role="status">Carregando entradas…</p>}
      {entries !== null && entries.length === 0 && (
        <div className="empty-state">
          <h3>Nenhuma entrada de contexto</h3>
          <p>Crie uma entrada manualmente ou importe um planejamento técnico já existente.</p>
        </div>
      )}

      {entries !== null && entries.length > 0 && (
        <ul className="context-entry-list">
          {entries.map((entry) => (
            <li key={entry.id} className="card context-entry">
              <header>
                <div>
                  <span className="kicker">{DOMAIN_LABEL.get(entry.domain) ?? entry.domain}</span>
                  <h4>{entry.title}</h4>
                </div>
                <ContextStateBadge entry={entry} />
              </header>
              <p className="pre-wrap context-entry-body">{entry.body}</p>
              <footer>
                <div className="context-entry-meta">
                  {entry.source_refs.length === 0 ? (
                    <span className="context-meta-chip">sem fontes declaradas</span>
                  ) : (
                    entry.source_refs.map((ref) => (
                      <span key={ref} className="context-meta-chip">
                        <code>{ref}</code>
                      </span>
                    ))
                  )}
                  {entry.origin === 'imported_planning' && (
                    <span className="context-meta-chip">importada do planejamento</span>
                  )}
                </div>
                <div className="form-actions">
                  <button type="button" className="button secondary compact" onClick={() => setEditing({ entry })}>
                    Editar
                  </button>
                  <button
                    type="button"
                    className="icon-button danger-icon"
                    aria-label={`Excluir ${entry.title}`}
                    onClick={() => setDeleting(entry)}
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </footer>
            </li>
          ))}
        </ul>
      )}

      {editing && (
        <ContextEntryEditor
          workspaceId={workspaceId}
          entry={editing.entry}
          onSaved={upsert}
          onClose={() => setEditing(null)}
        />
      )}

      {importing && (
        <ImportPanel
          workspaceId={workspaceId}
          plannings={plannings}
          onImported={(created) => {
            setNotice(`${created} entrada(s) importada(s).`)
            setVerification(null)
            void load()
          }}
          onClose={() => setImporting(false)}
        />
      )}

      {deleting && (
        <Modal title="Excluir entrada" onClose={() => setDeleting(null)}>
          <p className="confirm-copy">
            Excluir <strong>{deleting.title}</strong>? A exclusão é livre e não corrompe a auditoria
            histórica — o que já foi entregue a um agente fica preservado no artefato renderizado.
          </p>
          <div className="form-actions">
            <button type="button" className="button secondary" onClick={() => setDeleting(null)}>
              Cancelar
            </button>
            <button type="button" className="button danger" onClick={() => void confirmDelete()} disabled={busy}>
              Excluir
            </button>
          </div>
        </Modal>
      )}
    </section>
  )
}
