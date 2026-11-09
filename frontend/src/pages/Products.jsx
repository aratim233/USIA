import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, isAdmin } from '../api'
import { KeepUntil, Notice, StatusBadge } from '../components'
import { fmtDate, fmtNumber } from '../format'

const EMPTY_PRODUCT = { name: '', vendor: '', description: '' }
const EMPTY_VERSION = { version: '', placed_on_market: '', support_period_end: '' }

export default function Products() {
  const [products, setProducts] = useState([])
  const [newProduct, setNewProduct] = useState(EMPTY_PRODUCT)
  const [versionForms, setVersionForms] = useState({})   // one "add version" form per product, keyed by product id
  const [message, setMessage] = useState('')

  function loadProducts() {
    api.get('/products').then(setProducts).catch((err) => setMessage(err.message))
  }
  useEffect(loadProducts, [])

  async function addProduct(event) {
    event.preventDefault()
    try {
      const created = await api.post('/products', newProduct)
      setNewProduct(EMPTY_PRODUCT)
      setMessage(`Product ${created.code} created`)
      loadProducts()
    } catch (err) {
      setMessage(err.message)
    }
  }

  async function deleteProduct(productId) {
    if (!confirm('Delete this product?')) return
    try {
      await api.del(`/products/${productId}`)
      loadProducts()
    } catch (err) {
      setMessage(err.message)
    }
  }

  function versionForm(productId) {
    return versionForms[productId] || EMPTY_VERSION
  }

  function updateVersionForm(productId, field, value) {
    setVersionForms({ ...versionForms, [productId]: { ...versionForm(productId), [field]: value } })
  }

  async function addVersion(productId) {
    const form = versionForm(productId)
    const body = {
      version: form.version,
      placed_on_market: form.placed_on_market || null,
      support_period_end: form.support_period_end || null,
    }
    try {
      await api.post(`/products/${productId}/versions`, body)
      setVersionForms({ ...versionForms, [productId]: EMPTY_VERSION })
      loadProducts()
    } catch (err) {
      setMessage(err.message)
    }
  }

  return (
    <>
      <h2>Products & SBOMs</h2>
      <p className="muted">
        Retention follows the customer: once a version is delivered (Customers page), it is kept until the
        customer's support end or the customer's regulations require, whichever is later.
      </p>
      <Notice text={message} onClose={() => setMessage('')} />

      {isAdmin() && (
        <form className="card inline" onSubmit={addProduct}>
          <h3>New product </h3>
          <input
            placeholder="Name"
            value={newProduct.name}
            onChange={(e) => setNewProduct({ ...newProduct, name: e.target.value })}
            required
          />
          <input
            placeholder="Vendor"
            value={newProduct.vendor}
            onChange={(e) => setNewProduct({ ...newProduct, vendor: e.target.value })}
          />
          <input
            placeholder="Description"
            value={newProduct.description}
            onChange={(e) => setNewProduct({ ...newProduct, description: e.target.value })}
          />
          <button className="primary">Add product</button>
        </form>
      )}

      {products.map((product) => (
        <div className="card" key={product.id}>
          <div className="row">
            <h3>{product.code} — {product.name} <small className="muted">{product.vendor}</small></h3>
            {isAdmin() && <button className="danger" onClick={() => deleteProduct(product.id)}>Delete</button>}
          </div>
          {product.description && <p className="muted">{product.description}</p>}

          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Placed on market</th>
                <th>Support end</th>
                <th>SBOMs</th>
                <th>Delivered to</th>
                <th>Retain until</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {product.versions.map((version) => (
                <VersionRow key={version.id} version={version} />
              ))}

              {isAdmin() && (
                <tr className="formrow">
                  <td>
                    <input
                      placeholder="1.0.0"
                      value={versionForm(product.id).version}
                      onChange={(e) => updateVersionForm(product.id, 'version', e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      type="date"
                      value={versionForm(product.id).placed_on_market}
                      onChange={(e) => updateVersionForm(product.id, 'placed_on_market', e.target.value)}
                    />
                  </td>
                  <td>
                    <input
                      type="date"
                      value={versionForm(product.id).support_period_end}
                      onChange={(e) => updateVersionForm(product.id, 'support_period_end', e.target.value)}
                    />
                  </td>
                  <td colSpan="5">
                    <button onClick={() => addVersion(product.id)} disabled={!versionForm(product.id).version}>
                      Add version
                    </button>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ))}
    </>
  )
}

function VersionRow({ version }) {
  const retention = version.retention       // empty when the version hasn't been delivered yet
  const bindingRegulation = retention?.binding_policy?.replace(/_/g, ' ')

  return (
    <tr>
      <td>{version.version}</td>
      <td>{fmtDate(version.placed_on_market)}</td>
      <td>{fmtDate(version.support_period_end)}</td>
      <td>{fmtNumber(version.sbom_count)}</td>
      <td>
        {version.customers.length > 0
          ? version.customers.join(', ')
          : <span className="muted small">no customer yet</span>}
      </td>
      <td><KeepUntil date={retention?.retain_until} note={bindingRegulation} /></td>
      <td>
        {retention
          ? <StatusBadge status={retention.status} />
          : <span className="muted small">not delivered</span>}
      </td>
      <td><Link to={`/versions/${version.id}`}>Open →</Link></td>
    </tr>
  )
}
