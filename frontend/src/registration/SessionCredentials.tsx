import { useState } from 'react'
import { Copy, ChevronDown } from 'lucide-react'

// StarBridge view only. The original registration console/source stays untouched.
export default function SessionCredentials({ value, busy, onCopy }: {
  value: Record<string, any>; busy?: boolean; onCopy: (value: string) => void
}) {
  const [expanded, setExpanded] = useState<string[]>([])
  const session = value.session && typeof value.session === 'object'
    ? JSON.stringify(value.session, null, 2) : ''
  const fields = [
    ['网页登录 AT', value.access_token], ['RT（Refresh Token）', value.oauth?.refresh_token],
    ['Session', session], ['OAuth AT', value.oauth?.access_token],
  ]
  const sessionNote = session ? '完整 Session JSON，可复制或使用“邮箱+Session”下载。'
    : value.session_status === 'pending' ? 'Session 正在获取，请稍后重新查看。'
      : value.session_status === 'failed' ? 'Session 获取失败，请联系管理员。' : '此账号尚未保存完整 Session。'
  return <section className="p-token-section">
    <h3>AT / RT / Session</h3>
    {fields.map(([label, token]) => <div className="p-token-row" key={label}>
      <label>{label}<div className="p-inline">
        <input aria-label={label} readOnly value={token || ''} />
        <button className="p-button secondary" aria-label={`复制${label}`} disabled={busy || !token}
          onClick={() => onCopy(token)}><Copy size={15} /></button>
        <button className="p-button secondary" aria-label={`展开${label}`} aria-expanded={expanded.includes(label)} disabled={!token}
          onClick={() => setExpanded(v => v.includes(label) ? v.filter(x => x !== label) : [...v, label])}><ChevronDown size={15} /></button>
      </div></label>
      {expanded.includes(label) && <textarea aria-label={`${label}完整内容`} readOnly value={token || ''} rows={label === 'Session' ? 8 : 4} />}
      {label === 'Session' && <p className="p-muted p-fine">{sessionNote}</p>}
    </div>)}
    <p className="p-muted p-fine">查看和下载不会刷新登录有效期。</p>
  </section>
}
