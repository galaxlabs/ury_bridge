from __future__ import annotations

import frappe

from ury_bridge.utils.common import api_response, as_bool
from ury_bridge.utils.menu import (
	get_active_categories,
	get_menu_product_by_slug,
	get_menu_products as get_menu_product_rows,
	serialize_menu_category,
	serialize_menu_product_card,
	serialize_menu_product_detail,
)


@frappe.whitelist(allow_guest=True)
def get_menu_categories():
	rows = get_active_categories()
	return api_response(data=[serialize_menu_category(row) for row in rows])


@frappe.whitelist(allow_guest=True)
def get_menu_products_list(
	category: str | None = None,
	featured: str | None = None,
	active_only: str | None = "1",
	pickup_only: str | None = "0",
):
	rows = get_menu_product_rows(
		{
			"category": category,
			"featured": as_bool(featured, default=False),
			"active_only": as_bool(active_only, default=True),
			"pickup_only": as_bool(pickup_only, default=False),
			"allow_order_only": True,
		}
	)
	return api_response(data=[serialize_menu_product_card(row) for row in rows])


@frappe.whitelist(allow_guest=True)
def get_menu_products(
	category: str | None = None,
	featured: str | None = None,
	active_only: str | None = "1",
	pickup_only: str | None = "0",
):
	return get_menu_products_list(
		category=category,
		featured=featured,
		active_only=active_only,
		pickup_only=pickup_only,
	)


@frappe.whitelist(allow_guest=True)
def get_menu_product_detail(slug: str):
	doc = get_menu_product_by_slug(slug)
	return api_response(data=serialize_menu_product_detail(doc))
