import assert from 'node:assert/strict'
import test from 'node:test'
import { directShareMessage, splitDouyinLinks } from '../runtime/douyin_share.mjs'

test('作品链接拆成文字和原生分享卡', () => {
  assert.deepEqual(splitDouyinLinks('看这个 https://www.douyin.com/video/123456789。'), [
    { type: 'text', text: '看这个 ' },
    { type: 'share', url: 'https://www.douyin.com/video/123456789' },
  ])
  assert.deepEqual(directShareMessage('https://www.douyin.com/video/123456789'), {
    type: 'share',
    work: { itemId: '123456789' },
  })
})

test('抖音短链使用链接卡', () => {
  assert.deepEqual(directShareMessage('https://v.douyin.com/abc123'), {
    type: 'link',
    link: { url: 'https://v.douyin.com/abc123' },
  })
})
