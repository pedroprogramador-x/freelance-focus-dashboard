import { describe, expect, it } from 'vitest'
import { createRoadmap } from '../data/roadmap'
import type { Contract } from '../types'
import { contractMetrics, isTaskOverdue, netBrl, netHourly, netUsd, taskMetrics } from '../utils/calculations'

describe('cálculos do painel', () => {
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

  it('calcula valor líquido, conversão e valor por hora', () => {
    const contract: Contract = { id: '1', project: 'API', client: 'Acme', platform: 'Upwork', service: 'FastAPI', startDate: '2026-01-01', deadline: '', grossUsd: 1000, platformFeePercent: 10, exchangeRate: 5.5, hoursWorked: 20, status: 'Entregue', rating: 5, notes: '' }
    expect(netUsd(contract)).toBe(900)
    expect(netBrl(contract)).toBe(4950)
    expect(netHourly(contract)).toBe(45)
    expect(contractMetrics([contract])).toMatchObject({ revenueUsd: 900, averageProject: 900, averageHourly: 45, clients: 1 })
  })
})
