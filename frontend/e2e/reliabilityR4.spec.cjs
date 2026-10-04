const { test, expect } = require('@playwright/test');
test.beforeEach(async ({ browser }, info) => {
  await info.attach('browser-runtime', { body: JSON.stringify({ version: browser.version(), channel: process.env.CLIPFORGE_BROWSER_CHANNEL || 'chrome', headless: true }), contentType: 'application/json' });
});
async function seed(request, data = {}) {
  const response = await request.post('/__test__/seed', { data }); expect(response.ok()).toBeTruthy(); return response.json();
}
async function open(page) {
  await page.goto('/');
  const selected = page.getByRole('textbox', { name: 'Tên dự án' });
  if (!await selected.isVisible()) await page.locator('.project-list').getByRole('button', { name: /R4 browser A/ }).click();
  await expect(selected).toHaveValue('R4 browser A');
}
const facts = async request => (await request.get('/__test__/facts')).json();

test('missing TTS cache can be confirmed and resumed with one completion refresh', async ({ page, request }) => {
  const f = await seed(request, { kind: 'voice', hold: true }); await open(page);
  await page.getByRole('button', { name: 'Tác vụ', exact: true }).click();
  await page.getByRole('button', { name: 'Chi tiết', exact: true }).click();
  const retry = page.getByRole('button', { name: 'Xác nhận gửi lại', exact: true });
  await expect(retry).toBeDisabled();
  await page.getByRole('checkbox', { name: 'Tôi hiểu gửi lại có thể bị tính phí lần nữa.' }).check();
  await retry.click();
  await page.getByRole('button', { name: 'Tiếp tục hàng đợi' }).click();
  await expect(page.locator('.task-row .task-status')).toHaveText('Đang chạy');
  let gets = 0;
  page.on('request', req => { if (new URL(req.url()).pathname === `/api/projects/${f.pid}` && req.method() === 'GET') gets++; });
  await request.post('/__test__/release');
  await expect(page.locator('.task-row .task-status')).toHaveText('Hoàn tất');
  await expect.poll(async () => (await facts(request)).jobs[0].attempt).toBe(2);
  await expect.poll(() => gets).toBe(1);
  expect((await (await request.get(`/api/projects/${f.pid}`)).json()).script).toBe('Recovered TTS result');
  await page.waitForTimeout(2200); expect(gets).toBe(1);
});

test('resume error attempt 1 → done attempt 2 refreshes exactly once and opens this MP4', async ({ page, request }) => {
  const f = await seed(request, { hold: true }); await open(page);
  await page.getByRole('button', { name: 'Tác vụ', exact: true }).click();
  await page.getByRole('button', { name: 'Chi tiết', exact: true }).click();
  await page.getByRole('button', { name: 'Bắt đầu tác vụ đã lưu' }).click();
  await page.getByRole('button', { name: 'Tiếp tục hàng đợi' }).click();
  await expect(page.locator('.task-row .task-status')).toHaveText('Đang chạy');
  let gets = 0; page.on('request', req => { if (new URL(req.url()).pathname === `/api/projects/${f.pid}` && req.method() === 'GET') gets++; });
  await request.post('/__test__/release');
  await expect(page.locator('.task-row .task-status')).toHaveText('Hoàn tất');
  await page.getByRole('button', { name: 'Quay lại', exact: true }).click();
  await expect(page.locator('video.rendered-video')).toHaveAttribute('src', new RegExp(`/exports/${f.jid}/video.mp4$`));
  await expect.poll(async () => page.locator('video.rendered-video').evaluate(video => video.readyState)).toBeGreaterThan(0);
  await expect.poll(async () => (await facts(request)).jobs[0].attempt).toBe(2);
  expect(gets).toBe(1);
  await page.waitForTimeout(2200); expect(gets).toBe(1);
});

test('a done event during typing keeps the dirty draft and saves it with the new export', async ({ page, request }) => {
  const f = await seed(request); await open(page);
  await page.getByRole('textbox', { name: 'Tên dự án' }).fill('Draft being typed');
  await request.post(`/__test__/complete/${f.jid}`);
  await expect(page.locator('video.rendered-video')).toHaveAttribute('src', new RegExp(f.jid));
  await expect(page.getByRole('textbox', { name: 'Tên dự án' })).toHaveValue('Draft being typed');
  await expect.poll(async () => (await (await request.get(`/api/projects/${f.pid}`)).json()).name).toBe('Draft being typed');
  const saved = await (await request.get(`/api/projects/${f.pid}`)).json();
  expect(saved.exports[0].id).toBe(f.jid); expect(saved.revision).toBeGreaterThan(2);
});

