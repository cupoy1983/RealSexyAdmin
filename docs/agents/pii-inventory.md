# 敏感信息清单：哪些数据要脱敏

`docs/agents/shared-rules.md` §3.4 是尺（规则），本文是**刻度**：每一类数据叫什么、真值住在哪张表哪一列、允许写成什么样、今天还存在哪儿、机器闸能不能自己抓到。改脱敏做法前先读这两份，不要凭印象。

证据基准：**2026-11-11 实测**（本文所有数字都是当场跑出来的，不是估的）。重新核对的命令写在文末。
本文自身**不含任何真值**：只写字段名、文件路径、行号和掩码后的形状。

---

## 一、按数据类别

级别三档：**必脱**＝无争议，出现即改；**不脱**＝业务方明确不要改（下面带 AT 号的就是拍过的）；**待拍**＝还没人点头，本文只给建议和代价。

**已拍的（2026-11-11，业务方原话）**：AT7 店名脱 · **平台名不脱**（他补充裁定「只脱店名 不脱平台」，电商平台的名称照写）· AT8 商品名直接以 SKU 编码展现 · AT9 金额无所谓（不脱，示例不必换假数）· ~~AT10 考勤组名／部门名／公司名也要脱敏~~ → **当天全面撤销：部门名／公司名／品牌名／考勤组名都不脱**（见下面补充裁定第三条，考勤组名是他随后单独补的一句）· AT11 邮箱／公网 IP 脱敏，头像无所谓（不脱）· **AT12 真值清单走 ②（开发库只读 SELECT）＋ ①（挖历史 blob）兜底**。
三条 2026-11-11 补充裁定（无新号）：**品名存量不追溯**——文档里已经写进去的 27 处品名不再逐条返工，**此后新写的一律用规格编码或主商家编码代替品名**（第 11 类）；**代码文件里的真值按处裁定**——钉钉考勤组 id 那几处批准改成假数（已执行，见 §五），B 类脚本里真读的文件路径常量**不单独改**，随「脚本常量改命令行参数」那次收口一起清（§3.4「本机绝对路径」那条；早先这里误写成「§2.5 T7」，T7 其实是「B 类必须回滚」，2026-11-12 全文改指）；**组织名这一类整体退出脱敏范围**——部门名、公司名、以及作为本项目品牌名的 `RealSexyAdmin` 都照写不脱（实测：历史那 72 条整行规则里有 53 条改的就是这个"部门名"，它同时是产品名／仓库名，脱它等于把品牌从历史里抹掉，得不偿失）。**考勤组名同日也撤销**——他补的原话是「考勤组名 也不脱」，理由同源：组名进规则会连上游 vendor 文件与产品名一起刷坏，代价大于收益。至此组织名四类（部门名／公司名／品牌名／考勤组名）整类退出脱敏范围。**AU8／AU9（同一天深夜）**：钉钉部门 id 与 IP 解析出的城市串也一并撤出脱敏范围（原话「钉钉部门 id 也不脱了吧，这个 id 别人也没用吧，以后尽量不要写了」「城市串不用脱敏，部门ID也不用脱敏，这些都无所谓的」——"以后尽量不要写"是对**部门 id** 的写作偏好，不是新增强制项）；形状像人名但其实是常用词的（房租／范围／杜总）当场判"非人名"，工具侧登记后不再当候选重复来烦人，姓名档与候选的合成筛选表由 `pii_names_review.py` 现生成（含真值，只在本机 Temp）。
只剩一处字面留白，不另开新号、也不替他定：第 6 类的**私有／回环 IP** 要不要跟公网 IP 一起脱（表格对应行标了「未拍」）。

**AU10（2026-11-12，本表 §一 的第 4 轮裁定）**：把「要拦的全量清单」（`pii-block-review-2026-11-12.md`，含真值、只在本机 Temp）交给他逐条勾之后，他的裁定是一句话——**「就按照清单来脱敏，然后在编码、文档的规则里加上这些信息（姓名、电话、邮箱、账号名等）不要写入代码和文档，后期不用专门的测试用例筛选」**。落成三件事：
1. **类别写作禁令**（正文在 §3.4「类别清单（AU10）」）：上表 1／2／3／5／6／9／10／13 这八类，**任何位置都不许写**——正文、注释、文档串、测试夹具、提交说明一视同仁。机制只有两层：形状闸 + 写作规则；**不再为脱敏新建测试用例**（A 类跑在干净环境里，既抓不到也拦不住，塞进去只会多一份没人维护的重复检查）。
2. **两条豁免**，都是实测逼出来的，不是他松的口子：**FIXTURE 占位号**（`1380013800x`／`1390000000x`／`1380000000x`、以及值本身就等于占位模板的 `x@example.com`）——闸本来就放行，规则生成器过去不放行，于是生成过"把占位号换成另一个占位号"的空转规则；**公开仓库 URL 里的 owner 名**——本仓是 public fork，地址本身就带 owner，脱它改坏 `AGENTS.md`／`issue-tracker.md` 且隐私收益为 0（豁免只覆盖 token 内部那一处，同名的其它写法照旧必脱）。加这两条豁免后：字典 128→**123**、通用词 17→**14**、真敏感 109、整行规则 26→**24**、涉及路径 11→**10**、模拟重写残留 **0**。
3. **脚本路径常量收口**（本文先前把它叫「T7」，是错的——`shared-rules.md` §2.5 T7 是「B 类必须回滚」；正确锚点是 §3.4「本机绝对路径」那条例外）：B 类脚本那 3 处 `C:\Users\...\Desktop\*.xlsx` 常量改成环境变量（`ECOM_EXPORT_NEW_FILE`／`ECOM_EXPORT_OLD_FILE`／`ECOM_EXPORT_ORDER_FILE`，`backend/tests/live/README.md` 已同步），`pii-waivers.json` 的 LOCAL_PATH 由 2 条 3 处降到 **0**。**另外抓到一处闸自身的漏洞**：`pii.mjs` 被自己的 `SKIP` 排除、扫不到自己，而它的正向手机号探针用的号码与开发库 `sys_user.mobile` 的一条同值——已换成不撞字典的连号（探针必须是编造的，这条写进了 §3.4）。

**24 条规则的演练跑了两轮 A–F 全过**（2026-11-12：`drill9.git` 区间 13 笔 → 新 tip `f006f989`；masked 提交 `e1fe6957` 发布之后再跑 `drill10.git`，区间 14 笔 → 新 tip `e3e7c4d2`，**tip 内容与已发布版本一字不差**，六项数字见 §六），并且**④ 强推已在 2026-11-12 执行**：`origin/master` 现在是 `e3e7c4d2`，推完当场拿 `pii_verify13.py --after` 指向公开仓库复扫——全历史规则行残留 **0 处**、上游 1282 笔逐笔相等、428 个签名未动。**`git log -p` 翻旧提交已经翻不到真名。** 还剩三条尾巴见 §八。

