import { describe, expect, it } from 'vitest'
import { createRoadmap } from '../data/roadmap'
import { EMPTY_TASK_FILTERS, filterTasks } from '../utils/filters'

describe('filtros de tarefas', () => {
  it('filtra por busca, semana, prioridade e status', () => {
    const tasks = createRoadmap('2026-01-01')
    const first = { ...tasks[0], status: 'Em andamento' as const, priority: 'Alta' as const }
    tasks[0] = first
    expect(filterTasks(tasks, { ...EMPTY_TASK_FILTERS, search: 'pasta central', week: '1', priority: 'Alta', status: 'Em andamento' }, new Date('2025-01-01'))).toEqual([first])
  })

  it('filtra tarefa de hoje e tarefas atrasadas', () => {
    const tasks = createRoadmap('2026-01-01')
    expect(filterTasks(tasks, { ...EMPTY_TASK_FILTERS, todayOnly: true }, new Date('2026-01-02T12:00:00'))[0].day).toBe(2)
    expect(filterTasks(tasks, { ...EMPTY_TASK_FILTERS, overdueOnly: true }, new Date('2026-01-03T12:00:00'))).toHaveLength(2)
  })
})
