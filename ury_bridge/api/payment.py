from __future__ import annotations

import frappe

from ury_bridge.utils.common import api_response
from ury_bridge.utils.order import ensure_customer_owns_order, get_order_doc_for_tracking
from ury_bridge.utils.payment import create_stripe_checkout_session, process_stripe_webhook


@frappe.whitelist(allow_guest=True)
def create_checkout_session(order_name: str, email: str | None = None, phone: str | None = None):
	if frappe.session.user == "Guest":
		order_doc = get_order_doc_for_tracking(order_id=order_name, email=email, phone=phone)
	else:
		order_doc = ensure_customer_owns_order(frappe.get_doc("Cozy Order", order_name), allow_staff=True)

	session = create_stripe_checkout_session(order_doc.name)
	return api_response(
		data={
			"checkout_session_id": session.get("id"),
			"url": session.get("url"),
			"payment_intent_id": session.get("payment_intent"),
		},
		message="Checkout session created successfully.",
	)


@frappe.whitelist(allow_guest=True)
def stripe_webhook():
	payload = frappe.request.get_data(as_text=True) or ""
	signature = frappe.get_request_header("Stripe-Signature")
	result = process_stripe_webhook(payload, signature)
	return api_response(data=result, message="Webhook processed.")
