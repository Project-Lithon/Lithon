#include <iostream>
#include <fstream>
#include <sstream>
#include <string>

#include "runtime/value.h"
#include "ir/ir.h"
#include "ir/text_parser.h"
#include "interpreter/interpreter.h"
#include "typecheck/typecheck.h"

namespace {

// Detects whether this module carries ANY type annotation at all --
// if so, Lithon's mandatory typing discipline applies and the
// type-checker runs automatically. A fully untyped module (no
// annotations anywhere) is treated as the original, pre-0.6
// execution-only path, unchanged -- this is what keeps the original
// 11-program regression suite working without modification.
// 4.1 REMOVED: this used to skip the type checker entirely for a module with no
// annotations at all, which made adding an annotation a whole-module semantic
// switch -- annotate one variable and every unannotated variable in the file
// became an error. It also meant most of the regression corpus was never
// checked at all: 14 of 18 programs in tests/programs/ had zero annotations and
// so were silently exempt. The checker is unconditional now.
} // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "usage: hello <ir_file>\n";
        return 1;
    }

    std::string ir_path = argv[1];

    std::ifstream file(ir_path);
    if (!file) {
        std::cerr << "error: cannot open " << ir_path << "\n";
        return 1;
    }

    std::stringstream buffer;
    buffer << file.rdbuf();

    try {
        lithon::ir::Module module = lithon::ir::parse_ir_text(buffer.str());

        {
            auto errors = lithon::typecheck::check_module(module);
            if (!errors.empty()) {
                for (const auto& e : errors) {
                    // LITHON-Exxxx messages are self-identifying; prose-only
                    // messages keep the legacy "RCR error:" tag.
                    const std::string& m = e.message;
                    std::cerr << (m.rfind("LITHON-", 0) == 0 ? "" : "RCR error: ") << m << "\n";
                }
                return 1;
            }
        }

        lithon::interp::run_main(module);

    } catch (const std::exception& e) {
        // LITHON-Exxxx messages are self-identifying (same rule as the coded
        // typecheck errors above); prose-only runtime messages keep "error: ".
        const std::string m = e.what();
        std::cerr << (m.rfind("LITHON-", 0) == 0 ? "" : "error: ") << m << "\n";
        return 1;
    }

    return 0;
}
