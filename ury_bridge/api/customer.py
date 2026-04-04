from __future__ import annotations

import frappe

from ury_bridge.utils.common import api_response, parse_request_data
from ury_bridge.utils.customer import (
	create_customer_address,
	ensure_customer_panel_enabled,
	get_current_portal_user,
	get_customer_for_user,
	get_recent_orders,
	list_customer_addresses as list_customer_address_rows,
	register_customer_account,
	serialize_address,
	update_customer_address_record,
)


@frappe.whitelist(allow_guest=True)
def register_customer(payload=None, **kwargs):
	data = parse_request_data(payload, **kwargs)
	result = register_customer_account(data)
	return api_response(
		data={
			"user": result["user"],
			"customer": result["customer"],
			"contact": result["contact"],
		},
		message="Customer account created successfully.",
	)


@frappe.whitelist(allow_guest=True)
def login_customer(email: str, password: str):
	ensure_customer_panel_enabled()
	frappe.local.login_manager.authenticate(user=email, pwd=password)
	frappe.local.login_manager.post_login()

	return api_response(
		data={
			"user": frappe.session.user,
			"is_authenticated": True,
		},
		message="Login successful.",
	)


@frappe.whitelist()
def logout_customer():
	frappe.local.login_manager.logout()
	return api_response(message="Logout successful.")


@frappe.whitelist(allow_guest=True)
def get_session_status():
	customer = get_customer_for_user()
	return api_response(
		data={
			"is_authenticated": frappe.session.user != "Guest",
			"user": None if frappe.session.user == "Guest" else frappe.session.user,
			"customer": customer,
		}
	)


@frappe.whitelist()
def get_my_profile():
	user = get_current_portal_user()
	customer = get_customer_for_user(user)
	addresses = list_customer_address_rows(customer) if customer else []
	recent_orders = get_recent_orders(customer) if customer else []
	user_doc = frappe.get_doc("User", user)

	return api_response(
		data={
			"full_name": user_doc.full_name,
			"email": user_doc.email,
			"mobile": user_doc.mobile_no,
			"linked_customer": customer,
			"saved_addresses_count": len(addresses),
			"recent_orders": [
				{
					"order_id": row.name,
					"date": str(row.creation),
					"status": row.order_status,
					"total": row.total_amount,
				}
				for row in recent_orders
			],
		}
	)


@frappe.whitelist()
def get_profile():
	return get_my_profile()


@frappe.whitelist()
def list_customer_addresses():
	customer = get_customer_for_user(get_current_portal_user())
	rows = list_customer_address_rows(customer)
	return api_response(data=[serialize_address(row) for row in rows])


@frappe.whitelist()
def save_customer_address(payload=None, **kwargs):
	customer = get_customer_for_user(get_current_portal_user())
	data = parse_request_data(payload, **kwargs)
	address = create_customer_address(customer, data)
	return api_response(data=serialize_address(address.as_dict()), message="Address saved successfully.")


@frappe.whitelist()
def update_customer_address(address_name: str, payload=None, **kwargs):
	customer = get_customer_for_user(get_current_portal_user())
	data = parse_request_data(payload, **kwargs)
	address = update_customer_address_record(customer, address_name, data)
	return api_response(data=serialize_address(address.as_dict()), message="Address updated successfully.")
