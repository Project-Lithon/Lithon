; Lithon highlight queries (Zed captures).
; Ordering matters: earlier patterns win, so more specific captures come first.

(comment) @comment

(string) @string

(integer) @number
(float) @number

(identifier) @variable

(function_definition name: (identifier) @function)
(call_expression (dotted_name (identifier) @function))
(call_expression (identifier) @function)
(call_expression (type_identifier) @function)
(dotted_name (identifier) @variable.parameter)
(decorator) @attribute
(assignment) @variable

(class_definition name: (type_identifier) @type)
(type_identifier) @type
(generic_type) @type
(parameter name: (identifier) @variable.parameter)

(break_statement) @keyword.control
(continue_statement) @keyword.control

[
  ("class" @keyword.operator)
  ("def" @keyword.operator)
  ("return" @keyword.control)
  ("if" @keyword.control)
  ("elif" @keyword.control)
  ("else" @keyword.control)
  ("while" @keyword.control)
  ("for" @keyword.control)
  ("in" @keyword.operator)
  ("match" @keyword.control)
  ("case" @keyword.control)
  ("try" @keyword.control)
  ("except" @keyword.control)
  ("finally" @keyword.control)
  ("as" @keyword.operator)
  ("is" @keyword.operator)
  ("and" @keyword.operator)
  ("or" @keyword.operator)
  ("not" @keyword.operator)
(boolean) @constant.builtin
]

[("+" @operator)
 ("-" @operator)
 ("*" @operator)
 ("/" @operator)
 ("%" @operator)
 ("**" @operator)
 ("<<" @operator)
 (">>" @operator)
 ("&" @operator)
 ("|" @operator)
 ("^" @operator)
 ("~" @operator)
 ("==" @operator)
 ("!=" @operator)
 ("<=" @operator)
 (">=" @operator)
 ("<" @operator)
 (">" @operator)
 ("+=" @operator)
 ("-=" @operator)
 ("*=" @operator)
 ("/=" @operator)
 ("%=" @operator)
 ("^=" @operator)
 ("&=" @operator)
 ("|=" @operator)
 ("<<=" @operator)
 (">>=" @operator)
 ("=" @operator)
 ("->" @operator)
]

[
  ("(" @punctuation.bracket)
  (")" @punctuation.bracket)
  ("[" @punctuation.bracket)
  ("]" @punctuation.bracket)
  ("," @punctuation.delimiter)
  (":" @punctuation.delimiter)
  ("@" @punctuation.delimiter)
]
