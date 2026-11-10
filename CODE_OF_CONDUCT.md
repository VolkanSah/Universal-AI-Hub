# Code of Conduct

## The Short Version

Build things that work. Understand what you ship. Don't use this to harm people.

---

## What This Project Stands For

The MCP ecosystem is full of servers built for the demo, not for production. Hardcoded keys, zero sandboxing, `os.environ` everywhere — and nobody reads the code before forking it.

This project exists as a counter-example. The goal is not to be the most popular MCP server. The goal is to be one that people can actually trust — and learn from.

That shapes what kind of community belongs here.

---

## What We Expect

**Understand before you contribute.**  
Read the architecture. Understand why `app/*` can't touch `fundaments/`. Understand why there are two databases. If something seems over-engineered, ask first — there's usually a reason.

**Security is not optional.**  
Don't submit PRs that weaken the sandbox, expose secrets through new code paths, or add convenience features that bypass the Guardian pattern. If you think a security boundary is wrong, open an issue and argue the case — don't just route around it.

**AI is a tool, not a co-author.**  
Using AI to help write code, docs, or tests is fine. Submitting AI-generated code you don't understand is not. You are responsible for what you put your name on. If you can't explain it in a code review, it doesn't belong here.

**Be direct, not brutal.**  
Bad code should be called bad code. Vague encouragement helps nobody. But there's a difference between "this approach has a security problem because..." and being dismissive. Explain the problem. Propose a fix. Move on.

**No hype, no growth-hacking.**  
Don't open issues asking to add integrations just because they're trending. Don't submit PRs that add features nobody asked for to pad a portfolio. This is a security-focused project — new features get scrutinized for attack surface, not applauded for volume.

---

## What We Don't Tolerate

- Using this project to harm, surveil, or exploit others — the [ESOL v1.1](ESOL) license makes this automatic grounds for termination of all rights
- Submitting intentionally malicious code, backdoors, or obfuscated logic
- Harassment, discrimination, or personal attacks — in issues, PRs, or discussions
- Deliberate misrepresentation of how the code works in documentation or comments

---

## On Contributing

Before opening a PR:

1. Open an issue first for anything non-trivial — discuss the approach before writing the code
2. Make sure your change doesn't break the sandbox rules (see [SECURITY.md](SECURITY.md))
3. Write code you'd be comfortable explaining line by line in a review
4. If you add a provider, add the dummy class pattern — future contributors will thank you

Small, focused PRs get reviewed faster than large ones. A PR that fixes one thing well is worth more than one that does five things loosely.

---

## Enforcement

Violations of the ethical use constraints in ESOL v1.1 are reported to the author directly and, where appropriate, to relevant authorities. There's no three-strikes policy for deliberate misuse.

For everything else — bad behavior in the community, violations of the conduct guidelines above — the maintainer will handle it case by case. The goal is to keep the project useful and trustworthy, not to maintain a leaderboard of infractions.

---

*This project was built by someone who wanted to understand how things actually work — not how they look in a README. That's the kind of contributor who belongs here.*
