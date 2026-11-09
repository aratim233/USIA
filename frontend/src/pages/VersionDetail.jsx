import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmtBytes, isAdmin } from '../api'
import { KeepUntil, Notice, StatusBadge } from '../components'
import { fmtDate, fmtDateTime, fmtNumber, fmtRelative } from '../format'

// One product version: its retention, its stored SBOMs and the consolidated SBOM.
export default function VersionDetail() {
  const { id } = useParams()
  const [version, setVersion] = useState(null)
  const [consolidated, setConsolidated] = useState(null)
  const [openSbom, setOpenSbom] = useState(null)      // the SBOM whose components are being viewed
  const [message, setMessage] = useState('')

  async function load() {
    try {
      setVersion(await api.get(`/versions/${id}`))
      setConsolidated(await api.get(`/versions/${id}/components`))
    } catch (err) {
      setMessage(err.message)
    }
  }
  useEffect(() => { load() }, [id])

  async function runIntegrityCheck() {
    const result = await api.post(`/versions/${id}/integrity`)
    if (result.all_ok) {
      setMessage(`Integrity verified: all ${result.documents.length} originals match their SHA-256`)
    } else {
      const changedFiles = result.documents.filter((d) => !d.ok).map((d) => d.file_name)
      setMessage(`INTEGRITY FAILURE: ${changedFiles.join(', ')}`)
    }
    load()
  }

  async function deleteSbom(sbomId) {
    if (!confirm('Delete this SBOM?')) return
    try {
      await api.del(`/sboms/${sbomId}`)
      load()
    } catch (err) {
      setMessage(err.message)   // e.g. "SBOM is under retention ... deletion refused"
    }
  }

  async function viewComponents(sbom) {
    const rows = await api.get(`/sboms/${sbom.id}/components`)
    setOpenSbom({ name: sbom.file_name, rows })
  }

  if (!version) return <p>{message || 'Loading…'}</p>

  return (
    <>
      <p><Link to="/products">← Products</Link></p>
      <h2>{version.product.code} {version.product.name} — v{version.version}</h2>
      <Notice text={message} onClose={() => setMessage('')} />

      <div className="grid2">
        <RetentionCard version={version} onIntegrityCheck={runIntegrityCheck} />
        {isAdmin() && <LifecycleDatesCard key={version.id} version={version} onSaved={load} onMessage={setMessage} />}
      </div>

      {isAdmin() && <UploadForm versionId={id} onUploaded={load} onMessage={setMessage} />}

      <StoredSboms sboms={version.sboms} onView={viewComponents} onDelete={deleteSbom} />

      {openSbom && <SbomComponents sbom={openSbom} onClose={() => setOpenSbom(null)} />}

      {consolidated && <ConsolidatedSbom versionId={id} data={consolidated} />}
    </>
  )
}

