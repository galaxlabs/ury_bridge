from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint, flt, now_datetime, today

from ury_bridge.utils.common import (
	ensure_public_order_api_enabled,
	get_default_company,
	get_default_price_list,
)
from ury_bridge.utils.customer import (
	create_customer_address,
	create_customer_record,
	ensure_customer_contact,
	find_customer_by_email_or_phone,
	get_customer_for_user,
)
from ury_bridge.utils.menu import get_menu_product_by_slug


def validate_ordering_flags(order_type: str = "Pickup") -> None:
	ensure_public_order_api_enabled()
	settings = frappe.get_single("Ury Website Settings")

	if not cint(settings.enable_ordering):
		frappe.throw("Ordering is currently disabled.")

	if order_type == "Pickup" and not cint(settings.enable_pickup):
		frappe.throw("Pickup is currently disabled.")

	if order_type == "Delivery" and not cint(settings.enable_delivery):
		frappe.throw("Delivery is currently disabled.")


def normalize_order_payload(payload: dict[str, Any]) -> dict[str, Any]:
	order_type = (payload.get("order_type") or "Pickup").title()
	return {
		"customer": payload.get("customer") or {},
		"items": payload.get("items") or [],
		"order_type": order_type,
		"note": payload.get("note"),
		"payment_method": payload.get("payment_method"),
		"allergy_confirmation": bool(payload.get("allergy_confirmation")),
		"requested_time": payload.get("requested_time"),
		"address": payload.get("address") or {},
		"app_order_id": payload.get("app_order_id"),
		"delivery_fee": payload.get("delivery_fee"),
		"discount_amount": payload.get("discount_amount"),
		"address_text": payload.get("address_text"),
	}


def validate_order_payload(payload: dict[str, Any]) -> None:
	if not payload.get("items"):
		frappe.throw("At least one order item is required.")

	if payload.get("order_type") not in {"Pickup", "Delivery"}:
		frappe.throw("order_type must be Pickup or Delivery.")

	if not payload.get("payment_method"):
		frappe.throw("payment_method is required.")


def resolve_order_items(items: list[dict[str, Any]], order_type: str = "Pickup") -> list[dict[str, Any]]:
	resolved_items = []

	for row in items:
		menu_product = get_menu_product_by_slug(row.get("product_slug"))
		if not cint(menu_product.allow_order):
			frappe.throw(f"{menu_product.title} is not available for ordering.")
		if order_type == "Pickup" and not cint(menu_product.available_for_pickup):
			frappe.throw(f"{menu_product.title} is not available for pickup.")
		if order_type == "Delivery" and not cint(menu_product.available_for_delivery):
			frappe.throw(f"{menu_product.title} is not available for delivery.")

		qty = flt(row.get("qty") or 0)
		if qty <= 0:
			frappe.throw(f"Invalid quantity for {menu_product.title}.")

		resolved = resolve_single_order_item(menu_product, row, qty)
		resolved_items.append(resolved)

	return resolved_items


def resolve_single_order_item(menu_product, row: dict[str, Any], qty: float) -> dict[str, Any]:
	if menu_product.product_type == "Bundle":
		return {
			"menu_product": menu_product.name,
			"product_slug": menu_product.slug,
			"product_type": "Bundle",
			"item_code": None,
			"bundle_code": menu_product.mapped_bundle,
			"qty": qty,
			"unit_price": None,
			"line_total": None,
			"selected_options": row.get("selected_options") or [],
		}

	item_code = row.get("selected_variant_item_code") or menu_product.mapped_item
	validate_variant_selection(menu_product.mapped_item, item_code)
	price_info = get_item_price(item_code)

	return {
		"menu_product": menu_product.name,
		"product_slug": menu_product.slug,
		"product_type": "Item",
		"item_code": item_code,
		"bundle_code": None,
		"qty": qty,
		"unit_price": price_info["price_list_rate"],
		"line_total": flt(price_info["price_list_rate"]) * qty,
		"selected_options": row.get("selected_options") or [],
	}


def validate_variant_selection(mapped_item_code: str, selected_item_code: str) -> None:
	if mapped_item_code == selected_item_code:
		return

	base_item = frappe.get_cached_doc("Item", mapped_item_code)
	selected_item = frappe.get_cached_doc("Item", selected_item_code)
	template_code = base_item.variant_of or base_item.name
	if selected_item.variant_of != template_code:
		frappe.throw(f"Selected variant {selected_item_code} does not belong to {mapped_item_code}.")


def get_item_price(item_code: str) -> dict[str, Any]:
	price_row = frappe.db.get_value(
		"Item Price",
		{
			"item_code": item_code,
			"price_list": get_default_price_list(),
			"selling": 1,
		},
		["name", "price_list_rate", "currency"],
		as_dict=True,
	)
	if not price_row:
		frappe.throw(f"No selling price found for item {item_code}.")
	return price_row


def get_or_create_order_customer(customer_payload: dict[str, Any]) -> dict[str, Any]:
	email = (customer_payload.get("email") or "").strip().lower()
	phone = (customer_payload.get("phone") or "").strip()
	full_name = (customer_payload.get("full_name") or "").strip()

	if not full_name or not (email or phone):
		frappe.throw("customer.full_name and at least one of customer.email or customer.phone are required.")

	customer_name = None
	if email or phone:
		customer_name = find_customer_by_email_or_phone(email=email, mobile_no=phone)

	if customer_name:
		return {
			"customer_name": customer_name,
			"email": email,
			"phone": phone,
			"is_guest_customer": False,
		}

	customer_doc = create_customer_record(full_name=full_name)
	ensure_customer_contact(customer=customer_doc.name, full_name=full_name, email=email, mobile_no=phone)

	return {
		"customer_name": customer_doc.name,
		"email": email,
		"phone": phone,
		"is_guest_customer": True,
	}


