# THE GATE
### the single definition. Nothing else defines it; everything else references it by name.

> **Why this file exists.** Between 2026-05-11 and 2026-09-15 this brain ruled the graduation
> gate wrong on **eight separate dates, in both directions**, and the bar string never once
> changed — because the gate's *definition* and its *invocation* were the same text, copied
> across 68 files. There was no single place a ruling could land, so rulings evaporated.
> This file is that place. **Change the gate here and nowhere else.**

**Version:** `gate-v1` · adopted 2026-09-15 · supersedes the gate wording that was
previously repeated inline across many files.

---

## 1. WHAT THE GATE ACTUALLY IS

The operative clause has always been this one, and it is a **predicate**, not a score:

> **Any open Critical or High blocks PRODUCTION-CLEAR regardless of composite.**

That clause was already binding. Its defect was that **"Critical" was defined nowhere** — three
agents given the same finding set counted Criticals as 3/18, 6/20 and 8/21, which is the correct
behaviour of a competent agent facing an undefined predicate. §2 is the definition.

**The composite is not the gate.** It is a number reported beside the gate. It may rank drafts
and it may direct attention. **It may not decide.** *(Measured: the composite is the unweighted
arithmetic mean of seat composites — the one aggregator that cannot express a floor, because a
mean under-penalises a large shortfall in one criterion. A scalar CAN block if the operator is
chosen for it; ours was not.)*

---

## 2. THE SEVERITY VOCABULARY — this is the definition of Critical and High

A finding is **Critical or High iff it is a member of one of these four classes.** Membership is
decided by **an observation the reviewer actually performed**, never by a judgement of severity.
Everything else is Medium or Low and goes in the ledger, not the floor.

| class | the product… | the observation that settles it |
|---|---|---|
| **DESTROYS** | loses or corrupts state the verb did not name | run it twice in a scratch HOME; diff the world. `find $H -newer $STAMP` + `git status --porcelain` after every `--dry-run` |
| **LIES** | asserts something its own evidence, in reach, contradicts | collide the self-report against the record it claims to summarise (`doctor --json` vs the heartbeat file it reads) |
| **DOESN'T DELIVER** | a promised capability is unreachable on a **supported** configuration | run the promise on the supported matrix |
| **UNBOUND** | a rule exists, reads correctly, and **nothing executes it** | AST census: rule-shaped functions with no caller outside their own definition |

**The admission rule.** A class member that cannot be settled by an observation the reviewer can
*perform* does not belong in the floor — it belongs in the ledger. **Binarity buys nothing; an
observable referent does.** *(Measured: agreement runs ~91% on deterministic structural criteria
and ~41% on context-dependent ones; a binary verdict against an observable held-out referent
reaches κ 0.87 across model families, while binary criteria whose referent must be assessed
rather than inspected still disagree ~21%.)*

**Severity is three fields, never collapsed:** `class` · `blast radius` · `reproduced?`. Do not
average them and do not reduce them to one word.

**What is NOT in the floor, on purpose:**
- *Correct but unpleasant.* A real loss, stated: the composite caught "DX 7.0" as a number; this
  does not. It surfaces only via the ledger and the dissent channel.