| # | 数据 | 真值来源（实测列） | 级别 | 允许的写法 | 闸能否自动抓 |
|---|---|---|---|---|---|
| 1 | 员工／用户姓名 | `sys_user.name`（真实姓名）＋ `sys_user.username`（登录账号，常是拼音真名）、`iac_account_binding.external_name`、`iac_attendance_monthly_report.name`（姓名快照）、`iac_salary_payslip.user_name`（姓名快照） | **必脱** | 主键定位：`sys_user.id=10`（绑定表里叫 `oa_user_id`）；要区分多人用员工甲／乙／丙 | **抓不到行文里的名字**。只有写成 `user_name='张三'` 或「某人 23/17」这两种形状才响（NAME_PREDICATE／NAME_RATIO） |
| 2 | 手机号 | `sys_user.mobile`、`iac_account_binding.mobile` | **必脱** | 不写；要证明"有这个字段"就 `136****5678` 或只写末两位 | ✓ MOBILE（`1[3-9]xxxxxxxxx`） |
| 3 | 钉钉 userid / unionid（**工号已于 2026-11-12 AU11 续整类退出，见 §九**） | `iac_account_binding.external_user_id`、`group_id`；`job_number` 与 `iac_attendance_*.job_number` 已退出 | **userid 必脱**；**`iac_account_dept_mapping.dingtalk_dept_id`（钉钉部门 id）2026-11-11 AU8 判为不脱**（原话「钉钉部门 id 也不脱了吧，这个 id 别人也没用吧」）；工号同 AU11 续（原话「工号不敏感没事」） | 主键或"见本机运维记录"；**部门 id 以后新写尽量不要出现**（他的写作偏好，不是闸强制） | △ 半盲。LONG_ID 只认 **16–18 位纯数字**，而 §九 实测生产那 7 个 userid 是 **14／17／18／20 四种宽度**（7 个里漏 4 个）；字母数字混合的 userid 也抓不到。**缺口 2026-11-12 当场判「不加规则、完全匹配就好」，保留不修**。撤部门 id 的实测代价是 **0**：它一条整行规则都没进过，重跑演练的 tip 与撤之前一字不差（`14f33023`） |
| 4 | 身份证号 | （库里没这列，但交付 SQL／导出 Excel 里可能有） | **必脱** | 一律不进仓库 | △ 18 位含末位 X 的形状可能不响 |
| 5 | 邮箱／头像 URL | `sys_user.email`、`sys_user.avatar` | 邮箱**必脱**（AT11）／头像**不脱**（AT11「无所谓」） | 邮箱写 `x@…` 占位或不写；头像 URL 可原样留 | 邮箱：现在**没有规则**，但可以加（`EMAIL` 形状实测 19 处命中、4 个文件，且全是占位或上游示例：`backend/sql/sys_user.json`、`frontend/app/src/uni_modules/mp-html/README.md`、`frontend/web/package.json`、`frontend/web/src/views/fastlink/fachat/index.vue`——加之前要先立反向探针）。**AU10 没有改这一格**：他拍的是「邮箱这一类不得写入代码与文档」这条**写作禁令**（已进 §3.4 类别清单），不是给闸加 `EMAIL` 形状规则——加形状规则仍要做反向探针，仍是留白。头像：✗ |
| 6 | 登录 IP / 主机地址 | `sys_login_log.login_ip`＋`login_location`（IP 解析出的地理串）、`sys_operation_log.request_ip`、`task_storage_node.host` | 公网 IP**必脱**（AT11）；私有／回环地址**不脱**（AT11 只说了公网 IP，这条是我的字面读法，**未拍**）；`login_location` 这种城市级地理串 **2026-11-11 AU8 判为不脱**（原话「城市串不用脱敏，……这些都无所谓的」），所以清单里也不取它 | `<本机运维记录>` | ✗ **裸 IPv4 形状规则不能用**：实测 43 处命中里 18 处是私有／回环，剩下的主力是版本号 `2.1.0.2`、`2.4.2.6-9` 和公共 DNS `8.8.8.8`。按 §3.4 末段，误报一次这个闸就废了——真要做必须"公网段＋上下文"再配反向探针 |
| 7 | 考勤组名／部门名／公司名 | `iac_account_binding.group_name`、`iac_account_binding.dingtalk_dept_name`、`iac_account_dept_mapping.dingtalk_dept_name`、`sys_dept.name` | **整类不脱**（AT10 当天全面撤销：部门名／公司名是他拍的「部门名就不脱敏了…公司名也不脱了吧」，考勤组名是他补的「考勤组名 也不脱」） | 四类都**可原样写**，不再换"组甲" | ✗ 无形状；已从字典按类别剔除，闸与规则都不再管它 |
| 8 | 真实工资／实发金额 | `iac_salary_profile.base_salary`、`iac_salary_payslip.{base_salary,prorated_base,net_amount}` | **不脱**（AT9「金额无所谓」，文档示例不必换假数） | 真实金额可留 | △ 只在"人名+比值"句式里响（NAME_RATIO）。注意：**「可识别字段不与真实金额同段」那条是姓名的规矩，AT9 没有重开它**，维持原样 |
| 9 | 平台订单号／系统单号／运单号／售后单号 | `ecommerce_order.system_order_no`、`ecommerce_order_detail.{platform_order_no,source_detail_id,tracking_no,after_sales_no}`、`ecommerce_courier_bill.waybill_no`、批次号 `order-2026-09` | **必脱**（单号能反查到收件人） | `6005…462465`（首4末6）或 `主键 id=NNN，完整号见本机` | ✓ LONG_ID（16–18 位）；`order-2026-09` 这种月粒度标识**不响，也不需要响** |
| 10 | 店铺名／平台名 | `ecommerce_store.store_name`＋`kuaimai_shop_id`；平台列 `ecommerce_store.platform`／`ecommerce_order.platform` | 店名**必脱**；**平台名不脱**（2026-11-11 补充裁定「只脱店名 不脱平台」，电商平台名照写） | 店铺甲／店铺乙；平台名原样 | ✗ 无形状 |
| 11 | 商品名／SKU 编码 | `ecommerce_order_detail.product_name`＋`sku_name`、`ecommerce_sku_cost.{product_name,sku_name}`；编码列 `sku_code`／`main_sku_code` | 品名**不写**，**直接以 SKU 编码展现**（AT8）；SKU 编码保留（它是算钱的键，也认不出人）。**存量 27 处不追溯**（2026-11-11 补充裁定），新写的用 `sku_code`（规格商家编码）或 `main_sku_code`（主商家编码） | 写该商品自己的 `main_sku_code`，不写品名 | ✗ |
| 12 | 收件人姓名／电话／省以下地址 | `ecommerce_order.{province,city}`、`ecommerce_courier_bill.receiver_province`（库里只到省／市，**没有**收件人姓名列） | 省以下**必脱**（城市要不要留是留白，见下） | 只到省 | ✗ |
| 13 | 本机绝对路径（含 Windows 真实用户名） | 不在库里，写在文档与脚本常量里 | **必脱** | `C:/Users/.../Temp/`；B 类脚本里真读的常量**已改环境变量**（§3.4「本机绝对路径」那条，AU10 2026-11-12 收口：`ECOM_EXPORT_NEW_FILE`／`ECOM_EXPORT_OLD_FILE`／`ECOM_EXPORT_ORDER_FILE`），豁免底账随之归零、文件当天删除 | ✓ LOCAL_PATH（现在工作树 0 命中） |
| 14 | 凭据／密钥／生产配置 | `backend/env/.env.*`（**已被 `.gitignore` 挡住**）、`*.example` 是故意留空的 | **必脱**，且**靠忽略而非靠闸**：闸只扫会被 `git add` 带上去的文件 | 不进 Git，永远不进 | ✗ 设计上不扫被忽略的文件 |
| 15 | 生产数据库本身／交付 SQL／快麦导出 Excel | 库 `realsexy_admin`、桌面文件 | **必脱**（＝永不进仓库） | 本机留存 | ✗（同上，靠忽略） |

**闸的四个盲区，必须记住**：① 行文里的真名（无形状）；② 字母数字混合的钉钉 id；③ 店名／品名——AT7／AT8 把它们定成要改，可它们**没有形状，闸抓不到**，只能靠本清单 + 人工改写（金额按 AT9、平台名按 AT7 补充裁定都不脱；组织名四类——部门名／公司名／品牌名／考勤组名——当天整类改判为不脱，已从字典里按类别剔除，闸和规则都不再看它）；④ **历史版本**——`pii.mjs` 的文件集合是 `git ls-files` + 未跟踪未忽略，旧提交里的内容它一眼都看不到。

所以分工是：**闸管"从此不再新增"，本清单＋一次性重写管"已经存在的怎么清"**。别指望改完历史闸会替你报警。

---

## 二、今天真实的暴露面（实测，2026-11-11）

**曾经进了公开历史（2026-11-12 重写并已发布，`git log -p` 现在翻不到真名）**

