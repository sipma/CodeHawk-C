/* Binary search index-upper-bound precision gap.

   Models fold_search() from u8c (github_rdentato_u8c/u8c/u8c.c) at small
   scale to study two CodeHawk analysis phenomena.

   1. WHY OPEN POs PERSIST EVEN IN THE FIXED VERSION (fold_search_fixed):
      The loop invariant that bounds k is relational: i <= j <= N-1, so
      k = (i+j)/2 <= N-1.  The interval domain cannot express the
      relationship between i and j, so interval widening overestimates the
      upper bound of i (beyond N-1), which in turn pushes the computed
      upper bound of k above N-1.  The PO k <= N-1 remains open.
      The same widening gap affects fold_search_buggy (j=N); both functions
      produce identical open PO counts.  The difference is correctness, not
      what CodeHawk can prove.

   2. WHY THE BUGGY VERSION (fold_search_buggy) IS NOT FLAGGED AS VIOLATED:
      A violated PO means CodeHawk proves the constraint is *always* false.
      k's abstract value spans [0, k_max] where k_max > N-1, but the lower
      end of the interval is within [0, N-1].  Since k can be valid on
      early iterations, CodeHawk cannot prove k is always >= N, so the PO
      is open (might be violated) rather than violated (always violated).

   3. WHAT DOES PRODUCE A VIOLATION:
      fold_search_oob_step hard-codes the loop state at the step where the
      OOB read occurs (i=N, j=N, giving k=N).  The constant index k=N is
      immediately seen to be >= the array size, so the PO is violated.
      This confirms CodeHawk can detect the OOB access; it just cannot
      back-propagate that reachable state through the loop abstraction.
*/

#define N 5

static int arr[N] = {10, 20, 30, 40, 50};

/* Buggy: j = N = 5; valid indices are 0..4 (OOB when all arr elements < cp). */
int fold_search_buggy(int cp)
{
    int i = 0, j = N, k;
    while (i <= j) {
        k = (i + j) / 2;
        if (cp == arr[k]) return 1;
        if (cp < arr[k]) j = k - 1;
        else i = k + 1;
    }
    return 0;
}

/* Fixed: j = N-1 = 4; last valid index.  Same open POs as buggy version
   because the interval domain cannot exploit the invariant i <= j <= 4. */
int fold_search_fixed(int cp)
{
    int i = 0, j = N - 1, k;
    while (i <= j) {
        k = (i + j) / 2;
        if (cp == arr[k]) return 1;
        if (cp < arr[k]) j = k - 1;
        else i = k + 1;
    }
    return 0;
}

/* Hard-codes the loop state at the last valid iteration of fold_search_fixed:
   i = N-1, j = N-1  =>  k = (N-1 + N-1) / 2 = N-1, the last valid index.
   Produces a concrete safe index-upper-bound PO, confirming the fixed version
   is safe when the loop state is known precisely. */
int fold_search_safe_step(void)
{
    int i = N - 1, j = N - 1;
    int k = (i + j) / 2;
    return arr[k];
}

/* Hard-codes the loop state at the step where fold_search_buggy reads OOB:
   i = N, j = N  =>  k = (N + N) / 2 = N, which is >= array size N.
   Produces a concrete violated index-upper-bound PO. */
int fold_search_oob_step(void)
{
    int i = N, j = N;
    int k = (i + j) / 2;
    return arr[k];
}
