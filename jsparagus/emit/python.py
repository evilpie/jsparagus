"""Emit code and parser tables in Python."""

from ..grammar import InitNt, CallMethod, Some, is_concrete_element, Nt
from ..actions import Action, Reduce, Lookahead, CheckNotOnNewLine, FilterFlag, PushFlag, PopFlag, FunCall, Seq
from ..runtime import SPECIAL_CASE_TAG

def write_python_parse_table(out, parse_table):
    out.write("from jsparagus import runtime\n")
    if any(isinstance(key, Nt) for key in parse_table.nonterminals):
        out.write("from jsparagus.runtime import Nt, InitNt, End, ErrorToken, StateTermValue, ShiftError, ShiftAccept\n")
    out.write("\n")

    methods = set()
    def write_action(act, indent = ""):
        assert isinstance(act, Action)
        assert not act.is_inconsistent()
        if isinstance(act, Reduce):
            out.write("{}replay = [StateTermValue(0, {}, value)]\n".format(indent, repr(act.nt)))
            if act.replay > 0:
                out.write("{}replay = replay + parser.stack[-{}:]\n".format(indent, act.replay))
            if act.replay + act.pop > 0:
                out.write("{}parser.stack = parser.stack[:-{}]\n".format(indent, act.replay + act.pop))
            out.write("{}parser.shift_list(replay, lexer)\n".format(indent))
            return indent, False
        if isinstance(act, Lookahead):
            raise ValueError("Unexpected Lookahead action")
        if isinstance(act, CheckNotOnNewLine):
            # TODO: This code does not handle larger lookahead.
            if act.offset == -1:
                out.write("{}if lexer.saw_line_terminator():".format(indent))
                out.write("{}    raise ShiftError()".format(indent))
            return indent + "    ", True
        if isinstance(act, FilterFlag):
            out.write("{}if parser.flags[{}][-1] == {}:\n".format(indent, act.flag, act.value))
            return indent + "    ", True
        if isinstance(act, PushFlag):
            out.write("{}parser.flags[{}].append({})\n".format(indent, act.flag, act.value))
            return indent, True
        if isinstance(act, PopFlag):
            out.write("{}parser.flags[{}].pop()\n".format(indent, act.flag))
            return indent, True
        if isinstance(act, FunCall):
            def map_with_offset(args):
                get_value = "parser.stack[{}].value"
                for a in args:
                    if isinstance(a, int):
                        yield get_value.format(-(a + act.offset))
                    elif isinstance(a, str):
                        yield a
                    elif isinstance(a, Some) and isinstance(a.inner, int):
                        yield get_value.format(-(a.inner + act.offset))
                    elif isinstance(a, Some) and isinstance(a.inner, str):
                        yield "Some({})".format(a.inner)
                    elif a is None:
                        yield "None"
                    else:
                        raise ValueError(a)
            if act.method == "id":
                assert len(act.args) == 1
                out.write("{}{} = {}\n".format(indent, act.set_to, next(map_with_offset(act.args))))
            elif act.method == "accept":
                assert len(act.args) == 0
                out.write("{}raise ShiftAccept()\n".format(indent))
            else:
                methods.add(act)
                out.write("{}{} = parser.methods.{}({})\n".format(
                    indent, act.set_to, act.method,
                    ", ".join(map_with_offset(act.args))
                ))
            return indent, True
        if isinstance(act, Seq):
            res = True
            for a in act.actions:
                indent, res = write_action(a, indent)
            return indent, res
        raise ValueError("Unknown action type")

    # Write code correspond to each action which has to be performed.
    for i, state in enumerate(parse_table.states):
        assert i == state.index
        if state.epsilon == []:
            continue
        out.write("def state_{}_actions(parser, lexer):\n".format(i))
        out.write("{}\n".format(parse_table.debug_context(i, "\n", "    # ")))
        out.write("    value = None\n")
        for term, dest in state.edges():
            try:
                indent, res = write_action(term, "    ")
            except:
                print("Error while writting code for {}\n\n".format(state))
                parse_table.debug_info = True
                print(parse_table.debug_context(state.index, "\n", "# "))
                raise
            if res:
                out.write("{}parser.stack.append(StateTermValue({}, None, value))\n".format(
                    indent, dest
                ))
            out.write("{}return\n".format(indent))
        out.write("\n")

    out.write("actions = [\n")
    for i, state in enumerate(parse_table.states):
        assert i == state.index
        out.write("    # {}.\n{}\n".format(i, parse_table.debug_context(i, "\n", "    # ")))
        if state.epsilon == []:
            row = { term: dest for term, dest in state.edges() }
            out.write("    " + repr(row) + ",\n")
        else:
            out.write("    state_{}_actions,\n".format(i))
        out.write("\n")
    out.write("]\n\n")

    out.write("error_codes = [\n")
    SLICE_LEN = 16
    for i in range(0, len(parse_table.states), SLICE_LEN):
        states_slice = parse_table.states[i:i + SLICE_LEN]
        out.write("    {}\n".format(
            " ".join(repr(state.get_error_symbol()) + "," for state in states_slice)))
    out.write("]\n\n")

    out.write("goal_nt_to_init_state = {}\n\n".format(
        repr({ nt.name: goal for nt, goal in parse_table.named_goals })
    ))

    if len(parse_table.named_goals) == 1:
        init_nt = parse_table.named_goals[0][0]
        default_goal = '=' + repr(init_nt.name)
    else:
        default_goal = ''

    # Class used to provide default methods when not defined by the caller.
    out.write("class DefaultMethods:\n")
    for act in methods:
        assert isinstance(act, FunCall)
        args = ", ".join("x{}".format(i) for i in range(len(act.args)))
        out.write("    def {}(self, {}): pass\n"
                  .format(act.method, args))
    if not methods:
        out.write("    pass\n")
    out.write("\n")

    out.write("class Parser(runtime.ParserV2):\n")
    out.write("    def __init__(self, goal{}, builder=None):\n".format(default_goal))
    out.write("        if builder is None:\n")
    out.write("            builder = DefaultMethods()\n")
    out.write("        super().__init__(actions, error_codes, goal_nt_to_init_state[goal], builder)\n")
    out.write("\n")

