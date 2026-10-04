// Same add(3, 4) = 7 proof as zero_dep_test.cpp, but now built from
// the reusable x86_encoder.h helpers instead of a hardcoded byte
// array which confirms the encoder produces byte-identical output to
// the hand-verified sequence.

#include "x86_encoder.h"
#include <sys/mman.h>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <stdexcept>
#include <vector>

using namespace lithon::jit;

typedef int64_t (*AddFunc)(int64_t, int64_t);

static int failures = 0;

// Byte-exact check of one SSE encoding.
static void expect_bytes(const char* what, const CodeBuffer& code,
                         std::initializer_list<uint8_t> want) {
    std::vector<uint8_t> expected(want);
    if (code == expected) return;
    std::fprintf(stderr, "FAIL %s\n  want:", what);
    for (auto b : expected) std::fprintf(stderr, " %02x", b);
    std::fprintf(stderr, "\n  got :");
    for (auto b : code) std::fprintf(stderr, " %02x", b);
    std::fprintf(stderr, "\n");
    ++failures;
}

// Xmm::none is a "no register" sentinel (a memory rm operand), not a register.
// Passed as a register operand it would encode as xmm7, silently, so every
// emitter must refuse it.
template <class F>
static void expect_refused(const char* what, F emit) {
    try {
        emit();
    } catch (const std::logic_error&) {
        return;
    }
    std::fprintf(stderr, "FAIL %s: Xmm::none was accepted as a register operand\n", what);
    ++failures;
}

