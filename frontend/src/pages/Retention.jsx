import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, isAdmin } from '../api'
import { KeepUntil, Notice, StatusBadge } from '../components'
import { fmtDate } from '../format'

// One retention policy per regulation or compliance scheme (EU CRA, EU MDR, JC-STAR, internal ...).
const EMPTY_POLICY = {
  code: '',
  name: '',
  legal_basis: '',
  description: '',
  min_years: 10,
  clock_start: 'placed_on_market',
  use_support_period_if_longer: true,
  warn_days_before_expiry: 365,
  apply_by_default: false,
  is_active: true,
  apply_to_all_customers: false,
}

const CLOCK_OPTIONS = {
  placed_on_market: 'Placing on market',
  delivered_to_customer: 'Delivery to the customer',
  support_period_end: 'End of support',
}

// "10 years after placing on market, or end of support if later"
function describeRule(policy) {
  const clock = (CLOCK_OPTIONS[policy.clock_start] || policy.clock_start).toLowerCase()
  const rule = `${policy.min_years} years after ${clock}`
  return policy.use_support_period_if_longer ? `${rule}, or end of support if later` : rule
}

// The form sends numbers as numbers and an empty description as null.
function toRequestBody(policy) {
  return {
    code: policy.code,
    name: policy.name,
    legal_basis: policy.legal_basis,
    description: policy.description || null,
    min_years: Number(policy.min_years),
    clock_start: policy.clock_start,
    use_support_period_if_longer: policy.use_support_period_if_longer,
    warn_days_before_expiry: Number(policy.warn_days_before_expiry),
    apply_by_default: policy.apply_by_default,
    is_active: policy.is_active,
  }
}

