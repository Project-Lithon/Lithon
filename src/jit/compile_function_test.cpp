// The real, first end-to-end proof: compiles a typed ir::Function --
// the exact shape of tests/typed_regression/function.py's
// add(a: int[64], b: int[64]) -> int[64]: return a + b -- to genuine
// x86-64 machine code via compile_module, executes it, and compares
// against the known-correct result the interpreter already produces
// for the same program (7 for add(3, 4)).

#include "compile_function.h"
#include "ir/ir.h"
#include "exec_memory.h"
#include <cstdio>
#include <cstring>

using namespace lithon::ir;
using namespace lithon::jit;

typedef int64_t (*AddFunc)(int64_t, int64_t);

int main() {
    Function fn;
    fn.name = "add";
    fn.params = {"a", "b"};

    BasicBlock block0;
    block0.label = "block0";
    { Instr i; i.op = Op::Load; i.result = 0; i.name = "a"; block0.instrs.push_back(i); }
    { Instr i; i.op = Op::Load; i.result = 1; i.name = "b"; block0.instrs.push_back(i); }
    { Instr i; i.op = Op::Add; i.result = 2; i.args = {0, 1}; block0.instrs.push_back(i); }
    { Instr i; i.op = Op::Return; i.result = kInvalidValue; i.args = {2}; block0.instrs.push_back(i); }
    fn.blocks = {block0};

    Module module;
    module.functions = {fn};
    CompiledModule compiled = compile_module(module);

    std::printf("compiled %zu bytes:", compiled.code.size());
    for (auto b : compiled.code) std::printf(" %02x", b);
    std::printf("\n");

    // Portable W^X executable memory (mmap/mprotect on POSIX, VirtualAlloc/VirtualProtect on Windows).
    ExecutableBuffer exec_mem(compiled.code);
    void* mem = exec_mem.data();

    size_t add_offset = compiled.function_offset.at("add");
    AddFunc compiled_add = reinterpret_cast<AddFunc>(
        reinterpret_cast<uint8_t*>(mem) + add_offset);

    int64_t r1 = compiled_add(3, 4);
    int64_t r2 = compiled_add(100, 200);
    int64_t r3 = compiled_add(-5, 5);

    std::printf("compiled add(3, 4) = %lld (expect 7, matches interpreter)\n", (long long)r1);
    std::printf("compiled add(100, 200) = %lld (expect 300)\n", (long long)r2);
    std::printf("compiled add(-5, 5) = %lld (expect 0)\n", (long long)r3);

    if (r1 != 7 || r2 != 300 || r3 != 0) {
        std::fprintf(stderr, "FAIL\n");
        return 1;
    }
    // ---------------------------------------------------------------- 4.1
    // list[int[64], N] end to end through the real machine-code path: reserve
    // the run, IndexStore into each element at a DISTINCT index, read them all
    // back, and fold the capacity with Len.
    //
    // Distinct values per element are the point. An aliasing bug -- every
    // element landing on one slot, or the run laid out backwards so xs[0] reads
    // the last element -- still prints four lines and only shows up as four
    // copies of the same number, so the expected values are checked individually.
    {
        Function lf;
        lf.name = "lists";

        BasicBlock lb;
        lb.label = "block0";
        auto I = [](Op op, ValueId res, std::vector<ValueId> args) {
            Instr i; i.op = op; i.result = res; i.args = std::move(args); return i;
        };
        { Instr d; d.op = Op::Store; d.name = "xs"; d.type_kind = "list";
          d.type_width = 4; d.type_elem_kind = "int"; d.type_elem_width = 64;
          lb.instrs.push_back(d); }
        for (int k = 0; k < 4; ++k) {
            Instr c; c.op = Op::ConstInt; c.result = ValueId(k); c.int_imm = k;
            lb.instrs.push_back(c);
        }
        for (int k = 0; k < 4; ++k) {
            Instr v; v.op = Op::ConstInt; v.result = ValueId(4 + k); v.int_imm = 100 * (k + 1);
            lb.instrs.push_back(v);
        }
        for (int k = 0; k < 4; ++k) {
            Instr is; is.op = Op::IndexStore; is.name = "xs";
            is.args = {ValueId(k), ValueId(4 + k)};
            lb.instrs.push_back(is);
        }
        for (int k = 0; k < 4; ++k) {
            Instr ix; ix.op = Op::Index; ix.name = "xs";
            ix.result = ValueId(8 + k); ix.args = {ValueId(k)};
            lb.instrs.push_back(ix);
        }
        // Sum the four elements. Returning only Len would pass even if every
        // element aliased one slot; the sum only comes out right when all four
        // are distinct AND in the right order (100+200+300+400 = 1000).
        // (r0 + r1) + (r2 + r3), not a running fold: the pairwise form is what
        // makes a mix-up between r1 and r2 visible.
        Instr a1; a1.op = Op::Add; a1.result = 12; a1.args = {8, 9}; lb.instrs.push_back(a1);
        Instr a2; a2.op = Op::Add; a2.result = 13; a2.args = {10, 11}; lb.instrs.push_back(a2);
        Instr a3; a3.op = Op::Add; a3.result = 14; a3.args = {12, 13}; lb.instrs.push_back(a3);
        { Instr ln; ln.op = Op::Len; ln.name = "xs"; ln.result = 15; lb.instrs.push_back(ln); }
        Instr a4; a4.op = Op::Add; a4.result = 16; a4.args = {15, 15}; lb.instrs.push_back(a4);
        Instr a5; a5.op = Op::Add; a5.result = 17; a5.args = {14, 16}; lb.instrs.push_back(a5);
        { Instr r; r.op = Op::Return; r.result = kInvalidValue; r.args = {17};
          lb.instrs.push_back(r); }
        lf.blocks = {lb};

        Module lm; lm.functions = {lf};
        CompiledModule lc = compile_module(lm);
        ExecutableBuffer lmem(lc.code);
        typedef int64_t (*ListFunc)(void);
        ListFunc lfn = reinterpret_cast<ListFunc>(
            reinterpret_cast<uint8_t*>(lmem.data()) + lc.function_offset.at("lists"));
        // 100+200+300+400 == 1000, plus len(xs)==4 doubled == 8.
        int64_t n = lfn();
        std::printf("compiled sum(xs[0..3]) + 2*len(xs) = %lld (expect 1008)\n", (long long)n);
        if (n != 1008) {
            std::fprintf(stderr, "FAIL: expected 1008, got %lld -- elements aliased or "
                            "laid out out of order\n", (long long)n);
            return 1;
        }
    }

    // A dynamic (non-literal) index that is IN RANGE must still work. The
    // bounds check added for 4.1 emits a trap on failure, and the trap calls
    // host_report_error, which exits the process -- so the failing half cannot
    // be asserted here. This pins the other half: the guard must not
    // false-positive on a legal computed index.
    {
        Function df;
        df.name = "didx";
        BasicBlock db;
        db.label = "block0";
        { Instr d; d.op = Op::Store; d.name = "ds"; d.type_kind = "list";
          d.type_width = 4; d.type_elem_kind = "int"; d.type_elem_width = 64;
          db.instrs.push_back(d); }
        for (int k = 0; k < 4; ++k) {
            Instr c; c.op = Op::ConstInt; c.result = ValueId(k); c.int_imm = k;
            db.instrs.push_back(c);
        }
        for (int k = 0; k < 4; ++k) {
            Instr v; v.op = Op::ConstInt; v.result = ValueId(4 + k); v.int_imm = 100 * (k + 1);
            db.instrs.push_back(v);
        }
        for (int k = 0; k < 4; ++k) {
            Instr is; is.op = Op::IndexStore; is.name = "ds";
            is.args = {ValueId(k), ValueId(4 + k)};
            db.instrs.push_back(is);
        }
        // The index is a Load rather than a constant, so the typechecker cannot
        // range-check it and the runtime guard is the only thing standing
        // between xs[i] and arbitrary frame memory.
        // Store 2 into i, then read it back. args[0] of a Store is the VALUE,
        // not the name, so this is {ValueId(2)} -- passing the loaded value here
        // instead stores whatever was in the uninitialized slot, which is how
        // this test first failed with a spurious out-of-range trap.
        Instr st; st.op = Op::Store; st.name = "i"; st.args = {2};
        db.instrs.push_back(st);
        Instr ld2; ld2.op = Op::Load; ld2.name = "i"; ld2.result = 9; db.instrs.push_back(ld2);
        Instr ix; ix.op = Op::Index; ix.name = "ds"; ix.result = 10; ix.args = {9};
        db.instrs.push_back(ix);
        Instr rr; rr.op = Op::Return; rr.result = kInvalidValue; rr.args = {10};
        db.instrs.push_back(rr);
        df.blocks = {db};
        Module dm; dm.functions = {df};
        CompiledModule dc = compile_module(dm);
        ExecutableBuffer dmem(dc.code);
        typedef int64_t (*DF)(void);
        DF dfn = reinterpret_cast<DF>(
            reinterpret_cast<uint8_t*>(dmem.data()) + dc.function_offset.at("didx"));
        int64_t dv = dfn();
        std::printf("compiled ds[i] with dynamic i=2 -> %lld (expect 300)\n", (long long)dv);
        if (dv != 300) {
            std::fprintf(stderr, "FAIL: expected 300, got %lld -- the dynamic bounds "
                            "check rejected a legal index\n", (long long)dv);
            return 1;
        }
    }

    // A float[64]-returning function, called through the real machine-code
    // path and checked for the exact value.
    //
    // This lives in a JIT test rather than in tests/typed_regression/ because
    // `hello` is interpreter-only -- run_typed_regression.py drives that binary,
    // so a typed_regression program can NEVER catch a native-codegen bug. It
    // compares tier-0 against expected/*.out, and tier-0 was always right here.
    // Both halves of the ABI had to be fixed together: Op::Return has to publish
    // the double in XMM0, and Op::Call has to read it from there. Reverting
    // either one alone still fails this check.
    {
        Function ff;
        ff.name = "get_two";
        ff.return_type_kind = "float";
        ff.return_type_width = 64;

        BasicBlock fb;
        fb.label = "block0";
        { Instr c; c.op = Op::ConstFloat; c.result = 0; c.float_imm = 2.0;
          fb.instrs.push_back(c); }
        { Instr r; r.op = Op::Return; r.result = kInvalidValue; r.args = {0};
          fb.instrs.push_back(r); }
        ff.blocks = {fb};

        // use_two CALLS get_two inside compiled code and returns what it got.
        // Calling get_two directly from C only exercises Op::Return: a real C
        // caller reads XMM0 because that is the ABI, so the Op::Call capture
        // block is never reached. This second function is what puts that block
        // under test -- and it is the half that was missed the first time.
        Function uf;
        uf.name = "use_two";
        uf.return_type_kind = "float";
        uf.return_type_width = 64;

        BasicBlock ub;
        ub.label = "block0";
        { Instr c; c.op = Op::Call; c.name = "get_two"; c.result = 0;
          ub.instrs.push_back(c); }
        // Consuming the captured value, not just forwarding it. Returning %0
        // directly is too weak: Op::Return re-reads the same temp, so a broken
        // capture and a working one can look identical. Adding 100.0 forces the
        // captured value through the SSE add, where garbage cannot cancel out.
        { Instr k; k.op = Op::ConstFloat; k.result = 1; k.float_imm = 100.0;
          ub.instrs.push_back(k); }
        { Instr a; a.op = Op::Add; a.result = 2; a.args = {0, 1}; ub.instrs.push_back(a); }
        { Instr r; r.op = Op::Return; r.result = kInvalidValue; r.args = {2};
          ub.instrs.push_back(r); }
        uf.blocks = {ub};

        Module fm; fm.functions = {ff, uf};
        CompiledModule fc = compile_module(fm);
        ExecutableBuffer fmem(fc.code);
        typedef double (*FloatFunc)(void);
        auto slot = [&](const char* nm) {
            return reinterpret_cast<FloatFunc>(
                reinterpret_cast<uint8_t*>(fmem.data()) + fc.function_offset.at(nm));
        };
        FloatFunc g2 = slot("get_two");
        FloatFunc u2 = slot("use_two");

        double d1 = g2();
        double d2 = g2();
        double d3 = g2();
        // Repeated because a single call can land on a frame slot that happens
        // to hold the right bits by luck; the stale-RAX read was intermittent
        // until it was not.
        std::printf("compiled get_two() = %g, %g, %g (expect 2 2 2)\n", d1, d2, d3);
        if (d1 != 2.0 || d2 != 2.0 || d3 != 2.0) {
            std::fprintf(stderr, "FAIL: float[64] return is not in XMM0 -- got %g/%g/%g, "
                            "expected 2.0 (matches interpreter and CPython)\n",
                         d1, d2, d3);
            return 1;
        }

        double u1 = u2();
        double u2v = u2();
        std::printf("compiled use_two() = get_two() + 100 = %g, %g (expect 102 102)\n",
                    u1, u2v);
        if (u1 != 102.0 || u2v != 102.0) {
            std::fprintf(stderr, "FAIL: Op::Call did not read a float[64] result from XMM0 "
                            "-- got %g/%g, expected 102.0\n", u1, u2v);
            return 1;
        }
    }

    std::printf("PASS: real ir::Function compiled to genuine native machine code and executed correctly\n");
    return 0;
}
