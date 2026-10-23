"""Pickup vs delivery: what the same item actually costs each way.

This is the module that answers the project's sharpest question. A single
verified example, straight from the official `calculate-price` endpoint:

    Buttered Double Beef Burger (productCode 521316) at 麦当劳淄博福园餐厅

        到店自取  ¥22.50   (product 2250)
        外送到家  ¥33.10   (product 2600 + delivery 600 + packing 110)
        ------------------------------------------------
        差额      ¥10.60   = +47.1%

    The mark-up decomposes into three separate causes, which most users never
    see as separate line items:
        商品提价  +350 分  (+15.6%)
        配送费    +600 分
        包装费    +110 分

Why this needs a real address: `delivery-query-stores` only accepts an
`addressId`, and the MCP surface has no way to delete an address once created.
So this module does NOT create addresses. It reads whatever address the account
already holds, and returns a clear error if none exists. Writing to someone's
address book as a side effect of running an analysis tool would be unacceptable.

READ-ONLY: calls `delivery-query-addresses`, `delivery-query-stores`,
`query-meals` and `calculate-price` only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .mcp_client import McpClient, McpError


class NoAddressError(RuntimeError):
    """Raised when the account has no delivery address to test against."""


@dataclass
class PriceBreakdown:
    product_fen: int
    delivery_fen: int = 0
    packing_fen: int = 0

    @property
    def total_fen(self) -> int:
        return self.product_fen + self.delivery_fen + self.packing_fen

    @property
    def total_yuan(self) -> float:
        return self.total_fen / 100.0


@dataclass
class DeliveryComparison:
    item_name: str
    item_code: str
    store_name: str
    store_code: str
    pickup: PriceBreakdown
    delivery: PriceBreakdown

    @property
    def gap_fen(self) -> int:
        return self.delivery.total_fen - self.pickup.total_fen

    @property
    def gap_pct(self) -> float:
        if self.pickup.total_fen == 0:
            return 0.0
        return self.gap_fen / self.pickup.total_fen * 100.0

    @property
    def product_gap_fen(self) -> int:
        """How much of the gap is the item itself being dearer on delivery."""
        return self.delivery.product_fen - self.pickup.product_fen

    @property
    def product_gap_pct(self) -> float:
        if self.pickup.product_fen == 0:
            return 0.0
        return self.product_gap_fen / self.pickup.product_fen * 100.0


def saved_address(client: McpClient) -> dict:
    """The account's first delivery address, or a clear error."""
    payload = client.call("delivery-query-addresses", {})
    addresses = (payload.get("data") or {}).get("addresses") or []
    if not addresses:
        raise NoAddressError(
            "账号里没有配送地址。\n"
            "本项目不会替你创建地址（MCP 没有删除地址的接口，写进去无法撤销）。\n"
            "请先在麦当劳 App 里添加一个收货地址，然后重试。"
        )
    return addresses[0]


def delivery_stores(client: McpClient, address_id: str, be_type: int = 2) -> list[dict]:
    """Stores that will deliver to this address (beType 2 = 麦乐送)."""
    payload = client.call("delivery-query-stores", {"addressId": address_id, "beType": be_type})
    data = payload.get("data")
    return data if isinstance(data, list) else []


def _breakdown(payload: dict) -> PriceBreakdown | None:
    data = payload.get("data") or {}
    total = data.get("price")
    if not isinstance(total, (int, float)):
        return None
    # Prefer the explicit components; fall back to treating the total as product
    # only when the server does not break it down (self-pickup case).
    product = data.get("productPrice")
    delivery = data.get("deliveryPrice") or 0
    packing = data.get("packingPrice") or 0
    if isinstance(product, (int, float)):
        return PriceBreakdown(int(product), int(delivery), int(packing))
    return PriceBreakdown(int(total) - int(delivery) - int(packing), int(delivery), int(packing))


def compare_item(
    client: McpClient,
    store_code: str,
    be_code: str,
    product_code: str,
    item_name: str = "",
) -> DeliveryComparison | None:
    """Price one product both ways at one store and decompose the difference."""
    pickup_raw = client.call(
        "calculate-price",
        {"storeCode": store_code, "orderType": 1, "beType": 1,
         "items": [{"productCode": product_code, "quantity": 1}]},
    )
    delivery_raw = client.call(
        "calculate-price",
        {"storeCode": store_code, "orderType": 2, "beType": 2, "beCode": be_code,
         "items": [{"productCode": product_code, "quantity": 1}]},
    )
    pickup = _breakdown(pickup_raw)
    delivery = _breakdown(delivery_raw)
    if pickup is None or delivery is None:
        return None
    return DeliveryComparison(
        item_name=item_name,
        item_code=product_code,
        store_name="",
        store_code=store_code,
        pickup=pickup,
        delivery=delivery,
    )


def common_items(client: McpClient, store_code: str, be_code: str) -> list[tuple[str, str, float, float]]:
    """(code, name, pickup_price, delivery_price) for items sold both ways.

    Useful because the two channels do not share a catalogue: in a measured
    sample they had 107 and 118 items with only 19 codes in common. Comparing
    only the intersection is the honest thing to do.
    """
    self_menu = (client.call("query-meals", {"storeCode": store_code, "orderType": 1, "beType": 1})
                 .get("data") or {}).get("meals") or {}
    dlv_menu = (client.call("query-meals", {"storeCode": store_code, "orderType": 2,
                                            "beType": 2, "beCode": be_code})
                .get("data") or {}).get("meals") or {}
    rows: list[tuple[str, str, float, float]] = []
    for code in set(self_menu) & set(dlv_menu):
        a = _yuan(self_menu[code].get("currentPrice"))
        b = _yuan(dlv_menu[code].get("currentPrice"))
        if a is None or b is None:
            continue
        rows.append((str(code), self_menu[code].get("name") or "", a, b))
    rows.sort(key=lambda row: -(row[3] - row[2]))
    return rows


def _yuan(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
