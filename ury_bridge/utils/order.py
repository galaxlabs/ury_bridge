from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from frappe.utils import cint, flt, now_datetime, today

from ury_bridge.utils.common import (
	as_bool,
	ensure_public_order_api_enabled,
	get_api_settings,
	get_default_company,
	get_default_currency,
	get_default_price_list,
)
from ury_bridge.utils.customer import (
	ensure_customer_contact,
	find_customer_by_email_or_phone,
	get_customer_master_defaults,
)
from ury_bridge.utils.menu import (
	get_bundle_summary,
	get_default_currency as get_menu_currency,
	get_item_price,
	get_menu_product_by_slug,
	resolve_variant_from_options,
)


COZY_ORDER_DOCTYPE = "Cozy Order"
COZY_ORDER_ITEM_DOCTYPE = "Cozy Order Item"
COZY_ORDER_WORKFLOW = "Cozy Kitchen Cozy Order Flow"
FINAL_ORDER_STATUSES = {"Completed", "Cancelled", "Rejected"}
PAYMENT_PAID_STATUSES = {"Paid", "COD"}
STAFF_ROLES = {"System Manager", "Desk User", "Cozy Admin", "Kitchen Staff"}


def ensure_staff_order_access(ptype: str = "read") -> None:
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	user_roles = set(frappe.get_roles(frappe.session.user))
	if user_roles.intersection(STAFF_ROLES):
		return
	frappe.throw(_("You are not permitted to {0} cozy orders.").format(ptype))


def run_as_order_system_user(fn, *args, **kwargs):
	original_user = frappe.session.user
	try:
		if original_user == "Guest":
			frappe.set_user("Administrator")
		return fn(*args, **kwargs)
	finally:
		if frappe.session.user != original_user:
			frappe.set_user(original_user)


def normalize_order_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
	payload = payload or {}
	payload = extract_order_payload_root(payload)
	customer = extract_order_customer_payload(payload)

	items = extract_order_items(payload)
	normalized_items = []
	for row in items:
		if not isinstance(row, dict):
			continue
		normalized_items.append(
			{
				"product_slug": (
					row.get("product_slug")
					or row.get("slug")
					or row.get("item_slug")
					or row.get("product")
					or ""
				).strip(),
				"qty": flt(row.get("qty") or 0),
				"selected_variant_item_code": (row.get("selected_variant_item_code") or row.get("variant_item_code") or "").strip(),
				"selected_options": row.get("selected_options") or row.get("options") or {},
				"bundle_selections": row.get("bundle_selections") or row.get("bundle_options") or {},
				"notes": row.get("notes") or row.get("note"),
			}
		)

	return {
		"customer": {
			"full_name": (customer.get("full_name") or payload.get("customer_name") or "").strip(),
			"phone": (customer.get("phone") or customer.get("mobile") or payload.get("customer_phone") or "").strip(),
			"email": (customer.get("email") or payload.get("customer_email") or "").strip().lower(),
			"address_name": (customer.get("address_name") or payload.get("address_name") or "").strip(),
			"address_text": (customer.get("address_text") or payload.get("delivery_address_text") or "").strip(),
		},
		"customer_name": (payload.get("customer_name") or "").strip(),
		"order_source": payload.get("order_source") or "Web App",
		"order_type": payload.get("order_type") or "Pickup",
		"payment_method": payload.get("payment_method") or "Cash",
		"payment_status": payload.get("payment_status"),
		"special_instructions": payload.get("special_instructions") or payload.get("note") or "",
		"requested_time": payload.get("requested_time"),
		"allergy_confirmation": cint(payload.get("allergy_confirmation") or 0),
		"terms_accepted": cint(payload.get("terms_accepted") or 0),
		"app_order_id": payload.get("app_order_id") or payload.get("order_id"),
		"delivery_fee": flt(payload.get("delivery_fee") or 0),
		"discount_amount": flt(payload.get("discount_amount") or 0),
		"items": normalized_items,
	}


