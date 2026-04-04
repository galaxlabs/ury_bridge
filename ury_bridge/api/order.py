from __future__ import annotations

import frappe

from ury_bridge.utils.common import api_response, parse_request_data
from ury_bridge.utils.customer import get_current_portal_user, get_customer_for_user
from ury_bridge.utils.order import (
	apply_sales_order_workflow_action as apply_order_workflow_action,
	build_sales_order,
	ensure_staff_order_access,
	get_admin_orders as get_admin_order_rows,
	get_customer_orders,
	get_or_create_order_customer,
	get_order_detail_payload,
	get_order_doc_for_tracking,
	normalize_order_payload,
	resolve_order_items,
	run_as_order_system_user,
	serialize_order_summary,
	serialize_order_tracking,
	validate_minimum_order_amount,
	validate_order_payload,
	validate_ordering_flags,
)


@frappe.whitelist(allow_guest=True)
def place_order(payload=None, **kwargs):
	request_data = parse_request_data(payload, **kwargs)
	if isinstance(request_data.get("payload"), dict):
		request_data = request_data["payload"]

	data = normalize_order_payload(request_data)
	data["order_source"] = "Web App"
	validate_order_payload(data, is_pos=False)
	validate_ordering_flags(data["order_type"])

	customer_context = run_as_order_system_user(get_or_create_order_customer, data["customer"])
	resolved_items = resolve_order_items(data["items"], order_type=data["order_type"])
	validate_minimum_order_amount(data, resolved_items)
	order_doc = run_as_order_system_user(build_sales_order, data, customer_context, resolved_items)

	return api_response(
		data={"order": serialize_order_summary(order_doc)},
		message="Order placed successfully.",
	)


@frappe.whitelist()
def create_pos_order(payload=None, **kwargs):
	ensure_staff_order_access(ptype="write")
	request_data = parse_request_data(payload, **kwargs)
	if isinstance(request_data.get("payload"), dict):
		request_data = request_data["payload"]

	data = normalize_order_payload(request_data)
	data["order_source"] = "POS"
	validate_order_payload(data, is_pos=True)
	validate_ordering_flags(data["order_type"])

	customer_payload = data["customer"] or {}
	if data.get("customer_name"):
		customer_payload["full_name"] = customer_payload.get("full_name") or data["customer_name"]

	customer_context = run_as_order_system_user(
		get_or_create_order_customer,
		customer_payload,
		allow_minimal_identity=True,
	)
	resolved_items = resolve_order_items(data["items"], order_type=data["order_type"])
	validate_minimum_order_amount(data, resolved_items)
	order_doc = run_as_order_system_user(build_sales_order, data, customer_context, resolved_items)

	return api_response(
		data={"order": serialize_order_summary(order_doc)},
		message="POS order created successfully.",
	)


@frappe.whitelist()
def apply_sales_order_workflow_action(sales_order: str, action: str):
	order_doc = apply_order_workflow_action(sales_order=sales_order, action=action)
	return api_response(
		data={
			"sales_order": order_doc.name,
			"current_state": getattr(order_doc, "kitchen_status", None) or order_doc.status,
			"order": serialize_order_summary(order_doc),
		},
		message="Workflow action applied successfully.",
	)


@frappe.whitelist()
def get_my_orders(limit_start: int = 0, limit_page_length: int = 20):
	customer = get_customer_for_user(get_current_portal_user())
	rows = get_customer_orders(customer, start=limit_start, page_length=limit_page_length)
	data = []
	for row in rows:
		item_rows = frappe.get_all(
			"Sales Order Item",
			filters={"parent": row.name},
			fields=["item_name", "qty"],
			order_by="idx asc",
			limit=3,
		)
		data.append(
			{
				"order_id": row.name,
				"date": str(row.transaction_date),
				"status": row.kitchen_status or row.status,
				"total": row.grand_total,
				"currency": row.currency,
				"payment_status": "Paid" if row.per_billed >= 100 else "Pending",
				"pickup_delivery_type": row.custom_order_type,
				"items_summary": [{"item_name": item.item_name, "qty": item.qty} for item in item_rows],
			}
		)

	return api_response(data=data)


@frappe.whitelist(allow_guest=True)
def get_order_status(order_id: str, email: str | None = None, phone: str | None = None):
	order_doc = get_order_doc_for_tracking(order_id=order_id, email=email, phone=phone)
	return api_response(data=serialize_order_tracking(order_doc))


@frappe.whitelist()
def get_admin_orders(
	kitchen_status: str | None = None,
	order_type: str | None = None,
	limit_start: int = 0,
	limit_page_length: int = 50,
):
	return api_response(
		data=get_admin_order_rows(
			kitchen_status=kitchen_status,
			order_type=order_type,
			start=limit_start,
			page_length=limit_page_length,
		)
	)


@frappe.whitelist()
def get_order_detail(sales_order: str):
	return api_response(data=get_order_detail_payload(sales_order))
