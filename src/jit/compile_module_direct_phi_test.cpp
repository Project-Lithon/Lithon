// 2.7: direct float Op::Phi, a merge that never touches memory.
//
// Before this, a float merge was a memory round trip by construction.
// resolve_phis named a variable, stored each incoming value into it on the
// predecessor edge, and loaded it at the top of the join. With direct_phis the
// Phi stays in the IR and the backend emits the copies itself, so a double
// merge costs one movsd per incoming edge instead of a store and a load.
//
// The differential suites already proved the answers were right. They passed
// before this phase existed, because the memory path was correct. So an
// assertion about the output alone would prove nothing here. What these tests
// pin is the pair that only holds together. The copies went to registers, and
// the value is still right. A test that broke the movsd would have to fail the
// output. A test that kept the value by falling back to memory would have to
// fail the counter. Neither alone is the claim.
//
// The float cases need real machine code executed, so each one compiles,
// allocates executable memory and runs the entry point in process, capturing
// stdout so the printed answer can be asserted on rather than eyeballed.

#include "compile_function.h"
#include "ir/ir.h"
#include "ir/text_parser.h"
#include "exec_memory.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <ctime>
#include <string>
#include <sys/wait.h>
#include <unistd.h>

using namespace lithon::ir;
using namespace lithon::jit;

typedef void (*VoidFunc)();

static int failures = 0;

static void check(bool ok, const std::string& what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what.c_str());
        ++failures;
    }
}

static void check_eq(const std::string& got, const std::string& want,
                     const std::string& what) {
    if (got != want) {
        std::printf("FAIL: %s (got \"%s\", want \"%s\")\n",
                    what.c_str(), got.c_str(), want.c_str());
        ++failures;
    }
}

// Compiles, runs, and returns what the program printed.
//
// The program is run in a child process, and that is not paranoia about
// crashes. A merge wired to the wrong predecessor does not fault, it picks up
// the other arm's value. For a loop that means the counter is reset on every
// iteration, so the loop never terminates and the process spins forever
// instead of failing. Without a bound, a bug like that turns into a hung test
// run rather than a red one, which is the worst of both. It says nothing, and
// it holds the slot. So the child gets a wall clock alarm and the parent
// reports a timeout as a failure like any other.
struct RunResult {
    std::string out;
    bool timed_out = false;
    bool crashed = false;
    size_t copies = 0;
};

static RunResult run_child(Module m, CompileOptions opt) {
    RunResult r;
    CompiledModule c = compile_module(m, opt);   // may throw; let it propagate
    r.copies = c.phi_copies_direct;

    int fds[2];
    if (pipe(fds) != 0) {
        r.crashed = true;
        return r;
    }
    std::fflush(stdout);
    const pid_t pid = fork();
    if (pid < 0) {
        r.crashed = true;
        return r;
    }
    if (pid == 0) {
        // Child: send print() output down the pipe, run, leave.
        close(fds[0]);
        dup2(fds[1], STDOUT_FILENO);
        close(fds[1]);
        alarm(10);
        ExecutableBuffer mem(c.code);
        reinterpret_cast<VoidFunc>(reinterpret_cast<uint8_t*>(mem.data()) +
                                   c.function_offset.at("main"))();
        std::fflush(stdout);
        _exit(0);
    }

    // Parent: drain the pipe while waiting, so a chatty child cannot fill it
    // and deadlock against the wait.
    close(fds[1]);
    char buf[512];
    ssize_t n;
    while ((n = read(fds[0], buf, sizeof buf)) > 0) r.out.append(buf, static_cast<size_t>(n));
    close(fds[0]);

    int status = 0;
    const timespec slice{0, 10 * 1000 * 1000};   // 10ms
    for (;;) {
        const pid_t done = waitpid(pid, &status, WNOHANG);
        if (done == pid) break;
        if (done < 0) { r.crashed = true; break; }
        if (r.out.size() > (1u << 20)) break;      // runaway output: stop waiting
        nanosleep(&slice, nullptr);
    }
    if (waitpid(pid, &status, WNOHANG) == 0) {    // still running
        kill(pid, SIGKILL);
        waitpid(pid, &status, 0);
        r.timed_out = true;
    } else if (WIFEXITED(status)) {
        if (WEXITSTATUS(status) != 0) r.crashed = true;
    } else {
        r.crashed = true;                          // killed by a signal
    }
    return r;
}

