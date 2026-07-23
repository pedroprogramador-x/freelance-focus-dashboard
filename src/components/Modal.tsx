import { X } from 'lucide-react'
import { useEffect, useRef, type ReactNode } from 'react'

export function Modal({ title, onClose, children, size = 'normal' }: { title: string; onClose: () => void; children: ReactNode; size?: 'normal' | 'large' }) {
  const modalRef = useRef<HTMLElement>(null)
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    const focusableSelector = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
    const focusable = () => Array.from(modalRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? []).filter((element) => !element.hidden)
    focusable()[0]?.focus()
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
      previousFocus?.focus()
    }
  }, [onClose])
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
