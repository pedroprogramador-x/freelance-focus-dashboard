import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkspaceProvider } from '../context/WorkspaceProvider'
import { DevWorkspaces } from '../pages/DevWorkspaces'
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

const workspaceFixture = (over: Partial<Workspace> = {}): Workspace => ({
  id: 'w1',
  name: 'Alpha',
  type: 'freelance',
  local_path: '/home/dev/alpha',
  linked_project_id: null,
  repository_url: null,
  default_branch: null,
  status: 'active',
  created_at: '2026-09-03T10:00:00+00:00',
  updated_at: '2026-09-03T10:00:00+00:00',
  ...over,
})

const renderPage = () => render(<WorkspaceProvider><DevWorkspaces /></WorkspaceProvider>)

beforeEach(() => {
  __resetSessionTokenCache()
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

describe('DevWorkspaces em HOSTED_COMMERCIAL_ONLY', () => {
  it('mostra o estado indisponível e não faz nenhuma chamada de rede', async () => {
    const spy = installRouter(() => ({ body: [] }))

    renderPage()

    expect(await screen.findByText('Disponível apenas na execução local')).toBeInTheDocument()
    // dá tempo de um eventual efeito assíncrono disparar
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(spy).not.toHaveBeenCalled()
  })
})

describe('DevWorkspaces em LOCAL_DEV_WORKSPACE', () => {
  beforeEach(() => {
    vi.stubEnv('VITE_APP_MODE', 'local_dev_workspace')
  })

  it('lista os workspaces vindos da API', async () => {
    installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces') {
        return { body: [workspaceFixture(), workspaceFixture({ id: 'w2', name: 'Beta', status: 'archived' })] }
      }
      return { status: 404, body: { code: 'not_found' } }
    })

    renderPage()

    expect(await screen.findByText('Alpha')).toBeInTheDocument()
    expect(screen.getByText('Beta')).toBeInTheDocument()
    expect(screen.getByText('archived')).toBeInTheDocument()
  })

  it('cria um workspace e ele aparece na lista', async () => {
    const created = workspaceFixture({ id: 'w9', name: 'Novo', local_path: '/home/dev/novo' })
    const spy = installRouter((method, path, body) => {
      if (method === 'GET' && path === '/workspaces') return { body: [] }
      if (method === 'POST' && path === '/workspaces') {
        expect(body).toMatchObject({ name: 'Novo', type: 'study', local_path: '/home/dev/novo' })
        return { status: 201, body: created }
      }
      return { status: 404, body: {} }
    })

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Novo workspace' }))

    const dialog = screen.getByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Nome *'), { target: { value: 'Novo' } })
    fireEvent.change(within(dialog).getByLabelText('Tipo'), { target: { value: 'study' } })
    fireEvent.change(within(dialog).getByLabelText('Caminho local (absoluto) *'), {
      target: { value: '/home/dev/novo' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar workspace' }))

    expect(await screen.findByText('Novo')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith('/api/workspaces', expect.objectContaining({ method: 'POST' }))
  })

  it('mostra a mensagem de erro quando a criação falha', async () => {
    installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces') return { body: [] }
      if (method === 'POST' && path === '/workspaces') {
        return { status: 422, body: { code: 'invalid_local_path', message: 'local_path não existe ou não é acessível' } }
      }
      return { status: 404, body: {} }
    })

    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Novo workspace' }))
    const dialog = screen.getByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Nome *'), { target: { value: 'X' } })
    fireEvent.change(within(dialog).getByLabelText('Caminho local (absoluto) *'), {
      target: { value: '/nao/existe' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Registrar workspace' }))

    expect(await screen.findByText('local_path não existe ou não é acessível')).toBeInTheDocument()
  })

  it('mostra o erro de carregamento da lista', async () => {
    installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces') return { status: 500, body: { code: 'internal_error', message: 'falhou' } }
      return { status: 404, body: {} }
    })

    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('falhou')
  })
})
