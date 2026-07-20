"""瑞幸点单 Skill 式状态机：找店 → 搜品 → 预览 → 口头确认 → 下单 → 查单。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from luckin_mcp import (
    LuckinMcpClient,
    LuckinMcpError,
    as_price,
    dig,
    luckin_enabled,
    unwrap_list,
)

STATE_IDLE = "idle"
STATE_AWAIT_CONFIRM = "await_confirm"
STATE_AWAIT_PAY = "await_pay"


def _log(msg: str) -> None:
    print(f"[Luckin] {msg}", flush=True)

ORDER_INTENT_RE = re.compile(
    r"(点咖啡|瑞幸|luckin|美式|拿铁|生椰|生酪|澳白|卡布|摩卡|浓缩|"
    r"来一[杯份]|来两[杯份]|帮我点|点一[杯份]|点杯|下单|"
    r"附近.*(瑞幸|咖啡)|有哪些.*(瑞幸|门店)|查(一下)?(订单|取餐))",
    re.I,
)
SHOP_QUERY_RE = re.compile(r"(附近|门店|哪[里儿有].*(瑞幸|咖啡)|有哪些.*(瑞幸|店))", re.I)
CONFIRM_RE = re.compile(
    r"^(确认|确定|下单|好的|可以|行|要|嗯|对|买|支付|付款)(下单|订单|购买|支付)?[\s，。！!？?]*$",
    re.I,
)
# 口语确认：确认下单 / 确认订单 / 就这个 / 下单吧 …
CONFIRM_LOOSE_RE = re.compile(
    r"(确认\s*(下单|订单|购买|支付)?|确定\s*(下单|订单)?|"
    r"就这个|就它|下单吧|可以下单|帮我下单|提交订单)",
    re.I,
)
CANCEL_RE = re.compile(r"(取消|算了|不要了|别点了|停下)", re.I)
DELIVERY_RE = re.compile(r"(外送|外卖|配送|送到|送上门)", re.I)
ORDER_STATUS_RE = re.compile(r"(查(一下)?订单|取餐码|订单状态|付(完|了)?款|已支付|支付完)", re.I)
AMOUNT_RE = re.compile(r"([一二两三四五六七八九十两\d]+)\s*杯")

CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


@dataclass
class OrderAction:
    """Result of handling one user utterance."""

    handled: bool = False
    speak: str | None = None
    pay_url: str | None = None
    pay_qr_url: str | None = None
    debug: str | None = None


@dataclass
class PendingPreview:
    dept_id: int
    dept_name: str
    product_name: str
    product_list: list[dict[str, Any]]
    coupon_code_list: list[str]
    discount_price: str | None
    initial_price: str | None
    longitude: float
    latitude: float
    raw_preview: Any = None


@dataclass
class LuckinOrderSession:
    state: str = STATE_IDLE
    pending: PendingPreview | None = None
    last_order_id: str | None = None
    last_pay_url: str | None = None
    last_pay_qr_url: str | None = None
    client: LuckinMcpClient | None = field(default=None, repr=False)

    def _client(self) -> LuckinMcpClient:
        if self.client is None:
            self.client = LuckinMcpClient()
        return self.client

    def reset(self) -> None:
        prev = self.state
        self.state = STATE_IDLE
        self.pending = None
        if prev != STATE_IDLE:
            _log(f"状态 {prev} → {STATE_IDLE}（已重置）")

    def handle_utterance(self, text: str) -> OrderAction:
        text = (text or "").strip()
        if not text:
            return OrderAction(handled=False)
        if not luckin_enabled():
            return OrderAction(handled=False)

        state_before = self.state
        _log(f"收到 ASR={text!r} | 当前状态={state_before}")
        try:
            action = self._handle(text)
        except LuckinMcpError as e:
            self.reset()
            action = OrderAction(
                handled=True,
                speak=f"瑞幸点单出错了：{e}。请稍后再试，或检查 Token 是否有效。",
                debug=str(e),
            )
        except Exception as e:  # noqa: BLE001 — surface to voice
            self.reset()
            action = OrderAction(
                handled=True,
                speak=f"瑞幸点单出现异常：{e}",
                debug=str(e),
            )

        if action.handled:
            _log(
                f"处理完毕 handled=True | {state_before} → {self.state} | "
                f"speak={(action.speak or '')[:80]!r}"
            )
        else:
            _log(f"未命中点单逻辑 handled=False | 状态仍为 {self.state}（交给豆包闲聊）")
        return action

    def _handle(self, text: str) -> OrderAction:
        if CANCEL_RE.search(text) and self.state != STATE_IDLE:
            _log(f"分支=取消 | state={self.state}")
            self.reset()
            return OrderAction(handled=True, speak="好的，已取消这次瑞幸点单。")

        if DELIVERY_RE.search(text) and ORDER_INTENT_RE.search(text):
            _log("分支=拒绝外送")
            return OrderAction(
                handled=True,
                speak="目前仅支持到店自取，还不支持外送。可以说门店名或直接点饮品。",
            )

        if self.state == STATE_AWAIT_CONFIRM:
            if _is_confirm(text):
                _log(f"分支=确认下单 | ASR={text!r}")
                return self._create_from_pending()
            if ORDER_INTENT_RE.search(text) and not _is_confirm(text):
                _log("分支=待确认时重新预览（新的点单意图）")
                return self._start_preview_flow(text)
            _log("分支=待确认但话术未识别为确认，继续催确认")
            return OrderAction(
                handled=True,
                speak="上一杯还在等你确认。请说「确认下单」或「确认订单」，或说「取消」。",
            )

        if self.state == STATE_AWAIT_PAY:
            if ORDER_STATUS_RE.search(text) or "取餐" in text:
                _log("分支=查单/取餐码")
                return self._query_last_order()
            if ORDER_INTENT_RE.search(text) and not ORDER_STATUS_RE.search(text):
                _log("分支=已下单后再点新品 → 重新预览")
                self.reset()
                return self._start_preview_flow(text)
            if _is_confirm(text):
                _log("分支=已下单后再次确认 → 提醒去支付")
                return OrderAction(
                    handled=True,
                    speak="订单已创建。请在瑞幸 App 里完成付款；付完后可以说「查取餐码」。",
                    pay_url=self.last_pay_url,
                    pay_qr_url=self.last_pay_qr_url,
                )

        # 空闲时说了确认，但还没有预览单
        if _is_confirm(text):
            _log("分支=空闲态确认（尚无预览单）")
            return OrderAction(
                handled=True,
                speak="现在还没有待确认的瑞幸订单。请先说想喝什么，例如「来一杯冰美式」。",
            )

        if not ORDER_INTENT_RE.search(text):
            return OrderAction(handled=False)

        if ORDER_STATUS_RE.search(text) and self.last_order_id:
            _log("分支=查历史订单")
            return self._query_last_order()

        if SHOP_QUERY_RE.search(text) and not _looks_like_drink_order(text):
            _log("分支=查附近门店")
            return self._list_shops(text)

        _log("分支=点单预览流水线")
        return self._start_preview_flow(text)

    def _coords(self) -> tuple[float, float]:
        lon = os.environ.get("LUCKIN_LONGITUDE", "").strip()
        lat = os.environ.get("LUCKIN_LATITUDE", "").strip()
        if not lon or not lat:
            raise LuckinMcpError(
                "未配置 LUCKIN_LONGITUDE / LUCKIN_LATITUDE，无法定位附近门店"
            )
        return float(lon), float(lat)

    def _resolve_shop(self, text: str) -> tuple[int, str, float, float]:
        fixed = os.environ.get("LUCKIN_DEPT_ID", "").strip()
        lon, lat = self._coords()
        if fixed:
            name = os.environ.get("LUCKIN_DEPT_NAME", "").strip() or f"门店{fixed}"
            return int(fixed), name, lon, lat

        dept_name = _extract_shop_name_hint(text)
        raw = self._client().query_shop_list(lon, lat, dept_name=dept_name)
        shops = unwrap_list(raw, "shopList", "deptList")
        if not shops and isinstance(raw, dict):
            shops = unwrap_list(raw.get("data"), "shopList", "deptList")
        if not shops:
            raise LuckinMcpError("附近没有找到营业中的瑞幸门店")

        shop = shops[0]
        dept_id = int(shop.get("deptId") or shop.get("dept_id") or shop.get("id"))
        name = str(
            shop.get("deptName") or shop.get("dept_name") or shop.get("name") or dept_id
        )
        shop_lon = float(shop.get("longitude") or lon)
        shop_lat = float(shop.get("latitude") or lat)
        return dept_id, name, shop_lon, shop_lat

    def _list_shops(self, text: str) -> OrderAction:
        lon, lat = self._coords()
        dept_name = _extract_shop_name_hint(text)
        raw = self._client().query_shop_list(lon, lat, dept_name=dept_name)
        shops = unwrap_list(raw, "shopList", "deptList")
        if not shops and isinstance(raw, dict):
            shops = unwrap_list(raw.get("data"), "shopList", "deptList")
        if not shops:
            return OrderAction(handled=True, speak="附近没有找到瑞幸门店。")

        lines = []
        for i, shop in enumerate(shops[:5], 1):
            name = shop.get("deptName") or shop.get("name") or "门店"
            dist = shop.get("distance")
            addr = shop.get("address") or ""
            dist_s = f"，约{dist}公里" if dist is not None else ""
            lines.append(f"{i}. {name}{dist_s}。{addr}".strip())
        speak = (
            "附近瑞幸门店："
            + "；".join(lines)
            + "。目前仅支持到店自取，可以说想喝什么帮你预览下单。"
        )
        return OrderAction(handled=True, speak=speak)

    def _start_preview_flow(self, text: str) -> OrderAction:
        query = _drink_query(text)
        if not query:
            _log("预览中止：未能抽出饮品名")
            return OrderAction(
                handled=True,
                speak="想喝什么？可以说「冰美式」或「生椰拿铁」。",
            )

        amount = _parse_amount(text)
        _log(f"预览步骤1/3 找店 | query={query!r} amount={amount}")
        dept_id, dept_name, lon, lat = self._resolve_shop(text)
        _log(f"预览步骤1/3 完成 | dept={dept_name} id={dept_id}")

        _log("预览步骤2/3 搜商品")
        search = self._client().search_product(dept_id, query)
        products = unwrap_list(search, "productList", "products", "recommendList")
        if not products and isinstance(search, dict):
            products = unwrap_list(search.get("data"), "productList", "products")
        if not products:
            _log(f"预览中止：无商品 | query={query!r}")
            return OrderAction(
                handled=True,
                speak=f"在{dept_name}没搜到「{query}」相关商品，换个名字试试？",
            )

        product = products[0]
        product_id = int(product.get("productId") or product.get("product_id"))
        sku_code = str(product.get("skuCode") or product.get("sku_code") or "")
        product_name = str(
            product.get("productName") or product.get("product_name") or query
        )
        if not sku_code:
            detail = self._client().query_product_detail(dept_id, product_id)
            sku_code = str(
                dig(detail, "skuCode")
                or dig(detail, "data", "skuCode")
                or dig(detail, "sku_code")
                or ""
            )
            if not sku_code:
                sku_code = _first_sku(detail) or ""
        if not sku_code:
            raise LuckinMcpError(f"商品 {product_name} 缺少 skuCode，无法预览")
        _log(f"预览步骤2/3 完成 | product={product_name} id={product_id} sku={sku_code}")

        product_list = [
            {"productId": product_id, "skuCode": sku_code, "amount": amount}
        ]
        _log("预览步骤3/3 previewOrder")
        preview = self._client().preview_order(dept_id, product_list)
        preview_data = (
            preview.get("data")
            if isinstance(preview, dict) and "data" in preview
            else preview
        )
        if not isinstance(preview_data, dict):
            preview_data = preview if isinstance(preview, dict) else {}

        coupons = (
            preview_data.get("couponCodeList")
            or preview_data.get("coupon_code_list")
            or []
        )
        if not isinstance(coupons, list):
            coupons = []
        coupons = [str(c) for c in coupons if c]

        discount = as_price(
            preview_data.get("discountPrice")
            or preview_data.get("discount_price")
            or preview_data.get("payPrice")
        )
        initial = as_price(
            preview_data.get("totalInitialPrice")
            or preview_data.get("initialPrice")
            or product.get("initialPrice")
            or product.get("estimatePrice")
        )

        self.pending = PendingPreview(
            dept_id=dept_id,
            dept_name=dept_name,
            product_name=product_name,
            product_list=product_list,
            coupon_code_list=coupons,
            discount_price=discount,
            initial_price=initial,
            longitude=lon,
            latitude=lat,
            raw_preview=preview_data,
        )
        self.state = STATE_AWAIT_CONFIRM

        price_part = f"应付约{discount}元" if discount else "价格已预览"
        if initial and discount and initial != discount:
            price_part = f"原价约{initial}元，{price_part}"
        speak = (
            f"预览好了：{dept_name}，{amount}杯{product_name}，{price_part}，到店自取。"
            f"请说「确认下单」或「确认订单」继续，或说「取消」。下单后请在瑞幸 App 付款。"
        )
        _log(
            f"★★ 预览完成 → state=await_confirm | {product_name} | "
            f"应付={discount} 券数={len(coupons)} | 下一步请说确认"
        )
        return OrderAction(handled=True, speak=speak)

    def _create_from_pending(self) -> OrderAction:
        pending = self.pending
        if not pending:
            _log("确认失败：pending 为空")
            self.reset()
            return OrderAction(
                handled=True, speak="没有待确认的订单，请重新说想喝什么。"
            )

        _log(f"调用 createOrder | {pending.product_name} @ {pending.dept_name}")
        result = self._client().create_order(
            pending.dept_id,
            pending.product_list,
            pending.longitude,
            pending.latitude,
            coupon_code_list=pending.coupon_code_list or None,
        )
        data = (
            result.get("data")
            if isinstance(result, dict) and "data" in result
            else result
        )
        if not isinstance(data, dict):
            data = result if isinstance(result, dict) else {}

        order_id = str(
            data.get("orderId")
            or data.get("orderIdStr")
            or data.get("order_id")
            or dig(data, "orderInfo", "orderId")
            or ""
        )
        pay_url = (
            data.get("payOrderUrl")
            or data.get("pay_order_url")
            or data.get("payUrl")
            or dig(data, "payInfo", "payOrderUrl")
        )
        qr_url = data.get("payOrderQrCodeUrl") or data.get("pay_order_qr_code_url")
        price = as_price(data.get("discountPrice") or pending.discount_price)

        self.last_order_id = order_id or None
        self.last_pay_url = str(pay_url or "") or None
        self.last_pay_qr_url = str(qr_url or "") or None
        if not self.last_pay_url and self.last_pay_qr_url:
            # Some responses only return QR image URL
            self.last_pay_url = None
        self.pending = None
        self.state = STATE_AWAIT_PAY if self.last_order_id else STATE_IDLE

        parts = ["已创建订单"]
        if pending.product_name:
            parts.append(pending.product_name)
        if price:
            parts.append(f"应付约{price}元")
        if order_id:
            parts.append(f"订单号{order_id}")
        speak = (
            "，".join(parts)
            + "。请在瑞幸 App 里完成付款；付完后可以说「查取餐码」。"
        )
        if not self.last_pay_url and not self.last_pay_qr_url:
            speak += "（未返回支付信息，请打开瑞幸 App 查看待支付订单。）"
        _log(
            f"★★ createOrder 成功 → state={self.state} | orderId={order_id} | "
            f"has_pay_url={bool(self.last_pay_url or self.last_pay_qr_url)}"
        )
        return OrderAction(
            handled=True,
            speak=speak,
            pay_url=self.last_pay_url,
            pay_qr_url=self.last_pay_qr_url,
        )

    def _query_last_order(self) -> OrderAction:
        if not self.last_order_id:
            return OrderAction(handled=True, speak="还没有可查询的瑞幸订单。")
        detail = self._client().query_order_detail(self.last_order_id)
        data = (
            detail.get("data")
            if isinstance(detail, dict) and "data" in detail
            else detail
        )
        if not isinstance(data, dict):
            data = detail if isinstance(detail, dict) else {}

        code = (
            dig(data, "takeMealCodeInfo", "takeMealCode")
            or dig(data, "takeMealCode")
            or dig(data, "mealCode")
            or dig(data, "pickupCode")
            or data.get("takeMealCode")
        )
        status = (
            data.get("orderStatus") or data.get("status") or data.get("orderStatusName")
        )
        status_map = {
            10: "待付款",
            20: "下单成功",
            30: "制作中",
            60: "等待取餐",
            80: "已完成",
            100: "已取消",
        }
        status_text = status_map.get(status, str(status) if status is not None else "未知")
        speak = f"订单{self.last_order_id}状态：{status_text}。"
        if code:
            speak += f"取餐码是{code}。"
        else:
            speak += "暂未拿到取餐码，可能还没支付或还在制作。"
        return OrderAction(
            handled=True,
            speak=speak,
            pay_url=self.last_pay_url,
            pay_qr_url=self.last_pay_qr_url,
        )


def _is_confirm(text: str) -> bool:
    t = (text or "").strip()
    if not t or CANCEL_RE.search(t):
        return False
    if CONFIRM_RE.match(t):
        return True
    if CONFIRM_LOOSE_RE.search(t):
        return True
    # 「确认xxx」且不是取消
    if ("确认" in t or "确定" in t) and "取消" not in t:
        return True
    return False


def _looks_like_drink_order(text: str) -> bool:
    return bool(
        re.search(
            r"(美式|拿铁|生椰|生酪|澳白|卡布奇诺|摩卡|浓缩|点一|来一|帮我点|下单)",
            text,
        )
    )


def _extract_shop_name_hint(text: str) -> str | None:
    """Only treat explicit「在/去 XX店/瑞幸」as shop name; ignore 附近/有哪些…"""
    m = re.search(
        r"(?:在|去)([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:店|门店|瑞幸)",
        text,
    )
    if not m:
        return None
    name = m.group(1)
    junk = {
        "附近",
        "哪家",
        "哪",
        "哪里",
        "哪儿",
        "本地",
        "这里",
        "哪些",
        "有哪些",
        "一下",
        "帮我",
    }
    if name in junk or any(j in name for j in ("附近", "哪些", "哪里", "哪儿")):
        return None
    return name


def _drink_query(text: str) -> str:
    cleaned = ORDER_INTENT_RE.sub(" ", text)
    cleaned = re.sub(
        r"(帮我|我想|我要|来|点|杯|份|的|一下|确认|下单|瑞幸|咖啡)", " ", cleaned
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。！!?、")
    if cleaned and len(cleaned) >= 2:
        return cleaned
    m = re.search(
        r"((冰|热)?(美式|拿铁|澳白|卡布奇诺|摩卡|浓缩|生椰拿铁|生酪拿铁|椰云拿铁)[^\s，。]{0,8})",
        text,
    )
    return m.group(1) if m else cleaned


def _parse_amount(text: str) -> int:
    m = AMOUNT_RE.search(text)
    if not m:
        return 1
    raw = m.group(1)
    if raw.isdigit():
        return max(1, min(int(raw), 10))
    if raw in CN_NUM:
        return CN_NUM[raw]
    return 1


def _first_sku(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("skuCode", "sku_code"):
            if data.get(key):
                return str(data[key])
        for val in data.values():
            found = _first_sku(val)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _first_sku(item)
            if found:
                return found
    return None


def build_external_rag(speak: str, title: str = "瑞幸点单") -> str:
    """Payload string for Doubao ChatRAGText external_rag field."""
    return json.dumps(
        [{"title": title, "content": speak}],
        ensure_ascii=False,
    )


def luckin_system_hint() -> str:
    if not luckin_enabled():
        return ""
    return (
        "【瑞幸点单】你可以通过外部知识（瑞幸点单结果）帮用户播报到店自取的预览、确认与取餐信息。"
        "不要编造订单号、价格或支付链接；只根据外部知识内容口语化转述。"
        "用户未明确确认前不要声称已经下单成功。目前仅支持到店自取。"
    )
