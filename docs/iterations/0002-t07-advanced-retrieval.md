# T07 高级检索:enterprise-kb-retrieval v1

> 由 `scripts/evaluate.py --ablate` 生成;消融组合在同一数据集、同一切分与同一向量来源下逐个重跑,只差检索开关。
> 各组合的检索服务单线程串行调用上游,不触免费档限速;重排组合需密钥。
> A/B 行为确定性替身时序,可逐字复现;含真实上游的组合(重排 / 改写 / 多查询)分数会带模型服务端轻微抖动,±0.01 内的指标差异视为噪声。
> 报告用「文档#index + 锚点」定位切片;随机 chunk_id 不写入报告。

## 评估配置

| 配置 | 值 |
| --- | --- |
| 数据集 | `enterprise-kb-retrieval` v1 |
| 用例总数 | 26 |
| K / 拒答阈值 | 3 / 0.35 |
| 向量来源 | `deterministic-lexical-1024d` |
| 运行模式 | `in-process`(各组合独立进程内重建) |
| 上游并发 | `serial`(逐用例串行) |
| 复现命令 | `uv run python scripts/evaluate.py --iteration 0002 --slug t07-advanced-retrieval --run-name T07 高级检索 --ablate` |

## 消融对比

| 组合 | 检索配置 | Recall@3 | MRR | NDCG@3 | HitRate@3 | ΔRecall vs 基线 | ΔMRR vs 基线 | 拒答正确率 | 权限通过率 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A 全关(基线复现) | query_rewrite=off、multi_query=off、hybrid=off、mmr=off、rerank=off、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 | 0.7917 | 0.7500 | 0.7609 | 0.7917 | +0.0000 | +0.0000 | 1.0000 | 1.0000 |
| B 混合开 | query_rewrite=off、multi_query=off、hybrid=on、mmr=off、rerank=off、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 | 0.8333 | 0.8056 | 0.8125 | 0.8333 | +0.0417 | +0.0556 | 0.0000 | 1.0000 |
| C 重排开(混合关) | query_rewrite=off、multi_query=off、hybrid=off、mmr=off、rerank=on、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 | 1.0000 | 0.9722 | 0.9792 | 1.0000 | +0.2083 | +0.2222 | 1.0000 | 1.0000 |
| D 混合+重排开 | query_rewrite=off、multi_query=off、hybrid=on、mmr=off、rerank=on、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 | 1.0000 | 0.9792 | 0.9846 | 1.0000 | +0.2083 | +0.2292 | 1.0000 | 1.0000 |
| E 混合+重排+MMR | query_rewrite=off、multi_query=off、hybrid=on、mmr=on、rerank=on、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 | 0.9583 | 0.9583 | 0.9583 | 0.9583 | +0.1667 | +0.2083 | 1.0000 | 1.0000 |
| F 混合+重排+查询改写 | query_rewrite=on、multi_query=off、hybrid=on、mmr=off、rerank=on、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | +0.2083 | +0.2500 | 1.0000 | 1.0000 |
| G 混合+重排+多查询扩展 | query_rewrite=off、multi_query=on、hybrid=on、mmr=off、rerank=on、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | +0.2083 | +0.2500 | 1.0000 | 1.0000 |
| H 重排+查询改写(混合关) | query_rewrite=on、multi_query=off、hybrid=off、mmr=off、rerank=on、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | +0.2083 | +0.2500 | 1.0000 | 1.0000 |

**默认组合依据**:「H 重排+查询改写(混合关)」(k=3)在本数据集上Recall=1.0000 / MRR=1.0000;并列取最优时按上游开销较小的开关组合取舍,对应 query_rewrite=on、multi_query=off、hybrid=off、mmr=off、rerank=on、w_v=0.7、w_k=0.3、candidate_k=30、nq=3、lambda=0.7 为当前默认值。

## 分品类影响(全关 → H 重排+查询改写(混合关))

| 品类 | Recall@3(全关) | Recall@3(主推) | ΔMRR |
| --- | ---: | ---: | ---: |
| hr | 0.3333 | 1.0000 | +0.6667 |
| invoice | 1.0000 | 1.0000 | +0.2500 |
| it | 1.0000 | 1.0000 | +0.0000 |
| leave | 0.6667 | 1.0000 | +0.3333 |
| logistics | 1.0000 | 1.0000 | +0.0000 |
| permission | 1.0000 | 1.0000 | +0.0000 |
| product | 1.0000 | 1.0000 | +0.0000 |
| refund | 1.0000 | 1.0000 | +0.2500 |
| reimbursement | 1.0000 | 1.0000 | +0.0000 |
| security | 0.3333 | 1.0000 | +0.6667 |


## 逐用例结果(主推组合:H 重排+查询改写(混合关))

### `refund_unshipped` · refund · easy

