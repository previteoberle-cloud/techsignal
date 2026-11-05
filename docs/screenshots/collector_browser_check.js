// Collector MVP browser acceptance (real Chromium). Normal mode: public sources only, no dev opt-in.
// Phase "collect": create a task in the home page, collect twice from a REAL public source, search,
// download CSV and Markdown, check the status page. Phase "after-restart": the workbench was restarted
// with the same data directory; the items must still be there and a new collection adds no duplicates.
// Usage: node collector_browser_check.js <out dir> <workbench url> <collect|after-restart> <source url>
const { chromium } = require('playwright');
const [out, url, phase, source] = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const settle = async () => { await page.waitForTimeout(600); await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 120000 }).catch(() => {}); await page.waitForTimeout(800); };
  const top = async () => { await page.evaluate(() => document.querySelectorAll('[data-testid="stMain"], [data-testid="stAppViewContainer"], section.main').forEach(e => e.scrollTo(0, 0))); };
  const shot = async (file, full = true) => { await settle(); await top(); await page.waitForTimeout(300); await page.screenshot({ path: `${out}/${file}.png`, fullPage: full }); };
  const field = (label) => page.getByRole('textbox', { name: label, exact: false }).first();
  const has = async (text) => (await page.getByText(text, { exact: false }).count()) > 0;
  const alert = async (text) => (await page.locator('[data-testid="stAlert"]').filter({ hasText: text }).count()) > 0;
  const tab = async (label) => { await page.locator('[data-testid="stMain"] [data-testid="stRadio"] label').filter({ hasText: label }).first().click(); await settle(); };
  const overflow = async (name) => {
    const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    console.log(`${name}: ${r.sw > r.cw ? 'HORIZONTAL OVERFLOW' : 'no horizontal overflow'}`);
  };
  await page.goto(url, { waitUntil: 'networkidle' }); await settle();

  if (phase === 'collect') {
    console.log('home page is the collector:', await has('新建采集任务'), 'no judgement fields:', !(await has('当前研究判断')));
    await shot('c01_new_task', false);
    await field('采集任务名称').fill('AI 芯片行业动态');
    await field('关注的企业或关键词').fill('Tenstorrent，Blackhole，Wormhole');
    await field('分类标签').fill('AI芯片');
    await field('公开信息来源网址').fill(`${source} tt-metal README（官方仓库）`); await settle();
    await page.getByRole('button', { name: '创建采集任务' }).click(); await settle();
    console.log('task created, empty feed:', await has('还没有采集到信息'));
    await page.getByRole('button', { name: '立即采集' }).click(); await settle();
    console.log('first collection adds items:', await alert('新增 1 条'));
    await overflow('信息流'); await shot('c02_first_collection');
    await page.getByRole('button', { name: '立即采集' }).click(); await settle();
    console.log('second collection adds nothing (dedup):', await alert('新增 0 条'));
    await field('搜索标题和摘要').fill('Blackhole'); await page.keyboard.press('Enter'); await settle();
    console.log('search keeps the item:', await has('筛选后 1 条'));
    await field('搜索标题和摘要').fill('不存在的词'); await page.keyboard.press('Enter'); await settle();
    console.log('search can exclude:', await has('筛选后 0 条'));
    await field('搜索标题和摘要').fill(''); await page.keyboard.press('Enter'); await settle();
    const [csv] = await Promise.all([page.waitForEvent('download'), page.getByText('导出 CSV').click()]);
    await csv.saveAs(`${out}/export.csv`); console.log('csv downloaded:', csv.suggestedFilename());
    const [md] = await Promise.all([page.waitForEvent('download'), page.getByText('导出 Markdown').click()]);
    await md.saveAs(`${out}/export.md`); console.log('markdown downloaded:', md.suggestedFilename());
    const link = page.locator('[data-testid="stMain"] a').filter({ hasText: 'tt-metal README' }).first();
    console.log('item links to the original source:', (await link.getAttribute('href')) === source);
    await shot('c03_after_second_collection');
    await tab('采集状态');
    console.log('status page shows success:', await has('✅ 成功'));
    await shot('c04_status');
  } else {
    console.log('items survive a restart:', await has('共 1 条'), await has('tt-metal README'));
    await page.getByRole('button', { name: '立即采集' }).click(); await settle();
    console.log('collection after restart adds no duplicates:', await alert('新增 0 条'));
    await page.setViewportSize({ width: 390, height: 844 }); await settle();
    await overflow('手机宽度'); await shot('c05_after_restart_mobile', false);
  }
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
