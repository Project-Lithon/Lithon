#pragma once

#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

// Zero-dependency x86-64 instruction encoder. Hand-written, no
// external assembler or library.
//
// All sixteen general-purpose registers are supported. r8-r15 are
// reached through the REX.R / REX.B extension bits, which rex() below
// computes from the operands, so every emitter works uniformly for
// both register banks.

namespace lithon::jit {

enum class Reg : uint8_t {
    RAX = 0, RCX = 1, RDX = 2, RBX = 3,
    RSP = 4, RBP = 5, RSI = 6, RDI = 7,
    R8 = 8, R9 = 9, R10 = 10, R11 = 11,
    R12 = 12, R13 = 13, R14 = 14, R15 = 15
};

inline uint8_t reg_low3(Reg r) { return static_cast<uint8_t>(static_cast<uint8_t>(r) & 7); }
inline bool reg_is_extended(Reg r) { return static_cast<uint8_t>(r) >= 8; }

// REX prefix. W selects 64-bit operand size, R extends the ModRM.reg
// field, B extends the ModRM.rm field (or the opcode-embedded register).
inline uint8_t rex(bool w, Reg reg_field, Reg rm_field) {
    return static_cast<uint8_t>(0x40 | (w ? 8 : 0) |
                                (reg_is_extended(reg_field) ? 4 : 0) |
                                (reg_is_extended(rm_field) ? 1 : 0));
}

// 4.1. The SIB form needs the REX.X bit, which the two-field rex() above has no
// place for: X extends the SIB.index field, not ModRM.rm. Without it, an index
// in r8..r15 addresses the low three bits and the upper five are read as the
// next instruction -- a silent, wildly out-of-range address rather than a
// decoding failure, so this bit is load-bearing for any index above rbx.
inline uint8_t rex3(bool w, Reg reg_field, Reg index_field, Reg base_field) {
    return static_cast<uint8_t>(0x40 | (w ? 8 : 0) |
                                (reg_is_extended(reg_field) ? 4 : 0) |
                                (reg_is_extended(index_field) ? 2 : 0) |
                                (reg_is_extended(base_field) ? 1 : 0));
}

using CodeBuffer = std::vector<uint8_t>;

inline void emit_u8(CodeBuffer& buf, uint8_t byte) {
    buf.push_back(byte);
}

inline void emit_u32_le(CodeBuffer& buf, uint32_t bits) {
    for (int i = 0; i < 4; ++i) emit_u8(buf, static_cast<uint8_t>((bits >> (8 * i)) & 0xFF));
}

// ModRM byte for register-direct addressing: mod=11, reg field,
// rm field. Used by every reg-to-reg instruction below.
inline uint8_t modrm_reg_reg(Reg reg_field, Reg rm_field) {
    return static_cast<uint8_t>(0xC0 | (reg_low3(reg_field) << 3) | reg_low3(rm_field));
}

// mov dst, src  (64-bit register to register)
// Encoding: REX.W + 89 /r   (MOV r/m64, r64 -- src is the "reg" field,
// dst is the "r/m" field). Verified: mov rax, rdi = 48 89 f8.
inline void emit_mov_reg_reg(CodeBuffer& buf, Reg dst, Reg src) {
    emit_u8(buf, rex(true, src, dst));
    emit_u8(buf, 0x89);
    emit_u8(buf, modrm_reg_reg(src, dst));
}

// mov dst, imm64  (always the 10-byte movabs form)
// Encoding: REX.W + B8+r io.
inline void emit_mov_reg_imm64(CodeBuffer& buf, Reg dst, int64_t imm) {
    emit_u8(buf, static_cast<uint8_t>(0x48 | (reg_is_extended(dst) ? 1 : 0)));
    emit_u8(buf, static_cast<uint8_t>(0xB8 + reg_low3(dst)));
    uint64_t bits;
    std::memcpy(&bits, &imm, sizeof(bits));
    for (int i = 0; i < 8; ++i) {
        emit_u8(buf, static_cast<uint8_t>((bits >> (8 * i)) & 0xFF));
    }
}

inline bool fits_imm32(int64_t v) { return v >= INT32_MIN && v <= INT32_MAX; }
inline bool fits_imm8(int64_t v) { return v >= -128 && v <= 127; }

// mov dst, imm -- picks the shortest correct encoding:
//   0 <= imm <= 0xFFFFFFFF : mov r32, imm32   (5 bytes; zero-extends to 64)
//   fits signed 32 bits    : mov r/m64, imm32 (7 bytes; sign-extends)
//   otherwise              : movabs           (10 bytes)
inline void emit_mov_reg_imm(CodeBuffer& buf, Reg dst, int64_t imm) {
    if (imm >= 0 && imm <= 0xFFFFFFFFLL) {
        if (reg_is_extended(dst)) emit_u8(buf, 0x41);
        emit_u8(buf, static_cast<uint8_t>(0xB8 + reg_low3(dst)));
        emit_u32_le(buf, static_cast<uint32_t>(imm));
    } else if (fits_imm32(imm)) {
        emit_u8(buf, rex(true, Reg::RAX, dst));
        emit_u8(buf, 0xC7);
        emit_u8(buf, static_cast<uint8_t>(0xC0 | reg_low3(dst)));
        emit_u32_le(buf, static_cast<uint32_t>(static_cast<int32_t>(imm)));
    } else {
        emit_mov_reg_imm64(buf, dst, imm);
    }
}

// add dst, src  (64-bit register to register)
// Encoding: REX.W + 01 /r   (ADD r/m64, r64)
inline void emit_add_reg_reg(CodeBuffer& buf, Reg dst, Reg src) {
    emit_u8(buf, rex(true, src, dst));
    emit_u8(buf, 0x01);
    emit_u8(buf, modrm_reg_reg(src, dst));
}

// sub dst, src  (64-bit register to register)
// Encoding: REX.W + 29 /r   (SUB r/m64, r64)
inline void emit_sub_reg_reg(CodeBuffer& buf, Reg dst, Reg src) {
    emit_u8(buf, rex(true, src, dst));
    emit_u8(buf, 0x29);
    emit_u8(buf, modrm_reg_reg(src, dst));
}

// imul dst, src  (64-bit signed multiply, register to register)
// Encoding: REX.W + 0F AF /r   (IMUL r64, r/m64 -- operand order is
// REVERSED from add/sub: dst is the "reg" field here)
inline void emit_imul_reg_reg(CodeBuffer& buf, Reg dst, Reg src) {
    emit_u8(buf, rex(true, dst, src));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0xAF);
    emit_u8(buf, modrm_reg_reg(dst, src));
}

// cqo -- sign-extend RAX into RDX:RAX. CWD/CDQ/CQO are the same opcode
// 0x99, and REX.W is what selects the 64-bit form.
// Encoding: REX.W + 99.
inline void emit_cqo(CodeBuffer& buf) {
    emit_u8(buf, rex(true, Reg::RAX, Reg::RAX));
    emit_u8(buf, 0x99);
}

// idiv r/m64 -- signed divide RDX:RAX by the operand, leaving the quotient
// in RAX and the remainder in RDX.
// Encoding: REX.W + F7 /7. (The /1 and /5 forms are imul, /0 is test,
// /4 is mul, so 7 is idiv.)
//
// Raises #DE on a zero divisor, and also on INT64_MIN / -1, which has no
// representable quotient. The caller is responsible for both; this encoder
// does not paper over them.
inline void emit_idiv_reg(CodeBuffer& buf, Reg src) {
    emit_u8(buf, rex(true, Reg::RDX, src));
    emit_u8(buf, 0xF7);
    emit_u8(buf, static_cast<uint8_t>(0xF8 | reg_low3(src)));
}

// sar dst, imm8  (64-bit arithmetic shift right by a count in 1..63)
// Encoding: [REX.W] C1 /7 ib. Only a real shift count is encodable here;
// x86 masks the count to 6 bits on execution, so a count of 0 is encoded as
// 32, not 0. Left-shifting the count into the /7 slot is what puts imm8
// where the CPU reads it.
inline void emit_sar_reg_imm8(CodeBuffer& buf, Reg dst, uint8_t imm) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0xC1);
    emit_u8(buf, static_cast<uint8_t>(0xF8 | reg_low3(dst)));
    emit_u8(buf, imm);
}

// and dst, imm8  (64-bit, sign-extended immediate)
// Encoding: REX.W + 83 /4 ib.
//
// Only valid when the mask fits a signed byte. The 83 /4 ib form
// SIGN-EXTENDS its immediate, so `and r, 1023` encoded this way masks with
// 0xFFFFFFFFFFFFFFFF and does nothing at all -- a silently wrong result rather
// than a crash, which is why emit_and_reg_imm32 exists and the caller has to
// choose between them.
inline void emit_and_reg_imm8(CodeBuffer& buf, Reg dst, int8_t imm) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0x83);
    emit_u8(buf, static_cast<uint8_t>(0xE0 | reg_low3(dst)));
    emit_u8(buf, static_cast<uint8_t>(imm));
}

// and dst, imm32  (64-bit, sign-extended immediate) -- the /4 form of the
// group-1 ALU op, for masks that do not fit a signed byte.
// Encoding: REX.W + 81 /4 id. /n: add=0, sub=5, cmp=7, and=4.
inline void emit_and_reg_imm32(CodeBuffer& buf, Reg dst, int32_t imm) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0x81);
    emit_u8(buf, static_cast<uint8_t>(0xE0 | reg_low3(dst)));
    emit_u32_le(buf, static_cast<uint32_t>(imm));
}

// imul dst, src, imm32  (three-operand form, REX.W + 69 /r id).
// Works even when dst == src.
inline void emit_imul_reg_reg_imm32(CodeBuffer& buf, Reg dst, Reg src, int32_t imm) {
    emit_u8(buf, rex(true, dst, src));
    emit_u8(buf, 0x69);
    emit_u8(buf, modrm_reg_reg(dst, src));
    emit_u32_le(buf, static_cast<uint32_t>(imm));
}

