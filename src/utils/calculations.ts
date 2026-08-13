import type { Client, Currency, Project, ProjectTask, Proposal, RoadmapTask } from '../types'
import { toDateInput } from '../data/roadmap'

export const isTaskOverdue = (task: RoadmapTask, today = new Date()) => {
  if (task.status === 'Concluído' || task.status === 'Adiado') return false
  const target = task.rescheduledDate ?? task.plannedDate
  return new Date(`${target}T23:59:59`).getTime() < today.getTime()
}

export function taskMetrics(tasks: RoadmapTask[], today = new Date()) {
  const completed = tasks.filter((task) => task.status === 'Concluído').length
  const inProgress = tasks.filter((task) => task.status === 'Em andamento').length
  const overdue = tasks.filter((task) => isTaskOverdue(task, today)).length
  return { completed, inProgress, overdue, remaining: tasks.length - completed, progress: tasks.length ? Math.round((completed / tasks.length) * 100) : 0 }
}

export function calculateStreak(tasks: RoadmapTask[], today = new Date()) {
  const completedDays = new Set(tasks.filter((task) => task.completedAt).map((task) => toDateInput(new Date(task.completedAt!))))
  const cursor = new Date(today)
  if (!completedDays.has(toDateInput(cursor))) cursor.setDate(cursor.getDate() - 1)
  let streak = 0
  while (completedDays.has(toDateInput(cursor))) { streak += 1; cursor.setDate(cursor.getDate() - 1) }
  return streak
}

export function proposalMetrics(proposals: Proposal[]) {
  const sent = proposals.filter((item) => item.status !== 'Rascunho').length
  const open = proposals.filter((item) => ['Enviada', 'Aguardando resposta'].includes(item.status)).length
  const accepted = proposals.filter((item) => item.status === 'Aceita').length
  const platformConnects = proposals.reduce((sum, item) => sum + (item.platformData?.connects ?? 0), 0)
  return { total: proposals.length, sent, open, accepted, acceptanceRate: sent ? accepted / sent * 100 : 0, connects: platformConnects }
}

const totalsByCurrency = <T>(items: T[], value: (item: T) => number, currency: (item: T) => Currency) => items.reduce<Record<Currency, number>>((totals, item) => {
  totals[currency(item)] += value(item)
  return totals
}, { BRL: 0, USD: 0 })

export function projectMetrics(projects: Project[]) {
  const valid = projects.filter((item) => item.status !== 'Cancelado')
  const active = valid.filter((item) => !['Entregue', 'Cancelado', 'Pausado'].includes(item.status)).length
  const contracted = totalsByCurrency(valid, (item) => item.amount, (item) => item.currency)
  const received = totalsByCurrency(valid, (item) => item.amountReceived, (item) => item.currency)
  const pending = { BRL: contracted.BRL - received.BRL, USD: contracted.USD - received.USD }
  return { active, contracted, received, pending, hours: valid.reduce((sum, item) => sum + item.workedHours, 0) }
}

export function projectExecutionMetrics(projects: Project[], projectTasks: ProjectTask[], today = new Date()) {
  const start = toDateInput(today)
  const endDate = new Date(today)
  endDate.setDate(endDate.getDate() + 7)
  const end = toDateInput(endDate)
  return {
    pendingTasks: projectTasks.filter((task) => task.status === 'Pendente').length,
    blockedTasks: projectTasks.filter((task) => task.status === 'Bloqueado').length,
    projectsNearDeadline: projects.filter((project) => !['Entregue', 'Cancelado'].includes(project.status) && project.deadline && project.deadline >= start && project.deadline <= end).length,
  }
}

export function clientMetrics(clients: Client[]) {
  return { active: clients.filter((item) => item.status === 'Cliente ativo').length, leads: clients.filter((item) => item.status === 'Lead').length }
}

export function clientFinancials(clientId: string, projects: Project[]) {
  const related = projects.filter((item) => item.clientId === clientId && item.status !== 'Cancelado')
  const contracted = totalsByCurrency(related, (item) => item.amount, (item) => item.currency)
  const received = totalsByCurrency(related, (item) => item.amountReceived, (item) => item.currency)
  return { contracted, received }
}
