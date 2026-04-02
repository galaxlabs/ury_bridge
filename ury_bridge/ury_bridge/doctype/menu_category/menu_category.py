# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

# Copyright (c) 2026, Galaxy Labs and contributors
# For license information, please see license.txt

import re
import frappe
from frappe.model.document import Document


class MenuCategory(Document):
	def before_validate(self):
		self.title = (self.title or "").strip()
		self.slug = self.generate_unique_slug(self.slug or self.title)

	def generate_unique_slug(self, value: str) -> str:
		slug = self.slugify(value)

		if not slug:
			frappe.throw("Slug could not be generated. Please enter a valid Title.")

		original_slug = slug
		counter = 2

		while frappe.db.exists("Menu Category", {"slug": slug, "name": ["!=", self.name or ""]}):
			slug = f"{original_slug}-{counter}"
			counter += 1

		return slug

	@staticmethod
	def slugify(value: str) -> str:
		value = (value or "").strip().lower()
		value = re.sub(r"[^a-z0-9\s-]", "", value)
		value = re.sub(r"[\s_-]+", "-", value)
		value = re.sub(r"^-+|-+$", "", value)
		return value