// Group-1 ALU op with an immediate: REX.W + 83 /n ib (imm8) or
// REX.W + 81 /n id (imm32). /n: add=0, sub=5, cmp=7.
inline void emit_alu_reg_imm(CodeBuffer& buf, uint8_t digit, Reg dst, int32_t imm) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    if (fits_imm8(imm)) {
        emit_u8(buf, 0x83);
        emit_u8(buf, static_cast<uint8_t>(0xC0 | (digit << 3) | reg_low3(dst)));
        emit_u8(buf, static_cast<uint8_t>(static_cast<int8_t>(imm)));
    } else {
        emit_u8(buf, 0x81);
        emit_u8(buf, static_cast<uint8_t>(0xC0 | (digit << 3) | reg_low3(dst)));
        emit_u32_le(buf, static_cast<uint32_t>(imm));
    }
}

inline void emit_add_reg_imm32(CodeBuffer& buf, Reg dst, int32_t imm) { emit_alu_reg_imm(buf, 0, dst, imm); }
inline void emit_sub_reg_imm32(CodeBuffer& buf, Reg dst, int32_t imm) { emit_alu_reg_imm(buf, 5, dst, imm); }
inline void emit_cmp_reg_imm32(CodeBuffer& buf, Reg lhs, int32_t imm) { emit_alu_reg_imm(buf, 7, lhs, imm); }

// cmp lhs, rhs  (64-bit register to register)
// Encoding: REX.W + 39 /r   (CMP r/m64, r64)
inline void emit_cmp_reg_reg(CodeBuffer& buf, Reg lhs, Reg rhs) {
    emit_u8(buf, rex(true, rhs, lhs));
    emit_u8(buf, 0x39);
    emit_u8(buf, modrm_reg_reg(rhs, lhs));
}

// test reg, reg  (64-bit) -- ANDs the operand with itself purely to
// set flags (ZF set iff reg == 0), without modifying either operand.
// Encoding: REX.W + 85 /r (TEST r/m64, r64).
// Verified: test rax, rax = 48 85 c0.
inline void emit_test_reg_reg(CodeBuffer& buf, Reg reg) {
    emit_u8(buf, rex(true, reg, reg));
    emit_u8(buf, 0x85);
    emit_u8(buf, modrm_reg_reg(reg, reg));
}

// ret
inline void emit_ret(CodeBuffer& buf) {
    emit_u8(buf, 0xC3);
}

// A patch point: the byte offset within the buffer where a jump's
// 4-byte rel32 displacement lives, and the offset marking the END of
// that jump/call instruction (relative displacements are always
// computed from the address immediately following the instruction).
struct JumpPatch {
    size_t rel32_offset;
    size_t instr_end_offset;
};

