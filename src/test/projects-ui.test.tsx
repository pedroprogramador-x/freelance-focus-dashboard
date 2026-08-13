import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProvider } from '../context/AppContext'
import { createDefaultData } from '../data/defaults'
import { ProjectsPage } from '../pages/ProjectsPage'
import { saveData } from '../services/storage'

const prepare = () => {
  const data = createDefaultData('2026-01-01')
  data.clients = [{ id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Outro', referredBy: '', status: 'Cliente ativo', notes: '', createdAt: '2026-01-01', updatedAt: '2026-01-01' }]
  data.proposals = [{ id: 'proposal-1', clientId: 'client-1', serviceId: null, title: 'API aprovada', description: '', amount: 1000, currency: 'BRL', source: 'Contato direto', status: 'Aceita', createdAt: '2026-01-01', sentAt: '2026-01-01', validUntil: null, followUpDate: null, estimatedHours: 20, notes: '' }]
  saveData(data)
}
const renderPage = () => render(<AppProvider><ProjectsPage navigate={vi.fn()} /></AppProvider>)

afterEach(cleanup)

describe('Projetos', () => {
  beforeEach(() => { localStorage.clear(); prepare() })

  it('cria projeto com cliente, proposta opcional, horas e pagamento parcial', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Novo projeto' }))
    const dialog = screen.getByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Proposta relacionada'), { target: { value: 'proposal-1' } })
    fireEvent.change(within(dialog).getByLabelText('Nome do projeto *'), { target: { value: 'API Acme' } })
    fireEvent.change(within(dialog).getByLabelText('Status'), { target: { value: 'Em desenvolvimento' } })
    fireEvent.change(within(dialog).getByLabelText('Valor'), { target: { value: '1000' } })
    fireEvent.change(within(dialog).getByLabelText('Valor recebido'), { target: { value: '400' } })
    fireEvent.change(within(dialog).getByLabelText('Horas estimadas'), { target: { value: '20' } })
    fireEvent.change(within(dialog).getByLabelText('Horas trabalhadas'), { target: { value: '5' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Salvar projeto' }))
    expect(screen.getByText('API Acme')).toBeInTheDocument()
    expect(screen.getAllByText(/1\.000,00/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/400,00/).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/600,00/).length).toBeGreaterThan(0)
  })

  it('edita projeto entregue e registra conclusão e pagamento integral', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Novo projeto' }))
    fireEvent.change(screen.getByLabelText('Nome do projeto *'), { target: { value: 'Entrega final' } })
    fireEvent.change(screen.getByLabelText('Valor'), { target: { value: '500' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar projeto' }))
    fireEvent.click(screen.getByRole('button', { name: 'Editar detalhes' }))
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'Entregue' } })
    expect(screen.getByLabelText('Concluído em')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Pagamento'), { target: { value: 'Pago' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar projeto' }))
    expect(screen.getByText('Entregue')).toBeInTheDocument()
    expect(screen.getAllByText(/500,00/).length).toBeGreaterThan(1)
  })

  it('permite criar sem proposta e excluir o projeto', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Novo projeto' }))
    expect(screen.getByLabelText('Proposta relacionada')).toHaveValue('')
    fireEvent.change(screen.getByLabelText('Nome do projeto *'), { target: { value: 'Projeto avulso' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar projeto' }))
    fireEvent.click(screen.getByRole('button', { name: 'Excluir projeto' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirmar' }))
    expect(screen.queryByText('Projeto avulso')).not.toBeInTheDocument()
  })

  it('pré-preenche projeto a partir de proposta aceita sem criá-lo automaticamente', () => {
    const handled = vi.fn()
    render(<AppProvider><ProjectsPage navigate={vi.fn()} proposalForProjectId="proposal-1" onProposalHandled={handled} /></AppProvider>)
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByLabelText('Cliente *')).toHaveValue('client-1')
    expect(within(dialog).getByLabelText('Proposta relacionada')).toHaveValue('proposal-1')
    expect(within(dialog).getByLabelText('Nome do projeto *')).toHaveValue('API aprovada')
    expect(within(dialog).getByLabelText('Valor')).toHaveValue(1000)
    expect(screen.queryByRole('button', { name: 'Editar detalhes' })).not.toBeInTheDocument()
    expect(handled).toHaveBeenCalled()
  })
})
