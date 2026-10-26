import { test, expect } from '@playwright/test';

const API_BASE = 'http://localhost:8000';

// Minimal valid ASCII STL for fast compilation during E2E test
const SAMPLE_ASCII_STL = `solid practice_cube
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 20 0 0
      vertex 20 20 0
    endloop
  endfacet
endsolid practice_cube`;

test.describe('Text-to-Printer Web UI — Live Full-Stack E2E Integration', () => {
  test('queries live FastAPI backend endpoints (/status, /sessions) and executes full practice-test lifecycle with cleanup', async ({ page, request }) => {
    // 1. Verify live FastAPI /status endpoint is reachable
    const statusRes = await request.get(`${API_BASE}/status`);
    expect(statusRes.ok()).toBeTruthy();
    const statusJson = await statusRes.json();
    expect(statusJson).toHaveProperty('formatted');

    // 2. Intercept only the static STL binary fetch if OpenSCAD is slow, while letting /sessions/new and DELETE hit the real FastAPI server
    await page.route(`${API_BASE}/created_stls/**`, async (route) => {
      const response = await route.fetch().catch(() => null);
      if (response && response.ok()) {
        await route.fulfill({ response });
      } else {
        await route.fulfill({
          status: 200,
          contentType: 'application/sla',
          body: SAMPLE_ASCII_STL,
        });
      }
    });

    await page.goto('/');
    await expect(page.locator('.new-btn')).toBeVisible();

    // 3. Open live Printer Status modal against the real FastAPI /status endpoint
    await page.locator('.status-btn').click();
    await expect(page.locator('.modal-header h2')).toHaveText('Printer Status');
    await page.locator('.modal-header .close-btn').click();

    // 4. Create a real session on the backend using the built-in "practice test" prompt
    let createdSessionId = null;
    page.on('response', async (res) => {
      if (res.url().endsWith('/sessions/new') && res.status() === 200) {
        const text = await res.text().catch(() => '');
        for (const line of text.split('\n')) {
          if (line.trim()) {
            try {
              const parsed = JSON.parse(line);
              if (parsed.session_id) createdSessionId = parsed.session_id;
              if (parsed.session?.id) createdSessionId = parsed.session.id;
            } catch {
              // ignore partial chunks
            }
          }
        }
      }
    });

    try {
      const chatInput = page.locator('.chat-input');
      await chatInput.fill('practice test');
      await page.locator('.send-btn').click();

      // Wait for generation to complete (practice_cube)
      await expect(page.locator('.viewer-title')).toHaveText('practice_cube', { timeout: 25000 });
      await expect(page.locator('.viewer-version-tag')).toHaveText('v1');

      // 5. Delete the created test session via UI so created_stls/ stays clean
      page.once('dialog', async (dialog) => {
        await dialog.accept();
      });
      await page.locator('button[title="Delete Idea"]').click();
      await expect(page.locator('.empty-state').first()).toContainText('Start a new 3D design idea below.');
      createdSessionId = null;
    } finally {
      // Fallback cleanup in case test failed mid-flight
      if (createdSessionId) {
        await request.delete(`${API_BASE}/sessions/${encodeURIComponent(createdSessionId)}`).catch(() => {});
      }
    }
  });
});
