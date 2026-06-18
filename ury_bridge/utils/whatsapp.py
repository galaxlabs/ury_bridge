from __future__ import annotations

import frappe


def send_new_order_whatsapp_alert(order_doc) -> None:
	"""Send a WhatsApp notification to the restaurant's own number when a new order is created."""
	try:
		settings = frappe.get_single("Ury API Settings")

		if not settings.enable_whatsapp_order_alert:
			return

		account_sid = settings.twilio_account_sid or ""
		auth_token = settings.get_password("twilio_auth_token") or ""
		from_number = (settings.twilio_whatsapp_from or "").strip()
		alert_number = (settings.whatsapp_alert_number or "").strip()

		if not (account_sid and auth_token and from_number and alert_number):
			frappe.log_error(
				"WhatsApp order alert is enabled but Twilio credentials or alert number are not fully configured.",
				"WhatsApp Order Alert – Missing Config",
			)
			return

		# Normalise the destination number to whatsapp:+XXXX format
		if not alert_number.startswith("whatsapp:"):
			alert_number = f"whatsapp:{alert_number if alert_number.startswith('+') else '+' + alert_number}"

		# Build items summary
		items_lines = []
		for row in order_doc.items:
			items_lines.append(f"  - {row.item_name} × {int(row.qty)}  ({order_doc.currency} {row.amount:.2f})")
		items_text = "\n".join(items_lines) or "  (no items)"

		message = (
			f"🛎 *New Order Received* — {order_doc.name}\n"
			f"Customer : {order_doc.customer_name or 'N/A'}\n"
			f"Phone    : {order_doc.customer_phone or 'N/A'}\n"
			f"Type     : {order_doc.order_type}\n"
			f"Payment  : {order_doc.payment_method} ({order_doc.payment_status})\n"
			f"Total    : {order_doc.currency} {order_doc.total_amount:.2f}\n"
			f"Items:\n{items_text}"
		)
		if order_doc.special_instructions:
			message += f"\nNote: {order_doc.special_instructions}"

		_call_twilio(account_sid, auth_token, from_number, alert_number, message)

	except Exception:
		# Never let a notification failure break the order flow
		frappe.log_error(frappe.get_traceback(), "WhatsApp Order Alert – Error")


def _call_twilio(account_sid: str, auth_token: str, from_number: str, to_number: str, body: str) -> None:
	import base64
	import urllib.parse
	import urllib.request

	url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
	data = urllib.parse.urlencode({"From": from_number, "To": to_number, "Body": body}).encode()
	credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()

	req = urllib.request.Request(url, data=data, method="POST")
	req.add_header("Authorization", f"Basic {credentials}")
	req.add_header("Content-Type", "application/x-www-form-urlencoded")

	with urllib.request.urlopen(req, timeout=10) as resp:
		if resp.status not in (200, 201):
			body_text = resp.read().decode("utf-8", errors="replace")
			frappe.log_error(f"Twilio response {resp.status}: {body_text}", "WhatsApp Order Alert – Twilio Error")
