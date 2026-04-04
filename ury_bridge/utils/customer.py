from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from ury_bridge.utils.common import get_api_settings


def ensure_customer_panel_enabled() -> None:
	if not get_api_settings().enable_customer_panel:
		frappe.throw("Customer panel is disabled.")


def get_current_portal_user() -> str:
	if frappe.session.user == "Guest":
		frappe.throw(_("Login required."))
	return frappe.session.user


def get_customer_for_user(user: str | None = None) -> str | None:
	user = user or frappe.session.user
	if not user or user == "Guest":
		return None

	return find_customer_by_email_or_phone(email=user)


def get_customer_master_defaults() -> dict[str, str | None]:
	customer_group = frappe.db.get_single_value("Selling Settings", "customer_group")
	territory = frappe.db.get_single_value("Selling Settings", "territory")

	return {
		"customer_group": customer_group,
		"territory": territory or "All Territories",
	}


def validate_customer_identity(email: str, mobile_no: str | None = None) -> None:
	if frappe.db.exists("User", email):
		frappe.throw(_("A user already exists with this email address."))

	if mobile_no and frappe.db.exists("Contact Phone", {"phone": mobile_no}):
		frappe.throw(_("A contact already exists with this phone number."))


def register_customer_account(payload: dict[str, Any]) -> dict[str, Any]:
	ensure_customer_panel_enabled()

	email = (payload.get("email") or "").strip().lower()
	password = payload.get("password")
	full_name = (payload.get("full_name") or "").strip()
	mobile_no = (payload.get("mobile") or "").strip()

	if not email or not password or not full_name:
		frappe.throw("full_name, email, and password are required.")

	validate_customer_identity(email=email, mobile_no=mobile_no)

	user = create_website_user(email=email, password=password, full_name=full_name, mobile_no=mobile_no)
	customer = create_customer_record(full_name=full_name)
	contact = ensure_customer_contact(customer=customer.name, full_name=full_name, email=email, mobile_no=mobile_no)

	return {
		"user": user.name,
		"customer": customer.name,
		"contact": contact.name if contact else None,
	}


def create_website_user(email: str, password: str, full_name: str, mobile_no: str | None = None):
	first_name, *rest = full_name.split(" ", 1)
	last_name = rest[0] if rest else ""

	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": first_name,
			"last_name": last_name,
			"full_name": full_name,
			"user_type": "Website User",
			"send_welcome_email": 0,
			"enabled": 1,
			"language": "en-GB",
			"mobile_no": mobile_no,
			"new_password": password,
		}
	)
	user.insert(ignore_permissions=True)
	return user


def create_customer_record(full_name: str):
	defaults = get_customer_master_defaults()
	customer = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": full_name,
			"customer_type": "Individual",
			"customer_group": defaults.get("customer_group"),
			"territory": defaults.get("territory"),
		}
	)
	customer.insert(ignore_permissions=True)
	return customer


def find_customer_by_email_or_phone(email: str | None = None, mobile_no: str | None = None) -> str | None:
	contact_names = set()

	if email:
		contact_names.update(frappe.get_all("Contact Email", filters={"email_id": email}, pluck="parent"))

	if mobile_no:
		contact_names.update(frappe.get_all("Contact Phone", filters={"phone": mobile_no}, pluck="parent"))

	if not contact_names:
		return None

	return frappe.db.get_value(
		"Dynamic Link",
		{
			"parenttype": "Contact",
			"parent": ["in", list(contact_names)],
			"link_doctype": "Customer",
		},
		"link_name",
	)