// Asserts the child behaved, so a timeout or a fault is reported as what it is
// instead of as a mysterious output mismatch.
static void check_sane(const RunResult& r, const std::string& what) {
    check(!r.timed_out, what + ": the program terminated");
    check(!r.crashed, what + ": the program exited normally");
}

static std::string run_and_capture(Module m, CompileOptions opt, size_t* copies) {
    RunResult r = run_child(m, opt);
    if (copies) *copies = r.copies;
    check_sane(r, "run");
    return r.out;
}

// Two float merges with the comparison set up so both arms run. One merge is
// enough to produce a Phi, but a test that only ever takes the then arm cannot
// tell a correct edge mapping from one that is off by an index. Index confusion
// is the failure mode this code is most exposed to, because the operand is
// picked by matching a predecessor against CFG pred.
static const char* kTwoFloatMerges = R"(
function main():
block0:
    %0 = const_f64 1.5
    store f, %0
    %1 = const_f64 2.5
    store g, %1
    %2 = load f
    %3 = load g
    %4 = lt %2, %3
    branch %4, block1, block2
block1:
    %5 = load f
    store __m0, %5
    jump block3
block2:
    %6 = load g
    store __m0, %6
    jump block3
block3:
    %7 = load __m0
    call print, %7
    %8 = load f
    %9 = load g
    %10 = lt %8, %9
    branch %10, block4, block5
block4:
    %11 = load g
    store __m1, %11
    jump block6
block5:
    %12 = load f
    store __m1, %12
    jump block6
block6:
    %13 = load __m1
    call print, %13
    return
)";

static void test_float_merges_are_direct_moves() {
    Module m = parse_ir_text(kTwoFloatMerges);
    CompileOptions opt;
    opt.ssa_pipeline = true;
    opt.direct_phis = true;
    size_t copies = 0;
    const std::string out = run_and_capture(m, opt, &copies);

    check_eq(out, "1.5\n2.5\n", "both float merge arms keep their value");
    // Two merges, two predecessors each: one Phi is two copies.
    check(copies == 4, "four incoming-edge float copies (got " +
                            std::to_string(copies) + ")");
}

// The mirror of kTwoFloatMerges. Same shape, but the comparisons are set so
// both merges take their else arm.
//
// This exists because the operand is chosen by finding the predecessor in the
// successor's predecessor list, and a mapping that is off by an index still
// produces plausible code. It just hands a merge the wrong arm's value. On the
// then arm only program that mistake is invisible, because pred[0] happens to
// be right. Here pred[0] is the arm that never runs, so an off by one shows up
// as a wrong number.
//
// It also has to be loop free on purpose. When the same mistake hits a loop's
// back edge it resets the counter and the program spins forever. Catching it
// there is possible, but only as a timeout, which says far less than a wrong
// value does.
static const char* kElseArmFloatMerges = R"(
function main():
block0:
    %0 = const_f64 1.5
    store f, %0
    %1 = const_f64 2.5
    store g, %1
    %2 = load f
    %3 = load g
    %4 = gt %2, %3
    branch %4, block1, block2
block1:
    %5 = load f
    store __m0, %5
    jump block3
block2:
    %6 = load g
    store __m0, %6
    jump block3
block3:
    %7 = load __m0
    call print, %7
    %8 = load f
    %9 = load g
    %10 = gt %8, %9
    branch %10, block4, block5
block4:
    %11 = load g
    store __m1, %11
    jump block6
block5:
    %12 = load f
    store __m1, %12
    jump block6
block6:
    %13 = load __m1
    call print, %13
    return
)";

static void test_else_arm_merges_take_their_own_operand() {
    Module m = parse_ir_text(kElseArmFloatMerges);
    CompileOptions opt;
    opt.ssa_pipeline = true;
    opt.direct_phis = true;
    size_t copies = 0;
    const std::string out = run_and_capture(m, opt, &copies);

    check_eq(out, "2.5\n1.5\n", "else-arm merges take their own operand");
    check(copies == 4, "four copies on the else edges (got " +
                            std::to_string(copies) + ")");
}

