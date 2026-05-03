"""Tests for multi-project (multi-company) catalog tagging and filtering."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from acu_buddy.project_indexer import build_catalog
from acu_buddy.validator import validate

DAC_TEMPLATE = """\
using PX.Data;

namespace {ns}
{{
    public class ARInvoiceExt : PXCacheExtension<PX.Objects.AR.ARInvoice>
    {{
        [PXDBString(60)]
        public virtual string {field_name} {{ get; set; }}
        public abstract class {field_lc} : PX.Data.BQL.BqlString.Field<{field_lc}> {{ }}
    }}
}}
"""


class MultiProjectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        root = Path(cls.tmpdir.name)
        # wwwroot-style layout: <root>/CstSrc/<Company>/Code/...
        for company, field in [("CompanyA", "UsrAFlag"), ("CompanyB", "UsrBFlag")]:
            code_dir = root / "CstSrc" / company / "Code"
            code_dir.mkdir(parents=True)
            (code_dir / f"{company}_ARInvoiceExt.cs").write_text(
                DAC_TEMPLATE.format(
                    ns=f"{company}.Customization", field_name=field, field_lc=field.lower()
                )
            )
        cls.root = str(root)
        cls.catalog = build_catalog(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def test_each_company_resolves_to_its_own_project(self):
        projects = sorted({d.project for d in self.catalog.dacs})
        self.assertEqual(projects, ["CompanyA", "CompanyB"])

    def test_list_projects_summary(self):
        names = sorted(self.catalog.projects())
        self.assertEqual(names, ["CompanyA", "CompanyB"])

    def test_field_collision_only_within_same_project(self):
        # Adding UsrAFlag to ARInvoice in CompanyA collides; CompanyB is independent.
        code = """
        public class AnotherARInvoiceExt : PXCacheExtension<PX.Objects.AR.ARInvoice>
        {
            [PXDBString(60)]
            public virtual string UsrAFlag { get; set; }
            public abstract class usraflag : PX.Data.BQL.BqlString.Field<usraflag> { }
        }
        """
        # Scoped to CompanyA: collision -> error
        a_issues = validate(code, self.catalog, project="CompanyA")
        a_errors = [i for i in a_issues if i.severity == "error"]
        self.assertTrue(any(i.kind == "field_collision" for i in a_errors))

        # Scoped to CompanyB: no collision (UsrAFlag isn't there)
        b_issues = validate(code, self.catalog, project="CompanyB")
        b_errors = [i for i in b_issues if i.severity == "error"]
        self.assertEqual(b_errors, [])

    def test_unscoped_validate_sees_collisions_from_any_project(self):
        code = """
        public class AnotherARInvoiceExt : PXCacheExtension<PX.Objects.AR.ARInvoice>
        {
            [PXDBString(60)]
            public virtual string UsrBFlag { get; set; }
            public abstract class usrbflag : PX.Data.BQL.BqlString.Field<usrbflag> { }
        }
        """
        issues = validate(code, self.catalog)  # no project filter
        errors = [i for i in issues if i.severity == "error"]
        self.assertTrue(any(i.kind == "field_collision" for i in errors))


if __name__ == "__main__":
    unittest.main()