- 引入点只有两个提交：`5200f8bb`（2026-11-03，内务中心考勤绩效那一大笔）、`cd022e08`（2026-11-11 12:26，7 份内务 ADR + `salary/service.py`）。强推之后这两个号在远端已不存在，对应新号 `abc2cf7e`／`f006f989`；旧号只在本机备份与 reflog 里还能解析。
- `5200f8bb` 之后跟到 **13 个提交**（含 2026-11-12 的去标识化那笔 `e1fe6957`）→ 重写改变了 **14 个 commit hash**，更早的 1282 个未动（共 1296 个提交，packed 约 155 MB）。
- **远端 `origin/master` 现为 `e3e7c4d2`**：先发布内容干净的新提交 `e1fe6957`（12 个文件 28 增 28 删，纯替换），再把它下面 13 笔的旧版本一并换号。E 项实测重写后的 tip 与已发布版本**内容一字不差**（差 0 个），所以强推没有改动任何人手上的文件，只动历史；推完当场对公开仓库复扫，全历史规则行残留 0 处。

**在工作树、提交前必须改掉（当时是靠豁免底账放行的，底账现已清空并删除）**

- ~~3 个文件 38 处~~ → 2026-11-11 工作树清洗剩 2 个文件 3 处 → **AU10（2026-11-12）这 3 处已收口，命中归零**。做法：`backend/tests/live/ecommerce/verify_ac_round.py`／`verify_an_round.py` 里那三个「脚本真读的业务方导出文件」常量不再写死路径，改读环境变量 `ECOM_EXPORT_NEW_FILE`／`ECOM_EXPORT_OLD_FILE`／`ECOM_EXPORT_ORDER_FILE`（§3.4「本机绝对路径」那条）。`scripts/pii-waivers.json` 因此从 **2 条 3 处 → 0 条**，并按既定口径（归零后连文件一起删）**于 2026-11-12 删除**；当天复测 `node scripts/pii.mjs` = 1012 文件 / 0 命中，`--selftest` 12 条探针全过——闸对缺失文件按空豁免处理，删它不影响判定。**此后命中只有「改」一条路。**（当天那个 1012 后来变成 **1013**：`.gitignore` 里那行 `AGENTS.md` 按业务方点头删除后，合并版 `AGENTS.md` 从没跟踪的 ignore 名单里出来，进了扫描集。多的是被扫的文件，不是多出的命中。）
- **闸数出来的从来只是一小部分。** AT7／AT8／AT10 一拍，同一份设计文档里的真实店名、平台名、商品名也进了必改范围，而店名／组名／品名**一个都不会被 `pii.mjs` 数到**——所以"豁免底账归零"不等于"这份文档脱敏完成"。底账从 38 降到 3 之后，那份设计文档还剩 **27 处品名**：2026-11-11 补充裁定**存量不追溯**，这 27 处维持原样，只立"新写必须用规格／主商家编码"的规矩。

**已 `git add`（当时）→ 现已提交并推送（2026-11-12 `b354d0e9`）**

- `docs/ecommerce/`、`backend/tests/live/`、`backend/app/plugin/module_ecommerce/`、前后端 `module_ecommerce`。2026-11-12 实测这些路径都在索引里（那三条路径共 59 项：30 项 `A`、29 项 `AM`），不再是"未跟踪"。**§六.6 那笔门禁已解除**：这批文件的命中在 AU 轮就按 §3.4 缩略完了（那份设计文档实测已无 ≥12 位连续数字，39 处订单号写成 `6005…462465` 这种缩略形态），豁免底账也归零删除，所以不再有"因为怕泄漏而不能提交"的理由。**提交与推送的动作仍归业务方本人**，批次与顺序由他定。

**确认不用动（本轮实测排掉的误报）**

- `backend/app/plugin/module_ecommerce/deploy/menu_dict_seed.sql`：124 行，店名／单号／手机号 **0 处**。
- `frontend/web/src/utils/auth/index.ts` 里的 `access_token`／`refresh_token` 是 localStorage 键名，不是令牌值。
- 真密钥只住在被 `.gitignore` 挡住的 `backend/env/.env.*` 里。

**不该由"仓库脱敏"操心的**：开发库数据本身、桌面导出 Excel、生产备份。它们的纪律只有一条——**不复制粘贴进仓库**。

---

## 三、代号对照表怎么管（这条最容易二次泄漏）

员工甲／乙／丙 ↔ 真名 的映射**只放本机**（Temp 或密码管理器），**绝不进 Git**：一旦把对照表提交，代号就等于白起。
需要核对时由业务方自己维护；agent 不建这份表，也不把它写进任何文档或提交信息。

重写历史用的 `--replace-text` 规则文件同理：**含真值，只能在你本机存在**，跑完就删，不提交、不发聊天。

---

## 四、AT12 已定：② 为主 ＋ ① 兜底

**AT7–AT12 全部拍完（2026-11-11）**：店名脱、平台名不脱（AT7＋补充裁定）· 商品名直接以 SKU 编码展现（AT8）· 金额不脱（AT9）· 考勤组名／部门名／公司名**当天全面撤销为不脱**（AT10＋两条补充裁定，以最后为准）· 邮箱／公网 IP 脱、头像不脱（AT11）· 真值清单从开发库只读 SELECT，再用历史 blob 兜底（AT12）。

**AT12 在问什么**（历史重写的第一块砖）：重写靠 `git filter-repo --replace-text <规则文件>`，规则文件必须**逐条写出真值本身**（一行一条 `真值==>替换成什么`），git 才知道替换谁。所以必须先凑齐真值清单。

**清单怎么凑（正在做的两步）**：

1. **② 从开发库查（表列名按 model 实测）**：`sys_user`（`name` 真实姓名／`username` 登录账号／`mobile`／`email`）、`iac_account_binding`（`external_name`／`mobile`／`job_number`／`external_user_id`／`group_id`／`group_name`／`dingtalk_dept_name`）、`sys_dept`＋`iac_account_dept_mapping`（部门名／钉钉部门 id）、`ecommerce_store`（`store_name`／`kuaimai_shop_id`）、`ecommerce_sku_cost`＋`ecommerce_order_detail`（品名→`main_sku_code` 对照）。**只读**（脚本 `START TRANSACTION READ ONLY` ＋ 结束 `rollback`），结果只落 `C:/Users/.../Temp/pii-values.tsv` 与 `pii-replace-rules.txt`，不进仓库、不发聊天、跑完就删。**订单号不在这里全量拉**——库里几万条，重写只需要"仓库里真出现过"的那几十条，交给步骤①。
2. **① 从历史 blob 挖**：拿步骤②的人名／店名／部门名去逐个 `git log -S` 反查，能定位到真值进过哪些提交；再扫一遍旧提交里的形状类真值（手机号、15–20 位数字串），补上库里查不到的旧写法（已离职的人、已改名的店、已删的号）。这一步要求所有 `git show`／`git ls-tree` 都带 `-c core.quotePath=false`，否则中文文件名被转义成八进制、路径匹配不上，上一轮就是这么漏成 0 命中的。

**替换写法逐类固定**（别让规则文件自己发明口径）：姓名→员工甲／乙…（同一代号前后一致）· 手机号／钉钉 id→`<见本机运维记录>`（工号已退出，见 §九） · 店名→店铺甲 · 单号／运单号→首4末6（`6005…462465`）· 品名→**该商品自己的 SKU 编码**（AT8，不是"商品甲"）· **考勤组名／部门名／公司名／品牌名都不换**（AT10 当天整类撤销，不再写"组甲"）· 邮箱→`x@example.com` · 平台名**不动**（补充裁定）。

规则文件与对照表**含真值，只在你本机存在**，跑完就删，永不提交（§三）。生成完先在**演练 clone** 里跑一遍、把改写后的历史给你验，验过了再谈强推——强推和请 GitHub 清缓存**每次单独点头**。

