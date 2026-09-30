# ADA 知识库与检索程序交付说明

当前受控知识库版本为 `ada2026-ch9-cf6259c5b1fb35c5`，构建于2026-09-29。范围是ADA 2026第9章药物治疗材料，包含正文及Figure 9.1–9.5、Table 9.1–9.4共9组经人工确认的完整图表。本程序接收病例、返回带来源的证据，不生成治疗方案、不调用生成API、不计算临床依从性评分。

## GitHub中有什么

本目录公开检索、构建、审核、打包程序，固定依赖清单、许可证说明、自动化测试和去除病例原文及指南转录后的[测试汇总](test_reports/20260929/README.md)。代码可供组员检查与运行测试。

完整知识库包 `ADA_KB_V1_TEAM.zip` 不在公开GitHub中。包内包含指南PDF、来源图片和转录，应向项目维护者申请受控访问，并确认使用权限。模型快照、原始审批材料、病例CSV和完整证据输出也未在本次上传。克隆仓库不等于已经获得这些文件；公开测试汇总不是完整公开数据集。

## 当前知识库规模

| 项目 | 实际数量 |
| --- | ---: |
| 来源材料 | 1份PDF，33页 |
| 保留记录 | 210：193条正文、8条扁平表格载体、9组整图表 |
| 可检索证据组 | 111：102条正文、9组整图表 |
| 检索窗口与向量 | 528，384维 |
| 整图表覆盖 | 9组，13个PDF页 |
| 图表原文块与关系说明 | 359块、157项关系 |

检索使用固定MiniLM快照、CPU和NumPy归一化向量内积。长病例按模型实际256 token上限分窗，返回完整病例及处理记录。每组图表只占一个主证据名额，命中后返回完整图表内容、条件、脚注、来源定位与审核状态，不把局部价格或剂量当作独立获批结论。

历史623条切片候选与当前整图表发布是不同表示方式。整图表批准没有把旧候选自动全部批准。旧候选文档中的“0 released visual records”描述当时的候选包，不代表当前受控九图表版本没有视觉证据。[KB_V1_README.md](KB_V1_README.md)保留历史候选流程，并由旧候选打包工具使用；最新正式包不使用 `--allow-candidate`。

## 组员运行现成知识库包

收到授权分享的完整ZIP后解压，进入 `ADA_KB_V1_TEAM`。先安装Python 3.12。首次安装依赖通常需要网络；查询加载包内模型，不下载模型，不需要Ollama或API key。

macOS：

```sh
python3.12 -m venv ../ada-kb-env
../ada-kb-env/bin/python -m pip install -r tools/requirements-v1-consumer.lock.txt
../ada-kb-env/bin/python selftest.py --output "../ADA acceptance macOS"
../ada-kb-env/bin/python tools/kb_v1.py query --bundle bundle --cases examples/synthetic_cases.csv --output ../ada-demo-evidence
```

Windows PowerShell（命令供实机验收，尚未在Windows实测）：

```powershell
py -3.12 -m venv ..\ada-kb-env
..\ada-kb-env\Scripts\python.exe -m pip install -r tools\requirements-v1-consumer.lock.txt
..\ada-kb-env\Scripts\python.exe selftest.py --output "..\ADA acceptance Windows"
..\ada-kb-env\Scripts\python.exe tools\kb_v1.py query --bundle bundle --cases examples\synthetic_cases.csv --output ..\ada-demo-evidence
```

CSV默认只需 `case_id,vignette_text`，使用UTF-8，只放病例事实，不能将生成的治疗方案用作查询。同一编号不能对应不同病例。单病例使用 `--vignette-file one_case.txt --case-id example-001`，替代 `--cases`。结构化字段模式需显式添加 `--input-mode structured`。所有输出目录必须是新目录。

结果为 `evidence.jsonl`、`evidence.md` 和 `retrieval_summary.json`。组员可在后续API工作流中使用JSONL，保留知识库版本、证据ID、来源、审核状态及限制。本次不提供自动生成或临床评分集成；相似度不是临床正确率。

## 从源码运行测试和检索

在仓库根目录进入流水线目录：

```sh
cd Data-Analyses/adaguideline/open_source_kb_pipeline
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-v1-maintainer.lock.txt
.venv/bin/python kb_v1.py doctor --maintainer
.venv/bin/python run_public_tests.py --output outputs/my_public_test_result.json
```

Windows对应使用 `py -3.12 -m venv .venv` 和 `.venv\Scripts\python.exe`。发布前复测使用既有macOS Python 3.12.4环境；这不是全新依赖安装验证。缺少受控fixtures的集成测试明确跳过，不能把跳过算作通过。也可用标准命令 `python -m unittest discover -s tests -v`。

已经收到授权知识库包时，可在源码目录指定其绝对路径：

```sh
python kb_v1.py verify --bundle "/path/to/ADA_KB_V1_TEAM/bundle"
python kb_v1.py query --bundle "/path/to/ADA_KB_V1_TEAM/bundle" --cases "/path/to/cases.csv" --input-mode text --top-k 8 --output outputs/retrieval_run_01
```

维护端重建需要完整章节文本提取、原PDF、七图表审批工作区、两张补充表格审批工作区及MiniLM实体快照；这些不是公开测试fixtures。使用新输出目录：

```sh
python kb_v1.py build --kb-dir "/path/to/full_guideline_kb" --pdf "/path/to/guideline.pdf" --model "/path/to/1110a243fdf4706b3f48f1d95db1a4f5529b4d41" --chart-workspace "/path/to/approved_seven_charts" --supplement-workspace "/path/to/approved_tables_9_1_9_4" --output outputs/rebuild_9charts/bundle
python kb_v1.py verify --bundle outputs/rebuild_9charts/bundle
python kb_v1_chart_acceptance.py --bundle outputs/rebuild_9charts/bundle --output outputs/rebuild_9charts/chart_probes
python kb_v1_chart_delivery.py package --bundle outputs/rebuild_9charts/bundle --output outputs/rebuild_9charts/ADA_KB_V1_TEAM --zip
```

`kb_v1_acceptance.py`另提供维护端原10例验收，需要受控病例表；它不是新增10例的选样器。新增10例的原始运行记录保留本地，公开统计及复现边界见测试汇总。

## 校验与已知限制

原受控ZIP的SHA-256：

`b72fa32a20e2a1926f144afa5b39b4b463196a050b24dc7b67f77523e875bef5`

知识库 `manifest.json` 的SHA-256：

`73d58d6666b6b195574db5b42336d11b91e50d1ff303989e311628194d269bfa`

本次代码发布未重建或修改该ZIP。新加入的公开测试报告工具不属于上述已冻结ZIP，因此源码目录与包内工具文件集合不完全相同。

已验证macOS本机离线检索与中文、空格路径迁移；Windows及全新环境安装仍待验收。新增10例均完成检索，但质量抽查仍发现不适用人群、特殊情境混入、相邻正文重叠及跨页条件未绑定的问题。Table 9.1、9.4在该批病例中未自然进入前8名，不能把“已经入库”理解为“每次都会检索到”。

普通正文并未全部逐条人工审批。整图表批准也不能证明每一条返回内容适用于当前病例。没有独立专家标注全集，本轮不报告Recall、Precision或临床准确率提升。下一步是建立适用证据标注集、评估人群与情境过滤、去重和跨页关联，再接入团队API工作流。授权边界见[模型与来源声明](licenses/MODEL_AND_SOURCE_NOTICE.md)。
