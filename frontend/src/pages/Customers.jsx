import { useEffect, useState } from 'react'
import { api, isAdmin } from '../api'
import { Notice, StatusBadge } from '../components'
import { fmtDate, fmtNumber, fmtRelative } from '../format'

// Customers -> solutions -> solution versions -> product versions delivered.
// Each customer also has the regulations of its market (EU CRA, JC-STAR ...).
export default function Customers() {
  const [customers, setCustomers] = useState([])
  const [productVersions, setProductVersions] = useState([])   // for the "add product version" drop-down
  const [policies, setPolicies] = useState([])                 // for the regulation picker
  const [message, setMessage] = useState('')

  async function load() {
    try {
      const [customerList, versionList, policyList] = await Promise.all([
        api.get('/customers'),
        api.get('/product-versions'),
        api.get('/retention/policies'),
      ])
      setCustomers(customerList)
      setProductVersions(versionList)
      setPolicies(policyList)
    } catch (err) {
      setMessage(err.message)
    }
  }
  useEffect(() => { load() }, [])

  // Every change on this page goes through here: run it, show a message, reload.
  async function run(action, successMessage = '') {
    try {
      await action()
      if (successMessage) setMessage(successMessage)
      await load()
    } catch (err) {
      setMessage(err.message)
    }
  }

  function confirmDelete(what, path) {
    if (confirm(`Delete ${what}? This cannot be undone.`)) {
      run(() => api.del(path), `${what} deleted`)
    }
  }

  const shared = { run, confirmDelete, productVersions, showMessage: setMessage }

  return (
    <>
      <h2>Customers & Solutions</h2>
      <p className="muted">
        A solution version (1.0, 2.0 …) is a major release for a customer. Each delivery freezes the consolidated
        SBOM of what was delivered and locks it. A minor update — a newer version of one product — is delivered
        again under the same solution version: the earlier delivery stays locked and the retention date stays the
        same. Delivered SBOMs are kept until the customer's support end or the customer's regulations require,
        whichever is later.
      </p>
      <Notice text={message} onClose={() => setMessage('')} />

      {isAdmin() && <NewCustomerForm run={run} onCreated={setMessage} />}

      {customers.map((customer) => (
        <CustomerCard key={customer.id} customer={customer} policies={policies} {...shared} />
      ))}
    </>
  )
}

function NewCustomerForm({ run, onCreated }) {
  const [name, setName] = useState('')
  const [country, setCountry] = useState('')

  function handleSubmit(event) {
    event.preventDefault()
    run(async () => {
      const created = await api.post('/customers', { name, country })
      setName('')
      setCountry('')
      onCreated(`Customer ${created.code} created`)
    })
  }

  return (
    <form className="card inline" onSubmit={handleSubmit}>
      <h3>New customer</h3>
      <input placeholder="Customer name" value={name} onChange={(e) => setName(e.target.value)} required />
      <input placeholder="Country" value={country} onChange={(e) => setCountry(e.target.value)} />
      <button className="primary">Add customer</button>
    </form>
  )
}

function CustomerCard({ customer, policies, run, confirmDelete, productVersions, showMessage }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(customer.name)
  const [country, setCountry] = useState(customer.country || '')
  const [newSolutionName, setNewSolutionName] = useState('')

  function startEditing() {
    setName(customer.name)
    setCountry(customer.country || '')
    setEditing(true)
  }

  function save(event) {
    event.preventDefault()
    run(async () => {
      await api.put(`/customers/${customer.id}`, { name, country: country || null })
      setEditing(false)
    }, 'Customer updated')
  }

  function addSolution() {
    run(async () => {
      await api.post(`/customers/${customer.id}/solutions`, { name: newSolutionName })
      setNewSolutionName('')
    })
  }

  return (
    <div className="card">
      {editing ? (
        <form className="inline" onSubmit={save}>
          <b>{customer.code}</b>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Customer name" required />
          <input value={country} onChange={(e) => setCountry(e.target.value)} placeholder="Country" />
          <button className="primary">Save</button>
          <button type="button" onClick={() => setEditing(false)}>Cancel</button>
        </form>
      ) : (
        <div className="row">
          <h3>{customer.code} — {customer.name} <small className="muted">{customer.country}</small></h3>
          {isAdmin() && (
            <div className="actions">
              <button onClick={startEditing}>Edit</button>
              <button className="danger" onClick={() => confirmDelete(`customer ${customer.name}`, `/customers/${customer.id}`)}>
                Delete
              </button>
            </div>
          )}
        </div>
      )}

      <RegulationPicker customer={customer} policies={policies} run={run} />

      {customer.solutions.map((solution) => (
        <SolutionBlock
          key={solution.id}
          solution={solution}
          run={run}
          confirmDelete={confirmDelete}
          productVersions={productVersions}
          showMessage={showMessage}
        />
      ))}

      {isAdmin() && (
        <div className="inline">
          <input placeholder="New solution name" value={newSolutionName} onChange={(e) => setNewSolutionName(e.target.value)} />
          <button disabled={!newSolutionName} onClick={addSolution}>Add solution</button>
        </div>
      )}
    </div>
  )
}

