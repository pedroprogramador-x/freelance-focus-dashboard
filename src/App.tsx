import { useCallback, useEffect, useState } from 'react'
import { Layout, type PageId } from './components/Layout'
import { UnsavedChangesModal } from './components/Modal'
import { useApp } from './context/AppContext'
import { ClientsPage } from './pages/ClientsPage'
import { DashboardPage } from './pages/DashboardPage'
import { DevWorkspaces } from './pages/DevWorkspaces'
import { ProjectsPage } from './pages/ProjectsPage'
import { ProposalsPage } from './pages/ProposalsPage'
import { RoadmapPage } from './pages/RoadmapPage'
import { ServicesPage } from './pages/ServicesPage'
import { SettingsPage } from './pages/SettingsPage'

const validPages: PageId[] = ['dashboard', 'roadmap', 'clients', 'proposals', 'projects', 'services', 'workspaces', 'settings']
const getHashPage = (): PageId => { const value = window.location.hash.slice(1) as PageId; return validPages.includes(value) ? value : 'dashboard' }

export function App() {
  const { ready } = useApp()
  const [page, setPage] = useState<PageId>(getHashPage)
  const [proposalForProjectId, setProposalForProjectId] = useState<string | null>(null)
  const [planningDirty, setPlanningDirty] = useState(false)
  const [pendingPage, setPendingPage] = useState<PageId | null>(null)
  const commitNavigation = useCallback((next: PageId) => { setPage(next); window.history.replaceState(null, '', `#${next}`); window.scrollTo({ top: 0, behavior: 'smooth' }) }, [])
  const navigate = useCallback((next: PageId) => {
    if (next === page) return
    if (planningDirty) { setPendingPage(next); return }
    commitNavigation(next)
  }, [commitNavigation, page, planningDirty])
  useEffect(() => {
    const listener = () => {
      const next = getHashPage()
      if (next === page) return
      if (planningDirty) { window.history.replaceState(null, '', `#${page}`); setPendingPage(next); return }
      setPage(next)
    }
    window.addEventListener('hashchange', listener)
    return () => window.removeEventListener('hashchange', listener)
  }, [page, planningDirty])
  const createProjectFromProposal = useCallback((proposalId: string) => { setProposalForProjectId(proposalId); navigate('projects') }, [navigate])
  const clearProposalForProject = useCallback(() => setProposalForProjectId(null), [])
  if (!ready) return <div className="loading-screen"><div className="loading-brand"><span>F</span><strong>Freelance Focus</strong></div><div className="skeleton wide" /><div className="skeleton-grid"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div><p>Preparando seu plano...</p></div>
  const content = page === 'dashboard' ? <DashboardPage navigate={navigate} /> : page === 'roadmap' ? <RoadmapPage /> : page === 'clients' ? <ClientsPage /> : page === 'proposals' ? <ProposalsPage navigate={navigate} onCreateProject={createProjectFromProposal} /> : page === 'projects' ? <ProjectsPage navigate={navigate} proposalForProjectId={proposalForProjectId} onProposalHandled={clearProposalForProject} onPlanningDirtyChange={setPlanningDirty} /> : page === 'services' ? <ServicesPage /> : page === 'workspaces' ? <DevWorkspaces /> : <SettingsPage />
  return <><Layout page={page} setPage={navigate}>{content}</Layout>{pendingPage && <UnsavedChangesModal onStay={() => setPendingPage(null)} onDiscard={() => { const next = pendingPage; setPendingPage(null); setPlanningDirty(false); commitNavigation(next) }} />}</>
}
