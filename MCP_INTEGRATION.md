# MCP 集成说明 · mcd-delivery-xray

本项目实际使用的麦当劳 MCP Server、Tool、调用流程与业务价值。

---

## 一、接入信息

| 项 | 值 |
|---|---|
| MCP Server | 麦当劳中国 MCP（`mcd-mcp`） |
| 接入地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 鉴权 | `Authorization: Bearer <MCD_MCP_TOKEN>` |
| 协议版本 | `2025-06-18` |
| 实测服务端版本 | `mcd-mcp v1.0.0`，`tools/list` 返回 **35 个工具** |

Token 只从环境变量或仓库根目录的 `.env` 读取（`.env` 已列入 `.gitignore`，仓库内不含任何真实凭证）。
仓库内提供 `mcp-config.example.json`，仅含环境变量占位符。

---

## 二、逐工具说明

> 下表只列出本项目**真实调用**的工具，并写明调用时机与在业务中的角色。
> 本项目不使用 `create-order` / `cancel-order` / `auto-bind-coupons` / `draw-lottery` / `mall-create-order` / `party-order-create` 等任何写操作工具。

### 2.1 门店坐标链（核心数据来源）

| Tool | 调用时机 | 在项目中的作用 | 关键返回字段 |
|---|---|---|---|
| `mall-points-products` | 采集起点，`catRuleIds=1>6` | 取得可办主题活动的商品 `spuId`，这是整条坐标链的入口 | `spuId`、`spuName`、`shopId` |
| `query-party-city` | 拿到 `spuId` 后 | 返回该商品可参与的城市列表，**附带城市级真实经纬度** | `code`、`name`、`latitude`、`longitude` |
| `query-party-store` | 拿到城市 `code` 后 | 返回该城市可办活动的门店列表，**附带门店级真实经纬度** | `code`、`name`、`address`、`latitude`、`longitude`、`businessStatus` |

**为什么必须走这条链路**：`query-nearby-stores` 返回的 `latitude` / `longitude` **恒为 `0.0`**（实测），
无法用于任何几何计算。派对链是当前 MCP 中**唯一**公开真实门店坐标的只读入口。
采集结果 4022 家门店 / 277 个城市，坐标全部非零。

### 2.2 菜单与价格链

| Tool | 调用时机 | 在项目中的作用 | 关键返回字段 |
|---|---|---|---|
| `query-meals` | 已知 `storeCode` 时 | 拉取该门店可售餐品，建立「门店 → 商品编码 → 标价」映射 | `data.categories[].meals[].code`、`data.meals{code}.name/currentPrice` |
| `calculate-price` | 需要权威金额时 | **所有对外展示的金额都以它为准**，返回整数「分」 | `price`、`productPrice`、`deliveryPrice`、`packingPrice` |
| `delivery-query-addresses` | 外送对比前 | 读取账号已有配送地址，**不创建地址** | `addressId`、`fullAddress` |
| `delivery-query-stores` | 拿到 `addressId` 后 | 查该地址可配送门店，取得外送 `beCode` | `storeCode`、`beCode`、`storeName` |
| `list-nutrition-foods` | 营养维度分析 | 为餐品补充能量/蛋白质等官方营养值 | 能量、蛋白质、脂肪、碳水、钠、钙 |

### 2.3 本项目未使用但调研过的工具

`query-nearby-stores`（坐标恒为 0.0，仅用其 `distance` 做交叉校验）、`order-list`、`query-my-account`、
`available-coupons`、`query-my-coupons`、`query-store-coupons`、`campaign-calendar`、`now-time-info` 等。

---

## 三、调用流程

### 3.1 门店坐标采集（离线，可复现）

```
mall-points-products(catRuleIds="1>6")
        │  得到 spuId 列表
        ▼
query-party-city(spuId)                    ── 每个 spuId 一份城市表
        │  城市 code + 城市经纬度
        ▼
query-party-store(spuId, code, lat, lng)   ── 逐城市拉门店
        │  门店 code + 门店经纬度
        ▼
data/stores_raw.json                        ── 提交进仓库，供离线分析
```

