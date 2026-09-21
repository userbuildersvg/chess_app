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
import { Admin } from './pages/Admin'
import { About } from './pages/About';
import { ImprovementProfile } from './pages/ImprovementProfile';
import { ForgotPassword, ResetPassword } from './pages/PasswordReset'
import { BetaGate } from './components/BetaGate'
import { Contact, Privacy, RequestAccess, Terms } from './pages/BetaPages'
import { HomeOrApp } from './pages/Home'
import { Analytics } from '@vercel/analytics/react'

// Replaced by Vite at build time (vite.config.ts). `typeof` first, as
// buildInfo.ts does, so a build that did not define it is "not Vercel"
// rather than a ReferenceError.
declare const __ON_VERCEL__: boolean;
const ON_VERCEL = typeof __ON_VERCEL__ === 'boolean' && __ON_VERCEL__;

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      {/* The private-surface beta gate. Inside the router because it has to know which
          route is being asked for - sign-in and the information pages stay
          reachable while locked out - and outside <Routes> because it decides
          whether any of them render at all.

          It draws the landing page; it does not authorize anything. The server
          refuses unauthorized private API requests in beta_gate.py before any
          route runs. `/` and the named guest-demo APIs are explicit Shipaton
          exceptions. See BetaGate.tsx. */}
      <BetaGate>
      <Routes>
        {/* The app itself. Everything below is a full surface rather than a
            modal, because signing in is the moment a person's history stops
            belonging to one browser - and because a URL can be linked to,
            bookmarked, and returned to after a Google round trip. */}
        {/* The board - or, for a signed-out stranger, the public homepage.
            See Home.tsx for who gets which. */}
        <Route path="/" element={<HomeOrApp />} />
        <Route path="/signin" element={<SignIn />} />
        <Route path="/signup" element={<SignUp />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/admin" element={<Admin />} />
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
      {/* Vercel Web Analytics: page views and the vitals, first-party. The
          script is /_vercel/insights/script.js and the beacon is
          /_vercel/insights/view - both the page's own origin, which is why
          the shipping CSP (vercel.json: script-src 'self', connect-src
          'self') needs no change for it. Mounted only in a bundle Vercel
          built; see __ON_VERCEL__ in vite.config.ts. */}
      {ON_VERCEL && <Analytics />}
    </BrowserRouter>
  </StrictMode>,
)
