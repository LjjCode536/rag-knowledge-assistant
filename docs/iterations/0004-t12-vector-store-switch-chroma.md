# T12 向量库切换 · 后(Chroma):enterprise-kb-retrieval v1

> 由 `scripts/evaluate.py` 生成。检索指标只依赖切片编号与排序,不依赖生成模型;
> 默认注入确定性向量替身,可在无密钥环境(CI)复现。
> 报告用「文档#index + 锚点」定位切片;随机 chunk_id 不写入报告,跨运行 diff 只反映指标变化。

## 评估配置

| 配置 | 值 |
| --- | --- |
| 数据集 | `enterprise-kb-retrieval` v1 |
| 用例总数 | 26 |
| 可回答 / 拒答用例 | 24 / 2 |
| 权限边界用例 | 1 |
| K / top_k | 3 / 3 |
| 拒答阈值 min_score | 0.07 |
| 切分策略 / 尺寸 / 重叠 | table_aware / 500 / 50 |
| 向量来源 | `deterministic-lexical-1024d` |
| 运行模式 | `in-process` |
| 回答生成器 | `rule_based` |
| 复现命令 | `uv run python scripts/evaluate.py --iteration 0004 --slug t12-vector-store-switch-chroma --run-name T12 向量库切换 · 后(Chroma) --min-score 0.07 --vector-store chroma` |
| 检索配置 | query_rewrite=off、multi_query=off、hybrid=off、mmr=off、rerank=off、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 |

## 摘要

| 指标 | 数值 | 口径 |
| --- | --- | --- |
| Recall@3 | 0.7917 | 可回答用例均值 |
| MRR | 0.7500 | 可回答用例均值 |
| NDCG@3 | 0.7609 | 可回答用例均值 |
| HitRate@3 | 0.7917 | 可回答用例均值 |
| 拒答正确率(检索口径) | 1.0000 | 拒答用例均值 |
| 权限边界通过率 | 1.0000 | 权限用例均值 |

### 生成指标(T09,经 /chat 主缝,规则法判定)

| 指标 | 数值 | 口径 |
| --- | --- | --- |
| 忠实度(规则法) | 1.0000 | 作答用例均值(N=23) |
| 引用准确率(规则法) | 0.6377 | 作答用例均值(N=23) |
| 拒答正确率(生成口径·双向) | 0.9615 | 应拒答被正确拒答,库中有据可答不被误拒 |
| 作答率 | 0.9583 | 可回答用例中实际作答比例(其中误拒 1 条) |

忠实度判定:回答分句需在所引资料里找到词面支撑;资料外的「保证 / 立即到账」类过度承诺额外扣分。引用准确率计支撑回答的引用占比。`--qa-generator llm` 换真实 LLM 后,同类指标落同一批字段。

共 26 条用例;拒答用例不参与 Recall / MRR / NDCG 均值。
拒答正确率有两条口径:检索口径沿用 T06(应拒答未出现高分命中);生成口径见上面的生成指标段,覆盖答与拒两个方向。

## 分品类指标

| 品类 | 可回答 / 全部 | Recall@3 | MRR | NDCG@3 | HitRate@3 |
| --- | ---: | ---: | ---: | ---: | ---: |
| hr | 3 / 3 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |
| invoice | 2 / 2 | 1.0000 | 0.7500 | 0.8155 | 1.0000 |
| it | 2 / 2 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| leave | 3 / 3 | 0.6667 | 0.6667 | 0.6667 | 0.6667 |
| logistics | 1 / 1 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| permission | 1 / 1 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| product | 4 / 4 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| refund | 2 / 2 | 1.0000 | 0.7500 | 0.8155 | 1.0000 |
| reimbursement | 3 / 3 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| security | 3 / 3 | 0.3333 | 0.3333 | 0.3333 | 0.3333 |
| unknown | 0 / 2 | — | — | — | — |

`unknown` 等只有拒答用例的品类不参与检索均值,单独计入拒答正确率。

