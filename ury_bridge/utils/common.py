from __future__ import annotations

import json
import re
from typing import Any

import frappe
from frappe.utils import cint


def api_response(
	data: Any = None,
	message: str = "OK",
	meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
	return {
		"success": True,
		"message": message,
		"data": data,
		"meta": meta or {},
	}


def api_error(
	message: str,
	code: str = "VALIDATION_ERROR",
	details: dict[str, Any] | None = None,
) -> dict[str, Any]:
	return {
		"success": False,
		"message": message,
		"error": {
			"code": code,
			"details": details or {},
		},
	}


def parse_request_data(payload: Any = None, **kwargs: Any) -> dict[str, Any]:
	if payload is None:
		request_json = frappe.request.get_json(silent=True) if getattr(frappe, "request", None) else None
		if isinstance(request_json, dict):
			payload = request_json

	if payload is None and getattr(frappe, "request", None):
		raw_body = frappe.request.get_data(as_text=True) or ""
		raw_body = raw_body.strip()
		if raw_body:
			try:
				parsed_body = json.loads(raw_body)
				if isinstance(parsed_body, dict):
					payload = parsed_body
			except Exception:
				pass

	if isinstance(payload, str) and payload.strip():
		payload = json.loads(payload)

	if payload is None:
		payload = {}

	if not isinstance(payload, dict):
		frappe.throw("Payload must be a JSON object.")

	filtered_kwargs = {
		key: value
		for key, value in kwargs.items()
		if key not in {"cmd", "data", "_", "csrf_token"}
	}

	return {**payload, **filtered_kwargs}


def as_bool(value: Any, default: bool = False) -> bool:
	if value is None:
		return default

	if isinstance(value, bool):
		return value

	if isinstance(value, str):
		return value.strip().lower() in {"1", "true", "yes", "y", "on"}

	return bool(cint(value))


def get_single_doc(doctype: str):
	if not frappe.db.exists("DocType", doctype):
		frappe.throw(f"{doctype} DocType is missing.")

	return frappe.get_single(doctype)


def get_api_settings():
	return get_single_doc("Ury API Settings")


def get_website_settings():
	return get_single_doc("Ury Website Settings")


def ensure_public_menu_api_enabled() -> None:
	settings = get_api_settings()
	if not cint(settings.enable_public_menu_api):
		frappe.throw("Public menu API is disabled.")


def ensure_public_order_api_enabled() -> None:
	settings = get_api_settings()
	if not cint(settings.enable_public_order_api):
		frappe.throw("Public order API is disabled.")


def format_time_value(value: Any) -> str | None:
	if not value:
		return None
	return str(value)


def normalize_file_url(file_url: str | None) -> str | None:
	if not file_url:
		return None

	if file_url.startswith(("http://", "https://")):
		return file_url

	base_url = (get_api_settings().erp_public_base_url or "").rstrip("/")
	if not base_url:
		return file_url

	return f"{base_url}{file_url}"


def slugify_value(value: str | None) -> str:
	text = (value or "").strip().lower()
	text = re.sub(r"[^a-z0-9]+", "-", text)
	return text.strip("-")


def get_default_company() -> str:
	company = get_api_settings().default_company
	if not company:
		frappe.throw("Default Company is not configured in Ury API Settings.")
	return company


def get_default_price_list() -> str:
	price_list = get_api_settings().default_price_list
	if not price_list:
		frappe.throw("Default Price List is not configured in Ury API Settings.")
	return price_list


def get_default_currency() -> str | None:
	return get_api_settings().default_currency
