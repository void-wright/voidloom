# THE GATE CORPUS — what was read, and what each paper is for
*Sweep run 2026-09-15. 122 validated arXiv queries · 1,086 unique papers surfaced · 68 full texts read (6.7M chars) · 0 fetch failures.*

**Reproduce the sweep:** `<owner-home>/.arxiv_gates/` — `queries.py`, `queries_b.py`, `sweep.py`,
`sweep_retry.py`, `fetch.py`, `READING_BRIEF.md`, `sweep1.json`, `sweep2.json`, `shortlist.json`, `txt/`.
**Fetch ladder (measured):** `arxiv.org/html/<id>` → `ar5iv.labs.arxiv.org/html/<id>` → `arxiv.org/pdf` + `pdftotext -layout`.
62 of 68 resolved on the first rung, including a 2017 paper. Memories: `vl_arxiv_html_is_the_full_text_avenue`.

---

## THE SPINE — read these six first

| id | what it gives you |
|---|---|
| **2205.04522** | **INDEFEASIBILITY as the stopping criterion**, verbatim. The disposition ledger, 20 years early. Also the residual-doubt classes (Significant / Minor / Manageable / Negligible) and the cumulative cap: *"10 might be acceptable, but not 100."* |
| **2606.25120** | **DO-178C**: completeness is *"an emergent property of the traceability network's structural closure, not a separately measurable attribute."* DAL A–E. Extraneous code is a finding *"regardless of whether they function correctly."* DO-330: *"the burden of proof is on the applicant; the default is invalidation."* |
| **2411.03657** | The field **tried numeric confidence gates and backed out**. n=19 practitioners: 68% aware/never used, 11% ever used, **32% ever used one to support a deploy decision.** |
| **2606.00093** | **Protocol choices moved reported accuracy 34.8 points on a fixed verdict set.** κ̂ SE 0.04–0.06 at N=300. κ=q·φ. Binary Pearson=Spearman=τ_b=φ=MCC are one statistic. |
| **2609.12191** | **Calibrated abstention.** Decision-disagreement 31% on near-equal pairs vs 0.9% on wide ones; ensembling cannot fix it (common-mode); abstention halves error. *"Evaluator reliability is determined by evidence access, not model capability."* |
| **2506.09929** | **Waymo**: a production safety org scores everything 0–3, refuses to average, and keeps the score **off** the ship decision. Evidence-status scale is the best worked example of an observable referent. |

## BY QUESTION

**What is a gate / how does one close?** 2405.15800 (closure predicate: zero active defeaters, zero
unsupported nodes) · 2604.00034 (why every quantification method misbehaves — 12 demolished) ·
2409.10665 · 2506.09929 · 2102.02625 (the template slots + **two dispositions we lacked**:
not-applicable, change-the-claim) · 2411.03336 (termination = red team has no proposal the *judge*
accepts; reviewer-incentive design) · 2501.17315 (held-out split; named invalidation protocol).

**Does the instrument measure anything?** 2509.20293 (**schema incoherence ~55% unexplained; factor
collapse >0.93; ELO manufactures R²=0.998**) · 2602.15532 · 2510.23191 · 2603.15121 · 2607.02586
(five failure modes of a validity audit) · 2608.29420 · 2306.05336 (**67% of authors don't follow
their own definition**).

**Binary vs graded — the correction.** 2605.06283 (**holistic beat binary-decomposed**; a null edit
moved τ by 0.30) · 2508.15218 (**0 of 48 significant in direct scoring**; ~40% of items degrade
agreement) · 2603.00077 (expert binary criteria still disagree 21.5%; the split is construct type) ·
2607.29252 (within one binary bank, per-item κ spans 0.498→0.983) · 2607.19322 · 2603.07019.

