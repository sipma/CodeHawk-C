/* Reproducer for open signed-to-signed-cast obligations caused by missing
   range propagation through bitwise OR.

   When a constant C with the high bit set (C > 127) is OR'd with a
   non-negative integer x, the result is provably in [C, INT_MAX], so:
     - signed-to-signed-cast-lb (result >= -128) is safe  (C >= 0 >= -128)
     - signed-to-signed-cast-ub (result <= 127)  is a violation  (result >= C > 127)

   Both are currently 'open' because xop_2_numexpr in xprUtil.ml (xprlib)
   translates XBOr to an unconstrained fresh temporary (RANDOM), so the
   abstract interpreter produces no invariant for the OR expression.

   Observed as 18 open obligations (9 lb + 9 ub) in u8encode_() in u8c.

   Recommended fix (Option A) -- no new infrastructure needed:
   In cCHPOCheckSignedToSignedCastLB.ml, extend xpr_implies_safe_lb to
   recognise XOp(XBOr, [XConst c; _]) and conclude safe when c#geq safelb.
   In cCHPOCheckSignedToSignedCastUB.ml, extend xpr_implies_safe_ub to
   recognise XOp(XBOr, [XConst c; _]) and conclude violation when c#gt safeub.
*/

void encode_lead_byte(int x, char *s)
{
    if (x >= 0)
        *s = (char)(0xC0 | x);
}
