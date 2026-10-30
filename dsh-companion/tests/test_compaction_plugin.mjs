import test from 'node:test'
import assert from 'node:assert/strict'
import { summarizeInChunks } from '../src/dsh_companion/compaction_plugin.mjs'

const agent = {options: {provider: 'p', model: 'm'}, session: {id: 's', requestHeader: () => null}}

test('chunked summaries carry previous state and account for all calls', async () => {
  const requests = []
  const ctx = {llm: {async *stream(options) {
    requests.push(options)
    yield {type: 'text-delta', index: 0, text: `summary-${requests.length}`}
    yield {type: 'usage', usage: {inputTokens: 10, outputTokens: 2}}
    yield {type: 'finish', reason: {kind: 'stop'}}
  }}}
  const input = {messages: [{role: 'system', content: [{type: 'text', text: 'skip'}]},
    {role: 'user', content: [{type: 'text', text: 'first-second-third'}]}]}
  const result = await summarizeInChunks(ctx, input, agent, new AbortController().signal, 12)
  assert.ok(requests.length > 1)
  assert.match(requests[1].messages[0].content[0].text, /summary-1/)
  assert.equal(result.summary[0].text, `summary-${requests.length}`)
  assert.equal(result.usage.inputTokens, requests.length * 10)
  assert.equal(result.usage.outputTokens, requests.length * 2)
  assert.equal(result.llmStreamCall, undefined)
})

test('a failed or truncated chunk never returns a successful checkpoint', async () => {
  const ctx = {llm: {async *stream() {
    yield {type: 'text-delta', index: 0, text: 'partial'}
    yield {type: 'finish', reason: {kind: 'max-tokens'}}
  }}}
  await assert.rejects(summarizeInChunks(ctx, {messages: [{role: 'user', content: [{type: 'text', text: 'hello'}]}]},
    agent, new AbortController().signal), /max-tokens/)
})

test('cancellation stops before a model request', async () => {
  const controller = new AbortController(); controller.abort()
  const ctx = {llm: {stream() { throw new Error('must not run') }}}
  await assert.rejects(summarizeInChunks(ctx, {messages: [{role: 'user', content: [{type: 'text', text: 'hello'}]}]},
    agent, controller.signal), /abort/i)
})
