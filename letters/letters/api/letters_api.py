from __future__ import annotations

import json

import frappe
from frappe import _

from .recipients import _normalize_recipient_config
from ..doctype.letter._content import _unique_letter_title


def _sync_subject_to_notification(letter_name: str, subject: str):
    """Update the linked Notification's subject if it differs from the letter's subject."""
    row = frappe.db.sql(
        "SELECT name, subject FROM `tabNotification` WHERE letter = %s LIMIT 1",
        letter_name,
        as_dict=True,
    )
    if row and row[0].get("subject") != subject:
        frappe.db.set_value("Notification", row[0]["name"], "subject", subject)


@frappe.whitelist(methods=["GET", "POST"])
def get_letter(name: str):
    doc = frappe.get_doc("Letter", name)
    frappe.has_permission("Letter", "read", doc=doc, throw=True)
    return doc.as_builder_dict()


@frappe.whitelist(methods=["POST"])
def save_letter(name: str | None = None, title: str | None = None, subject: str | None = None, preview_text: str | None = None, sender_name: str | None = None, sender_email: str | None = None, blocks: str | None = None, email_width: int | None = None, canvas_background: str | None = None, recipient_config: str | None = None, folder: str | None = None, include_unsubscribe: bool | None = None):
    blocks_json = json.dumps(blocks if isinstance(blocks, list) else json.loads(blocks or "[]"))
    normalized_config = _normalize_recipient_config(recipient_config)

    if name:
        doc = frappe.get_doc("Letter", name)
        frappe.has_permission("Letter", "write", doc=doc, throw=True)
        if title is not None:
            doc.title = title
        if subject is not None:
            doc.subject = subject
        if preview_text is not None:
            doc.preview_text = preview_text
        if sender_name is not None:
            doc.sender_name = sender_name
        if sender_email is not None:
            doc.sender_email = sender_email
        if email_width is not None:
            try:
                doc.email_width = int(email_width)
            except (AttributeError, TypeError, ValueError):
                pass
        if canvas_background is not None:
            doc.canvas_background = canvas_background
        if normalized_config is not None:
            doc.recipient_config = normalized_config
        if folder is not None:
            doc.folder = folder
        if include_unsubscribe is not None:
            doc.include_unsubscribe = 1 if include_unsubscribe else 0
        doc.blocks_json = blocks_json
        doc.save()
        if subject is not None and doc.subject:
            _sync_subject_to_notification(doc.name, doc.subject)
    else:
        frappe.has_permission("Letter", "create", throw=True)
        doc = frappe.get_doc({
            "doctype": "Letter",
            "title": _unique_letter_title(title or "Untitled Letter"),
            "subject": subject or "",
            "preview_text": preview_text or "",
            "sender_name": sender_name or "",
            "sender_email": sender_email or "",
            "status": "Draft",
            "email_width": int(email_width) if email_width else 600,
            "canvas_background": canvas_background or "#ffffff",
            "blocks_json": blocks_json,
            "recipient_config": normalized_config or "",
            "folder": folder or "",
            "include_unsubscribe": 1 if include_unsubscribe else 0,
        })
        doc.insert()

    return {"name": doc.name, "title": doc.title, "status": doc.status}


@frappe.whitelist(methods=["GET", "POST"])
def get_templates():
    """Return all active Letters Templates with rendered preview HTML for the picker."""
    from letters.letters.utils.email_compiler import EmailCompiler

    # //// Neoffice — security audit (neoffice-maintenance#2296): require read on Letters Template.
    # //// Without it the whole template library (blocks_json + compiled preview_html) was returned
    # //// to any signed-in account, a portal Website User included; the picker is a Letters-author
    # //// tool and Letters Manager / Marketing Manager hold that read.
    frappe.has_permission("Letters Template", "read", throw=True)
    templates = frappe.get_all(
        "Letters Template",
        filters={"is_active": 1},
        fields=["name", "title", "thumbnail", "blocks_json", "sort_order"],
        order_by="sort_order asc, title asc",
    )
    for tpl in templates:
        blocks_data = json.loads(tpl.get("blocks_json") or "[]")
        try:
            compiler = EmailCompiler(blocks_data, preview_text="", email_width=600)
            tpl["preview_html"] = compiler.compile()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Letters template preview error")
            tpl["preview_html"] = ""
    return templates


