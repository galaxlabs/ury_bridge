from __future__ import annotations

import frappe

from ury_bridge.utils.common import (
	api_response,
	format_time_value,
	get_website_settings,
	normalize_file_url,
)


@frappe.whitelist(allow_guest=True)
def get_public_settings():
	settings = get_website_settings()

	return api_response(
		data={
			"brand_name": settings.brand_name,
			"logo": normalize_file_url(settings.logo),
			"favicon": normalize_file_url(settings.favicon),
			"support_phone": settings.support_phone,
			"whatsapp_number": settings.whatsapp_number,
			"support_email": settings.support_email,
			"contact_address": settings.contact_address,
			"google_maps_link": settings.google_maps_link,
			"instagram_url": settings.instagram_url,
			"facebook_url": settings.facebook_url,
			"opening_time": format_time_value(settings.opening_time),
			"closing_time": format_time_value(settings.closing_time),
			"open_days_text": settings.open_days_text,
			"enable_ordering": bool(settings.enable_ordering),
			"enable_pickup": bool(settings.enable_pickup),
			"enable_delivery": bool(settings.enable_delivery),
			"hero_title": settings.hero_title,
			"hero_subtitle": settings.hero_subtitle,
			"hero_cta_text": settings.hero_cta_text,
			"footer_text": settings.footer_text,
			"meta_title": settings.meta_title,
			"meta_description": settings.meta_description,
		}
	)
