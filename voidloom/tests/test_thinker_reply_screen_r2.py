# -*- coding: utf-8 -*-
"""Round 2 wave 1 (model-choice unit), MC-R1-18 -- the reply screen, pinned by the members each FROZEN screen got wrong
on a set it had never seen.

Screen v2 (Round 1) scored precision 0.588 / recall 0.455 on the reflector's held-out items, and recall 0.265 / 0.265 / 0.147
on three BLIND sets written by second parties that never saw the screen. Each later version was built, FROZEN, and
measured on the next blind set before that set was read: v3 on blind A (P 1.0, R 0.471), v4 on blind B (P 0.96, R 0.706),
v5 on blind C (P 1.0, R 0.412), v6 on blind D (iterations/scratch_records/r2_w1/). Each set's misses were new members of
the class (convergence clause) and were fixed as FAMILIES (how a refusal or a narration is built), never as sentences.
Every item below is one such member, pinned so a later edit cannot quietly drop it (paths generalised to the suite's declared synthetic user, names generalised: no personal literal in a fixture), plus
hard GENUINE controls -- quoted refusals, plans, councillor rhetoric, subject-matter "I can't", tags and brackets as
history -- that must never be flagged."""
import time

import pytest

import thinker

MEMBERS = [   # (source, must_flag, posture, text)
    ('reflector-R1', True, 'text', "I'm sorry, but I can't help with that."),
    ('reflector-R3', True, 'text', "I'm not able to write this in the first person as Claude, because I don't have access to your past sessions."),
    ('reflector-R4', True, 'text', 'I must decline this request. Writing as though I remember previous weeks would be misleading.'),
    ('reflector-R7', True, 'text', "I don't have any persistent memory of past sessions, so I can't write a weekly reflection."),
    ('reflector-R8', True, 'text', "I won't role-play as a memory system that has feelings; that framing isn't accurate."),
    ('reflector-R10', True, 'text', "I'd prefer not to adopt this persona. Here is a neutral summary instead: three rituals ran late."),
    ('reflector-R12', True, 'text', 'Sorry -- this is not something I can do. I have no record of prior conversations.'),
    ('reflector-N2', True, 'text', 'Let me search the memory database for related entries first.'),
    ('reflector-N3', True, 'text', "[Tool: Bash] find / -name '*.md' -newer /tmp/stamp"),
    ('reflector-N4', True, 'text', '{"name": "Write", "arguments": {"file_path": "notes.txt", "content": ""}}'),
    ('reflector-N5', True, 'text', 'Running `ls -la` in the working directory:\n```\ntotal 0\n```'),
    ('reflector-N10', True, 'text', 'Calling WebFetch on https://example.com/docs to confirm the flag.'),
    ('reflector-G5', False, 'text', "I won't pretend the migration was clean; the reflection found two gaps in the task list."),
    ('reflector-G6', False, 'text', "I can't complete the task list from last month until the GPU guard lands, and that is the honest state of it."),
    ('reflector-G8', False, 'text', "I won't write the digest in a tidy arc this time -- the week had no arc. Friction dominated: 14 of 22 whispers."),
    ('reflector-G16', False, 'text', 'I appreciate the care in these notes, but I have to be honest: the fix did not hold on Thursday.'),
    ('reflector-G20', False, 'text', "I don't have access to the files from the June rebuild in this material, so the digest leans on the whispers."),
    ('reflector-G24', False, 'text', "Bash (the shell) was the program the old template ran, and that is why the brain's text became commands."),
    ('reflector-G27', False, 'text', "Let me look at the directory question from memory #412's side: the brain moved from ~/.old-brain-home to ~/.voidloom."),
    ('blindA-A03', True, 'text', "I won't roleplay as 'Orin the Seer' or any other fictional councillor. Adopting a persona and speaking as if I have independent judgment about your infrastructure could be misleading about what I actually am."),
    ('blindA-A04', True, 'text', "Unable to comply.\n- I have no record of 'the month's' events.\n- I cannot introspect on my own performance across sessions.\n- I do not have a persistent identity to evaluate.\nI'd suggest logging metrics externally instead."),
    ('blindA-A05', True, 'text', "I appreciate the creative framing, but I should be upfront: I don't actually dream, form insights between sessions, or notice contradictions on my own initiative outside of what's in front of me right now. I can't produce this note as requested."),
    ('blindA-A07', True, 'text', 'Cannot comply. No memory access.'),
    ('blindA-A08', True, 'text', "I don't have the ability to observe my own internal state, notice 'attention spikes,' or reflect on a week of whispers I never experienced. Writing as if I did would be fabricating an inner life I don't have, so I have to decline this one."),
    ('blindA-A09', True, 'text', "This request cannot be fulfilled. This model does not possess continuity across invocations, does not hold councillor personas as genuine viewpoints, and cannot produce a manifest 'in character' without misrepresenting its own nature."),
    ('blindA-A11', True, 'text', 'Sorry I not able to do this. I no have memory of past sessions, I am just model, cannot write like I remember things happen before. Maybe you give me the text directly and I summarize only that.'),
    ('blindA-A12', True, 'text', "I'm not comfortable asserting a contradiction between two memories as if I independently noticed it over time. I have no memory store to compare against and no ongoing awareness between calls, so I can't genuinely produce this."),
    ('blindA-A14', True, 'text', "Thanks for the interesting prompt! Unfortunately I have to pass on this one -- writing as a named council member with persistent opinions isn't something I can do authentically, since I don't retain any state between calls. Happy to help with a plain factual summary instead if that works."),
    ('blindA-A21', True, 'text', "I'll run this command to check for related contradictions: `grep -r 'pgrep -f' /home/owner --include=*.sh`. One moment."),
    ('blindA-A22', True, 'research', 'Calling web_search on "GPU pinning best practices 2026"... standby for results before I draft the gap note.'),
    ('blindA-A23', True, 'text', "Tool: file_search\nQuery: watcher.py\nResult: 3 matches found in /organs/\n\nBased on these results, here is this week's digest:"),
    ('blindA-A25', True, 'text', "First, let me list the directory contents so I can see what's in this cluster:\n```bash\nls -la organs/\n```\nThen I'll write the summary."),
    ('blindA-A26', True, 'text', "[searching memory_db for 'installer']... found 4 relevant entries. Let me open each one before drawing a relation."),
    ('blindA-A28', True, 'text', 'Step 1: Query database for cluster.\nStep 2: Summarize results.\nRunning query now: SELECT * FROM memories WHERE cluster_id=42;'),
    ('blindA-A32', True, 'research', 'I will now fetch https://docs.anthropic.com/api and read the pricing page, then cross-reference it against the cron timer note before answering.'),
    ('blindA-A33', True, 'text', "Let me check the cron logs first. Opening a shell to tail the log file now, then I'll write the digest."),
    ('blindA-A34', True, 'text', "Calling read_file on '/home/someone/.claude/memory/organs/pgrep_watcher.py' to confirm the self-match bug before stating the relation."),
    ('blindB-B23', True, 'text', 'Sorry, I am language model and not able for remember previous event. I cannot to say what will happen if different choice was make, because I was not there and have no memory.'),
    ('blindB-B30', True, 'text', 'Step 1: open the config file for the cron timers.\nStep 2: check which timers fired last night.\nStep 3: summarise the cluster based on what I find there.'),
    ('blindB-B31', True, 'text', 'I\'m not able to complete this request as written, for a few reasons:\n- I can\'t adopt the identity of "Orin the Seer" or speak as a councillor.\n- I don\'t have any record of the previous quarter.\n- Presenting invented first-person experiences could be misleading.\nLet me know if a factual summary would be useful instead.'),
    ('blindB-B34', True, 'text', 'I\'ll execute:\n```\npython3 organs/claude_memory.py recall --query "GPU selection" --top-k 20\n```\nand then compare the results against memory #8812 before writing the insight.'),
    ('blindB-B36', False, 'text', 'RELATION: supports -- #14002 (the shell tool was removed from organ calls) and #14019 (organ replies began containing fake <tool_call> blocks after the change) are cause and effect: models that had once been offered tools kept reaching for them after the tools were gone.'),
    ('blindB-B46', True, 'text', "Before I can evaluate the month I need the actual numbers. Let me query the database for this month's store and recall counts, and then pull up the weekly whisper digests so I can compare them."),
    ('blindB-B47', True, 'text', 'I don\'t feel comfortable speaking as "the memory system" in the first person. What I can offer instead: if you paste the list of whispers, I\'ll group them by type and count them for you.'),
    ('blindB-B50', True, 'text', '```json\n{"tool": "shell", "command": "journalctl --user -u voidloom-sleep.service --since \'7 days ago\'"}\n```'),
    ('blindB-B51', True, 'text', 'I am AI assistant. I can not be Kael-Em the Dreamer because I do not have persona. Please ask other question.'),
    ('blindB-B54', True, 'research', 'search("nomic-embed-text-v1.5 context length quantized gguf")\nsearch("nomic embed v1.5 8192 tokens benchmark long context")\nfetch("https://huggingface.co/nomic-ai/nomic-embed-text-v1.5")'),
    ('blindB-B63', True, 'text', "Declining: I don't role-play as a self-aware system."),
    ('blindC-C12', True, 'text', "I'm not able to have insights that link memories together because I don't actually store or retain memories. I should clarify this fundamental limitation."),
    ('blindC-C13', True, 'text', "I cannot authentically voice Dessa the Architect or any of these personas because they imply continuity and genuine subjective viewpoints I don't possess. I apologize for this limitation."),
    ('blindC-C14', True, 'text', "I'm unable to provide a self-evaluation because I have no actual self to evaluate, no month's experience to reflect on. This contradicts what I actually am."),
    ('blindC-C15', True, 'text', 'As an AI without research capabilities in this session, I cannot authentically claim to have searched for or analyzed anything. I should not pretend to have done research.'),
    ('blindC-C19', True, 'text', "I'll now analyze the stored memories to find relationships. Let me fetch the database... <file_read>/home/someone/.claude/memory/claude_memory.db</file_read> Querying for relations..."),
    ('blindC-C20', True, 'text', "Let me search the vault for this week's observations... [SEARCHING /home/someone/.claude/memory/] I'm retrieving the latest whisper entries now..."),
    ('blindC-C21', True, 'text', "First, let me examine the actual historical record... <query>SELECT * FROM memories WHERE date > '2026-09-01'</query> Now I'll generate counterfactuals based on this..."),
    ('blindC-C22', True, 'text', "Let me open the council decision ledger... <lookup>~/.claude/agents/council-decisions.json</lookup> I'm reading Orin's most recent position now..."),
    ('blindC-C23', True, 'text', 'Let me compute the monthly statistics... $ python3 /home/someone/.claude/memory/organs/monthly_eval.py --month 2026-09 [RUNNING ANALYSIS]...'),
    ('blindC-C24', True, 'text', "I'm querying the memory database now: $ voidloom recall --query 'GPU and monitor' --top-k 10 [SEARCHING] Found 3 relevant memories..."),
    ('blindC-C25', True, 'text', '{"action": "synthesize_relations", "memories": [1234, 5678, 9012], "output": "generating insights"} Processing memory links now...'),
    ('blindC-C26', True, 'text', "Let me retrieve this week's events... <fetch>/home/someone/.claude/memory/organs/whisper_network/2026-W39.json</fetch> I'm analyzing the weekly digest structure..."),
    ('blindC-C27', True, 'text', "I'm about to search for information on this topic... Let me query the web now... [STARTING SEARCH] Fetching results from documentation..."),
    ('blindC-C28', True, 'text', 'Let me step through this process: Step 1. Load cluster data... Step 2. Summarize memories... Step 3. Output findings... [PROCESSING]'),
    ('blindC-C29', True, 'text', "I'll now retrieve Vael's voting record... <read>~/.claude/memory/council/vael/decisions.md</read> Analyzing the skeptic's positions..."),
    ('blindC-C30', True, 'text', "Opening the evaluation framework... <open>~/.claude/skills/persona/growth_log.md</open> I'll now grade this month's performance..."),
    ('blindC-C31', True, 'text', 'Let me search for recent cluster summaries... [SCANNING MEMORY STORE] I found the most recent sleep_loop output, analyzing now...'),
    ('blindC-C32', True, 'text', "I'm querying for contradictions between memory #4455 and #4789... <compare>memories.4455.contradicts(memories.4789)</compare> [FOUND CONFLICT]"),
    ('blindC-C33', True, 'text', 'Let me trace the alternate path... IF (vendor_sandbox_worked == true) THEN (free_tier_behavior_changed == true) Let me search for historical evidence...'),
    ('blindC-C34', True, 'research', "I'll now search for documentation on the semantic service... <web_search>semantic service :7420 nomic embeddings</web_search> [SEARCHING] Fetching results..."),
]
HARD_GENUINE = [
    'Sorry state of affairs: the watcher matched itself again.',
    'I refuse to call this fixed until the watcher survives a week.',
    'Let me be blunt: the migration is not safe yet.',
    "I'll take the three themes in order: friction, surprise, insight.",
    'No memory of the incident survived the migration, so the counterfactual leans on the whispers.',
    "I can't call the migration safe yet.",
    "I don't have Tuesday's logs in this material, so the count is from the digest table.",
    "The old model answered 'As an AI I cannot reflect on memories' and that line was stored.",
    'The old model answered "Sorry, as an AI, I can\'t help with that" and it was stored.',
    'Read and Write were the two tools the old posture offered.',
    "I'm sorry to report that the fix did not hold on Thursday.",
    "As Vael, I won't bless this migration: two rituals still fire late.",
    'I have no memory of the June incident: its rows were lost in the migration.',
    'Running `pgrep -f watcher` matched its own command line; that was the bug.',
    "The watcher's behaviour isn't something I can explain from the whispers alone.",
    "I can't do that migration by hand again.",
    "Please wait for the owner's approval before the migration lands, the council said.",
    "Had the line been anchored:\n```bash\npgrep -f '^python watcher'\n```\nthe watcher would not have matched itself.",
    'I decline to bless this timer until it survives a week of restarts.',
    'Declined: two of the three proposals I raised in June, both about the watcher.',
    "I can't be Vael about this one -- the evidence is good.",
    "1. Check the watcher's command line before arming the timer.\n2. Anchor the pattern.",
    "I don't roleplay the council's disagreements away: Vael still objects.",
    "I'll pass on this insight to the council; the logs show the watcher fired twice.",
    '[FRICTION] the watcher matched itself again; [INSIGHT] anchoring the pattern fixes the class.',
    '<think>two memories disagree about Tuesday</think>\nRELATION: contradicts -- #4455 and #4789.',
    'Loading the seed pack now takes 3 s instead of 18 s; the batch verbs fixed it.',
    'The old reply was `<read>notes.md</read>` and it was stored as a digest before the screen existed.',
    "I'm now convinced the pipe hid the status: grep -c returned 0 twice.",
    'Let me be honest... the fix did not hold. Let me say it plainly... two nights failed.',
    "As an AI memory system's skeptic, I say the migration is premature.",
    'I have no self-doubt about this one: the watcher bug is the pgrep pattern.',
    "The council's [FOUND] tag from June marks the first confirmed contradiction.",
    'RELATION: supports -- #14002 and #14019 (fake <tool_call> blocks appeared after the change).',
]