def write_python_parser_states(out, parser_states):
    grammar = parser_states.grammar
    states = parser_states.states
    prods = parser_states.prods
    init_state_map = parser_states.init_state_map

    out.write("from jsparagus import runtime\n")
    if any(isinstance(key, Nt) for key in grammar.nonterminals):
        out.write("from jsparagus.runtime import Nt, ErrorToken\n")
    out.write("\n")

    special_case_cache = {}
    special_cases = []
    def render_action(action):
        if isinstance(action, tuple):
            if action not in special_case_cache:
                if action[0] == 'IfSameLine':
                    special_case_cache[action] = len(special_cases)
                    special_cases.append(
                        "lambda lexer, t: {} if lexer.saw_line_terminator() else {}"
                        .format(action[2], action[1]))
                else:
                    raise ValueError("unrecognized kind of special case: " + repr(action))
            index = special_case_cache[action]
            return SPECIAL_CASE_TAG + index
        else:
            assert isinstance(action, int)
            return action

    out.write("actions = [\n")
    for i, state in enumerate(states):
        out.write("    # {}. {}\n".format(i, state.traceback() or "<empty>"))
        # for item in state._lr_items:
        #     out.write("    #       {}\n".format(grammar.lr_item_to_str(prods, item)))
        out.write("    {"
                  + ", ".join("{!r}: {!r}".format(t, render_action(action))
                              for t, action in state.action_row.items())
                  + "},\n")
        out.write("\n")
    out.write("]\n\n")

    out.write("ctns = [\n")
    for state in states:
        row = {
            nt.pretty(): state_id
            for nt, state_id in state.ctn_row.items()
        }
        out.write("    " + repr(row) + ",\n")
    out.write("]\n\n")

    out.write("special_cases = [\n")
    for case in special_cases:
        out.write("    {},\n".format(case))
    out.write("]\n\n")

    out.write("error_codes = [\n")
    SLICE_LEN = 16
    for i in range(0, len(states), SLICE_LEN):
        slice = states[i:i + SLICE_LEN]
        out.write("    {}\n".format(
            " ".join(repr(e.error_code) + "," for e in slice)))
    out.write("]\n\n")

    def compile_reduce_expr(expr):
        """Compile a reduce expression to Python"""
        if isinstance(expr, CallMethod):
            method_name = expr.method.replace(" ", "_P")
            return "builder.{}({})".format(method_name, ', '.join(map(compile_reduce_expr, expr.args)))
        elif isinstance(expr, Some):
            return compile_reduce_expr(expr.inner)
        elif expr is None:
            return "None"
        else:
            # can't be 'accept' because we filter out InitNt productions
            assert isinstance(expr, int)
            return "x{}".format(expr)

    out.write("reductions = [\n")
    for prod_index, prod in enumerate(prods):
        if isinstance(prod.nt.name, InitNt):
            continue
        nparams = sum(1 for e in prod.rhs if is_concrete_element(e))
        names = ["x" + str(i) for i in range(nparams)]
        fn = ("lambda builder, "
              + ", ".join(names)
              + ": " + compile_reduce_expr(prod.reducer))
        out.write("    # {}. {}\n".format(
            prod_index,
            grammar.production_to_str(prod.nt, prod.rhs, prod.reducer)))
        out.write("    ({!r}, {!r}, {}),\n".format(prod.nt.pretty(), len(names), fn))
    out.write("]\n\n\n")  # two blank lines before class.

    out.write("class DefaultBuilder:\n")
    for tag, method_type in grammar.methods.items():
        method_name = tag.replace(' ', '_P')
        args = ", ".join("x{}".format(i)
                         for i in range(len(method_type.argument_types)))
        out.write("    def {}(self, {}): return ({!r}, {})\n"
                  .format(method_name, args, tag, args))
    out.write("\n\n")

    out.write("goal_nt_to_init_state = {\n")
    for init_nt, index in init_state_map.items():
        out.write("    {!r}: {!r},\n".format(init_nt.name, index))
    out.write("}\n\n")

    if len(init_state_map) == 1:
        init_nt = next(iter(init_state_map.keys()))
        default_goal = '=' + repr(init_nt.name)
    else:
        default_goal = ''
    out.write("class Parser(runtime.Parser):\n")
    out.write("    def __init__(self, goal{}, builder=None):\n".format(default_goal))
    out.write("        if builder is None:\n")
    out.write("            builder = DefaultBuilder()\n")
    out.write("        super().__init__(actions, ctns, reductions, special_cases, error_codes,\n")
    out.write("                         goal_nt_to_init_state[goal], builder)\n")
    out.write("\n")
