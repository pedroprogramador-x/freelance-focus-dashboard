import { BarChart3, BriefcaseBusiness, CalendarDays, ChevronLeft, ChevronRight, CircleDollarSign, Menu, Moon, Settings, Sun, Wrench, X, type LucideIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useApp } from '../context/AppContext'

export type PageId = 'dashboard' | 'roadmap' | 'proposals' | 'contracts' | 'services' | 'settings'
const navigation: { id: PageId; label: string; icon: LucideIcon }[] = [
  { id: 'dashboard', label: 'Painel', icon: BarChart3 }, { id: 'roadmap', label: 'Plano de 90 dias', icon: CalendarDays },
  { id: 'proposals', label: 'Propostas', icon: BriefcaseBusiness }, { id: 'contracts', label: 'Clientes e ganhos', icon: CircleDollarSign },
  { id: 'services', label: 'Serviços', icon: Wrench }, { id: 'settings', label: 'Configurações', icon: Settings },
]

export function Layout({ page, setPage, children }: { page: PageId; setPage: (page: PageId) => void; children: React.ReactNode }) {
  const { data, updateSettings, toast } = useApp()
  const [menuOpen, setMenuOpen] = useState(false)
  const [collapsed, setCollapsed] = useState(false)
  const sidebarRef = useRef<HTMLElement>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const menuWasOpen = useRef(false)
  const active = navigation.find((item) => item.id === page)!

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setPage('roadmap') }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [setPage])

  useEffect(() => {
    if (!menuOpen) {
      if (menuWasOpen.current) menuButtonRef.current?.focus()
      menuWasOpen.current = false
      return
    }
    menuWasOpen.current = true
    const selector = 'button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
    const focusable = () => Array.from(sidebarRef.current?.querySelectorAll<HTMLElement>(selector) ?? []).filter((element) => element.offsetParent !== null)
    const activeItem = sidebarRef.current?.querySelector<HTMLElement>('[aria-current="page"]')
    activeItem?.focus()
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setMenuOpen(false); return }
      if (event.key !== 'Tab') return
      const elements = focusable()
      if (!elements.length) return
      const first = elements[0]
      const last = elements[elements.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [menuOpen])

  const toggleTheme = () => updateSettings({ ...data.settings, theme: document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark' })
  return <div className={`app-shell ${collapsed ? 'sidebar-collapsed' : ''}`}>
    <aside ref={sidebarRef} id="sidebar-navigation" className={`sidebar ${menuOpen ? 'open' : ''}`}>
      <div className="brand"><div className="brand-mark">F</div><div className="brand-copy"><strong>Freelance Focus</strong><span>90 dias de progresso</span></div><button className="mobile-close icon-button" onClick={() => setMenuOpen(false)} aria-label="Fechar menu"><X size={20} /></button></div>
      <nav aria-label="Navegação principal">{navigation.map(({ id, label, icon: Icon }) => <button key={id} className={`nav-item ${page === id ? 'active' : ''}`} onClick={() => { setPage(id); setMenuOpen(false) }} aria-current={page === id ? 'page' : undefined}><Icon size={20} /><span>{label}</span></button>)}</nav>
      <div className="sidebar-tip"><span>Atalho rápido</span><strong>Ctrl K</strong><small>abre o plano</small></div>
      <button className="collapse-button" onClick={() => setCollapsed((value) => !value)} aria-label={collapsed ? 'Expandir menu' : 'Recolher menu'}>{collapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}</button>
    </aside>
    {menuOpen && <button className="menu-overlay" onClick={() => setMenuOpen(false)} aria-label="Fechar menu" />}
    <div className="main-column">
      <header className="topbar"><button ref={menuButtonRef} className="menu-button icon-button" onClick={() => setMenuOpen(true)} aria-label="Abrir menu" aria-expanded={menuOpen} aria-controls="sidebar-navigation"><Menu size={22} /></button><div><span className="eyebrow">Freelance Focus</span><h1>{active.label}</h1></div><button className="theme-toggle" onClick={toggleTheme} aria-label="Alternar tema"><Sun className="sun-icon" size={18} /><Moon className="moon-icon" size={18} /><span>Tema</span></button></header>
      <main className="page-content">{children}</main>
    </div>
    {toast && <div className={`toast ${toast.tone}`} role="status">{toast.message}</div>}
  </div>
}
