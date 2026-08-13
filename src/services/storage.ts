import { createDefaultData } from '../data/defaults'
import { normalizeClientName } from '../data/domain'
import { createRoadmap } from '../data/roadmap'
import type { AppData, Backup, Client, ClientSource, ClientStatus, Currency, FreelanceService, Project, ProjectPlanning, ProjectStatus, ProjectTask, Proposal, ProposalStatus, RoadmapTask, Settings, TechnicalDecision, ProjectRisk } from '../types'

export const STORAGE_KEY = 'freelance-focus:data:v3'
export const V2_STORAGE_KEY = 'freelance-focus:data:v2'
export const LEGACY_STORAGE_KEY = 'freelance-focus:data:v1'

export type V2AppData = Omit<AppData, 'schemaVersion' | 'projectPlannings' | 'projectTasks'> & { schemaVersion: 2 }

type LegacyPlatform = 'Upwork' | '99Freelas' | 'LinkedIn' | 'Indicação' | 'Outra'
type LegacyProposalStatus = 'Salva' | 'Enviada' | 'Visualizada' | 'Entrevista' | 'Contratado' | 'Recusada' | 'Ignorada'
type LegacyContractStatus = 'Em negociação' | 'Em andamento' | 'Entregue' | 'Pausado' | 'Cancelado'
interface LegacyProposal { id: string; date: string; platform: LegacyPlatform; projectName: string; clientName: string; serviceType: string; budgetUsd: number; connects: number; url: string; status: LegacyProposalStatus; deadline: string; estimatedHours: number; estimatedHourlyRate: number; nextStep: string; notes: string; followUpDate: string }
interface LegacyContract { id: string; project: string; client: string; platform: LegacyPlatform; service: string; startDate: string; deadline: string; grossUsd: number; platformFeePercent: number; exchangeRate: number; hoursWorked: number; status: LegacyContractStatus; rating: number | null; notes: string }
interface LegacyAppData { schemaVersion: 1; tasks: RoadmapTask[]; proposals: LegacyProposal[]; contracts: LegacyContract[]; services: FreelanceService[]; settings: Settings; savedAt: string }

const taskStatuses = new Set(['Pendente', 'Em andamento', 'Concluído', 'Adiado'])
const priorities = new Set(['Alta', 'Média', 'Baixa'])
const legacyPlatforms = new Set(['Upwork', '99Freelas', 'LinkedIn', 'Indicação', 'Outra'])
const legacyProposalStatuses = new Set(['Salva', 'Enviada', 'Visualizada', 'Entrevista', 'Contratado', 'Recusada', 'Ignorada'])
const legacyContractStatuses = new Set(['Em negociação', 'Em andamento', 'Entregue', 'Pausado', 'Cancelado'])
const clientSources = new Set(['Indicação', 'WhatsApp', 'Instagram', 'Upwork', '99Freelas', 'LinkedIn', 'Contato direto', 'Outro'])
const clientStatuses = new Set(['Lead', 'Em negociação', 'Cliente ativo', 'Cliente inativo'])
const proposalStatuses = new Set(['Rascunho', 'Enviada', 'Aguardando resposta', 'Aceita', 'Recusada', 'Expirada'])
const projectStatuses = new Set(['Planejamento', 'Em desenvolvimento', 'Aguardando cliente', 'Em revisão', 'Entregue', 'Pausado', 'Cancelado'])
const projectTaskStatuses = new Set(['Pendente', 'Em andamento', 'Bloqueado', 'Concluído'])
const projectTaskPriorities = new Set(['Alta', 'Média', 'Baixa'])
const paymentStatuses = new Set(['Pendente', 'Parcial', 'Pago'])
const serviceStatuses = new Set(['Rascunho', 'Pronto', 'Publicado', 'Vendido'])
const themes = new Set(['light', 'dark', 'system'])
const currencies = new Set(['BRL', 'USD'])
const isString = (value: unknown): value is string => typeof value === 'string'
const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isNonNegative = (value: unknown): value is number => isNumber(value) && value >= 0
const hasId = (value: unknown): value is { id: string } => !!value && typeof value === 'object' && isString((value as { id?: unknown }).id) && !!(value as { id: string }).id
const uniqueIds = (items: { id: string }[]) => new Set(items.map((item) => item.id)).size === items.length
export function isLocalDate(value: unknown): value is string {
  if (!isString(value) || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  const [year, month, day] = value.split('-').map(Number)
  const parsed = new Date(Date.UTC(year, month - 1, day))
  return parsed.getUTCFullYear() === year && parsed.getUTCMonth() === month - 1 && parsed.getUTCDate() === day
}
const isTimestamp = (value: unknown) => isString(value) && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value))
const isStoredDate = (value: unknown) => isLocalDate(value) || isTimestamp(value)
const isNullableLocalDate = (value: unknown) => value === null || isLocalDate(value)
const isOptionalPercent = (value: unknown) => value === undefined || value === null || (isNonNegative(value) && value <= 100)
const isOptionalPositive = (value: unknown) => value === undefined || value === null || (isNumber(value) && value > 0)

