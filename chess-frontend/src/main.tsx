import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/obsidian.css'
// The layout every mode shares. After the token layer and before any
// component stylesheet, so a mode overrides the shell and never the reverse.
import './styles/shell.css'
import App from './App.tsx'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { SignIn, SignUp } from './pages/AuthPages'
import { Settings } from './pages/Settings'
import { About } from './pages/About';
import { ImprovementProfile } from './pages/ImprovementProfile';
import { ForgotPassword, ResetPassword } from './pages/PasswordReset'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        {/* The app itself. Everything below is a full surface rather than a
            modal, because signing in is the moment a person's history stops
            belonging to one browser - and because a URL can be linked to,
            bookmarked, and returned to after a Google round trip. */}
        <Route path="/" element={<App />} />
        <Route path="/signin" element={<SignIn />} />
        <Route path="/signup" element={<SignUp />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/about" element={<About />} />
        {/* The multi-game workflow. A page you go into and come out of,
            NOT a fourth mode - the three-mode shell is deliberate, and
            this holds nothing a navigation would destroy. */}
        <Route path="/profile" element={<ImprovementProfile />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        {/* The link in the email lands here, carrying ?token=... */}
        <Route path="/reset-password" element={<ResetPassword />} />
        {/* Anything else is the board. A 404 page would be a surface with
            nothing useful on it. */}
        <Route path="*" element={<App />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
