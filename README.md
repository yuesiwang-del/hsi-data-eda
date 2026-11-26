# 恒生指数日线 · 分工 A

从腾讯财经按年采集恒生指数（`hkHSI`）日线。清洗后取 **2007-06-04 至 2026-11-26 的 4,800 个交易日**，覆盖 2008 年金融危机、2020 年疫情及 2022 年美联储加息周期。

**交给 B 的结论为情况 1。** 训练集 ADF 在 5% 水平无法拒绝对数价格的单位根假设，一阶差分得到的对数收益率显著拒绝单位根。B 可向 ARIMA 输入已算好的收益率并从 `d=0` 起步。月度季节性检验在 5% 水平未发现显著证据。平方收益率的相关性与 ARCH-LM 检验支持 B 考察 GARCH 波动模型。

## 小组怎么接

| 组员 | 文件 | 交接 |
| --- | --- | --- |
| B | [returns.csv](data/returns.csv)、[训练集检验](report/train_tests.csv) | 按 `date` 做单变量一步滚动预测，目标是 `log_return` |
| C | [features.csv](data/features.csv)、[输入列清单](data/feature_columns.csv) | 用共享特征构造 LSTM 回归窗口，预测 `target_return` |
| D | [features.csv](data/features.csv)、[交接说明](HANDOFF.md) | 按组内最终方案构造分类标签或汇总预测，用 `target_date` 对齐 B/C |

按样本终点往前约三年切出测试期。所有组员共用 **2023-11-27 至 2026-11-26 的 738 个测试目标日**。特征表训练集有 4,001 行。完整字段、时间切分和评估对齐规则见 [交接说明](HANDOFF.md)。可直接纳入小组报告的文字及四张基础图见 [EDA 章节](report/EDA.md)。

## 用 uv 复现

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。在仓库根目录执行：

```bash
uv sync --locked
uv run --locked python pipeline.py
```

默认从冻结的 [源数据快照](data/source_snapshot.csv) 重算 CSV、统计检验和图表。需验证在线采集时执行：

```bash
uv run --locked python pipeline.py --download
```

在线运行按 2005 至 2026 年逐年抓取，截止 2026-11-26，并重新抓取港交所及归档雅虎字段，会更新快照及 [请求清单](data/source_manifest.json)。接口可能修订历史数据，作业复现以提交的快照为准。

## 数据来源

- [腾讯财经恒生指数行情页](https://gu.qq.com/hkHSI)
- [已实测的 2008 年日线 JSON 接口](https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hkHSI,day,2008-01-01,2008-12-31,640,qfq)
- [恒生指数公司指数介绍](https://www.hsi.com.hk/eng/indexes/all-indexes/hsi)

## 分工 A 验收

| 分工图要求 | 交付位置 |
| --- | --- |
| 约 20 年、4,800 个交易日，日期和指数开高低收 | [采集和清洗代码](pipeline.py)、[源数据](data/source_snapshot.csv)、[清洗数据](data/hsi_clean.csv) |
| 成交量及成交额 | [港交所补充数据](data/market_supplement.csv)，覆盖全部 4,800 日，为港股主板市场口径 |
| 调整收盘价 | 清洗表的 `adjusted_close` 为归档雅虎字段，覆盖 4,084 日。其余 716 日留空并 [逐日列出](report/missing_adjusted_close.csv) |
| 休市、缺失、异常、对数收益率 | [EDA 章节](report/EDA.md)、[审计摘要](report/audit.json)、[收益率表](data/returns.csv) |
| ADF、白噪声检验、均值／方差／偏度／峰度、尖峰厚尾、波动聚集和季节性 | [检验](report/train_tests.csv)、[描述统计](report/descriptive_stats.csv)、[EDA 章节和四张基础图](report/EDA.md) |
| 趋势、滚动均值／波动率、ACF／PACF、月度和年度图及重大事件 | [图表](report/figures) |
| 滞后收益率、滚动特征、一次性时间切分和 B/C/D 交接 | [特征](data/features.csv)、[切分表](data/split_summary.csv)、[交接](HANDOFF.md) |
| 可选外部因子 | [港股主板成交量与成交额](data/optional_market_features.csv)按特征起点对齐，供 C/D 扩展。分工图另列美元指数、美债收益率为可选项 |

成交量来自[港交所主板统计档案](https://www.hkex.com.hk/Market-Data/Statistics/Consolidated-Reports/Securities-Statistics-Archive/Trading_Value_Volume_And_Number_Of_Deals?sc_lang=en)及[逐日日报](https://www.hkex.com.hk/eng/stat/smstat/dayquot/d261126e.htm)，调整收盘价来自 [CRAN quarks 保存的 Yahoo Finance 快照](https://search.r-project.org/CRAN/refmans/quarks/html/HSI.html)。抓取地址和响应校验值见 [补充数据请求清单](data/supplement_manifest.json)。