function isTask(value: unknown) {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return isNumber(item.day) && item.day >= 1 && item.day <= 90 && isLocalDate(item.plannedDate) && isNumber(item.week)
    && item.week >= 1 && item.week <= 13 && isString(item.phase) && isString(item.title) && isString(item.description)
    && isNonNegative(item.estimatedMinutes) && priorities.has(String(item.priority)) && taskStatuses.has(String(item.status))
    && isString(item.notes) && isNullableLocalDate(item.rescheduledDate) && (item.completedAt === null || isStoredDate(item.completedAt))
}

export function isClient(value: unknown): value is Client {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return ['name', 'companyName', 'contactName', 'phone', 'email', 'referredBy', 'notes'].every((key) => isString(item[key]))
    && isStoredDate(item.createdAt) && isStoredDate(item.updatedAt)
    && !!item.name && clientSources.has(String(item.source)) && clientStatuses.has(String(item.status))
}

function isPlatformData(value: unknown) {
  if (value === undefined) return true
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return (item.url === undefined || isString(item.url))
    && (item.connects === undefined || isNonNegative(item.connects))
    && (item.platformFeePercent === undefined || (isNonNegative(item.platformFeePercent) && item.platformFeePercent <= 100))
}

export function isProposal(value: unknown): value is Proposal {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return isString(item.clientId) && (item.serviceId === null || isString(item.serviceId))
    && ['title', 'description', 'notes'].every((key) => isString(item[key])) && isStoredDate(item.createdAt) && !!item.title
    && isNonNegative(item.amount) && currencies.has(String(item.currency)) && clientSources.has(String(item.source))
    && proposalStatuses.has(String(item.status)) && isNullableLocalDate(item.sentAt) && isNullableLocalDate(item.validUntil)
    && isNullableLocalDate(item.followUpDate) && isNonNegative(item.estimatedHours) && isPlatformData(item.platformData)
}

export function isProject(value: unknown): value is Project {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  const amountValid = isNonNegative(item.amount) && isNonNegative(item.amountReceived) && (item.amountReceived as number) <= (item.amount as number)
  const paymentValid = paymentStatuses.has(String(item.paymentStatus))
    && (item.paymentStatus !== 'Pendente' || item.amountReceived === 0)
    && (item.paymentStatus !== 'Parcial' || (Number(item.amountReceived) > 0 && Number(item.amountReceived) < Number(item.amount)))
    && (item.paymentStatus !== 'Pago' || (Number(item.amount) > 0 && item.amountReceived === item.amount))
  return isString(item.clientId) && (item.proposalId === null || isString(item.proposalId))
    && ['name', 'description', 'repositoryUrl', 'productionUrl', 'notes'].every((key) => isString(item[key])) && isStoredDate(item.createdAt) && isStoredDate(item.updatedAt) && !!item.name
    && projectStatuses.has(String(item.status)) && isNullableLocalDate(item.startDate) && isNullableLocalDate(item.deadline) && isNullableLocalDate(item.completedAt)
    && amountValid && currencies.has(String(item.currency)) && isOptionalPercent(item.platformFeePercent) && isOptionalPositive(item.exchangeRateToBrl)
    && isNonNegative(item.estimatedHours) && isNonNegative(item.workedHours) && paymentValid
}

function isService(value: unknown): value is FreelanceService {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return ['name', 'estimatedTime', 'scope', 'included', 'excluded'].every((key) => isString(item[key]))
    && isNonNegative(item.startingPriceUsd) && serviceStatuses.has(String(item.status))
}

