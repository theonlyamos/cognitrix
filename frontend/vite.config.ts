import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'
import { readFileSync } from 'fs'

// Single source of truth for the version: the repo-root pyproject.toml. Read it
// at build time and inject it, so a bump only ever touches pyproject.toml.
const pyproject = readFileSync(path.resolve(__dirname, '../pyproject.toml'), 'utf-8')
const appVersion = /^version = "([^"]+)"/m.exec(pyproject)?.[1] ?? '0.0.0'

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // noVNC (the agents' computer live screen) uses top-level await; every
  // browser and webview the app supports runs it.
  build: {
    target: ['es2022', 'chrome89', 'edge89', 'firefox89', 'safari15'],
  },
  optimizeDeps: {
    esbuildOptions: { target: 'es2022' },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true, // the agents' computer live screen
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    css: true,
  },
})
