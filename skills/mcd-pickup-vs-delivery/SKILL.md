---
name: mcd-pickup-vs-delivery
description: 麦当劳自提 vs 外送决策助手。当用户在麦当劳（麦当劳/麦麦/金拱门/麦乐送）下单前纠结"自己去拿还是叫外送""哪个更划算""外送值不值这个配送费"时使用；也适用于"我就想吃俩汉堡，怎么买合适""这个距离叫外送亏不亏"这类诉求。它用官方 calculate-price 同时算出两条渠道的实付金额，把差额拆成商品加价/配送费/包装费，再结合用户的步行距离和时间价值给出一句话结论。只读，不下单。
agent_created: true
---

# 自提还是外送

## 这个 Skill 解决什么问题

麦当劳同一份餐，自提和外送是两个价格。用户看到的只有最终金额，看不到差额从哪来：

- 商品本身在外送渠道就更贵（不同商品加价 0%~70%）
- 固定配送费 ¥6.00（每单一次，不是每件）
- 包装费按件收，但会封顶
- 有些商品**外送渠道根本不卖**

这些差异叠加起来，用户很难判断"这单到底该不该叫外送"。
本 Skill 把这件事一次算清楚，并给出可执行的结论。

## 何时使用

- "我想吃俩汉堡，自提还是外送？"
- "外送配送费 6 块，值不值？"
- "我家离麦当劳 1.5 公里，走过去还是叫外送？"
- "外送怎么比店里贵这么多？"
- "这个单叫外送亏不亏？"

## 工作流

### 第 1 步：搞清楚三件事

缺哪个问哪个，不要一次问一长串：

1. **想吃什么**（可模糊，如"俩汉堡""巨无霸""随便一个套餐"）
2. **在哪儿**（外送必须有配送地址；算出行成本需要用户位置）
3. **时间值多少钱**（可选，默认 ¥30/小时；用户说"我时间不值钱"就调低）

### 第 2 步：跑脚本拿数据

```bash
python3 skills/mcd-pickup-vs-delivery/scripts/answer.py \
  --want 双层吉士汉堡 培根蔬萃双层牛堡 \
  --at 36.8076,118.0256 \
  --hourly-rate 30
```

默认输出 JSON。加 `--format text` 输出人类可读表格（调试用）。

| 参数 | 含义 | 默认 |
|---|---|---|
| `--want` | 想吃的餐品名，可多个 | 必填 |
| `--at` | 用户位置 `lat,lng`；不传则只比价格、不算出行成本 | 空 |
| `--hourly-rate` | 用户时间价值（元/小时） | 30 |
| `--store` | 用第几家可配送门店 | 0 |
| `--format` | `json` / `text` | `json` |

### 第 3 步：你自己判断，然后讲给用户

**脚本不给结论。** 你拿到 JSON 后：

1. 读 `gap` 看差额构成，找出最值得说的一点
2. 读 `legs` 找用户能接受的出行方式
3. 结合用户情境（赶时间？带娃？下雨？）下判断
4. 先给一句话结论，用户追问再展开细节

判断方法见下一节。

## 数据长什么样（JSON 节选）

```json
{
  "pickup":   {"total_fen": 4750},
  "delivery": {"total_fen": 6020},
  "gap": {
    "total_fen": 1270,
    "product_markup_fen": 450,
    "delivery_fee_fen": 600,
    "packing_fee_fen": 220,
    "reconciles": true
  },
  "distance_m": 5291,
  "legs": [
    {"mode": "walk",    "label": "步行",      "minutes": 70.5, "fare_yuan": 0.00,  "time_cost_yuan": 35.27, "total_cost_yuan": 35.27, "fare_estimated": true},
    {"mode": "bike",    "label": "骑行",      "minutes": 21.2, "fare_yuan": 1.50,  "time_cost_yuan": 10.58, "total_cost_yuan": 12.08, "fare_estimated": true},
    {"mode": "transit", "label": "公交/地铁", "minutes": 13.2, "fare_yuan": 2.00,  "time_cost_yuan": 6.61,  "total_cost_yuan": 8.61,  "fare_estimated": true},
    {"mode": "drive",   "label": "驾车",      "minutes": 10.6, "fare_yuan": 8.17,  "time_cost_yuan": 5.29,  "total_cost_yuan": 13.47, "fare_estimated": true},
    {"mode": "taxi",    "label": "打车",      "minutes": 11.8, "fare_yuan": 18.73, "time_cost_yuan": 5.88,  "total_cost_yuan": 24.61, "fare_estimated": true}
  ],
  "short_trip_note": {"walk_recommended_under_m": 800, "motorized_unrealistic_under_m": 1200},
  "legs_source": "great-circle+estimated-duration"
}
```

## 脚本只给数据，判断由你做

**这是本 Skill 最重要的设计约定。**

脚本 `answer.py` **不做推荐、不下结论**。它只输出结构化的硬数据：

