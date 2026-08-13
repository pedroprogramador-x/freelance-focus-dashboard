import { ArrowLeft, ExternalLink, Pencil, Plus, Save, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Modal, UnsavedChangesModal } from '../components/Modal'
import { ProgressRing } from '../components/ProgressRing'
import { useApp } from '../context/AppContext'
import { createEmptyProjectPlanning, filterProjectTasks, hasValidEntityReferences, projectTaskProgress } from '../data/domain'
import { toDateInput } from '../data/roadmap'
import type { Currency, Project, ProjectPlanning, ProjectTask, ProjectTaskPriority, ProjectTaskStatus, ProjectRisk, TechnicalDecision } from '../types'
import { hasUnsavedPlanningChanges } from '../utils/projectPlanning'

type ProjectTab = 'overview' | 'planning' | 'tasks' | 'notes'

const taskStatuses: ProjectTaskStatus[] = ['Pendente', 'Em andamento', 'Bloqueado', 'Concluído']
const taskPriorities: ProjectTaskPriority[] = ['Alta', 'Média', 'Baixa']
const timestamp = () => new Date().toISOString()
const dateNow = () => toDateInput(new Date())
const money = (value: number, currency: Currency) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency }).format(value)
const formatDate = (value: string | null) => value ? value.split('-').reverse().join('/') : 'Não informado'
const priorityClass = (priority: ProjectTaskPriority) => priority.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()

