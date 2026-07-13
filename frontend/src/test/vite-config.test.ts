// @vitest-environment node

import { describe, expect, it } from 'vitest';
import { loadConfigFromFile } from 'vite';

describe('Vite development API routing', () => {
  it('proxies versioned API requests to the local backend', async () => {
    const result = await loadConfigFromFile({ command: 'serve', mode: 'development' });
    const config = result?.config;

    expect(config?.server?.proxy?.['/api']).toMatchObject({
      target: 'http://localhost:8000',
      changeOrigin: true,
    });
  });
});
