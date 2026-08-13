import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { AppProvider, useApp } from '../context/AppContext'
import { createDefaultData } from '../data/defaults'
import { RoadmapPage } from '../pages/RoadmapPage'
import { saveData, STORAGE_KEY } from '../services/storage'

function ToastProbe() {
  const { toast } = useApp()
  return toast ? <div role="status">{toast.message}</div> : null
}

const renderRoadmap = () => render(<AppProvider><RoadmapPage /><ToastProbe /></AppProvider>)

afterEach(cleanup)

describe('conclusão de tarefa', () => {
  beforeEach(() => localStorage.clear())
  it('permite concluir clicando e persiste a alteração', async () => {
    renderRoadmap()
    const button = screen.getAllByLabelText('Concluir tarefa', { selector: 'button' })[0]
    fireEvent.click(button)
    expect(button).toHaveAccessibleName('Desfazer conclusão')
    await waitFor(() => expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).tasks[0].status).toBe('Concluído'), { timeout: 1000 })
  })
})

describe('alteração da data inicial no roadmap', () => {
  beforeEach(() => localStorage.clear())

  it('exibe, altera, recalcula e persiste sem apagar o progresso', async () => {
    const initial = createDefaultData('2026-08-04')
    initial.tasks[0] = {
      ...initial.tasks[0],
      status: 'Concluído',
      notes: 'Progresso preservado',
      completedAt: '2026-08-04T15:00:00.000Z',
      priority: 'Alta',
    }
    initial.tasks[1] = { ...initial.tasks[1], status: 'Adiado', rescheduledDate: '2026-09-01' }
    initial.clients = [{ id: 'client-1', name: 'Cliente', companyName: '', contactName: '', phone: '', email: '', source: 'Upwork', referredBy: '', status: 'Cliente ativo', notes: '', createdAt: '2026-08-01', updatedAt: '2026-08-01' }]
    initial.proposals = [{ id: 'proposal-1', clientId: 'client-1', serviceId: null, title: 'API', description: 'Integração', amount: 300, currency: 'USD', source: 'Upwork', status: 'Enviada', createdAt: '2026-08-01', sentAt: '2026-08-01', validUntil: '2026-08-20', followUpDate: '2026-08-08', estimatedHours: 10, notes: 'Manter', platformData: { connects: 8 } }]
    initial.projects = [{ id: 'project-1', clientId: 'client-1', proposalId: null, name: 'Automação', description: 'Python', status: 'Em desenvolvimento', startDate: '2026-08-02', deadline: '2026-08-30', completedAt: null, amount: 500, currency: 'USD', estimatedHours: 10, workedHours: 2, repositoryUrl: '', productionUrl: '', paymentStatus: 'Pendente', amountReceived: 0, notes: 'Manter', createdAt: '2026-08-02', updatedAt: '2026-08-02' }]
    saveData(initial)

    renderRoadmap()

    expect(screen.getByText(/Início atual:/)).toHaveTextContent('4 de agosto de 2026')
    fireEvent.click(screen.getByRole('button', { name: 'Alterar data de início' }))

    const dialog = screen.getByRole('dialog', { name: 'Alterar data de início' })
    const dateInput = screen.getByLabelText('Nova data de início')
    const applyButton = screen.getByRole('button', { name: 'Aplicar nova data' })
    expect(dialog).toBeInTheDocument()
    expect(dateInput).toHaveFocus()
    expect(applyButton).toBeDisabled()

    fireEvent.input(dateInput, { target: { value: '' } })
    expect(applyButton).toBeDisabled()
    fireEvent.input(dateInput, { target: { value: '2026-08-10' } })
    expect(applyButton).toBeEnabled()
    fireEvent.click(applyButton)

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText(/Início atual:/)).toHaveTextContent('10 de agosto de 2026')
    expect(screen.getByRole('status')).toHaveTextContent('Data inicial atualizada. As datas foram recalculadas sem apagar seu progresso.')

    await waitFor(() => {
      const persisted = JSON.parse(localStorage.getItem(STORAGE_KEY)!)
      expect(persisted.settings.roadmapStartDate).toBe('2026-08-10')
      expect(persisted.tasks[0]).toMatchObject({
        id: initial.tasks[0].id,
        title: initial.tasks[0].title,
        plannedDate: '2026-08-10',
        status: 'Concluído',
        notes: 'Progresso preservado',
        completedAt: '2026-08-04T15:00:00.000Z',
        priority: 'Alta',
      })
      expect(persisted.tasks[1]).toMatchObject({ status: 'Adiado', rescheduledDate: '2026-09-01', plannedDate: '2026-08-11' })
      expect(persisted.tasks[89].plannedDate).toBe('2026-11-07')
      expect(persisted.proposals).toEqual(initial.proposals)
      expect(persisted.clients).toEqual(initial.clients)
      expect(persisted.projects).toEqual(initial.projects)
      expect(persisted.services).toEqual(initial.services)
    }, { timeout: 1000 })
  })

  it('cancela sem alterar a configuração', () => {
    saveData(createDefaultData('2026-08-04'))
    renderRoadmap()
    fireEvent.click(screen.getByRole('button', { name: 'Alterar data de início' }))
    fireEvent.input(screen.getByLabelText('Nova data de início'), { target: { value: '2026-08-20' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText(/Início atual:/)).toHaveTextContent('4 de agosto de 2026')
  })
})
