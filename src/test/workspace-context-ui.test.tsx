// Gate 8 — a aba de Contexto renderizada, com o backend simulado no nível do `fetch`.
//
// O que estes casos protegem, além do caminho feliz:
//
// - o indicador de estado mostra os quatro estados **e o motivo** de cada `stale`;
// - editar só o texto manda um `PATCH` **sem** `source_refs` — a prova, do lado do
//   cliente, de que uma correção de digitação não reconfirma a linha de base;
// - `Verificar` mostra o commit verificado e a divergência coberta, e diz que não cura;
// - a importação exige confirmação depois de uma prévia, e avisa que sempre cria.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProvider } from '../context/AppContext'
import { ContextTab, type PlanningChoice } from '../pages/WorkspaceContext'
import { __resetSessionTokenCache } from '../services/workspaceApi'
import type { ContextEntry } from '../services/contextApi'
import type { ProjectPlanning } from '../types'

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
    const body: unknown = init?.body ? JSON.parse(init.body as string) : undefined
    const { status = 200, body: payload = null } = router(method, path, body)
    return fakeResponse(status, payload) as unknown as Response
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

const entryFixture = (over: Partial<ContextEntry> = {}): ContextEntry => ({
  id: 'e1',
  workspace_id: 'w1',
  domain: 'modules',
  title: 'Módulo src',
  body: 'Descreve o código sob src/.',
  structured: null,
  tags: ['backend'],
  source_refs: ['src/**'],
  content_hash: 'c'.repeat(64),
  source_hash: 's'.repeat(64),
  source_hash_commit: 'a'.repeat(40),
  state: 'fresh',
  stale_reason: null,
  origin: 'manual',
  last_verified_at: null,
  last_verified_commit: null,
  created_at: '2026-09-05T10:00:00+00:00',
  updated_at: '2026-09-05T10:00:00+00:00',
  ...over,
})

const renderTab = (plannings?: PlanningChoice[]) =>
  render(
    <AppProvider>
      <ContextTab workspaceId="w1" plannings={plannings} />
    </AppProvider>,
  )

const planningFixture = (): ProjectPlanning => ({
  id: 'p1',
  projectId: 'proj1',
  problem: 'Problema',
  objective: 'Objetivo',
  functionalRequirements: ['RF1'],
  nonFunctionalRequirements: [],
  stack: ['React'],
  architecture: '',
  technicalDecisions: [{ id: 'd1', title: 'D1', decision: 'x', reason: 'y' }],
  risks: [],
  createdAt: '2026-09-05T10:00:00+00:00',
  updatedAt: '2026-09-05T10:00:00+00:00',
})

// Ajuda a alcançar o botão certo: `Tags` e `Fontes` têm ambos um "Adicionar".
const fieldOf = (input: HTMLElement) => {
  const fieldset = input.closest('fieldset')
  if (!fieldset) throw new Error('campo sem fieldset')
  return within(fieldset as HTMLElement)
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
  window.localStorage.clear()
})

describe('ContextTab — listagem e indicador de estado', () => {
  it('mostra vazio quando não há entradas', async () => {
    installRouter((_method, path) => (path === '/workspaces/w1/context' ? { body: [] } : { status: 404, body: {} }))

    renderTab()

    expect(await screen.findByText('Nenhuma entrada de contexto')).toBeInTheDocument()
  })

  it('rotula cada um dos quatro estados, com o motivo em cada stale', async () => {
    installRouter((_method, path) =>
      path === '/workspaces/w1/context'
        ? {
            body: [
              entryFixture({ id: 'a', title: 'Fresca', state: 'fresh', stale_reason: null }),
              entryFixture({ id: 'b', title: 'Código mudou', state: 'stale', stale_reason: 'sources_changed' }),
              entryFixture({ id: 'c', title: 'Árvore suja', state: 'stale', stale_reason: 'working_tree' }),
              entryFixture({ id: 'd', title: 'Sem git', state: 'unknown', stale_reason: null }),
            ],
          }
        : { status: 404, body: {} },
    )

    renderTab()

    expect(await screen.findByText('Atualizada')).toBeInTheDocument()
    expect(screen.getByText('Desatualizada · o código mudou')).toBeInTheDocument()
    expect(screen.getByText('Desatualizada · trabalho não commitado')).toBeInTheDocument()
    expect(screen.getByText('Não verificável')).toBeInTheDocument()
  })

  it('mostra as fontes declaradas e sinaliza quando não há nenhuma', async () => {
    installRouter((_method, path) =>
      path === '/workspaces/w1/context'
        ? {
            body: [
              entryFixture({ id: 'a', source_refs: ['src/**', 'docs/*.md'] }),
              entryFixture({ id: 'b', title: 'Autoral', source_refs: [], origin: 'imported_planning' }),
            ],
          }
        : { status: 404, body: {} },
    )

    renderTab()

    expect(await screen.findByText('src/**')).toBeInTheDocument()
    expect(screen.getByText('docs/*.md')).toBeInTheDocument()
    expect(screen.getByText('sem fontes declaradas')).toBeInTheDocument()
    expect(screen.getByText('importada do planejamento')).toBeInTheDocument()
  })

  it('mostra o erro do backend sem inventar mensagem própria', async () => {
    installRouter(() => ({ status: 404, body: { code: 'workspace_not_found', message: 'workspace não encontrado' } }))

    renderTab()

    expect(await screen.findByRole('alert')).toHaveTextContent('workspace não encontrado')
  })
})

