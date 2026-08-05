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
    initial.proposals = [{ id: 'proposal-1', date: '2026-08-01', platform: 'Upwork', projectName: 'API', clientName: 'Cliente', serviceType: 'Integração', budgetUsd: 300, connects: 8, url: '', status: 'Enviada', deadline: '2026-08-20', estimatedHours: 10, estimatedHourlyRate: 30, nextStep: 'Aguardar', notes: 'Manter', followUpDate: '2026-08-08' }]
    initial.contracts = [{ id: 'contract-1', project: 'Automação', client: 'Cliente', platform: 'Upwork', service: 'Python', startDate: '2026-08-02', deadline: '2026-08-30', grossUsd: 500, platformFeePercent: 10, exchangeRate: 5.5, hoursWorked: 2, status: 'Em andamento', rating: null, notes: 'Manter' }]
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

    fireEvent.change(dateInput, { target: { value: '' } })
    expect(applyButton).toBeDisabled()
    fireEvent.change(dateInput, { target: { value: '2026-08-10' } })
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
      expect(persisted.contracts).toEqual(initial.contracts)
      expect(persisted.services).toEqual(initial.services)
    }, { timeout: 1000 })
  })

  it('cancela sem alterar a configuração', () => {
    saveData(createDefaultData('2026-08-04'))
    renderRoadmap()
    fireEvent.click(screen.getByRole('button', { name: 'Alterar data de início' }))
    fireEvent.change(screen.getByLabelText('Nova data de início'), { target: { value: '2026-08-20' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancelar' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByText(/Início atual:/)).toHaveTextContent('4 de agosto de 2026')
  })
})
