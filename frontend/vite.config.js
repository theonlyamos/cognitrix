var _a, _b;
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { readFileSync } from 'fs';
// Single source of truth for the version: the repo-root pyproject.toml. Read it
// at build time and inject it, so a bump only ever touches pyproject.toml.
var pyproject = readFileSync(path.resolve(__dirname, '../pyproject.toml'), 'utf-8');
var appVersion = (_b = (_a = /^version = "([^"]+)"/m.exec(pyproject)) === null || _a === void 0 ? void 0 : _a[1]) !== null && _b !== void 0 ? _b : '0.0.0';
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
});