// The float Phi must not be counted as a general purpose register move, and it
// must not have gone to memory either. If either number is non zero the merge
// took a path this phase was supposed to replace, and the value assertions
// above would still pass. That is exactly why they are separate.
static void test_float_merge_never_reports_a_gp_register() {
    Module m = parse_ir_text(kTwoFloatMerges);
    CompileOptions opt;
    opt.ssa_pipeline = true;
    opt.direct_phis = true;
    const CompiledModule c = compile_module(m, opt);

    check(c.phi_copies_in_registers == 0,
          "a double merge is never a general-purpose register move (got " +
              std::to_string(c.phi_copies_in_registers) + ")");
    check(c.phi_copies_total == 0,
          "a direct float merge spends no memory copy (got " +
              std::to_string(c.phi_copies_total) + ")");
}

// The loop carried merge, which is the case with no equal alternative. The
// header has two predecessors and one of them is a back edge. The accumulator
// has to advance. An implementation that emitted copies only for the entry
// edge, the easy one because it is a forward jump, leaves it at its entry
// value and the loop prints 0.
static const char* kFloatAccumulatorLoop = R"(
function main():
block0:
    %0 = const_f64 0.0
    store acc, %0
    %1 = const_i64 0
    store i, %1
    jump block1
block1:
    %2 = load i
    %3 = const_i64 3
    %4 = lt %2, %3
    branch %4, block2, block3
block2:
    %5 = load acc
    %6 = const_f64 1.5
    %7 = add %5, %6
    store acc, %7
    %8 = load i
    %9 = const_i64 1
    %10 = add %8, %9
    store i, %10
    jump block1
block3:
    %11 = load acc
    call print, %11
    return
)";

static void test_loop_carried_float_merge() {
    Module m = parse_ir_text(kFloatAccumulatorLoop);
    CompileOptions opt;
    opt.ssa_pipeline = true;
    opt.direct_phis = true;
    size_t copies = 0;
    const std::string out = run_and_capture(m, opt, &copies);

    check_eq(out, "4.5\n", "the float accumulator advances across the back edge");
    // i and acc, each with two predecessors: four copies.
    check(copies == 4, "four incoming-edge copies across the loop (got " +
                            std::to_string(copies) + ")");
}

// A merge nested in one arm of a branch. The branch of block0 has a merge on
// one target only, which is the shape that cannot use the shared compare and
// branch helper. The two arms need different copies and only one can be the
// fall through, so the arm with no merge has to be routed around. Getting that
// layout wrong does not crash. It silently picks up the other arm's code.
static const char* kNestedFloatMerge = R"(
function main():
block0:
    %0 = const_f64 1.5
    store f, %0
    %1 = const_f64 2.5
    store g, %1
    %2 = const_f64 4.5
    store p, %2
    %3 = const_f64 9.5
    store q, %3
    %4 = load f
    %5 = load g
    %6 = lt %4, %5
    branch %6, block1, block2
block1:
    %7 = load f
    store __m0, %7
    jump block5
block2:
    %8 = load p
    %9 = load q
    %10 = lt %8, %9
    branch %10, block3, block4
block3:
    %11 = load g
    store __m1, %11
    jump block6
block4:
    %12 = load q
    store __m1, %12
    jump block6
block6:
    %13 = load __m1
    store __m0, %13
    jump block5
block5:
    %14 = load __m0
    call print, %14
    %15 = load f
    %16 = load g
    %17 = lt %15, %16
    branch %17, block7, block8
block7:
    %18 = load g
    store __m2, %18
    jump block9
block8:
    %19 = load f
    store __m2, %19
    jump block9
block9:
    %20 = load __m2
    call print, %20
    return
)";

static void test_nested_float_merge() {
    Module m = parse_ir_text(kNestedFloatMerge);
    CompileOptions opt;
    opt.ssa_pipeline = true;
    opt.direct_phis = true;
    size_t copies = 0;
    const std::string out = run_and_capture(m, opt, &copies);

    check_eq(out, "1.5\n2.5\n", "a merge nested in one branch arm survives");
    check(copies > 0, "the nested layout emitted copies (got " +
                          std::to_string(copies) + ")");
}

