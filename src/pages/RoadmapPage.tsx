import { CalendarDays, Check, Clock3, Grid3X3, List, Play, RotateCcw, Search, SlidersHorizontal } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useApp } from '../context/AppContext'
import { WEEK_PHASES } from '../data/roadmap'
import { EMPTY_TASK_FILTERS, filterTasks, type TaskFilters } from '../utils/filters'
import { isTaskOverdue, taskMetrics } from '../utils/calculations'
import type { RoadmapTask, TaskStatus } from '../types'

export function RoadmapPage() {
  const { data, updateTask, toggleTask, notify } = useApp()
  const [filters, setFilters] = useState<TaskFilters>(EMPTY_TASK_FILTERS)
  const [view, setView] = useState<'list' | 'weeks'>('list')
  const tasks = useMemo(() => filterTasks(data.tasks, filters), [data.tasks, filters])
  const metrics = taskMetrics(data.tasks)
  const updateFilter = <K extends keyof TaskFilters>(key: K, value: TaskFilters[K]) => setFilters((current) => ({ ...current, [key]: value }))
  const act = (task: RoadmapTask, status: TaskStatus) => {
    updateTask(task.id, { status, completedAt: status === 'Concluído' ? new Date().toISOString() : null })
    notify(status === 'Concluído' ? 'Tarefa concluída e salva.' : 'Status atualizado.', status === 'Concluído' ? 'success' : 'info')
  }
  const taskCard = (task: RoadmapTask) => <article className={`roadmap-task ${task.status === 'Concluído' ? 'completed' : ''}`} key={task.id} id={task.id}>
    <button className="task-check" onClick={() => toggleTask(task.id)} aria-label={task.status === 'Concluído' ? 'Desfazer conclusão' : 'Concluir tarefa'}>{task.status === 'Concluído' && <Check size={16} />}</button>
    <div className="task-day"><strong>{task.day}</strong><small>Dia</small></div>
    <div className="task-main"><div className="task-badges"><span>Semana {task.week}</span><span>{task.phase}</span>{isTaskOverdue(task) && <span className="overdue-badge">Atrasada</span>}</div><h3>{task.title}</h3><p>{task.description}</p><div className="task-meta"><span><Clock3 size={14} /> {task.estimatedMinutes} min</span><span className={`priority priority-${task.priority.toLowerCase().replace('é', 'e')}`}>{task.priority}</span><select aria-label={`Status da tarefa ${task.day}`} value={task.status} onChange={(event) => act(task, event.target.value as TaskStatus)}><option>Pendente</option><option>Em andamento</option><option>Concluído</option><option>Adiado</option></select></div>
      <details><summary>Observações e reagendamento</summary><div className="task-edit-row"><textarea aria-label={`Observações da tarefa ${task.day}`} placeholder="Adicione observações..." value={task.notes} onChange={(event) => updateTask(task.id, { notes: event.target.value })} /><label>Nova data<input type="date" value={task.rescheduledDate ?? ''} onChange={(event) => updateTask(task.id, { rescheduledDate: event.target.value || null, status: event.target.value ? 'Adiado' : task.status })} /></label></div></details>
    </div>
    <div className="task-actions"><button className="icon-button" onClick={() => act(task, 'Em andamento')} aria-label="Iniciar tarefa"><Play size={17} /></button><button className="icon-button" onClick={() => act(task, 'Pendente')} aria-label="Voltar para pendente"><RotateCcw size={17} /></button></div>
  </article>
  return <div>
    <section className="page-intro"><div><span className="kicker">Jornada completa</span><h2>Plano de 90 dias</h2><p>Uma ação prática por dia, da fundação ao próximo ciclo.</p></div><button className="button primary" onClick={() => { setFilters({ ...EMPTY_TASK_FILTERS, todayOnly: true }); document.querySelector('.filters-panel')?.scrollIntoView({ behavior: 'smooth' }) }}><CalendarDays size={17} /> Ir para a tarefa de hoje</button></section>
    <section className="roadmap-summary"><div><strong>{metrics.progress}%</strong><span>progresso geral</span></div><div className="summary-progress"><span style={{ width: `${metrics.progress}%` }} /></div><div><strong>{metrics.completed}</strong><span>concluídas</span></div><div><strong>{metrics.remaining}</strong><span>restantes</span></div><div><strong>{metrics.overdue}</strong><span>atrasadas</span></div></section>
    <section className="filters-panel card">
      <div className="search-field"><Search size={18} /><input type="search" placeholder="Pesquisar nas 90 metas..." value={filters.search} onChange={(event) => updateFilter('search', event.target.value)} /></div>
      <div className="filter-row"><select aria-label="Filtrar por semana" value={filters.week} onChange={(event) => updateFilter('week', event.target.value)}><option value="">Todas as semanas</option>{Array.from({ length: 13 }, (_, i) => <option key={i} value={i + 1}>Semana {i + 1}</option>)}</select><select aria-label="Filtrar por fase" value={filters.phase} onChange={(event) => updateFilter('phase', event.target.value)}><option value="">Todas as fases</option>{WEEK_PHASES.map((phase) => <option key={phase}>{phase}</option>)}</select><select aria-label="Filtrar por prioridade" value={filters.priority} onChange={(event) => updateFilter('priority', event.target.value as TaskFilters['priority'])}><option value="">Todas as prioridades</option><option>Alta</option><option>Média</option><option>Baixa</option></select><select aria-label="Filtrar por status" value={filters.status} onChange={(event) => updateFilter('status', event.target.value as TaskFilters['status'])}><option value="">Todos os status</option><option>Pendente</option><option>Em andamento</option><option>Concluído</option><option>Adiado</option></select></div>
      <div className="filter-foot"><div className="toggle-filters"><label><input type="checkbox" checked={filters.todayOnly} onChange={(event) => updateFilter('todayOnly', event.target.checked)} /> Somente hoje</label><label><input type="checkbox" checked={filters.overdueOnly} onChange={(event) => updateFilter('overdueOnly', event.target.checked)} /> Somente atrasadas</label>{JSON.stringify(filters) !== JSON.stringify(EMPTY_TASK_FILTERS) && <button className="text-button" onClick={() => setFilters(EMPTY_TASK_FILTERS)}>Limpar filtros</button>}</div><div className="view-toggle"><button className={view === 'list' ? 'active' : ''} aria-pressed={view === 'list'} onClick={() => setView('list')} aria-label="Visualização em lista"><List size={17} /></button><button className={view === 'weeks' ? 'active' : ''} aria-pressed={view === 'weeks'} onClick={() => setView('weeks')} aria-label="Visualização por semanas"><Grid3X3 size={17} /></button></div></div>
    </section>
    <div className="results-count"><SlidersHorizontal size={15} /> {tasks.length} meta{tasks.length !== 1 ? 's' : ''} encontrada{tasks.length !== 1 ? 's' : ''}</div>
    {tasks.length === 0 ? <div className="empty-state"><Search size={34} /><h3>Nenhuma meta encontrada</h3><p>Ajuste os filtros para voltar ao seu plano.</p><button className="button secondary" onClick={() => setFilters(EMPTY_TASK_FILTERS)}>Limpar filtros</button></div> : view === 'list' ? <div className="roadmap-list">{tasks.map(taskCard)}</div> : <div className="weeks-grid">{Array.from(new Set(tasks.map((task) => task.week))).map((week) => <section className="week-column card" key={week}><header><span>Semana {week}</span><strong>{WEEK_PHASES[week - 1]}</strong></header>{tasks.filter((task) => task.week === week).map(taskCard)}</section>)}</div>}
  </div>
}
