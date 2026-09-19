import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'


// The security headers, kept next to the two servers that have to send them.
//
// The SHIPPING set. Byte-identical to `vercel.json` and to the `add_header`
// lines in `nginx.conf` - CLAUDE.md §28 explains every source in it. Vite's
// `preview` server serves the real production build, so pointing a browser at
// `npm run preview` is how this policy gets tested before it is deployed;
// `vercel.json` remains what actually sends it in production.
const SHIPPING_CSP = [
  "default-src 'self'",
  "script-src 'self' https://js.stripe.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com",
  "img-src 'self' data: https://icons.pawwalls.com",
  "connect-src 'self' https://api.revenuecat.com https://e.revenue.cat",
  "frame-ancestors 'none'",
  "frame-src https://js.stripe.com",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'self'",
  "worker-src 'self'",
  "manifest-src 'self'",
].join('; ')

// The DEV set, and it is deliberately weaker in exactly two places.
//
// This is the one honest thing to do here. The dev server is not the shipping
// build and cannot be made to behave like it: `@vitejs/plugin-react` injects
// the React Refresh preamble as an INLINE module script, and HMR needs a
// websocket back to the dev server. So dev needs `'unsafe-inline'` on
// script-src and `ws:` on connect-src, and pretending otherwise would mean
// either a dev server that does not run or - far worse - quietly adding those
// two to the shipping policy so that one CSP could serve both.
//
// Everything else is identical, which is the part worth having on :3001:
// framing, sniffing, referrer and permissions behave in dev exactly as they
// will in production, so a regression in any of them is visible where the work
// happens rather than after a deploy.
const DEV_CSP = SHIPPING_CSP
  .replace("script-src 'self'", "script-src 'self' 'unsafe-inline'")
  .replace("connect-src 'self'", "connect-src 'self' ws: wss:")

// Identical in both, and in `vercel.json` and `nginx.conf`. No HSTS: neither
// of these servers is HTTPS, and HSTS from localhost poisons plain-HTTP
// localhost for every project on the machine (CLAUDE.md §28).
const COMMON_HEADERS = {
  'X-Frame-Options': 'DENY',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Resource-Policy': 'same-origin',
  'Permissions-Policy':
    'accelerometer=(), autoplay=(), camera=(), display-capture=(), ' +
    'encrypted-media=(), geolocation=(), gyroscope=(), magnetometer=(), ' +
    'microphone=(), midi=(), payment=(self "https://js.stripe.com"), picture-in-picture=(), ' +
    'publickey-credentials-get=(), screen-wake-lock=(), usb=(), ' +
    'xr-spatial-tracking=()',
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
    headers: { ...COMMON_HEADERS, 'Content-Security-Policy': DEV_CSP },
    // Set VITE_POLL=1 when the dev server runs inside WSL but the source
    // lives on a Windows drive (/mnt/c/...). inotify does not fire for
    // writes made by Windows-side processes, so the default watcher never
    // sees the edit: HMR goes quiet and Vite keeps serving the previous
    // version of the file, which looks exactly like "my CSS change did
    // nothing". Polling is slower, so it stays opt-in.
    watch: process.env.VITE_POLL ? { usePolling: true, interval: 300 } : undefined,
    proxy: {
      '/api': {
        // Overridable so the dev server can point at a backend on another
        // port - 8080 is a popular default and is often already taken
        // (Docker Desktop grabs it on Windows, for one).
        target: process.env.VITE_PROXY_TARGET || 'http://localhost:8080',
        changeOrigin: true,
        secure: false
      }
    }
  },
  // `npm run preview` serves the real production build. It carries the
  // SHIPPING headers, so this is where the deployed CSP can be exercised in a
  // browser before it is deployed - the dev server above cannot do that job,
  // and the Docker build (:3000) can but has to be rebuilt first.
  preview: {
    port: 4173,
    headers: { ...COMMON_HEADERS, 'Content-Security-Policy': SHIPPING_CSP },
    proxy: {
      '/api': {
        target: process.env.VITE_PROXY_TARGET || 'http://localhost:8081',
        changeOrigin: true,
        secure: false
      }
    }
  },
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify(process.env.VITE_API_URL || 'http://localhost:8080'),
    // The commit this bundle was built from, baked in because by the time the
    // page runs there is nothing left to ask. Vercel sets
    // VERCEL_GIT_COMMIT_SHA itself; BUILD_SHA is the manual override for any
    // other builder. Short, because the footer shows it and twelve characters
    // is already more than enough to identify a commit.
    __BUILD_VERSION__: JSON.stringify(
      (process.env.VERCEL_GIT_COMMIT_SHA || process.env.BUILD_SHA || 'dev').slice(0, 12)
    ),
    // True only for a bundle Vercel built (it sets VERCEL=1 at build time).
    // Web Analytics is mounted on that condition alone: the script it loads
    // is served by Vercel's edge at /_vercel/insights/script.js, and on the
    // Docker build or the dev server that path is an index.html rewrite - a
    // 200 that is not JavaScript, and a console error on every load.
    __ON_VERCEL__: JSON.stringify(process.env.VERCEL === '1'),
  }
})
