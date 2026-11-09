import { useEffect, useState } from 'react'
import { api, fmtBytes } from '../api'
import { StatusBadge } from '../components'
import { fmtNumber } from '../format'

const STATUSES = ['ACTIVE', 'EXPIRING', 'EXPIRED', 'PENDING', 'NOT DELIVERED']

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get('/dashboard').then(setStats).catch((err) => setError(err.message))
  }, [])

  if (error) return <div className="error">{error}</div>
  if (!stats) return <p>Loading…</p>

  const tiles = [
    { label: 'Products', value: fmtNumber(stats.products) },
    { label: 'Product versions', value: fmtNumber(stats.product_versions) },
    { label: 'Stored SBOMs', value: fmtNumber(stats.sboms) },
    { label: 'Unique components', value: fmtNumber(stats.components) },
    { label: 'Components with a package URL', value: `${stats.purl_coverage_pct}%` },
    { label: 'Evidence storage', value: fmtBytes(stats.storage_bytes) },
    { label: 'Customers', value: fmtNumber(stats.customers) },
    { label: 'Solutions', value: fmtNumber(stats.solutions) },
    { label: 'Locked deliveries', value: fmtNumber(stats.deliveries) },
  ]
  const formats = Object.entries(stats.formats)

  return (
    <>
      <h2>Dashboard</h2>

      <div className="tiles">
        {tiles.map((tile) => (
          <div className="tile" key={tile.label}>
            <div className="v">{tile.value}</div>
            <div className="k">{tile.label}</div>
          </div>
        ))}
      </div>

      <div className="grid2">
        <div className="card">
          <h3>Retention status per product version</h3>
          <p className="muted small">
            Binding (latest) date across the regulations of every customer that received the version
            · {stats.policies} active policies
          </p>
          {STATUSES.map((status) => (
            <div key={status} className="row">
              <StatusBadge status={status} />
              <b>{fmtNumber(stats.retention[status] || 0)}</b>
            </div>
          ))}
        </div>

        <div className="card">
          <h3>SBOM formats stored</h3>
          {formats.length === 0 && <p className="muted">No SBOMs uploaded yet.</p>}
          {formats.map(([format, total]) => (
            <div key={format} className="row">
              <span>{format}</span>
              <b>{fmtNumber(total)} {total === 1 ? 'file' : 'files'}</b>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