def extract_order_payload_root(payload: dict[str, Any]) -> dict[str, Any]:
	for key in ("order", "data"):
		nested = payload.get(key)
		if isinstance(nested, dict):
			return nested
	return payload


def extract_order_customer_payload(payload: dict[str, Any]) -> dict[str, Any]:
	customer = payload.get("customer")
	if isinstance(customer, dict):
		return customer

	for key in ("customer_details", "profile", "contact"):
		nested = payload.get(key)
		if isinstance(nested, dict):
			return nested

	return {}


def extract_order_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
	for key in ("items", "order_items", "lines", "cart_items", "cart"):
		value = payload.get(key)
		if isinstance(value, list):
			return value
	return []


def validate_order_payload(data: dict[str, Any], is_pos: bool = False) -> None:
	ensure_public_order_api_enabled()

	if data.get("order_type") not in {"Pickup", "Delivery"}:
		frappe.throw(_("order_type must be Pickup or Delivery."))

	if not data.get("items"):
		frappe.throw(_("At least one order item is required."))

	for row in data["items"]:
		if not row.get("product_slug"):
			frappe.throw(_("Each order item must include product_slug."))
		if flt(row.get("qty")) <= 0:
			frappe.throw(_("Each order item must have qty greater than zero."))

	customer = data.get("customer") or {}
	if not is_pos and not (customer.get("full_name") and (customer.get("email") or customer.get("phone"))):
		frappe.throw(_("Customer full name and either email or phone are required."))

	if data.get("order_type") == "Delivery" and not (customer.get("address_name") or customer.get("address_text")):
		frappe.throw(_("Delivery address is required for delivery orders."))

	if not is_pos and not cint(data.get("allergy_confirmation")):
		frappe.throw(_("Please confirm that you have read the allergy notice before placing the order."))

	if not is_pos and not cint(data.get("terms_accepted")):
		frappe.throw(_("Please accept the terms and conditions before placing the order."))


def validate_ordering_flags(order_type: str) -> None:
	website_settings = frappe.get_single("Ury Website Settings")
	settings = get_api_settings()
	if not cint(website_settings.enable_ordering):
		frappe.throw(_("Ordering is currently disabled."))

	if order_type == "Pickup" and not cint(website_settings.enable_pickup):
		frappe.throw(_("Pickup ordering is currently disabled."))

	if order_type == "Delivery":
		if not cint(website_settings.enable_delivery) or not cint(settings.default_delivery_enabled):
			frappe.throw(_("Delivery ordering is currently disabled."))


def validate_minimum_order_amount(data: dict[str, Any], resolved_items: list[dict[str, Any]]) -> None:
	settings = get_api_settings()
	minimum_amount = flt(settings.minimum_order_amount or 0)
	subtotal = sum(flt(row["amount"]) for row in resolved_items)
	total_before_discount = subtotal + flt(data.get("delivery_fee") or 0)
	if minimum_amount and total_before_discount < minimum_amount:
		frappe.throw(_("Minimum order amount is {0}.").format(minimum_amount))


def get_or_create_order_customer(
	customer_payload: dict[str, Any],
	allow_minimal_identity: bool = False,
) -> dict[str, Any]:
	customer_payload = customer_payload or {}
	email = (customer_payload.get("email") or "").strip().lower()
	phone = (customer_payload.get("phone") or "").strip()
	full_name = (customer_payload.get("full_name") or "").strip()

	customer_name = find_customer_by_email_or_phone(email=email, mobile_no=phone)
	if customer_name:
		customer_doc = frappe.get_doc("Customer", customer_name)
		portal_user = email if email and frappe.db.exists("User", email) else None
		if email or phone:
			ensure_customer_contact(customer_name, full_name or customer_doc.customer_name, email=email or None, mobile_no=phone or None)
		return {"customer": customer_doc, "portal_user": portal_user}

	if not cint(get_api_settings().auto_create_customer):
		frappe.throw(_("Customer record not found and auto customer creation is disabled."))

	if not full_name and not allow_minimal_identity:
		frappe.throw(_("Customer full name is required."))

	defaults = get_customer_master_defaults()
	customer_doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": full_name or email or phone or "Guest Customer",
			"customer_type": "Individual",
			"customer_group": defaults.get("customer_group"),
			"territory": defaults.get("territory"),
		}
	)
	customer_doc.insert(ignore_permissions=True)
	if email or phone:
		ensure_customer_contact(customer_doc.name, customer_doc.customer_name, email=email or None, mobile_no=phone or None)

	portal_user = email if email and frappe.db.exists("User", email) else None
	return {"customer": customer_doc, "portal_user": portal_user}


