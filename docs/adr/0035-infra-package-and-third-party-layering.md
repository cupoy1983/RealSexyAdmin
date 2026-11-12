# 第三方对接归到 `app/plugin/infra`，归层判据是「删掉某个插件它还成立吗」

原先三个外部系统客户端（快麦、钉钉、企微群机器人）放在 `module_ecommerce/integration/` 里，当成电商的一个子域。它们确实只服务电商，但里面写的是「怎么跟快麦拿令牌」「怎么调钉钉审批接口」——这些语句和「店铺」「运费」「批次」没有任何关系，换任何一个插件都长一个样。于是把它们移出插件，归到新的基础包 `backend/app/plugin/infra/`，包下只有一个 `integration/`：`kuaimai_client.py` / `dingtalk_client.py` / `wecom_bot.py` 三个适配器，加一个 `http.py` 承载共用的发请求与 `access_token` 缓存机制。

**包名叫 `infra`，不叫 `common` / `base` / `shared` / `toolkit` / `support` / `_shared`**，理由是这些名字在仓库里都已经有主或已被占用另一种含义：

- `common` 撞两次。框架有 `backend/app/common/`（`enums.py`、`base/`、`data/`），而 `module_ecommerce/common/` 又是电商自己的批次子域。第三个 `common` 会让人在 import 时赌错——`from app.common…` 与 `from …common…` 都合法，指的是完全不同的东西。
- `base` 与框架里的 `CRUDBase` / `ModelMixin` 那套「基类」含义混叠，读代码的人会把目录当成类的继承层次。
- `shared` / `support` / `toolkit` 是「谁都能拿点东西」的口气，不承诺任何归层判据，实际会退化成杂物间。
- `_shared` 用下划线声明私有，但 `app/plugin/` 下的东西本来就是给插件用的，前缀只是噪音。

`infra` 说的是「基础设施」这一层职责，名字本身自带提问：这里的东西是不是不依赖任何具体业务？

**归层判据只有一句：删掉某个插件，它还成立吗？**成立就留在 `infra`，不成立就退回那个插件。这一句替代了「有几个消费者」这类统计式判断——`kuaimai_client` **目前只有电商在用**，但「拿 access_token → 带令牌调接口」这件事不属于电商的领域，所以它属于 `infra`；反过来，导入批次只有订单和账单在用，而订单和账单就是电商本身，所以它留在插件里。判据看的是**性质**，不是消费者数量，因此不需要等第二个插件出现才敢往里放东西。

**三层职责分清，才知道每段代码该落在哪**：

1. **传输机制**（`infra/integration/http.py`）：`request_json()` 发一次带超时的请求并把非 JSON 响应变成带系统名的报错；`cached_access_token()` 先读 Redis、没有再调 `fetch` 回调并回写缓存。它不认识任何外部系统，「系统名」是调用方传进去的参数。
2. **适配器**（每家一个文件）：`kuaimai_client.py` / `dingtalk_client.py` / `wecom_bot.py`。一个外部系统一个文件，只解决「它的 URL、它的参数格式、它的错误码长什么样」。不混层的具体代价是原来的写法：三个文件各自写了一遍 `httpx` 调用，其中两家还各自复制了一遍令牌缓存，改超时或改缓存策略要改三处。
3. **业务翻译**（留在各插件内）：把外部字段变成我们自己的概念。例如 `order/importers/kuaimai_api_source.py` 把快麦返回的订单字典转成 `RawOrderRecord`——它用的是电商的词（系统订单号、买家实付、运费收入），删掉电商插件它就毫无意义，所以不进 `infra`。

密钥仍然只从 `.env` 读（`settings.KUAIMAI_*` / `DINGTALK_*` / `WECOM_*`），不入库、不进代码明文，`access_token` 只落 Redis。`infra` 化没有改变这一点，只是让「读 env」这件事出现在一个中立位置，供后来的插件复用。

