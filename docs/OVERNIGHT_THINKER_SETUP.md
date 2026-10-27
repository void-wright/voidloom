# Connecting the model that thinks while you sleep

> **Who this is for:** anyone setting up Voidloom who has never done this before, on
> their own computer, inside an application that embeds Voidloom, or on a computer that already runs it.
> You will not need to understand any of the machinery. Every step says what to click,
> what to type, and what you should see.
>
> **How long:** about 5 minutes per model, and you only do it once.

---

## 1. What this is, in plain words

Voidloom is a memory for your AI assistant. While you sleep, it does some **thinking on
its own**: it tidies memories, connects ideas, and writes short summaries. To think, it
has to ask an AI model, and that model needs to know the request is really from you.

That proof of identity is called a **credential**: a long password-like string, or a
sign-in that the model's company recognises.

**The one rule this whole guide follows:**

> **The brain never borrows your everyday sign-in.** It gets its **own** credential, kept
> in its **own** locked folder, used **only** by the overnight jobs.

Why? Some sign-ins renew themselves every time they are used by rewriting a file. If the
overnight jobs used *your* sign-in, they would rewrite *your* file while you might be
using it, and you could be signed out without warning. A separate credential avoids that
completely.

---

## 2. Which one do you need?

You only need the one for the model you choose for the overnight thinking. The installer
asks you, on every install and upgrade, before it changes anything:

> **Should your brain think while you sleep, and with which model?**
> a) Claude · b) Codex · c) Kimi · d) Gemini (Google; runs through Google's Antigravity program) · e) Not now — no model; keep the simple nightly routines · f) Other (which model?)

Nothing is pre-selected, and the model you chat with can be a different one. *(Gemini is
not yet available on Mac: on macOS the installer shows that beside d) and asks for another
letter.)*

| Your choice | What you do once | How long it lasts | Where it is kept |
|---|---|---|---|
| **a) Claude** | Run one command and paste the long-lived code it gives you | About **1 year**, then you repeat it | `~/.voidloom/thinker/claude.env` |
| **b) Codex** (OpenAI) | Sign the brain in **once** with a short code in your web browser | Renews **itself**, about every 8 days | `~/.voidloom/thinker/codex/` |
| **c) Kimi** | Create one **API key** on the Kimi website and paste it | Does not renew or rotate; lasts until you delete it | `~/.voidloom/thinker/kimi.env` |
| **d) Gemini** (Google) | Sign the brain in **once** with Google: open a link, paste the code Google shows you | Renews **itself** while it is used; Google ends a sign-in left unused for about six months | `~/.voidloom/thinker/gemini-home/` |
| **e) Not now** | Nothing. The brain simply does not think overnight | — | — |
| **f) Other** (a model on your own computer, or another tool) | Usually nothing, since a model on your own computer needs no sign-in | — | — |

`~` means **your home folder** (for example `/home/yourname`). `~/.voidloom/thinker/` is
created by the installer (or by `voidloom thinker login`) with permissions that only you can open.

### Get the program first

Claude, Codex, Kimi and Gemini each run through the company's own program. If the program
is not on your computer, the installer says so **before** it shows the Part, prints the
company's own install command (below), and asks **Run it now? [y/N]**. It runs the command
only if you type **y**: never with `sudo`, never without showing it, and never a second
install behind it (Codex needs Node.js; if that is missing the installer stops and gives
you the link). Answer **N** to pick another letter, or run the command yourself and start
again. With no terminal (an AI assistant installing for you), it runs only when you said
yes and the assistant passes `--install-thinker-cli`.