def ensure_delivery_address(customer_name: str, customer_payload: dict[str, Any]) -> str | None:
	if customer_payload.get("address_name"):
		address_name = customer_payload["address_name"]
		if frappe.db.exists(
			"Dynamic Link",
			{
				"parenttype": "Address",
				"parent": address_name,
				"link_doctype": "Customer",
				"link_name": customer_name,
			},
		):
			return address_name
		frappe.throw(_("Selected address does not belong to this customer."))

	if not customer_payload.get("address_text"):
		return None

	if not cint(get_api_settings().auto_create_address_for_delivery):
		return None

	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": customer_payload.get("full_name") or customer_name,
			"address_type": "Shipping",
			"address_line1": customer_payload.get("address_text"),
			"country": "United Kingdom",
			"links": [{"link_doctype": "Customer", "link_name": customer_name}],
		}
	)
	address.insert(ignore_permissions=True)
	return address.name


def resolve_order_items(order_items: list[dict[str, Any]], order_type: str) -> list[dict[str, Any]]:
	resolved_items = []
	for row in order_items:
		product = get_menu_product_by_slug(row["product_slug"])
		if order_type == "Pickup" and not product.get("available_for_pickup"):
			frappe.throw(_("{0} is not available for pickup.").format(product["title"]))
		if order_type == "Delivery" and not product.get("available_for_delivery"):
			frappe.throw(_("{0} is not available for delivery.").format(product["title"]))
		if not product.get("allow_order"):
			frappe.throw(_("{0} is not available for ordering.").format(product["title"]))

		qty = flt(row["qty"])
		if product["product_type"] == "Bundle":
			resolved_items.append(resolve_bundle_order_item(product, qty, row))
		else:
			resolved_items.append(resolve_item_order_item(product, qty, row))
	return resolved_items


def resolve_item_order_item(product: dict[str, Any], qty: float, row: dict[str, Any]) -> dict[str, Any]:
	item_code = product["_item_code"]
	source_name = item_code
	selected_variant_item_code = (row.get("selected_variant_item_code") or "").strip()
	selected_options = row.get("selected_options") or {}

	if product["source_summary"].get("has_variants"):
		item_code = selected_variant_item_code or resolve_variant_from_options(product["_item_code"], selected_options)
		if not item_code:
			frappe.throw(_("{0} requires a variant selection.").format(product["title"]))
		if not frappe.db.exists("Item", item_code):
			frappe.throw(_("Selected variant does not exist."))

	price = get_item_price(item_code, fallback_item_code=product["_item_code"])
	if not price:
		frappe.throw(_("No selling price found for {0}.").format(product["title"]))

	item_doc = frappe.get_doc("Item", item_code)
	return {
		"item_code": item_code,
		"item_name": item_doc.item_name,
		"qty": qty,
		"rate": flt(price.price_list_rate),
		"amount": flt(price.price_list_rate) * qty,
		"source_type": "Item",
		"source_name": source_name,
		"notes": row.get("notes"),
		"variant_snapshot_json": json.dumps(
			{
				"selected_variant_item_code": item_code,
				"selected_options": selected_options,
			}
		),
		"bundle_snapshot_json": None,
	}