describe('ContextTab — editor', () => {
  it('editar só o texto manda um PATCH sem source_refs', async () => {
    const spy = installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces/w1/context') return { body: [entryFixture()] }
      if (method === 'PATCH' && path === '/context/e1') {
        return { body: entryFixture({ body: 'Corpo revisado' }) }
      }
      return { status: 404, body: {} }
    })

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/Conteúdo \*/), {
      target: { value: 'Corpo revisado' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: /Salvar entrada/ }))

    await waitFor(() => {
      const patch = spy.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
      expect(patch).toBeDefined()
      const sent = JSON.parse((patch?.[1] as RequestInit).body as string) as Record<string, unknown>
      expect(sent).toEqual({ body: 'Corpo revisado' })
      expect('source_refs' in sent).toBe(false)
    })
  })

  it('alterar as fontes avisa que a linha de base vai ser reconfirmada, e as envia', async () => {
    const spy = installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces/w1/context') return { body: [entryFixture()] }
      if (method === 'PATCH' && path === '/context/e1') return { body: entryFixture() }
      return { status: 404, body: {} }
    })

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialog = await screen.findByRole('dialog')
    const fontes = within(dialog).getByLabelText('Fontes (source_refs)')
    fireEvent.change(fontes, { target: { value: 'docs/**' } })
    fireEvent.click(fieldOf(fontes).getByRole('button', { name: 'Adicionar' }))

    expect(await within(dialog).findByText(/reconfirmar a linha de base/)).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: /Salvar entrada/ }))

    await waitFor(() => {
      const patch = spy.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')
      const sent = JSON.parse((patch?.[1] as RequestInit).body as string) as Record<string, unknown>
      expect(sent).toEqual({ source_refs: ['src/**', 'docs/**'] })
    })
  })

  it('cria uma entrada nova pelo POST', async () => {
    const spy = installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces/w1/context') return { body: [] }
      if (method === 'POST' && path === '/workspaces/w1/context') {
        return { status: 201, body: entryFixture({ id: 'novo', title: 'Contrato X' }) }
      }
      return { status: 404, body: {} }
    })

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: /Nova entrada/ }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/Título \*/), { target: { value: 'Contrato X' } })
    fireEvent.change(within(dialog).getByLabelText(/Conteúdo \*/), { target: { value: 'Corpo' } })
    fireEvent.click(within(dialog).getByRole('button', { name: /Salvar entrada/ }))

    await waitFor(() => {
      const post = spy.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'POST')
      const sent = JSON.parse((post?.[1] as RequestInit).body as string) as Record<string, unknown>
      expect(sent).toMatchObject({ domain: 'modules', title: 'Contrato X', body: 'Corpo' })
      expect(sent).not.toHaveProperty('origin')
    })
  })

  it('recusa JSON inválido no campo estruturado antes de chamar a API', async () => {
    const spy = installRouter((method, path) =>
      method === 'GET' && path === '/workspaces/w1/context' ? { body: [entryFixture()] } : { status: 404, body: {} },
    )

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Conteúdo estruturado'), {
      target: { value: '{quebrado' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: /Salvar entrada/ }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent('JSON inválido')
    expect(spy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')).toBe(false)
  })

  it('avisa antes de descartar alterações não salvas', async () => {
    installRouter((method, path) =>
      method === 'GET' && path === '/workspaces/w1/context' ? { body: [entryFixture()] } : { status: 404, body: {} },
    )

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText(/Título \*/), { target: { value: 'mexido' } })
    expect(within(dialog).getByText('● Alterações não salvas')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancelar' }))

    expect(await screen.findByText('Alterações não salvas')).toBeInTheDocument()
  })

  it('mostra o motivo do backend quando o source_ref é recusado', async () => {
    installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces/w1/context') return { body: [entryFixture()] }
      if (method === 'PATCH' && path === '/context/e1') {
        return {
          status: 422,
          body: {
            code: 'invalid_source_refs',
            message: 'a expansão alcança um caminho classificado como segredo',
          },
        }
      }
      return { status: 404, body: {} }
    })

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Editar' }))
    const dialog = await screen.findByRole('dialog')
    const fontes = within(dialog).getByLabelText('Fontes (source_refs)')
    fireEvent.change(fontes, { target: { value: 'config/*' } })
    fireEvent.click(fieldOf(fontes).getByRole('button', { name: 'Adicionar' }))
    fireEvent.click(within(dialog).getByRole('button', { name: /Salvar entrada/ }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'a expansão alcança um caminho classificado como segredo',
    )
  })
})