### 分品类生成指标

| 品类 | 作答 / 可回答 | 忠实度 | 引用准确率 | 拒答正确率(双向) | 错误拒答 |
| --- | ---: | ---: | ---: | ---: | ---: |
| hr | 3 / 3 | 1.0000 | 0.5556 | 1.0000 | 0 |
| invoice | 2 / 2 | 1.0000 | 0.4167 | 1.0000 | 0 |
| it | 2 / 2 | 1.0000 | 0.8333 | 1.0000 | 0 |
| leave | 3 / 3 | 1.0000 | 0.6111 | 1.0000 | 0 |
| logistics | 1 / 1 | 1.0000 | 1.0000 | 1.0000 | 0 |
| permission | 1 / 1 | 1.0000 | 0.3333 | 1.0000 | 0 |
| product | 4 / 4 | 1.0000 | 0.7083 | 1.0000 | 0 |
| refund | 2 / 2 | 1.0000 | 0.5000 | 1.0000 | 0 |
| reimbursement | 3 / 3 | 1.0000 | 0.6111 | 1.0000 | 0 |
| security | 2 / 3 | 1.0000 | 0.8333 | 0.6667 | 1 |
| unknown | 0 / 0 | — | — | 1.0000 | 0 |


## 未满分用例速览

| 用例 | 品类 | 现象 |
| --- | --- | --- |
| `refund_unshipped` | refund | 命中但排序次优(MRR=0.5000) |
| `leave_sick_report` | leave | Recall@3=0.0000 |
| `hr_resignation_notice` | hr | Recall@3=0.0000 |
| `hr_training_budget` | hr | Recall@3=0.0000 |
| `invoice_payment_terms` | invoice | 命中但排序次优(MRR=0.5000) |
| `security_password_rotation` | security | Recall@3=0.0000 |
| `security_incident_report` | security | Recall@3=0.0000 |

## 与基线对比

注:本次拒答阈值为 0.07 与基线 0.35 不同(拒答正确率按各自口径判分,Recall / MRR / NDCG 不受影响)。
基线:T06 检索指标基线(向量来源 `deterministic-lexical-1024d`)

| 指标 | 基线 | 本次 | 差值 |
| --- | ---: | ---: | ---: |
| Recall@3 | 0.7917 | 0.7917 | +0.0000 |
| MRR | 0.7500 | 0.7500 | +0.0000 |
| NDCG@3 | 0.7609 | 0.7609 | +0.0000 |
| HitRate@3 | 0.7917 | 0.7917 | +0.0000 |
| 拒答正确率 | 1.0000 | 1.0000 | +0.0000 |
| 权限边界通过率 | 1.0000 | 1.0000 | +0.0000 |
| 忠实度(生成口径) | — | 1.0000 | — |
| 引用准确率(生成口径) | — | 0.6377 | — |
| 拒答正确率(生成口径·双向) | — | 0.9615 | — |


## 逐用例结果

### `refund_unshipped` · refund · easy

- 问题:没发货可以退钱吗？
- 过滤:tenant=acme
- 预期:`after_sales.md` · 未发货退款「订单尚未发货时」 → `after_sales.md#0`
- 检索:
  1. `product_faq.md#3` · 服务等级 · score=0.151186
  2. `after_sales.md#0` · 未发货退款 · score=0.124274 ✅
  3. `employee_handbook.md#0` · 年假制度 · score=0.115278
- 指标:Recall@3=1.0000 · MRR=0.5000 · NDCG@3=0.6309 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.3333
  回答:根据知识库资料,订单未发货时,用户可以申请取消订单并退款。
  引用:`product_faq.md#3`
  引用:`after_sales.md#0`
  引用:`employee_handbook.md#0`
- 备注:教材样例 case_refund_001:口语化退款问题
- 结论:△ 部分命中

### `refund_shipped` · refund · medium

