frappe.ui.form.on("Cozy Order", {
	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		set_order_indicator(frm);
		set_invoice_status_message(frm);

		if (frm.doc.sales_invoice) {
			frm.add_custom_button(__("View Sales Invoice"), () => {
				frappe.set_route("Form", "Sales Invoice", frm.doc.sales_invoice);
			}, __("Create"));
		}

		if (!can_create_sales_invoice(frm)) {
			return;
		}

		frm.add_custom_button(__("Create Sales Invoice"), () => {
			frappe.confirm(
				__("Create and submit a Sales Invoice for this Cozy Order?"),
				() => create_sales_invoice(frm)
			);
		}, __("Create"));
	},
});

function set_order_indicator(frm) {
	if (!frm.page || !frm.page.set_indicator) {
		return;
	}

	const statusColorMap = {
		Pending: "orange",
		Confirmed: "blue",
		Preparing: "blue",
		Ready: "green",
		"Out for Delivery": "blue",
		Completed: "green",
		Cancelled: "red",
		Rejected: "red",
	};

	frm.page.set_indicator(__(frm.doc.order_status || "Pending"), statusColorMap[frm.doc.order_status] || "gray");
}

function set_invoice_status_message(frm) {
	if (!frm.dashboard) {
		return;
	}

	if (frm.dashboard.clear_headline) {
		frm.dashboard.clear_headline();
	}

	if (!frm.dashboard.set_headline_alert) {
		return;
	}

	if (frm.doc.sales_invoice) {
		frm.dashboard.set_headline_alert(
			__("Sales Invoice linked: {0}", [frm.doc.sales_invoice]),
			"green"
		);
		return;
	}

	if (frm.doc.order_status === "Completed") {
		frm.dashboard.set_headline_alert(
			__("Invoice not created yet. Use Create > Create Sales Invoice."),
			"orange"
		);
		return;
	}

	frm.dashboard.set_headline_alert(
		__("Sales Invoice can be created after this Cozy Order reaches Completed."),
		"blue"
	);
}

function can_create_sales_invoice(frm) {
	return (
		!frm.doc.sales_invoice &&
		frm.doc.order_status === "Completed"
	);
}

function create_sales_invoice(frm) {
	frappe.call({
		method: "ury_bridge.api.order.create_sales_invoice_from_cozy_order",
		args: {
			order_name: frm.doc.name,
			submit_invoice: 1,
		},
		freeze: true,
		freeze_message: __("Creating Sales Invoice..."),
		callback: (response) => {
			const invoiceName = response.message?.data?.sales_invoice;
			if (!invoiceName) {
				frappe.msgprint(__("Sales Invoice was not created."));
				return;
			}

			frappe.show_alert({
				message: __("Sales Invoice {0} created successfully.", [invoiceName]),
				indicator: "green",
			});

			frm.reload_doc().then(() => {
				frappe.set_route("Form", "Sales Invoice", invoiceName);
			});
		},
		error: (error) => {
			const message =
				error?.message ||
				error?.exc ||
				__("Unable to create Sales Invoice.");
			frappe.msgprint(message);
		},
	});
}