```json
{
  "pickup":   {"total_fen": 4750, "product_fen": 4750, "delivery_fen": 0,  "packing_fen": 0},
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
    {"mode": "walk",    "minutes": 70.5, "fare_yuan": 0.00,  "time_cost_yuan": 35.27, "total_cost_yuan": 35.27},
    {"mode": "bike",    "minutes": 21.2, "fare_yuan": 1.50,  "time_cost_yuan": 10.58, "total_cost_yuan": 12.08},
    {"mode": "transit", "minutes": 13.2, "fare_yuan": 2.00,  "time_cost_yuan": 6.61,  "total_cost_yuan": 8.61},
    {"mode": "drive",   "minutes": 10.6, "fare_yuan": 8.17,  "time_cost_yuan": 5.29,  "total_cost_yuan": 13.47},
    {"mode": "taxi",    "minutes": 11.8, "fare_yuan": 18.73, "time_cost_yuan": 5.88,  "total_cost_yuan": 24.61}
  ]
}
```

**为什么这样设计**：同一个 ¥12.70 的差价，对不同用户该给不同建议——

- 赶时间的上班族 → 叫外送
- 学生、时间不值钱 → 骑车去拿
- 带娃的家长 → 打车去拿
- 下雨天 → 叫外送

这些判断需要用户情境，脚本拿不到。**所以由你来判断。**

## 你可以怎么判断（参考，不是硬规则）

### 1. 先看 `gap.total_fen` 和它的构成

```
商品溢价 = gap.product_markup_fen    ← 这部分的钱买不到任何服务
配送费   = gap.delivery_fee_fen      ← 这部分的钱买的是"不用出门"
包装费   = gap.packing_fee_fen
```

若 `商品溢价` 占差额一半以上，可以直接告诉用户：
**"你多付的钱里有一大半不是跑腿费，是同一样东西在外送渠道卖得更贵。"**
这通常是最让人意外的信息。

### 2. 再看 `legs`，找时间成本能被接受的方式

`total_cost_yuan` = 出行费 + 时间成本（时间按用户时薪折算）。

- 有某一行 `total_cost_yuan < gap.total_fen / 100` → 那种方式自提更划算
- 所有行都更高 → 外送划算

### 3. 短距离必须讲可行性，不能只比数字

脚本会给出 `short_trip_note`：

- **800 米内**：只建议走路。不要推荐骑车或坐车。
- **1200 米内**：只建议走路或骑车。**公交/打车在这段距离上数学可能更便宜，但现实没人这么干。**
- 超过 1200 米：五种方式都可以摆出来

**这条很重要。** 单纯按成本最小化，会得出"1 公里坐公交"这种荒谬建议。

### 4. 时薪可以问用户

默认 ¥30/小时。如果用户说"我时间不值钱"，把 `--hourly-rate` 调到 5；
如果说"我时薪很高"，调高。**时薪变了，结论会翻转**，这是合理的。

### 5. 数据可信度字段要尊重

| 字段 | 含义 | 你怎么用 |
|---|---|---|
| `gap.reconciles` | 三项拆解能否还原总差额 | 若为 `false`，说明数据异常，**不要给用户看**，改提示稍后重试 |
| `fare_estimated` | 出行费是否为估算 | 为 `true` 时，**说"大约"而不是"就是"** |
| `legs_source` | `amap-duration+estimated-fare` 或 `great-circle+estimated-duration` | 前者时长为真实路况，后者是估算 |
| `items[].match` | 编码匹配方式 | `prefix` 表示靠 90 前缀推定，**建议核对名称** |

### 6. 缺数据就明说，不要补

- 外送无货 → 脚本不给 `delivery` 字段，说明只能自提，**不要说"外送贵"**
- 没有坐标 → 不给 `legs`，只能比价格
- 没有配送地址 → 只给自提数据

## 数据来源与纪律

| 数据 | 来源 |
|---|---|
| 可配送门店 | `delivery-query-stores` |
| 两条渠道菜单 | `query-meals`（orderType 1 / 2） |
| **编码对应关系** | `resolve_channels.py`（按名字 + 前缀兜底） |
| **实付金额** | `calculate-price`（唯一权威，单位「分」） |

**四条硬纪律：**

1. **金额只认 `calculate-price`**。菜单里的 `currentPrice` 是「元」字符串，
   `calculate-price` 返回的是「分」整数，两者单位不同，绝不可混用。
   （单位搞错会让价格差 100 倍。）
2. **两条渠道各自用自己目录里的编码**。不能拿自提编码去查外送，反之亦然。
   出错了也不会报错，只会静默返回假报价。
3. **外送渠道没有的商品直接排除**。这类商品接口仍返回 ¥6 配送费但
   `productPrice = 0`，不做判断会算出"加价 -48%"这种荒谬结果。
4. **拿不到距离就不说距离**。不编造步行时间。

## 只读承诺

本 Skill **不会下单、不会取消订单、不会领券、不会抽奖、不会写入收货地址**。
所有调用都是查询。

## 已知限制

- 只对比**一家门店**（默认最近的可配送门店）。如果用户想去更远但更便宜的门店，用 `--store` 切换。
- 外送与自提的**商品目录不完全重合**（实测 107 项 vs 118 项，交集仅 19 项），
  所以脚本会明确提示"外送没有 XXX"，不会假装有货。
- 不含优惠券叠加。如果用户有券，先领券再跑本脚本，价格会自动包含券后价。
- 步行时间按 75 米/分钟估算，不是真实路径。要真实路线需配置高德 Key（见仓库 README）。