// The SSE2 encodings that float support depends on. Each of these three
// bugs shipped and each produced wrong values or a segfault rather than a
// compile error, so they are pinned byte-for-byte here.
static int float_encoding_checks() {
    std::printf("--- SSE2 double-precision encodings ---\n");

    // addsd xmm0, xmm1 = f2 0f 58 c1. Neither register is extended, so
    // there is no REX prefix at all.
    {
        CodeBuffer c;
        emit_addsd(c, Xmm::XMM0, Xmm::XMM1);
        expect_bytes("addsd xmm0, xmm1", c, {0xf2, 0x0f, 0x58, 0xc1});
    }

    // The REX test. Addsd xmm15, xmm14 needs REX.R *and* REX.B (0x45):
    // 0x44 alone extends only the ModRM.reg field, so the CPU would read
    // the rm field as xmm6 and compute on the wrong register. Both
    // operands here are the reserved float scratch registers, which is
    // exactly the case that got this wrong.
    {
        CodeBuffer c;
        emit_addsd(c, Xmm::XMM15, Xmm::XMM14);
        // F2 first, REX immediately before the opcode: a REX byte is only a REX
        // prefix when nothing follows it but the opcode, so the mandatory legacy
        // prefix has to come first. GNU as agrees (f2 45 0f 58 fe).
        expect_bytes("addsd xmm15, xmm14 (REX.R and REX.B)", c,
                     {0xf2, 0x45, 0x0f, 0x58, 0xfe});
    }
    // One extended operand is enough to need a REX prefix, and only the
    // bit for the field that is actually extended.
    {
        CodeBuffer c;
        emit_addsd(c, Xmm::XMM15, Xmm::XMM1);
        expect_bytes("addsd xmm15, xmm1 (REX.R only)", c,
                     {0xf2, 0x44, 0x0f, 0x58, 0xf9});
    }
    // The mirror case: only the rm operand is extended, so only REX.B (0x41)
    // is set. Together with the two above this covers R-only, B-only and R+B,
    // so a REX bit wired to the wrong field cannot pass.
    {
        CodeBuffer c;
        emit_addsd(c, Xmm::XMM1, Xmm::XMM15);
        expect_bytes("addsd xmm1, xmm15 (REX.B only)", c,
                     {0xf2, 0x41, 0x0f, 0x58, 0xcf});
    }

    // Memory operands, where the rm field is a BASE register, not an xmm. The
    // old xmm_is_extended(Xmm::none) answered true, so rex_sse() set a spurious
    // REX.B here and [rbp+disp] was fetched from [r13+disp]: same instruction
    // text, wild address. REX.R alone (0x44) is correct for an extended xmm in
    // the reg field; there must be NO B bit.
    {
        CodeBuffer c;
        emit_movsd_xmm_rbp(c, Xmm::XMM15, 8);
        expect_bytes("movsd xmm15, [rbp+8] (REX.R only, no REX.B)", c,
                     {0xf2, 0x44, 0x0f, 0x10, 0xbd, 0x08, 0x00, 0x00, 0x00});
    }
    {
        CodeBuffer c;
        emit_movsd_rbp_mem(c, Xmm::XMM15, 8);
        expect_bytes("movsd [rbp+8], xmm15 (REX.R only, no REX.B)", c,
                     {0xf2, 0x44, 0x0f, 0x11, 0xbd, 0x08, 0x00, 0x00, 0x00});
    }
    // The scaled forms need REX.X for an extended INDEX register, and REX.R for
    // an extended xmm, independently. r10 is the JIT's own scratch index.
    {
        CodeBuffer c;
        emit_movsd_xmm_rbp_scaled(c, Xmm::XMM0, Reg::R10, 16);
        expect_bytes("movsd xmm0, [rbp+r10*8+16] (REX.X only)", c,
                     {0xf2, 0x42, 0x0f, 0x10, 0x84, 0xd5, 0x10, 0x00, 0x00, 0x00});
    }
    {
        CodeBuffer c;
        emit_movsd_rbp_scaled(c, Xmm::XMM15, Reg::R10, 16);
        expect_bytes("movsd [rbp+r10*8+16], xmm15 (REX.R and REX.X)", c,
                     {0xf2, 0x46, 0x0f, 0x11, 0xbc, 0xd5, 0x10, 0x00, 0x00, 0x00});
    }

    // The sentinel must never reach an emitter as a register. Dst and src of the
    // reg-reg ops, and the register of every memory form.
    {
        CodeBuffer c;
        expect_refused("addsd none, xmm1", [&] { emit_addsd(c, Xmm::none, Xmm::XMM1); });
        expect_refused("addsd xmm1, none", [&] { emit_addsd(c, Xmm::XMM1, Xmm::none); });
        expect_refused("movsd xmm, xmm with none", [&] { emit_movsd_xmm_xmm(c, Xmm::XMM0, Xmm::none); });
        expect_refused("movsd none, [rbp+8]", [&] { emit_movsd_xmm_rbp(c, Xmm::none, 8); });
        expect_refused("movsd [rbp+8], none", [&] { emit_movsd_rbp_mem(c, Xmm::none, 8); });
        expect_refused("movsd none, [rbp+r10*8]", [&] { emit_movsd_xmm_rbp_scaled(c, Xmm::none, Reg::R10, 0); });
        expect_refused("movsd [rbp+r10*8], none", [&] { emit_movsd_rbp_scaled(c, Xmm::none, Reg::R10, 0); });
        expect_refused("movsd none, [rip]", [&] { emit_movsd_xmm_rip(c, Xmm::none); });
        expect_refused("ucomisd none, xmm1", [&] { emit_ucomisd(c, Xmm::none, Xmm::XMM1); });
        expect_refused("cvtsi2sd none, rax", [&] { emit_cvtsi2sd(c, Xmm::none, Reg::RAX); });
        expect_refused("xorpd none", [&] { emit_xorpd_zero(c, Xmm::none); });
    }

    // The SETcc opcode table. Cond::NotParity is 0x9B as a Jcc, and its
    // SETcc form is *also* 0x9B -- unlike every other condition, where
    // SETcc is the Jcc byte plus 0x10. A blanket +0x10 turned setnp into
    // 0xAB, which is stosd, so the float comparison silently did the
    // wrong thing. Both halves are pinned.
    {
        CodeBuffer c;
        emit_setcc(c, Cond::Equal, Reg::RAX);
        expect_bytes("sete al (Jcc 0x84 -> SETcc 0x94)", c,
                     {0x40, 0x0f, 0x94, 0xc0});
    }
    {
        CodeBuffer c;
        emit_setcc(c, Cond::Below, Reg::RAX);
        expect_bytes("setb al (Jcc 0x92 -> SETcc 0x92)", c,
                     {0x40, 0x0f, 0x92, 0xc0});
    }
    {
        CodeBuffer c;
        emit_setcc(c, Cond::NotParity, Reg::R10);
        expect_bytes("setnp r10b (Jcc 0x9B -> SETcc 0x9B, no +0x10)", c,
                     {0x41, 0x0f, 0x9b, 0xc2});
    }
    {
        CodeBuffer c;
        emit_setcc(c, Cond::Parity, Reg::RAX);
        expect_bytes("setp al (Jcc 0x9A -> SETcc 0x9A, no +0x10)", c,
                     {0x40, 0x0f, 0x9a, 0xc0});
    }

    // and dst, src is AND r/m, r: src goes in ModRM.reg and dst in rm.
    // Getting these the other way round computed `tmp &= dst` and left
    // the comparison result untouched, which is how a NaN comparison came
    // out True. Pinned in both directions because the operand order is
    // not visible in the C++ signature.
    {
        CodeBuffer c;
        emit_and_reg_reg(c, Reg::RAX, Reg::R10);
        expect_bytes("and rax, r10 (dst in rm, src in reg)", c,
                     {0x44, 0x21, 0xd0});
    }

    // ucomisd xmm7, xmm6 = 66 0f 2e fe, and the RIP-relative pool load
    // must carry a 4-byte disp32 placeholder. Without it the constant
    // pool was unreachable: the following instruction's bytes were
    // consumed as the displacement, and the pool load read whatever
    // followed. movsd_rip_disp_offset() computes the patch site as
    // size()-4, so the placeholder is load-bearing.
    {
        CodeBuffer c;
        emit_ucomisd(c, Xmm::XMM7, Xmm::XMM6);
        expect_bytes("ucomisd xmm7, xmm6", c, {0x66, 0x0f, 0x2e, 0xfe});
    }
    {
        CodeBuffer c;
        emit_movsd_xmm_rip(c, Xmm::XMM0);
        expect_bytes("movsd xmm0, [rip+d32] (9 bytes, placeholder present)", c,
                     {0xf2, 0x0f, 0x10, 0x05, 0x00, 0x00, 0x00, 0x00});
        // f2 0f 10 /r with a RIP-relative rm is 1 F2 + 2 opcode + 1 ModRM
        // + 4 disp32 = 8, so the disp32 is the last 4 bytes and the patch
        // site movsd_rip_disp_offset() reports is byte 4. An earlier version
        // of the emitter omitted the disp32 entirely, which left this at
        // 4 bytes and made the patch land inside the *next* instruction.
        if (c.size() != 8 || movsd_rip_disp_offset(c) != 4) {
            std::fprintf(stderr,
                         "FAIL movsd rip patch site: size=%zu offset=%zu, want 8 and 4\n",
                         c.size(), movsd_rip_disp_offset(c));
            ++failures;
        }
    }
    {
        CodeBuffer c;
        emit_movsd_mem_rip(c, Xmm::XMM0);
        expect_bytes("movsd [rip+d32], xmm0 (9 bytes, placeholder present)", c,
                     {0xf2, 0x0f, 0x11, 0x05, 0x00, 0x00, 0x00, 0x00});
    }

    if (failures) {
        std::fprintf(stderr, "FAIL: %d SSE2 encoding check(s) wrong\n", failures);
        return 1;
    }
    std::printf("PASS: SSE2 encodings byte-exact\n");
    return 0;
}

int main() {
    CodeBuffer code;

    // System V AMD64: arg0 in rdi, arg1 in rsi, return in rax.
    emit_mov_reg_reg(code, Reg::RAX, Reg::RDI);  // rax = a
    emit_add_reg_reg(code, Reg::RAX, Reg::RSI);  // rax += b
    emit_ret(code);

    std::printf("encoded %zu bytes:", code.size());
    for (auto b : code) std::printf(" %02x", b);
    std::printf("\n");

    void* mem = mmap(nullptr, code.size(), PROT_READ | PROT_WRITE,
                      MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) { std::perror("mmap"); return 1; }

    std::memcpy(mem, code.data(), code.size());

    if (mprotect(mem, code.size(), PROT_READ | PROT_EXEC) != 0) {
        std::perror("mprotect");
        return 1;
    }

    AddFunc fn = reinterpret_cast<AddFunc>(mem);
    int64_t result = fn(3, 4);

    std::printf("native add(3, 4) = %lld\n", (long long)result);
    if (result != 7) {
        std::fprintf(stderr, "FAIL: expected 7, got %lld\n", (long long)result);
        return 1;
    }
    std::printf("PASS: reusable encoder confirmed working\n");

    return float_encoding_checks();
}
