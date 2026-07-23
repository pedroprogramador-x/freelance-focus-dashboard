import { describe, expect, it } from 'vitest'
import { createRoadmap, recalculateRoadmapDates } from '../data/roadmap'

describe('roadmap padrão', () => {
  it('inicializa exatamente 90 metas em 13 semanas', () => {
    const tasks = createRoadmap('2026-01-01')
    expect(tasks).toHaveLength(90)
    expect(tasks.filter((task) => task.week <= 12)).toHaveLength(84)
    expect(tasks.filter((task) => task.week === 13)).toHaveLength(6)
    expect(tasks.every((task) => task.estimatedMinutes >= 20 && task.estimatedMinutes <= 60)).toBe(true)
  })

  it('altera a data inicial sem perder status e observações', () => {
    const tasks = createRoadmap('2026-01-01')
    tasks[0] = { ...tasks[0], status: 'Concluído', notes: 'Mantida' }
    const changed = recalculateRoadmapDates(tasks, '2026-02-10')
    expect(changed[0]).toMatchObject({ plannedDate: '2026-02-10', status: 'Concluído', notes: 'Mantida' })
    expect(changed[89].plannedDate).toBe('2026-05-10')
  })
})