// Shows the customer's regulations as labels; "Change" opens a list of checkboxes.
function RegulationPicker({ customer, policies, run }) {
  const [selectedIds, setSelectedIds] = useState(null)   // null = not editing
  const editing = selectedIds !== null

  function toggle(policyId) {
    if (selectedIds.includes(policyId)) {
      setSelectedIds(selectedIds.filter((id) => id !== policyId))
    } else {
      setSelectedIds([...selectedIds, policyId])
    }
  }

  function save() {
    run(async () => {
      await api.put(`/customers/${customer.id}/policies`, { policy_ids: selectedIds })
      setSelectedIds(null)
    }, 'Regulations saved; retention recomputed for everything delivered to this customer')
  }

  if (editing) {
    return (
      <div className="sv">
        <b>Regulations that apply to {customer.name}</b>
        <div className="chips">
          {policies.map((policy) => (
            <label key={policy.id} className="check">
              <input type="checkbox" checked={selectedIds.includes(policy.id)} onChange={() => toggle(policy.id)} />
              {policy.name}{!policy.is_active && ' (inactive)'}
            </label>
          ))}
        </div>
        <div className="actions">
          <button className="primary" onClick={save}>Save</button>
          <button onClick={() => setSelectedIds(null)}>Cancel</button>
        </div>
      </div>
    )
  }

  return (
    <div className="chips">
      <span className="muted small">Regulations:</span>
      {customer.policies.length === 0 && (
        <span className="muted small">none — products delivered here are not under retention for this customer</span>
      )}
      {customer.policies.map((policy) => (
        <span key={policy.id} className={`chip ${policy.is_active ? '' : 'off'}`}>{policy.name}</span>
      ))}
      {isAdmin() && (
        <button className="link-blue" onClick={() => setSelectedIds(customer.policies.map((p) => p.id))}>Change</button>
      )}
    </div>
  )
}

function SolutionBlock({ solution, run, confirmDelete, productVersions, showMessage }) {
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(solution.name)
  const [newVersion, setNewVersion] = useState('')
  const [newSupportEnd, setNewSupportEnd] = useState('')

  function saveName(event) {
    event.preventDefault()
    run(async () => {
      await api.put(`/solutions/${solution.id}`, { name })
      setRenaming(false)
    }, 'Solution renamed')
  }

  function addVersion() {
    run(async () => {
      await api.post(`/solutions/${solution.id}/versions`, { version: newVersion, support_end: newSupportEnd || null })
      setNewVersion('')
      setNewSupportEnd('')
    })
  }

  return (
    <div className="sub">
      {renaming ? (
        <form className="inline" onSubmit={saveName}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Solution name" required />
          <button className="primary">Save</button>
          <button type="button" onClick={() => setRenaming(false)}>Cancel</button>
        </form>
      ) : (
        <div className="row">
          <h4>Solution: {solution.name}</h4>
          {isAdmin() && (
            <div className="actions">
              <button onClick={() => { setName(solution.name); setRenaming(true) }}>Rename</button>
              <button className="danger" onClick={() => confirmDelete(`solution ${solution.name}`, `/solutions/${solution.id}`)}>
                Delete
              </button>
            </div>
          )}
        </div>
      )}

      {solution.versions.map((solutionVersion) => (
        <SolutionVersionBlock
          key={solutionVersion.id}
          solutionVersion={solutionVersion}
          run={run}
          confirmDelete={confirmDelete}
          productVersions={productVersions}
          showMessage={showMessage}
        />
      ))}

      {isAdmin() && (
        <div className="inline">
          <input placeholder="New major version (e.g. 2.0)" value={newVersion} onChange={(e) => setNewVersion(e.target.value)} />
          <label className="check">Customer support end&nbsp;
            <input type="date" value={newSupportEnd} onChange={(e) => setNewSupportEnd(e.target.value)} />
          </label>
          <button disabled={!newVersion} onClick={addVersion}>Add solution version</button>
        </div>
      )}
    </div>
  )
}

