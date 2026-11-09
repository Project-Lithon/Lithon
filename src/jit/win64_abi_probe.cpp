// Microsoft x64 ABI conformance probe (4.16). Windows-only: built with MinGW and run
// under wine (or on Windows) by tools/check_win64.py.
//
//   win64_abi_probe.exe <module.ir> <function> <int0> <int1> <double0> <double1>
//
// It compiles the module with the JIT, then calls <function> through a small
// assembly probe that follows the Win64 convention exactly: the integer arguments
// go in RCX and RDX, the doubles in XMM0 and XMM1 (the probe sets all four, so a
// callee reads whichever its positional parameters name), 32 bytes of shadow space
// are reserved above the return address, and the stack is 16-byte aligned at the
// call. BEFORE the call it loads every register the ABI makes callee-saved --
// RBX, RDI, RSI, R12-R15 and the full 128 bits of XMM6-XMM15 -- with a distinct
// sentinel, and AFTER it compares each one. A function that clobbers any of them
// breaks its caller (a C compiler may keep a live value there), so every one must
// come back bit-for-bit.
//
// Output, one line each:   RET <int64>   DRET <double, %.17g>   ABI OK
//                          or   ABI CLOBBERED <register> ...

#include <cstdint>
#include <cstdio>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>

#include "compile_function.h"
#include "exec_memory.h"
#include "ir/text_parser.h"
#include "runtime/class_runtime.h"
#include "runtime/heap.h"

#if !(defined(_WIN32) && defined(__x86_64__))
#error "win64_abi_probe is for Windows x86-64 only (build it with x86_64-w64-mingw32-g++)"
#endif

struct Probe {
    int64_t i0, i1;              //   0, 8
    double d0, d1;               //  16, 24
    int64_t ret;                 //  32
    double dret;                 //  40
    uint8_t xin[10][16];         //  48 .. 208   sentinels loaded into xmm6..xmm15
    uint8_t xout[10][16];        // 208 .. 368   values read back
    int64_t gin[7];              // 368 .. 424   rbx rdi rsi r12 r13 r14 r15
    int64_t gout[7];             // 424 .. 480
};
static_assert(offsetof(Probe, i0) == 0 && offsetof(Probe, d0) == 16, "probe layout");
static_assert(offsetof(Probe, ret) == 32 && offsetof(Probe, dret) == 40, "probe layout");
static_assert(offsetof(Probe, xin) == 48 && offsetof(Probe, xout) == 208, "probe layout");
static_assert(offsetof(Probe, gin) == 368 && offsetof(Probe, gout) == 424, "probe layout");

extern "C" void abi_probe(void* fn, Probe* p);

// rcx = function, rdx = &Probe. Saves every register it is about to overwrite so
// the probe itself is a conforming Win64 function.
asm(R"(
.intel_syntax noprefix
.text
.globl abi_probe
abi_probe:
    push rbp
    mov rbp, rsp
    push rbx
    push rdi
    push rsi
    push r12
    push r13
    push r14
    push r15
    sub rsp, 56                      # 32 shadow + 8 saved &Probe + 16 pad; rsp 16-aligned
    mov [rsp+32], rdx
    mov rax, rdx
    movups xmm6,  [rax+48]
    movups xmm7,  [rax+64]
    movups xmm8,  [rax+80]
    movups xmm9,  [rax+96]
    movups xmm10, [rax+112]
    movups xmm11, [rax+128]
    movups xmm12, [rax+144]
    movups xmm13, [rax+160]
    movups xmm14, [rax+176]
    movups xmm15, [rax+192]
    mov rbx, [rax+368]
    mov rdi, [rax+376]
    mov rsi, [rax+384]
    mov r12, [rax+392]
    mov r13, [rax+400]
    mov r14, [rax+408]
    mov r15, [rax+416]
    movsd xmm0, [rax+16]
    movsd xmm1, [rax+24]
    mov r10, rcx
    mov rcx, [rax+0]
    mov rdx, [rax+8]
    call r10
    mov r10, [rsp+32]
    mov [r10+32], rax
    movsd [r10+40], xmm0
    movups [r10+208], xmm6
    movups [r10+224], xmm7
    movups [r10+240], xmm8
    movups [r10+256], xmm9
    movups [r10+272], xmm10
    movups [r10+288], xmm11
    movups [r10+304], xmm12
    movups [r10+320], xmm13
    movups [r10+336], xmm14
    movups [r10+352], xmm15
    mov [r10+424], rbx
    mov [r10+432], rdi
    mov [r10+440], rsi
    mov [r10+448], r12
    mov [r10+456], r13
    mov [r10+464], r14
    mov [r10+472], r15
    add rsp, 56
    pop r15
    pop r14
    pop r13
    pop r12
    pop rsi
    pop rdi
    pop rbx
    pop rbp
    ret
.att_syntax prefix
)");

int main(int argc, char** argv) {
    if (argc != 7) {
        std::fprintf(stderr, "usage: %s <module.ir> <function> <i0> <i1> <d0> <d1>\n", argv[0]);
        return 2;
    }
    std::ifstream f(argv[1]);
    if (!f) { std::fprintf(stderr, "cannot open %s\n", argv[1]); return 2; }
    std::stringstream buf;
    buf << f.rdbuf();
    lithon::ir::Module m = lithon::ir::parse_ir_text(buf.str());
    lithon::jit::CompiledModule compiled = lithon::jit::compile_module(m);
    auto it = compiled.function_offset.find(argv[2]);
    if (it == compiled.function_offset.end()) {
        std::fprintf(stderr, "no function %s\n", argv[2]);
        return 2;
    }
    lithon::jit::ExecutableBuffer exec;
    exec.load(compiled.code.data(), compiled.code.size());
    lithon::runtime::reset_heap();

    Probe p;
    std::memset(&p, 0, sizeof p);
    p.i0 = std::strtoll(argv[3], nullptr, 10);
    p.i1 = std::strtoll(argv[4], nullptr, 10);
    p.d0 = std::strtod(argv[5], nullptr);
    p.d1 = std::strtod(argv[6], nullptr);
    for (int r = 0; r < 10; ++r)
        for (int b = 0; b < 16; ++b)
            p.xin[r][b] = static_cast<uint8_t>(0x10 * (r + 1) + b + 1);   // all distinct
    const int64_t gseed[7] = {0x1111111111111111LL, 0x2222222222222222LL, 0x3333333333333333LL,
                              0x4444444444444444LL, 0x5555555555555555LL, 0x6666666666666666LL,
                              0x7777777777777777LL};
    std::memcpy(p.gin, gseed, sizeof gseed);

    abi_probe(exec.data() + it->second, &p);
    std::fflush(stdout);

    std::printf("RET %lld\n", static_cast<long long>(p.ret));
    std::printf("DRET %.17g\n", p.dret);
    static const char* gnames[7] = {"rbx", "rdi", "rsi", "r12", "r13", "r14", "r15"};
    std::string bad;
    for (int i = 0; i < 7; ++i)
        if (p.gout[i] != p.gin[i]) bad += std::string(" ") + gnames[i];
    for (int r = 0; r < 10; ++r)
        if (std::memcmp(p.xout[r], p.xin[r], 16) != 0) bad += " xmm" + std::to_string(r + 6);
    if (bad.empty()) std::printf("ABI OK\n");
    else std::printf("ABI CLOBBERED%s\n", bad.c_str());
    return bad.empty() ? 0 : 1;
}
