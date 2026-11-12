-- 电商利润模块（module_ecommerce）菜单/权限 一次性订正脚本
--
-- 用途：§10/§11.1 已确认——菜单与权限点的权威录入方式是手工走管理界面「菜单管理」，
-- 这份脚本只是上线后用来核对/补录的订正副本，不走 backend/sql/*.json 的自动种子机制
-- （那套机制只在 sys_menu 表当前为空时触发，现网库已有数据不适用）。
--
-- 使用方式：先在管理界面手工录入完一遍，再用本脚本核对字段是否完全一致；
-- 如果环境是全新初始化（sys_menu 恰好为空），也可以直接执行本脚本代替手工操作。
-- 本脚本依赖 MySQL 的 LAST_INSERT_ID()，仅适用于本项目当前的 MySQL 部署（§数据库配置）。
--
-- 执行前提：ecommerce_* 表已通过 `uv run main.py upgrade --env=dev/prod` 建好
-- （菜单记录本身不依赖业务表外键，但建议随同一次上线一起执行，保持顺序一致）。

-- ── 一级目录：电商利润 ──────────────────────────────────────────────────
INSERT INTO sys_menu
  (uuid, is_deleted, created_time, updated_time,
   name, type, `order`, permission, icon, route_name, route_path, component_path, redirect,
   hidden, keep_alive, always_show, title, affix, link, is_iframe, is_hide_tab, show_badge,
   status, description, parent_id)
VALUES
  (UUID(), 0, NOW(), NOW(),
   '电商利润', 1, 50, NULL, 'ri:store-2-line', 'Ecommerce', '/ecommerce', NULL, '/ecommerce/store',
   0, 1, 0, NULL, 0, NULL, 0, 0, 0,
   0, '电商店铺月度利润核算', NULL);
SET @ecommerce_root_id = LAST_INSERT_ID();

-- ── 二级菜单：店铺管理（store + tag 两个 tab 合在一个页面） ──
INSERT INTO sys_menu
  (uuid, is_deleted, created_time, updated_time,
   name, type, `order`, permission, icon, route_name, route_path, component_path, redirect,
   hidden, keep_alive, always_show, title, affix, link, is_iframe, is_hide_tab, show_badge,
   status, description, parent_id)
VALUES
  (UUID(), 0, NOW(), NOW(),
   '店铺管理', 2, 10, 'module_ecommerce:store:query', 'ri:store-line', 'EcommerceStore', 'store', 'module_ecommerce/store/index', NULL,
   0, 1, 0, NULL, 0, NULL, 0, 0, 0,
   0, '店铺 / 负责人(tag)（店铺从订单数据自动建档，无映射表）', @ecommerce_root_id);
SET @ecommerce_store_menu_id = LAST_INSERT_ID();

-- ── 按钮权限点（type=3），均挂在「店铺管理」二级菜单下 ──────────────────
-- 店铺(Store)：查询权限直接复用二级菜单自身的 permission，这里补 增/改/删/详情
INSERT INTO sys_menu (uuid, is_deleted, created_time, updated_time, name, type, `order`, permission, hidden, keep_alive, always_show, affix, is_iframe, is_hide_tab, show_badge, status, parent_id) VALUES
  (UUID(), 0, NOW(), NOW(), '新增店铺', 3, 1, 'module_ecommerce:store:create', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id),
  (UUID(), 0, NOW(), NOW(), '修改店铺', 3, 2, 'module_ecommerce:store:update', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id),
  (UUID(), 0, NOW(), NOW(), '删除店铺', 3, 3, 'module_ecommerce:store:delete', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id),
  (UUID(), 0, NOW(), NOW(), '店铺详情', 3, 4, 'module_ecommerce:store:detail', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id);

-- 负责人(Tag)
INSERT INTO sys_menu (uuid, is_deleted, created_time, updated_time, name, type, `order`, permission, hidden, keep_alive, always_show, affix, is_iframe, is_hide_tab, show_badge, status, parent_id) VALUES
  (UUID(), 0, NOW(), NOW(), '查询负责人', 3, 5, 'module_ecommerce:tag:query', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id),
  (UUID(), 0, NOW(), NOW(), '新增负责人', 3, 6, 'module_ecommerce:tag:create', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id),
  (UUID(), 0, NOW(), NOW(), '修改负责人', 3, 7, 'module_ecommerce:tag:update', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id),
  (UUID(), 0, NOW(), NOW(), '删除负责人', 3, 8, 'module_ecommerce:tag:delete', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id),
  (UUID(), 0, NOW(), NOW(), '负责人详情', 3, 9, 'module_ecommerce:tag:detail', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_store_menu_id);

-- 录入后记得在「角色管理」里把以上权限点勾给需要使用电商利润模块的角色，
-- 否则页面和按钮即使菜单存在也不会显示（权限串需出现在角色的 permissions 里）。

-- ── 二级菜单：订单与商品成本（order：订单查询导入 + SKU成本维护 + 缺失成本待补充） ──
INSERT INTO sys_menu
  (uuid, is_deleted, created_time, updated_time,
   name, type, `order`, permission, icon, route_name, route_path, component_path, redirect,
   hidden, keep_alive, always_show, title, affix, link, is_iframe, is_hide_tab, show_badge,
   status, description, parent_id)
VALUES
  (UUID(), 0, NOW(), NOW(),
   '订单与商品成本', 2, 20, 'module_ecommerce:order:query', 'ri:file-list-3-line', 'EcommerceOrder', 'order', 'module_ecommerce/order/index', NULL,
   0, 1, 0, NULL, 0, NULL, 0, 0, 0,
   0, '订单查询导入 / SKU成本维护 / 缺失成本待补充', @ecommerce_root_id);
SET @ecommerce_order_menu_id = LAST_INSERT_ID();

INSERT INTO sys_menu (uuid, is_deleted, created_time, updated_time, name, type, `order`, permission, hidden, keep_alive, always_show, affix, is_iframe, is_hide_tab, show_badge, status, parent_id) VALUES
  (UUID(), 0, NOW(), NOW(), '订单详情', 3, 1, 'module_ecommerce:order:detail', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id),
  (UUID(), 0, NOW(), NOW(), '导入订单', 3, 2, 'module_ecommerce:order:import', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id),
  (UUID(), 0, NOW(), NOW(), '更新订单', 3, 3, 'module_ecommerce:order:update', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id),
  (UUID(), 0, NOW(), NOW(), '刷单核对', 3, 4, 'module_ecommerce:order:scam_reconciliation', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id),
  (UUID(), 0, NOW(), NOW(), '查询商品成本', 3, 5, 'module_ecommerce:sku_cost:query', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id),
  (UUID(), 0, NOW(), NOW(), '新增商品成本', 3, 6, 'module_ecommerce:sku_cost:create', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id),
  (UUID(), 0, NOW(), NOW(), '修改商品成本', 3, 7, 'module_ecommerce:sku_cost:update', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id),
  (UUID(), 0, NOW(), NOW(), '删除商品成本', 3, 8, 'module_ecommerce:sku_cost:delete', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id),
  (UUID(), 0, NOW(), NOW(), '商品成本详情', 3, 9, 'module_ecommerce:sku_cost:detail', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_order_menu_id);

-- ── 二级菜单：快递账单（logistics：账单详情（导入·异常处理·批次确认）/ 解析规则管理；缺账单已退化为订单列表的筛选项，§24） ──
INSERT INTO sys_menu
  (uuid, is_deleted, created_time, updated_time,
   name, type, `order`, permission, icon, route_name, route_path, component_path, redirect,
   hidden, keep_alive, always_show, title, affix, link, is_iframe, is_hide_tab, show_badge,
   status, description, parent_id)
VALUES
  (UUID(), 0, NOW(), NOW(),
   '快递账单', 2, 30, 'module_ecommerce:logistics:query', 'ri:truck-line', 'EcommerceLogistics', 'logistics', 'module_ecommerce/logistics/index', NULL,
   0, 1, 0, NULL, 0, NULL, 0, 0, 0,
   0, '账单详情（导入·异常处理·批次确认）/ 解析规则管理（ADR-0033/0034）', @ecommerce_root_id);
SET @ecommerce_logistics_menu_id = LAST_INSERT_ID();

INSERT INTO sys_menu (uuid, is_deleted, created_time, updated_time, name, type, `order`, permission, hidden, keep_alive, always_show, affix, is_iframe, is_hide_tab, show_badge, status, parent_id) VALUES
  (UUID(), 0, NOW(), NOW(), '导入账单', 3, 1, 'module_ecommerce:logistics:import', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '账单行改对置已恢复', 3, 2, 'module_ecommerce:logistics:update', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '查询解析规则', 3, 3, 'module_ecommerce:bill_parse_rule:query', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '新增解析规则', 3, 4, 'module_ecommerce:bill_parse_rule:create', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '修改解析规则', 3, 5, 'module_ecommerce:bill_parse_rule:update', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '删除解析规则', 3, 6, 'module_ecommerce:bill_parse_rule:delete', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '解析规则详情', 3, 7, 'module_ecommerce:bill_parse_rule:detail', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id);

-- ── 本期新增：账单行逻辑删除 + 导入批次查询与确认（ADR-0033/0034） ──
-- 批次确认端点挂在 logistics 与 order 两个路由下，共用同一个权限点，勾一次即可两边可用。
INSERT INTO sys_menu (uuid, is_deleted, created_time, updated_time, name, type, `order`, permission, hidden, keep_alive, always_show, affix, is_iframe, is_hide_tab, show_badge, status, parent_id) VALUES
  (UUID(), 0, NOW(), NOW(), '删除账单行', 3, 8, 'module_ecommerce:logistics:delete', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '查询导入批次', 3, 9, 'module_ecommerce:batch:query', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '确认导入批次', 3, 10, 'module_ecommerce:batch:confirm', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id);

-- ── 2026-10-20 追加：账单批次作废 + 声明本月无账单（ADR-0033） ──
-- 上面整段已经执行过的环境只需要单独跑这里两行；父菜单按 permission 回查，不依赖会话变量。
-- 本脚本不做幂等，重复执行会插出重名权限行，跑前先按 permission 查一次重。
SELECT id INTO @ecommerce_logistics_menu_id
  FROM sys_menu WHERE permission = 'module_ecommerce:logistics:query' AND type = 2 AND is_deleted = 0 LIMIT 1;

INSERT INTO sys_menu (uuid, is_deleted, created_time, updated_time, name, type, `order`, permission, hidden, keep_alive, always_show, affix, is_iframe, is_hide_tab, show_badge, status, parent_id) VALUES
  (UUID(), 0, NOW(), NOW(), '作废账单批次', 3, 11, 'module_ecommerce:batch:delete', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id),
  (UUID(), 0, NOW(), NOW(), '声明本月无账单', 3, 12, 'module_ecommerce:batch:declare', 0, 1, 0, 0, 0, 0, 0, 0, @ecommerce_logistics_menu_id);

-- 同样记得把 order/logistics 的权限点也勾给相应角色。

