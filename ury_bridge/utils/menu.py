from __future__ import annotations

from collections import defaultdict
from typing import Any

import frappe
from frappe.utils import cint, flt

from ury_bridge.utils.common import (
	ensure_public_menu_api_enabled,
	get_api_settings,
	get_default_currency,
	get_default_price_list,
	normalize_file_url,
	slugify_value,
)


def get_menu_group_names() -> list[str]:
	ensure_public_menu_api_enabled()
	settings = get_api_settings()
	root_group = settings.root_item_group or "Products"

	if not frappe.db.exists("Item Group", root_group):
		frappe.throw(f"Root Item Group {root_group} is not configured correctly.")

	group_names = [root_group]
	if cint(settings.include_subgroups):
		lft, rgt = frappe.db.get_value("Item Group", root_group, ["lft", "rgt"])
		descendants = frappe.get_all(
			"Item Group",
			filters={"lft": [">", lft], "rgt": ["<", rgt]},
			pluck="name",
			order_by="lft asc",
		)
		group_names.extend(descendants)

	return group_names


def get_public_menu_products(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
	filters = filters or {}
	products = get_item_products()

	if cint(get_api_settings().include_product_bundles):
		products.extend(get_bundle_products())

	category_filter = (filters.get("category") or "").strip().lower()
	if category_filter:
		products = [
			row
			for row in products
			if category_filter
			in {
				(row["category"] or {}).get("slug", "").lower(),
				(row["category"] or {}).get("title", "").lower(),
				row.get("_category_name", "").lower(),
			}
		]

	if filters.get("pickup_only"):
		products = [row for row in products if row["available_for_pickup"]]

	if filters.get("allow_order_only", True):
		products = [row for row in products if row["allow_order"]]

	return sorted(products, key=lambda row: (row["category"]["title"] if row["category"] else "", row["title"]))


def get_public_menu_payload(filters: dict[str, Any] | None = None) -> dict[str, Any]:
	products = get_public_menu_products(filters)
	category_map: dict[str, dict[str, Any]] = {}
	for product in products:
		category = product.get("category")
		if not category:
			continue
		category_map[category["slug"]] = {
			**category,
			"is_featured": True,
			"image": None,
			"description": None,
			"sort_order": 0,
		}

	return {
		"categories": sorted(category_map.values(), key=lambda row: row["title"]),
		"products": [serialize_menu_product_card(row) for row in products],
	}


def get_menu_product_by_slug(slug: str) -> dict[str, Any]:
	for row in get_public_menu_products({"allow_order_only": False}):
		if row["slug"] == slug:
			return row
	frappe.throw("Menu product not found.")


def get_active_categories() -> list[dict[str, Any]]:
	return get_public_menu_payload({"allow_order_only": False})["categories"]


def get_item_products() -> list[dict[str, Any]]:
	group_names = get_menu_group_names()
	bundle_parent_item_codes = set(get_bundle_parent_item_codes()) if cint(get_api_settings().include_product_bundles) else set()
	items = frappe.get_all(
		"Item",
		filters={
			"item_group": ["in", group_names],
			"disabled": 0,
			"is_sales_item": 1,
			"variant_of": ["in", ["", None]],
		},
		fields=[
			"name",
			"item_name",
			"item_group",
			"description",
			"image",
			"has_variants",
			"variant_of",
		],
		order_by="item_group asc, item_name asc",
	)

	return [build_item_product(item) for item in items if item["name"] not in bundle_parent_item_codes]


def get_bundle_products() -> list[dict[str, Any]]:
	group_names = set(get_menu_group_names())
	bundles = frappe.get_all(
		"Product Bundle",
		filters={"disabled": 0},
		fields=["name", "new_item_code", "description"],
		order_by="name asc",
	)

	products = []
	for bundle in bundles:
		if not bundle.new_item_code:
			continue

		parent_item = frappe.db.get_value(
			"Item",
			bundle.new_item_code,
			["name", "item_name", "item_group", "description", "image", "disabled", "is_sales_item"],
			as_dict=True,
		)
		if not parent_item:
			continue
		if parent_item.item_group not in group_names:
			continue

		products.append(build_bundle_product(bundle, parent_item))

	return products


def get_bundle_parent_item_codes() -> list[str]:
	return frappe.get_all(
		"Product Bundle",
		filters={"disabled": 0, "new_item_code": ["is", "set"]},
		pluck="new_item_code",
	)


def build_item_product(item: dict[str, Any]) -> dict[str, Any]:
	price = get_item_display_price(item["name"], has_variants=bool(cint(item.get("has_variants"))))
	return {
		"_source_type": "Item",
		"_item_code": item["name"],
		"_category_name": item.get("item_group"),
		"title": item.get("item_name") or item.get("name"),
		"slug": slugify_value(item.get("item_name") or item.get("name")),
		"image": normalize_file_url(item.get("image")),
		"short_description": item.get("description"),
		"long_description": item.get("description"),
		"selling_price": price,
		"allergy_alert": None,
		"halal_badge_text": None,
		"spicy_level": None,
		"display_label": None,
		"is_featured": True,
		"allow_order": price["amount"] is not None,
		"available_for_pickup": bool(cint(get_api_settings().default_pickup_enabled)),
		"available_for_delivery": bool(cint(get_api_settings().default_delivery_enabled)),
		"product_type": "Item",
		"category": build_category_summary(item.get("item_group")),
		"source_summary": {
			"source_doctype": "Item",
			"source_name": item["name"],
			"item_name": item.get("item_name"),
			"has_variants": bool(cint(item.get("has_variants"))),
			"variant_of": item.get("variant_of"),
		},
	}


def build_bundle_product(bundle: dict[str, Any], parent_item: dict[str, Any]) -> dict[str, Any]:
	price = get_item_price(parent_item["name"])
	child_items = get_bundle_summary(bundle["name"])["items"]
	return {
		"_source_type": "Bundle",
		"_bundle_name": bundle["name"],
		"_item_code": parent_item["name"],
		"_category_name": parent_item.get("item_group"),
		"title": parent_item.get("item_name") or bundle["name"],
		"slug": slugify_value(parent_item.get("item_name") or bundle["name"]),
		"image": normalize_file_url(parent_item.get("image")),
		"short_description": bundle.get("description") or parent_item.get("description"),
		"long_description": bundle.get("description") or parent_item.get("description"),
		"selling_price": {
			"amount": flt(price.price_list_rate) if price else None,
			"currency": (price.currency if price else None) or get_default_currency(),
			"source": "item_price",
		},
		"allergy_alert": None,
		"halal_badge_text": None,
		"spicy_level": None,
		"display_label": None,
		"is_featured": True,
		"allow_order": bool(price and child_items),
		"available_for_pickup": bool(cint(get_api_settings().default_pickup_enabled)),
		"available_for_delivery": bool(cint(get_api_settings().default_delivery_enabled)),
		"product_type": "Bundle",
		"category": build_category_summary(parent_item.get("item_group")),
		"source_summary": {
			"source_doctype": "Product Bundle",
			"source_name": bundle["name"],
			"parent_item_code": parent_item["name"],
			"child_count": len(child_items),
		},
	}


def build_category_summary(item_group: str | None) -> dict[str, Any] | None:
	if not item_group:
		return None

	return {
		"title": item_group,
		"slug": slugify_value(item_group),
	}


def serialize_menu_category(row: dict[str, Any]) -> dict[str, Any]:
	return {
		"title": row.get("title"),
		"slug": row.get("slug"),
		"image": normalize_file_url(row.get("image")),
		"description": row.get("description"),
		"is_featured": bool(cint(row.get("is_featured"))),
		"sort_order": cint(row.get("sort_order")),
	}


def serialize_menu_product_card(row: dict[str, Any]) -> dict[str, Any]:
	return {
		"title": row.get("title"),
		"slug": row.get("slug"),
		"image": row.get("image"),
		"short_description": row.get("short_description"),
		"selling_price": row.get("selling_price"),
		"allergy_alert": row.get("allergy_alert"),
		"halal_badge_text": row.get("halal_badge_text"),
		"spicy_level": row.get("spicy_level"),
		"display_label": row.get("display_label"),
		"is_featured": bool(row.get("is_featured")),
		"allow_order": bool(row.get("allow_order")),
		"available_for_pickup": bool(row.get("available_for_pickup")),
		"available_for_delivery": bool(row.get("available_for_delivery")),
		"product_type": row.get("product_type"),
		"category": row.get("category"),
	}


def serialize_menu_product_detail(row: dict[str, Any]) -> dict[str, Any]:
	payload = serialize_menu_product_card(row)
	payload.update(
		{
			"long_description": row.get("long_description"),
			"source_summary": row.get("source_summary"),
			"options": get_product_options(row),
		}
	)
	return payload


def get_product_options(row: dict[str, Any]) -> dict[str, Any]:
	if row["product_type"] == "Bundle":
		return {
			"type": "bundle",
			"bundle_summary": get_bundle_summary(row["_bundle_name"]),
		}

	return {
		"type": "item",
		"variant_groups": get_item_variant_groups(row["_item_code"]),
		"variants": get_item_variants(row["_item_code"]),
	}


def get_item_variant_groups(item_code: str) -> list[dict[str, Any]]:
	item = frappe.db.get_value(
		"Item",
		item_code,
		["name", "has_variants"],
		as_dict=True,
	)
	if not item or not cint(item.has_variants):
		return []

	variants = frappe.get_all(
		"Item",
		filters={"variant_of": item_code, "disabled": 0, "is_sales_item": 1},
		fields=["name", "item_name"],
		order_by="item_name asc",
	)
	if not variants:
		return []

	variant_codes = [row.name for row in variants]
	attr_rows = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": ["in", variant_codes]},
		fields=["parent", "attribute", "attribute_value"],
		order_by="idx asc",
	)

	group_map: dict[str, dict[str, Any]] = {}
	for attr_row in attr_rows:
		group = group_map.setdefault(
			attr_row.attribute,
			{"attribute": attr_row.attribute, "is_required": True, "options": []},
		)
		if any(option["value"] == attr_row.attribute_value for option in group["options"]):
			continue
		group["options"].append({"label": attr_row.attribute_value, "value": attr_row.attribute_value})

	return list(group_map.values())