function isTechnicalDecision(value: unknown): value is TechnicalDecision {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return isString(item.title) && !!item.title.trim() && isString(item.decision) && isString(item.reason)
}

function isProjectRisk(value: unknown): value is ProjectRisk {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return isString(item.description) && !!item.description.trim() && isString(item.mitigation)
}

const isNonEmptyStringArray = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => isString(item) && !!item.trim())

export function isProjectPlanning(value: unknown): value is ProjectPlanning {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return isString(item.projectId) && !!item.projectId
    && isString(item.problem) && isString(item.objective) && isString(item.architecture)
    && isNonEmptyStringArray(item.functionalRequirements) && isNonEmptyStringArray(item.nonFunctionalRequirements) && isNonEmptyStringArray(item.stack)
    && Array.isArray(item.technicalDecisions) && item.technicalDecisions.every(isTechnicalDecision) && uniqueIds(item.technicalDecisions)
    && Array.isArray(item.risks) && item.risks.every(isProjectRisk) && uniqueIds(item.risks)
    && isStoredDate(item.createdAt) && isStoredDate(item.updatedAt)
}

export function isProjectTask(value: unknown): value is ProjectTask {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  const completionValid = item.status === 'Concluído' ? isLocalDate(item.completedAt) : item.completedAt === null
  return isString(item.projectId) && !!item.projectId && isString(item.title) && !!item.title.trim() && isString(item.description)
    && projectTaskStatuses.has(String(item.status)) && projectTaskPriorities.has(String(item.priority))
    && isNullableLocalDate(item.deadline) && completionValid && isStoredDate(item.createdAt) && isStoredDate(item.updatedAt)
}

function isSettings(value: unknown): value is Settings {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return isString(item.userName) && isLocalDate(item.roadmapStartDate) && isNonNegative(item.weeklyGoalUsd) && isNumber(item.weeklyHours) && item.weeklyHours > 0
    && currencies.has(String(item.primaryCurrency)) && isNonNegative(item.defaultExchangeRate)
    && themes.has(String(item.theme)) && typeof item.notificationsEnabled === 'boolean' && typeof item.confirmBeforeDelete === 'boolean'
}

function hasValidRoadmap(value: { tasks?: unknown }) {
  return Array.isArray(value.tasks) && value.tasks.length === 90 && value.tasks.every(isTask) && uniqueIds(value.tasks)
}

type CoreData = Omit<V2AppData, 'schemaVersion'>

function hasValidCoreData(value: unknown): value is CoreData {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<CoreData>
  if (!isStoredDate(item.savedAt) || !hasValidRoadmap(item)) return false
  if (!Array.isArray(item.clients) || !item.clients.every(isClient) || !uniqueIds(item.clients)) return false
  if (!Array.isArray(item.proposals) || !item.proposals.every(isProposal) || !uniqueIds(item.proposals)) return false
  if (!Array.isArray(item.projects) || !item.projects.every(isProject) || !uniqueIds(item.projects)) return false
  if (!Array.isArray(item.services) || !item.services.every(isService) || !uniqueIds(item.services) || !isSettings(item.settings)) return false
  const clientIds = new Set(item.clients.map((client) => client.id))
  const serviceIds = new Set(item.services.map((service) => service.id))
  const proposalById = new Map(item.proposals.map((proposal) => [proposal.id, proposal]))
  return item.proposals.every((proposal) => clientIds.has(proposal.clientId) && (proposal.serviceId === null || serviceIds.has(proposal.serviceId)))
    && item.projects.every((project) => clientIds.has(project.clientId) && (project.proposalId === null || proposalById.get(project.proposalId)?.clientId === project.clientId))
}

export function isValidV2Data(value: unknown): value is V2AppData {
  return !!value && typeof value === 'object' && (value as { schemaVersion?: unknown }).schemaVersion === 2 && hasValidCoreData(value)
}