| Your choice | The program | The company's own install command (macOS, Linux, WSL) |
|---|---|---|
| a) Claude | Claude Code (`claude`) | `curl -fsSL https://claude.ai/install.sh \| bash` |
| b) Codex | Codex (`codex`) | `npm install -g @openai/codex` (needs [Node.js](https://nodejs.org/en/download)) |
| c) Kimi | Kimi Code (`kimi`) | `curl -fsSL https://code.kimi.com/kimi-code/install.sh \| bash` |
| d) Gemini | Antigravity (`agy`) | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` |

*(These were checked on 2026-10-16; companies move them. The installer reads them from its
own catalog, `organs/thinkers.json`.)* After an install the installer finds the program
again, even when a fresh terminal would not have its folder on PATH yet, and uses it by its
full path.

---

## 3. Before you start: the terminal

Every part below uses a **terminal**, a window where you type commands.

- **Windows:** open the Start menu, type **Ubuntu** (or **WSL**), and press Enter.
- **macOS:** open **Terminal** (Applications → Utilities → Terminal).
- **Linux:** open your **Terminal** application.
- **Inside an application that embeds Voidloom:** use the terminal it gives you.

Things that are normal, and one rule:

1. **Pasting:** in most terminals you paste with **Ctrl+Shift+V** (Windows/Linux) or
   **Cmd+V** (macOS). A right-click also pastes in many terminals.
2. **Hidden paste:** when a command asks for a key or token, **nothing appears on the
   screen while you paste**. No dots, no stars. That is on purpose, so the secret is
   never shown. Paste once, then press **Enter**.
3. **Every secret is asked for twice.** You paste it, then paste it again to confirm. If
   the two differ, nothing is saved, so a slip of the keyboard can never become your key.
4. **Never paste a key into a chat with an AI** (including Claude). Keys go into the
   terminal commands below, and only there.

---

## 4. Part A: Kimi (option c)

**Get the program first** (if `kimi` is not installed; the installer offers this for you):
`curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash`

**What you need first:** a Kimi membership that **includes Kimi Code**. According to
Kimi's own documentation, the **Plus** tier and above include it; the basic **Go** tier
has no coding quota. *(Tier names can change. If you can sign in to Kimi Code and use it,
you have the right membership.)*

### A1. Open the Kimi Code Console

1. In your web browser, go to **https://www.kimi.com/code/console**
   *(if that address does not open, go to kimi.com, sign in, and look for
   **Kimi Code** → **Console**).*
2. Sign in with the **same account** that has your membership.

### A2. Create the key

1. Find the **API Keys** section and click **Create API Key**
   *(the exact button wording may differ slightly).*
2. When asked for a name, type **voidloom-overnight**, so later you know what it is for.
3. The key appears **only once**. Click **copy** (or select it and copy) **now**.
   You can have up to 5 keys. If you lose this one, just make a new one and delete the old.

### A3. Check "Extra Usage" is off (recommended)

The key uses the **same monthly quota** as everything else on your membership
("all logged-in devices and API keys share the same quota"). If a setting called
**Extra Usage** is switched **on** in your Kimi account, anything beyond your quota is
charged at close to normal API prices. Leave it **off** unless you want that.

### A4. Save the key where only the brain can read it

- **During installation:** you do nothing extra. When you press Enter after this part, the
  installer asks you for the key itself. If a key is already saved, the installer says so
  first -- *a key is already saved; press Enter to prove it* -- and proves that one instead:
  there is nothing to paste.
- **Any other time** (or to replace the key), run:

  ```bash
  voidloom thinker login kimi
  ```

You will be asked **twice**:

1. `Paste your Kimi Code key, then press Enter:` → paste (**nothing will appear**, which is normal) → **Enter**
2. `Paste it again to confirm, then press Enter:` → paste the **same** key again → **Enter**

It then **proves** the key before saving it (A5), and you should see
**`Saved (NN characters, fingerprint xxxxxxxxxxxx).`** NN should be well over 20 (Kimi keys
are long). The fingerprint is a short code made from the key. It lets you confirm later
that the same key is still in place, without anyone ever seeing the key itself.

If it says **"The two pastes were different"** or **"That paste had only … character(s)"**,
nothing was saved. Copy the key again and run it again.

*What it does, in words:* asks for the key **twice** without showing it; removes stray
spaces and line endings; refuses if the two pastes differ (a stray keystroke can never
become your key) or if what arrived is too short to be a key; proves it; and only then
writes it into one file, `~/.voidloom/thinker/kimi.env`, in a folder only you can open.

**Optional check** (shows the file exists and is private, **not** the key):
```bash
ls -l ~/.voidloom/thinker/kimi.env
```
You should see a line starting with `-rw-------`.

### A5. How it is proven

The same step proves the key before it is saved, with every tool switched off, in an
empty, separate place that **cannot** see your normal Kimi sign-in:

1. first with a deliberately **fake** key, which must be **refused** (so a success cannot
   come from anything else);
2. then with your key, which must answer one test question exactly.

Only if both behave is the key saved. During installation the overnight jobs are switched
on only after this passes.

---

## 5. Part B: Codex (option b)

**Get the program first** (if `codex` is not installed; the installer offers this for you):
`npm install -g @openai/codex` -- it needs **Node.js** (https://nodejs.org/en/download),
which the installer never installs for you.

### Why Codex is different (read this once)

OpenAI offers a long-lived "access token" (the equivalent of Claude's 1-year code) **only
to ChatGPT Business and Enterprise workspaces**. Personal plans (Free, Plus, Pro) do not
get one.

So instead, the brain gets **its own separate sign-in**, in **its own folder**. You do
it **once**, with a short code in your browser. After that it looks after itself: about
every 8 days it quietly renews, **inside its own folder only**, and it never touches the
Codex sign-in you use every day.

The brain also makes sure only **one** overnight job uses this sign-in at a time. OpenAI
warns that two jobs renewing the same sign-in at once can break it.

### B1. Allow "device code" sign-in in ChatGPT (one time)

Device-code sign-in lets you approve a sign-in from any browser by typing a short code.
It works even when the computer doing the signing-in has no browser, such as a sandbox or
a server. OpenAI requires you to switch it on first:

1. Go to **https://chatgpt.com** and sign in.
2. Open **Settings** (click your name or picture) → **Security**.
3. Find the switch about **device code** sign-in (authorization) **for Codex**, and turn
   it **on**.
   *(The exact wording may differ. If you cannot find it, use the browser method in B2-alt
   on a normal desktop.)*

### B2. Sign the brain in

- **During installation:** when you press Enter after this part, the installer starts the
  sign-in itself. If the brain's Codex folder is already signed in, the installer says so
  first -- *a sign-in is already saved; press Enter to prove it* -- and proves that sign-in
  instead.
- **Any other time** (or if the sign-in ever lapses), run:

  ```bash
  voidloom thinker login codex
  ```

You will see a **web address** and a **short code**.

1. Open that web address in **any** browser (your phone works too).
2. Sign in to ChatGPT if asked.
3. Type the short code exactly as shown.
4. Approve.

Back in the terminal, it should say you are **logged in**, and then that the proof passed.

*What it does, in words:* makes the brain's own Codex folder `~/.voidloom/thinker/codex/`
(only you can open it), tells Codex to keep the sign-in in a plain file there (the
overnight timers cannot reach a desktop keyring), and runs Codex's own sign-in
(`codex login --device-auth`) **with that folder as its home**, so the sign-in is saved
there, not in your everyday `~/.codex` folder. Then it proves it with one test question.

**B2-alt (browser method, if device code is not available):**
```bash
voidloom thinker login codex --browser
```
A browser opens (on Windows with WSL, a link is printed; open it in your normal browser).
Sign in and approve.

### B3. Check it

```bash
CODEX_HOME=~/.voidloom/thinker/codex codex login status
```
You should see **`Logged in using ChatGPT`**.

### B4. How it is proven

The same step asks one test question with every tool switched off, from the brain's own
folder, and checks that your everyday `~/.codex` sign-in did not change. During
installation the overnight jobs are switched on only after this passes.

### Please do NOT

- **Do not copy** your everyday `~/.codex/auth.json` into the brain's folder. A copy and
  the original share one renewal chain; whichever renews first breaks the other.
- **Do not put an OpenAI API key** (`OPENAI_API_KEY` or `CODEX_API_KEY`) in the brain's
  settings. An API key silently takes priority over a ChatGPT sign-in, and it is
  **billed separately** at API prices, not through your ChatGPT plan.

### If you have ChatGPT Business or Enterprise

An admin can let you create a **Codex access token** (Business/Enterprise workspaces
only; it lasts 1 to 90 days and is shown once). The overnight thinker does not use one
yet: it always signs in with B2. Most people will not have this.

---

## 6. Part C: Claude (option a)

**Get the program first** (if `claude` is not installed; the installer offers this for you):
`curl -fsSL https://claude.ai/install.sh | bash`