inline void patch_u32_at(CodeBuffer& buf, size_t offset, uint32_t value) {
    buf[offset + 0] = static_cast<uint8_t>(value & 0xFF);
    buf[offset + 1] = static_cast<uint8_t>((value >> 8) & 0xFF);
    buf[offset + 2] = static_cast<uint8_t>((value >> 16) & 0xFF);
    buf[offset + 3] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

inline void resolve_jump_patch(CodeBuffer& buf, const JumpPatch& patch, size_t target_offset) {
    int32_t rel = static_cast<int32_t>(
        static_cast<int64_t>(target_offset) - static_cast<int64_t>(patch.instr_end_offset));
    patch_u32_at(buf, patch.rel32_offset, static_cast<uint32_t>(rel));
}

// jmp rel32 (unconditional). Encoding: E9 cd.
inline JumpPatch emit_jmp_rel32(CodeBuffer& buf) {
    emit_u8(buf, 0xE9);
    size_t rel32_offset = buf.size();
    emit_u32_le(buf, 0);
    return JumpPatch{rel32_offset, buf.size()};
}

// Conditional jumps, following a preceding cmp or test. Encoding:
// 0F 8x cd. The x86 condition codes come in complementary pairs that
// differ only in the lowest bit, which invert() exploits.
enum class Cond : uint8_t {
    Less = 0x8C, GreaterEq = 0x8D, LessEq = 0x8E, Greater = 0x8F,
    Equal = 0x84, NotEqual = 0x85, NotZero = 0x85,
    // Unsigned-style conditions, named after the flags they read. These are
    // what comisd/ucomisd need: they treat the compared pair as if it were
    // an unsigned integer, which is exactly the ordering a double has.
    //   Below    = CF   (lhs <  rhs)
    //   Above    = !CF && !ZF  (lhs > rhs)
    //   Parity   = PF   (set when the operands were unordered, i.e. NaN)
    //   NotParity = !PF
    Below = 0x92, Above = 0x97, Parity = 0x9A, NotParity = 0x9B,
    // TRAP: this enum mixes two opcode spaces, and only the primary names
    // above are in the Jcc space.
    //
    //   Less/GreaterEq/LessEq/Greater/Equal/NotEqual are Jcc opcodes
    //     (0x8C = jl, 0x8F = jg, 0x84 = je, 0x85 = jne, ...) and
    //     emit_jcc_rel32 uses them directly.
    //   Below/Above/Parity/NotParity are SETcc opcodes, which are always
    //     Jcc + 0x10 (0x92 = setb, 0x97 = seta, ...) because comisd and
    //     ucomisd need a flag test as a byte, not a branch. They are ONLY
    //     valid with emit_setcc.
    //
    // Handing a SETcc value to emit_jcc_rel32 does not fault. It encodes a
    // completely different instruction: 0x97 as a Jcc byte is not `ja`, it is
    // `seta m8` -- a store of one byte to a computed address, which silently
    // corrupts memory. So the unsigned JUMP needed for a runtime range check
    // gets its own explicitly Jcc-spaced name here, rather than borrowing
    // Cond::Above and hoping.
    JumpAbove = 0x87,   // ja: unsigned lhs > rhs. Jcc space. emit_jcc_rel32 only.
    JumpBelowEq = 0x86, // jbe: unsigned lhs <= rhs. Jcc space. emit_jcc_rel32 only.
};

inline Cond invert(Cond c) {
    return static_cast<Cond>(static_cast<uint8_t>(c) ^ 1);
}

inline JumpPatch emit_jcc_rel32(CodeBuffer& buf, Cond cond) {
    emit_u8(buf, 0x0F);
    emit_u8(buf, static_cast<uint8_t>(cond));
    size_t rel32_offset = buf.size();
    emit_u32_le(buf, 0);
    return JumpPatch{rel32_offset, buf.size()};
}

// --- Stack-relative addressing, for spilled values and locals ---
// Always uses the disp32 ModRM form (mod=10), not the shorter disp8
// form -- one uniform code path, correct for any offset magnitude.

inline void emit_disp32_le(CodeBuffer& buf, int32_t disp) {
    emit_u32_le(buf, static_cast<uint32_t>(disp));
}

// cmp lhs, [rbp + disp]  (64-bit register against a frame slot)
//
// 4.3. Exists for the dict probe, which has two registers for three live values.
// The bucket index and the loaded table key take one each, which leaves nowhere
// to put a search key that has to survive every iteration of the walk. Holding
// it in a frame slot and comparing against memory keeps the loop down to two
// registers.
//
// The slot is read as a full 8 bytes. A narrower slot would compare the low
// bytes against whatever was above them in the register, so the caller has to
// have spilled a sign or zero extended value, not a truncated one.
inline void emit_cmp_reg_rbp_offset(CodeBuffer& buf, Reg lhs, int32_t disp) {
    emit_u8(buf, rex(true, lhs, Reg::RBP));
    emit_u8(buf, 0x3B);
    emit_u8(buf, static_cast<uint8_t>(0x80 | (reg_low3(lhs) << 3) | reg_low3(Reg::RBP)));
    emit_disp32_le(buf, disp);
}

// cmp byte ptr [rip + disp32], imm8 -- 80 /7 ib with mod=00 rm=101.
// The one-instruction hoisted CPUID gate the vectorizer uses:
//
//     cmp byte ptr [rip+flag], 0     ;  80 3D <rel32> 00
//     jz  L_fallback                 ;  only AVX2 hosts run the ymm block
//
// REX is deliberately absent: the encoded form reads the RIP-relative base
// without an extension, so adding REX would force mod=11 decoding and break
// the memory operand. The immutable follows the disp32, so the displacement's
// first byte sits at buf.size()-5; call cmp_byte_rip_imm_disp_offset()
// immediately afterwards and patch the rel32 once the flag byte's position
// in the pool is known (exactly as movsd_rip_disp_offset does for doubles).
inline void emit_cmp_byte_rip_imm(CodeBuffer& buf, uint8_t imm) {
    emit_u8(buf, 0x80);
    emit_u8(buf, 0x3D);
    emit_disp32_le(buf, 0);
    emit_u8(buf, imm);
}
inline size_t cmp_byte_rip_imm_disp_offset(CodeBuffer& buf) { return buf.size() - 5; }


// 4.1. Scaled-index forms of the two above: [rbp + disp + index*stride]. A list
// element lives at base + i*stride, so with a RUNNING index the address cannot
// be a constant displacement -- it needs a SIB byte.
//
// The SIB always carries a displacement even when the index is 0, because
// mod=00 with base=RBP (101) means RIP-relative on x86-64, not [rbp+0]. Using
// mod=10 unconditionally sidesteps that special case rather than branching on
// it, at the cost of four bytes.
//
// The stride is packed, i.e. exactly sizeof(T), because that is what makes
// `ptr[T]` arithmetic in 4.4 mean "one element": if a list spaced its elements
// 8 bytes apart while pointer arithmetic assumed sizeof(T), addressof(xs, i)
// would compute the wrong address for every type narrower than 8 bytes and the
// two features would silently disagree. This turns out to be cheaper than the
// uniform-8-byte stride it replaces rather than dearer -- every scalar type in
// the language is 1, 2, 4 or 8 bytes, which is exactly the set of scale factors
// the SIB byte can encode, so all of them are a single SIB form with no lea, no
// imul and no extra instruction. A stride outside that set (a nested container,
// 4.1. Scaled-index forms: [rbp + disp + index*stride]. A list element lives at
// base + i*stride, so with a RUNNING index the address cannot be a constant
// displacement -- it needs a SIB byte.
//
// The SIB always carries a displacement even when the index is 0, because
// mod=00 with base=RBP (101) means RIP-relative on x86-64, not [rbp+0]. Using
// mod=10 unconditionally sidesteps that special case rather than branching on
// it, at the cost of four bytes.
//
// The stride is packed, i.e. exactly sizeof(T), because that is what makes
// `ptr[T]` arithmetic in 4.4 mean "one element": if a list spaced its elements
// 8 bytes apart while pointer arithmetic assumed sizeof(T), addressof(xs, i)
// would compute the wrong address for every type narrower than 8 bytes and the
// two features would silently disagree. This turned out to be CHEAPER than the
// uniform-8-byte stride it replaces rather than dearer -- every scalar type is
// 1, 2, 4 or 8 bytes, which is exactly the set of factors the SIB byte can
// encode, so all four widths are one SIB form with no lea and no imul. A stride
// outside that set (a nested container, whose inner length makes it a
// non-power-of-two) is not representable and is rejected at compile time rather
// than silently mis-addressed.
//
// SS is 00=x1, 01=x2, 10=x4, 11=x8. Writing the wrong constant here silently
// emits a different scale -- caught by tools/check_encoder_vs_as.py disagreeing
// with GNU as on the *4 vs *8 operand text, which is a better bug report than
// the wrong answer at runtime would have been.
constexpr uint8_t kSibScale1 = 0 << 6;   // SS=00 -> scale factor 1
constexpr uint8_t kSibScale2 = 1 << 6;   // SS=01 -> scale factor 2
constexpr uint8_t kSibScale4 = 2 << 6;   // SS=10 -> scale factor 4
constexpr uint8_t kSibScale8 = 3 << 6;   // SS=11 -> scale factor 8

// SIB scale field for an element stride in bytes, or -1 when the stride is not
// one of the four encodable factors.
//
// -1 rather than 0 as the failure value: scale 1 is SS=00, which IS 0, so a 0
// sentinel is indistinguishable from a perfectly valid answer and every
// 1-byte-stride access would have been reported as unrepresentable.
inline int sib_scale_for_stride(int stride) {
    switch (stride) {
        case 1: return kSibScale1;
        case 2: return kSibScale2;
        case 4: return kSibScale4;
        case 8: return kSibScale8;
        default: return -1;
    }
}

// The stride must be one of 1/2/4/8. Anything else has no SIB encoding, so this
// fails here rather than emitting an address that reads the wrong memory.
inline uint8_t checked_sib_scale(int stride) {
    const int s = sib_scale_for_stride(stride);
    if (s < 0) throw std::logic_error("x86_encoder: element stride " +
                                      std::to_string(stride) +
                                      " has no SIB scale encoding");
    return static_cast<uint8_t>(s);
}

inline void emit_sib_tail(CodeBuffer& buf, Reg data_reg, Reg index_reg, uint8_t scale) {
    // ModRM: mod=10 (disp32), reg=data_reg, r/m=100 (SIB follows)
    emit_u8(buf, static_cast<uint8_t>(0x80 | (reg_low3(data_reg) << 3) | 0x4));
    // SIB: scale, index=index_reg, base=RBP
    emit_u8(buf, static_cast<uint8_t>(scale | (reg_low3(index_reg) << 3) |
                                      reg_low3(Reg::RBP)));
}

// add dst, [rbp + disp + index*stride] -- the accumulator step of the
// vectorizer's scalar tail loop. Opcode 03 /r (destination-register ADD,
// the same shape emit_load_rbp_scaled uses for 8B). `wide` mirrors the mov
// emitters' width contract: wide=false is `add r32d, [mem]`, whose result is
// truncated to 32 bits exactly like the vector lanes, which is the width the
// tail accumulator must match for the scalar merge to agree with the ymm sum.
inline void emit_add_reg_rbp_scaled(CodeBuffer& buf, Reg dst, Reg index_reg,
                                    int32_t disp, int stride = 8, bool wide = true) {
    const bool rex_r = reg_is_extended(dst);
    const bool rex_x = reg_is_extended(index_reg);
    if (wide || rex_r || rex_x)
        emit_u8(buf, static_cast<uint8_t>(0x40 | (wide ? 8 : 0) |
                                         (rex_r ? 4 : 0) | (rex_x ? 2 : 0)));
    emit_u8(buf, 0x03);
    emit_sib_tail(buf, dst, index_reg, checked_sib_scale(stride));
    emit_disp32_le(buf, disp);
}

// 4.1. Width-correct integer element access, scaled or not.
//
// Packed layout means sizeof(T) is BOTH the SIB scale and the operand width, so
// one `bytes` argument drives both. That coincidence is why packing is cheap:
// a 4-byte element is `mov dword ptr [rbp+d+i*4], r32` -- the same SIB form
// with REX.W dropped. There is no separate narrow path.
//
// REX.W must NOT be set on a sub-8-byte access. Leaving it set silently widens
// the access to 8 bytes, so storing element i would overwrite element i+1 and
// run off the end of the frame; that surfaced as element 0 reading back as
// `20<<32 + 10` and a segfault on return once the overrun reached the saved
// return address. REX is dropped entirely when neither the data nor the index
// register is extended, which is the common case.
inline void emit_xor_zero(CodeBuffer& buf, Reg reg);

inline void emit_rbp_mov_narrow(CodeBuffer& buf, Reg data_reg, Reg addr_reg, int32_t disp,
                                int bytes, bool store, bool scaled,
                                bool zero_extend = true) {
    // An 8- or 16-bit load is a PARTIAL register write: `mov al, [mem]` writes
    // AL and leaves bits 8..63 of the destination exactly as they were. A
    // 32-bit load zero-extends for free, which is why only the 1- and 2-byte
    // element types were affected -- reading a stored 10 back printed
    // 12586541842954, the low byte correct and every bit above it leftover
    // garbage from whatever used that register last. Zero the destination first.
    // A store is unaffected: only the low bits of the source are written.
    if (zero_extend && !store && (bytes == 1 || bytes == 2)) emit_xor_zero(buf, data_reg);
    if (bytes != 1 && bytes != 2 && bytes != 4 && bytes != 8)
        throw std::logic_error("x86_encoder: element width " + std::to_string(bytes) +
                               " is not 1, 2, 4 or 8");
    const bool wide = bytes == 8;
    const bool byte_form = bytes == 1;
    const bool word_form = bytes == 2;
    // 0x8B/0x89 are specifically the 32-bit forms. A 16-bit operand needs the
    // 0x66 prefix and an 8-bit one needs the 0x8A/0x88 opcodes entirely -- reusing
    // 0x8B for a byte access assembled `mov eax, DWORD PTR [rbp-0x12c]` from
    // `mov al, BYTE PTR [rbp-0x12c]`, silently widening a 1-byte list element to
    // 4 and corrupting every element packed after it.
    //
    // spl/bpl/sil/dil have NO encoding unless a REX byte is present, and
    // al/cl/dl/bl have none WITH one, so REX is width-dependent here too.
    const bool byte_reg_needs_rex =
        byte_form && (data_reg == Reg::RSP || data_reg == Reg::RBP ||
                      data_reg == Reg::RSI || data_reg == Reg::RDI);
    // R and X do NOT depend on `wide`. Only W does. Gating R/X on `wide` made a
    // 4-byte access with an r8..r15 index emit REX with X clear, so the index
    // contributed only its low three bits and the access read a wild address --
    // the same failure the SSE path had. addr_reg is the SIB index when scaled
    // and the frame base when not, so X is only ever about an index.
    const bool rex_r = reg_is_extended(data_reg);
    const bool rex_x = scaled && reg_is_extended(addr_reg);
    if (word_form) emit_u8(buf, 0x66);
    if (wide || byte_reg_needs_rex || rex_r || rex_x)
        emit_u8(buf, static_cast<uint8_t>(0x40 | (wide ? 8 : 0) |
                                         (rex_r ? 4 : 0) | (rex_x ? 2 : 0)));
    emit_u8(buf, byte_form ? (store ? 0x88 : 0x8A) : (store ? 0x89 : 0x8B));
    if (scaled)
        emit_sib_tail(buf, data_reg, addr_reg, checked_sib_scale(bytes));
    else
        emit_u8(buf, static_cast<uint8_t>(0x80 | (reg_low3(data_reg) << 3) |
                                          reg_low3(Reg::RBP)));
    emit_disp32_le(buf, disp);
}

// 4.1. Narrow unscaled element access, same width contract as the scaled form.
inline void emit_load_rbp_narrow(CodeBuffer& buf, Reg dst, int32_t disp, int bytes,
                                 bool zero_extend = true) {
    emit_rbp_mov_narrow(buf, dst, Reg::RBP, disp, bytes, /*store=*/false, /*scaled=*/false,
                        zero_extend);
}

inline void emit_store_rbp_narrow(CodeBuffer& buf, Reg src, int32_t disp, int bytes) {
    emit_rbp_mov_narrow(buf, src, Reg::RBP, disp, bytes, /*store=*/true, /*scaled=*/false);
}

// mov dst, [rbp + disp + index*stride], at the element's own width
inline void emit_load_rbp_scaled(CodeBuffer& buf, Reg dst, Reg index_reg, int32_t disp,
                                 int stride = 8, bool zero_extend = true) {
    emit_rbp_mov_narrow(buf, dst, index_reg, disp, stride, /*store=*/false, /*scaled=*/true,
                        zero_extend);
}

// mov [rbp + disp + index*stride], src, at the element's own width
inline void emit_store_rbp_scaled(CodeBuffer& buf, Reg src, Reg index_reg, int32_t disp,
                                  int stride = 8) {
    emit_rbp_mov_narrow(buf, src, index_reg, disp, stride, /*store=*/true, /*scaled=*/true);
}

inline void emit_store_rbp_offset(CodeBuffer& buf, Reg src, int32_t offset, int bytes = 8) {
    if (bytes == 8) {
        emit_u8(buf, rex(true, src, Reg::RBP));
        emit_u8(buf, 0x89);
        emit_u8(buf, static_cast<uint8_t>(0x80 | (reg_low3(src) << 3) | reg_low3(Reg::RBP)));
        emit_disp32_le(buf, offset);
        return;
    }
    emit_rbp_mov_narrow(buf, src, Reg::RBP, offset, bytes, /*store=*/true, /*scaled=*/false);
}

// mov dst, [rbp + offset]   (load from a stack slot)
// Encoding: REX.W + 8B /r, ModRM(mod=10, reg=dst, rm=RBP), disp32
inline void emit_load_rbp_offset(CodeBuffer& buf, Reg dst, int32_t offset) {
    emit_u8(buf, rex(true, dst, Reg::RBP));
    emit_u8(buf, 0x8B);
    emit_u8(buf, static_cast<uint8_t>(0x80 | (reg_low3(dst) << 3) | reg_low3(Reg::RBP)));
    emit_disp32_le(buf, offset);
}

// 4.4. Register-indirect access: mov dst, [addr]. Every other memory form in
// this file is rbp-relative; these are the first that use an actual ADDRESS
// value in a register, which is the whole point of AddressOf. The pointer is
// a GP register produced by AddressOf -- a temp from kTempPool or a scratch
// register, never RSP/RBP -- but the encoding handles the full register file
// anyway rather than trusting that.
//
// mod=00 + rm=100 would decode as "SIB follows" and mod=00 + rm=101 as
// RIP-relative, so the three collision cases get their real form: RSP/R12 go
// through the no-index SIB byte (which names base RSP/R12 under REX.B), and an
// rm=5 base -- RBP or, under REX.B, R13 -- uses mod=01 with a zero disp8. Note
// that mod=00 + rm=101 is RIP-relative even when REX.B extends it to R13, so
// R13 needs the same escape RBP does; the allocator hands out R13 as a scratch
// register, so this is not a theoretical case.
inline void emit_load_reg_indirect_raw(CodeBuffer& buf, Reg data_reg, Reg addr_reg,
                                       int bytes, bool zero_extend = true) {
    if (bytes != 1 && bytes != 2 && bytes != 4 && bytes != 8)
        throw std::logic_error("x86_encoder: indirect element width " + std::to_string(bytes) +
                               " is not 1, 2, 4 or 8");
    // An 8- or 16-bit load is a partial register write: `mov al, [addr]`
    // leaves bits 8..63 of the destination untouched. Zero the destination
    // first, exactly as the rbp forms do for per-element access.
    if (zero_extend && (bytes == 1 || bytes == 2)) emit_xor_zero(buf, data_reg);
    const bool wide = bytes == 8;
    const bool byte_form = bytes == 1;
    const bool word_form = bytes == 2;
    const bool byte_reg_needs_rex =
        byte_form && (data_reg == Reg::RSP || data_reg == Reg::RBP ||
                      data_reg == Reg::RSI || data_reg == Reg::RDI);
    const uint8_t rm = reg_low3(addr_reg);
    const bool needs_sib = rm == 4;                                  // RSP/R12
    const bool fi_disp_escape = rm == 5;                             // RBP/R13
    if (word_form) emit_u8(buf, 0x66);
    if (wide || byte_reg_needs_rex || reg_is_extended(data_reg) || reg_is_extended(addr_reg))
        emit_u8(buf, static_cast<uint8_t>(0x40 | (wide ? 8 : 0) |
                                         (reg_is_extended(data_reg) ? 4 : 0) |
                                         (reg_is_extended(addr_reg) ? 1 : 0)));
    emit_u8(buf, byte_form ? 0x8A : 0x8B);
    if (needs_sib) {
        emit_u8(buf, static_cast<uint8_t>((reg_low3(data_reg) << 3) | 0x4));
        // scale=0, index=100 (no index), base=addr
        emit_u8(buf, static_cast<uint8_t>(0x20 | rm));
    } else if (fi_disp_escape) {
        emit_u8(buf, static_cast<uint8_t>(0x40 | (reg_low3(data_reg) << 3) | rm));
        emit_u8(buf, 0);   // disp8 = 0
    } else {
        emit_u8(buf, static_cast<uint8_t>(reg_low3(data_reg) << 3 | rm));
    }
}

// mov dst, [addr] at the pointee's own width (1/2/4 zero-extend, 8 wide).
inline void emit_load_reg_indirect(CodeBuffer& buf, Reg data_reg, Reg addr_reg, int bytes) {
    emit_load_reg_indirect_raw(buf, data_reg, addr_reg, bytes, /*zero_extend=*/true);
}

// push reg / pop reg. Encoding: [41] 50+r / [41] 58+r.
inline void emit_push_reg(CodeBuffer& buf, Reg reg) {
    if (reg_is_extended(reg)) emit_u8(buf, 0x41);
    emit_u8(buf, static_cast<uint8_t>(0x50 + reg_low3(reg)));
}

inline void emit_pop_reg(CodeBuffer& buf, Reg reg) {
    if (reg_is_extended(reg)) emit_u8(buf, 0x41);
    emit_u8(buf, static_cast<uint8_t>(0x58 + reg_low3(reg)));
}

// sub rsp, imm32 -- always uses the imm32 form (REX.W + 81 /5 id).
inline void emit_sub_rsp_imm32(CodeBuffer& buf, int32_t imm) {
    emit_u8(buf, 0x48);
    emit_u8(buf, 0x81);
    emit_u8(buf, static_cast<uint8_t>(0xC0 | (5 << 3) | reg_low3(Reg::RSP)));
    emit_disp32_le(buf, imm);
}

// add rsp, imm32 (REX.W + 81 /0 id). Used to release Windows shadow space.
inline void emit_add_rsp_imm32(CodeBuffer& buf, int32_t imm) {
    emit_u8(buf, 0x48);
    emit_u8(buf, 0x81);
    emit_u8(buf, static_cast<uint8_t>(0xC0 | (0 << 3) | reg_low3(Reg::RSP)));
    emit_disp32_le(buf, imm);
}

// Standard function prologue: push rbp, mov rbp, rsp, sub rsp, frame_size.
inline void emit_prologue(CodeBuffer& buf, int32_t frame_size) {
    emit_push_reg(buf, Reg::RBP);
    emit_mov_reg_reg(buf, Reg::RBP, Reg::RSP);
    if (frame_size > 0) {
        emit_sub_rsp_imm32(buf, frame_size);
    }
}

// Standard function epilogue: mov rsp, rbp, pop rbp. Caller emits
// `ret` separately.
inline void emit_epilogue(CodeBuffer& buf) {
    emit_mov_reg_reg(buf, Reg::RSP, Reg::RBP);
    emit_pop_reg(buf, Reg::RBP);
}

// and dst, src  (64-bit register to register)
// Encoding: [REX] 21 /r   (AND r/m64, r64 -- src is the "reg" field,
// dst is the "r/m" field). Matches emit_add_reg_reg / emit_sub_reg_reg.
//
// NOTE this is the only ALU reg-reg emitter here WITHOUT REX.W, i.e. it is a
// 32-bit and that zero-extends into the high half. That is deliberate and
// currently harmless: its only callers AND together two 0/1 booleans from
// setcc, where dropping bits 32..63 changes nothing. It is NOT a general
// 64-bit and -- do not reach for it from BitAnd, where a 32-bit and on
// -1 & mask would return the mask instead of the real 64-bit result. Use
// emit_and_reg_reg64.
inline void emit_and_reg_reg(CodeBuffer& buf, Reg dst, Reg src) {
    if (reg_is_extended(dst) || reg_is_extended(src)) emit_u8(buf, rex(false, src, dst));
    emit_u8(buf, 0x21);
    emit_u8(buf, modrm_reg_reg(src, dst));
}

// ---- 64-bit bitwise ALU (REX.W set) ------------------------------------
// These are the ones BitAnd/BitOr/BitXor use. REX.W is not optional: without
// it the op is 32-bit and writes a zero-extended result, silently discarding
// the high half of any negative operand.

inline void emit_and_reg_reg64(CodeBuffer& buf, Reg dst, Reg src) {
    emit_u8(buf, rex(true, src, dst));
    emit_u8(buf, 0x21);
    emit_u8(buf, modrm_reg_reg(src, dst));
}

// or dst, src  (64-bit). Encoding: REX.W + 09 /r.
inline void emit_or_reg_reg64(CodeBuffer& buf, Reg dst, Reg src) {
    emit_u8(buf, rex(true, src, dst));
    emit_u8(buf, 0x09);
    emit_u8(buf, modrm_reg_reg(src, dst));
}

// xor dst, src  (64-bit). Encoding: REX.W + 31 /r.
inline void emit_xor_reg_reg64(CodeBuffer& buf, Reg dst, Reg src) {
    emit_u8(buf, rex(true, src, dst));
    emit_u8(buf, 0x31);
    emit_u8(buf, modrm_reg_reg(src, dst));
}

// ---- shifts --------------------------------------------------------------
// The group-1 shift immediates are /4 shl, /5 shr (LOGICAL), /7 sar
// (arithmetic); /6 is undefined. The imm8 form is C1 /digit ib and the
// variable form is D3 /digit, which reads the count from CL and takes no
// immediate.
//
// The imm8 form is worth having: it encodes the count in the instruction
// itself, so a literal shift needs no scratch register, no move into CL, and
// no save/restore around it. The D3 form is the only option for a count that
// is not known at compile time, and it is the one that requires RCX to be
// preserved by the caller -- there is no encoding of a variable shift that
// uses any other register.

inline void emit_shl_reg_imm8(CodeBuffer& buf, Reg dst, uint8_t imm) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0xC1);
    emit_u8(buf, static_cast<uint8_t>(0xE0 | reg_low3(dst)));
    emit_u8(buf, imm);
}

