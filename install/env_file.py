#!/usr/bin/env python3
"""install/env_file.py -- THE ONE READER of the env file every unit loads, by systemd's EnvironmentFile= rules.

WHY IT EXISTS (reference-host round, iteration 4; memory #21871: "a guard must parse a file the way its consumer
does"). Every packaged unit names the env file as `EnvironmentFile=-<path>`, so its consumer is systemd. install.sh
used to read it with a SHELL's rules, and so proved and recorded a value the units never received. Measured by the
iteration-3 reflector: an owner line `CUDA_VISIBLE_DEVICES=1 # the compute card` was read as '1' -- P4 proved card 1,
INSTALL_RECORD said gpu_pin=1 -- while systemd handed every unit '1 # the compute card', which the units' resolver
(organs/voidloom_device.py) rejects, so every unit fell to card 0: the display card.

Every read of the env file that informs P4, the one-pin chain, the out-of-block key rule, the read-back and the
record goes through this module (install.sh: env_outside). It is a port of systemd 259's parse_env_file_internal()
and load_env_file_push() (src/basic/env-file.c) plus the manager's strv_env_clean_with_callback() -- the exact path
of EnvironmentFile= (exec_context_load_environment) -- and voidloom/tests/test_env_file_reader.py feeds the SAME
bytes to this reader, to golden values taken from systemd.exec(5), and (where the host has it) to systemd's own
load_env_file() through libsystemd-shared, asserting agreement on a corpus that holds every divergent construct.

THE RULES (systemd.exec(5), EnvironmentFile=; each one is in the corpus):
  * only a line whose first non-blank character is '#' or ';' is a comment -- there are NO inline comments:
    `KEY=1 # x` assigns '1 # x';
  * an unquoted value loses its leading and trailing whitespace and KEEPS interior whitespace verbatim;
  * '...' is verbatim; "..." honours \\ \\" \\` \\$ and a backslash-newline; either may span lines; text after a
    closing quote is appended with the whitespace before it dropped: `KEY="1" # x` assigns '1# x';
  * an unquoted backslash keeps the next character; a backslash-newline continues the line;
  * nothing is expanded: no $VAR, ${VAR}, $(...), `...`, ~;
  * whitespace before '=' is dropped; a key that is not [A-Za-z_][A-Za-z0-9_]* is DROPPED -- so `export KEY=v`
    assigns nothing -- and the LAST assignment of a key wins;
  * a NUL byte anywhere, or invalid UTF-8 in any assignment, makes systemd reject the WHOLE file; the units name
    it with a leading '-', so each starts WITHOUT it, silently.

A SHELL reads the same bytes differently (the managed block documents `set -a; . FILE; set +a`), so every line is
also classified:
  safe        a shell and systemd assign the same key the same value (or both assign nothing);
  divergent   a shape this module models exactly whose two readings DIFFER -- an `export` line, an inline comment;
  unmodelled  anything whose shell reading this module does not compute ($, backticks, ';', whitespace inside a
              value, continuations, multi-line quotes, CR line ends, a byte-order mark, ...): never assumed equal.
`check` turns that into the installer's policy (install.sh env_file_check). Nothing here ever prints the value of
a key other than the two pin keys and WIZARD_DISPLAY_INDEX: the file may carry a token.

STDLIB ONLY; python >= 3.6 syntax. Imports organs/voidloom_device.py (stdlib-only by contract) for the units' own
pin rules -- imported, never re-typed.
"""
import os
import re
import sys

sys.dont_write_bytecode = True          # never leave a __pycache__ in the package this runs from

PIN_KEYS = ("CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE")
DISPLAY_KEY = "WIZARD_DISPLAY_INDEX"
ACCEL_KEY = "WIZARD_ACCELERATOR"     # contract C2: auto|cuda|rocm|mps|xpu|cpu; this installer WRITES only auto / cpu
ACCEL_WRITTEN = ("auto", "cpu")
VALUE_KEYS = PIN_KEYS + (DISPLAY_KEY, ACCEL_KEY)  # the only keys whose values any message may print

# systemd's character classes (src/basic/string-util.h, escape.h)
_WS = b" \t\n\r"
_NL = b"\n\r"
_COMMENTS = b"#;"
_NEED_ESC = b'"\\`$'
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# strv_env_clean_with_callback() DROPS an assignment longer than the MANAGER's ARG_MAX - 3 (env_assignment_is_valid);
# ARG_MAX follows the manager's stack limit (2,097,152 here), and Linux never goes below 131,072. Measured with
# load_env_file() through libsystemd-shared 259: a 2 MiB value is dropped, and an EARLIER assignment of the key goes too.
_ARG_MAX_FLOOR = 131072


def _sd_unichars_ok(s):
    """systemd's utf8_is_valid() also rejects what Python's strict decoder accepts: the noncharacters U+FDD0..U+FDEF and
    every U+xxFFFE / U+xxFFFF (unichar_is_valid(), src/basic/utf8.c). Measured with load_env_file() through
    libsystemd-shared 259: `A=\\xef\\xbf\\xbf` (U+FFFF) is -EINVAL for the WHOLE file."""
    return not any(0xFDD0 <= ord(c) <= 0xFDEF or (ord(c) & 0xFFFE) == 0xFFFE for c in s)

(PRE_KEY, KEY, PRE_VALUE, VALUE, VALUE_ESCAPE, SQ, DQ, DQ_ESC, COMMENT, COMMENT_ESCAPE) = range(10)
_PUSH_AT_EOF = (PRE_VALUE, VALUE, VALUE_ESCAPE, SQ, DQ, DQ_ESC)


def _tokenize(data):
    """systemd's parse_env_file_internal(), byte for byte. Returns (pushes, line_states): pushes are
    (key_bytes, value_bytes, start_offset) in file order; line_states[i] is the tokenizer state at the first
    byte of physical line i (lines split on '\\n')."""
    state = PRE_KEY
    key, value = bytearray(), bytearray()
    lkw = lvw = None                       # last_key_whitespace / last_value_whitespace (None = SIZE_MAX)
    start = 0
    pushes, line_states = [], [PRE_KEY]

    def push():
        k = bytes(key[:lkw]) if lkw is not None else bytes(key)
        pushes.append((k, bytes(value), start))

    for i, c in enumerate(bytearray(data)):
        if state == PRE_KEY:
            if c in _COMMENTS:
                state = COMMENT
            elif c not in _WS:
                state, lkw, start = KEY, None, i
                key = bytearray([c])
        elif state == KEY:
            if c in _NL:
                state, key = PRE_KEY, bytearray()
            elif c == 0x3D:                                   # '='
                state, lvw = PRE_VALUE, None
                value = bytearray()
            else:
                if c not in _WS:
                    lkw = None
                elif lkw is None:
                    lkw = len(key)
                key.append(c)
        elif state == PRE_VALUE:
            if c in _NL:
                push()
                state, key, value = PRE_KEY, bytearray(), bytearray()
            elif c == 0x27:                                   # "'"
                state = SQ
            elif c == 0x22:                                   # '"'
                state = DQ
            elif c == 0x5C:                                   # '\\'
                state = VALUE_ESCAPE
            elif c not in _WS:
                state = VALUE
                value.append(c)
        elif state == VALUE:
            if c in _NL:
                if lvw is not None:
                    del value[lvw:]                           # chomp trailing whitespace
                push()
                state, key, value = PRE_KEY, bytearray(), bytearray()
            elif c == 0x5C:
                state, lvw = VALUE_ESCAPE, None
            else:
                if c not in _WS:
                    lvw = None
                elif lvw is None:
                    lvw = len(value)
                value.append(c)
        elif state == VALUE_ESCAPE:
            state = VALUE
            if c not in _NL:                                  # an escaped newline is eaten entirely
                value.append(c)
        elif state == SQ:
            if c == 0x27:
                state = PRE_VALUE
            else:
                value.append(c)
        elif state == DQ:
            if c == 0x22:
                state = PRE_VALUE
            elif c == 0x5C:
                state = DQ_ESC
            else:
                value.append(c)
        elif state == DQ_ESC:
            state = DQ
            if c in _NEED_ESC:
                value.append(c)
            elif c != 0x0A:                                   # any other character keeps its backslash
                value.extend(b"\\" + bytes([c]))
        elif state == COMMENT:
            if c == 0x5C:
                state = COMMENT_ESCAPE
            elif c in _NL:
                state = PRE_KEY
        elif state == COMMENT_ESCAPE:                         # v254+: the next line is NOT part of the comment
            state = PRE_KEY if c in _NL else COMMENT
        if c == 0x0A:
            line_states.append(state)
    if state in _PUSH_AT_EOF:
        if state == VALUE and lvw is not None:
            del value[lvw:]
        push()
    return pushes, line_states


class Unit(object):
    """One logical line as systemd reads it: a physical line that starts in PRE_KEY, plus every following line
    that starts inside its value (a continuation or an open quote)."""
    __slots__ = ("line", "end_line", "text", "inside", "marker", "sd", "cls", "construct", "bash", "keys")

    def __init__(self, line):
        self.line, self.end_line, self.text = line, line, ""
        self.inside = self.marker = False
        self.sd = []              # [(key, value)] systemd assigns from this unit (valid names only)
        self.cls, self.construct = "safe", ""
        self.bash = None          # (key, value) a shell assigns; None: nothing, or not modelled (see cls)
        self.keys = set()         # every key this unit may assign under EITHER reading


# ------------------------------------------------------------------------------ the shell's reading (modelled)
# Characters a shell and systemd both take literally inside an unquoted assignment value. Deliberately tight:
# anything else makes the line UNMODELLED, never assumed equal.
_SAFE_WORD = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_@%+,./:=-#")
_WHY = {"$": "a '$' (a shell expands it; systemd keeps it verbatim)",
        "`": "a backtick (a shell runs it as a command; systemd keeps it verbatim)",
        "\\": "a backslash escape",
        ";": "a ';' (a shell ends the command there; systemd keeps it in the value)",
        "&": "a '&'", "|": "a '|'", "(": "a '('", ")": "a ')'", "<": "a '<'", ">": "a '>'",
        "~": "a '~' (a shell expands it to a home directory; systemd keeps it verbatim)"}


