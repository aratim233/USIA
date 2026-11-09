import { useState } from 'react'
import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { api, auth } from './api'
import Audit from './pages/Audit'
import Customers from './pages/Customers'
import Dashboard from './pages/Dashboard'
import Products from './pages/Products'
import Retention from './pages/Retention'
import Search from './pages/Search'
import VersionDetail from './pages/VersionDetail'

const MENU = [
  { to: '/', label: 'Dashboard' },
  { to: '/products', label: 'Products & SBOMs' },
  { to: '/customers', label: 'Customers & Solutions' },
  { to: '/retention', label: 'Retention & Compliance' },
  { to: '/search', label: 'Impact Search' },
  { to: '/audit', label: 'Audit Log' },
]

function Login() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  async function handleSubmit(event) {
    event.preventDefault()
    setError('')
    try {
      await api.login(username, password)
      navigate('/')
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={handleSubmit}>
        <h1>USIA: SBOM Vault</h1>
        <p className="muted">Unified SBOM Integration and Analysis Framework for Multi-Products</p>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="primary">Sign in</button>
      </form>
    </div>
  )
}

// Sidebar + page area, shown on every page after signing in.
function Layout({ children }) {
  const navigate = useNavigate()

  function logOut() {
    auth.clear()
    navigate('/login')
  }

  return (
    <div className="shell">
      <aside>
        <div className="brand">USIA<span>SBOM Vault</span></div>
        {MENU.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === '/'}>{item.label}</NavLink>
        ))}
        <div className="user">
          {auth.user?.username} <small>({auth.user?.role})</small>
          <button onClick={logOut}>Log out</button>
        </div>
      </aside>
      <main>{children}</main>
    </div>
  )
}

// Pages behind the login: without a token, go to the login page.
function Private({ children }) {
  if (!auth.token) return <Navigate to="/login" />
  return <Layout>{children}</Layout>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Private><Dashboard /></Private>} />
      <Route path="/products" element={<Private><Products /></Private>} />
      <Route path="/versions/:id" element={<Private><VersionDetail /></Private>} />
      <Route path="/customers" element={<Private><Customers /></Private>} />
      <Route path="/retention" element={<Private><Retention /></Private>} />
      <Route path="/search" element={<Private><Search /></Private>} />
      <Route path="/audit" element={<Private><Audit /></Private>} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  )
}