// One major version of a solution (1.0, 2.0 ...).
// The product list is the working list for the next delivery. "Deliver" freezes it,
// with its consolidated SBOM, as a locked delivery. Changing a product to a newer
// version and delivering again is a minor update: delivery 2, 3 ... of the same version.
function SolutionVersionBlock({ solutionVersion, run, confirmDelete, productVersions, showMessage }) {
  const [editing, setEditing] = useState(false)
  const [version, setVersion] = useState(solutionVersion.version)
  const [supportEnd, setSupportEnd] = useState(solutionVersion.support_end || '')
  const [productToAdd, setProductToAdd] = useState('')
  const [deliveryDate, setDeliveryDate] = useState('')
  const [deliveryNote, setDeliveryNote] = useState('')

  const hasProducts = solutionVersion.items.length > 0
  const deliveries = solutionVersion.deliveries
  const isFirstDelivery = deliveries.length === 0
  const canDeliver = hasProducts && (isFirstDelivery || solutionVersion.undelivered_changes)
  const retention = solutionVersion.retention

  function startEditing() {
    setVersion(solutionVersion.version)
    setSupportEnd(solutionVersion.support_end || '')
    setEditing(true)
  }

  function save(event) {
    event.preventDefault()
    run(async () => {
      await api.put(`/solution-versions/${solutionVersion.id}`, { version, support_end: supportEnd || null })
      setEditing(false)
    }, 'Solution version updated; retention recomputed')
  }

  function previewAs(format, fileName) {
    api.download(`/solution-versions/${solutionVersion.id}/consolidated?format=${format}`, fileName)
      .catch((err) => showMessage(err.message))
  }

  function addProductVersion() {
    run(async () => {
      await api.post(`/solution-versions/${solutionVersion.id}/items`, { product_version_id: Number(productToAdd) })
      setProductToAdd('')
    })
  }

  function deliver() {
    const what = isFirstDelivery ? 'Deliver' : 'Deliver this minor update'
    if (!confirm(`${what}? The SBOM of this delivery will be locked and can no longer be changed.`)) return
    run(async () => {
      const result = await api.post(`/solution-versions/${solutionVersion.id}/deliveries`, {
        delivered_on: deliveryDate || null,
        note: deliveryNote || null,
      })
      setDeliveryDate('')
      setDeliveryNote('')
      showMessage(`Delivery ${result.number} recorded: ${result.components} components, SBOM locked (sha256 ${result.sha256.slice(0, 12)}…)`)
    })
  }

  async function verify(delivery) {
    try {
      const result = await api.post(`/deliveries/${delivery.id}/verify`)
      showMessage(result.intact
        ? `Delivery ${delivery.number}: locked SBOM is intact`
        : `Delivery ${delivery.number}: INTEGRITY FAILURE — the locked SBOM was changed`)
    } catch (err) {
      showMessage(err.message)
    }
  }

  return (
    <div className="sv">
      {editing ? (
        <form className="inline" onSubmit={save}>
          <label>Version<input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="1.0" required /></label>
          <label>Customer support end<input type="date" value={supportEnd} onChange={(e) => setSupportEnd(e.target.value)} /></label>
          <button className="primary">Save</button>
          <button type="button" onClick={() => setEditing(false)}>Cancel</button>
        </form>
      ) : (
        <div className="row">
          <span>
            <b>v{solutionVersion.version}</b>{' '}
            <span className="muted">
              {solutionVersion.support_end ? `support until ${fmtDate(solutionVersion.support_end)}` : 'no support end set'}
            </span>{' '}
            <StatusBadge status={retention.status} />{' '}
            {retention.retain_until && (
              <span className="muted small">keep until {fmtDate(retention.retain_until)} ({fmtRelative(retention.retain_until)})</span>
            )}
          </span>
          <div className="actions">
            {isAdmin() && <button onClick={startEditing}>Edit</button>}
            {isAdmin() && (
              <button
                className="danger"
                onClick={() => confirmDelete(`solution version ${solutionVersion.version}`, `/solution-versions/${solutionVersion.id}`)}
              >
                Delete
              </button>
            )}
          </div>
        </div>
      )}
      {retention.basis && <div className="muted small">{retention.basis}</div>}

      <DeliveryHistory deliveries={deliveries} onVerify={verify} />

      <h4 className="small">
        {isFirstDelivery ? 'Products to deliver' : 'Products for the next minor update'}
        {solutionVersion.undelivered_changes && !isFirstDelivery && <span className="chip">changed since delivery {deliveries.length}</span>}
      </h4>
      <ul>
        {solutionVersion.items.map((item) => (
          <li key={item.id}>
            {item.label} × {item.quantity}
            {isAdmin() && (
              <button className="link" onClick={() => run(() => api.del(`/solution-items/${item.id}`))}>remove</button>
            )}
          </li>
        ))}
      </ul>

      {isAdmin() && (
        <div className="inline">
          <select value={productToAdd} onChange={(e) => setProductToAdd(e.target.value)}>
            <option value="">— add product version —</option>
            {productVersions.map((pv) => <option key={pv.id} value={pv.id}>{pv.label}</option>)}
          </select>
          <button disabled={!productToAdd} onClick={addProductVersion}>Add</button>
          <button disabled={!hasProducts} onClick={() => previewAs('cyclonedx', 'preview.cdx.json')}>Preview CycloneDX</button>
          <button disabled={!hasProducts} onClick={() => previewAs('spdx', 'preview.spdx.json')}>Preview SPDX</button>
        </div>
      )}

      {isAdmin() && (
        <div className="inline">
          <label>Delivery date<input type="date" value={deliveryDate} onChange={(e) => setDeliveryDate(e.target.value)} /></label>
          <input
            placeholder={isFirstDelivery ? 'Note (optional)' : 'What changed, e.g. P02 1.27 → 1.28'}
            value={deliveryNote}
            onChange={(e) => setDeliveryNote(e.target.value)}
            style={{ flex: 1 }}
          />
          <button className="primary" disabled={!canDeliver} onClick={deliver}>
            {isFirstDelivery ? 'Deliver and lock SBOM' : 'Deliver minor update'}
          </button>
        </div>
      )}
    </div>
  )
}