# ADVISORY 7's CLASS -- a line after which a shell's `set -a; . FILE` does not assign a later line as systemd does
# (env-file writer pass: iteration-1 reflection EW1-05, widened in iterations 2-4). The class is the shell's WHOLE grammar
# plus every command that changes how the shell runs the lines after it -- a list of stops can never be complete
# (measured: a 146-line bash corpus and random fragment fuzzers kept finding members a list missed: an `if` after a ';',
# a ')' after a word, `false readonly X`; then `test -v 'A[E]'` and `A=([E]=1)`, whose subscript is ARITHMETIC, which
# assigns; `break` in a file sourced from a loop; a function or alias of the SOURCING shell that any command word may
# name). So the model is a WHITELIST with NO COMMAND in it (iteration 4): a shell's reading of the lines after an
# unmodelled line is computed ONLY when that line runs no command at all (_shell_plain: every word an assignment NAME=v
# / NAME+=v of plain characters, quoted strings and $NAME / ${NAME} expansions, no operator, parenthesis, redirection,
# substitution or here-document) -- or is a complete one-line array `KEY=(a b)` with no '[' in it. A line that runs a
# command -- ANY command word, a builtin (`export`, `set`, `test`, `unset`, `break`) or not (`pkill -P $PPID`) -- is not
# plain: the command may be a function or an alias of the sourcing shell, or end or interrupt it, and nothing in the
# file says which. So is an assignment to a variable the SHELL ITSELF acts on (_SHELL_MODE_KEYS, each measured with
# bash: POSIXLY_CORRECT puts it in POSIX mode, where `UID=0` ENDS it; PS4 is expanded before every traced command;
# PS0-PS3, PROMPT_COMMAND and a MAILPATH message are expanded or run at an interactive prompt -- `$((KEY=0))` there
# assigns KEY; bash's read-only names end a shell in POSIX mode). After ANY line that is not plain, what a shell assigns
# is NOT MODELLED -- UNKNOWN, never assumed equal, whether or not a line assigned the key before it (a line with no pin
# before it, `eval K""EY=0`, and one after the managed block's own CUDA_VISIBLE_DEVICES, `unset $K`, may assign it
# unnamed: iteration 4) -- and the ambient shape refuses there, naming the line to change. A refusal says a shell STOPS
# only where that is certain (_shell_stop): a line starting with a closer or an operator ( ')' '}' ']]' 'fi' 'then'
# 'else' 'elif' 'do' 'done' 'esac' 'in' '&&' '||' '|' '&' ';' ';;'), `A=x)`, an unterminated quote at the end of the
# file, or a construct opened on the line (an `if`, `case`, loop, `{`, `(`, `[[`, `((`, a function, an array `A=(`,
# `$(` / `${` / a backtick, a here-document) whose closer appears on NO later line -- each a syntax error that runs
# nothing; or a command that ends the sourcing -- `exit` / `return` (no argument or a number), `exec CMD`, `set -n`, a
# here-document with no delimiter line -- whose OWN effect is not modelled (it runs, and may be a function); or it
# cannot reassign the key (`readonly KEY`, `declare -r KEY`, `typeset -r KEY` as the command). After a certain stop no
# later line runs (EW2-02).
# RESIDUE, bounded (the same words are in README.md and docs/ONBOARDING_GUIDE.md): what this check cannot see is the
# state of the shell that sources the file -- a variable attribute it already set (an integer, a nameref, a read-only
# name, a local scope when the file is sourced inside a function), an option (errexit, nounset, xtrace with its own PS4,
# POSIX mode) or a trap of its own can change what that shell assigns from a plain KEY=value line. The installer assumes
# the file is sourced as the block's own comment says, by a shell with none of these set.
_SHELL_ARRAY_CLOSED = re.compile(r"[ \t]*[A-Za-z_][A-Za-z0-9_]*=\([^()'\"`$\\;&|<>#\[]*\)[ \t]*\Z")
_SHELL_WORDCHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./:@%+,=~-*?[]^{}!")
# variables a shell ITSELF acts on (iteration-3 reflection EW3-01, and iteration 4; each measured with bash): an
# assignment to one is never plain, and a refusal naming its line says why (_MODE_WHY)
_MODE_WHY = {
    "POSIXLY_CORRECT": "puts the shell in POSIX mode at once",
    "PS0": "an interactive shell expands it at every prompt", "PS1": "an interactive shell expands it at every prompt",
    "PS2": "an interactive shell expands it at every prompt", "PS3": "an interactive shell expands it at every prompt",
    "PROMPT_COMMAND": "an interactive shell runs it at every prompt",
    "PS4": "a shell expands it before every command it traces",
    "MAILPATH": "an interactive shell expands its message when mail arrives",
    "UID": "bash keeps it read-only, and a shell in POSIX mode ends on that line",
    "EUID": "bash keeps it read-only, and a shell in POSIX mode ends on that line",
    "PPID": "bash keeps it read-only, and a shell in POSIX mode ends on that line",
    "SHELLOPTS": "bash keeps it read-only, and a shell in POSIX mode ends on that line",
    "BASHOPTS": "bash keeps it read-only, and a shell in POSIX mode ends on that line",
    "BASH_VERSINFO": "bash keeps it read-only, and a shell in POSIX mode ends on that line",
}
_SHELL_MODE_KEYS = tuple(sorted(_MODE_WHY))
_EXPORT_LINE = re.compile(r"[ \t]*export[ \t]")
_SHELL_CLOSERS = ("fi", "then", "else", "elif", "do", "done", "esac", "in", ")", "}", "]]", "))", "&&", "||", "|", "&", ";",
                  ";;", "|&")
# a CALM text holds nothing a shell could read as a second syntax error inside an open construct (bash recovers from
# one by discarding the line and reading on -- measured) and nothing that could close it
_SHELL_CALM = re.compile(r"[^;&|()<>`{}]*\Z")
_SHELL_OPENERS = {"if": "fi", "case": "esac", "for": "done", "while": "done", "until": "done", "select": "done",
                  "{": "}", "(": ")", "[[": "]]", "((": "))", "function": "}"}


def _unquoted(text):
    """`text` with every '...' and "..." span and every backslash escape blanked, length kept: the characters a shell
    PARSES as grammar, not the ones it quotes (`\\(` and `"("` are a literal '(')."""
    # a quote left OPEN runs to the end of the text: to a shell everything after it is quoted (iteration 4: `MULTI='a`
    # then `SHELLOPTS=x` on the next line is ONE quoted value, not an assignment of SHELLOPTS)
    return re.sub(r"'[^']*(?:'|\Z)|\"(?:[^\"\\]|\\.)*(?:\"|\Z)|\\.", lambda m: " " * len(m.group(0)), text, flags=re.S)


def _uncomment(uq):
    """An unquoted text (_unquoted) without its comment: from an unquoted '#' that starts a word to the line's end."""
    return re.sub(r"(?:^|(?<=\s))#.*", "", uq)


def _shell_words(text, depth=0):
    """The words of ONE physical line as a shell splits a PLAIN simple command: [(word, quoted, raw)] -- or None when
    the line holds anything else: an operator, a parenthesis, a redirection, a `$(...)` or backtick that is not itself
    closed on the line around a plain command, a `${` other than ${NAME}, `$` before anything but a name, an
    unterminated quote, a trailing backslash. An unquoted '#' at the start of a word ends the line (a comment)."""
    out, i, n, cur, quoted, start = [], 0, len(text), None, False, 0
    while i < n:
        c = text[i]
        if c in " \t":
            if cur is not None:
                out.append((cur, quoted, text[start:i]))
            cur, quoted = None, False
            i += 1
            continue
        if c == "#" and cur is None:
            break
        if cur is None:
            cur, start = "", i
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                return None
            cur, quoted, i = cur + text[i + 1:j], True, j + 1
        elif c == '"':
            j, buf = i + 1, ""
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf += text[j:j + 2]
                    j += 2
                    continue
                if text[j] == "`" or text.startswith("$(", j) or (text[j] == "$" and not re.match(
                        r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})", text[j:])):
                    return None
                buf += text[j]
                j += 1
            if j >= n:
                return None
            cur, quoted, i = cur + buf, True, j + 1
        elif c == "\\":
            if i + 1 >= n:
                return None                                   # a continuation: not a one-line command
            cur, quoted, i = cur + text[i + 1], True, i + 2
        elif c == "$" and text.startswith("$(", i) and not text.startswith("$((", i) and depth < 2:
            k, lvl = i + 2, 1                                 # a closed `$( plain command )`: runs in a subshell
            while k < n and lvl:
                lvl += {"(": 1, ")": -1}.get(text[k], 0)
                k += 1
            if lvl or not _plain_text(text[i + 2:k - 1], depth + 1):
                return None
            cur, quoted, i = cur + text[i:k], True, k
        elif c == "`" and depth < 2:
            k = text.find("`", i + 1)
            if k < 0 or not _plain_text(text[i + 1:k], depth + 1):
                return None
            cur, quoted, i = cur + text[i:k + 1], True, k + 1
        elif c == "$":
            m = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})", text[i:])
            if not m:
                return None
            cur, quoted, i = cur + m.group(0), True, i + len(m.group(0))
        elif c in _SHELL_WORDCHARS:
            cur, i = cur + c, i + 1
        else:
            return None
    if cur is not None:
        out.append((cur, quoted, text[start:]))
    return out


def _plain_words(words):
    """The words of a line run NO COMMAND: every word is an assignment NAME=value / NAME+=value (iteration 4). Any command
    word -- a builtin or not, quoted or not -- may be a function or an alias of the SOURCING shell (the file cannot say),
    or end, interrupt or reconfigure that shell (`break` in a file sourced from a loop, `test -v 'A[E]'`, whose subscript
    is arithmetic, `pkill -P $PPID`); so is `!` / `time` before one."""
    return all(re.match(r"[A-Za-z_][A-Za-z0-9_]*\+?=", raw) for _w, _q, raw in words)


def _plain_text(text, depth=0):
    words = _shell_words(text, depth)
    return words is not None and _plain_words(words)


def _shell_plain(u):
    """True when `u` (one logical line outside the block) runs no command and assigns no variable the shell itself acts on
    (a shell then reads the lines after it as this model does) -- or is a complete one-line array assignment."""
    if "\r" in u.text:
        return False
    if u.keys.intersection(_SHELL_MODE_KEYS):
        return False                                          # EW3-01: a variable the shell itself acts on
    if _SHELL_ARRAY_CLOSED.match(u.text):
        return True
    return _plain_text(u.text)