- 问题:已经发货了还能直接退款吗？
- 过滤:tenant=acme
- 预期:`after_sales.md` · 已发货退款「订单已经发货后」 → `after_sales.md#1`
- 检索:
  1. `after_sales.md#1` · 已发货退款 · score=0.193122 ✅
  2. `after_sales.md#0` · 未发货退款 · score=0.148704
  3. `it_security.md#3` · 安全事件 · score=0.103418
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.6667
  回答:根据知识库资料,订单未发货时,用户可以申请取消订单并退款。
  引用:`after_sales.md#1`
  引用:`after_sales.md#0`
  引用:`it_security.md#3`
- 备注:教材样例 case_refund_002:已发货退款规则
- 结论:✅ 完全命中

### `logistics_delay` · logistics · easy

- 问题:快递超过七天没动怎么办？
- 过滤:tenant=acme
- 预期:`after_sales.md` · 物流异常「超过七天没有更新」 → `after_sales.md#2`
- 检索:
  1. `after_sales.md#2` · 物流异常 · score=0.279508 ✅
  2. `product_faq.md#1` · 数据导出 · score=0.062017
  3. `finance_invoice.md#1` · 付款账期 · score=0.058722
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=1.0000
  回答:根据知识库资料,可以参考以下内容:物流信息超过七天没有更新时,客服应主动联系承运商核查,并向客户说明处理进度。
  引用:`after_sales.md#2`
- 备注:教材样例 case_logistics_001:物流异常
- 结论:✅ 完全命中

### `invoice_enterprise` · invoice · easy

- 问题:企业发票需要填写什么？
- 过滤:tenant=acme
- 预期:`finance_invoice.md` · 企业发票「公司名称、纳税人识别号」 → `finance_invoice.md#0`
- 检索:
  1. `finance_invoice.md#0` · 企业发票 · score=0.388889 ✅
  2. `employee_handbook.md#3` · 差旅报销 · score=0.154303
  3. `after_sales.md#2` · 物流异常 · score=0.117851
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.3333
  回答:根据知识库资料,企业发票需要填写公司名称和纳税人识别号。
  引用:`finance_invoice.md#0`
  引用:`employee_handbook.md#3`
  引用:`after_sales.md#2`
- 备注:教材样例 case_invoice_001:发票规则
- 结论:✅ 完全命中

### `leave_annual_tenure` · leave · easy

- 问题:入职多久可以申请年假？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 年假制度「入职满一年」 → `employee_handbook.md#0`
- 检索:
  1. `employee_handbook.md#0` · 年假制度 · score=0.355830 ✅
  2. `after_sales.md#0` · 未发货退款 · score=0.219199
  3. `product_faq.md#1` · 数据导出 · score=0.130744
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.3333
  回答:根据知识库资料,订单未发货时,用户可以申请取消订单并退款。
  引用:`employee_handbook.md#0`
  引用:`after_sales.md#0`
  引用:`product_faq.md#1`
- 备注:教材样例 case_leave_001:HR 年假制度
- 结论:✅ 完全命中

### `reimbursement_materials` · reimbursement · easy

- 问题:差旅报销要提交哪些材料？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 差旅报销「发票、行程单和审批记录」 → `employee_handbook.md#3`
- 检索:
  1. `employee_handbook.md#3` · 差旅报销 · score=0.341565 ✅
  2. `employee_handbook.md#4` · 培训预算 · score=0.108465
  3. `finance_invoice.md#0` · 企业发票 · score=0.105409
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.3333
  回答:根据知识库资料,企业发票需要填写公司名称和纳税人识别号。
  引用:`employee_handbook.md#3`
  引用:`employee_handbook.md#4`
  引用:`finance_invoice.md#0`
- 备注:教材样例 case_reimbursement_001:财务报销制度
- 结论:✅ 完全命中

### `reimbursement_full_process` · reimbursement · medium

