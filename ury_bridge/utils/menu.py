from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint, flt

from ury_bridge.utils.common import (
	ensure_public_menu_api_enabled,
	get_default_currency,
	get_default_price_list,
	normalize_file_url,
)


MENU_CATEGORY_FIELDS = [
	"name",
	"title",
	"slug",
	"image",
	"description",
	"is_featured",
	"sort_order",
]

MENU_PRODUCT_FIELDS = [
	"name",
	"title",
	"slug",
	"image",
	"short_description",
	"long_description",
	"allergy_alert",
	"halal_badge_text",
	"spicy_level",
	"display_label",
	"is_featured",
	"is_active",
	"allow_order",
	"available_for_pickup",
	"available_for_delivery",
	"product_type",
	"mapped_item",
	"mapped_bundle",
	"sort_order",
	"menu_category",
]


def get_active_categories() -> list[dict[str, Any]]:
	ensure_public_menu_api_enabled()
	return frappe.get_all(
		"Menu Category",
		filters={"is_active": 1},
		fields=MENU_CATEGORY_FIELDS,
		order_by="sort_order asc, title asc",
	)


def get_menu_products(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
	ensure_public_menu_api_enabled()
	filters = filters or {}

	db_filters: dict[str, Any] = {"is_active": 1}
	if filters.get("featured") is True:
		db_filters["is_featured"] = 1
	if filters.get("pickup_only") is True:
		db_filters["available_for_pickup"] = 1
	if filters.get("allow_order_only", True):
		db_filters["allow_order"] = 1

	category = filters.get("category")
	if category:
		db_filters["menu_category"] = ["in", get_category_names_from_filter(category)]

	return frappe.get_all(
		"Menu Product",
		filters=db_filters,
		fields=MENU_PRODUCT_FIELDS,
		order_by="sort_order asc, title asc",
	)


def get_category_names_from_filter(category: str) -> list[str]:
	rows = frappe.get_all(
		"Menu Category",
		filters=[["name", "=", category], ["slug", "=", category], ["title", "=", category]],
		or_filters=[["name", "=", category], ["slug", "=", category], ["title", "=", category]],
		pluck="name",
	)
	return rows or [category]


def get_menu_product_by_slug(slug: str):
	ensure_public_menu_api_enabled()
	name = frappe.db.get_value("Menu Product", {"slug": slug, "is_active": 1}, "name")
	if not name:
		frappe.throw("Menu product not found.")
	return frappe.get_doc("Menu Product", name)


def serialize_menu_category(doc: dict[str, Any]) -> dict[str, Any]:
	return {
		"title": doc.get("title"),
		"slug": doc.get("slug"),
		"image": normalize_file_url(doc.get("image")),
		"description": doc.get("description"),
		"is_featured": bool(cint(doc.get("is_featured"))),
		"sort_order": cint(doc.get("sort_order")),
	}


def serialize_menu_product_card(doc: dict[str, Any]) -> dict[str, Any]:
	category = get_category_summary(doc.get("menu_category"))
	price = get_product_base_price(doc)
	return {
		"title": doc.get("title"),
		"slug": doc.get("slug"),
		"image": normalize_file_url(doc.get("image")),
		"short_description": doc.get("short_description"),
		"selling_price": price,
		"allergy_alert": doc.get("allergy_alert"),
		"halal_badge_text": doc.get("halal_badge_text"),
		"spicy_level": doc.get("spicy_level"),
		"display_label": doc.get("display_label"),
		"is_featured": bool(cint(doc.get("is_featured"))),
		"allow_order": bool(cint(doc.get("allow_order"))),
		"available_for_pickup": bool(cint(doc.get("available_for_pickup"))),
		"available_for_delivery": bool(cint(doc.get("available_for_delivery"))),
		"product_type": doc.get("product_type"),
		"category": category,
	}


def serialize_menu_product_detail(doc) -> dict[str, Any]:
	payload = serialize_menu_product_card(doc.as_dict())
	payload.update(
		{
			"long_description": doc.long_description,
			"source_summary": get_source_summary(doc),
			"options": get_product_options(doc),
		}
	)
	return payload


def get_source_summary(menu_product) -> dict[str, Any]:
	if menu_product.product_type == "Item":
		item = frappe.get_cached_doc("Item", menu_product.mapped_item)
		return {
			"source_doctype": "Item",
			"source_name": item.name,
			"item_name": item.item_name,
			"has_variants": bool(cint(item.has_variants)),
			"variant_of": item.variant_of,
		}

	bundle = frappe.get_doc("Product Bundle", menu_product.mapped_bundle)
	return {
		"source_doctype": "Product Bundle",
		"source_name": bundle.name,
		"child_count": len(bundle.items or []),
	}


def get_product_options(menu_product) -> dict[str, Any]:
	if menu_product.product_type == "Item":
		return {
			"type": "item",
			"variant_groups": get_item_variant_groups(menu_product.mapped_item),
		}

	return {
		"type": "bundle",
		"bundle_summary": get_bundle_summary(menu_product.mapped_bundle),
	}


def get_item_variant_groups(item_code: str) -> list[dict[str, Any]]:
	item = frappe.get_cached_doc("Item", item_code)
	template_code = item.variant_of or item.name
	template = frappe.get_cached_doc("Item", template_code)

	if not cint(template.has_variants):
		return []

	template_attributes = []
	for row in template.attributes or []:
		template_attributes.append(
			{
				"attribute": row.attribute,
				"is_required": True,
				"options": [],
			}
		)

	variants = frappe.get_all(
		"Item",
		filters={"variant_of": template.name, "disabled": 0},
		fields=["name", "item_name"],
		order_by="item_name asc",
	)

	variant_attr_rows = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": ["in", [row.name for row in variants] or [""]]},
		fields=["parent", "attribute", "attribute_value"],
		order_by="idx asc",
	)

	for group in template_attributes:
		for variant in variants:
			value = next(
				(
					row.attribute_value
					for row in variant_attr_rows
					if row.parent == variant.name and row.attribute == group["attribute"]
				),
				None,
			)
			if not value:
				continue

			group["options"].append(
				{
					"label": value,
					"value": value,
					"variant_item_code": variant.name,
					"variant_item_name": variant.item_name,
				}
			)

	return template_attributes