def _shell_stop(u, later, key):
    """For a NOT plain line `u` (every earlier outside line being plain): (kind, message) -- 'stop' where it is CERTAIN a
    shell assigns no later line and the line itself runs nothing (a syntax error), 'stop-runs' where no later line runs
    but the line's own command RUNS (`exit`, `return`, `exec CMD`, a here-document with no delimiter line: its effect is
    not modelled -- it may be a function of the sourcing shell), 'freeze' where it certainly cannot reassign `key`, else
    'open' (what a shell assigns after it is not modelled). `set -n` is 'open': an interactive shell ignores it. Messages
    name line numbers and shell keywords only, never the line's text (it may carry a value: W4)."""
    t, n = u.text, u.line
    stops = "a shell stops reading the file at line %d " % n
    if u.construct.startswith("a line starting with ';'"):
        return "stop", stops + "(%s)" % u.construct
    if u.construct.startswith("an unterminated"):
        # a shell's quote runs on to the NEXT such quote anywhere later in the file -- the managed block's own
        # `CUDA_VISIBLE_DEVICES='1'` included (iteration 4, found by fuzz4.py: `APOS=it's` before the block left a shell
        # without the block's pin while this model said it stopped): certain only when no later line holds one
        q = "'" if "' quote" in u.construct else '"'
        if not any(q in v.text for v in later):
            return "stop", stops + "(%s)" % u.construct
    if u.construct == _WHY[")"]:
        return "stop", stops + "(a ')' that closes nothing: a syntax error)"
    first = re.match(r"[ \t]*(\(\(|\)\)|;;|&&|\|\||\|&|[;&|()]|[^\s;&|()<>]+)", t)
    first = first.group(1) if first else ""
    rest = t.strip()[len(first):].strip() if first else ""
    if first in _SHELL_CLOSERS:
        return "stop", stops + "(its first word '%s' is a syntax error there)" % first
    if first in ("exit", "return") and re.match(r"(?:\d+)?[ \t]*(?:#.*)?\Z", rest):
        return "stop-runs", stops + "('%s' ends the sourcing)" % first
    if first == "exec" and re.match(r"[^\s<>&|;#0-9]", rest) and _SHELL_CALM.match(_uncomment(_unquoted(rest))):
        return "stop-runs", stops + "('exec' replaces the shell)"
    if first in ("readonly", "declare", "typeset"):
        ws = _shell_words(t) or []
        flags = "".join(w for w, q, _r in ws[1:] if not q and w.startswith("-"))
        names = [w.split("=", 1)[0] for w, _q, _r in ws[1:] if not w.startswith(("-", "+"))]
        if (first == "readonly" or "r" in flags) and key in names:
            return "freeze", "a shell cannot reassign it after line %d, which makes it read-only" % n
    # a construct opened on this line: CERTAIN to stop only when its closer is on no later line (comments and quoted
    # text aside) -- bash then reads to the end of the file and stops with a syntax error, having run none of it
    uq = _uncomment(_unquoted(t))                             # the grammar a shell parses: no quoted text, no comment
    closer, what = None, ""
    if "<<" in re.sub(r"<<<", "", uq):
        m = re.search(r"(?<!<)<<(-?)[ \t]*(['\"]?)([A-Za-z0-9_]+)\2", t[:len(uq)])
        if m and _SHELL_CALM.match(uq[:uq.find("<<")]) and uq.count("<<") == 1 and _SHELL_CALM.match(uq[m.end():]):
            for v in later:
                if (v.text.lstrip("\t") if m.group(1) else v.text) == m.group(3):
                    return "open", ("a shell reads lines %d-%d as the text of a here-document opened on line %d -- what "
                                    "it assigns after line %d is not modelled" % (n + 1, v.line, n, n))
            return "stop-runs", stops + "(the here-document opened there has no delimiter line: it takes the rest of the file)"
    else:
        toks = re.findall(r"\(\(|\)\)|\[\[|\]\]|[{}()]|[A-Za-z_][A-Za-z0-9_]*", uq)
        fam = None
        if first in ("for", "while", "until", "select"):
            fam = (("for", "while", "until", "select"), "done", "'%s'" % first)
        elif first in ("if", "case", "[[", "((", "(", "{"):
            fam = ((first,), _SHELL_OPENERS[first], "'%s'" % first)
        elif first == "function" or re.match(r"[ \t]*[A-Za-z_][A-Za-z0-9_-]*[ \t]*\([ \t]*\)", t):
            fam = (("{",), "}", "function definition")
        elif u.construct == _WHY["("]:
            fam = (("(",), ")", "array '('")
        elif "$(" in uq:
            fam = (("(",), ")", "command substitution '$('")
        elif "${" in uq:
            fam = (("{",), "}", "parameter expansion '${'")
        at = None
        if fam is not None:
            opens = sum(toks.count(o) for o in fam[0]) + (1 if fam[2] == "function definition" and "{" not in toks else 0)
            if opens > toks.count(fam[1]):
                closer, what = fam[1], fam[2]
                loc = {"array '('": "=(", "command substitution '$('": "$(", "parameter expansion '${'": "${"}.get(what)
                if what == "function definition":
                    loc = "{" if "{" in toks else ")"
                elif loc is None:
                    loc = first
                at = uq.find(loc) + len(loc) if loc in uq else None
        elif uq.count("`") % 2 == 1:
            closer, what, at = "`", "command substitution '`'", uq.find("`") + 1
    if closer is not None:
        # the rest of the line after the opener, and every later line, must be CALM for the stop to be certain
        calm = at is not None and _SHELL_CALM.match(uq[at:]) is not None
        for v in later:
            vt = _uncomment(_unquoted(v.text))
            if closer in (vt if len(closer) == 1 else re.findall(r"[A-Za-z_]+|\]\]|\)\)", vt)):
                calm = False
                break
            calm = calm and v.end_line == v.line and _SHELL_CALM.match(vt) is not None
        if calm:
            return "stop", stops + "(the %s opened there is never closed: a syntax error at the end of the file)" % what
    return "open", ("a shell's reading of the lines after line %d is not modelled (it holds shell grammar, or a command "
                    "that changes how the shell runs the lines after it) -- what it assigns after line %d is not modelled"
                    % (n, n))


def _candidate_keys(text):
    ks = set(re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)[ \t]*\+?=", text))
    ks.update(re.findall(r"\bunset[ \t]+(?:-[vf][ \t]+)?([A-Za-z_][A-Za-z0-9_]*)", text))
    return ks


def _shell_reading(line):
    """(kind, bash_view, construct) for ONE physical line (no newline). kind is 'modelled' or 'unmodelled';
    bash_view is None (assigns nothing) or (key, value)."""
    s = line.lstrip(" \t")
    if s == "":
        return "modelled", None, ""
    if s[0] == "#":
        if s.endswith("\\"):
            return "unmodelled", None, ("a comment line ending in a backslash (systemd before v254 continues the "
                                        "comment onto the next line)")
        return "modelled", None, ""
    if s[0] == ";":
        return "unmodelled", None, ("a line starting with ';' (a comment to systemd, a syntax error to a shell, "
                                    "which stops reading the file there)")
    m = re.match(r"(export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\Z", s, re.S)    # re.S: a quoted value may span lines
    if not m:
        if "=" in s:
            return "unmodelled", None, ("an assignment a shell does not read as KEY=value (whitespace around '=', an "
                                        "invalid name, or a command before it)")
        return "unmodelled", None, "a line that is not KEY=value (systemd ignores it; a shell runs it as a command)"
    exported, key, rest = bool(m.group(1)), m.group(2), m.group(3)
    pos, val = 0, []
    while pos < len(rest):
        ch = rest[pos]
        if ch == "'":
            j = rest.find("'", pos + 1)
            if j < 0:
                return "unmodelled", None, "an unterminated ' quote (systemd reads on to the next quote or the end of the file)"
            val.append(rest[pos + 1:j])
            pos = j + 1
        elif ch == '"':
            # inside double quotes bash and systemd BOTH drop a backslash before \\ \" $ ` and keep it before anything
            # else (bash(1) QUOTING; systemd's DQ_ESC, measured through libsystemd-shared): an escaped value reads alike
            # (iteration 4 -- the spelling a refusal and the writer hand a value that holds a '); an UNESCAPED $ or `
            # is expanded by a shell and kept by systemd
            j, buf = pos + 1, []
            while j < len(rest) and rest[j] != '"':
                if rest[j] == "\\" and j + 1 < len(rest):
                    buf.append(rest[j + 1] if rest[j + 1] in '"\\$`' else rest[j:j + 2])
                    j += 2
                    continue
                if rest[j] in "$`":
                    return "unmodelled", None, "a double-quoted value holding " + _WHY[rest[j]]
                buf.append(rest[j])
                j += 1
            if j >= len(rest):
                return "unmodelled", None, 'an unterminated " quote (systemd reads on to the next quote or the end of the file)'
            val.append("".join(buf))
            pos = j + 1
        elif ch in _SAFE_WORD:
            val.append(ch)
            pos += 1
        elif ch in " \t":
            break
        else:
            return "unmodelled", None, _WHY.get(ch, "a character outside the plain set [A-Za-z0-9_@%+,./:=#-] in an "
                                                    "unquoted value (never printed: the file may carry a token)")
    tail = rest[pos:]
    comment = False
    if tail.strip(" \t"):
        if re.match(r"[ \t]+#", tail):
            comment = True
        else:
            return "unmodelled", None, ("whitespace inside an unquoted value (systemd keeps it; a shell assigns only "
                                        "for one command, running the rest)")
    construct = []
    if exported:
        construct.append("it is written with `export`, which systemd's EnvironmentFile= drops (the key 'export %s' "
                         "is not a valid name)" % key)
    if comment:
        construct.append("it carries an inline '#' comment, which systemd's EnvironmentFile= keeps as part of the "
                         "value (only a line STARTING with '#' is a comment)")
    return "modelled", (key, "".join(val)), "; and ".join(construct)


# ------------------------------------------------------------------------------ the reader
def parse(data, begin=None, end=None):
    """Read `data` (bytes) as systemd does and classify every logical line against a shell's reading.
    Returns a dict: error / error_line (systemd rejects the WHOLE file), units, outside and effective
    ({key: (value, line)}, LAST-WINS, valid names only; outside = not between the managed-block markers)."""
    res = {"error": None, "error_line": None, "units": [], "outside": {}, "effective": {}, "markers": [],
           "long_line": None, "eof_open": _tokenize(data + b"\n")[1][-1] != PRE_KEY, "eof_open_line": None}
    if b"\x00" in data:
        res["error"], res["error_line"] = "a NUL byte", data[:data.index(b"\x00")].count(b"\n") + 1
    pushes, line_states = _tokenize(data)
    raw_lines = data.split(b"\n")
    if raw_lines and raw_lines[-1] == b"":
        raw_lines.pop()
    starts, off = [], 0
    for rl in raw_lines:
        starts.append(off)
        off += len(rl) + 1
    units, inside, cur = [], False, None
    for i, rl in enumerate(raw_lines):
        text = rl.decode("utf-8", "surrogateescape")
        st = line_states[i] if i < len(line_states) else PRE_KEY
        stripped = text.strip()
        if st == PRE_KEY or cur is None:
            cur = Unit(i + 1)
            cur.text = text
            units.append(cur)
            if begin is not None and st == PRE_KEY and stripped in (begin, end):
                cur.marker = True
                res["markers"].append((i + 1, "begin" if stripped == begin else "end"))
                inside = stripped == begin
                continue
            cur.inside = inside
        else:
            cur.end_line = i + 1
            cur.text += "\n" + text
            if begin is not None and stripped in (begin, end):
                cur.cls = "marker-in-value"
                cur.construct = ("a managed-block marker line (line %d) lies inside a value that starts on line %d; the "
                                 "block writer would cut that value in two" % (i + 1, cur.line))
    # attribute each push to its unit; the manager keeps only valid names (strv_env_clean_with_callback)
    by_line = {}
    for u in units:
        for ln in range(u.line, u.end_line + 1):
            by_line[ln] = u
    for k, v, so in pushes:
        ln = data[:so].count(b"\n") + 1
        u = by_line.get(ln)
        try:
            ks, vs = k.decode("utf-8"), v.decode("utf-8")
            if not (_sd_unichars_ok(ks) and _sd_unichars_ok(vs)):
                raise UnicodeDecodeError("utf-8", k + v, 0, 1, "a Unicode noncharacter (systemd rejects it)")
        except UnicodeDecodeError:
            if res["error"] is None:
                res["error"], res["error_line"] = ("invalid UTF-8 in an assignment (systemd refuses the file, "
                                                   "not the line)"), ln
            continue
        if len(k) + 1 + len(v) > _ARG_MAX_FLOOR - 3 and res["long_line"] is None:
            res["long_line"] = ln
        if u is not None and _NAME.match(ks):
            u.keys.add(ks)
            u.sd.append((ks, vs))
    for u in units:
        if u.marker:
            continue
        u.keys.update(_candidate_keys(_unquoted(u.text)))     # quoted text assigns nothing ('X=\'PS4=1\'': X)
        if u.cls == "marker-in-value":
            continue
        if u.end_line > u.line:
            kind, bash, construct = _shell_reading(u.text) if "\r" not in u.text else ("unmodelled", None, "")
            if kind == "modelled" and "inline '#' comment" in construct and u.text.split("\n", 1)[0].rstrip().endswith("\\"):
                # `KEY=v # note\` + a next line: systemd keeps '# note' in the value and the backslash JOINS the next line
                # into it; a shell ends the comment at the line end and reads the next line ON ITS OWN (reflector-5,
                # measured: 24 of 25 accepted files a shell read differently -- a pin line swallowed for the units only)
                u.cls, u.bash = "unmodelled", None
                u.construct = ("an inline '#' comment ending in a backslash: systemd joins lines %d-%d into one value, "
                               "while a shell ends the comment at the end of line %d and reads line %d on its own"
                               % (u.line, u.end_line, u.line, u.line + 1))
                continue
            if kind == "modelled" and not construct.startswith("it is written with"):
                # ONE assignment whose quoted value spans the lines (`NOTE='a` ... `b'`): a shell reads it as systemd does
                # -- or, with an inline comment after the closing quote, differently (divergent, below)
                u.bash = bash
                if bash is not None:
                    u.keys.add(bash[0])
                sd = u.sd[-1] if u.sd else None
                u.cls, u.construct = ("safe", "") if bash == sd else ("divergent", construct or "a shell and systemd "
                                                                      "read this line differently")
                continue
            u.cls, u.bash = "unmodelled", None
            u.construct = ("a value that continues onto the next line (a backslash-newline or an open quote): systemd "
                           "joins lines %d-%d" % (u.line, u.end_line))
            continue
        if "\r" in u.text:
            u.cls, u.bash = "unmodelled", None
            u.construct = "a carriage return (a CRLF line end: systemd ends the line there, a shell keeps it in the value)"
            continue
        if u.line == 1 and u.text.startswith("﻿"):
            u.cls, u.bash = "unmodelled", None
            u.construct = "a byte-order mark (systemd reads it as part of the first key and drops the line)"
            continue
        try:
            u.text.encode("utf-8")
        except UnicodeEncodeError:
            if u.sd or "=" in u.text:
                u.cls, u.bash, u.construct = "unmodelled", None, "bytes that are not UTF-8"
            continue
        kind, bash, construct = _shell_reading(u.text)
        if kind == "unmodelled":
            u.cls, u.bash, u.construct = "unmodelled", None, construct
            continue
        u.bash = bash
        if bash is not None:
            u.keys.add(bash[0])
        sd = u.sd[-1] if u.sd else None
        if bash == sd:
            u.cls, u.construct = "safe", ""
        else:
            u.cls, u.construct = "divergent", construct or "a shell and systemd read this line differently"
    res["units"] = units
    if res["eof_open"]:
        # the line whose value is still open at the end of the file (an open quote, a trailing backslash): what an
        # APPENDED line would be read into (reference-host round, env-file writer pass: the refusal names it)
        res["eof_open_line"] = units[-1].line if units else 1
    if res["error"] is None:
        for u in units:
            for k, v in u.sd:
                res["effective"][k] = (v, u.line)
                if not u.inside:
                    res["outside"][k] = (v, u.line)
    return res


