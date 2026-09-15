import { useState } from 'react'
import axios from 'axios'
import { Button, Select, Textarea } from '@mantine/core'
import { useT } from '@/lib/i18n'

/**
 * 接口调用器：始终打真接口验真（不走 mock）。
 * 骨架阶段可打 /health、/、/api/v1/chatbot/chat。
 */
export function ApiTester() {
  const t = useT()
  const [endpoint, setEndpoint] = useState('/health')
  const [body, setBody] = useState('{\n  "messages": [{ "role": "user", "content": "你好" }]\n}')
  const [method, setMethod] = useState<'GET' | 'POST'>('GET')
  const [result, setResult] = useState('')
  const [busy, setBusy] = useState(false)

  const send = async () => {
    setBusy(true)
    setResult('')
    // vite proxy 只转 /api；/health 与 / 走后端直连（dev 默认 8765，Electron 会注入真实端口）。
    const backend = import.meta.env.VITE_BACKEND_ORIGIN ?? 'http://localhost:8765'
    const url = endpoint.startsWith('/api') ? endpoint : `${backend}${endpoint}`
    try {
      const res =
        method === 'GET'
          ? await axios.get(url, { timeout: 8000 })
          : await axios.post(url, JSON.parse(body || '{}'), { timeout: 30000 })
      setResult(JSON.stringify({ status: res.status, data: res.data }, null, 2))
    } catch (err) {
      if (axios.isAxiosError(err)) {
        setResult(
          JSON.stringify(
            { error: err.message, status: err.response?.status, data: err.response?.data },
            null,
            2,
          ),
        )
      } else {
        setResult(String(err))
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card api-tester">
      <h2>{t('dev.apiTester')}</h2>
      <div className="dev-row" style={{ marginBottom: '0.75rem' }}>
        <Select
          value={method}
          onChange={(v) => setMethod((v as 'GET' | 'POST') ?? 'GET')}
          data={['GET', 'POST']}
          style={{ width: 100 }}
          allowDeselect={false}
        />
        <Select
          value={endpoint}
          onChange={(v) => setEndpoint(v ?? '/health')}
          data={['/health', '/', '/api/v1/chatbot/chat']}
          style={{ flex: 1 }}
          allowDeselect={false}
        />
        <Button onClick={send} disabled={busy}>
          {busy ? '…' : t('dev.send')}
        </Button>
      </div>
      {method === 'POST' && (
        <Textarea value={body} onChange={(e) => setBody(e.currentTarget.value)} spellCheck={false} minRows={5} />
      )}
      <div style={{ marginTop: '0.75rem' }}>
        <label>{t('dev.response')}</label>
        <pre>{result || '—'}</pre>
      </div>
    </div>
  )
}
