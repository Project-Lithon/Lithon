#pragma once

// 4.10.1. The class runtime: the per-run table of ClassDescriptors, and the
// host helpers both tiers call for the class ops that are NOT a plain fixed
// offset -- allocation (New) and the reference-count traffic (Retain/Release).
//
// Attribute READ and WRITE (RefLoad/RefStore) are deliberately NOT here: they
// are a bare `[ref + disp]` in both tiers (the offset is absolute, computed by
// the frontend), so each tier emits its own load or store exactly as the 4.6
// field ops do. Routing them through a helper would add a call to the hot path
// for no gain in agreement, since the agreement already comes from the shared
// descriptor and the shared offset.
//
// What DOES have to be shared, and is therefore here:
//
//   * The DESCRIPTOR TABLE. New needs the instance size to allocate; Release
//     needs the instance size to return the block to the right size class; a
//     later __del__ needs the destructor slot. Building the table once, from
//     Module.classes, means the interpreter and the JIT free an object to the
//     SAME size class and run __del__ through the SAME pointer -- which is what
//     makes the two tiers' observable lifetime behaviour identical.
//
//   * The ALLOCATION and DROP helpers. Both tiers call the very same
//     heap_alloc/release_drop, so `when does __del__ run` cannot differ.
//
// Descriptors live for the life of the process (like register_print_desc's
// pool): emitted code carries a descriptor address and may run long after
// compilation, so the storage must outlive the compiler.

#include <algorithm>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#include "runtime/heap.h"
#include "runtime/object.h"

namespace lithon::runtime {

// The per-run table. Rebuilt from the module at each entry point (beside
// reset_heap), so consecutive runs in one process -- the tier drivers run the
// same process for several modes -- never see a stale descriptor.
struct ClassTable {
    std::vector<ClassDescriptor*> descriptors;   // index == class index in the IR
    std::vector<std::string> names;              // index -> class name
    // 4.10.2. slot_names[index][slot] is the method name at that slot for that
    // class (the class's chain names it; a subclass inherits or overrides). The
    // interpreter uses it to dispatch a virtual call to the emitted method
    // without a per-slot trampoline; the JIT ignores it and calls d->slots[slot].
    std::vector<std::vector<std::string>> slot_names;
    // 4.10.4. slot_owners[index][slot] is the class that DECLARES the method at
    // that global slot. The interpreter keys its `__virt__<Owner>.<method>`
    // lookup on it; a subclass with an inherited slot has no function of its
    // own to find.
    std::vector<std::vector<std::string>> slot_owners;
    // 4.10.4. Stable storage for descriptors' `destructor_owner` strings. Filled
    // before any descriptor points into it, so c_str() addresses stay valid.
    std::vector<std::string> destructor_names;

