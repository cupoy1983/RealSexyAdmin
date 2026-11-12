from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.base_model import MappedBase, ModelMixin, UserMixin


class StoreModel(ModelMixin, UserMixin):
    """店铺 - 利润核算的最小单位，以快麦店铺id唯一标识"""

    __tablename__: str = "ecommerce_store"
    __table_args__: dict[str, str] = {"comment": "电商店铺"}

    kuaimai_shop_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True, comment="快麦店铺id")
    store_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True, comment="店铺名称")
    platform: Mapped[str] = mapped_column(String(64), nullable=False, comment="所属平台")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:正常 1:停用)")

    tags: Mapped[list["TagModel"]] = relationship(
        "TagModel", secondary="ecommerce_store_tag", back_populates="stores",
    )


class TagModel(ModelMixin, UserMixin):
    """负责人(tag) - 对一个或多个店铺负责的人，是间接成本分摊的归集单位"""

    __tablename__: str = "ecommerce_tag"
    __table_args__: dict[str, str] = {"comment": "电商店铺负责人(tag)"}

    # 唯一（§16 C2）：重名判定原先只有 Python 那道闸（Z13），界面并发改一下就能建出两个「张三」，
    # 分摊归集就把一份间接成本拆给两个查不到的人。软删的幽灵行也占名字——负责人删除已改成物理删除。
    tag_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True, comment="负责人名称")
    description: Mapped[str | None] = mapped_column(Text, default=None, nullable=True, comment="备注")
    status: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="状态(0:正常 1:停用)")

    stores: Mapped[list["StoreModel"]] = relationship(
        "StoreModel", secondary="ecommerce_store_tag", back_populates="tags",
    )


class StoreTagModel(MappedBase):
    """店铺-负责人 关联表（多对多，纯关联，不走通用 CRUD，由 StoreModel.tags 维护）"""

    __tablename__: str = "ecommerce_store_tag"
    __table_args__: dict[str, str] = {"comment": "店铺与负责人(tag)关联表"}

    store_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ecommerce_store.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True, comment="店铺ID",
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ecommerce_tag.id", ondelete="CASCADE", onupdate="CASCADE"),
        primary_key=True, comment="负责人(tag)ID",
    )
