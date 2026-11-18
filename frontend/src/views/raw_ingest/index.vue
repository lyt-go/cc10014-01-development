<template>
  <section class="page" data-module="raw_ingest">
    <header class="page-head">
      <div><h2>原始入库</h2><p class="page-desc">维护原始入库批次的输入、执行约束与结果记录，为原始数据入库提供可验证的数据工程流程。</p></div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记入库批次</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出作业清单</button>
        <RouterLink class="btn" to="/ingest_manifest" data-testid="open-manifest-list">入库批次清单</RouterLink>
      </div>
    </header>
    <div class="filter-bar">
      <label><span>开始日期</span><input type="date" v-model="dateFrom" /></label>
      <label><span>结束日期</span><input type="date" v-model="dateTo" /></label>
      <button class="btn" type="button" @click="reload">查询</button>
    </div>
    <div class="timeline-wrap">
      <div v-for="item in timelineItems" :key="item.id" class="timeline-row">
        <span class="timeline-label">{{ item.name || item.label }}</span>
        <div class="timeline-track">
          <div class="timeline-bar" :class="`status-${item.status}`" :style="{ left: barLeft(item) + '%', width: barWidth(item) + '%' }" :data-testid="`bar-${item.id}`" :title="`${item.start} ~ ${item.end}`" @click="selectItem(item)">&nbsp;</div>
        </div>
        <span style="font-size:12px;color:var(--muted);margin-left:8px;">{{ item.start }} ~ {{ item.end }}</span>
        <span class="timeline-actions">
          <button class="link" type="button" :data-testid="`manifest-${item.id}`" @click="openManifest(item)">查看清单</button>
          <button class="link" type="button" :data-testid="`confirm-${item.id}`" @click="openConfirm(item)">确认入库</button>
        </span>
      </div>
      <p v-if="!timelineItems.length" style="text-align:center;color:var(--muted)">暂无作业记录</p>
    </div>
    <div v-if="selectedItem" class="timeline-log" data-testid="timeline-log">
      <strong>{{ selectedItem.name || selectedItem.label }}</strong> — 状态：{{ selectedItem.status }}
      <div style="margin-top:4px;">{{ selectedItem.logs || '暂无日志' }}</div>
    </div>
    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate"><h3>登记入库批次</h3>
        <label v-for="field in columns.slice(0,3)" :key="field" class="create-field"><span>{{ field }}</span><input v-model="createValues[field]" :placeholder="`请输入${field}`" required /></label>
        <label class="create-field"><span>原始文件内容（可选，登记后确认入库时解析行数与校验摘要）</span><textarea v-model="createValues['原始内容']" data-testid="create-raw-content" rows="4" placeholder="粘贴 CSV / JSON 原始内容"></textarea></label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-create">提交</button>
        </div>
      </form>
    </div>

    <div v-if="confirmTarget" class="modal-backdrop" data-testid="confirm-modal">
      <form class="modal" @submit.prevent="submitConfirm">
        <h3>确认入库：{{ confirmTarget.name }}</h3>
        <p class="page-desc">确认后生成入库批次清单，原始行数、校验摘要与字段映射一并落库；解析失败不会推进状态。</p>
        <label class="create-field">
          <span>原始入库文件内容（留空则按批次登记信息生成样例解析摘要）</span>
          <textarea v-model="rawContent" data-testid="confirm-raw-content" rows="6" placeholder="粘贴 CSV / JSON 原始内容"></textarea>
        </label>
        <div class="page-actions" style="justify-content:flex-start">
          <button class="btn" type="button" data-testid="preview-raw" @click="previewRaw">解析预览</button>
          <span v-if="previewMessage" class="preview-text" data-testid="preview-message">{{ previewMessage }}</span>
        </div>
        <div v-if="preview" class="preview-box" data-testid="preview-result">
          <div>字段：{{ (preview.字段列表 || []).join('、') || '—' }}</div>
          <div>数据行数：{{ preview.行数 ?? '—' }}</div>
          <div>校验摘要：{{ preview.校验摘要 || '—' }}</div>
        </div>
        <div class="page-actions">
          <button class="btn" type="button" @click="closeConfirm">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-confirm" :disabled="confirming">
            {{ confirming ? '确认中…' : '确认入库并生成清单' }}
          </button>
        </div>
        <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      </form>
    </div>
  </section>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { request } from '@/api/client'
