import { parseMessageContent } from 'douyin-im/protocol'

export function messageData(event) {
  const group = event.conversationType === 2
  const contact = group ? event.group : event.chatType === 'stranger' ? event.stranger : event.friend
  const parsed = event.content ?? parseMessageContent(event.rawContent ?? '', event.messageType)
  let content = parsed
  if (parsed.kind === 'emoji') {
    const raw = typeof event.rawContent === 'string' ? JSON.parse(event.rawContent || '{}') : event.rawContent ?? {}
    const resource = raw.url ?? {}
    const urls = [...new Set([...(resource.url_list ?? []), ...(parsed.urls ?? []),
      parsed.url, resource.uri].filter(url => typeof url === 'string' && /^https?:\/\//.test(url)))]
    content = { ...parsed, url: urls[0] ?? '', urls }
  }
  const external = event.serverMessageId ?? event.clientMessageId
  return {
    platform: 'douyin', account_id: String(event.account.uid ?? ''),
    event_type: group ? 'group_message' : 'message',
    external_id: external ? (group ? `${event.threadId}:${external}` : String(external)) : null,
    conversation_id: String(event.threadId), sender_id: String(event.senderUid),
    sender_name: group ? (event.member?.displayName ?? event.member?.nickname ?? '') : (contact?.nickname ?? ''),
    occurred_at: event.time,
    payload: { text: event.text ?? content.text ?? '', message_type: event.messageType,
      sender_sec_uid: event.senderSecUid, conversation_short_id: event.conversationShortId,
      raw_content: event.rawContent, raw: event.raw, content,
      group_name: group ? contact?.name ?? '' : undefined,
      mentions: event.mentions ?? content.mentions ?? [], is_mention_me: Boolean(event.isMentionMe) },
  }
}

export function historyEvent(message, group, account) {
  const parsed = parseMessageContent(message.content ?? '', message.msgType)
  const timestamp = Number(message.createTime ?? 0)
  const own = new Set([String(account.uid ?? ''), String(account.imUid ?? '')])
  return messageData({ account, group, conversationType: 2, threadId: group.threadId,
    conversationShortId: group.conversationShortId, senderUid: String(message.senderUid),
    senderSecUid: message.senderSecUid, serverMessageId: message.msgId,
    clientMessageId: message.clientMessageId, messageType: message.msgType,
    rawContent: message.content, raw: message, content: parsed, text: parsed.text,
    time: timestamp > 10000000000 ? Math.floor(timestamp / 1000) : timestamp,
    member: group.pickMember(String(message.senderUid)), mentions: parsed.mentions ?? [],
    isMentionMe: (parsed.mentions ?? []).some(item => own.has(String(item.uid))) })
}