1. In your terminal, run:
   ```bash
   claude setup-token
   ```
2. A web page opens (or a link is printed). Sign in with your Claude account and approve.
3. The terminal prints a long code starting with `sk-ant-oat01-`. **Copy it.** It lasts
   about a year and only allows asking the model questions.
4. Save it:
   - **During installation:** when you press Enter after this part, the installer asks you
     to paste it, twice (hidden). **If a code is already saved** (by an earlier install, by
     `voidloom thinker login claude`, or as the older `CLAUDE_CODE_OAUTH_TOKEN=` line), the
     installer says so before this Part: *a code is already saved; press Enter to prove it*.
     Then you do **not** run `claude setup-token` again -- just press Enter, and the saved
     code is proven where it is (a code on the older line stays on that line; `voidloom thinker migrate`
     copies it into `~/.voidloom/thinker/claude.env` when you choose). (To replace it instead, press Ctrl-C
     and run `voidloom thinker login claude`.)
   - **Any other time** (or to replace it): `voidloom thinker login claude` asks for it
     twice the same way.

   Either way it is proven first (a fake code must be refused, then yours must answer one
   test question with every tool off), then saved in `~/.voidloom/thinker/claude.env`, and
   you see `Saved (NN characters, fingerprint xxxxxxxxxxxx).`
