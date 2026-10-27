"""The env file is read the way its CONSUMER reads it (reference-host round, iteration 4: the iteration-3 reflector's
HIGH 1, done as a CLASS -- memory #21871, "a guard must parse a file the way its consumer does").

Every packaged unit loads the env file through systemd's EnvironmentFile=. install.sh used to read it with a SHELL's
rules, so it proved and recorded a value the units never received -- measured: an owner line
`CUDA_VISIBLE_DEVICES=1 # the compute card` was proven and recorded as card 1 while systemd handed every unit
'1 # the compute card', which the units' resolver rejects, falling to card 0 (the display card). The installer now
reads the file through ONE reader, install/env_file.py, and these tests feed the SAME bytes to it and to:

  1. golden values written from systemd.exec(5), EnvironmentFile= -- portable, runs everywhere;
  2. systemd's OWN code, where the host has it: libsystemd-shared's load_env_file() (the call EnvironmentFile= makes,
     exec_context_load_environment) and strv_env_clean_with_callback() (the manager's drop of invalid names), called
     through ctypes in a SUBPROCESS -- unprivileged, in-process, no manager contacted;
  3. a SHELL (`set -a; . FILE; set +a`, the idiom the managed block documents): every line the reader calls SAFE
     reads the same to bash as to systemd, and every case where bash and systemd disagree is flagged -- the
     property the installer's refusals rest on.

The corpus holds every construct where the two readers diverge: inline comments (bare and after a quote), text after
a quote, `export`, backslash continuations and escapes, multi-line quotes, $-expansion, command substitution, '~',
whitespace around '=' and inside a value, two assignments on a line, ';' lines, CRLF, a BOM, invalid names, lines
without '=', a NUL, invalid UTF-8 in and out of an assignment, a comment ending in a backslash, and a marker-shaped
line inside a value. At the iteration-3 artifacts install/env_file.py does not exist: every test here is red.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
READER = ROOT / "install" / "env_file.py"
ORGANS = ROOT / "organs"
BEGIN, END = "# >>> wizard-library managed >>>", "# <<< wizard-library managed <<<"

REJECT = "REJECT"   # systemd refuses the WHOLE file (load_env_file < 0)

# (name, bytes, the environment systemd's EnvironmentFile= hands a unit -- systemd.exec(5)) -- keys are fixture
# names (A, B, ...) so no line here resembles a real declaration
CORPUS = [
    ("bare", b"A=1\n", {"A": "1"}),
    # "there are no inline comments": only a line STARTING with '#' or ';' is a comment
    ("inline_comment", b"A=1 # c\n", {"A": "1 # c"}),
    ("inline_comment_tab", b"A=1\t# c\n", {"A": "1\t# c"}),
    ("dq_then_comment", b'A="1" # c\n', {"A": "1# c"}),
    ("sq_then_comment", b"A='1' # c\n", {"A": "1# c"}),
    ("empty_then_comment", b"A= # c\n", {"A": "# c"}),
    ("dq_then_text", b'A="1" x\n', {"A": "1x"}),
    # a key must be a valid name: `export A` is not, so the line assigns NOTHING
    ("export", b"export A=1\n", {}),
    ("export_after_bare", b"A=1\nexport A=0\n", {"A": "1"}),
    # a backslash-newline continues the line; an unquoted backslash keeps the next character
    ("continuation", b"A=a\\\nb\n", {"A": "ab"}),
    ("continuation_swallows_next_key", b"A=\\\nB=2\n", {"A": "B=2"}),
    ("unquoted_backslash", b"A=a\\ b\\#c\n", {"A": "a b#c"}),
    # '...' verbatim and may span lines; "..." honours \\ \" \` \$ and keeps any other backslash
    ("sq_multiline", b"A='a\nb'\nB=2\n", {"A": "a\nb", "B": "2"}),
    ("dq_escapes", b'A="a\\"b\\$c\\qd"\n', {"A": 'a"b$c\\qd'}),
    ("quote_concat", b"A=\"a\"'b'c\n", {"A": "abc"}),
    ("dq_trailing_space_kept", b'A="1 "\n', {"A": "1 "}),
    # no expansion of any kind
    ("dollar", b"A=$HOME/x\nB=${HOME}\n", {"A": "$HOME/x", "B": "${HOME}"}),
    ("command_substitution", b"A=$(echo x)\nB=`echo y`\n", {"A": "$(echo x)", "B": "`echo y`"}),
    ("tilde", b"A=~/x\n", {"A": "~/x"}),
    # whitespace: dropped around '=' and at the ends of a value, KEPT inside it
    ("ws_around_eq", b"A = 1\n", {"A": "1"}),
    ("ws_after_eq", b"A=  1\n", {"A": "1"}),
    ("interior_ws", b"A=1 2\n", {"A": "1 2"}),
    ("two_assignments", b"A=1 B=2\n", {"A": "1 B=2"}),
    ("semicolon_in_value", b"A=1; B=2\n", {"A": "1; B=2"}),
    ("leading_trailing_ws", b"  A=1  \n\tB = 2\t\n", {"A": "1", "B": "2"}),
    ("semicolon_comment_line", b"; A=1\nB=2\n", {"B": "2"}),
    ("comment_ending_in_backslash", b"# c \\\nA=1\n", {"A": "1"}),   # systemd >= 254: the next line is NOT a comment
    ("crlf", b"A=1\r\nB=2\r\n", {"A": "1", "B": "2"}),
    ("bom", b"\xef\xbb\xbfA=1\nB=2\n", {"B": "2"}),
    ("unterminated_dq", b'A="1\nB=2\n', {"A": "1\nB=2\n"}),
    ("invalid_names", b"1A=1\nA-B=2\nC=3\n", {"C": "3"}),
    ("no_equals", b"A\nB=2\n", {"B": "2"}),
    ("unset_line", b"A=1\nunset A\n", {"A": "1"}),
    ("last_wins", b"A=1\nA=2\n", {"A": "2"}),
    ("empties", b"A=\nB=''\nC=\"\"\n", {"A": "", "B": "", "C": ""}),
    ("hash_inside_word", b"A=1#x\n", {"A": "1#x"}),
    ("no_final_newline", b"A=1", {"A": "1"}),
    ("utf8_value", "A=é\n".encode("utf-8"), {"A": "é"}),
    ("invalid_utf8_in_comment", b"# \xff\nA=1\n", {"A": "1"}),
    ("invalid_utf8_in_assignment", b"A=\xff\nB=2\n", REJECT),
    ("nul_byte", b"A=1\n# x\x00y\n", REJECT),
    ("marker_shaped_line_inside_a_value", ("A='x\n%s\n'\n" % BEGIN).encode(), {"A": "x\n%s\n" % BEGIN}),
    # systemd's utf8_is_valid() rejects the Unicode noncharacters that Python's strict decoder accepts (iteration 5)
    ("nonchar_uffff_in_value", b"A=\xef\xbf\xbf\nB=2\n", REJECT),
    ("nonchar_ufdd0_in_value", b"A=\xef\xb7\x90\nB=2\n", REJECT),
    ("nonchar_u1fffe_in_key", b"A\xf0\x9f\xbf\xbe=1\nB=2\n", REJECT),
]

# systemd's own EnvironmentFile= code path, by ctypes, in a subprocess (a wrong ABI can only kill that process)
_ORACLE = r"""
import ctypes, glob, json, sys
libs = sorted(glob.glob("/usr/lib/*/systemd/libsystemd-shared-*.so") + glob.glob("/usr/lib/systemd/libsystemd-shared-*.so")
              + glob.glob("/usr/lib64/systemd/libsystemd-shared-*.so"))
if not libs:
    print(json.dumps({"skip": "no libsystemd-shared on this host"})); sys.exit(0)
L = ctypes.CDLL(libs[-1])
for sym in ("load_env_file", "strv_env_clean_with_callback"):
    if not hasattr(L, sym):
        print(json.dumps({"skip": "%s does not export %s" % (libs[-1], sym)})); sys.exit(0)
L.load_env_file.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p))]
L.load_env_file.restype = ctypes.c_int
L.strv_env_clean_with_callback.argtypes = [ctypes.POINTER(ctypes.c_char_p), ctypes.c_void_p, ctypes.c_void_p]
L.strv_env_clean_with_callback.restype = ctypes.POINTER(ctypes.c_char_p)
out = {"lib": libs[-1], "files": {}}
for f in sys.argv[1:]:
    ret = ctypes.POINTER(ctypes.c_char_p)()
    rc = L.load_env_file(None, f.encode(), ctypes.byref(ret))
    env = {}
    if rc >= 0 and ret:
        cl = L.strv_env_clean_with_callback(ret, None, None)
        i = 0
        while cl and cl[i] is not None:
            k, _, v = cl[i].partition(b"=")
            env[k.decode("utf-8")] = v.decode("utf-8")
            i += 1
    out["files"][f] = {"rc": rc, "env": env}
