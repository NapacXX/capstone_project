# ADA 知识库 v1 — 组员使用与审核说明

> 最新交付（2026-09-29）：9组已批准图表已接入正式受控研究包。请先阅读[最终知识库交付说明](KB_V1_FINAL_DELIVERY.md)。下文保留逐条审核候选阶段的历史说明，其中0视觉、待逐条审核及`--allow-candidate`不适用于最新正式包。Windows仍待实机验收。

## 历史候选包说明（2026-09-28）

本轮状态：**审核候选版，不是正式 v1**。623 条视觉候选已完成 AI 辅助初审，但仍待团队最终处置和图表级确认。检索候选版只用于工程验收，不含未批准视觉结论；这不是以纯文本替代正式 v1 的交付标准。

本程序只返回来源证据，不生成治疗建议、不调用生成 API、不改组员提示词、不进行临床依从性评分。相似度不是正确率或临床适用性评分。

## 1. 打开哪个文件？

本地交付文件夹中：

- `START_HERE.md`：本说明。
- `SOURCE_NAVIGATION.md`：10 页、623 条候选的导航；点开 HTML 页可并排查原始内容、AI 意见及原图链接。
- `review/review_ledger.csv`：团队逐条审核副本。原团队审批表未改变。
- `review/group_reviews.json`：关系确认，共 10 个页级组；Table9.2还需要跨页核对。这不是10个独立图表，也不是模型自动证明的完整性。
- `ai_notes/`：逐条初审及各图表覆盖/遗漏报告。修正建议尚未应用。
- `bundle/`：明确标记 `review_candidate` 的离线工程检索包。
- `tools/`：独立检索入口、维护工具、固定依赖及许可证。
- `review_sources/`：60 个切片和 10 个整页图。
- `source_path_map.json`：历史绝对路径与包内相对路径的独立映射。不要直接改历史审计路径，它们参与旧审批指纹。
- `delivery_manifest.json`：交付文件清单与 SHA-256；它是完整性清单，不是经过身份认证的电子签名。
- `KB_V1_IMPLEMENTATION_STATUS.md`、`KB_V1_QUALITY_REVIEW.md`：实际完成状态与10病例质量抽查；`test_results/`为机器验收汇总。

历史审核Markdown中的原JSON裸文件名和绝对路径保留用于审计，不是跨平台浏览入口；请使用外层HTML导航中的原图、切片及冻结JSON链接。

包含受版权约束的 PDF/提取内容，只能在已获得相应权限的项目范围内受控共享。制作本地包不代表已经获得再分发许可；不要传到公开 GitHub 或公共网盘。本轮未上传。

## 2. Windows 与 macOS 安装

目标为 **Python 3.12、CPU**。首次安装依赖需要网络；正常查询从包内加载模型，不下载模型、不需要 API key，也不需要 Ollama。当前已在 macOS arm64 实测；Windows 步骤提供给组员验收，**未声称已在 Windows 运行成功**。

先解压完整交付包，进入其中含 `bundle` 与 `tools` 的文件夹。不要只复制 `embeddings.npy` 或模型缓存的符号链接。

