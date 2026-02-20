<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import AppIcon from './components/AppIcon.vue'
import { api, resolveApiUrl } from './lib/api'

const activeView = ref('capture')

const notice = reactive({
  type: 'info',
  text: '',
})

const session = reactive({
  status: 'unknown',
  token: null,
  account_name: null,
  account_avatar: null,
  last_error: null,
})

const overview = reactive({
  mps: 0,
  articles: 0,
  latest_article: null,
})

const busy = reactive({
  boot: false,
  session: false,
  qr: false,
  status: false,
  logout: false,
  search: false,
  capture: false,
  captureStatus: false,
  favoriteMp: '',
  mps: false,
  articles: false,
  refreshArticle: '',
  exportArticle: '',
  preview: false,
  dbTables: false,
  dbRows: false,
  dbWrite: false,
  mcp: false,
  mcpWrite: false,
})

const qrImageUrl = ref('')
const isPolling = ref(false)

const savedMpQuickRangeDays = [7, 30, 90, 180]

function toDateInputValue(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    return ''
  }

  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function parseDateInputValue(rawValue) {
  const text = String(rawValue || '').trim()
  const matched = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text)
  if (!matched) {
    return null
  }

  const year = Number(matched[1])
  const month = Number(matched[2])
  const day = Number(matched[3])
  const date = new Date(year, month - 1, day)
  if (
    Number.isNaN(date.getTime()) ||
    date.getFullYear() !== year ||
    date.getMonth() !== month - 1 ||
    date.getDate() !== day
  ) {
    return null
  }

  return date
}

function todayDateInputValue() {
  return toDateInputValue(new Date())
}

function daysAgoDateInputValue(days) {
  const safeDays = Math.max(0, Math.floor(Number(days) || 0))
  const date = new Date()
  date.setDate(date.getDate() - safeDays)
  return toDateInputValue(date)
}

function normalizeRangeDays(rawValue, fallback = 30) {
  const parsed = Math.floor(Number(rawValue))
  if (Number.isFinite(parsed) && parsed >= 1) {
    return Math.min(365, parsed)
  }

  const fallbackParsed = Math.floor(Number(fallback))
  if (Number.isFinite(fallbackParsed) && fallbackParsed >= 1) {
    return Math.min(365, fallbackParsed)
  }

  return 30
}

function buildRecentRangeByDays(days) {
  const rangeDays = normalizeRangeDays(days, 30)
  const date_end = todayDateInputValue()
  const date_start = daysAgoDateInputValue(rangeDays - 1)
  return {
    range_days: rangeDays,
    date_start,
    date_end,
  }
}

function normalizeDateRange(startRaw, endRaw) {
  const fallback = buildRecentRangeByDays(30)
  const parsedStart = parseDateInputValue(startRaw)
  const parsedEnd = parseDateInputValue(endRaw)

  let date_start = parsedStart ? toDateInputValue(parsedStart) : fallback.date_start
  let date_end = parsedEnd ? toDateInputValue(parsedEnd) : fallback.date_end

  if (date_start > date_end) {
    const temp = date_start
    date_start = date_end
    date_end = temp
  }

  return {
    date_start,
    date_end,
  }
}

function rangeDaysBetween(startRaw, endRaw) {
  const range = normalizeDateRange(startRaw, endRaw)
  const start = parseDateInputValue(range.date_start)
  const end = parseDateInputValue(range.date_end)
  if (!start || !end) {
    return 1
  }
  start.setHours(12, 0, 0, 0)
  end.setHours(12, 0, 0, 0)
  const diff = Math.floor((end.getTime() - start.getTime()) / 86400000)
  return Math.max(1, diff + 1)
}

function formatTimestampDate(ts) {
  const value = Number(ts)
  if (!Number.isFinite(value) || value <= 0) {
    return ''
  }
  const date = new Date(value * 1000)
  return toDateInputValue(date)
}

const defaultCaptureRange = buildRecentRangeByDays(30)

const captureForm = reactive({
  keyword: '',
  range_start: defaultCaptureRange.date_start,
  range_end: defaultCaptureRange.date_end,
})

const mpCandidates = ref([])
const mpSearchTotal = ref(0)
const selectedCandidate = ref(null)
const captureJob = ref(null)
const captureResult = ref(null)

const mps = ref([])

const favoriteMps = computed(() => mps.value.filter((item) => Boolean(item.is_favorite)))

const articleFilter = reactive({
  mp_id: '',
  keyword: '',
  offset: 0,
  limit: 12,
})

const articles = ref([])
const articleTotal = ref(0)

const articlePreview = reactive({
  open: false,
  title: '',
  updated_at: '',
  html: '',
  text: '',
  url: '',
})

const dbView = reactive({
  tables: [],
  tableInfos: [],
  table: '',
  tableComment: '',
  columns: [],
  columnDefs: [],
  primaryKeys: [],
  columnComments: {},
  rows: [],
  total: 0,
  offset: 0,
  limit: 50,
  keyword: '',
  searchColumns: [],
  exactFiltersText: '',
})

const dbLimitOptions = [20, 50, 100, 200]
const articleLimitOptions = [12, 24, 48, 96]
const articleLimitStorageKey = 'wemp.console.article.limit'
const savedMpCaptureStorageKey = 'wemp.console.saved.mp.capture'
const customCaptureRangesStorageKey = 'wemp.console.capture.custom.ranges'
const maxCustomCaptureRanges = 12
const defaultSavedMpCaptureConfig = {
  range_days: 30,
}

const savedMpCaptureConfigs = ref({})
const customCaptureRanges = ref([])

const selectedDbRowKeys = ref([])

const dbEditor = reactive({
  open: false,
  mode: 'create',
  payload: '',
  pk: {},
})

const mcp = reactive({
  server_name: '',
  database_path: '',
  config_json: '',
  file_path: '',
  install_steps: [],
  opencode_config_json: '',
  opencode_file_path: '',
  opencode_install_steps: [],
  codex_config_toml: '',
  codex_file_path: '',
  codex_install_steps: [],
  codex_cli_add_command: '',
  claude_mcp_docs_url: 'https://code.claude.com/docs/en/mcp',
  codex_mcp_docs_url: 'https://developers.openai.com/codex/mcp/',
  opencode_docs_url: 'https://opencode.ai/docs/config/',
  opencode_mcp_docs_url: 'https://opencode.ai/docs/mcp-servers/',
  tools: [],
})

const imageReadyMap = reactive({})

const statusMeta = {
  unknown: { label: '未知', tone: 'muted' },
  logged_out: { label: '未登录', tone: 'muted' },
  waiting_scan: { label: '等待扫码', tone: 'warn' },
  scanned: { label: '已扫码待确认', tone: 'warn' },
  logged_in: { label: '已登录', tone: 'good' },
  expired: { label: '二维码过期', tone: 'bad' },
  error: { label: '异常', tone: 'bad' },
}

const viewMeta = {
  capture: { label: '抓取', desc: '登录并抓取文章', icon: 'sparkles' },
  mcp: { label: 'MCP 接入', desc: '接入 Claude / Cursor / OpenCode / Codex', icon: 'plug' },
  database: { label: '数据库', desc: '网页查看表和数据', icon: 'database' },
}

const currentStatus = computed(() => statusMeta[session.status] || statusMeta.unknown)
const activeViewMeta = computed(() => viewMeta[activeView.value] || viewMeta.capture)
const canCapture = computed(() => session.status === 'logged_in')

const captureJobStatusMeta = {
  queued: { label: '排队中', tone: 'warn' },
  running: { label: '抓取中', tone: 'warn' },
  success: { label: '已完成', tone: 'good' },
  failed: { label: '失败', tone: 'bad' },
}

const currentCaptureJobStatus = computed(() => {
  const status = captureJob.value?.status
  if (!status) {
    return { label: '未开始', tone: 'muted' }
  }
  return captureJobStatusMeta[status] || { label: status, tone: 'muted' }
})

const hasActiveCaptureJob = computed(() => {
  const status = captureJob.value?.status
  return status === 'queued' || status === 'running'
})

const tokenPreview = computed(() => {
  if (!session.token) {
    return '-'
  }
  if (session.token.length <= 12) {
    return session.token
  }
  return `${session.token.slice(0, 8)}...${session.token.slice(-4)}`
})

const summaryCards = computed(() => [
  { label: '公众号', value: overview.mps, icon: 'users' },
  { label: '文章', value: overview.articles, icon: 'file-text' },
  { label: '状态', value: currentStatus.value.label, icon: 'shield' },
])

const captureFormRange = computed(() => {
  return normalizeDateRange(captureForm.range_start, captureForm.range_end)
})

const estimatedCaptureRange = computed(() => {
  const range = captureFormRange.value
  const days = rangeDaysBetween(range.date_start, range.date_end)
  return `${range.date_start} ~ ${range.date_end}（共 ${days} 天）`
})

function captureDateRangeText(startTs, endTs) {
  const start = formatTimestampDate(startTs)
  const end = formatTimestampDate(endTs)
  if (start && end) {
    return `${start} ~ ${end}`
  }
  return start || end || ''
}

function captureTargetText(payload) {
  if (!payload) {
    return ''
  }

  const dateRangeText = captureDateRangeText(payload.start_ts, payload.end_ts)
  if (dateRangeText) {
    return `范围 ${dateRangeText}`
  }

  const requested = Number(payload.requested_count || 0)
  if (requested > 0) {
    return `目标 ${requested} 条`
  }

  return '按时间范围'
}

const captureJobProgressText = computed(() => {
  if (!captureJob.value) {
    return ''
  }

  const targetText = captureTargetText(captureJob.value)
  const created = Number(captureJob.value.created || 0)
  const scanned = Number(captureJob.value.scanned_pages || 0)
  const maxPages = Number(captureJob.value.max_pages || 0)
  const duplicated = Number(captureJob.value.duplicates_skipped || 0)
  const pageText = maxPages > 0 ? `${scanned}/${maxPages}` : `${scanned}`
  return `${targetText} · 新增 ${created} · 跳过重复 ${duplicated} · 扫描进度 ${pageText}`
})

const dbRangeText = computed(() => {
  if (dbView.total <= 0) {
    return '0 / 0'
  }
  const start = dbView.offset + 1
  const end = Math.min(dbView.offset + dbView.limit, dbView.total)
  return `${start}-${end} / ${dbView.total}`
})

const dbSelectableRows = computed(() => dbView.rows.filter((row) => Boolean(extractDbPrimaryKey(row))))

const selectedDbPkList = computed(() => {
  return dbView.rows
    .filter((row) => isDbRowSelected(row))
    .map((row) => extractDbPrimaryKey(row))
    .filter((pk) => Boolean(pk))
})

const selectedDbCount = computed(() => selectedDbPkList.value.length)

