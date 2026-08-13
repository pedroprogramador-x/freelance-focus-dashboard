import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { AppProvider } from '../context/AppContext'
import { createDefaultData } from '../data/defaults'
import { createEmptyProjectPlanning } from '../data/domain'
import { ProjectsPage } from '../pages/ProjectsPage'
import { saveData, STORAGE_KEY } from '../services/storage'
import type { ProjectPlanning } from '../types'
import { hasUnsavedPlanningChanges } from '../utils/projectPlanning'

function prepare(existingPlanning = false) {
  const data = createDefaultData('2026-08-13')
  data.settings.notificationsEnabled = false
  data.clients = [{ id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Contato direto', referredBy: '', status: 'Cliente ativo', notes: '', createdAt: '2026-08-13', updatedAt: '2026-08-13' }]
  const baseProject = { id: 'project-1', clientId: 'client-1', proposalId: null, name: 'Projeto principal', description: '', status: 'Em desenvolvimento' as const, startDate: '2026-08-13', deadline: null, completedAt: null, amount: 1000, currency: 'BRL' as const, platformFeePercent: null, exchangeRateToBrl: null, estimatedHours: 10, workedHours: 2, repositoryUrl: '', productionUrl: '', paymentStatus: 'Pendente' as const, amountReceived: 0, notes: '', createdAt: '2026-08-13', updatedAt: '2026-08-13' }
  data.projects = [baseProject, { ...baseProject, id: 'project-2', name: 'Outro projeto' }]
  if (existingPlanning) data.projectPlannings = [{ ...createEmptyProjectPlanning('project-1', '2026-08-13'), id: 'planning-1', problem: 'Problema persistido', stack: ['Python'] }]
  saveData(data)
}

const renderProjects = (navigate = vi.fn(), onPlanningDirtyChange = vi.fn()) => render(<AppProvider><ProjectsPage navigate={navigate} onPlanningDirtyChange={onPlanningDirtyChange} /></AppProvider>)
const openFirstPlanning = () => {
  fireEvent.click(screen.getAllByRole('button', { name: 'Abrir projeto' })[0])
  fireEvent.click(screen.getByRole('tab', { name: 'Planejamento' }))
}
const problemField = () => screen.getByLabelText(/Qual problema do cliente/)

afterEach(() => { cleanup(); window.history.replaceState(null, '', '#dashboard'); vi.restoreAllMocks() })

describe('proteção de alterações não salvas no planejamento', () => {
  beforeEach(() => { localStorage.clear(); prepare() })

  it('inicia planning vazio limpo, ativa dirty com alteração real e limpa ao restaurar', () => {
    renderProjects()
    openFirstPlanning()
    expect(screen.queryByText('● Alterações não salvas')).not.toBeInTheDocument()
    fireEvent.change(problemField(), { target: { value: 'Texto temporário' } })
    expect(screen.getByText('● Alterações não salvas')).toBeInTheDocument()
    fireEvent.change(problemField(), { target: { value: '' } })
    expect(screen.queryByText('● Alterações não salvas')).not.toBeInTheDocument()
  })

  it('carrega planning existente limpo, detecta mudança e reconhece o valor original', () => {
    localStorage.clear()
    prepare(true)
    renderProjects()
    openFirstPlanning()
    expect(problemField()).toHaveValue('Problema persistido')
    expect(screen.queryByText('● Alterações não salvas')).not.toBeInTheDocument()
    fireEvent.change(problemField(), { target: { value: 'Problema alterado' } })
    expect(screen.getByText('● Alterações não salvas')).toBeInTheDocument()
    fireEvent.change(problemField(), { target: { value: 'Problema persistido' } })
    expect(screen.queryByText('● Alterações não salvas')).not.toBeInTheDocument()
  })

  it('salva planning novo, atualiza o snapshot e remove dirty', async () => {
    renderProjects()
    openFirstPlanning()
    fireEvent.change(problemField(), { target: { value: 'Problema salvo' } })
    expect(screen.getByText('● Alterações não salvas')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Salvar planejamento' }))
    expect(screen.queryByText('● Alterações não salvas')).not.toBeInTheDocument()
    await waitFor(() => expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).projectPlannings[0].problem).toBe('Problema salvo'))
    fireEvent.click(screen.getByRole('tab', { name: /Tarefas/ }))
    expect(screen.queryByRole('dialog', { name: 'Alterações não salvas' })).not.toBeInTheDocument()
  })

  it('protege tecnologia digitada e a inclui ao salvar mesmo antes de usar Adicionar', async () => {
    renderProjects()
    openFirstPlanning()
    fireEvent.change(screen.getByLabelText('Nova tecnologia'), { target: { value: 'Python' } })
    expect(screen.getByText('● Alterações não salvas')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Salvar planejamento' }))
    expect(screen.queryByText('● Alterações não salvas')).not.toBeInTheDocument()
    await waitFor(() => expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).projectPlannings[0].stack).toEqual(['Python']))
  })

  it('bloqueia troca de aba, e cancelar preserva aba, conteúdo, dirty e foco', () => {
    renderProjects()
    openFirstPlanning()
    fireEvent.change(problemField(), { target: { value: 'Ainda editando' } })
    const tasksTab = screen.getByRole('tab', { name: /Tarefas/ })
    tasksTab.focus()
    fireEvent.click(tasksTab)
    const dialog = screen.getByRole('dialog', { name: 'Alterações não salvas' })
    expect(within(dialog).getByRole('button', { name: 'Continuar editando' })).toHaveFocus()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continuar editando' }))
    expect(screen.getByRole('tab', { name: 'Planejamento' })).toHaveAttribute('aria-selected', 'true')
    expect(problemField()).toHaveValue('Ainda editando')
    expect(screen.getByText('● Alterações não salvas')).toBeInTheDocument()
    expect(tasksTab).toHaveFocus()
  })

  it('descartar permite trocar de aba e recarrega somente os dados persistidos ao retornar', () => {
    renderProjects()
    openFirstPlanning()
    fireEvent.change(problemField(), { target: { value: 'Não persistir' } })
    fireEvent.click(screen.getByRole('tab', { name: /Tarefas/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Sair sem salvar' }))
    expect(screen.getByRole('tab', { name: /Tarefas/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('tab', { name: 'Planejamento' }))
    expect(problemField()).toHaveValue('')
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).projectPlannings).toEqual([])
  })

  it('navega sem confirmação quando o formulário está limpo', () => {
    renderProjects()
    openFirstPlanning()
    fireEvent.click(screen.getByRole('tab', { name: /Tarefas/ }))
    expect(screen.getByRole('tab', { name: /Tarefas/ })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('protege a saída para outro projeto: cancelar mantém o atual e descartar permite abrir o outro', () => {
    renderProjects()
    openFirstPlanning()
    fireEvent.change(problemField(), { target: { value: 'Rascunho local' } })
    fireEvent.click(screen.getByRole('button', { name: 'Voltar para projetos' }))
    fireEvent.click(screen.getByRole('button', { name: 'Continuar editando' }))
    expect(screen.getByRole('heading', { name: 'Projeto principal' })).toBeInTheDocument()
    expect(problemField()).toHaveValue('Rascunho local')
    fireEvent.click(screen.getByRole('button', { name: 'Voltar para projetos' }))
    fireEvent.click(screen.getByRole('button', { name: 'Sair sem salvar' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Abrir projeto' })[1])
    expect(screen.getByRole('heading', { name: 'Outro projeto' })).toBeInTheDocument()
  })

  it('protege a navegação global para outra página', async () => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined)
    window.history.replaceState(null, '', '#projects')
    render(<AppProvider><App /></AppProvider>)
    await waitFor(() => expect(screen.getAllByRole('button', { name: 'Abrir projeto' })).toHaveLength(2))
    openFirstPlanning()
    fireEvent.change(problemField(), { target: { value: 'Mudança global' } })
    fireEvent.click(screen.getByRole('button', { name: 'Painel' }))
    expect(screen.getByRole('dialog', { name: 'Alterações não salvas' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continuar editando' }))
    expect(screen.getByRole('heading', { name: 'Projeto principal' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Painel' }))
    fireEvent.click(screen.getByRole('button', { name: 'Sair sem salvar' }))
    expect(await screen.findByRole('heading', { name: 'Painel', level: 1 })).toBeInTheDocument()
  })

  it('registra beforeunload somente com dirty e remove após salvar e desmontar', () => {
    const addListener = vi.spyOn(window, 'addEventListener')
    const removeListener = vi.spyOn(window, 'removeEventListener')
    const view = renderProjects()
    openFirstPlanning()
    expect(addListener).not.toHaveBeenCalledWith('beforeunload', expect.any(Function))
    const cleanEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(cleanEvent)
    expect(cleanEvent.defaultPrevented).toBe(false)

    fireEvent.change(problemField(), { target: { value: 'Protegido' } })
    expect(addListener).toHaveBeenCalledWith('beforeunload', expect.any(Function))
    const dirtyEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(dirtyEvent)
    expect(dirtyEvent.defaultPrevented).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Salvar planejamento' }))
    expect(removeListener).toHaveBeenCalledWith('beforeunload', expect.any(Function))
    const savedEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(savedEvent)
    expect(savedEvent.defaultPrevented).toBe(false)

    fireEvent.change(problemField(), { target: { value: 'Alterado novamente' } })
    view.unmount()
    const unmountedEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unmountedEvent)
    expect(unmountedEvent.defaultPrevented).toBe(false)
  })
})

describe('comparação semântica do ProjectPlanning', () => {
  it('ignora metadados e detecta mudanças reais em todas as áreas editáveis', () => {
    const base: ProjectPlanning = { ...createEmptyProjectPlanning('project-1', '2026-08-13'), id: 'planning-1' }
    expect(hasUnsavedPlanningChanges({ ...base, id: 'outro-id', updatedAt: '2026-08-14' }, base)).toBe(false)
    const changes: ProjectPlanning[] = [
      { ...base, problem: 'Problema' }, { ...base, objective: 'Objetivo' },
      { ...base, functionalRequirements: ['Importar'] }, { ...base, nonFunctionalRequirements: ['Rápido'] },
      { ...base, stack: ['Python'] }, { ...base, architecture: 'Entrada → saída' },
      { ...base, technicalDecisions: [{ id: 'decision-1', title: 'Banco', decision: 'Não usar', reason: 'Sem persistência' }] },
      { ...base, risks: [{ id: 'risk-1', description: 'Formato mudar', mitigation: 'Validar' }] },
    ]
    for (const changed of changes) expect(hasUnsavedPlanningChanges(changed, base)).toBe(true)
  })
})