function EditableStringList({ title, items, onChange }: { title: string; items: string[]; onChange: (items: string[]) => void }) {
  const add = () => onChange([...items, ''])
  return <section className="planning-section card">
    <div className="planning-section-heading"><div><h3>{title}</h3><p>Registre itens objetivos do escopo.</p></div><button type="button" className="button secondary compact" onClick={add}><Plus size={15} /> Adicionar</button></div>
    {items.length === 0 ? <p className="planning-empty">Nenhum item cadastrado.</p> : <div className="editable-list">{items.map((item, index) => <div key={index}>
      <input aria-label={`${title} ${index + 1}`} value={item} onChange={(event) => onChange(items.map((current, itemIndex) => itemIndex === index ? event.target.value : current))} />
      <button type="button" className="icon-button danger-icon" aria-label={`Remover ${title.toLowerCase()} ${index + 1}`} onClick={() => onChange(items.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={15} /></button>
    </div>)}</div>}
  </section>
}

function PlanningEditor({ project, onDirtyChange }: { project: Project; onDirtyChange: (dirty: boolean) => void }) {
  const { data, upsert, notify } = useApp()
  const stored = data.projectPlannings.find((item) => item.projectId === project.id)
  const [persistedSnapshot, setPersistedSnapshot] = useState<ProjectPlanning>(() => stored ?? createEmptyProjectPlanning(project.id))
  const [form, setForm] = useState<ProjectPlanning>(() => persistedSnapshot)
  const [stackInput, setStackInput] = useState('')
  const pendingTechnology = stackInput.trim()
  const stackInputChanges = !!pendingTechnology && !form.stack.some((item) => item.toLocaleLowerCase('pt-BR') === pendingTechnology.toLocaleLowerCase('pt-BR'))
  const dirty = useMemo(() => hasUnsavedPlanningChanges(form, persistedSnapshot) || stackInputChanges, [form, persistedSnapshot, stackInputChanges])
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange])
  useEffect(() => () => onDirtyChange(false), [onDirtyChange])
  useEffect(() => {
    if (!dirty) return
    const protectUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', protectUnload)
    return () => window.removeEventListener('beforeunload', protectUnload)
  }, [dirty])
  const set = <K extends keyof ProjectPlanning>(key: K, value: ProjectPlanning[K]) => setForm((current) => ({ ...current, [key]: value }))
  const updateDecision = (id: string, changes: Partial<TechnicalDecision>) => set('technicalDecisions', form.technicalDecisions.map((item) => item.id === id ? { ...item, ...changes } : item))
  const updateRisk = (id: string, changes: Partial<ProjectRisk>) => set('risks', form.risks.map((item) => item.id === id ? { ...item, ...changes } : item))
  const addStack = () => {
    const technology = stackInput.trim()
    if (!technology || form.stack.some((item) => item.toLocaleLowerCase('pt-BR') === technology.toLocaleLowerCase('pt-BR'))) return
    set('stack', [...form.stack, technology])
    setStackInput('')
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const planning: ProjectPlanning = {
      ...form,
      functionalRequirements: form.functionalRequirements.map((item) => item.trim()).filter(Boolean),
      nonFunctionalRequirements: form.nonFunctionalRequirements.map((item) => item.trim()).filter(Boolean),
      stack: [...form.stack, ...(stackInputChanges ? [pendingTechnology] : [])].map((item) => item.trim()).filter(Boolean),
      technicalDecisions: form.technicalDecisions.map((item) => ({ ...item, title: item.title.trim() })).filter((item) => item.title),
      risks: form.risks.map((item) => ({ ...item, description: item.description.trim() })).filter((item) => item.description),
      updatedAt: timestamp(),
    }
    if (!hasValidEntityReferences(data, 'projectPlannings', planning)) { notify('Não foi possível salvar: o projeto ou a unicidade do planejamento é inválida.', 'error'); return }
    upsert('projectPlannings', planning)
    setForm(planning)
    setPersistedSnapshot(planning)
    setStackInput('')
    notify('Planejamento técnico salvo.', 'success')
  }
  return <form className="planning-form" onSubmit={submit}>
    <section className="planning-copy-grid">
      <label className="planning-section card"><span>Problema</span><strong>Qual problema do cliente este projeto resolve?</strong><textarea value={form.problem} onChange={(event) => set('problem', event.target.value)} /></label>
      <label className="planning-section card"><span>Objetivo</span><strong>Qual resultado o cliente espera alcançar?</strong><textarea value={form.objective} onChange={(event) => set('objective', event.target.value)} /></label>
    </section>
    <EditableStringList title="Requisitos funcionais" items={form.functionalRequirements} onChange={(items) => set('functionalRequirements', items)} />
    <EditableStringList title="Requisitos não funcionais" items={form.nonFunctionalRequirements} onChange={(items) => set('nonFunctionalRequirements', items)} />
    <section className="planning-section card">
      <div className="planning-section-heading"><div><h3>Stack</h3><p>Tecnologias utilizadas apenas neste projeto.</p></div></div>
      <div className="stack-entry"><input aria-label="Nova tecnologia" placeholder="Ex.: Python" value={stackInput} onChange={(event) => setStackInput(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addStack() } }} /><button type="button" className="button secondary compact" onClick={addStack}>Adicionar</button></div>
      {form.stack.length === 0 ? <p className="planning-empty">Nenhuma tecnologia cadastrada.</p> : <div className="tag-list">{form.stack.map((technology) => <span key={technology}>{technology}<button type="button" aria-label={`Remover ${technology}`} onClick={() => set('stack', form.stack.filter((item) => item !== technology))}>×</button></span>)}</div>}
    </section>
    <section className="planning-section card">
      <div className="planning-section-heading"><div><h3>Arquitetura</h3><p>Descreva o fluxo técnico em texto livre.</p></div></div>
      <label className="sr-label">Descrição da arquitetura<textarea className="architecture-input" placeholder={'Arquivo de entrada\n↓\nValidação\n↓\nProcessamento\n↓\nResultado'} value={form.architecture} onChange={(event) => set('architecture', event.target.value)} /></label>
    </section>
    <section className="planning-section card">
      <div className="planning-section-heading"><div><h3>Decisões técnicas</h3><p>Registre o que foi decidido e o motivo.</p></div><button type="button" className="button secondary compact" onClick={() => set('technicalDecisions', [...form.technicalDecisions, { id: crypto.randomUUID(), title: '', decision: '', reason: '' }])}><Plus size={15} /> Adicionar</button></div>
      {form.technicalDecisions.length === 0 ? <p className="planning-empty">Nenhuma decisão cadastrada.</p> : <div className="nested-records">{form.technicalDecisions.map((item, index) => <article key={item.id}>
        <header><strong>Decisão {index + 1}</strong><button type="button" className="icon-button danger-icon" aria-label={`Excluir decisão ${index + 1}`} onClick={() => set('technicalDecisions', form.technicalDecisions.filter((decision) => decision.id !== item.id))}><Trash2 size={15} /></button></header>
        <label>Título *<input required value={item.title} onChange={(event) => updateDecision(item.id, { title: event.target.value })} /></label>
        <label>Decisão<textarea value={item.decision} onChange={(event) => updateDecision(item.id, { decision: event.target.value })} /></label>
        <label>Motivo<textarea value={item.reason} onChange={(event) => updateDecision(item.id, { reason: event.target.value })} /></label>
      </article>)}</div>}
    </section>
    <section className="planning-section card">
      <div className="planning-section-heading"><div><h3>Riscos</h3><p>Antecipe riscos e registre uma mitigação simples.</p></div><button type="button" className="button secondary compact" onClick={() => set('risks', [...form.risks, { id: crypto.randomUUID(), description: '', mitigation: '' }])}><Plus size={15} /> Adicionar</button></div>
      {form.risks.length === 0 ? <p className="planning-empty">Nenhum risco cadastrado.</p> : <div className="nested-records">{form.risks.map((item, index) => <article key={item.id}>
        <header><strong>Risco {index + 1}</strong><button type="button" className="icon-button danger-icon" aria-label={`Excluir risco ${index + 1}`} onClick={() => set('risks', form.risks.filter((risk) => risk.id !== item.id))}><Trash2 size={15} /></button></header>
        <label>Descrição *<textarea required value={item.description} onChange={(event) => updateRisk(item.id, { description: event.target.value })} /></label>
        <label>Mitigação<textarea value={item.mitigation} onChange={(event) => updateRisk(item.id, { mitigation: event.target.value })} /></label>
      </article>)}</div>}
    </section>
    <div className="sticky-save">{dirty && <span className="unsaved-indicator" role="status">● Alterações não salvas</span>}<button className="button primary"><Save size={16} /> Salvar planejamento</button></div>
  </form>
}

