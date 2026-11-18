<template>
  <section class="page" data-module="ingest_manifest">
    <header class="page-head">
      <div>
        <h2>入库批次清单</h2>
        <p class="page-desc">每个批次编号只保留一份完整清单，含原始数据行数、原始校验摘要、映射来源与字段列表；支持导入导出与文件交接。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出清单</button>
        <label class="btn" data-testid="import-trigger">
          导入清单
          <input type="file" accept=".csv,text/csv" data-testid="import-file" hidden @change="importCsv" />
        </label>
        <label class="btn" data-testid="import-handover-trigger">
          接收交接文件
          <input type="file" accept=".json,application/json" data-testid="import-handover-file" hidden @change="importHandover" />
        </label>
        <RouterLink class="btn" to="/raw_ingest" data-testid="back-raw-ingest">返回原始入库</RouterLink>
      </div>
    </header>

    <div v-if="importResult" class="detail-panel" data-testid="import-result">
      <h3>导入结果</h3>
      <p>{{ importResult.message }}</p>
      <table v-if="importResult.imported?.length" class="data-table small">
        <thead><tr><th>批次编号</th><th>结果</th></tr></thead>
        <tbody><tr v-for="item in importResult.imported" :key="item['批次编号']"><td>{{ item['批次编号'] }}</td><td>{{ item['结果'] }}</td></tr></tbody>
      </table>
      <table v-if="importResult.rejected?.length" class="data-table small">
        <thead><tr><th>行号</th><th>拒绝原因</th></tr></thead>
        <tbody><tr v-for="item in importResult.rejected" :key="item['行号']"><td>{{ item['行号'] }}</td><td>{{ item['原因'] }}</td></tr></tbody>
      </table>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>批次编号</span>
        <input v-model="keyword" data-testid="filter-批次编号" placeholder="按批次编号检索" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <span v-if="errorMessage" class="error-text" data-testid="manifest-error">{{ errorMessage }}</span>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>批次编号</th>
          <th>来源系统</th>
          <th>原始数据行数</th>
          <th>原始校验摘要</th>
          <th>清单校验摘要</th>
          <th>清单版本</th>
          <th>映射规则编号</th>
          <th>映射规则版本</th>
          <th>补写字段</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row['批次编号']" :data-testid="`row-${row['批次编号']}`">
          <td>{{ row['批次编号'] }}</td>
          <td>{{ row['来源系统'] || '—' }}</td>
          <td>{{ row['原始数据行数'] }}</td>
          <td class="mono">{{ row['原始校验摘要'] || '—' }}</td>
          <td class="mono">{{ row['清单校验摘要'] || '—' }}</td>
          <td>{{ row['清单版本'] }}</td>
          <td>{{ row['映射规则编号'] || '—' }}</td>
          <td>{{ row['映射规则版本'] || '—' }}</td>
          <td>{{ (row['补写字段清单'] || []).join('、') || '无' }}</td>
          <td class="row-actions">
            <RouterLink class="link" :to="`/ingest_manifest/${encodeURIComponent(row['批次编号'])}`" :data-testid="`detail-${row['批次编号']}`">查看详情</RouterLink>
            <RouterLink class="link" :to="{ path: '/ingest_manifest/' + encodeURIComponent(row['批次编号']), query: row['entry_id'] ? { entryId: String(row['entry_id']) } : {} }" :data-testid="`handover-${row['批次编号']}`">文件交接</RouterLink>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="10" class="empty-state">暂无入库批次清单，可从原始入库列表确认入库后生成</td>
        </tr>
      </tbody>
    </table>
    <footer class="page-foot">
      <span>共 {{ total }} 份在效清单（每个批次编号一份）</span>
      <span v-if="statusMessage" class="success-text" data-testid="manifest-status">{{ statusMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/ingest_manifest'
const rows = ref<Row[]>([])
const total = ref(0)
const keyword = ref('')
const errorMessage = ref('')
const statusMessage = ref('')
const importResult = ref<any>(null)

function blobDownload(content: BlobPart, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function reload() {
  errorMessage.value = ''
  statusMessage.value = ''
  try {
    const response = await request(ENDPOINT)
    if (!response.ok) throw new Error('入库批次清单读取失败')
    const payload = await response.json()
    const items: Row[] = payload.items ?? []
    rows.value = keyword.value.trim()
      ? items.filter((item) => String(item['批次编号'] ?? '').includes(keyword.value.trim()))
      : items
    total.value = rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '入库批次清单读取失败'
  }
}

async function exportRows() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/export`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    blobDownload(await response.blob(), 'ingest_manifest.csv', 'text/csv')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

async function importCsv(event: Event) {
  errorMessage.value = ''
  importResult.value = null
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const content = await file.text()
    const response = await request(`${ENDPOINT}/import`, {
      method: 'POST',
      body: JSON.stringify({ content, source_name: file.name }),
    })
    const payload = await response.json()
    importResult.value = payload
    if (!response.ok) throw new Error(payload.message || '导入失败')
    statusMessage.value = payload.message
    keyword.value = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导入失败'
  } finally {
    input.value = ''
  }
}

async function importHandover(event: Event) {
  errorMessage.value = ''
  importResult.value = null
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const payload = JSON.parse(await file.text())
    const response = await request(`${ENDPOINT}/handover/import`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    const result = await response.json()
    if (!response.ok || !result.ok) throw new Error(result.message || '交接文件接收失败')
    statusMessage.value = result.message
    keyword.value = ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交接文件接收失败'
  } finally {
    input.value = ''
  }
}

onMounted(reload)
</script>