// shl dst, 1  -- the D1 /4 short form, two bytes shorter than C1 /4 ib 01.
// Worth having because `x << 1` is one of the most common shifts there is.
// GNU as picks this form itself, which is what the encoder-vs-as cross-check
// notices when the C1 form is used for a count of 1.
inline void emit_shl_reg_1(CodeBuffer& buf, Reg dst) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0xD1);
    emit_u8(buf, static_cast<uint8_t>(0xE0 | reg_low3(dst)));
}

inline void emit_sar_reg_1(CodeBuffer& buf, Reg dst) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0xD1);
    emit_u8(buf, static_cast<uint8_t>(0xF8 | reg_low3(dst)));
}

// shr dst, imm8  -- LOGICAL right shift, REX.W + C1 /5 ib.
//
// The 4.3 dict hash needs this and `>>` does not: taking the top bits of the
// Fibonacci product is a logical shift, and the arithmetic /7 form would
// replicate the sign bit of a negative key and pull the whole table's layout
// into the top bit instead of the low one. The /5 form is emitted here ONLY for
// the hash, and it is deliberately a separate function from any operator so
// nothing reaches for it by mistake and gets Python's >> semantics.
inline void emit_shr_reg_imm8(CodeBuffer& buf, Reg dst, uint8_t imm) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0xC1);
    emit_u8(buf, static_cast<uint8_t>(0xE8 | reg_low3(dst)));
    emit_u8(buf, imm);
}

