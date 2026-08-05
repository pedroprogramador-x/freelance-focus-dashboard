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
    tasks[0] = { ...tasks[0], status: 'Concluído', notes: 'Mantida', completedAt: '2026-01-02T12:00:00.000Z', priority: 'Alta' }
    tasks[1] = { ...tasks[1], status: 'Adiado', rescheduledDate: '2026-03-01' }
    const changed = recalculateRoadmapDates(tasks, '2026-02-10')
    expect(changed[0]).toMatchObject({ id: tasks[0].id, title: tasks[0].title, plannedDate: '2026-02-10', status: 'Concluído', notes: 'Mantida', completedAt: '2026-01-02T12:00:00.000Z', priority: 'Alta' })
    expect(changed[1]).toMatchObject({ plannedDate: '2026-02-11', status: 'Adiado', rescheduledDate: '2026-03-01' })
    expect(changed[89].plannedDate).toBe('2026-05-10')
  })
})
