from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.utils.common_util import search_to_dict

from .crud import StoreCRUD, TagCRUD
from .model import StoreModel, StoreTagModel, TagModel
from .schema import (
    StoreCreateSchema,
    StoreOutSchema,
    StoreQueryParam,
    StoreUpdateSchema,
    TagCreateSchema,
    TagOutSchema,
    TagQueryParam,
    TagUpdateSchema,
)


def _trim_required(value: str | None, label: str) -> str:
    """手填的标识字段：去首尾空格后入库，trim 完为空就当没填（§16 A5/B3）。

    界面那把尺子更严（压根不许敲空格），这里只做兜底：同一个店铺编码带不带首尾空格，trim 后是同一家店，
    不 trim 就会给同一家店开出两个门牌，而订单导入那边是 trim 过的，永远认不出带空格的那条，
    自动建档（ADR-0024）会再建一条——一家店两条记录、两份间接成本。
    """
    text = (value or "").strip()
    if not text:
        raise CustomException(msg=f"{label}不能为空（纯空格也算空）")
    return text


# 文件「店铺全称」那一格的**占位写法**：快麦对没有店名的单据（出库单，店铺编码固定 `0`）就填 `-`。
# 2026-11-03 AN34 定的是「这种值不拿去覆盖档案」——人工建好的店，每导一次月被改成 `-` 一次。
# 出库单那家店（编码 `0`）现在由下面那条更宽的例外管，这份词表管的是别的店哪天也冒出占位写法。
# 与导入器里那份 `_EMPTY_TOKENS`（数值/整数列的「空」）是两份词表、各管一件事，而且**永远不能合成一份**：
# 这一份必须认 `0`（出库单拿 `0` 当店铺编码），数值那一份绝不能认 `0`——件数 `0`、成本 `0` 是有意的值（ADR-0023）。
# R 轮那笔「Excel 脏值原语合并」挂账 2026-11-07 业务方核对真实导出后定为不做，理由见设计文档 §11 最后一条。
_PLACEHOLDER_STORE_NAMES: frozenset[str] = frozenset({"-", "--", "/", "0", "无", "暂无"})

# 快麦给「出库单」这类无源单据留的固定店铺编码。这家店由人工建档挂负责人，2026-11-08 AP3 定它
# **整条不参与覆盖**（原话「编码0的店铺，店铺名和平台做一个例外，拉取时都不变」）。
# 文件那两格实测是 `-`（店名）与 `其它`（平台，AN33 逐行读过真导出）；档案里那个「快麦」不是文件刷进去的，
# 是他 2026-11-06 手工建店时自己填的。冻住覆盖之后这一格从此不跟文件走，而界面又改不动它
# （`platform` 从编辑里排除了，见下方 update），要动只能走数据库。档案「快麦」/主订单「其它」这个不一致
# 是 2026-11-08 AQ1 的定案（原话「编码0的店铺，就让平台为快麦吧」），不是待修缺口，见 ADR-0024 与设计文档 §27.7。
# 与上面那份词表是**先后**关系、不是重复：先问编码是不是 `0`（两格都不动），不是才轮到词表管店名、平台照刷。
OUTBOUND_SHOP_CODE: str = "0"