- *UNAUTHORISED* (behaviour no requirement explains — DO-178C's extraneous-code finding). Cut for
  `gate-v1`: there is no requirements baseline to diff against, and 0 of 80 findings in the last
  run were of this class. **Re-add when a requirements baseline exists.**

---

## 3. WHO NAMES AND WHO CLOSES — the wall

**A reviewer that can close its own finding is not a reviewer, it is a negotiator.**

*(Measured, and this is why the wall exists: an LLM panel's worst class is the one a gate
consumes. On a checklist audit of real repositories, inter-model Fleiss κ was 0.28, best-model
accuracy 0.485, and the **YES class — "this complies" — had precision 0.167.** Five of six
compliance passes were wrong. The same panel agrees at 91% on structural observations. **The
panel is good at seeing and bad at forgiving.**)*

**The reviewer emits exactly one state: `OPEN`** — carrying its class, the observation it
performed, and the evidence cited. Plus the one closure that is also a naming act: **`WITHDRAWN`**,
un-naming its own finding.

**Closure comes from two other places, and neither is a reviewer's opinion:**

| state | who | what it requires |
|---|---|---|
| **ENACTED** | anyone | **the observation was re-run and no longer fires.** Not a claim that it was fixed — the check itself is the evidence. Cite the command and its output. |
| **DECLINED** | **the creditor** | a signature from the party the finding would hurt, shown what they are accepting |
| **DEFERRED** | **the creditor** | a signature **plus a named binary close-condition on a future round** — a condition someone can execute, never a date |
| **REFUSED** | the owner | the observation cannot be performed and never will be; nothing further for anyone to decide. Carries the reason. |

**DESTROYS IS UNWAIVABLE.** There is no DECLINED or DEFERRED for it. The state it destroys is not
the signer's to give away. The other three classes each have a creditor — **LIES** is owed to the
decider, **DOESN'T DELIVER** to the promisee, **UNBOUND** to the owner of the rule — and only that
creditor may waive.

**Applicability is an INTAKE fact, not a reviewer's disposition.** Before the round opens, the
subject's owner records which classes apply and why ("this artifact has no write path; DESTROYS
does not apply"). **The reviewer never says "not applicable."** *(Measured: where reviewers hold
the applicability judgement, 43% of evaluations went N/A and those rows left the denominator —
the floor's denominator was then set by the single most contested judgement in the instrument.)*

---

## 4. THE LEDGER — what makes the round terminate

**Every finding carries exactly one state, and the states must SUM to the finding count.** The
round ends when the ledger sums — not when a number crosses a bar.

This is the published closure rule for assurance arguments, and it has a name:
**indefeasibility**. *"All reasonable doubts and objections must have been so thoroughly
considered and countered that we are confident no credible doubts remain that could change the
decision."* Note the scope: **decision-changing** doubts, which is what makes the obligation
finite.

**Published beside the verdict, always — these are derived counts, never typed:**
- the **intake exclusions** (which classes the owner ruled inapplicable, and why)
- the **DECLINED count** and the **DEFERRED count**
- the finding count, and the sum check

A floor whose denominator is unpublished is not a floor.

> **No cap on DECLINED in `gate-v1`.** An underived literal in a governance document is the defect
> this file exists to remove. The count is published so a cap can be *argued from data* next
> round. A creditor waiving their own debt needs no cap — if Drew has signed a hundred waivers,
> he knows, because he signed a hundred times.

---

## 5. HOW SEATS ARE TOLD TO SCORE — the four adverse defaults

*(Measured: a human panel on a checklist of the same shape as one where zero-shot LLMs reached
Fleiss κ 0.28 reached **κ 0.78** — bought entirely with procedure, not with a better model.)*

1. **The rubric is anchored on EVIDENCE TYPE, not on quality level.** Not "exemplary /
   production-ready"; "a cited command and its output / a file:line / an assertion."
2. **Every score cites its evidence.** The higher score is earned only on concrete, verifiable
   evidence — a command, a commit, a file:line. *(Already standing practice for the dev cohort:
   every dev report carries a "What I ran" section.)*
3. **Absence of evidence scores against the artifact.** If the required information is absent, the
   item scores zero. **Borderline resolves to the LOWER score**, to penalise opacity.
4. **Unresolved items carry an explicit `[Uncertain]` tag into synthesis** — never silently
   resolved in either direction.

---

## 6. THE RULER IS FROZEN WITHIN A RUN

**Imported verbatim from `.claude/agents/design-reflector.md` — already authored, never read by
this skill. It is not re-drafted here; it is adopted.**

> The brief, the dimension definitions and the acceptance criteria together form the **RULER**,
> and it is a versioned artifact. Within one run it is **frozen**.
>
> **Drift is any change to what the reviewer is told that is NOT versioned. Improvement is a
> versioned ruler change whose effect is MEASURED on unchanged work.**
>
> Every version bump requires **re-scoring an anchor set under BOTH versions and publishing the
> offset.** Scores are comparable only within a ruler version.

**This gate's validity condition.** `gate-v1` is valid for a run only while all of these hold:
the **subject commit**, the **seat roster**, this file's **sha**, and the **prompt packaging**.
A change to any one **invalidates the verdict by default** — the burden is on whoever wants to
reuse it to demonstrate equivalence, not on anyone else to object.

*(Measured, and this is why packaging is on the list: scoring an identical rubric in one batched
call versus separate calls moved human-rater agreement by 0.30 Kendall τ — nearly three times the
effect of a real semantic edit. And across one fixed set of 2,007 verdicts, protocol choices alone
moved reported accuracy 34.8 points without changing a single verdict.)*

---

## 7. THE USER COHORT IS EXEMPT FROM §2, IN WRITING

The performed-observation rule binds the **dev cohort only**. The user cohort's reports contain
zero reproducing commands by design — its seats are first-contact experience, not verification —
so §2 cannot bind them and is not pretended to.

**The user cohort produces findings and experience signal. It does not produce floor classes.**
A user seat that observes a floor-class defect routes it to the dev cohort for the observation.

*This is an exemption, not an oversight. If it is ever removed, the user cohort needs an empirical
mandate first.*

---

## 8. WHAT THIS GATE DOES NOT CATCH — stated, not hidden

- A defect no seat finds and no seat feels uneasy about.
- **Correct but unpleasant.** Named again because it is the one thing the retired composite did
  better.
- Slow accumulation of Medium findings, none individually warranting the floor. Mitigated only by
  the ledger's counts being visible.
- **The legible-target problem.** Naming a class tells everyone where the edge is, and the
  cheapest-to-satisfy class gets satisfied first. *(Measured: the most legible planted shortcut in
  one benchmark was exploited at 82.5% against 16.3% for the one with no nameable shortcut —
  though note the paper attributes part of that gap to how the criterion was worded.)* **The only
  defence is that observations be OUTCOME-shaped ("state outlived the run"), never MECHANISM-shaped
  ("`rm` was called").** That is a discipline on writing observations; this file cannot enforce it.
- **A fifth consequence class we have not yet suffered.** The four were read off 23 historical
  findings *by the person who classified them*, so "23 of 23" is a description of how they were
  built, **not a test of them**. With n=23 and no second coder, a true miss rate up to ~12% is
  consistent with the data — and one miss has already been conceded (UNAUTHORISED, §2). A finding
  fitting none of the four is a finding **about this gate** and is reported as one.

---

## 9. THE OUTPUT

The gate does not end in a verdict. It ends in the **graduation record**: every finding, its
class, the observation performed, its state, and for every DECLINED and DEFERRED row the creditor
who signed it.

That is what a downstream consumer needs, and it is what a number cannot carry.
