"""Tests for acu_buddy.validator against the fixture catalog."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from acu_buddy.project_indexer import build_catalog
from acu_buddy.validator import summarize, validate

FIXTURES = str(Path(__file__).resolve().parent / "fixtures")


class ValidatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = build_catalog(FIXTURES)

    def _severities(self, issues):
        return [i.severity for i in issues]

    def test_no_catalog_returns_note_for_empty_input(self):
        issues = validate("", catalog=None)
        self.assertTrue(any(i.kind == "empty_input" for i in issues))

    def test_clean_dac_extension_against_unknown_target_is_note_only(self):
        # SOOrder is not in the fixture catalog
        code = """
        public class SOOrderExt : PXCacheExtension<SOOrder>
        {
            [PXDBString(60)]
            public virtual string UsrFreshNote { get; set; }
            public abstract class usrFreshNote : PX.Data.BQL.BqlString.Field<usrFreshNote> { }
        }
        """
        issues = validate(code, self.catalog)
        sevs = self._severities(issues)
        self.assertNotIn("error", sevs)
        self.assertIn("note", sevs)

    def test_field_collision_on_existing_extension_is_error(self):
        # ARInvoiceExt already adds UsrCustomNote in the fixture
        code = """
        public class AnotherARInvoiceExt : PXCacheExtension<PX.Objects.AR.ARInvoice>
        {
            [PXDBString(60)]
            public virtual string UsrCustomNote { get; set; }
            public abstract class usrCustomNote : PX.Data.BQL.BqlString.Field<usrCustomNote> { }
        }
        """
        issues = validate(code, self.catalog)
        errors = [i for i in issues if i.severity == "error"]
        self.assertTrue(any(i.kind == "field_collision" for i in errors))
        self.assertTrue(any("UsrCustomNote" in i.message for i in errors))

    def test_field_collision_on_base_dac_is_error(self):
        # SampleDAC has OrderNbr; new DAC extension trying to redefine it
        code = """
        public class SampleDACExt : PXCacheExtension<SampleDAC>
        {
            [PXDBString]
            public virtual string OrderNbr { get; set; }
            public abstract class orderNbr : PX.Data.BQL.BqlString.Field<orderNbr> { }
        }
        """
        issues = validate(code, self.catalog)
        self.assertTrue(any(i.kind == "field_collision" for i in issues))

    def test_class_name_clash_is_warning(self):
        code = """
        public class SampleDAC : IBqlTable
        {
            [PXDBInt]
            public virtual int? Foo { get; set; }
            public abstract class foo : PX.Data.BQL.BqlInt.Field<foo> { }
        }
        """
        issues = validate(code, self.catalog)
        warnings = [i for i in issues if i.severity == "warning"]
        self.assertTrue(any(i.kind == "class_name_clash" for i in warnings))

    def test_event_handler_unknown_field_on_known_dac_is_error(self):
        code = """
        public class SampleGraphExt2 : PXGraphExtension<SampleGraph>
        {
            protected virtual void _(Events.FieldUpdated<SampleDAC, SampleDAC.bogusField> e) { }
        }
        """
        issues = validate(code, self.catalog)
        errors = [i for i in issues if i.severity == "error"]
        self.assertTrue(any(i.kind == "field_not_found" for i in errors))
        self.assertTrue(any("bogusField" in i.message for i in errors))

    def test_event_handler_known_field_on_known_dac_is_clean(self):
        code = """
        public class SampleGraphExt3 : PXGraphExtension<SampleGraph>
        {
            protected virtual void _(Events.FieldUpdated<SampleDAC, SampleDAC.status> e) { }
            protected virtual void _(Events.RowSelected<SampleDAC> e) { }
        }
        """
        issues = validate(code, self.catalog)
        errors = [i for i in issues if i.severity == "error"]
        self.assertEqual(errors, [])

    def test_legacy_event_with_known_field_is_clean(self):
        code = """
        public class SampleGraphExt4 : PXGraphExtension<SampleGraph>
        {
            protected virtual void SampleDAC_Status_FieldUpdated(PXCache sender, PXFieldUpdatedEventArgs e) { }
        }
        """
        issues = validate(code, self.catalog)
        errors = [i for i in issues if i.severity == "error"]
        self.assertEqual(errors, [])

    def test_summarize_shape(self):
        code = """
        public class SampleGraphExt5 : PXGraphExtension<SampleGraph>
        {
            protected virtual void _(Events.FieldUpdated<SampleDAC, SampleDAC.bogusField> e) { }
        }
        """
        issues = validate(code, self.catalog)
        out = summarize(issues)
        self.assertIn("ok", out)
        self.assertFalse(out["ok"])
        self.assertEqual(out["issue_counts"]["error"], 1)
        self.assertIn("issues", out)
        self.assertIn("summary", out)


if __name__ == "__main__":
    unittest.main()
