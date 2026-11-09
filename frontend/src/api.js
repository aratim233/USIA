// Every call to the backend goes through this file.
// It adds the login token, turns error replies into readable messages,
// and sends the user back to the login page when the token has expired.

const TOKEN_KEY = 'usia_token'
const USER_KEY = 'usia_user'

// The login token and the signed-in user are kept in the browser's localStorage.
export const auth = {
  get token() {
    return localStorage.getItem(TOKEN_KEY)
  },
  get user() {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY))
    } catch {
      return null
    }
  },
  save(loginReply) {
    localStorage.setItem(TOKEN_KEY, loginReply.access_token)
    localStorage.setItem(USER_KEY, JSON.stringify({ username: loginReply.username, role: loginReply.role }))
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  },
}

async function request(method, path, body, isFormData = false) {
  const headers = {}
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`
  if (body && !isFormData) headers['Content-Type'] = 'application/json'

  let payload
  if (body) payload = isFormData ? body : JSON.stringify(body)

  const response = await fetch(`/api${path}`, { method, headers, body: payload })

  // Token missing or expired: start again from the login page.
  if (response.status === 401 && path !== '/auth/login') {
    auth.clear()
    window.location.href = '/login'
  }

  if (!response.ok) {
    // FastAPI puts the reason in "detail"; show that instead of a bare status code.
    let message = response.statusText
    try {
      const errorBody = await response.json()
      message = typeof errorBody.detail === 'string' ? errorBody.detail : JSON.stringify(errorBody.detail)
    } catch {
      // the reply had no JSON body; keep the status text
    }
    throw new Error(message)
  }

  // Normal replies are JSON. File downloads are returned as they are.
  const contentType = response.headers.get('content-type') || ''
  const isDownload = Boolean(response.headers.get('content-disposition'))
  if (contentType.includes('application/json') && !isDownload) return response.json()
  return response
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body),
  put: (path, body) => request('PUT', path, body),
  patch: (path, body) => request('PATCH', path, body),
  del: (path) => request('DELETE', path),
  form: (path, formData) => request('POST', path, formData, true),

  // Login uses a form body, as the OAuth2 standard expects.
  async login(username, password) {
    const form = new URLSearchParams({ username, password })
    const response = await fetch('/api/auth/login', { method: 'POST', body: form })
    if (!response.ok) throw new Error((await response.json()).detail || 'Login failed')
    const reply = await response.json()
    auth.save(reply)
    return reply
  },

  // Save a file from the backend, using the file name the backend suggests.
  async download(path, fallbackName) {
    const response = await request('GET', path)
    const blob = await response.blob()
    const disposition = response.headers.get('content-disposition') || ''
    const suggestedName = (disposition.match(/filename="(.+)"/) || [])[1]

    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = suggestedName || fallbackName
    link.click()
    URL.revokeObjectURL(link.href)
  },
}

export const isAdmin = () => auth.user?.role === 'admin'

// 2048 -> "2.0 KB", 3145728 -> "3.0 MB"
export function fmtBytes(bytes) {
  if (bytes > 1048576) return `${(bytes / 1048576).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(1)} KB`
}
