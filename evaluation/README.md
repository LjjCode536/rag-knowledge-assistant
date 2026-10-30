# 黄金数据集与检索评估

本目录是 T06 评估基座的唯一数据源。数据集版本化进仓库,检索指标与后续迭代都对照它。

## 文件

- `golden_dataset.yaml`:数据集 v1,包含语料清单与 26 条用例(2 条拒答、1 条权限边界、2 条多切片)。
- `corpus/`:评估语料。上传时按 YAML 里的租户 / 部门 / 文档类型 / 版本入库。
- 报告输出:`docs/iterations/0001-t06-retrieval-baseline.{md,json}`。

## 一条命令

```bash
uv run python scripts/evaluate.py
```

默认在进程内启动应用并注入 1024 维确定性向量替身,不触网、不需要密钥。
后续迭代不要覆盖基线,用编号与 slug 生成新报告:

```bash
uv run python scripts/evaluate.py --iteration 0002 --slug t07-advanced-retrieval \
  --run-name "T07 高级检索"
```

连接真实服务(外部模式必须显式给出服务端 `min_score` 与 embedder 口径;建议对专用 / 重启后的服务跑,避免重复入库污染指标):

```bash
uv run python scripts/evaluate.py --base-url http://127.0.0.1:8000 \
  --min-score 0.35 --embedder-label BAAI/bge-m3
```

## 用例字段

| 字段 | 说明 |
| --- | --- |
| `id` | 全数据集唯一;报告与对比表用它定位 |
| `question` | 用户实际会问的问题;扩展用例鼓励用口语 / 同义说法 |
| `category` | 分品类指标维度(如 `leave` / `security` / `unknown` / `permission`) |
| `difficulty` | `easy` / `medium` / `hard`,便于解释失败分布 |
| `expected_chunks` | 预期切片列表;每条用「文档名 + 文内唯一锚点」定位 |
| `expected_answer_keywords` | 供 T09 生成指标使用;T06 只做检索指标 |
| `forbidden_documents` | 权限边界用例的禁读文档;评估器会跑无过滤对照 |
| `filters` | 检索前元数据过滤,如 `{tenant: acme}` |
| `should_refuse` | 知识库没有答案时为 `true`;此类用例不参与 Recall / MRR / NDCG 均值 |
| `notes` | 来源或边界说明 |

## 锚点规则

- 锚点必须是预期切片 `content` 里的**唯一**子串;命中 0 个或多个都会让评估直接失败。
- 不要写 `chunk_id`:每次入库都会生成新的 uuid,写死会让数据集无法长期使用;报告同样用 `文档#index` 定位,不输出随机 uuid。
- 切分参数变化时优先保持锚点在目标切片内,而不是改用例语义。

## 拒答与权限

- **拒答用例**:`should_refuse: true` 且 `expected_chunks: []`。T06 用 `qa_min_score` 判定"没有高分命中即正确拒答";完整生成器拒答正确率留待 T09。
- **权限边界用例**:除 `expected_chunks` 外声明 `forbidden_documents`。评估器检查 top-k 是否泄漏禁读文档,并额外跑一次无过滤检索,证明该文档确实会命中——权限通过不是因为语料无关。

## 增加用例

1. 需要新语料时放进 `corpus/`,并在 YAML 的 `corpus` 清单里补上租户等元数据。
2. 在任一目标切片里选一个唯一、稳定的短语作为 `anchor`。
3. 先用 `uv run python scripts/evaluate.py --report <临时路径> --json-report <临时路径>` 跑通,确认锚点唯一、报告可追溯。
4. 阈值 / K / 切分参数变化时,在报告中写明,避免和旧基线混比。
5. 多切片用例直接在 `expected_chunks` 下列多条锚点;报告会按 Recall@K / NDCG 体现"只召回一部分"的损失。
