from __future__ import annotations

from typing import Any

import frappe
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import cint, flt, now_datetime, today

from ury_bridge.utils.common import (
	ensure_public_order_api_enabled,
	get_api_settings,
	get_default_company,
	get_default_price_list,
	slugify_value,
)
from ury_bridge.utils.customer import (
	create_customer_address,
	create_customer_record,
	ensure_customer_contact,
	find_customer_by_email_or_phone,
	get_customer_for_user,
)
from ury_bridge.utils.menu import (
	get_bundle_summary,
	get_item_price,
	get_menu_product_by_slug,
	normalize_selected_options,
	resolve_variant_from_options,
)


ORDER_SOURCE_OPTIONS = {"Web App", "Mobile App", "POS", "Admin"}
PAYMENT_METHOD_OPTIONS = {"Cash", "Card", "Apple Pay", "Google Pay"}


def run_as_order_system_user(callback, *args, **kwargs):
	previous_user = frappe.session.user
	should_switch = previous_user == "Guest"
	try:
		if should_switch:
			frappe.set_user("Administrator")
		return callback(*args, **kwargs)
	finally:
		if should_switch:
			frappe.set_user(previous_user)


def ensure_staff_order_access(ptype: str = "read") -> None:
	if frappe.session.user == "Guest":
		frappe.throw("Login required.")

	if not (frappe.has_permission("Sales Order", ptype=ptype) or "System Manager" in frappe.get_roles()):
		frappe.throw("You do not have permission to access Sales Orders.")


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
	payment_method = payload.get("payment_method") or "Cash"
	order_source = payload.get("order_source") or "Web App"
	return {
		"customer": payload.get("customer") or {},
		"customer_name": payload.get("customer_name"),
		"items": payload.get("items") or [],
		"order_type": order_type,
		"note": payload.get("note"),
		"payment_method": payment_method,
		"allergy_confirmation": bool(payload.get("allergy_confirmation")),
		"requested_time": payload.get("requested_time"),
		"address": payload.get("address") or {},
		"app_order_id": payload.get("app_order_id"),
		"delivery_fee": flt(payload.get("delivery_fee") or 0),
		"discount_amount": flt(payload.get("discount_amount") or 0),
		"address_text": payload.get("address_text"),
		"order_source": order_source if order_source in ORDER_SOURCE_OPTIONS else "Web App",
	}


def validate_order_payload(payload: dict[str, Any], *, is_pos: bool = False) -> None:
	if not payload.get("items"):
		frappe.throw("At least one order item is required.")

	if payload.get("order_type") not in {"Pickup", "Delivery"}:
		frappe.throw("order_type must be Pickup or Delivery.")

	if payload.get("payment_method") not in PAYMENT_METHOD_OPTIONS:
		frappe.throw("payment_method must be one of Cash, Card, Apple Pay, or Google Pay.")

	if payload.get("order_source") not in ORDER_SOURCE_OPTIONS:
		frappe.throw("Invalid order_source.")

	if is_pos:
		return

	if frappe.session.user == "Guest" and not cint(get_api_settings().guest_checkout_enabled):
		frappe.throw("Guest checkout is disabled.")


def resolve_order_items(items: list[dict[str, Any]], order_type: str = "Pickup") -> list[dict[str, Any]]:
	resolved_items = []
	for row in items:
		resolved_items.append(resolve_single_order_item(row, order_type=order_type))
	return resolved_items


def resolve_single_order_item(row: dict[str, Any], order_type: str = "Pickup") -> dict[str, Any]:
	product = resolve_product_reference(row)

	if not product["allow_order"]:
		frappe.throw(f"{product['title']} is not currently configured for ordering.")

	if order_type == "Pickup" and not product["available_for_pickup"]:
		frappe.throw(f"{product['title']} is not available for pickup.")

	if order_type == "Delivery" and not product["available_for_delivery"]:
		frappe.throw(f"{product['title']} is not available for delivery.")

	qty = flt(row.get("qty") or 0)
	if qty <= 0:
		frappe.throw(f"Invalid quantity for {product['title']}.")

	if product["product_type"] == "Bundle":
		return resolve_bundle_order_item(product, qty, row)

	return resolve_item_order_item(product, qty, row)


