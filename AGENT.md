# AGENT.md — Rules for the coding agent (WaifuBot)

Read this file at the start of every session. The full specification is in `SPEC.md`; read the sections relevant to the current sprint. If the two ever disagree, stop and ask.

## Working protocol

1. At the start of a session: inspect the repository, `git status`, Python version, free disk and RAM. Never delete, reset, or overwrite existing work. Read a tracked file before editing it.
2. Work on **one sprint at a time**. The user names the current sprint. Do not start the next one.
3. At the end of the sprint, send the short report (template below) and **stop until the user explicitly approves the next sprint**.
4. Report only what you actually ran: the real command and the real result. If something was not tested, say "not tested". Never write "done" or "working" for untested behavior.
5. Ask only questions that block the current sprint, and only after checking the repo and config for the answer.
6. If the architecture needs a major change, explain it and wait for approval before implementing it.
7. Anything marked **[VERIFY]** in `SPEC.md` is an unverified external fact. Check it against the official documentation or a real test before relying on it, and report what you found.

## Hard prohibitions

- **Provider access.** Never run, import, or execute the Playwright proof-of-concept, and never open a provider page, except in the live steps of Sprint 0 and Sprint 2 after the user has explicitly approved that sprint. Before any live step, tell the user that a browser will open and a real generation request will be sent. Never at startup, in tests, in CI, or during repository inspection.
- **No circumvention.** On a CAPTCHA, challenge, explicit rate limit, or access-denied page: stop the job, record `blocked_by_provider`, and report. Do not add stealth flags, bypass logic, proxy rotation, or extra retries. Do not exceed the rate and scope recorded in `docs/provider-permission.md`.
- **No network in tests.** Tests use the mock provider and must not reach any external site or API.
- **No secrets in Git.** Never commit `.env`, tokens, cookies, browser profiles, databases, or generated images. Never log them.
- **Bounded everything.** No unbounded loops, retries, generations, scheduled jobs, or disk growth. Every limit is a configuration value with a hard maximum.
- **No invention.** Do not invent selectors, provider options, seeds, or image metadata. If the page or API does not provide it, store nothing.
- **Scope.** Do not build chat, memory, payments, NSFW, local diffusion models, public multi-user access, or Ollama integration. They are out of scope until the user approves them in writing.
- **Content safety.** SFW only. Every character must be explicitly adult. Do not weaken, bypass, or make optional the content checks in `SPEC.md` section 8.

## Amendment log

- **2026-10-16 (user decision):** the user's working proof-of-concept automation profile is approved and adopted: persistent browser profile, `slow_mo`, human-paced character-by-character typing with small pauses, and the Chromium argument `--disable-blink-features=AutomationControlled`. Background: controlled Sprint 0 spike runs without this profile were blocked by the provider at the generate step (plain `fill()` first, then human-paced typing alone), while the user's PoC with this profile generates successfully on the same machine; the user explicitly directed the agent to use that code. This exception is limited to that automation profile. Solving CAPTCHAs, bypassing challenge or access-denied pages, proxy rotation, and extra retries on `blocked_by_provider` remain prohibited. Recorded in `docs/provider-permission.md`.

## Sprint report template

```text
Sprint N — Report
1. Scope completed
2. Files changed
3. Tests run: command and actual result
4. Tests not run, and why
5. [VERIFY] items checked, and what was found
6. Risks and open issues
7. Decisions needed from the user
Status: waiting for explicit approval to continue.
```