const ENDPOINT = '/api/raw_ingest'
const columns = ["批次编号", "来源系统", "文件格式", "原始行数", "入库时间", "字符编码", "校验摘要", "落地路径"]
const actions = ["发起校验", "确认入库", "归档批次"]
const router = useRouter()
const timelineItems = ref<any[]>([])
const selectedItem = ref<any>(null)
const dateFrom = ref(''); const dateTo = ref('')
const errorMessage = ref(''); const showCreate = ref(false)
const createValues = ref<Record<string,string>>({})
const allItems = ref<any[]>([])
const confirmTarget = ref<any>(null)
const rawContent = ref('')
const preview = ref<any>(null)
const previewMessage = ref('')
const confirming = ref(false)
function barLeft(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 0; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return ((new Date(item.start).getTime() - min) / range) * 100 }
function barWidth(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 20; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return Math.max(5, ((new Date(item.end).getTime() - new Date(item.start).getTime()) / range) * 100) }
function selectItem(item: any) { selectedItem.value = selectedItem.value?.id === item.id ? null : item }
async function reload() {
  try { const res = await request(ENDPOINT); const data = await res.json(); allItems.value = data.items || []; applyFilter() } catch(e) { errorMessage.value = '加载失败' }
}
function applyFilter() {
  let items = allItems.value
  if(dateFrom.value) items = items.filter((i: any) => i.start >= dateFrom.value)
  if(dateTo.value) items = items.filter((i: any) => i.end <= dateTo.value)
  timelineItems.value = items; selectedItem.value = null
}
function openCreate() { createValues.value = {}; showCreate.value = true }
async function submitCreate() {
  try { const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: createValues.value }) }); if(!res.ok) throw new Error('创建失败'); showCreate.value = false; await reload() } catch(e: any) { errorMessage.value = e.message }
}
function openManifest(item: any) {
  router.push({ path: `/ingest_manifest/${encodeURIComponent(item.name)}`, query: { entryId: String(item.id) } })
}
function openConfirm(item: any) {
  confirmTarget.value = item
  rawContent.value = ''
  preview.value = null
  previewMessage.value = ''
  errorMessage.value = ''
}
function closeConfirm() { confirmTarget.value = null; rawContent.value = ''; preview.value = null; previewMessage.value = '' }
async function previewRaw() {
  previewMessage.value = ''
  preview.value = null
  try {
    const res = await request(`${ENDPOINT}/preview`, {
      method: 'POST',
      body: JSON.stringify({
        content: rawContent.value,
        '批次编号': confirmTarget.value?.name || '',
        '来源系统': confirmTarget.value?.source || '',
        '文件格式': confirmTarget.value?.format || '',
      }),
    })
    const payload = await res.json()
    previewMessage.value = payload.message || ''
    if (payload.parsed) {
      preview.value = { ...payload.parsed, 字段列表: payload.parsed.字段列表 || [] }
    }
  } catch (error) {
    previewMessage.value = error instanceof Error ? error.message : '解析失败'
  }
}
async function submitConfirm() {
  if (!confirmTarget.value) return
  confirming.value = true
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${confirmTarget.value.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action: '确认入库', raw_content: rawContent.value || null }),
    })
    const payload = await res.json()
    if (!res.ok || !payload.ok) throw new Error(payload.message || '确认入库失败')
    const target = confirmTarget.value
    closeConfirm()
    await reload()
    router.push({ path: `/ingest_manifest/${encodeURIComponent(target.name)}`, query: { entryId: String(target.id) } })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认入库失败'
  } finally {
    confirming.value = false
  }
}
function exportRows() { window.open(`${ENDPOINT}/export`, '_blank') }
onMounted(reload)
</script>
