# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import re
import frappe
from frappe import _
from frappe.model.document import Document


class MenuProduct(Document):
	def before_validate(self):
		self.title = (self.title or "").strip()
		self.slug = self.generate_unique_slug(self.slug or self.title)
		self.validate_mapping()

	def generate_unique_slug(self, value: str) -> str:
		slug = self.slugify(value)

		if not slug:
			frappe.throw(_("Slug could not be generated. Please enter a valid Title or Slug."))

		base_slug = slug
		counter = 2

		while frappe.db.exists(
			"Menu Product",
			{"slug": slug, "name": ["!=", self.name or ""]}
		):
			slug = f"{base_slug}-{counter}"
			counter += 1

		return slug

	def validate_mapping(self):
		if self.product_type == "Item":
			if not self.mapped_item:
				frappe.throw(_("Mapped Item is required when Product Type is Item."))
			if self.mapped_bundle:
				frappe.throw(_("Mapped Bundle must be empty when Product Type is Item."))

		elif self.product_type == "Bundle":
			if not self.mapped_bundle:
				frappe.throw(_("Mapped Bundle is required when Product Type is Bundle."))
			if self.mapped_item:
				frappe.throw(_("Mapped Item must be empty when Product Type is Bundle."))

	@staticmethod
	def slugify(value: str) -> str:
		value = (value or "").strip().lower()
		value = re.sub(r"[^a-z0-9\s-]", "", value)
		value = re.sub(r"[\s_-]+", "-", value)
		value = re.sub(r"^-+|-+$", "", value)
		return value