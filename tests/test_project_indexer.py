"""Smoke tests for acu_buddy.project_indexer against fixtures."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from acu_buddy.project_indexer import (
    build_catalog,
    load_catalog,
    read_file_safely,
    save_catalog,
    search_text,
)

FIXTURES = str(Path(__file__).resolve().parent / "fixtures")


class IndexerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = build_catalog(FIXTURES)

    def test_file_count(self):
        self.assertEqual(self.catalog.file_count, 2)

    def test_finds_dac(self):
        names = [d.name for d in self.catalog.dacs]
        self.assertIn("SampleDAC", names)
        sample = next(d for d in self.catalog.dacs if d.name == "SampleDAC")
        self.assertEqual(sample.kind, "dac")
        self.assertIsNone(sample.extends)
        field_names = [f.name for f in sample.fields]
        self.assertEqual(sorted(field_names), ["Amount", "OrderNbr", "Status"])

    def test_dac_field_attributes(self):
        sample = next(d for d in self.catalog.dacs if d.name == "SampleDAC")
        order_nbr = next(f for f in sample.fields if f.name == "OrderNbr")
        joined = " ".join(order_nbr.attributes)
        self.assertIn("PXDBString", joined)
        self.assertIn("PXDefault", joined)
        self.assertIn("PXUIField", joined)

    def test_finds_dac_extension(self):
        ext = next(d for d in self.catalog.dacs if d.name == "ARInvoiceExt")
        self.assertEqual(ext.kind, "dac_extension")
        self.assertIn("ARInvoice", ext.extends)
        names = sorted(f.name for f in ext.fields)
        self.assertEqual(names, ["UsrCustomNote", "UsrFlagged"])

    def test_finds_graph_with_primary_dac(self):
        g = next(g for g in self.catalog.graphs if g.name == "SampleGraph")
        self.assertEqual(g.kind, "graph")
        self.assertEqual(g.primary_dac, "SampleDAC")

    def test_finds_graph_extensions(self):
        exts = [g for g in self.catalog.graphs if g.kind == "graph_extension"]
        names = sorted(g.name for g in exts)
        self.assertEqual(names, ["ARInvoiceEntryExt", "ARInvoiceEntryExt2"])
        for g in exts:
            self.assertEqual(g.extends, "ARInvoiceEntry")

    def test_finds_modern_events(self):
        modern = [e for e in self.catalog.events if e.style == "modern"]
        kinds = sorted((e.enclosing_class, e.kind, e.target_dac, e.target_field) for e in modern)
        expected = sorted(
            [
                ("SampleGraph", "RowSelected", "SampleDAC", None),
                ("SampleGraph", "FieldUpdated", "SampleDAC", "status"),
                ("ARInvoiceEntryExt", "RowSelected", "ARInvoice", None),
                ("ARInvoiceEntryExt2", "RowPersisting", "ARInvoice", None),
            ]
        )
        self.assertEqual(kinds, expected)

    def test_finds_legacy_events(self):
        legacy = [e for e in self.catalog.events if e.style == "legacy"]
        triples = sorted((e.target_dac, e.target_field or "", e.kind) for e in legacy)
        self.assertIn(("SampleDAC", "OrderNbr", "FieldUpdated"), triples)
        self.assertIn(("SampleDAC", "", "RowPersisting"), triples)
        self.assertIn(("ARInvoice", "DocType", "FieldVerifying"), triples)

    def test_save_and_load_roundtrip(self):
        out_path = Path(FIXTURES).parent / "_tmp_catalog.json"
        try:
            save_catalog(self.catalog, str(out_path))
            loaded = load_catalog(str(out_path))
            self.assertEqual(loaded.file_count, self.catalog.file_count)
            self.assertEqual(len(loaded.dacs), len(self.catalog.dacs))
            self.assertEqual(len(loaded.graphs), len(self.catalog.graphs))
            self.assertEqual(len(loaded.events), len(self.catalog.events))
        finally:
            if out_path.exists():
                out_path.unlink()

    def test_search_text(self):
        hits = search_text(FIXTURES, "PXDBString")
        self.assertTrue(any("sample_dacs.cs" in h["file"] for h in hits))
        self.assertTrue(all("PXDBString".lower() in h["text"].lower() for h in hits))

    def test_read_file_safely_rejects_traversal(self):
        result = read_file_safely(FIXTURES, "../../etc/passwd")
        self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