export default function Retention() {
  const [data, setData] = useState(null)          // { policies, records }
  const [message, setMessage] = useState('')
  const [newPolicy, setNewPolicy] = useState(null) // the "New policy" form, null when closed
  const [editedPolicy, setEditedPolicy] = useState(null)
  const [regulationFilter, setRegulationFilter] = useState('')

  function load() {
    api.get('/retention').then(setData).catch((err) => setMessage(err.message))
  }
  useEffect(load, [])

  async function run(action, successMessage) {
    try {
      await action()
      setMessage(successMessage)
      load()
    } catch (err) {
      setMessage(err.message)
    }
  }

  function createPolicy() {
    run(async () => {
      await api.post('/retention/policies', {
        ...toRequestBody(newPolicy),
        apply_to_all_customers: newPolicy.apply_to_all_customers,
      })
      setNewPolicy(null)
    }, `Policy ${newPolicy.name} created`)
  }

  function savePolicy() {
    run(async () => {
      await api.put(`/retention/policies/${editedPolicy.id}`, toRequestBody(editedPolicy))
      setEditedPolicy(null)
    }, 'Policy saved; retention recomputed for every product version')
  }

  function deletePolicy(policy) {
    if (!confirm(`Delete policy ${policy.name}?`)) return
    run(() => api.del(`/retention/policies/${policy.id}`), `Policy ${policy.name} deleted`)
  }

  if (!data) return <p>{message || 'Loading…'}</p>

  const records = regulationFilter
    ? data.records.filter((record) => record.policy_code === regulationFilter)
    : data.records

  return (
    <>
      <h2>Retention & Compliance</h2>
      <p className="muted">
        Each regulation or compliance scheme has its own retention rule, and each customer falls under the
        regulations of its market (choose them on the Customers page). A product version delivered to a customer
        is kept under all of that customer's regulations, and at least until the support end agreed for that
        solution version; its SBOMs cannot be deleted until every applicable period has ended.
      </p>
      <Notice text={message} onClose={() => setMessage('')} />

      <div className="card">
        <div className="row">
          <h3>Compliance policies</h3>
          {isAdmin() && !newPolicy && (
            <button className="primary" onClick={() => setNewPolicy(EMPTY_POLICY)}>New policy</button>
          )}
        </div>

        {newPolicy && (
          <div className="sv">
            <h4>New policy</h4>
            <PolicyForm
              value={newPolicy}
              onChange={setNewPolicy}
              onSubmit={createPolicy}
              onCancel={() => setNewPolicy(null)}
              submitLabel="Create policy"
              isNew
            />
          </div>
        )}

        <table>
          <thead>
            <tr>
              <th>Code</th><th>Regulation</th><th>Legal basis</th><th>Retention rule</th>
              <th>Customers</th><th>Status</th><th></th>
            </tr>
          </thead>
          <tbody>
            {data.policies.map((policy) =>
              editedPolicy?.id === policy.id ? (
                <tr key={policy.id}>
                  <td colSpan="7">
                    <PolicyForm
                      value={editedPolicy}
                      onChange={setEditedPolicy}
                      onSubmit={savePolicy}
                      onCancel={() => setEditedPolicy(null)}
                      submitLabel="Save policy"
                    />
                  </td>
                </tr>
              ) : (
                <PolicyRow
                  key={policy.id}
                  policy={policy}
                  onEdit={() => setEditedPolicy({ ...policy })}
                  onDelete={() => deletePolicy(policy)}
                />
              )
            )}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="row">
          <h3>Retention per product version</h3>
          <label className="check">
            Regulation&nbsp;
            <select value={regulationFilter} onChange={(e) => setRegulationFilter(e.target.value)}>
              <option value="">All</option>
              {data.policies.map((policy) => <option key={policy.id} value={policy.code}>{policy.name}</option>)}
            </select>
          </label>
        </div>

        <table>
          <thead>
            <tr>
              <th>Product version</th><th>Regulation</th><th>Required by</th><th>Placed on market</th>
              <th>Support end</th><th>Keep until</th><th>Status</th><th>Basis</th><th>Integrity</th>
            </tr>
          </thead>
          <tbody>
            {records.map((record) => (
              <RecordRow key={`${record.product_version_id}-${record.policy_id}`} record={record} />
            ))}
            {records.length === 0 && (
              <tr>
                <td colSpan="9" className="muted">
                  No retention records yet. Give a customer its regulations and add product versions to its solutions.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </>
  )
}

function PolicyRow({ policy, onEdit, onDelete }) {
  return (
    <tr>
      <td className="mono">{policy.code.replace(/_/g, ' ')}</td>
      <td>
        <b>{policy.name}</b>
        {policy.description && <div className="muted small">{policy.description}</div>}
      </td>
      <td>{policy.legal_basis}</td>
      <td>{describeRule(policy)}</td>
      <td>
        {policy.customer_count}
        {policy.apply_by_default && <div className="muted small">default for new</div>}
      </td>
      <td>
        <span className={`badge ${policy.is_active ? 'ACTIVE' : ''}`}>{policy.is_active ? 'In use' : 'Switched off'}</span>
      </td>
      <td className="actions">
        {isAdmin() && <button onClick={onEdit}>Edit</button>}
        {isAdmin() && <button className="danger" onClick={onDelete}>Delete</button>}
      </td>
    </tr>
  )
}

function RecordRow({ record }) {
  let integrity = 'Not checked'
  if (record.integrity_ok === true) integrity = '✔ Intact'
  if (record.integrity_ok === false) integrity = '✘ Changed'

  return (
    <tr>
      <td><Link to={`/versions/${record.product_version_id}`}>{record.label}</Link></td>
      <td>{record.policy_name}</td>
      <td className="small">{record.required_by.join(', ')}</td>
      <td>{fmtDate(record.placed_on_market)}</td>
      <td>{fmtDate(record.support_period_end)}</td>
      <td><b><KeepUntil date={record.retain_until} /></b></td>
      <td><StatusBadge status={record.status} /></td>
      <td>{record.basis}</td>
      <td>{integrity}</td>
    </tr>
  )
}

// Used both for creating a policy and for editing one.
function PolicyForm({ value, onChange, onSubmit, onCancel, submitLabel, isNew }) {
  function set(field, fieldValue) {
    onChange({ ...value, [field]: fieldValue })
  }

  function handleSubmit(event) {
    event.preventDefault()
    onSubmit()
  }

  return (
    <form className="policy-form" onSubmit={handleSubmit}>
      <div className="inline">
        <label>
          Code
          <input placeholder="JC_STAR" value={value.code} onChange={(e) => set('code', e.target.value)} required />
        </label>
        <label className="grow">
          Name
          <input
            placeholder="Japan JC-STAR labelling scheme"
            value={value.name}
            onChange={(e) => set('name', e.target.value)}
            required
          />
        </label>
        <label className="grow">
          Legal basis / reference
          <input
            placeholder="Regulation, article or scheme document"
            value={value.legal_basis}
            onChange={(e) => set('legal_basis', e.target.value)}
            required
          />
        </label>
      </div>

      <div className="inline">
        <label>
          Retention (years)
          <input type="number" min="0" max="100" value={value.min_years} onChange={(e) => set('min_years', e.target.value)} required />
        </label>
        <label>
          Clock starts at
          <select value={value.clock_start} onChange={(e) => set('clock_start', e.target.value)}>
            {Object.entries(CLOCK_OPTIONS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        <label>
          Warn days before expiry
          <input
            type="number"
            min="0"
            value={value.warn_days_before_expiry}
            onChange={(e) => set('warn_days_before_expiry', e.target.value)}
          />
        </label>
        <Checkbox label="Extend to end of support if later" checked={value.use_support_period_if_longer}
          onChange={(checked) => set('use_support_period_if_longer', checked)} />
        <Checkbox label="Apply to new customers" checked={value.apply_by_default}
          onChange={(checked) => set('apply_by_default', checked)} />
        <Checkbox label="Active" checked={value.is_active}
          onChange={(checked) => set('is_active', checked)} />
        {isNew && (
          <Checkbox label="Apply to all existing customers now" checked={value.apply_to_all_customers}
            onChange={(checked) => set('apply_to_all_customers', checked)} />
        )}
      </div>

      <label>
        Description
        <input
          placeholder="What must be kept and why"
          value={value.description || ''}
          onChange={(e) => set('description', e.target.value)}
        />
      </label>

      <div className="actions">
        <button className="primary">{submitLabel}</button>
        {onCancel && <button type="button" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  )
}

function Checkbox({ label, checked, onChange }) {
  return (
    <label className="check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /> {label}
    </label>
  )
}