- 问题:差旅报销要交哪些材料,最晚什么时候交？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 差旅报销「发票、行程单和审批记录」 → `employee_handbook.md#3`
- 预期:`employee_handbook.md` · 差旅报销「十五个工作日内」 → `employee_handbook.md#3`
- 检索:
  1. `employee_handbook.md#3` · 差旅报销 · score=0.159364 ✅
  2. `after_sales.md#2` · 物流异常 · score=0.091287
  3. `product_faq.md#1` · 数据导出 · score=0.050637
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.5000
  回答:根据知识库资料,差旅报销需要提交发票、行程单和审批记录。
  引用:`employee_handbook.md#3`
  引用:`after_sales.md#2`
- 备注:同文档多切片用例:同时覆盖材料与提交时限
- 结论:✅ 完全命中

### `unknown_shuttle` · unknown · medium

- 问题:公司班车路线是什么？
- 过滤:tenant=acme
- 预期:无(应拒答)
- 检索:
  1. `product_faq.md#2` · 单点登录 · score=0.065653
  2. `it_security.md#0` · VPN 使用 · score=0.063500
  3. `after_sales.md#2` · 物流异常 · score=0.062500
- 指标:拒答正确(最高分 0.065653 < 阈值 0.07)
- 生成:按预期拒答,原因 low_score
- 备注:教材样例 case_unknown_001:知识库不存在的问题,用于测试兜底
- 结论:✅ 正确拒答

### `leave_sick_report` · leave · easy

- 问题:身体不舒服请假要提前通知谁？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 病假申请「当天上午十点前」 → `employee_handbook.md#1`
- 检索:
  1. `it_security.md#3` · 安全事件 · score=0.099015
  2. `employee_handbook.md#3` · 差旅报销 · score=0.089087
  3. `finance_invoice.md#1` · 付款账期 · score=0.053606
- 指标:Recall@3=0.0000 · MRR=0.0000 · NDCG@3=0.0000 · HitRate@3=0.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.5000
  回答:根据知识库资料,差旅报销需要提交发票、行程单和审批记录。
  引用:`it_security.md#3`
  引用:`employee_handbook.md#3`
- 备注:病假报备时限
- 结论:❌ 未命中

### `leave_overtime_comp` · leave · medium

- 问题:调休有效期是几个月？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 加班调休「三个月内」 → `employee_handbook.md#2`
- 检索:
  1. `employee_handbook.md#2` · 加班调休 · score=0.172062 ✅
  2. `it_security.md#2` · 设备管理 · score=0.062500
  3. `finance_invoice.md#0` · 企业发票 · score=0.058926
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=1.0000
  回答:根据知识库资料,可以参考以下内容:工作日加班可按一比一比例申请调休,调休需在加班发生后的三个月内使用完毕。
  引用:`employee_handbook.md#2`
- 备注:调休有效期
- 结论:✅ 完全命中

### `hr_resignation_notice` · hr · easy

- 问题:辞职要提前几天说？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 离职交接「提前三十天」 → `employee_handbook.md#5`
- 检索:
  1. `employee_handbook.md#4` · 培训预算 · score=0.194461
  2. `finance_invoice.md#0` · 企业发票 · score=0.125988
  3. `employee_handbook.md#3` · 差旅报销 · score=0.116642
- 指标:Recall@3=0.0000 · MRR=0.0000 · NDCG@3=0.0000 · HitRate@3=0.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.3333
  回答:根据知识库资料,企业发票需要填写公司名称和纳税人识别号。
  引用:`employee_handbook.md#4`
  引用:`finance_invoice.md#0`
  引用:`employee_handbook.md#3`
- 备注:离职通知期
- 结论:❌ 未命中

### `hr_training_budget` · hr · easy

- 问题:公司给个人学习经费吗？额度多少？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 培训预算「五千元」 → `employee_handbook.md#4`
- 检索:
  1. `finance_invoice.md#0` · 企业发票 · score=0.144338
  2. `product_faq.md#3` · 服务等级 · score=0.115470
  3. `employee_handbook.md#0` · 年假制度 · score=0.088045
