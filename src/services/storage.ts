import { createDefaultData } from '../data/defaults'
import { createRoadmap } from '../data/roadmap'
import type { AppData, Backup } from '../types'

export const STORAGE_KEY = 'freelance-focus:data:v1'

const taskStatuses = new Set(['Pendente', 'Em andamento', 'Concluído', 'Adiado'])
const priorities = new Set(['Alta', 'Média', 'Baixa'])
const platforms = new Set(['Upwork', '99Freelas', 'LinkedIn', 'Indicação', 'Outra'])
const proposalStatuses = new Set(['Salva', 'Enviada', 'Visualizada', 'Entrevista', 'Contratado', 'Recusada', 'Ignorada'])
const contractStatuses = new Set(['Em negociação', 'Em andamento', 'Entregue', 'Pausado', 'Cancelado'])
const serviceStatuses = new Set(['Rascunho', 'Pronto', 'Publicado', 'Vendido'])
const themes = new Set(['light', 'dark', 'system'])
const isString = (value: unknown): value is string => typeof value === 'string'
const isNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const isNullableString = (value: unknown) => value === null || isString(value)
const hasId = (value: unknown): value is { id: string } => !!value && typeof value === 'object' && isString((value as { id?: unknown }).id)

function isTask(value: unknown) {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return isNumber(item.day) && item.day >= 1 && item.day <= 90 && isString(item.plannedDate) && isNumber(item.week)
    && item.week >= 1 && item.week <= 13 && isString(item.phase) && isString(item.title) && isString(item.description)
    && isNumber(item.estimatedMinutes) && priorities.has(String(item.priority)) && taskStatuses.has(String(item.status))
    && isString(item.notes) && isNullableString(item.rescheduledDate) && isNullableString(item.completedAt)
}

function isProposal(value: unknown) {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return ['date', 'projectName', 'clientName', 'serviceType', 'url', 'deadline', 'nextStep', 'notes', 'followUpDate'].every((key) => isString(item[key]))
    && ['budgetUsd', 'connects', 'estimatedHours', 'estimatedHourlyRate'].every((key) => isNumber(item[key]))
    && platforms.has(String(item.platform)) && proposalStatuses.has(String(item.status))
}

function isContract(value: unknown) {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return ['project', 'client', 'service', 'startDate', 'deadline', 'notes'].every((key) => isString(item[key]))
    && ['grossUsd', 'platformFeePercent', 'exchangeRate', 'hoursWorked'].every((key) => isNumber(item[key]))
    && platforms.has(String(item.platform)) && contractStatuses.has(String(item.status))
    && (item.rating === null || isNumber(item.rating))
}

function isService(value: unknown) {
  if (!hasId(value)) return false
  const item = value as Record<string, unknown>
  return ['name', 'estimatedTime', 'scope', 'included', 'excluded'].every((key) => isString(item[key]))
    && isNumber(item.startingPriceUsd) && serviceStatuses.has(String(item.status))
}

function isSettings(value: unknown) {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return isString(item.userName) && isString(item.roadmapStartDate) && isNumber(item.weeklyGoalUsd) && isNumber(item.weeklyHours)
    && (item.primaryCurrency === 'USD' || item.primaryCurrency === 'BRL') && isNumber(item.defaultExchangeRate)
    && themes.has(String(item.theme)) && typeof item.notificationsEnabled === 'boolean' && typeof item.confirmBeforeDelete === 'boolean'
}

export function isValidAppData(value: unknown): value is AppData {
  if (!value || typeof value !== 'object') return false
  const item = value as Partial<AppData>
  return item.schemaVersion === 1 && isString(item.savedAt)
    && Array.isArray(item.tasks) && item.tasks.length === 90 && item.tasks.every(isTask) && new Set(item.tasks.map((task) => task.id)).size === 90
    && Array.isArray(item.proposals) && item.proposals.every(isProposal)
    && Array.isArray(item.contracts) && item.contracts.every(isContract)
    && Array.isArray(item.services) && item.services.every(isService)
    && isSettings(item.settings)
}

export function migrateData(value: unknown): AppData | null {
  if (isValidAppData(value)) return value
  if (!value || typeof value !== 'object') return null
  const legacy = value as Partial<AppData>
  const legacyVersion = (value as { schemaVersion?: unknown }).schemaVersion
  if (legacyVersion !== undefined && legacyVersion !== 0) return null
  const startDate = legacy.settings && isString(legacy.settings.roadmapStartDate) ? legacy.settings.roadmapStartDate : undefined
  const defaults = createDefaultData(startDate)
  const migrated = {
    ...defaults,
    ...legacy,
    schemaVersion: 1,
    settings: { ...defaults.settings, ...(legacy.settings ?? {}) },
    savedAt: legacy.savedAt ?? new Date().toISOString(),
  }
  return isValidAppData(migrated) ? migrated : null
}

export function loadData(storage: Storage = localStorage): AppData {
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return createDefaultData()
    const parsed: unknown = JSON.parse(raw)
    return migrateData(parsed) ?? createDefaultData()
  } catch {
    return createDefaultData()
  }
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
  if (!isValidAppData(parsed)) throw new Error('Este arquivo não é um backup válido do Freelance Focus.')
  return parsed
}

export function resetRoadmap(data: AppData): AppData {
  return { ...data, tasks: createRoadmap(data.settings.roadmapStartDate) }
}
