from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from urllib import error, parse, request

import frappe
from frappe import _
from frappe.utils import now_datetime

from ury_bridge.utils.common import get_api_settings
from ury_bridge.utils.order import (
	COZY_ORDER_DOCTYPE,
	mark_order_paid,
	maybe_auto_confirm_paid_order,
)


STRIPE_API_BASE = "https://api.stripe.com/v1"


def get_gateway_provider() -> str:
	return (get_api_settings().payment_gateway_provider or "Stripe").strip()


def get_stripe_credentials() -> tuple[str, str | None]:
	settings = get_api_settings()
	secret_key = settings.get_password("stripe_secret_key") if settings.stripe_secret_key else None
	webhook_secret = settings.get_password("stripe_webhook_secret") if settings.stripe_webhook_secret else None
	if not secret_key:
		frappe.throw(_("Stripe secret key is not configured in Ury API Settings."))
	return secret_key, webhook_secret


def create_stripe_checkout_session(order_name: str) -> dict[str, Any]:
	order_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_name)
	secret_key, _ = get_stripe_credentials()
	success_url = get_api_settings().stripe_success_url or "{0}/checkout/success?order_id={1}".format(
		get_api_settings().frontend_base_url.rstrip("/"),
		order_doc.name,
	)
	cancel_url = get_api_settings().stripe_cancel_url or "{0}/checkout/cancel?order_id={1}".format(
		get_api_settings().frontend_base_url.rstrip("/"),
		order_doc.name,
	)

	form_body = {
		"mode": "payment",
		"success_url": success_url,
		"cancel_url": cancel_url,
		"client_reference_id": order_doc.name,
		"metadata[cozy_order]": order_doc.name,
		"payment_intent_data[metadata][cozy_order]": order_doc.name,
	}
	for index, row in enumerate(order_doc.items):
		form_body[f"line_items[{index}][price_data][currency]"] = (order_doc.currency or "GBP").lower()
		form_body[f"line_items[{index}][price_data][product_data][name]"] = row.item_name
		form_body[f"line_items[{index}][price_data][unit_amount]"] = str(int(round(float(row.rate) * 100)))
		form_body[f"line_items[{index}][quantity]"] = str(int(row.qty))

	if order_doc.delivery_fee:
		index = len(order_doc.items)
		form_body[f"line_items[{index}][price_data][currency]"] = (order_doc.currency or "GBP").lower()
		form_body[f"line_items[{index}][price_data][product_data][name]"] = "Delivery Fee"
		form_body[f"line_items[{index}][price_data][unit_amount]"] = str(int(round(float(order_doc.delivery_fee) * 100)))
		form_body[f"line_items[{index}][quantity]"] = "1"

	response = stripe_api_post("/checkout/sessions", form_body, secret_key)
	order_doc.db_set("checkout_session_id", response.get("id"), update_modified=False)
	if response.get("payment_intent"):
		order_doc.db_set("payment_intent_id", response.get("payment_intent"), update_modified=False)
	order_doc.reload()
	return response


def stripe_api_post(path: str, form_body: dict[str, Any], secret_key: str) -> dict[str, Any]:
	encoded = parse.urlencode(form_body).encode()
	req = request.Request(
		f"{STRIPE_API_BASE}{path}",
		data=encoded,
		headers={
			"Authorization": f"Bearer {secret_key}",
			"Content-Type": "application/x-www-form-urlencoded",
		},
		method="POST",
	)
	try:
		with request.urlopen(req, timeout=30) as response:
			return json.loads(response.read().decode())
	except error.HTTPError as exc:
		body = exc.read().decode()
		frappe.throw(_("Stripe API error: {0}").format(body or exc.reason))


def verify_stripe_webhook(payload: str, signature_header: str | None, webhook_secret: str | None) -> None:
	if not webhook_secret:
		frappe.throw(_("Stripe webhook secret is not configured."))
	if not signature_header:
		frappe.throw(_("Missing Stripe signature header."))

	parts = dict(part.split("=", 1) for part in signature_header.split(",") if "=" in part)
	timestamp = parts.get("t")
	signature = parts.get("v1")
	if not timestamp or not signature:
		frappe.throw(_("Invalid Stripe signature header."))

	signed_payload = f"{timestamp}.{payload}".encode()
	expected_signature = hmac.new(webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
	if not hmac.compare_digest(expected_signature, signature):
		frappe.throw(_("Stripe webhook signature verification failed."))


def process_stripe_webhook(payload: str, signature_header: str | None) -> dict[str, Any]:
	secret_key, webhook_secret = get_stripe_credentials()
	verify_stripe_webhook(payload, signature_header, webhook_secret)

	event = json.loads(payload)
	event_type = event.get("type")
	data_object = ((event.get("data") or {}).get("object")) or {}
	order_name = (
		(data_object.get("metadata") or {}).get("cozy_order")
		or data_object.get("client_reference_id")
	)
	if not order_name:
		return {"processed": False, "reason": "No cozy_order metadata found."}
	if not frappe.db.exists(COZY_ORDER_DOCTYPE, order_name):
		return {"processed": False, "reason": "Cozy Order not found."}

	order_doc = frappe.get_doc(COZY_ORDER_DOCTYPE, order_name)
	order_doc.payment_gateway = get_gateway_provider()

	if data_object.get("payment_intent"):
		order_doc.payment_intent_id = data_object.get("payment_intent")
	if data_object.get("id") and event_type == "checkout.session.completed":
		order_doc.checkout_session_id = data_object.get("id")
	if data_object.get("charges", {}).get("data"):
		charge = data_object["charges"]["data"][0]
		order_doc.gateway_charge_id = charge.get("id")
	order_doc.save(ignore_permissions=True)

	if event_type in {"checkout.session.completed", "payment_intent.succeeded", "charge.succeeded"}:
		order_doc = mark_order_paid(order_name, payment_method=order_doc.payment_method)
		order_doc.payment_gateway = get_gateway_provider()
		order_doc.payment_confirmed_at = now_datetime()
		order_doc.save(ignore_permissions=True)
		order_doc = maybe_auto_confirm_paid_order(order_doc)
		return {"processed": True, "event_type": event_type, "order_status": order_doc.order_status, "payment_status": order_doc.payment_status}

	if event_type in {"payment_intent.payment_failed", "charge.failed"}:
		order_doc.payment_status = "Failed"
		order_doc.save(ignore_permissions=True)
		return {"processed": True, "event_type": event_type, "order_status": order_doc.order_status, "payment_status": order_doc.payment_status}

	return {"processed": True, "event_type": event_type, "order_status": order_doc.order_status, "payment_status": order_doc.payment_status}