print(json.dumps(out))
"""


def _reader():
    if not READER.is_file():
        raise AssertionError("install/env_file.py is absent: the installer has no systemd-semantics reader of the env file")
    sys.path.insert(0, str(READER.parent))
    try:
        import env_file  # type: ignore
        return env_file
    finally:
        sys.path.remove(str(READER.parent))


def _systemd_view(res):
    return REJECT if res["error"] else {k: v for k, (v, _line) in res["effective"].items()}


class _Corpus(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="envfile_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.ef = _reader()
        self.files = {}
        for name, data, _g in CORPUS:
            p = self.tmp / (name + ".env")
            p.write_bytes(data)
            self.files[name] = p


class TestTheReaderIsSystemd(_Corpus):

    def test_every_corpus_case_matches_systemd_exec_5(self):
        for name, _data, golden in CORPUS:
            got = _systemd_view(self.ef.read_file(str(self.files[name])))
            self.assertEqual(got, golden, "%s: the reader says %r, systemd.exec(5) says %r" % (name, got, golden))

    def test_every_corpus_case_matches_systemds_own_loader(self):
        """The same bytes through libsystemd-shared's load_env_file() -- the code EnvironmentFile= runs."""
        names = [n for n, _d, _g in CORPUS]
        p = subprocess.run([sys.executable, "-c", _ORACLE] + [str(self.files[n]) for n in names],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0 or not p.stdout.strip():
            self.skipTest("the systemd oracle could not run here (rc %s): %s" % (p.returncode, p.stderr[-300:]))
        o = json.loads(p.stdout.strip().splitlines()[-1])
        if "skip" in o:
            self.skipTest(o["skip"])
        for n in names:
            want = o["files"][str(self.files[n])]
            want = REJECT if want["rc"] < 0 else want["env"]
            got = _systemd_view(self.ef.read_file(str(self.files[n])))
            self.assertEqual(got, want, "%s: the reader says %r, systemd's load_env_file() (%s) says %r"
                             % (n, got, o["lib"], want))
            golden = [g for m, _d, g in CORPUS if m == n][0]
            self.assertEqual(golden, want, "%s: the golden value from systemd.exec(5) disagrees with systemd itself" % n)


class TestEveryShellDivergenceIsFlagged(_Corpus):
    """The SHELL is the file's second reader. A line the reader calls SAFE must read the same to bash; any case where
    bash and systemd disagree must carry at least one flagged line (divergent or unmodelled)."""

    def _bash(self, path):
        if not shutil.which("bash"):
            self.skipTest("no bash on this host")
        home = self.tmp / "h"
        home.mkdir(exist_ok=True)
        p = subprocess.run(["bash", "-c", 'set -a; . "$1" 2>/dev/null; set +a; exec "$2" -c '
                            '"import json, os; print(json.dumps(dict(os.environ)))"', "_", str(path), sys.executable],
                           env={"PATH": "/usr/bin:/bin", "HOME": str(home)}, capture_output=True, text=True, timeout=60)
        env = json.loads(p.stdout.strip().splitlines()[-1])
        for k in ("PATH", "HOME", "PWD", "SHLVL", "_", "OLDPWD", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"):
            env.pop(k, None)
        return env

    def test_safe_means_bash_agrees_and_every_disagreement_is_flagged(self):
        checked_disagreements = 0
        for name, _data, golden in CORPUS:
            if golden == REJECT:
                continue
            res = self.ef.read_file(str(self.files[name]))
            bash = self._bash(self.files[name])
            flagged = [u for u in res["units"] if u.cls != "safe"]
            if bash != golden:
                checked_disagreements += 1
                self.assertTrue(flagged, "%s: bash reads %r, systemd %r, and the reader flagged NO line"
                                % (name, bash, golden))
            if not flagged:
                self.assertEqual(bash, golden, "%s: every line was called SAFE but bash reads %r, systemd %r"
                                 % (name, bash, golden))
            for u in res["units"]:
                if u.cls == "divergent" and u.bash is not None and len(res["units"]) == 1:
                    self.assertEqual(bash.get(u.bash[0]), u.bash[1],
                                     "%s: the reader models bash as %r=%r; bash reads %r" % (name, u.bash[0], u.bash[1],
                                                                                           bash.get(u.bash[0])))
        self.assertGreaterEqual(checked_disagreements, 20, "the corpus no longer exercises the divergent constructs")

    def test_the_constructs_are_named(self):
        want = {"inline_comment": "inline '#' comment", "dq_then_comment": "inline '#' comment",
                "export": "`export`", "continuation": "continues onto the next line",
                "dollar": "'$'", "command_substitution": "'$'", "tilde": "'~'", "semicolon_comment_line": "';'",
                "crlf": "carriage return", "bom": "byte-order mark", "interior_ws": "whitespace inside",
                "two_assignments": "whitespace inside", "ws_around_eq": "whitespace around '='",
                "comment_ending_in_backslash": "ending in a backslash"}
        for name, frag in want.items():
            res = self.ef.read_file(str(self.files[name]))
            said = " | ".join(u.construct for u in res["units"] if u.cls != "safe")
            self.assertIn(frag, said, "%s: %r" % (name, said))


class TestTheInstallersPolicy(unittest.TestCase):
    """install/env_file.py `check`, in process: what install.sh refuses or warns about, per shape. The pins here are
    the fixture's own strings; nothing reads a GPU."""

    def setUp(self):
        self.ef = _reader()
        self.tmp = Path(tempfile.mkdtemp(prefix="envpolicy_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.n = 0

    def _check(self, owner, shape="ambient", block=""):
        self.n += 1
        p = self.tmp / ("e%d" % self.n)
        tail = (BEGIN + "\n" + block + END + "\n").encode("utf-8")
        p.write_bytes((owner.encode("utf-8") if isinstance(owner, str) else owner) + tail)
        res = self.ef.read_file(str(p), BEGIN, END)
        return res, self.ef.check(res, str(p), shape, str(ORGANS))

    def _levels(self, rows):
        return [lvl for lvl, _m in rows]

    def test_the_reflectors_case_is_refused_in_every_shape_naming_the_line(self):
        for shape in ("ambient", "venv", "tier0"):
            res, rows = self._check("CUDA_VISIBLE_DEVICES=1 # the compute card\n", shape)
            self.assertIn("REFUSE", self._levels(rows), (shape, rows))
            msg = [m for l, m in rows if l == "REFUSE"][0]
            self.assertIn("line 1: CUDA_VISIBLE_DEVICES reads as '1 # the compute card'", msg)
            self.assertIn("carries an inline '#' comment", msg)
            self.assertIn("REJECTS it", msg)

    def test_a_quoted_pin_with_a_trailing_comment_is_refused_both_keys(self):
        for line in ('CUDA_VISIBLE_DEVICES="1" # compute\n', "WIZARD_CUDA_DEVICE='1' # compute\n"):
            _res, rows = self._check(line, "venv")
            self.assertIn("REFUSE", self._levels(rows), (line, rows))
            self.assertIn("reads as '1# compute'", " ".join(m for _l, m in rows))

    def test_a_commented_wizard_cuda_device_is_what_the_units_read(self):
        """The units' resolver strips an inline comment from WIZARD_CUDA_DEVICE itself (voidloom_device's own
        leniency): systemd hands them '1 # c', they resolve 1, a shell reads 1 -- every reader agrees, no refusal."""
        res, rows = self._check("WIZARD_CUDA_DEVICE=1 # the compute card\n")
        self.assertNotIn("REFUSE", self._levels(rows), rows)
        self.assertEqual(self.ef.units_pin("WIZARD_CUDA_DEVICE", res["outside"]["WIZARD_CUDA_DEVICE"][0], str(ORGANS)), "1")

    def test_an_exported_pin_is_not_a_declaration(self):
        """LOW 5: `export WIZARD_CUDA_DEVICE=1` -- systemd drops it, so it declares nothing; in the ambient shape that
        is a refusal naming the line and the fix, elsewhere a warning (it used to be silent on P4's failure path)."""
        _res, rows = self._check("export WIZARD_CUDA_DEVICE=1\n", "ambient")
        refusal = " ".join(m for l, m in rows if l == "REFUSE")
        self.assertIn("line 1: WIZARD_CUDA_DEVICE", refusal)
        self.assertIn("is NOT a declaration", refusal)
        self.assertIn("Write it as a bare WIZARD_CUDA_DEVICE=", refusal)
        _res, rows = self._check("export WIZARD_CUDA_DEVICE=1\n", "venv")
        self.assertEqual(self._levels(rows), ["WARN"], rows)

    def test_an_empty_pin_with_a_comment_is_not_empty_to_systemd(self):
        _res, rows = self._check("CUDA_VISIBLE_DEVICES= # no device\n", "venv")
        self.assertIn("REFUSE", self._levels(rows))
        self.assertIn("reads as '# no device'", " ".join(m for _l, m in rows))

    def test_a_display_declaration_a_shell_reads_differently_is_refused_in_the_ambient_shape(self):
        for line in ("export WIZARD_DISPLAY_INDEX=0\n", "WIZARD_DISPLAY_INDEX=0 # the monitor\n"):
            _res, rows = self._check(line, "ambient")
            self.assertIn("REFUSE", self._levels(rows), (line, rows))
            _res, rows = self._check(line, "venv")
            self.assertNotIn("REFUSE", self._levels(rows), (line, rows))

    def test_a_file_systemd_rejects_whole_is_refused_in_every_shape(self):
        for data in (b"TOKEN_LIKE=\xff\xfe\n", b"A=1\n# \x00\n"):
            for shape in ("ambient", "venv"):
                _res, rows = self._check(data, shape)
                self.assertEqual(self._levels(rows), ["REFUSE"], (data, shape, rows))
                self.assertIn("rejects this WHOLE file", rows[0][1])

    def test_every_other_divergence_is_a_warning_that_never_prints_a_value(self):
        secret = "sk-not-a-real-token-" + "q" * 12
        owner = "CLAUDE_CODE_OAUTH_TOKEN=%s # mine\nexport OTHER=%s\nFOO=$HOME/%s\n" % (secret, secret, secret)
        _res, rows = self._check(owner, "venv")
        self.assertEqual(self._levels(rows), ["WARN", "WARN", "WARN"], rows)
        self.assertNotIn(secret, " ".join(m for _l, m in rows), "a warning printed a value")
        # iteration 4: an `export` line runs a COMMAND -- in the ambient shape it is refused, naming its line and the fix
        _res, rows = self._check(owner, "ambient")
        refusal = " ".join(m for lvl, m in rows if lvl == "REFUSE")
        self.assertIn("The line to change is line 2: it is written with `export`", refusal)
        self.assertIn("write it as a bare OTHER=<value> line", refusal)
        self.assertNotIn(secret, " ".join(m for _l, m in rows), "a refusal printed a value")

    def test_a_marker_anomaly_is_refused_in_every_shape(self):
        # iteration 5: an unterminated block made the writer drop every later owner line; a second block's stale pin
        # won in systemd (last-wins) over the one this run wrote -- measured: record gpu_pin=1, the units on card 0
        for body in (BEGIN + "\nPATH='/x'\nWIZARD_CUDA_DEVICE=1\n",
                     BEGIN + "\n" + END + "\n" + BEGIN + "\nCUDA_VISIBLE_DEVICES='0'\n" + END + "\n"):
            for shape in ("ambient", "venv"):
                p = self.tmp / ("m%d" % len(body)); p.write_text(body, encoding="utf-8")
                rows = self.ef.check(self.ef.read_file(str(p), BEGIN, END), str(p), shape, str(ORGANS))
                self.assertEqual(self._levels(rows), ["REFUSE"], (shape, rows))
                self.assertIn("not ONE begin/end marker pair", rows[0][1])

    def test_a_file_ending_inside_a_quote_is_refused_before_a_block_is_appended(self):
        p = self.tmp / "open"; p.write_bytes(b"WIZARD_CUDA_DEVICE=1\nNOTE='abc")
        rows = self.ef.check(self.ef.read_file(str(p), BEGIN, END), str(p), "venv", str(ORGANS))
        self.assertEqual(self._levels(rows), ["REFUSE"], rows)
        self.assertIn("ends inside a quote or after a backslash", rows[0][1])
        self.assertIn("line 2: the file ends inside a quote", rows[0][1], "the refusal must name the line")

    def test_a_warning_names_keys_never_value_fragments(self):
        # key names chosen so a secret scanner does not read the fixture as a credential (gitleaks' generic-api-key
        # rule flagged the proposal's `SOME_SECRET=...` spelling of this line); the property is the key name's to keep
        _res, rows = self._check("OWNER_NOTE=ab+Cd/FRAGMENTxyz== # mine\nOTHER_NOTE=x$y/FRAGMENTtwo=\n")
        said = " ".join(m for _l, m in rows)
        self.assertIn("(OWNER_NOTE)", said)
        self.assertNotIn("FRAGMENT", said, "a warning printed value text as a key name")

    def test_an_assignment_longer_than_the_arg_max_floor_is_refused(self):
        _res, rows = self._check("CUDA_VISIBLE_DEVICES=1\nNOTE=" + "y" * 131072 + "\n", "venv")
        self.assertEqual(self._levels(rows), ["REFUSE"], [m[:120] for _l, m in rows])
        self.assertIn("The line to change is line 2: shorten it", rows[0][1])

    # ADVISORY 7's CLASS (env-file writer pass: iteration-1 reflection EW1-05, widened in iteration 2): a line placed
    # BEFORE `WIZARD_CUDA_DEVICE=1` and what bash's `set -a; . FILE` then assigns to the pin -- MEASURED (env -i bash;
    # re-measured below where the host has bash; EXITED = the shell ended). The model is a WHITELIST (install/env_file.py
    # _shell_plain): after any line that is not a plain simple command the shell's reading is NOT MODELLED. So the
    # properties, for every line: bash does not read the pin as systemd does -> REFUSED in the ambient shape; a refusal
    # that says a shell STOPS is one where bash assigned nothing; a refusal of a line bash reads past says 'not model'.
    # Measured over THIS list: the iteration-1 artifacts refuse none of 71 lines after which bash does not read the pin
    # as systemd does, and 5 refusals claim a shell stops where bash reads on; 524c63e refuses none of 75. This pass: 0, 0.
    SHELL_CLASS = (
        (")", "UNSET"), ("fi", "UNSET"), ("then", "UNSET"), ("else", "UNSET"), ("elif x", "UNSET"), ("do", "UNSET"),
        ("done", "UNSET"), ("esac", "UNSET"), ("in", "UNSET"), ("}", "UNSET"), ("]]", "UNSET"), ("))", "UNSET"),
        ("&&", "UNSET"), ("||", "UNSET"), ("|", "UNSET"), ("&", "UNSET"), ("; a note", "UNSET"), (";;", "UNSET"),
        ("return", "UNSET"), ("exit", "EXITED"), ("exec true", "EXITED"), ("OWNER_NOTE=x)", "UNSET"),
        ("if true", "UNSET"), ("if true; then", "UNSET"), ("while false", "UNSET"), ("until true", "UNSET"),
        ("for x in a", "UNSET"), ("case x in", "UNSET"), ("select x in a", "UNSET"), ("function f {", "UNSET"),
        ("function f", "UNSET"), ("f() {", "UNSET"), ("f()", "UNSET"), ("(", "UNSET"), ("[[ x", "UNSET"), ("((", "UNSET"),
        ("if a; then if b; then :; fi", "UNSET"), ("cat <<EOF", "UNSET"), (": <<'X'", "UNSET"), ("OWNER_ARRAY=(", "UNSET"),
        ("OWNER_NOTE=$(x", "UNSET"), ("OWNER_NOTE=${", "UNSET"), ("OWNER_NOTE=${x", "UNSET"), ("OWNER_NOTE=`", "UNSET"),
        ("readonly WIZARD_CUDA_DEVICE=0", "0"), ("declare -r WIZARD_CUDA_DEVICE=0", "0"), ("typeset -r WIZARD_CUDA_DEVICE=0", "0"),
        ("declare -gr WIZARD_CUDA_DEVICE=0", "0"), ("declare -x -r WIZARD_CUDA_DEVICE=0", "0"),
        ("declare -rx WIZARD_CUDA_DEVICE=0", "0"), ("readonly WIZARD_CUDA_DEVICE", "UNSET"),
        ("eval readonly WIZARD_CUDA_DEVICE", "UNSET"), ("trap 'readonly WIZARD_CUDA_DEVICE' DEBUG", "UNSET"),
        ("set -n", "EXITED"), ("set -o noexec", "EXITED"), ("set -e\nfalse", "EXITED"), ("set -eu; false", "EXITED"),
        ("true && exit", "EXITED"), ("eval exit", "EXITED"), ("coproc", "UNSET"), ('"exit"', "EXITED"), ("x=1 exit", "EXITED"),
        # members a list of stops missed (iteration 2's fuzzer and corpus): grammar after a word or a separator
        ("echo (x", "UNSET"), ("echo x)", "UNSET"), ("echo a | (cat", "UNSET"), ("x=1 echo )", "UNSET"),
        ("echo a; }", "UNSET"), ("echo a && {", "UNSET"), ("echo a; if true", "UNSET"), ("x=1; if true", "UNSET"),
        ("( : ) ; }", "UNSET"), ("f () {", "UNSET"), ("case x in x) if true;; esac", "UNSET"), ("! esac", "UNSET"),
        ("time done", "UNSET"),
        # bash reads the pin after each of these
        ("set -e", "1"), ("false && exit", "1"), ("OWNER_ARRAY=(\nx\n)", "1"), ("{\n:\n}", "1"), ("if true; then\n:\nfi", "1"),
        (": <<X\nX", "1"), ("set -e\nOWNER_NOTE=1", "1"), ("OWNER_ARRAY=(a b)", "1"), ("OWNER_ARRAY=(x)", "1"),
        ("OWNER_ARRAY=(x y) z", "1"), ("{ :; }", "1"), ("( : )", "1"), ("if true; then :; fi", "1"), ("f() { :; }", "1"),
        ("for x in a; do :; done", "1"), ("case x in x) ;; esac", "1"), ("while false; do :; done", "1"), ("[[ x ]]", "1"),
        ("((1))", "1"), ("OWNER_NOTE=$(x)", "1"), ("cat <<<x", "1"), ("OWNER_NOTE='x <<EOF'", "1"), ("!", "1"),
        ("echo ok", "1"), ("x=1 y", "1"), ("PATH", "1"), ("cd /", "1"), ("set -a", "1"), ("set -u", "1"), ("set -t", "1"),
        ("source /nonexistent", "1"), ("false", "1"), ("unset WIZARD_CUDA_DEVICE", "1"), ("local -r WIZARD_CUDA_DEVICE=0", "1"),
        ("declare -p WIZARD_CUDA_DEVICE", "1"), ("break", "1"), ("continue", "1"), ("logout", "1"), ("time", "1"),
        ("exec >/dev/null", "1"), ("alias x=y", "1"), ("eval x", "1"), ("declare -a OWNER_ARRAY=(x)", "1"),
        ("export OWNER_ARRAY=(x)", "1"), ("echo {", "1"), ("echo }", "1"), ("echo a{b", "1"), ('echo "(" x', "1"),
        ("echo \\( x", "1"), ("x=1 y=2 fi", "1"), ("x=1 if", "1"), ("function f { :; }", "1"), ("echo {fi", "1"),
        ("cat <(echo x)", "1"), ("OWNER_NOTE='a\nb'", "1"), ("OWNER_NOTE=$HOME/x", "1"), ("! true", "1"),
        ("set +a", "1"), ("set -x +a", "1"), ("fc -s", "1"),
        # iteration 3 (found by applying the remedies): a '#' in a one-line array begins a comment -- the array stays open
        ("OWNER_ARRAY=(x # x)", "UNSET"), ("OWNER_ARRAY=(a #)", "UNSET"),
    )
    # a refusal says a shell STOPS (or cannot reassign the pin) where that is certain -- `set -n` is not (an interactive
    # shell ignores it: iteration 4) ...
    SHELL_STOPS = (")", "fi", "then", "done", "}", "&&", "; a note", "return", "exit", "exec true", "OWNER_NOTE=x)", "if true",
                   "while false", "f()", "(", "[[ x", "cat <<EOF", "OWNER_ARRAY=(", "OWNER_NOTE=$(x", "OWNER_NOTE=`")
    SHELL_FREEZES = ("readonly WIZARD_CUDA_DEVICE=0", "declare -r WIZARD_CUDA_DEVICE=0", "declare -x -r WIZARD_CUDA_DEVICE=0",
                     "readonly WIZARD_CUDA_DEVICE")
    # ... and a line that runs NO command -- assignments only (or a complete one-line array with no '[') -- is read as
    # systemd reads it: never refused. Iteration 4: EVERY command is 'not modelled' now -- any command word may be a
    # function or alias of the sourcing shell, `break` ends a file sourced from a loop, `test -v 'A[E]'` assigns -- so
    # `echo ok`, `cd /`, `set -a`, `unset K`, `break` below the line are refused as NOT MODELLED (honest over-refusal:
    # bash reads the pin after each; the property above holds -- such a refusal says 'not model', never 'stops')
    SHELL_READS = ("OWNER_ARRAY=(a b)", "OWNER_ARRAY=(x)", "OWNER_NOTE='a\nb'", "OWNER_NOTE=$HOME/x", "OWNER_A=1 OWNER_B=2",
                   'OWNER_NOTE="it\'s"', 'OWNER_NOTE="a\\\\b\\"c"')
    SHELL_COMMANDS_NOW_REFUSED = ("echo ok", "x=1 y", "PATH", "cd /", "false", "unset WIZARD_CUDA_DEVICE", "set -a", "break",
                                  "continue", "export OWNER_ARRAY=(x)", "echo {", "! true", "true", ":", "/bin/true",
                                  "test -n x", "export OWNER_E=1")

    def _refusals(self, first):
        _res, rows = self._check(first + "\nWIZARD_CUDA_DEVICE=1\n", "ambient")
        return " ".join(m for lvl, m in rows if lvl == "REFUSE")

    def test_advisory_7s_class_every_line_after_which_a_shell_may_not_read_the_pin(self):
        for first, bash in self.SHELL_CLASS:
            refusals = self._refusals(first)
            if bash != "1":
                self.assertTrue(refusals, "%r: no refusal, while a shell does not read the pin as systemd does" % first)
            if "stops reading" in refusals:
                self.assertNotEqual(bash, "1", "%r: a refusal that says 'stops' where bash assigned the pin" % first)
            if "cannot reassign" in refusals:
                self.assertNotEqual(bash, "1", first)
            if bash == "1" and refusals:
                self.assertIn("not model", refusals, "%r: bash reads the pin; a refusal may only say it is not modelled" % first)
        for first in self.SHELL_STOPS:
            self.assertIn("a shell stops reading the file at line 1", self._refusals(first), first)
        for first in self.SHELL_FREEZES:
            self.assertIn("a shell cannot reassign it after line 1", self._refusals(first), first)
        for first in self.SHELL_READS:
            self.assertEqual(self._refusals(first), "", first)
        for first in self.SHELL_COMMANDS_NOW_REFUSED:
            refusals = self._refusals(first)
            self.assertIn("not model", refusals, "%r: a command before the pin must be refused as NOT MODELLED" % first)
            self.assertNotIn("stops reading", refusals, first)

    def test_advisory_7s_class_premise_is_bashs_own_reading(self):
        if not shutil.which("bash"):
            self.skipTest("no bash on this host: the premise is re-measured where it exists")
        for first, want in self.SHELL_CLASS:
            p = self.tmp / "sh7"
            p.write_text(first + "\nWIZARD_CUDA_DEVICE=1\n", encoding="utf-8")
            b = subprocess.run(["bash", "-c", 'cd "$2" && set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; '
                                'printf "<%s>" "${WIZARD_CUDA_DEVICE-UNSET}"', "_", str(p), str(self.tmp)],
                               env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            got = b.stdout[1:-1] if b.stdout.startswith("<") else "EXITED"
            self.assertEqual(got, want, "premise: bash's reading of the pin after %r" % first)

    def test_advisory_7s_class_holds_under_a_seeded_fuzz_against_bash(self):
        """The class has no finite list of members, so it is also held against bash on random lines of harmless shell
        fragments (seeded: the same lines every run; a line never writes a file): no line after which bash does not read
        the pin as systemd does goes unrefused, and no refusal says a shell stops where bash read the pin line."""
        if not shutil.which("bash"):
            self.skipTest("no bash on this host")
        import random
        fr = ["echo", "x", "true", "false", ":", "if", "then", "else", "elif", "fi", "{", "}", "(", ")", ";", "&&", "||", "|",
              "&", "case", "in", "esac", "x)", ";;", "for", "do", "done", "while false", "until true", "a=1", "b=(x", "c=(x)",
              "$(", "$(x)", "`", "${", "${x}", '"("', "\\(", "'}'", "exit", "return", "readonly WIZARD_CUDA_DEVICE", "set -e",
              "set -n", "[[", "]]", "((", "))", "!", "time", "function", "f()", "#", "<<<x", "eval", "command", "fi;", "{fi",
              '"exit"', "x=1 exit", "WIZARD_CUDA_DEVICE=0", "read WIZARD_CUDA_DEVICE", ": ${WIZARD_CUDA_DEVICE:=0}",
              "declare -r WIZARD_CUDA_DEVICE=0", "'if'", "set -a", "A=(x y)", "echo ok", "<<EOF", "EOF", "exec true"]
        rnd, p, bad = random.Random(20260926), self.tmp / "fz", []
        for _k in range(400):
            first = "\n".join(" ".join(rnd.choice(fr) for _ in range(rnd.randint(1, 5))) for _ in range(rnd.randint(1, 2)))
            data = (first + "\nWIZARD_CUDA_DEVICE=1\n").encode("utf-8")
            p.write_bytes(data)
            try:
                b = subprocess.run(["bash", "-c", 'cd "$2" && set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; '
                                    'printf "<%s>" "${WIZARD_CUDA_DEVICE-UNSET}"', "_", str(p), str(self.tmp)],
                                   env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=10,
                                   stdin=subprocess.DEVNULL).stdout
            except subprocess.TimeoutExpired:
                continue
            bash = b[1:-1] if b.startswith("<") else "EXITED"
            res = self.ef.parse(data)
            if res["error"]:
                continue
            sd = self.ef.readings(res, "WIZARD_CUDA_DEVICE", str(ORGANS))[0]
            refusals = " ".join(m for lvl, m in self.ef.check(res, "F", "ambient", str(ORGANS)) if lvl == "REFUSE")
            # a line of this fuzz may assign the pin 0 BEFORE the pin line: a true 'stops' leaves bash on that 0
            if (bash != sd and not refusals) or ("stops reading" in refusals and bash == sd) \
                    or ("cannot reassign" in refusals and bash == sd):
                bad.append((first, bash, refusals[:160]))
        self.assertEqual(bad, [], "lines the model reads differently from bash")

    # EW2-01 (iteration-2 reflection, measured with bash): a line AFTER the pin that changes it without naming it
    # literally -- a plain `unset` whose name is quoted, escaped or expanded, or any line the whitelist does not model
    # (`read -r $K`, `printf -v "$K" 0`, `declare "$K=0"`, `eval K""EY=0`, an `if` that unsets it) -- left the shell's
    # value at the pin's: 13 files bash reads differently from systemd were not refused by the iteration-2 artifacts
    SHELL_AFTER_PIN = (
        ('unset -v "WIZARD_CUDA_""DEVICE"', "UNSET"), ("unset WIZARD_CUDA_DEVIC\\E", "UNSET"),
        ("K=WIZARD_CUDA_DEVICE\nunset $K", "UNSET"), ('K=WIZARD_CUDA_DEVICE\nunset "$K"', "UNSET"),
        ('unset -v WIZARD_CUDA_DEVIC"E"', "UNSET"), ("K=WIZARD_CUDA_DEVICE\nread -r $K </dev/null", ""),
        ('K=WIZARD_CUDA_DEVICE\nprintf -v "$K" 0', "0"), ('K=WIZARD_CUDA_DEVICE\ndeclare "$K=0"', "0"),
        ('eval WIZARD_CUDA_DEV""ICE=0', "0"), ("K=WIZARD_CUDA_DEVICE\nif true; then unset $K; fi", "UNSET"),
        ('K=WIZARD_CUDA_DEVICE\nfor v in 0; do export "$K=$v"; done', "0"),
        ('K=WIZARD_CUDA_DEVICE\n[ -n "$K" ] && unset "$K"', "UNSET"), ("K=WIZARD_CUDA_DEVICE\nunset $K; echo x", "UNSET"),
    )

    def test_a_line_after_the_pin_that_changes_it_unnamed_is_refused(self):
        for after, bash in self.SHELL_AFTER_PIN:
            _res, rows = self._check("WIZARD_CUDA_DEVICE=1\n" + after + "\n", "ambient")
            refusals = " ".join(m for lvl, m in rows if lvl == "REFUSE")
            self.assertTrue(refusals, "%r: no refusal, while bash reads %r and systemd '1'" % (after, bash))
        # a line after the pin that runs no command is still read as systemd reads it ...
        for after in ("OWNER_NOTE=x", "OWNER_ARRAY=(a b)", "OWNER_NOTE='a b'"):
            _res, rows = self._check("WIZARD_CUDA_DEVICE=1\n" + after + "\n", "ambient")
            self.assertEqual([m for lvl, m in rows if lvl == "REFUSE"], [], after)
        # ... and one that runs ANY command is not modelled (iteration 4: it may be a function of the sourcing shell)
        for after in ("unset OTHER", "echo ok", "cd /"):
            _res, rows = self._check("WIZARD_CUDA_DEVICE=1\n" + after + "\n", "ambient")
            self.assertIn("not model", " ".join(m for lvl, m in rows if lvl == "REFUSE"), after)

    def test_a_line_after_the_pin_premise_is_bashs_own_reading(self):
        if not shutil.which("bash"):
            self.skipTest("no bash on this host: the premise is re-measured where it exists")
        for after, want in self.SHELL_AFTER_PIN:
            p = self.tmp / "sh8"
            p.write_text("WIZARD_CUDA_DEVICE=1\n" + after + "\n", encoding="utf-8")
            b = subprocess.run(["bash", "-c", 'cd "$2" && set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; '
                                'printf "<%s>" "${WIZARD_CUDA_DEVICE-UNSET}"', "_", str(p), str(self.tmp)],
                               env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
            self.assertEqual(b.stdout[1:-1], want, "premise: bash's reading of the pin with %r after it" % after)

    def test_a_certain_stop_keeps_the_shells_value_and_the_remedy_names_the_line_to_change(self):
        # EW2-02: a shell runs no line after a CERTAIN stop -- a later line that merely names the key cannot change its
        # value (bash 1, systemd 1: not refused); a later assignment systemd reads still disagrees, and is refused
        _res, rows = self._check("WIZARD_CUDA_DEVICE=1\ndo\nunset WIZARD_CUDA_DEVICE\n", "ambient")
        self.assertEqual([m for lvl, m in rows if lvl == "REFUSE"], [], rows)
        _res, rows = self._check("WIZARD_CUDA_DEVICE=1\ndo\nWIZARD_CUDA_DEVICE=7\n", "ambient")
        refusal = " ".join(m for lvl, m in rows if lvl == "REFUSE")
        self.assertIn("a shell stops reading the file at line 2", refusal)
        self.assertIn("a shell that sources the file reads '1'", refusal)
        # EW2-03: the remedy names the line to change -- never 'write it as a bare KEY=value line' for a bare line
        _res, rows = self._check("set -e\nWIZARD_CUDA_DEVICE=1\n", "ambient")
        refusal = " ".join(m for lvl, m in rows if lvl == "REFUSE")
        self.assertIn("The line to change is line 1: take it out of this file", refusal)
        self.assertNotIn("Write it as a bare", refusal)
        _res, rows = self._check(")\nWIZARD_CUDA_DEVICE=1\n", "ambient")
        refusal = " ".join(m for lvl, m in rows if lvl == "REFUSE")
        self.assertIn("Line 2 is written correctly -- the line to change is line 1, where a shell stops reading", refusal)

    # EW2-01's class, swept (iteration 3, measured with bash): the reflector's 13 were each one member; these change the
    # pin after it by a spelling of `unset` the iteration-2 artifacts called plain -- or through `builtin` / `command` /
    # an expanded command word -- and each was UNDER-refused there
    SHELL_AFTER_PIN_SWEPT = (
        ("K=WIZARD_CUDA_DEVICE\n\\unset $K", "UNSET"), ("K=WIZARD_CUDA_DEVICE\nun''set $K", "UNSET"),
        ("K=WIZARD_CUDA_DEVICE\nbuiltin unset $K", "UNSET"), ("K=WIZARD_CUDA_DEVICE\ncommand unset $K", "UNSET"),
        ("K=WIZARD_CUDA_DEVICE\nunset -v -- $K", "UNSET"), ("C=unset\nK=WIZARD_CUDA_DEVICE\n$C $K", "UNSET"),
    )

    def test_a_line_after_the_pin_that_unsets_it_by_any_spelling_is_refused(self):
        for after, bash in self.SHELL_AFTER_PIN_SWEPT:
            _res, rows = self._check("WIZARD_CUDA_DEVICE=1\n" + after + "\n", "ambient")
            self.assertTrue([m for lvl, m in rows if lvl == "REFUSE"], "%r: no refusal, while bash reads %r" % (after, bash))
        if shutil.which("bash"):
            for after, want in self.SHELL_AFTER_PIN_SWEPT:
                p = self.tmp / "sh9"
                p.write_text("WIZARD_CUDA_DEVICE=1\n" + after + "\n", encoding="utf-8")
                b = subprocess.run(["bash", "-c", 'cd "$2" && set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; '
                                    'printf "<%s>" "${WIZARD_CUDA_DEVICE-UNSET}"', "_", str(p), str(self.tmp)],
                                   env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
                self.assertEqual(b.stdout[1:-1], want, "premise: bash's reading of the pin with %r after it" % after)

    # iteration 3's class sweep: `set +a` before the pin -- a shell still ASSIGNS it, but no longer EXPORTS it, so a
    # program started from that shell (an organ run by hand) never sees the pin while every unit reads it; and
    # `export -n $K` after it does the same. Measured with bash: the variable '1', a child's environment without it.
    SHELL_UNEXPORTS = ("set +a\nWIZARD_CUDA_DEVICE=1", "set -x +a\nWIZARD_CUDA_DEVICE=1",
                       "WIZARD_CUDA_DEVICE=1\nK=WIZARD_CUDA_DEVICE\nexport -n $K")

    def test_a_line_that_stops_the_pin_being_exported_is_refused(self):
        for text in self.SHELL_UNEXPORTS:
            _res, rows = self._check(text + "\n", "ambient")
            self.assertTrue([m for lvl, m in rows if lvl == "REFUSE"], "%r: no refusal, while a child of the shell sees no pin" % text)
        # `set -a` / `set -x` change nothing a shell exports -- but they are COMMANDS (a function of the sourcing shell may
        # be named `set`; PS4 under xtrace is expanded: iteration 4), refused as not modelled, never as a stop
        _res, rows = self._check("set -a\nset -x\nWIZARD_CUDA_DEVICE=1\n", "ambient")
        refusal = " ".join(m for lvl, m in rows if lvl == "REFUSE")
        self.assertIn("not model", refusal)
        self.assertNotIn("stops reading", refusal)
        if shutil.which("bash"):
            for text in self.SHELL_UNEXPORTS:
                p = self.tmp / "sh10"
                p.write_text(text + "\n", encoding="utf-8")
                b = subprocess.run(["bash", "-c", 'cd "$2" && set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; '
                                    'printf "<%s|%s>" "${WIZARD_CUDA_DEVICE-UNSET}" "$(env | sed -n "s/^WIZARD_CUDA_DEVICE=//p")"',
                                    "_", str(p), str(self.tmp)],
                                   env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
                self.assertEqual(b.stdout, "<1|>", "premise: bash assigns the pin but exports nothing after %r" % text)

    # iteration 3 (found by applying the remedies: remedy_exec.py): a here-document with no delimiter line takes every later
    # line as its text -- a CERTAIN stop -- but bash still RUNS the command on that line. The model said a shell never runs
    # the line it stops at: the iteration-2 artifacts under-refused each of these (measured with bash: 7, UNSET, UNSET)
    SHELL_HEREDOC_RUNS = (("WIZARD_CUDA_DEVICE=1\nexport WIZARD_CUDA_DEVICE=7 cat <<EOF", "7"),
                          ("WIZARD_CUDA_DEVICE=1\nK=WIZARD_CUDA_DEVICE\nunset $K <<EOF", "UNSET"),
                          ("WIZARD_CUDA_DEVICE=1\nunset WIZARD_CUDA_DEVICE OTHER=1 cat <<EOF", "UNSET"))

    def test_a_here_document_left_open_still_runs_its_own_line(self):
        for text, bash in self.SHELL_HEREDOC_RUNS:
            _res, rows = self._check(text + "\n", "ambient")
            self.assertTrue([m for lvl, m in rows if lvl == "REFUSE"], "%r: no refusal, while bash reads %r" % (text, bash))
        # before the pin: refused, and never told as 'a shell reads nothing' -- bash assigned 5 on that line
        _res, rows = self._check("export WIZARD_CUDA_DEVICE=5 <<EOF\nWIZARD_CUDA_DEVICE=1\n", "ambient")
        refusal = " ".join(m for lvl, m in rows if lvl == "REFUSE")
        self.assertTrue(refusal)
        self.assertNotIn("reads nothing", refusal)
        # a here-document before the pin that names nothing still swallows it: a certain stop, as before
        self.assertIn("a shell stops reading the file at line 1", self._refusals("cat <<EOF"))
        if shutil.which("bash"):
            for text, want in self.SHELL_HEREDOC_RUNS:
                p = self.tmp / "sh11"
                p.write_text(text + "\n", encoding="utf-8")
                b = subprocess.run(["bash", "-c", 'cd "$2" && set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; '
                                    'printf "<%s>" "${WIZARD_CUDA_DEVICE-UNSET}"', "_", str(p), str(self.tmp)],
                                   env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
                self.assertEqual(b.stdout[1:-1], want, "premise: bash's reading of the pin in %r" % text)

    def test_a_certain_stop_is_not_called_unknown(self):
        # EW2-02 alone: after a CERTAIN stop a shell runs no later line, so a later line that names the key changes
        # nothing -- the iteration-2 artifacts refused it saying the shell reads 'a value this installer does not model'
        for later in ("unset WIZARD_CUDA_DEVICE", "read WIZARD_CUDA_DEVICE", "unset WIZARD_CUDA_DEVICE exit 0"):
            _res, rows = self._check("WIZARD_CUDA_DEVICE=1\ndo\n" + later + "\n", "ambient")
            self.assertEqual([m for lvl, m in rows if lvl == "REFUSE"], [], (later, rows))

    # EW2-03: every refusal names THE LINE TO CHANGE and what to do with it -- held as an executed property: the remedy,
    # applied literally (as an owner who reads only the message would), clears the refusal within three rounds and never
    # deletes the line systemd reads the pin from. The iteration-2 artifacts named the pin line and told the owner to
    # 'write it as a bare KEY=value line' -- for a line that was already bare: applied, it changes nothing.
    REMEDY_CASES = (
        ("set -e\nWIZARD_CUDA_DEVICE=1", "The line to change is line 1: take it out of this file"),
        (")\nWIZARD_CUDA_DEVICE=1", "Line 2 is written correctly -- the line to change is line 1, where a shell stops reading"),
        ("WIZARD_CUDA_DEVICE=1\nK=WIZARD_CUDA_DEVICE\nunset $K", "The line to change is line 3: take it out of this file"),
        ("WIZARD_CUDA_DEVICE=1\nunset WIZARD_CUDA_DEVICE", "The line to change is line 2: it is a shell command that names"),
        ("WIZARD_CUDA_DEVICE=1\nexport -n WIZARD_CUDA_DEVICE", "The line to change is line 2: it is a shell command that names"),
        ('if [ "$WIZARD_CUDA_DEVICE" = 1 ]; then :; fi\nWIZARD_CUDA_DEVICE=1', "The line to change is line 1: take it out of this file"),
        ("export WIZARD_CUDA_DEVICE=1", "The line to change is line 1. Write it as a bare WIZARD_CUDA_DEVICE=<value> line"),
        ("WIZARD_CUDA_DEVICE=1; set -e", "The line to change is line 1. Write it as a bare WIZARD_CUDA_DEVICE=<index> line"),
        ("WIZARD_CUDA_DEVICE=1\ndo\nWIZARD_CUDA_DEVICE=7\nunset WIZARD_CUDA_DEVICE",
         "Line 3 is written correctly -- the line to change is line 2, where a shell stops reading"),
        ("set +a\nWIZARD_CUDA_DEVICE=1", "The line to change is line 1: take it out of this file"),
        ("set -e\nCUDA_VISIBLE_DEVICES=1 # the compute card", "The line to change is line 2. Write it as a bare CUDA_VISIBLE_DEVICES=<index> line"),
    )

    def _apply_remedy(self, msg, lines):
        """(the lines after the remedy the message states, the line number it DELETED or None) -- or (None, None)."""
        import re
        m = re.search(r"Line (\d+) is written correctly -- the line to change is line (\d+), where a shell stops reading", msg)
        if m:
            n = int(m.group(2))
            return lines[:n - 1] + lines[n:], n
        m = re.search(r"The line to change is line (\d+)(?::|,) (?:it is a shell command|take it out|where a shell stops)", msg)
        if m:
            n = int(m.group(1))
            return lines[:n - 1] + lines[n:], n
        m = re.search(r"The line to change is line (\d+)\. Write it as a bare (\w+)=<(?:value|index)> line", msg)
        if m:
            n = int(m.group(1))
            return lines[:n - 1] + ["%s=1" % m.group(2)] + lines[n:], None
        return None, None

    def test_every_refusal_names_the_line_to_change_and_its_remedy_clears_it(self):
        for text, said in self.REMEDY_CASES:
            _res, rows = self._check(text + "\n", "ambient")
            refusals = [m for lvl, m in rows if lvl == "REFUSE"]
            self.assertTrue(refusals, text)
            self.assertIn(said, refusals[0], text)
            lines = text.split("\n")
            for _round in range(3):
                res0 = self.ef.parse(("\n".join(lines) + "\n").encode("utf-8"))
                pin_lines = set(v[1] for k, v in res0["outside"].items() if k in self.ef.PIN_KEYS)
                new, deleted = self._apply_remedy(refusals[0], lines)
                self.assertIsNotNone(new, "%r: the refusal names no remedy an owner could apply: %s" % (text, refusals[0][-240:]))
                self.assertNotEqual(new, lines, "%r: the remedy, applied, changes nothing: %s" % (text, refusals[0][-240:]))
                self.assertNotIn(deleted, pin_lines, "%r: the remedy deleted the line systemd reads the pin from" % text)
                lines = new
                _res, rows = self._check("\n".join(lines) + "\n", "ambient")
                refusals = [m for lvl, m in rows if lvl == "REFUSE"]
                if not refusals:
                    break
            self.assertEqual(refusals, [], "%r: the remedies did not clear the refusal in three rounds" % text)

    def test_a_rejected_value_is_named_by_its_own_line_and_reason(self):
        # the REJECTED refusal named an EARLIER shell line's reading as the reason (`set -e` before `CVD=1 # c` said 'because
        # a shell's reading of the lines after line 1 is not modelled'); the reason is the value line's own
        _res, rows = self._check("set -e\nCUDA_VISIBLE_DEVICES=1 # the compute card\n", "ambient")
        refusal = " ".join(m for lvl, m in rows if lvl == "REFUSE")
        self.assertIn("line 2: CUDA_VISIBLE_DEVICES reads as '1 # the compute card'", refusal)
        self.assertIn("because it carries an inline '#' comment", refusal)
        _res, rows = self._check("CUDA_VISIBLE_DEVICES=1 # the compute card\nset -e\n", "ambient")
        refusal = " ".join(m for lvl, m in rows if lvl == "REFUSE")
        self.assertIn("line 1: CUDA_VISIBLE_DEVICES reads as '1 # the compute card'", refusal)
        self.assertIn("The line to change is line 1.", refusal)

    def test_a_key_only_the_block_assigns_is_not_called_undeclared_by_systemd(self):
        # a line after the managed block that names CUDA_VISIBLE_DEVICES, which only the block assigns: the refusal said
        # 'systemd never hands it to the units' -- the block does; it now says no OWNER line declares it
        _res, rows = self._check("", "ambient", block="CUDA_VISIBLE_DEVICES='1'\n")
        self.assertEqual(rows, [])
        p = self.tmp / "blk"
        p.write_text(BEGIN + "\nCUDA_VISIBLE_DEVICES='1'\n" + END + "\nunset CUDA_VISIBLE_DEVICES\n", encoding="utf-8")
        refusal = " ".join(m for lvl, m in self.ef.check(self.ef.read_file(str(p), BEGIN, END), str(p), "ambient", str(ORGANS))
                           if lvl == "REFUSE")
        self.assertNotIn("systemd never hands it to the units", refusal)
        self.assertIn("the units read the managed block's own", refusal)
        self.assertIn("The line to change is line 4: it is a shell command", refusal)

    def test_the_owner_facing_statements_match_the_behaviour(self):
        # EW2-04 / EW2-05: the guide told the owner the ambient shape refuses a pin 'a shell would read otherwise' -- it
        # also refuses one after any shell command, and nothing said to keep shell commands out; the write-set said the
        # env file is written with `cp -f`, the one command that REPLACED a symlink instead of writing through it (EW1-01)
        guide = (ROOT / "docs" / "ONBOARDING_GUIDE.md").read_text(encoding="utf-8")
        self.assertIn("naming the line to change: keep shell commands", guide)
        ws = (ROOT / "install" / "write_set.json").read_text(encoding="utf-8")
        self.assertNotIn("cp -f and chmod write THROUGH", ws)
        self.assertIn("cp (never cp -f) and chmod write THROUGH", ws)

    def test_a_shell_stops_reading_at_a_syntax_error(self):
        _res, rows = self._check("; a note\nWIZARD_CUDA_DEVICE=1\n", "ambient")
        self.assertIn("REFUSE", self._levels(rows), rows)
        # the class, not only ';': a ')' is a syntax error and `A=(` swallows the following lines into an array -- a
        # shell sourcing the file never assigns the pin after either (bash, executed below where the host has it)
        for first in ("OWNER_ARRAY=(\n", "OWNER_NOTE=x)\n"):
            res, rows = self._check(first + "WIZARD_CUDA_DEVICE=1\n", "ambient")
            self.assertIn("REFUSE", self._levels(rows), (first, rows))
            self.assertIn("a shell stops reading the file at line 1", " ".join(m for _l, m in rows))
            if shutil.which("bash"):
                p = self.tmp / "sh"
                p.write_text(first + "WIZARD_CUDA_DEVICE=1\n", encoding="utf-8")
                b = subprocess.run(["bash", "-c", 'set -a; . "$1" 2>/dev/null; printf "%s" "${WIZARD_CUDA_DEVICE-UNSET}"', "_",
                                    str(p)], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60)
                self.assertEqual(b.stdout, "UNSET", "premise: bash never assigns the pin after %r" % first)

    def test_the_blocks_own_check_names_keys_never_value_fragments(self):
        # EW1-06 (iteration-1 reflection): verify_block printed _candidate_keys' hits in a VALUE as key names --
        # `PATH="/opt/PATHPART=zz:$x"` was reported as '(PATH, PATHPART)'. Names only, as check() (W4)
        blk = (BEGIN + '\nPATH="/opt/PATHPART=zz:$x"\n' + END + "\n").encode()
        bad = self.ef.verify_block(self.ef.parse(blk, BEGIN, END))
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("block line 2 (PATH):", bad[0])
        self.assertNotIn("PATHPART", bad[0], "the block's check printed value text as a key name")

    def test_the_managed_block_is_the_installers_and_is_not_checked(self):
        _res, rows = self._check("", "ambient", block="export CUDA_VISIBLE_DEVICES=1 # an old block\n")
        self.assertEqual(rows, [])

    def test_a_marker_inside_an_owners_value_is_refused(self):
        _res, rows = self._check("A='x\n%s\ny'\n" % BEGIN, "venv")
        self.assertIn("REFUSE", self._levels(rows), rows)
        self.assertIn("The line to change is line 1: end its value before that marker line", " ".join(m for _l, m in rows))

    def test_the_landing_shape_is_clean(self):
        """The reference host's shape (key names only were ever read from the real file): a bare token line, a bare
        WIZARD_CUDA_DEVICE, a bare display declaration, the prior block -- nothing refused, nothing warned."""
        owner = "CLAUDE_CODE_OAUTH_TOKEN=fake-not-a-token\nWIZARD_CUDA_DEVICE=1\nWIZARD_DISPLAY_INDEX=0\nVOIDLOOM_AFFECT_PROBE=1\n"
        block = "PATH=\"/h/.local/bin:/usr/bin:/bin\"\nWIZARD_LIBRARY_HOME='/h/.claude/memory'\nWIZARD_EMBED_MODEL=jina\n"
        _res, rows = self._check(owner, "ambient", block=block)
        self.assertEqual(rows, [])

    def test_the_writers_block_must_read_the_same_to_both(self):
        ok = (BEGIN + "\nPATH=\"/a b/bin:/usr/bin\"\nWIZARD_LIBRARY_HOME='/x/.claude/memory'\n" + END + "\n").encode()
        bad = (BEGIN + "\nPATH=\"/a$b/bin:/usr/bin\"\nWIZARD_LIBRARY_HOME='/x/it's/memory'\n" + END + "\n").encode()
        self.assertEqual(self.ef.verify_block(self.ef.parse(ok, BEGIN, END)), [])
        self.assertEqual(len(self.ef.verify_block(self.ef.parse(bad, BEGIN, END))), 2)


class TestTheCliIsTheInstallersInterface(unittest.TestCase):
    """install.sh calls the reader as a program (env_outside): the keys, a value, the units' pin, the policy."""

    def setUp(self):
        _reader()
        self.tmp = Path(tempfile.mkdtemp(prefix="envcli_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.f = self.tmp / "env"
        self.f.write_text("WIZARD_CUDA_DEVICE=1 # c\nexport PATH=/x\n" + BEGIN + "\nPATH='/blk'\n" + END + "\n",
                          encoding="utf-8")

    def _run(self, *args):
        return subprocess.run([sys.executable, "-B", str(READER), "--file", str(self.f), "--begin", BEGIN, "--end", END,
                               "--organs", str(ORGANS)] + list(args), capture_output=True, text=True, timeout=60)

    def test_modes(self):
        pyc_before = sorted(p.name for p in ORGANS.glob("__pycache__/*"))
        self.assertEqual(self._run("keys").stdout.split(), ["WIZARD_CUDA_DEVICE"], "an export line is not a key")
        self.assertEqual(self._run("get", "WIZARD_CUDA_DEVICE").stdout, "1 # c\n")
        self.assertEqual(self._run("unitpin", "WIZARD_CUDA_DEVICE").stdout, "1\n")
        self.assertEqual(self._run("effective", "PATH").stdout, "/blk\n")
        self.assertEqual(self._run("get", "PATH").returncode, 1)
        chk = self._run("check", "venv")
        self.assertEqual(chk.returncode, 0, chk.stdout + chk.stderr)
        self.assertTrue(all(l.startswith("WARN\t") for l in chk.stdout.splitlines()), chk.stdout)
        self.assertEqual(sorted(p.name for p in ORGANS.glob("__pycache__/*")), pyc_before,
                         "the reader left bytecode in the package it ran from")

    def test_run_by_hand_without_organs_it_finds_the_resolver_beside_it(self):
        """W7 (found at the landing, #21961/#21962): `install/env_file.py --file F check ...` run by hand -- no
        --organs -- died with ModuleNotFoundError: voidloom_device. The resolver is the package's organs/ beside
        install/; where it is not there, the reader says so by name (rc 69) instead of a traceback."""
        for args, want in ((["unitpin", "WIZARD_CUDA_DEVICE"], (0, "1\n")), (["effective-pin"], (0, "1\n")),
                           (["check", "venv"], (0, None))):
            p = subprocess.run([sys.executable, "-B", str(READER), "--file", str(self.f)] + args, capture_output=True,
                               text=True, timeout=60, cwd=str(self.tmp))
            self.assertNotIn("Traceback", p.stderr, (args, p.stderr[-600:]))
            self.assertEqual(p.returncode, want[0], (args, p.stdout, p.stderr[-600:]))
            if want[1] is not None:
                self.assertEqual(p.stdout, want[1], args)
        alone = self.tmp / "alone" / "install"
        alone.mkdir(parents=True)
        shutil.copy2(str(READER), str(alone / "env_file.py"))
        p = subprocess.run([sys.executable, "-B", str(alone / "env_file.py"), "--file", str(self.f), "unitpin",
                            "WIZARD_CUDA_DEVICE"], capture_output=True, text=True, timeout=60, cwd=str(self.tmp))
        self.assertEqual(p.returncode, 69, p.stderr[-600:])
        self.assertIn("pass --organs", p.stderr)
        self.assertNotIn("Traceback", p.stderr)


class TestTheEffectivePinIsTheWholeFile(unittest.TestCase):
    """`effective-pin`: the pin every unit reads from the file AS WRITTEN -- systemd's reading of the WHOLE file, every
    block included -- through the units' resolver order (install.sh reads it back after Step D, ambient shape)."""

    def test_a_stale_second_block_wins_and_is_seen(self):
        _reader()
        tmp = Path(tempfile.mkdtemp(prefix="effpin_")); self.addCleanup(lambda: shutil.rmtree(str(tmp), ignore_errors=True))
        f = tmp / "env"
        f.write_text("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\n" + END + "\n" + BEGIN + "\nCUDA_VISIBLE_DEVICES='0'\n" + END + "\n",
                     encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(READER), "--file", str(f), "--organs", str(ORGANS), "effective-pin"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, "0\n"), "systemd reads the stale block's CUDA_VISIBLE_DEVICES last")
        f.write_text("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\n" + END + "\n", encoding="utf-8")
        r = subprocess.run([sys.executable, "-B", str(READER), "--file", str(f), "--organs", str(ORGANS), "effective-pin"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, "1\n"))


def _systemd_reads(paths):
    """systemd's OWN load_env_file() (the _ORACLE subprocess) on each path: {path: REJECT | {key: value}}, or None
    where the host has no libsystemd-shared (the golden corpus above binds the port there)."""
    p = subprocess.run([sys.executable, "-c", _ORACLE] + [str(x) for x in paths], capture_output=True, text=True,
                       timeout=60)
    if p.returncode != 0 or not p.stdout.strip():
        return None
    o = json.loads(p.stdout.strip().splitlines()[-1])
    if "skip" in o:
        return None
    return {f: (REJECT if v["rc"] < 0 else v["env"]) for f, v in o["files"].items()}


class TestTheWriterIsTheReaders(unittest.TestCase):
    """ENV-FILE WRITER PASS, W1 (the reference-host round's recorded HIGH, #21956): every WRITE of the env file is the
    one reader's own writer, `splice`. At 524c63e the writer was a block replacer reading with
    `open(encoding="utf-8").read().splitlines()` (its exit status unchecked), an appender and a `grep '^(export )?...'`
    token rescue -- measured: a Latin-1 COMMENT emptied the env file (rc 0), a form feed / CR / U+2028 re-split owner
    lines, a missing end marker dropped every later owner line, a stale second block's pin won in systemd, the rescue
    wrote an `export` line systemd drops, an append after an open quote was read into that value. Here every construct
    either keeps every owner byte and reads, to systemd's OWN loader, as it did (plus the block) -- or is REFUSED naming
    the line, nothing written. The installer-level runs are in test_tier1_ambient.py."""

    BLOCK = (BEGIN + "\nPATH=\"/h/.local/bin:/usr/bin:/bin\"\nWIZARD_LIBRARY_HOME='/h/.claude/memory'\n"
             "# CLAUDE_CODE_OAUTH_TOKEN=...   # set for the nocturnal crons (add it OUTSIDE this block)\n" + END + "\n").encode()

    def setUp(self):
        self.ef = _reader()
        self.tmp = Path(tempfile.mkdtemp(prefix="envwriter_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.n = 0

    def _splice(self, data):
        return self.ef.splice(data, self.BLOCK, BEGIN, END)

    def _sd(self, *images):
        """systemd's own reading of each image (written to a file), or None without the oracle."""
        paths = []
        for img in images:
            self.n += 1
            p = self.tmp / ("img%d" % self.n)
            p.write_bytes(img)
            paths.append(p)
        got = _systemd_reads(paths)
        return None if got is None else [got[str(p)] for p in paths]

    def test_every_owner_byte_outside_the_block_survives_and_reads_the_same_to_systemd(self):
        head = b"# caf\xe9 -- the compute card\nWIZARD_CUDA_DEVICE=1\r\nNOTE=a\x0cb\nNOTE2='x\xe2\x80\xa8y'\n"
        tail = b"AFTER=1\n# a trailing \xff comment\n"
        data = head + (BEGIN + "\nPATH='/old'\n" + END + "\n").encode() + tail
        image, how, rescued = self._splice(data)
        self.assertEqual((how, rescued), ("replaced", []))
        self.assertEqual(image, head + self.BLOCK + tail, "an owner byte outside the block changed")
        sd = self._sd(data, image)
        if sd is None:
            return
        before, after = sd
        self.assertNotEqual(after, REJECT)
        for k in ("WIZARD_CUDA_DEVICE", "NOTE", "NOTE2", "AFTER"):
            self.assertEqual(after[k], before[k], "systemd reads the owner's %s differently after the write" % k)
        self.assertEqual(after["PATH"], "/h/.local/bin:/usr/bin:/bin")

    def test_a_marker_shaped_line_inside_a_value_is_not_the_blocks_end(self):
        # the reader's rule: a marker is a marker only where a logical line starts. The proposal's replacer took the
        # first END-shaped line inside the old block's value and wrote a file its own check refuses on the next run
        data = (BEGIN + "\nA='x\n" + END + "\n'\nB=2\n" + END + "\nOWN=1\n").encode()
        image, how, _r = self._splice(data)
        self.assertEqual(image, self.BLOCK + b"OWN=1\n")
        self.assertEqual(self._splice(image)[0], image, "a second run must find exactly the block this run wrote")

    def test_the_token_the_units_had_is_the_token_they_keep(self):
        cases = (
            # systemd reads an indented line; a `grep '^...'` rescue missed it and the units lost their token
            ((BEGIN + "\n   CLAUDE_CODE_OAUTH_TOKEN=fake-indented\n" + END + "\n").encode(),
             b"\nCLAUDE_CODE_OAUTH_TOKEN=fake-indented\n", "fake-indented"),
            # an old block's `export` line (systemd dropped it): written back BARE, the value that line meant
            ((BEGIN + "\nexport CLAUDE_CODE_OAUTH_TOKEN=fake-exported\n" + END + "\n").encode(),
             b"\nCLAUDE_CODE_OAUTH_TOKEN=fake-exported\n", "fake-exported"),
            # a value with a blank in it: single-quoted, which systemd and a shell both read verbatim
            ((BEGIN + "\nCLAUDE_CODE_OAUTH_TOKEN='fake with blank'\n" + END + "\n").encode(),
             b"\nCLAUDE_CODE_OAUTH_TOKEN='fake with blank'\n", "fake with blank"),
        )
        for data, line, value in cases:
            image, _how, rescued = self._splice(data)
            self.assertEqual(rescued, ["CLAUDE_CODE_OAUTH_TOKEN"], data)
            self.assertTrue(image.endswith(line), image[-90:])
            res = self.ef.parse(image, BEGIN, END)
            self.assertEqual([u.cls for u in res["units"] if u.line == res["effective"]["CLAUDE_CODE_OAUTH_TOKEN"][1]],
                             ["safe"], "the re-appended line must read alike to systemd and a shell")
            sd = self._sd(image)
            if sd is not None:
                self.assertEqual(sd[0].get("CLAUDE_CODE_OAUTH_TOKEN"), value)
        # the owner's own token line OUTSIDE the block is theirs: kept once, nothing appended
        image, _how, rescued = self._splice(("CLAUDE_CODE_OAUTH_TOKEN=fake-outside\n" + BEGIN + "\n" + END + "\n").encode())
        self.assertEqual((rescued, image.count(b"CLAUDE_CODE_OAUTH_TOKEN=fake-outside")), ([], 1))
        # an old block's token line no two readers agree on is REFUSED by line -- never dropped, never rewritten as
        # something a shell would expand -- and the refusal prints no part of it
        with self.assertRaises(self.ef.Refusal) as cm:
            self._splice((BEGIN + '\nexport CLAUDE_CODE_OAUTH_TOKEN="fa$ke-x"\n' + END + "\n").encode())
        self.assertIn("line 2", str(cm.exception))
        self.assertNotIn("fa$ke-x", str(cm.exception))

    def test_what_the_writer_cannot_place_is_refused_naming_the_line(self):
        cases = (
            ("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\n" + END + "\n" + BEGIN + "\nCUDA_VISIBLE_DEVICES='0'\n" + END + "\n",
             "markers on lines 2 (begin), 3 (end), 4 (begin), 6 (end)"),
            (BEGIN + "\nPATH='/x'\nWIZARD_CUDA_DEVICE=1\nWIZARD_DISPLAY_INDEX=0\n", "markers on lines 1 (begin)"),
            (END + "\n" + BEGIN + "\n", "markers on lines 1 (end), 2 (begin)"),
            ("WIZARD_CUDA_DEVICE=1\nNOTE='abc", "line 2: the file ends inside a quote"),
            ("WIZARD_CUDA_DEVICE=1\nNOTE=abc\\", "line 2: the file ends inside a quote or after a backslash"),
            ("A='x\n" + BEGIN + "\ny'\n" + BEGIN + "\n" + END + "\n", "line 1:"),
            ("A=1\nB=\xff\n", "line 2: systemd's EnvironmentFile= rejects this WHOLE file"),
        )
        for text, frag in cases:
            data = text.encode("utf-8") if "\xff" not in text else text.encode("latin-1")
            with self.assertRaises(self.ef.Refusal, msg=text) as cm:
                self._splice(data)
            self.assertIn(frag, str(cm.exception))

    def test_no_file_and_no_block_are_the_two_other_writes(self):
        image, how, _r = self._splice(None)
        self.assertEqual((image, how), (self.BLOCK, "created"))
        image, how, _r = self._splice(b"WIZARD_CUDA_DEVICE=1")        # no final newline: kept, then one is added
        self.assertEqual((image, how), (b"WIZARD_CUDA_DEVICE=1\n" + self.BLOCK, "appended"))

    def test_the_cli_writes_only_a_verified_image_and_prints_no_value(self):
        env, blk, out = self.tmp / "env", self.tmp / "block", self.tmp / "out"
        blk.write_bytes(self.BLOCK)
        env.write_bytes((BEGIN + "\nexport CLAUDE_CODE_OAUTH_TOKEN=fake-not-printed\n" + END + "\n").encode())

        def run():
            return subprocess.run([sys.executable, "-B", str(READER), "--file", str(env), "--begin", BEGIN, "--end", END,
                                   "splice", str(blk), str(out)], capture_output=True, text=True, timeout=60)
        p = run()
        self.assertEqual((p.returncode, p.stdout), (0, "OK\treplaced\trescued=CLAUDE_CODE_OAUTH_TOKEN\n"), p.stderr)
        self.assertNotIn("fake-not-printed", p.stdout + p.stderr)
        self.assertEqual(os.stat(str(out)).st_mode & 0o777, 0o600, "the image carries the token: 0600 from creation")
        self.assertTrue(out.read_bytes().endswith(b"\nCLAUDE_CODE_OAUTH_TOKEN=fake-not-printed\n"))
        out.unlink()
        env.write_bytes((BEGIN + "\n" + END + "\n" + BEGIN + "\n" + END + "\n").encode())
        p = run()
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertTrue(p.stdout.startswith("REFUSE\t%s: the managed block is not ONE" % env), p.stdout)
        self.assertFalse(out.exists(), "a refused write left an image")

    def test_a_short_write_leaves_no_image_to_copy(self):
        """A file-size limit (as a full disk would) cuts the image short: 524c63e's replacer left the truncated temp
        file and copied it over the env file. The writer reads its image back from the disk and removes a short one."""
        try:
            import resource
            import signal
        except ImportError:
            self.skipTest("no resource/signal module on this platform")
        env, blk, out = self.tmp / "env", self.tmp / "block", self.tmp / "out"
        blk.write_bytes(self.BLOCK)
        env.write_bytes(b"# " + b"o" * 3000 + b"\nWIZARD_CUDA_DEVICE=1\n" + (BEGIN + "\n" + END + "\n").encode())

        def limit():
            signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
            resource.setrlimit(resource.RLIMIT_FSIZE, (2048, 2048))
        p = subprocess.run([sys.executable, "-B", str(READER), "--file", str(env), "--begin", BEGIN, "--end", END,
                            "splice", str(blk), str(out)], capture_output=True, text=True, timeout=60, preexec_fn=limit)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("could not be written whole", p.stderr)
        self.assertFalse(out.exists(), "a truncated image was left behind")

    def test_a_token_rescue_after_a_trailing_continuation_is_written_correctly(self):
        """EW1-03 (iteration-1 reflection): an owner line AFTER the block ending in a backslash-NEWLINE made the
        re-appended token line part of that value (the append was checked as if one more newline followed it); the
        writer's own read-back caught it -- AssertionError, splice rc 3, install.sh 'could not rehearse', rc 1. Now a
        blank line ends the continuation: systemd reads the owner's value and the token exactly as before."""
        data = ("WIZARD_CUDA_DEVICE=1\n" + BEGIN + "\nCLAUDE_CODE_OAUTH_TOKEN=fake-cont\n" + END + "\nOWNER_TAIL=1\\\n").encode()
        image, how, rescued = self._splice(data)
        self.assertEqual((how, rescued), ("replaced", ["CLAUDE_CODE_OAUTH_TOKEN"]))
        res = self.ef.parse(image, BEGIN, END)
        self.assertEqual((res["effective"]["OWNER_TAIL"][0], res["effective"]["CLAUDE_CODE_OAUTH_TOKEN"][0]), ("1", "fake-cont"))
        sd = self._sd(data, image)
        if sd is not None:
            self.assertEqual((sd[1].get("OWNER_TAIL"), sd[1].get("CLAUDE_CODE_OAUTH_TOKEN")),
                             (sd[0].get("OWNER_TAIL"), "fake-cont"))


class TestTheTokenDoctorReadsWhatTheCronsGet(unittest.TestCase):
    """The class beyond install.sh (#21871): tools/token_doctor.py tells an owner whether the crons have their token,
    and the crons get it through EnvironmentFile=. It took the FIRST line merely MENTIONING the variable -- measured
    on the iteration-3 tool: on a fresh install the managed block's own comment `# CLAUDE_CODE_OAUTH_TOKEN=...` read
    as a 63-character token, PRESENT rc 0, and an `export` line systemd drops read as PRESENT too. It now reads the
    file through the one reader."""

    def setUp(self):
        self.tool = ROOT / "tools" / "token_doctor.py"
        if not self.tool.is_file():
            raise unittest.SkipTest("tree lacks tools/token_doctor.py")
        self.tmp = Path(tempfile.mkdtemp(prefix="tokenv_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        (self.tmp / ".voidloom").mkdir()
        self.f = self.tmp / ".voidloom" / "wizard-library-env"

    def _doctor(self, body):
        self.f.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
        return subprocess.run([sys.executable, "-B", str(self.tool)], capture_output=True, text=True, timeout=60,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1"})

    def test_the_blocks_own_comment_is_not_a_token(self):
        block = (BEGIN + "\nPATH='/usr/bin:/bin'\n# CLAUDE_CODE_OAUTH_TOKEN=...   # set for the nocturnal crons (add it "
                 "OUTSIDE this block)\n" + END + "\n")
        p = self._doctor(block)
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("MISSING", p.stdout)
        p = self._doctor(block + "export CLAUDE_CODE_OAUTH_TOKEN=fake-not-a-token-exported\n")
        self.assertEqual(p.returncode, 3, "an exported line systemd drops read as a token: %s" % p.stdout)
        p = self._doctor(block + "CLAUDE_CODE_OAUTH_TOKEN=fake-not-a-token-bare\n")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PRESENT", p.stdout)
        self.assertNotIn("fake-not-a-token-bare", p.stdout, "the token must be masked")

    def test_the_doctor_reads_the_file_the_units_name(self):
        """ADVISORY 9: install.sh renders WIZARD_ENV_FILE into every unit's EnvironmentFile= and the resolver honours
        it; the doctor read the standard file regardless, so it reported on a file the units do not load."""
        other = self.tmp / "elsewhere" / "units-env"
        other.parent.mkdir()
        other.write_text("CLAUDE_CODE_OAUTH_TOKEN=fake-not-a-token-other\n", encoding="utf-8")
        self.f.write_text("# the standard file carries no token\n", encoding="utf-8")
        p = subprocess.run([sys.executable, "-B", str(self.tool)], capture_output=True, text=True, timeout=60,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1",
                                "WIZARD_ENV_FILE": str(other)})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(str(other), p.stdout)
        self.assertNotIn("fake-not-a-token-other", p.stdout, "the token must be masked")

    def test_a_file_systemd_rejects_whole_gives_the_crons_no_token(self):
        p = self._doctor(b"CLAUDE_CODE_OAUTH_TOKEN=fake-not-a-token\nOWNER_NOTE=caf\xe9\n")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertIn("rejects that WHOLE file", p.stdout)


# ------------------------------------------------------------------------------------------------------------------------
# ENV-FILE WRITER PASS, ITERATION 4 (the iteration-3 reflection's EW3-01 / EW3-02 and the shell-model residue)
# ------------------------------------------------------------------------------------------------------------------------
_BASH_MODES = {
    # the recipe the managed block documents, in the shells an owner sources the file into
    "n": ["bash", "-c", 'cd "$2" && set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; printf "<%s|%s>" '
                        '"${WIZARD_CUDA_DEVICE-UNSET}" "${CUDA_VISIBLE_DEVICES-UNSET}"', "_"],
    "p": ["bash", "--posix", "-c", 'cd "$2" && set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; printf "<%s|%s>" '
                                   '"${WIZARD_CUDA_DEVICE-UNSET}" "${CUDA_VISIBLE_DEVICES-UNSET}"', "_"],
    "l": ["bash", "-c", 'cd "$2" && for _i in 1; do set -a; . "$1" >/dev/null 2>&1 </dev/null; set +a; done; '
                        'printf "<%s|%s>" "${WIZARD_CUDA_DEVICE-UNSET}" "${CUDA_VISIBLE_DEVICES-UNSET}"', "_"],
}


def _bash_reads(path, cwd, mode):
    """(WIZARD_CUDA_DEVICE, CUDA_VISIBLE_DEVICES) as a sourcing bash reads them ('UNSET', or 'EXITED' when the shell
    ended inside the file). Mode n: `bash -c`; p: POSIX mode; l: sourced inside a loop; i: an INTERACTIVE shell (its
    prompts run: PS1 / PROMPT_COMMAND), fed the recipe on stdin."""
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd), "TERM": "dumb"}
    if mode == "i":
        script = ("cd '%s'\nset -a; . '%s' >/dev/null 2>&1 </dev/null; set +a\n:\n"
                  "printf '<%%s|%%s>' \"${WIZARD_CUDA_DEVICE-UNSET}\" \"${CUDA_VISIBLE_DEVICES-UNSET}\"\nexit\n" % (cwd, path))
        out = subprocess.run(["bash", "--norc", "--noprofile", "-i"], input=script, env=env, capture_output=True,
                             text=True, timeout=60).stdout
    else:
        out = subprocess.run(_BASH_MODES[mode] + [str(path), str(cwd)], env=env, capture_output=True, text=True,
                             timeout=60, stdin=subprocess.DEVNULL).stdout
    got = re.findall(r"<([^|<>]*)\|([^|<>]*)>", out)
    return got[-1] if got else ("EXITED", "EXITED")


class TestIteration4TheShellModelRunsNoCommand(unittest.TestCase):
    """The iteration-3 reflection (EW3-01, measured with bash): three sub-classes the whitelist admitted as PLAIN changed
    the pin -- ARITHMETIC in a plain line (`test -v 'A[E]'`, `A=([E]=1)`: a subscript is arithmetic, which assigns), a
    variable the shell itself acts on (PS4 under xtrace; PS0/PS1/PROMPT_COMMAND at an interactive prompt), POSIX mode
    (POSIXLY_CORRECT then a read-only name ends the shell). Iteration 4 closes the CLASS: a line is plain only when it
    runs NO command, and the shell-model residue the iteration-3 comment named is refused where it can be modelled
    (`break`/`continue` in a file sourced from a loop, a function/alias of the sourcing shell, an external command,
    `eval` with no pin line, `unset $K` after the block's own CUDA_VISIBLE_DEVICES, a certain stop before the block)."""

    # (file text, the key, the sourcing shell, bash's reading measured) -- every one is read differently from the units
    MEMBERS = (
        ("WIZARD_CUDA_DEVICE=1\nE=WIZARD_CUDA_DEVICE=0\ntest -v 'A[E]'\n", "WIZARD_CUDA_DEVICE", "n", "0"),
        ("WIZARD_CUDA_DEVICE=1\nE=WIZARD_CUDA_DEVICE=0\nA=([E]=1)\n", "WIZARD_CUDA_DEVICE", "n", "0"),
        ("WIZARD_CUDA_DEVICE=1\nE=WIZARD_CUDA_DEVICE=0\nexport A=([E]=1)\n", "WIZARD_CUDA_DEVICE", "n", "0"),
        ("PS4='$((WIZARD_CUDA_DEVICE=0))'\nset -x\nWIZARD_CUDA_DEVICE=1\nOWNER=1\n", "WIZARD_CUDA_DEVICE", "n", "0"),
        ("PROMPT_COMMAND='WIZARD_CUDA_DEVICE=0'\nWIZARD_CUDA_DEVICE=1\n", "WIZARD_CUDA_DEVICE", "i", "0"),
        ("PS1='$((WIZARD_CUDA_DEVICE=0))'\nWIZARD_CUDA_DEVICE=1\n", "WIZARD_CUDA_DEVICE", "i", "0"),
        ("POSIXLY_CORRECT=1\nUID=0\nWIZARD_CUDA_DEVICE=1\n", "WIZARD_CUDA_DEVICE", "n", "EXITED"),
        ("UID=0\nWIZARD_CUDA_DEVICE=1\n", "WIZARD_CUDA_DEVICE", "p", "EXITED"),
        ("OWNER=1\nbreak\nWIZARD_CUDA_DEVICE=1\n", "WIZARD_CUDA_DEVICE", "l", "UNSET"),
        ("OWNER=1\ncontinue\nWIZARD_CUDA_DEVICE=1\n", "WIZARD_CUDA_DEVICE", "l", "UNSET"),
        ('eval WIZARD_CUDA_DEV""ICE=0\n', "WIZARD_CUDA_DEVICE", "n", "0"),
        ("K=CUDA_VISIBLE_DEVICES\n" + BEGIN + "\nCUDA_VISIBLE_DEVICES='1'\n" + END + "\nunset $K\n", "CUDA_VISIBLE_DEVICES",
         "n", "UNSET"),
        (")\n" + BEGIN + "\nCUDA_VISIBLE_DEVICES='1'\n" + END + "\n", "CUDA_VISIBLE_DEVICES", "n", "UNSET"),
        ("APOS=it's\n" + BEGIN + "\nCUDA_VISIBLE_DEVICES='1'\n" + END + "\n", "CUDA_VISIBLE_DEVICES", "n", "UNSET"),
    )
    # refused by name without a premise here (a mail check needs new mail at a prompt: measured by the generator's probe;
    # `pkill -P $PPID` -- the named residue -- and `ll`, which only an ALIAS of the sourcing shell could make a command:
    # the file cannot say what a command word names, so no command is plain)
    REFUSED_TOO = ("MAILPATH=/nonexistent\nWIZARD_CUDA_DEVICE=1\n", "PS0=x\nWIZARD_CUDA_DEVICE=1\n",
                   "SHELLOPTS=x\nWIZARD_CUDA_DEVICE=1\n", "WIZARD_CUDA_DEVICE=1\n/bin/true\n", "WIZARD_CUDA_DEVICE=1\nsh -c :\n",
                   "WIZARD_CUDA_DEVICE=1\npkill -P $PPID -x no-such-process\n", "OWNER=1\nll\nWIZARD_CUDA_DEVICE=1\n")

    def setUp(self):
        self.ef = _reader()
        self.tmp = Path(tempfile.mkdtemp(prefix="envshell4_"))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))

    def _file(self, text, name="f"):
        p = self.tmp / name
        p.write_text(text, encoding="utf-8")
        return p

    def _refusals(self, p):
        return " ".join(m for lvl, m in self.ef.check(self.ef.read_file(str(p), BEGIN, END), str(p), "ambient", str(ORGANS))
                        if lvl == "REFUSE")

    def test_every_member_is_refused_in_the_ambient_shape(self):
        for text, key, _mode, bash in self.MEMBERS + tuple((t, "", "", "") for t in self.REFUSED_TOO):
            self.assertTrue(self._refusals(self._file(text)), "%r: not refused (bash reads %r)" % (text, bash))

    def test_every_members_premise_is_bashs_own_reading(self):
        if not shutil.which("bash"):
            self.skipTest("no bash on this host: the premise is re-measured where it exists")
        for text, key, mode, want in self.MEMBERS:
            p = self._file(text, "prem")
            got = _bash_reads(p, self.tmp, mode)
            self.assertEqual(got[0 if key == "WIZARD_CUDA_DEVICE" else 1], want,
                             "premise: bash (%s) reads %s as %r in %r" % (mode, key, got, text))

    def test_a_line_that_runs_no_command_is_still_read_as_systemd_reads_it(self):
        for text in ("OWNER_NOTE='a b'\nWIZARD_CUDA_DEVICE=1\n", "WIZARD_CUDA_DEVICE=1\nOWNER_LIST=(a b)\n",
                     'OWNER_NOTE="it\'s"\nWIZARD_CUDA_DEVICE=1\n', "OWNER_NOTE='a\nb'\nWIZARD_CUDA_DEVICE=1\n",
                     "X='PS4=1'\nWIZARD_CUDA_DEVICE=1\n", BEGIN + "\nCUDA_VISIBLE_DEVICES='1'\n" + END + "\nOWNER=1\n"):
            self.assertEqual(self._refusals(self._file(text)), "", text)

    # EW3-02: the words a refusal gives for a line systemd READS -- quote it, never 'take it out, systemd ignores it'
    READ_BY_SYSTEMD = (("URL=http://h/?a=1&b=2", "URL"), ("OWNER_NOTE=a;b", "OWNER_NOTE"), ("PROXY=http://h:1|x", "PROXY"),
                       ("TAG=a&b", "TAG"), ("OWNER_LIST=(a b\nc)", "OWNER_LIST"), ("APOS=it's", "APOS"),
                       ("SPACEY=a b", "SPACEY"), ("LT=a<b", "LT"))

    def test_a_line_systemd_reads_is_quoted_never_deleted_and_the_quoted_line_reads_the_same(self):
        for line, key in self.READ_BY_SYSTEMD:
            p = self._file(line + "\nWIZARD_CUDA_DEVICE=1\n")
            refusal = self._refusals(p)
            self.assertIn("every unit receives %s from it" % key, refusal, line)
            self.assertIn("keep the value and quote it", refusal, line)
            self.assertNotIn("systemd ignores it", refusal, line)
            before = self.ef.read_file(str(p))["effective"][key][0]
            quoted = ("%s='%s'" % (key, before) if "'" not in before else
                      '%s="%s"' % (key, "".join("\\" + c if c in '\\"$`' else c for c in before)))
            q = self._file(quoted + "\nWIZARD_CUDA_DEVICE=1\n", "quoted")
            self.assertEqual(self._refusals(q), "", "%r: the quoted spelling the words give is refused" % line)
            self.assertEqual(self.ef.read_file(str(q))["effective"][key][0], before, "%r: quoting changed the value" % line)
            sd = _systemd_reads([p, q])
            if sd is not None:
                self.assertEqual(sd[str(q)].get(key), sd[str(p)].get(key), "%r: systemd's own value changed" % line)

    def test_a_marker_swallowed_by_an_owner_value_is_named_first_one_inside_the_old_block_is_the_blocks(self):
        """An OWNER value that runs over a marker line is refused naming ITS line and what to do (end the value before the
        marker) -- before the generic marker message, which named no line (iteration 4, found by replaying the words).
        A marker-shaped line inside a value INSIDE the old block is the block's own: the writer replaces it whole (W1
        (ii-b)), and the check must not refuse it (measured: the first draft of the reorder did, a regression the gate's
        kept W1 section caught)."""
        owner = self._file("MULTI='a\n" + BEGIN + "\nCUDA_VISIBLE_DEVICES='1'\n" + END + "\nWIZARD_CUDA_DEVICE=1\n", "swallow")
        self.assertIn("The line to change is line 1: end its value before that marker line", self._refusals(owner))
        inside = self._file(BEGIN + "\nPATH='/old'\nA='x\n" + END + "\n'\nB=2\n" + END + "\nWIZARD_CUDA_DEVICE=1\nOWN=1\n", "inblock")
        self.assertEqual(self._refusals(inside), "")

    def test_a_name_inside_another_keys_value_is_never_called_the_line_it_sets(self):
        """reflector-4, measured: `BS=a\\` + `UID=0` is ONE value (BS) to systemd AND to a shell (a backslash-newline joins
        the lines); the words said 'it sets UID ... take it out of this file', and followed literally they deleted BS from
        every unit (8 + 6 files of 2,904 refused, two seeds)."""
        refusal = self._refusals(self._file("BS=a\\\nUID=0\nWIZARD_CUDA_DEVICE=1\n", "bs_uid"))
        self.assertNotIn("it sets UID", refusal)
        self.assertIn("every unit receives BS from it", refusal)

    def test_a_value_open_at_the_end_of_the_file_is_ended_never_requoted(self):
        """reflector-4, measured: an owner value whose quote is never closed swallows every later line (a pin line
        included); 'keep the value and quote it' re-quoted the swallowed lines and never converged (a LOOP)."""
        refusal = self._refusals(self._file(BEGIN + "\nCUDA_VISIBLE_DEVICES='1'\n" + END + '\nOPEN2="x\nWIZARD_CUDA_DEVICE=\n',
                                            "open_eof"))
        self.assertIn("is still open at the end of the file", refusal)
        self.assertNotIn("keep the value and quote it", refusal)

    def test_an_overridden_line_is_never_said_to_reach_the_units(self):
        """Iteration 5, EW4-09 (reflector-4): `WIZARD_CUDA_DEVICE=1;` then `WIZARD_CUDA_DEVICE=1` -- the words said 'every
        unit receives WIZARD_CUDA_DEVICE from it ... keep the value and quote it' of line 1, which the units never read
        (last-wins: line 2) -- and quoting kept a value ('1;') the units' resolver rejects. Now: a later line assigns it,
        so take line 1 out -- and followed literally, the file converges with the units' value unchanged."""
        p = self._file("WIZARD_CUDA_DEVICE=1;\nWIZARD_CUDA_DEVICE=1\n", "overridden")
        refusal = self._refusals(p)
        self.assertNotIn("every unit receives WIZARD_CUDA_DEVICE from it", refusal)
        self.assertIn("The line to change is line 1: a later line assigns WIZARD_CUDA_DEVICE again, and every unit reads "
                      "that later line, not this one -- take it out of this file", refusal)
        p.write_text("WIZARD_CUDA_DEVICE=1\n", encoding="utf-8")        # the words, followed
        self.assertEqual(self._refusals(p), "")

    def test_a_value_that_spans_lines_is_named_by_its_whole_span(self):
        """Iteration 5, EW4-09 (reflector-4): the words said 'the line to change is line N' of ONE value spanning lines
        N..M; an owner who changed only line N left the rest (replay variant B lost a unit variable). Now the span."""
        refusal = self._refusals(self._file("BS=a\\\nUID=0\nWIZARD_CUDA_DEVICE=1\n", "span"))
        self.assertIn("The lines to change are lines 1-2 (one value that spans them): every unit receives BS from it",
                      refusal)

    def test_a_variable_the_shell_acts_on_that_swallows_the_next_line_is_named_as_itself(self):
        """Iteration 5 (the generator's own fuzz, gen5.py -- reflection 4's class EW4-02 seen from the other side):
        `UID=0\\` + `WIZARD_DISPLAY_INDEX=0` is ONE value, UID's, to systemd; the words said 'write it as a bare
        WIZARD_DISPLAY_INDEX=<value> line' of line 1, and followed literally they deleted UID without naming it. Now the
        words name what the lines ARE: a variable a shell acts on, spanning both lines -- moved to a drop-in, by name."""
        refusal = self._refusals(self._file("UID=0\\\nWIZARD_DISPLAY_INDEX=0\n", "uid_swallows"))
        self.assertNotIn("Write it as a bare WIZARD_DISPLAY_INDEX", refusal)
        self.assertIn("The lines to change are lines 1-2 (one value that spans them): they set UID, a variable a shell "
                      "itself acts on", refusal)

    def test_a_variable_the_shell_acts_on_is_moved_to_a_drop_in_never_silently_dropped(self):
        refusal = self._refusals(self._file("PS4=x\nWIZARD_CUDA_DEVICE=1\n"))
        self.assertIn("it sets PS4, a variable a shell itself acts on", refusal)
        self.assertIn("give it to that unit in a drop-in", refusal)

    def test_following_every_refusal_literally_never_deletes_a_unit_variable(self):
        """EW3-02 / EW3-04 as an executed property: seeded files of owner lines; while the ambient check refuses, the FIRST
        refusal's words are applied as an owner would (delete the named line, quote its value as the words spell it,
        rewrite it bare, close a quote, move a variable the shell acts on to a unit drop-in); the replay must converge,
        and every key systemd handed the units before must reach them after (in the file, or -- only for a variable the
        shell itself acts on -- in the drop-in the words name). Measured: the iteration-3 words deleted a unit variable
        in 160 of 394 refused files of the reflector's population."""
        import random
        frags = ["OWNER_A=x", "URL=http://h/?a=1&b=2", "LIST=a;b", "PIPE=a|b", "SPACEY=a b", "APOS=it's", "OPEN=(a", "LT=a<b",
                 "echo hi", "set -e", "export OWNER_E=1", "unset OWNER_A", "if true; then :; fi", ")", "PS4=x", "UID=0",
                 "MULTI='a", "b'", "WIZARD_CUDA_DEVICE=1", "WIZARD_CUDA_DEVICE=1 # c", "export WIZARD_CUDA_DEVICE=1",
                 "WIZARD_DISPLAY_INDEX=0", "# a comment", "", "BS=a\\b", 'DQ="a\\"b"', "A=([E]=1)", "test -v 'A[E]'"]
        rnd, bad = random.Random(4404), []
        for _k in range(300):
            lines = [rnd.choice(frags) for _ in range(rnd.randint(1, 6))]
            if rnd.random() < 0.4:
                lines.insert(rnd.randint(0, len(lines)), BEGIN + "\n# a prior block\n" + END)
            text = "\n".join(lines) + "\n"
            lines = text.rstrip("\n").split("\n")
            before = self.ef.parse(text.encode("utf-8"), BEGIN, END)
            if before["error"]:
                continue
            keys0, moved = set(before["effective"]), set()
            for _round in range(10):
                cur = "\n".join(lines) + "\n"
                res = self.ef.parse(cur.encode("utf-8"), BEGIN, END)
                ref = [m for lvl, m in self.ef.check(res, "F", "ambient", str(ORGANS)) if lvl == "REFUSE"]
                if not ref:
                    break
                lines, mv = _apply_words(self.ef, res, lines, ref[0])
                if lines is None:
                    bad.append(("UNPARSED", text, ref[0][-200:]))
                    break
                moved |= mv
            else:
                bad.append(("STUCK", text, ""))
                continue
            if lines is None:
                continue
            after = self.ef.parse(("\n".join(lines) + "\n").encode("utf-8"), BEGIN, END)
            lost = keys0 - set(after["effective"]) - moved
            if lost:
                bad.append(("KEY-LOST %s" % sorted(lost), text, ""))
        self.assertEqual(bad, [], "files whose refusals, followed literally, lose a unit variable or never converge")


def _apply_words(ef, res, lines, msg):
    """One refusal's words, applied as an owner reading only the message would: (new lines, keys moved to a drop-in)."""
    units = {u.line: u for u in res["units"]}

    def extent(n):
        u = units.get(n)
        return (n, u.end_line) if u is not None else (n, n)
    m = re.search(r"Line (\d+) is written correctly -- the line to change is line (\d+), where a shell stops reading", msg)
    if m:
        a, b = extent(int(m.group(2)))
        return lines[:a - 1] + lines[b:], set()
    # the words name ONE value: line N, or lines N-M when it spans them (iteration 5, EW4-09) -- either way the whole value
    m = re.search(r"The lines? to change (?:is line|are lines) (\d+)(?:-\d+ \(one value that spans them\))?: a later "
                  r"line assigns", msg)
    if m:
        a, b = extent(int(m.group(1)))
        return lines[:a - 1] + lines[b:], set()
    m = re.search(r"The lines? to change (?:is line|are lines) (\d+)(?:-\d+ \(one value that spans them\))?: every unit "
                  r"receives ([A-Za-z_0-9, ]+) from it.*? -- keep the value and quote it", msg)
    if m:
        a, b = extent(int(m.group(1)))
        key = m.group(2).split(",")[0].strip()
        one = ef.parse(("\n".join(lines[a - 1:b]) + "\n").encode("utf-8"))
        v = one["effective"].get(key, ("",))[0]
        spelled = ("%s='%s'" % (key, v) if "'" not in v else
                   '%s="%s"' % (key, "".join("\\" + c if c in '\\"$`' else c for c in v)))
        return lines[:a - 1] + spelled.split("\n") + lines[b:], set()
    m = re.search(r"The lines? to change (?:is line|are lines) (\d+)(?:-\d+ \(one value that spans them\))?: (?:it sets|they "
                  r"set) ([A-Z_0-9]+), a variable a shell itself acts on", msg)
    if m:
        a, b = extent(int(m.group(1)))
        return lines[:a - 1] + lines[b:], {m.group(2)}
    m = re.search(r"The line to change is line (\d+): it is written with `export`", msg)
    if m:
        n = int(m.group(1))
        return lines[:n - 1] + [re.sub(r"^[ \t]*export[ \t]+", "", lines[n - 1])] + lines[n:], set()
    m = re.search(r"The line to change is line (\d+): end its value before that marker line", msg)
    if m:
        n = int(m.group(1))
        mk = next((j for j in range(n, len(lines)) if lines[j].strip() in (BEGIN, END)), None)
        q = re.search(r"(['\"])", lines[n - 1])
        if mk is None or q is None:
            return None, set()
        return lines[:mk - 1] + [lines[mk - 1] + q.group(1)] + lines[mk:], set()
    m = re.search(r"The line to change is line (\d+): the value of \w+ that starts there is still open at the end", msg)
    if m:
        n = int(m.group(1))
        t = lines[n - 1]
        if t.endswith("\\"):
            return lines[:n - 1] + [t[:-1]] + lines[n:], set()
        for qc in ("'", '"'):
            if t.count(qc) % 2:
                return lines[:n - 1] + [t + qc] + lines[n:], set()
        return None, set()
    # model-choice Round 1, Wave B (EW5-03/04/05): a value holding a line break, an end marker swallowed inside the old
    # block, an inline comment ending in a backslash -- each: END the value on its own line (the reflector's replay_r5)
    m = (re.search(r"The line to change is line (\d+): the value of \w+ that starts there holds a line break", msg)
         or re.search(r"The line to change is line (\d+): end its value on that line \(remove its trailing backslash", msg)
         or re.search(r"The line to change is line (\d+): it carries an inline '#' comment that ends in a backslash", msg))
    if m:
        n = int(m.group(1))
        t = lines[n - 1]
        if t.endswith("\\"):
            return lines[:n - 1] + [t[:-1]] + lines[n:], set()
        for qc in ("'", '"'):
            if t.count(qc) % 2:
                return lines[:n - 1] + [t + qc] + lines[n:], set()
        return None, set()
    m = re.search(r"line (\d+): the file ends inside a quote or after a backslash", msg)
    if m:
        q = re.search(r"=[ \t]*(['\"])", lines[int(m.group(1)) - 1])
        if lines[-1].endswith("\\"):
            return lines[:-1] + [lines[-1][:-1]], set()
        return (lines[:-1] + [lines[-1] + q.group(1)], set()) if q else (None, set())
    for pat in (r"The line to change is line (\d+), where a shell stops reading the file",
                r"The line to change is line (\d+): it is a shell command that names",
                r"The line to change is line (\d+): take it out of this file"):
        m = re.search(pat, msg)
        if m:
            a, b = extent(int(m.group(1)))
            return lines[:a - 1] + lines[b:], set()
    m = re.search(r"The line to change is line (\d+)\. Write it as a bare ([A-Z_]+)=<(?:value|index)> line", msg)
    if m:
        a, b = extent(int(m.group(1)))
        return lines[:a - 1] + ["%s=1" % m.group(2)] + lines[b:], set()
    return None, set()


class TestTheResidueIsBoundedInTheSameWords(unittest.TestCase):
    """What the shell model cannot see -- the sourcing shell's own state -- is stated ONCE, in the same words, in the
    module's contract comment and in both owner-facing documents (the scope's rule: bounded where it cannot be refused)."""

    SENTENCE = ("what this check cannot see is the state of the shell that sources the file -- a variable attribute it "
                "already set (an integer, a nameref, a read-only name, a local scope when the file is sourced inside a "
                "function), an option (errexit, nounset, xtrace with its own PS4, POSIX mode) or a trap of its own can "
                "change what that shell assigns from a plain KEY=value line. The installer assumes the file is sourced as "
                "the block's own comment says, by a shell with none of these set.")

    def _assert_carries(self, rel):
        want = " ".join(self.SENTENCE.lower().split())
        text = (ROOT / rel).read_text(encoding="utf-8")
        flat = " ".join(re.sub(r"(?m)^\s*#\s?", " ", text).lower().split())
        self.assertIn(want, flat, "%s does not carry the residue sentence verbatim" % rel)

    def test_the_same_sentence_is_in_the_code_and_the_guide(self):
        # both are laid into every brain (root=brain): held from a checkout AND from an installed brain
        for rel in ("install/env_file.py", "docs/ONBOARDING_GUIDE.md"):
            self._assert_carries(rel)

    def _names_every_mode_key(self, rel):
        """Iteration 5, EW4-08 (reflector-4): the documents named 5 of the variables the check refuses as 'a variable the
        shell itself acts on'; every one is DERIVED from install/env_file.py's own list, so the page cannot drift."""
        sys.path.insert(0, str(ROOT / "install"))
        try:
            import env_file as ef
        finally:
            sys.path.remove(str(ROOT / "install"))
        text = (ROOT / rel).read_text(encoding="utf-8")
        missing = [k for k in ef._SHELL_MODE_KEYS if "`%s`" % k not in text]
        self.assertEqual(missing, [], "%s does not name the variable(s) the check refuses: %s" % (rel, missing))

    def test_the_guide_names_every_variable_the_shell_acts_on(self):
        self._names_every_mode_key("docs/ONBOARDING_GUIDE.md")

    def test_the_readme_names_every_variable_the_shell_acts_on(self):
        if not (ROOT / "install.sh").is_file():
            self.skipTest("NOT A CHECKOUT: %s has no install.sh (README.md, like install.sh, is root=null: ships, not "
                          "laid). Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)
        self._names_every_mode_key("README.md")

    def test_the_same_sentence_is_in_the_readme(self):
        # README.md is root=null, like install.sh: it ships and is not laid, so an installed brain has none (measured:
        # iteration 4's first version of this test read it unconditionally and FAILED from an installed brain)
        if not (ROOT / "install.sh").is_file():
            self.skipTest("NOT A CHECKOUT: %s has no install.sh (README.md, like install.sh, is root=null: ships, not "
                          "laid). Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)
        self._assert_carries("README.md")


class TestTheUnitsFallbackReaderIsSystemds(_Corpus):
    """organs/voidloom_device.py _read_env_file_once -- the units' OWN layer-3 reader (a unit whose environment lacks the
    pin reads the declaring file itself) -- reads the file as systemd hands it to that unit (iteration 4: it stripped
    `export ` and read `export WIZARD_CUDA_DEVICE=1` as 1, which systemd drops). Held on the corpus and on the pin
    shapes that diverge, against systemd's OWN loader where the host has it (else the reader it is bound to)."""

    PIN_CASES = ((b"export WIZARD_CUDA_DEVICE=1\n", None), (b"WIZARD_CUDA_DEVICE=1\n", "1"),
                 (b'WIZARD_CUDA_DEVICE="1" # c\n', None), (b"WIZARD_CUDA_DEVICE=1 # c\n", "1"),
                 (b"WIZARD_CUDA_DEVICE=1\nexport WIZARD_CUDA_DEVICE=0\n", "1"), (b"WIZARD_CUDA_DEVICE='1\n'\n", "1"),
                 (b"WIZARD_CUDA_DEVICE=\\\n1\n", "1"), (b"WIZARD_CUDA_DEVICE=1\r\n", "1"),
                 (b"\xef\xbb\xbfWIZARD_CUDA_DEVICE=1\n", None), (b"WIZARD_CUDA_DEVICE = 1\n", "1"),
                 (b"A=\xff\nWIZARD_CUDA_DEVICE=1\n", None), (b"# \xff\nWIZARD_CUDA_DEVICE=1\n", "1"))

    def _fallback(self, path):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import voidloom_device as d; from pathlib import Path; "
                "v, h = d._read_env_file_once(Path(sys.argv[2])); print('%s|%s' % (v, h))")
        p = subprocess.run([sys.executable, "-B", "-c", code, str(ORGANS), str(path)], capture_output=True, text=True,
                           timeout=60, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        v = p.stdout.strip().split("|")[0]
        return None if v == "None" else v

    def test_the_fallback_reads_what_systemd_hands_the_units(self):
        paths, want = [], {}
        for i, (data, golden) in enumerate(self.PIN_CASES):
            p = self.tmp / ("pin%d.env" % i)
            p.write_bytes(data)
            paths.append(p)
            want[str(p)] = golden
        paths += list(self.files.values())
        sd = _systemd_reads(paths)
        for p in paths:
            if sd is not None:
                env = sd[str(p)]
                raw = None if env == REJECT else env.get("WIZARD_CUDA_DEVICE")
            else:
                res = self.ef.read_file(str(p))
                raw = None if res["error"] else (res["effective"].get("WIZARD_CUDA_DEVICE") or (None,))[0]
            units = None if raw is None else self.ef.units_pin("WIZARD_CUDA_DEVICE", raw, str(ORGANS))
            self.assertEqual(self._fallback(p), units, "%s: the units' fallback reader and systemd disagree" % p.name)
            if str(p) in want:
                self.assertEqual(units, want[str(p)], "%s: the golden reading" % p.name)


class TestDoubleQuotesReadAlike(unittest.TestCase):
    """The spelling the EW3-02 words and the writer give a value that holds a ': double quotes with a backslash before
    each \\ " $ ` -- read ALIKE by systemd (its DQ escape rule) and by bash (its double-quote rule); held on bash and
    on systemd's own loader where the host has them."""

    VALUES = ("it's", "a'b\"c$d`e\\f", "x\\qy", "O'Brien Q", "$HOME", "`date`", "\\")

    def test_the_writer_spelling_is_safe_and_reads_the_value_back(self):
        ef = _reader()
        tmp = Path(tempfile.mkdtemp(prefix="envdq_"))
        self.addCleanup(lambda: shutil.rmtree(str(tmp), ignore_errors=True))
        paths = []
        for i, v in enumerate(self.VALUES):
            line = ef._assignment_for("OWNER_V", v)
            self.assertIsNotNone(line, v)
            res = ef.parse(line)
            self.assertEqual([u.cls for u in res["units"]], ["safe"], (v, line))
            self.assertEqual(res["effective"]["OWNER_V"][0], v, (v, line))
            p = tmp / ("v%d" % i)
            p.write_bytes(line)
            paths.append((p, v))
        sd = _systemd_reads([p for p, _v in paths])
        for p, v in paths:
            if sd is not None:
                self.assertEqual(sd[str(p)].get("OWNER_V"), v, "systemd's own loader: %r" % v)
            if shutil.which("bash"):
                b = subprocess.run(["bash", "-c", '. "$1"; printf "<%s>" "$OWNER_V"', "_", str(p)], capture_output=True,
                                   text=True, timeout=60, env={"PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL)
                self.assertEqual(b.stdout, "<%s>" % v, "bash: %r" % v)


class TestTheWindowsWriterIsTheReadersToo(unittest.TestCase):
    """ENV-FILE WRITER PASS, iteration 4 (carried item: install/install.ps1 STEP F.3, the Windows env writer). The one
    reader of the env file on Windows is this package's install/env_file.py (systemd's rules: token_doctor reads the file
    through it, and organs/voidloom_device.py by the same rules). Measured on 524c63e's writer, EXECUTED by Windows
    PowerShell 5.1 on an extracted copy: `Set-Content -Encoding UTF8` wrote a byte-order mark and CRLF line ends and wrote
    Windows paths unquoted, so the reader read WIZARD_LIBRARY_DB=D:\\brain\\... as 'D:brain...' (an unquoted backslash
    keeps only the next character) and flagged every line; and its `Test-Path $EnvFile` is a WILDCARD test -- under a
    directory named with '[' it answered 'absent' for an EXISTING owner file and went on to write (saved only by
    Set-Content failing on the same wildcard path; with no file there it created none, silently). Now one function
    between two marker lines writes it: spelled for the reader, no BOM, LF, create-only twice over, read back."""

    PS1 = ROOT / "install" / "install.ps1"
    BEGIN_W, END_W = "# >>> env-writer", "# <<< env-writer <<<"

    def _text(self):
        if not self.PS1.is_file():
            self.skipTest("install/install.ps1 is not here (it is laid on Windows only): UNMEASURABLE on this tree")
        return self.PS1.read_bytes().decode("utf-8-sig").split("\n")

    def _between(self, lines, a, b, inclusive):
        s = [i for i, l in enumerate(lines) if l.lstrip().startswith(a)]
        e = [i for i, l in enumerate(lines) if l.lstrip().startswith(b)]
        if not s or not e:
            return None
        return "\n".join(lines[s[0]:(e[0] + 1 if inclusive else e[0])])

    def test_the_windows_writer_is_one_function_spelled_encoded_and_read_back_for_the_reader(self):
        lines = self._text()
        func = self._between(lines, self.BEGIN_W, self.END_W, True)
        self.assertIsNotNone(func, "install.ps1 has no env-writer function between the marker lines")
        self.assertEqual(sum(1 for l in lines if l.startswith(self.BEGIN_W)), 1)
        f3 = self._between(lines, "# F.3", "# F.4", False)
        self.assertIsNotNone(f3, "install.ps1 has no STEP F.3 block")
        self.assertIn("Write-VoidloomEnvFile", f3, "STEP F.3 does not call the one writer")
        self.assertNotIn("Set-Content", f3, "STEP F.3 writes the env file with Set-Content (a BOM and CRLF in PS 5.1)")
        for must in ("UTF8Encoding($false)", '-join "`n"', "[System.IO.FileMode]::CreateNew", "Test-Path -LiteralPath $Path",
                     "ReadAllBytes", "import env_file"):
            self.assertIn(must, func, "the env writer lost %r" % must)
        self.assertTrue(all(ord(c) < 128 for c in func), "the env-writer block must stay ASCII (PS 5.1 reads a script by its BOM)")

    def test_the_windows_writer_executed_writes_what_the_reader_reads(self):
        """EXECUTED where Windows PowerShell is reachable (WSL): the extracted F.3 block (and the function it calls) is
        run by powershell.exe from a scratch dir under VOIDLOOM_SCRATCH, through its \\\\wsl.localhost path -- never
        install.ps1 itself. Elsewhere: SKIP with the reason (UNMEASURABLE, never PASS)."""
        lines = self._text()
        ps = shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
        if not os.path.isfile(ps) or not shutil.which("wslpath"):
            self.skipTest("no Windows PowerShell reachable from here (not WSL): the executed half is UNMEASURABLE")
        func = self._between(lines, self.BEGIN_W, self.END_W, True) or ""
        f3 = self._between(lines, "# F.3", "# F.4", False)
        self.assertIsNotNone(f3, "install.ps1 has no STEP F.3 block")
        scratch = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
        scratch.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="ps1writer_", dir=str(scratch)))
        self.addCleanup(lambda: shutil.rmtree(str(tmp), ignore_errors=True))
        (tmp / "pkg" / "install").mkdir(parents=True)
        shutil.copy2(str(READER), str(tmp / "pkg" / "install" / "env_file.py"))
        w = subprocess.run(["wslpath", "-w", str(tmp)], capture_output=True, text=True).stdout.strip()
        if not w:
            self.skipTest("wslpath gave no Windows path for the scratch dir: UNMEASURABLE")
        q = lambda s: "'" + s.replace("'", "''") + "'"
        owner = b"OWNER_LINE=keep\n# an owner comment\n"
        (tmp / "D[1]").mkdir()
        (tmp / "D[1]" / "wizard-library-env").write_bytes(owner)
        cases = {"A": "D:\\Profiles\\O'Brien Q\\.wizard-library", "B": "D:\\Profiles\\Ay\u015fe\\.wizard-library",
                 "D": "D:\\Profiles\\plain\\.wizard-library"}
        drv = ["$ErrorActionPreference = 'Continue'", "function Say ($m) { Write-Host \"[install] $m\" }",
               "function Warn ($m) { Write-Warning \"[install] $m\" }",
               "$PySys = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'py -3' }",
               "$PkgRoot = " + q(w + "\\pkg"), "$Tier1Ok = $true",
               # F.3 names the installer's tier by $EmbedTier (iteration 4 of the tier1-upgrade loop): the ONE line
               # that chooses it, taken from install.ps1 itself, never re-typed here
               *[l for l in lines if l.startswith("$EmbedTier =")],
               func, "$F3 = {", f3, "}"]
        for c, brain in cases.items():
            drv += ["$BrainDir = " + q(brain), "$DbPath = Join-Path $BrainDir 'claude_memory.db'",
                    "$EnvFile = " + q(w + ("\\D[1]" if c == "D" else "\\" + c + " dir") + "\\wizard-library-env"), ". $F3"]
        (tmp / "run.ps1").write_bytes(b"\xef\xbb\xbf" + "\n".join(drv).encode("utf-8"))
        p = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", w + "\\run.ps1"],
                           capture_output=True, timeout=300, stdin=subprocess.DEVNULL)
        said = (p.stdout + p.stderr).decode("utf-8", "replace")
        ef = _reader()
        for c, brain in cases.items():
            f = tmp / ("D[1]" if c == "D" else c + " dir") / "wizard-library-env"
            self.assertTrue(f.is_file(), "case %s: no env file was written (%s)" % (c, said[-800:]))
            b = f.read_bytes()
            if c == "D":
                self.assertEqual(b, owner, "case D: the owner's EXISTING file was changed (create-only)")
                continue
            self.assertFalse(b.startswith(b"\xef\xbb\xbf"), "case %s: a byte-order mark (systemd's rules drop the first key)" % c)
            self.assertNotIn(b"\r", b, "case %s: CR line ends" % c)
            r = ef.parse(b)
            self.assertIsNone(r["error"])
            got = dict((k, v) for k, (v, _l) in r["effective"].items())
            self.assertEqual(got, {"WIZARD_LIBRARY_DB": brain + "\\claude_memory.db", "WIZARD_LIBRARY_HOME": brain,
                                   "WIZARD_EMBED_MODEL": "nomic"}, "case %s: the reader misreads the written file" % c)
            self.assertEqual([u.line for u in r["units"] if u.cls != "safe"], [], "case %s: a line systemd and a shell read apart" % c)


if __name__ == "__main__":
    unittest.main()