const isDbAllSelected = computed(() => {
  if (!dbSelectableRows.value.length) {
    return false
  }
  return dbSelectableRows.value.every((row) => isDbRowSelected(row))
})

const dbPrimaryKeyText = computed(() => {
  if (!dbView.primaryKeys.length) {
    return '无（只读）'
  }
  return dbView.primaryKeys.join(', ')
})

const dbEditorTitle = computed(() => (dbEditor.mode === 'create' ? '新增记录' : '编辑记录'))

const articlePageCount = computed(() => {
  const limit = Math.max(1, Number(articleFilter.limit) || 12)
  const total = Math.max(0, Number(articleTotal.value) || 0)
  return Math.max(1, Math.ceil(total / limit))
})

const articleCurrentPage = computed(() => {
  const limit = Math.max(1, Number(articleFilter.limit) || 12)
  return Math.floor(Math.max(0, articleFilter.offset) / limit) + 1
})

const articleRangeText = computed(() => {
  const total = Math.max(0, Number(articleTotal.value) || 0)
  if (total <= 0) {
    return '0 / 0'
  }
  const start = articleFilter.offset + 1
  const end = Math.min(articleFilter.offset + articleFilter.limit, total)
  return `${start}-${end} / ${total}`
})

const articlePageItems = computed(() => {
  const total = articlePageCount.value
  const current = articleCurrentPage.value

  if (total <= 7) {
    return Array.from({ length: total }, (_, idx) => idx + 1)
  }

  const pages = new Set([1, total, current - 1, current, current + 1])

  if (current <= 3) {
    pages.add(2)
    pages.add(3)
    pages.add(4)
  }

  if (current >= total - 2) {
    pages.add(total - 1)
    pages.add(total - 2)
    pages.add(total - 3)
  }

  const sorted = [...pages].filter((page) => page >= 1 && page <= total).sort((a, b) => a - b)
  const items = []

  for (let index = 0; index < sorted.length; index += 1) {
    if (index > 0 && sorted[index] - sorted[index - 1] > 1) {
      items.push('...')
    }
    items.push(sorted[index])
  }

  return items
})

const qrPlaceholder =
  'https://images.unsplash.com/photo-1563986768609-322da13575f3?auto=format&fit=crop&w=720&q=60'
const wechatImageHostPattern = /(?:qpic\.cn|qlogo\.cn|weixin\.qq\.com|wx\.qlogo\.cn)/i

let pollTimer = null
let captureJobPollTimer = null
let noticeTimer = null
let captureJobRefreshPending = false
let lastCaptureJobNoticeId = ''

function setNotice(type, text, timeout = 3600) {
  notice.type = type
  notice.text = text
  if (noticeTimer) {
    clearTimeout(noticeTimer)
  }
  if (timeout > 0) {
    noticeTimer = setTimeout(() => {
      notice.text = ''
    }, timeout)
  }
}

function normalizeArticleLimit(rawValue) {
  const parsed = Math.floor(Number(rawValue))
  if (articleLimitOptions.includes(parsed)) {
    return parsed
  }
  return articleLimitOptions[0]
}

function loadArticleLimitPreference() {
  try {
    const saved = localStorage.getItem(articleLimitStorageKey)
    if (saved !== null) {
      articleFilter.limit = normalizeArticleLimit(saved)
    }
  } catch {
    articleFilter.limit = normalizeArticleLimit(articleFilter.limit)
  }
}

function saveArticleLimitPreference() {
  try {
    localStorage.setItem(articleLimitStorageKey, String(articleFilter.limit))
  } catch {}
}

function customCaptureRangeKey(range) {
  if (!range) {
    return ''
  }
  return `${range.date_start || ''}|${range.date_end || ''}`
}

function normalizeCustomCaptureRange(rawRange = {}) {
  const source = rawRange && typeof rawRange === 'object' ? rawRange : {}
  const normalized = normalizeDateRange(source.date_start, source.date_end)
  const rawId = typeof source.id === 'string' ? source.id.trim() : ''

  return {
    id: rawId || `range_${normalized.date_start}_${normalized.date_end}`,
    date_start: normalized.date_start,
    date_end: normalized.date_end,
  }
}

function loadCustomCaptureRangesPreference() {
  try {
    const raw = localStorage.getItem(customCaptureRangesStorageKey)
    if (!raw) {
      return
    }

    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return
    }

    const seen = new Set()
    const normalized = []
    for (const item of parsed) {
      const row = normalizeCustomCaptureRange(item)
      const key = customCaptureRangeKey(row)
      if (!key || seen.has(key)) {
        continue
      }
      seen.add(key)
      normalized.push(row)
      if (normalized.length >= maxCustomCaptureRanges) {
        break
      }
    }

    customCaptureRanges.value = normalized
  } catch {
    customCaptureRanges.value = []
  }
}

function saveCustomCaptureRangesPreference() {
  try {
    localStorage.setItem(customCaptureRangesStorageKey, JSON.stringify(customCaptureRanges.value))
  } catch {}
}

function customCaptureRangeLabel(range) {
  const normalized = normalizeCustomCaptureRange(range)
  const days = rangeDaysBetween(normalized.date_start, normalized.date_end)
  return `${normalized.date_start} ~ ${normalized.date_end}（${days} 天）`
}

function isCustomCaptureRangeActive(range) {
  const current = captureFormRange.value
  const normalized = normalizeCustomCaptureRange(range)
  return current.date_start === normalized.date_start && current.date_end === normalized.date_end
}

function applyCustomCaptureRange(range) {
  const normalized = normalizeCustomCaptureRange(range)
  captureForm.range_start = normalized.date_start
  captureForm.range_end = normalized.date_end
}