- 指标:Recall@3=0.0000 · MRR=0.0000 · NDCG@3=0.0000 · HitRate@3=0.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.3333
  回答:根据知识库资料,企业发票需要填写公司名称和纳税人识别号。
  引用:`finance_invoice.md#0`
  引用:`product_faq.md#3`
  引用:`employee_handbook.md#0`
- 备注:培训预算额度
- 结论:❌ 未命中

### `offboarding_cross_doc` · hr · hard

- 问题:离职要提前多久申请,账号什么时候回收？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 离职交接「提前三十天」 → `employee_handbook.md#5`
- 预期:`it_security.md` · 账号回收「两个工作日内」 → `it_security.md#4`
- 检索:
  1. `employee_handbook.md#5` · 离职交接 · score=0.179787 ✅
  2. `it_security.md#4` · 账号回收 · score=0.155700 ✅
  3. `employee_handbook.md#4` · 培训预算 · score=0.132842
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=1.0000
  回答:根据知识库资料,可以参考以下内容:离职需提前三十天提交书面申请,并在最后工作日前完成工作交接与资产归还。
  引用:`employee_handbook.md#5`
  引用:`it_security.md#4`
  引用:`employee_handbook.md#4`
- 备注:跨文档多切片用例:同时覆盖离职通知期与账号回收
- 结论:✅ 完全命中

### `reimbursement_deadline` · reimbursement · medium

- 问题:报销单最晚什么时候交？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 差旅报销「十五个工作日内」 → `employee_handbook.md#3`
- 检索:
  1. `employee_handbook.md#3` · 差旅报销 · score=0.102869 ✅
  2. `product_faq.md#1` · 数据导出 · score=0.065372
  3. `after_sales.md#2` · 物流异常 · score=0.058926
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=1.0000
  回答:根据知识库资料,差旅报销需要提交发票、行程单和审批记录。
  引用:`employee_handbook.md#3`
- 备注:报销提交时限
- 结论:✅ 完全命中

### `invoice_payment_terms` · invoice · easy

- 问题:给供应商打款要等多少天？
- 过滤:tenant=acme
- 预期:`finance_invoice.md` · 付款账期「验收合格后的三十天」 → `finance_invoice.md#1`
- 检索:
  1. `it_security.md#1` · 密码策略 · score=0.117444
  2. `finance_invoice.md#1` · 付款账期 · score=0.117444 ✅
  3. `product_faq.md#3` · 服务等级 · score=0.063246
- 指标:Recall@3=1.0000 · MRR=0.5000 · NDCG@3=0.6309 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.5000
  回答:根据知识库资料,可以参考以下内容:密码至少十二位,必须包含大小写字母、数字和特殊符号,并且每九十天更换一次。
  引用:`it_security.md#1`
  引用:`finance_invoice.md#1`
- 备注:供应商付款账期
- 结论:△ 部分命中

### `it_vpn_access` · it · easy

- 问题:在家办公怎么连公司系统？
- 过滤:tenant=acme
- 预期:`it_security.md` · VPN 使用「公司 VPN」 → `it_security.md#0`
- 检索:
  1. `it_security.md#0` · VPN 使用 · score=0.113592 ✅
  2. `it_security.md#2` · 设备管理 · score=0.111803
  3. `after_sales.md#1` · 已发货退款 · score=0.101274
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.6667
  回答:根据知识库资料,可以参考以下内容:远程办公必须通过公司 VPN 接入内网。VPN 账号与员工工号绑定,不得转借他人使用。
  引用:`it_security.md#0`
  引用:`it_security.md#2`
  引用:`after_sales.md#1`
- 备注:远程接入方式
- 结论:✅ 完全命中

### `security_password_rotation` · security · easy