采集脚本：`tools/collect_stores.py`。限流保护：每次请求间隔 0.12s，远低于官方 600 次/分钟上限。

### 3.2 自提 vs 外送对比（需真实地址）

```
delivery-query-addresses()
        │  addressId（若为空则明确报错，不会替你创建）
        ▼
delivery-query-stores(addressId, beType=2)
        │  storeCode + beCode
        ▼
query-meals(storeCode, orderType=1, beType=1)          ── 到店目录
query-meals(storeCode, orderType=2, beType=2, beCode)  ── 外送目录
        │  取两边商品编码的交集（实测 107 / 118，交集仅 19）
        ▼
calculate-price(orderType=1, beType=1)                 ── 自提权威价
calculate-price(orderType=2, beType=2, beCode)         ── 外送权威价
        │
        ▼
拆解：商品价差 / 配送费 / 包装费 → 差额与幅度
```

### 3.3 自提门店比价（离线）

```
data/stores_raw.json + data/menu_cache/*.json
        │  统一球面距离排序
        ▼
branches: 同一份餐在附近各门店的标价与菜品数
        ▼
pickup: 距离 vs 价格的权衡结论
```

### 3.4 门店密度分析（离线）

```
data/stores_raw.json
        │  逐店计算最近邻距离
        ▼
density: 1 公里内还有第二家麦当劳的门店占比、城市排行、全国最贴近门店对
```

---

## 四、业务价值

| 价值 | 说明 |
|---|---|
| **量化了外送溢价结构** | 把一个汉堡的差价拆成「商品提价 + 配送费 + 包装费」三项，用官方 `calculate-price` 的原始字段直接得出，不是估算 |
| **揭示了目录不重合** | 外送与到店商品目录实测只有 19 个编码重合，这解释了为什么用户常觉得「外送上没有我想吃的」 |
| **量化了门店空间重叠** | 3334 家可比门店中 925 家（28%）一公里内另有门店，为选址与运营研究提供公开可复现的口径 |
| **给出了可执行的取舍** | 「多走 X 米 / 多花 Y 元」把距离与价格放进同一个结论里 |
| **全程只读** | 不下单、不领券、不抽奖、不写地址簿，可安全地在任何账号上运行 |

---

## 五、工程细节

| 项 | 说明 |
|---|---|
| 依赖 | **零第三方依赖**，仅 Python 3.9+ 标准库 |
| 响应解析 | 服务端在 SSE 与普通 JSON 两种形态间切换；`mcp_client.py` 同时兼容，并优先读 `structuredContent`，回退解析 `content[].text` 中的 `## Original Response` |
| 单位 | MCP 金额返回整数「分」，项目内部一律用分计算，仅在展示层换算为元，避免浮点误差 |
| 缓存 | 菜单缓存提交进仓库，保证无 Token 也能复现全部离线分析 |
| 数据清洗 | 自动过滤 11 家「虚拟门店请勿下单(悟空测试…)」；它们坐标相同，不过滤会伪造出「吴忠市 82% 门店重叠」的假结论 |
| 限流 | 采集脚本 0.12–0.15s 间隔，规避 429 |

---

## 六、只读边界是如何被强制的

不是靠约定，而是靠代码：

```python
FORBIDDEN_FRAGMENTS = ("create", "cancel", "draw", "auto-bind", "bind")

def call(self, name, arguments=None):
    if any(fragment in name.lower() for fragment in FORBIDDEN_FRAGMENTS):
        raise McpError(f"拒绝调用写操作工具 {name!r}")
```

`tests/test_core.py::NoWriteCapabilityTests` 对这条边界做了单元测试，
确保后续改动不会意外引入写操作路径。
