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
