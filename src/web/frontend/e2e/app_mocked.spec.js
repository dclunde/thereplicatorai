import { test, expect } from '@playwright/test';
import { Buffer } from 'node:buffer';

const API_BASE = 'http://localhost:8000';

// Minimal valid ASCII STL for Three.js STLLoader in browser
const SAMPLE_ASCII_STL = `solid cube
  facet normal 0 0 1
    outer loop
      vertex 0 0 0
      vertex 10 0 0
      vertex 10 10 0
    endloop
  endfacet
endsolid cube`;

const MOCK_SESSIONS_LIST = [
  {
    id: 'sess-1',
    title: 'Desk Cable Clip',
    last_updated: '2026-10-17T08:00:00',
    active_job: null,
  },
  {
    id: 'sess-2',
    title: 'Missing STL Stand',
    last_updated: '2026-10-17T07:00:00',
    active_job: null,
  },
];

const MOCK_SESSION_1 = {
  id: 'sess-1',
  title: 'Desk Cable Clip',
  enable_supports: false,
  versions: 2,
  latest_stl: '/created_stls/sess-1/v2/clip_v2.stl',
  last_updated: '2026-10-17T08:00:00',
  active_job: null,
  history: [
    { role: 'user', text: 'make a desk cable clip' },
    {
      role: 'assistant',
      code: 'cube([20, 20, 10]);',
      size: '0.79 × 0.79 × 0.39 in',
      time: '18m',
      grams: 8.5,
    },
    { role: 'user', text: 'make it thicker' },
    {
      role: 'assistant',
      code: 'cube([25, 25, 15]);',
      size: '0.98 × 0.98 × 0.59 in',
      time: '25m',
      grams: 14.5,
    },
  ],
  all_versions: [
    { version: 1, stl_url: '/created_stls/sess-1/v1/clip_v1.stl', has_stl: true, has_code: true },
    { version: 2, stl_url: '/created_stls/sess-1/v2/clip_v2.stl', has_stl: true, has_code: true },
  ],
};

const MOCK_SESSION_2_MISSING_STL = {
  id: 'sess-2',
  title: 'Missing STL Stand',
  enable_supports: true,
  versions: 1,
  latest_stl: '',
  last_updated: '2026-10-17T07:00:00',
  active_job: null,
  history: [
    { role: 'user', text: 'phone stand' },
    {
      role: 'assistant',
      code: 'cube([30, 30, 30]);',
      size: '1.18 × 1.18 × 1.18 in',
      time: 'Unknown',
      grams: 0,
    },
  ],
  all_versions: [
    { version: 1, stl_url: '', has_stl: false, has_code: true },
  ],
};

