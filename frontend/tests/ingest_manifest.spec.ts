import { expect, test } from '@playwright/test'

test('入库批次清单：从原始入库确认并发起交接，详情页按批次编号核对', async ({ page }) => {
  test.setTimeout(120_000)
  const browserErrors: string[] = []
  page.on('pageerror', (e) => browserErrors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`)
  })

  // 1. 列表页发起：登记批次并带原始文件内容
  await page.goto('/raw_ingest')
  await page.getByTestId('open-create').click()
  const batchNo = `RAW-E2E-${Date.now()}`
  const fields = ['批次编号', '来源系统', '文件格式']
  const values = [batchNo, '订单系统', 'CSV']
  for (const [index, field] of fields.entries()) {
    await page.locator('.create-field input').nth(index).fill(values[index])
  }
  await page.getByTestId('create-raw-content').fill('order_id,amount,status,coupon\nO1,10,new,C1\nO2,20,paid,\n')
  await page.getByTestId('submit-create').click()
  await expect(page.getByTestId('create-modal')).toHaveCount(0)

  const bar = page.locator('.timeline-row', { hasText: batchNo })
  await bar.getByRole('button', { name: '确认入库' }).click()
  await expect(page.getByTestId('confirm-modal')).toBeVisible()
  await page.getByTestId('confirm-raw-content').fill('order_id,amount,status,coupon\nO1,10,new,C1\nO2,20,paid,\n')
  await page.getByTestId('preview-raw').click()
  await expect(page.getByTestId('preview-result')).toContainText('数据行数：2')
  await page.getByTestId('submit-confirm').click()

  // 2. 进入清单详情页核对
  await page.waitForURL(new RegExp(`/ingest_manifest/${batchNo}`))
  await expect(page.getByTestId('raw-batch-no')).toHaveText(batchNo)
  await expect(page.getByTestId('raw-parsed-rows')).toHaveText('2')
  await expect(page.getByTestId('manifest-checksum')).not.toHaveText('—')
  await expect(page.getByTestId('manifest-mapping-source')).toContainText('本批次生成')
  await expect(page.getByTestId('field-0')).toContainText('order_id')
  await expect(page.getByTestId('field-3')).toContainText('coupon')

  // 3. 文件交接：下载交接文件并在清单列表页导入
  const downloadPromise = page.waitForEvent('download')
  await page.getByTestId('handover-download').click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe(`handover_${batchNo}.json`)
  await page.getByTestId('handover-send').click()
  await expect(page.getByTestId('handover-result')).toContainText('清单校验通过')

  // 4. 清单列表页能看到该批次，且只有一份
  await page.goto('/ingest_manifest')
  const row = page.getByTestId(`row-${batchNo}`)
  await expect(row).toHaveCount(1)
  await expect(row).toContainText('v1')
  await expect(row).toContainText('2')

  // 5. 导出与导入
  const exportPromise = page.waitForEvent('download')
  await page.getByTestId('export').click()
  const exported = await exportPromise
  expect(exported.suggestedFilename()).toBe('ingest_manifest.csv')
  const path = await exported.path()
  expect(path).toBeTruthy()
  await page.getByTestId('import-file').setInputFiles(path as string)
  await expect(page.getByTestId('import-result')).toContainText('已存在，未重复写入')

  expect(browserErrors).toEqual([])
})

test('入库批次清单：重复批次补写字段保留原顺序并留历史快照', async ({ page }) => {
  test.setTimeout(120_000)
  await page.goto('/raw_ingest')
  await page.getByTestId('open-create').click()
  const batchNo = `RAW-DUP-${Date.now()}`
  const values = [batchNo, '订单系统', 'CSV']
  for (const [index, value] of values.entries()) {
    await page.locator('.create-field input').nth(index).fill(value)
  }
  await page.getByTestId('submit-create').click()
  await expect(page.getByTestId('create-modal')).toHaveCount(0)

  const bar = page.locator('.timeline-row', { hasText: batchNo })
  await bar.getByRole('button', { name: '确认入库' }).click()
  await page.getByTestId('confirm-raw-content').fill('order_id,amount,status\nO1,10,new\n')
  await page.getByTestId('submit-confirm').click()
  await page.waitForURL(new RegExp(`/ingest_manifest/${batchNo}`))
  await expect(page.getByTestId('manifest-state')).toHaveText('v1')

  // 重新发起确认，带一个补写字段
  await page.goto('/raw_ingest')
  await page.locator('.timeline-row', { hasText: batchNo }).getByRole('button', { name: '确认入库' }).click()
  await page.getByTestId('confirm-raw-content').fill('order_id,amount,status,coupon\nO1,10,new,C1\n')
  await page.getByTestId('submit-confirm').click()
  await page.waitForURL(new RegExp(`/ingest_manifest/${batchNo}`))

  await expect(page.getByTestId('manifest-state')).toHaveText('v2')
  await expect(page.getByTestId('mapping-appended')).toContainText('coupon')
  await expect(page.getByTestId('mapping-conflict')).toContainText('重复批次')
  await expect(page.getByTestId('mapping-rule-version')).toHaveText('v2')
  await expect(page.getByTestId('mapping-current')).toContainText('order_id,amount,status,coupon')
  await expect(page.getByTestId('history-v1')).toHaveCount(1)
  await expect(page.getByTestId('manifest-fields')).toContainText('coupon')
})