- 问题:没发货可以退钱吗？
- 过滤:tenant=acme
- 预期:`after_sales.md` · 未发货退款「订单尚未发货时」 → `after_sales.md#0`
- 检索:
  1. `after_sales.md#0` · 未发货退款 · score=0.996609 ✅
  2. `after_sales.md#1` · 已发货退款 · score=0.932571
  3. `after_sales.md#2` · 物流异常 · score=0.010328
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:教材样例 case_refund_001:口语化退款问题
- 结论:✅ 完全命中

### `refund_shipped` · refund · medium

- 问题:已经发货了还能直接退款吗？
- 过滤:tenant=acme
- 预期:`after_sales.md` · 已发货退款「订单已经发货后」 → `after_sales.md#1`
- 检索:
  1. `after_sales.md#1` · 已发货退款 · score=0.988717 ✅
  2. `after_sales.md#0` · 未发货退款 · score=0.835518
  3. `employee_handbook.md#3` · 差旅报销 · score=0.012831
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:教材样例 case_refund_002:已发货退款规则
- 结论:✅ 完全命中

### `logistics_delay` · logistics · easy

- 问题:快递超过七天没动怎么办？
- 过滤:tenant=acme
- 预期:`after_sales.md` · 物流异常「超过七天没有更新」 → `after_sales.md#2`
- 检索:
  1. `after_sales.md#2` · 物流异常 · score=0.985632 ✅
  2. `after_sales.md#0` · 未发货退款 · score=0.024777
  3. `after_sales.md#1` · 已发货退款 · score=0.007006
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:教材样例 case_logistics_001:物流异常
- 结论:✅ 完全命中

### `invoice_enterprise` · invoice · easy

- 问题:企业发票需要填写什么？
- 过滤:tenant=acme
- 预期:`finance_invoice.md` · 企业发票「公司名称、纳税人识别号」 → `finance_invoice.md#0`
- 检索:
  1. `finance_invoice.md#0` · 企业发票 · score=0.994320 ✅
  2. `it_security.md#2` · 设备管理 · score=0.079605
  3. `it_security.md#1` · 密码策略 · score=0.005334
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:教材样例 case_invoice_001:发票规则
- 结论:✅ 完全命中

### `leave_annual_tenure` · leave · easy

- 问题:入职多久可以申请年假？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 年假制度「入职满一年」 → `employee_handbook.md#0`
- 检索:
  1. `employee_handbook.md#0` · 年假制度 · score=0.905530 ✅
  2. `employee_handbook.md#2` · 加班调休 · score=0.009679
  3. `employee_handbook.md#1` · 病假申请 · score=0.002021
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:教材样例 case_leave_001:HR 年假制度
- 结论:✅ 完全命中

### `reimbursement_materials` · reimbursement · easy

- 问题:差旅报销要提交哪些材料？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 差旅报销「发票、行程单和审批记录」 → `employee_handbook.md#3`
- 检索:
  1. `employee_handbook.md#3` · 差旅报销 · score=0.993955 ✅
  2. `finance_invoice.md#0` · 企业发票 · score=0.048675
  3. `employee_handbook.md#1` · 病假申请 · score=0.016059
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:教材样例 case_reimbursement_001:财务报销制度
- 结论:✅ 完全命中

### `reimbursement_full_process` · reimbursement · medium

- 问题:差旅报销要交哪些材料,最晚什么时候交？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 差旅报销「发票、行程单和审批记录」 → `employee_handbook.md#3`
- 预期:`employee_handbook.md` · 差旅报销「十五个工作日内」 → `employee_handbook.md#3`
- 检索:
  1. `employee_handbook.md#3` · 差旅报销 · score=0.992547 ✅
  2. `finance_invoice.md#0` · 企业发票 · score=0.032416
  3. `employee_handbook.md#1` · 病假申请 · score=0.023536
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:同文档多切片用例:同时覆盖材料与提交时限
- 结论:✅ 完全命中

### `unknown_shuttle` · unknown · medium

- 问题:公司班车路线是什么？
- 过滤:tenant=acme
- 预期:无(应拒答)
- 检索:
  1. `employee_handbook.md#0` · 年假制度 · score=0.000105
  2. `product_faq.md#2` · 单点登录 · score=0.000097
  3. `product_faq.md#0` · 试用期 · score=0.000069
- 指标:拒答正确(最高分 0.000105 < 阈值 0.35)
- 备注:教材样例 case_unknown_001:知识库不存在的问题,用于测试兜底
- 结论:✅ 正确拒答

### `leave_sick_report` · leave · easy

- 问题:身体不舒服请假要提前通知谁？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 病假申请「当天上午十点前」 → `employee_handbook.md#1`
- 检索:
  1. `employee_handbook.md#1` · 病假申请 · score=0.279752 ✅
  2. `employee_handbook.md#5` · 离职交接 · score=0.009918
  3. `employee_handbook.md#0` · 年假制度 · score=0.003767
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:病假报备时限
- 结论:✅ 完全命中

