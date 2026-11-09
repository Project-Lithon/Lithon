// Emits every x86-32 encoder form back to back, one per line of the table, and
// writes the raw bytes to argv[1]. tools/check_x86_32_encoding.py disassembles
// them with objdump and compares each decoded instruction with the text given here
// (printed to stdout, one line per instruction).
#include <cstdio>
#include <fstream>

#include "x86_32_encoder.h"

using namespace lithon::jit::x86_32;

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    Code c;
    auto say = [](const char* s) { std::puts(s); };
    const int32_t D = -0x1000;   // forces a disp32
    mov_r_m(c, R::EAX, D);                       say("mov eax,DWORD PTR [ebp-0x1000]");
    mov_r_m(c, R::EDX, 0x2000);                  say("mov edx,DWORD PTR [ebp+0x2000]");
    mov_m_r(c, D, R::ECX);                       say("mov DWORD PTR [ebp-0x1000],ecx");
    mov_r_imm(c, R::EBX, 0x12345678);            say("mov ebx,0x12345678");
    mov_m_imm(c, D, 0xdeadbeef);                 say("mov DWORD PTR [ebp-0x1000],0xdeadbeef");
    mov_r_r(c, R::EBP, R::ESP);                  say("mov ebp,esp");
    mov_r_r(c, R::EAX, R::EDX);                  say("mov eax,edx");
    mov_r_abs(c, R::ECX, 0x00402000);            say("mov ecx,DWORD PTR ds:0x402000");
    lea_r_m(c, R::EAX, D);                       say("lea eax,[ebp-0x1000]");
    movzx_r_r8(c, R::EAX, R::EAX);               say("movzx eax,al");
    movzx_r_r8(c, R::EDX, R::ECX);               say("movzx edx,cl");
    const struct { Alu op; const char* n; } alus[] = {
        {Alu::Add, "add"}, {Alu::Or, "or"}, {Alu::Adc, "adc"}, {Alu::Sbb, "sbb"},
        {Alu::And, "and"}, {Alu::Sub, "sub"}, {Alu::Xor, "xor"}, {Alu::Cmp, "cmp"}};
    static char buf[8][4][96];
    int k = 0;
    for (auto& a : alus) {
        alu_r_m(c, a.op, R::EAX, D);   std::snprintf(buf[k][0], 96, "%s eax,DWORD PTR [ebp-0x1000]", a.n); say(buf[k][0]);
        alu_r_r(c, a.op, R::EDX, R::ECX); std::snprintf(buf[k][1], 96, "%s edx,ecx", a.n); say(buf[k][1]);
        alu_r_imm(c, a.op, R::ECX, 0x1234); std::snprintf(buf[k][2], 96, "%s ecx,0x1234", a.n); say(buf[k][2]);
        ++k;
    }
    alu_abs_imm8(c, Alu::Add, 0x00403000, 1);    say("add DWORD PTR ds:0x403000,0x1");
    alu_abs_imm8(c, Alu::Adc, 0x00403004, 0);    say("adc DWORD PTR ds:0x403004,0x0");
    alu_abs_imm8(c, Alu::Sub, 0x00403000, 1);    say("sub DWORD PTR ds:0x403000,0x1");
    alu_abs_imm8(c, Alu::Sbb, 0x00403004, 0);    say("sbb DWORD PTR ds:0x403004,0x0");
    mov_r_mem(c, R::EAX, at_ebp_idx(D, R::ECX, 8));      say("mov eax,DWORD PTR [ebp+ecx*8-0x1000]");
    mov_mem_r(c, at_ebp_idx(D, R::EDX, 4), R::ECX);      say("mov DWORD PTR [ebp+edx*4-0x1000],ecx");
    mov_r_mem(c, R::EDX, at_reg(R::ECX, 0x10));          say("mov edx,DWORD PTR [ecx+0x10]");
    mov_mem_r(c, at_reg(R::EAX, 0x14), R::ECX);          say("mov DWORD PTR [eax+0x14],ecx");
    mov_mem_imm(c, at_reg(R::EDX, 8), 0x55);             say("mov DWORD PTR [edx+0x8],0x55");
    lea_r_mem(c, R::EAX, at_ebp_idx(D, R::EDX, 2));      say("lea eax,[ebp+edx*2-0x1000]");
    movzx_r_mem8(c, R::EAX, at_ebp_idx(D, R::ECX, 1));   say("movzx eax,BYTE PTR [ebp+ecx*1-0x1000]");
    movsx_r_mem8(c, R::EAX, at_reg(R::EDX, 3));          say("movsx eax,BYTE PTR [edx+0x3]");
    movzx_r_mem16(c, R::ECX, at_reg(R::EAX, 4));         say("movzx ecx,WORD PTR [eax+0x4]");
    movsx_r_mem16(c, R::EAX, at_ebp_idx(D, R::ECX, 2));  say("movsx eax,WORD PTR [ebp+ecx*2-0x1000]");
    mov_mem8_r(c, at_ebp_idx(D, R::ECX, 1), R::EAX);     say("mov BYTE PTR [ebp+ecx*1-0x1000],al");
    mov_mem16_r(c, at_reg(R::EDX, 6), R::ECX);           say("mov WORD PTR [edx+0x6],cx");
    cdq(c);                                              say("cdq");
    shld_r_r_imm8(c, R::EDX, R::EAX, 5);                 say("shld edx,eax,0x5");
    shrd_r_r_imm8(c, R::EAX, R::EDX, 7);                 say("shrd eax,edx,0x7");
    shl_r_imm8(c, R::EAX, 3);                            say("shl eax,0x3");
    shr_r_imm8(c, R::EDX, 9);                            say("shr edx,0x9");
    sar_r_imm8(c, R::ECX, 31);                           say("sar ecx,0x1f");
    movsd_x_mem(c, 1, at_abs(0x00405000));               say("movsd xmm1,QWORD PTR ds:0x405000");
    addsd_x_mem(c, 0, at_abs(0x00405008));               say("addsd xmm0,QWORD PTR ds:0x405008");
    subsd_x_mem(c, 0, at_ebp_idx(D, R::ECX, 8));         say("subsd xmm0,QWORD PTR [ebp+ecx*8-0x1000]");
    mulsd_x_mem(c, 1, at_reg(R::EAX, 8));                say("mulsd xmm1,QWORD PTR [eax+0x8]");
    divsd_x_mem(c, 0, at_abs(0x00405010));               say("divsd xmm0,QWORD PTR ds:0x405010");
    ucomisd_x_mem(c, 0, at_abs(0x00405018));             say("ucomisd xmm0,QWORD PTR ds:0x405018");
    alu_r_mem(c, Alu::Or, R::EAX, at_abs(0x00405020));   say("or eax,DWORD PTR ds:0x405020");
    push_mem(c, at_abs(0x00405024));                     say("push DWORD PTR ds:0x405024");
    imul_r_r_imm32(c, R::EAX, R::ECX, 0x1234);           say("imul eax,ecx,0x1234");
    movsd_x_mem(c, 0, at_ebp_idx(D, R::ECX, 8));         say("movsd xmm0,QWORD PTR [ebp+ecx*8-0x1000]");
    movsd_mem_x(c, at_reg(R::EAX, 0x18), 1);             say("movsd QWORD PTR [eax+0x18],xmm1");
    movsd_x_m(c, 0, D);                          say("movsd xmm0,QWORD PTR [ebp-0x1000]");
    movsd_m_x(c, D, 1);                          say("movsd QWORD PTR [ebp-0x1000],xmm1");
    addsd_x_m(c, 0, D);                          say("addsd xmm0,QWORD PTR [ebp-0x1000]");
    subsd_x_m(c, 1, D);                          say("subsd xmm1,QWORD PTR [ebp-0x1000]");
    mulsd_x_m(c, 0, D);                          say("mulsd xmm0,QWORD PTR [ebp-0x1000]");
    divsd_x_m(c, 1, D);                          say("divsd xmm1,QWORD PTR [ebp-0x1000]");
    ucomisd_x_m(c, 0, D);                        say("ucomisd xmm0,QWORD PTR [ebp-0x1000]");
    setcc_r8(c, Cc::A, R::EAX);                  say("seta al");
    setcc_r8(c, Cc::NP, R::ECX);                 say("setnp cl");
    setcc_r8(c, Cc::P, R::EDX);                  say("setp dl");
    test_r_r(c, R::EAX, R::EAX);                 say("test eax,eax");
    test_r_r(c, R::EDX, R::ECX);                 say("test edx,ecx");
    imul_r_m(c, R::EAX, D);                      say("imul eax,DWORD PTR [ebp-0x1000]");
    mul_m(c, D);                                 say("mul DWORD PTR [ebp-0x1000]");
    imul_r1(c, R::ECX);                          say("imul ecx");
    idiv_r(c, R::ECX);                           say("idiv ecx");
    neg_r(c, R::EAX);                            say("neg eax");
    not_r(c, R::EDX);                            say("not edx");
    xor_zero(c, R::EDX);                         say("xor edx,edx");
    setcc_r8(c, Cc::L, R::EAX);                  say("setl al");
    setcc_r8(c, Cc::NE, R::ECX);                 say("setne cl");
    push_r(c, R::EAX);                           say("push eax");
    pop_r(c, R::EBP);                            say("pop ebp");
    push_imm32(c, 0x00401000);                   say("push 0x401000");
    push_m(c, D);                                say("push DWORD PTR [ebp-0x1000]");
    add_esp(c, 0x1000);                          say("add esp,0x1000");
    sub_esp(c, 0x1000);                          say("sub esp,0x1000");
    call_r(c, R::EAX);                           say("call eax");
    ret(c);                                      say("ret");
    const Cc ccs[] = {Cc::O, Cc::NO, Cc::B, Cc::AE, Cc::E, Cc::NE, Cc::BE, Cc::A, Cc::S, Cc::NS, Cc::L, Cc::GE, Cc::LE, Cc::G};
    const char* ccn[] = {"jo", "jno", "jb", "jae", "je", "jne", "jbe", "ja", "js", "jns", "jl", "jge", "jle", "jg"};
    static char jb[14][32];
    for (int i = 0; i < 14; ++i) {
        size_t at = jcc_rel32(c, ccs[i]);
        patch_rel32(c, at, c.size() + 0x10);   // target = end of this jcc + 0x10
        std::snprintf(jb[i], 32, "%s <rel+0x10>", ccn[i]);
        say(jb[i]);
    }
    size_t j = jmp_rel32(c); patch_rel32(c, j, c.size() + 0x20); say("jmp <rel+0x20>");
    size_t cl = call_rel32(c); patch_rel32(c, cl, c.size() + 0x30); say("call <rel+0x30>");
    prologue(c, 0x100);                          say("push ebp"); say("mov ebp,esp"); say("sub esp,0x100");
    epilogue(c);                                 say("mov esp,ebp"); say("pop ebp");
    std::ofstream(argv[1], std::ios::binary).write(reinterpret_cast<const char*>(c.data()), static_cast<std::streamsize>(c.size()));
    return 0;
}
