# 自提还是外送 · mcd-pickup-vs-delivery

> 麦当劳同一份餐，自提和外送是两个价。这个 Skill 把两个价都算清楚，
> 连同「自己去拿要花多少时间和路费」一起交给 AI，让它替你决定。

麦当劳程序员创意开发大赛参赛作品 · 基于 [麦当劳中国 MCP](https://github.com/M-China/mcd-mcp-server) · 非麦当劳官方产品。

**只读。不下单、不领券、不抽奖、不写地址簿。**

---

## 它解决什么问题

你在麦当劳下单时看到的是最终金额，看不到这个数字是怎么来的：

- 同一份餐，外送的商品价本身就更贵（不同商品加价 0%～70%）
- 配送费 ¥6.00 按单收，不是按件
- 包装费按件收，但有上限
- 有些商品**外送渠道根本不卖**
- 同一个商品，两个渠道用**不同的商品编码**

这些叠在一起，用户没法判断「这单该不该叫外送」。

而且"该不该"没有标准答案——取决于你离店多远、时间值多少钱、下雨没有。
所以这个 Skill 不替你决定，它**提供可靠的数字，由调用它的 AI 结合情境判断**。

---

## 三分钟跑起来

### 前置

```bash
git clone <this-repo>
cd mcd-pickup-vs-delivery
echo "MCD_MCP_TOKEN=你的token" > .env     # .env 已 gitignore
```

Token 在 [麦当劳 MCP 控制台](https://open.mcd.cn/mcp) 申请。
零第三方依赖，只用 Python 3.9+ 标准库。

### 给 AI 用（默认输出 JSON）

```bash
python3 skills/mcd-pickup-vs-delivery/scripts/answer.py \
  --want 双层吉士汉堡 培根蔬萃双层牛堡 \
  --at 36.7600,118.0256
```

### 人看（表格形式）

```bash
python3 skills/mcd-pickup-vs-delivery/scripts/answer.py \
  --want 双层吉士汉堡 --at 36.7600,118.0256 --format text
```

```
  麦当劳淄博福园餐厅

  自提实付   ¥23.00
  外送实付   ¥32.10
  差额       ¥9.10
    商品溢价 ¥2.00
    配送费   ¥6.00
    包装费   ¥1.10

  距店 5291 米，各方式成本：

  方式              耗时      出行费      时间成本       合计
  ------------------------------------------------
  步行             70分     0.00     35.27    35.27
  骑行             21分     1.50     10.58    12.08
  公交/地铁          13分     2.00      6.61     8.61
  驾车             11分     8.17      5.29    13.47
  打车             12分    18.73      5.88    24.61
```

**注意：这里没有「推荐」。** 这是本 Skill 的核心设计，下一节说明。

### 解析两条渠道的商品编码

```bash
python3 skills/mcd-pickup-vs-delivery/scripts/resolve_channels.py \
  --want 巨无霸 锡兰红茶 培根安格斯厚牛堡
```

```
  商品                    自提编码        外送编码      自提价      外送价      匹配方式
  ------------------------------------------------------------------------------
  巨无霸                     1100        901100   ¥26.00   ¥29.00        name
  锡兰红茶                   3505        903505   ¥11.50   ¥12.00        name
  培根安格斯厚牛堡           511781        902060   ¥34.50   ¥37.00        name
```

### 挂到 Agent 上

`skills/mcd-pickup-vs-delivery/SKILL.md` 可直接给支持 Skill 的 Agent 使用
（WorkBuddy / Claude Code / Cursor 等），里面写了触发条件、参数和判断指引。

---

## 设计：脚本给数据，AI 做判断

```
用户：俩汉堡，该自提还是外送？
   ↓
AI：先问在哪儿、赶不赶时间
   ↓
脚本：吐结构化 JSON（金额拆解 + 五种出行方式成本）
   ↓
AI：结合情境判断 → 一句话回答
```

**为什么不让脚本直接给结论？** 同一个 ¥9.10 的差价，对不同人该给不同建议：

| 用户 | 该建议 |
|---|---|
| 赶时间的上班族 | 叫外送 |
| 学生、时间不值钱 | 骑车去拿 |
| 带娃的家长 | 打车去拿 |
| 下雨天 | 叫外送 |

这些情境脚本拿不到，**只有对话中的 AI 知道**。所以判断放在 SKILL.md 里
作为参考指引，不写死在代码里。

### 脚本输出什么

```json
{
  "pickup":   {"total_fen": 4750, "product_fen": 4750, "delivery_fen": 0,   "packing_fen": 0},
  "delivery": {"total_fen": 6020, "product_fen": 5200, "delivery_fen": 600, "packing_fen": 220},
  "gap": {
    "total_fen": 1270,
    "product_markup_fen": 450,
    "delivery_fee_fen": 600,
    "packing_fee_fen": 220,
    "reconciles": true
  },
  "distance_m": 5291,
  "legs": [
    {"mode": "walk",    "minutes": 70.5, "fare_yuan": 0.00,  "time_cost_yuan": 35.27, "total_cost_yuan": 35.27, "fare_estimated": true},
    {"mode": "bike",    "minutes": 21.2, "fare_yuan": 1.50,  "time_cost_yuan": 10.58, "total_cost_yuan": 12.08, "fare_estimated": true},
    {"mode": "transit", "minutes": 13.2, "fare_yuan": 2.00,  "time_cost_yuan": 6.61,  "total_cost_yuan": 8.61,  "fare_estimated": true},
    {"mode": "drive",   "minutes": 10.6, "fare_yuan": 8.17,  "time_cost_yuan": 5.29,  "total_cost_yuan": 13.47, "fare_estimated": true},
    {"mode": "taxi",    "minutes": 11.8, "fare_yuan": 18.73, "time_cost_yuan": 5.88,  "total_cost_yuan": 24.61, "fare_estimated": true}
  ],
  "short_trip_note": {"walk_recommended_under_m": 800, "motorized_unrealistic_under_m": 1200},
  "legs_source": "great-circle+estimated-duration"
}
```

### 数据带自校验

`gap.reconciles` 表示「商品溢价 + 配送费 + 包装费」能否还原总差额。
如果接口哪天变了导致算不平，这个字段变 `false`，SKILL.md 要求 AI
**不要给用户看这份数据**，改提示稍后重试。

### 缺数据就说缺

- 外送渠道无货 → 不输出 `delivery` 字段，只能自提
- 门店无坐标 → 不输出 `legs`，只能比价格
- 账号无地址 → 只给自提数据

脚本不会用估算填补空缺。

### 时薪可以变，结论跟着变

同一份数据，时薪 ¥5 / ¥30 / ¥150 会得出完全相反的建议：

| 时薪 | 外送差价 ¥10.10 时，哪些方式自提更省 |
|---:|---|
| ¥5 | 步行、骑行、公交、驾车（4 种） |
| ¥30 | 公交（1 种） |
| ¥150 | 一种都没有，全该叫外送 |

这是把判断权交给 AI 的直接好处——用户说一句"我时间不值钱"，结论就该变。

---

## 数据可信度

| 数据 | 来源 | 性质 |
|---|---|---|
| 两条渠道实付金额 | `calculate-price` | **官方权威**，整数「分」 |
| 两条渠道菜单 | `query-meals` | 官方实时接口 |
| 可配送门店与 `beCode` | `delivery-query-stores` | 官方实时接口 |
| 配送地址 | `delivery-query-addresses` | 官方实时接口，**只读** |
| 门店坐标 | `query-party-city` → `query-party-store` | 官方实时接口，实测坐标全部非零 |
| 步行/骑行/驾车/公交**时长** | 高德路径规划 API | 配置 `AMAP_KEY` 后为真实路况；未配置则按典型速度估算 |
| 出行**费用** | 按口径估算 | **全部是估算**，输出中标 `fare_estimated: true` |
| 时薪 | 用户可调 | 默认 ¥30，非实测工资 |

### 关于出行费用，必须说清楚

| 项 | 能不能拿到真实值 |
|---|---|
| 步行/骑行/驾车**时长** | ✅ 高德返回 |
| 公交**票价** | ❌ 高德不返回，按 ¥2 估算 |
| **打车费** | ❌ **普通开发者拿不到** |

打车估价接口属于高德的「网约车行业解决方案」，需要单独申请行业权限，
且要求调用方自己传入起步价、里程费等计价规则——它是计价引擎，不是查价接口。
高德 MCP 的「打车」能力是**唤起 App 叫车**，不是查询价格。

所以本项目**不声称拿到真实打车费**，一律按「起步 ¥13 含 3 公里、之后 ¥2.5/公里」估算并标注。

---

## 安全边界

这些是代码强制的，不是约定：

```python
FORBIDDEN_FRAGMENTS = ("create", "cancel", "draw", "auto-bind", "bind")

def call(self, name, arguments=None):
    if any(fragment in name.lower() for fragment in FORBIDDEN_FRAGMENTS):
        raise McpError(f"拒绝调用写操作工具 {name!r}")
```

- 全项目没有下单、取消、领券、抽奖的代码路径
- **不会写入账号地址簿**：`delivery-create-address` 被拒绝调用。
  MCP 没有删除地址的接口，一旦写入无法撤销，所以本工具只读用户已有地址。
- Token 只从环境变量或 `.env` 读取，`.env` 已列入 `.gitignore`，仓库内无任何真实凭证
- `tests/test_core.py::NoWriteCapabilityTests` 对这条边界做了单元测试

---

## 测试

```bash
python3 -m unittest discover -s tests -v
```

37 个测试，覆盖：

- 几何计算（球面距离、圆重叠、覆盖率）
- 数据清洗（虚拟门店过滤、坐标 (0,0) 排除）
- **渠道编码解析**（三种关系：`90` 前缀、同名异码、单边独有）
- **多渠道成本模型**（短距离可行性、时薪翻转、推荐与表格一致）
- 只读边界（写操作工具必须被拒绝）

---

## 项目结构

```
mcd-pickup-vs-delivery/
├── skills/mcd-pickup-vs-delivery/
│   ├── SKILL.md                    Skill 定义：触发条件、工作流、判断指引
│   └── scripts/
│       ├── answer.py               输出两条渠道的硬数据（JSON）
│       └── resolve_channels.py     解析两条渠道的商品编码
├── src/mcd_pickup_vs_delivery/
│   ├── mcp_client.py               极简只读 MCP 客户端
│   ├── multimodal.py               五种出行方式的时间与成本
│   ├── geo.py                      球面距离、圆重叠、网格采样
│   ├── stores.py                   门店快照、城市分组、测试门店过滤
│   ├── menu.py                     菜单缓存与官方核价
│   ├── amap.py                     高德增强层（可选）
│   ├── density.py                  门店密度分析
│   ├── branches.py                 多门店价格比对
│   ├── delivery.py                 渠道差价拆解
│   ├── strategy.py                 固定费摊薄与加价分析
│   ├── value.py                    价值比对模型
│   ├── pickup.py                   自提推荐
│   └── cli.py                      分析命令入口
├── data/
│   ├── stores_raw.json             4022 家门店快照（含坐标）
│   ├── menu_cache/                 已采样门店菜单（34 家）
│   └── channels/                   双渠道核价快照
├── tools/                          数据采集脚本（需要 token）
├── tests/                          单元测试
├── MCP_INTEGRATION.md              逐工具说明与调用流程
└── CONTEST_DECLARATION.md          参赛声明
```

---

## 局限

- **只对比一家门店**（默认最近的可配送门店）。想去更远但更便宜的门店，用 `--store` 切换。
- **两条渠道的菜单样本有限**：已采集 34 家门店，覆盖淄博 3 家可配送门店。
  要测其他城市需运行 `tools/collect_menus.py` 与 `tools/collect_channels.py`。
- **门店坐标来自派对业务接口**，因此覆盖的是「可办主题活动的门店」（4022 家），
  不是麦当劳全国门店总数。比值类结论不受影响，绝对数会偏低。
- **不含优惠券叠加**。若用户有券，先领券再跑本脚本，价格会自动包含券后价。
- **出行费用均为估算**，见上方「关于出行费用」。
- **打车费无法从高德获取真实值**，需申请网约车行业权限。

---

## 附录：开发中发现的数据规律

以下是在开发过程中用 MCP 只读接口实测到的现象。它们解释了本 Skill
为什么要做那些防护，也可作为 MCP 数据特性的参考。

复现命令（全部离线可跑，基于仓库内快照）：

```bash
python3 -m mcd_pickup_vs_delivery delivery     # 渠道差价明细
python3 -m mcd_pickup_vs_delivery strategy     # 固定费如何随件数摊薄
python3 -m mcd_pickup_vs_delivery density      # 全国门店密度
python3 -m mcd_pickup_vs_delivery price-tiers  # 同城不同门店的价格档位
```

### A1. 同一个商品，两条渠道编码不同

| 关系 | 例子 | 在 107 项自提商品中的占比 |
|---|---|---|
| 外送 = `90` + 自提 | 巨无霸 `1100` → `901100` | 15 项 |
| 编码无关，仅名字相同 | 安格斯堡 `511781` → `902060` | 46 项 |
| 编码恰好相同 | — | 19 项 |

**用错编码不会报错**，接口会返回 `productPrice = 0` 的假报价，只剩 ¥6 配送费，
看起来像"外送更便宜"。开发中曾因此把锡兰红茶算成「加价 −48%」。

### A2. 配送费按单收，包装费按件但有上限

同一家店、同一个商品，只改数量：

| 份数 | 配送费 | 包装费 | 隐形满减 | 实付 |
|---:|---:|---:|---:|---:|
| 1 | ¥6.00 | ¥1.90 | ¥0.00 | ¥33.40 |
| 3 | ¥6.00 | ¥3.00 | −¥2.70 | ¥85.50 |
| 10 | ¥6.00 | ¥6.00 | −¥13.00 | ¥267.00 |

- 配送费 ¥6.00 **点多少份都不变**
- 包装费涨到 ¥3.00 后封顶
- 存在**菜单里看不到的满减**：`discount` 字段只有 `calculate-price` 会算出来，任何菜单接口都不返回

### A3. 外送加价因商品而异

| 餐品 | 自提 | 外送商品价 | 加价 |
|---|---:|---:|---:|
| 燕麦奶铁 | ¥23.00 | ¥23.00 | 0% |
| 马苏拉里芝士条 | ¥11.00 | ¥11.90 | 8% |
| 高达吉士双牛堡 | ¥22.50 | ¥26.00 | 16% |
| 龙焰鸡腿堡 | ¥15.90 | ¥27.00 | **70%** |

咖啡类外送完全不加价，部分汉堡加价 70%。

### A4. 门店在市中心高度重叠

84 个门店数足够的城市中，**28% 的门店一公里内还有第二家麦当劳**
（福州 53%、北京 48%、上海 41%）。最近的一对相距 70 米
（武汉 HAPPY 站台餐厅 ↔ 武汉江汉路大汉口餐厅）。

### A5. 数据质量问题（已在代码中处理）

1. **11 家虚拟测试门店**（「虚拟门店请勿下单(悟空测试A1…)」）全在吴忠市同一坐标。
   未过滤会伪造出「吴忠市 82% 门店重叠」的假结论。`stores.py` 按名称过滤。
2. **`query-nearby-stores` 的经纬度恒为 `0.0`**，无法算距离。坐标一律取自派对链。
3. **`query-nearby-stores` 的 `distance` 不可跨调用比较**：它以关键词解析出的地标为原点，
   换关键词结果就变（同一门店：「人民广场」=144m，「西藏中路」=80m）。门店间比较一律用球面距离。

---

## 授权

代码 MIT。门店与餐品数据版权归麦当劳中国所有，本项目仅作技术演示。

*本项目为参赛作品，非麦当劳官方产品。餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。*
