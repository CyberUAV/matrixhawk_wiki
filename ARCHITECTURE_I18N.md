# 双语 Wiki 架构（中/英）

matrixhawk_wiki fork 自 `ArduPilot/ardupilot_wiki`，在其 Sphinx 多分册体系上加了
gettext 国际化层，目标是一套源码构建出 `/en/`（英文原文）+ `/zh/`（简体中文）
两个完整站点。本文记录架构决策、已知问题与工具链用法。

## 1. 架构总览

```
                 ┌ 上游同步 ┐
ArduPilot/ardupilot_wiki ──► original 分支 ──merge──► master
                                                        │
      每分册 RST 源码 (copter/ plane/ ... common/)       │
                │  sphinx gettext (i18n_extract.sh)     │
                ▼                                       ▼
      locale/<vehicle>/zh_CN/LC_MESSAGES/*.po ◄── AI 预翻译 + 人工校对
                │                                (i18n_ai_pretranslate.py)
                │  质量门: i18n_lint.py + msgfmt -c (CI)
                ▼
      update.py: (11 分册 × N 语言) 笛卡尔积并行构建
                │  confoverrides={language, html_search_language}
                ▼
      /en/copter/ ... /zh/copter/ ... + 各语言 frontend 落地页
      + 顶层 index.html 按 Accept-Language 重定向
```

单一事实来源：`common_conf.LANGUAGES`（sphinx 代码 ↔ URL 前缀 ↔ 显示名），
update.py、各分册 conf.py、主题模板都从这里读。新增语言只改这一处 +
建 `locale/*/<code>/` 目录。

- **URL 方案**：`/en/<vehicle>/`、`/zh/<vehicle>/`，短前缀由 `URL_PREFIX` 映射。
- **覆盖率横幅**：`mwiki_translation_coverage.py`（Sphinx 扩展）按页统计
  .po 翻译率注入 html_context，主题据此显示"本页未完全翻译"横幅；
  fuzzy 条目按未译计——正确，fuzzy 是草稿不会被 Sphinx 渲染。
- **中文搜索**：Sphinx 不会把 `language='zh_CN'` 自动映射到中文分词器
  （其搜索语言表里只有 `zh`），会静默回退英文分词 → 中文页面搜不到。
  已修：`common_conf.SEARCH_LANGUAGE` + update.py 注入
  `html_search_language`，依赖 jieba（requirements.txt）。

## 2. 翻译质量工具链

| 工具 | 作用 |
|---|---|
| `scripts/i18n_ai_pretranslate.py` | Claude API 批量草译（写 fuzzy），术语表 `i18n_glossary_zh_CN.yaml` |
| `scripts/i18n_lint.py` | reST 标记完整性检查（9 类缺陷 A-I），CI 质量门 |
| `scripts/i18n_autofix.py` | 机械修复：恢复 target/URL/替换引用/literal、CJK 贴字插空格、bogus python-format 标志 |
| `scripts/i18n_inject.py` | 人工/AI 校对后的译文批量写回（marker 预检 + msgfmt 后检） |
| `.github/workflows/i18n-lint.yml` | push/PR 触及 locale/ 时跑 lint + msgfmt |

### 缺陷类别（i18n_lint.py）

- **A/B** 链接 target 被翻译（`:ref:`target`` / `:ref:`标签 <target>``——标签可译，target 不可）
- **C/D** 命名链接/裸 URL 丢失或损坏
- **E** `|替换引用|` 被翻译（图片/公式替换失效）
- **F1/F2** ``literal`` 反引号被丢弃 / 内容被翻译（wiki 惯例：双反引号=代码/参数/UI，保英文）
- **G** fuzzy 草稿
- **H** 批次错位嫌疑（长英文段落的"译文"完全无中文或短得离谱）
- **I** 行内标记紧贴 CJK 字母——docutils 只在标记前后是空白/标点时才识别行内标记，
  汉字属字母类（Lo），``x`` 贴汉字会按原文渲染。修法：两侧补半角空格。

### 2026-07 首次全量体检的教训（739 条缺陷 / 18.4 万条,0.4%）

1. **批次错位**（最严重）：早期 AI 流水线按 JSON id 映射批量译文，个别批次
   发生了平移错位——整段译文安到了相邻 msgid 上（如 xbstation 页出现 YJUAV
   飞控的译文）。表现为 B/C/D/H 类集中爆发在少数文件。教训：**凡批量生成，
   必须有与生成方独立的一致性校验**（lint 的 H 类启发式 + marker 比对）。
2. **xgettext 误标 python-format**：原文含 "90% of" 之类会被 gettext 启发式
   打上 `#, python-format`，中文标点跟在 `%` 后即成"非法格式符"，msgfmt -c
   整文件报错。修法：Sphinx 散文目录一律改 `no-python-format`。
3. **msgmerge fuzzy 错配**：字符串相近的标题（"Compass Calibration" ←
   "ESC Calibration"）被 fuzzy 复用了错误译文。fuzzy 必须逐条复核，不能批量去旗。
