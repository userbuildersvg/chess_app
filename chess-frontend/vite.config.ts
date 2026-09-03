import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 3000,
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
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify(process.env.VITE_API_URL || 'http://localhost:8080')
  }
})
