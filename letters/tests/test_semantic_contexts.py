# //// Neoffice — added file (no upstream equivalent)
"""Static guard for the translation contexts that keep our wording apart from other apps.

Frappe merges every installed app's French catalogue into one dictionary and the
app loaded last wins. Two apps giving the same bare English key different senses
therefore collide. A call site that passes a context is looked up as "msg:context"
first, so our PO entry (msgctxt + msgid) cannot be overridden by another app's bare key.

No site and no database needed:
    python3 -m unittest letters.tests.test_semantic_contexts -v
"""
import re
import unittest
from pathlib import Path

from babel.messages.pofile import read_po

APP_DIR = Path(__file__).resolve().parents[1]

# (msgid, context, expected French, source file relative to the app directory, call-site regex)
CASES = [
    (
        "Duplicate",
        "Action",
        "Dupliquer",
        "letters/doctype/letter/letter.js",
        r'__\(\s*"Duplicate"\s*,\s*null\s*,\s*"Action"\s*\)',
    ),
]


def _catalog():
    with open(APP_DIR / "locale" / "fr.po", "rb") as handle:
        return read_po(handle)


class TestSemanticContexts(unittest.TestCase):
    def test_every_context_entry_is_translated(self):
        catalog = _catalog()
        for msgid, context, expected, _source, _pattern in CASES:
            with self.subTest(msgid=msgid, context=context):
                message = catalog.get(msgid, context)
                self.assertIsNotNone(message, f"missing PO entry {msgid!r} / {context!r}")
                self.assertEqual(message.string, expected)
                self.assertNotIn("fuzzy", message.flags)

    def test_every_call_site_passes_its_context(self):
        for msgid, context, _expected, source, pattern in CASES:
            with self.subTest(msgid=msgid, context=context):
                text = (APP_DIR / source).read_text(encoding="utf-8")
                self.assertRegex(text, pattern)

    def test_no_call_site_is_left_on_the_bare_key(self):
        for msgid, _context, _expected, source, _pattern in CASES:
            with self.subTest(msgid=msgid):
                text = (APP_DIR / source).read_text(encoding="utf-8")
                bare = re.compile(r'__\(\s*"%s"\s*\)' % re.escape(msgid))
                self.assertIsNone(bare.search(text), f"bare __({msgid!r}) still in {source}")


if __name__ == "__main__":
    unittest.main()
