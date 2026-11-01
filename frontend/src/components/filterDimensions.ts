/**
 * 过滤维度的规范术语映射。
 *
 * **为什么要这层**:后端的过滤字段名是契约(`tenant` / `department` /
 * `doc_type` / `version` / `document_id`),但界面文案要跟 `GLOSSARY.md` 的
 * 规范术语走(租户 / 部门 / 文档类型 / 版本 / 文档名)—— 用户读的是术语,
 * 不是字段名。两处界面都要展示"实际生效的过滤维度"(检索页与问答页),
 * 故把这层映射放在一处。
 */

/** 过滤字段名 → 界面文案。 */
const FILTER_DIMENSION_LABELS: Record<string, string> = {
  tenant: '租户',
  department: '部门',
  doc_type: '文档类型',
  version: '版本',
  document_id: '文档名',
}

/** 一个过滤维度的界面文案;未登记的维度退回字段名(不隐藏事实)。 */
export function filterDimensionLabel(key: string): string {
  return FILTER_DIMENSION_LABELS[key] ?? key
}

/**
 * 把生效过滤维度渲染成一行文案。
 *
 * 空过滤维度渲染成"无" —— 界面要如实说"这次没加过滤",而不是留白。
 */
export function describeEffectiveFilters(filters: Record<string, string>): string {
  const entries = Object.entries(filters)
  if (entries.length === 0) {
    return '无'
  }
  return entries.map(([key, value]) => `${filterDimensionLabel(key)}:${value}`).join('，')
}
