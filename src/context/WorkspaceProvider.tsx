// Provider do AI Dev Workspace — SEPARADO do AppContext (docs/architecture/06 §4).
//
// O domínio comercial (AppContext) é síncrono, local e salvo por inteiro a cada mudança.
// O workspace é assíncrono, remoto e com estado de servidor. Misturá-los faria o
// documento comercial ser reserializado a cada evento. Nada aqui toca `localStorage`.

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { isWorkspaceModeEnabled } from '../config/appMode'
import {
  createWorkspace as apiCreateWorkspace,
  listWorkspaces as apiListWorkspaces,
  patchWorkspaceStatus as apiPatchStatus,
  WorkspaceApiError,
  type Workspace,
  type WorkspaceCreateInput,
  type WorkspaceStatus,
} from '../services/workspaceApi'

interface WorkspaceContextValue {
  enabled: boolean
  workspaces: Workspace[]
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  createWorkspace: (input: WorkspaceCreateInput) => Promise<Workspace>
  updateStatus: (id: string, status: WorkspaceStatus) => Promise<Workspace>
}

const DISABLED_VALUE: WorkspaceContextValue = {
  enabled: false,
  workspaces: [],
  loading: false,
  error: null,
  refresh: async () => undefined,
  createWorkspace: () => Promise.reject(new WorkspaceApiError('workspace_mode_disabled', 'Indisponível fora da execução local.')),
  updateStatus: () => Promise.reject(new WorkspaceApiError('workspace_mode_disabled', 'Indisponível fora da execução local.')),
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null)

function messageOf(error: unknown): string {
  if (error instanceof WorkspaceApiError) return error.message
  if (error instanceof Error) return error.message
  return 'Erro inesperado ao falar com o backend.'
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const enabled = isWorkspaceModeEnabled()
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [loading, setLoading] = useState(enabled)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (!enabled) return
    setLoading(true)
    setError(null)
    try {
      setWorkspaces(await apiListWorkspaces())
    } catch (caught) {
      setError(messageOf(caught))
    } finally {
      setLoading(false)
    }
  }, [enabled])

  const createWorkspace = useCallback(async (input: WorkspaceCreateInput) => {
    const created = await apiCreateWorkspace(input)
    setWorkspaces((current) => [created, ...current.filter((item) => item.id !== created.id)])
    return created
  }, [])

  const updateStatus = useCallback(async (id: string, status: WorkspaceStatus) => {
    const updated = await apiPatchStatus(id, status)
    setWorkspaces((current) => current.map((item) => (item.id === updated.id ? updated : item)))
    return updated
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const value = useMemo<WorkspaceContextValue>(
    () => ({ enabled, workspaces, loading, error, refresh, createWorkspace, updateStatus }),
    [enabled, workspaces, loading, error, refresh, createWorkspace, updateStatus],
  )

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

// Contexto e hook juntos para manter a API pública num módulo só, como AppContext.
// eslint-disable-next-line react-refresh/only-export-components
export function useWorkspaces(): WorkspaceContextValue {
  return useContext(WorkspaceContext) ?? DISABLED_VALUE
}