**这两个东西分别是什么（大白话）**：`git filter-repo` 是 git 官方的"批量改写历史"工具，作用是在**本地副本**里把那 13 个提交逐个重做一遍——每重做一个是把里面的文件内容照规则文件替换掉，于是 commit 的哈希必然变化，看起来就像"这些提交当初就是干净的"。它对远端什么都不做，改动只落在这台机器上。**演练 clone** 就是从真仓库再 `git clone` 一份出来专给它当草稿纸：先在这份副本上跑、用 `git log -p` 翻给自己看有没有真名残留、有没有把不该动的东西刷坏，验过了才在真仓库跑同一套规则；跑坏了那份副本直接删掉重来，真仓库和远端从头到尾没被碰过。最后一步（把改写后的历史推上 GitHub 覆盖公开的那份）才是不可逆的，所以要单独点头。

---

## 五、工作树清洗的执行结果（2026-11-11）

历史重写归 `git filter-repo`（**本节写于 2026-11-11，那天还没跑；11-12 已跑完并发布，强推 `e3e7c4d2`，方案见 §六、发布后的尾巴见 §八**），这一节管的是**现在就摆在 tip 上的东西**。工具在 `C:/Users/.../Temp/pii_apply.py`，默认 dry-run。

**替换集的三档闸**（一条值能不能自动改，看的是"它像不像一个通用词"）：

1. **数字类**（单号／手机号／钉钉 id／邮箱，且长度 ≥6；**工号 2026-11-12 AU11 续已整类退出，见 §九**）→ 文档和代码都能自动换。纯数字一律带 `(?<!\d)…(?!\d)` 边界，否则一个更长编号里嵌着的子串会被拦腰截断——那是改坏，不是改脱。
2. **专有名词**（姓名／姓名快照／登录账号／店名——组织名四类已整类退出，不在此档）→ **只自动改文档**。落到 `.py`／`.vue` 里就是改动断言和夹具，属于"改行为"，要逐条点头；点头的方式是 `pii-code-approve.tsv` 里按**遮值整行**登记一条（2026-11-11 AU7 首次用到，见 §六）。
3. **品名**→ 一律**只报告不自动改**。实测挡下的理由：中文商品标题里全是"现货速发／包邮／一件代发"这类通用词，78 处公开命中里 54 处是误伤（`module_internal/account/index.vue` 一个文件就中 15 个）。AT8 要的"品名→该商品自己的 SKU 编码"是**按上下文的一对一改写**，机器做不了。

外加一条独立规则：主目录绝对路径里那个真实用户名，一律写成 `C:/Users/...`。代码文件里曾有 3 处没动（B 类脚本真读的文件常量，替换完脚本就跑不动）——**2026-11-12 AU10 已收口**：三个常量改读环境变量，路径不再进仓库，脚本没设变量时走 skip 并提示变量名。

**代码侧补充执行（2026-11-11，业务方批准"钉钉 id 那 2 处"）**：`backend/app/plugin/module_internal/config.py:5` 的文档串示例和 `backend/tests/module_internal/test_rest_day_mode_policy.py:30-31` 的两个 `OFFICE`／`WAREHOUSE` 常量，真考勤组 id 换成 `111111111`／`222222222`。判断依据：这两个数只是测试**自己注入**的 env 键（`_env()` 用同一对常量拼 JSON），不参与任何真实数据匹配，文档串那行更是纯示例——换成假数不改行为。改后该文件 6 passed，另外 6 项报 alembic「Multiple heads」，与本改动无关（未改的 `test_attendance_month_confirm.py` 一样全错；那是 #14 那笔迁移链欠账）。代码类命中从 39 处降到 **37 处**，但其中真值只有下面核对出的那几处，其余是撞车（见下一段）。

**"业务代码里应该没有敏感信息吧"——形状核对之后：基本成立，但别照着闸的计数下结论。** 闸在代码类里报了 37 处，逐条按**值的形状＋出处**核对之后是这样：
- **真信息只有 2 处**：`module_internal/config.py:5` 文档串示例和 `test_rest_day_mode_policy.py:30-31` 常量里的**钉钉考勤组 id**（已按批准换假数）。
- ~~3 处是真事但不归这次~~：`tests/live/ecommerce/verify_{ac,an}_round.py` 里 B 类脚本真读的导出文件路径常量——**2026-11-12 AU10 已收口**（改读环境变量，见 §二）。
- **2 处是品牌名**：`salary/notification.py:175,205` App 推送文案里的"××Admin"——字典把它算成"部门名"（钉钉顶级部门与品牌同名），当天已裁定部门名／公司名／品牌名不脱。
- **剩下 30 处全是撞车**：品名类字典有 2074 条，里面混着 3 位纯数字和 2 字通用词，于是 Vue 里的列宽数字、`attendance/service.py` 的钉钉列号、测试里的金额小数都被"命中"；考勤组名那 3 处也是撞车——字典里唯一的考勤组名是一条 9 位纯 ASCII 的演示值，恰好等于测试夹具自己写的标签。**所以代码类计数不能当脱敏依据，要的是类别＋形状＋出处三样一起看。**

**由此新增一条口径（2026-11-11 业务方提出并确认）**：代码里的敏感信息若落在**注释或文档串**，直接删掉或改写成中性描述，**不换成代号**——代号在代码里没人能对照，留着只会误导读代码的人；只有这个值**参与运行**（配置键、夹具常量）时才换成假数，理由同上那 2 处考勤组 id。

**实跑结果**：文档 **9 个文件 56 处**——单号 35、未识别长号 4、店名 7、考勤组名 4、钉钉 id／工号 2、手机号 2、本机路径 2。代码侧 20 个文件 39 处命中**一处没改**（其中真属"自动"档的只有 5 处，其余 34 处是品名／部门名／组名的只报告档）。

**跑完之后**：`node scripts/pii.mjs` 从 38 处降到 3 处，2026-11-12 AU10 收口那条「本机绝对路径」例外后到 **0 处**，`scripts/pii-waivers.json` 随之清空（38 处 → 2 条 3 处 → **0 条**）并按口径删除。**第二轮历史重写已明确不开**（AU11，同日）。那份电商设计文档里**剩下的 27 处品名当天已裁定"存量不追溯"**（§一 补充裁定），闸本来就看不见它们，现在也不打算追。

**核对与回滚**：改后的整段预览落在 `C:/Users/.../Temp/pii-apply-preview.txt`（只有"改后"的行——"改前"含真值，贴进聊天就是一次新泄漏）；未跟踪文件改前已逐份拷进 `Temp/pii-backup-20261111/`，跟踪中的那 9 份用 `git checkout --` 就能退回。**代号↔真名的对应表始终只在 Temp 的字典文件里，跑完删**（§三）。

---

## 六、历史重写：整行锚定 + 自建"只重建这 13 笔"的引擎（2026-11-11 AT/AU 两轮定案）

`git filter-repo --replace-text` 是**字面子串替换**，而且作用在**每一个 blob**上——它没法只挑那 13 个提交，也没法只挑我们的目录。所以"一条真值一行规则"这种做法被实测否掉了：

- 字典里的姓名有 2~3 字的值和常用词同形（那个 2 字值在**上游文件**里出现 40 次、3 字值 128 次），一条 `某值==>员工甲` 会把整个仓库（含 1282 个上游提交）里所有"高优先级"这类词一起刷坏。
- 库里种子数据带来的假手机号／占位邮箱也在字典里（`sys_user.mobile` 的演示行）。按真值盲替，会命中 **58 个 origin/master 上的上游文件**（`backend/app/scripts/data/*.json`、`backend/sql/**/*dump.sql`、`chinaMap.json`、redoc 打包件…）——那不是我们的泄漏，是撞车。

**还有第二条更硬的理由（当天实测）**：`git filter-repo` 没有保留 `gpgsig` 的开关（无 `--preserve-commit-headers`），全量重写会把上游 1282 笔里的 **428 个签名**抹掉、1295 笔全部换号。业务方因此拍 **AU2：只重写我们这 13 笔，别人的 1282 笔不改。**

