import assert from 'node:assert/strict'
import { mkdtempSync, writeFileSync, unlinkSync, rmdirSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { test } from 'node:test'
import { apply } from '../src/dsh_companion/context_plugin.mjs'

test('empty persona adds no personality section; custom persona is injected', () => {
  const dir = mkdtempSync(join(tmpdir(), 'companion-context-'))
  const previous = process.env.COMPANION_CONTEXT_DIR
  process.env.COMPANION_CONTEXT_DIR = dir
  const file = join(dir, 'test.json')
  try {
    let section
    apply({ on() {}, effect() {}, systemPrompt: { section(value) { section = value } } })
    const context = { agent: { session: { id: 'test' } } }
    writeFileSync(file, JSON.stringify({ actor_id: 'main', persona: '', recent_context: 'current goal' }))
    const blank = section.text(context)
    assert.ok(!blank.includes('共享核心人格'))
    assert.ok(blank.includes('current goal'))
    writeFileSync(file, JSON.stringify({ actor_id: 'main', persona: 'custom personality', recent_context: '' }))
    assert.ok(section.text(context).includes('custom personality'))
  } finally {
    unlinkSync(file)
    rmdirSync(dir)
    if (previous === undefined) delete process.env.COMPANION_CONTEXT_DIR
    else process.env.COMPANION_CONTEXT_DIR = previous
  }
})

// Regression for the production fatal EPERM at writeContext -> renameSync.
import fs, { existsSync, readFileSync } from 'node:fs'
import { mock } from 'node:test'
import { publishJson } from '../src/dsh_companion/context_plugin.mjs'

test('locked Windows snapshot retains complete old JSON and can publish on the next attempt', () => {
  const dir = mkdtempSync(join(tmpdir(), 'companion-locked-snapshot-'))
  const file = join(dir, 'test.context.json')
  const rename = fs.renameSync
  try {
    writeFileSync(file, JSON.stringify({revision: 1}))
    for (const code of ['EPERM', 'EACCES', 'EBUSY']) {
      const fault = mock.method(fs, 'renameSync', () => { throw Object.assign(new Error('file locked'), {code}) })
      assert.equal(publishJson(file, {revision: 2}), false)
      assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), {revision: 1})
      fault.mock.restore()
    }
    assert.equal(publishJson(file, {revision: 3}), true)
    assert.deepEqual(JSON.parse(readFileSync(file, 'utf8')), {revision: 3})
    const fault = mock.method(fs, 'renameSync', () => { throw Object.assign(new Error('disk full'), {code:'ENOSPC'}) })
    assert.throws(() => publishJson(file, {}), /disk full/)
    fault.mock.restore()
  } finally {
    fs.renameSync = rename
    for (const path of [file, `${file}.tmp`]) if (existsSync(path)) unlinkSync(path)
    rmdirSync(dir)
  }
})

test('timer retries the newest context and compact result without killing other sessions', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'companion-context-retry-'))
  const previous = process.env.COMPANION_CONTROL_DIR
  process.env.COMPANION_CONTROL_DIR = dir
  const listeners = new Map()
  const session = {id:'one'}, other = {id:'two'}
  const agent = {session}, agents = [agent, {session:other}]
  let tick, revision = 1, locked = true
  const originalRename = fs.renameSync
  const interval = mock.method(globalThis, 'setInterval', callback => {
    tick = callback
    return {unref(){}}
  })
  const rename = mock.method(fs, 'renameSync', (from, to) => {
    if (locked && to.includes('one.')) throw Object.assign(new Error('locked'), {code:'EPERM'})
    return originalRename(from, to)
  })
  try {
    apply({
      on(name, callback) {listeners.set(name, callback)},
      effect(callback) {callback()},
      agents: {list() {return agents}},
      sessionProjections: {snapshot() {return {values:{contextPressure:{contextTokens:10}},asOfSeq:revision}}},
      compaction: {async compactNow() {return {summarySeq:9,shadowedSeqs:[1,2],shadowedTokenCount:50}}},
      systemPrompt: {section(){}},
    })
    listeners.get('session/event')(session)
    listeners.get('session/event')(other)
    assert.doesNotThrow(() => tick())
    assert.equal(existsSync(join(dir, 'one.context.json')), false)
    assert.equal(JSON.parse(readFileSync(join(dir, 'two.context.json'))).revision, 1)
    revision = 7
    listeners.get('session/event')(session)
    writeFileSync(join(dir, 'one.compact'), '')
    tick()
    await new Promise(resolve => setImmediate(resolve))
    tick() // compact result is also still locked, but not lost
    assert.equal(existsSync(join(dir, 'one.compact-result.json')), false)
    locked = false
    tick()
    assert.equal(JSON.parse(readFileSync(join(dir, 'one.context.json'))).revision, 7)
    const result = JSON.parse(readFileSync(join(dir, 'one.compact-result.json')))
    assert.equal(result.ok, true)
    assert.equal(result.compacted, true)
    assert.equal(result.summary_seq, 9)
  } finally {
    interval.mock.restore();rename.mock.restore()
    for (const name of fs.readdirSync(dir)) unlinkSync(join(dir, name))
    rmdirSync(dir)
    if (previous === undefined) delete process.env.COMPANION_CONTROL_DIR
    else process.env.COMPANION_CONTROL_DIR = previous
  }
})
