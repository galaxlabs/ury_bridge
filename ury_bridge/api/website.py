from __future__ import annotations

import frappe

from ury_bridge.utils.common import api_response
from ury_bridge.utils.logging import get_website_delivery_options_payload


@frappe.whitelist(allow_guest=True)
def get_website_delivery_options():
	return api_response(data=get_website_delivery_options_payload())
