using System;
using PX.Data;
using PX.Data.BQL;

namespace AcuBuddy.Sample
{
    [Serializable]
    [PXCacheName("Sample DAC")]
    public class SampleDAC : IBqlTable
    {
        #region OrderNbr
        [PXDBString(15, IsKey = true, InputMask = ">CCCCCCCCCCCCCCC")]
        [PXDefault]
        [PXUIField(DisplayName = "Order Nbr.", Visibility = PXUIVisibility.SelectorVisible)]
        public virtual string OrderNbr { get; set; }
        public abstract class orderNbr : PX.Data.BQL.BqlString.Field<orderNbr> { }
        #endregion

        #region Status
        [PXDBString(1, IsFixed = true)]
        [PXDefault("N")]
        [PXUIField(DisplayName = "Status")]
        public virtual string Status { get; set; }
        public abstract class status : PX.Data.BQL.BqlString.Field<status> { }
        #endregion

        [PXDBDecimal(4)]
        [PXDefault(TypeCode.Decimal, "0.0")]
        public virtual decimal? Amount { get; set; }
        public abstract class amount : PX.Data.BQL.BqlDecimal.Field<amount> { }
    }

    public class ARInvoiceExt : PXCacheExtension<PX.Objects.AR.ARInvoice>
    {
        [PXDBString(60)]
        [PXUIField(DisplayName = "Custom Note")]
        public virtual string UsrCustomNote { get; set; }
        public abstract class usrCustomNote : PX.Data.BQL.BqlString.Field<usrCustomNote> { }

        [PXDBBool]
        [PXDefault(false)]
        public virtual bool? UsrFlagged { get; set; }
        public abstract class usrFlagged : PX.Data.BQL.BqlBool.Field<usrFlagged> { }
    }
}