def resolve_product_reference(row: dict[str, Any]) -> dict[str, Any]:
	product_slug = row.get("product_slug")
	if product_slug:
		return get_menu_product_by_slug(product_slug)

	item_code = row.get("selected_variant_item_code") or row.get("item_code")
	if item_code:
		item = frappe.db.get_value(
			"Item",
			item_code,
			["variant_of", "item_name"],
			as_dict=True,
		)
		if not item:
			frappe.throw(f"Item {item_code} was not found.")
		template_code = item.variant_of or item_code
		template_name = frappe.db.get_value("Item", template_code, "item_name") or template_code
		return get_menu_product_by_slug(slugify_value(template_name))

	frappe.throw("Each order item must include product_slug or item_code.")


def resolve_bundle_order_item(product: dict[str, Any], qty: float, row: dict[str, Any]) -> dict[str, Any]:
	bundle_summary = get_bundle_summary(product["_bundle_name"])
	if not bundle_summary["items"]:
		frappe.throw(f"Bundle {product['title']} has no bundle items configured.")

	price = get_item_price(product["_item_code"])
	if not price:
		frappe.throw(f"No selling price found for bundle {product['title']}.")

	return {
		"product_slug": product["slug"],
		"title": product["title"],
		"product_type": "Bundle",
		"item_code": product["_item_code"],
		"bundle_name": product["_bundle_name"],
		"qty": qty,
		"unit_price": flt(price.price_list_rate),
		"line_total": flt(price.price_list_rate) * qty,
		"selected_options": row.get("selected_options") or [],
	}


def resolve_item_order_item(product: dict[str, Any], qty: float, row: dict[str, Any]) -> dict[str, Any]:
	template_item_code = product["_item_code"]
	item_meta = frappe.db.get_value(
		"Item",
		template_item_code,
		["name", "has_variants", "is_sales_item", "disabled"],
		as_dict=True,
	)
	if not item_meta or cint(item_meta.disabled) or not cint(item_meta.is_sales_item):
		frappe.throw(f"Item {product['title']} is not available for sale.")

	selected_item_code = row.get("selected_variant_item_code")
	if cint(item_meta.has_variants):
		if not selected_item_code:
			selected_item_code = resolve_variant_from_options(template_item_code, row.get("selected_options"))
		if not selected_item_code:
			frappe.throw(f"Item {product['title']} is a template, please select one of its variants.")
		validate_variant_selection(template_item_code, selected_item_code)
	else:
		selected_item_code = selected_item_code or template_item_code

	selected_item = frappe.db.get_value(
		"Item",
		selected_item_code,
		["name", "item_name", "disabled", "is_sales_item"],
		as_dict=True,
	)
	if not selected_item or cint(selected_item.disabled) or not cint(selected_item.is_sales_item):
		frappe.throw(f"Selected item {selected_item_code} is not available for sale.")

	price = get_item_price(selected_item_code, fallback_item_code=template_item_code)
	if not price:
		frappe.throw(f"No selling price found for item {selected_item_code}.")

	return {
		"product_slug": product["slug"],
		"title": product["title"],
		"product_type": "Item",
		"item_code": selected_item_code,
		"bundle_name": None,
		"qty": qty,
		"unit_price": flt(price.price_list_rate),
		"line_total": flt(price.price_list_rate) * qty,
		"selected_options": normalize_selected_options(row.get("selected_options")),
	}


def validate_variant_selection(template_item_code: str, selected_item_code: str) -> None:
	if template_item_code == selected_item_code:
		return

	variant_of = frappe.db.get_value("Item", selected_item_code, "variant_of")
	if variant_of != template_item_code:
		frappe.throw(f"Selected variant {selected_item_code} does not belong to {template_item_code}.")


def validate_minimum_order_amount(order_payload: dict[str, Any], resolved_items: list[dict[str, Any]]) -> None:
	minimum_order_amount = flt(get_api_settings().minimum_order_amount or 0)
	subtotal = sum(flt(row["line_total"]) for row in resolved_items)
	if subtotal < minimum_order_amount:
		frappe.throw(f"Minimum order amount is {minimum_order_amount}.")


