// 独立抖音 IM 进程：只处理登录和私信，调度与记忆由 Python 宿主负责。
import { createInterface } from 'node:readline'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { createClient, segment } from 'douyin-im'
import { messageData, historyEvent } from './douyin_messages.mjs'
import { directShareMessage, splitDouyinLinks } from './douyin_share.mjs'
import { resolveContact } from './douyin_contacts.mjs'

const directory = process.argv[2]
await mkdir(directory, { recursive: true })
const client = createClient({ dataDir: join(directory, 'accounts') })
let status = { state: 'idle', authenticated: false }
const contacts = new Map()
const groups = new Map()
const send = value => process.stdout.write(JSON.stringify(value, (_, v) => typeof v === 'bigint' ? v.toString() : v) + '\n')
const update = value => { status = { ...status, ...value }; send({ event: 'status', data: status }) }

async function sendTextOrShares(contact, text, shareUrls) {
  const urls = Array.isArray(shareUrls) ? shareUrls : shareUrls ? [shareUrls] : []
  const parts = urls.length
    ? (text ? [{ type: 'text', text }] : [])
    : splitDouyinLinks(text || '')
  const hasShares = urls.length > 0 || parts.some(part => part.type === 'share')
  if (!hasShares) return { hasShares: false, textResult: text ? await contact.sendMsg(text) : null, shareResults: [] }
  const textResults = []
  const shareResults = []
  for (const part of parts) {
    if (part.type === 'text' && part.text) textResults.push(await contact.sendMsg(part.text))
    if (part.type === 'share') shareResults.push(await contact.sendMsg(directShareMessage(part.url)))
  }
  for (const url of urls) shareResults.push(await contact.sendMsg(directShareMessage(url)))
  return { hasShares: true, textResult: textResults.length === 1 ? textResults[0] : textResults, shareResults }
}

client.on('system.login.qrcode', async ({ account, qrcodeBase64 }) => {
  const qr = join(directory, 'login-qr.png')
  await writeFile(qr, Buffer.from(qrcodeBase64, 'base64'))
  update({ state: 'scan', authenticated: false, qr_image: qr })
  await account.continueLogin()
})
client.on('system.login.qrcode.status', ({ status: value, description }) => {
  const state = { new: 'scan', scanned: 'confirm', confirmed: 'logging-in', expired: 'timeout' }[value] ?? value
  update({ state, description })
})
client.on('system.login.verification', async ({ verification }) => {
  update({ state: 'verification', methods: verification.methods })
  await verification.open()
})
client.on('system.login.error', ({ error }) => update({ state: 'error', authenticated: false, error: error.message }))
client.on('system.reconnecting', ({ reason }) => update({ state: 'reconnecting', authenticated: false, reason }))
client.on('system.offline', () => update({ state: 'offline', authenticated: false }))
client.on('system.online', ({ account }) => update({ state: 'online', authenticated: true,
  account_id: account.uid, nickname: account.nickname }))
client.on('message', event => {
  if (event.senderUid === event.account.uid || event.senderUid === event.account.imUid) return
  if (event.conversationType === 2) groups.set(event.threadId, event.group)
  else contacts.set(event.threadId, event.chatType === 'stranger' ? event.stranger : event.friend)
  send({ event: 'message', data: messageData(event) })
})