def build_sales_order(order_payload: dict[str, Any], customer_context: dict[str, Any], resolved_items: list[dict[str, Any]]):
	order_doc = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"company": get_default_company(),
			"customer": customer_context["customer_name"],
			"transaction_date": today(),
			"delivery_date": today(),
			"order_type": "Sales",
			"selling_price_list": get_default_price_list(),
			"items": build_sales_order_items(resolved_items),
		}
	)

	apply_sales_order_bridge_fields(order_doc, order_payload, customer_context)

	if order_payload["order_type"] == "Delivery" and order_payload.get("address"):
		address = create_customer_address(customer_context["customer_name"], order_payload["address"])
		if hasattr(order_doc, "custom_delivery_address"):
			order_doc.set("custom_delivery_address", address.get_display())

	order_doc.insert(ignore_permissions=True)
	order_doc.submit()
	return order_doc


def build_sales_order_items(resolved_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
	items = []
	for row in resolved_items:
		if row["product_type"] == "Bundle":
			items.append(
				{
					"item_code": row["bundle_code"],
					"qty": row["qty"],
				}
			)
			continue

		items.append(
			{
				"item_code": row["item_code"],
				"qty": row["qty"],
				"rate": row["unit_price"],
			}
		)
	return items


def apply_sales_order_bridge_fields(order_doc, order_payload: dict[str, Any], customer_context: dict[str, Any]) -> None:
	custom_field_map = {
		"custom_order_source": "cozy_kitchen_web",
		"custom_order_type": order_payload.get("order_type"),
		"custom_customer_phone": customer_context.get("phone"),
		"custom_customer_email": customer_context.get("email"),
		"custom_order_note": order_payload.get("note"),
		"custom_app_order_id": order_payload.get("app_order_id"),
		"custom_payment_method": order_payload.get("payment_method"),
		"custom_delivery_fee": order_payload.get("delivery_fee"),
		"custom_discount_amount": order_payload.get("discount_amount"),
		"custom_allergy_confirmation": 1 if order_payload.get("allergy_confirmation") else 0,
		"custom_requested_time": order_payload.get("requested_time"),
	}

	if order_payload.get("order_type") == "Delivery":
		custom_field_map["custom_delivery_address"] = order_payload.get("address_text")

	for fieldname, value in custom_field_map.items():
		if hasattr(order_doc, fieldname):
			order_doc.set(fieldname, value)


def serialize_order_summary(order_doc) -> dict[str, Any]:
	return {
		"order_id": order_doc.name,
		"order_number": order_doc.name,
		"order_status": order_doc.status,
		"payment_status": get_payment_status(order_doc),
		"order_type": getattr(order_doc, "custom_order_type", None),
		"currency": order_doc.currency,
		"subtotal": order_doc.net_total,
		"grand_total": order_doc.grand_total,
		"placed_at": now_datetime().isoformat(),
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": row.qty,
				"rate": row.rate,
				"amount": row.amount,
			}
			for row in order_doc.items
		],
	}


def get_payment_status(order_doc) -> str:
	if getattr(order_doc, "per_billed", 0) >= 100:
		return "Paid"
	return "Pending"


def get_customer_orders(customer: str, start: int = 0, page_length: int = 20) -> list[dict[str, Any]]:
	return frappe.get_all(
		"Sales Order",
		filters={"customer": customer},
		fields=[
			"name",
			"transaction_date",
			"status",
			"grand_total",
			"currency",
			"per_billed",
			"custom_order_type",
		],
		order_by="transaction_date desc, creation desc",
		start=start,
		page_length=page_length,
	)


def get_order_doc_for_tracking(order_id: str, email: str | None = None, phone: str | None = None):
	order_doc = frappe.get_doc("Sales Order", order_id)
	if frappe.session.user != "Guest":
		customer = get_customer_for_user(frappe.session.user)
		if customer != order_doc.customer:
			frappe.throw("Order not found.")
		return order_doc

	email_matches = email and getattr(order_doc, "custom_customer_email", None) == email
	phone_matches = phone and getattr(order_doc, "custom_customer_phone", None) == phone
	if not (email_matches or phone_matches):
		frappe.throw("Guest order tracking requires a matching email or phone.")

	return order_doc


def serialize_order_tracking(order_doc) -> dict[str, Any]:
	return {
		"order_number": order_doc.name,
		"order_status": order_doc.status,
		"kitchen_status": getattr(order_doc, "custom_kitchen_status", None) or order_doc.status,
		"payment_status": get_payment_status(order_doc),
		"order_type": getattr(order_doc, "custom_order_type", None),
		"delivery_address": getattr(order_doc, "custom_delivery_address", None),
		"customer_phone": getattr(order_doc, "custom_customer_phone", None),
		"customer_email": getattr(order_doc, "custom_customer_email", None),
		"timestamps": {
			"created_at": str(order_doc.creation),
			"modified_at": str(order_doc.modified),
			"delivery_date": str(order_doc.delivery_date) if order_doc.delivery_date else None,
		},
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": row.qty,
				"amount": row.amount,
			}
			for row in order_doc.items
		],
	}
