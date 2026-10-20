#pragma once

// 4.3. The one definition of the dict hash, shared by both execution tiers.
//
// It lives here rather than in either tier because the two tiers have to agree
// on it exactly. The interpreter walks buckets in C++, and the JIT emits the
// same walk in machine code. If they disagreed, construction would put a key in
// one bucket and the lookup would search another, and every lookup would miss.
// That failure would look like a broken table rather than a broken hash, so it
// is worth having a single place that both are derived from.
//
// Fibonacci hashing. The multiply by the golden ratio spreads the low bits of
// a small integer across the whole word before the high bits are taken, so 1, 2,
// 3 and 4 land far apart instead of four adjacent buckets. Without it a table of
// small keys degenerates into a single collision chain, and every lookup becomes
// a linear scan, which would make the probe path the only thing the tests ever
// exercised.

#include <cstdint>

namespace lithon::dict {

// 2^64 / phi, rounded to the nearest odd integer. Odd so it is invertible mod a
// power of two, which is what keeps the map from many-to-one on the low bits.
constexpr uint64_t kHashMultiplier = 0x9E3779B97F4A7C15ull;

// The number of bits the bucket index occupies. The count is required to be a
// power of two, which the typechecker enforces, so this is exact for every
// legal table.
inline int log2_buckets(int buckets) {
    int bits = 0;
    while ((1 << bits) < buckets) ++bits;
    return bits;
}

// How far down the product the bucket index starts.
//
// The index is taken from the HIGH bits of the product, so the shift is the
// distance from the top of the word: 61 for a table of 8 buckets, 0 for one of
// 2^64. This is the part that is easy to get backwards. Shifting by
// log2(buckets) instead would still spread small keys, so a wrong version does
// not look wrong at all, it just picks a different window of the product.
inline int hash_shift(int buckets) {
    const int bits = log2_buckets(buckets);
    return bits >= 64 ? 64 : 64 - bits;
}

// The bucket a key starts its probe at. The mask covers the low log2(buckets)
// bits of what the shift leaves behind, which are the top bits of the product.
inline int64_t bucket_of(int64_t key, int buckets) {
    const uint64_t h = static_cast<uint64_t>(key) * kHashMultiplier;
    const int shift = hash_shift(buckets);
    // A one bucket table shifts by 64, which is undefined for a 64-bit shift, and
    // the answer is 0 for every key, so it is short circuited. The JIT skips the
    // shift for the same reason.
    if (shift >= 64) return 0;
    return static_cast<int64_t>((h >> shift) & static_cast<uint64_t>(buckets - 1));
}

// The form a key is stored and compared in.
//
// Two things decide it. A bool key is its int value, so True is the key 1, which
// is why {True: a, 1: b} is one entry and not two. A key narrower than 64 bits
// is truncated to its width, because that is all the frame has room for: an
// int[8] table stores one byte per key, so reading it back has to be compared
// against one byte or -1 and 255 would be different entries in the two tiers.
//
// This is written once and called by both tiers for the same reason the hash is.
// A second definition of "the same key" is a second definition of the table.
inline int64_t canonical_key(int64_t key, bool is_bool, int width_bits) {
    if (is_bool) return key ? 1 : 0;
    if (width_bits <= 0 || width_bits >= 64) return key;
    const uint64_t mask = (uint64_t{1} << width_bits) - 1;
    return static_cast<int64_t>(static_cast<uint64_t>(key) & mask);
}

// 4.3. String keys.
//
// A str[N] key is a run of bytes rather than a word, so it needs a byte hash and
// a byte compare where an int key needs a multiply and a single cmp. Both are
// defined here, once, for the same reason the integer hash is: the interpreter
// walks the table in C++ and the JIT asks a host helper to walk it in C++ too,
// and both must agree byte for byte about what "the same key" means.
//
// FNV-1a. One xor and one multiply per byte, which is the cheapest hash that
// still scatters short keys -- and short keys are the whole point of a bounded
// string. A sum would make "ab" and "ba" collide, and a table of two-character
// keys would degenerate into one probe chain.
//
// The bytes hashed are the string's CONTENT: up to the first NUL, capped at the
// run's capacity. Content rather than the whole run because the capacities can
// differ -- `d["ab"]` materialises a str[3] search key while the table stores a
// str[16] slot -- and two strings are the same key exactly when their text
// agrees. Hashing the padding instead would make the same text two different
// keys depending on the capacity it was copied from.
constexpr uint64_t kFnvOffsetBasis = 14695981039346656037ull;
constexpr uint64_t kFnvPrime = 1099511628211ull;

inline uint64_t hash_str_content(const uint8_t* p, int cap) {
    uint64_t h = kFnvOffsetBasis;
    for (int i = 0; i < cap; ++i) {
        const uint8_t c = p[i];
        if (c == 0) break;
        h ^= c;
        h *= kFnvPrime;
    }
    return h;
}

// The starting bucket for a string key. The byte hash is folded back through
// bucket_of so a str key and an int key share the one window-and-mask step.
inline int64_t bucket_of_str(const uint8_t* p, int cap, int buckets) {
    return bucket_of(static_cast<int64_t>(hash_str_content(p, cap)), buckets);
}

// Content equality, bounded by each run's capacity. Missing bytes count as NUL,
// so a shorter run and a longer run with the same text compare equal, which is
// what makes a str[3] search key match a str[16] stored slot.
inline bool str_content_eq(const uint8_t* a, int a_cap, const uint8_t* b, int b_cap) {
    int i = 0;
    while (true) {
        const uint8_t ca = (i < a_cap) ? a[i] : 0;
        const uint8_t cb = (i < b_cap) ? b[i] : 0;
        if (ca != cb) return false;
        if (ca == 0) return true;
        ++i;
    }
}

} // namespace lithon::dict