const emptyProjectTask = (projectId: string): ProjectTask => ({ id: crypto.randomUUID(), projectId, title: '', description: '', status: 'Pendente', priority: 'Média', deadline: null, completedAt: null, createdAt: timestamp(), updatedAt: timestamp() })

function ProjectTaskForm({ initial, onClose }: { initial: ProjectTask; onClose: () => void }) {
  const { data, upsert, notify } = useApp()
  const [form, setForm] = useState(initial)
  const set = <K extends keyof ProjectTask>(key: K, value: ProjectTask[K]) => setForm((current) => ({ ...current, [key]: value }))
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!form.title.trim()) return
    const task: ProjectTask = { ...form, title: form.title.trim(), completedAt: form.status === 'Concluído' ? (form.completedAt ?? dateNow()) : null, updatedAt: timestamp() }
    if (!hasValidEntityReferences(data, 'projectTasks', task)) { notify('Não foi possível salvar: o projeto relacionado não existe.', 'error'); return }
    upsert('projectTasks', task)
    notify('Tarefa do projeto salva.', 'success')
    onClose()
  }
  return <form className="form-grid" onSubmit={submit}>
    <label className="wide">Título *<input autoFocus required value={form.title} onChange={(event) => set('title', event.target.value)} /></label>
    <label className="wide">Descrição<textarea value={form.description} onChange={(event) => set('description', event.target.value)} /></label>
    <label>Status<select value={form.status} onChange={(event) => set('status', event.target.value as ProjectTaskStatus)}>{taskStatuses.map((item) => <option key={item}>{item}</option>)}</select></label>
    <label>Prioridade<select value={form.priority} onChange={(event) => set('priority', event.target.value as ProjectTaskPriority)}>{taskPriorities.map((item) => <option key={item}>{item}</option>)}</select></label>
    <label>Prazo<input type="date" value={form.deadline ?? ''} onChange={(event) => set('deadline', event.target.value || null)} /></label>
    <div className="form-actions wide"><button type="button" className="button secondary" onClick={onClose}>Cancelar</button><button className="button primary">Salvar tarefa</button></div>
  </form>
}