@frappe.whitelist(methods=["GET", "POST"])
def render_preview(name: str | None = None, blocks: str | None = None, preview_text: str | None = None, email_width: int | None = None):
    """Compile blocks to email-safe HTML."""
    from letters.letters.utils.email_compiler import EmailCompiler

    def _first_bg(blocks_data):
        """Return the first explicit background-color in the block tree (depth-first)."""
        stack = list(blocks_data)
        while stack:
            node = stack.pop(0)
            bg = (node.get("props") or {}).get("background_color", "")
            if bg and bg != "transparent":
                return bg
            stack[:0] = node.get("children", [])
        return "#ffffff"

    from letters.letters.api.notifications import resolve_merge_tags_for_preview

    if name and not blocks:
        doc = frappe.get_doc("Letter", name)
        frappe.has_permission("Letter", "read", doc=doc, throw=True)
        try:
            html = doc.render_preview_html(preview_text=preview_text, email_width=email_width)
            html = resolve_merge_tags_for_preview(html, name)
            blocks_data = json.loads(doc.blocks_json or "[]")
            ew = int(email_width) if email_width else (getattr(doc, "email_width", None) or 600)
            return {"html": html, "first_bg": _first_bg(blocks_data), "email_width": ew}
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Letters render_preview error")
            frappe.throw(str(e))
    else:
        # //// Neoffice — security audit (neoffice-maintenance#2296): the blocks branch ran for ANY
        # //// signed-in account (a portal Website User included) with no check, driving the merge-tag
        # //// Jinja render and writing social-icon PNGs to the public files folder for free. Gate it
        # //// like send_test / the link checker: read on the named Letter, else create on Letter.
        if name:
            gate = frappe.get_doc("Letter", name)
            frappe.has_permission("Letter", "read", doc=gate, throw=True)
        else:
            frappe.has_permission("Letter", "create", throw=True)
        blocks_data = blocks if isinstance(blocks, list) else json.loads(blocks or "[]")
        try:
            compiler = EmailCompiler(blocks_data, preview_text=preview_text or "", email_width=email_width or 600)
            html = resolve_merge_tags_for_preview(compiler.compile(), name)
            return {"html": html, "first_bg": _first_bg(blocks_data), "email_width": int(email_width) if email_width else 600}
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Letters render_preview error")
            frappe.throw(str(e))


@frappe.whitelist(methods=["GET", "POST"])
def get_letters(folder: str | None = None):
    """Return Letter records for the dashboard, excluding those used as notification templates."""
    # //// Neoffice — security audit (neoffice-maintenance#2296): list through frappe.get_list, not a
    # //// raw frappe.qb select. The qb query applied only the Notification exclusion, so it skipped
    # //// share scope, User Permissions and if_owner — a user to whom a single Letter was shared (or
    # //// one narrowed by a User Permission) saw the metadata of up to 200 Letters they cannot open.
    # //// frappe.get_list applies all of that AND the Letter permission_query_conditions hook, which
    # //// still excludes Notification-linked Letters (letter.get_permission_query_conditions).
    filters = {"folder": folder} if folder else None
    return frappe.get_list(
        "Letter",
        filters=filters,
        fields=["name", "title", "status", "subject", "modified", "creation", "owner", "folder"],
        order_by="modified desc",
        limit_page_length=200,
    )
    # //// Neoffice — security audit (neoffice-maintenance#2296): replaces the former raw frappe.qb tail.


@frappe.whitelist(methods=["POST"])
def save_letter_as_template(letter_name: str, title: str):
    """Create a Letters Template from an existing Letter's current blocks."""
    letter = frappe.get_doc("Letter", letter_name)
    frappe.has_permission("Letter", "read", doc=letter, throw=True)
    frappe.has_permission("Letters Template", "create", throw=True)

    title = (title or "").strip() or letter.title
    if frappe.db.exists("Letters Template", title):
        frappe.throw(_("A template named {0} already exists").format(frappe.bold(title)))

    doc = frappe.get_doc({
        "doctype": "Letters Template",
        "title": title,
        "blocks_json": letter.blocks_json,
    })
    doc.insert()
    return {"name": doc.name, "title": doc.title}


@frappe.whitelist(methods=["POST"])
def duplicate_letter(name: str):
    """Create an exact copy of a letter as a new Draft."""
    original = frappe.get_doc("Letter", name)
    frappe.has_permission("Letter", "read", doc=original, throw=True)
    return original.duplicate()
