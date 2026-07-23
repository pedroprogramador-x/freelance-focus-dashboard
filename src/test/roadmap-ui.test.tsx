import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { AppProvider } from '../context/AppContext'
import { RoadmapPage } from '../pages/RoadmapPage'
import { STORAGE_KEY } from '../services/storage'

describe('conclusão de tarefa', () => {
  beforeEach(() => localStorage.clear())
  it('permite concluir clicando e persiste a alteração', async () => {
    render(<AppProvider><RoadmapPage /></AppProvider>)
    const button = screen.getAllByLabelText('Concluir tarefa', { selector: 'button' })[0]
    fireEvent.click(button)
    expect(button).toHaveAccessibleName('Desfazer conclusão')
    await waitFor(() => expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!).tasks[0].status).toBe('Concluído'), { timeout: 1000 })
  })
})