class StoreService:
    """店铺管理模块服务层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def detail(self, id: int) -> StoreOutSchema:
        obj = await StoreCRUD(self.auth, self.db).get(id=id, preload=["tags"])
        if not obj:
            raise CustomException(msg="该店铺不存在")
        return StoreOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: StoreQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[StoreOutSchema]:
        offset = (page_no - 1) * page_size
        return await StoreCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search, {}),
            preload=["tags"],
            out_schema=StoreOutSchema,
        )

    async def _resolve_tags(self, tag_ids: list[int]) -> list[TagModel]:
        """按 ID 列表取 Tag 对象，缺一个都要报错，不能静默忽略不存在的负责人"""
        if not tag_ids:
            return []
        tags = await TagCRUD(self.auth, self.db).get_list(search={"id": ("in", tag_ids)})
        if len(tags) != len(set(tag_ids)):
            missing = sorted(set(tag_ids) - {t.id for t in tags})
            raise CustomException(msg=f"负责人(tag)不存在: {missing}")
        return list(tags)

    async def ensure_store(self, *, kuaimai_shop_id: str, store_name: str, platform: str) -> StoreModel:
        """订单导入时按快麦店铺id自动建档：存在则更新名称/平台，不存在则新建（ADR-0024）。

        新建店铺不分配负责人(tag)，在被人工分配前不参与间接成本分摊，并挡住当月结算。

        名称与平台走手工建档那把尺（`_trim_required`，§20.3 AF3）：不 trim 就会给同一家店开出
        两个门牌（带空格的一条、不带的一条），而「不同即覆盖」每份文件都会把带空格的值重新判成
        变了。导入器已按行号拦下空的名称/平台，这里的拒绝是写入边界的第二道，不靠它报错给用户。

        「不同即覆盖」有两条例外，按先后判：
        ① 店铺编码是 `OUTBOUND_SHOP_CODE`（出库单）时**店名与平台两格都不动**，直接返回原档案（AP3）；
        ② 其余店：文件店名那一格是占位写法（`_PLACEHOLDER_STORE_NAMES`）时**不动**店名——出库单之外也有
        人拿 `-` 占位，人工建好并挂上负责人的店每导一次月会被改成 `-` 一次——平台照旧刷新。
        两条例外都只作用在**覆盖**上：档案里本来没有这家店时照旧按文件新建，所以顺序是先建店挂负责人、
        再补导那批订单（AP5：建歪了由业务方手工改）。
        """
        store_name = _trim_required(store_name, "店铺名称")
        platform = _trim_required(platform, "所属平台")
        obj: StoreModel | None = await StoreCRUD(self.auth, self.db).get(kuaimai_shop_id=kuaimai_shop_id)
        if obj:
            if kuaimai_shop_id == OUTBOUND_SHOP_CODE:
                # AP3：出库单那家店是人工档案，店名与平台都不吃「不同即覆盖」，连 UPDATE 都不发。
                return obj
            # AN34：文件里那一格是占位写法时**不动**档案里的店名；平台照旧刷新（按平台汇总是硬需求）。
            rename = store_name not in _PLACEHOLDER_STORE_NAMES and obj.store_name != store_name
            if rename or obj.platform != platform:
                obj = await StoreCRUD(self.auth, self.db).update(
                    id=obj.id,
                    data={"store_name": store_name if rename else obj.store_name, "platform": platform},
                )
            return obj
        return await StoreCRUD(self.auth, self.db).create(
            data={"kuaimai_shop_id": kuaimai_shop_id, "store_name": store_name, "platform": platform, "status": 0},
        )

    async def create(self, data: StoreCreateSchema) -> StoreOutSchema:
        kuaimai_shop_id = _trim_required(data.kuaimai_shop_id, "快麦店铺id")
        store_name = _trim_required(data.store_name, "店铺名称")
        platform = _trim_required(data.platform, "所属平台")
        exist_obj = await StoreCRUD(self.auth, self.db).get(kuaimai_shop_id=kuaimai_shop_id)
        if exist_obj:
            raise CustomException(msg=f"创建失败，快麦店铺id {kuaimai_shop_id} 已被其它店铺使用，请勿重复建档")

        tags = await self._resolve_tags(data.tag_ids)

        create_data = data.model_dump(exclude={"tag_ids"})
        create_data |= {"kuaimai_shop_id": kuaimai_shop_id, "store_name": store_name, "platform": platform}
        obj = await StoreCRUD(self.auth, self.db).create(data=create_data)

        if tags:
            # 替换关联前必须先把对象按 preload=["tags"] 取回来：create() 返回的对象上 tags 是未加载
            # 的懒加载属性，直接 clear() 会触发一次同步 SQL（异步会话里报 MissingGreenlet，整单 500）。
            obj = await StoreCRUD(self.auth, self.db).get(id=obj.id, preload=["tags"])
            await StoreCRUD(self.auth, self.db).set_store_tags_crud(store_obj=obj, tag_objs=tags)

        obj = await StoreCRUD(self.auth, self.db).get(id=obj.id, preload=["tags"])
        return StoreOutSchema.model_validate(obj)

    async def update(self, id: int, data: StoreUpdateSchema) -> StoreOutSchema:
        obj: StoreModel | None = await StoreCRUD(self.auth, self.db).get(id=id, preload=["tags"])
        if not obj:
            raise CustomException(msg="更新失败，该店铺不存在")

        # 所属平台与快麦店铺id 不可修改：由订单导入自动建档时确定，人工编辑只改名称/状态/负责人
        update_data = data.model_dump(
            exclude_unset=True,
            exclude_none=True,
            exclude={"tag_ids", "kuaimai_shop_id", "platform"},
        )
        if "store_name" in update_data:
            update_data["store_name"] = _trim_required(update_data["store_name"], "店铺名称")
        if update_data:
            await StoreCRUD(self.auth, self.db).update(id=id, data=update_data)

        if data.tag_ids is not None:
            tags = await self._resolve_tags(data.tag_ids)
            await StoreCRUD(self.auth, self.db).set_store_tags_crud(store_obj=obj, tag_objs=tags)

        obj = await StoreCRUD(self.auth, self.db).get(id=id, preload=["tags"])
        return StoreOutSchema.model_validate(obj)

    async def batch_set_tags(self, ids: list[int], tag_ids: list[int]) -> None:
        """批量把选中店铺的负责人(tag)整体替换为 tag_ids。"""
        stores = await self._load_stores_or_raise(ids)
        tags = await self._resolve_tags(tag_ids)
        for store in stores:
            await StoreCRUD(self.auth, self.db).set_store_tags_crud(store_obj=store, tag_objs=tags)

    async def batch_set_status(self, ids: list[int], status: int) -> None:
        """批量设置选中店铺的状态(0:正常 1:停用)。"""
        stores = await self._load_stores_or_raise(ids)
        for store in stores:
            await StoreCRUD(self.auth, self.db).update(id=store.id, data={"status": status})

    async def _load_stores_or_raise(self, ids: list[int]) -> list[StoreModel]:
        """按 ID 列表取店铺（预加载 tags 以便替换关联），缺一个都报错，不静默忽略。"""
        if not ids:
            raise CustomException(msg="请选择要操作的店铺")
        stores: Sequence[StoreModel] = await StoreCRUD(self.auth, self.db).get_list(
            search={"id": ("in", ids)}, preload=["tags"],
        )
        found = {s.id for s in stores}
        missing = sorted(set(ids) - found)
        if missing:
            raise CustomException(msg=f"店铺不存在: {missing}")
        return list(stores)


    async def delete(self, ids: list[int]) -> None:
        """店铺不提供删除（§15.5 AA8）：只能停用。

        软删挡不住 `kuaimai_shop_id` 上的唯一索引——幽灵行仍占着那个店铺id，下月再导出这家店的
        订单会撞键、整批 500。既然不给删，也就没有「停用腾键」的必要。权限点保留，界面无入口。
        """
        raise CustomException(msg="店铺不支持删除，请改用「停用」")


class TagService:
    """负责人(tag)管理模块服务层"""

    def __init__(self, auth: AuthSchema, db: AsyncSession) -> None:
        self.auth = auth
        self.db = db

    async def detail(self, id: int) -> TagOutSchema:
        obj = await TagCRUD(self.auth, self.db).get(id=id)
        if not obj:
            raise CustomException(msg="该负责人不存在")
        return TagOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: TagQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> PageResultSchema[TagOutSchema]:
        offset = (page_no - 1) * page_size
        return await TagCRUD(self.auth, self.db).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=search_to_dict(search, {}),
            out_schema=TagOutSchema,
        )

    async def _assert_tag_name_free(self, tag_name: str, *, exclude_id: int | None = None) -> None:
        """重名按**完全相同的字符串**判（§15.5 Z13 + §16 AB2）：仍不忽略大小写。

        比 `张三` 与 `张三` 只差大小写算两个人——这与商家编码的规则 C（统一存大写）不是同一把尺子。
        比对放在 Python 而不是 SQL，正是因为 SQL 走 MySQL 的 ci 排序规则、会把两者判成相等。
        传进来的名字已经过 `_trim_required`（§16 B3），所以这条闸实际判的是「trim 之后仍完全相同」；
        数据库上的唯一索引（§16 C2）是 ci 的、比这里更严，它只会在 Python 放过时补刀。
        """
        for tag in await TagCRUD(self.auth, self.db).get_list():
            if tag.id != exclude_id and tag.tag_name == tag_name:
                raise CustomException(msg=f"负责人名称已存在：{tag_name}")

    async def create(self, data: TagCreateSchema) -> TagOutSchema:
        tag_name = _trim_required(data.tag_name, "负责人名称")
        await self._assert_tag_name_free(tag_name)
        obj = await TagCRUD(self.auth, self.db).create(data=data.model_copy(update={"tag_name": tag_name}))
        return TagOutSchema.model_validate(obj)

    async def update(self, id: int, data: TagUpdateSchema) -> TagOutSchema:
        obj = await TagCRUD(self.auth, self.db).get(id=id)
        if not obj:
            raise CustomException(msg="更新失败，该负责人不存在")
        update_data = data.model_dump(exclude_unset=True, exclude={"id"})
        if "tag_name" in update_data:
            tag_name = _trim_required(update_data["tag_name"], "负责人名称")
            update_data["tag_name"] = tag_name
            if tag_name != obj.tag_name:
                await self._assert_tag_name_free(tag_name, exclude_id=id)
        obj = await TagCRUD(self.auth, self.db).update(id=id, data=update_data)
        return TagOutSchema.model_validate(obj)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除失败，删除对象不能为空")
        objs: Sequence[TagModel] = await TagCRUD(self.auth, self.db).get_list(search={"id": ("in", ids)})
        obj_map = {o.id: o for o in objs}
        for id_ in ids:
            if id_ not in obj_map:
                raise CustomException(msg="删除失败，该负责人不存在")
        # 仍被店铺挂着就不允许删（§15.5 Z12）。级联解绑看着省事，实际等于把这些店当月悄悄变成
        # 「无负责人」，而结算门禁对无负责人是静默挡住——人会去查利润代码，不会想到是这里少了一个tag。
        rows = (
            await self.db.execute(
                select(StoreTagModel.tag_id, StoreModel.store_name)
                .join(StoreModel, StoreModel.id == StoreTagModel.store_id)
                .where(StoreTagModel.tag_id.in_(ids), StoreModel.is_deleted.is_(False)),
            )
        ).all()
        if rows:
            stores_by_tag: dict[int, list[str]] = {}
            for tag_id, store_name in rows:
                stores_by_tag.setdefault(tag_id, []).append(store_name)
            detail = "；".join(
                f"{obj_map[tag_id].tag_name}：{'、'.join(names[:5])}{'…' if len(names) > 5 else ''}（{len(names)} 个店铺）"
                for tag_id, names in stores_by_tag.items()
            )
            raise CustomException(msg=f"以下负责人仍被店铺挂着，请先在店铺里解绑：{detail}")
        # 物理删除（§16 C2）：`tag_name` 上是数据库唯一索引，而软删的幽灵行照样占着这个名字——
        # 界面看不到它、列表里也没有它，可是同名的人再也建不回来，只会得到一句「负责人名称已存在」。
        # 关联表一并删：Z12 只挡住了未删除的店铺，软删店铺残留下的关联行会变成永远数不到的孤儿。
        await self.db.execute(delete(StoreTagModel).where(StoreTagModel.tag_id.in_(ids)))
        await self.db.execute(delete(TagModel).where(TagModel.id.in_(ids)))
        await self.db.flush()