describe('ContextTab — verificação', () => {
  it('mostra o commit verificado, a divergência coberta e o aviso de que não cura', async () => {
    installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces/w1/context') return { body: [entryFixture()] }
      if (method === 'POST' && path === '/workspaces/w1/context/verify') {
        return {
          body: {
            verification_commit: 'abcdef0123456789abcdef0123456789abcdef01',
            entries: [
              entryFixture({
                state: 'stale',
                stale_reason: 'working_tree',
                last_verified_commit: 'abcdef0123456789abcdef0123456789abcdef01',
              }),
            ],
            working_tree_divergence: {
              dirty_file_count: 3,
              covered: [{ path: 'src/app.py', kind: 'modified', entry_id: 'e1' }],
            },
          },
        }
      }
      return { status: 404, body: {} }
    })

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: /Verificar contexto/ }))

    expect(await screen.findByText('abcdef012345')).toBeInTheDocument()
    expect(screen.getByText('3 arquivo(s) divergente(s) no workspace')).toBeInTheDocument()
    expect(screen.getByText('1 caminho(s)')).toBeInTheDocument()
    expect(screen.getByText('src/app.py')).toBeInTheDocument()
    expect(screen.getByText(/nunca move a linha de base/)).toBeInTheDocument()
    expect(screen.getByText('Desatualizada · trabalho não commitado')).toBeInTheDocument()
  })

  it('explica quando não há commit para verificar', async () => {
    installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces/w1/context') return { body: [] }
      if (method === 'POST' && path === '/workspaces/w1/context/verify') {
        return {
          body: {
            verification_commit: null,
            entries: [],
            working_tree_divergence: { dirty_file_count: null, covered: [] },
          },
        }
      }
      return { status: 404, body: {} }
    })

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: /Verificar contexto/ }))

    expect(await screen.findByText('sem commit (não é repositório Git)')).toBeInTheDocument()
    expect(screen.getByText('indisponível')).toBeInTheDocument()
  })
})

describe('ContextTab — importação', () => {
  it('sem planejamento cadastrado, explica em vez de oferecer um botão morto', async () => {
    installRouter((method, path) =>
      method === 'GET' && path === '/workspaces/w1/context' ? { body: [] } : { status: 404, body: {} },
    )

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: /Importar planejamento/ }))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByText(/Nenhum planejamento técnico disponível/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: /Importar 0 entrada/ })).toBeDisabled()
  })

  it('exige confirmação depois da prévia e avisa que sempre cria', async () => {
    const spy = installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces/w1/context') return { body: [] }
      if (method === 'POST' && path === '/workspaces/w1/context/import') {
        return { status: 201, body: { created: 4, entries: [] } }
      }
      return { status: 404, body: {} }
    })

    renderTab([{ planning: planningFixture(), label: 'Projeto Alfa' }])

    fireEvent.click(await screen.findByRole('button', { name: /Importar planejamento/ }))
    const dialog = await screen.findByRole('dialog')

    // a prévia aparece **antes** de qualquer chamada de importação
    expect(within(dialog).getByText('Prévia — será criado')).toBeInTheDocument()
    expect(within(dialog).getByText(/sempre cria entradas novas/)).toBeInTheDocument()
    expect(spy.mock.calls.some(([url]) => String(url).includes('/import'))).toBe(false)

    // objetivo (1) + requisitos funcionais (1) + stack (1) + decisões (1) = 4
    fireEvent.click(within(dialog).getByRole('button', { name: 'Importar 4 entrada(s)' }))

    await waitFor(() => {
      const post = spy.mock.calls.find(([url]) => String(url).includes('/import'))
      expect(post).toBeDefined()
      const sent = JSON.parse((post?.[1] as RequestInit).body as string) as Record<string, unknown>
      expect(sent).toMatchObject({
        problem: 'Problema',
        functional_requirements: ['RF1'],
        stack: ['React'],
      })
      // ADR-0002: nenhum identificador do domínio comercial atravessa a fronteira
      expect(sent).not.toHaveProperty('projectId')
      expect(sent).not.toHaveProperty('id')
    })

    expect(await screen.findByText('4 entrada(s) importada(s).')).toBeInTheDocument()
  })
})

describe('ContextTab — exclusão', () => {
  it('pede confirmação e chama DELETE', async () => {
    const spy = installRouter((method, path) => {
      if (method === 'GET' && path === '/workspaces/w1/context') return { body: [entryFixture()] }
      if (method === 'DELETE' && path === '/context/e1') return { status: 204, body: null }
      return { status: 404, body: {} }
    })

    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Excluir Módulo src' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Excluir' }))

    await waitFor(() => {
      expect(
        spy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'DELETE'),
      ).toBe(true)
    })
  })
})
