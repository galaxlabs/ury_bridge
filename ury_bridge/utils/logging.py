from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import now_datetime

from ury_bridge.utils.common import get_website_settings
from ury_bridge.utils.customer import get_customer_for_user
from ury_bridge.utils.menu import get_menu_product_by_slug


DEFAULT_UBER_REDIRECT_MESSAGE = "You will complete delivery order on Uber Eats"
DEFAULT_DELIVERY_CTA_LABEL = "Delivery with Uber Eats"
DEFAULT_PICKUP_CTA_LABEL = "Pickup from Cozy Kitchen"
DEFAULT_UBER_UNAVAILABLE_MESSAGE = "Uber Eats delivery link is not available yet."
EVENT_SOURCE_OPTIONS = {"website", "cart", "checkout", "product_page"}


def get_website_delivery_options_payload() -> dict[str, Any]:
	settings = get_website_settings()
	store_url = (settings.uber_eats_store_url or "").strip()
	return {
		"enable_uber_eats_redirect": bool(settings.enable_uber_eats_redirect),
		"uber_eats_store_url": store_url or None,
		"uber_eats_redirect_message": (settings.uber_eats_redirect_message or DEFAULT_UBER_REDIRECT_MESSAGE).strip(),
		"delivery_cta_label": (settings.delivery_cta_label or DEFAULT_DELIVERY_CTA_LABEL).strip(),
		"pickup_cta_label": (settings.pickup_cta_label or DEFAULT_PICKUP_CTA_LABEL).strip(),
		"is_available": bool(settings.enable_uber_eats_redirect and store_url),
		"unavailable_message": DEFAULT_UBER_UNAVAILABLE_MESSAGE,
	}


def log_product_view_event(payload: dict[str, Any]) -> tuple[bool, str | None]:
	return _log_website_interaction("product_view", payload)


def log_uber_eats_click_event(payload: dict[str, Any]) -> tuple[bool, str | None]:
	return _log_website_interaction("uber_eats_click", payload)


def _log_website_interaction(event_type: str, payload: dict[str, Any]) -> tuple[bool, str | None]:
	try:
		log_doc = frappe.get_doc(
			{
				"doctype": "Website Interaction Log",
				"event_type": event_type,
				"event_time": now_datetime(),
				"source": _sanitize_source(payload.get("source")),
				"page_url": _sanitize_text(payload.get("page_url") or payload.get("route"), 500),
				"session_id": _sanitize_text(payload.get("session_id"), 140),
				"notes": _sanitize_text(payload.get("notes"), 1000),
				"payload_json": _serialize_payload(payload.get("payload") or payload.get("context") or payload),
				"user_agent": _get_user_agent(),
				"ip_address": _get_ip_address(),
			}
		)

		user = frappe.session.user if frappe.session.user != "Guest" else None
		if user:
			log_doc.portal_user = user
			log_doc.customer = get_customer_for_user(user)

		product_slug = _sanitize_text(payload.get("product_slug") or payload.get("slug"), 140)
		product_name = _sanitize_text(payload.get("product_name") or payload.get("title"), 255)
		product_code = _sanitize_text(payload.get("product_code") or payload.get("item_code"), 140)

		if product_slug and (not product_name or not product_code):
			try:
				product = get_menu_product_by_slug(product_slug)
			except Exception:
				product = None
			if product:
				product_name = product_name or _get_product_value(product, "title")
				product_code = product_code or _get_product_value(product, "mapped_item") or _get_product_value(product, "mapped_bundle")

		log_doc.product_slug = product_slug
		log_doc.product_name = product_name
		log_doc.product_code = product_code

		if log_doc.customer and not log_doc.customer_name:
			log_doc.customer_name = frappe.db.get_value("Customer", log_doc.customer, "customer_name")
		elif payload.get("customer_name"):
			log_doc.customer_name = _sanitize_text(payload.get("customer_name"), 255)

		log_doc.insert(ignore_permissions=True)
		return True, log_doc.name
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Website Interaction Logging Failed")
		return False, None


def _sanitize_source(value: Any) -> str:
	source = (cstr(value) or "").strip() or "website"
	return source if source in EVENT_SOURCE_OPTIONS else "website"


def _sanitize_text(value: Any, limit: int) -> str | None:
	text = (cstr(value) or "").strip()
	if not text:
		return None
	return text[:limit]


def _serialize_payload(value: Any) -> str | None:
	if value in (None, "", [], {}):
		return None
	try:
		return json.dumps(value, default=str)
	except Exception:
		return _sanitize_text(value, 10000)


def _get_user_agent() -> str | None:
	request = getattr(frappe, "request", None)
	if not request:
		return None
	return _sanitize_text(request.headers.get("User-Agent"), 1000)


def _get_ip_address() -> str | None:
	request = getattr(frappe, "request", None)
	if not request:
		return None
	for header_name in ("X-Forwarded-For", "CF-Connecting-IP", "X-Real-IP"):
		value = request.headers.get(header_name)
		if value:
			return _sanitize_text(value.split(",")[0], 140)
	return _sanitize_text(getattr(request, "remote_addr", None), 140)


def cstr(value: Any) -> str:
	if value is None:
		return ""
	return str(value)


def _get_product_value(product: Any, key: str) -> Any:
	if isinstance(product, dict):
		return product.get(key)
	return getattr(product, key, None)