// With register promotion off nothing gets a register, so the same float
// merges have to work through the frame slots. This is the path where a copy
// source is a spilled double and the move reads from the frame rather than from
// another register. If only the register form were implemented, this would
// either trap or read the wrong slot.
static void test_float_merges_without_register_promotion() {
    Module m = parse_ir_text(kTwoFloatMerges);
    CompileOptions opt;
    opt.ssa_pipeline = true;
    opt.direct_phis = true;
    opt.promote_registers = false;
    size_t copies = 0;
    const std::string out = run_and_capture(m, opt, &copies);

    check_eq(out, "1.5\n2.5\n", "float merges work with promotion off");
    check(copies == 4, "the copies are still emitted, just to slots (got " +
                            std::to_string(copies) + ")");
}

// Direct mode is opt in. Left off, the pipeline resolves Phis to memory as it
// always did and the new counter must read zero. Otherwise the flag would have
// no observable meaning and could be working by accident.
static void test_off_by_default_is_zero() {
    Module m = parse_ir_text(kTwoFloatMerges);
    CompileOptions opt;
    opt.ssa_pipeline = true;      // direct_phis defaults to false
    const CompiledModule c = compile_module(m, opt);

    check(c.phi_copies_direct == 0,
          "no direct copies without the flag (got " +
              std::to_string(c.phi_copies_direct) + ")");
}

// The memory path and the direct path must agree exactly, run on the same
// input. Comparing against a hardcoded string only proves the code matches
// what someone wrote down once. This proves the two implementations of the same
// merge agree, which is the property that lets the flag be flipped.
static void test_direct_matches_the_memory_path() {
    const char* irs[] = {kTwoFloatMerges, kElseArmFloatMerges,
                         kFloatAccumulatorLoop, kNestedFloatMerge};
    const char* names[] = {"two float merges", "else-arm float merges",
                           "float accumulator loop", "nested float merge"};
    for (int i = 0; i < 4; ++i) {
        CompileOptions mem;
        mem.ssa_pipeline = true;
        CompileOptions direct;
        direct.ssa_pipeline = true;
        direct.direct_phis = true;

        const std::string want = run_and_capture(parse_ir_text(irs[i]), mem, nullptr);
        const std::string got = run_and_capture(parse_ir_text(irs[i]), direct, nullptr);
        check_eq(got, want, std::string("direct matches memory path: ") + names[i]);
    }
}

// Two nested loops, which is where the parallel copy hazard actually bites.
//
// The outer accumulator and the inner index end up sharing a register, because
// their live ranges merely touch at the edge into the inner header rather than
// overlapping, and two values that do not interfere may share. The copies on
// that edge are then
//
//     %j   <- zero
//     %tot <- %tot_old
//
// so the accumulator silently restarts at zero every outer iteration. Nothing
// at the IR level is wrong. No ValueId is ever both a source and a destination,
// and validate_ssa is happy. It is only wrong once the allocator has turned
// distinct values into the same location.
//
// range(5) is not incidental. At range(2) the two registers do not collide and
// the bug is invisible. It appears as soon as the inner loop is long enough to
// need a value of its own. The expected number is the square of the sum of zero
// through four, which is 100, and the broken encoding printed 40. That is the
// last outer iteration's contribution alone, which is exactly what a reset every
// time looks like.
static const char* kNestedLoopAccumulators = R"(
function main():
block0:
    %0 = const_i64 0
    store total, %0
    %1 = const_i64 0
    store i, %1
    %2 = const_i64 0
    store j, %2
    %3 = const_i64 5
    store n, %3
    jump block1
block1:
    %4 = load i
    %5 = load n
    %6 = lt %4, %5
    branch %6, block2, block9
block2:
    %7 = const_i64 0
    store j, %7
    %8 = const_i64 5
    store m, %8
    jump block3
block3:
    %10 = load j
    %11 = load m
    %12 = lt %10, %11
    branch %12, block4, block6
block4:
    %13 = load total
    %14 = load i
    %15 = load j
    %16 = mul %14, %15
    %17 = add %13, %16
    store total, %17
    %18 = load j
    %19 = const_i64 1
    %20 = add %18, %19
    store j, %20
    jump block3
block6:
    %21 = load i
    %22 = const_i64 1
    %23 = add %21, %22
    store i, %23
    jump block1
block9:
    %24 = load total
    call print, %24
    return
)";