def resolve_bundle_order_item(product: dict[str, Any], qty: float, row: dict[str, Any]) -> dict[str, Any]:
	price = get_item_price(product["_item_code"])
	if not price:
		frappe.throw(_("No selling price found for bundle {0}.").format(product["title"]))

	bundle_summary = get_bundle_summary(product["_bundle_name"])
	if not bundle_summary["items"]:
		frappe.throw(_("Product bundle {0} has no bundle items configured.").format(product["title"]))
	bundle_summary = apply_bundle_component_selections(bundle_summary, row.get("bundle_selections"), qty)

	return {
		"item_code": product["_item_code"],
		"item_name": product["title"],
		"qty": qty,
		"rate": flt(price.price_list_rate),
		"amount": flt(price.price_list_rate) * qty,
		"source_type": "Product Bundle",
		"source_name": product["_bundle_name"],
		"notes": row.get("notes"),
		"variant_snapshot_json": None,
		"bundle_snapshot_json": json.dumps(bundle_summary),
	}


def apply_bundle_component_selections(
	bundle_summary: dict[str, Any],
	bundle_selections: dict[str, Any] | None,
	order_qty: float,
) -> dict[str, Any]:
	bundle_selections = bundle_selections or {}
	selectable_components = bundle_summary.get("selectable_components") or []
	if not selectable_components:
		return bundle_summary

	selected_map = {
		str(key): normalize_bundle_selection_values(value)
		for key, value in bundle_selections.items()
	}
	items = []
	for item in bundle_summary.get("items") or []:
		updated_item = dict(item)
		selector_key = updated_item.get("selector_key")
		component = next(
			(
				row
				for row in selectable_components
				if row.get("component_key") == selector_key
			),
			None,
		)
		if component:
			valid_variants = {
				variant.get("item_code"): variant
				for variant in component.get("variants") or []
				if variant.get("item_code")
			}
			required_slots = max(1, cint(round(flt(component.get("included_qty") or updated_item.get("qty") or 1) * flt(order_qty or 1))))
			selected_codes = selected_map.get(selector_key) or []
			selected_codes = [code for code in selected_codes if code in valid_variants]
			default_code = component.get("default_variant_item_code")
			while len(selected_codes) < required_slots and default_code:
				selected_codes.append(default_code)
			selected_codes = selected_codes[:required_slots]
			if not selected_codes:
				frappe.throw(_("Please select bundle variants for {0}.").format(component.get("label")))

			grouped_counts: dict[str, int] = {}
			for code in selected_codes:
				grouped_counts[code] = grouped_counts.get(code, 0) + 1

			for code, selected_count in grouped_counts.items():
				selected_variant = valid_variants.get(code)
				if not selected_variant:
					frappe.throw(_("Selected bundle variant does not exist for {0}.").format(component.get("label")))
				items.append(
					{
						**updated_item,
						"item_code": selected_variant["item_code"],
						"qty": flt(selected_count),
						"description": selected_variant.get("item_name") or updated_item.get("description"),
						"selected_variant_item_code": selected_variant["item_code"],
						"selected_variant_label": selected_variant.get("label"),
						"selected_variant_codes": selected_codes,
					}
				)
			continue

		updated_item["qty"] = flt(updated_item.get("qty") or 0) * flt(order_qty or 1)
		items.append(updated_item)

	return {
		**bundle_summary,
		"items": items,
	}


def normalize_bundle_selection_values(value: Any) -> list[str]:
	if isinstance(value, list):
		return [str(row).strip() for row in value if str(row).strip()]
	if isinstance(value, str) and value.strip():
		return [value.strip()]
	return []


