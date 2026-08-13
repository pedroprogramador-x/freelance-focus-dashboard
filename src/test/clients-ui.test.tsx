import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { AppProvider } from '../context/AppContext'
import { createDefaultData } from '../data/defaults'
import { filterClients } from '../data/domain'
import { ClientsPage } from '../pages/ClientsPage'
import { saveData } from '../services/storage'
import type { Client } from '../types'

const client: Client = { id: 'client-1', name: 'Acme', companyName: 'Acme Ltda', contactName: 'Ana', phone: '85999999999', email: 'ana@acme.test', source: 'Indicação', referredBy: 'Bruno', status: 'Cliente ativo', notes: 'Prefere contato por WhatsApp.', createdAt: '2026-01-01', updatedAt: '2026-01-01' }
const renderPage = () => render(<AppProvider><ClientsPage /></AppProvider>)

afterEach(cleanup)

describe('Clientes', () => {
  beforeEach(() => localStorage.clear())

  it('cadastra e edita um cliente', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Novo cliente' }))
    fireEvent.change(screen.getByLabelText('Nome *'), { target: { value: 'Maria' } })
    fireEvent.change(screen.getByLabelText('Empresa'), { target: { value: 'Studio Maria' } })
    fireEvent.change(screen.getByLabelText('Telefone'), { target: { value: '85988887777' } })
    fireEvent.change(screen.getByLabelText('Origem'), { target: { value: 'Indicação' } })
    fireEvent.change(screen.getByLabelText('Indicado por'), { target: { value: 'João' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar cliente' }))
    expect(screen.getByRole('button', { name: /^Maria / })).toBeInTheDocument()
    expect(screen.getByText('Studio Maria')).toBeInTheDocument()
    expect(screen.getByText('João')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Editar' }))
    fireEvent.change(screen.getByLabelText('Empresa'), { target: { value: 'Studio Maria V2' } })
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'Cliente ativo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Salvar cliente' }))
    expect(screen.getByText('Studio Maria V2')).toBeInTheDocument()
    expect(screen.getAllByText('Cliente ativo').length).toBeGreaterThan(1)
  })

  it('exclui cliente sem relacionamentos', () => {
    const data = createDefaultData('2026-01-01'); data.clients = [client]; saveData(data)
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Excluir Acme' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Confirmar' }))
    expect(screen.queryByRole('button', { name: /Acme/ })).not.toBeInTheDocument()
  })

  it('filtra por busca e status', () => {
    const lead = { ...client, id: 'client-2', name: 'Beta', companyName: 'Beta Co', status: 'Lead' as const }
    expect(filterClients([client, lead], 'beta co', '')).toEqual([lead])
    expect(filterClients([client, lead], '', 'Cliente ativo')).toEqual([client])
  })

  it('mostra relacionamentos, totais e bloqueia exclusão que criaria órfãos', () => {
    const data = createDefaultData('2026-01-01')
    data.clients = [client]
    data.proposals = [{ id: 'proposal-1', clientId: client.id, serviceId: null, title: 'Automação', description: '', amount: 2000, currency: 'BRL', source: 'Indicação', status: 'Aceita', createdAt: '2026-01-02', sentAt: '2026-01-02', validUntil: null, followUpDate: null, estimatedHours: 20, notes: '' }]
    data.projects = [{ id: 'project-1', clientId: client.id, proposalId: 'proposal-1', name: 'Automação', description: '', status: 'Em desenvolvimento', startDate: '2026-01-03', deadline: null, completedAt: null, amount: 2000, currency: 'BRL', estimatedHours: 20, workedHours: 4, repositoryUrl: '', productionUrl: '', paymentStatus: 'Parcial', amountReceived: 500, notes: '', createdAt: '2026-01-03', updatedAt: '2026-01-03' }]
    saveData(data)
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /^Acme / }))
    const dialog = screen.getByRole('dialog', { name: 'Acme' })
    expect(within(dialog).getByText('Propostas relacionadas (1)')).toBeInTheDocument()
    expect(within(dialog).getByText('Projetos relacionados (1)')).toBeInTheDocument()
    expect(within(dialog).getAllByText(/2\.000,00/).length).toBeGreaterThan(0)
    expect(within(dialog).getByText('Prefere contato por WhatsApp.')).toBeInTheDocument()
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Fechar' })[1])
    fireEvent.click(screen.getByRole('button', { name: 'Excluir Acme' }))
    expect(screen.queryByRole('dialog', { name: 'Excluir cliente?' })).not.toBeInTheDocument()
  })
})