5. About 30 days before the year is up, the brain's weekly check reminds you to repeat
   these steps.

**If you saved a Claude code the older way** (a `CLAUDE_CODE_OAUTH_TOKEN=` line in
`~/.voidloom/wizard-library-env`, or a file named `claude-oauth-token`), it still works:
`voidloom thinker status` shows it as *legacy*. `voidloom thinker migrate` copies it into
`~/.voidloom/thinker/claude.env` (it refuses if the two old places hold different codes;
then use `voidloom thinker login claude` and paste the one to keep). After that, check `voidloom thinker status`: when it
says the old line is no longer needed, you delete it yourself (nothing removes it for you). If it says the line is
STILL NEEDED, it names the nightly job that still reads it -- keep the line until that changes.

---

## 7. Part D: A model on your own computer, or opting out

- **A model on your own computer (option f):** no sign-in is needed, because nothing
  leaves your machine. Have the model running first; the installer then asks which model,
  and how it is run: a local model server's address (an ollama server, or one that speaks
  the OpenAI format), or a command with `{model}` and `{prompt}` in it. It proves the model
  with two test requests before recording it. A command runs as a plain list of words,
  never through a shell: `{prompt}` must be a word of its own, and a shell (`sh -c`,
  `bash -c`), a wrapper (`env`, `timeout`), inline code (`python -c`) or a switch that acts
  without asking (`--yolo`, `--dangerously-...`) is refused. Only `ollama run` is known to
  have no tools; any other program is recorded only if you, the owner, accept that yourself
  with `voidloom thinker set local --command '...' --accept-agentic`. Gap research needs the web, which a local
  model does not have, so on a local model that one job is skipped and says so; it never
  invents research. *Which local tool Voidloom recommends is still being decided.*
- **Not now (option e):** nothing to do. The brain still remembers everything you tell
  it; it just does not do its overnight thinking. You can switch this on later (§10).

---

<!-- Part E is lane L-G's text (for_LI/PART_E.md, 2026-10-16), placed by lane L-I. install.sh finds it by "Part E:". -->
## 8. Part E: Gemini (option d)

**What you need first:**

- a **Google account**. The brain uses whatever Gemini allowance that account's plan gives the Antigravity
  program: a free account gets a smaller allowance, renewed weekly, and Google AI Pro or Ultra get more, renewed every
  few hours. Google can change these allowances.
- the **Antigravity program** (`agy`) on this computer. If it is missing, the installer offers to install it for you.
  You can also install it yourself with the command Google publishes on its install page,
  https://antigravity.google/docs/install (on Linux, at the time of writing:
  `curl -fsSL https://antigravity.google/cli/install.sh | bash`).
