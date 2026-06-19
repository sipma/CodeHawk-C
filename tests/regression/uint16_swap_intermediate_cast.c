/* Reproducer for false-positive signed-to-unsigned-cast-ub caused by
   missing intermediate narrowing-cast truncation in a byte-swap expression.

   Pattern (from the GLib-style UINT16_SWAP_LE_BE_CONSTANT macro in libbmp):

     (uint16_t) (
       (uint16_t) ((uint16_t)(val) >> 8) |
       (uint16_t) ((uint16_t)(val) << 8) )

   The inner cast (uint16_t)((uint16_t)(val) << 8) truncates the left-shifted
   value to 16 bits before the OR.  The OR of two uint16_t-bounded operands
   is always in [0, 65535], so the outer cast to uint16_t is always safe.

   Root cause: the abstract interpreter does not model caste(uint16_t, X) as
   a modular reduction when X > 65535.  The abstract value of (uint16_t)(val)
   << 8 is kept as the un-truncated int, and the OR is computed with that
   large value, giving a lower bound that exceeds 65535.

   This is distinct from R4 (signed_to_signed_cast_bitor.c) where XBOr ->
   RANDOM (no range at all).  Here the OR IS computed correctly -- but with
   the wrong (un-truncated) operand value.

   Three cases:

   (a) byte_swap_hi: val = 63488 (0xF800)
       val << 8 = 16252928; (uint16_t)(16252928) = 0
       correct result: 248 | 0 = 248 (safe).
       CodeHawk computes: 248 | 16252928 = 16253176 > 65535 -> violation.

   (b) byte_swap_mid: val = 2016 (0x07E0)
       val << 8 = 516096; (uint16_t)(516096) = 57344
       correct result: 7 | 57344 = 57351 (safe).
       CodeHawk computes: 7 | 516096 = 516103 > 65535 -> violation.

   (c) byte_swap_lo: val = 31 (0x001F)
       val << 8 = 7936; (uint16_t)(7936) = 7936 (no truncation needed)
       correct result: 0 | 7936 = 7936 (safe).
       CodeHawk computes: 0 | 7936 = 7936 <= 65535 -> correctly safe.

   Recommended fix: in the abstract domain's evaluation of caste(uint16_t, X),
   when the abstract value of X has an upper bound exceeding 65535, intersect
   the result with [0, 65535] (modular-reduction semantics for unsigned casts).
   After this fix all three cases should be discharged as safe.

   Observed as 2 violations in bmp_write_palette() in github_envyen_libbmp.
*/

#include <stdint.h>

/* (a) val << 8 overflows uint16_t -- intermediate truncation discards high bits */
uint16_t byte_swap_hi(void)
{
    uint16_t val = 63488;   /* 0xF800 */
    return (uint16_t)((uint16_t)((uint16_t)(val) >> 8) |
                      (uint16_t)((uint16_t)(val) << 8));
}

/* (b) val << 8 overflows uint16_t -- intermediate truncation keeps low bits */
uint16_t byte_swap_mid(void)
{
    uint16_t val = 2016;    /* 0x07E0 */
    return (uint16_t)((uint16_t)((uint16_t)(val) >> 8) |
                      (uint16_t)((uint16_t)(val) << 8));
}

/* (c) val << 8 fits in uint16_t -- no truncation, correctly proved safe */
uint16_t byte_swap_lo(void)
{
    uint16_t val = 31;      /* 0x001F */
    return (uint16_t)((uint16_t)((uint16_t)(val) >> 8) |
                      (uint16_t)((uint16_t)(val) << 8));
}
