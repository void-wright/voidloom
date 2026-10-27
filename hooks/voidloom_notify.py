#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voidloom_notify.py — Notification hook (best-effort desktop notify-send).

Fires a desktop notification when a NEW self-eval / approval lands, i.e. when
the pending-count (the stable Loop C contract) has RISEN since the last time we
notified. This makes the brain able to tap Drew on the shoulder when the
monthly self-eval cron produces fresh proposals — without him having to start a
session to find out.

Design notes:
  - CHEAP only: one covered-index COUNT (no model, no network).
  - DEBOUNCED, on the ATTENTION SET rather than a count. `decision_units.
    attention_signal` fires on a SET DIFFERENCE over open unit ids, so it sees a
    swap that a cardinality delta cannot; and after notifying we ACKNOWLEDGE the
    units we just told Drew about, which is what makes the next invocation
    silent. Telling him IS the seeing. The legacy count state file is still
    written so the fallback path stays correct on an un-migrated brain.
  - BEST-EFFORT: notify-send may be absent (headless WSL, no DBus). We try a few
    transports and silently no-op if none work. The hook ALWAYS exits 0.
  - Claude Code may invoke Notification frequently; the debounce makes repeat
    invocations cheap and silent once Drew has been told.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = os.environ.get("WIZARD_LIBRARY_HOME") or str(Path(__file__).resolve().parent.parent)
# The engine + organ modules moved into organs/ on 2026-09-07. HOME stays the
# tree root (the DB, the manifest, the CLI shim all live there); ORGANS is the
# import path. Probed, so a flat legacy tree still resolves.
ORGANS = HOME if not os.path.isdir(os.path.join(HOME, "organs")) else os.path.join(HOME, "organs")
DB = os.environ.get("WIZARD_LIBRARY_DB") or str(Path(HOME) / "claude_memory.db")
STATE = os.environ.get("VOIDLOOM_NOTIFY_STATE") or str(Path(HOME) / ".voidloom_notify_state.json")


def _sanitize(text: object) -> str:
    """Strip terminal control sequences from memory-derived text (defense in
    depth). Reuses the shared void_render primitive when importable; degrades to
    a no-op coercion if the module is unavailable (notify is never load-bearing).
    The injection-proofing of the toast command does NOT depend on this — see
    ``_send_desktop`` — but a notification body should still never carry raw
    OSC/CSI escapes into whatever surface renders it.
    """
    try:
        sys.path.insert(0, ORGANS)
        import void_render as _vr
        return _vr.sanitize_for_terminal(text)
    except Exception:
        if text is None:
            return ""
        try:
            return str(text)
        except Exception:
            return ""

TITLE = "Voidloom"


def _read_last_notified() -> int:
    try:
        d = json.loads(Path(STATE).read_text(encoding="utf-8"))
        return int(d.get("last_notified", 0))
    except Exception:
        return 0


def _write_last_notified(n: int) -> None:
    try:
        Path(STATE).write_text(json.dumps({"last_notified": int(n)}),
                               encoding="utf-8")
    except Exception:
        pass


