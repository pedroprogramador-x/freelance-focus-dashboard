import type { Contract, Proposal, RoadmapTask } from '../types'
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
  while (completedDays.has(toDateInput(cursor))) {
    streak += 1
    cursor.setDate(cursor.getDate() - 1)
  }
  return streak
}

export const netUsd = (contract: Contract) => contract.grossUsd * (1 - contract.platformFeePercent / 100)
export const netBrl = (contract: Contract) => netUsd(contract) * contract.exchangeRate
export const netHourly = (contract: Contract) => contract.hoursWorked > 0 ? netUsd(contract) / contract.hoursWorked : 0

export function contractMetrics(contracts: Contract[]) {
  const active = contracts.filter((item) => item.status !== 'Cancelado')
  const revenueUsd = active.reduce((sum, item) => sum + netUsd(item), 0)
  const revenueBrl = active.reduce((sum, item) => sum + netBrl(item), 0)
  const hours = active.reduce((sum, item) => sum + item.hoursWorked, 0)
  const currentMonth = toDateInput(new Date()).slice(0, 7)
  const monthlyRevenue = active.filter((item) => item.startDate.startsWith(currentMonth)).reduce((sum, item) => sum + netUsd(item), 0)
  const clients = new Set(active.map((item) => item.client.trim().toLowerCase()).filter(Boolean))
  const counts = active.reduce<Record<string, number>>((acc, item) => { const key = item.client.trim().toLowerCase(); if (key) acc[key] = (acc[key] ?? 0) + 1; return acc }, {})
  return { revenueUsd, revenueBrl, monthlyRevenue, hours, averageProject: active.length ? revenueUsd / active.length : 0, averageHourly: hours ? revenueUsd / hours : 0, clients: clients.size, recurringClients: Object.values(counts).filter((count) => count > 1).length }
}

export function proposalMetrics(proposals: Proposal[]) {
  const sent = proposals.filter((item) => item.status !== 'Salva').length
  const viewed = proposals.filter((item) => ['Visualizada', 'Entrevista', 'Contratado'].includes(item.status)).length
  const interviews = proposals.filter((item) => ['Entrevista', 'Contratado'].includes(item.status)).length
  const hired = proposals.filter((item) => item.status === 'Contratado').length
  return { total: proposals.length, sent, viewed, interviews, hired, viewRate: sent ? viewed / sent * 100 : 0, interviewRate: sent ? interviews / sent * 100 : 0, hireRate: sent ? hired / sent * 100 : 0, connects: proposals.reduce((sum, item) => sum + item.connects, 0), wonValue: proposals.filter((item) => item.status === 'Contratado').reduce((sum, item) => sum + item.budgetUsd, 0) }
}