**`infra` 里不放 `controller.py`，这是硬约定不是偏好**。路由发现（`app/core/discover.py`）只扫 `app/plugin/module_*/**/controller.py`：`infra/` 不以 `module_` 开头，放在它下面的 `controller.py` **不会被注册，而且启动时一行警告都没有**——不像导入失败会中止启动，也不像模块里没有 `APIRouter` 会打 warning，这个文件根本不在扫描结果里，接口静默消失，只有等用户点页面拿到 404 才发现。所以「`infra` 不放 controller」写进了 `http.py` 的模块文档字符串，因为光靠口头约定挡不住这个坑。需要 HTTP 入口的功能，入口必须写在某个 `module_*` 插件里。

**`infra` 不建 `plugin.toml`**：那份元数据在运行时没有任何代码读取（只有代码生成器会顺手写一份），插件注册靠的是目录名前缀，不是配置文件。

**导入批次不下沉到 `infra`，而是在电商插件里下沉一级**：`module_ecommerce/common/` 下的五件套搬进 `module_ecommerce/common/import_batch/`。批次号（`order-2026-09`、`bill-yto-2026-09`）、确认状态、`row_count` 全是电商的领域概念（ADR-0033），按上面的判据不进 `infra`；而 `module_ecommerce/common/` 这个目录要留给「电商内部多家共用」的东西，批次长期蹲在里面会让这个位置的名声变差——下次真要有通用的电商工具时，没人分得清 `common` 是干什么的。下沉一级同时把中间目录空了出来，`common/` 从此只作为分类目录存在。`common/` 的**准入声明**：只有「电商多个子域共用、且不是外部系统对接」的代码才进 `common/`；外部对接进 `infra`，单家专有的逻辑留在它自己的子域。

> Redis 缓存键跟着一起去了前缀：`app/common/enums.py` 里 `KUAIMAI_ACCESS_TOKEN` / `DINGTALK_ACCESS_TOKEN` 的 key 从 `ecommerce_kuaimai_access_token` / `ecommerce_dingtalk_access_token` 改为 `kuaimai_access_token` / `dingtalk_access_token`（ADR-0019 说业务代码不动框架，这是那次约定的例外，已单独批准）。理由是键名是给运维读的诊断信息，一个属于中立基础包的东西顶着某插件的名字，会让下一个用同一家外部系统的插件无所适从。`WX_MINI_ACCESS_TOKEN` 本来就没带前缀，未动。这两个键只是 `access_token` 的缓存，改键名最坏的后果是重新取一次令牌，不需要迁移数据。

**本轮没做的一件事**：`order/importers/excel_source.py` 与 `logistics/parsers/generic_parser.py` 里各有一套「Excel 脏值转数字/日期」的小函数（空串集合、失败静默变 0、报错带不带行号），看着也像可以抽进 `infra/excel.py` 的机制。它们确实符合判据，但抽出来会改动两条已经验证过的导入回归路径（V1–V16、V17+），需要重跑一遍才有资格说没坏，所以另起一轮单独做，不与这次搬迁混在一次改动里。

> **修订（2026-11-07）：这里「另起一轮」的那件事定为不做，不再是挂账。**报错口径那一半 AF2 轮已经做了（脏值带行号整份不落库）；剩下的「三份空值词表合成一份共享原语」，业务方核对真实导出后确认数值格子里只有空与数值，`--` `/` `无` `暂无` 一种都没出现过（只有出库单「店铺全称」那格写 `-`，那归 `store/service.py` 的 `_PLACEHOLDER_STORE_NAMES` 管）。合一份的收益随之缩成「以后改名单少改两处」，而代价（`common/` 新增文件 + 三条导入路径重跑回归）没变——不划算。三份词表保持分开，且**不许再靠"合并"把它们取并集**：店铺那份必须认 `0`（出库单拿 `0` 当编码），数值那两份绝不能认 `0`（件数 0、成本 0 是有意的值，ADR-0023）。销账记录在设计文档 §11「本期挂账」最后一条。
