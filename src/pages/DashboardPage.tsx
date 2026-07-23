import { AlertTriangle, ArrowRight, BriefcaseBusiness, CalendarCheck, Check, CheckCircle2, CircleDollarSign, Clock3, Flame, Play, RotateCw, Send, TimerReset, TrendingUp, Users } from 'lucide-react'
import { useState } from 'react'
import { useApp } from '../context/AppContext'
import { calculateStreak, contractMetrics, isTaskOverdue, proposalMetrics, taskMetrics } from '../utils/calculations'
import { ProgressRing } from '../components/ProgressRing'
import type { PageId } from '../components/Layout'
import { toDateInput } from '../data/roadmap'

const formatDate = (value: string) => new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' }).format(new Date(`${value}T12:00:00`))
const usd = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'USD' })
const brl = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })

export function DashboardPage({ navigate }: { navigate: (page: PageId) => void }) {
  const { data, updateTask, toggleTask, notify } = useApp()
  const [celebrate, setCelebrate] = useState(false)
  const now = new Date()
  const todayIso = toDateInput(now)
  const tasks = taskMetrics(data.tasks, now)
  const proposals = proposalMetrics(data.proposals)
  const contracts = contractMetrics(data.contracts)
  const todayTask = data.tasks.find((task) => (task.rescheduledDate ?? task.plannedDate) === todayIso && task.status !== 'Concluído') ?? data.tasks.find((task) => task.status !== 'Concluído') ?? data.tasks[89]
  const nextTasks = data.tasks.filter((task) => task.status !== 'Concluído').slice(0, 5)
  const currentWeek = todayTask.week
  const weekTasks = data.tasks.filter((task) => task.week === currentWeek)
  const weekCompleted = weekTasks.filter((task) => task.status === 'Concluído').length
  const weekProgress = Math.round(weekCompleted / weekTasks.length * 100)
  const alerts = (() => {
    const items: string[] = []
    const overdue = data.tasks.filter((task) => isTaskOverdue(task, now)).length
    const clientsDue = data.contracts.filter((item) => item.status === 'Em andamento' && item.deadline && new Date(`${item.deadline}T12:00:00`).getTime() - now.getTime() < 3 * 86_400_000).length
    const followUps = data.proposals.filter((item) => item.followUpDate && item.followUpDate <= todayIso && !['Contratado', 'Recusada', 'Ignorada'].includes(item.status)).length
    if (overdue) items.push(`${overdue} tarefa${overdue > 1 ? 's' : ''} precisa${overdue > 1 ? 'm' : ''} de atenção.`)
    if (clientsDue) items.push(`${clientsDue} prazo de cliente está próximo.`)
    if (followUps) items.push(`${followUps} proposta${followUps > 1 ? 's' : ''} aguardando acompanhamento.`)
    if (calculateStreak(data.tasks, now) === 0 && data.tasks.some((task) => task.status === 'Concluído')) items.push('Retome uma pequena ação hoje para recuperar o ritmo.')
    return items
  })()

  const complete = () => {
    toggleTask(todayTask.id); setCelebrate(true); notify('Meta concluída. Bom trabalho mantendo o ritmo.', 'success')
    window.setTimeout(() => setCelebrate(false), 1800)
  }
  const statCards = [
    { label: 'Tarefas concluídas', value: tasks.completed, icon: CheckCircle2, tone: 'green' }, { label: 'Em andamento', value: tasks.inProgress, icon: Play, tone: 'amber' },
    { label: 'Atrasadas', value: tasks.overdue, icon: TimerReset, tone: 'red' }, { label: 'Propostas enviadas', value: proposals.sent, icon: Send, tone: 'blue' },
    { label: 'Entrevistas', value: proposals.interviews, icon: Users, tone: 'violet' }, { label: 'Contratos', value: proposals.hired, icon: BriefcaseBusiness, tone: 'green' },
    { label: 'Líquido em dólar', value: usd.format(contracts.revenueUsd), icon: CircleDollarSign, tone: 'green' }, { label: 'Líquido em real', value: brl.format(contracts.revenueBrl), icon: TrendingUp, tone: 'blue' },
    { label: 'Horas trabalhadas', value: `${contracts.hours.toFixed(1)}h`, icon: Clock3, tone: 'amber' }, { label: 'Líquido médio/hora', value: usd.format(contracts.averageHourly), icon: TrendingUp, tone: 'violet' },
  ]
  return <div className="dashboard-page">
    <section className="welcome-row"><div><p className="kicker">{new Intl.DateTimeFormat('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' }).format(now)}</p><h2>{data.settings.userName ? `Olá, ${data.settings.userName}.` : 'Seu próximo passo está claro.'}</h2><p>Roadmap de 90 dias para começar a trabalhar como freelancer.</p></div><div className="streak"><Flame size={22} /><span><strong>{calculateStreak(data.tasks, now)} dias</strong> de sequência</span></div></section>
    <div className="dashboard-grid focus-grid">
      <section className={`card focus-card ${celebrate ? 'celebrate' : ''}`}>
        <div className="section-heading"><div><span className="section-label">Foco de hoje</span><h3>Dia {todayTask.day} · {todayTask.phase}</h3></div><span className={`status-pill status-${todayTask.status.toLowerCase().replaceAll(' ', '-')}`}>{todayTask.status}</span></div>
        <div className="focus-body"><div className="day-tile"><strong>{todayTask.day}</strong><span>de 90</span></div><div><p className="focus-title">{todayTask.title}</p><div className="meta-row"><span><CalendarCheck size={15} />{formatDate(todayTask.rescheduledDate ?? todayTask.plannedDate)}</span><span><Clock3 size={15} />{todayTask.estimatedMinutes} min</span><span className={`priority priority-${todayTask.priority.toLowerCase().replace('é', 'e')}`}>{todayTask.priority}</span></div></div></div>
        <label className="notes-field"><span>Observações</span><textarea value={todayTask.notes} onChange={(event) => updateTask(todayTask.id, { notes: event.target.value })} placeholder="Registre decisões, links ou o resultado da tarefa..." /></label>
        <div className="focus-actions"><button className="button secondary" onClick={() => updateTask(todayTask.id, { status: 'Em andamento' })}><Play size={17} /> Iniciar</button><button className="button primary" onClick={complete} disabled={todayTask.status === 'Concluído'}><Check size={17} /> Concluir</button><label className="button ghost reschedule"><RotateCw size={16} /> Reagendar<input type="date" aria-label="Nova data" onChange={(event) => event.target.value && updateTask(todayTask.id, { rescheduledDate: event.target.value, status: 'Adiado' })} /></label></div>
        {celebrate && <div className="celebration" aria-live="polite"><CheckCircle2 /> Meta concluída</div>}
      </section>
      <section className="card overall-card"><div className="section-heading"><div><span className="section-label">Visão geral</span><h3>Progresso total</h3></div></div><ProgressRing value={tasks.progress} /><p><strong>{tasks.completed}</strong> de 90 metas concluídas</p><div className="progress-track"><span style={{ width: `${tasks.progress}%` }} /></div><div className="overall-meta"><span>{tasks.remaining} restantes</span><span>Semana {currentWeek} de 13</span></div><button className="text-button" onClick={() => navigate('roadmap')}>Continuar de onde parei <ArrowRight size={16} /></button></section>
    </div>
    <section className="stats-grid" aria-label="Indicadores">{statCards.map(({ label, value, icon: Icon, tone }) => <article className="stat-card" key={label}><div className={`stat-icon ${tone}`}><Icon size={19} /></div><div><span>{label}</span><strong>{value}</strong></div></article>)}</section>
    <div className="dashboard-grid lower-grid">
      <section className="card progress-card"><div className="section-heading"><div><span className="section-label">Ritmo</span><h3>Progresso por semana</h3></div><span className="week-value">{weekProgress}% nesta semana</span></div><div className="week-bars">{Array.from({ length: 13 }, (_, index) => { const list = data.tasks.filter((task) => task.week === index + 1); const done = list.filter((task) => task.status === 'Concluído').length; const percent = done / list.length * 100; return <div className="week-bar-wrap" key={index}><div className="week-bar"><span style={{ height: `${Math.max(percent, 4)}%` }} className={index + 1 === currentWeek ? 'current' : ''} /></div><small>{index + 1}</small></div> })}</div><div className="chart-legend"><span><i className="legend-dot teal" /> Concluído</span><span><i className="legend-dot muted" /> Previsto</span></div></section>
      <section className="card next-card"><div className="section-heading"><div><span className="section-label">Em seguida</span><h3>Próximas metas</h3></div><button className="text-button" onClick={() => navigate('roadmap')}>Ver plano</button></div><div className="next-list">{nextTasks.map((task) => <button className="next-item" key={task.id} onClick={() => navigate('roadmap')}><span className="mini-day">{task.day}</span><span><strong>{task.title}</strong><small>Semana {task.week} · {task.estimatedMinutes} min</small></span><ArrowRight size={16} /></button>)}</div></section>
    </div>
    <section className={`alerts-card ${alerts.length ? '' : 'all-clear'}`}><div className="alerts-icon">{alerts.length ? <AlertTriangle size={20} /> : <CheckCircle2 size={20} />}</div><div><strong>{alerts.length ? 'Pontos de atenção' : 'Tudo sob controle'}</strong>{alerts.length ? <ul>{alerts.map((alert) => <li key={alert}>{alert}</li>)}</ul> : <p>Não há alertas no momento. Continue com uma meta de cada vez.</p>}</div></section>
  </div>
}