function ProjectTasks({ project }: { project: Project }) {
  const { data, upsert, remove, notify } = useApp()
  const tasks = data.projectTasks.filter((item) => item.projectId === project.id)
  const [statusFilter, setStatusFilter] = useState<'' | ProjectTaskStatus>('')
  const [priorityFilter, setPriorityFilter] = useState<'' | ProjectTaskPriority>('')
  const [editing, setEditing] = useState<ProjectTask | null>(null)
  const [deleting, setDeleting] = useState<ProjectTask | null>(null)
  const filtered = filterProjectTasks(tasks, statusFilter, priorityFilter)
  const progress = projectTaskProgress(tasks)
  const update = (task: ProjectTask, changes: Partial<ProjectTask>) => {
    const status = changes.status ?? task.status
    upsert('projectTasks', { ...task, ...changes, completedAt: status === 'Concluído' ? (task.completedAt ?? dateNow()) : null, updatedAt: timestamp() })
  }
  const requestDelete = (task: ProjectTask) => {
    if (data.settings.confirmBeforeDelete) setDeleting(task)
    else { remove('projectTasks', task.id); notify('Tarefa excluída.') }
  }
  return <div className="project-tasks-area">
    <section className="task-progress-card card">
      {progress.percentage === null ? <div><strong>Nenhuma tarefa cadastrada</strong><p>Adicione tarefas para acompanhar o progresso automaticamente.</p></div> : <><ProgressRing value={progress.percentage} size={92} /><div><strong>{progress.completed} / {progress.total} concluídas</strong><p>O progresso é calculado pelas tarefas concluídas.</p></div></>}
      <button className="button primary" onClick={() => setEditing(emptyProjectTask(project.id))}><Plus size={16} /> Nova tarefa</button>
    </section>
    <section className="project-task-filters card" aria-label="Filtros de tarefas">
      <label>Status<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as '' | ProjectTaskStatus)}><option value="">Todos</option>{taskStatuses.map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Prioridade<select value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value as '' | ProjectTaskPriority)}><option value="">Todas</option>{taskPriorities.map((item) => <option key={item}>{item}</option>)}</select></label>
      <span>{filtered.length} de {tasks.length} tarefas</span>
    </section>
    {filtered.length === 0 ? <div className="empty-state compact-empty"><h3>{tasks.length ? 'Nenhuma tarefa corresponde aos filtros' : 'Comece pelas próximas ações'}</h3><p>{tasks.length ? 'Ajuste os filtros para ver outras tarefas.' : 'Crie uma lista simples e acompanhe o andamento sem progresso manual.'}</p></div> : <div className="project-task-list">{filtered.map((task) => <article className={`card project-task-item ${task.status === 'Concluído' ? 'completed' : ''}`} key={task.id}>
      <div className="project-task-main"><div className="task-title-row"><span className={`priority-chip priority-${priorityClass(task.priority)}`}>{task.priority}</span><h3>{task.title}</h3></div>{task.description && <p>{task.description}</p>}<small>Prazo: {formatDate(task.deadline)}{task.completedAt ? ` · Concluída em ${formatDate(task.completedAt)}` : ''}</small></div>
      <label>Status<select aria-label={`Status de ${task.title}`} value={task.status} onChange={(event) => update(task, { status: event.target.value as ProjectTaskStatus })}>{taskStatuses.map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Prioridade<select aria-label={`Prioridade de ${task.title}`} value={task.priority} onChange={(event) => update(task, { priority: event.target.value as ProjectTaskPriority })}>{taskPriorities.map((item) => <option key={item}>{item}</option>)}</select></label>
      <div className="task-actions"><button className="icon-button" aria-label={`Editar ${task.title}`} onClick={() => setEditing(task)}><Pencil size={15} /></button><button className="icon-button danger-icon" aria-label={`Excluir ${task.title}`} onClick={() => requestDelete(task)}><Trash2 size={15} /></button></div>
    </article>)}</div>}
    {editing && <Modal title={tasks.some((item) => item.id === editing.id) ? 'Editar tarefa' : 'Nova tarefa'} onClose={() => setEditing(null)}><ProjectTaskForm initial={editing} onClose={() => setEditing(null)} /></Modal>}
    {deleting && <Modal title="Excluir tarefa?" onClose={() => setDeleting(null)}><p className="confirm-copy">A tarefa “{deleting.title}” será removida deste projeto.</p><div className="form-actions"><button className="button secondary" onClick={() => setDeleting(null)}>Cancelar</button><button className="button danger" onClick={() => { remove('projectTasks', deleting.id); notify('Tarefa excluída.'); setDeleting(null) }}>Confirmar</button></div></Modal>}
  </div>
}

