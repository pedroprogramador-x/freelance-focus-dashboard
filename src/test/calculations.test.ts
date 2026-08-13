import { describe, expect, it } from 'vitest'
import { createRoadmap } from '../data/roadmap'
import type { Client, Project, ProjectTask, Proposal } from '../types'
import { clientFinancials, clientMetrics, isTaskOverdue, projectExecutionMetrics, projectMetrics, proposalMetrics, taskMetrics } from '../utils/calculations'

const client: Client = { id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Contato direto', referredBy: '', status: 'Cliente ativo', notes: '', createdAt: '2026-01-01', updatedAt: '2026-01-01' }
const proposal: Proposal = { id: 'proposal-1', clientId: client.id, serviceId: null, title: 'API', description: '', amount: 1000, currency: 'USD', source: 'Upwork', status: 'Aceita', createdAt: '2026-01-01', sentAt: '2026-01-01', validUntil: null, followUpDate: null, estimatedHours: 20, notes: '', platformData: { connects: 8 } }
const project: Project = { id: 'project-1', clientId: client.id, proposalId: proposal.id, name: 'API', description: '', status: 'Em desenvolvimento', startDate: '2026-01-01', deadline: null, completedAt: null, amount: 1000, currency: 'USD', estimatedHours: 20, workedHours: 5, repositoryUrl: '', productionUrl: '', paymentStatus: 'Parcial', amountReceived: 400, notes: '', createdAt: '2026-01-01', updatedAt: '2026-01-01' }

describe('cálculos do painel V2', () => {
  it('calcula progresso e tarefas restantes', () => {
    const tasks = createRoadmap('2026-01-01').map((task, index) => index < 9 ? { ...task, status: 'Concluído' as const } : task)
    expect(taskMetrics(tasks, new Date('2025-12-01'))).toMatchObject({ completed: 9, remaining: 81, progress: 10 })
  })

  it('identifica atraso, exceto tarefa concluída ou adiada', () => {
    const [task] = createRoadmap('2026-01-01')
    expect(isTaskOverdue(task, new Date('2026-01-03T10:00:00'))).toBe(true)
    expect(isTaskOverdue({ ...task, status: 'Concluído' }, new Date('2026-01-03'))).toBe(false)
    expect(isTaskOverdue({ ...task, status: 'Adiado' }, new Date('2026-01-03'))).toBe(false)
  })

  it('calcula propostas abertas, aceitas e connects opcionais', () => {
    expect(proposalMetrics([proposal, { ...proposal, id: 'proposal-2', status: 'Aguardando resposta', platformData: undefined }])).toMatchObject({ sent: 2, open: 1, accepted: 1, connects: 8 })
  })

  it('calcula projetos e separa totais por moeda', () => {
    const brlProject = { ...project, id: 'project-2', proposalId: null, amount: 500, amountReceived: 500, currency: 'BRL' as const, paymentStatus: 'Pago' as const }
    expect(projectMetrics([project, brlProject])).toMatchObject({ active: 2, contracted: { USD: 1000, BRL: 500 }, received: { USD: 400, BRL: 500 }, pending: { USD: 600, BRL: 0 }, hours: 10 })
  })

  it('calcula indicadores e relacionamentos do cliente', () => {
    expect(clientMetrics([client, { ...client, id: 'client-2', status: 'Lead' }])).toEqual({ active: 1, leads: 1 })
    expect(clientFinancials(client.id, [project])).toEqual({ contracted: { BRL: 0, USD: 1000 }, received: { BRL: 0, USD: 400 } })
  })

  it('calcula indicadores simples de execução sem misturar tarefas do roadmap', () => {
    const baseTask: ProjectTask = { id: 'project-task-1', projectId: project.id, title: 'Implementar', description: '', status: 'Pendente', priority: 'Alta', deadline: null, completedAt: null, createdAt: '2026-01-01', updatedAt: '2026-01-01' }
    const nearDeadline = { ...project, deadline: '2026-01-07' }
    expect(projectExecutionMetrics([nearDeadline], [baseTask, { ...baseTask, id: 'blocked', status: 'Bloqueado' }], new Date('2026-01-01T12:00:00'))).toEqual({ pendingTasks: 1, blockedTasks: 1, projectsNearDeadline: 1 })
  })
})
