// Run this async function with Playwright's `page` on the generated sample HTML.
async (page) => {
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const checks = [], errors = [], remoteRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/^https?:/.test(request.url())) remoteRequests.push(request.url()); });
  await page.setViewportSize({width:1440,height:1000});
  await page.reload();
  await page.locator('.node').first().waitFor();
  assert(await page.locator('.node').count() === 7, 'Expected seven sample jobs');
  assert(await page.locator('.edge-label').count() === 5, 'Expected five deduplicated connections');
  assert(await page.locator('.node.cyclic').count() === 2, 'Expected two cyclic jobs');
  assert(await page.locator('.link.cyclic').count() === 2, 'Expected two cycle edges');
  checks.push('initial graph, deduplication, cycle rendering');

  const five = page.locator('.edge-label').filter({hasText:'5 Tables'});
  await five.click();
  assert(await page.locator('#table-dialog').evaluate(el => el.open), 'Edge dialog must open');
  const tables = await page.locator('#dialog-body li').allTextContents();
  assert(JSON.stringify(tables) === JSON.stringify(['stg.customers','stg.orders','stg.payments','stg.products','stg.shipments']), 'Dialog must list five unique shared tables');
  await page.keyboard.press('Escape');
  await five.focus();
  await page.keyboard.press('Enter');
  assert(await page.locator('#table-dialog').evaluate(el => el.open), 'Keyboard edge activation');
  await page.locator('#close-dialog').click();
  checks.push('edge click, exact shared tables, keyboard and Escape');

  await page.locator('.node[data-job="J002"]').focus();
  await page.keyboard.press('Enter');
  assert(await page.locator('.node[data-job="J002"]').evaluate(el => el === document.activeElement), 'Keyboard node focus survives rendering');
  assert(await page.locator('#details .pill').first().textContent() === 'READ 5', 'Read count');
  assert(await page.locator('#details li').count() === 6, 'Complete READ and WRITE lists');
  await page.locator('#scope').selectOption('upstream');
  assert(await page.locator('.node').count() === 2, 'J002 upstream includes J001 and J002');
  await page.locator('#scope').selectOption('downstream');
  assert(await page.locator('.node').count() === 3, 'J002 downstream includes J002/J003/J004');
  await page.locator('#scope').selectOption('both');
  assert(await page.locator('.node').count() === 4, 'J002 combined lineage');
  await page.locator('#all').click();
  checks.push('node detail and upstream/downstream traversal');

  await page.locator('#search').fill('stg.shipments');
  assert(await page.locator('#results button').count() === 2, 'Table search should find writer and reader');
  await page.locator('#search').press('Enter');
  assert(await page.locator('#details h2').textContent() === '주문 수집', 'Search selects first job');
  await page.locator('#all').click();
  await page.locator('.node[data-job="J005"]').click();
  await page.locator('#scope').selectOption('both');
  assert(await page.locator('.node').count() === 2, 'Cycle traversal terminates with both jobs');
  assert(await page.locator('.edge-label').count() === 2, 'Both cycle directions retained');
  await page.locator('#all').click();
  await page.locator('.node[data-job="J007"]').click();
  assert((await page.locator('#details .warning').textContent()).includes('동일 테이블'), 'In-place read/write detail');
  checks.push('table search, cycle traversal and in-place update');

  await page.locator('#diagnostics').click();
  assert((await page.locator('#dialog-body').textContent()).includes('순환 그룹 1'), 'Cycle diagnostics');
  assert(await page.locator('#dialog-body li').count() === 2, 'Diagnostics must list both cycle jobs');
  await page.keyboard.press('Escape');
  await page.locator('#scope').selectOption('both');
  const downloadEvent = page.waitForEvent('download');
  await page.locator('#export').click();
  const download = await downloadEvent;
  assert(download.suggestedFilename() === 'job_table_mapping.csv', 'Canonical CSV export filename');
  // Read through the browser Blob API as well to verify canonical rows without
  // relying on a test runner's filesystem or extra dependencies.
  const csv = await page.evaluate(async () => {
    const original = URL.createObjectURL;
    let captured;
    URL.createObjectURL = blob => { captured = blob; return original(blob); };
    document.getElementById('export').click();
    URL.createObjectURL = original;
    return captured.text();
  });
  assert(csv.includes('"job_id","job_name","table_name","relation_type"'), 'Export headers');
  assert(csv.includes('"J007","재고 갱신","ods.inventory","READ"'), 'Export read mapping');
  assert(csv.includes('"J007","재고 갱신","ods.inventory","WRITE"'), 'Export write mapping');
  assert(!csv.includes('J001'), 'Export respects visible subset');
  checks.push('diagnostics and CSV export of visible jobs');

  await page.locator('#all').click();
  const before = await page.locator('#viewport').getAttribute('transform');
  await page.locator('#plus').click();
  assert(await page.locator('#viewport').getAttribute('transform') !== before, 'Zoom transform');
  await page.locator('#fit').click();
  const fitBefore = await page.locator('#viewport').getAttribute('transform');
  const bounds = await page.locator('#chart').boundingBox();
  await page.mouse.move(bounds.x+15,bounds.y+15);
  await page.mouse.down();await page.mouse.move(bounds.x+65,bounds.y+45);await page.mouse.up();
  assert(await page.locator('#viewport').getAttribute('transform') !== fitBefore, 'Pan transform');
  checks.push('zoom, fit and drag');

  await page.setViewportSize({width:390,height:844});
  await page.locator('#fit').click();
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No mobile horizontal page overflow');
  await page.locator('.node[data-job="J002"]').click();
  assert(await page.locator('#details h2').textContent() === '매출 집계', 'Mobile selection');
  assert(errors.length === 0, 'Browser errors: '+errors.join('; '));
  assert(remoteRequests.length === 0, 'Standalone page must not request remote assets');
  checks.push('mobile layout, selection, no runtime errors or remote dependencies');
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#fit').click();
  return {passed:checks.length,checks,errors,remoteRequests};
}