**The panel, priced.** 2605.02091 (**Fleiss κ=0.28; best accuracy 0.485; GPT-5 YES-precision 0.167;
43% N/A excluded from the denominator**) · 2508.04325 (**the recipe to κ=0.78**: evidence-type rubric,
calibration rounds, mandatory citation, adverse defaults) · 2604.22517 (**expert α ≤0.06 in 11/18
cells; aggregating hurts; escalate, don't average**).

**Gaming.** 2606.04923 (**the rubric is what gets gamed**) · 2608.30724 (**legibility tracks
exploitation: 82.5% vs 16.3%**; a definitional clause moved the rate 57.1%→77.5%) · 2604.13602
(taxonomy: feature / representation / evaluator / environment) · 2512.19027 (**scoring frame must be
mismatched from the production frame**) · 2407.14503 (**Thm 5/6: a threshold works iff proxy error is
light-tailed AND independent of true quality**).

**Tier by consequence.** 2606.25120 (DAL) · 2602.00078 (EU: horizontal *methods* + sectoral
*thresholds*; *"a miss rate acceptable in content moderation would be unacceptable in medical
diagnosis"*) · 2606.21276 (**CEDAR: identical evidence, pass rate 22.4%→46.2% by aggregation alone;
a per-dimension floor on a mean removed 6 of 49**) · 2512.14902 (assurance grade **capped by evidence
access**) · 2510.09090 (influence × consequence → oversight *kind*) · 2607.03510 · 2505.01643
(**target maturity per process, usually level 3 of 5, not the ceiling**).

**Self-gating.** 2512.01166 (**version-diffs: "would"→"may"; "2x from 2020–2024"→"from historical";
named bodies→"appropriate governance function". Precision is what leaves.** 10/12 state a process;
**12/12 score <25% on stating a tolerance**) · 2505.01643 · 2512.14902.

**Release practice.** 2208.04003 (**n=137: universal DoD is itself problem #4 at 71%**; 62% omitted
work *because the DoD permitted it*) · 2403.18958 (65 sources, 44 orgs → **zero thresholds**) ·
2603.15676 (**the one-lethal-defect rollback: 100/100/97 and evidence coverage 50**) · 2603.27355
(**"runs that violate policies cannot pass gates even if their scalar score is high"**) · 2605.27827
(threshold sensitivity as a first-class quantity) · 2609.00805 (atomisation buys agreement, **not
accuracy**; conformal intervals) · 2507.06260 (a real release: **no numeric cut-point anywhere**).

**The dissent.** 2604.05662 — *"accounts must be assessed holistically… just amassing conjunctions of
claims undermines confidence."* A ledger can certify an artifact nobody understands. Countermeasure:
designed friction, an Understanding Basis, defending the case **with the tooling removed**.

## HONEST NEGATIVES
- **No one in 68 papers defends a numeric acceptance cut-point on an aggregate score.**
- **Psychometric standard-setting (cut scores, Angoff, criterion-referenced) is essentially absent
  from arXiv** — it lives in journals arXiv does not cover. The non-arXiv route is still open.
- **SE release-gate literature is thin on arXiv** (quality gate → 8, definition of done → 7,
  "when to stop testing" → 0). It publishes at ICSE/FSE.
- **Our central thesis — subject-shaped gate beats general gate — is nowhere measured.** The paper
  chosen to test it (2607.14336) explicitly declines the comparison.
- **The MCDA compensatory/non-compensatory statement is not in 2508.16087**, which is a
  ranking-methods chapter. What it does give: VIKOR's max-regret veto term and the L1 under-penalty.

## INSTRUMENT FAULTS FOUND IN OUR OWN TOOLING
- **arXiv quoted phrases are not adjacency searches.** `abs:"cut score"` → 6 results about cutting
  planes. The count lands in the readable band; **only the titles tell you.**
- **arXiv rate-limits per client, not per process** — two polite sweeps are one impolite one.
- **My sweep logger printed only on success**; 32 of 46 queries died to 429 and the log ended clean.