**换成的机制**（生成器 `C:/Users/.../Temp/pii_hist_rules.py`，重写引擎 `pii_rewrite13.py`，验收 `pii_verify13.py`）：规则左边 = **含敏感值的那一整行原文**，右边 = 同一行、值已换代号。一行几十字，撞进别人内容的概率实测为 0。重写走 `rev-list 5200f8bb^..HEAD` → `ls-tree -r -z` 枚举文本 blob → 整行精确比对 → `hash-object -w` → **递归**重建子树（未命中的子树原样复用 sha）→ `commit-tree`（`GIT_AUTHOR_*`／`GIT_COMMITTER_*` 与父指针原样照抄）→ `update-ref`；碰到 `gpgsig` 立即停手。提交说明走同一张规则表（**AU3：备注里的敏感信息也替换**）——实测这 13 笔的说明只有 2 处命中，两处都在上游对象里成串出现过，属通用词，**实际需替换 0 处**。

三条闸门：

1. 出处闸：只有那 13 个提交**产出的 blob**（`diff-tree` 的改后镜像）才用来生成规则；路径不敏感，所以 `backend/app/config/setting.py`、`docker/docker-compose.yaml` 这种"我们改过的框架／部署文件"也在内。
2. 同形闸：某条整行只要在任何**非泄漏 blob**（上游文件 + 这些文件的更早版本）里原样出现过，就挡下来不写。字典值同理：在上游对象里出现过的按"通用词"剔除，AU10 加豁免前实测剔掉 **17 条**，加豁免后字典缩到 123，这一条跟着变成 **14 条**。
3. 收敛闸：把规则在内存里模拟作用到泄漏 blob 上，再扫一遍残留；有残留就把新的整行加成规则，直到**残留 0** 才停。

**规则文件的格式（当天踩过的坑，必须记）**：数据行一律 `R<TAB>整行原文==>{整行改后}`，原文本身含 `==>` 的走 `R64<TAB>`（base64，其字母表没有 `>`，切分无歧义）。加前缀的起因是**实测丢规则**：`## …` 这类 markdown 标题行本身就是"含敏感值的整行"，被解析器 `startswith("#")` 的注释判断吃掉，25 条只读进 23 条——那 2 行等于没脱，而且闸和验收脚本当时都用同一份错规则，D 检查因此也是绿的。规则文件与字典**只住本机 Temp，跑完即删，永不提交**。

**字典完备性（AU1，2026-11-11 晚）**：花名册的**权威来源是生产库只读 SELECT**，开发库不是它的超集——生产 `sys_user` 里 7 条业务账号展开成 21 个值，字典原本缺 15 个，且这 15 个在开发库值宇宙里一个都没有。交付物是 `pii_prod_universe_gen.sql`（每列生成一条 `SELECT DISTINCT`，先在开发库验过 656 条 / 0 失败）+ `pii_prod_universe.sh`（全程 SELECT，口令走 `read -s`，产物 600 权限）。**坑：MariaDB 客户端默认一遇错即停，批量必须加 `--force`**，否则字母序排在最后的 `sys_user` 被静默截断——上一版就是这么少的 149 列。

**实测结果**（AU10 之后：字典补齐 + AU7 + 撤 2 条部门 id + 加两条豁免）：字典字面值 **123 条** → 通用词剔 14 → **真敏感 109 条**（数字档 67、专名档 42）→ 整行规则 **24 条 / 10 个路径**（7 份内务 ADR 21 条 + `module_internal/config.py`、`test_rest_day_mode_policy.py`、`salary/service.py` 各 1 条）→ 挡 0 条 → **模拟重写后残留 0 处**。比 26 条少的 2 条正是那两条豁免各自干掉的一条：一条是"把顺序占位手机号换成另一个占位手机号"的空转规则，一条是把 `docs/内务中心-测试目录布局与回归验证记录-2026-11-10.md` 里 GitHub 仓库地址的 owner 名换成代号——后者改坏文档且零收益。撤部门 id 依旧**零代价**：那 2 条值从没进过任何整行规则。上游同形按上述原因**不动**；13 笔之外仍含字典值的 blob 全在 `origin/master` 上，逐条看是上游种子／演示数据（`backend/app/scripts/data/*.json` 的假手机号、占位邮箱、`chinaMap.json`、redoc 打包件…），不是我们的泄漏。

**AU7（唯一一处代码文件专名命中）**：`salary/service.py:617` 的文档串里有一个 3 字姓名，工作树这一行早已改成中性表述（还未提交），只有历史 blob 带名。业务方点头把它按「姓名→员工代号」加进规则表；加的方式是遮值整行白名单 `pii-code-approve.tsv`（见 §五 第 2 档），**没有**放开"代码文件整体可改"——字典以后变大，新出现的代码行仍只进 `pii-code-hits.tsv` 等逐条裁定。重写后该文件只有 617 这一行不同，参与运行的值一个没碰。

两个副作用要记着：`--replace-text` 会把 `\n` 之类当转义读，口径不确定，所以含反斜杠的行两种写法各下一条（不匹配的那条是空操作）；本地曾有 `refs/codex/turn-diffs/*` 三个 IDE 快照 ref 指向工作树 tree（`fcd43371f28d…`、`b6574828007d…` 两个 tree 对象），它们**不是提交**、filter-repo 不会重写，反查时也已用 `--branches --tags --remotes` 排除；2026-11-11 业务方裁定"没用就删"，三条 ref 已 `update-ref -d` 删除（`git for-each-ref refs/codex` 现为空）。

**演练验收（AU6 的第 ② 步，工作树全程未碰）**：每个 drill 都从当前已发布的 tip 全新 `clone --bare` 一份当基线（`pre14.git`），再 clone 一份当演练库（`drill10.git`），跑 `pii_rewrite13.py` 后 `reflog expire` + `gc --prune=now`，再 `pii_verify13.py`。**最新一轮 `drill10.git`（24 条规则、区间已是 14 笔）A–F 全过**：A 上游 1282 笔 hash 逐笔相等、428 个签名没动、**1296 进 1296 出** · B 全 14 笔里内容变过的路径**恰好 10 个**，与规则命中的 10 个路径一字不差 · C 提交说明被改 **0 笔**，作者／时间／时区／父指针原样 · D 全部可达对象里规则行残留 **0 处 / 0 个文件**、说明残留 0 处 · **E tip 文件数 1228 = 1228，内容有差 0 个**——因为 masked 提交 `e1fe6957` 已发布，重写后的新 tip `e3e7c4d2` 内容与它完全相同，**强推不会改动任何人 checkout 下来的文件，只改历史里的旧版本** · F 旧 14 笔 gc 后物理消失，仍从 master 可达的旧笔只有 `60c1deb2`（内容零改动所以 hash 天然相同，D 已证明它不含规则行，**真残留 0 笔**）。换号 **13 笔**：`5200f8bb→abc2cf7e`…`cd022e08→f006f989`、`e1fe6957→e3e7c4d2`。上一轮 `drill9.git`（区间 13 笔、新 tip `f006f989`）同样全过，那组数字已被这一轮取代。**这仍只证明方案可行，不等于已上线**：④ 强推单独点头。

**规则文件是"当前这 13 个提交"的函数**：工作树一提交，范围就变，`pii_hist_rules.py` 必须重跑一遍再拿去重写。上一版是 72 条／53 条含品牌名，就是被组织名那条改判缩下来的。

品名不在这 24 条里（AT8 要按上下文改，机器改必错），所以上面那句"残留 0"只覆盖数字类＋人名类＋登录账号／店名。

**AU10 这一版的演练状态（2026-11-12 跑了两轮，并已发布）**：`drill9.git`（区间 13 笔）和 `drill10.git`（区间 14 笔，masked 提交 `e1fe6957` 发布之后）**都 A–F 全过**，六项数字见上一段——每轮都是先 `--dry-run`（只算不写）过一遍，业务方点名放行后才真正建对象、`update-ref`、`reflog expire` + `gc --prune=now`，再跑 `pii_verify13.py`。**24 条规则 / 14 笔 / 换号 13 笔**（`60c1deb2` 内容零改动，hash 天然相同），新 tip `e3e7c4d2`。跑的命令在本文末。**④ 强推当天已执行**（业务方点名「同意执行 git push --force origin master」），公开仓库的历史已换号，推完拿 `pii_verify13.py --after` 指向本工作仓库再复扫一遍：D 项 0 残留、A 项上游 1282 笔逐笔相等。他再提交任何一笔，区间又变长，规则与演练都要再跑一轮——但规律已经清楚：**新提交只要内容本身是干净的，就不新增规则，只是跟着换号**（E 项「内容有差 0 个」说的就是这个）。