function ProjectOverview({ project }: { project: Project }) {
  const { data } = useApp()
  const tasks = data.projectTasks.filter((item) => item.projectId === project.id)
  const progress = projectTaskProgress(tasks)
  const nextTasks = tasks.filter((item) => item.status !== 'Concluído').sort((a, b) => (a.deadline ?? '9999').localeCompare(b.deadline ?? '9999')).slice(0, 3)
  return <div className="project-overview-grid">
    <section className="card project-summary-card"><h3>Resumo financeiro</h3><div className="project-financial-grid"><div><span>Valor</span><strong>{money(project.amount, project.currency)}</strong></div><div><span>Recebido</span><strong>{money(project.amountReceived, project.currency)}</strong></div><div><span>Pendente</span><strong>{money(project.amount - project.amountReceived, project.currency)}</strong></div></div><p>Pagamento: <strong>{project.paymentStatus}</strong></p></section>
    <section className="card project-summary-card"><h3>Progresso técnico</h3>{progress.percentage === null ? <p>Nenhuma tarefa cadastrada.</p> : <div className="overview-progress"><ProgressRing value={progress.percentage} size={92} /><div><strong>{progress.completed} / {progress.total} concluídas</strong><p>Calculado automaticamente.</p></div></div>}</section>
    <section className="card project-summary-card"><h3>Datas e horas</h3><dl><div><dt>Início</dt><dd>{formatDate(project.startDate)}</dd></div><div><dt>Prazo</dt><dd>{formatDate(project.deadline)}</dd></div><div><dt>Conclusão</dt><dd>{formatDate(project.completedAt)}</dd></div><div><dt>Horas</dt><dd>{project.workedHours.toFixed(1)}h / {project.estimatedHours.toFixed(1)}h</dd></div></dl></section>
    <section className="card project-summary-card"><h3>Links</h3><div className="detail-links">{project.repositoryUrl ? <a href={project.repositoryUrl} target="_blank" rel="noreferrer"><ExternalLink size={15} /> Repositório</a> : <span>Repositório não informado</span>}{project.productionUrl ? <a href={project.productionUrl} target="_blank" rel="noreferrer"><ExternalLink size={15} /> Produção</a> : <span>Produção não informada</span>}</div></section>
    <section className="card project-summary-card wide-summary"><h3>Próximos itens</h3>{nextTasks.length ? <ul>{nextTasks.map((task) => <li key={task.id}><span>{task.title}</span><strong>{task.deadline ? formatDate(task.deadline) : task.status}</strong></li>)}</ul> : <p>Nenhuma tarefa pendente.</p>}</section>
  </div>
}

