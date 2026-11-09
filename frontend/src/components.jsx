// Small building blocks used on several pages.
import { fmtDate, fmtRelative, statusLabel } from './format'

// A message bar at the top of a page. Click it to dismiss.
export function Notice({ text, onClose }) {
  if (!text) return null
  return <div className="info" onClick={onClose}>{text}</div>
}

// Coloured label for a retention status: "Retained", "Expiring soon" ...
export function StatusBadge({ status }) {
  return <span className={`badge ${status}`}>{statusLabel(status)}</span>
}

// A retention date with how long is left underneath: "8 Nov 2036 / in 10 years".
export function KeepUntil({ date, note }) {
  if (!date) return '—'
  return (
    <>
      {fmtDate(date)}
      <div className="muted small">
        {fmtRelative(date)}
        {note && ` · ${note}`}
      </div>
    </>
  )
}
