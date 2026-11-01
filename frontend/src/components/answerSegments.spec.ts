/**
 * 回答正文分段的单元测试(接缝:纯函数 `splitAnswerSegments`)。
 *
 * 角标解析的边界行为在这里钉住:越界编号不得变成死链接;普通文本里的
 * 方括号不得被误吞。
 */
import { describe, expect, it } from 'vitest'

import { splitAnswerSegments } from '@/components/answerSegments'

describe('回答正文分段', () => {
  it('把带编号标记的正文拆成文字段与角标段', () => {
    const segments = splitAnswerSegments('年假规则[1]。详见[2]。', new Set([1, 2]))

    expect(segments).toEqual([
      { kind: 'text', text: '年假规则' },
      { kind: 'citation', index: 1 },
      { kind: 'text', text: '。详见' },
      { kind: 'citation', index: 2 },
      { kind: 'text', text: '。' },
    ])
  })

  it('没有编号标记时整段是文字', () => {
    expect(splitAnswerSegments('没有引用标记的回答', new Set([1]))).toEqual([
      { kind: 'text', text: '没有引用标记的回答' },
    ])
  })

  it('越界编号保留为文字(不指向不存在的引用)', () => {
    const segments = splitAnswerSegments('前[1]后[9]', new Set([1]))

    expect(segments).toEqual([
      { kind: 'text', text: '前' },
      { kind: 'citation', index: 1 },
      { kind: 'text', text: '后[9]' },
    ])
  })

  it('非数字的方括号不当作角标', () => {
    expect(splitAnswerSegments('见[附录 A]与[B]', new Set([1]))).toEqual([
      { kind: 'text', text: '见[附录 A]与[B]' },
    ])
  })

  it('正文为空时没有分段', () => {
    expect(splitAnswerSegments('', new Set([1]))).toEqual([])
  })
})