4. **docutils CJK 贴字规则**（I 类）：中文技术写作必须在行内标记两侧留空格。

### 2026-07 第二轮全量深查（质量之外的成熟度维度）

第一轮修 reST 标记完整性；第二轮把「同样全量、同样彻底」扩展到风格与仓库工程：

1. **跨册译文漂移（K 类，系统性）**：common-\*.po 每册一份、AI 又按册独立翻译，
   同一英文句子最多累计出 17 种中文说法（若/如果、须/需、空格风格）。
   新工具 `scripts/i18n_harmonize.py` 投票归一：859 个 msgid、4074 条统一为
   最高频版本（限 len≥40 的散文句，避免误伤上下文相关短串）。
2. **术语漂移（N 类）**：遥测/数传、自动驾驶仪/飞控、故障保护|失效保护/失控保护、
   接收器/接收机、振动/震动、伺服/舵机并存。harmonize `--terms` 以
   **英文原文含该术语**为闸门做归一（931 条），不误伤无关用法。归一后各术语唯一。
3. **纯链接行标签未译**：54 个 `:ref:` 整行条目 msgstr==msgid，其中 38 个
   含可译短语（"Camera Control in Auto Missions" 等）已译（124 条），
   其余为产品型号名，保留英文是正确策略。
4. **上游源码级 typo 顺手修复**（fork 的红利）：
   - `common-CSKYF405.rst`: "En**images/CSKY405_wiring.png**able Battery monitor."
     （图片路径粘断了 Enable）
   - `rover-apm2-setup.rst`: ``:ref:`here) <sonar-sensors>``` 标签内混入 `)`
   - `plane-navigation-overview.rst`: ``` ``set_``next_WP```` ``` 嵌套反引号
     （msgid 同步更新，否则译文失配）
5. **源树污染**：vehicle source 里发现 167 个未跟踪的主题 demo 残留
   （foo.rst/demo.rst/webpack.\*.js/jquery.js/searchindex.js 等，来自
   sphinx_rtd_theme docs），未被 toctree 引用但会被 Sphinx 当孤儿页构建、
   `_static/` 里的 jquery.js 会**覆盖主题同名文件**。已备份后清除。
   `git status` 保持干净是防污染的唯一常态手段——不要用 .gitignore 把它们藏起来。
6. **PO header 全量健康**：3672 个文件 Language=zh_CN、UTF-8、
   `nplurals=1; plural=0;` 全部正确。obsolete 条目 2476 条——保留
   （msgmerge 的翻译记忆），不计入覆盖率。
7. **判读方法论教训**：全量扫描的第一轮结果里大量"缺陷"其实是
   **译文修正了原文的 typo**（英文源括号本就不配对）或**合法保留**
   （GCS 报错原文、编译宏、产品名、论文标题必须保英文）。
   自动检查器给的是"嫌疑清单"，判定必须回到原文逐条对照——
   这也是 lint 只把强判据（A-J）设为 CI 阻断、风格类(K/N)只做报告的原因。

## 3. 已知架构债与建议路线

### 3.1 common-*.po 重复 11 份（最大的债）

上游把 `common/` 的 RST 复制进每个分册再构建，因此 626 个 common-*.po
在 11 个分册的 locale 里各存一份——18.4 万条目里约 85% 是重复。
现状靠 `scripts/i18n_sync_common.py`（读 update.py 生成的
`locale/_common_manifest.json`）在分册间同步，但共享目录方案
（`locale/common/` 单一目录 + 各 conf.py `locale_dirs` 按序查找）
**设计了未执行**——manifest 还没生成过。
建议：跑一次完整 update.py 生成 manifest → 迁移到共享目录 →
分册目录只留分册特有 .po。收益：翻译只改一处、仓库瘦 ~80%、
lint/构建时间等比例下降。

### 3.2 部署（.readthedocs.yaml 目前是摆设）

`.readthedocs.yaml` 只构建 `ardupilot/source/conf.py` 单分册英文版，
与真实的 update.py 多分册双语构建完全脱节。两条路：

- **自托管（推荐，与上游一致）**：GitHub Actions 跑
  `update.py --languages en,zh_CN --destdir ...`，产物推静态托管
  （对象存储 / Pages / 自有服务器）。上游 ardupilot.org 就是这么干的。
- RTD `build.commands` 自定义构建也能跑 update.py，但 RTD 的
  版本/语言模型与本仓库"一次构建全语言"的模型不匹配，不建议。

### 3.3 上游同步循环（standing workflow）

```
git fetch upstream && git checkout original && git merge upstream/master
git checkout master && git merge original          # 解决 RST 冲突
./scripts/i18n_extract.sh                          # 重新抽取 pot + msgmerge
python3 scripts/i18n_ai_pretranslate.py locale/    # 只译新增/fuzzy
python3 scripts/i18n_lint.py locale/               # 质量门
python3 scripts/i18n_autofix.py locale/            # 机械修复
# 残余进人工/AI 校对 → i18n_inject.py 写回
```

### 3.4 部署前必须换掉的上游遗留（rebrand 清单）