// NOTE there is deliberately no emit_shr_reg_imm8 here. The /5 form is a
// LOGICAL shift and Python's >> is arithmetic, so emitting /5 for `>>` is
// simply the wrong instruction: -8 >> 1 would be 0x7FFFFFFFFFFFFFFC instead of
// -4. `>>` uses the existing emit_sar_reg_imm8 above (/7).

// shl dst, cl  (count in CL). Encoding: REX.W + D3 /4. RCX is an implicit
// operand here, so the caller owns preserving it.
inline void emit_shl_reg_cl(CodeBuffer& buf, Reg dst) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0xD3);
    emit_u8(buf, static_cast<uint8_t>(0xE0 | reg_low3(dst)));
}

// sar dst, cl  (arithmetic, sign-propagating). Encoding: REX.W + D3 /7.
inline void emit_sar_reg_cl(CodeBuffer& buf, Reg dst) {
    emit_u8(buf, rex(true, Reg::RAX, dst));
    emit_u8(buf, 0xD3);
    emit_u8(buf, static_cast<uint8_t>(0xF8 | reg_low3(dst)));
}

// setcc_opcode -- the 0F 9x byte for a condition.
//
// The two tables the CPU keeps are offset, and only for their first half:
//   Jcc    0F 80-8F   (je, jne, jl, jlE, jg, jge, jle, ja, ...)
//   SETcc  0F 90-9F   (sete, setne, setl, ..., setp, setnp)
// so within 0x80-0x8F the SETcc byte is the Jcc byte plus 0x10. The parity
// conditions break that rule: jp/jnp are 0F 9A/0F 9B, and setp/setnp are
// *also* 0F 9A/0F 9B. Adding 0x10 to a parity condition would land on
// 0F AA/0F AB, which are unrelated instructions (stosb, stosd) -- the
// program would silently do something else entirely. Hence the split
// rather than a blanket +0x10.
inline uint8_t setcc_opcode(Cond cond) {
    uint8_t c = static_cast<uint8_t>(cond);
    return static_cast<uint8_t>(c < 0x90 ? c + 0x10 : c);
}

// setcc dst_low_byte -- sets the low 8 bits of dst to 0 or 1 based
// on the flags from a preceding cmp/test. Encoding: [REX] 0F 9x /0.
// A REX prefix is always emitted: it is required for r8b-r15b and
// makes indices 4-7 mean spl/bpl/sil/dil instead of ah/ch/dh/bh.
//
// Do not confuse this with branching. In the 0F 9x range the byte alone
// does not say which instruction it is: 0F 9A is `setp r/m8` when the
// following byte is a register ModRM, and `jp rel32` otherwise, and the
// displacement of a forward jump very often does look like a
// non-register ModRM (0x0d is mod=00 rm=101, RIP-relative). Emitting a
// parity Jcc with a rel32 is therefore a trap; spell the condition out
// with setcc + and_reg_reg instead.
inline void emit_setcc(CodeBuffer& buf, Cond cond, Reg dst_low_byte) {
    emit_u8(buf, static_cast<uint8_t>(0x40 | (reg_is_extended(dst_low_byte) ? 1 : 0)));
    emit_u8(buf, 0x0F);
    emit_u8(buf, setcc_opcode(cond));
    emit_u8(buf, static_cast<uint8_t>(0xC0 | reg_low3(dst_low_byte)));
}

// movzx dst64, src_low_byte -- zero-extends an 8-bit value into a
// full 64-bit register. Encoding: REX.W + 0F B6 /r.
inline void emit_movzx_reg_reg8(CodeBuffer& buf, Reg dst64, Reg src_low_byte) {
    emit_u8(buf, rex(true, dst64, src_low_byte));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0xB6);
    emit_u8(buf, modrm_reg_reg(dst64, src_low_byte));
}

// xor reg32, reg32 (self-xor to zero a register). Encoding: [REX] 31 /r.
// Verified: xor eax, eax = 31 c0.
inline void emit_xor_zero(CodeBuffer& buf, Reg reg) {
    if (reg_is_extended(reg)) emit_u8(buf, rex(false, reg, reg));
    emit_u8(buf, 0x31);
    emit_u8(buf, modrm_reg_reg(reg, reg));
}

// call reg (indirect call through a register holding a runtime
// address -- used to call host-process functions like printf).
// Encoding: [41] FF /2 (CALL r/m64). Verified: call rax = ff d0.
inline void emit_call_reg(CodeBuffer& buf, Reg reg) {
    if (reg_is_extended(reg)) emit_u8(buf, 0x41);
    emit_u8(buf, 0xFF);
    emit_u8(buf, static_cast<uint8_t>(0xC0 | (2 << 3) | reg_low3(reg)));
}

// ---------------------------------------------------------------------
// SSE2 scalar double-precision.
//
// A genuinely separate opcode family from everything above: these use
// the F2 0F mandatory prefix for packed-vs-scalar-double
// disambiguation, and XMM register numbers run 0-15, so the low-3 bits
// plus a REX.R extension describe the whole range. XMM0-XMM7 need no
// REX at all; XMM8-XMM15 need REX.R (bit 2) on the reg field.
//
// There is no "mov xmm, imm64" in x86-64. A double literal is always
// loaded from memory, so ConstFloat materializes into a constant pool
// and loads from [rip + disp32] -- see compile_function.h.
// ---------------------------------------------------------------------

enum class Xmm : uint8_t { XMM0 = 0, XMM1, XMM2, XMM3, XMM4, XMM5, XMM6, XMM7,
                           XMM8, XMM9, XMM10, XMM11, XMM12, XMM13, XMM14, XMM15,
                           // Not a register. Passed to rex_sse for the rm
                           // field of an instruction whose rm operand is
                           // memory, where no REX.B bit applies. The encoding
                           // helpers never accept it as a register operand:
                           // xmm_low3() refuses it, and every emitter below
                           // reads each register operand through xmm_low3().
                           none = 0xFF };

// The ModRM/opcode low three bits of a register operand.
//
// Xmm::none is 0xFF, whose low three bits are 7, so without this check a `none`
// that reached an emitter as a REGISTER operand would silently encode as xmm7 --
// the same hidden-register failure the xmm_is_extended fix below closed for the
// REX bits, one step further along. Every emitter reads each register operand
// through here, and `none` is only ever legitimate as rex_sse's rm argument
// (which never calls this), so refusing it here covers every emitter at once and
// turns a silently wrong program into an error that names the problem.
inline uint8_t xmm_low3(Xmm r) {
    if (r == Xmm::none) {
        throw std::logic_error(
            "x86_encoder: Xmm::none used as a register operand (it is a 'no register' "
            "sentinel for memory operands, and would encode as xmm7)");
    }
    return static_cast<uint8_t>(static_cast<uint8_t>(r) & 7);
}
inline bool xmm_is_extended(Xmm r) {
    // Xmm::none is 0xFF, so a bare `>= 8` said TRUE for it -- and rex_sse(x) with
    // a memory rm operand defaults rm to none. That set a spurious REX.B, which
    // does not just waste a prefix bit: it reinterprets the base register, so
    // [rbp+disp] was fetched from [r13+disp]. A wild address, i.e. SIGBUS.
    return static_cast<uint8_t>(r) >= 8 && r != Xmm::none;
}

