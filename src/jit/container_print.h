#pragma once

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include "float_runtime.h"
#include "dict_hash.h"

// 6.1. Host-side support for printing a whole container.
//
// A container is a run of cells, not a value, so it can never be an operand of
// the scalar print family. Instead the frontend emits one PrintContainer op that
// NAMES the container variable, and each tier renders the aggregate itself:
//
//   list  -> [e0, e1, ...]
//   tuple -> (e0, e1, ...)   (and (e0,) for a one-element tuple)
//   dict  -> {k0: v0, k1: v1, ...}   in bucket order
//
// The joining and the scalar leaf rendering live here ONCE so the interpreter
// and the JIT cannot drift: the interpreter calls format_* directly on the
// values it holds, and the JIT calls the two host entry points below, which call
// the same format_* over the flat bytes in its frame. That is the same
// structural guarantee host_format_double already gives the float path.
//
// The host entry points take exactly two arguments (a base address and a
// descriptor) because the emitted-call convention only marshals the first two
// integer arguments in registers; every layout constant that is known at compile
// time therefore travels in the descriptor rather than as its own argument.

namespace lithon::jit {

// Leaf kind codes shared by the emitted call and the interpreter. They select
// the renderer, not the storage width (which rides alongside).
//
// 6.2. Str is a leaf ONLY inside an aggregate: a `list[str[8], N]` prints each
// element the way Python's repr quotes a string (`['ab', 'cd']`), while a
// standalone `print(s)` prints the bare bytes (str(), not repr()). The leaf's
// `width` in this case is the string's byte capacity, not a bit width.
enum class LeafCode : int { Int = 0, Float = 1, Bool = 2, Str = 3 };

inline int leaf_code_of(const std::string& kind) {
    if (kind == "float") return static_cast<int>(LeafCode::Float);
    if (kind == "bool") return static_cast<int>(LeafCode::Bool);
    if (kind == "int") return static_cast<int>(LeafCode::Int);
    return -1;
}

// Renders one scalar leaf from an already-resolved value. Used by the
// interpreter, whose containers hold values rather than bytes.
inline void append_leaf_value(std::string& out, int code, int64_t iv, double fv,
                              bool bv) {
    if (code == static_cast<int>(LeafCode::Float)) {
        // The SAME function the scalar float print path uses, so a float inside
        // a container renders exactly as it does on its own.
        out += host_format_double(fv);
    } else if (code == static_cast<int>(LeafCode::Bool)) {
        out += bv ? "True" : "False";
    } else {
        out += std::to_string(iv);
    }
}

// Renders one scalar leaf from its packed bytes. Used by the JIT helper. An int
// is sign-extended from its declared width exactly as an Index load is; a bool
// is one byte; a float is an 8-byte double.
inline void append_leaf_bytes(std::string& out, int code, int width,
                              const unsigned char* cell) {
    if (code == static_cast<int>(LeafCode::Float)) {
        double d = 0.0;
        std::memcpy(&d, cell, sizeof(double));
        out += host_format_double(d);
    } else if (code == static_cast<int>(LeafCode::Bool)) {
        out += (*cell ? "True" : "False");
    } else {
        switch (width) {
            case 8:  out += std::to_string(static_cast<int>(*reinterpret_cast<const int8_t*>(cell))); break;
            case 16: { int16_t t; std::memcpy(&t, cell, 2); out += std::to_string(static_cast<int>(t)); break; }
            case 32: { int32_t t; std::memcpy(&t, cell, 4); out += std::to_string(t); break; }
            default: { int64_t t; std::memcpy(&t, cell, 8); out += std::to_string(t); break; }
        }
    }
}

inline std::string format_sequence(const std::vector<std::string>& elems,
                                   bool is_tuple) {
    std::string out;
    out += is_tuple ? '(' : '[';
    for (size_t i = 0; i < elems.size(); ++i) {
        if (i) out += ", ";
        out += elems[i];
    }
    // Python spells a one-element tuple with a trailing comma so it is not read
    // as a parenthesised scalar: (e0,) not (e0). An empty tuple is ().
    if (is_tuple && elems.size() == 1) out += ',';
    out += is_tuple ? ')' : ']';
    return out;
}

inline std::string format_mapping(const std::vector<std::string>& keys,
                                  const std::vector<std::string>& values) {
    std::string out = "{";
    for (size_t i = 0; i < keys.size(); ++i) {
        if (i) out += ", ";
        out += keys[i];
        out += ": ";
        out += values[i];
    }
    out += '}';
    return out;
}

// The layout of a list/tuple print, fixed at compile time except for the base.
struct ContainerSequenceDesc {
    int64_t count;
    int64_t stride;
    int code;
    int width;
    int is_tuple;
    // Phase 6.7. Byte offset (from the base the helper is handed) of the list's
    // live-length companion, or kNoLenOffset when there is none (a tuple, or a
    // hand-built descriptor). Frame offsets run DOWNWARD from RBP, so a real
    // offset is negative -- the "absent" sentinel cannot be a plain -1.
    int64_t len_offset;
};

// Sentinel for "this sequence has no run-time length". Frame offsets are
// negative, so no real len_offset can ever equal INT64_MIN.
inline constexpr int64_t kNoLenOffset = INT64_MIN;

// 6.2. Bounded-string rendering. A str[N] is N bytes whose content ends at the
// first NUL. `format_string` prints the bare text (a standalone print(s));
// `format_string_repr` produces Python's repr, used when a str is an ELEMENT of
// an aggregate (`['ab', 'cd']`), matching `print("ab")` vs `print(["ab"])`.
inline std::string format_string(const unsigned char* p, int64_t cap) {
    std::string out;
    for (int64_t i = 0; i < cap; ++i) {
        const unsigned char c = p[i];
        if (c == 0) break;
        out.push_back(static_cast<char>(c));
    }
    return out;
}

// 6.8. How many bytes of a run are its text: everything up to the first NUL,
// capped at the run's capacity. Both the hash and the comparison below read
// exactly this, and reading them differently is the one way two tiers could
// disagree about a str key while each still looks right: hashing a run that ends
// in NUL stops at the NUL, so a zero-padded slot and an unpadded one agree on
// the KEY, and then two different keys whose text is a prefix of one another
// land in the same bucket and one of them is unreachable. This is the bound both
// read with, stated once.
inline int64_t string_content_len(const unsigned char* p, int64_t cap) {
    int64_t n = 0;
    while (n < cap && p[n] != 0) ++n;
    return n;
}

inline std::string format_string_repr(const unsigned char* p, int64_t cap) {
    std::string body;
    for (int64_t i = 0; i < cap; ++i) {
        const unsigned char c = p[i];
        if (c == 0) break;
        body.push_back(static_cast<char>(c));
    }
    // Python chooses a quoting character that minimizes escaping: single quotes
    // normally, double quotes when the text has a single quote but no double.
    const bool has_single = body.find('\'') != std::string::npos;
    const bool has_double = body.find('"') != std::string::npos;
    const char quote = (has_single && !has_double) ? '"' : '\'';
    std::string out;
    out.push_back(quote);
    for (unsigned char c : body) {
        if (c == static_cast<unsigned char>(quote)) {
            out.push_back('\\');
            out.push_back(static_cast<char>(c));
        } else if (c == '\\') {
            out += "\\\\";
        } else if (c == '\n') {
            out += "\\n";
        } else if (c == '\r') {
            out += "\\r";
        } else if (c == '\t') {
            out += "\\t";
        } else if (c < 0x20 || c == 0x7f) {
            char buf[8];
            std::snprintf(buf, sizeof buf, "\\x%02x", c);
            out += buf;
        } else {
            // Printable ASCII, and every byte >= 0x80 emitted raw so a UTF-8
            // sequence passes through as the character, as Python's repr does
            // for printable text.
            out.push_back(static_cast<char>(c));
        }
    }
    out.push_back(quote);
    return out;
}

// 4.1 (phase 3). The layout of a nested list/tuple print. A nested container is
// one flat leaf run, so the print is a depth-first walk of a recursive
// descriptor: a sequence node visits `count` children spaced `byte_stride`
// apart, a leaf node renders `width` bytes with `code`. The tree is built
// bottom-up at compile time (child pointers must be stable) and registered in
// the same pool as the flat descriptors.
struct FlatPrintNode {
    int is_leaf;
    int code;    // leaf: LeafCode
    int width;   // leaf: bits
    int is_tuple;      // sequence
    int64_t count;     // sequence
    int64_t byte_stride;
    const FlatPrintNode* child;
    // Phase 6.7. For the ROOT node of a list only: the byte offset (from the
    // base the helper is handed) of the live-length companion, or kNoLenOffset
    // for a tuple / inner level / hand-built node. host_print_flat renders
    // min(live, count) top-level children when it is present.
    int64_t len_offset;
};

inline void render_flat_node(std::string& out, const FlatPrintNode* node,
                             const unsigned char* p) {
    if (node->is_leaf) {
        // 6.2. A str leaf renders as a quoted repr (width is its byte capacity);
        // the numeric leaves render as before.
        if (node->code == static_cast<int>(LeafCode::Str))
            out += format_string_repr(p, node->width);
        else
            append_leaf_bytes(out, node->code, node->width, p);
        return;
    }
    out += node->is_tuple ? '(' : '[';
    for (int64_t i = 0; i < node->count; ++i) {
        if (i) out += ", ";
        render_flat_node(out, node->child, p + i * node->byte_stride);
    }
    if (node->is_tuple && node->count == 1) out += ',';
    out += node->is_tuple ? ')' : ']';
}

inline void host_print_flat(const void* base, const FlatPrintNode* root) {
    std::string out;
    const unsigned char* p = static_cast<const unsigned char*>(base);
    // Phase 6.7. A list's root carries its live length; render only that many
    // top-level children (inner levels keep their declared counts).
    if (root->len_offset != kNoLenOffset) {
        int64_t live = 0;
        std::memcpy(&live, p + root->len_offset, 8);
        FlatPrintNode top = *root;
        if (live < 0) live = 0;
        if (live < top.count) top.count = live;
        render_flat_node(out, &top, p);
    } else {
        render_flat_node(out, root, p);
    }
    std::fputs(out.c_str(), stdout);
}

// The layout of a dict print. All three regions are addressed relative to the
// frame base, so one base pointer covers keys, occupancy and values.
struct ContainerMappingDesc {
    int64_t buckets;
    int64_t keys_offset;
    int64_t occupied_offset;
    int64_t values_offset;
    int64_t key_stride;
    int64_t value_stride;
    int key_code;
    int key_width;
    int value_code;
    int value_width;
    // 4.3. A str key is a run of bytes, not a leaf, so it renders from its bytes
    // as the Python repr rather than through append_leaf_bytes (whose str case
    // is the "unknown kind" path and would print the first bytes as a number).
    int key_is_str;
    // 6.9. A dict VALUE may itself be a list or a tuple, in which case it has no
    // leaf code and is rendered by the recursive flat walk over the bucket's own
    // slice of the value region -- the same walk a list element uses, so a
    // nested value comes out with the same brackets the interpreter writes.
    // `value_is_flat` is 0 for a scalar value, and `value_flat` then carries the
    // node the walk starts at.
    int value_is_flat;
    const FlatPrintNode* value_flat;
};

// Keeps a compile-time descriptor alive for the life of the process. Emitted
// code carries the descriptor's address and may run long after compilation, so
// the storage must outlive the compiler. Heap objects are used because their
// addresses are stable even as the pool vector grows.
template <typename T>
inline const T* register_print_desc(const T& d) {
    static std::vector<std::unique_ptr<T>> pool;
    pool.push_back(std::make_unique<T>(d));
    return pool.back().get();
}

// JIT entry points. Called by address from emitted machine code, the same way
// host_format_double and std::printf already are.
inline void host_print_sequence(const void* base, const ContainerSequenceDesc* d) {
    const unsigned char* p = static_cast<const unsigned char*>(base);
    // Phase 6.7. A list renders its LIVE length; a tuple (no length field)
    // renders its whole declared run.
    int64_t count = d->count;
    if (d->len_offset != kNoLenOffset) {
        int64_t live = 0;
        std::memcpy(&live, p + d->len_offset, 8);
        if (live < 0) live = 0;
        if (live < count) count = live;
    }
    std::vector<std::string> elems;
    elems.reserve(static_cast<size_t>(count));
    for (int64_t i = 0; i < count; ++i) {
        std::string s;
        append_leaf_bytes(s, d->code, d->width, p + i * d->stride);
        elems.push_back(std::move(s));
    }
    const std::string out = format_sequence(elems, d->is_tuple != 0);
    std::fputs(out.c_str(), stdout);
}

inline void host_print_mapping(const void* base, const ContainerMappingDesc* d) {
    const unsigned char* b = static_cast<const unsigned char*>(base);
    const unsigned char* keys = b + d->keys_offset;
    const unsigned char* occ = b + d->occupied_offset;
    const unsigned char* vals = b + d->values_offset;
    std::vector<std::string> ks, vs;
    for (int64_t i = 0; i < d->buckets; ++i) {
        int64_t marker = 0;
        std::memcpy(&marker, occ + i * 8, 8);
        if (marker == 0) continue;
        std::string k, v;
        // 4.3. A str key renders from its stored bytes as the Python repr -- the
        // same format_string_repr the interpreter uses, so the two agree on the
        // quoting without a second definition of it.
        if (d->key_is_str) {
            k = format_string_repr(keys + i * d->key_stride, d->key_stride);
        } else {
            append_leaf_bytes(k, d->key_code, d->key_width, keys + i * d->key_stride);
        }
        // 6.9. A container value walks its own bucket's slice of the value
        // region through the recursive node the frontend's type describes. The
        // walk starts at the slice, so `render_flat_node` sees one value and not
        // the whole table, and the interpreter slices the same way.
        if (d->value_is_flat && d->value_flat != nullptr) {
            render_flat_node(v, d->value_flat, vals + i * d->value_stride);
        } else {
            append_leaf_bytes(v, d->value_code, d->value_width, vals + i * d->value_stride);
        }
        ks.push_back(std::move(k));
        vs.push_back(std::move(v));
    }
    const std::string out = format_mapping(ks, vs);
    std::fputs(out.c_str(), stdout);
}

// 6.2. Bounded-string entry points. Unlike the sequence/mapping printers these
// take (base, descriptor), because the emitted call convention only marshals two
// integer arguments in registers; for strlen and streq the descriptor carries
// the capacity/bases that a third argument could not.

// Print a string: the bytes before the NUL, as text. A string is the one
// aggregate that is NOT bracketed, so it has its own descriptor rather than a
// FlatPrintNode leaf.
struct ContainerStringDesc {
    int64_t cap;
};

inline void host_print_string(const void* base, const ContainerStringDesc* d) {
    const std::string out =
        format_string(static_cast<const unsigned char*>(base), d->cap);
    std::fputs(out.c_str(), stdout);
}

// len(s): the number of bytes before the first NUL, capped at the capacity.
struct ContainerStrLenDesc {
    int64_t cap;
};

inline int64_t host_strlen(const void* base, const ContainerStrLenDesc* d) {
    const unsigned char* p = static_cast<const unsigned char*>(base);
    int64_t n = 0;
    while (n < d->cap && p[n] != 0) ++n;
    return n;
}

// s == t: byte-for-byte up to the NUL. The two strings may live at different
// frame offsets and have different capacities, so the descriptor carries both
// (bases relative to the single frame-base pointer the call passes).
struct ContainerStrEqDesc {
    int64_t base1;
    int64_t cap1;
    int64_t base2;
    int64_t cap2;
};

inline int64_t host_streq(const void* frame_base, const ContainerStrEqDesc* d) {
    const unsigned char* f = static_cast<const unsigned char*>(frame_base);
    const unsigned char* a = f + d->base1;
    const unsigned char* b = f + d->base2;
    int64_t la = 0, lb = 0;
    while (la < d->cap1 && a[la] != 0) ++la;
    while (lb < d->cap2 && b[lb] != 0) ++lb;
    if (la != lb) return 0;
    return std::memcmp(a, b, static_cast<size_t>(la)) == 0 ? 1 : 0;
}

// 4.3. The str-key probe, in ONE place for both tiers.
//
// An int key hashes a word and compares a word, so the interpreter can walk
// buckets in C++ while the JIT emits the same walk in machine code and both are
// derived from dict::bucket_of. A str key does neither: the hash is a byte hash
// and the comparison is byte-wise, and writing that walk twice -- once as a C++
// loop and once as emitted machine code -- would be two definitions of which
// bucket a string belongs in, disagreeing only when a lookup missed.
//
// So the JIT hands its layout to a host helper and runs this function, and the
// interpreter calls it directly over its own storage. `keys` is the byte region,
// `key_stride` the bytes per slot, `occupied` one 8-byte marker per bucket --
// the exact shapes both tiers already store.
//
// Returns the bucket the key belongs to, or -1 when it is not in the table.
// With `insert` set, a bucket that needs writing receives the key first (NUL
// padded to the full stride) and is returned whether or not it already held the
// key, which is what makes a construction probe and a lookup probe agree.
inline int64_t dict_str_probe(unsigned char* keys, const uint64_t* occupied,
                              int64_t buckets, int64_t key_stride,
                              const unsigned char* search, int64_t search_cap,
                              bool insert, bool* found) {
    // 6.8. The key is the search run's TEXT, not its whole run. Reading bytes
    // past the NUL would make a padded slot and an unpadded one different keys
    // that compare equal, and the hash and the comparison would have to be told
    // about each other to avoid disagreeing about where that key lives.
    const int64_t len = string_content_len(search, search_cap);
    const int64_t start = dict::bucket_of_str(search, static_cast<int>(len),
                                              static_cast<int>(buckets));
    const auto write_key = [&](int64_t b) {
        unsigned char* slot = keys + b * key_stride;
        std::memset(slot, 0, static_cast<size_t>(key_stride));
        const int64_t n = len < key_stride ? len : key_stride;
        std::memcpy(slot, search, static_cast<size_t>(n));
    };
    int64_t b = start;
    for (int64_t step = 0; step < buckets; ++step) {
        if (occupied[b] == 0) {
            *found = false;
            if (insert) {
                write_key(b);
                return b;
            }
            return -1;
        }
        if (dict::str_content_eq(keys + b * key_stride, static_cast<int>(key_stride),
                                 search, static_cast<int>(len))) {
            *found = true;
            if (insert) write_key(b);
            return b;
        }
        b = (b + 1) & (buckets - 1);
    }
    // Exhausted. A lookup missed; an insert falls back to the starting bucket,
    // exactly as the interpreter's probe does, so a full table answers the same
    // way in both tiers instead of one writing off the end.
    *found = false;
    if (insert) {
        write_key(start);
        return start;
    }
    return -1;
}

// Everything the probe needs that is known at compile time except the search
// key, which is a frame run named per operation.
struct DictStrDesc {
    int64_t buckets;
    int64_t keys_offset;
    int64_t occupied_offset;
    int64_t key_stride;
    int64_t search_base;   // frame offset of the key run
    int64_t search_cap;
};

// A read. Returns the bucket the key occupies, or -1 when it is absent.
inline int64_t host_dict_str_find(void* frame_base, const DictStrDesc* d) {
    unsigned char* f = static_cast<unsigned char*>(frame_base);
    const unsigned char* search = f + d->search_base;
    const uint64_t* occupied =
        reinterpret_cast<const uint64_t*>(f + d->occupied_offset);
    bool found = false;
    const int64_t b = dict_str_probe(f + d->keys_offset, occupied, d->buckets,
                                     d->key_stride, search, d->search_cap,
                                     /*insert=*/false, &found);
    return found ? b : -1;
}

// A construction store. Writes the key into the bucket it returns.
inline int64_t host_dict_str_insert(void* frame_base, const DictStrDesc* d) {
    unsigned char* f = static_cast<unsigned char*>(frame_base);
    const unsigned char* search = f + d->search_base;
    const uint64_t* occupied =
        reinterpret_cast<const uint64_t*>(f + d->occupied_offset);
    bool found = false;
    return dict_str_probe(f + d->keys_offset, occupied, d->buckets, d->key_stride,
                          search, d->search_cap, /*insert=*/true, &found);
}

// 6.4. s.find(sub) / s.count(sub): the haystack and needle are two frame
// offsets with their own capacities, exactly like streq. Both helpers first
// scan each run for its NUL-terminated length, then do the substring walk.
//
// The empty needle is the Python edge case, and it is why both helpers special
// -case it rather than falling into the loop: `h.find("")` is 0 (the empty
// string occurs at index 0) and `h.count("")` is len(h) + 1 (it occurs at every
// boundary, including just past the end). Both must agree with CPython exactly.
struct ContainerStrFindDesc {
    int64_t base1;
    int64_t cap1;
    int64_t base2;
    int64_t cap2;
};

inline int64_t host_strfind(const void* frame_base, const ContainerStrFindDesc* d) {
    const unsigned char* f = static_cast<const unsigned char*>(frame_base);
    const unsigned char* h = f + d->base1;
    const unsigned char* n = f + d->base2;
    int64_t hl = 0, nl = 0;
    while (hl < d->cap1 && h[hl] != 0) ++hl;
    while (nl < d->cap2 && n[nl] != 0) ++nl;
    if (nl == 0) return 0;
    for (int64_t i = 0; i + nl <= hl; ++i) {
        if (std::memcmp(h + i, n, static_cast<size_t>(nl)) == 0) return i;
    }
    return -1;
}

inline int64_t host_strcount(const void* frame_base, const ContainerStrFindDesc* d) {
    const unsigned char* f = static_cast<const unsigned char*>(frame_base);
    const unsigned char* h = f + d->base1;
    const unsigned char* n = f + d->base2;
    int64_t hl = 0, nl = 0;
    while (hl < d->cap1 && h[hl] != 0) ++hl;
    while (nl < d->cap2 && n[nl] != 0) ++nl;
    if (nl == 0) return hl + 1;
    int64_t count = 0;
    for (int64_t i = 0; i + nl <= hl; ) {
        if (std::memcmp(h + i, n, static_cast<size_t>(nl)) == 0) {
            ++count;
            i += nl;          // non-overlapping, as in Python
        } else {
            ++i;
        }
    }
    return count;
}

// 6.7. s.split(sep): split the source run into pieces, write each piece (NUL
// terminated, truncated to the piece capacity) into the destination list of
// bounded strings, and return the number of pieces. All three runs are
// RBP-relative, so one frame base covers them -- the same shape as the str find
// helpers above.
//
// The destination is `list[str[K], M]`: M slots of K bytes, laid out flat. The
// helper writes at most M pieces and returns the TRUE count; the emitted code
// traps when that exceeds M, which is what the interpreter's throw does too.
// A piece longer than K-1 bytes is truncated (the language's bounded string has
// no room for more), and an empty separator yields one piece, the whole source
// (the frontend refuses a literal empty separator; Python raises).
struct StrSplitDesc {
    int64_t src_offset;
    int64_t src_cap;
    int64_t sep_offset;
    int64_t sep_cap;
    int64_t dest_offset;   // first piece = first leaf of the destination run
    int64_t piece_cap;     // K bytes per piece
    int64_t max_pieces;    // M pieces
};

inline int64_t host_strsplit(const void* frame_base, const StrSplitDesc* d) {
    const unsigned char* f = static_cast<const unsigned char*>(frame_base);
    auto read_run = [&](int64_t off, int64_t cap) {
        std::string s;
        const unsigned char* q = f + off;
        for (int64_t i = 0; i < cap; ++i) {
            if (q[i] == 0) break;
            s.push_back(static_cast<char>(q[i]));
        }
        return s;
    };
    const std::string src = read_run(d->src_offset, d->src_cap);
    const std::string sep = read_run(d->sep_offset, d->sep_cap);

    std::vector<std::string> pieces;
    if (sep.empty()) {
        pieces.push_back(src);
    } else {
        size_t start = 0, pos = 0;
        while (pos + sep.size() <= src.size()) {
            if (src.compare(pos, sep.size(), sep) == 0) {
                pieces.push_back(src.substr(start, pos - start));
                pos += sep.size();
                start = pos;
            } else {
                ++pos;
            }
        }
        pieces.push_back(src.substr(start));
    }

    unsigned char* dst = const_cast<unsigned char*>(f + d->dest_offset);
    const int64_t K = d->piece_cap;
    const int64_t M = d->max_pieces;
    for (int64_t k = 0; k < M; ++k) {
        unsigned char* slot = dst + k * K;
        const bool has = k < static_cast<int64_t>(pieces.size());
        for (int64_t j = 0; j < K; ++j) {
            unsigned char c = 0;
            // The last byte stays NUL: a str[K] holds at most K-1 content bytes,
            // so a longer piece is truncated rather than run together with the
            // next slot.
            if (has && j < K - 1 &&
                j < static_cast<int64_t>(pieces[static_cast<size_t>(k)].size()))
                c = static_cast<unsigned char>(pieces[static_cast<size_t>(k)][static_cast<size_t>(j)]);
            slot[j] = c;
        }
    }
    return static_cast<int64_t>(pieces.size());
}

}   // namespace lithon::jit
