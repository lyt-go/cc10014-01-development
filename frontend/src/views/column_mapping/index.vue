<template>
  <section class="page" data-module="column_mapping">
    <header class="page-head">
      <div>
        <h2>字段映射管理</h2>
        <p class="page-desc">维护字段映射规则的输入、执行约束与结果记录，为字段映射提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记映射规则</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出字段映射清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" :data-testid="`history-${row.id}`" @click="openHistory(row)">版本历史</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无字段映射数据，可先登记映射规则</td>
        </tr>
      </tbody>
    </table>

    <section v-if="historyRow" class="detail-panel" data-testid="mapping-history-panel">
      <h3>规则版本历史：{{ historyRow['规则编号'] }}</h3>
      <p class="page-desc">
        当前版本 {{ historyRow['规则版本'] }}；旧版本作为历史快照单独可查，清单里的字段顺序以既有映射为准。
      </p>
      <div v-if="historyManifest" class="preview-box" data-testid="mapping-manifest-view">
        引用清单：{{ historyManifest['批次编号'] }}（{{ historyManifest['清单版本'] }}）·
        原始行数 {{ historyManifest['原始数据行数'] }} · 校验 {{ historyManifest['清单校验摘要'] }} ·
        补写字段 {{ (historyManifest['补写字段清单'] || []).join('、') || '无' }}
      </div>
      <table class="data-table small">
        <thead><tr><th>版本</th><th>原因</th><th>源字段名</th><th>目标字段名</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="(item, index) in historyItems" :key="index" :data-testid="`history-item-${index}`">
            <td>{{ item['版本'] }}</td>
            <td>{{ item['原因'] }}</td>
            <td>{{ item['数据']?.['源字段名'] }}</td>
            <td>{{ item['数据']?.['目标字段名'] }}</td>
            <td>{{ item['时间'] }}</td>
          </tr>
          <tr v-if="!historyItems.length"><td colspan="5" class="empty-state">暂无历史版本</td></tr>
        </tbody>
      </table>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记映射规则</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条字段映射记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/column_mapping'
const columns = ["规则编号", "源字段名", "目标字段名", "数据类型", "默认值", "转换函数", "规则版本", "生效日期"]
const actions = ["提交校验", "标记生效", "标记废弃"]
const statuses = ["草稿", "待校验", "已生效", "已废弃"]
const metricNames = ["映射规则总数", "待校验规则", "已生效规则"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const historyRow = ref<Row | null>(null)
const historyItems = ref<Record<string, any>[]>([])
const historyManifest = ref<Record<string, any> | null>(null)
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function openHistory(row: Row) {
  errorMessage.value = ''
  historyRow.value = historyRow.value?.id === row.id ? null : row
  if (!historyRow.value) return
  try {
    const response = await request(`${ENDPOINT}/${row.id}/history`)
    const payload = await response.json()
    if (!response.ok) throw new Error('映射版本历史读取失败')
    historyItems.value = payload.items ?? []
    historyManifest.value = payload.manifest ?? null
  } catch (error) {
    historyItems.value = []
    historyManifest.value = null
    errorMessage.value = error instanceof Error ? error.message : '映射版本历史读取失败'
  }
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'column_mapping.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('字段映射动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段映射操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('映射规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (historyRow.value) historyRow.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段映射列表读取失败'
  }
}

onMounted(reload)
</script>