function RetentionCard({ version, onIntegrityCheck }) {
  const summary = version.retention

  let integrityText = 'Not checked yet'
  if (summary && summary.integrity_ok != null) {
    integrityText = summary.integrity_ok ? '✔ All originals intact' : '✘ A stored file was changed'
    if (summary.last_integrity_check) integrityText += ` · checked ${fmtDateTime(summary.last_integrity_check)}`
  }

  return (
    <div className="card">
      <h3>Retention</h3>

      {!summary && (
        <p className="muted">
          This version has not been delivered to a customer yet, so its SBOMs are not under retention.
          Add it to a customer's solution and press Deliver on the Customers page.
        </p>
      )}

      {summary && (
        <>
          <div className="row"><span>Status</span><StatusBadge status={summary.status} /></div>
          <div className="row">
            <span>Keep until (binding)</span>
            <span><b>{fmtDate(summary.retain_until)}</b> <span className="muted small">{fmtRelative(summary.retain_until)}</span></span>
          </div>
          <div className="row"><span>Basis</span><span>{summary.basis}</span></div>
          <div className="row">
            <span>Deletion</span>
            <span>{summary.locked ? 'Locked while any retention period runs' : 'Allowed'}</span>
          </div>
          <div className="row"><span>Integrity</span><span>{integrityText}</span></div>
        </>
      )}

      {version.retention_records.length > 0 && (
        <table>
          <thead>
            <tr><th>Regulation</th><th>Required by</th><th>Keep until</th><th>Status</th><th>Rule applied</th></tr>
          </thead>
          <tbody>
            {version.retention_records.map((record) => (
              <tr key={record.policy_id}>
                <td title={record.legal_basis}>{record.policy_name}</td>
                <td className="small">{record.required_by.join(', ')}</td>
                <td><KeepUntil date={record.retain_until} /></td>
                <td><StatusBadge status={record.status} /></td>
                <td className="small">{record.basis}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <button onClick={onIntegrityCheck}>Run integrity check</button>
    </div>
  )
}

function LifecycleDatesCard({ version, onSaved, onMessage }) {
  const [placedOnMarket, setPlacedOnMarket] = useState(version.placed_on_market || '')
  const [supportEnd, setSupportEnd] = useState(version.support_period_end || '')

  async function save() {
    try {
      await api.patch(`/versions/${version.id}`, {
        version: version.version,
        placed_on_market: placedOnMarket || null,
        support_period_end: supportEnd || null,
      })
      onMessage('Dates updated; retention recomputed')
      onSaved()
    } catch (err) {
      onMessage(err.message)
    }
  }

  return (
    <div className="card">
      <h3>Lifecycle dates</h3>
      <label>
        Placed on market
        <input type="date" value={placedOnMarket} onChange={(e) => setPlacedOnMarket(e.target.value)} />
      </label>
      <label>
        Support period end
        <input type="date" value={supportEnd} onChange={(e) => setSupportEnd(e.target.value)} />
      </label>
      <button onClick={save}>Save</button>
    </div>
  )
}

function UploadForm({ versionId, onUploaded, onMessage }) {
  const [microservice, setMicroservice] = useState('')
  const [file, setFile] = useState(null)

  async function upload(event) {
    event.preventDefault()
    const formData = new FormData()
    formData.append('microservice', microservice)
    formData.append('file', file)

    try {
      const result = await api.form(`/versions/${versionId}/sboms`, formData)
      onMessage(`Uploaded ${result.format} SBOM with ${result.components} components (sha256 ${result.sha256.slice(0, 12)}…)`)
      setMicroservice('')
      setFile(null)
      event.target.reset()    // clears the file picker
      onUploaded()
    } catch (err) {
      onMessage(err.message)
    }
  }

  return (
    <form className="card inline" onSubmit={upload}>
      <h3>Upload SBOM (CycloneDX or SPDX JSON)</h3>
      <input placeholder="Microservice name" value={microservice} onChange={(e) => setMicroservice(e.target.value)} required />
      <input type="file" accept=".json" onChange={(e) => setFile(e.target.files[0])} required />
      <button className="primary">Upload</button>
    </form>
  )
}

function StoredSboms({ sboms, onView, onDelete }) {
  return (
    <div className="card">
      <h3>Stored SBOMs (originals preserved)</h3>
      <table>
        <thead>
          <tr>
            <th>Microservice</th><th>File</th><th>Format</th><th>Tool</th>
            <th>Components</th><th>Size</th><th>SHA-256</th><th></th>
          </tr>
        </thead>
        <tbody>
          {sboms.map((sbom) => (
            <tr key={sbom.id}>
              <td>{sbom.microservice}</td>
              <td>{sbom.file_name}</td>
              <td>{sbom.format} {sbom.spec_version}</td>
              <td>
                {sbom.generator_tool || '—'}
                <div className="muted small">
                  uploaded {fmtDateTime(sbom.uploaded_at)}
                  {sbom.uploaded_by && ` by ${sbom.uploaded_by}`}
                </div>
              </td>
              <td>{fmtNumber(sbom.component_count)}</td>
              <td>{fmtBytes(sbom.size_bytes)}</td>
              <td className="mono">{sbom.sha256.slice(0, 16)}…</td>
              <td className="actions">
                <button onClick={() => onView(sbom)}>View</button>
                <button onClick={() => api.download(`/sboms/${sbom.id}/original`, sbom.file_name)}>Original</button>
                {isAdmin() && <button className="danger" onClick={() => onDelete(sbom.id)}>Delete</button>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// Components of one SBOM: the PURL as declared in the file next to our normalised one.
function SbomComponents({ sbom, onClose }) {
  return (
    <div className="card">
      <div className="row">
        <h3>Components in {sbom.name}</h3>
        <button onClick={onClose}>Close</button>
      </div>
      <table>
        <thead>
          <tr><th>Name</th><th>Version</th><th>Declared PURL</th><th>Canonical PURL</th><th>Licences</th><th>Supplier</th></tr>
        </thead>
        <tbody>
          {sbom.rows.map((component, index) => (
            <tr key={index}>
              <td>{component.name}</td>
              <td>{component.version}</td>
              <td className="mono">{component.declared_purl || '— (none)'}</td>
              <td className="mono">{component.canonical_purl}</td>
              <td>{(component.licenses || []).join(', ')}</td>
              <td>{component.supplier}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// All SBOMs of this version merged into one list, with each component listed once.
function ConsolidatedSbom({ versionId, data }) {
  function exportAs(format, fileName) {
    api.download(`/versions/${versionId}/consolidated?format=${format}`, fileName)
  }

  return (
    <div className="card">
      <div className="row">
        <h3>
          Consolidated SBOM — {fmtNumber(data.unique_count)} unique of {fmtNumber(data.raw_count)} declared components
        </h3>
        <div className="actions">
          <button className="primary" onClick={() => exportAs('cyclonedx', 'consolidated.cdx.json')}>Export CycloneDX 1.5</button>
          <button className="primary" onClick={() => exportAs('spdx', 'consolidated.spdx.json')}>Export SPDX 2.3</button>
        </div>
      </div>
      <table>
        <thead>
          <tr><th>Component</th><th>Version</th><th>PURL</th><th>Licences</th><th>Found in</th></tr>
        </thead>
        <tbody>
          {data.components.map((component) => (
            <tr key={component.purl}>
              <td>{component.name}</td>
              <td>{component.version}</td>
              <td className="mono">{component.purl}</td>
              <td>{component.licenses.join(', ')}</td>
              <td>{[...new Set(component.found_in)].join(', ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