**曾经卡住的一步（当天已拍）**：这 72 条里有 53 条左边含同一个 8 位 ASCII"部门名"——那是本项目的**产品名／仓库名**（fork 在 GitHub 上就叫 `RealSexyAdmin`，`frontend/app` 标题、`package.json` 的 name、`deploy.sh` 的 `PROJECT_NAME` 里到处是它，tip 上 61 处），进字典只因钉钉顶级部门与品牌同名。2026-11-11 业务方拍**品牌名与部门名、公司名都不脱**，随后补一句「考勤组名 也不脱」，于是这几类从字典里按类别剔除（`pii_hist_rules.py` 的 `EXCLUDE_CAT`），剩下的全是姓名／姓名快照／手机号／登录账号／钉钉 id／工号／单号。

---

## 七、重新核对本文数字的命令

```bash
git rev-list --count HEAD                        # 提交总数（本文 1296）
git rev-list --count 5200f8bb..HEAD              # 重写受影响的后续提交数（本文 13；重写区间含首笔共 14 笔）
                                                 # 注：强推后旧号只在本机对象还在时能解析，远端对应新号是 abc2cf7e
git log --all --format="%h %ad %s" --date=short -S"某个真名"   # 某真值进过哪些提交
git grep -c -E "(^|[^0-9])1[3-9][0-9]{9}([^0-9]|$)" HEAD -- docs/adr   # tip 上的手机号形状
node scripts/pii.mjs                             # 工作树命中（2026-11-12 实测 1013 文件 / 0 处；豁免底账已删；只在提交／发布前跑，日常不跑）
node scripts/pii.mjs --selftest                  # 闸自己（12 条探针）
```

闸的规则原文在 `scripts/pii.mjs` 顶部 `RULES`。**业务方 2026-11-12（AU11）已正式判定不再给闸加新规则**（"不用规则了，你完全匹配就好了"），所以下面这句只在真要动规则时成立：改规则要连 `--selftest` 的反向探针一起改，理由见 §3.4 末段。核对某批真值在不在库里，用的是本机那份值清单做**完全匹配**，命令与 `Argument list too long` 那个坑见本文 §九。

**演练一轮的四条命令**（`pii_rewrite13.py`／`pii_verify13.py`／`pii-line-rules.txt` 只住本机 Temp，跑完即删；`drillN.git` 每轮新建，**没有任何一条碰本工作树或远端**）：

```bash
# 0) 一对新库：先 clone 一份当基线（未改写，验收拿它对拍），再从基线 clone 一份当演练库
git clone --bare "D:/AI/Project/RealSexyAdmin" "D:/AI/Backup/pii-rewrite-20261111/preN.git"
git clone --bare "D:/AI/Backup/pii-rewrite-20261111/preN.git" "D:/AI/Backup/pii-rewrite-20261111/drillN.git"
# 1) 先只算不写，看清每笔改几个文件（脚本里 `--dry-run` 不建对象、不落 ref）
PYTHONIOENCODING=utf-8 uv run --no-project python pii_rewrite13.py --repo "D:/AI/Backup/pii-rewrite-20261111/drillN.git" --rules pii-line-rules.txt --ref master --dry-run
# 2) 真跑：建对象 + update-ref，然后把旧对象物理挤掉（少了这步，旧提交仍能被翻出来）
PYTHONIOENCODING=utf-8 uv run --no-project python pii_rewrite13.py --repo "D:/AI/Backup/pii-rewrite-20261111/drillN.git" --rules pii-line-rules.txt --ref master
git -C "D:/AI/Backup/pii-rewrite-20261111/drillN.git" reflog expire --expire=now --all
git -C "D:/AI/Backup/pii-rewrite-20261111/drillN.git" gc --prune=now
# 3) A–F 验收（纯只读）
PYTHONIOENCODING=utf-8 uv run --no-project python pii_verify13.py --before "D:/AI/Backup/pii-rewrite-20261111/RealSexyAdmin-origin.git" --after "D:/AI/Backup/pii-rewrite-20261111/drillN.git" --rules pii-line-rules.txt --map pii-rewrite13-report.tsv
```

第 1、2 步之前先确认规则表是**当前区间**生成的：工作树一提交，`5200f8bb^..HEAD` 就变长，`pii_hist_rules.py` 必须重跑（§六）。三条命令都在 Temp 目录下执行，脚本里的 `RANGE` 取目标库自己的 `HEAD`，所以喂错库只会改错那个库，改不到 `D:/AI/Project/RealSexyAdmin`。

**发布（AU6 ④ 强推）四条，只能从 `origin` 指向 GitHub 的那个仓库发**：

```bash
git -C "D:/AI/Project/RealSexyAdmin" fetch "D:/AI/Backup/pii-rewrite-20261111/drillN.git" refs/heads/master:refs/masked/tip
git -C "D:/AI/Project/RealSexyAdmin" ls-remote origin refs/heads/master    # 必须打出当前远端 tip，不是就停手
git -C "D:/AI/Project/RealSexyAdmin" push --force origin refs/masked/tip:master
git -C "D:/AI/Project/RealSexyAdmin" update-ref refs/heads/master refs/masked/tip   # 不跟上去，下次普通 push 会复活脏历史
```

两条踩过的坑，别再踩：

- **演练库的 `origin` 是本地备份，不是 GitHub**（`git clone --bare` 会把源目录记成 origin）。2026-11-12 有一次把 `push --force origin master` 发到 `drillN.git` 里，结果盖掉的是验收基线 `pre14.git` 的 `master`（对象没丢，`update-ref` 一行还原）。发布动作前先 `git remote -v` 看清 origin 到底是谁。
- **裸库里没有 `origin/master` 这种远程跟踪引用**，所以「先确认远端 tip」那道保险写成 `log --oneline -1 origin/master` 会直接报 `unknown revision` 而不是拦住推送——保险空转。要核远端只有两条路：`ls-remote <真实URL> refs/heads/master`，或读目标库自己的 `refs/heads/*`。

---

## 八、强推之后剩下的尾巴（2026-11-12 实测收口，task #32）

「公开仓库翻不到真名」要分四层，**第一层做完不等于清完**。下面每一行都是**当天实测**，不是机制推断：

| 层 | 实测结果 | 状态 |
|---|---|---|
| GitHub 的 `master` 分支 | `e3e7c4d2`，`git log -p` 走这条时间线翻不到真名（对公开仓库复扫 D 项 0 残留） | ✅ 完成 |
| GitHub 远端**引用** | `ls-remote` 只有 `refs/heads/master` 一条，**无 tag、无 `refs/pull/*`**；仓库 fork／star／watcher 全 0 | ✅ 没有别的东西钉住旧提交 |
| GitHub **服务端旧对象＋缓存视图** | 强推之后仍然取得到：`GET /commit/cd022e08…` → **200**、`GET /commit/cd022e08….patch` → **200（整份 diff 30 KB）**、`git fetch origin cd022e08…` → **成功打包送回**；把那一页按字典反查得 **5 条真值 / 69 次**（3 条姓名 + 1 条姓名快照 + 1 条登录账号）。`GET /commit/5200f8bb….patch` → 200，含 3 条真值 | ⚠️ **只有 GitHub Support 能清**——工单 **#4992805 已于 2026-11-12 由他本人提交**，正文见下，复验口径在 §八 末尾 |
| 协作者手上的旧副本 | 兄弟 clone 已同步；本仓库 `.git` 的孤儿旧对象已 `reflog expire` + `gc --prune=now` 物理删除（`cat-file -t` 两个旧号都报 could not get object info，`fsck` 无告警，`rev-list --count master` 仍 1296，工作树 11 个改动一字未动） | ✅ 完成 |