def get_bundle_summary(bundle_name: str) -> dict[str, Any]:
	bundle = frappe.get_doc("Product Bundle", bundle_name)
	return {
		"bundle_name": bundle.name,
		"items": [
			{
				"item_code": row.item_code,
				"qty": flt(row.qty),
				"description": row.description,
			}
			for row in bundle.items or []
		],
	}


def get_category_summary(category_name: str | None) -> dict[str, Any] | None:
	if not category_name:
		return None

	category = frappe.db.get_value(
		"Menu Category",
		category_name,
		["title", "slug"],
		as_dict=True,
	)
	if not category:
		return None

	return {
		"title": category.title,
		"slug": category.slug,
	}


def get_product_base_price(menu_product: dict[str, Any]) -> dict[str, Any]:
	product_type = menu_product.get("product_type")
	item_code = menu_product.get("mapped_item")

	if product_type == "Bundle":
		return {
			"amount": None,
			"currency": get_default_currency(),
			"source": "bundle",
		}

	if not item_code:
		return {
			"amount": None,
			"currency": get_default_currency(),
			"source": "missing_item",
		}

	price_row = frappe.db.get_value(
		"Item Price",
		{
			"item_code": item_code,
			"price_list": get_default_price_list(),
			"selling": 1,
		},
		["price_list_rate", "currency"],
		as_dict=True,
	)

	return {
		"amount": flt(price_row.price_list_rate) if price_row else None,
		"currency": (price_row.currency if price_row else None) or get_default_currency(),
		"source": "item_price",
	}
