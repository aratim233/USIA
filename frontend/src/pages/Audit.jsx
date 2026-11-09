import { useEffect, useState } from 'react'
import { api } from '../api'
import { actionLabel, detailLines, entityLabel, fmtDateTime } from '../format'

export default function Audit() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  useEffect(() => { api.get('/audit').then(setRows).catch((e) => setErr(e.message)) }, [])
  return (
    <>
      <h2>Audit Log</h2>
      <p className="muted">Every change made through the service layer is recorded here, newest first.</p>
      {err && <div className="error">{err}</div>}
      <div className="card">
        <table>
          <thead><tr><th>When</th><th>Who</th><th>What happened</th><th>Details</th></tr></thead>
          <tbody>{rows.map((a) => (
            <tr key={a.id}>
              <td>{fmtDateTime(a.at)}</td>
              <td>{a.username || 'system'}</td>
              <td><span className={`badge ${a.action === 'DELETE_DENIED' ? 'EXPIRED' : ''}`}>{actionLabel(a.action)}</span>{' '}
                {entityLabel(a.entity)}{a.entity_id != null && <span className="muted"> #{a.entity_id}</span>}</td>
              <td className="small">{detailLines(a.details).map((l, i) => <div key={i}>{l}</div>)}</td>
            </tr>))}
          </tbody>
        </table>
      </div>
    </>
  )
}
