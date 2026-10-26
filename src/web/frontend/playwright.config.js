import { defineConfig, devices } from '@playwright/test';
import process from 'node:process';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: 'list',
  timeout: 30000,
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'on-first-retry',
    headless: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    ...(process.env.PLAYWRIGHT_MOCKED_ONLY === '1'
      ? []
      : [
          {
            command: 'uv run --project ../.. uvicorn main:app --host 127.0.0.1 --port 8000',
            cwd: '../backend',
            url: 'http://localhost:8000/status',
            reuseExistingServer: !process.env.CI,
            timeout: 30000,
            env: {
              ...process.env,
              PYTEST_CURRENT_TEST: '1',
            },
          },
        ]),
    {
      command: 'npm run dev -- --port 5173 --strictPort',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 30000,
    },
  ],
});

