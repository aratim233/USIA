// Human-readable formatting for everything the API returns as codes or ISO strings.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// "2026-11-08" -> "8 Nov 2026" (read from the string itself, so no timezone shift)
export function fmtDate(iso) {
  if (!iso) return '—'
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return String(iso)
  return `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}`
}

// "2026-11-08T10:42:13.123" -> "8 Nov 2026, 10:42"
export function fmtDateTime(iso) {
  if (!iso) return '—'
  const t = String(iso).match(/[T ](\d{2}):(\d{2})/)
  return t ? `${fmtDate(iso)}, ${t[1]}:${t[2]}` : fmtDate(iso)
}

// "2036-11-08" -> "in 10 years", "2025-01-01" -> "1 year ago", today -> "today"
export function fmtRelative(iso) {
  const m = iso && String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/)
  if (!m) return ''
  const target = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
  const now = new Date(); now.setHours(0, 0, 0, 0)
  const days = Math.round((target - now) / 86400000)
  if (days === 0) return 'today'
  const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  const abs = Math.abs(days)
  if (abs < 45) return rtf.format(days, 'day')
  if (abs < 730) return rtf.format(Math.round(days / 30.44), 'month')
  return rtf.format(Math.round(days / 365.25), 'year')
}

export const fmtNumber = (n) => (n == null ? '—' : Number(n).toLocaleString('en-IN'))

const STATUS = {
  ACTIVE: 'Retained', EXPIRING: 'Expiring soon', EXPIRED: 'Retention ended',
  PENDING: 'Clock not started', 'NOT DELIVERED': 'Not delivered',
}
export const statusLabel = (s) => STATUS[s] || humanize(s)

const ACTIONS = {
  LOGIN: 'Signed in', CREATE: 'Created', UPDATE: 'Updated', DELETE: 'Deleted', DELETE_DENIED: 'Deletion refused',
  UPLOAD: 'Uploaded', DOWNLOAD: 'Downloaded', EXPORT: 'Exported', INTEGRITY_CHECK: 'Integrity check',
  RECOMPUTE: 'Recalculated', DELIVER: 'Delivered',
}
export const actionLabel = (a) => ACTIONS[a] || humanize(a)

const ENTITIES = {
  app_user: 'user', product: 'product', product_version: 'product version', sbom_document: 'SBOM',
  customer: 'customer', solution: 'solution', solution_version: 'solution version', solution_item: 'solution item',
  retention_policy: 'retention policy', retention_record: 'retention records', customer_policy: 'customer regulations',
  solution_delivery: 'delivery',
}
export const entityLabel = (e) => ENTITIES[e] || humanize(e)

// "support_period_end" -> "Support period end", "DELETE_DENIED" -> "Delete denied"
export function humanize(s) {
  if (s == null) return ''
  const t = String(s).replace(/_/g, ' ').toLowerCase()
  return t.charAt(0).toUpperCase() + t.slice(1)
}

const KEY_LABELS = {
  sha256: 'SHA-256', min_years: 'retention years', use_support_period_if_longer: 'extend to end of support',
  warn_days_before_expiry: 'warning days', apply_by_default: 'default for new customers',
  clock_start: 'clock starts at', retain_until: 'retained until', all_ok: 'all files intact',
  support_end: 'customer support end', locked_versions: 'locked versions',
}
const isCode = (s) => /^[A-Z0-9]+(_[A-Z0-9]+)+$/.test(s)          // EU_CRA, JC_STAR
const keyLabel = (k) => KEY_LABELS[k] || (isCode(k) ? k.replace(/_/g, ' ') : humanize(k).toLowerCase())
const topLabel = (k) => { const l = keyLabel(k); return isCode(k) ? l : l.charAt(0).toUpperCase() + l.slice(1) }

export function fmtValue(v) {
  if (v === null || v === undefined || v === '' || v === 'None') return '—'
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (Array.isArray(v)) return v.length ? v.map(fmtValue).join(', ') : 'none'
  if (typeof v === 'object') return Object.entries(v).map(([k, x]) => `${keyLabel(k)} ${fmtValue(x)}`).join(', ')
  if (typeof v === 'number') return fmtNumber(v)
  const s = String(v)
  if (/^\d{4}-\d{2}-\d{2}T/.test(s)) return fmtDateTime(s)
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return fmtDate(s)
  if (/^[0-9a-f]{64}$/.test(s)) return `${s.slice(0, 12)}…`
  if (isCode(s)) return s.replace(/_/g, ' ')
  return /^[a-z]+(_[a-z]+)+$/.test(s) ? humanize(s) : s
}

// Retention date with how long is left underneath
export function retainText(iso) {
  return iso ? { date: fmtDate(iso), rel: fmtRelative(iso) } : { date: '—', rel: '' }
}

// Audit details object -> list of short readable lines
export function detailLines(d) {
  if (!d || typeof d !== 'object') return []
  const plainObj = (x) => x && typeof x === 'object' && !Array.isArray(x)
  if (plainObj(d.before) && plainObj(d.after)) {
    const keys = [...new Set([...Object.keys(d.before), ...Object.keys(d.after)])]
    const changed = keys.filter((k) => JSON.stringify(d.before[k]) !== JSON.stringify(d.after[k]))
    const lines = changed.map((k) => `${topLabel(k)}: ${fmtValue(d.before[k])} → ${fmtValue(d.after[k])}`)
    const rest = Object.entries(d).filter(([k]) => k !== 'before' && k !== 'after')
    return [...(lines.length ? lines : ['No field changed']), ...rest.map(([k, v]) => `${topLabel(k)}: ${fmtValue(v)}`)]
  }
  if (d.before !== undefined && d.after !== undefined) {
    const rest = Object.entries(d).filter(([k]) => k !== 'before' && k !== 'after')
    return [`${fmtValue(d.before)} → ${fmtValue(d.after)}`, ...rest.map(([k, v]) => `${topLabel(k)}: ${fmtValue(v)}`)]
  }
  return Object.entries(d).map(([k, v]) => `${topLabel(k)}: ${fmtValue(v)}`)
}