// Every delivery of one solution version, oldest first. Each one's SBOM is locked.
function DeliveryHistory({ deliveries, onVerify }) {
  if (deliveries.length === 0) {
    return <p className="muted small">Not delivered yet — nothing is locked.</p>
  }

  return (
    <table>
      <thead>
        <tr><th>Delivery</th><th>Date</th><th>Products delivered</th><th>Components</th><th>Locked SBOM</th><th></th></tr>
      </thead>
      <tbody>
        {deliveries.map((delivery) => (
          <tr key={delivery.id}>
            <td>
              <b>{delivery.number}</b>
              {delivery.note && <div className="muted small">{delivery.note}</div>}
            </td>
            <td>{fmtDate(delivery.delivered_on)}</td>
            <td className="small">{delivery.products.join(', ')}</td>
            <td>{fmtNumber(delivery.component_count)}</td>
            <td className="small">
              🔒 {delivery.intact ? 'intact' : <b>changed!</b>}
              <div className="mono muted">{delivery.sha256.slice(0, 12)}…</div>
            </td>
            <td className="actions">
              <button onClick={() => api.download(`/deliveries/${delivery.id}/sbom?format=cyclonedx`, `delivery-${delivery.number}.cdx.json`)}>CycloneDX 1.5</button>
              {delivery.spdx_sha256 && (
                <button onClick={() => api.download(`/deliveries/${delivery.id}/sbom?format=spdx`, `delivery-${delivery.number}.spdx.json`)}>SPDX 2.3</button>
              )}
              <button onClick={() => onVerify(delivery)}>Verify</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
