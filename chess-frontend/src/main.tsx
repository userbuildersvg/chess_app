import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/obsidian.css'
// The layout every mode shares. After the token layer and before any
// component stylesheet, so a mode overrides the shell and never the reverse.
import './styles/shell.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