test('two tabs keep the losing draft after a real 409 instead of overwriting it', async ({ page, context, request }) => {
  const f = await seed(request); await open(page); const second = await context.newPage(); await open(second);
  let release; const gate = new Promise(resolve => { release = resolve; });
  await second.route(`**/api/projects/${f.pid}`, async route => {
    if (route.request().method() === 'PUT') await gate;
    await route.continue();
  });
  await second.getByRole('textbox', { name: 'Tên dự án' }).fill('Tab B draft');
  await page.getByRole('textbox', { name: 'Tên dự án' }).fill('Tab A saved');
  await expect.poll(async () => (await (await request.get(`/api/projects/${f.pid}`)).json()).name).toBe('Tab A saved');
  release();
  await expect(second.locator('.draft-conflict')).toBeVisible();
  await expect(second.getByRole('textbox', { name: 'Tên dự án' })).toHaveValue('Tab B draft');
  await second.reload(); await expect(second.getByRole('textbox', { name: 'Tên dự án' })).toHaveValue('Tab B draft');
  expect((await (await request.get(`/api/projects/${f.pid}`)).json()).name).toBe('Tab A saved');
});

test('two tabs and reload submit one automatic assembly for a completed upload', async ({ page, context, request }) => {
  const f = await seed(request, { kind: 'upload', status: 'done' });
  const second = await context.newPage(); await Promise.all([open(page), open(second)]);
  const assemblies = async () => (await facts(request)).jobs.filter(j => j.kind === 'assemble-audio');
  await expect.poll(async () => (await assemblies()).length).toBe(1);
  await Promise.all([page.reload(), second.reload()]);
  await expect(page.getByRole('textbox', { name: 'Tên dự án' })).toBeVisible();
  await expect(second.getByRole('textbox', { name: 'Tên dự án' })).toBeVisible();
  await page.waitForTimeout(2200);
  expect((await assemblies()).length).toBe(1); expect((await assemblies())[0].payload.upload_job_id).toBe(f.jid);
});

test('backend process exits, UI retains rows and reconnects after an actual restart', async ({ page, request }) => {
  await seed(request); await open(page);
  await page.getByRole('button', { name: 'Tác vụ', exact: true }).click();
  await expect(page.locator('.task-connection')).toContainText('Đã đồng bộ');
  await request.post('/__test__/crash');
  await expect(page.locator('.task-connection')).toContainText('Mất kết nối', { timeout: 15000 });
  await expect(page.locator('.task-row .task-status')).toHaveText('Lỗi');
  await expect(page.locator('.task-connection')).toContainText('Đã đồng bộ', { timeout: 22000 });
  await expect(page.locator('.task-row .task-status')).toHaveText('Lỗi');
});

test('project B stays selected when a completion fetch for A returns late', async ({ page, request }) => {
  const f = await seed(request); await open(page);
  let release, received; const gate = new Promise(resolve => { release = resolve; });
  const intercepted = new Promise(resolve => { received = resolve; }); let held = false;
  await page.route(`**/api/projects/${f.pid}`, async route => {
    if (route.request().method() !== 'GET' || held) return route.continue();
    held = true; const response = await route.fetch(); received(); await gate; await route.fulfill({ response });
  });
  await request.post(`/__test__/complete/${f.jid}`); await intercepted;
  await page.locator('.project-list').getByRole('button', { name: /R4 browser B/ }).click();
  await expect(page.getByRole('textbox', { name: 'Tên dự án' })).toHaveValue('R4 browser B');
  release(); await page.waitForTimeout(2200);
  await expect(page.getByRole('textbox', { name: 'Tên dự án' })).toHaveValue('R4 browser B');
  await expect(page.locator('video.rendered-video')).toHaveCount(0);
});

for (const status of ['error', 'interrupted', 'blocked']) {
  test(`discard ${status} unpins cache in the browser and keeps the original source`, async ({ page, request }) => {
    await seed(request, { status, checkpoint: true }); await open(page);
    await page.getByRole('button', { name: 'Dung lượng', exact: true }).click();
    await page.getByRole('button', { name: 'Xem trước dung lượng' }).click();
    await page.getByRole('button', { name: 'Dọn cache', exact: true }).click();
    await expect(page.locator('.management-error')).toContainText(/tác vụ|checkpoint/i);
    await page.getByRole('button', { name: 'Tác vụ', exact: true }).click();
    await expect(page.locator('.task-discard-summary').first()).toContainText('Bỏ bản xuất chưa lưu');
    await page.getByRole('button', { name: 'Bỏ tác vụ và dữ liệu phục hồi', exact: true }).click();
    await expect(page.locator('.task-row .task-status')).toHaveText('Đã hủy');
    await expect(page.getByText(/Đã cập nhật dung lượng/)).toBeVisible();
    await page.getByRole('button', { name: 'Dung lượng', exact: true }).click();
    await page.getByRole('button', { name: 'Xem trước dung lượng' }).click();
    await page.getByRole('button', { name: 'Dọn cache', exact: true }).click();
    await expect(page.locator('.confirm-strip')).toHaveCount(0);
    expect((await facts(request)).sources).toEqual(['original source']);
    expect(Object.values((await facts(request)).pins)).toEqual([false]);
  });
}
