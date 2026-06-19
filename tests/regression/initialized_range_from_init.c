/* Regression test for R8: initialized-range discharge handler does not derive
   from scalar assignment, struct-copy assignment, or memset.

   fwrite generates an initialized-range PO for its buffer argument.
   All three patterns below produce open POs even though the memory is
   provably initialized by the time fwrite is called.

   Pattern A -- scalar local with constant initializer:
     uint16_t val = 42;
     fwrite(&val, sizeof(uint16_t), 1, fp);
   The scalar initialized(val) would be safe, but initialized-range(&val, 2)
   is open because the handler does not derive byte-range initialization
   from a scalar assignment fact.

   Pattern B -- struct-copy initialized local:
     pair_t p = *src;
     fwrite(&(p.a), sizeof(uint32_t), 1, fp);
   initialized((*src)) is delegated to the API (caller's guarantee).
   The struct copy p = *src initializes all fields, but
   initialized-range(&p.a, 4) is open because the handler does not
   propagate struct-level initialization to per-field byte ranges.

   Pattern C -- memset-initialized buffer:
     char *buf = malloc(n);
     memset(buf, 0, n);
     fwrite(buf, n, 1, fp);
   memset(buf, 0, n) establishes initialized-range(buf, n) semantically,
   but the handler has no transfer function for memset, so the PO is open.

   Goal: after adding the three transfer functions, all initialized-range
   POs here should become safe (patterns A and B) or safe once buf is
   non-null (pattern C).
*/

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* Pattern A */
void write_scalar(FILE *fp)
{
    uint16_t val = 42;
    fwrite(&val, sizeof(uint16_t), 1, fp);
}

/* Pattern B */
typedef struct { uint32_t a; uint16_t b; } pair_t;

void write_struct_fields(pair_t *src, FILE *fp)
{
    pair_t p = *src;
    fwrite(&(p.a), sizeof(uint32_t), 1, fp);
    fwrite(&(p.b), sizeof(uint16_t), 1, fp);
}

/* Pattern C */
void write_memset_buffer(FILE *fp, int n)
{
    char *buf = malloc(n);
    memset(buf, 0, n);
    fwrite(buf, n, 1, fp);
    free(buf);
}