async function setupBaseMocks(page) {
  await page.route(`${API_BASE}/created_stls/**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/sla',
      body: SAMPLE_ASCII_STL,
    });
  });

  await page.route(`${API_BASE}/ams_colors`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        0: { color: 'FF0000', type: 'PLA', remain: 80, is_bambu: true },
        1: { color: '00FF00', type: 'PETG', remain: 90, is_bambu: true },
        2: { color: '0000FF', type: 'PLA', remain: 1, is_bambu: true }, // 10g < 14.5g required
        4: { color: 'FFFFFF', type: 'PLA', remain: 50, is_bambu: true, is_external: true },
      }),
    });
  });

  await page.route(`${API_BASE}/status`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        connected: true,
        state: 'IDLE',
        status: { state: 'IDLE' },
        formatted: 'Printer Status:\nState: Not Printing (IDLE)',
      }),
    });
  });

  await page.route(`${API_BASE}/sessions*`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/sessions') {
      const q = (url.searchParams.get('q') || '').toLowerCase();
      const filtered = q
        ? MOCK_SESSIONS_LIST.filter((s) => s.title.toLowerCase().includes(q))
        : MOCK_SESSIONS_LIST;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(filtered),
      });
      return;
    }
    await route.fallback();
  });

  await page.route(`${API_BASE}/sessions/sess-1`, async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(MOCK_SESSION_1),
      });
    } else if (route.request().method() === 'DELETE') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', message: 'Session deleted' }),
      });
    } else {
      await route.fallback();
    }
  });

  await page.route(`${API_BASE}/sessions/sess-2`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(MOCK_SESSION_2_MISSING_STL),
    });
  });
}

test.describe('Text-to-Printer Web UI — Mocked E2E Feature & Edge-Case Suite', () => {
  test('loads sidebar sessions, filters via debounced search, and resets on + New Idea', async ({ page }) => {
    await setupBaseMocks(page);
    await page.goto('/');

    // Initial empty state
    await expect(page.locator('.empty-state').first()).toContainText('Start a new 3D design idea below.');
    await expect(page.locator('.session-item')).toHaveCount(2);
    await expect(page.locator('.session-item').first()).toContainText('Desk Cable Clip');

    // Load session 1
    await page.locator('.session-item').first().click();
    await expect(page.locator('.viewer-title')).toHaveText('Desk Cable Clip');
    await expect(page.locator('.viewer-version-tag')).toHaveText('v2');
    await expect(page.locator('.chat-container')).toContainText('0.98 × 0.98 × 0.59 in');
    await expect(page.locator('.chat-container')).toContainText('14.5g');
    await expect(page.locator('.chat-container')).toContainText('25m');

    // Switch version selector between v1 and v2
    await page.locator('.version-btn', { hasText: 'v1' }).click();
    await expect(page.locator('.viewer-version-tag')).toHaveText('v1');
    await page.locator('.version-btn', { hasText: 'v2' }).click();
    await expect(page.locator('.viewer-version-tag')).toHaveText('v2');

    // Search filtering
    const searchInput = page.locator('.search-input');
    await searchInput.fill('Missing');
    await expect(page.locator('.session-item')).toHaveCount(1);
    await expect(page.locator('.session-item').first()).toContainText('Missing STL Stand');

    await searchInput.fill('');
    await expect(page.locator('.session-item')).toHaveCount(2);

    // + New Idea resets active session
    await page.locator('.new-btn').click();
    await expect(page.locator('.empty-state').first()).toContainText('Start a new 3D design idea below.');
  });

  test('streams new design creation (POST /sessions/new) and handles AI error gracefully', async ({ page }) => {
    await setupBaseMocks(page);

    const newSessionObj = {
      id: 'sess-new-100',
      title: 'Hexagon Coaster',
      enable_supports: true,
      versions: 1,
      latest_stl: '/created_stls/sess-new-100/v1/coaster.stl',
      last_updated: '2026-10-17T09:00:00',
      active_job: null,
      history: [
        { role: 'user', text: 'make a hexagon coaster' },
        {
          role: 'assistant',
          code: 'cylinder(h=4, r=45, $fn=6);',
          size: '3.54 × 3.54 × 0.16 in',
          time: '32m',
          grams: 19.2,
        },
      ],
      all_versions: [
        { version: 1, stl_url: '/created_stls/sess-new-100/v1/coaster.stl', has_stl: true, has_code: true },
      ],
    };

    let sentBody = null;
    await page.route(`${API_BASE}/sessions/new`, async (route) => {
      sentBody = JSON.parse(route.request().postData());
      const ndjson = [
        JSON.stringify({ status: 'init', session_id: 'sess-new-100', session: { ...newSessionObj, latest_stl: '', all_versions: [] } }),
        JSON.stringify({ status: 'step', message: 'Generating design...', progress: 33, attempt: 1, max_calls: 3 }),
        JSON.stringify({ status: 'complete', session: newSessionObj }),
      ].join('\n') + '\n';

      await route.fulfill({
        status: 200,
        contentType: 'application/x-ndjson',
        body: ndjson,
      });
    });

    await page.goto('/');

    // Toggle supports checkbox ON
    const supportsCheckbox = page.locator('.input-area input[type="checkbox"]');
    await supportsCheckbox.check();
    await expect(page.locator('.input-area')).toContainText('Supports (ON)');

    // Shift+Enter inserts newline without submitting
    const chatInput = page.locator('.chat-input');
    await chatInput.fill('make a hexagon coaster');
    await chatInput.press('Shift+Enter');
    expect(sentBody).toBeNull();

    // Click Send
    await page.locator('.send-btn').click();
    expect(sentBody).toEqual({
      prompt: 'make a hexagon coaster\n',
      enable_supports: true,
    });

    await expect(page.locator('.viewer-title')).toHaveText('Hexagon Coaster');
    await expect(page.locator('.chat-container')).toContainText('3.54 × 3.54 × 0.16 in');
    await expect(page.locator('.chat-container')).toContainText('Enabled');
  });

  test('displays missing-STL recovery card and streams re-render (POST /sessions/{id}/versions/{v}/rerender)', async ({ page }) => {
    await setupBaseMocks(page);

    const recoveredSession = {
      ...MOCK_SESSION_2_MISSING_STL,
      latest_stl: '/created_stls/sess-2/v1/stand.stl',
      all_versions: [
        { version: 1, stl_url: '/created_stls/sess-2/v1/stand.stl', has_stl: true, has_code: true },
      ],
    };

    await page.route(`${API_BASE}/sessions/sess-2/versions/1/rerender`, async (route) => {
      const ndjson = [
        JSON.stringify({ status: 'step', message: 'Rendering OpenSCAD to STL...', progress: 60 }),
        JSON.stringify({ status: 'complete', session: recoveredSession }),
      ].join('\n') + '\n';
      await route.fulfill({
        status: 200,
        contentType: 'application/x-ndjson',
        body: ndjson,
      });
    });

    await page.goto('/');
    await page.locator('.session-item', { hasText: 'Missing STL Stand' }).click();

    // Missing STL card and warning icon on v1 button should be visible
    await expect(page.locator('.version-btn', { hasText: 'v1 ⚠️' })).toBeVisible();
    await expect(page.locator('.missing-stl-card')).toContainText('3D Model file (STL) for v1 is missing');

    // Click Re-render STL button
    await page.locator('.missing-stl-action').click();

    // Card disappears and Print button appears
    await expect(page.locator('.missing-stl-card')).toHaveCount(0);
    await expect(page.locator('.print-btn')).toBeVisible();
  });

  test('handles Printer Status modal, camera snapshot, and Stop Print states (IDLE vs RUNNING)', async ({ page }) => {
    await setupBaseMocks(page);

    // Override /snapshot with a valid 1x1 PNG/JPEG blob
    await page.route(`${API_BASE}/snapshot`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'image/jpeg',
        body: Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
      });
    });

    await page.goto('/');
    await page.locator('.status-btn').click();
    await expect(page.locator('.modal-header h2')).toHaveText('Printer Status');
    await expect(page.locator('.modal-body pre')).toContainText('State: Not Printing (IDLE)');

    // Clicking Stop Print while IDLE triggers an alert blocking the stop call
    let dialogMessage = '';
    page.once('dialog', async (dialog) => {
      dialogMessage = dialog.message();
      await dialog.accept();
    });
    await page.locator('button', { hasText: 'Stop Print' }).click();
    expect(dialogMessage).toContain('Printer is currently IDLE (not printing)');

    // Clicking Get Snapshot displays the snapshot img
    await page.locator('button', { hasText: 'Get Snapshot' }).click();
    await expect(page.locator('img[alt="Printer Snapshot"]')).toBeVisible();

    // Now simulate printer RUNNING and test Stop Print confirmation + POST /stop_print
    await page.route(`${API_BASE}/status`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          connected: true,
          state: 'RUNNING',
          status: { state: 'RUNNING', percent: 55 },
          formatted: 'Printer Status:\nState: RUNNING\nProgress: 55%',
        }),
      });
    });

    let stopCalled = false;
    await page.route(`${API_BASE}/stop_print`, async (route) => {
      stopCalled = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', message: 'Print stop command sent.' }),
      });
    });

    await page.locator('button', { hasText: 'Refresh Status' }).click();
    await expect(page.locator('.modal-body pre')).toContainText('State: RUNNING');

    page.on('dialog', async (dialog) => {
      await dialog.accept();
    });
    await page.locator('button', { hasText: 'Stop Print' }).click();
    await expect.poll(() => stopCalled).toBe(true);
  });

  test('validates Print Modal AMS slots (PLA vs PETG disabled, insufficient grams border, External Spool, offline guard)', async ({ page }) => {
    await setupBaseMocks(page);

    let printPayload = null;
    await page.route(`${API_BASE}/sessions/sess-1/print`, async (route) => {
      printPayload = JSON.parse(route.request().postData());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', message: 'Print job started' }),
      });
    });

    await page.goto('/');
    await page.locator('.session-item').first().click();
    await page.locator('.print-btn').click();

    await expect(page.locator('.modal-header h2')).toHaveText('Print Settings');
    await expect(page.locator('.modal-body')).toContainText('Model requires: ~14.5g of filament');

    // Slot 1 is PETG -> clicking it should NOT select it
    const slot1Swatch = page.locator('.color-swatch', { hasText: /^1$/ });
    await slot1Swatch.click();
    await expect(slot1Swatch).not.toHaveClass(/selected/);

    // Slot 0 is PLA (800g) -> clicking selects it
    const slot0Swatch = page.locator('.color-swatch', { hasText: /^0$/ });
    await slot0Swatch.click();
    await expect(slot0Swatch).toHaveClass(/selected/);

    // External Spool (Ext / Slot 4) -> clicking selects slot 4
    const extSwatch = page.locator('.color-swatch', { hasText: 'Ext' });
    await extSwatch.click();
    await expect(extSwatch).toHaveClass(/selected/);

    // Toggle Supports ON inside Print Modal
    await page.locator('.modal-body button', { hasText: '🛠️ On' }).click();

    // Confirm Print
    await page.locator('button', { hasText: 'Confirm Print' }).click();
    expect(printPayload).toEqual({
      version: 2,
      ams_slot: 4,
      enable_supports: true,
    });
    await expect(page.locator('.modal-header h2')).toHaveCount(0);

    // Verify offline printer guard disables Confirm Print button
    await page.route(`${API_BASE}/status`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          connected: false,
          state: null,
          status: {},
          formatted: 'Offline',
        }),
      });
    });
    await page.locator('.print-btn').click();
    await expect(page.locator('.modal-body')).toContainText('Printer is offline / disconnected.');
    const offlineBtn = page.locator('button', { hasText: 'Printer Offline' });
    await expect(offlineBtn).toBeDisabled();
  });

  test('handles pane resizing, Open Folder, and Delete Idea confirmation', async ({ page }) => {
    await setupBaseMocks(page);

    let openFolderVersion = null;
    await page.route(`${API_BASE}/sessions/sess-1/open_folder`, async (route) => {
      openFolderVersion = JSON.parse(route.request().postData()).version;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok' }),
      });
    });

    await page.goto('/');

    // Drag sidebar divider to resize sidebar
    const sidebar = page.locator('.sidebar');
    const divider = page.locator('.pane-divider').first();
    const box = await divider.boundingBox();
    await page.mouse.move(box.x + 2, box.y + 100);
    await page.mouse.down();
    await page.mouse.move(box.x + 80, box.y + 100);
    await page.mouse.up();
    const newStyle = await sidebar.getAttribute('style');
    expect(newStyle).not.toContain('260px');

    // Load session 1 and click Open Folder
    await page.locator('.session-item').first().click();
    await page.locator('button[title="Open Folder in File Explorer"]').click();
    expect(openFolderVersion).toBe(2);

    // Click Delete Idea and accept confirmation
    page.once('dialog', async (dialog) => {
      await dialog.accept();
    });
    await page.locator('button[title="Delete Idea"]').click();
    await expect(page.locator('.session-item')).toHaveCount(1);
    await expect(page.locator('.empty-state').first()).toContainText('Start a new 3D design idea below.');
  });
});
