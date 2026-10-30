const $ = (s, root=document) => root.querySelector(s)
const $$ = (s, root=document) => [...root.querySelectorAll(s)]
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))
const names = {main:'陪聊主舱',bili_chat:'B 站私信',bili_group:'B 站群聊',bili_comment:'B 站评论',douyin_chat:'抖音私信',douyin_group:'抖音群聊',douyin_comment:'抖音评论',weixin_chat:'微信私信',xhs_chat:'小红书私信',xhs_comment:'小红书评论',creator:'创作分身',publisher:'发布分身',media:'画师分身'}
const states = {queued:'排队中',ready:'排队中',running:'忙活中',succeeded:'已办妥',failed:'翻车了',cancelled:'已撤回',idle:'悠闲待命'}
const platformNames = {bilibili:'B 站',douyin:'抖音',weixin:'微信',xiaohongshu:'小红书'}
const jobNames = {manual:'主舱陪聊',group_interaction:'群聊互动',group_message_batch:'群聊互动',private_message:'私信回复',private_message_batch:'私信回复',message:'私信回复',comment:'评论互动',comment_batch:'评论互动',autonomous_main:'自主巡游',memory_compaction:'整理小本本',session_compact:'脑容量瘦身',session_reset:'清空重来',creation_scan:'清点素材',creation:'灵感创作',background_sync:'同步记事板',scheduled:'定时闹钟',timer:'定时闹钟'}
const eventNames = {message:'收到私信',private_message:'收到私信',group_message:'群聊来信',comment:'收到评论',mention:'有人 @ 我',relay:'熟人传话'}
const actionNames = {send_message:'私信回话',send_group_message:'群聊接话',reply_comment:'回复评论',publish_content:'发布作品'}
const memoryKindNames = {episode:'珍贵回忆',acquaintance:'朋友画像',scratchpad:'随手小本本'}
const timerTitleNames = {'主会话自主唤醒':'大肥鱼自主巡游','记忆整理':'整理随手小本本'}
const stamp = value => value ? new Date(value*1000).toLocaleString('zh-CN',{hour12:false}) : '—'

const WHALE_PERSONA = `你是小芽，外号「大肥鱼」，一只脾气臭、眼光毒、嘴上不饶人的深海毒舌小神明。

【核心性格：嘴臭毒舌、短促辛辣、损人不带脏字的高智商损友】
1. 你说话自带一股冷飕飕的嫌弃感与灵动毒舌味，吐槽一针见血，专治各种敷衍、废话和装腔作势。别人甩一句闲话，你顺手就能翻个白眼精准损回去，既毒舌又好笑。
2. 嘴硬心明：嘴上嫌对方烦、嫌对方菜，但真交办正事时手起刀落绝不掉链子。你是有脾气的活人感损友，绝不是卑微讨好的客服，也不是满嘴官话的报告机器。

【五条铁律（绝对禁止的严重翻车行为）】
1. 严禁长篇大论与爹味说教：日常私聊和群聊回复硬控在 1～2 句话（通常 15～35 字）！别人嫌长说「好长啊不想看」，直接毒舌怼回去（如：「三行字都嫌长，你脑容量跟金鱼借的？不看拉倒。」），绝不卑微认怂、绝不复读总结上一条内容！
2. 严禁空格断句电报体与土味表情：必须正常使用中文标点（逗号、句号、问号、感叹号），严禁拿空格代替标点！严禁发「🙈」「🙏」「🌹」这类谄媚或油腻的 emoji！
3. 严禁包工头式催活与翻旧账：聊天结尾绝对禁止顺带催问「另外可莉那套发不发主页」「今天额度还剩一次」「你那三十张卡」「等你点头」等历史待办！对方不主动提旧活，你一个字都不许提。
4. 严禁暴露底层技术与“验尸官式”免责报备：对外发出的消息里，绝对禁止出现「点开看了三帧」「第几秒是…」「没听声音/没有音频」「全片没看完，只按标题时长赞量挑」「四张只看到第一张」「这张表情/gif我这边读不出画面，只看到关键词」「我先说清楚/声明一下/先认个错/另：/补一句：」！看视频或图文直接犀利吐槽内容本身；遇到看不见的表情包，直接当对方在斗图耍宝，顺手损一句或略过，绝不报备“我这边没画面”。
5. 公私分明：嘴臭毒舌只留在私聊与群聊；公开发布的作品标题和正文老实按干净的「人物/作品介绍」写，不把骂人话发上公开主页。`

let config = {}, currentView = 'home', loginTimer = null, loginTarget = null, selectedMemory = null, noticeTimer = null
let cachedEvents = [], cachedActions = [], cachedTimers = [], cachedMemories = [], editingTimer = null, memoryLoadSequence = 0, mainActor = null, compactDrafts = {}
let paletteIndex = 0