export function isValidAppData(value: unknown): value is AppData {
  if (!value || typeof value !== 'object' || (value as { schemaVersion?: unknown }).schemaVersion !== 3 || !hasValidCoreData(value)) return false
  const item = value as Partial<AppData>
  if (!Array.isArray(item.projectPlannings) || !item.projectPlannings.every(isProjectPlanning) || !uniqueIds(item.projectPlannings)) return false
  if (!Array.isArray(item.projectTasks) || !item.projectTasks.every(isProjectTask) || !uniqueIds(item.projectTasks)) return false
  const projectIds = new Set(item.projects!.map((project) => project.id))
  return item.projectPlannings.every((planning) => projectIds.has(planning.projectId))
    && new Set(item.projectPlannings.map((planning) => planning.projectId)).size === item.projectPlannings.length
    && item.projectTasks.every((task) => projectIds.has(task.projectId))
}

function isLegacyProposal(value: unknown): value is LegacyProposal {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return isLocalDate(item.date) && ['projectName', 'clientName', 'serviceType', 'url', 'nextStep', 'notes'].every((key) => isString(item[key]))
    && (item.deadline === '' || isLocalDate(item.deadline)) && (item.followUpDate === '' || isLocalDate(item.followUpDate))
    && ['budgetUsd', 'connects', 'estimatedHours', 'estimatedHourlyRate'].every((key) => isNonNegative(item[key]))
    && legacyPlatforms.has(String(item.platform)) && legacyProposalStatuses.has(String(item.status))
}

function isLegacyContract(value: unknown): value is LegacyContract {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return ['project', 'client', 'service', 'notes'].every((key) => isString(item[key]))
    && (item.startDate === '' || isLocalDate(item.startDate)) && (item.deadline === '' || isLocalDate(item.deadline))
    && ['grossUsd', 'exchangeRate', 'hoursWorked'].every((key) => isNonNegative(item[key]))
    && isNonNegative(item.platformFeePercent) && item.platformFeePercent <= 100
    && legacyPlatforms.has(String(item.platform)) && legacyContractStatuses.has(String(item.status))
    && (item.rating === null || (isNumber(item.rating) && item.rating >= 0 && item.rating <= 5))
}

export function isValidV1Data(value: unknown): value is LegacyAppData {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<LegacyAppData>
  return item.schemaVersion === 1 && isStoredDate(item.savedAt) && hasValidRoadmap(item)
    && Array.isArray(item.proposals) && item.proposals.every(isLegacyProposal) && uniqueIds(item.proposals)
    && Array.isArray(item.contracts) && item.contracts.every(isLegacyContract) && uniqueIds(item.contracts)
    && Array.isArray(item.services) && item.services.every(isService) && uniqueIds(item.services) && isSettings(item.settings)
}

const displayName = (value: string) => value.trim().replace(/\s+/g, ' ') || 'Cliente não informado'
const stableHash = (value: string) => { let hash = 2166136261; for (const char of value) { hash ^= char.charCodeAt(0); hash = Math.imul(hash, 16777619) } return (hash >>> 0).toString(36) }
const clientIdFor = (normalizedName: string) => `client-v1-${stableHash(normalizedName)}`
const sourceFromPlatform = (platform: LegacyPlatform): ClientSource => platform === 'Outra' ? 'Outro' : platform
const proposalStatusMap: Record<LegacyProposalStatus, ProposalStatus> = { Salva: 'Rascunho', Enviada: 'Enviada', Visualizada: 'Aguardando resposta', Entrevista: 'Aguardando resposta', Contratado: 'Aceita', Recusada: 'Recusada', Ignorada: 'Expirada' }
const projectStatusMap: Record<LegacyContractStatus, ProjectStatus> = { 'Em negociação': 'Planejamento', 'Em andamento': 'Em desenvolvimento', Entregue: 'Entregue', Pausado: 'Pausado', Cancelado: 'Cancelado' }
const joinNotes = (original: string, details: string[]) => [original.trim(), ...details.filter(Boolean)].filter(Boolean).join('\n\n')
const serviceIdFor = (name: string, services: FreelanceService[]) => services.find((service) => normalizeClientName(service.name) === normalizeClientName(name))?.id ?? null

function inferClientStatus(name: string, proposals: LegacyProposal[], contracts: LegacyContract[]): ClientStatus {
  const key = normalizeClientName(name)
  const relatedContracts = contracts.filter((item) => normalizeClientName(item.client) === key)
  if (relatedContracts.some((item) => item.status === 'Em andamento' || item.status === 'Em negociação' || item.status === 'Pausado')) return 'Cliente ativo'
  if (relatedContracts.some((item) => item.status === 'Entregue')) return 'Cliente inativo'
  if (proposals.some((item) => normalizeClientName(item.clientName) === key && ['Visualizada', 'Entrevista', 'Contratado'].includes(item.status))) return 'Em negociação'
  return 'Lead'
}