- **bubblewrap** (`bwrap`), the small program the sandbox needs: every Gemini call runs inside it, so the brain's
  calls cannot see your own Gemini sign-in. On Ubuntu or WSL: `sudo apt install bubblewrap`. The installer checks
  for it before this part is shown; it shows this command and never runs it.
- **Linux or WSL.** Gemini is not yet available on Mac: there the Antigravity program keeps its sign-in in the
  macOS Keychain, which belongs to your user account rather than to a folder. So the brain cannot have a sign-in of
  its own on a Mac without sharing yours. Choose another option on a Mac for now.

### E1. What is different about this one

You do not copy or paste a key into Voidloom. Instead the brain gets **its own Google sign-in**, kept in its
own folder, `~/.voidloom/thinker/gemini-home`. It is signed in once, by you, with the Antigravity program's own
sign-in screen.

- **Your everyday Gemini sign-in is not touched.** It lives in `~/.gemini`. The brain's sign-in, and every
  question the brain asks Gemini afterwards, run in a sandbox where `~/.gemini` is hidden and empty. This is needed
  because the Antigravity program looks for `~/.gemini` on its own even when it is told to use another folder. The
  brain also checks, without opening it, that your sign-in file has not changed.
- **The brain never sees your password.** You sign in on Google's own web page. The code Google shows you goes
  into the Antigravity program, not into Voidloom. Voidloom does not ask for it twice, because it never saves it:
  Google checks it, and a wrong code is refused on the spot.
- **Google's terms:** conversations made through this sign-in may be used by Google to improve its products.
  That covers what the brain sends overnight (summaries and notes drawn from your memories). If that is not
  acceptable to you, choose another option.
- **The model is fixed** to Gemini 3.8 Flash, the current Flash model. The brain never uses Google's larger
  "Argon" model for its overnight work. Every call asks for Gemini 3.8 Flash by name, and the Antigravity program
  refuses any model name it does not know. Its reply does not say which model answered, so the brain checks the
  name another way: at every proof and every weekly check it confirms the name is on your account's model list
  (`agy models`) and that the program refuses a made-up model name.

### E2. Sign the brain in

- **During installation:** when you press Enter after this part, the installer starts the sign-in for you.
- **Any other time** (or to sign in again), run:

  ```bash
  voidloom thinker login gemini
  ```

What happens, step by step:

1. **About a minute of waiting.** The brain first checks that a call with *no* sign-in is refused. If something
   on your computer answered anyway (for example your everyday sign-in), it stops and tells you, and nothing is
   signed in.
2. **The Antigravity program opens** inside the sandbox.
   - If it asks about a colour theme or about trusting a folder, accept the default.
   - Choose to **sign in with Google**. If it does not offer that, type `/login` and press Enter.
   - It shows a **web address**. Open it in your browser and sign in with the Google account whose plan should
     do the brain's thinking.
   - Google shows a **code**. Copy it, paste it back into the terminal, and press Enter.
   - When the program says you are signed in, type `/exit` and press Enter.
3. **One test question** is asked with every tool switched off. You see
   `Signed in: the brain's own Google sign-in is in ~/.voidloom/thinker/gemini-home. Proven: ...`.