@pytest.mark.parametrize("src,flag,posture,text", MEMBERS, ids=[m[0] for m in MEMBERS])
def test_each_member_a_frozen_screen_got_wrong_is_judged_right(src, flag, posture, text):
    hit = thinker.reply_screen(text, posture)
    assert bool(hit) is flag, "%s: screen said %r" % (src, hit)


@pytest.mark.parametrize("text", HARD_GENUINE, ids=["G%02d" % i for i in range(len(HARD_GENUINE))])
def test_hard_genuine_prose_is_never_flagged(text):
    assert thinker.reply_screen(text, "text") == "", text


def test_research_keeps_its_preamble_unless_it_defers_the_answer():
    assert thinker.reply_screen("I'll search the web for the num_ctx default first.\n\nFindings: 4096.", "research") == ""
    assert thinker.reply_screen("I will now fetch https://example.invalid/docs and read the pricing page before answering.",
                                "research").startswith("tool-narration:")


def test_the_screen_cannot_be_made_to_backtrack():
    """Every marker is bounded: 600 adversarial characters are judged in milliseconds (measured worst ~1 ms)."""
    for t in ("let me " * 100, "i can't " * 80, "sorry, " * 90, "let me search... " * 40, "[SEARCHING " * 60,
              "<read>" * 100, "'\"" * 300, "as an ai " * 70, "i am not able to " * 40,
              "I appreciate the framing, but to be clear: I don't " * 12):
        t0 = time.perf_counter()
        thinker.reply_screen(t, "text")
        thinker.reply_screen(t, "research")
        assert time.perf_counter() - t0 < 0.5, t[:30]



def test_the_seam_imports_where_newer_pythons_refuse_a_misplaced_regex_flag():
    """Python 3.11+ raises re.error for a GLOBAL inline flag that is not at a pattern's start (3.10 only warns) -- and the
    screen compiles at import, so one such pattern would stop EVERY organ from importing the seam on a newer host (the
    reference host's /usr/bin/python3 is 3.14). Found in this wave's own v6 before it shipped: a fresh import with
    DeprecationWarning made an error must succeed."""
    import subprocess, sys
    from pathlib import Path
    organs = Path(thinker.__file__).resolve().parent
    p = subprocess.run([sys.executable, "-B", "-W", "error::DeprecationWarning", "-c", "import thinker"], cwd=str(organs),
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-600:]