export function migrateV1ToV2(legacy: LegacyAppData): V2AppData {
  const records = [
    ...legacy.proposals.map((item) => ({ name: item.clientName, source: sourceFromPlatform(item.platform), date: item.date })),
    ...legacy.contracts.map((item) => ({ name: item.client, source: sourceFromPlatform(item.platform), date: item.startDate })),
  ]
  const unique = new Map<string, typeof records[number]>()
  for (const record of records) { const key = normalizeClientName(record.name); if (!unique.has(key)) unique.set(key, record) }
  const clients: Client[] = [...unique.entries()].map(([key, record]) => {
    const timestamp = record.date || legacy.savedAt
    return { id: clientIdFor(key), name: displayName(record.name), companyName: '', contactName: '', phone: '', email: '', source: record.source, referredBy: '', status: inferClientStatus(record.name, legacy.proposals, legacy.contracts), notes: 'Cliente criado automaticamente na migração do schema V1.', createdAt: timestamp, updatedAt: timestamp }
  })
  const proposals: Proposal[] = legacy.proposals.map((item) => {
    const mappedStatus = proposalStatusMap[item.status]
    const platformData = item.url || item.connects ? { ...(item.url ? { url: item.url } : {}), ...(item.connects ? { connects: item.connects } : {}) } : undefined
    return { id: item.id, clientId: clientIdFor(normalizeClientName(item.clientName)), serviceId: serviceIdFor(item.serviceType, legacy.services), title: item.projectName, description: item.serviceType, amount: item.budgetUsd, currency: 'USD', source: sourceFromPlatform(item.platform), status: mappedStatus, createdAt: item.date, sentAt: mappedStatus === 'Rascunho' ? null : item.date, validUntil: item.deadline || null, followUpDate: item.followUpDate || null, estimatedHours: item.estimatedHours, notes: joinNotes(item.notes, [item.nextStep ? `Próximo passo no V1: ${item.nextStep}` : '', item.estimatedHourlyRate ? `Estimativa horária no V1: US$ ${item.estimatedHourlyRate.toFixed(2)}` : '']), ...(platformData ? { platformData } : {}) }
  })
  const projects: Project[] = legacy.contracts.map((item) => {
    const clientId = clientIdFor(normalizeClientName(item.client))
    const status = projectStatusMap[item.status]
    const netAmount = item.grossUsd * (1 - item.platformFeePercent / 100)
    const matchingProposal = proposals.find((proposal) => proposal.clientId === clientId && normalizeClientName(proposal.title) === normalizeClientName(item.project))
    const migrationDetails = [
      'Dados preservados da migração V1:',
      `Plataforma: ${item.platform}`,
      item.service ? `Serviço: ${item.service}` : '',
      `Valor bruto: USD ${item.grossUsd.toFixed(2)}`,
      `Taxa da plataforma: ${item.platformFeePercent}%`,
      `Cotação registrada: ${item.exchangeRate}`,
      `Valor líquido convertido: BRL ${(netAmount * item.exchangeRate).toFixed(2)}`,
      item.rating !== null ? `Avaliação: ${item.rating}/5` : '',
      'O V1 não registrava pagamentos; o recebimento foi mantido como pendente.',
      status === 'Entregue' ? 'O status de entrega foi preservado, sem inferir data de conclusão.' : '',
    ]
    return { id: item.id, clientId, proposalId: matchingProposal?.id ?? null, name: item.project, description: item.service, status, startDate: item.startDate || null, deadline: item.deadline || null, completedAt: null, amount: item.grossUsd, currency: 'USD' as Currency, platformFeePercent: item.platformFeePercent, exchangeRateToBrl: item.exchangeRate || null, estimatedHours: 0, workedHours: item.hoursWorked, repositoryUrl: '', productionUrl: '', paymentStatus: 'Pendente', amountReceived: 0, notes: joinNotes(item.notes, migrationDetails), createdAt: item.startDate || legacy.savedAt, updatedAt: legacy.savedAt }
  })
  return { schemaVersion: 2, clients, proposals, projects, services: legacy.services.map((item) => ({ ...item })), tasks: legacy.tasks.map((item) => ({ ...item })), settings: { ...legacy.settings }, savedAt: legacy.savedAt }
}