function saveCurrentCaptureRangeAsCustom() {
  const normalized = normalizeCustomCaptureRange(captureFormRange.value)
  const key = customCaptureRangeKey(normalized)
  const currentList = customCaptureRanges.value
  const existingIndex = currentList.findIndex((item) => customCaptureRangeKey(item) === key)

  if (existingIndex === 0) {
    setNotice('info', '该时间范围已在自定义列表')
    return
  }

  const existing = existingIndex >= 0 ? normalizeCustomCaptureRange(currentList[existingIndex]) : null
  const nextItem =
    existing ||
    {
      ...normalized,
      id: `range_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
    }

  const next = [
    nextItem,
    ...currentList.filter((item, index) => {
      if (index === existingIndex) {
        return false
      }
      return customCaptureRangeKey(item) !== key
    }),
  ].slice(0, maxCustomCaptureRanges)

  customCaptureRanges.value = next
  saveCustomCaptureRangesPreference()
  setNotice('success', '已保存到自定义时间范围')
}

function removeCustomCaptureRange(rangeId) {
  if (!rangeId) {
    return
  }
  const next = customCaptureRanges.value.filter((item) => item.id !== rangeId)
  if (next.length === customCaptureRanges.value.length) {
    return
  }
  customCaptureRanges.value = next
  saveCustomCaptureRangesPreference()
}

function clearCustomCaptureRanges() {
  if (!customCaptureRanges.value.length) {
    return
  }
  customCaptureRanges.value = []
  saveCustomCaptureRangesPreference()
}

function normalizeSavedMpCaptureConfig(rawConfig = {}, fallbackConfig = defaultSavedMpCaptureConfig) {
  const source = rawConfig && typeof rawConfig === 'object' ? rawConfig : {}

  return {
    range_days: normalizeRangeDays(source.range_days, fallbackConfig.range_days),
  }
}

function loadSavedMpCapturePreference() {
  try {
    const raw = localStorage.getItem(savedMpCaptureStorageKey)
    if (!raw) {
      return
    }

    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return
    }

    const normalized = {}
    for (const [mpId, config] of Object.entries(parsed)) {
      if (!mpId) {
        continue
      }
      normalized[mpId] = normalizeSavedMpCaptureConfig(config, defaultSavedMpCaptureConfig)
    }

    savedMpCaptureConfigs.value = normalized
  } catch {
    savedMpCaptureConfigs.value = {}
  }
}

function saveSavedMpCapturePreference() {
  try {
    localStorage.setItem(savedMpCaptureStorageKey, JSON.stringify(savedMpCaptureConfigs.value))
  } catch {}
}

function getSavedMpCaptureConfig(mpId) {
  if (!mpId) {
    return { ...defaultSavedMpCaptureConfig }
  }

  const config = savedMpCaptureConfigs.value[mpId]
  if (!config) {
    return { ...defaultSavedMpCaptureConfig }
  }

  return normalizeSavedMpCaptureConfig(config, defaultSavedMpCaptureConfig)
}

function setSavedMpCaptureConfig(mpId, patch = {}) {
  if (!mpId) {
    return
  }

  const current = getSavedMpCaptureConfig(mpId)
  const next = normalizeSavedMpCaptureConfig(
    {
      range_days: patch.range_days === undefined ? current.range_days : patch.range_days,
    },
    current,
  )

  savedMpCaptureConfigs.value = {
    ...savedMpCaptureConfigs.value,
    [mpId]: next,
  }
  saveSavedMpCapturePreference()
}

function setSavedMpCaptureDays(mpId, nextDays) {
  setSavedMpCaptureConfig(mpId, { range_days: nextDays })
}

function savedMpCaptureDays(item) {
  return getSavedMpCaptureConfig(item?.id).range_days
}

function savedMpCaptureOptionList(item) {
  const current = savedMpCaptureDays(item)
  if (savedMpQuickRangeDays.includes(current)) {
    return savedMpQuickRangeDays
  }
  return [...savedMpQuickRangeDays, current].sort((a, b) => a - b)
}

function savedMpCaptureButtonText(item) {
  const rangeDays = savedMpCaptureDays(item)
  return `抓取近 ${rangeDays} 天`
}

function resolveCaptureJobOptions(options = null) {
  const fallbackRange = captureFormRange.value

  let date_start = fallbackRange.date_start
  let date_end = fallbackRange.date_end
  let range_days = rangeDaysBetween(date_start, date_end)

  if (options && (options.date_start !== undefined || options.date_end !== undefined)) {
    const normalized = normalizeDateRange(
      options.date_start === undefined ? fallbackRange.date_start : options.date_start,
      options.date_end === undefined ? fallbackRange.date_end : options.date_end,
    )
    date_start = normalized.date_start
    date_end = normalized.date_end
    range_days = rangeDaysBetween(date_start, date_end)
  } else if (options && options.range_days !== undefined && options.range_days !== null) {
    const relativeRange = buildRecentRangeByDays(options.range_days)
    date_start = relativeRange.date_start
    date_end = relativeRange.date_end
    range_days = relativeRange.range_days
  }

  return {
    range_days,
    date_start,
    date_end,
  }
}

function applyCapturePresetRangeDays(days) {
  const range = buildRecentRangeByDays(days)
  captureForm.range_start = range.date_start
  captureForm.range_end = range.date_end
}

function applySession(data = {}) {
  session.status = data.status || session.status || 'unknown'
  session.token = data.token ?? session.token
  session.account_name = data.account_name ?? session.account_name
  session.account_avatar = data.account_avatar ?? session.account_avatar
  session.last_error = data.last_error ?? null
}

function proxiedImageUrl(rawUrl) {
  if (!rawUrl) {
    return qrPlaceholder
  }

  let url = String(rawUrl).trim()
  if (!url) {
    return qrPlaceholder
  }

  if (url.startsWith('data:') || url.startsWith('blob:')) {
    return url
  }

  if (url.startsWith('/api/') || url.startsWith('/static/')) {
    return url
  }

  if (url.startsWith('//')) {
    url = `https:${url}`
  }

  if (!/^https?:\/\//i.test(url)) {
    return url
  }

  if (!wechatImageHostPattern.test(url)) {
    return url
  }

  return resolveApiUrl(`/assets/image?url=${encodeURIComponent(url)}`)
}

function markImageReady(url) {
  if (!url) {
    return
  }
  imageReadyMap[url] = true
}

function isImageReady(url) {
  return Boolean(imageReadyMap[url])
}

function onImageError(event, url) {
  markImageReady(url)
  const target = event?.target
  if (target) {
    target.src = qrPlaceholder
  }
}

function imageCount(article) {
  try {
    const parsed = JSON.parse(article?.images_json || '[]')
    return Array.isArray(parsed) ? parsed.length : 0
  } catch {
    return 0
  }
}

function formatDateTime(value) {
  if (!value) {
    return '-'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '-'
  }
  return date.toLocaleString('zh-CN')
}

function sanitizeInlineStyle(styleText = '') {
  return String(styleText)
    .replace(/visibility\s*:\s*hidden\s*;?/gi, '')
    .replace(/opacity\s*:\s*0(?:\.0+)?\s*;?/gi, '')
    .replace(/display\s*:\s*none\s*;?/gi, '')
    .replace(/;\s*;/g, ';')
    .trim()
    .replace(/^;|;$/g, '')
}

function normalizePreviewHtml(rawHtml = '') {
  if (!rawHtml) {
    return ''
  }

  let html = String(rawHtml)
    .replace(/<script[\s\S]*?<\/script>/gi, '')
    .replace(/<style[\s\S]*?<\/style>/gi, '')

  if (typeof window === 'undefined' || !window.DOMParser) {
    return html
      .replace(/visibility\s*:\s*hidden\s*;?/gi, '')
      .replace(/opacity\s*:\s*0(?:\.0+)?\s*;?/gi, '')
      .replace(/display\s*:\s*none\s*;?/gi, '')
  }

  const parser = new window.DOMParser()
  const doc = parser.parseFromString(`<div id="__preview_root">${html}</div>`, 'text/html')
  const root = doc.getElementById('__preview_root')
  if (!root) {
    return html
  }

  const nodes = [root, ...root.querySelectorAll('*')]
  for (const node of nodes) {
    if (!(node instanceof window.HTMLElement)) {
      continue
    }

    node.removeAttribute('hidden')

    const style = node.getAttribute('style')
    if (style) {
      const nextStyle = sanitizeInlineStyle(style)
      if (nextStyle) {
        node.setAttribute('style', nextStyle)
      } else {
        node.removeAttribute('style')
      }
    }

    if (node.tagName === 'IMG') {
      const rawSrc =
        node.getAttribute('src') || node.getAttribute('data-src') || node.getAttribute('data-ori-src')
      if (rawSrc) {
        node.setAttribute('src', proxiedImageUrl(rawSrc))
      }
    }
  }

  return root.innerHTML
}

function visibleTextLengthFromHtml(rawHtml = '') {
  if (!rawHtml) {
    return 0
  }
  const text = String(rawHtml)
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  return text.length
}

function startPolling() {
  if (pollTimer) {
    return
  }
  isPolling.value = true
  pollTimer = setInterval(async () => {
    await checkAuthStatus(true)
  }, 2400)
}

function stopPolling() {
  isPolling.value = false
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function stopCaptureJobPolling() {
  if (captureJobPollTimer) {
    clearInterval(captureJobPollTimer)
    captureJobPollTimer = null
  }
}

function buildCaptureResultFromJob(job) {
  return {
    mp: {
      id: job?.mp_id || '',
      nickname: job?.mp_nickname || '目标公众号',
    },
    sync: {
      created: Number(job?.created || 0),
      updated: Number(job?.updated || 0),
      content_updated: Number(job?.content_updated || 0),
      duplicates_skipped: Number(job?.duplicates_skipped || 0),
      scanned_pages: Number(job?.scanned_pages || 0),
      pages: Number(job?.max_pages || 0),
    },
    requested_count: Number(job?.requested_count || 0),
    start_ts: Number(job?.start_ts || 0),
    end_ts: Number(job?.end_ts || 0),
    reached_target: Boolean(job?.reached_target),
  }
}

async function applyCaptureJobUpdate(job, silent = false) {
  captureJob.value = job || null

  if (!job) {
    stopCaptureJobPolling()
    return
  }

  if (job.status === 'queued' || job.status === 'running') {
    if (!captureJobPollTimer) {
      captureJobPollTimer = setInterval(() => {
        refreshCaptureJob(true)
      }, 2800)
    }
    return
  }

  stopCaptureJobPolling()

  if (job.status === 'success') {
    captureResult.value = buildCaptureResultFromJob(job)
    if (job.mp_id) {
      articleFilter.mp_id = job.mp_id
    }
    await Promise.all([loadOverview(), loadMps(), loadArticles()])

    if (!silent && lastCaptureJobNoticeId !== job.id) {
      const requested = Number(job.requested_count || 0)
      if (job.reached_target || requested <= 0) {
        setNotice('success', '后台抓取完成，结果已更新')
      } else {
        setNotice(
          'warn',
          `后台抓取完成：目标 ${requested} 条，实际新增 ${job.created} 条（可能源数据不足）`,
        )
      }
      lastCaptureJobNoticeId = job.id
    }
    return
  }

  if (job.status === 'failed' && !silent && lastCaptureJobNoticeId !== job.id) {
    setNotice('error', job.error || '后台抓取失败')
    lastCaptureJobNoticeId = job.id
  }
}

async function refreshCaptureJob(silent = false) {
  if (!captureJob.value?.id || captureJobRefreshPending) {
    return
  }

  captureJobRefreshPending = true
  busy.captureStatus = !silent
  try {
    const data = await api(`/mps/sync/jobs/${encodeURIComponent(captureJob.value.id)}`)
    await applyCaptureJobUpdate(data, silent)
  } catch (err) {
    if (!silent) {
      setNotice('error', err.message || '读取抓取任务状态失败')
    }
  } finally {
    busy.captureStatus = false
    captureJobRefreshPending = false
  }
}

async function loadLatestCaptureJob() {
  try {
    const data = await api('/mps/sync/jobs?offset=0&limit=1')
    const latest = Array.isArray(data?.list) ? data.list[0] : null
    if (!latest) {
      return
    }
    await applyCaptureJobUpdate(latest, true)
    if (latest.status === 'queued' || latest.status === 'running') {
      await refreshCaptureJob(true)
    }
  } catch {
    // 抓取任务状态非阻断
  }
}

async function loadSession() {
  busy.session = true
  try {
    const data = await api('/auth/session')
    applySession(data)
    if (session.status === 'logged_in') {
      stopPolling()
    }
  } catch (err) {
    setNotice('error', err.message || '读取登录态失败')
  } finally {
    busy.session = false
  }
}

async function loadOverview() {
  try {
    const data = await api('/ops/overview')
    if (data?.auth) {
      applySession(data.auth)
    }
    overview.mps = data?.counts?.mps || 0
    overview.articles = data?.counts?.articles || 0
    overview.latest_article = data?.latest_article || null
  } catch {
    // overview非阻断
  }
}

async function requestQrCode() {
  busy.qr = true
  try {
    const data = await api('/auth/qr')
    qrImageUrl.value = resolveApiUrl(data.qr_image_url)
    setNotice('info', '二维码已生成，请在手机确认登录')
    startPolling()
  } catch (err) {
    setNotice('error', err.message || '获取二维码失败')
  } finally {
    busy.qr = false
  }
}

async function checkAuthStatus(silent = false) {
  busy.status = !silent
  try {
    const data = await api('/auth/status')
    applySession(data)

    if (session.status === 'logged_in') {
      stopPolling()
      await Promise.all([loadOverview(), loadMps(), loadArticles()])
      if (!silent) {
        setNotice('success', '登录成功，准备就绪')
      }
      return
    }

    if (session.status === 'expired') {
      stopPolling()
      setNotice('warn', '二维码过期，请重新获取')
      return
    }

    if (!silent) {
      if (session.status === 'error' && session.last_error) {
        setNotice('error', session.last_error)
      } else {
        setNotice('info', `当前状态：${currentStatus.value.label}`)
      }
    }
  } catch (err) {
    if (!silent) {
      setNotice('error', err.message || '状态检查失败')
    }
  } finally {
    busy.status = false
  }
}

async function logout() {
  busy.logout = true
  try {
    await api('/auth/logout', { method: 'POST' })
    stopPolling()
    qrImageUrl.value = ''
    applySession({
      status: 'logged_out',
      token: null,
      account_name: null,
      account_avatar: null,
      last_error: null,
    })
    setNotice('info', '已注销登录会话')
  } catch (err) {
    setNotice('error', err.message || '注销失败')
  } finally {
    busy.logout = false
  }
}

async function searchCandidates() {
  if (!captureForm.keyword.trim()) {
    setNotice('warn', '请输入公众号关键词')
    return
  }
  if (!canCapture.value) {
    setNotice('warn', '请先扫码登录')
    return
  }

  busy.search = true
  try {
    const query = new URLSearchParams({
      keyword: captureForm.keyword.trim(),
      offset: '0',
      limit: '10',
    })
    const data = await api(`/mps/search?${query.toString()}`)
    mpCandidates.value = data.list || []
    mpSearchTotal.value = data.total || mpCandidates.value.length
    selectedCandidate.value = mpCandidates.value[0] || null

    if (!mpCandidates.value.length) {
      setNotice('warn', '未搜到公众号，换个关键词试试')
      return
    }

    setNotice('info', '请选择并确认要抓取的公众号')
  } catch (err) {
    setNotice('error', err.message || '搜索失败')
  } finally {
    busy.search = false
  }
}

function chooseCandidate(item) {
  selectedCandidate.value = item
}

async function submitCaptureJobByMpId(mpId, options = null) {
  const resolved = resolveCaptureJobOptions(options)
  const job = await api(`/mps/${encodeURIComponent(mpId)}/sync/jobs`, {
    method: 'POST',
    body: {
      date_start: resolved.date_start,
      date_end: resolved.date_end,
    },
  })

  captureResult.value = null
  articleFilter.mp_id = mpId
  await applyCaptureJobUpdate(job, true)
  await Promise.all([loadOverview(), loadMps()])
  return job
}

async function confirmCapture() {
  if (!selectedCandidate.value) {
    setNotice('warn', '请先选择公众号')
    return
  }
  if (!canCapture.value) {
    setNotice('warn', '请先扫码登录')
    return
  }
  if (hasActiveCaptureJob.value) {
    setNotice('warn', '已有抓取任务在执行，请等待完成')
    return
  }

  busy.capture = true
  try {
    const candidate = selectedCandidate.value
    const captureOptions = resolveCaptureJobOptions({
      date_start: captureForm.range_start,
      date_end: captureForm.range_end,
    })
    const saved = await api('/mps', {
      method: 'POST',
      body: {
        fakeid: candidate.fakeid,
        nickname: candidate.nickname || '未命名公众号',
        alias: candidate.alias || null,
        avatar: candidate.avatar || null,
        intro: candidate.intro || null,
        biz: candidate.biz || null,
      },
    })

    await submitCaptureJobByMpId(saved.id, captureOptions)
    setSavedMpCaptureConfig(saved.id, { range_days: captureOptions.range_days })
    setNotice('info', '抓取任务已提交到后台，页面可关闭或切换，稍后回来查看结果')
  } catch (err) {
    setNotice('error', err.message || '抓取失败')
  } finally {
    busy.capture = false
  }
}

async function captureSavedMp(item) {
  if (!item?.id) {
    setNotice('warn', '公众号信息不完整')
    return
  }
  if (!canCapture.value) {
    setNotice('warn', '请先扫码登录')
    return
  }
  if (hasActiveCaptureJob.value) {
    setNotice('warn', '已有抓取任务在执行，请等待完成')
    return
  }

  busy.capture = true
  try {
    const captureOptions = resolveCaptureJobOptions({
      range_days: savedMpCaptureDays(item),
    })
    await submitCaptureJobByMpId(item.id, captureOptions)
    setSavedMpCaptureConfig(item.id, { range_days: captureOptions.range_days })
    setNotice('info', `已为 ${item.nickname || '目标公众号'} 提交抓取任务（近 ${captureOptions.range_days} 天）`)
  } catch (err) {
    setNotice('error', err.message || '提交抓取失败')
  } finally {
    busy.capture = false
  }
}

async function toggleFavoriteMp(item) {
  if (!item?.id) {
    return
  }

  const nextFavorite = !Boolean(item.is_favorite)
  busy.favoriteMp = item.id
  try {
    const updated = await api(`/mps/${encodeURIComponent(item.id)}/favorite`, {
      method: 'PATCH',
      body: { is_favorite: nextFavorite },
    })

    const index = mps.value.findIndex((mp) => mp.id === item.id)
    if (index >= 0) {
      mps.value[index] = updated
    }
    mps.value = [...mps.value].sort((a, b) => {
      const af = a.is_favorite ? 1 : 0
      const bf = b.is_favorite ? 1 : 0
      if (bf !== af) {
        return bf - af
      }
      const at = a.last_used_at ? new Date(a.last_used_at).getTime() : 0
      const bt = b.last_used_at ? new Date(b.last_used_at).getTime() : 0
      return bt - at
    })

    setNotice('success', nextFavorite ? '已设为常用公众号' : '已取消常用公众号')
  } catch (err) {
    setNotice('error', err.message || '更新常用状态失败')
  } finally {
    busy.favoriteMp = ''
  }
}

async function loadMps() {
  busy.mps = true
  try {
    const data = await api('/mps?offset=0&limit=100')
    mps.value = data.list || []
  } catch (err) {
    setNotice('error', err.message || '加载公众号失败')
  } finally {
    busy.mps = false
  }
}

async function loadArticles() {
  busy.articles = true
  try {
    const query = new URLSearchParams({
      offset: String(articleFilter.offset || 0),
      limit: String(articleFilter.limit || 12),
    })

    if (articleFilter.mp_id) {
      query.set('mp_id', articleFilter.mp_id)
    }
    if (articleFilter.keyword.trim()) {
      query.set('keyword', articleFilter.keyword.trim())
    }

    const data = await api(`/articles?${query.toString()}`)
    articles.value = data.list || []
    articleTotal.value = data.total || 0

    if (!articles.value.length && articleTotal.value > 0 && articleFilter.offset > 0) {
      const maxOffset = Math.max(0, (Math.ceil(articleTotal.value / articleFilter.limit) - 1) * articleFilter.limit)
      if (maxOffset !== articleFilter.offset) {
        articleFilter.offset = maxOffset
        await loadArticles()
      }
    }
  } catch (err) {
    setNotice('error', err.message || '加载文章失败')
  } finally {
    busy.articles = false
  }
}

function applyArticleFilters() {
  if (busy.articles) {
    return
  }
  articleFilter.offset = 0
  loadArticles()
}

function goToArticlePage(page) {
  if (busy.articles) {
    return
  }
  const target = Math.min(articlePageCount.value, Math.max(1, Number(page) || 1))
  const nextOffset = (target - 1) * articleFilter.limit

  if (nextOffset === articleFilter.offset) {
    return
  }

  articleFilter.offset = nextOffset
  loadArticles()
}

function changeArticlePage(step) {
  goToArticlePage(articleCurrentPage.value + step)
}

function changeArticlePageSize() {
  if (busy.articles) {
    return
  }
  articleFilter.limit = normalizeArticleLimit(articleFilter.limit)
  saveArticleLimitPreference()
  articleFilter.offset = 0
  loadArticles()
}

async function openArticlePreview(article) {
  articlePreview.open = true
  busy.preview = true
  articlePreview.title = article.title
  articlePreview.updated_at = article.updated_at
  articlePreview.url = article.url
  articlePreview.html = ''
  articlePreview.text = ''

  try {
    const data = await api(`/articles/${article.id}`)
    articlePreview.title = data.title || article.title
    articlePreview.updated_at = data.updated_at || article.updated_at
    articlePreview.url = data.url || article.url
    const normalizedHtml = normalizePreviewHtml(data.content_html || '')
    const plainText = (data.content_text || '').trim()
    const htmlTextLen = visibleTextLengthFromHtml(normalizedHtml)

    if (htmlTextLen < 20 && plainText) {
      articlePreview.html = ''
      articlePreview.text = plainText
    } else {
      articlePreview.html = normalizedHtml
      articlePreview.text = plainText
    }
  } catch (err) {
    setNotice('error', err.message || '预览加载失败')
  } finally {
    busy.preview = false
  }
}

function closeArticlePreview() {
  articlePreview.open = false
}

async function refreshArticle(article) {
  busy.refreshArticle = article.id
  try {
    const refreshed = await api(`/articles/${article.id}/refresh`, { method: 'POST' })
    const idx = articles.value.findIndex((item) => item.id === article.id)
    if (idx >= 0) {
      articles.value[idx] = refreshed
    }
    setNotice('success', '正文已刷新')
  } catch (err) {
    setNotice('error', err.message || '刷新失败')
  } finally {
    busy.refreshArticle = ''
  }
}

async function exportArticle(article, format = 'pdf') {
  busy.exportArticle = `${article.id}:${format}`
  try {
    const data = await api(`/exports/article/${article.id}`, {
      method: 'POST',
      body: { format },
    })
    window.open(resolveApiUrl(data.download_url), '_blank', 'noopener')
    setNotice('success', `${format.toUpperCase()} 导出成功`)
  } catch (err) {
    setNotice('error', err.message || '导出失败')
  } finally {
    busy.exportArticle = ''
  }
}

async function loadDbTables() {
  busy.dbTables = true
  try {
    const data = await api('/ops/db/tables')
    dbView.tables = data.tables || []
    if (Array.isArray(data.table_infos) && data.table_infos.length) {
      dbView.tableInfos = data.table_infos
    } else {
      dbView.tableInfos = dbView.tables.map((name) => ({
        name,
        row_count: 0,
      }))
    }
    if (!dbView.table && dbView.tables.length) {
      dbView.table = dbView.tables.includes('articles') ? 'articles' : dbView.tables[0]
    }
  } catch (err) {
    setNotice('error', err.message || '读取数据表失败')
  } finally {
    busy.dbTables = false
  }
}

async function loadDbRows() {
  if (!dbView.table) {
    return
  }
  busy.dbRows = true
  try {
    const query = new URLSearchParams({
      offset: String(dbView.offset),
      limit: String(dbView.limit),
    })

    if (dbView.keyword.trim()) {
      query.set('keyword', dbView.keyword.trim())
    }
    if (dbView.searchColumns.length) {
      query.set('search_columns', dbView.searchColumns.join(','))
    }
    if (dbView.exactFiltersText.trim()) {
      query.set('exact_filters', dbView.exactFiltersText.trim())
    }

    const data = await api(`/ops/db/table/${encodeURIComponent(dbView.table)}?${query.toString()}`)
    dbView.tableComment = data.table_comment || ''
    dbView.columns = data.columns || []
    dbView.columnDefs = data.column_defs || []
    dbView.primaryKeys = data.primary_keys || []
    dbView.columnComments = data.column_comments || {}
    dbView.rows = data.rows || []
    dbView.total = data.total || 0
    dbView.searchColumns = data.search_columns || dbView.searchColumns
    dbView.exactFiltersText = formatExactFilters(data.exact_filters)
    selectedDbRowKeys.value = []
  } catch (err) {
    setNotice('error', err.message || '读取表数据失败')
  } finally {
    busy.dbRows = false
  }
}

async function refreshDb() {
  await loadDbTables()
  dbView.offset = 0
  selectedDbRowKeys.value = []
  await loadDbRows()
}

function switchDbTable() {
  dbView.offset = 0
  dbView.keyword = ''
  dbView.searchColumns = []
  dbView.exactFiltersText = ''
  selectedDbRowKeys.value = []
  loadDbRows()
}

function toggleDbSearchColumn(column) {
  const index = dbView.searchColumns.indexOf(column)
  if (index >= 0) {
    dbView.searchColumns.splice(index, 1)
    return
  }
  dbView.searchColumns.push(column)
}

function applyDbFilters() {
  dbView.offset = 0
  selectedDbRowKeys.value = []
  loadDbRows()
}

function clearDbFilters() {
  dbView.offset = 0
  dbView.keyword = ''
  dbView.searchColumns = []
  dbView.exactFiltersText = ''
  selectedDbRowKeys.value = []
  loadDbRows()
}

function changeDbLimit() {
  dbView.offset = 0
  selectedDbRowKeys.value = []
  loadDbRows()
}

function formatExactFilters(filters) {
  if (!filters || typeof filters !== 'object') {
    return ''
  }
  const parts = Object.entries(filters)
    .filter(([key]) => Boolean(key))
    .map(([key, value]) => `${key}=${value}`)
  return parts.join(', ')
}

function changeDbPage(step) {
  const nextOffset = Math.max(0, dbView.offset + step * dbView.limit)
  if (nextOffset >= dbView.total && dbView.total > 0) {
    return
  }
  dbView.offset = nextOffset
  selectedDbRowKeys.value = []
  loadDbRows()
}

function extractDbPrimaryKey(row) {
  if (!row || !dbView.primaryKeys.length) {
    return null
  }

  const pk = {}
  for (const key of dbView.primaryKeys) {
    if (!(key in row)) {
      return null
    }
    pk[key] = row[key]
  }
  return pk
}

function buildDbSelectionKeyFromPk(pk) {
  if (!pk) {
    return ''
  }
  return JSON.stringify(dbView.primaryKeys.map((key) => [key, pk[key] ?? null]))
}

function buildDbRowSelectionKey(row) {
  return buildDbSelectionKeyFromPk(extractDbPrimaryKey(row))
}

function isDbRowSelected(row) {
  const key = buildDbRowSelectionKey(row)
  if (!key) {
    return false
  }
  return selectedDbRowKeys.value.includes(key)
}

function canEditDbRow(row) {
  return Boolean(dbView.primaryKeys.length && extractDbPrimaryKey(row))
}

function toggleDbRowSelection(row) {
  const key = buildDbRowSelectionKey(row)
  if (!key) {
    return
  }

  const index = selectedDbRowKeys.value.indexOf(key)
  if (index >= 0) {
    selectedDbRowKeys.value.splice(index, 1)
    return
  }
  selectedDbRowKeys.value.push(key)
}

function toggleAllDbRows(checked) {
  if (!checked) {
    selectedDbRowKeys.value = []
    return
  }

  selectedDbRowKeys.value = dbSelectableRows.value
    .map((row) => buildDbRowSelectionKey(row))
    .filter((key) => Boolean(key))
}

function buildDbCreateTemplate() {
  const required = {}
  for (const column of dbView.columnDefs) {
    if (column.autoincrement) {
      continue
    }
    if (!column.nullable && !column.has_default) {
      required[column.name] = null
    }
  }
  return required
}

function openDbCreateDialog() {
  if (!dbView.table) {
    setNotice('warn', '请先选择数据表')
    return
  }
  dbEditor.mode = 'create'
  dbEditor.pk = {}
  dbEditor.payload = JSON.stringify(buildDbCreateTemplate(), null, 2)
  dbEditor.open = true
}

function openDbEditDialog(row) {
  if (!row) {
    setNotice('warn', '当前行不可编辑')
    return
  }

  const pk = extractDbPrimaryKey(row)
  if (!pk) {
    setNotice('warn', '当前表没有主键，无法编辑')
    return
  }

  dbEditor.mode = 'update'
  dbEditor.pk = pk
  dbEditor.payload = JSON.stringify(row, null, 2)
  dbEditor.open = true
}

function closeDbEditor() {
  dbEditor.open = false
}

function parseDbEditorPayload() {
  try {
    const parsed = JSON.parse(dbEditor.payload)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error('请输入 JSON 对象，例如 {"id": "demo"}')
    }
    return parsed
  } catch (err) {
    setNotice('warn', err.message || 'JSON 格式不正确')
    return null
  }
}

async function submitDbEditor() {
  if (busy.dbWrite || !dbView.table) {
    return
  }

  const values = parseDbEditorPayload()
  if (!values) {
    return
  }

  busy.dbWrite = true
  try {
    if (dbEditor.mode === 'create') {
      await api(`/ops/db/table/${encodeURIComponent(dbView.table)}/row`, {
        method: 'POST',
        body: { values },
      })
      setNotice('success', '新增记录成功')
    } else {
      await api(`/ops/db/table/${encodeURIComponent(dbView.table)}/row`, {
        method: 'PUT',
        body: {
          pk: dbEditor.pk,
          values,
        },
      })
      setNotice('success', '更新记录成功')
    }

    dbEditor.open = false
    selectedDbRowKeys.value = []
    await loadDbRows()
    await loadDbTables()
  } catch (err) {
    setNotice('error', err.message || '提交失败')
  } finally {
    busy.dbWrite = false
  }
}

async function deleteSelectedDbRows() {
  if (busy.dbWrite || !dbView.table) {
    return
  }

  const pkList = selectedDbPkList.value
  if (!pkList.length) {
    setNotice('warn', '请先勾选要删除的记录')
    return
  }

  const confirmed = window.confirm(`确认删除选中的 ${pkList.length} 条记录？`)
  if (!confirmed) {
    return
  }

  busy.dbWrite = true
  try {
    let deletedCount = 0
    for (const pk of pkList) {
      await api(`/ops/db/table/${encodeURIComponent(dbView.table)}/row`, {
        method: 'DELETE',
        body: { pk },
      })
      deletedCount += 1
    }

    setNotice('success', `已删除 ${deletedCount} 条记录`)

    const nextTotal = Math.max(0, dbView.total - deletedCount)
    const maxOffset = nextTotal > 0 ? Math.floor((nextTotal - 1) / dbView.limit) * dbView.limit : 0
    if (dbView.offset > maxOffset) {
      dbView.offset = maxOffset
    }

    selectedDbRowKeys.value = []
    await loadDbRows()
    await loadDbTables()
  } catch (err) {
    setNotice('error', err.message || '删除失败')
  } finally {
    busy.dbWrite = false
  }
}

async function loadMcpConfig() {
  busy.mcp = true
  try {
    const data = await api('/ops/mcp/config')
    mcp.server_name = data.server_name || ''
    mcp.database_path = data.database_path || ''
    mcp.config_json = data.claude_config_json || data.config_json || ''
    mcp.install_steps = Array.isArray(data.claude_install_steps)
      ? data.claude_install_steps
      : Array.isArray(data.install_steps)
        ? data.install_steps
        : []
    mcp.opencode_config_json = data.opencode_config_json || ''
    mcp.opencode_install_steps = Array.isArray(data.opencode_install_steps)
      ? data.opencode_install_steps
      : []
    mcp.codex_config_toml = data.codex_config_toml || ''
    mcp.codex_install_steps = Array.isArray(data.codex_install_steps)
      ? data.codex_install_steps
      : []
    mcp.codex_cli_add_command = data.codex_cli_add_command || ''
    mcp.claude_mcp_docs_url = data.claude_mcp_docs_url || 'https://code.claude.com/docs/en/mcp'
    mcp.codex_mcp_docs_url = data.codex_mcp_docs_url || 'https://developers.openai.com/codex/mcp/'
    mcp.opencode_docs_url = data.opencode_docs_url || 'https://opencode.ai/docs/config/'
    mcp.opencode_mcp_docs_url = data.opencode_mcp_docs_url || 'https://opencode.ai/docs/mcp-servers/'
    mcp.tools = Array.isArray(data.tools) ? data.tools : []
  } catch (err) {
    setNotice('error', err.message || '读取 MCP 配置失败')
  } finally {
    busy.mcp = false
  }
}

async function generateMcpFile() {
  busy.mcpWrite = true
  try {
    const data = await api('/ops/mcp/generate-file', { method: 'POST' })
    mcp.file_path = data.file_path || ''
    mcp.opencode_file_path = data.opencode_file_path || ''
    mcp.codex_file_path = data.codex_file_path || ''
    mcp.config_json = data.claude_config_json || data.config_json || mcp.config_json
    mcp.install_steps = Array.isArray(data.claude_install_steps)
      ? data.claude_install_steps
      : Array.isArray(data.install_steps)
        ? data.install_steps
        : mcp.install_steps
    mcp.opencode_config_json = data.opencode_config_json || mcp.opencode_config_json
    mcp.opencode_install_steps = Array.isArray(data.opencode_install_steps)
      ? data.opencode_install_steps
      : mcp.opencode_install_steps
    mcp.codex_config_toml = data.codex_config_toml || mcp.codex_config_toml
    mcp.codex_install_steps = Array.isArray(data.codex_install_steps)
      ? data.codex_install_steps
      : mcp.codex_install_steps
    mcp.codex_cli_add_command = data.codex_cli_add_command || mcp.codex_cli_add_command
    mcp.claude_mcp_docs_url = data.claude_mcp_docs_url || mcp.claude_mcp_docs_url
    mcp.codex_mcp_docs_url = data.codex_mcp_docs_url || mcp.codex_mcp_docs_url
    mcp.opencode_docs_url = data.opencode_docs_url || mcp.opencode_docs_url
    mcp.opencode_mcp_docs_url = data.opencode_mcp_docs_url || mcp.opencode_mcp_docs_url
    mcp.tools = Array.isArray(data.tools) ? data.tools : mcp.tools
    setNotice('success', '配置文件已生成')
  } catch (err) {
    setNotice('error', err.message || '生成配置文件失败')
  } finally {
    busy.mcpWrite = false
  }
}

async function copyText(text, label = '内容') {
  if (!text) {
    setNotice('warn', `${label}为空`)
    return
  }

  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      throw new Error('clipboard unavailable')
    }
    setNotice('success', `${label}已复制`)
  } catch {
    setNotice('warn', `浏览器不支持自动复制，请手动复制${label}`)
  }
}

onMounted(async () => {
  busy.boot = true
  loadArticleLimitPreference()
  loadSavedMpCapturePreference()
  loadCustomCaptureRangesPreference()
  await Promise.all([
    loadSession(),
    loadOverview(),
    loadMps(),
    loadArticles(),
    loadDbTables(),
    loadMcpConfig(),
    loadLatestCaptureJob(),
  ])
  await loadDbRows()

  if (session.status === 'waiting_scan' || session.status === 'scanned') {
    startPolling()
  }

  busy.boot = false
})

onBeforeUnmount(() => {
  stopPolling()
  stopCaptureJobPolling()
  if (noticeTimer) {
    clearTimeout(noticeTimer)
  }
})
</script>

<template>
  <div class="console-layout">
    <aside class="console-sidebar">
      <div class="sidebar-brand">
        <span class="sidebar-brand__badge"><AppIcon name="sparkles" :size="13" /> WEMP CONSOLE</span>
        <h1>公众号数据控制台</h1>
        <p>登录、抓取、预览、数据库、MCP 一体化工作台。</p>
      </div>

      <div class="sidebar-session">
        <span class="status-chip" :class="`status-chip--${currentStatus.tone}`">{{ currentStatus.label }}</span>
        <div class="sidebar-session__user" v-if="session.account_name">
          <img
            :src="proxiedImageUrl(session.account_avatar || qrPlaceholder)"
            alt="avatar"
            @load="markImageReady(proxiedImageUrl(session.account_avatar || qrPlaceholder))"
            @error="onImageError($event, proxiedImageUrl(session.account_avatar || qrPlaceholder))"
          />
          <span>{{ session.account_name }}</span>
        </div>
        <code>token: {{ tokenPreview }}</code>
      </div>

      <nav class="sidebar-nav">
        <button
          v-for="(item, key) in viewMeta"
          :key="key"
          class="sidebar-nav__btn"
          :class="{ 'sidebar-nav__btn--active': activeView === key }"
          @click="activeView = key"
        >
          <AppIcon :name="item.icon" :size="15" />
          <div>
            <strong>{{ item.label }}</strong>
            <span>{{ item.desc }}</span>
          </div>
        </button>
      </nav>

      <div class="sidebar-metrics">
        <article class="metric-card" v-for="item in summaryCards" :key="item.label">
          <span><AppIcon :name="item.icon" :size="13" /> {{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </article>
      </div>
    </aside>

    <main class="console-main">
      <header class="main-header">
        <div>
          <h2>{{ activeViewMeta.label }}</h2>
          <p>{{ activeViewMeta.desc }}</p>
        </div>
      </header>

      <div v-if="notice.text" class="notice" :class="`notice--${notice.type}`">{{ notice.text }}</div>

      <template v-if="activeView === 'capture'">
        <section class="capture-layout">
          <div class="capture-column">
            <article class="panel">
            <header class="panel__head">
              <h3 class="title-row"><AppIcon name="qrcode" :size="16" /> 登录账号</h3>
              <div class="actions">
                <button class="btn btn--primary" :disabled="busy.qr" @click="requestQrCode">
                  <span class="btn__inner">
                    <AppIcon name="qrcode" :size="15" />
                    <span>{{ busy.qr ? '生成中...' : '获取二维码' }}</span>
                  </span>
                </button>
                <button class="btn" :disabled="busy.status" @click="checkAuthStatus(false)">
                  <span class="btn__inner">
                    <AppIcon name="refresh" :size="15" />
                    <span>{{ busy.status ? '检查中...' : '检查状态' }}</span>
                  </span>
                </button>
                <button class="btn" @click="isPolling ? stopPolling() : startPolling()">
                  <span class="btn__inner">
                    <AppIcon :name="isPolling ? 'pause' : 'pulse'" :size="15" />
                    <span>{{ isPolling ? '停止轮询' : '自动轮询' }}</span>
                  </span>
                </button>
                <button class="btn btn--danger" :disabled="busy.logout || hasActiveCaptureJob" @click="logout">
                  <span class="btn__inner">
                    <AppIcon name="logout" :size="15" />
                    <span>{{ busy.logout ? '处理中...' : hasActiveCaptureJob ? '任务执行中' : '注销' }}</span>
                  </span>
                </button>
              </div>
            </header>

            <div class="login-layout">
              <div class="qr-box">
                <img
                  :src="qrImageUrl || qrPlaceholder"
                  alt="login qrcode"
                  @load="markImageReady(qrImageUrl || qrPlaceholder)"
                  @error="onImageError($event, qrImageUrl || qrPlaceholder)"
                />
              </div>
              <div class="kv-list">
                <div class="kv-item"><span>状态</span><strong>{{ currentStatus.label }}</strong></div>
                <div class="kv-item"><span>账号</span><strong>{{ session.account_name || '-' }}</strong></div>
                <div class="kv-item"><span>Token</span><code>{{ session.token || '-' }}</code></div>
                <div class="kv-item" v-if="session.last_error"><span>错误</span><strong>{{ session.last_error }}</strong></div>
              </div>
            </div>
          </article>

          <article class="panel panel--saved">
            <header class="panel__head">
              <h3 class="title-row"><AppIcon name="users" :size="16" /> 常用公众号与任务进度</h3>
            </header>

            <div class="saved-stack">
              <div class="capture-job" v-if="captureJob">
                <div class="capture-job__head">
                  <span class="capture-job__status" :class="`capture-job__status--${currentCaptureJobStatus.tone}`">
                    {{ currentCaptureJobStatus.label }}
                  </span>
                  <code>{{ captureJob.id }}</code>
                </div>
                <p class="capture-job__meta">{{ captureJobProgressText }}</p>
                <p class="capture-job__tip" v-if="hasActiveCaptureJob">
                  后台处理中，可离开当前页面；回来后会自动继续展示最新状态。任务执行时请勿注销账号。
                </p>
                <p class="capture-job__tip capture-job__tip--bad" v-if="captureJob.status === 'failed' && captureJob.error">
                  {{ captureJob.error }}
                </p>
                <div class="actions">
                  <button class="btn" :disabled="busy.captureStatus" @click="refreshCaptureJob(false)">
                    <span class="btn__inner">
                      <AppIcon name="refresh" :size="15" />
                      <span>{{ busy.captureStatus ? '刷新中...' : '刷新任务状态' }}</span>
                    </span>
                  </button>
                </div>
              </div>

              <div class="quick-result" v-if="captureResult">
                <p>
                  已写入 <strong>{{ captureResult.mp.nickname }}</strong>，{{ captureTargetText(captureResult) }}，
                  新增 {{ captureResult.sync.created }}，更新 {{ captureResult.sync.updated }}，
                  跳过重复 {{ captureResult.sync.duplicates_skipped || 0 }}，
                  扫描进度 {{ captureResult.sync.scanned_pages || captureResult.sync.pages }}。
                </p>
              </div>

              <div class="saved-mps">
                <div class="saved-mps__head">
                  <h4>已保存公众号（可一键抓取）</h4>
                  <span>常用 {{ favoriteMps.length }} / {{ mps.length }}</span>
                </div>

                <div v-if="mps.length" class="saved-mps__list">
                  <article
                    v-for="item in mps"
                    :key="item.id"
                    class="saved-mp-card"
                    :class="{ 'saved-mp-card--favorite': item.is_favorite }"
                  >
                    <img
                      :src="proxiedImageUrl(item.avatar || qrPlaceholder)"
                      alt="saved mp avatar"
                      @load="markImageReady(proxiedImageUrl(item.avatar || qrPlaceholder))"
                      @error="onImageError($event, proxiedImageUrl(item.avatar || qrPlaceholder))"
                    />
                    <div class="saved-mp-card__main">
                      <div class="saved-mp-card__title">
                        <strong>{{ item.nickname || '未命名公众号' }}</strong>
                        <span class="saved-mp-card__tag" v-if="item.is_favorite">常用</span>
                      </div>
                      <p>{{ item.alias ? `@${item.alias}` : item.fakeid }}</p>
                      <p class="sub">抓取次数 {{ item.use_count || 0 }} · 最近提交 {{ formatDateTime(item.last_used_at) }}</p>
                      <div class="saved-mp-card__controls">
                        <span class="saved-mp-card__control-label">时间范围</span>
                        <div class="saved-mp-card__count-options">
                          <button
                            v-for="count in savedMpCaptureOptionList(item)"
                            :key="`${item.id}-${count}`"
                            type="button"
                            class="saved-mp-card__count-btn"
                            :class="{ 'saved-mp-card__count-btn--active': savedMpCaptureDays(item) === count }"
                            @click="setSavedMpCaptureDays(item.id, count)"
                          >
                            近 {{ count }} 天
                          </button>
                        </div>
                      </div>
                    </div>
                    <div class="saved-mp-card__actions">
                      <button
                        class="btn btn--primary"
                        :disabled="busy.capture || !canCapture || hasActiveCaptureJob"
                        @click="captureSavedMp(item)"
                      >
                        <span class="btn__inner">
                          <AppIcon name="rocket" :size="14" />
                          <span>{{ busy.capture ? '提交中...' : savedMpCaptureButtonText(item) }}</span>
                        </span>
                      </button>
                      <button
                        class="btn"
                        :disabled="busy.favoriteMp === item.id"
                        @click="toggleFavoriteMp(item)"
                      >
                        <span class="btn__inner">
                          <AppIcon name="target" :size="14" />
                          <span>{{ item.is_favorite ? '取消常用' : '设为常用' }}</span>
                        </span>
                      </button>
                    </div>
                  </article>
                </div>

                <p v-else class="empty">暂无已保存公众号，先搜索并提交一次抓取任务。</p>
              </div>
            </div>
          </article>

          </div>

          <div class="capture-column">
            <article class="panel">
            <header class="panel__head">
              <h3 class="title-row"><AppIcon name="target" :size="16" /> 选择公众号与抓取范围</h3>
              <div class="actions">
                <input
                  v-model="captureForm.keyword"
                  type="text"
                  placeholder="输入公众号关键词，例如：生财有术"
                  @keyup.enter="searchCandidates"
                />
                <button class="btn" :disabled="busy.search" @click="searchCandidates">
                  <span class="btn__inner">
                    <AppIcon name="search" :size="15" />
                    <span>{{ busy.search ? '搜索中...' : '搜索公众号' }}</span>
                  </span>
                </button>
              </div>
            </header>

            <div v-if="busy.search" class="candidate-skeleton-list">
              <div class="candidate-skeleton" v-for="n in 3" :key="n"></div>
            </div>

            <div v-else-if="mpCandidates.length" class="candidate-grid">
              <article
                v-for="item in mpCandidates"
                :key="item.fakeid"
                class="candidate-card"
                :class="{ 'candidate-card--active': selectedCandidate?.fakeid === item.fakeid }"
              >
                <img
                  :src="proxiedImageUrl(item.avatar || qrPlaceholder)"
                  alt="mp avatar"
                  @load="markImageReady(proxiedImageUrl(item.avatar || qrPlaceholder))"
                  @error="onImageError($event, proxiedImageUrl(item.avatar || qrPlaceholder))"
                />
                <div>
                  <h3>{{ item.nickname || '未命名公众号' }}</h3>
                  <p v-if="item.alias">@{{ item.alias }}</p>
                  <p>{{ item.fakeid }}</p>
                </div>
                <button class="btn" @click="chooseCandidate(item)">
                  <span class="btn__inner">
                    <AppIcon name="check-circle" :size="15" />
                    <span>选择</span>
                  </span>
                </button>
              </article>
            </div>

            <p v-else class="empty">先搜索公众号，然后确认目标对象。</p>

            <div class="confirm-card" v-if="selectedCandidate">
              <div class="confirm-card__main">
                <img
                  :src="proxiedImageUrl(selectedCandidate.avatar || qrPlaceholder)"
                  alt="selected mp"
                  @load="markImageReady(proxiedImageUrl(selectedCandidate.avatar || qrPlaceholder))"
                  @error="onImageError($event, proxiedImageUrl(selectedCandidate.avatar || qrPlaceholder))"
                />
                <div>
                  <strong>{{ selectedCandidate.nickname || '未命名公众号' }}</strong>
                  <p>{{ selectedCandidate.alias ? `@${selectedCandidate.alias}` : selectedCandidate.fakeid }}</p>
                  <p class="sub">搜索结果共 {{ mpSearchTotal }} 个，当前已选抓取对象</p>
                </div>
              </div>

              <div class="confirm-card__ops">
                <label>
                  开始日期
                  <input v-model="captureForm.range_start" type="date" />
                </label>
                <label>
                  结束日期
                  <input v-model="captureForm.range_end" type="date" />
                </label>
                <div class="saved-mp-card__count-options">
                  <button
                    v-for="days in savedMpQuickRangeDays"
                    :key="`capture-range-${days}`"
                    type="button"
                    class="saved-mp-card__count-btn"
                    @click="applyCapturePresetRangeDays(days)"
                  >
                    近 {{ days }} 天
                  </button>
                </div>
                <div class="capture-custom-range-tools">
                  <button class="btn" type="button" @click="saveCurrentCaptureRangeAsCustom">
                    <span class="btn__inner">
                      <AppIcon name="file-plus" :size="14" />
                      <span>保存为自定义</span>
                    </span>
                  </button>
                  <button v-if="customCaptureRanges.length" class="btn" type="button" @click="clearCustomCaptureRanges">
                    <span class="btn__inner">
                      <AppIcon name="x" :size="14" />
                      <span>清空自定义</span>
                    </span>
                  </button>
                </div>
                <span class="capture-estimate"><AppIcon name="calendar" :size="13" /> {{ estimatedCaptureRange }}</span>
                <button
                  class="btn btn--primary"
                  :disabled="busy.capture || !canCapture || hasActiveCaptureJob"
                  @click="confirmCapture"
                >
                  <span class="btn__inner">
                    <AppIcon name="rocket" :size="15" />
                    <span>
                      {{
                        busy.capture ? '提交中...' : hasActiveCaptureJob ? '任务进行中（请等待）' : '提交抓取任务'
                      }}
                    </span>
                  </span>
                </button>
              </div>

              <div class="capture-custom-range-list" v-if="customCaptureRanges.length">
                <div class="capture-custom-range-item" v-for="item in customCaptureRanges" :key="item.id">
                  <button
                    type="button"
                    class="saved-mp-card__count-btn"
                    :class="{ 'saved-mp-card__count-btn--active': isCustomCaptureRangeActive(item) }"
                    @click="applyCustomCaptureRange(item)"
                  >
                    {{ customCaptureRangeLabel(item) }}
                  </button>
                  <button type="button" class="capture-custom-range-remove" @click="removeCustomCaptureRange(item.id)">
                    删除
                  </button>
                </div>
              </div>

              <p class="confirm-card__tip">
                任务提交后在服务端后台执行，你可以离开当前页面，稍后回来继续查看结果。
              </p>
            </div>
          </article>

          <article class="panel panel--article">
            <header class="panel__head">
              <h3 class="title-row"><AppIcon name="file-text" :size="16" /> 文章预览与导出</h3>
              <div class="actions">
                <select v-model="articleFilter.mp_id" @change="applyArticleFilters">
                  <option value="">全部公众号</option>
                  <option v-for="item in mps" :key="item.id" :value="item.id">{{ item.nickname }}</option>
                </select>
                <input
                  v-model="articleFilter.keyword"
                  type="text"
                  placeholder="标题关键词"
                  @keyup.enter="applyArticleFilters"
                />
                <select v-model.number="articleFilter.limit" @change="changeArticlePageSize">
                  <option v-for="size in articleLimitOptions" :key="`article-limit-${size}`" :value="size">
                    每页 {{ size }} 条
                  </option>
                </select>
                <button class="btn" :disabled="busy.articles" @click="applyArticleFilters">
                  <span class="btn__inner">
                    <AppIcon name="refresh" :size="15" />
                    <span>刷新列表</span>
                  </span>
                </button>
              </div>
            </header>

            <div class="article-scroll">
              <div v-if="busy.articles" class="article-skeleton-list">
                <div class="article-skeleton" v-for="n in 4" :key="n"></div>
              </div>

              <div v-else-if="articles.length" class="article-list">
                <article class="article-row" v-for="item in articles" :key="item.id">
                  <div
                    class="article-cover"
                    :class="{ 'article-cover--ready': isImageReady(proxiedImageUrl(item.cover_url || qrPlaceholder)) }"
                  >
                    <img
                      :src="proxiedImageUrl(item.cover_url || qrPlaceholder)"
                      alt="cover"
                      loading="lazy"
                      @load="markImageReady(proxiedImageUrl(item.cover_url || qrPlaceholder))"
                      @error="onImageError($event, proxiedImageUrl(item.cover_url || qrPlaceholder))"
                    />
                  </div>
                  <div class="article-main">
                    <h3>{{ item.title }}</h3>
                    <p>
                      {{ item.author || '未知作者' }} · {{ new Date(item.updated_at).toLocaleString('zh-CN') }} ·
                      图片 {{ imageCount(item) }}
                    </p>
                    <a :href="item.url" target="_blank" rel="noreferrer">查看原文</a>
                  </div>
                  <div class="article-actions">
                    <button class="btn" :disabled="busy.preview" @click="openArticlePreview(item)">
                      <span class="btn__inner">
                        <AppIcon name="eye" :size="15" />
                        <span>预览正文</span>
                      </span>
                    </button>
                    <button class="btn" :disabled="busy.refreshArticle === item.id" @click="refreshArticle(item)">
                      <span class="btn__inner">
                        <AppIcon name="rotate-cw" :size="15" />
                        <span>{{ busy.refreshArticle === item.id ? '刷新中...' : '刷新正文' }}</span>
                      </span>
                    </button>
                    <button class="btn btn--primary" :disabled="busy.exportArticle === `${item.id}:pdf`" @click="exportArticle(item, 'pdf')">
                      <span class="btn__inner">
                        <AppIcon name="download" :size="15" />
                        <span>{{ busy.exportArticle === `${item.id}:pdf` ? '导出中...' : '导出 PDF' }}</span>
                      </span>
                    </button>
                  </div>
                </article>
              </div>

              <p v-else class="empty">暂无文章，请先完成抓取。</p>
            </div>

            <div class="article-pager" v-if="articleTotal > articleFilter.limit">
              <button class="btn" :disabled="busy.articles || articleCurrentPage <= 1" @click="changeArticlePage(-1)">
                <span class="btn__inner">
                  <AppIcon name="arrow-left" :size="14" />
                  <span>上一页</span>
                </span>
              </button>

              <div class="article-pager__numbers">
                <template v-for="(item, idx) in articlePageItems" :key="`article-page-${idx}-${item}`">
                  <span v-if="item === '...'" class="article-pager__ellipsis">...</span>
                  <button
                    v-else
                    class="article-page-btn"
                    :class="{ 'article-page-btn--active': item === articleCurrentPage }"
                    :disabled="busy.articles"
                    @click="goToArticlePage(item)"
                  >
                    {{ item }}
                  </button>
                </template>
              </div>

              <button
                class="btn"
                :disabled="busy.articles || articleCurrentPage >= articlePageCount"
                @click="changeArticlePage(1)"
              >
                <span class="btn__inner">
                  <span>下一页</span>
                  <AppIcon name="arrow-right" :size="14" />
                </span>
              </button>
            </div>

            <p class="empty article-total" v-if="articleTotal > 0">当前筛选 {{ articleRangeText }}（第 {{ articleCurrentPage }} / {{ articlePageCount }} 页）</p>
          </article>
          </div>
        </section>
      </template>

      <template v-else-if="activeView === 'database'">
        <section class="panel-grid panel-grid--single">
          <article class="panel panel--wide">
            <header class="panel__head">
              <h3 class="title-row"><AppIcon name="database" :size="16" /> 数据库在线浏览</h3>
              <div class="actions">
                <select v-model="dbView.table" @change="switchDbTable">
                  <option v-for="info in dbView.tableInfos" :key="info.name" :value="info.name">
                    {{ info.name }} ({{ info.row_count }})
                  </option>
                </select>
                <select v-model.number="dbView.limit" @change="changeDbLimit">
                  <option v-for="size in dbLimitOptions" :key="size" :value="size">每页 {{ size }} 条</option>
                </select>
                <button class="btn" :disabled="busy.dbTables || busy.dbRows" @click="refreshDb">
                  <span class="btn__inner">
                    <AppIcon name="refresh" :size="15" />
                    <span>刷新</span>
                  </span>
                </button>
              </div>
            </header>

            <div class="db-meta">
              <span>表：{{ dbView.table || '-' }}</span>
              <span v-if="dbView.tableComment">说明：{{ dbView.tableComment }}</span>
              <span>主键：{{ dbPrimaryKeyText }}</span>
              <span>记录：{{ dbRangeText }}</span>
            </div>

            <div class="db-toolbar">
              <div class="db-toolbar__row">
                <input
                  v-model="dbView.keyword"
                  type="text"
                  placeholder="关键词模糊搜索（留空则不筛选）"
                  @keyup.enter="applyDbFilters"
                />
                <input
                  v-model="dbView.exactFiltersText"
                  type="text"
                  placeholder="精确筛选：col=val,col2=val2"
                  @keyup.enter="applyDbFilters"
                />
                <button class="btn btn--primary" :disabled="busy.dbRows" @click="applyDbFilters">
                  <span class="btn__inner">
                    <AppIcon name="search" :size="15" />
                    <span>应用筛选</span>
                  </span>
                </button>
                <button class="btn" :disabled="busy.dbRows" @click="clearDbFilters">
                  <span class="btn__inner">
                    <AppIcon name="x" :size="15" />
                    <span>清空条件</span>
                  </span>
                </button>
              </div>

              <div class="db-columns">
                <span>搜索字段：</span>
                <button
                  v-for="column in dbView.columns"
                  :key="column"
                  class="db-column-chip"
                  :class="{ 'db-column-chip--active': dbView.searchColumns.includes(column) }"
                  :title="dbView.columnComments[column] || column"
                  @click="toggleDbSearchColumn(column)"
                >
                  {{ column }}
                </button>
              </div>

              <div class="db-crud">
                <button
                  class="btn btn--primary"
                  :disabled="busy.dbRows || busy.dbWrite || !dbView.table"
                  @click="openDbCreateDialog"
                >
                  <span class="btn__inner">
                    <AppIcon name="file-plus" :size="15" />
                    <span>新增记录</span>
                  </span>
                </button>
                <button
                  class="btn btn--danger"
                  :disabled="busy.dbRows || busy.dbWrite || !selectedDbCount || !dbView.primaryKeys.length"
                  @click="deleteSelectedDbRows"
                >
                  <span class="btn__inner">
                    <AppIcon name="x" :size="15" />
                    <span>{{ selectedDbCount ? `删除选中 (${selectedDbCount})` : '删除选中' }}</span>
                  </span>
                </button>
              </div>
            </div>

            <div class="db-table-wrap" v-if="busy.dbRows">
              <div class="db-row-skeleton" v-for="n in 8" :key="n"></div>
            </div>

            <div class="db-table-wrap" v-else-if="dbView.rows.length">
              <table>
                <thead>
                  <tr>
                    <th class="db-col-select">
                      <input
                        class="db-row-check"
                        type="checkbox"
                        :checked="isDbAllSelected"
                        :disabled="busy.dbRows || busy.dbWrite || !dbSelectableRows.length"
                        @change="toggleAllDbRows($event.target.checked)"
                      />
                    </th>
                    <th v-for="column in dbView.columns" :key="column" :title="dbView.columnComments[column] || ''">
                      {{ column }}
                    </th>
                    <th class="db-col-actions">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(row, idx) in dbView.rows"
                    :key="idx"
                    :class="{ 'db-row--active': isDbRowSelected(row) }"
                  >
                    <td class="db-col-select">
                      <input
                        class="db-row-check"
                        type="checkbox"
                        :checked="isDbRowSelected(row)"
                        :disabled="!canEditDbRow(row)"
                        @change="toggleDbRowSelection(row)"
                        @click.stop
                      />
                    </td>
                    <td v-for="column in dbView.columns" :key="column">
                      <span :title="String(row[column] ?? '')">{{ row[column] ?? '-' }}</span>
                    </td>
                    <td class="db-col-actions">
                      <button class="btn db-row-action-btn" :disabled="busy.dbWrite || !canEditDbRow(row)" @click.stop="openDbEditDialog(row)">
                        <span class="btn__inner">
                          <span>编辑</span>
                        </span>
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <p v-else class="empty">当前表暂无数据。</p>

            <div class="pager">
              <button class="btn" :disabled="dbView.offset <= 0 || busy.dbRows" @click="changeDbPage(-1)">
                <span class="btn__inner">
                  <AppIcon name="arrow-left" :size="15" />
                  <span>上一页</span>
                </span>
              </button>
              <button
                class="btn"
                :disabled="dbView.offset + dbView.limit >= dbView.total || busy.dbRows"
                @click="changeDbPage(1)"
              >
                <span class="btn__inner">
                  <AppIcon name="arrow-right" :size="15" />
                  <span>下一页</span>
                </span>
              </button>
            </div>
          </article>
        </section>
      </template>

      <template v-else>
        <section class="panel-grid panel-grid--single">
          <article class="panel panel--wide">
            <header class="panel__head">
              <h3 class="title-row"><AppIcon name="plug" :size="16" /> MCP 接入配置</h3>
              <div class="actions">
                <button class="btn" :disabled="busy.mcp" @click="loadMcpConfig">
                  <span class="btn__inner">
                    <AppIcon name="refresh" :size="15" />
                    <span>刷新配置</span>
                  </span>
                </button>
                <button class="btn btn--primary" :disabled="busy.mcpWrite" @click="generateMcpFile">
                  <span class="btn__inner">
                    <AppIcon name="file-plus" :size="15" />
                    <span>{{ busy.mcpWrite ? '生成中...' : '生成配置文件' }}</span>
                  </span>
                </button>
              </div>
            </header>

            <div class="mcp-kv">
              <div><span>Server 名称</span><strong>{{ mcp.server_name || '-' }}</strong></div>
              <div><span>数据库路径</span><code>{{ mcp.database_path || '-' }}</code></div>
              <div v-if="mcp.file_path"><span>Claude/Cursor 配置文件</span><code>{{ mcp.file_path }}</code></div>
              <div v-if="mcp.opencode_file_path"><span>OpenCode 配置片段文件</span><code>{{ mcp.opencode_file_path }}</code></div>
              <div v-if="mcp.codex_file_path"><span>Codex 配置片段文件</span><code>{{ mcp.codex_file_path }}</code></div>
            </div>

            <div class="mcp-guide" v-if="mcp.install_steps.length">
              <h4>Claude / Cursor 接入步骤</h4>
              <ol>
                <li v-for="(step, idx) in mcp.install_steps" :key="`${idx}-${step}`">{{ step }}</li>
              </ol>
              <p class="mcp-guide__links">
                官方文档：
                <a :href="mcp.claude_mcp_docs_url" target="_blank" rel="noreferrer">Claude Code MCP</a>
              </p>
            </div>

            <div class="mcp-guide" v-if="mcp.opencode_install_steps.length">
              <h4>OpenCode 配置步骤</h4>
              <ol>
                <li v-for="(step, idx) in mcp.opencode_install_steps" :key="`opencode-${idx}-${step}`">{{ step }}</li>
              </ol>
              <p class="mcp-guide__links">
                官方文档：
                <a :href="mcp.opencode_mcp_docs_url" target="_blank" rel="noreferrer">MCP Servers</a>
                <span> · </span>
                <a :href="mcp.opencode_docs_url" target="_blank" rel="noreferrer">Config</a>
              </p>
            </div>

            <div class="mcp-guide" v-if="mcp.codex_install_steps.length">
              <h4>Codex 配置步骤</h4>
              <ol>
                <li v-for="(step, idx) in mcp.codex_install_steps" :key="`codex-${idx}-${step}`">{{ step }}</li>
              </ol>
              <p class="mcp-guide__links">
                官方文档：
                <a :href="mcp.codex_mcp_docs_url" target="_blank" rel="noreferrer">Codex MCP</a>
              </p>
              <pre class="mcp-command" v-if="mcp.codex_cli_add_command">{{ mcp.codex_cli_add_command }}</pre>
            </div>

            <div class="mcp-guide" v-if="mcp.tools.length">
              <h4>MCP 工具</h4>
              <ul>
                <li v-for="tool in mcp.tools" :key="tool.name">
                  <code>{{ tool.name }}</code>
                  <span>{{ tool.description }}</span>
                </li>
              </ul>
            </div>

            <div class="actions actions--mcp">
              <button class="btn" @click="copyText(mcp.config_json, 'Claude/Cursor MCP配置 JSON')">
                <span class="btn__inner">
                  <AppIcon name="copy" :size="15" />
                  <span>复制 Claude/Cursor 配置</span>
                </span>
              </button>
              <button class="btn" @click="copyText(mcp.opencode_config_json, 'OpenCode 配置 JSON')">
                <span class="btn__inner">
                  <AppIcon name="copy" :size="15" />
                  <span>复制 OpenCode 配置</span>
                </span>
              </button>
              <button class="btn" @click="copyText(mcp.codex_config_toml, 'Codex 配置 TOML')">
                <span class="btn__inner">
                  <AppIcon name="copy" :size="15" />
                  <span>复制 Codex 配置</span>
                </span>
              </button>
              <button class="btn" @click="copyText(mcp.codex_cli_add_command, 'Codex MCP add 命令')">
                <span class="btn__inner">
                  <AppIcon name="copy" :size="15" />
                  <span>复制 Codex CLI 命令</span>
                </span>
              </button>
              <button class="btn" @click="copyText(mcp.database_path, '数据库路径')">
                <span class="btn__inner">
                  <AppIcon name="copy" :size="15" />
                  <span>复制数据库路径</span>
                </span>
              </button>
            </div>

            <div class="mcp-guide">
              <h4>Claude / Cursor 配置 JSON</h4>
              <textarea class="mcp-textarea mcp-textarea--compact" readonly :value="mcp.config_json"></textarea>
            </div>

            <div class="mcp-guide">
              <h4>OpenCode 配置片段（opencode.json）</h4>
              <textarea class="mcp-textarea mcp-textarea--compact" readonly :value="mcp.opencode_config_json"></textarea>
            </div>

            <div class="mcp-guide">
              <h4>Codex 配置片段（config.toml）</h4>
              <textarea class="mcp-textarea mcp-textarea--compact" readonly :value="mcp.codex_config_toml"></textarea>
            </div>
          </article>
        </section>
      </template>
    </main>

    <div class="preview-overlay" v-if="dbEditor.open" @click.self="closeDbEditor">
      <section class="db-editor-panel">
        <header>
          <h3>{{ dbEditorTitle }}</h3>
          <button class="btn" :disabled="busy.dbWrite" @click="closeDbEditor">
            <span class="btn__inner">
              <AppIcon name="x" :size="15" />
              <span>关闭</span>
            </span>
          </button>
        </header>

        <p class="db-editor-tip">
          <span v-if="dbEditor.mode === 'update'">主键：{{ JSON.stringify(dbEditor.pk) }}</span>
          <span>请输入 JSON 对象，使用 <code>null</code> 表示空值。</span>
        </p>

        <textarea
          v-model="dbEditor.payload"
          class="db-editor-textarea"
          spellcheck="false"
          placeholder='例如：{"id": "demo", "name": "示例"}'
        ></textarea>

        <div class="actions">
          <button class="btn" :disabled="busy.dbWrite" @click="closeDbEditor">
            <span class="btn__inner">
              <span>取消</span>
            </span>
          </button>
          <button class="btn btn--primary" :disabled="busy.dbWrite" @click="submitDbEditor">
            <span class="btn__inner">
              <AppIcon name="check-circle" :size="15" />
              <span>{{ busy.dbWrite ? '提交中...' : dbEditor.mode === 'create' ? '确认新增' : '确认更新' }}</span>
            </span>
          </button>
        </div>
      </section>
    </div>

    <div class="boot-overlay" v-if="busy.boot">
      <div class="boot-card">
        <AppIcon name="sparkles" :size="18" />
        <span>控制台初始化中...</span>
      </div>
    </div>

    <div class="preview-overlay" v-if="articlePreview.open" @click.self="closeArticlePreview">
      <section class="preview-panel">
        <header>
          <div>
            <h3>{{ articlePreview.title || '正文预览' }}</h3>
            <p>{{ articlePreview.updated_at ? new Date(articlePreview.updated_at).toLocaleString('zh-CN') : '-' }}</p>
          </div>
          <div class="actions">
            <a :href="articlePreview.url" target="_blank" rel="noreferrer" class="preview-link">
              <AppIcon name="external-link" :size="14" />
              <span>打开原文</span>
            </a>
            <button class="btn" @click="closeArticlePreview">
              <span class="btn__inner">
                <AppIcon name="x" :size="15" />
                <span>关闭</span>
              </span>
            </button>
          </div>
        </header>

        <div class="preview-body" v-if="busy.preview">
          <div class="preview-skeleton" v-for="n in 8" :key="n"></div>
        </div>

        <div class="preview-body" v-else>
          <article v-if="articlePreview.html" v-html="articlePreview.html"></article>
          <pre v-else>{{ articlePreview.text || '暂无正文，请尝试刷新正文后再预览。' }}</pre>
        </div>
      </section>
    </div>
  </div>
</template>
