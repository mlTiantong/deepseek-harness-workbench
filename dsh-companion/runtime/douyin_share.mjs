import { segment } from 'douyin-im'

const trailingPunctuation = /[.,!?;:，。！？；：、)\]}＞》」』】）]+$/u

function isDouyinHost(hostname) {
  return hostname === 'douyin.com' || hostname.endsWith('.douyin.com')
}

function cleanUrl(value) {
  return value.replace(trailingPunctuation, '')
}

export function splitDouyinLinks(text) {
  if (!text) return []
  const parts = []
  let cursor = 0
  for (const match of text.matchAll(/https?:\/\/[^\s]+/gu)) {
    const raw = match[0]
    const url = cleanUrl(raw)
    let target
    try {
      target = new URL(url)
    } catch {
      continue
    }
    if (!isDouyinHost(target.hostname)) continue
    if (match.index > cursor) parts.push({ type: 'text', text: text.slice(cursor, match.index) })
    parts.push({ type: 'share', url: target.href })
    cursor = match.index + raw.length
  }
  if (cursor < text.length) parts.push({ type: 'text', text: text.slice(cursor) })
  return parts.length ? parts : [{ type: 'text', text }]
}

export function directShareMessage(url) {
  const target = new URL(url)
  const work = target.pathname.match(/^\/(?:video|note)\/(\d+)/u)
  return work ? segment.share({ itemId: work[1] }) : segment.link({ url: target.href })
}