def build_cozy_order(
	data: dict[str, Any],
	customer_context: dict[str, Any],
	resolved_items: list[dict[str, Any]],
) -> frappe.model.document.Document:
	customer_doc = customer_context["customer"]
	customer_payload = data.get("customer") or {}
	delivery_address = None
	if data["order_type"] == "Delivery":
		delivery_address = ensure_delivery_address(customer_doc.name, customer_payload)

	payment_method = data.get("payment_method") or "Cash"
	payment_status = data.get("payment_status") or default_payment_status_for_method(payment_method)

	order_doc = frappe.get_doc(
		{
			"doctype": COZY_ORDER_DOCTYPE,
			"naming_series": "COZY-ORD-.YYYY.-.#####",
			"customer": customer_doc.name,
			"customer_name": customer_payload.get("full_name") or customer_doc.customer_name,
			"customer_phone": customer_payload.get("phone"),
			"customer_email": customer_payload.get("email"),
			"portal_user": customer_context.get("portal_user"),
			"order_source": data.get("order_source") or "Web App",
			"order_type": data["order_type"],
			"order_status": "Pending",
			"payment_method": payment_method,
			"payment_status": payment_status,
			"currency": get_default_currency() or get_menu_currency(),
			"company": get_default_company(),
			"price_list": get_default_price_list(),
			"allergy_acknowledged": cint(data.get("allergy_confirmation") or 0),
			"terms_accepted": cint(data.get("terms_accepted") or 0),
			"terms_accepted_at": now_datetime() if cint(data.get("terms_accepted") or 0) else None,
			"special_instructions": data.get("special_instructions"),
			"requested_time": data.get("requested_time"),
			"delivery_address": delivery_address,
			"delivery_address_text": customer_payload.get("address_text"),
			"payment_gateway": data.get("payment_gateway"),
			"payment_intent_id": data.get("payment_intent_id"),
			"checkout_session_id": data.get("checkout_session_id"),
			"gateway_charge_id": data.get("gateway_charge_id"),
			"gateway_customer_id": data.get("gateway_customer_id"),
			"delivery_fee": flt(data.get("delivery_fee") or 0),
			"discount_amount": flt(data.get("discount_amount") or 0),
			"items": [
				{
					"doctype": COZY_ORDER_ITEM_DOCTYPE,
					"item_code": row["item_code"],
					"item_name": row["item_name"],
					"qty": row["qty"],
					"rate": row["rate"],
					"amount": row["amount"],
					"source_type": row["source_type"],
					"source_name": row["source_name"],
					"notes": row.get("notes"),
					"variant_snapshot_json": row.get("variant_snapshot_json"),
					"bundle_snapshot_json": row.get("bundle_snapshot_json"),
				}
				for row in resolved_items
			],
		}
	)
	order_doc.insert(ignore_permissions=True)
	return order_doc


def default_payment_status_for_method(payment_method: str) -> str:
	method = (payment_method or "").strip()
	if method == "Cash":
		return "COD"
	if method in {"Card", "Apple Pay", "Google Pay"}:
		return "Pending Confirmation"
	if method == "Bank Transfer":
		return "Pending Confirmation"
	return "Unpaid"


def serialize_cozy_order_summary(order_doc) -> dict[str, Any]:
	return {
		"order_id": order_doc.name,
		"order_status": order_doc.order_status,
		"payment_status": order_doc.payment_status,
		"order_type": order_doc.order_type,
		"customer_name": order_doc.customer_name,
		"currency": order_doc.currency,
		"subtotal": flt(order_doc.subtotal),
		"delivery_fee": flt(order_doc.delivery_fee),
		"discount_amount": flt(order_doc.discount_amount),
		"total_amount": flt(order_doc.total_amount),
		"sales_invoice": order_doc.sales_invoice,
		"placed_at": str(order_doc.creation),
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"rate": flt(row.rate),
				"amount": flt(row.amount),
				"source_type": row.source_type,
				"source_name": row.source_name,
			}
			for row in order_doc.items
		],
	}