// The SSE scaled forms need REX.R for an extended XMM reg field and REX.X for
// an extended index SIMULTANEOUSLY, and must emit the prefix whenever EITHER is
// extended -- an earlier version emitted it only for the XMM, so an index in
// r8..r15 lost its X bit and addressed the low three bits of the register. That
// is not a decoding failure; it reads a wildly out-of-frame address and faults.
inline uint8_t rex3_xmm(Xmm reg_field, Reg index_field) {
    return static_cast<uint8_t>(0x40 |
                                (xmm_is_extended(reg_field) ? 4 : 0) |
                                (reg_is_extended(index_field) ? 2 : 0));
}


// REX for an SSE instruction, given the register that lands in ModRM.reg
// and, when ModRM.rm is a register rather than memory, the one in rm.
//
// Both extension bits matter. REX.R extends reg and REX.B extends rm, and
// they are independent: an instruction reading xmm14 as its rm operand needs
// REX.B set even when its reg operand is xmm0. kScratchFloat (XMM15) and
// kScratchFloatB (XMM14) are both in the extended range, so every reg-reg
// float op here sets both bits. Leaving B clear makes the CPU read the low
// three bits as xmm6, silently decoding a different register.
//
// Pass `none` for the rm argument when rm is a memory operand (ModRM rm=101
// RIP-relative, or mod=10 rbp-relative); memory operands are never extended.
inline uint8_t rex_sse(Xmm reg_field, Xmm rm_field = Xmm::none) {
    return static_cast<uint8_t>(0x40 | (xmm_is_extended(reg_field) ? 4 : 0) |
                                (xmm_is_extended(rm_field) ? 1 : 0));
}

// F2 0F <opcode> /r, reg <- reg op reg. Covers addsd/subsd/mulsd/divsd,
// which differ only in the opcode byte. Verified: addsd xmm0, xmm1 =
// f2 0f 58 c1.
inline void emit_sse_sd_op(CodeBuffer& buf, uint8_t opcode, Xmm dst, Xmm src) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(dst) || xmm_is_extended(src)) emit_u8(buf, rex_sse(dst, src));
    emit_u8(buf, 0x0F);
    emit_u8(buf, opcode);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(dst)), static_cast<Reg>(xmm_low3(src))));
}

inline void emit_addsd(CodeBuffer& buf, Xmm dst, Xmm src) { emit_sse_sd_op(buf, 0x58, dst, src); }
inline void emit_subsd(CodeBuffer& buf, Xmm dst, Xmm src) { emit_sse_sd_op(buf, 0x5C, dst, src); }
inline void emit_mulsd(CodeBuffer& buf, Xmm dst, Xmm src) { emit_sse_sd_op(buf, 0x59, dst, src); }
inline void emit_divsd(CodeBuffer& buf, Xmm dst, Xmm src) { emit_sse_sd_op(buf, 0x5E, dst, src); }

// roundsd dst, src, imm8 -- round the double in src to integral, using the
// current rounding mode selected by imm8. Used only to get truncation for
// the float modulo sequence, so imm8 is fixed at 0x0B:
//   imm8[2:0] = 3   round toward zero
//   imm8[3]   = 1   suppress the precision (inexact) exception
// Encoding: 66 0F 3A 0B /r ib. Needs the mandatory 0x3A escape byte, unlike
// the arithmetic ops above. Verified: roundsd xmm12, xmm0, 0x0b =
// 66 44 0f 3a 0b e0 0b (the 0x0B opcode byte was the missing piece).
inline void emit_roundsd_imm8(CodeBuffer& buf, Xmm dst, Xmm src, uint8_t imm) {
    emit_u8(buf, 0x66);
    if (xmm_is_extended(dst) || xmm_is_extended(src)) emit_u8(buf, rex_sse(dst, src));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x3A);
    emit_u8(buf, 0x0B);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(dst)), static_cast<Reg>(xmm_low3(src))));
    emit_u8(buf, imm);
}

inline constexpr uint8_t kRoundTowardZeroSuppressInexact = 0x0B;

// comisd xmm, xmm -- sets ZF/PF/CF like cmp, raising Invalid on either NaN.
// Encoding: 66 0F 2F /r. Verified: comisd xmm0, xmm1 = 66 0f 2f c1.
// Used for both float ordering compares and the Div-by-zero check.
inline void emit_comisd(CodeBuffer& buf, Xmm lhs, Xmm rhs) {
    emit_u8(buf, 0x66);
    if (xmm_is_extended(lhs) || xmm_is_extended(rhs)) emit_u8(buf, rex_sse(lhs, rhs));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x2F);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(lhs)), static_cast<Reg>(xmm_low3(rhs))));
}

// ucomisd xmm, xmm -- the quiet variant: identical ZF/PF/CF result, but it
// raises Invalid only for QNaN, so an SNaN operand does not fault.
// Encoding: 66 0F 2E /r. Prefers ucomisd over comisd for the ordering
// compares because it never traps on a signalling NaN.
inline void emit_ucomisd(CodeBuffer& buf, Xmm lhs, Xmm rhs) {
    emit_u8(buf, 0x66);
    if (xmm_is_extended(lhs) || xmm_is_extended(rhs)) emit_u8(buf, rex_sse(lhs, rhs));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x2E);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(lhs)), static_cast<Reg>(xmm_low3(rhs))));
}

// comisd against an all-zero double held in memory: comisd xmm, [rip+d32].
// Encoding: 66 0F 2F /r with ModRM(mod=00, reg=lhs, rm=101), disp32.
// The rm=101 + mod=00 combination is RIP-relative, which is what makes
// this position-independent -- the JIT emits code into an mmap'd buffer
// whose address it does not control.
inline void emit_comisd_zero(CodeBuffer& buf, Xmm lhs, int32_t disp32) {
    emit_u8(buf, 0x66);
    if (xmm_is_extended(lhs)) emit_u8(buf, rex_sse(lhs));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x2F);
    emit_u8(buf, static_cast<uint8_t>(0x05 | (xmm_low3(lhs) << 3)));  // mod=00 rm=101
    emit_disp32_le(buf, disp32);
}

// movsd xmm, [rip + disp32] -- load a double from the embedded constant
// pool. Encoding: F2 0F 10 /r, ModRM(mod=00, reg=dst, rm=101), disp32.
// The disp32 is emitted as a zero placeholder and patched by the caller
// once the pool's final position is known; see movsd_rip_disp_offset.
// The rm=101 + mod=00 combination is RIP-relative, which is what makes
// this position-independent -- the JIT emits code into an mmap'd buffer
// whose address it does not control.
inline void emit_movsd_xmm_rip(CodeBuffer& buf, Xmm dst) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(dst)) emit_u8(buf, rex_sse(dst));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x10);
    emit_u8(buf, static_cast<uint8_t>(0x05 | (xmm_low3(dst) << 3)));  // mod=00 rm=101
    emit_disp32_le(buf, 0);
}

// movsd [rip + disp32], xmm -- store a double to the constant pool or a
// code-embedded data slot. Encoding: F2 0F 11 /r, ModRM(mod=00,
// reg=src, rm=101), disp32. Like emit_movsd_xmm_rip, the disp32 is a zero
// placeholder the caller patches once the target position is final.
inline void emit_movsd_mem_rip(CodeBuffer& buf, Xmm src) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(src)) emit_u8(buf, rex_sse(src));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x11);
    emit_u8(buf, static_cast<uint8_t>(0x05 | (xmm_low3(src) << 3)));  // mod=00 rm=101
    emit_disp32_le(buf, 0);
}

// Returns the offset of the RIP-relative disp32 that emit_movsd_xmm_rip or
// emit_movsd_mem_rip just wrote, so the caller can patch it once the pool's
// final position is known.
//
// This must be called IMMEDIATELY after the emitter, while the disp32 is
// still the last thing in the buffer. It is not a query about an arbitrary
// earlier instruction -- it is a "where did that just-written instruction
// put its displacement" helper, and the two are only equivalent because the
// emitters put nothing after the disp32.
inline size_t movsd_rip_disp_offset(CodeBuffer& buf) { return buf.size() - 4; }

// movsd xmm, xmm -- register-to-register double move. Encoding:
// F2 0F 10 /r. This is how a double gets from the register it was
// computed in to the register it is consumed from.
inline void emit_movsd_xmm_xmm(CodeBuffer& buf, Xmm dst, Xmm src) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(dst) || xmm_is_extended(src)) emit_u8(buf, rex_sse(dst, src));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x10);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(dst)), static_cast<Reg>(xmm_low3(src))));
}

// 4.4. movsd xmm, [addr] -- load a double through a pointer value in a
// register. Encoding: F2 0F 10 /r, ModRM(mod=00, reg=dst, rm=addr), with
// the same two collision-register escapes as the integer indirect path
// (RSP/R12 need the no-index SIB byte, and an rm=5 base -- RBP or, under
// REX.B, R13 -- uses mod=01 with a zero disp8, because mod=00 + rm=101 is
// RIP-relative even when extended).
inline void emit_movsd_xmm_mem_reg(CodeBuffer& buf, Xmm dst, Reg addr_reg) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(dst) || reg_is_extended(addr_reg))
        emit_u8(buf, static_cast<uint8_t>(0x40 |
                                         (xmm_is_extended(dst) ? 4 : 0) |
                                         (reg_is_extended(addr_reg) ? 1 : 0)));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x10);
    const uint8_t rm = reg_low3(addr_reg);
    const bool needs_sib = rm == 4;
    const bool fi_disp_escape = rm == 5;
    if (needs_sib) {
        emit_u8(buf, static_cast<uint8_t>((xmm_low3(dst) << 3) | 0x4));
        emit_u8(buf, static_cast<uint8_t>(0x20 | rm));
    } else if (fi_disp_escape) {
        emit_u8(buf, static_cast<uint8_t>(0x40 | (xmm_low3(dst) << 3) | rm));
        emit_u8(buf, 0);   // disp8 = 0
    } else {
        emit_u8(buf, static_cast<uint8_t>(xmm_low3(dst) << 3 | rm));
    }
}