def get_item_variants(item_code: str) -> list[dict[str, Any]]:
	item = frappe.db.get_value("Item", item_code, ["name", "has_variants"], as_dict=True)
	if not item or not cint(item.has_variants):
		return []

	variants = frappe.get_all(
		"Item",
		filters={"variant_of": item_code, "disabled": 0, "is_sales_item": 1},
		fields=["name", "item_name"],
		order_by="item_name asc",
	)
	if not variants:
		return []

	variant_codes = [row.name for row in variants]
	attr_rows = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": ["in", variant_codes]},
		fields=["parent", "attribute", "attribute_value"],
		order_by="idx asc",
	)

	payload = []
	for variant in variants:
		attributes = {row.attribute: row.attribute_value for row in attr_rows if row.parent == variant.name}
		price = get_item_price(variant.name, fallback_item_code=item_code)
		payload.append(
			{
				"item_code": variant.name,
				"item_name": variant.item_name,
				"attributes": attributes,
				"price": {
					"amount": flt(price.price_list_rate) if price else None,
					"currency": (price.currency if price else None) or get_default_currency(),
				},
			}
		)

	return payload


def get_bundle_summary(bundle_name: str) -> dict[str, Any]:
	bundle = frappe.get_doc("Product Bundle", bundle_name)
	items = []
	selectable_components = []
	for row in bundle.items or []:
		component = {
			"item_code": row.item_code,
			"qty": flt(row.qty),
			"description": row.description,
		}
		selector = build_bundle_component_selector(row.item_code, row.description, row.qty)
		if selector:
			component["selector_key"] = selector["component_key"]
			component["selectable"] = True
			selectable_components.append(selector)
		items.append(component)

	return {
		"bundle_name": bundle.name,
		"parent_item_code": bundle.new_item_code,
		"items": items,
		"selectable_components": selectable_components,
	}