    // Find a class by name; -1 when there is none. Linear over a handful of
    // classes, which is fine: this runs once per New-type op at COMPILE time,
    // not per execution.
    int index_of(const std::string& name) const {
        for (size_t i = 0; i < names.size(); ++i)
            if (names[i] == name) return static_cast<int>(i);
        return -1;
    }
};

inline ClassTable& class_table() {
    static ClassTable t;
    return t;
}

// A block big enough for a descriptor with `slots` vtable entries. The header
// declares `slots[1]`, so the tail beyond one entry is over-allocated here.
inline ClassDescriptor* alloc_descriptor(uint32_t slots) {
    const size_t bytes = sizeof(ClassDescriptor) +
                         (slots > 1 ? (slots - 1) * sizeof(void (*)(void)) : 0);
    void* mem = std::malloc(bytes);
    std::memset(mem, 0, bytes);
    return static_cast<ClassDescriptor*>(mem);
}

// Descriptors are owned by a process-lifetime pool so their addresses are
// stable and they are never freed mid-run. Rebuilding the table clears the
// pool first: a fresh entry point means fresh descriptors.
inline std::vector<std::unique_ptr<ClassDescriptor, void (*)(ClassDescriptor*)>>&
descriptor_pool() {
    static std::vector<std::unique_ptr<ClassDescriptor, void (*)(ClassDescriptor*)>> pool;
    return pool;
}

// The per-descriptor arrays of class-typed attribute offsets. Kept in their own
// pool (rather than inline past the vtable) so a descriptor's layout does not
// change with the number of references it holds, and so the pointer stays valid
// for the life of the run alongside the descriptor it belongs to.
inline std::vector<std::vector<uint32_t>>& ref_attr_pool() {
    static std::vector<std::vector<uint32_t>> pool;
    return pool;
}

// 4.10.4. Per-descriptor ownership arrays. Like ref_attr_pool, each inner
// buffer is stable for the life of the run even when the outer vector grows.
inline std::vector<std::vector<uint64_t>>& ancestor_bits_pool() {
    static std::vector<std::vector<uint64_t>> pool;
    return pool;
}

inline std::vector<std::vector<uint64_t>>& own_slot_bits_pool() {
    static std::vector<std::vector<uint64_t>> pool;
    return pool;
}

inline std::vector<std::vector<const ClassDescriptor*>>& mro_pool() {
    static std::vector<std::vector<const ClassDescriptor*>> pool;
    return pool;
}

// Build one descriptor per ClassInfo, in IR order (so index == class index).
//
// 4.10.2. type ids are assigned PRE-ORDER and subtree-contiguous: walking the
// classes in IR order and recursing into each class's children assigns a class
// and its whole inheritance subtree consecutive ids. That is the invariant
// `is_instance_of` rests on -- `C.subtree_lo <= x.type_id <= C.subtree_hi` is
// true for exactly C and its subclasses, with no walk. (IR order already emits
// a base before its subclasses, so a single pass that assigns each root and
// then its descendants in order is exactly pre-order.)
//
// `resolve_destructor` maps a class name to the address of its emitted
// `__del__<Class>` function (or nullptr when there is none / it cannot be
// resolved in this tier). `resolve_slot` maps a (class name, slot index) to the
// address of the method's emitted code, or nullptr for an inherited/abstract
// slot this tier does not implement. Both tiers use the SAME convention
// (`__del__` + class name, `<Class>.<method>` for a vtable slot), which is what
// keeps the observable behaviour identical.
template <typename ClassInfoRange, typename DestructorResolver, typename SlotResolver>
inline void build_class_table(const ClassInfoRange& classes,
                              DestructorResolver resolve_destructor,
                              SlotResolver resolve_slot) {
    ClassTable& t = class_table();
    t.descriptors.clear();
    t.names.clear();
    t.slot_names.clear();
    t.slot_owners.clear();
    t.destructor_names.clear();
    auto& pool = descriptor_pool();
    pool.clear();
    auto& ref_pool = ref_attr_pool();
    ref_pool.clear();
    auto& anc_pool = ancestor_bits_pool();
    anc_pool.clear();
    auto& own_pool = own_slot_bits_pool();
    own_pool.clear();
    auto& mro_pool_ref = mro_pool();
    mro_pool_ref.clear();

    const size_t n = static_cast<size_t>(classes.size());
    std::vector<ClassDescriptor*> descs(n, nullptr);
    std::vector<std::vector<const ClassDescriptor*>> mros(n);
    t.names.reserve(n);
    t.slot_names.reserve(n);
    t.slot_owners.reserve(n);
    t.destructor_names.reserve(n);
    ref_pool.reserve(n);
    anc_pool.reserve(n);
    own_pool.reserve(n);
    mro_pool_ref.reserve(n);

    // One descriptor per class first, so an MRO can name descriptors before the
    // second pass fills their vtable.
    for (size_t i = 0; i < n; ++i) {
        const auto& ci = classes[i];
        ClassDescriptor* d = alloc_descriptor(ci.slot_count > 0 ? ci.slot_count : 1);
        pool.emplace_back(d, [](ClassDescriptor* p) { std::free(p); });
        d->type_id = static_cast<uint32_t>(i + 1);   // 0 is reserved (null vptr)
        d->subtree_lo = d->type_id;
        d->subtree_hi = d->type_id;
        d->slot_count = static_cast<uint32_t>(ci.slot_count);
        d->instance_size = static_cast<uint32_t>(ci.instance_size);
        d->flags = ci.abstract ? kClassFlagAbstract : 0u;
        d->table_index = static_cast<uint32_t>(i);
        d->name = nullptr;   // filled below from the table's stable storage
        d->destructor = nullptr;
        d->destructor_owner = nullptr;
        d->ancestor_word_count = 0;
        d->ancestor_bits = nullptr;
        d->own_slot_word_count = 0;
        d->own_slot_bits = nullptr;
        d->mro_count = 0;
        d->mro = nullptr;
        descs[i] = d;
        t.descriptors.push_back(d);
        t.names.push_back(ci.name);
    }

    // MRO in IR indices. A class emitted by 4.10.4 always carries `mro=`; the
    // legacy base_index walk keeps hand-built ClassInfos (unit tests and old
    // hand-written IR) meaningful.
    for (size_t i = 0; i < n; ++i) {
        const auto& ci = classes[i];
        std::vector<int> mro;
        if (!ci.mro.empty()) {
            mro = ci.mro;
        } else {
            int cur = static_cast<int>(i);
            size_t guard = 0;
            while (cur >= 0 && static_cast<size_t>(cur) < n && guard++ <= n) {
                mro.push_back(cur);
                cur = classes[static_cast<size_t>(cur)].base_index;
            }
        }
        if (mro.empty() || mro[0] != static_cast<int>(i)) mro.insert(mro.begin(), static_cast<int>(i));
        for (int idx : mro) {
            if (idx < 0 || static_cast<size_t>(idx) >= n)
                throw std::runtime_error("class table: MRO index out of range");
            mros[i].push_back(descs[static_cast<size_t>(idx)]);
        }
    }

    // Ancestor bitsets: each class sets its own bit and every MRO member's bit.
    const size_t words = (n + 1 + 63) / 64;   // type ids are 1..n
    for (size_t i = 0; i < n; ++i) {
        anc_pool.emplace_back(words, 0);
        std::vector<uint64_t>& bits = anc_pool.back();
        for (const ClassDescriptor* a : mros[i]) {
            const uint32_t id = a->type_id;
            bits[id >> 6] |= (uint64_t{1} << (id & 63));
        }
        descs[i]->ancestor_bits = bits.data();
        descs[i]->ancestor_word_count = static_cast<uint32_t>(words);
    }

    // Own-slot bitsets and the descriptor's MRO view.
    for (size_t i = 0; i < n; ++i) {
        const auto& ci = classes[i];
        const size_t own_words = (static_cast<size_t>(std::max(ci.slot_count, 0)) + 63) / 64;
        own_pool.emplace_back(std::max<size_t>(own_words, 1), 0);
        std::vector<uint64_t>& own = own_pool.back();
        for (int sidx : ci.own_slots) {
            if (sidx < 0 || sidx >= ci.slot_count) continue;
            own[static_cast<size_t>(sidx) >> 6] |=
                (uint64_t{1} << (static_cast<size_t>(sidx) & 63));
        }
        descs[i]->own_slot_bits = own.data();
        descs[i]->own_slot_word_count = static_cast<uint32_t>(own.size());
        mro_pool_ref.push_back(mros[i]);
        descs[i]->mro = mro_pool_ref.back().data();
        descs[i]->mro_count = static_cast<uint32_t>(mro_pool_ref.back().size());
    }

    // Vtables, destructor ownership, and reference attributes.
    for (size_t i = 0; i < n; ++i) {
        const auto& ci = classes[i];
        ClassDescriptor* d = descs[i];

        int dtor_idx = ci.destructor_owner;
        if (!ci.has_destructor) dtor_idx = -1;
        else if (dtor_idx < 0 || static_cast<size_t>(dtor_idx) >= n) dtor_idx = static_cast<int>(i);
        if (dtor_idx >= 0) {
            const std::string& owner = t.names[static_cast<size_t>(dtor_idx)];
            t.destructor_names.push_back(owner);
            d->destructor = resolve_destructor(owner);
        }

        // The class-typed attributes a Release must drop.
        ref_pool.emplace_back();
        std::vector<uint32_t>& refs = ref_pool.back();
        for (const auto& a : ci.attributes) {
            if (a.type_kind == "class")
                refs.push_back(static_cast<uint32_t>(a.offset));
        }
        d->ref_attr_count = static_cast<uint32_t>(refs.size());
        d->ref_attr_offsets = refs.empty() ? nullptr : refs.data();

        std::vector<std::string> owners = ci.slot_owners;
        owners.resize(static_cast<size_t>(std::max(ci.slot_count, 0)));
        t.slot_names.push_back(ci.slots);
        t.slot_owners.push_back(owners);
        for (int s = 0; s < ci.slot_count; ++s) {
            const size_t si = static_cast<size_t>(s);
            const char* slot_name = (si < ci.slots.size() && !ci.slots[si].empty())
                                        ? ci.slots[si].c_str()
                                        : nullptr;
            const std::string& owner =
                (si < owners.size() && !owners[si].empty()) ? owners[si] : ci.name;
            d->slots[si] = slot_name != nullptr
                               ? resolve_slot(owner, s, slot_name)
                               : nullptr;
        }
    }

    // Fill stable name / destructor-owner pointers only after every string has
    // been pushed, so vector growth above cannot invalidate an earlier c_str().
    for (size_t i = 0; i < n; ++i) {
        t.descriptors[i]->name = t.names[i].c_str();
        if (t.descriptors[i]->destructor != nullptr) {
            // destructor_names was filled in class order for classes that have
            // a destructor; point at the same stable string the resolver got.
            int dtor_idx = classes[i].destructor_owner;
            if (dtor_idx < 0 || static_cast<size_t>(dtor_idx) >= n)
                dtor_idx = static_cast<int>(i);
            t.descriptors[i]->destructor_owner =
                t.names[static_cast<size_t>(dtor_idx)].c_str();
        }
    }
}

// ---------------------------------------------------------------------------
// Host helpers (called by address from emitted code, and directly by the
// interpreter). They take plain integers so a JIT can pass values in registers
// without a struct layout the two tiers must agree on.
// ---------------------------------------------------------------------------

// New: allocate a zeroed instance of class `index`, stamp its vptr, and give it
// refcount 1 (the caller's reference). Returns the object pointer.
inline ObjectHeader* host_class_new(int64_t index) {
    ClassTable& t = class_table();
    if (index < 0 || static_cast<size_t>(index) >= t.descriptors.size())
        return nullptr;
    ClassDescriptor* d = t.descriptors[static_cast<size_t>(index)];
    ObjectHeader* obj = heap_alloc(d->instance_size);
    if (obj == nullptr) return nullptr;
    obj->vptr = d;
    obj->refcount = 1;
    return obj;
}

// An alias (`b = a`) takes a second reference. Immortal/null are no-ops.
inline ObjectHeader* host_class_retain(ObjectHeader* obj) {
    incref(obj);
    return obj;
}

// Drop one reference. Runs __del__ at the last drop when the class has one
// (rule 2); the size comes from the object's own descriptor. At the LAST drop it
// also releases one reference to every object this instance holds in a
// class-typed attribute, so a nested object's own __del__ runs and its block is
// freed -- without this, `h.leaf = Leaf(...)` would leak the Leaf forever and
// its destructor would never be observable.
inline void host_class_release(ObjectHeader* obj) {
    if (obj == nullptr || is_immortal(obj)) return;
    if (obj->refcount == 0) return;  // guard against a double drop
    if (--obj->refcount != 0) return;
    ClassDescriptor* d = obj->vptr;
    if (d != nullptr && d->destructor != nullptr) {
        d->destructor(obj);
        ++g_heap.destructor_runs;
    }
    // Release the nested references AFTER __del__ (the instance is still fully
    // formed for __del__, exactly as CPython's tp_dealloc runs __del__ before it
    // clears the instance), and BEFORE the block is freed.
    if (d != nullptr && d->ref_attr_count != 0) {
        for (uint32_t i = 0; i < d->ref_attr_count; ++i) {
            ObjectHeader* child = nullptr;
            std::memcpy(&child,
                        reinterpret_cast<uint8_t*>(obj) + d->ref_attr_offsets[i],
                        sizeof(child));
            if (child != nullptr) host_class_release(child);
        }
    }
    heap_free(obj, d != nullptr ? d->instance_size : sizeof(ObjectHeader));
}

// 4.10.2/4.10.4. IsInstance: the object's descriptor carries C's type-id bit
// exactly when the object is a C or a subclass. One bitset probe, no walk. The
// class is named by its table index; an out-of-range index is false rather
// than a crash.
inline bool host_class_is_instance(ObjectHeader* obj, int64_t index) {
    ClassTable& t = class_table();
    if (index < 0 || static_cast<size_t>(index) >= t.descriptors.size()) return false;
    return is_instance_of(obj, t.descriptors[static_cast<size_t>(index)]);
}

// 4.10.2. CallVirtual: read slot `slot` out of the receiver's OWN descriptor --
// so a subclass that overrides the method runs its override -- and call it with
// the receiver as the sole carried argument. Args beyond the receiver are
// passed through by the tier, which knows the arity; this helper is only for
// the single-argument case the tiers may use as a fallback.
inline void* host_class_slot(ObjectHeader* obj, int64_t slot) {
    if (obj == nullptr || obj->vptr == nullptr) return nullptr;
    if (slot < 0 || static_cast<uint32_t>(slot) >= obj->vptr->slot_count) return nullptr;
    return reinterpret_cast<void*>(obj->vptr->slots[static_cast<size_t>(slot)]);
}

// 4.10.4. Dynamic super(). `encoded` packs (defining class index << 32) | slot
// because the JIT ABI has only two GP argument registers. The receiver's own
// MRO decides what follows the defining class; the first later class whose
// own-slot bit is set supplies the implementation. Abstract stubs are not in
// own_slots, so they are skipped exactly as they are in the vtable.
inline int64_t host_class_super_owner(ObjectHeader* obj, int64_t encoded) {
    if (obj == nullptr || obj->vptr == nullptr) return -1;
    ClassTable& t = class_table();
    const uint32_t slot = static_cast<uint32_t>(encoded & 0xffffffffLL);
    const uint32_t defining = static_cast<uint32_t>(
        (static_cast<uint64_t>(encoded) >> 32) & 0xffffffffULL);
    if (defining >= t.descriptors.size()) return -1;
    const ClassDescriptor* start = t.descriptors[defining];
    const ClassDescriptor* actual = obj->vptr;
    size_t pos = actual->mro_count;
    for (size_t i = 0; i < actual->mro_count; ++i) {
        if (actual->mro[i] == start) { pos = i; break; }
    }
    if (pos >= actual->mro_count) return -1;
    for (size_t i = pos + 1; i < actual->mro_count; ++i) {
        const ClassDescriptor* cand = actual->mro[i];
        if (slot >= cand->slot_count || cand->own_slot_bits == nullptr) continue;
        const uint32_t word = slot >> 6;
        if (word >= cand->own_slot_word_count) continue;
        if (((cand->own_slot_bits[word] >> (slot & 63)) & 1u) != 0)
            return static_cast<int64_t>(cand->table_index);
    }
    return -1;
}

inline void* host_class_super_slot(ObjectHeader* obj, int64_t encoded) {
    const int64_t owner = host_class_super_owner(obj, encoded);
    if (owner < 0) return nullptr;
    ClassTable& t = class_table();
    if (static_cast<size_t>(owner) >= t.descriptors.size()) return nullptr;
    const uint32_t slot = static_cast<uint32_t>(encoded & 0xffffffffLL);
    const ClassDescriptor* d = t.descriptors[static_cast<size_t>(owner)];
    if (slot >= d->slot_count) return nullptr;
    return reinterpret_cast<void*>(d->slots[static_cast<size_t>(slot)]);
}

// Attribute read/write, generic over the attribute's kind. The JIT inlines the
// common integer case; these exist for the interpreter and as the reference
// semantics the JIT's inline loads/stores must match. The offset is absolute
// from the object start (it already includes the 16-byte header).
inline uint64_t class_attr_load_u64(const ObjectHeader* obj, int64_t offset,
                                    int64_t bytes, bool is_signed) {
    const uint8_t* p = reinterpret_cast<const uint8_t*>(obj) + offset;
    uint64_t v = 0;
    switch (bytes) {
        case 1: v = *p; break;
        case 2: { uint16_t x; std::memcpy(&x, p, 2); v = x; break; }
        case 4: { uint32_t x; std::memcpy(&x, p, 4); v = x; break; }
        case 8: { uint64_t x; std::memcpy(&x, p, 8); v = x; break; }
        default: break;
    }
    if (is_signed) {
        // Sign-extend from the attribute's width, exactly as a narrow int load.
        switch (bytes) {
            case 1: v = static_cast<uint64_t>(static_cast<int64_t>(static_cast<int8_t>(v))); break;
            case 2: v = static_cast<uint64_t>(static_cast<int64_t>(static_cast<int16_t>(v))); break;
            case 4: v = static_cast<uint64_t>(static_cast<int64_t>(static_cast<int32_t>(v))); break;
            default: break;
        }
    }
    return v;
}

inline void class_attr_store_u64(ObjectHeader* obj, int64_t offset, int64_t bytes,
                                 uint64_t value) {
    uint8_t* p = reinterpret_cast<uint8_t*>(obj) + offset;
    switch (bytes) {
        case 1: *p = static_cast<uint8_t>(value); break;
        case 2: { uint16_t x = static_cast<uint16_t>(value); std::memcpy(p, &x, 2); break; }
        case 4: { uint32_t x = static_cast<uint32_t>(value); std::memcpy(p, &x, 4); break; }
        case 8: { std::memcpy(p, &value, 8); break; }
        default: break;
    }
}

inline double class_attr_load_f64(const ObjectHeader* obj, int64_t offset) {
    double v;
    std::memcpy(&v, reinterpret_cast<const uint8_t*>(obj) + offset, 8);
    return v;
}

inline void class_attr_store_f64(ObjectHeader* obj, int64_t offset, double value) {
    std::memcpy(reinterpret_cast<uint8_t*>(obj) + offset, &value, 8);
}

}  // namespace lithon::runtime
