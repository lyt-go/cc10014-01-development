import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  timeout: 300_000,
  expect: { timeout: 10_000 },
  workers: 3,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
    browserName: 'chromium',
    headless: true,
    actionTimeout: 10_000,
  },
  reporter: 'line',
  projects: [
    // 基础用例依赖各模块的初始 3 条示例数据，必须先在干净后端上跑完。
    { name: 'core', testMatch: /functionality\.spec\.ts/ },
    // 入库批次清单用例会真实确认入库并写入字段映射，放在基础用例之后串行执行，
    // 避免共享内存后端时把其它模块的行数断言污染掉。
    {
      name: 'ingest-manifest',
      testMatch: /ingest_manifest\.spec\.ts/,
      dependencies: ['core'],
    },
  ],
})
