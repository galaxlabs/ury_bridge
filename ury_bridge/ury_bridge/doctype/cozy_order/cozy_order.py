# Copyright (c) 2026, Galaxy Labs and contributors

from frappe.model.document import Document
from frappe.utils import flt


class CozyOrder(Document):
	def validate(self):
		self.subtotal = flt(sum(flt(row.amount) for row in self.items or []))
		self.delivery_fee = flt(self.delivery_fee)
		self.discount_amount = flt(self.discount_amount)
		self.total_amount = flt(self.subtotal) + flt(self.delivery_fee) - flt(self.discount_amount)