def get_or_create_order_customer(customer_payload: dict[str, Any], *, allow_minimal_identity: bool = False) -> dict[str, Any]:
	if frappe.session.user != "Guest":
		customer_name = get_customer_for_user(frappe.session.user)
		if customer_name:
			return {
				"customer_name": customer_name,
				"email": (customer_payload.get("email") or frappe.session.user or "").strip().lower(),
				"phone": (customer_payload.get("phone") or "").strip(),
				"full_name": (customer_payload.get("full_name") or frappe.db.get_value("User", frappe.session.user, "full_name") or "").strip(),
			}

	email = (customer_payload.get("email") or "").strip().lower()
	phone = (customer_payload.get("phone") or "").strip()
	full_name = (customer_payload.get("full_name") or "").strip()

	if not full_name:
		frappe.throw("customer.full_name is required.")

	if not allow_minimal_identity and not (email or phone):
		frappe.throw("At least one of customer.email or customer.phone is required.")

	customer_name = find_customer_by_email_or_phone(email=email, mobile_no=phone) if (email or phone) else None
	if customer_name:
		return {
			"customer_name": customer_name,
			"email": email,
			"phone": phone,
			"full_name": full_name,
		}

	if not cint(get_api_settings().auto_create_customer):
		frappe.throw("Customer was not found and automatic customer creation is disabled.")

	customer_doc = create_customer_record(full_name=full_name)
	if email or phone:
		ensure_customer_contact(customer=customer_doc.name, full_name=full_name, email=email or None, mobile_no=phone or None)

	return {
		"customer_name": customer_doc.name,
		"email": email,
		"phone": phone,
		"full_name": full_name,
	}


def build_sales_order(
	order_payload: dict[str, Any],
	customer_context: dict[str, Any],
	resolved_items: list[dict[str, Any]],
):
	order_doc = frappe.get_doc(
		{
			"doctype": "Sales Order",
			"company": get_default_company(),
			"customer": customer_context["customer_name"],
			"transaction_date": today(),
			"delivery_date": today(),
			"order_type": "Sales",
			"selling_price_list": get_default_price_list(),
			"currency": get_api_settings().default_currency,
			"kitchen_status": get_api_settings().new_order_status or "Pending",
			"items": build_sales_order_items(resolved_items),
		}
	)

	apply_sales_order_bridge_fields(order_doc, order_payload, customer_context, resolved_items)

	if order_payload["order_type"] == "Delivery" and order_payload.get("address") and cint(get_api_settings().auto_create_address_for_delivery):
		address = create_customer_address(customer_context["customer_name"], order_payload["address"])
		if hasattr(order_doc, "custom_delivery_address"):
			order_doc.set("custom_delivery_address", address.get_display())

	order_doc.insert(ignore_permissions=True)
	return order_doc


def build_sales_order_items(resolved_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
	return [
		{
			"item_code": row["item_code"],
			"qty": row["qty"],
			"rate": row["unit_price"],
		}
		for row in resolved_items
	]


def apply_sales_order_bridge_fields(
	order_doc,
	order_payload: dict[str, Any],
	customer_context: dict[str, Any],
	resolved_items: list[dict[str, Any]],
) -> None:
	subtotal = sum(flt(row["line_total"]) for row in resolved_items)
	delivery_fee = flt(order_payload.get("delivery_fee") or 0)
	discount_amount = flt(order_payload.get("discount_amount") or 0)

	custom_field_map = {
		"custom_order_source": order_payload.get("order_source") or "Web App",
		"custom_order_type": order_payload.get("order_type"),
		"custom_customer_phone": customer_context.get("phone"),
		"custom_customer_email": customer_context.get("email"),
		"custom_delivery_address": order_payload.get("address_text"),
		"custom_order_note": order_payload.get("note"),
		"custom_app_order_id": order_payload.get("app_order_id"),
		"custom_payment_method": order_payload.get("payment_method"),
		"custom_delivery_fee": delivery_fee,
		"custom_discount_amount": discount_amount,
		"custom_allergy_confirmation": 1 if order_payload.get("allergy_confirmation") else 0,
		"custom_requested_time": order_payload.get("requested_time"),
		"kitchen_status": get_api_settings().new_order_status or "Pending",
	}

	for fieldname, value in custom_field_map.items():
		if hasattr(order_doc, fieldname):
			order_doc.set(fieldname, value)

	order_doc.po_no = order_payload.get("app_order_id")
	order_doc.po_date = today() if order_payload.get("app_order_id") else None
	order_doc.customer_note = order_payload.get("note")
	order_doc.rounding_adjustment = 0
	order_doc.ignore_pricing_rule = 1
	order_doc.set_missing_values()
	order_doc.calculate_taxes_and_totals()

	if delivery_fee and hasattr(order_doc, "custom_delivery_fee"):
		order_doc.set("custom_delivery_fee", delivery_fee)
	if discount_amount and hasattr(order_doc, "custom_discount_amount"):
		order_doc.set("custom_discount_amount", discount_amount)


def get_current_order_state(order_doc) -> str:
	return getattr(order_doc, "kitchen_status", None) or getattr(order_doc, "workflow_state", None) or order_doc.status


def serialize_order_summary(order_doc, *, include_actions: bool = False) -> dict[str, Any]:
	payload = {
		"order_id": order_doc.name,
		"order_number": order_doc.name,
		"order_status": get_current_order_state(order_doc),
		"system_status": order_doc.status,
		"payment_status": get_payment_status(order_doc),
		"order_type": getattr(order_doc, "custom_order_type", None),
		"currency": order_doc.currency,
		"subtotal": order_doc.net_total,
		"grand_total": order_doc.grand_total,
		"placed_at": str(order_doc.creation or now_datetime()),
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
	if include_actions:
		payload["available_actions"] = get_available_workflow_actions(order_doc)
	return payload


def get_payment_status(order_doc) -> str:
	if flt(getattr(order_doc, "per_billed", 0) or 0) >= 100:
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
			"kitchen_status",
		],
		order_by="transaction_date desc, creation desc",
		start=start,
		page_length=page_length,
	)


