// Resolve existing private chats, including after the in-memory event cache was lost.
export async function resolveContact(account, contacts, params) {
  const find = () => contacts.get(params.conversation_id) ?? account.pickFriend(params.recipient_id)
    ?? account.pickStranger(params.recipient_id)
    ?? [...account.fl.values(), ...account.sl.values()].find(item => item.threadId === params.conversation_id)
  let contact = find()
  if (contact) return contact
  await account.refreshStrangerConversations()
  const strangers = await account.getStrangerList()
  contact = strangers.find(item => item.threadId === params.conversation_id) ?? account.pickStranger(params.recipient_id)
  if (contact) return contact
  const friends = await account.getFriendList()
  return friends.find(item => item.threadId === params.conversation_id) ?? account.pickFriend(params.recipient_id)
}
