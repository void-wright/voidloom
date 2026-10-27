"""voidloom — the LLM's execution surface over the Voidloom memory brain.

A thin CLI *facade* over the canonical brain engine ``claude_memory.py``. This
package is the L5-X "Adaptive Loom" execution surface: the toolkit a fresh LLM
discovers (via ``voidloom agent-context``) and uses to recall, store, load
session context, and shepherd the unified approval queue.

Design spine (council-ruled — see plan memories #6042, #6044, #6047, #6105):

  * **Facade only.** ``claude_memory.py`` remains the one canonical engine. This
    package IMPORTS it and calls its ``cmd_*`` entry points — it never shells out
    and never re-implements engine logic. Deleting the ``voidloom/`` package
    leaves the engine byte-for-byte untouched (clean rollback).
  * **Lazy heavy imports.** The bootstrap path (``bootstrap``/``agent-context``/
    ``doctor`` happy path) is stdlib-pure: no ``torch``/``jina``/embedding import.
    Heavy modules load ONLY inside the verbs that need them. This is what lets
    *install* (lay the package down) differ from *onboard* (pull GPU models).
  * **Ungated locally because gated on the local filesystem** (locality-as-auth,
    #6105). The local-FS guard IS the auth model — core behaviour, not deferred.
  * **Structured ``--json`` envelopes** on every verb: a ``status`` enum
    (``ok``|``error``|``refused``), a machine-readable ``code``, and ``data``.
    Callers assert on envelope keys, never on substring error text.

v0.1 verbs: bootstrap, recall, store, context, approvals, agent-context, doctor.
"""

__version__ = "1.0.0"

# NOTE: keep this module stdlib-pure. Importing ``voidloom`` must NEVER drag in
# torch / sentence_transformers / claude_memory. Submodules are imported lazily
# by ``cli.py`` so that ``voidloom bootstrap`` stays a pure-stdlib code path.

__all__ = ["__version__"]