def build_bundle_component_selector(
	item_code: str,
	description: str | None = None,
	included_qty: float | None = None,
) -> dict[str, Any] | None:
	item_doc = frappe.db.get_value(
		"Item",
		item_code,
		["name", "item_name", "variant_of", "item_group"],
		as_dict=True,
	)
	if not item_doc or not item_doc.variant_of:
		return None
	if (item_doc.item_group or "").strip().lower() != "drinks":
		return None

	template_doc = frappe.db.get_value(
		"Item",
		item_doc.variant_of,
		["name", "item_name"],
		as_dict=True,
	)
	if not template_doc:
		return None

	return {
		"component_key": item_code,
		"label": template_doc.item_name or item_doc.item_name or description or item_code,
		"included_qty": flt(included_qty or 1),
		"template_item_code": template_doc.name,
		"template_item_name": template_doc.item_name or template_doc.name,
		"default_variant_item_code": item_code,
		"default_variant_label": item_doc.item_name or description or item_code,
		"variants": [
			{
				**variant,
				"label": format_variant_label(variant),
			}
			for variant in get_item_variants(template_doc.name)
		],
	}


def format_variant_label(variant: dict[str, Any]) -> str:
	attributes = variant.get("attributes") or {}
	if attributes:
		return " / ".join(str(value) for value in attributes.values() if value)
	return variant.get("item_name") or variant.get("item_code") or "Variant"