// movsd xmm, [rbp + disp32] -- load a double from a frame slot. The XMM
// counterpart of emit_load_rbp_offset: same rbp-relative addressing, F2
// 0F 10 /r with ModRM(mod=10, reg=dst, rm=RBP).
// 4.1. Scaled-index forms of the two movsd helpers below, for a
// list[float[64]] element. The r/m field selects SIB (100) and the base is
// still RBP, so -- exactly as in the integer pair -- mod=10 is used
// unconditionally to sidestep the "mod=00 + base=RBP means RIP-relative" rule.
inline void emit_movsd_xmm_rbp_scaled(CodeBuffer& buf, Xmm dst, Reg index_reg, int32_t disp,
int stride = 8) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(dst) || reg_is_extended(index_reg)) emit_u8(buf, rex3_xmm(dst, index_reg));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x10);
    emit_u8(buf, static_cast<uint8_t>(0x80 | (xmm_low3(dst) << 3) | 0x4));
    emit_u8(buf, static_cast<uint8_t>(checked_sib_scale(stride) | (reg_low3(index_reg) << 3) |
                                      reg_low3(Reg::RBP)));
    emit_disp32_le(buf, disp);
}

inline void emit_movsd_rbp_scaled(CodeBuffer& buf, Xmm src, Reg index_reg, int32_t disp,
 int stride = 8) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(src) || reg_is_extended(index_reg)) emit_u8(buf, rex3_xmm(src, index_reg));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x11);
    emit_u8(buf, static_cast<uint8_t>(0x80 | (xmm_low3(src) << 3) | 0x4));
    emit_u8(buf, static_cast<uint8_t>(checked_sib_scale(stride) | (reg_low3(index_reg) << 3) |
                                      reg_low3(Reg::RBP)));
    emit_disp32_le(buf, disp);
}

inline void emit_movsd_xmm_rbp(CodeBuffer& buf, Xmm dst, int32_t offset) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(dst)) emit_u8(buf, rex_sse(dst));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x10);
    emit_u8(buf, static_cast<uint8_t>(0x80 | (xmm_low3(dst) << 3) | reg_low3(Reg::RBP)));
    emit_disp32_le(buf, offset);
}

// movsd [rbp + disp32], xmm -- store a double into a frame slot.
inline void emit_movsd_rbp_mem(CodeBuffer& buf, Xmm src, int32_t offset) {
    emit_u8(buf, 0xF2);
    if (xmm_is_extended(src)) emit_u8(buf, rex_sse(src));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x11);
    emit_u8(buf, static_cast<uint8_t>(0x80 | (xmm_low3(src) << 3) | reg_low3(Reg::RBP)));
    emit_disp32_le(buf, offset);
}

// cvtsi2sd xmm, r64 -- convert a signed 64-bit integer to double.
// Encoding: F2 REX.W 0F 2A /r. Required because the type lattice
// promotes int+float to Float, so an int operand of a mixed expression
// must be widened before the SSE op. Verified: cvtsi2sd xmm0, rax =
// f2 48 0f 2a c0.
inline void emit_cvtsi2sd(CodeBuffer& buf, Xmm dst, Reg src64) {
    emit_u8(buf, 0xF2);
    // REX.W, REX.R for an extended xmm dst (ModRM.reg), REX.B for an extended GP src.
    emit_u8(buf, static_cast<uint8_t>(0x48 | (xmm_is_extended(dst) ? 4 : 0) | (reg_is_extended(src64) ? 1 : 0)));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x2A);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(dst)), src64));
}

// cvttsd2si r64, xmm -- convert a double to a signed 64-bit integer,
// truncating toward zero. Encoding: F2 REX.W 0F 2C /r.
inline void emit_cvttsd2si(CodeBuffer& buf, Reg dst64, Xmm src) {
    emit_u8(buf, 0xF2);
    // REX.W, REX.R for an extended GP dst (ModRM.reg), REX.B for an extended xmm src (ModRM.rm).
    emit_u8(buf, static_cast<uint8_t>(0x48 | (reg_is_extended(dst64) ? 4 : 0) | (xmm_is_extended(src) ? 1 : 0)));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x2C);
    emit_u8(buf, modrm_reg_reg(dst64, static_cast<Reg>(xmm_low3(src))));
}

// xorpd xmm, xmm -- zero an XMM register. Unlike xor on GP registers
// this needs no REX.W: SSE operands are 128 bits, already full size.
// Encoding: 66 0F 57 /r. Verified: xorpd xmm0, xmm0 = 66 0f 57 c0.
// Used to materialize 0.0 for the Div-by-zero check.
inline void emit_xorpd_zero(CodeBuffer& buf, Xmm reg) {
    emit_u8(buf, 0x66);
    // reg is BOTH the reg and rm operand, so an extended register needs R and B.
    if (xmm_is_extended(reg)) emit_u8(buf, rex_sse(reg, reg));
    emit_u8(buf, 0x0F);
    emit_u8(buf, 0x57);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(reg)), static_cast<Reg>(xmm_low3(reg))));
}

// =====================================================================
// 4.5  VEX / AVX2
//
// The first VEX-prefixed opcode family, added for the vectorizing pass.
// Everything below was checked byte-for-byte against GNU `as` via
// encoder_asm_dump.cpp + tools/check_encoder_vs_as.py; the probe decodes
// that pinned the two VEX prefix bytes down:
//
//   C4           -- 3-byte VEX marker
//   byte1:      (~R)<<7 | (~X)<<6 | (~B)<<5 | mmap(5 bits)
//                   mmap: 1 = 0F, 2 = 0F38, 3 = 0F3A
//   byte2:      ((~vvvv) & 0xF)<<3 | L<<2 | pp(2 bits)
//                   pp: 0 = none, 1 = 66, 2 = F3, 3 = F2
//
// Operand order decode for the 3-operand dword family (vpaddd & friends):
// ModRM.reg is the DESTINATION (extended by VEX.R), ModRM.rm is the second
// SOURCE register or memory (extended by VEX.B, index by VEX.X) and
// VEX.vvvv is the FIRST source. L selects 256-bit (ymm) vs 128-bit (xmm) --
// not REX.W, which stays 0 for dword ops.
//
// The integer vector path also owns two safety rules the scalar path can
// now state in bytes instead of words:
//   - a 4-byte element is `vmovdqu ymm, [rbp+disp+i*4]`: the SIB scale is
//     BOTH the address stride and the operand width, so the stride 4 is
//     what makes the whole thing int[32], no REX.W anywhere.
//   - GNU as uses a disp8 with mod=01 whenever the displacement fits a
//     signed byte (mod=10 disp32 otherwise, since mod=00 + SIB base RBP
//     would mean no base register at all), so the mem form below mirrors
//     that exactly or the assembler comparison would pin every short
//     displacement as "different".
// ---------------------------------------------------------------------

enum : uint8_t { kVexPpNone = 0, kVexPp66 = 1, kVexPpF3 = 2, kVexPpF2 = 3 };

// emit_vex3's `vvvv` argument is the first-source REGISTER NUMBER; the helper
// stores it inverted. For an instruction with no first source the field must
// read 1111 in the byte, i.e. the argument must be register 0 (~0 & 0xF). Passing
// 0xF here stores 0000 -- "ymm15" -- which objdump decodes as (bad) and the CPU
// rejects with #UD for every one-source op (vmovdqu, vpbroadcastd, vextracti128,
// vpshufd, vmovd).
constexpr uint8_t kVexNoSource = 0;

inline void emit_vex3(CodeBuffer& buf, uint8_t map, bool wide, uint8_t pp,
                      bool r_ext, bool x_ext, bool b_ext, uint8_t vvvv) {
    emit_u8(buf, 0xC4);
    emit_u8(buf, static_cast<uint8_t>(((r_ext ? 0 : 1) << 7) |
                                      ((x_ext ? 0 : 1) << 6) |
                                      ((b_ext ? 0 : 1) << 5) |
                                      (map & 0x1F)));
    emit_u8(buf, static_cast<uint8_t>(((~vvvv & 0xF) << 3) | (wide ? 4 : 0) | (pp & 3)));
}

// vzeroupper. 2.3-byte-prefixed no-operand opcode (VEX.128.0F 77). Must
// precede every `ret` and every `call` in a function that touched a ymm
// register; see tools/check_vex_transitions.py. Verified: C5 F8 77.
inline void emit_vzeroupper(CodeBuffer& buf) {
    emit_u8(buf, 0xC5);
    emit_u8(buf, 0xF8);
    emit_u8(buf, 0x77);
}

// mod = 01 (disp8) when the displacement fits a signed byte, else mod = 10
// (disp32). SIB base is always RBP.
inline void emit_sib_disp(CodeBuffer& buf, Reg dst, Reg index, uint8_t scale,
                          int32_t disp) {
    if (disp >= -128 && disp <= 127) {
        emit_u8(buf, static_cast<uint8_t>(0x40 | (reg_low3(dst) << 3) | 0x4));
        emit_u8(buf, static_cast<uint8_t>(scale | (reg_low3(index) << 3) | reg_low3(Reg::RBP)));
        emit_u8(buf, static_cast<uint8_t>(disp));
    } else {
        emit_u8(buf, static_cast<uint8_t>(0x80 | (reg_low3(dst) << 3) | 0x4));
        emit_u8(buf, static_cast<uint8_t>(scale | (reg_low3(index) << 3) | reg_low3(Reg::RBP)));
        emit_disp32_le(buf, disp);
    }
}

