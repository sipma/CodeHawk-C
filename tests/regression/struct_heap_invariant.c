/* Open proof obligations on a heap-allocated struct field accessed through
   a bounds-guarded setter.

   Pattern: a struct holds a malloc'd array and its size.  A "create"
   function establishes the invariant; a "set" function accesses the
   array after a bounds check.  Because the invariant

       not-null(v->data)  &&  size(v->data) == v->size

   is established by ivec_create but not tracked across the function
   boundary, the write v->data[i] = val in ivec_set carries open POs
   even though the bounds check i < 0 || i >= v->size is correct.

   Open POs in ivec_set at v->data[i] = val:
     not-null(v->data)              data field came from malloc in another fn
     valid-mem(v->data)             no cross-function heap model
     in-scope(v->data)              same
     initialized(v->data)           data pointer was set in another fn
     ptr-upper-bound-deref(v->data, i)   i < v->size does not imply
                                         i < size(v->data) without invariant
     upper-bound(v->data, i)        same
     lower-bound(v->data, i)        same (though i >= 0 is known from guard)

   The bounds guard is correct -- the access is safe -- but the interval
   domain cannot bridge the gap between the runtime value v->size and the
   allocated size of v->data without an explicit struct invariant.

   Inspired by bmp_set_pixel() in github_envyen_libbmp, which has 13 open
   POs on bmp->pixels[x][y] = pixel for the same reason.  The two levels
   of indirection there add further not-null, valid-mem, and
   ptr-upper-bound-deref obligations for the row pointers, but the root
   cause is identical.

   Goal: with data-structure invariant support expressing
       not-null(this->data)  &&  size(this->data) >= this->size
   as a postcondition of ivec_create and an assumed precondition of
   ivec_set, all open POs should be discharged as safe.
*/

#include <stdlib.h>

typedef struct {
    int  size;
    int *data;
} ivec_t;

/* Establishes the struct invariant: data is non-null, allocated for
   exactly size ints, all initialised to 0. */
ivec_t *ivec_create(int n)
{
    ivec_t *v = malloc(sizeof(ivec_t));
    v->data = malloc(sizeof(int) * n);
    int i;
    for (i = 0; i < n; ++i)
        v->data[i] = 0;
    v->size = n;
    return v;
}

/* Bounds-checked setter.  The guard is correct; the open POs arise
   because the analyzer has no model connecting size(v->data) to v->size. */
int ivec_set(ivec_t *v, int i, int val)
{
    if (i < 0 || i >= v->size)
        return 0;
    v->data[i] = val;
    return 1;
}