def read_file(path, begin=None, end=None):
    with open(path, "rb") as fh:
        return parse(fh.read(), begin, end)


# ------------------------------------------------------------------------------ the units' own pin rules
_VD = None


# The package's organs/ directory, beside install/ -- in the checkout AND in an installed brain (both lay install/ and
# organs/ as siblings). Run by hand without --organs, this reader used to die with ModuleNotFoundError: voidloom_device
# (env-file writer pass, W7: found at the landing of the reference-host round, #21961).
SIBLING_ORGANS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "organs")


class OrgansMissing(Exception):
    """organs/voidloom_device.py (the units' own pin rules) cannot be imported."""


def _device_rules(organs):
    """organs/voidloom_device.py -- the resolver every unit runs -- imported for its own rules. `organs` None/empty:
    the package's own organs/ beside install/ (SIBLING_ORGANS)."""
    global _VD
    if _VD is None:
        where = organs or SIBLING_ORGANS
        if not os.path.isfile(os.path.join(where, "voidloom_device.py")):
            raise OrgansMissing("the units' resolver organs/voidloom_device.py is not in %s -- pass --organs <the "
                                "package's organs/ directory>" % where)
        if where not in sys.path:
            sys.path.insert(0, where)
        import voidloom_device  # noqa: E402  (stdlib-only by contract)
        _VD = voidloom_device
    return _VD


def units_pin(key, value, organs):
    """The value the units' resolver derives from `value` (as systemd delivered it) for a pin key, or None when
    it REJECTS it. CUDA_VISIBLE_DEVICES is the ambient layer (validated raw, then stripped); WIZARD_CUDA_DEVICE
    is the env layer (an inline comment stripped first -- the resolver's own leniency)."""
    vd = _device_rules(organs)
    if key == "CUDA_VISIBLE_DEVICES":
        return value.strip() if vd._valid(value) else None
    v = vd._strip_inline_comment(value)
    if getattr(vd, "declares_no_gpu", None) and vd.declares_no_gpu(v):
        return ""                  # Drew's ruling: WIZARD_CUDA_DEVICE=-1 / none -> no GPU (CUDA_VISIBLE_DEVICES='')
    return v if vd._valid(v) else None


class _Sentinel(object):
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


ABSENT = _Sentinel("ABSENT")      # the reading assigns nothing
UNKNOWN = _Sentinel("UNKNOWN")    # the shell's reading is not modelled
BLOCK = _Sentinel("BLOCK")        # the value the managed block assigns (this run writes it: the check runs before)
REJECTED = _Sentinel("REJECTED")  # systemd assigns it, and the units' resolver rejects the value


def _q(v):
    if v is ABSENT:
        return "nothing (no assignment)"
    if v is UNKNOWN:
        return "a value this installer does not model"
    if v is REJECTED:
        return "nothing usable (their resolver rejects it)"
    if v is BLOCK:
        return "the managed block's own value"
    return "'%s'" % v


def _assigns(u, key):
    """True when line `u` is an ASSIGNMENT of `key` in some form -- systemd reads one there, a shell does, or the line
    writes `key=` / `key+=` (whitespace around '=', a command around it): its remedy is a bare KEY=value line. A line
    that only NAMES the key (`unset KEY`, `read KEY`, `[ "$KEY" = 1 ]`, `: ${KEY:=0}`) is a command: take it out."""
    return (any(k == key for k, _v in u.sd) or (u.bash is not None and u.bash[0] == key)
            or re.search(r"(?<![A-Za-z0-9_${])%s[ \t]*\+?=(?!=)" % re.escape(key), u.text) is not None)


def _walk(res, key, organs=None):
    """readings(), with the REMEDY attributed where each decision is made (iteration-2 reflection EW2-03: a refusal
    named the pin line and told the owner to 'write it as a bare KEY=value line' -- a line that was already bare -- while
    the line to change was an earlier one). Returns a dict: sd_v, units_v, sh_v, line, construct (as readings), sd_line /
    sd_construct (the line systemd reads the value from, and what is wrong with THAT line), and fix:
      ("bare", L)      line L is an assignment of the key the two read differently: write it as a bare KEY=value line
      ("command", L)   line L is a shell command that names the key: take it out
      ("shell", N)     line N runs a command, or sets a variable the shell acts on, which this reader does not model
                       (before the key's line, after it, or with no line for the key at all): change or take out line N
                       (_remedy: quote it when systemd hands a unit a variable from it -- EW3-02 -- never delete it)
      ("stop", N, P)   a shell certainly stops reading at line N (a syntax error: it runs nothing); line P, after it, is
                       the value systemd reads: take N out (or quote it, when systemd reads a variable from it), or move
                       P above it
      None             nothing disagrees, or the value line itself is the fix (the REJECTED branch)"""
    outside = [u for u in res["units"] if not u.marker and not u.inside]
    sd = res["outside"].get(key)
    sd_v = sd[0] if sd else ABSENT
    if sd is None:
        units_v = ABSENT
    elif key in PIN_KEYS:
        u = units_pin(key, sd_v, organs)
        units_v = REJECTED if u is None else u
    else:
        units_v = sd_v
    sh_v, line, construct, fix, sh_line = ABSENT, (sd[1] if sd else None), "", None, None
    stop, stop_why, stop_kind = None, "", None
    r1 = False              # the disagreement is only that a line that never names the key may assign it (R1)
    later_all = [u for u in res["units"] if not u.marker]
    word = re.compile(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(key))
    # THE MANAGED BLOCK IS READ TOO (iteration 4): with no owner pin declared outside it, the block this run writes holds
    # the CUDA_VISIBLE_DEVICES every unit reads (install.sh build_env_block) -- at the OLD block's place, or appended
    # after the last line. A shell must REACH it: a certain stop before it leaves the shell without the units' pin.
    block_at = res["markers"][0][0] if res["markers"] else None           # None: appended after the last line
    reach = {"due": key == "CUDA_VISIBLE_DEVICES" and not any(k in res["outside"] for k in PIN_KEYS)}
    if reach["due"]:
        units_v = BLOCK

    def reach_block():
        nonlocal sh_v, line, construct, fix
        reach["due"] = False
        if stop is None:
            sh_v = BLOCK
        elif stop_kind == "stop":
            line, fix = stop.line, ("block", stop.line)
            construct = "%s -- before the managed block, which holds the %s every unit reads" % (stop_why, key)
    for u in outside:
        if reach["due"] and block_at is not None and u.line > block_at:
            reach_block()
        # an UNMODELLED line that names the key anywhere may assign it by a route other than KEY=value (`read KEY`,
        # `: ${KEY:=0}`, `printf -v KEY`): the shell's reading of the key there is not modelled
        touches = key in u.keys or (u.cls == "unmodelled" and bool(word.search(u.text)))
        if stop is not None and touches:
            r1 = False
            if stop_kind == "stop":
                # a shell runs NO line after a CERTAIN stop (EW2-02): its value stays what it was before the stop, and
                # only a later line systemd reads differently can make the two disagree -- that line (systemd's own,
                # below) is the value line; the line to change is the stop
                line, construct, fix = u.line, stop_why, ("stop", stop.line, u.line)
            elif stop_kind == "stop-runs":
                # no later line runs, and the stop's own command ran (its effect not modelled): sh_v is UNKNOWN already.
                # A later line systemd READS the key from is a value the shell never reaches -- there the stop IS the
                # reason; a later line that only names the key changes nothing, and the command's reason stands
                if any(k == key for k, _v in u.sd):
                    line, construct, fix = u.line, stop_why, ("shell", stop.line)
            else:
                sh_v, line, construct, fix = UNKNOWN, u.line, stop_why, ("shell", stop.line)
            continue
        mode = sorted(u.keys.intersection(_SHELL_MODE_KEYS))
        runs_cmd = (u.cls == "unmodelled" or bool(mode)
                    or (u.cls == "divergent" and _EXPORT_LINE.match(u.text) is not None))   # `export` is a command too
        if runs_cmd and stop is None and not _shell_plain(u):
            # ADVISORY 7's class: after a line that runs a command (or sets a variable the shell itself acts on), a
            # shell's reading is not modelled -- or, where it is certain, it stops / cannot reassign the key (_shell_stop)
            kind, stop_why = _shell_stop(u, [v for v in later_all if v.line > u.line], key)
            stop, stop_kind = u, kind
            if kind == "stop-runs":
                # `exit` / `return` / `exec CMD` / a here-document's command RUNS (and may be a function of the sourcing
                # shell): what it does to the key -- named on the line or not (`unset $K <<EOF`) -- is not modelled, and
                # no later line runs (iteration 4: a line with no pin before it included). The reason given is the
                # command; that the shell then stops is said where it matters -- of a LATER line the shell never reads
                # (stop_why, above) -- never as the reason for a value it read before (iteration 4: the EW2-02 fuzzes
                # read 'a shell stops reading' as a claim about the pin in `KEY=1` + `return`)
                prev, sh_v, line, fix = sh_v, UNKNOWN, u.line, ("shell", u.line)
                if prev is ABSENT:
                    construct = ("a shell runs line %d, a command this installer does not model that ends the sourcing -- it "
                                 "may assign %s there without naming it" % (u.line, key))
                else:
                    construct = ("a shell runs line %d, a command this installer does not model that ends the sourcing, after "
                                 "%s assigns %s -- it may change %s there without naming it"
                                 % (u.line, "the managed block" if prev is BLOCK else ("line %d" % sh_line if sh_line else "an earlier line"),
                                    key, key))
                continue
            if kind == "stop":
                # a shell never runs the line it stops at: whatever systemd assigns there, the shell's value stays
                if touches:
                    line, construct, fix = u.line, stop_why, ("stop", u.line, u.line)
                continue
            if kind == "open" and (not touches or mode):
                # the line runs shell grammar this reader does not compute: it may change the key WITHOUT NAMING IT --
                # assigned earlier (`unset $K`, `read -r $K`, `printf -v "$K" 7`, `eval K""EY=7` -- EW2-01) or not at
                # all (a file with no pin line, or one after the managed block's own CUDA_VISIBLE_DEVICES: iteration 4)
                prev, sh_v, line, fix = sh_v, UNKNOWN, u.line, ("shell", u.line)
                r1 = not touches
                if mode:
                    construct = ("line %d sets %s, a variable a shell itself acts on (%s) -- what a shell assigns after it "
                                 "is not modelled" % (u.line, mode[0], _MODE_WHY[mode[0]]))
                elif prev is ABSENT:
                    construct = ("a shell runs line %d, a command this installer does not model -- it may assign %s there "
                                 "without naming it" % (u.line, key))
                elif prev is BLOCK:
                    construct = ("a shell runs line %d, a command this installer does not model, after the managed block "
                                 "(from line %d) assigns %s -- it may change %s there without naming it"
                                 % (u.line, block_at, key, key))
                else:
                    construct = ("a shell runs line %d, a command this installer does not model, after %s assigns %s -- it "
                                 "may change %s there without naming it"
                                 % (u.line, "line %d" % sh_line if sh_line else "an earlier line", key, key))
        if not touches:
            continue
        r1 = False
        if u.cls in ("unmodelled", "marker-in-value"):
            sh_v, line, construct = UNKNOWN, u.line, u.construct
            fix = ("bare" if _assigns(u, key) else "command", u.line)
            sh_line = u.line
        elif u.bash is not None and u.bash[0] == key:
            sh_v, sh_line = u.bash[1], u.line
            if u.cls == "divergent":
                line, construct, fix = u.line, u.construct, ("bare", u.line)
            elif sd is not None and u.line == sd[1]:
                line, construct, fix = u.line, "", None
    if reach["due"]:
        reach_block()
    sd_construct = ""
    if sd is not None:
        su = [u for u in outside if u.line == sd[1]]
        sd_construct = su[0].construct if su else ""
        if not construct and sd_construct:
            line, construct, fix = sd[1], sd_construct, ("bare", sd[1])
        if fix is not None and fix[0] == "stop" and sd[1] > fix[1]:
            # the value systemd reads is its OWN line after the stop, not whichever later line last named the key
            line, fix = sd[1], ("stop", fix[1], sd[1])
    return {"sd_v": sd_v, "units_v": units_v, "sh_v": sh_v, "line": line, "construct": construct, "fix": fix,
            "sd_line": sd[1] if sd else None, "sd_construct": sd_construct, "r1": r1 and sh_v is UNKNOWN}


