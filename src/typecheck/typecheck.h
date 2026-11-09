#pragma once
#include <string>
#include <vector>
#include "ir/ir.h"

namespace lithon::typecheck {

struct RCRError {
    std::string message;
    // Where in the source the offending instruction came from (-1 / 0 = unknown); see
    // ir::Instr::src / line.
    int src = -1;
    int line = 0;
};

// Returns an empty vector on success. Non-empty means the module is
// rejected -- every entry is a distinct RCR violation.
std::vector<RCRError> check_module(const lithon::ir::Module& module);

// The text a driver prints for one error: LITHON-Exxxx messages are self-identifying, prose-only
// ones keep the legacy "RCR error:" tag, and a known source position adds a `  --> path:line`
// line (the same shape the runtime traps use, so one reader understands both).
std::string format_error(const RCRError& e, const lithon::ir::Module& module);

} // namespace lithon::typecheck
