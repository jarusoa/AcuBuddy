using System;
using PX.Data;
using PX.Objects.AR;

namespace AcuBuddy.Sample
{
    public class SampleGraph : PXGraph<SampleGraph, SampleDAC>
    {
        public PXSelect<SampleDAC> Items;

        public PXAction<SampleDAC> doStuff;

        // Modern event style
        protected virtual void _(Events.RowSelected<SampleDAC> e)
        {
            var row = e.Row;
        }

        protected virtual void _(Events.FieldUpdated<SampleDAC, SampleDAC.status> e)
        {
            // ...
        }

        // Legacy event style
        protected virtual void SampleDAC_OrderNbr_FieldUpdated(PXCache sender, PXFieldUpdatedEventArgs e)
        {
        }

        protected virtual void SampleDAC_RowPersisting(PXCache sender, PXRowPersistingEventArgs e)
        {
        }
    }

    public class ARInvoiceEntryExt : PXGraphExtension<ARInvoiceEntry>
    {
        public override void Initialize() { }

        protected virtual void _(Events.RowSelected<ARInvoice> e)
        {
        }

        protected virtual void ARInvoice_DocType_FieldVerifying(PXCache sender, PXFieldVerifyingEventArgs e)
        {
        }
    }

    public class ARInvoiceEntryExt2 : PXGraphExtension<ARInvoiceEntry>
    {
        protected virtual void _(Events.RowPersisting<ARInvoice> e) { }
    }
}