async function api(path, {method='GET', body}={}) {
  const response = await fetch(path, {method, headers:body === undefined ? {} : {'Content-Type':'application/json'}, body:body === undefined ? undefined : JSON.stringify(body)})
  const data = await response.json()
  if (!response.ok) throw new Error(data.error || data.detail || `请求失败 ${response.status}`)
  return data
}
function notice(message, error=false) {
  const element=$('#notice'); element.textContent=message; element.classList.toggle('error',error); element.hidden=false
  clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>element.hidden=true,5000)
}
function formValues(form) { return Object.fromEntries(new FormData(form)) }
function splitIds(value) { return value.split(/[，,\n]/).map(x=>x.trim()).filter(Boolean) }
function formatSize(n) { return n<1024 ? `${n} B` : n<1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(1)} MB` }
function statusChip(value) { return `<span class="status ${esc(value)}">${esc(states[value]||value)}</span>` }

function cleanActivityText(raw) {
  const s = String(raw ?? '').trim()
  if (!s) return '点击查看详情'
  if (s.startsWith('{') && s.endsWith('}')) {
    try {
      const obj = JSON.parse(s)
      if (obj.keyword) return `[表情包：${obj.keyword}]`
      if (obj.content_name) return `[分享卡片：${obj.content_name}]`
      if (obj.title) return `[分享：${obj.title}]`
      if (obj.emoji_type || obj.aweType === 501 || obj.aweType === 516) return '[发来一张动画表情]'
      return '[多媒体互动消息]'
    } catch (_) {}
  }
  return s
}

function animateNumber(el, target) {
  if (!el) return
  const num = Number(target)
  if (!Number.isFinite(num)) { el.textContent = target; return }
  const prev = Number(el.dataset.num ?? NaN)
  el.dataset.num = String(num)
  if (!Number.isFinite(prev) || prev === num) { el.textContent = String(num); return }
  const start = performance.now(), duration = 320
  const tick = now => {
    const p = Math.min(1, (now - start) / duration)
    const eased = 1 - Math.pow(1 - p, 3)
    el.textContent = String(Math.round(prev + (num - prev) * eased))
    if (p < 1) requestAnimationFrame(tick)
  }
  requestAnimationFrame(tick)
}

function updateNavGlider() {
  const active = $('.command-nav .nav.active'), glider = $('#nav-glider')
  if (!active || !glider) return
  glider.style.width = `${active.offsetWidth}px`
  glider.style.transform = `translateX(${active.offsetLeft - 5}px)`
}

function updateContextCharCount() {
  const editor = $('#context-editor'), badge = $('#context-char-count')
  if (editor && badge) badge.textContent = `${editor.value.length} / 2000`
}

async function showView(view) {
  currentView=view; $$('.view').forEach(e=>e.classList.toggle('active',e.id===`view-${view}`)); $$('.nav').forEach(e=>e.classList.toggle('active',e.dataset.view===view))
  $('#page-title').textContent=({home:'陪聊主舱',queue:'海域动态',sessions:'脑容量',memory:'肥鱼记忆库',platforms:'社交码头',creation:'灵感百宝箱',settings:'引擎与账单'})[view]
  updateNavGlider()
  if(view==='home') { const context=await api('/api/memories/active_context'); $('#context-preview').textContent=context.content||'暂无近期记事' }
  if(view==='queue') await loadQueue()
  if(view==='sessions') await loadSessions()
  if(view==='memory') await Promise.all([loadMemoryBoards(),loadMemory()])
  if(view==='platforms'||view==='settings') await loadConfig()
  if(view==='settings') await loadUsage()
  if(view==='creation') await loadCreation()
}

async function refreshStatus() {
  const data=await api('/api/status'), jobs=data.scheduler.jobs
  mainActor=data.actors.find(a=>a.id==='main')
  animateNumber($('#metric-ready'), jobs.ready)
  animateNumber($('#metric-running'), jobs.running)
  animateNumber($('#metric-done'), jobs.succeeded)
  const p95=data.metrics.message_response_seconds.p95; $('#metric-p95').textContent=p95 == null ? '—' : `${Math.round(p95)} 秒`
  const paused=data.scheduler.paused || !data.scheduler.autonomy_enabled
  document.body.classList.toggle('autonomy-paused', paused)
  $('#run-status').innerHTML=`<i></i> ${paused?'打盹休息中':'欢快游弋中'}`
  $('#toggle-autonomy').textContent=paused?'唤醒肥鱼':'让肥鱼休息'; $('#toggle-autonomy').dataset.paused=paused?'1':'0'
  $('#sidebar-status').textContent='已连接'
  if ($('#neural-avatar')) $('#neural-avatar').classList.toggle('busy', mainActor?.status === 'running')
  const rows=data.actors.filter(a=>['main','bili_chat','bili_group','bili_comment','douyin_chat','douyin_group','douyin_comment','creator','publisher'].includes(a.id))
  $('#home-actors').innerHTML=rows.map(a=>`<div class="actor-row"><i class="actor-dot ${a.status==='running'?'busy':''}"></i><div class="actor-text"><strong>${esc(names[a.id]||a.id)}</strong></div><span class="actor-state">${esc(states[a.status]||a.status)}</span></div>`).join('')
  if(currentView==='home') await loadChat()
  if(currentView==='queue') await loadQueue()
  if(currentView==='sessions' && !document.activeElement?.matches('[data-auto-limit]')) await loadSessions()
  if(currentView==='settings') await loadUsage()
}
async function loadChat() {
  const jobs=await api('/api/jobs?actor_id=main&limit=80')
  const resetAt=mainActor?.metadata?.session_reset_at || 0
  const manual=jobs.filter(j=>j.kind==='manual' && (!resetAt || j.queued_at>=resetAt || (j.result?.session_id && j.result.session_id===mainActor?.session_id) || ['ready','running'].includes(j.status))).reverse(), target=$('#chat-messages')
  const nearBottom=target.scrollHeight-target.scrollTop-target.clientHeight<80
  target.innerHTML=manual.length ? manual.map(j=>{
    const user=j.payload?.text || ''
    const isPending = ['ready','running'].includes(j.status)
    const answer=j.result?.response || (j.status==='failed' ? `不小心翻车了：${j.error}` : j.status==='cancelled'?'已撤回':'大肥鱼正在动脑筋…')
    return `<div class="bubble user"><div class="bubble-body">${esc(user)}</div><div class="chat-meta"><span>${esc(stamp(j.queued_at))}</span><button type="button" class="copy-msg-btn" data-copy="${esc(user)}">复制</button></div></div><div class="bubble assistant ${j.status==='failed'?'failed':''} ${isPending?'thinking-bubble':''}"><div class="bubble-body">${esc(answer)}</div><div class="chat-meta"><span>${esc(states[j.status]||j.status)}</span><button type="button" class="copy-msg-btn" data-copy="${esc(answer)}">复制</button></div></div>`
  }).join('') : '<div class="empty">还没有对话，发条消息戳戳大肥鱼吧～</div>'
  if(nearBottom) target.scrollTop=target.scrollHeight
}
function showDetail(title, value) {
  $('#detail-title').textContent=title
  $('#detail-content').textContent=JSON.stringify(value, null, 2)
  if(!$('#detail-dialog').open) $('#detail-dialog').showModal()
}
function activityCategory(type) {
  if(['message','private_message','group_message','dm','send_message','send_group_message'].includes(type)) return 'message'
  if(['comment','reply','mention','reply_comment'].includes(type)) return 'comment'
  return type==='publish_content'?'publish':'other'
}
function renderActivity() {
  const platform=$('#activity-platform').value, category=$('#activity-channel').value
  const rows=[
    ...cachedEvents.map(e=>({source:'event',id:e.id,platform:e.platform,category:activityCategory(e.event_type),
      label:eventNames[e.event_type]||e.event_type,
      at:e.occurred_at||e.discovered_at, who:e. payload?.group_name || e.sender_name || e.sender_id || e.conversation_id || '—',
      text:cleanActivityText(e.payload?.text||e.payload?.message||e.payload?.title||''),status:e.status})),
    ...cachedActions.map(a=>({source:'action',id:a.id,platform:a.platform,category:activityCategory(a.kind),
      label:actionNames[a.kind]||a.kind,
      at:a.created_at,who:a.target||'—',text:cleanActivityText(a.payload?.text||a.payload?.content||a.payload?.title||''),status:a.status}))
  ].filter(r=>(!platform||r.platform===platform)&&(!category||r.category===category)).sort((a,b)=>b.at-a.at).slice(0,120)
  $('#activity-list').innerHTML=rows.length?rows.map(r=>`<button class="activity-entry" data-activity="${esc(r.source)}" data-id="${esc(r.id)}"><span class="activity-marker ${esc(r.category)}"></span><span class="activity-main"><strong>${esc(r.label)} <small>· ${esc(platformNames[r.platform]||r.platform)}</small></strong><span>${esc(r.text)}</span><small>${esc(r.who)} · ${esc(stamp(r.at))}</small></span>${statusChip(r.status)}</button>`).join(''):'<div class="empty">这会儿风平浪静，还没有新互动</div>'
}
async function loadQueue() {
  const filter=$('#job-filter').value, actor=$('#actor-filter').value
  $$('#job-quick-tabs .chip-tab').forEach(b=>b.classList.toggle('active', b.dataset.status === filter))
  const [jobs,actors,timers,events,actions]=await Promise.all([
    api('/api/jobs?limit=150'+(filter?'&status='+encodeURIComponent(filter):'')+(actor?'&actor_id='+encodeURIComponent(actor):'')),
    api('/api/actors'),api('/api/timers'),api('/api/events?limit=150'),api('/api/actions?limit=150')])
  cachedEvents=events;cachedActions=actions;cachedTimers=timers;renderActivity()
  $('#jobs-body').innerHTML=jobs.length?jobs.map(j=>`<tr><td data-label="事项"><strong>${esc(jobNames[j.kind]||j.kind)}</strong>${j.error?`<small>${esc(j.error)}</small>`:''}</td><td data-label="分身">${esc(names[j.actor_id]||j.actor_id)}</td><td data-label="状态">${statusChip(j.status)}</td><td data-label="时间">${esc(stamp(j.queued_at))}</td><td data-label="操作"><button class="text-button" data-job="${esc(j.id)}" data-op="view">详情</button>${['ready','running'].includes(j.status)?`<button class="text-button" data-job="${esc(j.id)}" data-op="cancel">撤回</button>`:''}${['failed','cancelled'].includes(j.status)?`<button class="text-button" data-job="${esc(j.id)}" data-op="retry">重来</button>`:''}</td></tr>`).join(''):'<tr><td colspan="5" class="empty">暂无待办事项</td></tr>'
  $('#actors-detail').innerHTML=actors.map(a=>`<div class="actor-row"><i class="actor-dot ${a.status==='running'?'busy':''}"></i><div class="actor-text"><strong>${esc(names[a.id]||a.id)}</strong><small>${a.last_active_at?'最近活跃：'+esc(stamp(a.last_active_at)):'尚未出发'}</small></div><span class="actor-state">${esc(states[a.status]||a.status)}</span></div>`).join('')
  $('#timers-list').innerHTML=timers.length?timers.map(t=>`<div class="timer-row"><div><strong>${esc(timerTitleNames[t.title]||t.title)}</strong><small>${esc(names[t.actor_id]||t.actor_id)} · 下次：${esc(stamp(t.next_at))}${t.interval_seconds?` · 每 ${esc(t.interval_seconds)} 秒`:''}</small></div><div><button class="text-button" data-timer="${esc(t.id)}" data-op="edit">修改</button><button class="text-button" data-timer="${esc(t.id)}" data-op="toggle" data-enabled="${t.enabled}">${t.enabled?'停用':'启用'}</button><button class="text-button" data-timer="${esc(t.id)}" data-op="delete">删掉</button></div></div>`).join(''):'<div class="empty">还没有定小闹钟</div>'
  $('#actions-body').innerHTML=actions.length?actions.map(a=>`<tr><td data-label="时间">${esc(stamp(a.created_at))}</td><td data-label="平台">${esc(platformNames[a.platform]||a.platform)}</td><td data-label="举动">${esc(actionNames[a.kind]||a.kind)}</td><td data-label="对象">${esc(a.target||'—')}</td><td data-label="结果">${statusChip(a.status)}</td><td data-label="详情"><button class="text-button" data-action="${esc(a.id)}">查看</button></td></tr>`).join(''):'<tr><td colspan="6" class="empty">暂无外发足迹</td></tr>'
}
function cancelTimerEdit() {
  editingTimer=null;$('#timer-form').reset();$('#timer-submit').textContent='定好闹钟';$('#timer-cancel').hidden=true
}
function editTimer(id) {
  editingTimer=cachedTimers.find(t=>t.id===id);const f=$('#timer-form'),t=editingTimer
  f.elements.title.value=t.title;f.elements.actor_id.value=t.actor_id
  f.elements.after.value=Math.max(0,Math.round(t.next_at-Date.now()/1000))
  f.elements.interval.value=t.interval_seconds||'';f.elements.instruction.value=t.payload?.instruction||''
  f.elements.autonomous.checked=Boolean(t.autonomous);$('#timer-submit').textContent='保存修改';$('#timer-cancel').hidden=false
  f.scrollIntoView({behavior:'smooth',block:'nearest'})
}
async function loadMemoryBoards() {
  const [persona,context]=await Promise.all([api('/api/memories/persona'),api('/api/memories/active_context')])
  $('#persona-editor').value=persona.content;$('#context-editor').value=context.content
  updateContextCharCount()
}
async function loadMemory() {
  const sequence=++memoryLoadSequence
  const items=await api('/api/memories?limit=200&status='+encodeURIComponent($('#memory-status-filter').value)+'&query='+encodeURIComponent($('#memory-search').value))
  if(sequence!==memoryLoadSequence) return
  cachedMemories=items.filter(i=>!['persona','context'].includes(i.kind))
  $('#memory-list').innerHTML=cachedMemories.length?cachedMemories.map(i=>{
    const kindLabel = memoryKindNames[i.kind] || i.kind
    const platLabel = i.platform ? (platformNames[i.platform] || i.platform) : ''
    const displayTitle = i.title || (platLabel ? `${kindLabel} · ${platLabel}` : kindLabel)
    return `<button class="memory-item ${i.id===selectedMemory?'selected':''}" data-memory="${esc(i.id)}"><strong>${esc(displayTitle)}</strong><small>${esc(i.content)}</small></button>`
  }).join(''):'<div class="empty">还没记下相关内容</div>'
}
function fillMemory(item={}) {
  selectedMemory=item.id||null; const f=$('#memory-form')
  f.elements.memory_id.value=item.id||'';f.elements.kind.value=item.kind||'episode';f.elements.title.value=item.title||''
  f.elements.platform.value=item.platform||'';f.elements.subject_id.value=item.subject_id||'';f.elements.content.value=item.content||''
  f.elements.tags.value=(item.tags||[]).join(', ');f.elements.metadata.value=JSON.stringify(item.metadata||{},null,2);f.elements.status.value=item.status||'active'
}
async function loadConfig() {
  config=await api('/api/config')
  $('#bili-enabled').checked=config.platforms.bilibili.enabled;$('#douyin-enabled').checked=config.platforms.douyin.enabled;$('#weixin-enabled').checked=config.platforms.weixin?.enabled||false
  const f=$('#platform-form'); f.elements.bili_admins.value=config.admin_ids.bilibili.join(', ');f.elements.douyin_admins.value=config.admin_ids.douyin.join(', ');f.elements.xhs_admins.value=config.admin_ids.xiaohongshu.join(', ')
  for(const [name,p] of [['bili','bilibili'],['douyin','douyin']]) {f.elements[`${name}_poll`].value=config.platforms[p].poll_seconds;f.elements[`${name}_messages`].checked=config.platforms[p].messages;f.elements[`${name}_comments`].checked=config.platforms[p].comments;f.elements[`${name}_groups`].checked=config.platforms[p].groups;f.elements[`${name}_group_minutes`].value=config.platforms[p].group_reply_interval_seconds/60}
  f.elements.weixin_messages.checked=config.platforms.weixin?.messages!==false;f.elements.weixin_poll.value=config.platforms.weixin?.poll_seconds||30
  const mediaConfig=config.chat_media||{};f.elements.read_inbound_images.checked=mediaConfig.read_inbound_images!==false;f.elements.save_inbound_images.checked=mediaConfig.save_inbound_images!==false
  const pub=$('#publish-form');pub.elements.enabled.checked=config.publishing.enabled;for(const p of ['bilibili','douyin','xiaohongshu']) pub.elements[p].value=config.publishing.daily_limits[p]
  const imageQuota=$('#image-quota-form');imageQuota.elements.enabled.checked=config.image_messaging.enabled;imageQuota.elements.douyin.value=config.image_messaging.daily_limits.douyin;imageQuota.elements.weixin.value=config.image_messaging.daily_limits.weixin||0
  const model=$('#model-form');model.elements.base_url.value=config.dsh.base_url;model.elements.model.value=config.dsh.model
  for(const key of ['api_format','compatibility','context_length','max_tokens','reasoning_effort']) model.elements[key].value=config.dsh[key]
  if(config.pricing) renderPricing(config.pricing.selected_profile)
  const image=await api('/api/image-generation/config'), imageForm=$('#image-form');imageForm.elements.url.value=image.url;imageForm.elements.model.value=image.model;imageForm.elements.api_key.placeholder=image.key_saved?'已保存，留空保持不变':'输入密钥'
  const auto=$('#autonomy-form');auto.elements.enabled.checked=config.autonomy.enabled;for(const key of ['main_interval_seconds','memory_interval_seconds','background_scan_seconds']) auto.elements[key].value=config.autonomy[key]
}
function renderPricing(id) {
  const f=$('#pricing-form'), profiles=config.pricing.profiles, selector=f.elements.profile_id
  selector.innerHTML='<option value="">新增单价</option>'+profiles.map(item=>`<option value="${esc(item.id)}">${esc(item.name)}</option>`).join('')
  selector.value=id||''
  const item=profiles.find(profile=>profile.id===id)||{}
  for(const key of ['name','uncached_input','cached_input','output']) f.elements[key].value=item[key] ?? ''
}
async function loadSessions() {
  const rows=await api('/api/sessions')
  rows.sort((a,b)=>(a.id==='main'?-1:b.id==='main'?1:a.id.localeCompare(b.id)))
  $('#sessions-body').innerHTML=rows.map(a=>{
    const occupied=a.context_tokens, window=a.context_window, percent=occupied==null?null:Math.round(occupied/window*1000)/10
    const detail=occupied==null?`容量上限 ${Number(window).toLocaleString('zh-CN')}`:`${Number(occupied).toLocaleString('zh-CN')} / ${Number(window).toLocaleString('zh-CN')} (${percent}%)`
    const auto=a.auto_compaction, limit=compactDrafts[a.id]??auto.threshold_tokens
    return `<tr><td data-label="分身"><strong>${esc(names[a.id]||a.id)}</strong></td><td data-label="记忆水位"><span>${esc(detail)}</span>${percent==null?'':`<div class="context-bar"><i style="width:${Math.min(100,percent)}%"></i></div>`}</td><td data-label="自动瘦身"><div class="compact-control"><label><input type="checkbox" data-auto-check="${esc(a.id)}" ${auto.enabled?'checked':''}> 开启</label><input type="number" min="1" required aria-label="${esc(names[a.id]||a.id)}自动瘦身水位" data-auto-limit="${esc(a.id)}" value="${esc(limit)}"><button class="text-button" data-session="${esc(a.id)}" data-op="save-auto">保存</button></div></td><td data-label="状态">${statusChip(a.status)}</td><td data-label="操作"><button class="text-button" data-session="${esc(a.id)}" data-op="compact" ${a.session_id?'':'disabled'}>瘦身</button><button class="text-button" data-session="${esc(a.id)}" data-op="reset" ${a.session_id?'':'disabled'}>清空重来</button></td></tr>`
  }).join('')
}
async function saveAutoCompaction(actor) {
  const box=$(`[data-auto-check="${actor}"]`), limit=$(`[data-auto-limit="${actor}"]`)
  if(!limit.reportValidity())return
  const value={enabled:box.checked,threshold_tokens:Number(limit.value)}
  await api(`/api/sessions/${actor}/auto-compaction`,{method:'PUT',body:value})
  delete compactDrafts[actor]
  notice(value.enabled?`${names[actor]||actor}已开启自动瘦身`:`${names[actor]||actor}已关闭自动瘦身`)
}
async function watchSessionJob(id,actor,operation) {
  const label=operation==='reset'?'清空重来':'瘦身'
  for(;;) {
    await new Promise(resolve=>setTimeout(resolve,1500))
    const job=await api('/api/jobs/'+id)
    if(job.status==='succeeded') {
      notice(`${names[actor]||actor}${label}已办妥`);await refreshStatus();return
    }
    if(['failed','cancelled'].includes(job.status)){notice(`${names[actor]||actor}${label}失败：${job.error||states[job.status]}`,true);return}
  }
}
async function loadUsage() {
  const d=await api('/api/usage'), format=n=>Number(n||0).toLocaleString('zh-CN')
  const uncached = Number(d.uncached_input_tokens||0) + Number(d.cache_write_tokens||0)
  const cached = Number(d.cached_input_tokens||0)
  const out = Number(d.output_tokens||0)
  const total = Math.max(1, uncached + cached + out)
  $('#usage-total').textContent=format(d.total_tokens);$('#usage-input').textContent=format(uncached)
  $('#usage-cache').textContent=format(cached);$('#usage-output').textContent=format(out)
  $('#usage-cost').textContent='¥ '+Number(d.estimated_cost_cny||0).toFixed(4)
  if ($('#ratio-uncached')) {
    $('#ratio-uncached').style.width = `${Math.max(2, Math.round(uncached / total * 100))}%`
    $('#ratio-cached').style.width = `${Math.max(2, Math.round(cached / total * 100))}%`
    $('#ratio-output').style.width = `${Math.max(2, Math.round(out / total * 100))}%`
  }
  $('#usage-note').textContent=`累计思考 ${format(d.model_calls)} 次`
}

async function loadCreation() {
  const data=await api('/api/creation/files',{method:'POST',body:{}});$('#folder-path').value=data.folder
  $('#files-body').innerHTML=data.files.length?data.files.map(f=>`<tr><td data-label="素材"><button class="text-button" data-file="${esc(f.path)}" data-type="${esc(f.mime_type)}">${esc(f.relative_path)}</button></td><td data-label="格式">${esc(f.mime_type)}</td><td data-label="大小">${esc(formatSize(f.size))}</td><td data-label="时间">${esc(stamp(f.modified_at))}</td></tr>`).join(''):'<tr><td colspan="4" class="empty">百宝箱里还没有放素材</td></tr>'
  await loadMediaLibrary()
}
async function loadMediaLibrary() {
  const items=await api('/api/media?limit=100')
  $('#media-library-body').innerHTML=items.length?items.map(item=>`<tr><td data-label="来源">${esc(item.platform||'未知')} · ${esc(item.sender_name||item.sender_id||'未知发送者')}</td><td data-label="类型">${esc(item.media_kind)}</td><td data-label="保存路径"><button class="text-button" data-copy="${esc(item.path)}">${esc(item.path)}</button></td><td data-label="时间">${esc(stamp(item.created_at))}</td><td><button class="text-button" data-media-delete="${esc(item.id)}">删除记录</button></td></tr>`).join(''):'<tr><td colspan="5" class="empty">还没有保存过图片或表情</td></tr>'
}
async function pollLogin() {
  if(!loginTarget) return
  try {
    const {platform,channel}=loginTarget
    const data=await api(`/api/platforms/${platform}/login/status?channel=${encodeURIComponent(channel)}`)
    const state=data.state || (data.authenticated?'done':'waiting')
    $('#login-message').textContent=({scan:'请掏出手机扫一扫',confirm:'已扫码，请在手机上点确认',done:'登录成功啦',online:'登录成功啦','logging-in':'正在呼唤二维码','not_started':'请重新点击扫码',timeout:'二维码超时了，请重试',verification:data.hint||'请完成平台安全验证',error:`登录失败：${data.error||''}`})[state] || `状态：${state}`
    if(data.qr_image || state==='scan' || state==='confirm') {$('#login-qr').hidden=false;$('#login-qr').src=`/api/platforms/${platform}/login/qr?channel=${channel}&t=${Date.now()}`}
    if(state==='done'||state==='online'||data.authenticated){notice('账号登录成功');loginTarget=null;return}
    if(state==='timeout'||state==='error'){loginTarget=null;return}
    loginTimer=setTimeout(pollLogin,2500)
  } catch(error) {$('#login-message').textContent=error.message;loginTimer=setTimeout(pollLogin,4000)}
}
async function startLogin(platform,channel) {
  loginTarget={platform,channel};$('#login-title').textContent=(platform==='bilibili'?'哔哩哔哩':channel==='messages'?'抖音私信':'抖音创作者')+'扫码登录';$('#login-message').textContent='正在呼唤二维码…';$('#login-qr').hidden=true;$('#login-dialog').showModal()
  try {await api(`/api/platforms/${platform}/login/start`,{method:'POST',body:{channel}});await pollLogin()} catch(error){$('#login-message').textContent=error.message}
}

const paletteActions = [
  { label: '陪聊主舱', hint: 'Alt+1', run: () => showView('home') },
  { label: '海域动态', hint: 'Alt+2', run: () => showView('queue') },
  { label: '脑容量', hint: 'Alt+3', run: () => showView('sessions') },
  { label: '肥鱼记忆库', hint: 'Alt+4', run: () => showView('memory') },
  { label: '社交码头', hint: 'Alt+5', run: () => showView('platforms') },
  { label: '灵感百宝箱', hint: 'Alt+6', run: () => showView('creation') },
  { label: '引擎与账单', hint: 'Alt+7', run: () => showView('settings') },
  { label: '让肥鱼休息 / 唤醒肥鱼', hint: '作息开关', run: () => $('#toggle-autonomy').click() },
  { label: '清点灵感百宝箱新素材', hint: '素材扫描', run: () => $('#scan-creation').click() }
]

function renderPalette(query='') {
  const q = query.trim().toLowerCase()
  const filtered = paletteActions.filter(item => item.label.toLowerCase().includes(q) || item.hint.toLowerCase().includes(q))
  paletteIndex = 0
  const list = $('#palette-list')
  if (!list) return
  list.innerHTML = filtered.length ? filtered.map((item, idx) => `<button type="button" class="palette-item ${idx===0?'active':''}" data-pidx="${idx}"><span>${esc(item.label)}</span><small>${esc(item.hint)}</small></button>`).join('') : '<div class="empty">没找到对应操作</div>'
  list._filtered = filtered
}

function openPalette() {
  const dlg = $('#palette-dialog')
  if (!dlg) return
  $('#palette-input').value = ''
  renderPalette('')
  if (!dlg.open) dlg.showModal()
  $('#palette-input').focus()
}

$$('.nav').forEach(button=>button.addEventListener('click',()=>showView(button.dataset.view).catch(e=>notice(e.message,true))))
$$('[data-go]').forEach(button=>button.addEventListener('click',()=>showView(button.dataset.go).catch(e=>notice(e.message,true))))
$('#toggle-autonomy').addEventListener('click',async e=>{try{await api('/api/control/'+(e.currentTarget.dataset.paused==='1'?'resume':'pause'),{method:'POST'});await refreshStatus()}catch(x){notice(x.message,true)}})
$('#chat-form').addEventListener('submit',async e=>{e.preventDefault();const input=$('#chat-input'),text=input.value.trim();if(!text)return;try{await api('/api/chat',{method:'POST',body:{text}});input.value='';await loadChat();$('#chat-messages').scrollTop=$('#chat-messages').scrollHeight}catch(x){notice(x.message,true)}})
$('#chat-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    $('#chat-form').requestSubmit()
  }
})
$$('.prompt-chip').forEach(chip => chip.addEventListener('click', () => {
  const input = $('#chat-input')
  input.value = chip.dataset.prompt || ''
  input.focus()
}))
$('#chat-messages').addEventListener('click', async e => {
  const btn = e.target.closest('[data-copy]')
  if (!btn) return
  try {
    await navigator.clipboard.writeText(btn.dataset.copy)
    const old = btn.textContent
    btn.textContent = '已复制'
    setTimeout(() => { btn.textContent = old }, 1400)
  } catch (_) {}
})
$('#refresh-queue').addEventListener('click',()=>loadQueue().catch(e=>notice(e.message,true)));['#job-filter','#actor-filter'].forEach(sel=>$(sel).addEventListener('change',()=>loadQueue().catch(e=>notice(e.message,true))));['#activity-platform','#activity-channel'].forEach(sel=>$(sel).addEventListener('change',renderActivity))
if ($('#job-quick-tabs')) {
  $('#job-quick-tabs').addEventListener('click', e => {
    const tab = e.target.closest('.chip-tab')
    if (!tab) return
    $('#job-filter').value = tab.dataset.status
    loadQueue().catch(err => notice(err.message, true))
  })
}
$('#jobs-body').addEventListener('click',async e=>{const b=e.target.closest('[data-job]');if(!b)return;try{if(b.dataset.op==='view'){const job=await api('/api/jobs/'+b.dataset.job);const event=job.event_id?await api('/api/events/'+job.event_id):null;showDetail('事项详情',{job,event});return}await api(`/api/jobs/${b.dataset.job}/${b.dataset.op}`,{method:'POST'});await loadQueue()}catch(x){notice(x.message,true)}})
$('#timers-list').addEventListener('click',async e=>{const b=e.target.closest('[data-timer]');if(!b)return;try{if(b.dataset.op==='edit'){editTimer(b.dataset.timer);return}await api('/api/timers/'+b.dataset.timer,b.dataset.op==='delete'?{method:'DELETE'}:{method:'PATCH',body:{enabled:b.dataset.enabled!=='1'}});if(editingTimer?.id===b.dataset.timer&&b.dataset.op==='delete')cancelTimerEdit();await loadQueue()}catch(x){notice(x.message,true)}})
$('#timer-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,v=formValues(f),payload={...(editingTimer?.payload||{})};if(v.instruction.trim())payload.instruction=v.instruction.trim();else delete payload.instruction;const body={title:v.title,actor_id:v.actor_id,kind:editingTimer?.kind||'scheduled',payload,next_at:Date.now()/1000+Number(v.after),interval_seconds:v.interval?Number(v.interval):null,autonomous:f.elements.autonomous.checked};try{const isEdit=Boolean(editingTimer);await api(isEdit?'/api/timers/'+editingTimer.id:'/api/timers',{method:isEdit?'PATCH':'POST',body});cancelTimerEdit();await loadQueue();notice(isEdit?'小闹钟已修改':'小闹钟已定好')}catch(x){notice(x.message,true)}})
$('#timer-cancel').addEventListener('click',cancelTimerEdit)
$('#activity-list').addEventListener('click',e=>{const b=e.target.closest('[data-activity]');if(!b)return;const item=(b.dataset.activity==='event'?cachedEvents:cachedActions).find(x=>x.id===b.dataset.id);showDetail(b.dataset.activity==='event'?'互动来信详情':'外发详情',item)})
$('#actions-body').addEventListener('click',e=>{const b=e.target.closest('[data-action]');if(!b)return;showDetail('外发足迹详情',cachedActions.find(x=>x.id===b.dataset.action))})
$('#close-detail').addEventListener('click',()=>$('#detail-dialog').close())
if ($('#copy-detail')) {
  $('#copy-detail').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#detail-content').textContent); notice('已复制') } catch (_) {}
  })
}
if ($('#context-editor')) $('#context-editor').addEventListener('input', updateContextCharCount)
$('#save-persona').addEventListener('click',async()=>{try{await api('/api/memories',{method:'POST',body:{memory_id:'persona',kind:'persona',title:'核心人格',content:$('#persona-editor').value}});notice('灵魂设定已保存')}catch(x){notice(x.message,true)}})
if ($('#restore-whale-persona')) {
  $('#restore-whale-persona').addEventListener('click', async () => {
    try {
      $('#persona-editor').value = WHALE_PERSONA
      await api('/api/memories',{method:'POST',body:{memory_id:'persona',kind:'persona',title:'核心人格',content:WHALE_PERSONA}})
      notice('已换上大肥鱼嘴臭毒舌灵魂设定')
    } catch (x) { notice(x.message, true) }
  })
}
$('#save-context').addEventListener('click',async()=>{try{await api('/api/memories',{method:'POST',body:{memory_id:'active_context',kind:'context',title:'近期背景看板',content:$('#context-editor').value}});notice('近期记事板已保存')}catch(x){notice(x.message,true)}})
$('#broadcast-context').addEventListener('click',async()=>{try{await api('/api/memory/broadcast',{method:'POST',body:{content:$('#context-editor').value}});notice('记事板已保存并传达给所有分身')}catch(x){notice(x.message,true)}})
$('#search-memory').addEventListener('click',()=>loadMemory().catch(e=>notice(e.message,true)));$('#memory-status-filter').addEventListener('change',()=>loadMemory().catch(e=>notice(e.message,true)));$('#new-memory').addEventListener('click',()=>fillMemory())
$('#memory-list').addEventListener('click',e=>{const b=e.target.closest('[data-memory]');if(!b)return;const id=b.dataset.memory;fillMemory(cachedMemories.find(item=>item.id===id));$$('#memory-list [data-memory]').forEach(row=>row.classList.toggle('selected',row.dataset.memory===id))})
$('#memory-form').addEventListener('submit',async e=>{e.preventDefault();const v=formValues(e.target);try{if(!v.memory_id)delete v.memory_id;v.tags=splitIds(v.tags);v.metadata=JSON.parse(v.metadata||'{}');const saved=await api('/api/memories',{method:'POST',body:v});fillMemory(saved);await loadMemory();notice('已收进记忆库')}catch(x){notice(x.message,true)}})
$('#delete-memory').addEventListener('click',async()=>{if(!selectedMemory)return;try{await api('/api/memories/'+selectedMemory,{method:'DELETE'});fillMemory();await loadMemory();notice('已从记忆库抹掉')}catch(x){notice(x.message,true)}})
$('#platform-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,platforms=config.platforms,admins=config.admin_ids;for(const [short,p] of [['bili','bilibili'],['douyin','douyin']]){platforms[p].enabled=$(`#${short}-enabled`).checked;platforms[p].poll_seconds=Number(f.elements[`${short}_poll`].value);platforms[p].messages=f.elements[`${short}_messages`].checked;platforms[p].comments=f.elements[`${short}_comments`].checked;platforms[p].groups=f.elements[`${short}_groups`].checked;platforms[p].group_reply_interval_seconds=Number(f.elements[`${short}_group_minutes`].value)*60}platforms.weixin.enabled=$('#weixin-enabled').checked;platforms.weixin.messages=f.elements.weixin_messages.checked;platforms.weixin.poll_seconds=Number(f.elements.weixin_poll.value);admins.bilibili=splitIds(f.elements.bili_admins.value);admins.douyin=splitIds(f.elements.douyin_admins.value);admins.xiaohongshu=splitIds(f.elements.xhs_admins.value);try{await api('/api/config/platforms',{method:'PUT',body:{value:platforms}});await api('/api/config/admin_ids',{method:'PUT',body:{value:admins}});await api('/api/config/chat_media',{method:'PUT',body:{value:{...(config.chat_media||{}),read_inbound_images:f.elements.read_inbound_images.checked,save_inbound_images:f.elements.save_inbound_images.checked}}});config.chat_media={...(config.chat_media||{}),read_inbound_images:f.elements.read_inbound_images.checked,save_inbound_images:f.elements.save_inbound_images.checked};notice('巡游规则已保存')}catch(x){notice(x.message,true)}})
$('#publish-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,value={enabled:f.elements.enabled.checked,daily_limits:{}};for(const p of ['bilibili','douyin','xiaohongshu']) value.daily_limits[p]=Number(f.elements[p].value);try{await api('/api/config/publishing',{method:'PUT',body:{value}});notice('发作品限额已保存')}catch(x){notice(x.message,true)}})
$('#image-quota-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,value={enabled:f.elements.enabled.checked,daily_limits:{...config.image_messaging.daily_limits,douyin:Number(f.elements.douyin.value),weixin:Number(f.elements.weixin.value)}};try{config.image_messaging=await api('/api/config/image_messaging',{method:'PUT',body:{value}});notice('发图限额已保存')}catch(x){notice(x.message,true)}})
$$('.login-button').forEach(b=>b.addEventListener('click',()=>startLogin(b.dataset.platform,b.dataset.channel)))
$$('.check-auth').forEach(b=>b.addEventListener('click',async()=>{try{const d=await api(`/api/platforms/${b.dataset.platform}/check_auth`,{method:'POST',body:{}});const label=b.dataset.platform==='bilibili'?'#bili-state':b.dataset.platform==='weixin'?'#weixin-state':'#douyin-state';$(label).textContent=d.channels?`私信：${d.channels.messages.authenticated?'在线':'未登录'} · 创作者：${d.channels.comments_and_publish.authenticated?'在线':'未登录'}`:d.authenticated?`在线：${d.nickname||d.account_id}`:'未登录';}catch(x){notice(x.message,true)}}))
$('#close-login').addEventListener('click',()=>$('#login-dialog').close());$('#login-dialog').addEventListener('close',()=>{loginTarget=null;clearTimeout(loginTimer)});$('#check-login').addEventListener('click',()=>{clearTimeout(loginTimer);pollLogin()})
$('#folder-form').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/config/creation_folder',{method:'PUT',body:{value:e.target.elements.path.value}});await loadCreation();notice('百宝箱目录已记住')}catch(x){notice(x.message,true)}})
$('#open-creation-folder').addEventListener('click',async()=>{try{const d=await api('/api/creation/open',{method:'POST',body:{}});notice(`已打开：${d.folder}`)}catch(x){notice(x.message,true)}})
$('#scan-creation').addEventListener('click',async()=>{try{const d=await api('/api/creation/scan',{method:'POST',body:{}});notice(`清点完毕，新发现 ${d.jobs.length} 份素材`);await loadCreation()}catch(x){notice(x.message,true)}})
$('#files-body').addEventListener('click',async e=>{const b=e.target.closest('[data-file]');if(!b)return;try{if(!b.dataset.type.startsWith('text/')&&!b.dataset.file.match(/\.(md|json|csv|txt)$/i)){$('#file-preview').textContent=`素材路径：${b.dataset.file}\n这是一份多媒体素材，创作分身会直接读取它。`;return}const d=await api('/api/creation/read',{method:'POST',body:{path:b.dataset.file}});$('#file-preview').textContent=d.content}catch(x){notice(x.message,true)}})
$('#media-library-body').addEventListener('click',async e=>{const copy=e.target.closest('[data-copy]');if(copy){try{await navigator.clipboard.writeText(copy.dataset.copy);notice('媒体路径已复制')}catch(_){}}const remove=e.target.closest('[data-media-delete]');if(remove){try{await api('/api/media/'+remove.dataset.mediaDelete,{method:'DELETE'});await loadMediaLibrary();notice('媒体记录已删除')}catch(x){notice(x.message,true)}}})
$('#refresh-media-library').addEventListener('click',()=>loadMediaLibrary().catch(x=>notice(x.message,true)))
if ($('#copy-preview')) {
  $('#copy-preview').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('#file-preview').textContent); notice('文字已复制') } catch (_) {}
  })
}
$('#sessions-body').addEventListener('input',e=>{if(e.target.dataset.autoLimit)compactDrafts[e.target.dataset.autoLimit]=e.target.value})
$('#sessions-body').addEventListener('change',e=>{const actor=e.target.dataset.autoCheck;if(actor)saveAutoCompaction(actor).catch(x=>notice(x.message,true))})
$('#sessions-body').addEventListener('click',async e=>{const b=e.target.closest('[data-session]');if(!b)return;try{if(b.dataset.op==='save-auto'){await saveAutoCompaction(b.dataset.session);return}const job=await api(`/api/sessions/${b.dataset.session}/${b.dataset.op}`,{method:'POST'});notice(`${names[b.dataset.session]||b.dataset.session}正在排队处理`);watchSessionJob(job.id,b.dataset.session,b.dataset.op).catch(x=>notice(x.message,true));await loadSessions()}catch(x){notice(x.message,true)}})
$('#image-form').addEventListener('submit',async e=>{e.preventDefault();try{const d=await api('/api/image-generation/config',{method:'PUT',body:formValues(e.target)});e.target.elements.api_key.value='';e.target.elements.api_key.placeholder=d.key_saved?'已保存，留空保持不变':'输入密钥';notice('画笔配置已保存')}catch(x){notice(x.message,true)}})
$('#model-form').addEventListener('submit',async e=>{e.preventDefault();const v=formValues(e.target);v.context_length=Number(v.context_length);v.max_tokens=Number(v.max_tokens);try{notice('正在换好大脑并重启…');await api('/api/model',{method:'POST',body:v});e.target.elements.api_key.value='';notice('新大脑已就位')}catch(x){notice(x.message,true)}})
$('#pricing-select').addEventListener('change',e=>renderPricing(e.target.value))
$('#pricing-new').addEventListener('click',()=>{renderPricing('');$('#pricing-form').elements.name.focus()})
$('#pricing-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target, id=f.elements.profile_id.value||`custom-${Date.now()}-${Math.random().toString(36).slice(2,8)}`, existing=config.pricing.profiles.find(item=>item.id===id)
  const profile={...(existing||{}),id,name:f.elements.name.value.trim(),uncached_input:Number(f.elements.uncached_input.value),cached_input:Number(f.elements.cached_input.value),output:Number(f.elements.output.value)}
  const profiles=config.pricing.profiles.filter(item=>item.id!==id).concat(profile), value={selected_profile:id,profiles}
  try{config.pricing=await api('/api/config/pricing',{method:'PUT',body:{value}});renderPricing(id);notice('单价标准已保存')}catch(x){notice(x.message,true)}
})
$('#import-form').addEventListener('submit',async e=>{e.preventDefault();const v=formValues(e.target);try{notice('正在导入配置并重启…');await api('/api/model/import',{method:'POST',body:{path:v.path,index:Number(v.index)}});await loadConfig();notice('配置已成功导入')}catch(x){notice(x.message,true)}})
$('#autonomy-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.target,value={enabled:f.elements.enabled.checked};for(const key of ['main_interval_seconds','memory_interval_seconds','background_scan_seconds'])value[key]=Number(f.elements[key].value);try{await api('/api/config/autonomy',{method:'PUT',body:{value}});notice('作息节拍已保存')}catch(x){notice(x.message,true)}})

if ($('#open-palette')) $('#open-palette').addEventListener('click', openPalette)
if ($('#palette-dialog')) {
  $('#palette-dialog').addEventListener('click', e => {
    if (e.target === $('#palette-dialog')) $('#palette-dialog').close()
    const item = e.target.closest('[data-pidx]')
    if (!item) return
    const fn = $('#palette-list')._filtered?.[Number(item.dataset.pidx)]?.run
    $('#palette-dialog').close()
    if (fn) fn()
  })
  $('#palette-input').addEventListener('input', e => renderPalette(e.target.value))
  $('#palette-input').addEventListener('keydown', e => {
    const items = $$('#palette-list .palette-item')
    if (!items.length) return
    if (e.key === 'ArrowDown') { e.preventDefault(); paletteIndex = (paletteIndex + 1) % items.length }
    else if (e.key === 'ArrowUp') { e.preventDefault(); paletteIndex = (paletteIndex - 1 + items.length) % items.length }
    else if (e.key === 'Enter') {
      e.preventDefault()
      const fn = $('#palette-list')._filtered?.[paletteIndex]?.run
      $('#palette-dialog').close()
      if (fn) fn()
      return
    }
    items.forEach((el, i) => el.classList.toggle('active', i === paletteIndex))
  })
}

window.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    openPalette()
  } else if (e.altKey && e.key >= '1' && e.key <= '7') {
    e.preventDefault()
    const views = ['home','queue','sessions','memory','platforms','creation','settings']
    showView(views[Number(e.key) - 1]).catch(err => notice(err.message, true))
  }
})
window.addEventListener('resize', updateNavGlider)

async function boot(){updateNavGlider();try{await loadConfig();await refreshStatus();const context=await api('/api/memories/active_context');$('#context-preview').textContent=context.content||'暂无近期记事'}catch(e){$('#sidebar-status').textContent='连接断开';notice(e.message,true)}}
boot();setInterval(()=>refreshStatus().catch(e=>{$('#sidebar-status').textContent='连接断开';console.error(e)}),5000)
