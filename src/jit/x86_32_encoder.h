#pragma once

// x86-32 (IA-32) instruction encoder (4.17). Only what the 32-bit backend emits.
//
// There is no REX prefix and no RIP-relative addressing on IA-32, so this is its
// own small encoder rather than a mode of x86_encoder.h: every register is one of
// the eight 32-bit ones, a memory operand is [ebp+disp32] or an absolute
// [disp32], and every immediate is at most 32 bits.
//
// Each emitter is byte-checked against the GNU assembler (i686-w64-mingw32-as) by
// tools/check_x86_32_encoding.py, which assembles the same instruction text and
// compares the bytes.

#include <cstddef>
#include <cstdint>
#include <vector>

namespace lithon::jit::x86_32 {

using Code = std::vector<uint8_t>;

enum class R : uint8_t { EAX = 0, ECX = 1, EDX = 2, EBX = 3, ESP = 4, EBP = 5, ESI = 6, EDI = 7 };

// Condition codes, the low nibble of 0F 8x (jcc) and 0F 9x (setcc).
enum class Cc : uint8_t {
    O = 0x0, NO = 0x1, B = 0x2, AE = 0x3, E = 0x4, NE = 0x5, BE = 0x6, A = 0x7,
    S = 0x8, NS = 0x9, P = 0xA, NP = 0xB, L = 0xC, GE = 0xD, LE = 0xE, G = 0xF,
};

// The 8x `op r32, r/m32` ALU forms (opcode bytes) and their /digit in the
// `83 /digit ib` and `81 /digit id` immediate forms.
enum class Alu : uint8_t { Add = 0, Or = 1, Adc = 2, Sbb = 3, And = 4, Sub = 5, Xor = 6, Cmp = 7 };

inline void u8(Code& c, uint8_t b) { c.push_back(b); }
inline void u32(Code& c, uint32_t v) {
    for (int i = 0; i < 4; ++i) c.push_back(static_cast<uint8_t>(v >> (8 * i)));
}
inline uint8_t reg(R r) { return static_cast<uint8_t>(r); }

// ModRM for [ebp + disp32]: mod=10, rm=101.
inline void modrm_ebp(Code& c, uint8_t field, int32_t disp) {
    u8(c, static_cast<uint8_t>(0x80 | (field << 3) | 5));
    u32(c, static_cast<uint32_t>(disp));
}
// ModRM for an absolute [disp32]: mod=00, rm=101.
inline void modrm_abs(Code& c, uint8_t field, uint32_t addr) {
    u8(c, static_cast<uint8_t>((field << 3) | 5));
    u32(c, addr);
}
// ModRM register-direct.
inline void modrm_rr(Code& c, uint8_t field, R rm) {
    u8(c, static_cast<uint8_t>(0xC0 | (field << 3) | reg(rm)));
}

// ---- moves ---------------------------------------------------------------
inline void mov_r_m(Code& c, R dst, int32_t disp) { u8(c, 0x8B); modrm_ebp(c, reg(dst), disp); }
inline void mov_m_r(Code& c, int32_t disp, R src) { u8(c, 0x89); modrm_ebp(c, reg(src), disp); }
inline void mov_r_imm(Code& c, R dst, uint32_t imm) { u8(c, static_cast<uint8_t>(0xB8 + reg(dst))); u32(c, imm); }
inline void mov_m_imm(Code& c, int32_t disp, uint32_t imm) {
    u8(c, 0xC7); modrm_ebp(c, 0, disp); u32(c, imm);
}
inline void mov_r_r(Code& c, R dst, R src) { u8(c, 0x89); modrm_rr(c, reg(src), dst); }
inline void mov_r_abs(Code& c, R dst, uint32_t addr) { u8(c, 0x8B); modrm_abs(c, reg(dst), addr); }
inline void lea_r_m(Code& c, R dst, int32_t disp) { u8(c, 0x8D); modrm_ebp(c, reg(dst), disp); }
inline void movzx_r_r8(Code& c, R dst, R src8) { u8(c, 0x0F); u8(c, 0xB6); modrm_rr(c, reg(dst), src8); }

// ---- ALU -----------------------------------------------------------------
// op r32, [ebp+disp32]
inline void alu_r_m(Code& c, Alu op, R dst, int32_t disp) {
    u8(c, static_cast<uint8_t>(static_cast<uint8_t>(op) * 8 + 3));
    modrm_ebp(c, reg(dst), disp);
}
// op r32, r32
inline void alu_r_r(Code& c, Alu op, R dst, R src) {
    u8(c, static_cast<uint8_t>(static_cast<uint8_t>(op) * 8 + 3));
    modrm_rr(c, reg(dst), src);
}
// op r32, imm32
inline void alu_r_imm(Code& c, Alu op, R dst, uint32_t imm) {
    u8(c, 0x81); modrm_rr(c, static_cast<uint8_t>(op), dst); u32(c, imm);
}
// op dword [abs], imm8 (sign-extended)
inline void alu_abs_imm8(Code& c, Alu op, uint32_t addr, int8_t imm) {
    u8(c, 0x83); modrm_abs(c, static_cast<uint8_t>(op), addr); u8(c, static_cast<uint8_t>(imm));
}
inline void test_r_r(Code& c, R a, R b) { u8(c, 0x85); modrm_rr(c, reg(b), a); }
inline void imul_r_m(Code& c, R dst, int32_t disp) { u8(c, 0x0F); u8(c, 0xAF); modrm_ebp(c, reg(dst), disp); }
// mul dword [ebp+disp32]: EDX:EAX = EAX * m32 (unsigned)
inline void mul_m(Code& c, int32_t disp) { u8(c, 0xF7); modrm_ebp(c, 4, disp); }
// imul r32 (one operand): EDX:EAX = EAX * r32, signed, never overflows 64 bits.
inline void imul_r1(Code& c, R r) { u8(c, 0xF7); modrm_rr(c, 5, r); }
// idiv r32: EAX = EDX:EAX / r32, EDX = remainder (signed).
inline void idiv_r(Code& c, R r) { u8(c, 0xF7); modrm_rr(c, 7, r); }
inline void neg_r(Code& c, R r) { u8(c, 0xF7); modrm_rr(c, 3, r); }
inline void not_r(Code& c, R r) { u8(c, 0xF7); modrm_rr(c, 2, r); }
inline void xor_zero(Code& c, R r) { alu_r_r(c, Alu::Xor, r, r); }

inline void setcc_r8(Code& c, Cc cc, R dst8) {
    u8(c, 0x0F); u8(c, static_cast<uint8_t>(0x90 + static_cast<uint8_t>(cc))); modrm_rr(c, 0, dst8);
}

// ---- general memory operands -------------------------------------------------
// [base + disp32] or [ebp + index*scale + disp32]. The base is EBP (a frame slot)
// or any register but ESP (a pointer held in EAX/ECX/EDX); the displacement is
// always 32-bit so every form has one shape. `idx` < 0 means no index register.
struct Mem {
    R base = R::EBP;
    int idx = -1;       // register number, or -1
    uint8_t scale = 1;  // 1, 2, 4 or 8
    int32_t disp = 0;
    bool abs = false;   // an absolute [disp32] (no base)
};
inline Mem at_abs(uint32_t addr) { Mem m; m.abs = true; m.disp = static_cast<int32_t>(addr); return m; }
inline Mem at_ebp(int32_t disp) { return Mem{R::EBP, -1, 1, disp}; }
inline Mem at_ebp_idx(int32_t disp, R idx, uint8_t scale) { return Mem{R::EBP, static_cast<int>(idx), scale, disp}; }
inline Mem at_reg(R base, int32_t disp) { return Mem{base, -1, 1, disp}; }

inline void modrm_mem(Code& c, uint8_t field, const Mem& m) {
    if (m.abs) {
        u8(c, static_cast<uint8_t>((field << 3) | 5));
        u32(c, static_cast<uint32_t>(m.disp));
        return;
    }
    if (m.idx < 0) {
        u8(c, static_cast<uint8_t>(0x80 | (field << 3) | reg(m.base)));
    } else {
        u8(c, static_cast<uint8_t>(0x80 | (field << 3) | 4));
        const uint8_t ss = m.scale == 1 ? 0 : m.scale == 2 ? 1 : m.scale == 4 ? 2 : 3;
        u8(c, static_cast<uint8_t>((ss << 6) | (m.idx << 3) | reg(m.base)));
    }
    u32(c, static_cast<uint32_t>(m.disp));
}
inline void mov_r_mem(Code& c, R dst, const Mem& m) { u8(c, 0x8B); modrm_mem(c, reg(dst), m); }
inline void mov_mem_r(Code& c, const Mem& m, R src) { u8(c, 0x89); modrm_mem(c, reg(src), m); }
inline void mov_mem_imm(Code& c, const Mem& m, uint32_t imm) { u8(c, 0xC7); modrm_mem(c, 0, m); u32(c, imm); }
inline void lea_r_mem(Code& c, R dst, const Mem& m) { u8(c, 0x8D); modrm_mem(c, reg(dst), m); }
inline void movzx_r_mem8(Code& c, R dst, const Mem& m) { u8(c, 0x0F); u8(c, 0xB6); modrm_mem(c, reg(dst), m); }
inline void movsx_r_mem8(Code& c, R dst, const Mem& m) { u8(c, 0x0F); u8(c, 0xBE); modrm_mem(c, reg(dst), m); }
inline void movzx_r_mem16(Code& c, R dst, const Mem& m) { u8(c, 0x0F); u8(c, 0xB7); modrm_mem(c, reg(dst), m); }
inline void movsx_r_mem16(Code& c, R dst, const Mem& m) { u8(c, 0x0F); u8(c, 0xBF); modrm_mem(c, reg(dst), m); }
// 8/16-bit stores take their source from AL/CL/DL/BL (resp. AX/CX/DX/BX).
inline void mov_mem8_r(Code& c, const Mem& m, R src) { u8(c, 0x88); modrm_mem(c, reg(src), m); }
inline void mov_mem16_r(Code& c, const Mem& m, R src) { u8(c, 0x66); u8(c, 0x89); modrm_mem(c, reg(src), m); }
inline void alu_r_mem(Code& c, Alu op, R dst, const Mem& m) {
    u8(c, static_cast<uint8_t>(static_cast<uint8_t>(op) * 8 + 3));
    modrm_mem(c, reg(dst), m);
}
inline void push_mem(Code& c, const Mem& m) { u8(c, 0xFF); modrm_mem(c, 6, m); }
// shld/shrd r/m32(dst), r32(src), imm8 -- double-width shifts: the 64-bit shift of the
// EDX:EAX pair is built from these plus a plain shift of one half.
inline void shld_r_r_imm8(Code& c, R dst, R src, uint8_t n) { u8(c, 0x0F); u8(c, 0xA4); modrm_rr(c, reg(src), dst); u8(c, n); }
inline void shrd_r_r_imm8(Code& c, R dst, R src, uint8_t n) { u8(c, 0x0F); u8(c, 0xAC); modrm_rr(c, reg(src), dst); u8(c, n); }
inline void shl_r_imm8(Code& c, R r, uint8_t n) { u8(c, 0xC1); modrm_rr(c, 4, r); u8(c, n); }
inline void shr_r_imm8(Code& c, R r, uint8_t n) { u8(c, 0xC1); modrm_rr(c, 5, r); u8(c, n); }
inline void sar_r_imm8(Code& c, R r, uint8_t n) { u8(c, 0xC1); modrm_rr(c, 7, r); u8(c, n); }
inline void cdq(Code& c) { u8(c, 0x99); }
inline void imul_r_r_imm32(Code& c, R dst, R src, int32_t imm) {
    u8(c, 0x69); modrm_rr(c, reg(dst), src); u32(c, static_cast<uint32_t>(imm));
}
inline void sse_x_mem(Code& c, uint8_t prefix, uint8_t op, uint8_t xmm, const Mem& m) {
    if (prefix) u8(c, prefix);
    u8(c, 0x0F); u8(c, op); modrm_mem(c, xmm, m);
}
inline void addsd_x_mem(Code& c, uint8_t x, const Mem& m) { sse_x_mem(c, 0xF2, 0x58, x, m); }
inline void subsd_x_mem(Code& c, uint8_t x, const Mem& m) { sse_x_mem(c, 0xF2, 0x5C, x, m); }
inline void mulsd_x_mem(Code& c, uint8_t x, const Mem& m) { sse_x_mem(c, 0xF2, 0x59, x, m); }
inline void divsd_x_mem(Code& c, uint8_t x, const Mem& m) { sse_x_mem(c, 0xF2, 0x5E, x, m); }
inline void ucomisd_x_mem(Code& c, uint8_t x, const Mem& m) { sse_x_mem(c, 0x66, 0x2E, x, m); }
inline void movsd_x_mem(Code& c, uint8_t x, const Mem& m) { u8(c, 0xF2); u8(c, 0x0F); u8(c, 0x10); modrm_mem(c, x, m); }
inline void movsd_mem_x(Code& c, const Mem& m, uint8_t x) { u8(c, 0xF2); u8(c, 0x0F); u8(c, 0x11); modrm_mem(c, x, m); }

// ---- SSE2 scalar double ---------------------------------------------------
// The backend only ever uses XMM0 and XMM1, so an XMM register is its number.
// All memory operands are [ebp+disp32]. Needs an SSE2 CPU (every x86 CPU since
// the Pentium 4 / Athlon 64, and the baseline of every supported 32-bit OS).
inline void sse_x_m(Code& c, uint8_t prefix, uint8_t op, uint8_t xmm, int32_t disp) {
    if (prefix) u8(c, prefix);
    u8(c, 0x0F); u8(c, op); modrm_ebp(c, xmm, disp);
}
inline void movsd_x_m(Code& c, uint8_t x, int32_t d) { sse_x_m(c, 0xF2, 0x10, x, d); }
inline void movsd_m_x(Code& c, int32_t d, uint8_t x) { sse_x_m(c, 0xF2, 0x11, x, d); }
inline void addsd_x_m(Code& c, uint8_t x, int32_t d) { sse_x_m(c, 0xF2, 0x58, x, d); }
inline void subsd_x_m(Code& c, uint8_t x, int32_t d) { sse_x_m(c, 0xF2, 0x5C, x, d); }
inline void mulsd_x_m(Code& c, uint8_t x, int32_t d) { sse_x_m(c, 0xF2, 0x59, x, d); }
inline void divsd_x_m(Code& c, uint8_t x, int32_t d) { sse_x_m(c, 0xF2, 0x5E, x, d); }
inline void ucomisd_x_m(Code& c, uint8_t x, int32_t d) { sse_x_m(c, 0x66, 0x2E, x, d); }

// ---- stack and control flow ---------------------------------------------
inline void push_r(Code& c, R r) { u8(c, static_cast<uint8_t>(0x50 + reg(r))); }
inline void pop_r(Code& c, R r) { u8(c, static_cast<uint8_t>(0x58 + reg(r))); }
inline void push_imm32(Code& c, uint32_t imm) { u8(c, 0x68); u32(c, imm); }
inline void push_m(Code& c, int32_t disp) { u8(c, 0xFF); modrm_ebp(c, 6, disp); }
inline void add_esp(Code& c, int32_t n) { if (n) { u8(c, 0x81); modrm_rr(c, 0, R::ESP); u32(c, static_cast<uint32_t>(n)); } }
inline void sub_esp(Code& c, int32_t n) { if (n) { u8(c, 0x81); modrm_rr(c, 5, R::ESP); u32(c, static_cast<uint32_t>(n)); } }
inline void call_r(Code& c, R r) { u8(c, 0xFF); modrm_rr(c, 2, r); }
inline void ret(Code& c) { u8(c, 0xC3); }

// Relative jumps/calls return the offset of their rel32 field for patching.
inline size_t jcc_rel32(Code& c, Cc cc) {
    u8(c, 0x0F); u8(c, static_cast<uint8_t>(0x80 + static_cast<uint8_t>(cc)));
    size_t at = c.size(); u32(c, 0); return at;
}
inline size_t jmp_rel32(Code& c) { u8(c, 0xE9); size_t at = c.size(); u32(c, 0); return at; }
inline size_t call_rel32(Code& c) { u8(c, 0xE8); size_t at = c.size(); u32(c, 0); return at; }
// Point the rel32 at `at` to `target` (both offsets into the buffer).
inline void patch_rel32(Code& c, size_t at, size_t target) {
    const int32_t rel = static_cast<int32_t>(static_cast<int64_t>(target) - static_cast<int64_t>(at + 4));
    for (int i = 0; i < 4; ++i) c[at + i] = static_cast<uint8_t>(static_cast<uint32_t>(rel) >> (8 * i));
}

// Standard frame: push ebp; mov ebp, esp; sub esp, frame.
inline void prologue(Code& c, int32_t frame) {
    push_r(c, R::EBP); mov_r_r(c, R::EBP, R::ESP); sub_esp(c, frame);
}
inline void epilogue(Code& c) { mov_r_r(c, R::ESP, R::EBP); pop_r(c, R::EBP); }

}  // namespace lithon::jit::x86_32
