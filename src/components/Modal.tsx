import { X } from 'lucide-react'
import { useEffect, useRef, type ReactNode, type RefObject } from 'react'

export function Modal({ title, onClose, children, size = 'normal', initialFocusRef }: { title: string; onClose: () => void; children: ReactNode; size?: 'normal' | 'large'; initialFocusRef?: RefObject<HTMLElement> }) {
  const modalRef = useRef<HTMLElement>(null)
  const returnFocusRef = useRef<HTMLElement | null>(document.activeElement instanceof HTMLElement ? document.activeElement : null)
  useEffect(() => {
    const returnFocus = returnFocusRef.current
    const previousOverflow = document.body.style.overflow
    const focusableSelector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
    const focusable = () => Array.from(modalRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? []).filter((element) => !element.hidden)
    const autoFocusElement = modalRef.current?.querySelector<HTMLElement>('[autofocus]')
    const firstFormField = modalRef.current?.querySelector<HTMLElement>('.modal-body input:not([disabled]), .modal-body select:not([disabled]), .modal-body textarea:not([disabled])')
    const initialFocus = initialFocusRef?.current ?? autoFocusElement ?? firstFormField ?? focusable()[0]
    initialFocus?.focus()
    document.body.style.overflow = 'hidden'

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab') return
      const elements = focusable()
      if (!elements.length) return
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', handleKey)
    return () => {
      window.removeEventListener('keydown', handleKey)
      document.body.style.overflow = previousOverflow
      returnFocus?.focus()
    }
  }, [initialFocusRef, onClose])
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }} role="presentation">
    <section ref={modalRef} className={`modal ${size === 'large' ? 'modal-large' : ''}`} role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <header className="modal-header"><h2 id="modal-title">{title}</h2><button className="icon-button" onClick={onClose} aria-label="Fechar"><X size={20} /></button></header>
      <div className="modal-body">{children}</div>
    </section>
  </div>
}

export function ConfirmModal({ title, message, onConfirm, onClose, danger = false }: { title: string; message: string; onConfirm: () => void; onClose: () => void; danger?: boolean }) {
  return <Modal title={title} onClose={onClose}>
    <p className="confirm-copy">{message}</p>
    <div className="form-actions"><button className="button secondary" onClick={onClose}>Cancelar</button><button className={`button ${danger ? 'danger' : 'primary'}`} onClick={() => { onConfirm(); onClose() }}>Confirmar</button></div>
  </Modal>
}

export function UnsavedChangesModal({ onStay, onDiscard }: { onStay: () => void; onDiscard: () => void }) {
  const stayButtonRef = useRef<HTMLButtonElement>(null)
  return <Modal title="Alterações não salvas" onClose={onStay} initialFocusRef={stayButtonRef}>
    <p className="confirm-copy">Existem alterações não salvas no planejamento. Deseja sair e descartar essas alterações?</p>
    <div className="form-actions"><button ref={stayButtonRef} className="button secondary" onClick={onStay}>Continuar editando</button><button className="button danger" onClick={onDiscard}>Sair sem salvar</button></div>
  </Modal>
}
