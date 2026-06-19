/* Regression test for R9: no-overlap discharge handler does not use
   allocation-site disjointness.

   fwrite generates a no-overlap PO checking that the buffer and FILE pointer
   arguments do not point to overlapping memory.  When the two pointers come
   from provably different allocation origins (stack, malloc, fopen) they
   cannot overlap, but the handler does not apply this rule.

   Pattern A -- stack local vs FILE* from fopen:
     FILE *fp = fopen(...);
     uint16_t val = 42;
     fwrite(&val, sizeof(uint16_t), 1, fp);
     -- no-overlap(&val, fp): &val is a stack address (addrof_localvar_...),
        fp is a heap address from fopen.  Stack and heap are disjoint
        allocation regions; no-overlap should discharge.

   Pattern B -- malloc buffer vs FILE* from fopen:
     FILE *fp = fopen(...);
     char *buf = malloc(n);
     fwrite(buf, n, 1, fp);
     -- no-overlap(buf, fp): buf comes from malloc@this-callsite,
        fp comes from fopen@that-callsite.  Two allocations from different
        call sites produce non-overlapping regions; no-overlap should discharge.

   The investigate output shows the analyzer already has both allocation
   origins in each case (different sx/regions fields for the two arguments)
   but the handler does not use the mutual-exclusivity rule.

   Goal: with an allocation-site disjointness rule in the no-overlap handler,
   all no-overlap POs here should become safe.
*/

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>

/* Pattern A */
void write_scalar_to_file(const char *path)
{
    FILE *fp = fopen(path, "wb");
    if (fp == NULL) return;
    uint16_t val = 42;
    fwrite(&val, sizeof(uint16_t), 1, fp);
    fclose(fp);
}

/* Pattern B */
void write_buffer_to_file(const char *path, int n)
{
    FILE *fp = fopen(path, "wb");
    if (fp == NULL) return;
    char *buf = malloc(n);
    if (buf == NULL) { fclose(fp); return; }
    fwrite(buf, n, 1, fp);
    free(buf);
    fclose(fp);
}