def serialize_order_tracking(order_doc) -> dict[str, Any]:
	available_actions = get_available_workflow_actions(order_doc)
	return {
		"order_number": order_doc.name,
		"order_status": order_doc.order_status,
		"payment_status": order_doc.payment_status,
		"order_type": order_doc.order_type,
		"sales_invoice": order_doc.sales_invoice,
		"can_cancel": "Cancel" in available_actions,
		"available_actions": available_actions,
		"timeline": build_order_timeline(order_doc),
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"amount": flt(row.amount),
			}
			for row in order_doc.items
		],
	}


def build_order_timeline(order_doc) -> list[dict[str, Any]]:
	timeline = [{"label": "Created", "timestamp": str(order_doc.creation)}]
	for label, fieldname in [
		("Confirmed", "order_confirmed_at"),
		("Preparing", "preparing_at"),
		("Ready", "ready_at"),
		("Dispatched", "dispatched_at"),
		("Completed", "completed_at"),
		("Cancelled", "cancelled_at"),
	]:
		value = getattr(order_doc, fieldname, None)
		if value:
			timeline.append({"label": label, "timestamp": str(value)})
	return timeline


def get_customer_orders(customer: str, start: int = 0, page_length: int = 20):
	return frappe.get_all(
		COZY_ORDER_DOCTYPE,
		filters={"customer": customer},
		fields=[
			"name",
			"creation",
			"order_status",
			"payment_status",
			"order_type",
			"total_amount",
			"currency",
			"sales_invoice",
		],
		order_by="creation desc",
		start=start,
		page_length=page_length,
	)


def get_order_doc_for_tracking(order_id: str, email: str | None = None, phone: str | None = None):
	order_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_id)
	if frappe.session.user != "Guest":
		return ensure_customer_owns_order(order_doc, allow_staff=True)

	email = (email or "").strip().lower()
	phone = (phone or "").strip()
	if not email and not phone:
		frappe.throw(_("Email or phone is required to access this order."))
	if email and order_doc.customer_email and email == order_doc.customer_email.lower():
		return order_doc
	if phone and order_doc.customer_phone and phone == order_doc.customer_phone:
		return order_doc
	frappe.throw(_("Order not found."))


def get_orders_for_guest_tracking(email: str | None = None, phone: str | None = None, limit: int = 10):
	email = (email or "").strip().lower()
	phone = (phone or "").strip()
	if not email and not phone:
		frappe.throw(_("Email or phone is required."))

	filters = {}
	if email and phone:
		filters = [["customer_email", "=", email], ["customer_phone", "=", phone]]
	elif email:
		filters = {"customer_email": email}
	else:
		filters = {"customer_phone": phone}

	return frappe.get_all(
		COZY_ORDER_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"creation",
			"order_status",
			"payment_status",
			"order_type",
			"total_amount",
			"currency",
		],
		order_by="creation desc",
		limit_page_length=cint(limit) or 10,
	)


def ensure_customer_owns_order(order_doc, allow_staff: bool = False):
	if allow_staff and set(frappe.get_roles(frappe.session.user)).intersection(STAFF_ROLES):
		return order_doc
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	if order_doc.portal_user == frappe.session.user:
		return order_doc
	customer = find_customer_by_email_or_phone(email=frappe.session.user)
	if customer and order_doc.customer == customer:
		return order_doc
	frappe.throw(_("You do not have permission to access this order."))