macOS：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r tools/requirements-v1-consumer.lock.txt
.venv/bin/python tools/kb_v1.py doctor
.venv/bin/python tools/kb_v1.py verify --bundle bundle --allow-candidate
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r tools\requirements-v1-consumer.lock.txt
.\.venv\Scripts\python.exe tools\kb_v1.py doctor
.\.venv\Scripts\python.exe tools\kb_v1.py verify --bundle bundle --allow-candidate
```

无需激活环境，因此不需要放宽 PowerShell 脚本执行策略。安装失败时先保存错误和 `doctor` 输出，不要随意换一组依赖再把结果说成同一版本。固定依赖是本次 macOS 实测组合，并非多平台安装结果的保证。

`--allow-candidate` 是当前工程候选包的显式许可。正式发布包不应依赖此参数。默认拒绝候选包是预期行为，不是程序故障。

## 3. 单病例与批量检索

默认 CSV 使用 UTF-8 编码，至少有以下两列；不要把生成的治疗方案放进 `vignette_text`：

```csv
case_id,vignette_text
example-001,"Synthetic case: adult with type 2 diabetes, HbA1c 8.2%, eGFR 45 mL/min/1.73m2, currently taking metformin."
```

macOS 示例（Windows 将 `.venv/bin/python` 换成 `.\.venv\Scripts\python.exe`）：

```bash
.venv/bin/python tools/kb_v1.py query --bundle bundle --cases cases.csv --output evidence_run_01 --allow-candidate
```

单病例也可放在 UTF-8 文本文件中，保留全部病例内容：

```bash
.venv/bin/python tools/kb_v1.py query --bundle bundle --vignette-file one_case.txt --case-id my-case-001 --output evidence_one --allow-candidate
```

输出目录必须不存在，防止覆盖历史结果。每次可更换目录名。输出：

- `evidence.jsonl`：每行一个病例，包含完整病例输入、实际分窗、KB 版本、默认 8 组去重证据、来源和状态；相关依赖证据随主证据返回。
- `evidence.md`：可读版本及 PDF 页码链接。
- `retrieval_summary.json`：实际病例、窗口和证据数量。

程序拒绝空病例、缺少必需字段、重名字段，以及同一 `case_id` 对应不同病例内容。相同病例重复行只保留一次。过长病例按**实际模型 256 token 上限**分窗，每个证据取各查询窗口的最高相似度；不静默丢弃病例尾部。原始病例仍完整保留。

已有结构化病例可明确加 `--input-mode structured`。支持的字段见 `kb_v1_runtime.py` 的 `STRUCTURED_FIELDS`，包括 HbA1c、eGFR、UACR、BMI、CKD/HF/ASCVD 相关状态、MASH/MASLD、基线降糖药及偏好。未提供的字段不补成默认病情。至少一个可识别临床字段必须非空；`treatment_plan`、`prompt`、`rationale`、`model` 不参与查询。

仓库旧病例 CSV 有两个空列名，直接输入会因重复列名被拒绝。这是本轮实际发现的输入质量问题。维护端验收脚本会**明确投影并记录**所需病例字段到新的 CSV，不修改旧表，不读取治疗方案来构建查询。组员应按上面简洁的两列规范交病例。

## 4. 如何做人工审核？

### 逐条审核

先看 `SOURCE_NAVIGATION.md`，按页打开 HTML，再看源 PDF/切片。每条至少确认：

1. 文字、药物/类别、数值、单位及适用人群是否一致。
2. 条件、否定、AND/OR、频率、增减量是否被保留。
3. 箭头是否真的存在、方向和两端是否正确；表格行列不是默认的流程图节点。
4. 表格的完整行名、列名、单位、续页、图注/脚注是否齐全。
5. `+`、`$`、`†`、`‡` 等符号的图内角色，不把成本或相对优势当作推荐强度。
6. 是否与其他切片重复；若合并，明确保留目标，不删除原候选的历史对应关系。

AI 的 `reviewed_with_findings` 不等于该条必然错误：它也包含重复、依赖、上下文或定位待确认。AI 的 `reviewed_no_change` 也不是人工批准。临床语义分歧应交由具备相应专业能力的成员裁决。

最终处置可以是 `approved`、`rejected`、`merged`；修正后批准仍用 `approved`，但必须先获得修正后的新指纹。拒绝也要留下理由、审核人和时间。不能删掉待审行来制造“全部完成”。

### 修正与重新批准（维护端，在原仓库流水线目录）

AI 建议仅写在 `suggested_corrections`，不会自动改源证据。先复制 `review/validation/working_approval_template.csv` 到一个新的工作路径，由团队在该副本里修改允许的 `corrections_json`。**禁止给现有团队原表传入 Step 07 的 `--approval-csv`：该参数同时是输入和输出。**

```bash
python 07_validate_visual_logic_outputs.py --raw-dir outputs/visual_logic_raw_review_20260823_completed --approval-csv team_work/approvals.csv --output-dir team_work/validation_pass1 --symbol-registry guideline_symbol_registry.csv
```

有修正时，第一次重新验证会产生新的待审指纹。团队必须对照修正后的内容确认，再填写副本的批准字段；随后用新的输出目录重跑 Step 07。不要复用旧批准指纹。保留两次验证记录。

然后创建新的 v1 审核版本（输出不能已存在）：

```bash
python kb_v1.py review --raw-dir outputs/visual_logic_raw_review_20260823_completed --approvals team_work/approvals.csv --registry guideline_symbol_registry.csv --ai-notes outputs/kb_v1_work/20260928/ai_notes/all_623_ai_notes.jsonl --output team_work/review_after_corrections
```

AI 初审意见是针对原候选的，修正后仍需人工重新确认，不应把旧 AI 意见当作修正内容的再次核验。若需要新增结构/关系，而非现有允许字段的小修正，先建立可追溯的新候选版本、原候选映射和相应技术验证，不应通过手改 canonical 状态绕过验证。新增候选时给 `review` 指定 `--origin-manifest`，指向最初版本的 `review_manifest.json`；原始 623 条 ID 集合的校验和固定，新增不能替代或删除原候选的处置记录。

### 最终处置表与图表级确认

在新版本的 `review_ledger.csv` 中，只修改以下人工列：

- `final_disposition`、`human_reviewer`、`human_reviewed_at`、`human_notes`。
- `reviewed_content_sha256`：审核实际内容后，填写该版本对应的 `current_v1_content_sha256`；不是盲目批量复制。
- `merge_target`：合并时指向最终批准的保留记录。
- `dependency_ids`（JSON 数组）和 `dependency_reviewed`（明确 true/false）：批准时必须明确必要节点、条件、脚注；没有依赖也需显式确认空数组。不能用空数组掩盖缺失条件。

审核时间使用带时区的 ISO-8601，例如 `2026-09-28T15:30:00-04:00`。不要改 AI 意见、原始内容、来源定位或已有 hash 列。导入器只接受上述人工列变更，且不会自动补签名或批准：

```bash
python kb_v1.py import-human-review --review-dir team_work/review_after_corrections --csv team_work/human_review.csv --output team_work/human_review_ledger.jsonl
```

每个 `group_reviews.json` 组还需团队确认跨切片遗漏、去重、路径/表格依赖，填写 `human_confirmed`、`group_status=complete`、审核人、带时区时间、理由及核对后的 `reviewed_group_sha256`。所有记录都有最终处置且依赖完整后再检查：

```bash
python kb_v1.py release-check --review-dir team_work/review_after_corrections --ledger team_work/human_review_ledger.jsonl
```

机器只能检查填写完整性、指纹、来源与显式依赖，不能证明签名来自真实的人，也不能证明临床逻辑完整。团队需管理审批文件写权限和审阅责任。当前交付中这些人工字段全部保持待审。

## 5. 维护端重建

在仓库 `Data-Analyses/adaguideline/open_source_kb_pipeline/`，使用 Python 3.12：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-v1-maintainer.lock.txt
.venv/bin/python kb_v1.py doctor --maintainer
```

