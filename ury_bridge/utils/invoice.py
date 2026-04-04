from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from ury_bridge.utils.common import get_api_settings
from ury_bridge.utils.order import COZY_ORDER_DOCTYPE, ensure_staff_order_access


def create_sales_invoice_from_cozy_order(order_name: str, submit_invoice: bool = True):
	ensure_staff_order_access(ptype="write")
	order_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_name)
	if order_doc.sales_invoice:
		return frappe.get_doc("Sales Invoice", order_doc.sales_invoice)

	if order_doc.order_status in {"Cancelled", "Rejected"}:
		frappe.throw(_("Cancelled or rejected orders cannot be invoiced."))

	if order_doc.order_status != "Completed":
		frappe.throw(_("Only completed cozy orders can be invoiced in this MVP flow."))

	invoice = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"customer": order_doc.customer,
			"company": order_doc.company,
			"currency": order_doc.currency,
			"selling_price_list": order_doc.price_list,
			"set_posting_time": 1,
			"posting_date": frappe.utils.today(),
			"due_date": frappe.utils.today(),
			"update_stock": 0,
			"remarks": build_invoice_remarks(order_doc),
			"items": [
				{
					"item_code": row.item_code,
					"item_name": row.item_name,
					"qty": flt(row.qty),
					"rate": flt(row.rate),
					"amount": flt(row.amount),
				}
				for row in order_doc.items
			],
		}
	)

	add_delivery_fee_item(invoice, order_doc)
	if flt(order_doc.discount_amount):
		invoice.additional_discount_amount = flt(order_doc.discount_amount)
		invoice.apply_discount_on = "Grand Total"

	invoice.insert(ignore_permissions=True)
	if submit_invoice:
		invoice.submit()

	order_doc.db_set("sales_invoice", invoice.name, update_modified=False)
	order_doc.reload()
	return invoice


def add_delivery_fee_item(invoice, order_doc) -> None:
	if not flt(order_doc.delivery_fee):
		return
	item_code = get_api_settings().delivery_charge_item
	if not item_code:
		frappe.throw(_("Delivery charge item is required in Ury API Settings when delivery fee is used."))
	invoice.append(
		"items",
		{
			"item_code": item_code,
			"qty": 1,
			"rate": flt(order_doc.delivery_fee),
			"amount": flt(order_doc.delivery_fee),
		},
	)


def build_invoice_remarks(order_doc) -> str:
	return "Cozy Order {0} | {1} | Payment Status: {2}".format(
		order_doc.name,
		order_doc.order_type,
		order_doc.payment_status,
	)
