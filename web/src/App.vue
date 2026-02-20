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
  mcp: false,
  mcpWrite: false,
})

const qrImageUrl = ref('')
const isPolling = ref(false)

const captureForm = reactive({
  keyword: '',
  capture_count: 20,
  fetch_content: true,
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

const captureCount = computed(() => {
  const raw = Number(captureForm.capture_count)
  if (!Number.isFinite(raw) || raw <= 0) {
    return 1
  }
  return Math.min(250, Math.floor(raw))
})

const capturePages = computed(() => {
  return Math.max(1, Math.ceil(captureCount.value / 5))
})

const estimatedCaptureRange = computed(() => {
  return `目标抓取 ${captureCount.value} 条去重文章（重复不计入）`
})

const captureJobProgressText = computed(() => {
  if (!captureJob.value) {
    return ''
  }
  const requested = Number(captureJob.value.requested_count || 0)
  const created = Number(captureJob.value.created || 0)
  const scanned = Number(captureJob.value.scanned_pages || 0)
  const maxPages = Number(captureJob.value.max_pages || 0)
  const duplicated = Number(captureJob.value.duplicates_skipped || 0)
  const pageText = maxPages > 0 ? `${scanned}/${maxPages}` : `${scanned}`
  return `目标 ${requested} · 新增 ${created} · 跳过重复 ${duplicated} · 扫描页数 ${pageText}`
})

const dbRangeText = computed(() => {
  if (dbView.total <= 0) {
    return '0 / 0'
  }
  const start = dbView.offset + 1
  const end = Math.min(dbView.offset + dbView.limit, dbView.total)
  return `${start}-${end} / ${dbView.total}`
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
      pages: Number(job?.pages_hint || 0),
    },
    requested_count: Number(job?.requested_count || captureCount.value),
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
      if (job.reached_target) {
        setNotice('success', '后台抓取完成，结果已更新')
      } else {
        setNotice(
          'warn',
          `后台抓取完成：目标 ${job.requested_count} 条，实际新增 ${job.created} 条（可能源数据不足）`,
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

async function submitCaptureJobByMpId(mpId) {
  const job = await api(`/mps/${encodeURIComponent(mpId)}/sync/jobs`, {
    method: 'POST',
    body: {
      pages: capturePages.value,
      target_count: captureCount.value,
      fetch_content: Boolean(captureForm.fetch_content),
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

    await submitCaptureJobByMpId(saved.id)
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
    await submitCaptureJobByMpId(item.id)
    setNotice('info', `已为 ${item.nickname || '目标公众号'} 提交抓取任务`)
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
  } catch (err) {
    setNotice('error', err.message || '加载文章失败')
  } finally {
    busy.articles = false
  }
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
    dbView.columnComments = data.column_comments || {}
    dbView.rows = data.rows || []
    dbView.total = data.total || 0
    dbView.searchColumns = data.search_columns || dbView.searchColumns
    dbView.exactFiltersText = formatExactFilters(data.exact_filters)
  } catch (err) {
    setNotice('error', err.message || '读取表数据失败')
  } finally {
    busy.dbRows = false
  }
}

async function refreshDb() {
  await loadDbTables()
  dbView.offset = 0
  await loadDbRows()
}

function switchDbTable() {
  dbView.offset = 0
  dbView.keyword = ''
  dbView.searchColumns = []
  dbView.exactFiltersText = ''
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
  loadDbRows()
}

function clearDbFilters() {
  dbView.offset = 0
  dbView.keyword = ''
  dbView.searchColumns = []
  dbView.exactFiltersText = ''
  loadDbRows()
}

function changeDbLimit() {
  dbView.offset = 0
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
  loadDbRows()
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
        <section class="panel-grid">
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

          <article class="panel">
            <header class="panel__head">
              <h3 class="title-row"><AppIcon name="target" :size="16" /> 选择公众号与抓取参数</h3>
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
                  抓取条数
                  <input v-model.number="captureForm.capture_count" type="number" min="1" max="250" />
                </label>
                <label class="check">
                  <input v-model="captureForm.fetch_content" type="checkbox" />
                  抓正文
                </label>
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

              <p class="confirm-card__tip">
                任务提交后在服务端后台执行，你可以离开当前页面，稍后回来继续查看结果。
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
                  </div>
                  <div class="saved-mp-card__actions">
                    <button
                      class="btn btn--primary"
                      :disabled="busy.capture || !canCapture || hasActiveCaptureJob"
                      @click="captureSavedMp(item)"
                    >
                      <span class="btn__inner">
                        <AppIcon name="rocket" :size="14" />
                        <span>抓取</span>
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
                已写入 <strong>{{ captureResult.mp.nickname }}</strong>，目标去重 {{ captureResult.requested_count }} 条，
                新增 {{ captureResult.sync.created }}，更新 {{ captureResult.sync.updated }}，
                跳过重复 {{ captureResult.sync.duplicates_skipped || 0 }}，
                扫描 {{ captureResult.sync.scanned_pages || captureResult.sync.pages }} 页。
              </p>
            </div>
          </article>

          <article class="panel panel--wide">
            <header class="panel__head">
              <h3 class="title-row"><AppIcon name="file-text" :size="16" /> 文章预览与导出</h3>
              <div class="actions">
                <select v-model="articleFilter.mp_id">
                  <option value="">全部公众号</option>
                  <option v-for="item in mps" :key="item.id" :value="item.id">{{ item.nickname }}</option>
                </select>
                <input
                  v-model="articleFilter.keyword"
                  type="text"
                  placeholder="标题关键词"
                  @keyup.enter="loadArticles"
                />
                <button class="btn" :disabled="busy.articles" @click="loadArticles">
                  <span class="btn__inner">
                    <AppIcon name="refresh" :size="15" />
                    <span>刷新列表</span>
                  </span>
                </button>
              </div>
            </header>

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
            <p class="empty" v-if="articleTotal > 0">当前筛选共 {{ articleTotal }} 篇</p>
          </article>
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
            </div>

            <div class="db-table-wrap" v-if="busy.dbRows">
              <div class="db-row-skeleton" v-for="n in 8" :key="n"></div>
            </div>

            <div class="db-table-wrap" v-else-if="dbView.rows.length">
              <table>
                <thead>
                  <tr>
                    <th v-for="column in dbView.columns" :key="column" :title="dbView.columnComments[column] || ''">
                      {{ column }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, idx) in dbView.rows" :key="idx">
                    <td v-for="column in dbView.columns" :key="column">
                      <span :title="String(row[column] ?? '')">{{ row[column] ?? '-' }}</span>
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
