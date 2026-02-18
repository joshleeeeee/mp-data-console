const rawBase = (import.meta.env.VITE_API_BASE || '/api/v1').trim()
const apiBase = rawBase.replace(/\/+$/, '') || '/api/v1'

export function resolveApiUrl(path = '') {
  if (!path) {
    return apiBase
  }
  if (/^https?:\/\//i.test(path)) {
    return path
  }

  const normalized = path.startsWith('/') ? path : `/${path}`

  if (normalized.startsWith('/api/')) {
    if (/^https?:\/\//i.test(apiBase)) {
      const origin = new URL(apiBase).origin
      return `${origin}${normalized}`
    }
    return normalized
  }

  return `${apiBase}${normalized}`
}

function unwrapErrorDetail(detail) {
  if (!detail) {
    return '请求失败'
  }
  if (typeof detail === 'string') {
    return detail
  }
  if (typeof detail === 'object') {
    return detail.message || detail.msg || JSON.stringify(detail)
  }
  return String(detail)
}

export async function api(path, options = {}) {
  const res = await fetch(resolveApiUrl(path), {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  })

  const contentType = res.headers.get('content-type') || ''
  const isJson = contentType.includes('application/json')
  const payload = isJson ? await res.json() : await res.text()

  if (!res.ok) {
    const detail = isJson ? payload?.detail || payload : payload
    throw new Error(unwrapErrorDetail(detail))
  }

  if (isJson && payload && typeof payload === 'object' && 'data' in payload) {
    return payload.data
  }

  return payload
}