// 3-operand dword integer op whose second source is a register.
// vpaddd ymm_d, ymm_s1, ymm_s2 == C5 FD FE C1 (map 0F, opcode FE, pp 66).
// Extended-register form vpaddd ymm8,ymm9,ymm10 == C4 41 31 FE C2.
inline void emit_vex_binop_reg(CodeBuffer& buf, uint8_t map, uint8_t opcode,
                               bool wide, Xmm dst, Xmm src1, Xmm src2) {
    emit_vex3(buf, map, wide, kVexPp66, xmm_is_extended(dst), false,
              xmm_is_extended(src2), static_cast<uint8_t>(src1));
    emit_u8(buf, opcode);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(dst)),
                               static_cast<Reg>(xmm_low3(src2))));
}

// The same op with a memory second source: [rbp + index*stride + disp].
inline void emit_vex_binop_mem(CodeBuffer& buf, uint8_t map, uint8_t opcode,
                               bool wide, Xmm dst, Xmm src1, Reg index,
                               int32_t disp, int stride) {
    emit_vex3(buf, map, wide, kVexPp66, xmm_is_extended(dst),
              reg_is_extended(index), false, static_cast<uint8_t>(src1));
    emit_u8(buf, opcode);
    emit_sib_disp(buf, static_cast<Reg>(xmm_low3(dst)), index,
                  checked_sib_scale(stride), disp);
}

// vpaddd: map 0F, opcode FE. vpsubd: map 0F, opcode FA.
// vpmulld: map 0F38, opcode 40 (verified: vpmulld ymm0,ymm0,ymm1 = C4 E2 7D 40 C1).
// vpxor: map 0F, opcode EF.
inline void emit_vpaddd_reg(CodeBuffer& buf, Xmm d, Xmm s1, Xmm s2) {
    emit_vex_binop_reg(buf, 1, 0xFE, true, d, s1, s2);
}
inline void emit_vpaddd_mem(CodeBuffer& buf, Xmm d, Xmm s1, Reg idx, int32_t disp, int stride) {
    emit_vex_binop_mem(buf, 1, 0xFE, true, d, s1, idx, disp, stride);
}
inline void emit_vpaddd_xmm(CodeBuffer& buf, Xmm d, Xmm s1, Xmm s2) {
    emit_vex_binop_reg(buf, 1, 0xFE, false, d, s1, s2);
}
inline void emit_vpsubd_reg(CodeBuffer& buf, Xmm d, Xmm s1, Xmm s2) {
    emit_vex_binop_reg(buf, 1, 0xFA, true, d, s1, s2);
}
inline void emit_vpsubd_mem(CodeBuffer& buf, Xmm d, Xmm s1, Reg idx, int32_t disp, int stride) {
    emit_vex_binop_mem(buf, 1, 0xFA, true, d, s1, idx, disp, stride);
}
inline void emit_vpmulld_reg(CodeBuffer& buf, Xmm d, Xmm s1, Xmm s2) {
    emit_vex_binop_reg(buf, 2, 0x40, true, d, s1, s2);
}
inline void emit_vpmulld_mem(CodeBuffer& buf, Xmm d, Xmm s1, Reg idx, int32_t disp, int stride) {
    emit_vex_binop_mem(buf, 2, 0x40, true, d, s1, idx, disp, stride);
}
inline void emit_vpxor_reg(CodeBuffer& buf, Xmm d, Xmm s1, Xmm s2) {
    emit_vex_binop_reg(buf, 1, 0xEF, true, d, s1, s2);
}
inline void emit_vpxor_xmm(CodeBuffer& buf, Xmm d, Xmm s1, Xmm s2) {
    emit_vex_binop_reg(buf, 1, 0xEF, false, d, s1, s2);
}

// vmovdqu ymm, [rbp + index*stride + disp] -- unaligned 256-bit load.
// pp is the mandatory F3 for the 0F 6F/7F opcodes. Verified:
//   vmovdqu ymm8,[rbp+r15*4+0x40] = C4 21 7E 6F 44 BD 40   (disp8 form)
//   vmovdqu [rbp+r8*4-0x40],ymm12 = C4 21 7E 7F 64 85 C0
// A stride other than 1/2/4/8 has no SIB scale and is rejected up front.
inline void emit_vmovdqu_ymm(CodeBuffer& buf, Xmm dst, Reg index, int32_t disp,
                             int stride = 4) {
    emit_vex3(buf, 1, true, kVexPpF3, xmm_is_extended(dst),
              reg_is_extended(index), false, kVexNoSource);
    emit_u8(buf, 0x6F);
    emit_sib_disp(buf, static_cast<Reg>(xmm_low3(dst)), index,
                  checked_sib_scale(stride), disp);
}

inline void emit_vmovdqu_ymm_mem(CodeBuffer& buf, Xmm src, Reg index, int32_t disp,
                                 int stride = 4) {
    emit_vex3(buf, 1, true, kVexPpF3, xmm_is_extended(src),
              reg_is_extended(index), false, kVexNoSource);
    emit_u8(buf, 0x7F);
    emit_sib_disp(buf, static_cast<Reg>(xmm_low3(src)), index,
                  checked_sib_scale(stride), disp);
}

// vpbroadcastd ymm, dword ptr [rip + disp32] -- the AVX2 way to get a
// compile-time int32 constant into all 8 lanes. map 0F38, opcode 58, pp 66.
// Verified (pool form): vpbroadcastd ymm2,[rip+0x300] = C4 E2 7D 58 15 <d32>.
// The disp32 is a zero placeholder; call vpbroadcastd_rip_disp_offset()
// immediately after to patch it once the pool's position is known, exactly
// as emit_movsd_xmm_rip / movsd_rip_disp_offset work for doubles.
//
// The GP-register form (vpbroadcastd ymm, r32) is deliberately NOT encoded
// here: VEX has no such form (only EVEX does), so GNU as emits EVEX for it
// and any VEX attempt would be wrong. Constants are always pool dwords.
inline void emit_vpbroadcastd_rip(CodeBuffer& buf, Xmm dst) {
    emit_vex3(buf, 2, true, kVexPp66, xmm_is_extended(dst), false, false, kVexNoSource);
    emit_u8(buf, 0x58);
    emit_u8(buf, static_cast<uint8_t>(0x05 | (xmm_low3(dst) << 3)));
    emit_disp32_le(buf, 0);
}

// vpbroadcastd ymm, dword ptr [rbp + index*stride + disp] -- memory form,
// dumped to the assembler comparison for byte coverage of the 58 opcode.
inline void emit_vpbroadcastd_mem(CodeBuffer& buf, Xmm dst, Reg index,
                                  int32_t disp, int stride = 4) {
    emit_vex3(buf, 2, true, kVexPp66, xmm_is_extended(dst),
              reg_is_extended(index), false, kVexNoSource);
    emit_u8(buf, 0x58);
    emit_sib_disp(buf, static_cast<Reg>(xmm_low3(dst)), index,
                  checked_sib_scale(stride), disp);
}

inline size_t vpbroadcastd_rip_disp_offset(CodeBuffer& buf) { return buf.size() - 4; }

// vextracti128 xmm_dst, ymm_src, imm8 -- the first step of a horizontal
// reduce: split the high 128-bit lane of a ymm into a fresh xmm. Note the
// swap: ModRM.reg is the SOURCE ymm, ModRM.rm the DESTINATION xmm (VEX.R
// extends the source, VEX.B the destination). map 0F3A, opcode 39, pp 66.
// Verified: vextracti128 xmm3,ymm7,1 = C4 E3 7D 39 FB 01.
inline void emit_vextracti128_ymm_xmm(CodeBuffer& buf, Xmm dst_xmm, Xmm src_ymm,
                                      uint8_t imm) {
    emit_vex3(buf, 3, true, kVexPp66, xmm_is_extended(src_ymm), false,
              xmm_is_extended(dst_xmm), kVexNoSource);
    emit_u8(buf, 0x39);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(src_ymm)),
                               static_cast<Reg>(xmm_low3(dst_xmm))));
    emit_u8(buf, imm);
}

// vpshufd xmm_dst, xmm_src, imm8 -- 128-bit shuffle; the two halving steps of
// the horizontal reduce. map 0F, opcode 70, pp 66, L=0.
//   _MM_SHUFFLE(2,3,0,1)  = 0x4E   (swap adjacent dwords within each pair)
//   _MM_SHUFFLE(1,0,3,2)  = 0xB1   (swap the two dword halves)
// Verified: vpshufd xmm9,xmm8,0xB1 = C4 41 79 70 C8 B1.
inline void emit_vpshufd_xmm(CodeBuffer& buf, Xmm dst, Xmm src, uint8_t imm) {
    emit_vex3(buf, 1, false, kVexPp66, xmm_is_extended(dst), false,
              xmm_is_extended(src), kVexNoSource);
    emit_u8(buf, 0x70);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(dst)),
                               static_cast<Reg>(xmm_low3(src))));
    emit_u8(buf, imm);
}

enum : uint8_t { kVpshufdSwapPairs = 0x4E, kVpshufdSwapHalves = 0xB1 };

// vmovd r32, xmm -- move the low dword of an xmm/ymm to a GP register,
// which is what ends a horizontal reduce. map 0F, opcode 7E, pp 66, L=0.
// Note the swap again: ModRM.reg is the XMM source (VEX.R), ModRM.rm the GP
// destination (VEX.B). Verified: vmovd eax,xmm15 = C5 79 7E F8 and
// vmovd r10d,xmm15 = C4 41 79 7E FA.
inline void emit_vmovd_xmm_to_r32(CodeBuffer& buf, Reg dst32, Xmm src) {
    emit_vex3(buf, 1, false, kVexPp66, xmm_is_extended(src), false,
              reg_is_extended(dst32), kVexNoSource);
    emit_u8(buf, 0x7E);
    emit_u8(buf, modrm_reg_reg(static_cast<Reg>(xmm_low3(src)), dst32));
}

} // namespace lithon::jit