def ensure_customer_contact(
	customer: str,
	full_name: str,
	email: str | None = None,
	mobile_no: str | None = None,
):
	existing_contact_names = frappe.get_all(
		"Dynamic Link",
		filters={
			"link_doctype": "Customer",
			"link_name": customer,
			"parenttype": "Contact",
		},
		pluck="parent",
	)
	if existing_contact_names:
		contact = frappe.get_doc("Contact", existing_contact_names[0])
		if email and not any(row.email_id == email for row in contact.email_ids):
			contact.append("email_ids", {"email_id": email, "is_primary": 1 if not contact.email_ids else 0})
		if mobile_no and not any(row.phone == mobile_no for row in contact.phone_nos):
			contact.append("phone_nos", {"phone": mobile_no, "is_primary_mobile_no": 1 if not contact.phone_nos else 0})
		contact.save(ignore_permissions=True)
		return contact

	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": full_name,
			"is_primary_contact": 1,
			"is_billing_contact": 1,
			"email_ids": [{"email_id": email, "is_primary": 1}] if email else [],
			"phone_nos": [{"phone": mobile_no, "is_primary_mobile_no": 1}] if mobile_no else [],
			"links": [{"link_doctype": "Customer", "link_name": customer}],
		}
	)
	contact.insert(ignore_permissions=True)
	return contact


def list_customer_addresses(customer: str) -> list[dict[str, Any]]:
	address_names = frappe.get_all(
		"Dynamic Link",
		filters={
			"link_doctype": "Customer",
			"link_name": customer,
			"parenttype": "Address",
		},
		pluck="parent",
	)

	if not address_names:
		return []

	return frappe.get_all(
		"Address",
		filters={"name": ["in", address_names]},
		fields=[
			"name",
			"address_title",
			"address_type",
			"address_line1",
			"address_line2",
			"city",
			"county",
			"state",
			"pincode",
			"country",
			"is_primary_address",
		],
		order_by="is_primary_address desc, modified desc",
	)


def create_customer_address(customer: str, payload: dict[str, Any]):
	address = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": payload.get("label") or customer,
			"address_type": payload.get("address_type") or "Personal",
			"address_line1": payload.get("address_line1"),
			"address_line2": payload.get("address_line2"),
			"city": payload.get("city"),
			"county": payload.get("county"),
			"state": payload.get("state"),
			"pincode": payload.get("pincode"),
			"country": payload.get("country") or "United Kingdom",
			"is_primary_address": 1 if payload.get("is_primary") else 0,
			"links": [{"link_doctype": "Customer", "link_name": customer}],
		}
	)
	address.insert(ignore_permissions=True)
	return address


def update_customer_address_record(customer: str, address_name: str, payload: dict[str, Any]):
	if not frappe.db.exists(
		"Dynamic Link",
		{
			"parenttype": "Address",
			"parent": address_name,
			"link_doctype": "Customer",
			"link_name": customer,
		},
	):
		frappe.throw("Address not found.")

	address = frappe.get_doc("Address", address_name)
	for fieldname, source_key in {
		"address_title": "label",
		"address_type": "address_type",
		"address_line1": "address_line1",
		"address_line2": "address_line2",
		"city": "city",
		"county": "county",
		"state": "state",
		"pincode": "pincode",
		"country": "country",
	}.items():
		if source_key in payload:
			address.set(fieldname, payload.get(source_key))

	if "is_primary" in payload:
		address.is_primary_address = 1 if payload.get("is_primary") else 0

	address.save(ignore_permissions=True)
	return address


def serialize_address(doc: dict[str, Any]) -> dict[str, Any]:
	return {
		"name": doc.get("name"),
		"label": doc.get("address_title"),
		"address_type": doc.get("address_type"),
		"address_line1": doc.get("address_line1"),
		"address_line2": doc.get("address_line2"),
		"city": doc.get("city"),
		"county": doc.get("county"),
		"state": doc.get("state"),
		"pincode": doc.get("pincode"),
		"country": doc.get("country"),
		"is_primary": bool(doc.get("is_primary_address")),
	}


def get_recent_orders(customer: str, limit: int = 5) -> list[dict[str, Any]]:
	return frappe.get_all(
		"Cozy Order",
		filters={"customer": customer},
		fields=["name", "creation", "order_status", "total_amount"],
		order_by="creation desc",
		limit=limit,
	)
