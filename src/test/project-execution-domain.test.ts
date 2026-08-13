import { describe, expect, it } from 'vitest'
import { createDefaultData } from '../data/defaults'
import { createEmptyProjectPlanning, deletionBlockReason, filterProjectTasks, hasValidEntityReferences, projectTaskProgress, removeProjectWithRelatedData } from '../data/domain'
import { isProjectPlanning, isProjectTask, isValidAppData, migrateV2ToV3, parseBackup, type V2AppData } from '../services/storage'
import type { Client, Project, ProjectTask } from '../types'

const client: Client = { id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Contato direto', referredBy: '', status: 'Cliente ativo', notes: '', createdAt: '2026-08-13', updatedAt: '2026-08-13' }
const project: Project = { id: 'project-1', clientId: client.id, proposalId: null, name: 'Automação', description: '', status: 'Em desenvolvimento', startDate: '2026-08-13', deadline: '2026-08-30', completedAt: null, amount: 2000, currency: 'BRL', platformFeePercent: null, exchangeRateToBrl: null, estimatedHours: 20, workedHours: 3, repositoryUrl: '', productionUrl: '', paymentStatus: 'Pendente', amountReceived: 0, notes: '', createdAt: '2026-08-13', updatedAt: '2026-08-13' }
const task = (id: string, status: ProjectTask['status'] = 'Pendente', priority: ProjectTask['priority'] = 'Média'): ProjectTask => ({ id, projectId: project.id, title: `Tarefa ${id}`, description: '', status, priority, deadline: '2026-08-20', completedAt: status === 'Concluído' ? '2026-08-14' : null, createdAt: '2026-08-13', updatedAt: '2026-08-13' })

function preparedData() {
  const data = createDefaultData('2026-08-13')
  data.clients = [client]
  data.projects = [project]
  return data
}

describe('schema V3 e execução de projetos', () => {
  it('migra V2 para V3 deterministicamente sem alterar dados existentes', () => {
    const current = preparedData()
    const v2: V2AppData = { schemaVersion: 2, clients: current.clients, proposals: current.proposals, projects: current.projects, services: current.services, tasks: current.tasks, settings: current.settings, savedAt: current.savedAt }
    const migrated = migrateV2ToV3(v2)
    expect(migrated).toMatchObject({ schemaVersion: 3, clients: v2.clients, projects: v2.projects, settings: v2.settings })
    expect(migrated.tasks).toEqual(v2.tasks)
    expect(migrated.projectPlannings).toEqual([])
    expect(migrated.projectTasks).toEqual([])
    expect(migrateV2ToV3(v2)).toEqual(migrated)
    expect(parseBackup(JSON.stringify(v2))).toEqual(migrated)
  })

  it('valida planejamento completo, seus arrays, decisões, riscos e arquitetura', () => {
    const data = preparedData()
    const planning = {
      ...createEmptyProjectPlanning(project.id, '2026-08-13'),
      problem: 'Planilha manual', objective: 'Automatizar o cálculo',
      functionalRequirements: ['Importar XLSX', 'Exportar relatório'],
      nonFunctionalRequirements: ['Processar em até 10 segundos'], stack: ['Python', 'Pandas'],
      architecture: 'XLSX\n↓\nValidação\n↓\nRelatório',
      technicalDecisions: [{ id: 'decision-1', title: 'Banco de dados', decision: 'Não utilizar', reason: 'Processamento sem persistência' }],
      risks: [{ id: 'risk-1', description: 'Colunas alteradas', mitigation: 'Validar cabeçalhos' }],
    }
    expect(isProjectPlanning(planning)).toBe(true)
    expect(hasValidEntityReferences(data, 'projectPlannings', planning)).toBe(true)
    data.projectPlannings = [planning]
    expect(isValidAppData(data)).toBe(true)
    expect(isProjectPlanning({ ...planning, functionalRequirements: [''] })).toBe(false)
    expect(isProjectPlanning({ ...planning, technicalDecisions: [{ ...planning.technicalDecisions[0], title: '' }] })).toBe(false)
    expect(isProjectPlanning({ ...planning, risks: [{ ...planning.risks[0], description: '' }] })).toBe(false)
  })

  it('impede planning órfão, task órfã e dois planejamentos no mesmo projeto', () => {
    const data = preparedData()
    const planning = createEmptyProjectPlanning(project.id, '2026-08-13')
    data.projectPlannings = [planning]
    expect(hasValidEntityReferences(data, 'projectPlannings', { ...planning, id: 'planning-2' })).toBe(false)
    expect(hasValidEntityReferences(data, 'projectPlannings', { ...planning, projectId: 'missing' })).toBe(false)
    expect(hasValidEntityReferences(data, 'projectTasks', task('task-1'))).toBe(true)
    expect(hasValidEntityReferences(data, 'projectTasks', { ...task('task-2'), projectId: 'missing' })).toBe(false)
    expect(isValidAppData({ ...data, projectPlannings: [{ ...planning, projectId: 'missing' }] })).toBe(false)
    expect(isValidAppData({ ...data, projectTasks: [{ ...task('task-1'), projectId: 'missing' }] })).toBe(false)
    expect(isValidAppData({ ...data, projectPlannings: [planning, { ...planning, id: 'planning-2' }] })).toBe(false)
  })

  it('valida enums, datas locais e coerência de completedAt das tarefas', () => {
    expect(isProjectTask(task('pending'))).toBe(true)
    expect(isProjectTask(task('done', 'Concluído', 'Alta'))).toBe(true)
    expect(isProjectTask({ ...task('invalid'), status: 'Outro' })).toBe(false)
    expect(isProjectTask({ ...task('invalid'), priority: 'Urgente' })).toBe(false)
    expect(isProjectTask({ ...task('invalid'), deadline: '2026-02-30' })).toBe(false)
    expect(isProjectTask({ ...task('invalid'), status: 'Concluído', completedAt: null })).toBe(false)
    expect(isProjectTask({ ...task('invalid'), status: 'Pendente', completedAt: '2026-08-14' })).toBe(false)
  })

  it('calcula progresso sem porcentagem enganosa e filtra status e prioridade', () => {
    expect(projectTaskProgress([])).toEqual({ total: 0, completed: 0, percentage: null })
    const tasks = [task('1', 'Concluído', 'Alta'), task('2', 'Concluído', 'Baixa'), task('3', 'Em andamento', 'Alta'), task('4', 'Pendente', 'Média')]
    expect(projectTaskProgress(tasks)).toEqual({ total: 4, completed: 2, percentage: 50 })
    expect(projectTaskProgress(tasks.slice(0, 2))).toEqual({ total: 2, completed: 2, percentage: 100 })
    expect(filterProjectTasks(tasks, 'Concluído', '')).toHaveLength(2)
    expect(filterProjectTasks(tasks, '', 'Alta').map((item) => item.id)).toEqual(['1', '3'])
    expect(filterProjectTasks(tasks, 'Em andamento', 'Alta').map((item) => item.id)).toEqual(['3'])
  })

  it('bloqueia exclusão simples e permite exclusão conjunta somente pela ação explícita', () => {
    const data = preparedData()
    data.projectPlannings = [createEmptyProjectPlanning(project.id, '2026-08-13')]
    data.projectTasks = [task('task-1')]
    expect(deletionBlockReason(data, 'projects', project.id)).toContain('planejamento técnico ou tarefas')
    const removed = removeProjectWithRelatedData(data, project.id)
    expect(removed.projects).toEqual([])
    expect(removed.projectPlannings).toEqual([])
    expect(removed.projectTasks).toEqual([])
    expect(data.projects).toHaveLength(1)
  })
})
