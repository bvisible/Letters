# //// Neoffice — added file (no upstream equivalent): regression tests for the security audit of
# //// the Letters app (deep-app-audit, neoffice-maintenance#2296). Each test acts as the person who
# //// acts in real life (frappe.tests.roles.as_role), never as Administrator, and fails without the
# //// matching fix.
from __future__ import annotations

import json

import frappe
from frappe.tests.roles import as_role
from frappe.tests.utils import FrappeTestCase

AUTHOR = "role-test-letters-author@yopmail.com"
PORTAL = "role-test-letters-portal@yopmail.com"


def _make_user(email, roles, website=False):
    """Create (as scenery) a user holding exactly `roles`, return its email. `website` makes a
    roleless Website User — the finding's 'any logged-in user' actor, no ERPNext role needed."""
    previous = frappe.session.user
    frappe.set_user("Administrator")
    try:
        if frappe.db.exists("User", email):
            user = frappe.get_doc("User", email)
            user.enabled = 1
        else:
            user = frappe.new_doc("User")
            user.update({"email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
        if website:
            user.user_type = "Website User"
        user.set("roles", [{"role": r} for r in roles])
        user.save(ignore_permissions=True)
        frappe.clear_cache(user=email)
    finally:
        frappe.set_user(previous)
    return email


class LettersSecurityAudit(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self._created = []
        self.author = _make_user(AUTHOR, ["Marketing Manager"])
        self.portal = _make_user(PORTAL, [], website=True)

    def tearDown(self):
        frappe.set_user("Administrator")
        for doctype, name in reversed(self._created):
            try:
                frappe.delete_doc(doctype, name, force=True, ignore_missing=True)
            except Exception:
                pass
        frappe.db.commit()
        super().tearDown()

    def _letter(self, blocks=None, subject="Audit subject", owner=None):
        doc = frappe.get_doc({
            "doctype": "Letter",
            "title": f"Audit Letter {frappe.generate_hash(length=8)}",
            "subject": subject,
            "status": "Draft",
            "email_width": 600,
            "blocks_json": json.dumps(blocks or [{"type": "text", "props": {"html_content": "<p>Hi</p>"}}]),
        })
        doc.insert(ignore_permissions=True)
        if owner:
            frappe.db.set_value("Letter", doc.name, "owner", owner)
        self._created.append(("Letter", doc.name))
        return doc.name

    def _link_notification(self, letter_name, document_type="User"):
        notif = frappe.get_doc({
            "doctype": "Notification",
            "name": f"Audit Notif {frappe.generate_hash(length=8)}",
            "subject": "n",
            "document_type": document_type,
            "event": "New",
            "channel": "Email",
            "message": "",
            "letter_message_type": "Letter Builder",
            "enabled": 0,
            "letter": letter_name,
        })
        notif.flags.ignore_mandatory = True
        notif.insert(ignore_permissions=True)
        self._created.append(("Notification", notif.name))
        return notif.name

    # ── F4: count_doctype_recipients is not an oracle for a portal Website User ──────────
    def test_count_doctype_recipients_denied_to_website_user(self):
        from letters.letters.api.recipients import count_doctype_recipients
        # ToDo is doctype the automatic `All` role may read, so the ONLY thing that denies the
        # portal user here is the Letters-author gate — not the target doctype's own read check.
        with as_role(self.portal):
            with self.assertRaises(frappe.PermissionError):
                count_doctype_recipients("ToDo", "name", json.dumps({"description": ["like", "a%"]}))

    def test_count_doctype_recipients_works_for_author(self):
        from letters.letters.api.recipients import count_doctype_recipients
        self._letter(subject="countable")
        # Marketing Manager can read Letter; "subject" is a real column to count a real doctype.
        with as_role(self.author):
            result = count_doctype_recipients("Letter", "subject", None)
        self.assertIn("count", result)

    # ── F12: get_templates needs Letters Template read ───────────────────────────────────
    def test_get_templates_denied_to_website_user(self):
        from letters.letters.api.letters_api import get_templates
        with as_role(self.portal):
            with self.assertRaises(frappe.PermissionError):
                get_templates()

    # ── F11 / F1 reach: render_preview blocks branch needs an authoring right ────────────
    def test_render_preview_blocks_denied_to_website_user(self):
        from letters.letters.api.letters_api import render_preview
        with as_role(self.portal):
            with self.assertRaises(frappe.PermissionError):
                render_preview(blocks=json.dumps([{"type": "social", "props": {"color": "#111111",
                                                                               "website_url": "https://x.y"}}]))

    # ── F9: get_letters is scoped by permission ──────────────────────────────────────────
    def test_get_letters_denied_to_website_user(self):
        from letters.letters.api.letters_api import get_letters
        with as_role(self.portal):
            with self.assertRaises(frappe.PermissionError):
                get_letters()

    def test_get_letters_returns_a_list_for_author(self):
        from letters.letters.api.letters_api import get_letters
        self._letter()
        with as_role(self.author):
            rows = get_letters()
        self.assertIsInstance(rows, list)

    # ── F6: sending to an Email Group needs Email Group read ─────────────────────────────
    def test_group_resolution_requires_email_group_read(self):
        from letters.letters.api.recipients import _resolve_single_source_emails
        from letters.letters.doctype.letter._sending import _resolve_recipients
        # Marketing Manager holds Letter write but no Email Group read (Newsletter Manager only).
        with as_role(self.author):
            with self.assertRaises(frappe.PermissionError):
                _resolve_single_source_emails({"type": "group", "email_group": "whatever"})
            with self.assertRaises(frappe.PermissionError):
                _resolve_recipients("whatever", None, None, 50000, lambda: set(), lambda e: (e, 0))

    # ── F13: create_notification_pair needs Notification create ──────────────────────────
    def test_create_notification_pair_requires_notification_create(self):
        from letters.letters.api.notifications import create_notification_pair
        with as_role(self.author):
            with self.assertRaises(frappe.PermissionError):
                create_notification_pair()

    # ── F1 core: author Jinja renders against a read-only snapshot, under restrict_globals ─
    def test_preview_resolves_field_but_cannot_mutate_or_reach_globals(self):
        from letters.letters.api.notifications import resolve_merge_tags_for_preview
        # document_type the author may read (Marketing Manager reads Letter), so a sample is picked.
        sample = self._letter(subject="countable")
        host = self._letter()
        self._link_notification(host, document_type="Letter")
        before = frappe.db.get_value("Letter", host, "modified")
        with as_role(self.author):
            # (a) a plain field merge still resolves against the snapshot (the raw tag is gone)
            resolved = resolve_merge_tags_for_preview("<p>{{ doc.subject }}</p>", host)
            # (b) a doc method call cannot run: render fails closed, html returned unchanged
            mutated = resolve_merge_tags_for_preview("<p>{{ doc.save() }}</p>", host)
            # (c) frappe.get_doc is not reachable from the template (shadowed context + restrict_globals)
            globaled = resolve_merge_tags_for_preview(
                "<p>{{ frappe.get_doc('User', 'Administrator').name }}</p>", host
            )
        self.assertNotIn("{{ doc.subject }}", resolved)
        self.assertEqual(mutated, "<p>{{ doc.save() }}</p>")
        # frappe.get_doc is unreachable from the template: the render fails closed, raw returned.
        self.assertEqual(globaled, "<p>{{ frappe.get_doc('User', 'Administrator').name }}</p>")
        # The sampled document was never written by the author's template.
        self.assertEqual(before, frappe.db.get_value("Letter", host, "modified"))

    def test_preview_sample_uses_permission_respecting_query(self):
        # The sample is chosen with a permission-respecting query (frappe.get_list applies
        # permission_query_conditions, User Permissions and if_owner), never the old
        # ignore_permissions reads that pulled an arbitrary record of any doctype.
        import inspect

        from letters.letters.api import notifications
        # Keep code lines only (drop comments), so prose mentioning the old calls doesn't count.
        src = "\n".join(
            ln for ln in inspect.getsource(notifications._pick_sample_record).splitlines()
            if not ln.lstrip().startswith("#")
        )
        self.assertIn("frappe.get_list(", src)
        self.assertNotIn("frappe.get_all(", src)
        self.assertNotIn("frappe.db.get_value(", src)

    # ── F15: SSRF guard rejects carrier-grade NAT / shared address space ─────────────────
    def test_ip_is_public_rejects_cgnat_range(self):
        import ipaddress
        from letters.letters.api.links import _ip_is_public
        self.assertFalse(_ip_is_public(ipaddress.ip_address("100.64.1.1")))
        self.assertTrue(_ip_is_public(ipaddress.ip_address("93.184.216.34")))

    # ── F14: state-changing Notification endpoints are POST-only (no GET/CSRF) ────────────
    def test_notification_state_changers_are_post_only(self):
        import inspect

        from letters.letters.api import notifications
        src = inspect.getsource(notifications)
        for fn in (
            "def create_notification_pair",
            "def duplicate_notification_pair",
            "def delete_notification_pair",
            "def create_notification_for_letter",
            "def create_letter_for_notification",
        ):
            idx = src.index(fn)
            decorators = src[max(0, idx - 400):idx]
            self.assertIn('methods=["POST"]', decorators, f"{fn} must be POST-only")

    # ── F3: the builder Preview writes the email into a sandboxed frame (no allow-scripts) ──
    def test_preview_iframe_is_sandboxed_without_scripts(self):
        import os
        import re

        app = frappe.get_app_path("letters")
        # The Vue source and the committed bundle the desk actually loads (page_js) must agree.
        for path in (
            os.path.join(app, "..", "frontend", "src", "composables", "usePreview.js"),
            os.path.join(app, "public", "js", "letter-builder.js"),
        ):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            tags = re.findall(r'<iframe id="__preview-frame"[^>]*>', text)
            self.assertEqual(len(tags), 1, path)
            sandbox = re.search(r'sandbox="([^"]*)"', tags[0])
            self.assertIsNotNone(sandbox, f"preview iframe is not sandboxed in {path}")
            self.assertIn("allow-same-origin", sandbox.group(1))
            self.assertNotIn("allow-scripts", sandbox.group(1))

    # ── F2 / F10: the guest unsubscribe page escapes every reflected/stored value ─────────
    def test_unsubscribe_page_escapes_reflected_values(self):
        path = frappe.get_app_path("letters", "www", "letters_unsubscribe.html")
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
        # Escaped forms present, raw forms gone (Frappe's website Jinja does not autoescape).
        self.assertIn("{{ email | e }}", html)
        self.assertIn("{{ folder.name | e }}", html)
        self.assertIn("{{ folder.folder_name | e }}", html)
        self.assertNotIn("<strong>{{ email }}</strong>", html)
        self.assertNotIn('value="{{ folder.name }}"', html)
        # No value is interpolated into an inline-script string literal any more.
        self.assertNotIn("var EMAIL = '{{", html)
        self.assertNotIn("var TOKEN = '{{", html)
