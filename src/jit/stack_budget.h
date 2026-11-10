#pragma once

// Worst-case native stack, computed at compile time.
//
// C and C++ leave an overflow to the guard page and a signal; Rust prints "thread has
// overflowed its stack" after the same fault. Lithon can do better, because its call graph is
// known when the code is generated and every call depth is bounded (the E0401 guard traps past
// kMaxCallDepth frames in BOTH tiers): the exact worst-case stack a program can need is a number
// we can compute, give the program that much stack, and never overflow -- no runtime check, no
// segmentation fault, no difference between the tiers.
//
//   cost(f) = frame(f) + max over callees g of cost(g)                    f not recursive
//   cost(S) = kMaxCallDepth * max frame in S  +  max over S's exits       S a recursive group
//
// Edges: a `call` to a user function; a virtual or super call goes to ANY method (`__virt__*`);
// a `release` can run ANY destructor (`__del__*`). Host helpers (printf, input, the dict and
// print helpers) run on the same stack, so a fixed margin covers them.

#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <unordered_map>
#include <vector>

#include "ir/ir.h"
#include "stack_guard.h"

namespace lithon::jit {

inline constexpr int64_t kHostStackMargin = 512 * 1024;       // printf & friends, and slack
inline constexpr int64_t kInlineNativeStack = 4 * 1024 * 1024; // fits the thread we are on

// The most stack native code may be given. A 64-bit process has the address space for a
// generous reservation (it is only committed as it is touched); a 32-bit one does not.
// LITHON_NATIVE_STACK_LIMIT (bytes) overrides it, which is how the refusal path is tested.
inline int64_t native_stack_limit() {
    if (const char* e = std::getenv("LITHON_NATIVE_STACK_LIMIT")) {
        const long long v = std::atoll(e);
        if (v > 0) return v;
    }
    return sizeof(void*) == 8 ? (int64_t{2} << 30) : (int64_t{192} << 20);
}

inline int64_t worst_case_stack(const lithon::ir::Module& module,
                                const std::unordered_map<std::string, int64_t>& frame_bytes,
                                const std::string& entry = "__main__") {
    using lithon::ir::Op;
    const size_t n = module.functions.size();
    std::unordered_map<std::string, size_t> index;
    for (size_t i = 0; i < n; ++i) index[module.functions[i].name] = i;
    std::vector<size_t> virtuals, destructors;
    for (size_t i = 0; i < n; ++i) {
        const std::string& nm = module.functions[i].name;
        if (nm.rfind("__virt__", 0) == 0) virtuals.push_back(i);
        if (nm.rfind("__del__", 0) == 0) destructors.push_back(i);
    }
    std::vector<std::vector<size_t>> callees(n);
    for (size_t i = 0; i < n; ++i) {
        std::vector<bool> seen(n, false);
        auto add = [&](size_t j) { if (!seen[j]) { seen[j] = true; callees[i].push_back(j); } };
        for (const auto& b : module.functions[i].blocks) {
            for (const auto& in : b.instrs) {
                if (in.op == Op::Call) {
                    const auto it = index.find(in.name);
                    if (it != index.end()) add(it->second);
                } else if (in.op == Op::CallVirtual || in.op == Op::CallSuper) {
                    for (size_t j : virtuals) add(j);
                } else if (in.op == Op::Release) {
                    for (size_t j : destructors) add(j);
                }
            }
        }
    }
    auto frame_of = [&](size_t i) {
        const auto it = frame_bytes.find(module.functions[i].name);
        return it == frame_bytes.end() ? int64_t{256} : it->second;
    };

    // Tarjan's strongly connected components (iterative: the graph can be deep).
    std::vector<int> idx(n, -1), low(n, 0), comp(n, -1);
    std::vector<bool> on(n, false);
    std::vector<size_t> stk;
    int counter = 0, ncomp = 0;
    struct Frame { size_t v; size_t next; };
    for (size_t root = 0; root < n; ++root) {
        if (idx[root] != -1) continue;
        std::vector<Frame> work{{root, 0}};
        idx[root] = low[root] = counter++;
        stk.push_back(root);
        on[root] = true;
        while (!work.empty()) {
            Frame& f = work.back();
            if (f.next < callees[f.v].size()) {
                const size_t w = callees[f.v][f.next++];
                if (idx[w] == -1) {
                    idx[w] = low[w] = counter++;
                    stk.push_back(w);
                    on[w] = true;
                    work.push_back({w, 0});
                } else if (on[w]) {
                    low[f.v] = std::min(low[f.v], idx[w]);
                }
            } else {
                const size_t v = f.v;
                if (low[v] == idx[v]) {
                    while (true) {
                        const size_t w = stk.back();
                        stk.pop_back();
                        on[w] = false;
                        comp[w] = ncomp;
                        if (w == v) break;
                    }
                    ++ncomp;
                }
                work.pop_back();
                if (!work.empty()) low[work.back().v] = std::min(low[work.back().v], low[v]);
            }
        }
    }
    // Components are numbered callees-first, so a plain ascending pass is a topological order.
    std::vector<int64_t> maxframe(ncomp, 0), cost(ncomp, 0);
    std::vector<bool> cyclic(ncomp, false);
    std::vector<int> size(ncomp, 0);
    for (size_t v = 0; v < n; ++v) {
        maxframe[comp[v]] = std::max(maxframe[comp[v]], frame_of(v));
        ++size[comp[v]];
        for (size_t w : callees[v]) if (w == v) cyclic[comp[v]] = true;
    }
    for (int c = 0; c < ncomp; ++c) if (size[c] > 1) cyclic[c] = true;
    for (int c = 0; c < ncomp; ++c) {
        int64_t exit_cost = 0;
        for (size_t v = 0; v < n; ++v) {
            if (comp[v] != c) continue;
            for (size_t w : callees[v]) if (comp[w] != c) exit_cost = std::max(exit_cost, cost[comp[w]]);
        }
        cost[c] = cyclic[c] ? static_cast<int64_t>(kMaxCallDepth) * maxframe[c] + exit_cost
                            : maxframe[c] + exit_cost;
    }
    const auto e = index.find(entry);
    return e == index.end() ? 0 : cost[comp[e->second]];
}

}  // namespace lithon::jit
