/* Regression test for R10: argv discharge handler uses argc#init (unconstrained
   formal-parameter value) instead of the compound path-constraint interval when
   the path to the access point passes through multiple guards.

   The handler uses a parameter-constraint mechanism: "what constraint on argc#init
   is implied by the guards that must be passed to reach this statement?"

   For a single guard `if (argc < N) return`, the constraint argc#init >= N is
   derived and discharges argv[i] for i < N.  But when the path to the access
   point has BOTH an outer guard establishing argc >= 1 AND being inside a
   branch that implies argc < N, the compound constraint argc#init in [1, N-1]
   is not synthesised.  The handler reports "Unable to prove index < argc#init"
   because it uses argc#init as unconstrained.

   Concretely:
     if (argc == 0)          -->  establishes argc >= 1
         return 1;
     if (argc < 3) {         -->  inside this branch: argc in [1, 2]
         printf(argv[0]);    -->  null-terminated(argv[0]) OPEN
         return 1;           -->  handler needs 0 < argc#init -- true since argc >= 1
     }                       -->  but handler can't prove it from argc#init alone
     return atoi(argv[1])    -->  null-terminated(argv[1]) SAFE: argc#init >= 3 > 1
          + atoi(argv[2]);   -->  null-terminated(argv[2]) SAFE: argc#init >= 3 > 2

   The SAFE cases work because the single guard argc >= 3 gives a direct
   parameter constraint for the after-guard accesses.  The OPEN cases arise
   inside the guard branch where the constraint requires composing two guards.

   Open POs at the argv[0] access (line 13):
     null-terminated(argv[0])    Unable to prove index < argc#init
     initialized-range(argv[0])  Unable to prove index < argc#init
     not-null(argv[0])           Unable to prove index < argc#init
     initialized(argv[0])        Unable to prove index < argc#init
     ptr-upper-bound(argv[0])    Unable to prove index < argc#init
     valid-mem(argv[0])          Unable to prove index < argc#init

   Goal: with a compound-guard parameter constraint (composing outer and inner
   guards), all six open POs should become safe.
*/

#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    if (argc == 0)         /* establishes argc in [1, +inf) */
        return 1;
    if (argc < 3) {        /* inside: argc in [1, 2]; argv[0] should be valid */
        printf("usage: %s a b\n", argv[0]);
        return 1;
    }
    /* argc in [3, +inf): single-guard constraint argc#init >= 3 fires, both safe */
    return atoi(argv[1]) + atoi(argv[2]);
}
