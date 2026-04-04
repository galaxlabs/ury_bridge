from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


WORKFLOW_NAME = "Cozy Kitchen Sales Order Flow"
WORKFLOW_STATE_FIELD = "kitchen_status"


def execute():
	ensure_sales_order_custom_fields()
	ensure_workflow_states()
	ensure_sales_order_workflow()


def ensure_sales_order_custom_fields() -> None:
	custom_fields = {
		"Sales Order": [
			{
				"fieldname": "custom_order_source",
				"label": "Order Source",
				"fieldtype": "Select",
				"options": "Web App\nMobile App\nPOS\nAdmin",
				"insert_after": "kitchen_status",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_order_type",
				"label": "Order Type",
				"fieldtype": "Select",
				"options": "Delivery\nPickup",
				"insert_after": "custom_order_source",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_customer_phone",
				"label": "Customer Phone",
				"fieldtype": "Data",
				"insert_after": "custom_order_type",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_customer_email",
				"label": "Customer Email",
				"fieldtype": "Data",
				"insert_after": "custom_customer_phone",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_delivery_address",
				"label": "Delivery Address",
				"fieldtype": "Small Text",
				"insert_after": "custom_customer_email",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_order_note",
				"label": "Order Note",
				"fieldtype": "Small Text",
				"insert_after": "custom_delivery_address",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_app_order_id",
				"label": "App Order ID",
				"fieldtype": "Data",
				"insert_after": "custom_order_note",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_payment_method",
				"label": "Payment Method",
				"fieldtype": "Select",
				"options": "Cash\nCard\nApple Pay\nGoogle Pay",
				"insert_after": "custom_app_order_id",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_allergy_confirmation",
				"label": "Allergy Confirmation",
				"fieldtype": "Check",
				"insert_after": "custom_payment_method",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_requested_time",
				"label": "Requested Time",
				"fieldtype": "Datetime",
				"insert_after": "custom_allergy_confirmation",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_delivery_fee",
				"label": "Delivery Fee",
				"fieldtype": "Currency",
				"insert_after": "custom_requested_time",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "custom_discount_amount",
				"label": "Discount Amount",
				"fieldtype": "Currency",
				"insert_after": "custom_delivery_fee",
				"allow_on_submit": 1,
			},
		]
	}
	create_custom_fields(custom_fields, ignore_validate=True)


def ensure_workflow_states() -> None:
	states = {
		"Pending": "Warning",
		"Confirmed": "Info",
		"Preparing": "Primary",
		"Ready": "Success",
		"Out for Delivery": "Primary",
		"Completed": "Success",
		"Cancelled": "Danger",
		"Rejected": "Danger",
	}

	for name, style in states.items():
		if frappe.db.exists("Workflow State", name):
			if style:
				frappe.db.set_value("Workflow State", name, "style", style, update_modified=False)
			continue

		doc = frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": name, "style": style})
		doc.insert(ignore_permissions=True)


def ensure_sales_order_workflow() -> None:
	workflow = frappe.get_doc("Workflow", WORKFLOW_NAME) if frappe.db.exists("Workflow", WORKFLOW_NAME) else frappe.new_doc("Workflow")
	workflow.workflow_name = WORKFLOW_NAME
	workflow.document_type = "Sales Order"
	workflow.workflow_state_field = WORKFLOW_STATE_FIELD
	workflow.is_active = 1
	workflow.override_status = 0
	workflow.send_email_alert = 0

	workflow.states = []
	for state in [
		"Pending",
		"Confirmed",
		"Preparing",
		"Ready",
		"Out for Delivery",
		"Completed",
		"Cancelled",
		"Rejected",
	]:
		workflow.append(
			"states",
			{
				"state": state,
				"doc_status": "0",
				"allow_edit": "Desk User",
			},
		)

	workflow.transitions = []
	for transition in [
		("Pending", "Confirm", "Confirmed"),
		("Pending", "Reject", "Rejected"),
		("Pending", "Cancel", "Cancelled"),
		("Confirmed", "Start Preparing", "Preparing"),
		("Confirmed", "Cancel", "Cancelled"),
		("Preparing", "Mark Ready", "Ready"),
		("Preparing", "Cancel", "Cancelled"),
		("Ready", "Dispatch", "Out for Delivery"),
		("Ready", "Complete Pickup", "Completed"),
		("Ready", "Cancel", "Cancelled"),
		("Out for Delivery", "Complete Delivery", "Completed"),
		("Out for Delivery", "Cancel", "Cancelled"),
	]:
		state, action, next_state = transition
		workflow.append(
			"transitions",
			{
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": "Desk User",
			},
		)

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)