export function ProjectDetail({ project, onBack, onEdit, onDelete, onPlanningDirtyChange }: { project: Project; onBack: () => void; onEdit: () => void; onDelete: () => void; onPlanningDirtyChange?: (dirty: boolean) => void }) {
  const { data } = useApp()
  const [tab, setTab] = useState<ProjectTab>('overview')
  const [planningDirty, setPlanningDirty] = useState(false)
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null)
  const handlePlanningDirtyChange = useCallback((dirty: boolean) => setPlanningDirty(dirty), [])
  useEffect(() => { onPlanningDirtyChange?.(planningDirty) }, [onPlanningDirtyChange, planningDirty])
  useEffect(() => () => onPlanningDirtyChange?.(false), [onPlanningDirtyChange])
  const client = data.clients.find((item) => item.id === project.clientId)
  const tasks = useMemo(() => data.projectTasks.filter((item) => item.projectId === project.id), [data.projectTasks, project.id])
  const progress = projectTaskProgress(tasks)
  const tabs: { id: ProjectTab; label: string }[] = [{ id: 'overview', label: 'Visão geral' }, { id: 'planning', label: 'Planejamento' }, { id: 'tasks', label: `Tarefas (${tasks.length})` }, { id: 'notes', label: 'Notas' }]
  const requestNavigation = (action: () => void) => {
    if (planningDirty) { setPendingNavigation(() => action); return }
    action()
  }
  const discardAndNavigate = () => {
    const action = pendingNavigation
    setPendingNavigation(null)
    setPlanningDirty(false)
    action?.()
  }
  return <div className="project-detail">
    <button className="text-button back-button" onClick={() => requestNavigation(onBack)}><ArrowLeft size={16} /> Voltar para projetos</button>
    <section className="project-detail-hero card"><div><span className="kicker">{client?.name ?? 'Cliente indisponível'}</span><h2>{project.name}</h2><p>{project.description || 'Sem descrição cadastrada.'}</p></div><div className="project-detail-actions"><span className={`status-pill project-${project.status.toLowerCase().replaceAll(' ', '-')}`}>{project.status}</span><button className="button secondary" onClick={onEdit}><Pencil size={15} /> Editar</button><button className="icon-button danger-icon" aria-label="Excluir projeto" onClick={onDelete}><Trash2 size={16} /></button></div><div className="project-hero-metrics"><div><span>Prazo</span><strong>{formatDate(project.deadline)}</strong></div><div><span>Valor</span><strong>{money(project.amount, project.currency)}</strong></div><div><span>Recebido</span><strong>{money(project.amountReceived, project.currency)}</strong></div><div><span>Pendente</span><strong>{money(project.amount - project.amountReceived, project.currency)}</strong></div><div><span>Horas</span><strong>{project.workedHours.toFixed(1)}h / {project.estimatedHours.toFixed(1)}h</strong></div><div><span>Progresso</span><strong>{progress.percentage === null ? 'Sem tarefas' : `${progress.percentage}%`}</strong></div></div></section>
    <div className="project-tabs" role="tablist" aria-label="Áreas do projeto">{tabs.map((item) => <button key={item.id} role="tab" aria-selected={tab === item.id} className={tab === item.id ? 'active' : ''} onClick={() => { if (item.id !== tab) requestNavigation(() => setTab(item.id)) }}>{item.label}</button>)}</div>
    <section role="tabpanel">{tab === 'overview' && <ProjectOverview project={project} />}{tab === 'planning' && <PlanningEditor project={project} onDirtyChange={handlePlanningDirtyChange} />}{tab === 'tasks' && <ProjectTasks project={project} />}{tab === 'notes' && <section className="card project-notes"><header><div><h3>Notas do projeto</h3><p>Contexto livre mantido no cadastro principal.</p></div><button className="button secondary" onClick={onEdit}><Pencil size={15} /> Editar notas</button></header><p className="pre-wrap">{project.notes || 'Nenhuma nota cadastrada.'}</p></section>}</section>
    {pendingNavigation && <UnsavedChangesModal onStay={() => setPendingNavigation(null)} onDiscard={discardAndNavigate} />}
  </div>
}
