// R6 browser acceptance (real Chromium): create a research project in the workbench, connect a REAL
// changedetection.io, confirm watch creation, check once (baseline -> "no change"), change the test
// page, check again, read the judgement and its explanation, edit the judgement, view history.
// Usage: node r6_browser_check.js <out dir> <workbench url> <info.json from scripts/r6_acceptance_env.py>
const { chromium } = require('playwright');
const fs = require('fs');
const [out, url, infoPath] = process.argv.slice(2);
const info = JSON.parse(fs.readFileSync(infoPath, 'utf-8'));
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const settle = async () => { await page.waitForTimeout(600); await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"]'), null, { timeout: 120000 }).catch(() => {}); await page.waitForTimeout(800); };
  const overflow = async (name) => {
    const r = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    console.log(`${name}: ${r.sw > r.cw ? 'HORIZONTAL OVERFLOW' : 'no horizontal overflow'}`);
  };
  const top = async () => { await page.evaluate(() => document.querySelectorAll('[data-testid="stMain"], [data-testid="stAppViewContainer"], section.main').forEach(e => e.scrollTo(0, 0))); };
  const shot = async (file, full = true) => { await settle(); await top(); await page.waitForTimeout(300); await page.screenshot({ path: `${out}/${file}.png`, fullPage: full }); };
  const field = (label) => page.getByRole('textbox', { name: label, exact: false }).first();
  const tab = async (label) => { await page.locator('[data-testid="stMain"] [data-testid="stRadio"] label').filter({ hasText: label }).first().click(); await settle(); };
  const has = async (text) => (await page.getByText(text, { exact: false }).count()) > 0;

  await page.goto(url, { waitUntil: 'networkidle' }); await settle();
  await page.locator('[data-testid="stSidebar"] label').filter({ hasText: '我的研究项目' }).first().click(); await settle();
  console.log('new-project form shown:', await has('新建研究项目'));
  await shot('r6_01_new_project_form', false);

  // 1) an unsafe URL is refused and nothing is created
  await field('公司或主题名称').fill('星衡半导体');
  await field('每行一个网址').fill('http://169.254.169.254/latest/meta-data/ 云元数据');
  await page.getByRole('button', { name: '创建研究项目' }).click(); await settle();
  console.log('invalid input rejected:', await has('有 '), await has('请填写技术领域'));
  await shot('r6_02_validation_errors');

  // 2) the real form
  await field('技术领域或核心研究问题').fill('8 英寸碳化硅衬底产线的量产进度');
  await field('当前研究判断').fill('星衡半导体 8 英寸碳化硅产线将在 2026 年内量产。');
  await page.getByRole('textbox', { name: '假设 1', exact: true }).fill('8 英寸产线 2026 年内量产');
  await field('支持信号').fill('如期量产');
  await field('削弱信号').fill('推迟，延期');
  await field('每行一个网址').fill(`${info.page_url} 官方新闻页（本地测试页）`); await settle();
  await field('关注的主题词').fill('碳化硅'); await settle();
  await page.getByRole('button', { name: '创建研究项目' }).click(); await settle();
  console.log('project created, empty state shown:', await has('尚无监测变化'));
  await overflow('项目概况'); await shot('r6_03_project_created');

  // 3) monitoring with the real changedetection.io
  await tab('监测与评估');
  console.log('works without the service:', await has('当前未连接 changedetection.io'));
  await field('changedetection.io 地址').fill(info.cdio_url);
  await field('API key').fill(info.api_key);
  await page.getByRole('button', { name: '连接并读取监测项' }).click(); await settle();
  console.log('connected:', await has('已连接：changedetection.io'));
  console.log('create disabled before confirmation:', await page.getByRole('button', { name: '建立监测' }).isDisabled());
  await shot('r6_04_connect_and_confirm');
  await page.getByText('我确认：', { exact: false }).click(); await settle();
  await page.getByRole('button', { name: '建立监测' }).click(); await settle();
  await page.waitForTimeout(4000);
  await page.getByRole('button', { name: '立即检查一次并研判' }).click(); await settle();
  console.log('first check -> no change:', await has('本次没有新的变化'));
  await shot('r6_05_first_check_no_change');

  // the official page publishes a delay
  await fetch(info.control_url, { method: 'POST', body: '星衡半导体新闻中心\n最新动态\n星衡半导体8英寸碳化硅产线建设进展顺利\n星衡半导体宣布8英寸碳化硅产线量产推迟至2027年第二季度\n' });
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: '立即检查一次并研判' }).click(); await settle();
  console.log('second check -> assessed:', await has('已用真实流水线完成研判'));
  await shot('r6_06_second_check_assessed');

  await tab('研判结果');
  await page.getByText('为什么这样判断').first().click(); await settle();
  console.log('no regex shown:', !(await has('(?i)')));
  console.log('judgement source shown:', await has('判断来源'), 'weakens:', await has('削弱原判断'));
  await overflow('研判结果'); await shot('r6_07_result_explained');

  // 4) edit the judgement -> version history
  await tab('研究设定');
  await field('当前研究判断').fill('量产已公告推迟至 2027 年第二季度，原判断待复核。');
  await field('修改说明').fill('根据官网公告复核');
  await page.getByRole('button', { name: '保存修改' }).click(); await settle();
  await tab('修改记录');
  console.log('history shows v1 and v2:', await has('v1'), await has('v2'));
  await shot('r6_08_version_history');

  // 5) phone width
  await page.setViewportSize({ width: 390, height: 844 }); await tab('概况');
  await overflow('手机宽度概况'); await shot('r6_09_mobile_overview', false);
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
