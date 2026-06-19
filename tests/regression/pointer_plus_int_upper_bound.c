/*
 * Reproducer for a CodeHawk-C expression-reconstruction failure seen in
 * perlbench cpan/Digest-MD5/MD5.c:base64_16.
 *
 * The relevant shape is pointer increment on an output buffer:
 *
 *     *d++ = table[index];
 *
 * CH-C can lower pointer arithmetic to abstract XPlus/XMinus expressions.
 * In the failing checker path, XPlus over a char pointer and an integer was
 * reconstructed as arithmetic PlusA and get_integer_promotion was called on
 * (char *) and int, causing a fatal analyzer failure while checking an
 * upper-bound obligation.
 */

static const char table[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

char *pointer_plus_int_upper_bound(const unsigned char *from, char *to) {
    const unsigned char *end = from + 16;
    char *d = to;

    while (from < end) {
        unsigned char c1 = *from++;
        *d++ = table[c1 >> 2];
    }

    *d = '\0';
    return to;
}