async function handle({ id, method, params = {} }) {
  try {
    let result
    if (method === 'status') result = status
    else if (method === 'restore') {
      if (client.loadAccounts().length) {
        update({ state: 'restoring', authenticated: false })
        client.login().catch(error => update({ state: 'error', authenticated: false, error: error.message }))
      }
      result = status
    } else if (method === 'login') {
      if (!client.accounts.length) client.createAccount({ login: { method: 'qr' } })
      update({ state: 'logging-in', authenticated: false, error: null })
      client.login().catch(error => update({ state: 'error', authenticated: false, error: error.message }))
      result = status
    } else if (method === 'list_groups') {
      const account = client.pickAccount(params.account_id || undefined)
      const list = await account.getGroupList(params.refresh ?? true)
      for (const group of list) groups.set(group.threadId, group)
      result = list.map(group => ({ platform: 'douyin', account_id: String(account.uid),
        conversation_id: group.threadId, name: group.name ?? '' }))
    } else if (method === 'recent_group_history') {
      const account = client.pickAccount(params.account_id || undefined)
      const group = groups.get(params.conversation_id) ?? account.pickGroup(params.conversation_id)
      if (!group) throw new Error('没有找到该抖音群聊')
      const page = await group.getHistory({ count: params.limit ?? 50 })
      if (page.statusCode !== 0) throw new Error(`读取抖音群消息失败: ${page.statusCode} ${page.statusMsg}`)
      result = page.messages.map(message => historyEvent(message, group, account))
    } else if (method === 'group_history') {
      const account = client.pickAccount(params.account_id || undefined)
      const group = groups.get(params.conversation_id) ?? account.pickGroup(params.conversation_id)
      if (!group) throw new Error('没有找到该抖音群聊')
      const own = new Set([String(account.uid ?? ''), String(account.imUid ?? '')])
      const since = Number(params.since ?? 0)
      let cursor, previous
      result = []
      while (true) {
        const page = await group.getHistory({ count: 50, ...(cursor ? { cursor } : {}) })
        if (page.statusCode !== 0) throw new Error(`读取抖音群消息失败: ${page.statusCode} ${page.statusMsg}`)
        const batch = page.messages.map(message => historyEvent(message, group, account))
        result.push(...batch.filter(item => item.occurred_at >= since && !own.has(item.sender_id)))
        if (!page.hasMore || !batch.length || Math.min(...batch.map(item => item.occurred_at)) < since) break
        previous = cursor; cursor = page.cursor
        if (!cursor || cursor === previous) break
      }
    } else if (method === 'send_group_message') {
      const account = client.pickAccount(params.account_id || undefined)
      const group = groups.get(params.conversation_id) ?? account.pickGroup(params.conversation_id)
      if (!group) throw new Error('没有找到该抖音群聊')
      const sent = await sendTextOrShares(group, params.text, params.share_urls)
      const imagePaths = Array.isArray(params.image_paths)
        ? params.image_paths
        : params.image_paths ? [params.image_paths] : []
      if (!imagePaths.length) {
        result = sent.hasShares ? { text: sent.textResult, shares: sent.shareResults } : sent.textResult
      } else {
        const imageResults = []
        for (const imagePath of imagePaths) {
          imageResults.push(await group.sendMsg(segment.image(imagePath)))
        }
        result = { text: sent.textResult, shares: sent.shareResults, images: imageResults }
      }
    } else if (method === 'send_message') {
      const account = client.pickAccount(params.account_id || undefined)
      const contact = await resolveContact(account, contacts, params)
      if (!contact) throw new Error('没有找到该抖音私信会话或联系人')
      const imagePaths = Array.isArray(params.image_paths)
        ? params.image_paths
        : params.image_paths ? [params.image_paths] : []
      const sent = await sendTextOrShares(contact, params.text, params.share_urls)
      if (!imagePaths.length && !sent.hasShares) {
        result = sent.textResult
      } else {
        const imageResults = []
        for (const imagePath of imagePaths) {
          imageResults.push(await contact.sendMsg(segment.image(imagePath)))
        }
        result = { text: sent.textResult, shares: sent.shareResults, images: imageResults }
      }
    } else if (method === 'stop') {
      await client.logout()
      result = { stopped: true }
    } else throw new Error(`未知抖音 IM 方法：${method}`)
    send({ id, result })
  } catch (error) {
    send({ id, error: { message: error.message } })
  }
}

const input = createInterface({ input: process.stdin })
input.on('line', line => { void handle(JSON.parse(line)) })
input.on('close', () => { void client.logout().finally(() => process.exit(0)) })
// 恢复项目内以前建立的账号；首次启动不自动创建新登录。
if (process.env.DOUYIN_AUTO_RESTORE === '1' && client.loadAccounts().length) {
  update({ state: 'restoring', authenticated: false })
  client.login().catch(error => update({ state: 'error', authenticated: false, error: error.message }))
}