准备现有完整章节的 `outputs/full_guideline_kb/`、原 PDF、当前原始视觉 JSON 和固定 MiniLM 本地快照。模型版本为 `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`。模型需提前由维护者获取；构建/查询均不自动下载。

正式发布（**当前因人工审核未完成而应失败**）：

```bash
python kb_v1.py build --kb-dir outputs/full_guideline_kb --pdf "ADA principles for pharmacologic therapy.pdf" --model "/path/to/1110a243fdf4706b3f48f1d95db1a4f5529b4d41" --review-dir team_work/review_after_corrections --ledger team_work/human_review_ledger.jsonl --output outputs/releases/ada_ch9_v1
```

只有工程开发时明确加 `--candidate`，得到不含视觉证据的 `review_candidate`。正式流程要求全量候选最终处置、07/08 技术检查、v1 人工指纹、图表级确认和依赖闭包，并且至少一条视觉证据通过，零视觉时拒绝正式构建。

基础文本保持原文，检索副本只清除可明确识别的行政/参考文献部分。页面预览不占检索名额；同页完全包含于表格的重复文本只保留作审计。表格保留为 **尚未验证行列关系的 PDF 文本表格**，不是已经证明正确的结构化临床规则；同表续页与脚注作为依赖随证据提供。已知图页的普通PDF文本带布局警示：没有发布视觉模型记录，不代表这些文本就能正确表达原图的跨栏条件、箭头和脚注。