def readings(res, key, organs=None):
    """The three readings of `key` outside the managed block, each LAST-WINS over the outside lines:
    (systemd_value, units_value, shell_value, line, construct). systemd_value is what EnvironmentFile= hands
    every unit (or ABSENT); units_value is what the units' resolver makes of it for a pin key (REJECTED when it
    cannot read it) and the systemd value otherwise; shell_value is what `set -a; . FILE` assigns (UNKNOWN once an
    unmodelled line touches the key). line/construct name the line that decided the disagreement, if any."""
    w = _walk(res, key, organs)
    return w["sd_v"], w["units_v"], w["sh_v"], w["line"], w["construct"]


# The spelling a refusal hands the owner for a value systemd reads from a line a shell reads as grammar (EW3-02): it keeps
# the value -- the words never tell the owner to delete a variable every unit receives. Both spellings are read alike by
# systemd's EnvironmentFile= rules and by a shell (inside double quotes both drop the backslash before \\ \" $ ` and keep
# it before anything else); _assignment_for writes the same two.
_QUOTE_ADVICE = ("keep the value and quote it: write %s='<its value>' -- or, when the value holds a ', "
                 "%s=\"<its value>\" with a backslash before each \\, \", $ and ` in it")


def _line_info(outside, eof_open_line=None, owner=None):
    """{line: (keys systemd hands every unit from it, variables the shell itself acts on that it ASSIGNS -- by systemd's
    reading or a shell's, never a name that only appears in its text (reflector-4, measured: `BS=a\\` + `UID=0` is ONE
    value, BS, to both readers; the words 'it sets UID ... take it out' deleted BS), the key of an `export KEY=v` line,
    whether its value is still open at the end of the file, the LAST physical line of its value, and the keys whose LAST
    owner assignment is this line -- the value the units read (iteration 5, EW4-09: 'every unit receives K from it' was
    said of a line a later line overrides))} -- key NAMES only, never a value (W4). `owner`: parse()'s res["outside"]."""
    info = {}
    for u in outside:
        exp = u.bash[0] if (u.cls == "divergent" and u.bash and _EXPORT_LINE.match(u.text)) else None
        sd_keys = sorted(set(x for x, _v in u.sd))
        assigned = set(sd_keys) | (set([u.bash[0]]) if u.bash else set())
        last = [x for x in sd_keys if owner is None or (owner.get(x) or (None, 0))[1] == u.line]
        info[u.line] = (sd_keys, sorted(assigned.intersection(_SHELL_MODE_KEYS)), exp, u.line == eof_open_line,
                        u.end_line, last, any("\n" in v for _x, v in u.sd),
                        u.construct.startswith("an inline '#' comment ending in a backslash"))
    return info


def _where(n, end):
    """'The line to change is line N' -- or, for one value that spans lines N..M, the whole span (iteration 5, EW4-09:
    the words named line N of a two-line value, and an owner who changed only that line lost the value's key)."""
    if end and end > n:
        return "The lines to change are lines %d-%d (one value that spans them)" % (n, end)
    return "The line to change is line %d" % n


def _remedy_named(fix, k):
    """The remedy for a line that names the key itself: an assignment the two read differently, or a command."""
    if fix[0] == "bare":
        return ("The line to change is line %d. Write it as a bare %s=<value> line (any comment on its own line); then "
                "re-run." % (fix[1], k))
    return ("The line to change is line %d: it is a shell command that names %s, not a %s=value line -- take it out "
            "of this file (to set %s, write a bare %s=<value> line instead); then re-run." % (fix[1], k, k, k, k))


def _remedy(fix, k, info=None):
    """The next step of a refusal, in words an owner who is not a shell expert can act on: WHICH line to change and
    WHAT to do with it (EW2-03) -- and never an instruction that deletes a variable systemd hands the units (EW3-02: 'take
    it out -- systemd ignores it' was said of `URL=...&b=2`, `LIST=a;b`, `A=(a b` -- lines every unit receives a variable
    from). Line numbers and key names only -- never a line's text (it may carry a value: W4)."""
    if fix is None:
        return "Write it as a bare %s=<value> line (any comment on its own line)." % k
    n = fix[1]
    sd_keys, mode, exp, open_at_eof, end, last, breaks, cbs = (info or {}).get(n, ([], [], None, False, n, None, False,
                                                                                  False))
    if cbs and (sd_keys or mode):
        return ("The line to change is line %d: it carries an inline '#' comment that ends in a backslash, so systemd joins "
                "line %d into the value of %s while a shell reads line %d on its own -- end its value on that line "
                "(remove its trailing backslash, or close its quote); better, put the comment on a line of its own; then "
                "re-run." % (n, n + 1, (sd_keys or mode)[0], n + 1))
    if breaks and not open_at_eof and (sd_keys or mode) and end and end > n:
        # a quoted value that holds a line break (its quote opens on line n and closes only on a later line): it has no
        # one-line spelling, so 'keep the value and quote it' re-quotes the same lines and never converges (reflector-5,
        # measured: a LOOP on seeds 1231 and 9876). End it on line n; whatever is left is named next.
        return ("The line to change is line %d: the value of %s that starts there holds a line break (its quote opens there "
                "and closes only on line %d), so it swallows the lines between -- end it on line %d (close its quote "
                "there); then re-run." % (n, (sd_keys or mode)[0], end, n))
    if open_at_eof and (sd_keys or mode):
        # a value still open at the end of the file swallows every later line (a pin line included): 'keep the value and
        # quote it' re-quotes the swallowed lines and never converges (reflector-4, measured: a LOOP), and 'take it out'
        # of a variable the shell acts on would take the swallowed lines with it; nor is the line a bare KEY=value line
        # to rewrite -- the owner meant the value to end on its own line. End it FIRST; whatever is left is named next.
        return ("The line to change is line %d: the value of %s that starts there is still open at the end of the file (an "
                "open quote, or a trailing backslash), so it swallows every line after it -- end it on line %d (close its "
                "quote there, or remove its trailing backslash); then re-run." % (n, (sd_keys or mode)[0], n))
    kept = [x for x in sd_keys if x not in _SHELL_MODE_KEYS]
    recv = [x for x in kept if last is None or x in last]
    if kept and not recv and not mode:
        # every key systemd reads from line n is assigned again by a LATER owner line, and the units read that one
        # (iteration 5, EW4-09: 'every unit receives K from it' was said of an overridden line): taking line n out
        # changes nothing any unit receives, and ends what a shell reads differently there
        return ("%s: a later line assigns %s again, and every unit reads that later line, not this one -- take %s out "
                "of this file; then re-run." % (_where(n, end), ", ".join(kept), "it" if not end or end <= n else "them"))
    if fix[0] in ("bare", "command"):
        if recv and k not in kept:
            # line n is where systemd reads ANOTHER key (a value spanning lines swallowed the one that names k): a bare
            # k line written over it would delete that key from every unit -- quote the value systemd reads instead
            return ("%s: every unit receives %s from it, but a shell reads it as shell grammar -- %s; then re-run."
                    % (_where(n, end), ", ".join(recv), _QUOTE_ADVICE % (recv[0], recv[0])))
        if not (mode and k not in sd_keys):
            return _remedy_named(fix, k)
        # the text that names k is inside the value of a variable the SHELL acts on (`UID=0\` + `WIZARD_DISPLAY_INDEX=0`:
        # systemd reads ONE value, UID's -- iteration 5, the generator's own fuzz): 'write it as a bare k line' over it
        # deleted UID without a word; the words name what the lines ARE, below
    if mode:
        m = mode[0]
        return ("%s: %s %s, a variable a shell itself acts on (%s) -- take %s out of this file (if a unit needs %s, give it "
                "to that unit in a drop-in instead: systemctl --user edit <unit>, then an Environment= line); then re-run."
                % (_where(n, end), "it sets" if not end or end <= n else "they set", m, _MODE_WHY[m],
                   "it" if not end or end <= n else "them", m))
    if recv:
        what = ("every unit receives %s from it, but a shell reads it as shell grammar" % ", ".join(recv)
                if fix[0] == "shell" else "every unit receives %s from it, but a shell stops reading the file there "
                "(it reads the line as shell grammar)" % ", ".join(recv))
        return ("%s: %s -- %s; then re-run." % (_where(n, end), what, _QUOTE_ADVICE % (recv[0], recv[0])))
    if fix[0] == "shell" and exp:
        return ("The line to change is line %d: it is written with `export` -- a shell command, and a line systemd "
                "ignores -- write it as a bare %s=<value> line (or take it out of this file); then re-run." % (n, exp))
    if fix[0] == "shell":
        return ("The line to change is line %d: take it out of this file -- systemd ignores it, and while it is there this "
                "installer cannot prove that a shell sourcing the file reads %s as systemd does (if a shell needs that "
                "line, keep it in a file of its own); then re-run." % (n, k))
    if fix[0] == "block":
        return ("The line to change is line %d, where a shell stops reading the file before the managed block (which "
                "holds the %s every unit reads): take it out of this file; then re-run." % (n, k))
    p = fix[2]
    if p == n:
        return ("The line to change is line %d, where a shell stops reading the file: take it out of this file (to set "
                "%s, write a bare %s=<value> line instead); then re-run." % (n, k, k))
    return ("Line %d is written correctly -- the line to change is line %d, where a shell stops reading the file: take "
            "line %d out of this file, or move line %d ABOVE it; then re-run." % (p, n, n, p))


