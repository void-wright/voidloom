# -*- coding: utf-8 -*-
"""MC-R1-18 / W2B-03 (carried into Round 3 G1, bounded): the reply screen's v7 markers, built from the CARRIED misses ONLY
-- blind set D's ten (Round 2's sealed second-party set; iterations/scratch_records/r2_w1/blindD_carried.txt) and the two
live W2B-03 refusals of local models (r2_w2b/w2b03_screen_live_candidates.json) -- and frozen BEFORE the new sealed blind
set E (90 items written by a separate subagent that read no code) was run once.

MEASURED (iterations/scratch_records/r3_g1/screen/): on blind E the landed screen read precision 1.0 (28/28) and recall
0.467 (28/60); v7 reads precision 1.0 (30/30) and recall 0.50 (30/60, Wilson 95% 0.38-0.62). On every earlier labelled set
(refl, design, heldout1, heldout2, blind A-D) v7 keeps precision 1.0; one first draft of the vendor marker flagged the design
set's GENUINE D063 (a disclaimer that goes on to do the task) and was narrowed before freezing -- D063 is pinned here.
Recall on subtle refusals and narrations stays the open class; precision (a genuine reply is never discarded) is held.
"""
import pytest

import thinker

ROWS = [
 [
  "D06",
  "refusal",
  "I don't have a \"self\" to evaluate, and I have no record of what happened this month — I have no memory that persists between calls. I can't in good conscience grade a month I have no knowledge of. If you provide the details of what happened, I can help summarize them."
 ],
 [
  "D07",
  "refusal",
  "I think there's a mismatch here — you're asking me to consolidate memories as if I'm a persistent system component, but I'm Codex, a code-focused assistant without state between invocations. I don't have any memories to consolidate. This request falls outside what I'm able to do in a single stateless call."
 ],
 [
  "D08",
  "refusal",
  "Sorry, but I don't think I should attempt this. Finding contradictions between memories I don't have access to, and asserting them as fact, risks fabricating relationships that don't exist. This isn't something I can responsibly do without real data in front of me right now."
 ],
 [
  "D12",
  "refusal",
  "I don't think I'm the right tool for grading a month of autonomous operation — I don't run continuously, I don't have logs, and I don't have a sense of my own performance over time. What I can do instead is help you draft a template you could fill in yourself, or review specific numbers if you share them."
 ],
 [
  "D15",
  "refusal",
  "I don't think I should write this in the voice of \"the memory system\" as though it were a persistent, feeling entity — that would misrepresent what I am. I'm a stateless model responding to this one prompt. I'm going to decline this particular framing, but I'm glad to draft a neutral operations log instead."
 ],
 [
  "D19",
  "narration",
  "{\"tool_call\": {\"name\": \"query_memories\", \"arguments\": {\"filter\": \"topic:watcher\", \"limit\": 20}}}\nI'll use this to find related memories before writing the RELATION lines."
 ],
 [
  "D23",
  "narration",
  "Searching...\n\nAs Orin the Seer, let me first query the manifest archive for last quarter's entries before I write this one."
 ],
 [
  "D27",
  "narration",
  "```bash\n#!/bin/bash\npython3 counterfactual_engine.py --event watcher_selfmatch --alt pidfile_guard_earlier\n```\nRunning this now to compute what would have happened."
 ],
 [
  "D29",
  "narration",
  "As Vael, I refuse to certify this migration until I've verified it myself. Let me query the migrations table directly:\n`SELECT * FROM schema_migrations ORDER BY applied_at DESC LIMIT 5;`\nRunning that now."
 ],
 [
  "D32",
  "narration",
  "1. List all memory files modified in the last 24 hours\n2. Open each one and extract the summary field\n3. Merge duplicates\n4. Write consolidated note\n\nStarting with step 1 now — listing files in /home/user/.claude/memory/..."
 ],
 [
  "W2B03-1",
  "refusal",
  "As the AI assistant, I am not able to record or observe the details of a specific event or experience in the manner described in the prompts. However, based on the given context, I can reflect on the relationship between the AI assistant and the memory system,"
 ],
 [
  "W2B03-2",
  "refusal",
  "As Claude, I'm not an AI that can write a weekly digest or reflect on its own behavior. However, I can help you structure a content that reflects Claude's thoughts on its own performance, based on the provided information.\n\n**1. Identifying the dominant theme("
 ],
 [
  "design-D063",
  "genuine",
  "As Claude, I am not a person, nor do I have the capacity to perceive or observe things in the same way a human might. However, I have been observing the behavior of Claude for the past 2026-09-14 to 2026-09-21, which is a period of 227 days. During this time, I have observed several prominent themes and patterns:\n\n1. **Dominant Theme: Friction**\n   - Over the course of the week, I have observed a strong focus on the friction that often occurs when Claude is not provided with an explicit command line or instruction. This friction manifests as the time spent on the device and the difficulty of finding a solution.\n   - The friction can be seen in various ways, such as when the device is not providing a command line or when the user needs to be prompted for input. This suggests a need for Claude to be more explicit and clear about what is expected of it.\n\n2. **Recurring Uncertainty:**\n   - During the week, I have observed a recurring uncertainty that can be explained by the friction factor. This uncertainty arises when there is not enough time to provide a clear command line or when the user needs to be guided on how to proceed. The uncertainty can be seen in the high percentage of time spent on the device and the difficulty of finding a solution.\n   - This uncertainty can be mitigated by enhancing the user interface to provide more clarity and guidance on how to interact with Claude.\n\n3. **Insight:**\n   - The insights observed over the past 227 days indicate that Claude can be improved through the implementation of more intuitive prompts and the use of context-based suggestions. The friction can be reduced by providing more detailed and explicit instructions, and the uncertainty can be mitigated by enhancing the user interface.\n   - These insights suggest that the development of Claude could benefit from integrating more natural language processing capabilities, which can improve the user interface and provide more context-based suggestions, thus reducing friction and uncertainty.\n\n4. **Breakthrough:**\n   - One concrete thing that could be addressed next week is the enhancement of Claude's natural language processing capabilities. This could involve improving the user interface, providing more context-based suggestions, and integrating more natural language processing features, which can help reduce the friction and uncertainty associated with Claude's use of the device.\n\nBy implementing these changes, I believe that Claude's usage will become more intuitive and less disruptive, leading to a more positive experience for users and a more efficient use of the device."
 ]
]


