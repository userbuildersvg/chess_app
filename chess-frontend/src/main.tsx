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
import { BetaGate } from './components/BetaGate'
import { Contact, Privacy, RequestAccess, Terms } from './pages/BetaPages'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      {/* The closed beta gate. Inside the router because it has to know which
          route is being asked for - sign-in and the information pages stay
          reachable while locked out - and outside <Routes> because it decides
          whether any of them render at all.

          It draws the landing page; it does not authorize anything. The server
          refuses unauthorized API requests in beta_gate.py before any route
          runs, so this is what the browser shows while that is true, not what
          makes it true. See BetaGate.tsx. */}
      <BetaGate>
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
        {/* Reachable without an invitation, on purpose: a privacy policy
            behind the door it describes is not a privacy policy, and a
            footer link that goes nowhere on the one screen every uninvited
            visitor sees is the most visible thing the product could break.
            The same list lives in BetaGate.OPEN_ROUTES. */}
        <Route path="/request-access" element={<RequestAccess />} />
        <Route path="/contact" element={<Contact />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/terms" element={<Terms />} />
      </Routes>
      </BetaGate>
    </BrowserRouter>
  </StrictMode>,
)
