import { useState } from 'react'
import { api } from '../api'

// "A vulnerability was announced in component X - which of our products and customers are affected?"
export default function Search() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [error, setError] = useState('')

  async function handleSearch(event) {
    event.preventDefault()
    setError('')
    try {
      setResults(await api.get(`/search?q=${encodeURIComponent(query)}`))
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <>
      <h2>Impact Search</h2>
      <p className="muted">
        A new vulnerability is announced in a component — which product versions and which customer solutions contain it?
      </p>

      <form className="card inline" onSubmit={handleSearch}>
        <input
          placeholder="Component name or PURL, e.g. openssl or pkg:pypi/requests"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1 }}
        />
        <button className="primary">Search</button>
      </form>

      {error && <div className="error">{error}</div>}
      {results && results.length === 0 && <p>No matching components.</p>}

      {results && results.map((component) => (
        <div className="card" key={component.purl}>
          <h3>{component.name} {component.version} <small className="mono muted">{component.purl}</small></h3>
          <div className="grid2">
            <div>
              <h4>Affected product versions ({component.products.length})</h4>
              <ul>{component.products.map((line) => <li key={line}>{line}</li>)}</ul>
            </div>
            <div>
              <h4>Affected customer solutions ({component.solutions.length})</h4>
              <ul>{component.solutions.map((line) => <li key={line}>{line}</li>)}</ul>
              {component.solutions.length === 0 && <p className="muted">None delivered.</p>}
            </div>
          </div>
        </div>
      ))}
    </>
  )
}