**注意第 3 行这条结论推翻过一次判断**：2026-11-12 我先按 Git 机制推断「旧号极小概率被取到，工单可以不开」，随后实测是 `200` + `git fetch <旧sha>` 直接成功——**凡是没量过的外发动作别当已知**。按 SHA 取旧版本不需要任何权限，知道那串号就行，所以必须请 Support 清。

### 工单要说什么（官方清单在 [Removing sensitive data from a repository](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)，入口 https://support.github.com ）

官方要求提供：① owner/repo；② 受影响的 PR 数（本仓 **0**，实测无 `refs/pull/*`）；③ `git-filter-repo` 报的 *First Changed Commit*（我们没用它，等价物是最老那笔 `5200f8bb6716f0d4ffce61db521417460c1fba86`）；④ 是否有 LFS 孤儿对象（**本仓不用 LFS**，唯一的 `.gitattributes` 只有 linguist 两行）。然后请他们做三件事：解除／删除受影响 PR、**服务端 GC**、**移除 cached views**。

要请他们回收的旧号 **13 个**（新历史里已不存在；`60c1deb2` 不在此列——它内容零改动，新旧两条历史共用同一个号，且已证明不含真值）：

```
5200f8bb6716f0d4ffce61db521417460c1fba86   ← First changed commit（最老）
ed99087e89f0547b61696e2432baa2c1cf780aca
00f3aac721a286ba4f917cbf65af8c589bea3c76
ddf7b6cc32528e24de8e8c9a1417fbb9d1932785
638e680037fa9272111541dbbce559192c2f4634
2742ddf40914e1299c65e4957c83351b90c9f02e
2c100671875c718f6809c0140bab0987152ccdb4
9a10fceab0e7042f14bd9b0beb921198c6cd56ca
98df5498d878d93c02b7cc5dc177d0b87311571f
3c80f3406ac5368423ef0afa4c4a5f3232f26861
816cf234117e79979526b6a16d6a6a0644cebd9f
cd022e08c42a079baecc0e450c8248a092f766af
e1fe6957e303c96583d01b10f818f7058a229d3e
```

工单正文（英文，可直接粘；只含 SHA，不含任何真值）：

```text
Subject: Please run server-side GC and remove cached views after a history rewrite (my own public repo)

Repository: cupoy1983/RealSexyAdmin (I am the owner; the repository is public)

What happened: between 2026-11-03 and 2026-11-12 some commits contained personal
data of my employees (real names, plus phone-number-shaped and ID-shaped
identifiers) inside documentation and code comments. I have rewritten that part
of history and force-pushed it.

Current state:
- refs/heads/master = e3e7c4d21a6c31a72b90ecbf2b452ea205c77d3f (force-pushed 2026-11-12)
- The rewritten range only touches the top of the history; the 1282 upstream
  commits below it were not modified.
- Affected pull requests: 0. `git ls-remote origin "refs/pull/*"` returns nothing,
  and there are no tags. refs/heads/master is the only ref on the remote.
- Forks: 0, watchers: 0 — no other repository references this data.
- Git LFS: not used (the only .gitattributes contains linguist hints), so no LFS
  objects were orphaned by the rewrite.
- First changed commit (oldest superseded commit):
  5200f8bb6716f0d4ffce61db521417460c1fba86
- The 13 superseded commits to expunge:
  5200f8bb6716f0d4ffce61db521417460c1fba86
  ed99087e89f0547b61696e2432baa2c1cf780aca
  00f3aac721a286ba4f917cbf65af8c589bea3c76
  ddf7b6cc32528e24de8e8c9a1417fbb9d1932785
  638e680037fa9272111541dbbce559192c2f4634
  2742ddf40914e1299c65e4957c83351b90c9f02e
  2c100671875c718f6809c0140bab0987152ccdb4
  9a10fceab0e7042f14bd9b0beb921198c6cd56ca
  98df5498d878d93c02b7cc5dc177d0b87311571f
  3c80f3406ac5368423ef0afa4c4a5f3232f26861
  816cf234117e79979526b6a16d6a6a0644cebd9f
  cd022e08c42a079baecc0e450c8248a092f766af
  e1fe6957e303c96583d01b10f818f7058a229d3e

The data is still retrievable by SHA after the force-push — verified on
2026-11-12, this is not hypothetical:
- GET https://github.com/cupoy1983/RealSexyAdmin/commit/cd022e08c42a079baecc0e450c8248a092f766af -> 200
- GET .../commit/cd022e08c42a079baecc0e450c8248a092f766af.patch -> 200 (full diff of the personal data)
- `git fetch origin cd022e08c42a079baecc0e450c8248a092f766af` succeeds and packs the old commit

Please:
1. dereference / delete any affected pull requests — there are none;
2. run a garbage collection on the server so the 13 commits above are expunged;
3. remove the cached views for those commit URLs, including the .patch and .diff
   variants.

Please confirm once done, and I will re-verify that they return 404.
```

**工单已于 2026-11-12 由他本人提交：#4992805**（`https://help.github.com/ticket/personal/0/4992805`，Open／优先级正常）。我这边只填字段、点「继续」走到最终页，**Submit 是他点的**。

**分类这 9 条是实测过的，别再重踩**（官方文档只说"走 Support portal"，不指定分类，而 `仓库` 入口下每个 radio 都会追加子问题）：

- **Repository access issues** —— 唯一**不追加任何问题**的一条，本次用的就是它（业务方拍的）。
- 存储库功能 —— 逼你勾 **模板／版本／见解／分支**，四个都对不上"请服务端 GC + 清缓存视图"。
- **删除** —— 是「彻底清除一个已删除仓库」的流程，最后一格是 **删除／不删除** 的确认。**仓库还在用的人绝不能走这条。**
- 其余六条各问：是否收到邮件通知（邀请）／是否要解锁（迁移和锁定状态）／90 天恢复期三连问（恢复）／是否有转移权限（转移）／当时公开还是私有（Log request，索访问日志）／能否删库重建（Remove LFS objects，本仓不用 LFS）。
- 中间那页「解决方案」是官方 AI 助手，判"本地步骤已完成、剩余动作不可自助"并给出「继续创建工单」按钮——只是旁证，不是承诺。最终页的 **Type of Issue** 本次选「错误、问题；以及 API 速率限制、Actions 工作流程问题、迁移到 GitHub」。

**正文确实带过去了**：提交后在工单页量到主题在、13 个待回收 SHA + 当前 tip 共 **14 个不同 40 位号**、三条请求逐条命中、整页 2758 字符。填的时候我说过"证明不了会带过去"，这句现在已经被实测替掉。

**提交时刻的基线**：`GET /commit/cd022e08…` 当时仍是 **200**（服务端还没动）。**复验口径就一条**：上面那 13 个 SHA 的 `GET /commit/<sha>` 与 `.patch` 全部返回 **404**（用 `curl -s -o /dev/null -w "%{http_code}"` 逐个量，只看状态码，不落响应体）。GitHub 回复之前，`D:/AI/Backup/pii-rewrite-20261111/` 的备份不动。

**第一次复验（2026-11-13，工单提交后次日）**：13 个 SHA 的 `/commit/<sha>` 与 `/commit/<sha>.patch` **26 次请求全部 200**，服务端一次都没动。这条只记录「还没发生」，不改变任何别的排队——它唯一真正压住的动作是本节末尾「本机还留着脏历史的地方」那条**销毁备份**；新提交、CI、OA 拉取都不依赖它（强推后旧号只能按 SHA 直达，不在任何可达路径上）。后续复验照同一条口径量，带日期的结果一律追加、不覆盖。

**搜索引擎缓存**：`site:github.com cupoy1983 RealSexyAdmin` 检索回来的是无关站点，没有收录迹象（不是硬证明）。若复验时发现某条被 Google／Bing 快照收了，那要单独走对应引擎的删缓存请求，和 GitHub 工单无关。