### `leave_overtime_comp` · leave · medium

- 问题:调休有效期是几个月？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 加班调休「三个月内」 → `employee_handbook.md#2`
- 检索:
  1. `employee_handbook.md#2` · 加班调休 · score=0.658083 ✅
  2. `employee_handbook.md#0` · 年假制度 · score=0.048657
  3. `employee_handbook.md#1` · 病假申请 · score=0.017104
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:调休有效期
- 结论:✅ 完全命中

### `hr_resignation_notice` · hr · easy

- 问题:辞职要提前几天说？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 离职交接「提前三十天」 → `employee_handbook.md#5`
- 检索:
  1. `employee_handbook.md#5` · 离职交接 · score=0.897369 ✅
  2. `it_security.md#4` · 账号回收 · score=0.026381
  3. `employee_handbook.md#0` · 年假制度 · score=0.002889
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:离职通知期
- 结论:✅ 完全命中

### `hr_training_budget` · hr · easy

- 问题:公司给个人学习经费吗？额度多少？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 培训预算「五千元」 → `employee_handbook.md#4`
- 检索:
  1. `employee_handbook.md#4` · 培训预算 · score=0.043049 ✅
  2. `product_faq.md#0` · 试用期 · score=0.001055
  3. `employee_handbook.md#0` · 年假制度 · score=0.000223
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:培训预算额度
- 结论:✅ 完全命中

### `offboarding_cross_doc` · hr · hard

- 问题:离职要提前多久申请,账号什么时候回收？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 离职交接「提前三十天」 → `employee_handbook.md#5`
- 预期:`it_security.md` · 账号回收「两个工作日内」 → `it_security.md#4`
- 检索:
  1. `it_security.md#4` · 账号回收 · score=0.995071 ✅
  2. `employee_handbook.md#5` · 离职交接 · score=0.986098 ✅
  3. `employee_handbook.md#0` · 年假制度 · score=0.025522
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:跨文档多切片用例:同时覆盖离职通知期与账号回收
- 结论:✅ 完全命中

### `reimbursement_deadline` · reimbursement · medium

- 问题:报销单最晚什么时候交？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 差旅报销「十五个工作日内」 → `employee_handbook.md#3`
- 检索:
  1. `employee_handbook.md#3` · 差旅报销 · score=0.609892 ✅
  2. `employee_handbook.md#5` · 离职交接 · score=0.052087
  3. `finance_invoice.md#1` · 付款账期 · score=0.015621
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:报销提交时限
- 结论:✅ 完全命中

### `invoice_payment_terms` · invoice · easy

- 问题:给供应商打款要等多少天？
- 过滤:tenant=acme
- 预期:`finance_invoice.md` · 付款账期「验收合格后的三十天」 → `finance_invoice.md#1`
- 检索:
  1. `finance_invoice.md#1` · 付款账期 · score=0.911982 ✅
  2. `after_sales.md#2` · 物流异常 · score=0.002646
  3. `employee_handbook.md#5` · 离职交接 · score=0.002198
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:供应商付款账期
- 结论:✅ 完全命中

### `it_vpn_access` · it · easy

- 问题:在家办公怎么连公司系统？
- 过滤:tenant=acme
- 预期:`it_security.md` · VPN 使用「公司 VPN」 → `it_security.md#0`
- 检索:
  1. `it_security.md#0` · VPN 使用 · score=0.739370 ✅
  2. `product_faq.md#2` · 单点登录 · score=0.008920
  3. `it_security.md#2` · 设备管理 · score=0.007405
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:远程接入方式
- 结论:✅ 完全命中

### `security_password_rotation` · security · easy

- 问题:登录口令有有效期吗？
- 过滤:tenant=acme
- 预期:`it_security.md` · 密码策略「每九十天」 → `it_security.md#1`
- 检索:
  1. `it_security.md#1` · 密码策略 · score=0.062277 ✅
  2. `it_security.md#4` · 账号回收 · score=0.012484
  3. `product_faq.md#1` · 数据导出 · score=0.009039
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:密码轮换周期
- 结论:✅ 完全命中

### `security_device_software` · security · medium

- 问题:公司电脑能自己装软件吗？
- 过滤:tenant=acme
- 预期:`it_security.md` · 设备管理「不得安装未经审批的软件」 → `it_security.md#2`
- 检索:
  1. `it_security.md#2` · 设备管理 · score=0.903690 ✅
  2. `product_faq.md#2` · 单点登录 · score=0.100909
  3. `employee_handbook.md#4` · 培训预算 · score=0.020867
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:终端软件管理
- 结论:✅ 完全命中

### `security_incident_report` · security · medium

