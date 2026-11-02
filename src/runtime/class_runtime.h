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
    auto& pool = descriptor_pool();
    pool.clear();
    auto& ref_pool = ref_attr_pool();
    ref_pool.clear();

    // Index each class by name so a base_index (already an index into the IR
    // list) is used directly and a self/inherited attribute walk can name the
    // base. IR order guarantees a base precedes its subclass, which is all the
    // pre-order id assignment below relies on.
    std::vector<ClassDescriptor*> descs(classes.size(), nullptr);

    // Children lists, so a root can pull in its whole subtree before the next
    // root starts -- this is what makes each subtree's ids contiguous.
    std::vector<std::vector<size_t>> children(classes.size());
    std::vector<size_t> roots;
    for (size_t i = 0; i < classes.size(); ++i) {
        const int b = classes[i].base_index;
        if (b >= 0 && static_cast<size_t>(b) < classes.size())
            children[static_cast<size_t>(b)].push_back(i);
        else
            roots.push_back(i);
    }

    // Allocate a descriptor for every class FIRST (so the pre-order walk can
    // reference a child's descriptor while setting up its parent's subtree_hi),
    // then assign ids in pre-order.
    for (size_t i = 0; i < classes.size(); ++i) {
        const auto& ci = classes[i];
        ClassDescriptor* d = alloc_descriptor(ci.slot_count > 0 ? ci.slot_count : 1);
        pool.emplace_back(d, [](ClassDescriptor* p) { std::free(p); });
        d->type_id = 0;
        d->subtree_lo = 0;
        d->subtree_hi = 0;
        d->slot_count = static_cast<uint32_t>(ci.slot_count);
        d->instance_size = static_cast<uint32_t>(ci.instance_size);
        d->flags = ci.abstract ? kClassFlagAbstract : 0u;
        d->name = nullptr;   // filled below from the table's own stable storage
        d->destructor = ci.has_destructor ? resolve_destructor(ci.name) : nullptr;
        // The class-typed attributes, so a Release can drop the references this
        // instance holds before its own block goes back to the heap. A
        // self-reference (`nxt: Node` inside Node) is included too: a cycle must
        // still drop each link, and the object's own type is known at New.
        ref_pool.emplace_back();
        std::vector<uint32_t>& refs = ref_pool.back();
        for (const auto& a : ci.attributes) {
            if (a.type_kind == "class")
                refs.push_back(static_cast<uint32_t>(a.offset));
        }
        d->ref_attr_count = static_cast<uint32_t>(refs.size());
        d->ref_attr_offsets = refs.empty() ? nullptr : refs.data();
        // The vtable: slot k holds the method the class's chain names at slot k,
        // resolved by the tier. An inherited slot resolves through the declaring
        // class's name, which is exactly the code a virtual call must run.
        for (int s = 0; s < ci.slot_count; ++s) {
            // The slot NAME (the method at slot s) is what the resolver keys the
            // emitted `__virt__<Class>.<name>` function on; `ci.name` is the
            // class. A chain that names the slot always has a matching entry in
            // ci.slots, so a missing name is a malformed table, not a normal case.
            const char* slot_name = (static_cast<size_t>(s) < ci.slots.size())
                                        ? ci.slots[static_cast<size_t>(s)].c_str()
                                        : nullptr;
            void (*code)(void) = resolve_slot(ci.name, s, slot_name);
            d->slots[s] = code;
        }
        descs[i] = d;
        t.descriptors.push_back(d);
        t.names.push_back(ci.name);
        t.slot_names.push_back(ci.slots);
    }

    // Pre-order id assignment. Each root starts a fresh contiguous run; a root's
    // whole subtree is numbered before the next root, so every subtree is a
    // contiguous id interval.
    uint32_t next_id = 1;   // 0 is reserved: a null vptr must not match any class
    // Iterative pre-order to avoid recursion depth concerns with a deep chain.
    std::vector<size_t> stack;
    for (auto it = roots.rbegin(); it != roots.rend(); ++it) stack.push_back(*it);
    while (!stack.empty()) {
        const size_t i = stack.back();
        stack.pop_back();
        descs[i]->type_id = next_id++;
        descs[i]->subtree_lo = descs[i]->type_id;
        // Push children in reverse so they are visited in IR order.
        for (auto cit = children[i].rbegin(); cit != children[i].rend(); ++cit)
            stack.push_back(*cit);
        // subtree_hi is set after the subtree is numbered: since ids are
        // contiguous and this class's id is the subtree's minimum, hi is the
        // largest id in it -- computed here as (next_id - 1) once the whole
        // subtree has been assigned. The loop below patches it.
    }
    // Patch each class's subtree_hi from its children's ranges: a leaf's hi is
    // its own id, else the max of its children's hi.
    for (size_t i = classes.size(); i-- > 0;) {
        uint32_t hi = descs[i]->type_id;
        for (size_t c : children[i]) hi = std::max(hi, descs[c]->subtree_hi);
        descs[i]->subtree_hi = hi;
    }

    // The names must outlive the descriptors: point each at the table's own
    // std::string storage, which is stable for the life of the ClassTable.
    for (size_t i = 0; i < t.descriptors.size(); ++i) {
        t.descriptors[i]->name = t.names[i].c_str();
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

// 4.10.2. IsInstance: the object's type id lies in the class's subtree range.
// The class is named by its table index; an out-of-range index is false rather
// than a crash. This is the ONE runtime question inheritance adds, and it is two
// integer compares.
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