- 问题:登录口令有有效期吗？
- 过滤:tenant=acme
- 预期:`it_security.md` · 密码策略「每九十天」 → `it_security.md#1`
- 检索:
  1. `product_faq.md#2` · 单点登录 · score=0.131306
  2. `employee_handbook.md#0` · 年假制度 · score=0.107833
  3. `it_security.md#2` · 设备管理 · score=0.062500
- 指标:Recall@3=0.0000 · MRR=0.0000 · NDCG@3=0.0000 · HitRate@3=0.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=1.0000
  回答:根据知识库资料,员工入职满一年后,可以按照公司制度申请年假。
  引用:`product_faq.md#2`
  引用:`employee_handbook.md#0`
- 备注:密码轮换周期
- 结论:❌ 未命中

### `security_device_software` · security · medium

- 问题:公司电脑能自己装软件吗？
- 过滤:tenant=acme
- 预期:`it_security.md` · 设备管理「不得安装未经审批的软件」 → `it_security.md#2`
- 检索:
  1. `it_security.md#2` · 设备管理 · score=0.167705 ✅
  2. `product_faq.md#2` · 单点登录 · score=0.117444
  3. `employee_handbook.md#0` · 年假制度 · score=0.096449
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.6667
  回答:根据知识库资料,员工入职满一年后,可以按照公司制度申请年假。
  引用:`it_security.md#2`
  引用:`product_faq.md#2`
  引用:`employee_handbook.md#0`
- 备注:终端软件管理
- 结论:✅ 完全命中

### `security_incident_report` · security · medium

- 问题:怀疑邮箱被盗了第一步做什么？
- 过滤:tenant=acme
- 预期:`it_security.md` · 安全事件「立即断开网络」 → `it_security.md#3`
- 检索:
  1. `it_security.md#0` · VPN 使用 · score=0.051848
  2. `after_sales.md#2` · 物流异常 · score=0.051031
  3. `after_sales.md#0` · 未发货退款 · score=0.047458
- 指标:Recall@3=0.0000 · MRR=0.0000 · NDCG@3=0.0000 · HitRate@3=0.0000
- 生成:错误拒答(应回答),原因 low_score
- 备注:安全事件响应
- 结论:❌ 未命中

### `it_account_recovery` · it · medium

- 问题:人走了系统权限什么时候停？
- 过滤:tenant=acme
- 预期:`it_security.md` · 账号回收「两个工作日内」 → `it_security.md#4`
- 检索:
  1. `it_security.md#4` · 账号回收 · score=0.090909 ✅
  2. `after_sales.md#2` · 物流异常 · score=0.053300
  3. `employee_handbook.md#4` · 培训预算 · score=0.051709
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=1.0000
  回答:根据知识库资料,可以参考以下内容:员工离职或转岗时,部门负责人需在系统中提交账号权限回收申请,由信息技术部在两个工作日内处理。
  引用:`it_security.md#4`
- 备注:离职账号回收
- 结论:✅ 完全命中

### `product_trial` · product · easy

- 问题:企业版能免费用多久？
- 过滤:tenant=acme
- 预期:`product_faq.md` · 试用期「三十天免费试用」 → `product_faq.md#0`
- 检索:
  1. `product_faq.md#0` · 试用期 · score=0.262613 ✅
  2. `product_faq.md#2` · 单点登录 · score=0.131306
  3. `employee_handbook.md#2` · 加班调休 · score=0.114708
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.6667
  回答:根据知识库资料,可以参考以下内容:企业版提供三十天免费试用,试用期内可以随时升级为付费版本。
  引用:`product_faq.md#0`
  引用:`product_faq.md#2`
  引用:`employee_handbook.md#2`
- 备注:企业版试用期
- 结论:✅ 完全命中

### `product_data_export` · product · easy