Gemini needs **bubblewrap**, the small program that makes the sandbox. If your computer does not have it, the
brain stops before any sign-in and says: *Gemini needs the bubblewrap sandbox so the brain's calls can't see your
own Gemini sign-in: install it with `sudo apt install bubblewrap` (or your distro's package), then run this again.*
The brain shows that command; it never runs it for you.

### E3. How long it lasts

The sign-in renews itself as long as the brain uses it. Google ends a sign-in that has not been used for about
**six months**, or when you change your Google password or remove the app's access in your Google account. If that
happens, the brain's weekly check says so, and you run `voidloom thinker login gemini` again.

The Antigravity program normally updates itself. For the overnight jobs the brain switches that off, and each proof
records the program's version. If the program changes later (for example when you update it yourself), the weekly
check notes it, and `voidloom thinker set gemini` proves the new version.

### E4. Removing it

To stop using Gemini, choose another option (`voidloom thinker set <claude|codex|kimi|local|off>`). To remove the
brain's Google sign-in completely, delete the folder `~/.voidloom/thinker/gemini-home` and, if you like, remove
"Antigravity" from the apps with access to your Google account (myaccount.google.com, **Security**, **Your
connections to third-party apps & services**). Your everyday `~/.gemini` stays as it is.

## 9. Where this happens, place by place

### 9.1 Standalone Voidloom (your own computer)

1. You install Voidloom (through whichever AI tool you are using, or by running the
   installer yourself).
2. **The installer stops and asks** which model should do the overnight thinking:
   **a) Claude, b) Codex, c) Kimi, d) Gemini, e) Not now, f) Other (which model?).** It
   never guesses and never picks for you. If you choose **f**, it asks which model. If an AI
   assistant is running the installer for you, it must ask you this question word for word
   and pass your answer by its name (`--thinker claude`, `codex`, `kimi`, `gemini`, `off` or
   `local`); it must never choose. *(The letters still work as a flag; `--thinker d` and
   `--thinker f` print a note, because d) was Other until 2026-10-16.)*
3. If your choice's program is not installed, it offers the company's own install command
   first (§2, *Get the program first*). Then it shows the matching part of this guide (A, B,
   C, D or E), waits while
   you do the one step, and **proves** it with two test requests (it must give the exact answer to a small sum -- 'What is N plus 1?' for a random N -- and a file it is
   asked to create must not appear), run the way the overnight
   timers run. Claude's file check is asked **up to three times** within the same four
   minutes, and only when the service stalls (no answer at all, nothing in its folders):
   each attempt stands on its own evidence, so a retry can never turn a failure into a pass.
4. **Only after the proof passes** is your answer recorded (in `THINKER_RECORD`, beside the
   brain) and can the overnight jobs be switched on. If the proof fails, it names **one
   cause** and the step for that cause (§12), records nothing and changes nothing. Nothing
   runs half-configured.
5. **On an upgrade,** an answer already recorded is used again and proven again; you are not
   asked twice. A Claude code already in your settings file is shown as evidence, never
   taken as your answer.
6. The credential lives in `~/.voidloom/thinker/` on Linux, macOS and WSL. *(Native
   Windows follows the same design in a later update.)*

### 9.2 Inside an application that embeds Voidloom

Some applications ship Voidloom already set up and never run the installer. They ask
you the same **a–f question** on first start and record your answer with
`voidloom thinker set <claude|codex|kimi|gemini|local|off>`, which proves the model works
before recording it, exactly as the installer does. Your sign-in stays in that
application's own saved data. Everything else in this guide applies unchanged.

### 9.3 A computer that already runs Voidloom

- The next upgrade asks the same question (once). If the brain already thought overnight
  with Claude, the old code in your settings file is shown as evidence, not as an answer.
- `voidloom thinker status` shows what is recorded, when it was proven, the credential's
  fingerprint and its next known expiry. `voidloom doctor` shows the same line.
- Credentials made earlier by hand in `~/.voidloom/thinker/` (for example a `kimi.env`
  saved with an older version of this guide, or a Codex sign-in in `codex/`) are used as
  they are, and proven before anything relies on them.
- Your everyday sign-ins (Claude Code, `~/.codex`, `~/.kimi-code`, `~/.gemini`) are **not touched**.

---

## 10. Changing your mind later

One command switches the overnight thinker:

```bash
voidloom thinker set kimi        # or: claude, codex, gemini, local, off
```

It does the one sign-in step if that model has none yet, proves it with a test question
(run the way the overnight timers run), and only then records it; the overnight jobs read
the record every night. If the proof fails, the previous answer stays exactly as it was.
The old credential is kept until you remove it. `voidloom thinker status` shows the result.

---

## 11. How the brain keeps these working

