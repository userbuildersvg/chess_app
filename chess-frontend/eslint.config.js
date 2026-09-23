import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  // 'android' is Capacitor's generated project. Its build outputs include a
  // copy of Capacitor's own native-bridge.js, which is not this project's
  // code and fails these rules - so a local `npm run lint` after an APK
  // build would report an error in a file nobody here wrote. CI never sees
  // it (the directory is git-ignored and Android is not built there); this
  // keeps the local run honest too.
  globalIgnores(['dist', 'android']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
])