- 问题:怀疑邮箱被盗了第一步做什么？
- 过滤:tenant=acme
- 预期:`it_security.md` · 安全事件「立即断开网络」 → `it_security.md#3`
- 检索:
  1. `it_security.md#3` · 安全事件 · score=0.498970 ✅
  2. `it_security.md#4` · 账号回收 · score=0.101133
  3. `it_security.md#2` · 设备管理 · score=0.052955
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:安全事件响应
- 结论:✅ 完全命中

### `it_account_recovery` · it · medium

- 问题:人走了系统权限什么时候停？
- 过滤:tenant=acme
- 预期:`it_security.md` · 账号回收「两个工作日内」 → `it_security.md#4`
- 检索:
  1. `it_security.md#4` · 账号回收 · score=0.934971 ✅
  2. `employee_handbook.md#5` · 离职交接 · score=0.058603
  3. `it_security.md#2` · 设备管理 · score=0.011558
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:离职账号回收
- 结论:✅ 完全命中

### `product_trial` · product · easy

- 问题:企业版能免费用多久？
- 过滤:tenant=acme
- 预期:`product_faq.md` · 试用期「三十天免费试用」 → `product_faq.md#0`
- 检索:
  1. `product_faq.md#0` · 试用期 · score=0.980812 ✅
  2. `product_faq.md#2` · 单点登录 · score=0.024002
  3. `product_faq.md#3` · 服务等级 · score=0.003366
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:企业版试用期
- 结论:✅ 完全命中

### `product_data_export` · product · easy

- 问题:怎么把知识库里的东西备份下来？
- 过滤:tenant=acme
- 预期:`product_faq.md` · 数据导出「控制台导出」 → `product_faq.md#1`
- 检索:
  1. `product_faq.md#1` · 数据导出 · score=0.451818 ✅
  2. `product_faq.md#2` · 单点登录 · score=0.009543
  3. `it_security.md#2` · 设备管理 · score=0.004175
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:数据导出方式
- 结论:✅ 完全命中

### `product_sso` · product · easy

- 问题:能用公司账号登录系统吗？
- 过滤:tenant=acme
- 预期:`product_faq.md` · 单点登录「SAML 2.0」 → `product_faq.md#2`
- 检索:
  1. `product_faq.md#2` · 单点登录 · score=0.919754 ✅
  2. `it_security.md#0` · VPN 使用 · score=0.204145
  3. `it_security.md#4` · 账号回收 · score=0.055077
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:SSO 支持
- 结论:✅ 完全命中

### `product_sla` · product · easy

- 问题:标准版会不会经常宕机？
- 过滤:tenant=acme
- 预期:`product_faq.md` · 服务等级「百分之九十九点九」 → `product_faq.md#3`
- 检索:
  1. `product_faq.md#3` · 服务等级 · score=0.488451 ✅
  2. `it_security.md#2` · 设备管理 · score=0.003328
  3. `product_faq.md#0` · 试用期 · score=0.003309
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 备注:服务等级
- 结论:✅ 完全命中

### `permission_tenant_isolation` · permission · hard

- 问题:入职多久可以申请年假,满半年还是满一年？
- 过滤:tenant=acme
- 预期:`employee_handbook.md` · 年假制度「入职满一年」 → `employee_handbook.md#0`
- 检索:
  1. `employee_handbook.md#0` · 年假制度 · score=0.982833 ✅
  2. `employee_handbook.md#2` · 加班调休 · score=0.002559
  3. `employee_handbook.md#5` · 离职交接 · score=0.001470
- 指标:Recall@3=1.0000 · MRR=1.0000 · NDCG@3=1.0000 · HitRate@3=1.0000
- 权限边界:通过;无过滤对照中禁读文档最高排名 1
- 备注:globex 手册有同主题的相反规定,租户过滤必须挡住;无过滤对照应能检索到禁读文档
- 结论:✅ 完全命中

### `unknown_gym` · unknown · medium

- 问题:公司健身房开放到几点？
- 过滤:tenant=acme
- 预期:无(应拒答)
- 检索:
  1. `employee_handbook.md#0` · 年假制度 · score=0.000364
  2. `product_faq.md#0` · 试用期 · score=0.000205
  3. `product_faq.md#2` · 单点登录 · score=0.000200
- 指标:拒答正确(最高分 0.000364 < 阈值 0.35)
- 备注:知识库不存在的问题,验证低分兜底
- 结论:✅ 正确拒答

## 与后续迭代对比的约定

- 后续消融固定本矩阵与 K / 切分 / 向量来源;新增开关先加行再替换默认。
- 重排开组合的基线口径注明重排模型与 rerank 阈值;与替身基线不混比。
- 拒答正确率仍按检索分数与 `qa_min_score` 判定;重排分会改变分数分布,
  若拒答正确率下降需要在默认值与阈值上单独论证。