**本机还留着脏历史的地方（故意留的，别当成没清）**：`D:/AI/Backup/pii-rewrite-20261111/` 下的 `RealSexyAdmin-origin.git`（`cd022e08`）、`pre14.git`（`e1fe6957`）、`drill9.git`、bundle `pre-rewrite-all.bundle`——这些是验收对拍的基线，**整份脏历史在硬盘上原样存在**。它们只在这台机器上，不属于公开面。要不要连备份一起销毁，等新历史用稳了（CI 那批提交完、OA 那边跑过一轮）再单独决定，那是另一个点头。

## 九、生产库身份列实测（2026-11-12，AU11：字典到这就是全集）

业务方在那台跑生产库的机器上，按点名的 **47 个身份列**跑了一次 `SELECT DISTINCT`（`realsexy_admin`，只读），吐回 28 个值；扣掉当场退出的工号，**口径内真值 27 个**。他这次一共说了三件事，都落进 `shared-rules.md` §3.4 第 5 条（AU11 及其续）：

- **口径收到六类**：**「基本上姓名，电话，用户名，登录名，邮箱，钉钉id就可以了」**（前半句「也不是都要脱敏的」）——「用户名」和「登录名」在库里是同一列 `sys_user.username`，所以落到库上是五类列。
- **工号退出**：**「工号不敏感没事」**。
- **不再加验证**：**「我都把敏感信息给你了，不用规则了，你完全匹配就好了」**＋**「也说过后续不用加验证，只要你们agent不写敏感信息就好了」**——`scripts/pii.mjs` 保持现状，本节末尾那张缺口表因此只是记录，不是待办。

主控制是写作规则：**「后续在文档里表明，agent都不继续写的话，提交代码也不用脱敏了」**，所以**不再为新一批数据开第二轮历史重写**。这一节只管"值住在哪张表哪一列"。

### 列 ↔ 类别（只写表列名，一个真值都不落纸）

| 他的类别 | 生产库里的列 | 实测不同值 | 备注 |
|---|---|---|---|
| 真实姓名 | `sys_user.name` · `iac_account_binding.external_name` · `iac_attendance_monthly_report.name` · `iac_salary_payslip.user_name` | **6 个真人**，四张表同一集 | 另有 `超级管理员`/`管理员`/`普通用户` 三行是上游演示账号的显示名，**不录**（录了就是 433 处误杀的成因） |
| 手机号 | `sys_user.mobile` · `iac_account_binding.mobile` | **7 个真人号** | 另有 `138001380 00/01/02` 三条顺序占位号，走闸的 `FIXTURE` 放行 |
| 登录账号 | `sys_user.username`（`sys_login_log.username`、`sys_operation_log.username` 是它的下游副本） | **7 个** | 他点名的「用户名」与「登录名」在生产库里就是这一列，库里没有第二个账号列。**六类落到库上是五个**，不是少查了一类。`admin`/`super`/`user`/`unknown` 是框架自带，不录 |
| 邮箱 | `sys_user.email` | **0 个真邮箱** | 生产库里只有 `admin@`/`super@`/`user@example.com` 三条出厂占位 |
| 钉钉 userid | `iac_account_binding.external_user_id` | **7 个** | **长度是 14 / 17 / 18 / 20 四种**，不是单一宽度——这一条直接决定闸怎么改 |
| 工号 | `iac_account_binding.job_number` · `iac_attendance_monthly_report.job_number` | 1 个 | **当场判退出脱敏范围**（「工号不敏感没事」）——挖到了，但不算口径内，所以下面合计是 27 |

合计口径内真值 **27 个**（六类落到库上是五个，工号当场退出）。**与 2026-11-11 他截图那 7 行比：姓名／手机号／登录账号一个都没多**（那 7 行里 `杜总` 占两个账号，所以真人是 6 个），**字典净增只有 5 个钉钉 userid**（另 2 个 userid 早就从代码里录过）。生产库里**没有 `ecommerce_*` 表**，所以电商的单号／店名不在这把尺的射程内（对应 task #8：电商代码从未进 Git）。

**一并挖到、当场明确不脱的**：部门名／公司名／考勤组名（`*.dingtalk_dept_name` · `sys_dept.name` · `*.group_name`）· 考勤自由文本（`iac_attendance_daily_report.raw_text`/`approve_text`/`effective_text`、`iac_attendance_monthly_report.dingtalk_raw`——实测里面只有「上班迟到229分钟」这类状态串和纯数字 JSON，**不含姓名**）· 通知正文 `iac_salary_notification_log.content`（含姓名，但那是运行时数据，永远不进提交面）· IP 归属地 `sys_login_log.login_location` · 框架参数 `sys_param.*`（实测全是 FastApiAdmin 出厂值 + 一条 ICP 备案号）。

### 历史这条线为什么可以收口（命令可复现）

拿这 28 个值扫**已发布** `master` 的 1296 笔提交 ＋ 工作区（含当时尚未退出的工号，是口径内 27 个的超集，结论照用）：

```bash
git rev-list refs/heads/master \
  | xargs -n 250 sh -c 'git grep -I -l -F -f <(cat 生产token表) "$@" || true' sh
```

> **坑（踩过）**：把 1296 个 SHA 直接展开成命令行参数会 `Argument list too long`，而那句报错会被管道当成"命中一行"——第一次跑就是这样读出一个假的 `1 处命中`。必须分批，并且要拿**重写前的备份库**跑同一条命令做正对照，`0` 才算话。

- 正对照：`D:/AI/Backup/pii-rewrite-20261111/RealSexyAdmin-origin.git`（1295 笔，重写前）→ **16 个文件命中**。
- 已发布 `master`（1296 笔）→ 命中文件 8 个，其中**属于我们的那 7 个（6 份内务 ADR + `salary/service.py`）全部消失**，正对照的差集正好是它们。
- 剩下的命中两类，都不是我们泄的：① `xiaoxiao`（8 字母）在**上游自带**的 `README.md`/`README.en.md`/官网文档里（541 个修订命中，上游自己写过又删了）；② `cupoy` 命中我们一份测试记录文档，是仓库地址 `<owner>/RealSexyAdmin` 里 owner 名的**子串**——§3.4「公开仓库 URL 里的账号名不算泄漏」那条豁免正是为它建的（豁免清单在 Temp 的 `pii-public-identifiers.txt`，不进仓库）。

### 闸对不上这六类：缺口实测（AU11 续当场判「不用规则，完全匹配就好」，本表只作记录，不再是待办）

`scripts/pii.mjs` 现在 5 条规则全是形状闸，逐类一比：

| 类别 | 现有规则 | 差在哪 |
|---|---|---|
| 手机号 | `MOBILE` | ✅ 全覆盖 |
| 钉钉 userid | `LONG_ID`（16–18 位） | **7 个真 id 里漏 4 个**（14 位 1 个、20 位 3 个）。若要改就按这四种实测宽度改、不拍数——**本次已否决，不改** |
| 邮箱 | — | **无规则**，但 §3.4 写着邮箱要脱，闸抓不到。生产库实测**真邮箱 0 个**，只有三条出厂占位，所以这一类今天没有存量压力 |
| 登录账号 | — | **无规则**；且它与公开仓库地址里的 owner 名同形，真要加必须同时带 URL 边界，否则 `AGENTS.md` 每提一次仓库地址就红一次——**本次已否决，不加** |
| 真实姓名 | `NAME_PREDICATE` · `NAME_RATIO` | 只抓 `user_name='…'` 谓词和「人名 3/5」句式，**行文里的裸姓名抓不到**。真名清单又不能进仓库（闸自己会变成泄漏源）——要抓得让闸从本机读清单，这是一处新设计。**本次已否决** |
| 工号 | — | **整类退出**（AU11 续：「工号不敏感没事」）。不再有规则、也不再占字典条目；以前按工号拦下来的存量不返工 |

**这一节的结论不是待办**：他把控制点放在「agent 不写」这句写作规则上，核对手段是**拿本机那份值清单对全库完全匹配**（上面 §九 那条 `git grep -I -l -F -f` 命令，分批 + 正对照），而不是给闸加形状模式。以后**别再提补规则的建议**，也别在 `pii.mjs` 里动手——要动必须先有他的原话。


