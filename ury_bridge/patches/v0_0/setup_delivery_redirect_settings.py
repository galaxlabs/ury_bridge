from __future__ import annotations

import frappe


def execute():
	settings = frappe.get_single("Ury Website Settings")
	changed = False

	defaults = {
		"enable_uber_eats_redirect": 1,
		"uber_eats_redirect_message": "You will complete delivery order on Uber Eats",
		"delivery_cta_label": "Delivery with Uber Eats",
		"pickup_cta_label": "Pickup from Cozy Kitchen",
	}

	for fieldname, value in defaults.items():
		if settings.get(fieldname) in (None, ""):
			settings.set(fieldname, value)
			changed = True

	if changed:
		settings.save(ignore_permissions=True)
