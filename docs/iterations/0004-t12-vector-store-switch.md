# T12 向量库后端:Chroma 切换前后指标对比

> ADR-0003 三级存储的第二层落地:`app/repositories/vector_chroma.py` 实现同一个
> `VectorStore` 契约,内存基线(`app/repositories/vector_memory.py`)保留为测试 / CI 口径。
> 元数据文件不参与检索,随机 chunk_id 不写入报告;切片定位用「文档#index + 锚点」。
> 由 `scripts/evaluate.py` 经 HTTP 主缝产出,确定性向量替身,无密钥环境(CI)可复现。

## 评估配置(切换前后完全一致)

| 配置 | 值 |
| --- | --- |
| 数据集 | `enterprise-kb-retrieval` v1(可回答 / 拒答 / 权限边界 = 24 / 2 / 1) |
| K / top_k | 3 / 3 |
| 拒答阈值 min_score | 0.07(确定性替身分数口径,曲线见 docs/iterations/0003) |
| 切分策略 / 尺寸 / 重叠 | table_aware / 500 / 50 |
| 向量来源 | `deterministic-lexical-1024d` |
| 运行模式 | `in-process` |
| 回答生成器 | `rule_based` |
| 检索配置 | 全关(T06 基线口径,前后两侧同一把尺子) |
| 前(内存)复现命令 | `uv run python scripts/evaluate.py --iteration 0004 --slug t12-vector-store-switch-memory --run-name "T12 向量库切换 · 前(内存基线)" --min-score 0.07` |
| 后(Chroma)复现命令 | `uv run python scripts/evaluate.py --iteration 0004 --slug t12-vector-store-switch-chroma --run-name "T12 向量库切换 · 后(Chroma)" --min-score 0.07 --vector-store chroma` |
| 后端切换点 | 唯一改动:`Settings.vector_store = "chroma"`(`chroma_path` 带数据目录);应用装配、检索高级开关、API 契约零改动 |

## 摘要对比

| 指标 | 前(内存基线) | 后(Chroma) | Δ |
| --- | ---: | ---: | ---: |
| Recall@3 | 0.7917 | 0.7917 | 0.0000 |
| MRR | 0.7500 | 0.7500 | 0.0000 |
| NDCG@3 | 0.7609 | 0.7609 | 0.0000 |
| HitRate@3 | 0.7917 | 0.7917 | 0.0000 |
| 拒答正确率(检索口径) | 1.0000 | 1.0000 | 0.0000 |
| 权限边界通过率 | 1.0000 | 1.0000 | 0.0000 |

生成指标(规则法)同步零漂移:忠实度 1.0000 / 引用准确率 0.6377 / 拒答正确率(双向) 0.9615。
26 条逐用例核对:全部用例的命中集合、排序与指标逐位一致;同 rank 的检索分数按报告
6 位小数四舍五入后最大差值为 0.0(底层余弦分同源于领域层 `cosine_similarity`)。

## 可解释差异(不影响指标)

1. **同分并列的取位顺序**:两条拒答 / 低分用例的尾槽出现同分并列
   (`unknown_shuttle`:it_security.md#2 vs after_sales.md#2,分数同为 0.0625;
   `product_sla`:it_security.md#1 vs finance_invoice.md#1,同为 0.055989)。
   内存基线按写入顺序,Chroma 由 HNSW 返回序决定;因并列双方分数与预期命中
   的相对关系不变,Recall / MRR / NDCG 逐位一致。
2. **float32 物化精度**:Chroma 存储为 float32,重算余弦时与内存 float64 存在
   1e-5 级差异;本次确定性替身的语料上该差异未改变任何排序(0.0@6dp)。
3. **维度不一致的写入口径**:内存后端写入维度不一致的切片向量也会静默 0 分;
   Chroma 后端写入报错(换向量模型必须整库重嵌,ADR-0003 的明示代价),
   查询侧两侧一致按 0 分降级,不进 5xx。

单测与主缝一致性验证见 `tests/test_vector_chroma.py`
(同数据同查询与内存基线同序同分、检索前过滤、整组替换、版本退位连带清理、
持久化重开)与 `tests/test_search_api.py`(HTTP 主缝同题同结果的切换前后对比)。

## 落地与验收对照

- [x] 向量库后端经配置切换,内存 / Chroma 行为一致(同题同结果,差异可解释)
- [x] 元数据过滤在 Chroma 上等价生效(`where` 原生下推,检索前过滤契约不变)
- [x] 切换前后指标对比报告落 `docs/iterations`(本文件 + 前后两次运行报告)
- [x] API 主缝测试全绿(`uv run pytest` 236 通过)
