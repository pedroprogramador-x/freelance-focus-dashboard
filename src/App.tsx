import { useCallback, useEffect, useState } from 'react'
import { Layout, type PageId } from './components/Layout'
import { useApp } from './context/AppContext'
import { ClientsPage } from './pages/ClientsPage'
import { DashboardPage } from './pages/DashboardPage'
import { ProjectsPage } from './pages/ProjectsPage'
import { ProposalsPage } from './pages/ProposalsPage'
import { RoadmapPage } from './pages/RoadmapPage'
import { ServicesPage } from './pages/ServicesPage'
import { SettingsPage } from './pages/SettingsPage'

const validPages: PageId[] = ['dashboard', 'roadmap', 'clients', 'proposals', 'projects', 'services', 'settings']
const getHashPage = (): PageId => { const value = window.location.hash.slice(1) as PageId; return validPages.includes(value) ? value : 'dashboard' }

export function App() {
  const { ready } = useApp()
  const [page, setPage] = useState<PageId>(getHashPage)
  const [proposalForProjectId, setProposalForProjectId] = useState<string | null>(null)
  useEffect(() => { const listener = () => setPage(getHashPage()); window.addEventListener('hashchange', listener); return () => window.removeEventListener('hashchange', listener) }, [])
  const navigate = useCallback((next: PageId) => { setPage(next); window.history.replaceState(null, '', `#${next}`); window.scrollTo({ top: 0, behavior: 'smooth' }) }, [])
  const createProjectFromProposal = useCallback((proposalId: string) => { setProposalForProjectId(proposalId); navigate('projects') }, [navigate])
  const clearProposalForProject = useCallback(() => setProposalForProjectId(null), [])
  if (!ready) return <div className="loading-screen"><div className="loading-brand"><span>F</span><strong>Freelance Focus</strong></div><div className="skeleton wide" /><div className="skeleton-grid"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div><p>Preparando seu plano...</p></div>
  const content = page === 'dashboard' ? <DashboardPage navigate={navigate} /> : page === 'roadmap' ? <RoadmapPage /> : page === 'clients' ? <ClientsPage /> : page === 'proposals' ? <ProposalsPage navigate={navigate} onCreateProject={createProjectFromProposal} /> : page === 'projects' ? <ProjectsPage navigate={navigate} proposalForProjectId={proposalForProjectId} onProposalHandled={clearProposalForProject} /> : page === 'services' ? <ServicesPage /> : <SettingsPage />
  return <Layout page={page} setPage={navigate}>{content}</Layout>
}
