// 4.10.0 runtime foundation (and the 4.10.1 class-table lifetime) -- unit test.
//
// Pins the allocator, the object header layout, reference counting, and the
// ONE observable lifetime moment: the program point at which __del__ runs.
// Everything here is shared by both tiers (heap.h / object.h), so pinning it
// once pins it for the interpreter and the JIT alike; run_tier_diff.py then
// proves the two tiers agree byte-for-byte at the program level.
//
// The class-table section (4.10.1) also pins the reference-owning attribute
// walk: releasing a holder must drop the reference its class-typed attribute
// names, so a held object with its own last reference dies and runs __del__.
//
// Mirrors the style of stack_guard's and print_guard's tests: no framework, a
// small check() macro, exit code 0 on success.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "runtime/class_runtime.h"
#include "runtime/heap.h"
#include "runtime/object.h"
#include "ir/ir.h"

using namespace lithon::runtime;

namespace {

int g_failures = 0;

void check(bool cond, const char* what) {
    if (cond) {
        std::printf("  ok   %s\n", what);
    } else {
        std::printf("  FAIL %s\n", what);
        ++g_failures;
    }
}

// A descriptor with room for `nslots` vtable entries. Tests allocate these on
// the stack (immortal by construction -- the count is never a real one), which
// is exactly how class descriptors live in a real program: read-only, one per
// class, alive for the whole run.
struct DescBuf {
    uint32_t type_id;
    uint32_t subtree_lo;
    uint32_t subtree_hi;
    uint32_t slot_count;
    uint32_t instance_size;
    uint32_t flags;
    uint32_t ref_attr_count;
    const uint32_t* ref_attr_offsets;
    const char* name;
    void (*destructor)(void*);
    void (*slots[4])(void);
};

// Guard the mirror: DescBuf must stay field-for-field compatible with the real
// ClassDescriptor, or a test's descriptor would be read at the wrong offsets.
static_assert(offsetof(ClassDescriptor, ref_attr_count) ==
                  offsetof(DescBuf, ref_attr_count),
              "DescBuf must mirror ClassDescriptor (ref_attr_count)");
static_assert(offsetof(ClassDescriptor, ref_attr_offsets) ==
                  offsetof(DescBuf, ref_attr_offsets),
              "DescBuf must mirror ClassDescriptor (ref_attr_offsets)");
static_assert(offsetof(ClassDescriptor, name) == offsetof(DescBuf, name),
              "DescBuf must mirror ClassDescriptor (name)");
static_assert(offsetof(ClassDescriptor, destructor) == offsetof(DescBuf, destructor),
              "DescBuf must mirror ClassDescriptor (destructor)");

ClassDescriptor* make_desc(DescBuf& b) {
    return reinterpret_cast<ClassDescriptor*>(&b);
}

// The destructor log: the ORDER __del__ runs is the observable thing, so the
// test records the sequence, not just a count.
std::vector<std::string> g_log;

void del_a(void* self) {
    (void)self;
    g_log.push_back("A");
}
void del_b(void* self) {
    (void)self;
    g_log.push_back("B");
}

// The four implementations a one-slot hierarchy's vtable can name. They are
// never called -- the test only compares the pointers build_class_table stored
// -- so distinct bodies are all that is needed.
void animal_speak() {}
void dog_speak() {}
void puppy_speak() {}
void bird_speak() {}

// Build an instance of `desc`: allocate, stamp the vptr, set refcount 1 (the
// live handle the creator holds), exactly as a constructor does.
ObjectHeader* construct(ClassDescriptor* desc) {
    ObjectHeader* o = heap_alloc(desc->instance_size);
    o->vptr = desc;
    o->refcount = 1;
    return o;
}

void test_layout() {
    std::printf("layout\n");
    check(sizeof(ObjectHeader) == 16, "header is [vptr:8][refcount:8]");
    check(offsetof(ObjectHeader, vptr) == 0, "vptr is the first word");
    check(offsetof(ObjectHeader, refcount) == 8, "refcount is the second word");
    check(offsetof(ClassDescriptor, type_id) == 0, "type_id is the first descriptor word");
    check(kImmortalRefcount != 0, "immortal sentinel is distinct from a real count");
}

void test_alloc_free_reuse() {
    std::printf("allocator\n");
    reset_heap();
    DescBuf d = {};
    d.type_id = 1;
    d.subtree_lo = 1;
    d.subtree_hi = 1;
    d.instance_size = sizeof(ObjectHeader) + 8;
    d.name = "T";
    ClassDescriptor* desc = make_desc(d);

    ObjectHeader* o = heap_alloc(desc->instance_size);
    check(o != nullptr, "heap_alloc returns a block");
    check(g_heap.live_objects == 1, "a fresh block counts as one live object");
    heap_free(o, desc->instance_size);
    check(g_heap.live_objects == 0, "heap_free drops the live count");

    // Reuse: the next same-size allocation should hand back the freed block.
    ObjectHeader* o2 = heap_alloc(desc->instance_size);
    check(o2 == o, "a freed block is reused by the same size class");
    heap_free(o2, desc->instance_size);
}

void test_size_class_reservation() {
    std::printf("size classes\n");
    reset_heap();
    // A 24-byte request reserves a whole 32-byte class slot, so a later 32-byte
    // request may reuse a freed 24-byte block WITHOUT overflowing it. Guard the
    // second block with a canary at byte 31 and check it survives.
    ObjectHeader* a = heap_alloc(24);
    check(a != nullptr, "a 24-byte object allocates");
    heap_free(a, 24);

    ObjectHeader* b = heap_alloc(32);
    check(b == a, "a 32-byte request safely reuses a freed 24-byte block "
                  "(same size class, reservation rounded up)");
    uint8_t* bytes = reinterpret_cast<uint8_t*>(b);
    bytes[31] = 0xAB;  // the last byte of the reserved 32-byte class slot
    check(bytes[31] == 0xAB, "writing the full 32-byte class slot is in bounds");
    heap_free(b, 32);
}

void test_refcount_release_at_zero() {
    std::printf("refcounting\n");
    reset_heap();
    DescBuf d = {};
    d.type_id = 2;
    d.subtree_lo = 2;
    d.subtree_hi = 2;
    d.instance_size = sizeof(ObjectHeader) + 8;
    d.name = "R";
    ClassDescriptor* desc = make_desc(d);

    ObjectHeader* o = construct(desc);
    check(o->refcount == 1, "a fresh instance is born with one live reference");

    incref(o);
    check(o->refcount == 2, "a second name increments the count");

    // Dropping one reference must NOT free.
    release_drop(o, desc->instance_size);
    check(o->refcount == 1, "dropping one of two references leaves it alive");
    check(g_heap.live_objects == 1, "no free while a reference remains");

    // The last reference frees exactly once.
    release_drop(o, desc->instance_size);
    check(g_heap.live_objects == 0, "the last drop frees the object");
}

void test_destructor_at_zero_once() {
    std::printf("destructor timing\n");
    reset_heap();
    g_log.clear();
    DescBuf d = {};
    d.type_id = 3;
    d.subtree_lo = 3;
    d.subtree_hi = 3;
    d.instance_size = sizeof(ObjectHeader) + 8;
    d.name = "D";
    d.destructor = del_a;
    ClassDescriptor* desc = make_desc(d);

    ObjectHeader* o = construct(desc);
    incref(o);  // two names
    release_drop(o, desc->instance_size);
    check(g_log.empty(), "__del__ does NOT run while a reference remains");
    check(g_heap.destructor_runs == 0, "no destructor run yet");

    release_drop(o, desc->instance_size);
    check(g_log.size() == 1 && g_log[0] == "A",
          "__del__ runs at the last drop, exactly once");
    check(g_heap.destructor_runs == 1, "destructor_runs counts it once");
}

void test_destructor_order_is_observable() {
    std::printf("destructor order\n");
    reset_heap();
    g_log.clear();
    DescBuf da = {};
    da.type_id = 4;
    da.subtree_lo = 4;
    da.subtree_hi = 4;
    da.instance_size = sizeof(ObjectHeader) + 8;
    da.name = "A";
    da.destructor = del_a;
    DescBuf db = da;
    db.type_id = 5;
    db.subtree_lo = 5;
    db.subtree_hi = 5;
    db.name = "B";
    db.destructor = del_b;

    ObjectHeader* a = construct(make_desc(da));
    ObjectHeader* b = construct(make_desc(db));
    // Drop in reverse construction order; the log must record the same order.
    release_drop(b, sizeof(ObjectHeader) + 8);
    release_drop(a, sizeof(ObjectHeader) + 8);
    check(g_log.size() == 2 && g_log[0] == "B" && g_log[1] == "A",
          "destructors run in the order the last drops happen");
}

void test_immortal_is_noop() {
    std::printf("immortal objects\n");
    reset_heap();
    DescBuf d = {};
    d.type_id = 6;
    d.subtree_lo = 6;
    d.subtree_hi = 6;
    d.instance_size = sizeof(ObjectHeader) + 8;
    d.name = "S";
    d.destructor = del_a;
    ClassDescriptor* desc = make_desc(d);

    g_log.clear();
    ObjectHeader* o = construct(desc);
    o->refcount = kImmortalRefcount;  // e.g. a stack-allocated / descriptor object
    incref(o);
    check(o->refcount == kImmortalRefcount, "IncRef is a no-op on an immortal object");
    release_drop(o, desc->instance_size);
    check(o->refcount == kImmortalRefcount, "a drop is a no-op on an immortal object");
    check(g_log.empty(), "an immortal object never runs __del__");
    check(g_heap.live_objects == 1, "an immortal object is never freed by a drop");
}

void test_no_del_is_plain_free() {
    std::printf("no-__del__ classes\n");
    reset_heap();
    g_log.clear();
    DescBuf d = {};
    d.type_id = 7;
    d.subtree_lo = 7;
    d.subtree_hi = 7;
    d.instance_size = sizeof(ObjectHeader) + 8;
    d.name = "N";
    d.destructor = nullptr;  // no __del__: the drop is unobservable
    ClassDescriptor* desc = make_desc(d);

    ObjectHeader* o = construct(desc);
    release_drop(o, desc->instance_size);
    check(g_heap.live_objects == 0, "a class with no __del__ still frees at the last drop");
    check(g_heap.destructor_runs == 0, "and runs no destructor");
}

void test_isinstance_range() {
    std::printf("isinstance range\n");
    // A closed, single hierarchy with pre-order subtree-contiguous ids:
    //   Animal(1) has Dog(2), Puppy(3), Bird(4); subtree = [1,4]
    //   Dog covers [2,3], Puppy [3,3], Bird [4,4]; a sibling Cat would be 5.
    DescBuf animal = {};
    animal.type_id = 1; animal.subtree_lo = 1; animal.subtree_hi = 4; animal.name = "Animal";
    DescBuf dog = {};
    dog.type_id = 2; dog.subtree_lo = 2; dog.subtree_hi = 3; dog.name = "Dog";
    DescBuf puppy = {};
    puppy.type_id = 3; puppy.subtree_lo = 3; puppy.subtree_hi = 3; puppy.name = "Puppy";
    DescBuf bird = {};
    bird.type_id = 4; bird.subtree_lo = 4; bird.subtree_hi = 4; bird.name = "Bird";

    ObjectHeader obj = {};
    obj.vptr = make_desc(puppy);
    check(is_instance_of(&obj, make_desc(animal)), "a Puppy is an Animal");
    check(is_instance_of(&obj, make_desc(dog)), "a Puppy is a Dog");
    check(is_instance_of(&obj, make_desc(puppy)), "a Puppy is a Puppy");
    check(!is_instance_of(&obj, make_desc(bird)), "a Puppy is not a Bird");
    check(!is_instance_of(nullptr, make_desc(animal)), "null is not an instance of anything");

    obj.vptr = make_desc(bird);
    check(is_instance_of(&obj, make_desc(animal)), "a Bird is an Animal");
    check(!is_instance_of(&obj, make_desc(dog)), "a Bird is not a Dog");
}

// 4.10.2. build_class_table is the ONE place the hierarchy becomes runtime
// metadata: pre-order subtree-contiguous ids (so isinstance is two compares),
// the abstract flag, and each class's vtable slot pointing at the `__virt__`
// function the tier resolved for that class. Pinning it here pins the meaning of
// every CallVirtual/IsInstance the frontend emits, for both tiers at once.
void test_class_table_hierarchy() {
    std::printf("class table hierarchy\n");
    reset_heap();
    g_log.clear();

    auto make_class = [](const char* name, int base_index, int size, bool abstract) {
        lithon::ir::ClassInfo ci;
        ci.name = name;
        ci.base_index = base_index;
        ci.instance_size = size;
        ci.abstract = abstract;
        // One slot, the same method name at the same index all the way down:
        // slot indices are stable, so a call compiled against Animal reads slot
        // 0 on a Puppy and reaches Puppy's override.
        ci.slot_count = 1;
        ci.slots.push_back("speak");
        return ci;
    };
    std::vector<lithon::ir::ClassInfo> classes;
    // IR order: a base always precedes its subclasses (the frontend emits it
    // that way). Animal has Dog; Dog has Puppy; Bird is Animal's second child.
    classes.push_back(make_class("Animal", -1, static_cast<int>(sizeof(ObjectHeader)) + 8, true));
    classes.push_back(make_class("Dog", 0, static_cast<int>(sizeof(ObjectHeader)) + 16, false));
    classes.push_back(make_class("Puppy", 1, static_cast<int>(sizeof(ObjectHeader)) + 24, false));
    classes.push_back(make_class("Bird", 0, static_cast<int>(sizeof(ObjectHeader)) + 16, false));

    // The resolver is handed the class's OWN name and the slot name, exactly as
    // the tiers key their `__virt__<Class>.<method>` lookup. Record every call so
    // a silent reordering or a dropped slot is visible.
    std::vector<std::string> resolved;
    auto resolve = [&resolved](const std::string& cls, int slot,
                               const char* slot_name) -> void (*)(void) {
        resolved.push_back(cls + "." + (slot_name != nullptr ? slot_name : "?"));
        if (slot != 0) return nullptr;
        if (cls == "Animal") return animal_speak;
        if (cls == "Dog") return dog_speak;
        if (cls == "Puppy") return puppy_speak;
        if (cls == "Bird") return bird_speak;
        return nullptr;
    };
    build_class_table(classes,
                      [](const std::string&) -> void (*)(void*) { return nullptr; },
                      resolve);

    const ClassTable& t = class_table();
    check(t.names.size() == 4, "the table has all four classes");
    check(t.names[0] == "Animal" && t.names[1] == "Dog" &&
              t.names[2] == "Puppy" && t.names[3] == "Bird",
          "descriptors stay in IR order");
    check(resolved.size() == 4 &&
              resolved[0] == "Animal.speak" && resolved[1] == "Dog.speak" &&
              resolved[2] == "Puppy.speak" && resolved[3] == "Bird.speak",
          "the resolver is called once per class with that class's own name and slot name");

    // Pre-order, subtree-contiguous ids: Animal [1,4], Dog [2,3], Puppy [3,3],
    // Bird [4,4]. Every subtree is one interval, which is what makes isinstance
    // two integer compares and no walk.
    const uint32_t want_id[4] = {1, 2, 3, 4};
    const uint32_t want_lo[4] = {1, 2, 3, 4};
    const uint32_t want_hi[4] = {4, 3, 3, 4};
    for (size_t i = 0; i < 4; ++i) {
        const ClassDescriptor* d = t.descriptors[i];
        check(d->type_id == want_id[i] && d->subtree_lo == want_lo[i] &&
                  d->subtree_hi == want_hi[i],
              "pre-order subtree range is contiguous and covers only the subtree");
    }
    check((t.descriptors[0]->flags & kClassFlagAbstract) != 0,
          "the class with an abstract method carries the abstract flag");
    check((t.descriptors[1]->flags & kClassFlagAbstract) == 0 &&
              (t.descriptors[2]->flags & kClassFlagAbstract) == 0 &&
              (t.descriptors[3]->flags & kClassFlagAbstract) == 0,
          "a concrete subclass does not inherit the abstract flag");
    check(t.descriptors[2]->instance_size == sizeof(ObjectHeader) + 24,
          "the subclass descriptor carries its own (base-first) instance size");

    // Slot 0 is stable down the chain: each class's descriptor holds the code
    // the tier resolved for THAT class, so a virtual call compiled against
    // Animal runs the override when the receiver is a Dog/Puppy/Bird.
    check(t.descriptors[0]->slots[0] == animal_speak &&
              t.descriptors[1]->slots[0] == dog_speak &&
              t.descriptors[2]->slots[0] == puppy_speak &&
              t.descriptors[3]->slots[0] == bird_speak,
          "each class's vtable slot points at the implementation resolved for it");

    // A real constructed instance (host_class_new stamps the vptr from the
    // table) answers isinstance and CallVirtual through its own descriptor.
    ObjectHeader* puppy = host_class_new(2);
    check(puppy != nullptr && puppy->vptr == t.descriptors[2],
          "host_class_new stamps the instance with the class descriptor");
    check(host_class_is_instance(puppy, 0), "a Puppy is an Animal");
    check(host_class_is_instance(puppy, 1), "a Puppy is a Dog");
    check(host_class_is_instance(puppy, 2), "a Puppy is a Puppy");
    check(!host_class_is_instance(puppy, 3), "a Puppy is not a Bird");
    check(host_class_is_instance(puppy, 99) == false,
          "an out-of-range class index is false, not a crash");
    check(host_class_slot(puppy, 0) == reinterpret_cast<void*>(puppy_speak),
          "host_class_slot reads the receiver's own slot");
    check(host_class_slot(puppy, 1) == nullptr,
          "an out-of-range slot is null, not a crash");

    ObjectHeader* bird = host_class_new(3);
    check(host_class_is_instance(bird, 0) && !host_class_is_instance(bird, 1),
          "a Bird is an Animal but not a Dog");
    check(host_class_slot(bird, 0) == reinterpret_cast<void*>(bird_speak),
          "a Bird's slot reaches its own override");

    host_class_release(puppy);
    host_class_release(bird);
    check(g_heap.live_objects == 0, "the hierarchy test releases everything it allocated");
}

void test_aliasing_counter_zero_once() {
    std::printf("aliasing\n");
    reset_heap();
    g_log.clear();
    DescBuf d = {};
    d.type_id = 8;
    d.subtree_lo = 8;
    d.subtree_hi = 8;
    d.instance_size = sizeof(ObjectHeader) + 8;
    d.name = "Alias";
    d.destructor = del_a;
    ClassDescriptor* desc = make_desc(d);

    ObjectHeader* a = construct(desc);
    ObjectHeader* b = a;  // b = a: an alias, not a copy
    incref(b);
    check(a == b && a->refcount == 2, "an alias is the same pointer, counted twice");
    release_drop(a, desc->instance_size);  // drop a
    check(g_log.empty(), "the object survives while b still names it");
    release_drop(b, desc->instance_size);  // drop b -> here it dies
    check(g_log.size() == 1, "the destructor runs exactly once, at the last alias's drop");
}

// 4.10.1. A class attribute that names an object OWNS a reference to it. When
// the holder is released, the held object must be released too -- otherwise the
// held object leaks and its __del__ never runs. This is a REAL bug the hand
// written class_lifetime program caught (a `Wrap(Cell(...))` printed neither the
// holder's nor the held Cell's __del__); it is pinned here so the descriptor's
// ref-attribute list can never silently stop being walked.
//
// The layout is built by the SAME build_class_table the two tiers use, so this
// tests the shipping path and not a lookalike.
void test_nested_attribute_release() {
    std::printf("nested attribute references\n");
    reset_heap();
    g_log.clear();

    // Cell: 8 bytes of payload, a __del__ that logs "held".
    // Wrap: payload = the 8-byte `item` reference at absolute offset 16.
    auto make_cell = [] {
        lithon::ir::ClassInfo ci;
        ci.name = "Cell";
        ci.instance_size = static_cast<int>(sizeof(ObjectHeader)) + 8;
        ci.has_destructor = true;
        return ci;
    };
    std::vector<lithon::ir::ClassInfo> classes;
    classes.push_back(make_cell());
    lithon::ir::ClassInfo wrap;
    wrap.name = "Wrap";
    wrap.instance_size = static_cast<int>(sizeof(ObjectHeader)) + 8;
    wrap.has_destructor = true;
    lithon::ir::ClassAttr item;
    item.name = "item";
    item.offset = static_cast<int>(sizeof(ObjectHeader));  // absolute, header included
    item.type_kind = "class";
    item.type_ref = "Cell";
    wrap.attributes.push_back(item);
    classes.push_back(wrap);

    build_class_table(classes, [](const std::string& name) -> void (*)(void*) {
        if (name == "Cell") return [](void* self) { (void)self; g_log.push_back("held"); };
        if (name == "Wrap") return [](void* self) { (void)self; g_log.push_back("holder"); };
        return nullptr;
    }, [](const std::string&, int, const char*) -> void (*)(void) {
        return nullptr;
    });

    const ClassTable& t = class_table();
    check(t.names.size() == 2, "the table has both classes in IR order");
    ClassDescriptor* wrapd = t.descriptors[1];
    check(wrapd->ref_attr_count == 1, "the holder records one class-typed attribute");
    check(wrapd->ref_attr_offsets != nullptr &&
              wrapd->ref_attr_offsets[0] == sizeof(ObjectHeader),
          "the recorded offset is the absolute attribute offset");

    // wrap.item = cell, with the holder owning its own reference exactly as an
    // emitted Retain would provide.
    ObjectHeader* cellobj = host_class_new(0);
    ObjectHeader* wrapobj = host_class_new(1);
    incref(cellobj);  // the attribute's reference
    std::memcpy(reinterpret_cast<uint8_t*>(wrapobj) + sizeof(ObjectHeader),
                &cellobj, sizeof(cellobj));
    check(cellobj->refcount == 2, "the held object is counted once for the holder and once for the caller");

    host_class_release(wrapobj);  // the only reference to the holder
    check(g_log.size() == 1 && g_log[0] == "holder",
          "releasing the holder runs the holder's __del__, then drops the held reference");
    check(cellobj->refcount == 1, "the held object lost exactly the holder's reference");
    check(g_log.size() == 1, "the held object is still alive: the caller still names it");
    host_class_release(cellobj);  // the caller's own reference -- now the last
    check(g_log.size() == 2 && g_log[1] == "held",
          "the held object's own __del__ runs at ITS last drop, not the holder's");
}

// 4.10.3. A class reference is one 8-byte pointer leaf in a container's flat
// run, not a scalar with byte-run storage of its own. Both tiers size the run
// and compute an index's byte offset from this one function, so pinning the
// leaf size, the leaf count and the row-major strides here pins the layout the
// frontend, typechecker, interpreter and register allocator all share.
void test_class_container_layout() {
    std::printf("class container layout\n");
    auto cls = std::make_shared<lithon::ir::Type>();
    cls->kind = "class";
    cls->ref = "P";

    auto list_p = std::make_shared<lithon::ir::Type>();
    list_p->kind = "list";
    list_p->cap = 3;
    list_p->elem = cls;
    const auto lp = lithon::ir::container_layout(*list_p);
    check(lp.valid, "a list of class references has a flat-leaf layout");
    check(lp.leaf_bytes == 8, "one class reference leaf is one pointer (8 bytes)");
    check(lp.leaf_count == 3 && lp.total_bytes == 24,
          "a list[P, 3] run holds exactly three reference leaves");
    check(lp.strides.size() == 1 && lp.strides[0] == 8,
          "the deepest stride of a list[P, 3] is the 8-byte leaf");

    auto tuple_p = std::make_shared<lithon::ir::Type>();
    tuple_p->kind = "tuple";
    tuple_p->cap = 2;
    tuple_p->elem = cls;
    const auto tp = lithon::ir::container_layout(*tuple_p);
    check(tp.valid && tp.leaf_bytes == 8 && tp.leaf_count == 2 &&
              tp.total_bytes == 16 && tp.strides.size() == 1 &&
              tp.strides[0] == 8,
          "a tuple[P, 2] has the same one-level reference layout");

    // Nested containers multiply the cell count but add no per-level padding;
    // `inner`'s stride is its own capacity times the 8-byte leaf.
    auto inner = std::make_shared<lithon::ir::Type>();
    inner->kind = "list";
    inner->cap = 2;
    inner->elem = cls;
    auto outer = std::make_shared<lithon::ir::Type>();
    outer->kind = "list";
    outer->cap = 3;
    outer->elem = inner;
    const auto nested = lithon::ir::container_layout(*outer);
    check(nested.valid && nested.leaf_bytes == 8 && nested.leaf_count == 6 &&
              nested.total_bytes == 48,
          "list[list[P, 2], 3] is one run of six reference leaves");
    check(nested.strides.size() == 2 && nested.strides[0] == 16 &&
              nested.strides[1] == 8,
          "nesting strides one inner list (2 * 8) per outer index");

    // A bare class type is a value, not a container: it has no run of its own.
    const auto bare = lithon::ir::container_layout(*cls);
    check(!bare.valid, "a bare class type is not a container and has no flat run");

    // The scalar path is unchanged: an int[64] leaf is still 8 bytes.
    auto i64 = std::make_shared<lithon::ir::Type>();
    i64->kind = "int";
    i64->width = 64;
    auto ints = std::make_shared<lithon::ir::Type>();
    ints->kind = "list";
    ints->cap = 2;
    ints->elem = i64;
    const auto il = lithon::ir::container_layout(*ints);
    check(il.valid && il.leaf_bytes == 8 && il.leaf_count == 2 &&
              il.total_bytes == 16,
          "the scalar container layout is unchanged by the class leaf");
}

}  // namespace

int main() {
    std::printf("4.10.0 object runtime + 4.10.1-4.10.3 class tables\n");
    test_layout();
    test_alloc_free_reuse();
    test_size_class_reservation();
    test_refcount_release_at_zero();
    test_destructor_at_zero_once();
    test_destructor_order_is_observable();
    test_immortal_is_noop();
    test_no_del_is_plain_free();
    test_isinstance_range();
    test_class_table_hierarchy();
    test_class_container_layout();
    test_aliasing_counter_zero_once();
    test_nested_attribute_release();

    if (g_failures == 0) {
        std::printf("\nALL PASS\n");
        return 0;
    }
    std::printf("\n%d FAILURE(S)\n", g_failures);
    return 1;
}
