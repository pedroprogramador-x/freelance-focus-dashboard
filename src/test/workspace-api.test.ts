import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetSessionTokenCache,
  createWorkspace,
  getGitPreflight,
  getPurgePreview,
  listWorkspaces,
  patchWorkspaceStatus,
  purgeWorkspace,
  WorkspaceApiError,
} from '../services/workspaceApi'

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown }

const fakeResponse = (status: number, body: unknown) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => (body === undefined || body === null ? '' : JSON.stringify(body)),
})

function installFetch(handler: Handler) {
  const spy = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const { status = 200, body = null } = handler(url, init)
    return fakeResponse(status, body) as unknown as Response
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

function setSessionMeta(token: string) {
  const meta = document.createElement('meta')
  meta.name = 'ff-session-token'
  meta.content = token
  document.head.appendChild(meta)
}

beforeEach(() => {
  __resetSessionTokenCache()
  document.head.querySelector('meta[name="ff-session-token"]')?.remove()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('workspaceApi em HOSTED_COMMERCIAL_ONLY', () => {
  it('nenhuma função chega a chamar fetch', async () => {
    const spy = installFetch(() => ({ body: [] }))

    await expect(listWorkspaces()).rejects.toBeInstanceOf(WorkspaceApiError)
    await expect(
      createWorkspace({ name: 'x', type: 'personal', local_path: '/tmp/x' }),
    ).rejects.toBeInstanceOf(WorkspaceApiError)
    await expect(patchWorkspaceStatus('id', 'archived')).rejects.toBeInstanceOf(WorkspaceApiError)
    await expect(getGitPreflight('id')).rejects.toBeInstanceOf(WorkspaceApiError)
    await expect(getPurgePreview('id')).rejects.toBeInstanceOf(WorkspaceApiError)
    await expect(purgeWorkspace('id', 'token')).rejects.toBeInstanceOf(WorkspaceApiError)

    expect(spy).not.toHaveBeenCalled()
    await expect(listWorkspaces()).rejects.toMatchObject({ code: 'workspace_mode_disabled' })
  })
})

describe('workspaceApi em LOCAL_DEV_WORKSPACE', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_APP_MODE', 'local_dev_workspace')
  })

  it('anexa Authorization: Bearer a partir da <meta> e remove a meta do DOM', async () => {
    setSessionMeta('tok-abc')
    const spy = installFetch(() => ({ body: [] }))

    await listWorkspaces()

    const [url, init] = spy.mock.calls[0]
    expect(url).toBe('/api/workspaces')
    const headers = new Headers((init as RequestInit).headers)
    expect(headers.get('Authorization')).toBe('Bearer tok-abc')
    // a meta é copiada para memória e retirada do DOM (docs/architecture/06 §1)
    expect(document.head.querySelector('meta[name="ff-session-token"]')).toBeNull()
  })

  it('createWorkspace faz POST com corpo JSON', async () => {
    setSessionMeta('tok-1')
    const spy = installFetch(() => ({ status: 201, body: { id: 'w1', name: 'x' } }))

    await createWorkspace({ name: 'x', type: 'study', local_path: '/tmp/x' })

    const [url, init] = spy.mock.calls[0]
    expect(url).toBe('/api/workspaces')
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({
      name: 'x',
      type: 'study',
      local_path: '/tmp/x',
    })
  })

  it('erro HTTP vira WorkspaceApiError com code e status', async () => {
    setSessionMeta('tok-1')
    installFetch(() => ({ status: 422, body: { code: 'invalid_local_path', message: 'ruim' } }))

    await expect(
      createWorkspace({ name: 'x', type: 'personal', local_path: 'relativo' }),
    ).rejects.toMatchObject({ code: 'invalid_local_path', status: 422, message: 'ruim' })
  })

  it('purgeWorkspace envia o purge_token no corpo', async () => {
    setSessionMeta('tok-1')
    const spy = installFetch(() => ({ body: { workspaces: 1, tasks: 0, runs: 0, findings: 0, manifests: 0, artifacts: 0 } }))

    await purgeWorkspace('w1', 'purge-xyz')

    const [url, init] = spy.mock.calls[0]
    expect(url).toBe('/api/workspaces/w1/purge')
    expect(JSON.parse((init as RequestInit).body as string)).toEqual({ purge_token: 'purge-xyz' })
  })
})
