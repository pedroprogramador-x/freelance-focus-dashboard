import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AppProvider } from '../context/AppContext'
import { createDefaultData } from '../data/defaults'
import { ProposalsPage } from '../pages/ProposalsPage'
import { saveData } from '../services/storage'

const prepare = () => {
  const data = createDefaultData('2026-01-01')
  data.clients = [{ id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Outro', referredBy: '', status: 'Lead', notes: '', createdAt: '2026-01-01', updatedAt: '2026-01-01' }]
  saveData(data)
}
const renderPage = () => render(<AppProvider><ProposalsPage navigate={vi.fn()} /></AppProvider>)

afterEach(cleanup)

describe('Propostas V2', () => {
  beforeEach(() => { localStorage.clear(); prepare() })

  it('cria proposta em BRL relacionada ao cliente e altera status', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Nova proposta' }))
    fireEvent.change(screen.getByLabelText('Título *'), { target: { value: 'Dashboard financeiro' } })
    fireEvent.change(screen.getByLabelText('Valor'), { target: { value: '2500' } })
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'Enviada' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar proposta' }))
    expect(screen.getByRole('button', { name: /Dashboard financeiro/ })).toBeInTheDocument()
    expect(screen.getByText(/2\.500,00/)).toBeInTheDocument()
    expect(screen.getAllByText('Acme').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Enviada').length).toBeGreaterThan(1)

    fireEvent.click(screen.getByRole('button', { name: /Dashboard financeiro/ }))
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'Aceita' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar proposta' }))
    expect(screen.getAllByText('Aceita').length).toBeGreaterThan(1)
  })

  it('cria proposta em USD com dados opcionais apenas quando relevantes', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Nova proposta' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).queryByLabelText('Connects')).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Título *'), { target: { value: 'API internacional' } })
    fireEvent.change(within(dialog).getByLabelText('Valor'), { target: { value: '800' } })
    fireEvent.change(within(dialog).getByLabelText('Moeda'), { target: { value: 'USD' } })
    fireEvent.change(within(dialog).getByLabelText('Origem'), { target: { value: 'Upwork' } })
    fireEvent.change(within(dialog).getByLabelText('Connects'), { target: { value: '10' } })
    fireEvent.change(within(dialog).getByLabelText('Link da oportunidade'), { target: { value: 'https://example.com/job' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Salvar proposta' }))
    expect(screen.getByText(/800,00/)).toBeInTheDocument()
    expect(screen.getAllByText('Upwork').length).toBeGreaterThan(0)
  })

  it('orienta o cadastro quando não há clientes', () => {
    localStorage.clear()
    const navigate = vi.fn()
    render(<AppProvider><ProposalsPage navigate={navigate} /></AppProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Nova proposta' }))
    expect(navigate).toHaveBeenCalledWith('clients')
    expect(screen.getByText('Cadastre um cliente antes da primeira proposta')).toBeInTheDocument()
  })

  it('oferece ação explícita para criar projeto somente após aceitar a proposta', () => {
    const onCreateProject = vi.fn()
    render(<AppProvider><ProposalsPage navigate={vi.fn()} onCreateProject={onCreateProject} /></AppProvider>)
    fireEvent.click(screen.getByRole('button', { name: 'Nova proposta' }))
    fireEvent.change(screen.getByLabelText('Título *'), { target: { value: 'Portal do cliente' } })
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'Aceita' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar proposta' }))
    fireEvent.click(screen.getByRole('button', { name: 'Criar projeto desta proposta' }))
    expect(onCreateProject).toHaveBeenCalledOnce()
  })

  it('não permite trocar o cliente de uma proposta já ligada a projeto', () => {
    const data = createDefaultData('2026-01-01')
    data.clients = [
      { id: 'client-1', name: 'Acme', companyName: '', contactName: '', phone: '', email: '', source: 'Outro', referredBy: '', status: 'Cliente ativo', notes: '', createdAt: '2026-01-01', updatedAt: '2026-01-01' },
      { id: 'client-2', name: 'Beta', companyName: '', contactName: '', phone: '', email: '', source: 'Outro', referredBy: '', status: 'Lead', notes: '', createdAt: '2026-01-01', updatedAt: '2026-01-01' },
    ]
    data.proposals = [{ id: 'proposal-1', clientId: 'client-1', serviceId: null, title: 'API', description: '', amount: 1000, currency: 'BRL', source: 'Contato direto', status: 'Aceita', createdAt: '2026-01-01', sentAt: '2026-01-01', validUntil: null, followUpDate: null, estimatedHours: 10, notes: '' }]
    data.projects = [{ id: 'project-1', clientId: 'client-1', proposalId: 'proposal-1', name: 'API', description: '', status: 'Planejamento', startDate: null, deadline: null, completedAt: null, amount: 1000, currency: 'BRL', estimatedHours: 10, workedHours: 0, repositoryUrl: '', productionUrl: '', paymentStatus: 'Pendente', amountReceived: 0, notes: '', createdAt: '2026-01-01', updatedAt: '2026-01-01' }]
    saveData(data)
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /API Acme/ }))
    fireEvent.change(screen.getByLabelText('Cliente *'), { target: { value: 'client-2' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar proposta' }))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByLabelText('Cliente *')).toHaveValue('client-2')
  })
})
