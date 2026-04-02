// Copyright (c) 2026, Galaxy Labs and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Menu Product", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("Menu Product", {
	title(frm) {
		if (!frm.doc.title || frm.doc.slug) return;

		let slug = frm.doc.title
			.toLowerCase()
			.trim()
			.replace(/[^a-z0-9\s-]/g, "")
			.replace(/[\s_-]+/g, "-")
			.replace(/^-+|-+$/g, "");

		frm.set_value("slug", slug);
	}
});