import type { Priority, RoadmapTask, TaskStatus } from '../types'
import { isTaskOverdue } from './calculations'
import { toDateInput } from '../data/roadmap'

export interface TaskFilters { search: string; week: string; phase: string; priority: '' | Priority; status: '' | TaskStatus; overdueOnly: boolean; todayOnly: boolean }
export const EMPTY_TASK_FILTERS: TaskFilters = { search: '', week: '', phase: '', priority: '', status: '', overdueOnly: false, todayOnly: false }

export function filterTasks(tasks: RoadmapTask[], filters: TaskFilters, today = new Date()) {
  const isoToday = toDateInput(today)
  const term = filters.search.trim().toLocaleLowerCase('pt-BR')
  return tasks.filter((task) => {
    const date = task.rescheduledDate ?? task.plannedDate
    return (!term || `${task.title} ${task.description} ${task.notes}`.toLocaleLowerCase('pt-BR').includes(term))
      && (!filters.week || task.week === Number(filters.week))
      && (!filters.phase || task.phase === filters.phase)
      && (!filters.priority || task.priority === filters.priority)
      && (!filters.status || task.status === filters.status)
      && (!filters.overdueOnly || isTaskOverdue(task, today))
      && (!filters.todayOnly || date === isoToday)
  })
}