def get_order_doc_for_tracking(order_id: str, email: str | None = None, phone: str | None = None):
	order_doc = run_as_order_system_user(frappe.get_doc, "Sales Order", order_id)
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
	current_state = get_current_order_state(order_doc)
	return {
		"order_number": order_doc.name,
		"order_status": current_state,
		"kitchen_status": current_state,
		"system_status": order_doc.status,
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


def get_available_workflow_actions(order_doc) -> list[str]:
	try:
		return [row.get("action") for row in get_transitions(order_doc)]
	except Exception:
		return []


def apply_sales_order_workflow_action(sales_order: str, action: str):
	ensure_staff_order_access(ptype="write")
	order_doc = frappe.get_doc("Sales Order", sales_order)
	try:
		updated_doc = apply_workflow(order_doc, action)
	except Exception as exc:
		frappe.throw(f"Workflow action failed: {frappe.get_traceback(with_context=False) if False else str(exc)}")

	return updated_doc


def get_admin_orders(
	kitchen_status: str | None = None,
	order_type: str | None = None,
	start: int = 0,
	page_length: int = 50,
) -> dict[str, Any]:
	ensure_staff_order_access(ptype="read")
	filters: dict[str, Any] = {}
	if kitchen_status:
		filters["kitchen_status"] = kitchen_status
	if order_type:
		filters["custom_order_type"] = order_type

	rows = frappe.get_all(
		"Sales Order",
		filters=filters,
		fields=[
			"name",
			"customer",
			"customer_name",
			"transaction_date",
			"grand_total",
			"currency",
			"status",
			"kitchen_status",
			"custom_order_type",
			"custom_payment_method",
			"custom_customer_phone",
			"custom_customer_email",
		],
		order_by="modified desc",
		start=start,
		page_length=page_length,
	)

	count_rows = frappe.get_all(
		"Sales Order",
		fields=["kitchen_status as state", "count(name) as count"],
		group_by="kitchen_status",
		order_by="kitchen_status asc",
	)

	return {
		"orders": [
			{
				"order_id": row.name,
				"order_number": row.name,
				"customer": row.customer_name or row.customer,
				"date": str(row.transaction_date),
				"order_status": row.kitchen_status or row.status,
				"system_status": row.status,
				"total": row.grand_total,
				"currency": row.currency,
				"order_type": row.custom_order_type,
				"payment_method": row.custom_payment_method,
				"customer_phone": row.custom_customer_phone,
				"customer_email": row.custom_customer_email,
			}
			for row in rows
		],
		"counts_by_status": {row.state or "Unspecified": cint(row.count) for row in count_rows},
	}


def get_order_detail_payload(sales_order: str) -> dict[str, Any]:
	ensure_staff_order_access(ptype="read")
	order_doc = frappe.get_doc("Sales Order", sales_order)
	payload = serialize_order_summary(order_doc)
	payload["available_actions"] = get_available_workflow_actions(order_doc)
	payload.update(
		{
			"customer": {
				"name": order_doc.customer_name,
				"customer": order_doc.customer,
				"email": getattr(order_doc, "custom_customer_email", None),
				"phone": getattr(order_doc, "custom_customer_phone", None),
			},
			"note": getattr(order_doc, "custom_order_note", None) or getattr(order_doc, "customer_note", None),
			"delivery_address": getattr(order_doc, "custom_delivery_address", None),
			"payment_method": getattr(order_doc, "custom_payment_method", None),
			"requested_time": getattr(order_doc, "custom_requested_time", None),
		}
	)
	return payload
