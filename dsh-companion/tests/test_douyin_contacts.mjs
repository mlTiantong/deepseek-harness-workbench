import assert from 'node:assert/strict'
import { resolveContact } from '../runtime/douyin_contacts.mjs'
const chat = { threadId: 'thread' }
let refreshed = 0
const account = { fl: new Map(), sl: new Map(), pickFriend: () => undefined, pickStranger: () => undefined,
  refreshStrangerConversations: async () => { refreshed++ }, getStrangerList: async () => [chat],
  getFriendList: async () => { throw Error('should not load friends') } }
assert.equal(await resolveContact(account, new Map(), { conversation_id:'thread', recipient_id:'1' }), chat)
assert.equal(refreshed, 1)
assert.equal(await resolveContact(account, new Map([['thread', chat]]), { conversation_id:'thread' }), chat)
assert.equal(refreshed, 1)
console.log('contact resolution passed')
