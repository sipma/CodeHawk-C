/* Reproducer for false-positive stack-address-escape when a local array is
   used as a scratch buffer via a value-parameter pointer.

   Pattern: if the caller passes NULL for the output pointer, the function
   falls back to a local buffer so it can still compute and return the length.
   Because `s` is a plain `char *` (passed by value), the address of `t` can
   never reach the caller -- no escape is possible.  The analyzer should
   discharge all stack-address-escape obligations here as safe.

   Observed as 13 violations in u8encode_() in github_rdentato_u8c.
*/

int encode(int ch, char *s)
{
    char t[8];
    if (s == 0) s = t;
    *s++ = (char)ch;
    *s = '\0';
    return 1;
}