def check(res, path, shape, organs):
    """The installer's policy over one parse. Returns [(level, message)], level REFUSE or WARN.
      REFUSE, every shape: systemd rejects the whole file; a managed-block marker inside a value; a pin key whose
             systemd value the units' resolver REJECTS (they would fall back silently -- to card 0 on a host with no
             other declaration).
      REFUSE in the ambient shape, WARN in the others: a pin key or WIZARD_DISPLAY_INDEX that a shell reads
             differently from the units (P4 and the one-pin chain rest only on a declaration every reader agrees on).
      WARN: every other divergent or unmodelled line outside the managed block -- named by line and key, never by
             value."""
    out = []
    if res["error"]:
        return [("REFUSE", "%s: systemd's EnvironmentFile= rejects this WHOLE file (%s, line %d) -- every unit names "
                           "it with a leading '-', so each would start silently WITHOUT it: no PATH, no pin, no token. "
                           "Fix that line." % (path, res["error"], res["error_line"]))]
    kinds = [kind for _l, kind in res["markers"]]
    # a value that swallows a marker line: name ITS line and what to do FIRST (iteration 4: the generic marker message
    # named no line to change, and the file-ends-in-a-quote message said "appends" of a file that has a block)
    for u in res["units"]:
        # an OWNER value (outside the block) that swallows a marker line -- a value INSIDE the old block is the block's
        # own, and the writer replaces it whole (splice's rule; W1 (ii-b))
        if u.cls == "marker-in-value" and not u.inside:
            return [("REFUSE", "%s line %d: %s. The line to change is line %d: end its value before that marker line "
                               "(close its quote, or remove its trailing backslash); then re-run."
                               % (path, u.line, u.construct, u.line))]
    if kinds not in ([], ["begin", "end"]):
        return [("REFUSE", "%s: %s" % (path, marker_refusal(res)))]
    if not kinds and res["eof_open"]:
        return [("REFUSE", "%s line %d: the file ends inside a quote or after a backslash (the value that starts on "
                           "line %d is still open at the end of the file) -- systemd would read the managed block "
                           "this installer appends as part of that value. Close the quote (or remove the trailing "
                           "backslash), then re-run." % (path, res["eof_open_line"], res["eof_open_line"]))]
    if res["long_line"] is not None:
        return [("REFUSE", "%s line %d: an assignment longer than %d bytes -- systemd drops an assignment longer than "
                           "its manager's ARG_MAX - 3 (and with it any earlier assignment of the key), a limit this "
                           "installer cannot read. The line to change is line %d: shorten it (or move that value out "
                           "of this file); then re-run." % (path, res["long_line"], _ARG_MAX_FLOOR - 3, res["long_line"]))]
    outside = [u for u in res["units"] if not u.marker and not u.inside]
    info = _line_info(outside, res["eof_open_line"] if res["eof_open"] else None, res["outside"])   # EW3-02: key names per line
    named = set()
    for u in outside:
        if u.cls == "marker-in-value":
            out.append(("REFUSE", "%s line %d: %s. The line to change is line %d: end its value before that marker line "
                                  "(close its quote, or remove its trailing backslash); then re-run."
                                  % (path, u.line, u.construct, u.line)))
            named.add(u.line)
    walks = [(k, _walk(res, k, organs)) for k in VALUE_KEYS]
    # a key's OWN disagreement first; then a key whose only disagreement is R1 (a command line that may assign it
    # unnamed) -- once per line: the same line named for another key already carries the same remedy (iteration 4)
    for k, w in [kw for kw in walks if not kw[1]["r1"]] + [kw for kw in walks if kw[1]["r1"]]:
        sd_v, units_v, sh_v, line, construct = w["sd_v"], w["units_v"], w["sh_v"], w["line"], w["construct"]
        if units_v is REJECTED:
            # the value line is systemd's OWN line, and the reason is what is wrong with THAT line -- never an earlier
            # shell line's reading (iteration 3, measured: `set -e` + `KEY=1 # c` said 'because a shell's reading of the
            # lines after line 1 is not modelled')
            sl = w["sd_line"]
            out.append(("REFUSE", "%s line %d: %s reads as '%s' under systemd's EnvironmentFile= rules -- the value every "
                                  "unit receives -- because %s; the units' resolver (organs/voidloom_device.py) REJECTS "
                                  "it and falls back (to card 0 on a host with no other declaration), while a shell that "
                                  "sources the file reads %s. The line to change is line %d. Write it as a bare %s=<index> "
                                  "line (any comment on its own line); then re-run."
                                  % (path, sl, k, sd_v, w["sd_construct"] or "it is not a device index the units accept",
                                     _q(sh_v), sl, k)))
            named.add(sl)
            continue
        if sh_v is units_v or sh_v == units_v:
            continue
        if k in PIN_KEYS and isinstance(sh_v, str) and isinstance(units_v, str) \
                and units_pin(k, sh_v, organs) == units_v:
            # The shell's reading goes through the SAME pin normaliser the units' reading went through before the two
            # are compared (handoff round 1, L4; clean-room proof #28100): WIZARD_CUDA_DEVICE=-1 reads '-1' to a shell
            # and '' to the units' resolver (Drew's ruling: -1 / none mean no GPU), and both mean "no card" -- the
            # same answer spelled twice is agreement, not a divergence that teaches the owner to distrust the file.
            continue
        lvl = "REFUSE" if shape == "ambient" else "WARN"
        fx = w["fix"]
        if w["r1"] and (shape != "ambient" or (fx is not None and fx[1] in named)):
            # R1 (a command line that may assign the key unnamed) binds the ambient shape, where P4 rests on the shell
            # reading the pin as the units do; elsewhere that line gets its own per-line warning below. And once per line.
            continue
        assigned = any(k in u.keys for u in outside)
        if units_v is ABSENT and not assigned:
            verdict = "no line outside the managed block assigns it for the units" + (
                " (they read only the block's own)" if k in res["effective"] else "")
        elif units_v is BLOCK:
            verdict = "the units read the managed block's own (no owner line declares a pin)"
        elif units_v is ABSENT and k in res["effective"]:
            # only the managed block assigns it (the block is rewritten by this run): no OWNER line declares it
            verdict = "no line outside the managed block declares it (the units read only the block's own)"
        elif units_v is ABSENT and k in PIN_KEYS:
            verdict = "it is NOT a declaration: systemd never hands it to the units"
        elif units_v is ABSENT:
            verdict = ("it is NOT a declaration: this installer reads the file as the units' systemd does, which drops "
                       "the line")
        else:
            verdict = "the units read %s" % _q(units_v)
        out.append((lvl, "%s line %d: %s -- %s; %s, while a shell that sources the file reads %s. %s"
                         % (path, line, k, construct or "a shell and systemd read it differently", verdict, _q(sh_v),
                            _remedy(fx, k, info))))
        named.add(line)
        if fx is not None:
            named.add(fx[1])
    for u in outside:
        if u.cls == "safe" or u.line in named:
            continue
        # NAMES ONLY: the keys this line assigns under either reading, and any pin/display name it mentions -- never
        # _candidate_keys' other hits, which are VALUE text (`...Ab/SECRET==` yields 'SECRET'; measured, iteration 4)
        shown = set(k for k, _v in u.sd) | (set([u.bash[0]]) if u.bash else set()) | (u.keys & set(VALUE_KEYS))
        keys = ", ".join(sorted(shown)) or "no valid key"
        if u.cls == "divergent" and u.construct.startswith("it is written with `export`"):
            out.append(("WARN", "%s line %d: 'export %s' is written with `export`; systemd ignores that line -- write "
                                "it as bare KEY=value" % (path, u.line, u.bash[0] if u.bash else keys)))
        elif u.cls == "divergent":
            out.append(("WARN", "%s line %d (%s): %s -- put any comment on its own line" % (path, u.line, keys, u.construct)))
        else:
            out.append(("WARN", "%s line %d (%s): %s -- a shell and the units may read it differently; this installer "
                                "reads it as systemd does" % (path, u.line, keys, u.construct)))
    return out


def verify_block(res):
    """The installer's OWN managed block (a file holding only the block): every assignment must be SAFE -- read
    the same way by systemd and by a shell -- or the writer refuses to write it. Returns [messages]."""
    bad = []
    if res["error"]:
        bad.append("systemd would reject the block (%s, line %d)" % (res["error"], res["error_line"]))
    for u in res["units"]:
        if u.marker or u.cls == "safe":
            continue
        # NAMES ONLY, as check() (W4): _candidate_keys' other hits in u.keys are VALUE text ('/opt/X=y' yields 'X')
        shown = set(k for k, _v in u.sd) | (set([u.bash[0]]) if u.bash else set()) | (u.keys & set(VALUE_KEYS))
        bad.append("block line %d (%s): %s" % (u.line, ", ".join(sorted(shown)) or "?", u.construct))
    return bad


# ------------------------------------------------------------------------------ THE WRITER (install.sh: write_env_file)
# ENV-FILE WRITER PASS (the reference-host round's recorded residue, #21956). Every READ of the env file was systemd's
# reading; the WRITE was not. install.sh's block replacer read the file with `open(encoding="utf-8").read().splitlines()`
# and its exit status was never checked, so -- measured by the iteration-4 reflector and re-executed on 524c63e -- ONE
# Latin-1 byte in an owner's COMMENT (systemd accepts it) crashed it and an EMPTY temp file was copied over the env
# file (rc 0, "your other lines preserved", every owner line and the block gone, the units on card 0);
# str.splitlines() re-split a form feed, a CR or a U+2028 inside an owner's line; a missing end marker dropped every
# later owner line; only the FIRST of two blocks was rewritten while systemd read the stale second one last; the token
# rescue wrote back an `export` line systemd drops; and an append after an owner's open quote was read into that value.
# The writer is now HERE, beside the reader, and it is the reader's own reading that places the block:
#   * bytes in, bytes out: every owner byte outside the block is kept exactly (lines split on '\n' only);
#   * the block goes where THIS reader's markers are (a marker is a marker only at the start of a logical line --
#     never a marker-shaped line inside a value), and a file that is not ONE pair, or that ends inside an open value
#     before an append, or that systemd rejects whole, is REFUSED naming the line -- nothing is written;
#   * the token: the value systemd delivered BEFORE the write is the value it delivers AFTER (or, where an old block
#     carried it as an `export` line systemd dropped, the value that line meant), re-appended as a line systemd and a
#     shell both read -- never printed;
#   * the result is VERIFIED as written: re-read from the disk, byte-compared to the image, and read back by this
#     reader -- one pair, the block byte-equal, every owner key's systemd value unchanged, every block key as built.
TOKEN_KEY = "CLAUDE_CODE_OAUTH_TOKEN"
_TOKEN_LINE = re.compile(r"[ \t]*(export[ \t]+)?%s[ \t]*=" % TOKEN_KEY)


