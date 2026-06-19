/* Regression test for not-zero false positive on static const divisors.

   Pattern: static const variables initialized with compile-time constant
   expressions are not recognized as non-zero by CodeHawk. Any division or
   modulo that uses one as a divisor produces an open not-zero PO, even
   though all values are provably non-zero.

   Contrast: #define macros ARE treated as integer literals and do discharge
   not-zero obligations.

   Root cause: CodeHawk does not propagate constant values through static const
   variable initializers. Even a direct literal like `static const int A = 60`
   is modelled as an opaque global variable of unknown value. A chain like
   `static const int B = A * 60` is even further removed from any concrete
   bound.

   Three levels of initialization depth are shown:
     depth 0: literal initializer  (SECONDS_PER_MINUTE = 60)
     depth 1: one-level product    (SECONDS_PER_HOUR = MINUTES * SECS)
     depth 2: two-level product    (SECONDS_PER_DAY  = HOURS  * SECS_PER_HOUR)

   Both / and % are included because both generate not-zero obligations.

   Goal: with constant-value propagation for static const initializers, all
   not-zero POs below should become safe.
*/

static const int SECONDS_PER_MINUTE = 60;
static const int MINUTES_PER_HOUR   = 60;
static const int HOURS_PER_DAY      = 24;

static const int SECONDS_PER_HOUR = MINUTES_PER_HOUR * SECONDS_PER_MINUTE;
static const int SECONDS_PER_DAY  = HOURS_PER_DAY    * SECONDS_PER_HOUR;

/* Depth 0: literal initializer used as divisor. */
int div_by_literal_const(int seconds)
{
    return seconds / SECONDS_PER_MINUTE;    /* not-zero(SECONDS_PER_MINUTE) OPEN */
}

int mod_by_literal_const(int seconds)
{
    return seconds % SECONDS_PER_MINUTE;    /* not-zero(SECONDS_PER_MINUTE) OPEN */
}

/* Depth 1: one-level product used as divisor. */
int div_by_derived_const(int seconds)
{
    return seconds / SECONDS_PER_HOUR;      /* not-zero(SECONDS_PER_HOUR) OPEN */
}

int mod_by_derived_const(int seconds)
{
    return seconds % SECONDS_PER_HOUR;      /* not-zero(SECONDS_PER_HOUR) OPEN */
}

/* Depth 2: two-level product used as divisor. */
int div_by_chained_const(int seconds)
{
    return seconds / SECONDS_PER_DAY;       /* not-zero(SECONDS_PER_DAY) OPEN */
}

int mod_by_chained_const(int seconds)
{
    return seconds % SECONDS_PER_DAY;       /* not-zero(SECONDS_PER_DAY) OPEN */
}
