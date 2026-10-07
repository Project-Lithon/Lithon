// 4.3  The dict hash and the key form, pinned down.
//
// dict_hash.h is the one definition both execution tiers are derived from, which
// is exactly what makes it worth a test: if bucket_of or canonical_key changed,
// the interpreter and the JIT would still agree with each other, because they
// share the header, and every dict test in the tree would still pass. A test
// that only compared the two tiers cannot see this class of change at all.
//
// So these assertions are about the values themselves, not about agreement.
//
// Three things are pinned:
//
//   1. Fixed keys land in fixed buckets. The collision test in
//      tests/programs/dict_table.py needs keys 1, 6, 9 and 14 to share one
//      bucket of a four bucket table; that only holds while the hash is this
//      one, and it is what makes that test a probe test rather than a coin toss.
//   2. Fibonacci hashing actually spreads. Keys 1..8 into eight buckets are not
//      all in one place, which is what the multiplier is for.
//   3. The key form is total. A narrow key is masked, a bool key is 0 or 1, and
//      a negative narrow key is masked into its stored form rather than being
//      compared as its full width value.

#include <cstdint>
#include <cstdio>

#include "dict_hash.h"

using namespace lithon::dict;

namespace {

int failures = 0;

void expect_eq(const char* what, int64_t got, int64_t want) {
    if (got == want) {
        std::printf("  ok   %s\n", what);
    } else {
        std::printf("  FAIL %s: got %lld, want %lld\n", what,
                    static_cast<long long>(got), static_cast<long long>(want));
        ++failures;
    }
}

void expect_true(const char* what, bool cond) {
    expect_eq(what, cond ? 1 : 0, 1);
}

} // namespace

int main() {
    std::printf("dict hash: fixed keys, fixed buckets\n");

    // The four bucket assignments the collision test depends on. 1, 6, 9 and 14
    // all start at bucket 2, so a four entry table of them is one chain.
    expect_eq("bucket_of(1, 4)", bucket_of(1, 4), 2);
    expect_eq("bucket_of(6, 4)", bucket_of(6, 4), 2);
    expect_eq("bucket_of(9, 4)", bucket_of(9, 4), 2);
    expect_eq("bucket_of(14, 4)", bucket_of(14, 4), 2);
    expect_eq("bucket_of(2, 4)", bucket_of(2, 4), 0);
    expect_eq("bucket_of(3, 4)", bucket_of(3, 4), 3);
    expect_eq("bucket_of(4, 4)", bucket_of(4, 4), 1);

    // A one bucket table is bucket 0 for every key, which is the degenerate
    // table the shift used to make undefined.
    expect_eq("bucket_of(-5, 1)", bucket_of(-5, 1), 0);
    expect_eq("bucket_of(1 << 40, 1)", bucket_of(int64_t{1} << 40, 1), 0);

    // Fibonacci hashing spreads small keys. Without this the eight keys of a
    // small table would sit in one chain and the probe path would be the only
    // thing any dict test could exercise.
    bool seen[8] = {false, false, false, false, false, false, false, false};
    int distinct = 0;
    for (int k = 1; k <= 8; ++k) {
        const int64_t b = bucket_of(k, 8);
        expect_true("bucket_of(k, 8) is inside the table", b >= 0 && b < 8);
        if (!seen[b]) {
            seen[b] = true;
            ++distinct;
        }
    }
    expect_eq("distinct buckets for keys 1..8 in 8", distinct, 8);

    // Every bucket index is a valid array index, at every table size, for keys
    // on both sides of zero. A mask that only held for the tables a test
    // happened to use would still pass every test above.
    for (int buckets = 1; buckets <= 1024; buckets *= 2) {
        bool in_range = true;
        for (int k = -64; k <= 64 && in_range; ++k) {
            const int64_t b = bucket_of(k, buckets);
            if (b < 0 || b >= buckets) in_range = false;
        }
        expect_true("every key lands in range for a power of two table", in_range);
    }

    std::printf("dict key form: bool and narrow keys are stored as themselves\n");

    // A bool key is its int value, so True and 1 are one key and not two.
    expect_eq("canonical_key(1, bool)", canonical_key(1, true, 64), 1);
    expect_eq("canonical_key(0, bool)", canonical_key(0, true, 64), 0);
    expect_eq("canonical_key(-1, bool)", canonical_key(-1, true, 64), 1);
    expect_eq("canonical_key(7, bool)", canonical_key(7, true, 64), 1);

    // A narrow key is masked to its width, so -1 and 255 are one entry in an
    // int[8] table. Comparing them as full width values would make them two
    // entries in the interpreter and one in the JIT.
    expect_eq("canonical_key(-1, int[8])", canonical_key(-1, false, 8), 255);
    expect_eq("canonical_key(255, int[8])", canonical_key(255, false, 8), 255);
    expect_eq("canonical_key(128, int[8])", canonical_key(128, false, 8), 128);
    expect_eq("canonical_key(-128, int[8])", canonical_key(-128, false, 8), 128);
    expect_eq("canonical_key(32767, int[16])", canonical_key(32767, false, 16), 32767);
    expect_eq("canonical_key(-1, int[16])", canonical_key(-1, false, 16), 65535);
    expect_eq("canonical_key(100, int[32])", canonical_key(100, false, 32), 100);
    expect_eq("canonical_key(-1, int[32])", canonical_key(-1, false, 32), 4294967295ll);

    // A full width key is itself, and a width of 0 or 64 is the same key rather
    // than a mask that would erase it.
    expect_eq("canonical_key(-1, int[64])", canonical_key(-1, false, 64), -1);
    expect_eq("canonical_key(-1, no width)", canonical_key(-1, false, 0), -1);

    // Canonicalisation has to agree with the hash, or a narrow key would be
    // stored under one bucket and looked for under another. This is the join
    // between the two halves of the table's definition.
    expect_eq("narrow key hashes as its stored form",
              bucket_of(canonical_key(-1, false, 8), 4),
              bucket_of(255, 4));
    expect_eq("bool key hashes as 1", bucket_of(canonical_key(1, true, 64), 4),
              bucket_of(1, 4));

    if (failures) {
        std::printf("\n%d check(s) FAILED\n", failures);
        return 1;
    }
    std::printf("\nall checks passed\n");
    return 0;
}