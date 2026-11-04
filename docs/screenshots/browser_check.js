const { chromium } = require('playwright');
const out = process.argv[2];
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const settle = async () => { await page.waitForTimeout(600); await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 60000 }).catch(() => {}); await page.waitForTimeout(800); };
  const overflow = async (name) => {
    const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
      main: (document.querySelector('section.main, [data-testid="stMain"]') || document.body).scrollWidth }));
    console.log(`${name}: scrollWidth=${r.sw} clientWidth=${r.cw} main=${r.main} ${r.sw > r.cw ? 'HORIZONTAL OVERFLOW' : 'no horizontal overflow'}`);
  };
  await page.goto('http://127.0.0.1:8599', { waitUntil: 'networkidle' });
  await page.getByText('当前判断', { exact: false }).first().waitFor({ timeout: 60000 });
  await settle(); await overflow('首屏'); await page.screenshot({ path: `${out}/01_overview.png` });
  // switch to the cross-batch case
  await page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first().click();
  await page.getByText('跨批次跟踪', { exact: false }).last().click();
  await settle(); await overflow('跨批次总览'); await page.screenshot({ path: `${out}/02_cross_batch_overview.png` });
  for (const [label, file] of [['管理层速览', '03_brief'], ['变化时间线', '04_timeline'], ['补证过程', '05_verification'], ['研究判断', '06_thesis']]) {
    await page.locator('[data-testid="stRadio"] label').filter({ hasText: label }).first().click();
    await settle(); await overflow(label); await page.screenshot({ path: `${out}/${file}.png`, fullPage: true });
  }
  // toggle full trace
  await page.locator('[data-testid="stRadio"] label').filter({ hasText: '补证过程' }).first().click(); await settle();
  await page.getByText('展开完整补证轨迹').click(); await settle();
  console.log('trace expanded shows request URL:', await page.getByText('请求地址', { exact: false }).count() > 0);
  await page.screenshot({ path: `${out}/07_verification_full.png`, fullPage: true });
  // click an evidence id
  const ev = page.locator('button').filter({ hasText: /^EV-/ }).first();
  const evId = (await ev.innerText()).trim(); await ev.click(); await settle();
  console.log('evidence click ->', evId, 'detail heading visible:', await page.getByRole('heading', { name: evId }).count() > 0);
  await overflow('证据详情'); await page.screenshot({ path: `${out}/08_evidence.png`, fullPage: true });
  // download brief
  await page.locator('[data-testid="stRadio"] label').filter({ hasText: '管理层速览' }).first().click(); await settle();
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByText('下载中文速览（Markdown）').click()]);
  const p = `${out}/downloaded_brief.md`; await dl.saveAs(p);
  console.log('downloaded:', dl.suggestedFilename());
  // offline replay
  await page.locator('[data-testid="stSidebar"] label').filter({ hasText: '离线回放' }).first().click(); await settle();
  await page.getByRole('button', { name: '运行离线回放' }).click();
  await page.getByText('离线回放完成', { exact: false }).first().waitFor({ timeout: 120000 }); await settle();
  console.log('replay banner:', (await page.getByText('离线回放完成', { exact: false }).first().innerText()).slice(0, 60));
  await page.screenshot({ path: `${out}/09_replay.png` });
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