static void test_nested_loop_copies_do_not_clobber_each_other() {
    Module m = parse_ir_text(kNestedLoopAccumulators);
    CompileOptions opt;
    opt.ssa_pipeline = true;
    opt.direct_phis = true;
    size_t copies = 0;
    const std::string out = run_and_capture(m, opt, &copies);

    check_eq(out, "100\n", "nested-loop accumulator survives both merges");
    check(copies > 0, "the nested loop emitted direct copies (got " +
                          std::to_string(copies) + ")");

    // The memory path is the oracle. It resolves the same merges through the
    // frame, where register aliasing cannot happen at all.
    CompileOptions mem;
    mem.ssa_pipeline = true;
    const std::string want = run_and_capture(parse_ir_text(kNestedLoopAccumulators),
                                             mem, nullptr);
    check_eq(out, want, "nested loop agrees with the memory path");
}

// A loop with two conditionally-incremented accumulators, one per arm of the
// latch branch, where the same value is used again on the very next iteration.
// This is the shape that spins forever when the coalescer adopts a register a
// simultaneously-live value still holds.
//
// The mechanism is indirect. LivenessAnalysis declared its member rpo_ (the
// reverse-postorder table reverse_postorder() returns) but never assigned it,
// so the coalescer built an all -1 rpo_pos_ map: every merge extent read as
// [-1, -1], and the clash check "does any other live value already hold this
// register?" never fired. With that check silent, two merges -- hits+1 and
// x+1 here -- coalesced onto a register a live temp still owned, the latch
// copies crossed (mov rcx,rax under mov rax,r12) and the loop never terminated.
// No dict, no container, no wrong answer: a pre-existing 2.x bug surfaced only
// as a hang. The fix is rpo_ = rpo; in the LivenessAnalysis constructor.
//
// It hangs on exactly one path: ssa with register promotion on and direct phis
// off (the frame-based merge). With --direct-phis the incoming-edge copies keep
// the merge live on its own register and the program is fine, and with --no-
// promote nothing is a register at all. So this test runs that one path -
// ssa_pipeline with the default memory merge - and expects 5 iterations where
// hits increments whenever probe > 7, i.e. 4, so the answer is 4. If coalescing
// ever regresses again the child spins, alarm(10) fires, and check_sane fails
// the test rather than passing it silently.
static const char* kLatchMergeCrossedCopies = R"(
function main():
block0:
    %0 = const_i64 0
    store i, %0 : int[64]
    %1 = const_i64 0
    store hits, %1 : int[64]
    %2 = const_i64 0
    store x, %2 : int[64]
    jump block1
block1:
    %3 = load i
    %4 = const_i64 5
    %5 = lt %3, %4
    branch %5, block2, block3
block2:
    %6 = load i
    %7 = const_i64 7
    %8 = add %6, %7
    store probe, %8 : int[64]
    %9 = load probe
    %10 = const_i64 7
    %11 = gt %9, %10
    branch %11, block4, block5
block4:
    %12 = load hits
    %13 = const_i64 1
    %14 = add %12, %13
    store hits, %14
    jump block6
block5:
    %15 = load x
    %16 = const_i64 1
    %17 = add %15, %16
    store x, %17
    jump block6
block6:
    %18 = load i
    %19 = const_i64 1
    %20 = add %18, %19
    store i, %20
    jump block1
block3:
    %21 = load hits
    call print, %21
    return
)";

static void test_latch_copies_do_not_cross_registers() {
    Module m = parse_ir_text(kLatchMergeCrossedCopies);
    CompileOptions opt;
    opt.ssa_pipeline = true;
    // direct_phis deliberately left off: the frame-based merge is the path
    // whose coalescer hit the unassigned rpo_ and crossed the latch copies.
    const std::string out = run_and_capture(m, opt, nullptr);

    check_eq(out, "4\n", "latch accumulators keep their own registers");
}

int main() {
    test_float_merges_are_direct_moves();
    test_else_arm_merges_take_their_own_operand();
    test_float_merge_never_reports_a_gp_register();
    test_loop_carried_float_merge();
    test_nested_float_merge();
    test_float_merges_without_register_promotion();
    test_off_by_default_is_zero();
    test_nested_loop_copies_do_not_clobber_each_other();
    test_direct_matches_the_memory_path();
    test_latch_copies_do_not_cross_registers();

    if (failures == 0) {
        std::printf("all direct-Phi tests passed\n");
        return 0;
    }
    std::printf("%d direct-Phi failure(s)\n", failures);
    return 1;
}