def get_cozy_order_detail_payload(order_name: str, enforce_customer: bool = False) -> dict[str, Any]:
	order_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_name)
	if enforce_customer:
		ensure_customer_owns_order(order_doc)
	elif frappe.session.user != "Guest":
		ensure_customer_owns_order(order_doc, allow_staff=True)
	else:
		frappe.throw(_("Login required."))

	return {
		**serialize_cozy_order_summary(order_doc),
		"customer": {
			"name": order_doc.customer_name,
			"phone": order_doc.customer_phone,
			"email": order_doc.customer_email,
			"portal_user": order_doc.portal_user,
		},
		"delivery_address": {
			"name": order_doc.delivery_address,
			"text": order_doc.delivery_address_text,
		},
		"special_instructions": order_doc.special_instructions,
		"requested_time": str(order_doc.requested_time) if order_doc.requested_time else None,
		"payment_gateway": order_doc.payment_gateway,
		"payment_intent_id": order_doc.payment_intent_id,
		"checkout_session_id": order_doc.checkout_session_id,
		"gateway_charge_id": order_doc.gateway_charge_id,
		"payment_confirmed_at": str(order_doc.payment_confirmed_at) if order_doc.payment_confirmed_at else None,
		"available_actions": get_available_workflow_actions(order_doc),
		"timeline": build_order_timeline(order_doc),
	}


def get_available_workflow_actions(order_doc) -> list[str]:
	workflow = frappe.db.get_value("Workflow", {"document_type": COZY_ORDER_DOCTYPE, "is_active": 1}, "name")
	if not workflow:
		return []
	return [row.action for row in frappe.get_all("Workflow Transition", filters={"parent": workflow, "state": order_doc.order_status}, fields=["action"], order_by="idx asc")]


def get_admin_orders(
	order_status: str | None = None,
	order_type: str | None = None,
	search: str | None = None,
	start: int = 0,
	page_length: int = 50,
) -> dict[str, Any]:
	ensure_staff_order_access()
	filters: dict[str, Any] = {}
	if order_status:
		filters["order_status"] = order_status
	if order_type:
		filters["order_type"] = order_type
	if search:
		search_value = f"%{search.strip()}%"
		rows = frappe.db.sql(
			"""
			select
				name,
				creation,
				customer_name,
				customer_phone,
				customer_email,
				order_status,
				payment_status,
				order_type,
				total_amount,
				currency
			from `tabCozy Order`
			where (%(order_status)s is null or order_status = %(order_status)s)
			  and (%(order_type)s is null or order_type = %(order_type)s)
			  and (
				name like %(search)s
				or ifnull(customer_phone, '') like %(search)s
				or ifnull(customer_email, '') like %(search)s
				or ifnull(customer_name, '') like %(search)s
			  )
			order by creation desc
			limit %(start)s, %(page_length)s
			""",
			{
				"order_status": order_status,
				"order_type": order_type,
				"search": search_value,
				"start": cint(start),
				"page_length": cint(page_length),
			},
			as_dict=True,
		)
	else:
		rows = frappe.get_all(
			COZY_ORDER_DOCTYPE,
			filters=filters,
			fields=[
				"name",
				"creation",
				"customer_name",
				"customer_phone",
				"customer_email",
				"order_status",
				"payment_status",
				"order_type",
				"total_amount",
				"currency",
			],
			order_by="creation desc",
			start=start,
			page_length=page_length,
		)

	count_rows = frappe.get_all(
		COZY_ORDER_DOCTYPE,
		fields=["order_status", "count(name) as count"],
		group_by="order_status",
		order_by="order_status asc",
	)
	return {
		"orders": rows,
		"counts_by_status": {row.order_status: cint(row.count) for row in count_rows},
	}


def apply_cozy_order_workflow_action(order_name: str, action: str):
	ensure_staff_order_access(ptype="write")
	order_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_name)
	try:
		updated_doc = apply_workflow(order_doc, action)
	except Exception as exc:
		frappe.throw(_("Workflow action failed: {0}").format(exc))
	updated_doc.reload()
	update_order_timestamps(updated_doc)
	return updated_doc