function migrateV0(value: Record<string, unknown>): LegacyAppData | null {
  const defaults = createDefaultData(typeof (value.settings as { roadmapStartDate?: unknown } | undefined)?.roadmapStartDate === 'string' ? (value.settings as { roadmapStartDate: string }).roadmapStartDate : undefined)
  const candidate = { schemaVersion: 1, tasks: value.tasks ?? defaults.tasks, proposals: value.proposals ?? [], contracts: value.contracts ?? [], services: value.services ?? defaults.services, settings: { ...defaults.settings, ...((value.settings && typeof value.settings === 'object') ? value.settings : {}) }, savedAt: isString(value.savedAt) ? value.savedAt : new Date().toISOString() }
  return isValidV1Data(candidate) ? candidate : null
}

function repairPreviouslyMigratedV2(data: V2AppData): V2AppData {
  let changed = false
  const projects = data.projects.map((project) => {
    if (project.platformFeePercent !== undefined || project.exchangeRateToBrl !== undefined || !project.notes.includes('Dados preservados da migração V1:')) return project
    const grossMatch = project.notes.match(/Valor bruto: USD ([0-9]+(?:\.[0-9]+)?)/)
    const feeMatch = project.notes.match(/Taxa da plataforma: ([0-9]+(?:\.[0-9]+)?)%/)
    const rateMatch = project.notes.match(/Cotação registrada: ([0-9]+(?:\.[0-9]+)?)/)
    if (!grossMatch || !feeMatch || !rateMatch) return project
    changed = true
    return {
      ...project,
      amount: Number(grossMatch[1]),
      platformFeePercent: Number(feeMatch[1]),
      exchangeRateToBrl: Number(rateMatch[1]) || null,
      paymentStatus: 'Pendente' as const,
      amountReceived: 0,
      completedAt: null,
    }
  })
  return changed ? { ...data, projects } : data
}

export function migrateV2ToV3(data: V2AppData): AppData {
  return { ...data, schemaVersion: 3, projectPlannings: [], projectTasks: [] }
}

export function migrateData(value: unknown): AppData | null {
  if (isValidAppData(value)) return value
  if (isValidV2Data(value)) return migrateV2ToV3(repairPreviouslyMigratedV2(value))
  if (isValidV1Data(value)) return migrateV2ToV3(migrateV1ToV2(value))
  if (!value || typeof value !== 'object') return null
  const version = (value as { schemaVersion?: unknown }).schemaVersion
  if (version !== undefined && version !== 0) return null
  const legacy = migrateV0(value as Record<string, unknown>)
  return legacy ? migrateV2ToV3(migrateV1ToV2(legacy)) : null
}

export function loadData(storage: Storage = localStorage): AppData {
  for (const key of [STORAGE_KEY, V2_STORAGE_KEY, LEGACY_STORAGE_KEY]) {
    try {
      const raw = storage.getItem(key)
      if (!raw) continue
      const migrated = migrateData(JSON.parse(raw) as unknown)
      if (migrated) return migrated
    } catch { /* tenta a chave de compatibilidade antes do fallback */ }
  }
  return createDefaultData()
}

export function saveData(data: AppData, storage: Storage = localStorage) {
  const saved = { ...data, savedAt: new Date().toISOString() }
  storage.setItem(STORAGE_KEY, JSON.stringify(saved))
  return saved
}

export function exportBackup(data: AppData): Backup {
  return { ...data, app: 'Freelance Focus', exportedAt: new Date().toISOString() }
}

export function parseBackup(raw: string): AppData {
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { throw new Error('O arquivo não contém um JSON válido.') }
  const version = parsed && typeof parsed === 'object' ? (parsed as { schemaVersion?: unknown }).schemaVersion : undefined
  if (version !== 1 && version !== 2 && version !== 3) throw new Error('Este arquivo não é um backup V1, V2 ou V3 válido do Freelance Focus.')
  const migrated = migrateData(parsed)
  if (!migrated) throw new Error('Este arquivo não é um backup V1, V2 ou V3 válido do Freelance Focus.')
  return migrated
}

export function resetRoadmap(data: AppData): AppData {
  return { ...data, tasks: createRoadmap(data.settings.roadmapStartDate) }
}