class Refusal(Exception):
    """A write this writer will not make. The message names the line(s); nothing is written."""


def _spans(data):
    """(start, end) byte offsets of each physical line of `data`, split on b'\\n' ONLY (this reader's and systemd's line
    end); `end` includes the newline when the line has one. Same lines, same numbers as parse()."""
    out, off = [], 0
    for rl in data.split(b"\n"):
        out.append([off, off + len(rl) + 1])
        off += len(rl) + 1
    if data.endswith(b"\n") or data == b"":
        out.pop()
    else:
        out[-1][1] = len(data)
    return out


def marker_refusal(res):
    """The one message for a file whose managed block is not ONE begin/end pair (check and the writer share it)."""
    sw = [u for u in res["units"] if u.cls == "marker-in-value" and u.inside]
    if sw:
        # a value INSIDE the old block that runs over its end marker (reflector-5, measured: 43 of 2,973 refused files on
        # seeds 1231/9876 were told 'Leave exactly one pair' while the owner can SEE a pair -- words no one can follow)
        return ("the managed block's end marker is swallowed: the value that starts on line %d, INSIDE the managed block, "
                "runs over it (a trailing backslash, or an open quote). The line to change is line %d: end its value on "
                "that line (remove its trailing backslash, or close its quote); then re-run." % (sw[0].line, sw[0].line))
    return ("the managed block is not ONE begin/end marker pair (markers on lines %s) -- the block writer replaces "
            "exactly one pair, while systemd reads every line (a second block's assignments win, last-wins). Leave "
            "exactly one pair, then re-run." % (", ".join("%d (%s)" % m for m in res["markers"]) or "none"))


def _assignment_for(key, value):
    """KEY=value (bytes, newline-terminated) that systemd AND a shell read as `value` -- bare when every character is
    one both take literally, else single-quoted -- or None when no such spelling exists."""
    if value and all(c in _SAFE_WORD for c in value):
        return ("%s=%s\n" % (key, value)).encode("utf-8", "surrogateescape")
    if not any(c in value for c in "'\n\r\x00"):
        return ("%s='%s'\n" % (key, value)).encode("utf-8", "surrogateescape")
    if not any(c in value for c in "\n\r\x00"):
        # a value holding a ': double quotes, a backslash before each \\ " $ ` (read alike by both: _shell_reading)
        esc = "".join("\\" + c if c in '\\"$`' else c for c in value)
        return ('%s="%s"\n' % (key, esc)).encode("utf-8", "surrogateescape")
    return None


def splice(data, block, begin, end, keep=(TOKEN_KEY,)):
    """The env file after this run's managed block is written. `data`: the file's bytes as it stands (None: there is
    no file); `block`: the block's bytes, BEGIN line to END line. Returns (image, how, rescued) -- how is
    created|appended|replaced, rescued the keys re-appended -- or raises Refusal naming the line."""
    if not block.endswith(b"\n"):
        block += b"\n"
    bres = parse(block, begin, end)
    nb = len(_spans(block))
    if (bres["error"] or [k for _l, k in bres["markers"]] != ["begin", "end"]
            or bres["markers"][0][0] != 1 or bres["markers"][1][0] != nb):
        raise Refusal("the block this run built is not one BEGIN..END block systemd accepts (markers %s%s)"
                      % (bres["markers"], ", %s" % bres["error"] if bres["error"] else ""))
    bad = verify_block(bres)
    if bad:
        raise Refusal("the block this run built is read differently by systemd and by a shell: " + "; ".join(bad))
    old = None
    if data is None:
        head, tail, how = b"", b"", "created"
    else:
        old = parse(data, begin, end)
        if old["error"]:
            raise Refusal("line %d: systemd's EnvironmentFile= rejects this WHOLE file (%s) -- this installer does not "
                          "rewrite a file whose reading it cannot state. Fix that line." % (old["error_line"], old["error"]))
        kinds = [k for _l, k in old["markers"]]
        if kinds not in ([], ["begin", "end"]):
            raise Refusal(marker_refusal(old))
        for u in old["units"]:
            if u.cls == "marker-in-value" and not u.inside:
                raise Refusal("line %d: %s." % (u.line, u.construct))
        sp = _spans(data)
        if kinds:
            b_line, e_line = old["markers"][0][0], old["markers"][1][0]
            head, tail, how = data[:sp[b_line - 1][0]], data[sp[e_line - 1][1]:], "replaced"
        else:
            if old["eof_open"]:
                raise Refusal("line %d: the file ends inside a quote or after a backslash (the value that starts on "
                              "line %d is still open at the end of the file) -- systemd would read the managed block "
                              "appended after it as part of that value. Close the quote (or remove the trailing "
                              "backslash), then re-run." % (old["eof_open_line"], old["eof_open_line"]))
            head, tail, how = data + b"\n", b"", "appended"
    image = head + block + tail
    new = parse(image, begin, end)
    # ---- the token (and any other kept key): what systemd delivered before, it delivers after
    rescued, wants = [], {}
    for key in keep:
        want = None
        if old is not None:
            if key in old["effective"]:
                want = old["effective"][key][0]
            else:
                for u in old["units"]:
                    if not u.inside or u.marker or not _TOKEN_LINE.match(u.text) or key != TOKEN_KEY:
                        continue
                    if u.bash is not None and u.bash[0] == key:
                        want = u.bash[1]          # an old block's `export KEY=v`: systemd dropped it; its meaning is v
                    else:
                        raise Refusal("line %d (inside the managed block): a %s line this installer cannot read the "
                                      "same way systemd and a shell do (%s) -- the block is rewritten, so move it "
                                      "OUTSIDE the block as a bare %s=<value> line, then re-run."
                                      % (u.line, key, u.construct or "not a plain assignment", key))
        if want is None:
            continue
        wants[key] = want
        if key in new["effective"] and new["effective"][key][0] == want:
            continue
        line = _assignment_for(key, want)
        if line is None:
            raise Refusal("the %s the units read today (line %d) cannot be written back as a line systemd and a shell "
                          "read alike, and the block rewrite would drop it -- move it OUTSIDE the managed block as a "
                          "bare line by hand, then re-run." % (key, (old["effective"].get(key) or ("", 0))[1]))
        if new["eof_open"]:
            raise Refusal("line %d: the file ends inside a quote or after a backslash -- the %s line this run must "
                          "re-append would be read into that value. Close it, then re-run." % (new["eof_open_line"], key))
        sep = b"" if image.endswith(b"\n") else b"\n"
        if _tokenize(image + sep)[1][-1] != PRE_KEY:
            # the image ends in a backslash-NEWLINE continuation (an owner line after the block ending in '\'): the
            # line appended next would be read INTO that value. A blank line ends the continuation and systemd reads
            # the owner's value exactly as before (eof_open models an append after one extra newline; so does this).
            sep += b"\n"
        image = image + sep + line
        tail = image[len(head) + len(block):]
        new = parse(image, begin, end)
        rescued.append(key)
    problems = verify_image(image, block, begin, end, old, head, tail, wants)
    if problems:
        raise AssertionError("the writer's own image failed its read-back: " + "; ".join(problems))
    return image, how, rescued


def verify_image(image, block, begin, end, old, head, tail, wants):
    """The image read back as systemd reads it (this reader): [] or what is wrong. ONE marker pair; the block
    byte-equal to the one built; the owner's bytes before and after it exactly `head` and `tail`; every key the owner
    assigned outside the old block with its systemd value unchanged; every key the block assigns (and no owner line
    outside assigns) with the block's value; every kept key (the token) with the value it had."""
    bad = []
    new = parse(image, begin, end)
    if new["error"]:
        return ["systemd would reject the written file (%s, line %d)" % (new["error"], new["error_line"])]
    if [k for _l, k in new["markers"]] != ["begin", "end"]:
        return ["the written file does not hold exactly one managed block (markers %s)" % new["markers"]]
    sp = _spans(image)
    b, e = new["markers"][0][0], new["markers"][1][0]
    if image[sp[b - 1][0]:sp[e - 1][1]] != block:
        bad.append("the block as written is not the block built")
    if image[:sp[b - 1][0]] != head:
        bad.append("the owner's bytes before the block changed")
    if image[sp[e - 1][1]:] != tail:
        bad.append("the owner's bytes after the block changed")
    if old is not None:
        for k, (v, _ln) in old["outside"].items():
            if k in wants:
                continue
            if k not in new["outside"] or new["outside"][k][0] != v:
                bad.append("the owner's %s reads differently after the write (line %d)" % (k, _ln))
    for k, (v, _ln) in parse(block, begin, end)["effective"].items():
        if (old is None or k not in old["outside"]) and new["effective"].get(k, (None,))[0] != v:
            bad.append("the block's %s is not what systemd reads from the written file" % k)
    for k, v in wants.items():
        if new["effective"].get(k, (None,))[0] != v:
            bad.append("%s is not what the units read before the write" % k)
    return bad


# ------------------------------------------------------------------------------ the OWNER's declarations (G1)
# Env-file writer pass, iteration 5, G1 (the owner's ruling: "declared and recorded, never inferred"). On a host with two or
# more GPUs and nothing declared, install.sh ASKS the owner (its ASK HELPER) which card drives the monitor(s) and which the
# brain's background jobs use -- or takes his answer from --display-gpu / --gpu -- and records each answer as HIS
# declaration: the very lines an owner writes by hand (the reference desktop carries WIZARD_DISPLAY_INDEX=0 and
# WIZARD_CUDA_DEVICE=1 so), outside the managed block, each under a comment saying it was chosen at install and when. From
# then on the file DECLARES the card, and every reader (the resolver, P4, the units) treats it exactly as a hand-written
# line: a re-run reads it and never asks again.
DECLARE_KEYS = (DISPLAY_KEY, "WIZARD_CUDA_DEVICE", ACCEL_KEY)
CHOSEN_AT = re.compile(r"chosen at install (\d{4}-\d{2}-\d{2})(?: \(([^()]*)\))?")