## 6. 本地验收和组员验收

```bash
python -m unittest discover -s tests -v
python kb_v1_acceptance.py --bundle outputs/kb_v1_work/20260928/portable_candidate_v4 --source-cases ../../../data/raw_inputs/final_results_capstone_data_ver2.csv --output outputs/kb_v1_work/20260928/acceptance_teammate_01
```

维护端报告记录：10 个现有病例的文本与结构化两种检索、实际 token 窗口、NumPy/FAISS 同向量内积对照、中文空格路径及不同工作目录测试、来源文件校验和、单元测试日志。此脚本创建独立新目录，不覆盖结果。

Windows 组员需补交：Python 版本、安装日志、doctor、verify、至少一次示例检索、PDF 来源能否打开。通过后才能将 Windows 从“待验收”改为“已验收”。跨语言检索质量仍需单独建立评测基准。

### 复现本次候选工作区与本地交付（维护端）

以下在原仓库流水线目录运行，使用上述维护端环境。输入是已存在的完整章节KB、60个原始视觉JSON及其清单、原团队审批表、符号表、原图，以及本轮交付的AI意见。`--model`替换为本地固定快照路径。所有输出目录名都需是新的，避免覆盖已完成的工作。

```bash
python kb_v1.py review --raw-dir outputs/visual_logic_raw_review_20260823_completed --approvals manual_review_packages/ada_principles_20260823_qwen3vl_0533d743/visual_review_approvals.csv --registry guideline_symbol_registry.csv --ai-notes outputs/kb_v1_work/20260928/ai_notes/all_623_ai_notes.jsonl --output team_work/review_reproduced
python kb_v1.py build --kb-dir outputs/full_guideline_kb --pdf "ADA principles for pharmacologic therapy.pdf" --model "/path/to/1110a243fdf4706b3f48f1d95db1a4f5529b4d41" --review-dir team_work/review_reproduced --output team_work/candidate_reproduced --candidate
python kb_v1.py verify --bundle team_work/candidate_reproduced --allow-candidate
python kb_v1_acceptance.py --bundle team_work/candidate_reproduced --source-cases ../../../data/raw_inputs/final_results_capstone_data_ver2.csv --output team_work/acceptance_reproduced
python kb_v1_delivery.py --bundle team_work/candidate_reproduced --review team_work/review_reproduced --notes outputs/kb_v1_work/20260928/ai_notes --acceptance team_work/acceptance_reproduced --output team_work/ADA_KB_REVIEW_CANDIDATE --zip
```

维护端验收包括FAISS对照。本次macOS沙盒限制其共享内存，最终在获准的本地沙盒外通过；不要把原生库报错当成通过，也不要把这一维护端问题归咎于组员端NumPy检索。组员只查询已有包，不必重跑视觉提取或维护端FAISS测试。

旧PDF、原始JSON和图像仍保留其原始定位字符串。维护端要重建时必须有相应源文件；消费端只需完整解压包，不依赖这些旧绝对路径。AI建议的生成本身不是确定性重跑脚本，上述复现是对已保存、已绑定来源的初审结果重新验证和构建，不是声称每次AI审读都会得到完全相同判断。

## 7. 与 API 工作流的边界

组员将病例交给本检索器，获得带来源和 KB 版本的 JSONL 证据包；随后可在自己的 API 调用中决定怎样使用证据。接入提示词、模型生成、引用检查和下游评分是下一阶段。本轮不提供这部分，也没有证明临床准确率提升。

模型采用 NumPy 精确内积，无 FAISS 运行时依赖。精确同分按稳定 ID 排序；浮点误差范围内的微小分数差不具备临床意义。知识库缺少合适证据时仍可能返回相似文本，因此必须阅读条件和适用范围，不能把 top-1 自动当作正确答案。
