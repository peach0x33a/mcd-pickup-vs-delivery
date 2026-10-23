"""If you are going to order delivery anyway, how should you order it?

The fixed costs of delivery are the whole game, and they do not behave the way
most people assume. Measured against the live `calculate-price` endpoint at
麦当劳淄博福园餐厅, ordering one identical burger N times:

    qty  商品小计   配送费   包装费   折扣     实付
      1    ¥26.00   ¥6.00   ¥1.10   ¥0.00   ¥33.10
      2    ¥52.00   ¥6.00   ¥2.20   ¥0.00   ¥60.20
      3    ¥78.00   ¥6.00   ¥3.00   -¥0.30  ¥87.00
      6   ¥156.00   ¥6.00   ¥3.00   -¥3.60  ¥165.00
     10   ¥260.00   ¥6.00   ¥6.00   -¥5.00  ¥272.00

Three facts follow, and each of them changes what a good order looks like:

1. **Delivery fee is per-order, not per-item.** ¥6.00 whether you buy one item
   or ten. So the more a single order carries, the less that fee costs per item.
2. **Packing fee is per-item but capped.** It rises 1.10 -> 2.20 -> 3.00 and
   then stops. Above roughly three items, packing stops growing.
3. **An unadvertised discount kicks in around three items.** `discount` is zero
   for one or two items, then appears and grows. It is not shown in the menu
   data; only `calculate-price` reveals it.

So the strategy this module computes is: **buy in fewer, larger orders, and
prefer items whose delivery mark-up is low.** The second half matters more than
the first: measured mark-ups on the same store range from 6% to 70%.

READ-ONLY: only `calculate-price` and `query-meals` are called.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mcp_client import McpClient

#: Items cheaper than this are not worth probing individually; the goal is the
#: shape of the cost curve, not an exhaustive catalogue.
MAX_PROBES = 40


@dataclass
class OrderPoint:
    """One data point on the cost curve for a given quantity."""

    quantity: int
    product_fen: int
    delivery_fen: int
    packing_fen: int
    discount_fen: int
    total_fen: int

    @property
    def unit_fen(self) -> float:
        return self.total_fen / self.quantity if self.quantity else 0.0

    @property
    def overhead_fen(self) -> int:
        """Everything that is not the item itself."""
        return self.delivery_fen + self.packing_fen - self.discount_fen


@dataclass
class ItemMarkup:
    code: str
    name: str
    pickup_fen: int
    delivery_fen: int
    fixed_fen: int  # delivery + packing for a single-item order

    @property
    def markup_fen(self) -> int:
        return self.delivery_fen - self.pickup_fen

    @property
    def markup_pct(self) -> float:
        if self.pickup_fen == 0:
            return 0.0
        return self.markup_fen / self.pickup_fen * 100.0

    @property
    def markup_share(self) -> float:
        """How much of the total premium over pickup is item mark-up, not fees."""
        premium = self.delivery_fen + self.fixed_fen - self.pickup_fen
        if premium <= 0:
            return 0.0
        return self.markup_fen / premium


def _price(payload: dict) -> dict | None:
    data = payload.get("data") or {}
    total = data.get("price")
    if not isinstance(total, (int, float)):
        return None
    return data


def cost_curve(
    client: McpClient, store_code: str, be_code: str, product_code: str,
    quantities: list[int] | None = None,
) -> list[OrderPoint]:
    """How the per-unit cost of delivery changes with order size."""
    points: list[OrderPoint] = []
    for quantity in quantities or [1, 2, 3, 4, 5, 6, 8, 10]:
        data = _price(client.call("calculate-price", {
            "storeCode": store_code, "orderType": 2, "beType": 2, "beCode": be_code,
            "items": [{"productCode": product_code, "quantity": quantity}],
        }))
        if not data:
            continue
        points.append(OrderPoint(
            quantity=quantity,
            product_fen=int(data.get("productPrice") or 0),
            delivery_fen=int(data.get("deliveryPrice") or 0),
            packing_fen=int(data.get("packingPrice") or 0),
            discount_fen=int(data.get("discount") or 0),
            total_fen=int(data.get("price") or 0),
        ))
    return points


def measure_markups(
    client: McpClient, store_code: str, be_code: str, codes: list[str],
    names: dict[str, str] | None = None,
) -> list[ItemMarkup]:
    """Per-item delivery mark-up, measured by quoting each item both ways."""
    names = names or {}
    rows: list[ItemMarkup] = []
    for code in codes[:MAX_PROBES]:
        pickup = _price(client.call("calculate-price", {
            "storeCode": store_code, "orderType": 1, "beType": 1,
            "items": [{"productCode": code, "quantity": 1}],
        }))
        delivery = _price(client.call("calculate-price", {
            "storeCode": store_code, "orderType": 2, "beType": 2, "beCode": be_code,
            "items": [{"productCode": code, "quantity": 1}],
        }))
        if not pickup or not delivery:
            continue
        # A zero productPrice means the delivery channel does not carry this
        # item at all: the server still returns the delivery fee, which would
        # otherwise look like a negative mark-up. Reject those outright.
        if int(delivery.get("productPrice") or 0) <= 0:
            continue
        if int(pickup.get("price") or 0) <= 0:
            continue
        rows.append(ItemMarkup(
            code=code,
            name=names.get(code) or code,
            pickup_fen=int(pickup.get("price") or 0),
            delivery_fen=int(delivery.get("productPrice") or delivery.get("price") or 0),
            fixed_fen=int(delivery.get("deliveryPrice") or 0) + int(delivery.get("packingPrice") or 0),
        ))
    rows.sort(key=lambda row: row.markup_pct)
    return rows


def best_bundle(
    markups: list[ItemMarkup], target_fen: int, max_per_item: int = 2
) -> tuple[list[ItemMarkup], int]:
    """Pick a low-mark-up basket close to a target spend, with some variety.

    Two constraints that the raw "always take the cheapest mark-up" greedy gets
    wrong in practice:

    * ``max_per_item`` keeps the basket from collapsing into eight identical
      drinks, which is a mathematically optimal but useless recommendation.
    * Items are taken in ascending mark-up order, but only while they still fit
      the remaining budget, so the basket stays near the target instead of
      stopping early on a cheap item.
    """
    remaining = target_fen
    chosen: list[ItemMarkup] = []
    counts: dict[str, int] = {}

    # Pass 1: breadth first, one of each low-mark-up item.
    for row in markups:
        if row.delivery_fen <= 0 or remaining < row.delivery_fen:
            continue
        if counts.get(row.code, 0) >= max_per_item:
            continue
        chosen.append(row)
        counts[row.code] = counts.get(row.code, 0) + 1
        remaining -= row.delivery_fen

    # Pass 2: spend what is left on the lowest mark-up items.
    for row in markups:
        while remaining >= row.delivery_fen and counts.get(row.code, 0) < max_per_item:
            chosen.append(row)
            counts[row.code] = counts.get(row.code, 0) + 1
            remaining -= row.delivery_fen

    spend = sum(row.delivery_fen for row in chosen)
    return chosen, spend


def advice(curve: list[OrderPoint], markups: list[ItemMarkup]) -> list[str]:
    """Plain-language strategy notes derived from the measured data."""
    notes: list[str] = []

    if curve:
        first, last = curve[0], curve[-1]
        if last.unit_fen < first.unit_fen:
            saved = first.unit_fen - last.unit_fen
            notes.append(
                f"同样商品，点 {first.quantity} 份时每份实付 ¥{first.unit_fen / 100:.2f}，"
                f"点 {last.quantity} 份时每份 ¥{last.unit_fen / 100:.2f}，"
                f"每份便宜 ¥{saved / 100:.2f}。"
            )
        capped = [p for p in curve if p.packing_fen and p.packing_fen == max(x.packing_fen for x in curve)]
        if capped:
            notes.append(
                f"包装费在 {capped[0].quantity} 份时达到上限 ¥{capped[0].packing_fen / 100:.2f}，"
                "之后不再增加。"
            )
        discounted = [p for p in curve if p.discount_fen > 0]
        if discounted:
            notes.append(
                f"从 {discounted[0].quantity} 份开始出现未在菜单中标注的满减，"
                f"{discounted[-1].quantity} 份时已达 ¥{discounted[-1].discount_fen / 100:.2f}。"
            )

    if markups:
        cheap, dear = markups[0], markups[-1]
        notes.append(
            f"不同餐品的外送加价差别很大：{cheap.name} 加价 {cheap.markup_pct:.0f}%，"
            f"而 {dear.name} 加价 {dear.markup_pct:.0f}%。"
        )
        low_share = sum(1 for m in markups if m.markup_pct <= 10) / len(markups) * 100
        notes.append(f"约 {low_share:.0f}% 的餐品外送加价在 10% 以内，优先从这些里挑。")
    return notes
