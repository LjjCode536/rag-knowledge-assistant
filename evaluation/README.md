# 黄金数据集与检索评估

本目录是 T06 评估基座的唯一数据源。数据集版本化进仓库,检索指标与后续迭代都对照它。

## 文件

- `golden_dataset.yaml`:数据集 v1,包含语料清单与 26 条用例(2 条拒答、1 条权限边界、2 条多切片)。
- `corpus/`:评估语料。上传时按 YAML 里的租户 / 部门 / 文档类型 / 版本入库。
- 报告输出:`docs/iterations/0001-t06-retrieval-baseline.{md,json}` 与
  `0003-t09-generation-metrics.{md,json}`。

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

## T07 高级检索消融

无密钥的单次运行默认「全关」,与 T06 基线同口径(报告含「与基线对比」差值表):

```bash
uv run python scripts/evaluate.py --iteration 0002 --slug t07-ablation \
  --run-name "T07 高级检索" --ablate
```

- `--ablate` 在同一数据集上重跑开关矩阵:全关 / 混合 / 重排 / 混合+重排 / +MMR / +查询改写 / +多查询扩展;重排与 LLM 规划组合需要 `SILICONFLOW_API_KEY`,无密钥时显式标注跳过,不静默降级出数。
- `--hybrid/--no-hybrid`、`--rerank/--no-rerank`、`--mmr/--no-mmr`、`--rewrite/--no-rewrite`、`--multi-query/--no-multi-query` 覆盖单次运行的检索开关;`--use-app-defaults` 改用应用默认(需上游密钥的组合会做前置校验)。
- 各组合的上游调用逐用例串行,不触免费档限速。
- 消融报告落在 `docs/iterations/0002-t07-advanced-retrieval.{md,json}`,含与 T06 基线的差值表与默认组合依据。

## T09 生成指标与 CI 门禁

`uv run python scripts/evaluate.py` 一条命令同时产出检索指标与生成指标:评估器
在逐用例检索评估后追加一次 `/api/v1/chat`(同一问题、同一 top_k、同一过滤与
拒答阈值),对每条用例记录实际作答 / 拒答行为,并计算:

| 指标 | 口径 | 判定 |
| --- | --- | --- |
| 忠实度(Faithfulness,规则法) | 作答用例均值 | 回答分句需在所引资料里找到词面支撑;资料外的「保证 / 立即到账」类过度承诺扣分并留痕(ADR-0008) |
| 引用准确率(规则法) | 作答用例均值 | 支撑回答的引用 / 全部引用;一条引用支撑 = 与回答有 ≥ 2 个词面交集 |
| 拒答正确率(生成口径·双向) | 全部用例 | `refused == should_refuse`:应拒答被误答、可回答被误拒都算失败 |
| 拒答正确率(检索口径) | 拒答用例 | T06 口径不变:应拒答用例未出现高分命中 |

分品类生成指标(`generation_categories`)随 Markdown / JSON 报告一并输出。

### 无密钥环境(CI)可跑的指标子集

进程内模式(默认)注入确定性向量替身 + 规则版生成器,**不触网、不计费**,
完整覆盖上述全部指标;LLM-as-Judge 与真实 LLM 生成质量评估不在此子集。

⚠️ `--min-score` 是 embedder 口径绑定的:替身分数分布与生产 bge-m3 不同,
`0.35` 为生产操作点、`0.07` 为替身操作点。CI 用 `--min-score 0.07`,
换 embedder 必须重新校准并在报告注明。

### CI 门禁

```bash
uv run python scripts/evaluate.py --min-score 0.07 --check \
  --min-recall 0.75 --min-mrr 0.70 --min-ndcg 0.70 --min-hit-rate 0.75 \
  --min-faithfulness 0.90 --min-citation-accuracy 0.60 \
  --min-refusal-correctness 0.95 --min-permission-pass-rate 1.0 \
  --report ci-artifacts/evaluation.md --json-report ci-artifacts/evaluation.json
```

- `--check` 在报告产出后对阈值断言,跌破任一下限 exit 1(CI 红灯);阈值全部
  可用 `--min-*` 调整,缺省值即上表诸下限(`scripts/evaluate.py` 的
  `DEFAULT_GATE_THRESHOLDS`)。当前阈值锁定的是替身 + 全关开关的操作点;
  spec 参考阈值 Recall@3 ≥ 0.85 / MRR ≥ 0.75 对应真实上游(重排 + 查询改写,
  T07 消融 E/F 行)的组合,需密钥运行后再抬目标。
- **红灯验证(复现一次)**:把某个 `--min-*` 拉到不可能的值即可,例如
  `--min-recall 0.999` → 门禁逐项打 ✗、exit 1,报告仍然落盘可查
  (`tests/test_evaluate_cli.py::test_cli_gate_fails_below_threshold` 覆盖同一路径)。
- 生成器选择:`--qa-generator rule`(默认,零成本、逐字复现)/
  `--qa-generator llm`(真实 LLM 回答的忠实度 / 引用准确率,需密钥)。
- GitHub Actions:`.github/workflows/ci.yml` 的 `evaluation-gate` job 在
  push / PR 时运行并将报告作为 artifact 上传。

## 用例字段

| 字段 | 说明 |
| --- | --- |
| `id` | 全数据集唯一;报告与对比表用它定位 |
| `question` | 用户实际会问的问题;扩展用例鼓励用口语 / 同义说法 |
| `category` | 分品类指标维度(如 `leave` / `security` / `unknown` / `permission`) |
| `difficulty` | `easy` / `medium` / `hard`,便于解释失败分布 |
| `expected_chunks` | 预期切片列表;每条用「文档名 + 文内唯一锚点」定位 |
| `expected_answer_keywords` | 答案应该覆盖的关键词(评估报告以此展示,规则法忠实度不依赖它) |
| `forbidden_documents` | 权限边界用例的禁读文档;评估器会跑无过滤对照 |
| `filters` | 检索前元数据过滤,如 `{tenant: acme}` |
| `should_refuse` | 知识库没有答案时为 `true`;此类用例不参与 Recall / MRR / NDCG 均值 |
| `notes` | 来源或边界说明 |

## 锚点规则

- 锚点必须是预期切片 `content` 里的**唯一**子串;命中 0 个或多个都会让评估直接失败。
- 不要写 `chunk_id`:每次入库都会生成新的 uuid,写死会让数据集无法长期使用;报告同样用 `文档#index` 定位,不输出随机 uuid。
- 切分参数变化时优先保持锚点在目标切片内,而不是改用例语义。

## 拒答与权限

- **拒答用例**:`should_refuse: true` 且 `expected_chunks: []`。T06 的检索口径
  判"没有高分命中即正确拒答";T09 起 `/chat` 的实际拒答行为按双向口径判定
  (生成口径),两者并存于报告。
- **权限边界用例**:除 `expected_chunks` 外声明 `forbidden_documents`。评估器检查 top-k 是否泄漏禁读文档,并额外跑一次无过滤检索,证明该文档确实会命中——权限通过不是因为语料无关。

## 增加用例

1. 需要新语料时放进 `corpus/`,并在 YAML 的 `corpus` 清单里补上租户等元数据。
2. 在任一目标切片里选一个唯一、稳定的短语作为 `anchor`。
3. 先用 `uv run python scripts/evaluate.py --report <临时路径> --json-report <临时路径>` 跑通,确认锚点唯一、报告可追溯。
4. 阈值 / K / 切分参数变化时,在报告中写明,避免和旧基线混比。
5. 多切片用例直接在 `expected_chunks` 下列多条锚点;报告会按 Recall@K / NDCG 体现"只召回一部分"的损失。