def get_item_display_price(item_code: str, has_variants: bool = False) -> dict[str, Any]:
	direct_price = get_item_price(item_code)
	if direct_price:
		return {
			"amount": flt(direct_price.price_list_rate),
			"currency": direct_price.currency or get_default_currency(),
			"source": "item_price",
		}

	if has_variants:
		variant_prices = get_variant_price_rows(item_code)
		if variant_prices:
			amounts = [flt(row.price_list_rate) for row in variant_prices if row.price_list_rate is not None]
			if amounts:
				return {
					"amount": min(amounts),
					"currency": variant_prices[0].currency or get_default_currency(),
					"source": "variant_item_price",
				}

	return {
		"amount": None,
		"currency": get_default_currency(),
		"source": "missing_price",
	}


def get_item_price(item_code: str, fallback_item_code: str | None = None):
	for code in [item_code, fallback_item_code]:
		if not code:
			continue
		price = frappe.db.get_value(
			"Item Price",
			{"item_code": code, "price_list": get_default_price_list(), "selling": 1},
			["name", "price_list_rate", "currency"],
			as_dict=True,
		)
		if price:
			return price
	return None


def get_variant_price_rows(template_item_code: str) -> list[dict[str, Any]]:
	variant_codes = frappe.get_all("Item", filters={"variant_of": template_item_code, "disabled": 0}, pluck="name")
	if not variant_codes:
		return []

	return frappe.get_all(
		"Item Price",
		filters={"item_code": ["in", variant_codes], "price_list": get_default_price_list(), "selling": 1},
		fields=["item_code", "price_list_rate", "currency"],
		order_by="price_list_rate asc, item_code asc",
	)


def resolve_variant_from_options(template_item_code: str, selected_options: Any) -> str | None:
	option_map = normalize_selected_options(selected_options)
	if not option_map:
		return None

	variant_rows = frappe.get_all(
		"Item",
		filters={"variant_of": template_item_code, "disabled": 0, "is_sales_item": 1},
		fields=["name"],
	)
	if not variant_rows:
		return None

	variant_codes = [row.name for row in variant_rows]
	attr_rows = frappe.get_all(
		"Item Variant Attribute",
		filters={"parent": ["in", variant_codes]},
		fields=["parent", "attribute", "attribute_value"],
		order_by="idx asc",
	)

	variant_map: dict[str, dict[str, str]] = defaultdict(dict)
	for row in attr_rows:
		variant_map[row.parent][row.attribute] = row.attribute_value

	for variant_code, attributes in variant_map.items():
		if all(attributes.get(attr) == value for attr, value in option_map.items()):
			return variant_code

	return None


def normalize_selected_options(selected_options: Any) -> dict[str, str]:
	if isinstance(selected_options, dict):
		return {str(key): str(value) for key, value in selected_options.items() if value}

	result: dict[str, str] = {}
	for row in selected_options or []:
		attribute = row.get("attribute") or row.get("name")
		value = row.get("value") or row.get("label")
		if attribute and value:
			result[str(attribute)] = str(value)
	return result