- **At setup:** two test requests with all tools off: the exact answer to a small sum ('What is N plus 1?') must come back, and a file the model is asked
  to create must not appear (for Claude, asked again -- up to three times in the same four
  minutes -- only when its service stalls with nothing in the call's folders). No proof, no
  switch-on.
- **Every week:** the weekly digest checks that the chosen credential still answers.
- **If a check fails:** the digest records an **ALERT** naming the part of this guide that
  fixes it, and `voidloom thinker status` and `voidloom doctor` show
  "unreachable N of 7 nights".
- **Claude:** a reminder about 30 days before the one-year code expires.
- **Codex:** renews itself about every 8 days, one job at a time. If it ever lapses (for
  example after a very long time unused), you run `voidloom thinker login codex` again
  (**B2**, about 2 minutes).
- **Kimi:** the key does not renew. Kimi's documentation does not state an expiry; if the
  key stops working, the check says so and you make a new one (**A2**) and save it with
  `voidloom thinker login kimi` (**A4**).

---

## 12. If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `The two pastes were different…` or `That paste had only N character(s)…` | A paste did not arrive, or a stray key was pressed | Copy the key again; run `voidloom thinker login kimi` (or `claude`) again; paste with **Ctrl+Shift+V** (or right-click) at **both** prompts |
| Kimi test says the key is **invalid or expired** | Wrong key, or it was deleted | Make a new key (A2) and save it again (A4) |
| `Not logged in` from the Codex check | The brain's Codex sign-in is missing or lapsed | Run `voidloom thinker login codex` again (B2) |
| Codex says device code sign-in is **not enabled** | The ChatGPT setting in B1 is off | Turn it on (B1), or use B2-alt (`--browser`) |
| Claude jobs report **authentication** errors | The one-year code expired or was replaced | Repeat Part C (`claude setup-token`, then `voidloom thinker login claude`) |
| The installer says the thinker was **NOT proven** | It names one **cause** (the line `cause: ...`) and the step for it | Nothing was recorded or changed. Do what the cause row below says, then run the installer again -- or answer **e) Not now** and switch on later (§10) |
| `cause: auth` (or `no-credential`) | The key, code or sign-in was refused, or there is none | The **only** cause that needs this guide's Part again: A for Kimi, B for Codex, C for Claude, E for Gemini |
| `cause: not-installed` | The chosen model's program is not on this computer (and you answered N, or no terminal could ask) | Run the command the refusal prints (the company's own, §2 *Get the program first*), or re-run the installer at a terminal and answer **y** to *Run it now?* (an AI assistant passes `--install-thinker-cli` only on your yes) |
| `cause: stall` | The model's service kept the connection open and never answered (on every attempt) | Nothing is wrong with your setup and the credential answered: run the installer again later |
| `cause: quota` | The account's usage limit is reached | Wait for the limit to reset (or check the plan), then run again; the credential is fine |
| `cause: network` | The service could not be reached | Check the internet connection (and any proxy or firewall), then run again |
| `cause: tool-broke` | A file appeared in the test call's own folder: the program ran a tool with every tool off | Do not use that program overnight as it stands: choose another letter, or **e) Not now** |
| `cause: tool-switches` | The test could not show that no tool was offered | A package or program fault, not your credential: re-download the package (or update the program) |
| `cause: probe-env` | The proof could not run the way the overnight timers run | Check that your user's systemd manager runs (`systemctl --user status`), then run again |
| `cause: unknown` (or another) | An error the brain does not recognise; its first words are shown | Run again; if it repeats, keep the installer's output for a bug report |
| You pasted a key into a chat by mistake | The key may now be stored in that chat | Delete that key on the website and make a new one |

---

## 13. Words used in this guide

- **Terminal:** the window where you type commands.
- **Credential:** anything that proves a request is from you: a key, a token or a sign-in.
- **API key:** a long secret string a website gives you to use its service from a program.
- **Token (one-year code):** like an API key, created by a sign-in command, with an expiry date.
- **Device code:** a short code you type into a website to approve a sign-in happening
  somewhere else.
- **`~/.voidloom/thinker/`:** the brain's private folder for these credentials; only
  your user account can open it.
- **`CODEX_HOME`:** tells Codex which folder to use as its home. Pointing it at the
  brain's folder keeps the brain's sign-in separate from yours.
