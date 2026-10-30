// 独立微信 iLink 进程：二维码登录、收消息和发文字/图片通过 stdin/stdout 与 Python 通信。
import { createInterface } from 'node:readline'
import { basename, join } from 'node:path'
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

const directory = process.argv[2]
const packageRoot = process.env.DSH_WECHAT_ROOT
if (!directory || !packageRoot) throw new Error('微信桥接缺少目录或 dsh-wechat 包路径')
await mkdir(directory, { recursive: true })
await mkdir(join(directory, 'inbound'), { recursive: true })

const modulePath = name => pathToFileURL(join(packageRoot, 'dist', name)).href
const [{ login, loadToken }, { startMonitor }, { sendTextMessage, sendMediaMessage }, types,
  inbound, media, { qrSvgFor }] = await Promise.all([
  import(modulePath('weixin/auth.js')),
  import(modulePath('weixin/monitor.js')),
  import(modulePath('weixin/send.js')),
  import(modulePath('weixin/types.js')),
  import(modulePath('adapter/inbound.js')),
  import(modulePath('weixin/media.js')),
  import(modulePath('qr.js')),
])

const defaultBaseUrl = 'https://ilinkai.weixin.qq.com'
const cdnBaseUrl = 'https://novac2c.cdn.weixin.qq.com/c2c'
let token = loadToken(directory)
let monitorTask = null
let monitorAbort = null
let loginTask = null
let counter = 0
let status = { state: token ? 'restoring' : 'idle', authenticated: Boolean(token), account_id: token?.accountId || '' }

const send = value => process.stdout.write(JSON.stringify(value) + '\n')
const update = value => { status = { ...status, ...value }; send({ event: 'status', data: status }) }
const log = message => send({ event: 'log', data: { message } })

async function saveQr(dataUrl) {
  const qrUrl = String(dataUrl || '')
  if (!qrUrl) return
  const path = join(directory, 'login-qr.svg')
  await writeFile(path, qrSvgFor(qrUrl), 'utf8')
  update({ state: 'scan', authenticated: false, qr_image: path })
}

async function saveInboundImage(message) {
  const item = inbound.findMediaItem(message.item_list)
  if (!item || item.type !== types.MessageItemType.IMAGE || !item.image_item?.media) return null
  const mediaRef = item.image_item.media
  const aesKey = media.parseAesKey(mediaRef)
  if (!aesKey || !mediaRef.encrypt_query_param) return null
  const data = await media.downloadAndDecrypt(mediaRef.encrypt_query_param, aesKey, cdnBaseUrl)
  const ext = inbound.detectImageExtension(data)
  const id = String(message.message_id || message.seq || Date.now())
  const path = join(directory, 'inbound', `${id}-${Date.now()}.${ext}`)
  await writeFile(path, data)
  return { kind: 'image', path, mime_type: `image/${ext === 'jpg' ? 'jpeg' : ext}` }
}

async function handleMessage(message) {
  if (message.message_type === types.MessageType.BOT) return
  const sender = String(message.from_user_id || '')
  if (!sender) return
  const content = await saveInboundImage(message).catch(error => {
    log(`微信图片下载失败：${error.message}`)
    return null
  })
  const group = String(message.group_id || '')
  const conversation = group || sender
  const eventId = String(message.message_id || message.seq || `${conversation}:${message.create_time_ms || Date.now()}`)
  send({ event: 'message', data: {
    platform: 'weixin', account_id: String(token?.accountId || ''), event_type: 'message', external_id: eventId,
    conversation_id: conversation, sender_id: sender, sender_name: '',
    occurred_at: Number(message.create_time_ms || Date.now()) / 1000,
    payload: { text: inbound.extractText(message.item_list), content, context_token: message.context_token || '', raw: message },
  } })
}

async function startMonitorLoop() {
  if (!token || monitorTask) return
  monitorAbort = new AbortController()
  update({ state: 'online', authenticated: true, account_id: token.accountId || '', user_id: token.userId || '' })
  monitorTask = startMonitor({
    baseUrl: token.baseUrl || defaultBaseUrl, token: token.token, storageDir: directory,
    abortSignal: monitorAbort.signal, log, onMessage: message => { void handleMessage(message) },
    onSessionInvalid: () => update({ state: 'reconnecting', authenticated: false }),
    onSessionRecovered: () => update({ state: 'online', authenticated: true }),
    onSessionGiveUp: () => update({ state: 'timeout', authenticated: false, error: '微信登录已失效，请重新扫码' }),
  }).catch(error => {
    if (!monitorAbort?.signal.aborted) update({ state: 'error', authenticated: false, error: error.message })
  }).finally(() => { monitorTask = null; monitorAbort = null })
}

async function loginNow() {
  if (loginTask) return status
  update({ state: 'logging-in', authenticated: false, error: null })
  loginTask = login({ baseUrl: defaultBaseUrl, botType: '3', storageDir: directory, log, renderQrUrl: saveQr })
    .then(async result => {
      token = result
      update({ state: 'online', authenticated: true, account_id: result.accountId || '', user_id: result.userId || '' })
      await startMonitorLoop()
    })
    .catch(error => update({ state: 'error', authenticated: false, error: error.message }))
    .finally(() => { loginTask = null })
  return status
}

async function sendMedia(to, paths) {
  const results = []
  for (const filePath of (Array.isArray(paths) ? paths : paths ? [paths] : [])) {
    const buffer = await readFile(filePath)
    results.push(await sendMediaMessage(to, types.UploadMediaType.IMAGE, buffer, {
      baseUrl: token.baseUrl || defaultBaseUrl, token: token.token, cdnBaseUrl,
      fileName: basename(filePath), mimeType: 'image/png',
    }))
  }
  return results
}

async function handle({ id, method, params = {} }) {
  try {
    let result
    if (method === 'status') result = status
    else if (method === 'login') result = await loginNow()
    else if (method === 'restore') {
      token = loadToken(directory)
      if (token) await startMonitorLoop()
      result = status
    } else if (method === 'send_message' || method === 'send_group_message') {
      if (!token) throw new Error('微信尚未登录')
      const to = String(params.conversation_id || params.recipient_id || '')
      if (!to) throw new Error('微信发送缺少会话 ID')
      const text = String(params.text || '')
      const textId = text ? await sendTextMessage(to, text, { baseUrl: token.baseUrl || defaultBaseUrl, token: token.token }) : null
      const images = await sendMedia(to, params.image_paths)
      result = { text: textId, images }
    } else if (method === 'stop') {
      monitorAbort?.abort()
      await monitorTask?.catch(() => {})
      token = null
      update({ state: 'offline', authenticated: false })
      result = { stopped: true }
    } else throw new Error(`未知微信方法：${method}`)
    send({ id, result })
  } catch (error) {
    send({ id, error: { message: error.message } })
  }
}

const input = createInterface({ input: process.stdin })
input.on('line', line => { void handle(JSON.parse(line)) })
input.on('close', () => { monitorAbort?.abort(); process.exit(0) })

if (process.env.WEIXIN_AUTO_RESTORE === '1' && token) void startMonitorLoop()
