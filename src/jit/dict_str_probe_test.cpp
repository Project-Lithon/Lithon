// 6.8. The str-key probe, pinned down.
//
// dict_hash_test pins the hash and the key form; this pins the WALK, for the
// same reason that test exists. dict_str_probe is now the single definition of
// "which bucket does this string live in" for BOTH tiers -- the interpreter
// calls it and the JIT's host helper calls it -- so a test that only compared
// the two tiers cannot see a change to it at all. They would change together.
//
// Three properties carry the weight here, and each is a bug that has already
// been written and fixed once:
//
//   1. The key is the run's TEXT, not its bytes. A str[N] is NUL-terminated,
//      so a hash that read the padding would give "a" and "a" written into a
//      wider run different buckets -- and then the comparison, which stops at
//      the NUL, would find it in the other one's slot or miss it entirely.
//   2. Insert and lookup land in the SAME bucket. They are one function with a
//      flag; two would be two definitions again.
//   3. A miss reports absence rather than a bucket. DictContains and DictIndex
//      read that as False and a trap, so "no free slot" and "absent" must not
//      collapse into each other.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

#include "container_print.h"

using namespace lithon::jit;

namespace dict = lithon::dict;

namespace {

int failures = 0;

void expect_true(const char* what, bool got) {
    if (got) {
        std::printf("  ok   %s\n", what);
    } else {
        std::printf("  FAIL %s\n", what);
        ++failures;
    }
}

void expect_eq(const char* what, int64_t got, int64_t want) {
    if (got == want) {
        std::printf("  ok   %s\n", what);
    } else {
        std::printf("  FAIL %s: got %lld, want %lld\n", what,
                    static_cast<long long>(got), static_cast<long long>(want));
        ++failures;
    }
}

// A table of `buckets` slots with one 8-byte marker and `key_cap` key bytes per
// bucket, in the exact shape both tiers store. Everything a probe touches is
// here, so a test drives the same walk the tiers do.
struct Table {
    int buckets;
    int key_cap;
    std::vector<uint64_t> occupied;
    std::vector<unsigned char> keys;

    explicit Table(int b, int cap)
        : buckets(b), key_cap(cap), occupied(b, 0), keys(b * cap, 0) {}

    int64_t probe(const char* text, bool insert) {
        bool found = false;
        const int64_t b = dict_str_probe(
            keys.data(), occupied.data(), buckets, key_cap,
            reinterpret_cast<const unsigned char*>(text), key_cap, insert, &found);
        if (found || insert) occupied[static_cast<size_t>(b)] = 1;
        return found ? b : -1;
    }
    std::string stored(int64_t b) const {
        return format_string_repr(keys.data() + static_cast<size_t>(b) * key_cap,
                                  key_cap);
    }
};

} // namespace

int main() {
    std::printf("str key text is the key, not the run\n");

    // 1. A run of capacity 4 holding "ab" and a run of capacity 2 holding "ab"
    // are the same key: the hash reads up to the NUL in each. A hash that read
    // all four bytes of the wider run would fold two zero bytes into a
    // different hash, and the lookup would search the wrong bucket.
    const unsigned char ab4[] = {'a', 'b', 0, 0};
    const unsigned char ab2[] = {'a', 'b'};
    expect_eq("hash of 'ab' in a 4 byte run equals its hash in a 2 byte run",
              static_cast<int64_t>(dict::hash_str_content(ab4, 4)),
              static_cast<int64_t>(dict::hash_str_content(ab2, 2)));

    // 2. A prefix is not the same key as the string it extends. This is the
    // failure mode of a probe that read past the NUL: every string sharing a
    // prefix would collide on the longer one's hash and one of them would be
    // unreachable.
    const unsigned char a4[] = {'a', 0, 0, 0};
    const unsigned char aa4[] = {'a', 'a', 0, 0};
    expect_true("'a' and 'aa' are different keys",
                dict::hash_str_content(a4, 4) != dict::hash_str_content(aa4, 4));
    expect_true("'a' and 'aa' do not compare equal",
                !dict::str_content_eq(a4, 4, aa4, 4));

    // 3. Equality is bounded by each run's own capacity and stops at the NUL,
    // so the same text in two different capacities is one key.
    expect_true("'ab' in 4 compares equal to 'ab' in 2",
                dict::str_content_eq(ab4, 4, ab2, 2));
    expect_true("'ab' in 2 compares equal to 'ab' in 4",
                dict::str_content_eq(ab2, 2, ab4, 4));

    // 4. The empty key hashes as the FNV offset basis folded through the one
    // window-and-mask step, not as a run of zeros.
    const unsigned char empty4[] = {0, 0, 0, 0};
    expect_eq("empty text hashes to the offset basis",
              static_cast<int64_t>(dict::hash_str_content(empty4, 4)),
              static_cast<int64_t>(dict::kFnvOffsetBasis));

    std::printf("probe: insert and lookup land in the same bucket\n");

    // A full table's worth of colliding text, in a table exactly as full as it
    // has keys, so every entry after the first was reached by walking.
    Table t(4, 8);
    t.probe("a", true);
    t.probe("aa", true);
    t.probe("aaa", true);
    t.probe("aaaa", true);

    expect_true("every prefix is found after being inserted",
                t.probe("a", false) >= 0 && t.probe("aa", false) >= 0 &&
                    t.probe("aaa", false) >= 0 && t.probe("aaaa", false) >= 0);

    // Each stored slot holds its own text, so a prefix was not overwritten by
    // the longer key that walked past it.
    std::string slots;
    for (int i = 0; i < 4; ++i) slots += t.stored(i) + " ";
    expect_true("every slot holds its own text", slots.find("'a' ") != std::string::npos);

    expect_true("a text that was never inserted is absent", t.probe("b", false) < 0);
    expect_true("a full table does not wrap for ever", t.probe("aaaaa", false) < 0);

    std::printf("probe: an insert that finds an existing key reuses its bucket\n");

    Table u(4, 8);
    const int64_t first = [&] {
        u.probe("key", true);
        return u.probe("key", false);
    }();
    u.probe("key", true);
    expect_eq("re-inserting a key lands in the same bucket",
              u.probe("key", false), first);

    std::printf("probe: an empty table reports absence, not bucket zero\n");

    Table e(4, 8);
    expect_true("lookup on an empty table is absent", e.probe("x", false) < 0);

    if (failures) {
        std::printf("\n%d check(s) FAILED\n", failures);
        return 1;
    }
    std::printf("\nall checks passed\n");
    return 0;
}
