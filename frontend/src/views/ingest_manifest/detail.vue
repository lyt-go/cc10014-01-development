<template>
  <section class="page" data-module="ingest_manifest">
    <header class="page-head">
      <div>
        <h2>入库批次清单</h2>
        <p class="page-desc">按批次编号核对原始摘要、映射来源和字段列表；清单只保留一个完整版本，旧版本作为历史快照单独可查。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/raw_ingest" data-testid="back-raw-ingest">返回原始入库</RouterLink>
        <button class="btn" type="button" data-testid="export-manifest" @click="exportManifest">导出清单</button>
        <button class="btn" type="button" data-testid="handover-download" @click="downloadHandover">下载交接文件</button>
        <button class="btn primary" type="button" data-testid="handover-send" @click="sendHandover">文件交接</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="load">
      <label class="filter-item">
        <span>批次编号</span>
        <input v-model="batchNo" data-testid="filter-批次编号" placeholder="按批次编号检索" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <span v-if="errorMessage" class="error-text" data-testid="manifest-error">{{ errorMessage }}</span>
      <span v-if="statusMessage" class="success-text" data-testid="manifest-status">{{ statusMessage }}</span>
    </form>

    <div v-if="manifest" class="manifest-summary" data-testid="manifest-summary">
      <article class="stat-card">
        <span class="stat-label">清单状态</span>
        <strong class="stat-value" data-testid="manifest-state">{{ manifest['清单版本'] === '未生成' ? '待确认' : manifest['清单版本'] }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">原始数据行数</span>
        <strong class="stat-value" data-testid="manifest-rows">{{ manifest['原始数据行数'] }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">原始校验摘要</span>
        <strong class="stat-value mono" data-testid="manifest-checksum">{{ manifest['原始校验摘要'] || '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">清单校验摘要</span>
        <strong class="stat-value mono" data-testid="manifest-manifest-checksum">{{ manifest['清单校验摘要'] || '—' }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">映射来源</span>
        <strong class="stat-value" data-testid="manifest-mapping-source">{{ manifest['映射来源'] || '—' }}</strong>
      </article>
    </div>

    <div v-if="manifest" class="detail-grid">
      <section class="detail-panel" data-testid="manifest-raw">
        <h3>原始摘要</h3>
        <dl>
          <dt>批次编号</dt><dd data-testid="raw-batch-no">{{ manifest['批次编号'] }}</dd>
          <dt>来源系统</dt><dd>{{ manifest['来源系统'] || '—' }}</dd>
          <dt>文件格式</dt><dd>{{ manifest['文件格式'] || '—' }}</dd>
          <dt>登记原始行数</dt><dd>{{ manifest['原始行数'] || '—' }}</dd>
          <dt>解析数据行数</dt><dd data-testid="raw-parsed-rows">{{ manifest['原始数据行数'] }}</dd>
          <dt>原始校验摘要</dt><dd class="mono">{{ manifest['原始校验摘要'] || '—' }}</dd>
          <dt>解析校验摘要</dt><dd class="mono">{{ manifest['解析校验摘要'] || '—' }}</dd>
          <dt>解析来源</dt><dd>{{ manifest['解析来源'] || '—' }}</dd>
          <dt>落地路径</dt><dd>{{ manifest['落地路径'] || '—' }}</dd>
          <dt>批次状态</dt><dd>{{ manifest['状态'] || '—' }}</dd>
          <dt>确认次数</dt><dd data-testid="manifest-confirm-count">{{ manifest['确认次数'] ?? 0 }}</dd>
        </dl>
      </section>

      <section class="detail-panel" data-testid="manifest-mapping">
        <h3>映射来源</h3>
        <dl>
          <dt>规则编号</dt><dd data-testid="mapping-rule-no">{{ manifest['映射规则编号'] || '—' }}</dd>
          <dt>规则版本</dt><dd data-testid="mapping-rule-version">{{ manifest['映射规则版本'] || '—' }}</dd>
          <dt>映射来源</dt><dd>{{ manifest['映射来源'] || '—' }}</dd>
          <dt>冲突状态</dt><dd data-testid="mapping-conflict">{{ manifest['冲突状态'] || '—' }}</dd>
          <dt>冲突说明</dt><dd>{{ manifest['冲突说明'] || '—' }}</dd>
          <dt>补写字段</dt>
          <dd data-testid="mapping-appended">{{ (manifest['补写字段清单'] || []).join('、') || '无' }}</dd>
        </dl>
        <h4>当前映射记录</h4>
        <table class="data-table small" data-testid="mapping-current">
          <thead><tr><th>规则版本</th><th>源字段名</th><th>目标字段名</th><th>来源批次</th></tr></thead>
          <tbody>
            <tr v-if="mapping">
              <td>{{ mapping['规则版本'] }}</td>
              <td>{{ mapping['源字段名'] }}</td>
              <td>{{ mapping['目标字段名'] }}</td>
              <td>{{ mapping['来源批次'] || '—' }}</td>
            </tr>
            <tr v-else><td colspan="4" class="empty-state">暂无映射记录</td></tr>
          </tbody>
        </table>
      </section>
    </div>

    <section v-if="manifest" class="detail-panel" data-testid="manifest-fields">
      <h3>字段列表（{{ fields.length }}）</h3>
      <table class="data-table small">
        <thead><tr><th>#</th><th>源字段</th><th>目标字段</th><th>数据类型</th><th>默认值</th><th>转换函数</th></tr></thead>
        <tbody>
          <tr v-for="(field, index) in fields" :key="field['源字段'] + index" :data-testid="`field-${index}`">
            <td>{{ index + 1 }}</td>
            <td>{{ field['源字段'] }}</td>
            <td>{{ field['目标字段'] }}</td>
            <td>{{ field['数据类型'] || '—' }}</td>
            <td>{{ field['默认值'] || '—' }}</td>
            <td>{{ field['转换函数'] || '—' }}</td>
          </tr>
          <tr v-if="!fields.length"><td colspan="6" class="empty-state">尚无字段列表，确认入库后生成</td></tr>
        </tbody>
      </table>
    </section>

    <section v-if="manifest" class="detail-panel" data-testid="manifest-history">
      <h3>历史快照</h3>
      <table class="data-table small">
        <thead><tr><th>清单版本</th><th>原始数据行数</th><th>原始校验摘要</th><th>映射规则版本</th><th>字段数</th><th>被替换时间</th></tr></thead>
        <tbody>
          <tr v-for="snapshot in history" :key="snapshot['清单版本'] + snapshot['被替换时间']" :data-testid="`history-${snapshot['清单版本']}`">
            <td>{{ snapshot['清单版本'] }}</td>
            <td>{{ snapshot['原始数据行数'] }}</td>
            <td class="mono">{{ snapshot['原始校验摘要'] }}</td>
            <td>{{ snapshot['映射规则版本'] }}</td>
            <td>{{ (snapshot['字段列表'] || []).length }}</td>
            <td>{{ snapshot['被替换时间'] }}</td>
          </tr>
          <tr v-if="!history.length"><td colspan="6" class="empty-state">暂无历史版本，当前即首个完整版本</td></tr>
        </tbody>
      </table>
      <h4 v-if="mappingHistory.length">映射历史快照</h4>
      <table v-if="mappingHistory.length" class="data-table small">
        <thead><tr><th>版本</th><th>原因</th><th>源字段名</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="(snapshot, index) in mappingHistory" :key="index">
            <td>{{ snapshot['版本'] }}</td>
            <td>{{ snapshot['原因'] }}</td>
            <td>{{ snapshot['数据']?.['源字段名'] }}</td>
            <td>{{ snapshot['时间'] }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section v-if="handoverResult" class="detail-panel" data-testid="handover-result">
      <h3>交接结果</h3>
      <pre class="code-block">{{ JSON.stringify(handoverResult, null, 2) }}</pre>
    </section>

    <div v-if="!manifest && !errorMessage" class="empty-state">输入批次编号后查询，或从原始入库列表点击「查看清单」进入</div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

type Manifest = Record<string, any>

const ENDPOINT = '/api/ingest_manifest'
const route = useRoute()
const batchNo = ref(String(route.params.batchNo || route.query.batchNo || ''))
const entryId = ref(String(route.query.entryId || ''))
const manifest = ref<Manifest | null>(null)
const mapping = ref<Manifest | null>(null)
const history = ref<Manifest[]>([])
const mappingHistory = ref<Manifest[]>([])
const errorMessage = ref('')
const statusMessage = ref('')
const handoverResult = ref<any>(null)
const fields = computed(() => (manifest.value?.['字段明细'] || []) as Manifest[])

function blobDownload(content: BlobPart, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function load() {
  errorMessage.value = ''
  statusMessage.value = ''
  handoverResult.value = null
  const target = batchNo.value.trim()
  if (!target && entryId.value) {
    try {
      const response = await request(`${ENDPOINT}/by-entry/${entryId.value}`)
      const payload = await response.json()
      if (!response.ok) throw new Error(payload.detail || '清单读取失败')
      manifest.value = payload.manifest
      history.value = payload.history || []
      batchNo.value = payload.manifest?.['批次编号'] || ''
      mapping.value = null
      mappingHistory.value = []
      await loadByBatch(false)
      return
    } catch (error) {
      manifest.value = null
      errorMessage.value = error instanceof Error ? error.message : '清单读取失败'
      return
    }
  }
  if (!target) {
    manifest.value = null
    errorMessage.value = '请输入批次编号'
    return
  }
  try {
    await loadByBatch(true)
  } catch (error) {
    manifest.value = null
    errorMessage.value = error instanceof Error ? error.message : '清单读取失败'
  }
}

async function loadByBatch(report: boolean) {
  const target = batchNo.value.trim()
  const response = await request(`${ENDPOINT}/${encodeURIComponent(target)}`)
  const payload = await response.json()
  if (!response.ok) {
    if (report) {
      manifest.value = null
      errorMessage.value = payload.detail || `批次 ${target} 的清单不存在`
      return
    }
    // 由 by-entry 拿到的预览清单还没落库，不再按批次号强查
    return
  }
  manifest.value = payload.manifest
  mapping.value = payload.mapping
  history.value = payload.history || []
  mappingHistory.value = payload.mapping_history || []
  statusMessage.value = payload.校验?.message || ''
}

async function exportManifest() {
  errorMessage.value = ''
  const target = batchNo.value.trim()
  try {
    const response = await request(`${ENDPOINT}/export${target ? `?batch_no=${encodeURIComponent(target)}` : ''}`)
    if (!response.ok) throw new Error('导出失败')
    blobDownload(await response.blob(), target ? `ingest_manifest_${target}.csv` : 'ingest_manifest.csv', 'text/csv')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function currentEntryId(): string {
  return entryId.value || String(route.query.entryId || manifest.value?.['entry_id'] || '')
}

async function sendHandover() {
  errorMessage.value = ''
  const id = currentEntryId()
  if (!id) {
    errorMessage.value = '缺少入库批次定位信息，请从原始入库列表进入'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/handover/${id}`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '交接失败')
    handoverResult.value = payload.handover
    statusMessage.value = payload.message
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交接失败'
  }
}

async function downloadHandover() {
  errorMessage.value = ''
  const id = currentEntryId()
  if (!id) {
    errorMessage.value = '缺少入库批次定位信息，请从原始入库列表进入'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/handover/${id}/download`)
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}))
      throw new Error(payload.detail || '交接文件下载失败')
    }
    blobDownload(await response.blob(), `handover_${batchNo.value.trim() || id}.json`, 'application/json')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '交接文件下载失败'
  }
}

watch(() => route.params.batchNo, (value) => {
  if (value && value !== batchNo.value) {
    batchNo.value = String(value)
    entryId.value = String(route.query.entryId || '')
    void load()
  }
})

onMounted(load)
</script>
