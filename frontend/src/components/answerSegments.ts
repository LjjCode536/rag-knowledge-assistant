/**
 * 回答正文的分段解析:把带 `[编号]` 标记的回答正文拆成"文字段 + 引用角标段"。
 *
 * **为什么需要它**:回答正文里的 `[1]`、`[2]` 是生成器标注的依据(后端
 * Prompt 要求"用 [编号] 标注所依据的资料"),界面要把它们变成**可点击的
 * 角标**——点击定位到下方对应的引用条目。纯文本渲染会把标记原样留在正文里,
 * 用户看得见编号却点不动,也就无法核实"这个编号对应哪条资料"。
 *
 * **口径**:只识别**方括号包裹的十进制数字**(`[1]`、`[12]`),且编号落在
 * 当前回答实际给出的引用范围内;越界的编号(如 `[9]` 而引用只有 2 条)
 * 当作普通文字保留 —— 宁可少一个角标,也不要把用户引到一个不存在的锚点。
 *
 * 术语遵循 `GLOSSARY.md`:角标指向的是**引用**(不是"出处""来源")。
 */

/** 回答正文的一段:文字段或引用角标段。 */
export type AnswerSegment =
  | { kind: 'text'; text: string }
  | { kind: 'citation'; index: number }

/** 角标引用的编号集合(来自本次回答的引用列表)。 */
export type CitationIndexSet = ReadonlySet<number>

/** 形如 `[12]` 的编号标记。 */
const CITATION_PATTERN = /\[(\d+)\]/g

/**
 * 把回答正文拆成文字段与引用角标段。
 *
 * @param text 回答正文(可能已被打字机动画截断)
 * @param knownIndexes 本次回答实际给出的引用编号;不在其中的编号保留为文字
 */
export function splitAnswerSegments(
  text: string,
  knownIndexes: CitationIndexSet,
): AnswerSegment[] {
  const segments: AnswerSegment[] = []
  let cursor = 0

  for (const match of text.matchAll(CITATION_PATTERN)) {
    const index = Number.parseInt(match[1], 10)
    const start = match.index
    // 越界编号:不拆,留给后续匹配或留在文字段里
    if (!knownIndexes.has(index)) {
      continue
    }
    if (start > cursor) {
      segments.push({ kind: 'text', text: text.slice(cursor, start) })
    }
    segments.push({ kind: 'citation', index })
    cursor = start + match[0].length
  }

  if (cursor < text.length) {
    segments.push({ kind: 'text', text: text.slice(cursor) })
  }

  return segments
}