def declare(data, pairs, begin, end):
    """The env file after the owner's answers are recorded (G1). `data`: the file's bytes (None: there is no file);
    `pairs`: [(key, value, comment)] -- key one of DECLARE_KEYS, value a card index (or 'none' for the display). Each pair
    is APPENDED -- the writer's append path, with the token rescue's continuation rule -- OUTSIDE the managed block, at the
    end of the file, as `# <comment>` then `KEY=value`; every byte before it is kept. Verified as systemd reads it: each
    declared key reads back as its value, every key the file assigned before reads as before, the markers are where they
    were, and the new lines are ones a shell reads alike. Returns (image, first_line, last_line) of the appended lines;
    raises Refusal naming the line (nothing written)."""
    for k, v, c in pairs:
        if k not in DECLARE_KEYS:
            raise Refusal("internal: %s is not a key this writer declares" % k)
        if k == "WIZARD_CUDA_DEVICE" and v.strip().lower() in ("-1", "none"):
            # the one writer never records a "no GPU" spelling for the jobs' card. A hand-written '-1'/'none' means
            # CPU only to every consumer (Drew's ruling, vl_gpu_minus1_ruling); the installer still never writes it.
            raise Refusal("WIZARD_CUDA_DEVICE=%r is refused: this key takes a card index only. For no GPU use --force-cpu "
                          "(this run) or write WIZARD_CUDA_DEVICE= (empty: the jobs stay unpinned) by hand" % v)
        if k == ACCEL_KEY:
            # contract C2 (handoff round 1): the backend families are the RESOLVER's to read; the only values a writer
            # writes are 'auto' and 'cpu' (--force-cpu's durable spelling), so a writer can never name hardware it did
            # not prove
            if v not in ACCEL_WRITTEN:
                raise Refusal("internal: %s=%r is not a value this installer writes (%s)" % (k, v, "/".join(ACCEL_WRITTEN)))
        elif not (re.fullmatch(r"\d+", v) or (k == DISPLAY_KEY and v == "none")):
            raise Refusal("internal: %s=%r is not a card index%s" % (k, v, " or 'none'" if k == DISPLAY_KEY else ""))
        if "\n" in c or "\r" in c:
            raise Refusal("internal: a declaration's comment must be one line")
    old = None
    if data is None:
        data = b""
    else:
        old = parse(data, begin, end)
        if old["error"]:
            raise Refusal("line %d: systemd's EnvironmentFile= rejects this WHOLE file (%s) -- this installer does not "
                          "write into a file whose reading it cannot state. Fix that line." % (old["error_line"], old["error"]))
        kinds = [k for _l, k in old["markers"]]
        if kinds not in ([], ["begin", "end"]):
            raise Refusal(marker_refusal(old))
        for u in old["units"]:
            if u.cls == "marker-in-value" and not u.inside:
                raise Refusal("line %d: %s." % (u.line, u.construct))
        if old["eof_open"]:
            raise Refusal("line %d: the file ends inside a quote or after a backslash (the value that starts on line %d is "
                          "still open at the end of the file) -- systemd would read the declarations appended after it as "
                          "part of that value. Close the quote (or remove the trailing backslash), then re-run."
                          % (old["eof_open_line"], old["eof_open_line"]))
        for k, _v, _c in pairs:
            if k in old["outside"]:
                raise Refusal("line %d already declares %s outside the managed block: an answer is recorded only where "
                              "nothing is declared (edit that line to change it)" % (old["outside"][k][1], k))
    sep = b"" if (data == b"" or data.endswith(b"\n")) else b"\n"
    if data and _tokenize(data + sep)[1][-1] != PRE_KEY:
        sep += b"\n"                        # a backslash-NEWLINE continuation: a blank line ends it (the rescue's rule)
    add = b""
    for k, v, c in pairs:
        line = _assignment_for(k, v)
        add += ("# %s\n" % c).encode("utf-8") + line
    image = data + sep + add
    first = len(_spans(data + sep)) + 1
    last = len(_spans(image))
    new = parse(image, begin, end)
    bad = []
    if new["error"]:
        bad.append("systemd would reject the written file (%s, line %d)" % (new["error"], new["error_line"]))
    else:
        if not image.startswith(data):
            bad.append("the owner's bytes changed")
        if old is not None and new["markers"] != old["markers"]:
            bad.append("the managed block moved (markers %s, were %s)" % (new["markers"], old["markers"]))
        for k, v, _c in pairs:
            if new["outside"].get(k, (None,))[0] != v or new["effective"].get(k, (None,))[0] != v:
                bad.append("%s does not read back as declared" % k)
        if old is not None:
            for k, (v, _ln) in old["effective"].items():
                if new["effective"].get(k, (None,))[0] != v:
                    bad.append("the file's %s reads differently after the declaration" % k)
        for u in new["units"]:
            if u.line >= first and u.cls != "safe":
                bad.append("line %d of the declaration is not read alike by systemd and a shell (%s)" % (u.line, u.construct))
    if bad:
        raise AssertionError("the declaration's own image failed its read-back: " + "; ".join(bad))
    return image, first, last


def provenance(res, key):
    """'chosen at install <date>, <how>' when the line that declares `key` OUTSIDE the managed block sits directly under
    the comment the G1 writer puts above it -- the record says where a declaration came from -- else None."""
    got = res["outside"].get(key)
    if not got:
        return None
    for u in res["units"]:
        if u.end_line == got[1] - 1 and not u.sd and u.text.lstrip().startswith("#"):
            m = CHOSEN_AT.search(u.text)
            if m:
                return "chosen at install %s%s" % (m.group(1), ", %s" % m.group(2) if m.group(2) else "")
    return None


def write_verified(path, image):
    """Write `image` to `path` (created 0600), flush it to the disk, READ IT BACK and compare byte for byte. A short or
    failed write (a full disk, a file-size limit) removes the partial file and raises OSError: a truncated image is
    never left for a caller to copy over the env file."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        view, done = memoryview(image), 0
        while done < len(image):
            n = os.write(fd, view[done:])
            if n <= 0:
                raise OSError("a write of %s returned %d" % (path, n))
            done += n
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _unlink(path)
        raise
    os.close(fd)
    with open(path, "rb") as fh:
        back = fh.read()
    if back != image:
        _unlink(path)
        raise OSError("%s read back %d bytes, not the %d written" % (path, len(back), len(image)))


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# ------------------------------------------------------------------------------ CLI (install.sh: env_outside)
USAGE = ("usage: env_file.py --file F [--begin B --end E] [--organs DIR] "
         "keys | has | get KEY | effective KEY | unitpin KEY | effective-pin | check SHAPE | verify-block | "
         "splice BLOCK OUT | declare OUT KEY=VALUE COMMENT [KEY=VALUE COMMENT ...] | provenance KEY | line KEY | "
         "effective-line KEY   (--organs "
         "defaults to the package's organs/ beside install/)")


def main(argv):
    opts = {"--file": None, "--begin": None, "--end": None, "--organs": None}
    rest, i = [], 0
    while i < len(argv):
        if argv[i] in opts and i + 1 < len(argv):
            opts[argv[i]] = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    if not opts["--file"] or not rest:
        sys.stderr.write(USAGE + "\n")
        return 64
    path, mode, arg = opts["--file"], rest[0], (rest[1] if len(rest) > 1 else None)
    if mode == "splice":
        return _splice_cli(path, rest[1:], opts)
    if mode == "declare":
        return _declare_cli(path, rest[1:], opts)
    try:
        res = read_file(path, opts["--begin"], opts["--end"])
    except OSError as e:
        sys.stderr.write("env_file.py: cannot read %s: %s\n" % (path, e))
        return 66
    try:
        return _modes(res, path, mode, arg, opts)
    except OrgansMissing as e:
        sys.stderr.write("env_file.py: %s\n" % e)
        return 69


def _splice_cli(path, args, opts):
    """splice BLOCK OUT: write the env file as it will be after this run's block (the image) to OUT, verified as
    written; the env file itself is never touched here (install.sh copies OUT over it only on rc 0).
    rc 0 OK (stdout: OK<TAB>created|appended|replaced<TAB>rescued=<keys>); 2 REFUSED (stdout: REFUSE<TAB>the line
    named; OUT not written); 3 the image could not be written and read back whole (OUT removed); 64 usage; 66 I/O."""
    if len(args) != 2 or not opts["--begin"] or not opts["--end"]:
        sys.stderr.write(USAGE + "\n")
        return 64
    block_f, out_f = args
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        data = None
    except OSError as e:
        sys.stderr.write("env_file.py: cannot read %s: %s\n" % (path, e))
        return 66
    try:
        with open(block_f, "rb") as fh:
            block = fh.read()
    except OSError as e:
        sys.stderr.write("env_file.py: cannot read the block %s: %s\n" % (block_f, e))
        return 66
    try:
        image, how, rescued = splice(data, block, opts["--begin"], opts["--end"])
    except Refusal as e:
        msg = str(e).replace("\n", " ")
        print("REFUSE\t%s%s %s" % (path, "" if msg.startswith("line ") else ":", msg))
        return 2
    except AssertionError as e:
        sys.stderr.write("env_file.py: %s\n" % e)
        return 3
    try:
        write_verified(out_f, image)
    except OSError as e:
        sys.stderr.write("env_file.py: the verified image could not be written whole to %s (%s); it was removed\n"
                         % (out_f, e))
        return 3
    print("OK\t%s\trescued=%s" % (how, ",".join(rescued) or "none"))
    return 0


def _declare_cli(path, args, opts):
    """declare OUT KEY=VALUE COMMENT [...]: write the env file as it will be after the owner's declarations (G1) to OUT,
    verified as written; the env file itself is never touched here (install.sh copies OUT over it only on rc 0).
    rc 0 OK (stdout: OK<TAB>lines=<first>-<last>); 2 REFUSED (stdout: REFUSE<TAB>the line named; OUT not written); 3 the
    image failed its read-back or could not be written whole (OUT removed); 64 usage; 66 I/O."""
    if len(args) < 3 or (len(args) - 1) % 2 or not opts["--begin"] or not opts["--end"]:
        sys.stderr.write(USAGE + "\n")
        return 64
    out_f, rest = args[0], args[1:]
    pairs = []
    for i in range(0, len(rest), 2):
        kv, c = rest[i], rest[i + 1]
        if "=" not in kv:
            sys.stderr.write(USAGE + "\n")
            return 64
        k, v = kv.split("=", 1)
        pairs.append((k, v, c))
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        data = None
    except OSError as e:
        sys.stderr.write("env_file.py: cannot read %s: %s\n" % (path, e))
        return 66
    try:
        image, first, last = declare(data, pairs, opts["--begin"], opts["--end"])
    except Refusal as e:
        msg = str(e).replace("\n", " ")
        print("REFUSE\t%s%s %s" % (path, "" if msg.startswith("line ") else ":", msg))
        return 2
    except AssertionError as e:
        sys.stderr.write("env_file.py: %s\n" % e)
        return 3
    try:
        write_verified(out_f, image)
    except OSError as e:
        sys.stderr.write("env_file.py: the verified image could not be written whole to %s (%s); it was removed\n"
                         % (out_f, e))
        return 3
    print("OK\tlines=%d-%d" % (first, last))
    return 0


def _modes(res, path, mode, arg, opts):
    if mode in ("keys", "has"):
        if res["outside"]:
            print("\n".join(sorted(res["outside"])))
        return 0
    if mode in ("get", "effective", "unitpin") and arg:
        table = res["effective"] if mode == "effective" else res["outside"]
        if arg not in table:
            return 1
        v = table[arg][0]
        if mode == "unitpin" and arg in PIN_KEYS:
            u = units_pin(arg, v, opts["--organs"])
            v = v if u is None else u
        print(v)
        return 0
    if mode in ("line", "effective-line") and arg:
        # the LINE a key's value comes from: outside the managed block (line), or in systemd's reading of the whole file,
        # last-wins (effective-line) -- a number only, never a value (so a refusal can name the line to edit)
        table = res["effective"] if mode == "effective-line" else res["outside"]
        if res["error"] or arg not in table:
            return 1
        print(table[arg][1])
        return 0
    if mode == "provenance" and arg:
        if res["error"]:
            return 3
        pv = provenance(res, arg)
        if pv is None:
            return 1
        print(pv)
        return 0
    if mode == "effective-pin":
        # the pin EVERY unit reads from this file as written: systemd's reading of the WHOLE file (every block, last-
        # wins), then the units' resolver order -- CUDA_VISIBLE_DEVICES (ambient) before WIZARD_CUDA_DEVICE (env)
        if res["error"]:
            return 3
        for k in PIN_KEYS:
            if k in res["effective"]:
                u = units_pin(k, res["effective"][k][0], opts["--organs"])
                if u is None:
                    return 3
                print(u)
                return 0
        return 1
    if mode == "check" and arg:
        rows = check(res, path, arg, opts["--organs"])
        for lvl, msg in rows:
            print("%s\t%s" % (lvl, msg.replace("\n", " ")))
        return 2 if any(lvl == "REFUSE" for lvl, _ in rows) else 0
    if mode == "verify-block":
        bad = verify_block(res)
        for m in bad:
            print("REFUSE\t%s" % m)
        return 2 if bad else 0
    sys.stderr.write(USAGE + "\n")
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