def _build_encoded_command(title: str, body: str) -> str:
    """Return the base64 blob for ``powershell.exe -EncodedCommand``.

    SECURITY (C1, VULN #6104.2): there is ZERO string-interpolation of attacker-
    influenced ``title``/``body`` into a command line. Two independent layers:

      1. INNER — ``title`` and ``body`` are carried as base64 *data* literals and
         recovered inside PowerShell via ``[Convert]::FromBase64String`` +
         UTF-8 decode into ``$t``/``$b`` variables. The base64 alphabet
         (``[A-Za-z0-9+/=]``) contains no PowerShell metacharacter, so the
         payload can never form a command, expression, or sub-expression — no
         backtick, no ``$( )``, no ``;``, no quote-break is even representable.
      2. OUTER — the whole script is UTF-16LE-encoded then base64-encoded and
         handed to ``-EncodedCommand``, so no script text touches the shell
         command line at all (no further quoting/escaping surface).

    A ``body`` of ``'; calc; #`` therefore yields only the literal text in the
    Write-Output; ``calc`` is never executed.
    """
    # base64 the UTF-8 text payload (data, not code).
    t_b64 = base64.b64encode(title.encode("utf-8")).decode("ascii")
    b_b64 = base64.b64encode(body.encode("utf-8")).decode("ascii")
    # Script references the payload ONLY through base64 decode → no interpolation.
    script = (
        "[void][Windows.UI.Notifications.ToastNotificationManager];"
        f"$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{t_b64}'));"
        f"$b=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b_b64}'));"
        "Write-Output (\"{0}: {1}\" -f $t,$b)"
    )
    # -EncodedCommand expects base64 of the UTF-16LE script bytes.
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _send_desktop(title: str, body: str) -> bool:
    """Try notify-send, then a couple of fallbacks. Return True if one SUCCEEDED.

    All transports are best-effort: a missing binary, a raise, or a NON-ZERO
    EXIT moves to the next, and a total failure returns False (no exception).
    Memory-derived text is sanitized of terminal control sequences first, and
    the Windows-toast path is fully injection-proof (see ``_build_encoded_command``).

    THE CLASS THIS RETURN VALUE BELONGS TO -- "a state written on the strength
    of an action whose success was not observed". Until 2026-09-02 this returned
    True whenever ``subprocess.run`` merely did not RAISE, so a notify-send that
    exited 1 (no DBus session, the documented headless-WSL case) read as
    delivered. The caller's acknowledgement then marked units seen on the
    strength of it. Fixing the caller without fixing this would have moved the
    lie one frame down, so the exit status is now checked here: ``returncode ==
    0`` is the row that would look different if "delivered" were false, and the
    process that writes it is the transport, not this function.
    """
    title = _sanitize(title)
    body = _sanitize(body)
    # 1. notify-send (libnotify) — the common Linux path. argv form already
    #    prevents shell interpretation; sanitize strips OSC/CSI for the renderer.
    exe = shutil.which("notify-send")
    if exe:
        try:
            cp = subprocess.run([exe, "--app-name", title, title, body],
                                timeout=5, capture_output=True)
            if cp.returncode == 0:
                return True
            print(f"[voidloom_notify] notify-send exited {cp.returncode}; "
                  f"trying the next transport", file=sys.stderr)
        except Exception as e:
            print(f"[voidloom_notify] notify-send transport failed: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    # 2. WSL -> Windows toast via powershell.exe (best-effort; many WSL setups).
    #    -EncodedCommand carries the script with NO interpolation of title/body
    #    into any command line (C1 seal). -NonInteractive so a cron/hook never
    #    blocks on a prompt.
    ps = shutil.which("powershell.exe")
    if ps:
        try:
            encoded = _build_encoded_command(title, body)
            cp = subprocess.run(
                [ps, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                timeout=8, capture_output=True)
            if cp.returncode == 0:
                return True
            print(f"[voidloom_notify] powershell.exe toast exited "
                  f"{cp.returncode}", file=sys.stderr)
        except Exception as e:
            print(f"[voidloom_notify] powershell toast transport failed: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
    return False


def _all_dark(unread, breakdown=None) -> bool:
    """True only when EVERY source went dark. R7/C2 -- the one state that earns
    silence; anything less is a lower bound and a lower bound above zero is a
    reason to speak."""
    # R7/C5. No literal key set: without a breakdown we cannot prove
    # every source went dark, and the doubt resolves toward speaking.
    _un = set(str(x) for x in (unread or ()))
    _keys = set(breakdown or ())
    return bool(_keys) and _un >= _keys


def main() -> int:
    # Drain stdin (Claude Code sends JSON; we don't require it here).
    try:
        sys.stdin.read()
    except Exception:
        pass

    try:
        sys.path.insert(0, ORGANS)
        import voidloom_banner

        # R12 — the debounce is now a SET DIFFERENCE over open unit ids, not a
        # comparison of counts. `n > last` cannot see a swap, and it cannot see
        # the case that matters most here: one unit closing while another opens
        # keeps the count flat, so the notification never fires.
        #
        # R13 — and the quiet is per unit, so a unit that arrived after a snooze
        # is never hidden by it. The live global snooze runs to 2026-10-01; had
        # this hook obeyed it, the 2026-09-01 envelope-bug failure would have
        # been silent for a month.
        fire = None
        sig = None
        _DU = None
        # T3 -- THE COUNT NOW CARRIES WHETHER IT IS KNOWN. `read_pending_count`
        # returns a bare int, so "there is nothing pending" and "I could not tell"
        # arrived here as the same 0 -- and the `elif n != last` branch below then
        # WROTE that 0 into `.voidloom_notify_state.json`. Measured 2026-09-03: one
        # invocation against a tree whose banner could not reach the ledger
        # rewrote the state file 1 -> 0. `git checkout` restores code, not data, so
        # that write is not reversible by the mechanism this tier's premise rests
        # on. `trusted` is what lets the write sites refuse.
        trusted = True
        try:
            n, trusted = voidloom_banner.read_pending_count_ex(DB)
        except AttributeError:
            # An older voidloom_banner without the two-value reader. Keep working
            # on the historical single-value call rather than refusing to run --
            # but say so, because `trusted` is then an assumption, not a reading.
            n = voidloom_banner.read_pending_count(DB)
            print("[voidloom_notify] voidloom_banner has no read_pending_count_ex; "
                  "the count's trustworthiness is ASSUMED, not read", file=sys.stderr)
        # R7/C2 + C3 -- TWO DEFECTS IN THREE LINES, AND THIS FILE HAS NEVER
        # BEEN ON A SWEEP LIST.
        #
        # (1) SCOPE. `trusted` above comes from `read_pending_count_ex`, which
        #     counts SELF-EVAL AND NOTHING ELSE, and it is then used to gate
        #     durable writes of `n` -- which the next line overwrites with the
        #     THREE-SOURCE `sig.count`. An arity-1 bit gating a three-source
        #     number is the same scope error `architecture-005` names, and it
        #     is why five form-based sweeps kept finding a sixth member.
        # (2) THE BIT IS DROPPED ENTIRELY. `fire, n = sig.fire, sig.count`
        #     takes the two fields it wants off a trust-carrying object and
        #     leaves `trusted`/`unread` behind. That is not mis-reading the
        #     bit; it is never asking. The desktop toast is then gated on
        #     `fire` alone -- so on the C3 shape (count 5, every counted item
        #     keyless, fire False) three surfaces said 5 and this one said
        #     nothing at all.
        _unread = ()
        try:
            import decision_units as _DU
            sig = _DU.attention_signal(DB)
            fire, n = sig.fire, sig.count
            _unread = tuple(str(x) for x in (getattr(sig, "unread", ()) or ()))
            _sig_tr = getattr(sig, "trusted", None)
            if _sig_tr is not None:
                # THE THREE-SOURCE BIT REPLACES THE ONE-SOURCE BIT, because `n`
                # is now the three-source count. A partial read is NOT untrusted
                # for the purpose of speaking: it is a lower bound, and the
                # sources that answered answered correctly.
                trusted = bool(_sig_tr) or bool(
                    _unread and not _all_dark(_unread, getattr(sig, "breakdown", None)))
        except Exception as e:
            # RECORDED, not swallowed: this is the difference between "nothing to
            # notify" and "the signal could not be computed".
            print(f"[voidloom_notify] attention signal unavailable, falling back "
                  f"to the count debounce: {type(e).__name__}: {e}", file=sys.stderr)

        last = _read_last_notified()
        if fire is None:
            fire = (n > last and n > 0)
        if _unread and not _all_dark(_unread):
            # A PARTIAL COUNT THAT IS GREATER THAN ZERO IS A REASON TO SPEAK.
            # The sources that answered answered correctly, so a positive lower
            # bound is a real pending item; staying silent because a DIFFERENT
            # source went dark is the wholesale refusal this criterion forbids.
            fire = bool(fire) or n > 0

        if fire:
            body = voidloom_banner.build_notification_text(DB) or (
                f"{n} proposal set(s) await your review — run: voidloom approvals --tui"
            )
            delivered = _send_desktop(TITLE, body)
            # THE DEBOUNCE. `attention_signal` fires while any open unit is
            # UNACKNOWLEDGED, and until 2026-09-02 NOTHING on any shipped surface
            # called acknowledge() -- the TUI that would is G4 and out of scope --
            # so the unseen set never shrank. Measured: three consecutive hook
            # invocations gave fire=True, True, True where the legacy `n > last`
            # gave True, False, False. Claude Code invokes the Notification hook
            # frequently (see the module docstring), so that shipped one desktop
            # toast PER INVOCATION, indefinitely: R14 broken in the direction of
            # MORE noise, which is the exact failure the attention model exists to
            # prevent.
            #
            # TELLING DREW *IS* THE SEEING. This is the acknowledgement, and it is
            # per-unit (R14), never global: only the keys that actually fired are
            # marked, so a unit that arrives afterwards is untouched and still
            # fires (R13). It writes unit_attention and nothing else, so the
            # decision record is byte-identical across it.
            if not delivered:
                # A TOAST THAT WAS NEVER DELIVERED IS NOT DREW SEEING IT. Every
                # transport can fail on a headless WSL box (this module's own
                # docstring says so), and acknowledging anyway marks the fired
                # units seen and takes the session banner dark with the rest
                # still open -- measured 2026-09-02: ONE invocation with every
                # transport failing acknowledged 5 units and build_banner()
                # then returned '' with 9 open. That is writing the state we
                # wanted instead of deriving it from evidence. The legacy count
                # file is left alone for the same reason: writing it would make
                # the fallback debounce ("n > last") suppress the retry too.
                print(f"[voidloom_notify] no desktop transport succeeded; NOT "
                      f"acknowledging "
                      f"{len(sig.fired_keys) if sig is not None else 0} unit(s) "
                      f"and NOT advancing the count file -- the next invocation "
                      f"will try again", file=sys.stderr)
            elif _unread and not _all_dark(_unread):
                # Delivered on a LOWER BOUND. The number is real as far as it
                # goes and is NOT written: `last_seen`/`last_notified` debounce
                # future invocations, and a number short by an unread source
                # would suppress the ping that arrives when the source returns.
                # The decision is published; only the WRITE is withheld, and
                # the reason is said rather than left to be inferred.
                print(f"[voidloom_notify] delivered a PARTIAL count "
                      f"({', '.join(_unread)} UNREAD; at least {n}); NOT "
                      f"writing a lower bound to the state file",
                      file=sys.stderr)
            elif not trusted:
                # Delivered, but the number we would record is not a reading. The
                # toast went out on the ledger's `fire`; writing an untrusted
                # count into the durable state file would then debounce future
                # invocations against a number nobody computed.
                print(f"[voidloom_notify] delivered, but the pending count is "
                      f"UNKNOWN (not derivable); NOT writing it to the state "
                      f"file", file=sys.stderr)
            else:
                # Delivered. Only NOW does any "Drew has been told" state get
                # written -- the legacy count file first, so the fallback
                # debounce stays correct if the ledger later goes away.
                _write_last_notified(n)
            if delivered and sig is not None and _DU is not None and sig.fired_keys:
                try:
                    _DU.acknowledge(list(sig.fired_keys), DB)
                except Exception as e:
                    # RECORDED, never swallowed (R17): if the acknowledgement
                    # fails, the next invocation will notify again, and the reason
                    # why must be visible rather than inferred from the noise.
                    print(f"[voidloom_notify] notified but could NOT acknowledge "
                          f"{len(sig.fired_keys)} unit(s); the next invocation will "
                          f"notify again: {type(e).__name__}: {e}", file=sys.stderr)
        elif n != last and trusted:
            # Sync the legacy state file down without notifying, so the old
            # fallback path stays correct if the ledger is ever unavailable.
            #
            # T3 -- `and trusted`. THIS LINE IS WHERE THE DATA WAS LOST. With the
            # ledger absent the count read 0, `0 != 1` was true, and the file was
            # rewritten to 0 with no notification, no message, and no way back:
            # the state file is DATA and is not in git. A count that could not be
            # derived may not be written into durable state -- it is the same rule
            # `_send_desktop`'s caller already applies to "delivered".
            _write_last_notified(n)
        elif n != last and not trusted:
            print(f"[voidloom_notify] the pending count is UNKNOWN (not "
                  f"derivable); leaving .voidloom_notify_state.json at "
                  f"last_notified={last} rather than writing an unread {n}",
                  file=sys.stderr)
    except Exception as e:
        # Notifications are never load-bearing, so the outer guard stays. What
        # changes is that it no longer DISCARDS the reason -- R17's spirit on a
        # pre-existing path: degrade, but say why.
        print(f"[voidloom_notify] degraded: {type(e).__name__}: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
