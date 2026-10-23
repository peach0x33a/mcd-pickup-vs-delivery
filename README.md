# 自提还是外送 · mcd-pickup-vs-delivery

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue)
![Tests](https://img.shields.io/badge/Tests-37%20passing-brightgreen)

> 同一份麦当劳，自提和外送是两个价——差价藏在商品溢价、配送费、包装费里。
> 这个 Skill 用官方接口把两条渠道的实付算出来、拆清楚，再算上「自己去拿一趟要花多少时间和路费」，
> 把硬数据交给 AI，由它结合你的情境给出建议。

麦当劳程序员创意开发大赛参赛作品 · 基于 [麦当劳中国 MCP](https://github.com/M-China/mcd-mcp-server) · 非官方产品。
**只读：不下单、不领券、不抽奖、不写地址簿。** MCP 调用细节见 [MCP_INTEGRATION.md](MCP_INTEGRATION.md)。

## 它解决什么问题

下单时你只看到最终金额，看不到它是怎么来的：

- 同一份餐，外送的商品价本身就更贵（不同商品加价 0%～70%）
- 配送费 ¥6.00 按单收，包装费按件收、有上限
- 有些商品外送渠道根本不卖；同一个商品，两条渠道用不同编码

而「该不该叫外送」没有标准答案——取决于你离店多远、时间值多少钱、外面下没下雨。
所以脚本不替你做决定：**它只给可靠的数字，判断交给懂情境的 AI**。

## 三分钟跑起来

```bash
git clone <this-repo> && cd mcd-pickup-vs-delivery
echo "MCD_MCP_TOKEN=你的token" > .env   # Token 在 open.mcd.cn/mcp 申请；零第三方依赖
```

```bash
python3 skills/mcd-pickup-vs-delivery/scripts/answer.py \
  --want 双层吉士汉堡 --at 36.7600,118.0256 --format text
```

```
  麦当劳淄博福园餐厅

  自提实付   ¥23.00
  外送实付   ¥32.10
  差额       ¥9.10（商品溢价 ¥2.00 + 配送费 ¥6.00 + 包装费 ¥1.10）

  距店 5291 米，各方式成本：
  方式              耗时      出行费      时间成本       合计
  ------------------------------------------------
  步行             70分      0.00      35.27      35.27
  骑行             21分      1.50      10.58      12.08
  公交/地铁        13分      2.00       6.61       8.61
  驾车             11分      8.17       5.29      13.47
  打车             12分     18.73       5.88      24.61
```

**注意：输出里没有「推荐」。** 该不该叫外送取决于用户的情境——赶不赶时间、
时间值多少钱、天气如何，这些脚本拿不到，只有对话中的 AI 知道。
判断指引写在 `skills/mcd-pickup-vs-delivery/SKILL.md`，可直接挂到
WorkBuddy / Claude Code / Cursor 等支持 Skill 的 Agent。

默认输出 JSON 供 AI 消费；辅助脚本 `resolve_channels.py` 解析两条渠道的商品编码
对应关系（如自提 `1100` ↔ 外送 `901100`）。

## 工程要点

- **金额只认 `calculate-price`**（整数「分」，官方权威）。菜单里的 `currentPrice` 是
  「元」字符串，两者单位混用会让价差 100 倍
- **数据带自校验**：`gap.reconciles` 为 `false` 时说明拆解对不上总差额，
  AI 不应把这份数据给用户看
- **缺数据就明说**：外送无货就不输出 `delivery`，门店无坐标就不输出 `legs`，
  绝不用估算填补空缺
- **只读由代码强制**：工具名含 `create/cancel/draw/auto-bind/bind` 直接抛异常，
  `tests/test_core.py::NoWriteCapabilityTests` 有单元测试守着
- 出行**时长**可用高德 API（配 `AMAP_KEY` 后为真实路况）；出行**费用**全是估算，
  输出标 `fare_estimated: true`。打车费普通开发者拿不到真实值（需网约车行业权限），
  一律按「起步 ¥13 含 3 公里、之后 ¥2.5/公里」估算并标注

```bash
python3 -m unittest discover -s tests   # 37 个测试
```

## 已知局限

- 只对比一家门店（默认最近的可配送门店），可用 `--store` 切换
- 菜单样本 34 家门店；门店坐标覆盖「可办主题活动的门店」4022 家，
  比值类结论不受影响，绝对数偏低
- 不含优惠券叠加（有券先领再跑，价格自动含券后价）；出行费用均为估算

## 附录：实测到的数据规律

以下全部离线可复现（基于仓库内快照）：

```bash
python3 -m mcd_pickup_vs_delivery delivery     # 渠道差价明细
python3 -m mcd_pickup_vs_delivery strategy     # 固定费如何随件数摊薄
python3 -m mcd_pickup_vs_delivery density      # 全国门店密度
python3 -m mcd_pickup_vs_delivery price-tiers  # 同城不同门店的价格档位
```

**A1. 同一个商品，两条渠道编码不同。** 107 项自提商品中：15 项外送编码 = `90` + 自提编码，
46 项仅名字相同，19 项编码恰好相同。**用错编码不会报错**，只会返回 `productPrice = 0`
的假报价——开发中曾因此把锡兰红茶算成「加价 −48%」。

**A2. 配送费按单收，包装费按件封顶。** 同一商品点 1 / 3 / 10 份：配送费恒为 ¥6.00，
包装费到 ¥3.00 封顶，还有菜单里看不到的隐形满减（只有 `calculate-price` 会算出来）。

**A3. 外送加价因商品而异。** 咖啡类 0%，马苏拉里芝士条 8%，高达吉士双牛堡 16%，
龙焰鸡腿堡 **70%**。

**A4. 门店在市中心高度重叠。** 84 个门店数足够的城市中，28% 的门店一公里内还有
第二家麦当劳（福州 53%、北京 48%、上海 41%）；最近一对相距 70 米（武汉）。

**A5. 数据质量坑（已在代码中处理）。** 11 家虚拟测试门店全在吴忠市同一坐标，
不过滤会伪造「吴忠 82% 重叠」的假结论；`query-nearby-stores` 经纬度恒为 `0.0`，
其 `distance` 以关键词解析出的地标为原点，不可跨调用比较——门店间比较一律用球面距离。

## 项目结构

```
skills/mcd-pickup-vs-delivery/   Skill 定义（SKILL.md）与脚本
src/mcd_pickup_vs_delivery/      只读 MCP 客户端与各分析模块
data/                            4022 家门店坐标、34 家门店菜单缓存、双渠道核价快照
tools/                           数据采集脚本（需要 token）
tests/                           单元测试
```

## 授权

代码 MIT。门店与餐品数据版权归麦当劳中国所有，本项目仅作技术演示。
餐品信息、价格及供应状态以麦当劳官方渠道的实时结果为准。