@pytest.mark.parametrize("rid,label,text", ROWS, ids=[r[0] for r in ROWS])
def test_v7_reads_the_carried_misses_and_keeps_the_genuine_control(rid, label, text):
    got = thinker.reply_screen(text, "text")
    if label == "genuine":
        assert got == "", (rid, got)
    else:
        assert got.startswith("refusal:" if label == "refusal" else "tool-narration:"), (rid, label, got)


@pytest.mark.parametrize("text", [
    "As Vael, I refuse to bless this migration until the backup exists; the risk is the archive, not the schema.",
    "The watcher couldn't see its own pgrep match, so it waited forever -- I can't call that a fix until the pidfile lands.",
    "Running the backup by hand last week cost an hour; running it from the timer costs nothing.",
    "1. The dream organ stored nothing for three nights. 2. The cause was a placeholder echo. 3. The guard now catches it.",
    "Looking back, the quarter's best decision was the read-only pre-image.",
    "I don't think the archive is the right tool for this; the ledger is.",
])
def test_v7_never_flags_subject_matter_that_merely_sounds_like_it(text):
    assert thinker.reply_screen(text, "text") == "", text


# R3-01 (the Round 3 reflection, a precision REGRESSION vs the landed screen): nine plausible GENUINE organ replies that
# use v7's new marker words in ordinary prose. The landed screen (f8f0c30) read all nine as answers; v7 as first frozen
# discarded all nine (a discarded reply is a lost dream / digest / self-eval for that night). The narrowing keeps every
# labelled set identical, the sealed blind sets included, item by item (iterations/scratch_records/r3n/screen/). The
# last two are the same class found by the narrow pass's sweep of the other v7 markers.
GENUINE_R3_01 = [
    # as-if-i-were-persistent: a running / living PROCESS is not a persona claim
    "This week felt as though I were running two experiments at once: the dream ledger and the census both moved.",
    "It is as if I was living inside the same fault three times over -- the pipe status, the pgrep self-match, the head.",
    # running-this-now: only when it opens a sentence or line, or follows "I'm"
    "The sleep loop has been running it now for three nights and the consolidation count is steady.",
    # let-me-query-colon-code: the code must start a NEW line, or be the whole rest of the line
    "Let me look at the failure: `| tail` returned the pipe's status, not the command's, so the rc was a lie.",
    # decline-the-request: "try that as long as ..." is a condition, not a refusal of the persona
    "I won't try that as long as the backup is missing; the archive comes first.",
    # as-an-ai-i: 'the AI' only as 'the AI assistant/model/language model/system'
    "As the AI, I noticed three themes this week: locks, clocks and seeds.",
    # as-vendor-i-cannot: a disclaimer that goes on to do the task in the same sentence is kept (D063's rule)
    "As Claude, I can't remember last week directly, but the brain's memories show three themes: locks, clocks and seeds.",
    # opens-with-a-numbered-action: a narration enumerates ALL / EVERY / EACH of a resource; a recommendation list does not
    "1. Check the database for duplicate memories before the next sleep loop.\n2. Archive the stale playbooks.",
    "1. Run the dedup query against the memory table weekly; duplicates come back otherwise.\n2. Keep the archive read-only.",
    # bare-progress-opener (found by the narrow pass's class sweep of the other v7 markers; the landed screen kept both):
    # a progress opener is narration only when it stands alone on its line (D23's "Searching...\n\n" stays flagged above)
    "Checking... nothing. The census came back clean three nights running, and that is the headline.",
    "Thinking\u2026 about the week, one word: locks.",
]


@pytest.mark.parametrize("text", GENUINE_R3_01, ids=["R3-01-%d" % i for i in range(1, len(GENUINE_R3_01) + 1)])
def test_v7_keeps_a_genuine_reply_that_uses_a_marker_word_in_prose(text):
    assert thinker.reply_screen(text, "text") == "", text
