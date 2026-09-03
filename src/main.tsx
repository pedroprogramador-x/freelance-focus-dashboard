import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { AppProvider } from './context/AppContext'
import { WorkspaceProvider } from './context/WorkspaceProvider'
import './styles/global.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <AppProvider>
      <WorkspaceProvider>
        <App />
      </WorkspaceProvider>
    </AppProvider>
  </StrictMode>,
)
