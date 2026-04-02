from __future__ import annotations

import frappe

from ury_bridge.utils.common import api_response, parse_request_data
from ury_bridge.utils.customer import get_current_portal_user, get_customer_for_user
from ury_bridge.utils.order import (
	build_sales_order,
	get_customer_orders,
	get_order_doc_for_tracking,
	get_or_create_order_customer,
	normalize_order_payload,
	resolve_order_items,
	serialize_order_summary,
	serialize_order_tracking,
	validate_order_payload,
	validate_ordering_flags,
)


@frappe.whitelist(allow_guest=True)
def place_order(payload=None, **kwargs):
	data = normalize_order_payload(parse_request_data(payload, **kwargs))
	validate_order_payload(data)
	validate_ordering_flags(data["order_type"])

	customer_context = get_or_create_order_customer(data["customer"])
	resolved_items = resolve_order_items(data["items"], order_type=data["order_type"])
	order_doc = build_sales_order(data, customer_context, resolved_items)

	return api_response(
		data={
			"order": serialize_order_summary(order_doc),
		},
		message="Order placed successfully.",
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
				"status": row.status,
				"total": row.grand_total,
				"currency": row.currency,
				"payment_status": "Paid" if row.per_billed >= 100 else "Pending",
				"order_type": row.custom_order_type,
				"items_summary": [
					{
						"item_name": item.item_name,
						"qty": item.qty,
					}
					for item in item_rows
				],
			}
		)

	return api_response(data=data)


@frappe.whitelist(allow_guest=True)
def get_order_status(order_id: str, email: str | None = None, phone: str | None = None):
	order_doc = get_order_doc_for_tracking(order_id=order_id, email=email, phone=phone)
	return api_response(data=serialize_order_tracking(order_doc))