- 问题:怎么把知识库里的东西备份下来？
- 过滤:tenant=acme
- 预期:`product_faq.md` · 数据导出「控制台导出」 → `product_faq.md#1`
- 检索:
  1. `product_faq.md#1` · 数据导出 · score=0.108786 ✅
  2. `product_faq.md#2` · 单点登录 · score=0.051503
  3. `it_security.md#4` · 账号回收 · score=0.041812
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=1.0000
  回答:根据知识库资料,可以参考以下内容:管理员可以在控制台导出全部知识库数据,导出文件保留七天。
  引用:`product_faq.md#1`
- 备注:数据导出方式
- 结论:✅ 完全命中

### `product_sso` · product · easy

- 问题:能用公司账号登录系统吗？
- 过滤:tenant=acme
- 预期:`product_faq.md` · 单点登录「SAML 2.0」 → `product_faq.md#2`
- 检索:
  1. `product_faq.md#2` · 单点登录 · score=0.411054 ✅
  2. `it_security.md#0` · VPN 使用 · score=0.113592
  3. `employee_handbook.md#4` · 培训预算 · score=0.108465
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.6667
  回答:根据知识库资料,可以参考以下内容:企业版支持 SAML 2.0 单点登录,配置完成后员工可使用公司账号登录。
  引用:`product_faq.md#2`
  引用:`it_security.md#0`
  引用:`employee_handbook.md#4`
- 备注:SSO 支持
- 结论:✅ 完全命中

### `product_sla` · product · easy

- 问题:标准版会不会经常宕机？
- 过滤:tenant=acme
- 预期:`product_faq.md` · 服务等级「百分之九十九点九」 → `product_faq.md#3`
- 检索:
  1. `product_faq.md#3` · 服务等级 · score=0.180907 ✅
  2. `after_sales.md#0` · 未发货退款 · score=0.099136
  3. `finance_invoice.md#1` · 付款账期 · score=0.055989
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.5000
  回答:根据知识库资料,订单未发货时,用户可以申请取消订单并退款。
  引用:`product_faq.md#3`
  引用:`after_sales.md#0`
- 备注:服务等级
- 结论:✅ 完全命中

### `permission_tenant_isolation` · permission · hard

- 问题:入职多久可以申请年假,满半年还是满一年？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 年假制度「入职满一年」 → `employee_handbook.md#0`
- 检索:
  1. `employee_handbook.md#0` · 年假制度 · score=0.419371 ✅
  2. `after_sales.md#0` · 未发货退款 · score=0.164399
  3. `product_faq.md#1` · 数据导出 · score=0.098058
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 权限边界:通过;无过滤对照中禁读文档最高排名 2
- 生成:作答 · 忠实度=1.0000 · 引用准确率=0.3333
  回答:根据知识库资料,订单未发货时,用户可以申请取消订单并退款。
  引用:`employee_handbook.md#0`
  引用:`after_sales.md#0`
  引用:`product_faq.md#1`
- 备注:globex 手册有同主题的相反规定,租户过滤必须挡住;无过滤对照应能检索到禁读文档
- 结论:✅ 完全命中

### `unknown_gym` · unknown · medium

- 问题:公司健身房开放到几点？
- 过滤:tenant=acme
- 预期:无(应拒答)
- 检索:
  1. `product_faq.md#3` · 服务等级 · score=0.066667
  2. `product_faq.md#2` · 单点登录 · score=0.061898
  3. `it_security.md#0` · VPN 使用 · score=0.059868
- 指标:拒答正确(最高分 0.066667 < 阈值 0.07)
- 生成:按预期拒答,原因 low_score
- 备注:知识库不存在的问题,验证低分兜底
- 结论:✅ 正确拒答

## 与后续迭代对比的约定

- 对比时必须固定数据集版本、`K`、切分参数与向量后端;任何一项变化都要在报告中注明。
- 报告同时产出 Markdown(人读)与 JSON(CI / 门禁);指标字段名保持不变。
- 后续迭代在 `docs/iterations/` 递增编号,并附「基线 vs 本次」差值表。
- 无密钥环境默认使用 `deterministic-lexical-1024d`;真实上游模式需写明 embedder,不与替身基线混比。
