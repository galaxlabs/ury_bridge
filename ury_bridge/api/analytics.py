from __future__ import annotations

import frappe

from ury_bridge.utils.common import api_response, parse_request_data
from ury_bridge.utils.logging import log_product_view_event, log_uber_eats_click_event


@frappe.whitelist(allow_guest=True)
def log_product_view(payload=None, **kwargs):
	data = parse_request_data(payload, **kwargs)
	logged, log_name = log_product_view_event(data)
	return api_response(data={"logged": logged, "name": log_name})


@frappe.whitelist(allow_guest=True)
def log_uber_eats_click(payload=None, **kwargs):
	data = parse_request_data(payload, **kwargs)
	logged, log_name = log_uber_eats_click_event(data)
	return api_response(data={"logged": logged, "name": log_name})
