import test from 'node:test'
import assert from 'node:assert/strict'
import { messageData, historyEvent } from '../runtime/douyin_messages.mjs'

const account = { uid: 'self', imUid: 'im-self' }
const group = { threadId: 'group-1', conversationShortId: '123', name: '我的群',
  pickMember: uid => ({ displayName: `成员-${uid}` }) }

test('群消息含艾特、发送者、图片元信息', () => {
  const event = messageData({ account, group, conversationType: 2, threadId: 'group-1',
    senderUid: 'other', serverMessageId: 'm1', time: 123, member: { displayName: '小王' },
    content: { kind: 'image', image: { originUrls: ['https://cdn.test/pic'], skey: '00' } },
    mentions: [{ uid: 'self' }], isMentionMe: true })
  assert.equal(event.event_type, 'group_message')
  assert.equal(event.external_id, 'group-1:m1')
  assert.equal(event.payload.content.image.originUrls[0], 'https://cdn.test/pic')
  assert.equal(event.payload.is_mention_me, true)
})

test('历史群消息识别表情并与实时消息使用相同事件 ID', () => {
  const event = historyEvent({ senderUid: 'other', msgId: 'm2', msgType: 7, createTime: 1700000000000,
    content: JSON.stringify({ aweType: 507, url: { uri: 'https://cdn.test/emoji' } }) }, group, account)
  assert.equal(event.external_id, 'group-1:m2')
  assert.equal(event.payload.content.kind, 'emoji')
  assert.equal(event.payload.content.url, 'https://cdn.test/emoji')
  assert.equal(event.sender_name, '成员-other')
  assert.equal(event.occurred_at, 1700000000)
})
