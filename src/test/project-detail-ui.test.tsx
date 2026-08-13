import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProvider } from '../context/AppContext'
import { createDefaultData } from '../data/defaults'
import { createEmptyProjectPlanning } from '../data/domain'
import { ProjectsPage } from '../pages/ProjectsPage'
import { saveData, STORAGE_KEY } from '../services/storage'

function prepare(withRelated = false) {
  const data = createDefaultData('2026-08-13')
  data.clients = [{ id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Contato direto', referredBy: '', status: 'Cliente ativo', notes: '', createdAt: '2026-08-13', updatedAt: '2026-08-13' }]
  data.projects = [{ id: 'project-1', clientId: 'client-1', proposalId: null, name: 'Automação de planilhas', description: 'Processamento de XLSX', status: 'Em desenvolvimento', startDate: '2026-08-13', deadline: '2026-08-30', completedAt: null, amount: 2400, currency: 'BRL', platformFeePercent: null, exchangeRateToBrl: null, estimatedHours: 20, workedHours: 4, repositoryUrl: 'https://github.com/example/project', productionUrl: '', paymentStatus: 'Parcial', amountReceived: 900, notes: 'Validar amostras do cliente.', createdAt: '2026-08-13', updatedAt: '2026-08-13' }]
  if (withRelated) {
    data.settings.confirmBeforeDelete = false
    data.projectPlannings = [createEmptyProjectPlanning('project-1', '2026-08-13')]
    data.projectTasks = [{ id: 'task-1', projectId: 'project-1', title: 'Validar colunas', description: '', status: 'Pendente', priority: 'Alta', deadline: '2026-08-20', completedAt: null, createdAt: '2026-08-13', updatedAt: '2026-08-13' }]
  }
  saveData(data)
}

const renderPage = () => render(<AppProvider><ProjectsPage navigate={vi.fn()} /></AppProvider>)
const openProject = () => fireEvent.click(screen.getByRole('button', { name: 'Abrir projeto' }))

afterEach(cleanup)

describe('detalhe, planejamento e tarefas do projeto', () => {
  beforeEach(() => { localStorage.clear(); prepare() })

  it('exibe visão geral, notas e progresso vazio sem mostrar 100%', () => {
    renderPage()
    openProject()
    expect(screen.getByRole('heading', { name: 'Automação de planilhas' })).toBeInTheDocument()
    expect(screen.getAllByText('R$ 2.400,00').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Nenhuma tarefa cadastrada.').length).toBeGreaterThan(0)
    expect(screen.queryByText('100%')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: 'Notas' }))
    expect(screen.getByText('Validar amostras do cliente.')).toBeInTheDocument()
  })

  it('cria e edita planejamento com requisitos, stack, arquitetura, decisão e risco', async () => {
    renderPage()
    openProject()
    fireEvent.click(screen.getByRole('tab', { name: 'Planejamento' }))
    fireEvent.change(screen.getByLabelText(/Qual problema do cliente/), { target: { value: 'Cálculo manual sujeito a erro' } })
    fireEvent.change(screen.getByLabelText(/Qual resultado o cliente/), { target: { value: 'Gerar relatório automaticamente' } })

    const functional = screen.getByRole('heading', { name: 'Requisitos funcionais' }).closest('section')!
    fireEvent.click(within(functional).getByRole('button', { name: 'Adicionar' }))
    fireEvent.change(within(functional).getByLabelText('Requisitos funcionais 1'), { target: { value: 'Importar XLSX' } })
    const nonFunctional = screen.getByRole('heading', { name: 'Requisitos não funcionais' }).closest('section')!
    fireEvent.click(within(nonFunctional).getByRole('button', { name: 'Adicionar' }))
    fireEvent.change(within(nonFunctional).getByLabelText('Requisitos não funcionais 1'), { target: { value: 'Processar em até 10 segundos' } })

    const stack = screen.getByRole('heading', { name: 'Stack' }).closest('section')!
    fireEvent.change(within(stack).getByLabelText('Nova tecnologia'), { target: { value: 'Python' } })
    fireEvent.click(within(stack).getByRole('button', { name: 'Adicionar' }))
    const architecture = screen.getByRole('heading', { name: 'Arquitetura' }).closest('section')!
    fireEvent.change(within(architecture).getByLabelText('Descrição da arquitetura'), { target: { value: 'XLSX\n↓\nPandas\n↓\nRelatório' } })

    const decisions = screen.getByRole('heading', { name: 'Decisões técnicas' }).closest('section')!
    fireEvent.click(within(decisions).getByRole('button', { name: 'Adicionar' }))
    fireEvent.change(within(decisions).getByLabelText('Título *'), { target: { value: 'Banco de dados' } })
    fireEvent.change(within(decisions).getByLabelText('Decisão'), { target: { value: 'Não utilizar' } })
    fireEvent.change(within(decisions).getByLabelText('Motivo'), { target: { value: 'Não há persistência' } })
    const risks = screen.getByRole('heading', { name: 'Riscos' }).closest('section')!
    fireEvent.click(within(risks).getByRole('button', { name: 'Adicionar' }))
    fireEvent.change(within(risks).getByLabelText('Descrição *'), { target: { value: 'Cliente alterar colunas' } })
    fireEvent.change(within(risks).getByLabelText('Mitigação'), { target: { value: 'Validar cabeçalhos' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar planejamento' }))

    fireEvent.change(within(functional).getByLabelText('Requisitos funcionais 1'), { target: { value: 'Importar e validar XLSX' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar planejamento' }))
    await waitFor(() => {
      const persisted = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
      expect(persisted.projectPlannings).toHaveLength(1)
      expect(persisted.projectPlannings[0]).toMatchObject({ problem: 'Cálculo manual sujeito a erro', architecture: 'XLSX\n↓\nPandas\n↓\nRelatório', functionalRequirements: ['Importar e validar XLSX'], stack: ['Python'] })
      expect(persisted.projectPlannings[0].technicalDecisions[0].title).toBe('Banco de dados')
      expect(persisted.projectPlannings[0].risks[0].description).toBe('Cliente alterar colunas')
    })
  })

  it('cria, edita, filtra, conclui e exclui tarefas mantendo completedAt coerente', async () => {
    renderPage()
    openProject()
    fireEvent.click(screen.getByRole('tab', { name: 'Tarefas (0)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Nova tarefa' }))
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText('Título *'), { target: { value: 'Validar planilha' } })
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText('Prioridade'), { target: { value: 'Alta' } })
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText('Prazo'), { target: { value: '2026-08-20' } })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Salvar tarefa' }))
    expect(screen.getByText('Validar planilha')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Status de Validar planilha'), { target: { value: 'Concluído' } })
    expect(screen.getByText('1 / 1 concluídas')).toBeInTheDocument()
    expect(screen.getAllByText('100%').length).toBeGreaterThan(0)

    await waitFor(() => {
      const persisted = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
      expect(persisted.projectTasks[0].completedAt).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    })
    fireEvent.change(screen.getByLabelText('Status de Validar planilha'), { target: { value: 'Em andamento' } })
    await waitFor(() => expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).projectTasks[0].completedAt).toBeNull())

    fireEvent.click(screen.getByLabelText('Editar Validar planilha'))
    fireEvent.change(within(screen.getByRole('dialog')).getByLabelText('Título *'), { target: { value: 'Validar arquivo do cliente' } })
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Salvar tarefa' }))
    const filters = screen.getByRole('region', { name: 'Filtros de tarefas' })
    fireEvent.change(within(filters).getByLabelText('Status'), { target: { value: 'Pendente' } })
    expect(screen.queryByText('Validar arquivo do cliente')).not.toBeInTheDocument()
    fireEvent.change(within(filters).getByLabelText('Status'), { target: { value: '' } })
    fireEvent.click(screen.getByLabelText('Excluir Validar arquivo do cliente'))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirmar' }))
    expect(screen.queryByText('Validar arquivo do cliente')).not.toBeInTheDocument()
  })

  it('exige confirmação explícita e informa a exclusão conjunta mesmo com confirmação global desativada', () => {
    localStorage.clear()
    prepare(true)
    renderPage()
    openProject()
    fireEvent.click(screen.getByRole('button', { name: 'Excluir projeto' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(/serão excluídos explicitamente 1 planejamento/)).toBeInTheDocument()
    expect(within(dialog).getByText(/1 tarefa/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirmar' }))
    expect(screen.queryByText('Automação de planilhas')).not.toBeInTheDocument()
  })
})