def update_order_timestamps(order_doc) -> None:
	now_value = now_datetime()
	changed_fields: list[str] = []

	progression = [
		("Confirmed", "order_confirmed_at"),
		("Preparing", "preparing_at"),
		("Ready", "ready_at"),
		("Completed", "completed_at"),
	]
	if order_doc.order_type == "Delivery":
		progression.insert(3, ("Out for Delivery", "dispatched_at"))
	state_rank = {state: index for index, (state, _) in enumerate(progression)}
	current_rank = state_rank.get(order_doc.order_status)

	if current_rank is not None:
		for index, (_, fieldname) in enumerate(progression):
			if index > current_rank:
				break
			if not getattr(order_doc, fieldname):
				order_doc.db_set(fieldname, now_value, update_modified=False)
				changed_fields.append(fieldname)

	if order_doc.order_status == "Cancelled" and not getattr(order_doc, "cancelled_at"):
		order_doc.db_set("cancelled_at", now_value, update_modified=False)
		changed_fields.append("cancelled_at")

	if order_doc.order_status == "Rejected" and not getattr(order_doc, "cancelled_at"):
		order_doc.db_set("cancelled_at", now_value, update_modified=False)
		changed_fields.append("cancelled_at")

	if changed_fields:
		order_doc.reload()


def mark_order_paid(order_name: str, payment_method: str | None = None):
	order_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_name)
	if payment_method:
		order_doc.payment_method = payment_method
	order_doc.payment_status = "Paid"
	order_doc.payment_confirmed_at = now_datetime()
	order_doc.save(ignore_permissions=True)
	order_doc.reload()
	return order_doc


def maybe_auto_confirm_paid_order(order_doc):
	settings = get_api_settings()
	if order_doc.payment_status != "Paid":
		return order_doc
	if order_doc.order_status == "Pending" and cint(settings.auto_confirm_paid_orders):
		order_doc = apply_cozy_order_workflow_action(order_doc.name, "Confirm")
	if order_doc.order_status == "Confirmed" and cint(settings.auto_start_preparing_after_payment):
		order_doc = apply_cozy_order_workflow_action(order_doc.name, "Start Preparing")
	return order_doc


def mark_pickup_paid(order_name: str):
	ensure_staff_order_access(ptype="write")
	order_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_name)
	if order_doc.order_type != "Pickup":
		frappe.throw(_("Only pickup orders can be marked paid with this action."))
	order_doc = mark_order_paid(order_name, payment_method=order_doc.payment_method or "Cash")
	return order_doc


def cancel_order_for_customer(order_id: str, email: str | None = None, phone: str | None = None, reason: str | None = None):
	order_doc = get_order_doc_for_tracking(order_id=order_id, email=email, phone=phone)
	if order_doc.order_status in FINAL_ORDER_STATUSES:
		frappe.throw(_("This order can no longer be cancelled."))

	available_actions = get_available_workflow_actions(order_doc)
	if "Cancel" not in available_actions:
		frappe.throw(_("This order can no longer be cancelled from the tracking page."))

	def _apply_cancel():
		live_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_doc.name)
		try:
			updated_doc = apply_workflow(live_doc, "Cancel")
		except Exception as exc:
			frappe.throw(_("Unable to cancel this order: {0}").format(exc))
		if reason:
			updated_doc.db_set("cancellation_reason", reason, update_modified=False)
		updated_doc.reload()
		update_order_timestamps(updated_doc)
		return updated_doc

	return run_as_order_system_user(_apply_cancel)


def create_pos_order_payload(payload: dict[str, Any]) -> dict[str, Any]:
	data = normalize_order_payload(payload)
	data["order_source"] = payload.get("order_source") or "POS"
	if data.get("payment_method") == "Cash" and data["order_type"] == "Pickup":
		data["payment_status"] = "COD"
	return data


def get_orders_for_portal(customer: str, active_only: bool = False, limit: int = 20):
	filters = {"customer": customer}
	if active_only:
		filters["order_status"] = ["not in", list(FINAL_ORDER_STATUSES)]
	return frappe.get_all(
		COZY_ORDER_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"creation",
			"order_status",
			"payment_status",
			"order_type",
			"total_amount",
			"currency",
			"sales_invoice",
		],
		order_by="creation desc",
		limit=limit,
	)
