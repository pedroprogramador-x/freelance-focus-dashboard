import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkspaceProvider } from '../context/WorkspaceProvider'
import { WorkspaceDetail } from '../pages/WorkspaceDetail'
import { __resetSessionTokenCache, type Workspace } from '../services/workspaceApi'

type Route = { status?: number; body?: unknown }
type Router = (method: string, path: string, body: unknown) => Route

const fakeResponse = (status: number, body: unknown) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => (body == null ? '' : JSON.stringify(body)),
})

function installRouter(router: Router) {
  const spy = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    const path = url.replace(/^\/api/, '')
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? JSON.parse(init.body as string) : undefined
    const { status = 200, body: payload = null } = router(method, path, body)
    return fakeResponse(status, payload) as unknown as Response
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

const zeroCounts = { workspaces: 1, tasks: 0, runs: 0, findings: 0, manifests: 0, artifacts: 0 }

const workspaceFixture = (over: Partial<Workspace> = {}): Workspace => ({
  id: 'w1',
  name: 'Alpha',
  type: 'freelance',
  local_path: '/home/dev/alpha',
  linked_project_id: null,
  repository_url: null,
  default_branch: null,
  status: 'archived',
  created_at: '2026-09-03T10:00:00+00:00',
  updated_at: '2026-09-03T10:00:00+00:00',
  ...over,
})

const renderDetail = (workspace: Workspace, onBack = vi.fn()) => {
  render(
    <WorkspaceProvider>
      <WorkspaceDetail workspace={workspace} onBack={onBack} />
    </WorkspaceProvider>,
  )
  return onBack
}

beforeEach(() => {
  __resetSessionTokenCache()
  vi.stubEnv('VITE_APP_MODE', 'local_dev_workspace')
  const meta = document.createElement('meta')
  meta.name = 'ff-session-token'
  meta.content = 'test-token'
  document.head.appendChild(meta)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  document.head.querySelector('meta[name="ff-session-token"]')?.remove()
})

describe('WorkspaceDetail — abas', () => {
  it('mostra Contexto e Tarefas desabilitadas com indicação de fase futura', async () => {
    installRouter((method, path) => {
      if (path === '/workspaces') return { body: [] }
      if (path === '/workspaces/w1/git') return { body: { is_git_repo: false, head: null, branch: null, dirty_file_count: null } }
      return { status: 404, body: {} }
    })

    renderDetail(workspaceFixture())

    const contexto = await screen.findByRole('tab', { name: /Contexto/ })
    const tarefas = screen.getByRole('tab', { name: /Tarefas/ })
    expect(contexto).toBeDisabled()
    expect(tarefas).toBeDisabled()
    expect(within(contexto).getByText('· fase futura')).toBeInTheDocument()
  })
})

describe('WorkspaceDetail — git preflight', () => {
  it('renderiza HEAD, branch e divergência para um repositório real', async () => {
    installRouter((method, path) => {
      if (path === '/workspaces') return { body: [] }
      if (path === '/workspaces/w1/git') {
        return { body: { is_git_repo: true, head: 'abcdef0123456789abcdef', branch: 'main', dirty_file_count: 3 } }
      }
      return { status: 404, body: {} }
    })

    renderDetail(workspaceFixture())

    expect(await screen.findByText('abcdef012345')).toBeInTheDocument()
    expect(screen.getByText('main')).toBeInTheDocument()
    expect(screen.getByText('3 arquivo(s) não commitado(s)')).toBeInTheDocument()
  })

  it('explica quando o diretório não é repositório Git', async () => {
    installRouter((method, path) => {
      if (path === '/workspaces') return { body: [] }
      if (path === '/workspaces/w1/git') return { body: { is_git_repo: false, head: null, branch: null, dirty_file_count: null } }
      return { status: 404, body: {} }
    })

    renderDetail(workspaceFixture())

    expect(await screen.findByText(/não é um repositório Git/)).toBeInTheDocument()
  })
})

describe('WorkspaceDetail — purga', () => {
  it('exige a prévia visível antes de habilitar o botão de purgar, e confirma em modal', async () => {
    const onBack = vi.fn()
    const spy = installRouter((method, path) => {
      if (path === '/workspaces') return { body: [] }
      if (path === '/workspaces/w1/git') return { body: { is_git_repo: false, head: null, branch: null, dirty_file_count: null } }
      if (method === 'GET' && path === '/workspaces/w1/purge-preview') {
        return { body: { ...zeroCounts, purge_token: 'tok-purge' } }
      }
      if (method === 'POST' && path === '/workspaces/w1/purge') return { body: zeroCounts }
      return { status: 404, body: {} }
    })

    renderDetail(workspaceFixture(), onBack)

    const purgeButton = await screen.findByRole('button', { name: 'Purgar workspace' })
    expect(purgeButton).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: /Carregar prévia da purga/ }))
    await waitFor(() => expect(screen.getByText('Será removido')).toBeInTheDocument())
    expect(purgeButton).toBeEnabled()

    fireEvent.click(purgeButton)
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Purgar definitivamente' }))

    await waitFor(() => expect(onBack).toHaveBeenCalled())
    expect(spy).toHaveBeenCalledWith(
      '/api/workspaces/w1/purge',
      expect.objectContaining({ method: 'POST' }),
    )
  })

  it('para workspace ativo, instrui a arquivar antes', async () => {
    installRouter((method, path) => {
      if (path === '/workspaces') return { body: [] }
      if (path === '/workspaces/w1/git') return { body: { is_git_repo: false, head: null, branch: null, dirty_file_count: null } }
      return { status: 404, body: {} }
    })

    renderDetail(workspaceFixture({ status: 'active' }))

    expect(await screen.findByText('Arquive o workspace antes de poder purgá-lo.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Purgar workspace' })).not.toBeInTheDocument()
  })
})
