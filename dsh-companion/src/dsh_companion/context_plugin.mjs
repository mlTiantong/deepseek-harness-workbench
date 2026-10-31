import { existsSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

// 每次模型组装系统提示词时，读取该角色本轮的共享人格和背景。
export const name = 'companion-context'
export const inject = ['systemPrompt', 'agents', 'compaction', 'sessionProjections']

export function apply(ctx) {
  const running = new Map()
  const compacting = new Map()
  const dirty = new Set()
  function writeContext(session) {
    const snapshot = ctx.sessionProjections.snapshot(session, ['contextPressure'])
    const path = join(process.env.COMPANION_CONTROL_DIR, `${session.id}.context.json`)
    writeFileSync(`${path}.tmp`, JSON.stringify({ ...(snapshot.values.contextPressure ?? {}), revision: snapshot.asOfSeq }), 'utf8')
    renameSync(`${path}.tmp`, path)
  }
  ctx.on('session/event', session => dirty.add(session))
  ctx.on('agent/created', ({ agent }) => dirty.add(agent.session))
  ctx.on('agent/status', ({ agent, status }) => {
    if (status === 'running') running.set(agent.session.id, agent)
    else running.delete(agent.session.id)
  })
  ctx.effect(() => {
    async function compact(agent) {
      const id = agent.session.id
      const controller = new AbortController()
      compacting.set(id, controller)
      let response
      try {
        const result = await ctx.compaction.compactNow(agent, controller.signal)
        response = result ? { ok: true, compacted: true, summary_seq: result.summarySeq,
          shadowed_items: result.shadowedSeqs.length, shadowed_tokens: result.shadowedTokenCount }
          : { ok: true, compacted: false }
      } catch (error) {
        const cause = error.cause?.message
        response = { ok: false, error: `${error.name}: ${error.message}${cause ? ` (${cause})` : ''}` }
      } finally {
        writeContext(agent.session)
        compacting.delete(id)
      }
      const path = join(process.env.COMPANION_CONTROL_DIR, `${id}.compact-result.json`)
      writeFileSync(`${path}.tmp`, JSON.stringify(response), 'utf8')
      renameSync(`${path}.tmp`, path)
    }
    const timer = setInterval(() => {
      for (const session of dirty) writeContext(session)
      dirty.clear()
      for (const agent of ctx.agents.list()) {
        const id = agent.session.id
        const cancel = join(process.env.COMPANION_CONTROL_DIR, `${id}.cancel`)
        if (existsSync(cancel)) {
          unlinkSync(cancel)
          if (compacting.has(id)) compacting.get(id).abort()
          else if (running.has(id)) agent.cancel({ kind: 'user' })
        }
        const request = join(process.env.COMPANION_CONTROL_DIR, `${id}.compact`)
        if (existsSync(request)) {
          unlinkSync(request)
          void compact(agent)
        }
      }
    }, 200)
    timer.unref()
    return () => clearInterval(timer)
  })
  ctx.systemPrompt.section({
    name: 'companion:shared-context',
    order: 10,
    interpolate: false,
    text: context => {
      const sessionId = context.agent?.session?.id
      if (!sessionId) return ''
      const path = join(process.env.COMPANION_CONTEXT_DIR, `${sessionId}.json`)
      if (!existsSync(path)) return ''
      const data = JSON.parse(readFileSync(path, 'utf8'))
      const person = data.acquaintance
      return [
        `## 当前执行角色\n${data.actor_id}`,
        data.persona ? `## 共享核心人格\n${data.persona}` : '',
        `## 近期共同背景\n${data.recent_context}`,
        person ? `## 当前对象的熟人印象\n${person.content}` : '',
      ].filter(Boolean).join('\n\n')
    },
  })
}
