from __future__ import annotations

import frappe


WORKFLOW_NAME = "Cozy Kitchen Cozy Order Flow"
OLD_WORKFLOW_NAME = "Cozy Kitchen Sales Order Flow"
WORKFLOW_STATE_FIELD = "order_status"

OLD_SALES_ORDER_CUSTOM_FIELDS = [
	"kitchen_status",
	"custom_order_source",
	"custom_order_type",
	"custom_customer_phone",
	"custom_customer_email",
	"custom_delivery_address",
	"custom_order_note",
	"custom_app_order_id",
	"custom_payment_method",
	"custom_allergy_confirmation",
	"custom_requested_time",
	"custom_delivery_fee",
	"custom_discount_amount",
]


def execute():
	deactivate_old_sales_order_workflow()
	ensure_workflow_states()
	ensure_cozy_order_workflow()
	remove_old_sales_order_custom_fields()


def deactivate_old_sales_order_workflow() -> None:
	if not frappe.db.exists("Workflow", OLD_WORKFLOW_NAME):
		return
	workflow = frappe.get_doc("Workflow", OLD_WORKFLOW_NAME)
	workflow.is_active = 0
	workflow.save(ignore_permissions=True)


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
			frappe.db.set_value("Workflow State", name, "style", style, update_modified=False)
			continue
		doc = frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": name, "style": style})
		doc.insert(ignore_permissions=True)


def ensure_cozy_order_workflow() -> None:
	workflow = frappe.get_doc("Workflow", WORKFLOW_NAME) if frappe.db.exists("Workflow", WORKFLOW_NAME) else frappe.new_doc("Workflow")
	workflow.workflow_name = WORKFLOW_NAME
	workflow.document_type = "Cozy Order"
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
	for state, action, next_state in [
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
		workflow.append(
			"transitions",
			{
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": "Desk User",
				"allow_self_approval": 1,
			},
		)

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)


def remove_old_sales_order_custom_fields() -> None:
	for fieldname in OLD_SALES_ORDER_CUSTOM_FIELDS:
		custom_field_name = f"Sales Order-{fieldname}"
		if frappe.db.exists("Custom Field", custom_field_name):
			frappe.delete_doc("Custom Field", custom_field_name, ignore_permissions=True)