- `frontend/sitemap.xml`：还是 xml-sitemaps 生成的 **ardupilot.org 全量 URL**，
  对本站部署完全无效且误导爬虫——按真实域名+双语前缀重生成，或先删。
- `frontend/index*.html`：内嵌 `plausible.ardupilot.org` 统计脚本（数据打给上游）
  和多处 ardupilot.org/discuss 链接——统计换自有实例或删，社区链接可保留但应明示。
- `common_conf.wiki_base_url = 'https://ardupilot.org/'`：**intersphinx 逃逸**——
  所有跨分册 `:ref:` 链接（copter↔plane↔dev…）解析到上游英文站，
  中文读者点一下就离站。部署域名定了之后改成
  `wiki_base_url = 'https://<域名>/{lang}/'` 并按语言注入（需要 update.py
  confoverrides 一并传 intersphinx_mapping，或构建后做 objects.inv 本地化）。
  这是双语体验闭环的**最后一块架构短板**。

## 4. 业界对标（2026-07 硬件厂商文档站调研）

| 模式 | 代表 | 做法 | 适用前提 |
|---|---|---|---|
| **A. 平行源树** | Espressif esp-docs（ESP-IDF 英/中） | `docs/en` + `docs/zh_CN` 文件结构逐一对应，纪律是"一段一行 + 两语言行号对齐"，CI 校验目录同步（`check_lang_switch.py`），未译文件用 include 指令回退英文 | **自己原创双语内容**的厂商；两棵树靠人力纪律同步 |
| **B. TMS 字符串管理** | PX4 / QGroundControl（Crowdin） | 源码只有英文，Crowdin 自动导入变更字符串→社区翻译→审核→导出 PR；未译字符串显示英文；URL `/{version}/{lang}/` | 社区众包翻译；TM/术语库/进度面板/审核流由平台托管 |
| **C. 仓内 gettext .po** | 经典 Sphinx/RTD（**我们**） | .po 目录进仓库，msgmerge 跟踪源变更，段落级英文回退，工具链自持 | 跟踪上游的 fork + 自动化(AI)翻译 |
| （企业门户） | ST Developer Zone 中/日文版 | 独立本地化门户、企业 CMS，"feature parity" | 企业预算；不适用 |
| （不翻译） | Zephyr、Raspberry Pi、NVIDIA 技术文档 | 官方只维护英文 | ——反衬维护成本之高 |

**结论：模式 C 对我们是正确选型**，不必迁移：

- 模式 A 不可取：我们不原创英文，上游高频变更下两棵 RST 树的 merge 是灾难；
  Espressif 的行号对齐纪律只在"自家作者写双语"时成立。
- 模式 B 的核心能力我们已用 gettext 等价实现：字符串级变更跟踪=msgmerge、
  未译回退英文=gettext 内建（我们还多一个覆盖率横幅，比静默回退更诚实）、
  TM/术语=harmonize 投票+词表 YAML、质量门=lint(Crowdin 没有 reST 语义检查,我们更强)。
- PX4 的经验教训直接适用："translation closely tracks the source" 是维护
  多语言的生死线——**它靠 Crowdin 自动导入实现，我们必须靠自动化上游同步实现**
  （见 3.3 的循环，当前还是手动，这是与业界最大的 workflow 差距）。
- PX4 只维护"社区有承诺"的语言——维护成本是真实约束；我们的答案是
  AI 流水线 + lint 门禁把边际成本压到接近零。
- 若将来要引入社区校对：**Weblate**（自托管、原生 .po、内建 TM/术语库/审核流）
  可零架构改动接入——.po 选型保住了这扇门。

**对标催生的调整清单**（并入 3.5 优先级）：
- 上游同步自动化（cron CI：fetch upstream → msgmerge → AI 译增量 → lint → PR）
- 全站翻译覆盖率汇总页（复用 mwiki_translation_coverage 的数据，构建时聚合）
- 语言切换器逐页深链 + hreflang（Espressif/PX4 都有,已在 3.4）
- 版本化（Espressif/PX4/ST 都做）**明确不做**——上游 ArduPilot wiki 即无版本,
  参数页已有 --paramversioning 兜底

### 3.5 待办（按优先级）

1. common 目录去重（3.1）
2. 部署管线落地（3.2）+ 构建产物冒烟检查（每语言抽 N 页断链/搜索索引存在性）
3. SEO：`<link rel="alternate" hreflang>` 互指 + 每语言 sitemap
   （主题模板已有语言切换所需的 `languages`/`current_language` 上下文，
   hreflang 可在同一模板补）
4. 术语表扩充：把本次重译沉淀的术语（陷波=notch、总距=collective、
   尾桨=tailrotor、失控保护=failsafe、解锁=arm 等）补进
   `i18n_glossary_zh_CN.yaml` 的 terms 段
5. 清理：`locales/` 空目录已删；`common/source/_themes/` 是 11MB 未跟踪
   主题副本（无 conf.py 引用，主题实际走 pip 安装）——确认无用后删除
