from __future__ import annotations

import json

import frappe

from ury_bridge.utils.common import api_response, parse_request_data
from ury_bridge.utils.customer import get_current_portal_user, get_customer_for_user
from ury_bridge.utils.invoice import create_sales_invoice_from_cozy_order as build_sales_invoice
from ury_bridge.utils.order import (
	apply_cozy_order_workflow_action as apply_order_action,
	build_cozy_order,
	create_pos_order_payload,
	ensure_staff_order_access,
	get_admin_orders as get_admin_order_rows,
	get_cozy_order_detail_payload,
	get_customer_orders,
	get_or_create_order_customer,
	get_order_doc_for_tracking,
	mark_pickup_paid as mark_pickup_order_paid,
	normalize_order_payload,
	resolve_order_items,
	run_as_order_system_user,
	serialize_cozy_order_summary,
	serialize_order_tracking,
	validate_minimum_order_amount,
	validate_order_payload,
	validate_ordering_flags,
)


@frappe.whitelist(allow_guest=True)
def place_order(payload=None, **kwargs):
	request_data = parse_request_data(payload, **kwargs)
	request_data = _unwrap_nested_payload(request_data)

	data = normalize_order_payload(request_data)
	data["order_source"] = request_data.get("order_source") or "Web App"
	validate_order_payload(data, is_pos=False)
	validate_ordering_flags(data["order_type"])

	customer_context = run_as_order_system_user(get_or_create_order_customer, data["customer"])
	resolved_items = resolve_order_items(data["items"], order_type=data["order_type"])
	validate_minimum_order_amount(data, resolved_items)
	order_doc = run_as_order_system_user(build_cozy_order, data, customer_context, resolved_items)

	return api_response(
		data={"order": serialize_cozy_order_summary(order_doc)},
		message="Order placed successfully.",
	)


@frappe.whitelist()
def create_pos_order(payload=None, **kwargs):
	ensure_staff_order_access(ptype="write")
	request_data = parse_request_data(payload, **kwargs)
	request_data = _unwrap_nested_payload(request_data)

	data = create_pos_order_payload(request_data)
	validate_order_payload(data, is_pos=True)
	validate_ordering_flags(data["order_type"])

	customer_payload = data["customer"] or {}
	customer_context = run_as_order_system_user(
		get_or_create_order_customer,
		customer_payload,
		allow_minimal_identity=True,
	)
	resolved_items = resolve_order_items(data["items"], order_type=data["order_type"])
	validate_minimum_order_amount(data, resolved_items)
	order_doc = run_as_order_system_user(build_cozy_order, data, customer_context, resolved_items)

	return api_response(
		data={"order": serialize_cozy_order_summary(order_doc)},
		message="POS order created successfully.",
	)


@frappe.whitelist()
def apply_cozy_order_workflow_action(order_name: str | None = None, action: str | None = None, cozy_order: str | None = None):
	order_name = order_name or cozy_order
	order_doc = apply_order_action(order_name=order_name, action=action)
	return api_response(
		data={
			"cozy_order": order_doc.name,
			"current_state": order_doc.order_status,
			"order": serialize_cozy_order_summary(order_doc),
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
			"Cozy Order Item",
			filters={"parent": row.name},
			fields=["item_name", "qty"],
			order_by="idx asc",
			limit=3,
		)
		data.append(
			{
				"order_id": row.name,
				"date": str(row.creation),
				"status": row.order_status,
				"total": row.total_amount,
				"currency": row.currency,
				"payment_status": row.payment_status,
				"pickup_delivery_type": row.order_type,
				"sales_invoice": row.sales_invoice,
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
	order_status: str | None = None,
	order_type: str | None = None,
	search: str | None = None,
	limit_start: int = 0,
	limit_page_length: int = 50,
):
	return api_response(
		data=get_admin_order_rows(
			order_status=order_status,
			order_type=order_type,
			search=search,
			start=limit_start,
			page_length=limit_page_length,
		)
	)


@frappe.whitelist()
def get_order_detail(order_name: str | None = None, cozy_order: str | None = None):
	return api_response(data=get_cozy_order_detail_payload(order_name or cozy_order))


@frappe.whitelist()
def mark_pickup_paid(order_name: str | None = None, cozy_order: str | None = None):
	order_doc = mark_pickup_order_paid(order_name or cozy_order)
	return api_response(data={"order": serialize_cozy_order_summary(order_doc)}, message="Pickup payment marked successfully.")


@frappe.whitelist()
def create_sales_invoice_from_cozy_order(order_name: str | None = None, cozy_order: str | None = None, submit_invoice: int | str = 1):
	invoice = build_sales_invoice(order_name or cozy_order, submit_invoice=bool(int(submit_invoice)))
	return api_response(
		data={"sales_invoice": invoice.name, "docstatus": invoice.docstatus},
		message="Sales Invoice created successfully.",
	)


def _unwrap_nested_payload(request_data):
	nested_payload = request_data.get("payload")
	if isinstance(nested_payload, dict):
		return nested_payload
	if isinstance(nested_payload, str) and nested_payload.strip():
		try:
			parsed_payload = json.loads(nested_payload)
			if isinstance(parsed_payload, dict):
				return parsed_payload
		except Exception:
			pass
	return request_data
