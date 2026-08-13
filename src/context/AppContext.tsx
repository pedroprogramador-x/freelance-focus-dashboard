import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { recalculateRoadmapDates } from '../data/roadmap'
import { createDefaultData } from '../data/defaults'
import { deletionBlockReason, hasValidEntityReferences, removeProjectWithRelatedData, type DomainEntity, type EntityCollection } from '../data/domain'
import { loadData, saveData } from '../services/storage'
import type { AppData, Client, FreelanceService, Project, ProjectPlanning, ProjectTask, Proposal, RoadmapTask, Settings } from '../types'

type Entity = Client | Proposal | Project | ProjectPlanning | ProjectTask | FreelanceService
interface Toast { id: number; message: string; tone: 'success' | 'error' | 'info' }
interface AppContextValue {
  data: AppData
  ready: boolean
  toast: Toast | null
  lastSavedAt: string
  updateTask: (id: string, changes: Partial<RoadmapTask>) => void
  toggleTask: (id: string) => void
  upsert: (collection: EntityCollection, value: Entity) => void
  remove: (collection: EntityCollection, id: string) => void
  removeProjectWithRelated: (id: string) => void
  updateSettings: (settings: Settings) => void
  replaceData: (data: AppData) => void
  resetAll: () => void
  notify: (message: string, tone?: Toast['tone']) => void
}

const AppContext = createContext<AppContextValue | null>(null)

export function AppProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<AppData>(() => loadData())
  const [ready, setReady] = useState(false)
  const [toast, setToast] = useState<Toast | null>(null)
  const [lastSavedAt, setLastSavedAt] = useState(data.savedAt)

  useEffect(() => { const timer = window.setTimeout(() => setReady(true), 260); return () => clearTimeout(timer) }, [])
  useEffect(() => { if (ready) setLastSavedAt(saveData(data).savedAt) }, [data, ready])
  useEffect(() => {
    const theme = data.settings.theme
    const dark = theme === 'dark' || (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
  }, [data.settings.theme])

  const notify = useCallback((message: string, tone: Toast['tone'] = 'info') => {
    if (!data.settings.notificationsEnabled) return
    const id = Date.now()
    setToast({ id, message, tone })
    window.setTimeout(() => setToast((current) => current?.id === id ? null : current), 3200)
  }, [data.settings.notificationsEnabled])

  const updateTask = useCallback((id: string, changes: Partial<RoadmapTask>) => {
    setData((current) => ({ ...current, tasks: current.tasks.map((task) => task.id === id ? { ...task, ...changes } : task) }))
  }, [])

  const toggleTask = useCallback((id: string) => {
    setData((current) => ({ ...current, tasks: current.tasks.map((task) => task.id === id ? {
      ...task,
      status: task.status === 'Concluído' ? 'Pendente' : 'Concluído',
      completedAt: task.status === 'Concluído' ? null : new Date().toISOString(),
    } : task) }))
  }, [])

  const upsert = useCallback((collection: EntityCollection, value: Entity) => {
    setData((current) => {
      if (!hasValidEntityReferences(current, collection, value as DomainEntity)) return current
      const list = current[collection] as Entity[]
      const next = list.some((item) => item.id === value.id) ? list.map((item) => item.id === value.id ? value : item) : [value, ...list]
      return { ...current, [collection]: next }
    })
  }, [])

  const remove = useCallback((collection: EntityCollection, id: string) => {
    setData((current) => deletionBlockReason(current, collection, id) ? current : { ...current, [collection]: (current[collection] as Entity[]).filter((item) => item.id !== id) })
  }, [])

  const removeProjectWithRelated = useCallback((id: string) => {
    setData((current) => removeProjectWithRelatedData(current, id))
  }, [])

  const updateSettings = useCallback((settings: Settings) => {
    setData((current) => {
      const dateChanged = settings.roadmapStartDate !== current.settings.roadmapStartDate
      const tasks = dateChanged ? recalculateRoadmapDates(current.tasks, settings.roadmapStartDate) : current.tasks
      return { ...current, settings, tasks }
    })
  }, [])

  const value = useMemo<AppContextValue>(() => ({
    data, ready, toast, lastSavedAt, updateTask, toggleTask, upsert, remove, removeProjectWithRelated, updateSettings,
    replaceData: setData,
    resetAll: () => setData(createDefaultData()),
    notify,
  }), [data, ready, toast, lastSavedAt, updateTask, toggleTask, upsert, remove, removeProjectWithRelated, updateSettings, notify])

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>
}

// Contexto e hook ficam juntos para manter a API pública de estado em um único módulo.
// eslint-disable-next-line react-refresh/only-export-components
export function useApp() {
  const context = useContext(AppContext)
  if (!context) throw new Error('useApp precisa estar dentro de AppProvider')
  return context
}
