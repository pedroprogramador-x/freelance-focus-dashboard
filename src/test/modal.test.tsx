import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { Modal } from '../components/Modal'

function ModalHost() {
  const [open, setOpen] = useState(false)
  return <><button onClick={() => setOpen(true)}>Abrir modal</button>{open && <Modal title="Formulário" onClose={() => setOpen(false)}><label>Nome<input /></label><button>Salvar</button></Modal>}</>
}

describe('acessibilidade do modal', () => {
  it('move o foco, prende a navegação e devolve o foco ao fechar', () => {
    render(<ModalHost />)
    const trigger = screen.getByRole('button', { name: 'Abrir modal' })
    trigger.focus()
    fireEvent.click(trigger)
    const close = screen.getByRole('button', { name: 'Fechar' })
    expect(screen.getByRole('textbox', { name: 'Nome' })).toHaveFocus()
    close.focus()
    fireEvent.keyDown(window, { key: 'Tab', shiftKey: true })
    expect(screen.getByRole('button', { name: 'Salvar' })).toHaveFocus()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })
})
