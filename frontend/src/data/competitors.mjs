/**
 * competitors.js — single source of truth for every /vs/* comparison
 * page, the /compare hub, AND the build-time SEO snapshots
 * (scripts/seo-prerender.mjs imports this file — keep it pure data,
 * no JSX, no React imports).
 *
 * Honesty policy (Guard 2 — marketing truth gate):
 *  - AUREM claims trace to the production codebase / subscription_tiers.py
 *    SSOT (Free $0 · Starter $9/50 tasks · Pro $19/300 tasks · Team $49).
 *  - Competitor pricing verified June 2026 against vendor pages +
 *    independent trackers — re-verify quarterly.
 *  - We say where each competitor wins. No fake numbers, no fabricated
 *    benchmarks.
 */

export const LAST_VERIFIED = "June 30, 2026";

/* ── AUREM-side row values (current shipped feature set) ─────────── */
export const AUREM = {
  pricing:
    "Flat fee — $0 Free / $9 Starter (50 tasks) / $19 Pro (300 tasks) / $49 Team per month",
  freeTier: "10 tasks/month, no credit card",
  delivery: "Direct commit to your branch or a Pull Request — your choice",
  loop:
    "Loop Mode — Plan → Execute → Verify → Security scan → Ship, with checkpoints and one-click rollback",
  security:
    "Vanguard 2.0 — 25+ secret & dangerous-code patterns, 13 deep-mode rules, AST + esbuild gates before every commit",
  review: "Maxx mode — a second model (Claude Sonnet) reviews critical changes pre-commit",
  memory: "Project Brain per-repo memory + persistent correction rules",
  resilience: "4-hop LLM fallback chain — stays online when a provider goes down",
  patSecurity: "GitHub tokens encrypted at rest (HKDF-Fernet, per-tenant keys)",
  surfaces: "Web UI (mobile-friendly) + VS Code extension + MCP server for Cursor / Claude Desktop",
  selfQa: "Auto-QA agent, regression guards and a 6-category codebase health scanner",
};

export const COMPETITORS = {
  /* ═══════════════════ DEVIN ═══════════════════ */
  devin: {
    slug: "devin",
    name: "Devin",
    title: "ORA vs Devin (2026) — honest comparison | Devin alternative",
    description:
      "ORA vs Devin, compared honestly: flat $9–$49/mo vs $20 + $2.25/ACU " +
      "metered billing, free tier vs none, direct GitHub commit or PR, 25+ pattern " +
      "pre-commit security scan, two-model Maxx review. June 2026 data.",
    canonical: "https://auremcto.com/vs/devin",
    intro:
      "Both are autonomous AI software engineers: you describe the task, they read " +
      "the codebase, write the code, and deliver it to GitHub. The differences are " +
      "how the work is priced, how it lands in your repo, and what checks run " +
      "before it gets there.",
    pickAurem: [
      "A predictable flat bill — $19/mo Pro, 300 tasks included",
      "A real free tier (10 tasks/month, no card)",
      "Direct commit to your branch or a PR — your call",
      "A 25+ pattern security scan before anything lands",
      "Maxx mode: a second model reviewing every critical change",
      "To ship from your phone or any browser",
    ],
    pickThem: [
      "Hours-long unattended sessions in a cloud VM",
      "VPC deployment inside your own cloud (Enterprise)",
      "A strictly PR-only review workflow",
      "In-VM browser automation for end-to-end checks",
      "An ACU budget model your finance team already approved",
    ],
    pricingProse: [
      "Devin meters work in Agent Compute Units. The $20/month Core plan bills " +
      "$2.25 per ACU on top of the subscription, and an ACU covers roughly 15 " +
      "minutes of active agent work — third-party estimates put moderate Core " +
      "usage at about $70–220 per month all-in. The $500/month Team plan includes " +
      "250 ACUs, then $2.00 per additional ACU. AUREM is a flat fee: Free is " +
      "10 tasks/month, Starter is $9 for 50 tasks, Pro is $19 for 300 tasks, Team " +
      "is $49 per user. Your invoice never depends on how hard a task turned out to be.",
    ],
    pricingFootnote:
      "Devin figures last verified June 2026 from devin.ai/pricing and independent " +
      "pricing trackers. Always check the vendor page — prices change.",
    rows: [
      ["Pricing model", AUREM.pricing, "ACU-metered — $20/mo + $2.25/ACU (Core), $500/mo incl. 250 ACUs (Team)"],
      ["Free tier", AUREM.freeTier, "None"],
      ["Delivery mode", AUREM.delivery, "Pull Request"],
      ["Autonomy pipeline", AUREM.loop, "Cloud VM sessions, plan + execute"],
      ["Per-repo memory", AUREM.memory, "Devin Wiki / knowledge"],
      ["Pre-commit security scan", AUREM.security, "Not advertised"],
      ["Two-model review", AUREM.review, "Single agent"],
      ["LLM resilience", AUREM.resilience, "Single vendor stack"],
      ["Token security", AUREM.patSecurity, "Not documented publicly"],
      ["Works without an IDE", AUREM.surfaces, "Web UI (desktop), Slack, IDE beta"],
      ["Cloud VM / VPC deployment", "Not offered", "Yes — SaaS or VPC (Enterprise)"],
      ["Long unattended sessions", "Minutes-scale tasks", "Hours-scale autonomous sessions"],
    ],
    faq: [
      {
        q: "Is AUREM a good Devin alternative?",
        a: "Yes, if you want predictable cost and shipped commits. AUREM is " +
           "an autonomous AI engineer with flat pricing ($0 free tier, $9 Starter " +
           "with 50 tasks, $19 Pro with 300 tasks, $49 Team) instead of Devin's " +
           "$20/month plus $2.25 per Agent Compute Unit metering. AUREM also runs " +
           "a 25+ pattern security scan and an optional second-model code review " +
           "(Maxx mode) before anything reaches your repository.",
      },
      {
        q: "How is AUREM's pricing different from Devin's?",
        a: "Devin bills by Agent Compute Units: the $20/month Core plan charges " +
           "$2.25 per ACU on top, and the $500/month Team plan includes 250 ACUs. " +
           "Moderate Core usage typically lands around $70–220/month in total. " +
           "AUREM is a flat fee with generous task caps — Pro is $19/month " +
           "for 300 tasks. A hard task costs the same as an easy one.",
      },
      {
        q: "Does AUREM commit directly to GitHub like Devin opens PRs?",
        a: "AUREM supports two delivery modes: direct commit to your branch " +
           "via the GitHub REST API for solo speed, or a Pull Request flow when " +
           "your team prefers review. Devin delivers its work as pull requests.",
      },
      {
        q: "Which is safer — AUREM or Devin?",
        a: "AUREM runs the Vanguard scanner on every change before it reaches " +
           "GitHub: 25+ patterns covering leaked secrets (AWS, GitHub, Stripe, " +
           "OpenAI keys, DB connection strings) and dangerous code (eval, " +
           "shell=True, SQL string formatting, unsafe innerHTML), plus Python AST " +
           "and esbuild syntax validation. On Pro and Team, Maxx mode adds a " +
           "second-model review by Claude Sonnet before commit. Devin does not " +
           "advertise an equivalent pre-commit security gate.",
      },
      {
        q: "When is Devin the better choice?",
        a: "Devin is a strong fit for long-running, fully cloud-hosted sessions " +
           "in its own VM, organisations that want VPC deployment, and teams " +
           "already standardised on PR-only review with ACU budgets. If those " +
           "matter more to you than flat pricing, a free tier, mobile access, or " +
           "pre-commit security scanning, choose Devin.",
      },
    ],
  },

  /* ═══════════════════ CURSOR ═══════════════════ */
  cursor: {
    slug: "cursor",
    name: "Cursor",
    title: "ORA vs Cursor (2026) — honest comparison | Cursor alternative",
    description:
      "ORA vs Cursor, compared honestly: autonomous AI engineer that ships " +
      "commits vs an AI-first IDE you drive. Flat $9–$49/mo vs $20–$200/mo with " +
      "usage limits. Pre-commit security scan, works from mobile. June 2026 data.",
    canonical: "https://auremcto.com/vs/cursor",
    intro:
      "These are different species. Cursor is an AI-first code editor — you sit " +
      "in the IDE and steer every change. AUREM is an autonomous engineer — " +
      "you assign a task from any browser and it plans, codes, verifies and ships " +
      "to GitHub on its own. Many developers use both; here is where each one earns " +
      "its keep.",
    pickAurem: [
      "Tasks shipped end-to-end while you do something else",
      "Working from a phone, tablet or any browser — no IDE install",
      "A pre-commit security scan on every change (Vanguard 2.0)",
      "Flat pricing that ignores how hard the task was",
      "Checkpoints + one-click rollback on every autonomous run",
      "A free tier without a credit card",
    ],
    pickThem: [
      "Hands-on, keystroke-level control inside a desktop IDE",
      "Best-in-class tab completion while you write code yourself",
      "Choosing a specific frontier model per request",
      "Large extension/plugin ecosystem (VS Code fork)",
      "In-editor agent that you supervise line by line",
    ],
    pricingProse: [
      "Cursor's 2026 line-up is Hobby (free, limited), Pro $20/month, Pro+ " +
      "$60/month and Ultra $200/month, plus Teams at $40 per user — each tier " +
      "carries usage quotas, and heavy agent use can push you up the ladder. " +
      "AUREM is a flat fee: Free is 10 tasks/month, Starter is $9 for 50 " +
      "tasks, Pro is $19 for 300 tasks, Team is $49 per user. You are billed for " +
      "outcomes (tasks), not for tokens or compute minutes.",
      "They also compose: AUREM ships an MCP server, so you can call ORA from " +
      "inside Cursor and keep the IDE you love while delegating whole tasks.",
    ],
    pricingFootnote:
      "Cursor figures last verified June 2026 from cursor.com/pricing and " +
      "independent trackers. Always check the vendor page — prices change.",
    rows: [
      ["Product type", "Autonomous AI engineer — assign a task, get a commit/PR", "AI-first IDE — you write and steer code with AI assistance"],
      ["Pricing model", AUREM.pricing, "Hobby free · Pro $20/mo · Pro+ $60/mo · Ultra $200/mo · Teams $40/user, with usage quotas"],
      ["Free tier", AUREM.freeTier, "Hobby — limited completions and agent requests"],
      ["Delivery mode", AUREM.delivery, "You commit from the editor; agent edits stay local until you push"],
      ["Works without an IDE", AUREM.surfaces, "No — the desktop IDE is the product"],
      ["Pre-commit security scan", AUREM.security, "Not advertised"],
      ["Autonomy pipeline", AUREM.loop, "In-editor Agent mode — you supervise as it edits"],
      ["Per-repo memory", AUREM.memory, "Codebase indexing + .cursorrules files"],
      ["Two-model review", AUREM.review, "Single model per request (you choose which)"],
      ["Mobile access", "Yes — full web UI works on mobile", "No"],
      ["Use them together", "Yes — ORA's MCP server plugs into Cursor", "—"],
    ],
    faq: [
      {
        q: "Is AUREM a Cursor alternative or a different kind of tool?",
        a: "Different kind. Cursor is an AI-powered IDE where you do the driving; " +
           "AUREM is an autonomous AI engineer that takes a task description, " +
           "reads your GitHub repo, writes the code, verifies it, security-scans " +
           "it and ships a commit or PR without you touching an editor. If you " +
           "want to delegate whole tasks rather than accelerate your own typing, " +
           "AUREM is the alternative.",
      },
      {
        q: "How does AUREM's pricing compare to Cursor's?",
        a: "Cursor runs $20 (Pro) to $200 (Ultra) per month with usage quotas per " +
           "tier; Teams is $40 per user. AUREM is flat: $0 free tier, $9 " +
           "Starter with 50 tasks, $19 Pro with 300 tasks, $49 Team. AUREM bills " +
           "per task outcome, so a complex task doesn't cost more than a simple one.",
      },
      {
        q: "Can I use AUREM together with Cursor?",
        a: "Yes. AUREM ships an MCP server, so you can register ORA as a tool " +
           "inside Cursor (or Claude Desktop) and delegate full tasks from the " +
           "IDE while keeping Cursor for hands-on editing.",
      },
      {
        q: "When is Cursor the better choice?",
        a: "When you want to stay in the loop for every keystroke: elite tab " +
           "completion, in-editor chat, per-request model choice and a familiar " +
           "VS Code-style environment. If your workflow is 'I write code all day " +
           "and want it faster', Cursor wins. If it's 'I want this task done and " +
           "shipped while I focus elsewhere', AUREM wins.",
      },
    ],
  },

  /* ═══════════════════ GITHUB COPILOT ═══════════════════ */
  "github-copilot": {
    slug: "github-copilot",
    name: "GitHub Copilot",
    title: "ORA vs GitHub Copilot (2026) — honest comparison | Copilot alternative",
    description:
      "ORA vs GitHub Copilot, compared honestly: autonomous task shipping vs " +
      "in-IDE assistance and premium-request metering. Flat $9–$49/mo, pre-commit " +
      "security scan, mobile access. June 2026 data.",
    canonical: "https://auremcto.com/vs/github-copilot",
    intro:
      "Copilot is the default AI assistant inside your IDE and GitHub — " +
      "completions, chat, and a coding agent that opens PRs from issues. ORA " +
      "is a standalone autonomous engineer with its own verify-and-ship " +
      "pipeline. The real comparison is metered requests vs flat tasks, and " +
      "assistance vs delegation.",
    pickAurem: [
      "Whole tasks delegated and shipped, not autocomplete",
      "A verify pipeline: syntax gates, security scan, optional second-model review",
      "Flat task pricing — no premium-request metering",
      "Ship from any browser or phone, no IDE required",
      "Checkpoints + rollback on every autonomous run",
      "Per-repo Project Brain memory + your standing correction rules",
    ],
    pickThem: [
      "The cheapest entry point ($10/mo Pro) for in-IDE completions",
      "Native, first-party GitHub org / enterprise policy controls",
      "Ubiquity — VS Code, JetBrains, Visual Studio, Neovim",
      "Copilot coding agent tightly wired to GitHub Issues",
      "Enterprise compliance & IP indemnity from Microsoft/GitHub",
    ],
    pricingProse: [
      "Copilot's 2026 plans: Free (limited completions/chat), Pro $10/month, " +
      "Pro+ $39/month with 1,500 premium requests included then $0.04 per extra " +
      "request, Business $19/user and Enterprise $39/user. Agent-mode work " +
      "consumes premium requests, so heavy agent use is effectively metered. " +
      "AUREM is a flat fee per month — Free 10 tasks, Starter $9/50 tasks, " +
      "Pro $19/300 tasks, Team $49 — regardless of how many model calls a task " +
      "takes under the hood.",
    ],
    pricingFootnote:
      "Copilot figures last verified June 2026 from github.com/features/copilot/plans. " +
      "Always check the vendor page — prices change.",
    rows: [
      ["Product type", "Autonomous AI engineer — assign a task, get a commit/PR", "In-IDE assistant + coding agent attached to GitHub"],
      ["Pricing model", AUREM.pricing, "Free · Pro $10/mo · Pro+ $39/mo (1,500 premium requests, then $0.04/req) · Business $19/user · Enterprise $39/user"],
      ["Free tier", AUREM.freeTier, "Yes — limited completions and chats"],
      ["Delivery mode", AUREM.delivery, "Completions in-editor; coding agent opens PRs"],
      ["Pre-commit security scan", AUREM.security, "Separate product (GitHub Advanced Security), not part of Copilot"],
      ["Autonomy pipeline", AUREM.loop, "Agent mode / assign-issue-to-Copilot, PR-based"],
      ["Per-repo memory", AUREM.memory, "Instructions files + repo custom instructions"],
      ["Two-model review", AUREM.review, "Single model per request"],
      ["LLM resilience", AUREM.resilience, "GitHub-hosted model pool"],
      ["Works without an IDE", AUREM.surfaces, "github.com chat + IDE; agent via GitHub UI"],
      ["Mobile access", "Yes — full web UI works on mobile", "GitHub mobile app chat (limited)"],
    ],
    faq: [
      {
        q: "Is AUREM an alternative to GitHub Copilot?",
        a: "For autocomplete, no — Copilot owns in-IDE completions. For getting " +
           "whole tasks done and shipped, yes: AUREM reads your repo, writes " +
           "the code, runs syntax gates and a 25+ pattern security scan, and " +
           "delivers a commit or PR. Many developers keep Copilot for typing " +
           "speed and use AUREM for delegated tasks.",
      },
      {
        q: "How does AUREM's pricing compare to Copilot's?",
        a: "Copilot Pro is $10/month and Pro+ is $39/month with 1,500 premium " +
           "requests included, then $0.04 per extra request — agent work draws " +
           "from that meter. AUREM charges flat per month for task outcomes: " +
           "$0 free, $9 Starter (50 tasks), $19 Pro (300 tasks), $49 Team. No " +
           "per-request metering.",
      },
      {
        q: "Does Copilot run a security scan like Vanguard?",
        a: "Copilot itself does not gate its output behind a security scan; " +
           "GitHub sells scanning separately (GitHub Advanced Security / code " +
           "scanning). AUREM runs Vanguard 2.0 on every change before it " +
           "reaches your repository — 25+ secret and dangerous-code patterns, " +
           "deep-mode rules, plus AST and esbuild syntax gates.",
      },
      {
        q: "When is GitHub Copilot the better choice?",
        a: "If you mostly want faster typing inside your IDE, first-party GitHub " +
           "enterprise controls, or the $10 entry price, Copilot is the sensible " +
           "default. If you want an engineer you can hand tasks to from anywhere " +
           "— including your phone — with a verification pipeline before every " +
           "commit, that's AUREM.",
      },
    ],
  },

  /* ═══════════════════ REPLIT AGENT ═══════════════════ */
  "replit-agent": {
    slug: "replit-agent",
    name: "Replit Agent",
    title: "ORA vs Replit Agent (2026) — honest comparison | Replit alternative",
    description:
      "ORA vs Replit Agent, compared honestly: shipping to YOUR GitHub repo " +
      "vs building inside Replit's cloud workspace. Flat $9–$49/mo vs $25/mo + " +
      "effort-based billing. June 2026 data.",
    canonical: "https://auremcto.com/vs/replit-agent",
    intro:
      "Replit Agent builds and hosts apps inside Replit's own cloud workspace — " +
      "brilliant for zero-setup prototypes. AUREM works on the repositories " +
      "you already have on GitHub, and ships commits through a verify + security " +
      "pipeline. Where your code lives is the real fork in this road.",
    pickAurem: [
      "Work on your existing GitHub repos, not a hosted workspace",
      "Flat task pricing — no effort-based variability",
      "A pre-commit security scan + optional second-model review",
      "Direct commit or PR into your own branch protection flow",
      "Checkpoints + one-click rollback per run",
      "Per-repo memory that persists across tasks",
    ],
    pickThem: [
      "Zero-setup full-stack prototyping with instant hosting",
      "One place for code, database, deployments and domains",
      "Building from the Replit mobile app",
      "Education / teaching environments",
      "Going idea → live URL in a single session",
    ],
    pricingProse: [
      "Replit Core is $25/month ($20 billed annually) and includes monthly " +
      "credits; Agent work is billed effort-based — the cost of a task scales " +
      "with how much compute and complexity it needed (rollout completed for " +
      "existing subscribers from July 1, 2026). That makes heavy or gnarly tasks " +
      "cost more. AUREM bills flat per month for task outcomes: $0 free, $9 " +
      "Starter (50 tasks), $19 Pro (300 tasks), $49 Team — a hard task costs the " +
      "same as an easy one.",
    ],
    pricingFootnote:
      "Replit figures last verified June 2026 from replit.com/pricing and " +
      "docs.replit.com/billing. Always check the vendor page — prices change.",
    rows: [
      ["Where your code lives", "Your GitHub repos — AUREM commits to them", "Replit cloud workspace (GitHub import/export available)"],
      ["Pricing model", AUREM.pricing, "Core $25/mo ($20 annual) + effort-based Agent billing per task"],
      ["Free tier", AUREM.freeTier, "Starter — limited Agent access"],
      ["Cost predictability", "Flat — task difficulty never changes the bill", "Variable — effort-based billing scales with task complexity"],
      ["Delivery mode", AUREM.delivery, "Edits the workspace app; deploys on Replit hosting"],
      ["Pre-commit security scan", AUREM.security, "Not advertised as a pre-commit gate"],
      ["Autonomy pipeline", AUREM.loop, "Agent plans + builds + deploys in-workspace"],
      ["Hosting included", "No — your infra, your choice", "Yes — Replit deployments, DBs, domains"],
      ["Per-repo memory", AUREM.memory, "Workspace context"],
      ["Mobile access", "Yes — full web UI works on mobile", "Yes — Replit mobile app"],
    ],
    faq: [
      {
        q: "Is AUREM an alternative to Replit Agent?",
        a: "Yes, when your code lives on GitHub. Replit Agent shines when you " +
           "want an idea hosted at a live URL in one sitting, inside Replit's " +
           "workspace. AUREM is for the repos you already run in production: " +
           "it reads your GitHub project, writes the change, security-scans it " +
           "and ships a commit or PR to your branch.",
      },
      {
        q: "How does effort-based billing compare to AUREM's flat tasks?",
        a: "Replit bills Agent work by effort — complex tasks consume more and " +
           "cost more, which is flexible but hard to budget. AUREM charges " +
           "flat: $19/month Pro includes 300 tasks whether each one took 2 " +
           "minutes or 20. If you need cost certainty, flat wins.",
      },
      {
        q: "Can AUREM host my app like Replit does?",
        a: "No — and that's deliberate. AUREM ships code into your repository " +
           "and your existing deploy pipeline (Vercel, your CI, wherever). " +
           "Replit bundles hosting, databases and domains in one workspace, " +
           "which is excellent for prototypes and personal apps.",
      },
      {
        q: "When is Replit Agent the better choice?",
        a: "Brand-new projects you want live TODAY with zero setup, teaching " +
           "environments, and building from the Replit mobile app. If the " +
           "destination is a fresh hosted prototype rather than commits into an " +
           "existing GitHub codebase, choose Replit.",
      },
    ],
  },

  /* ═══════════════════ WINDSURF ═══════════════════ */
  windsurf: {
    slug: "windsurf",
    name: "Windsurf",
    title: "ORA vs Windsurf (2026) — honest comparison | Windsurf alternative",
    description:
      "ORA vs Windsurf, compared honestly: autonomous task shipping to GitHub " +
      "vs an agentic IDE (Cascade). Flat $9–$49/mo vs $20/mo quota plans. " +
      "Pre-commit security scan, mobile access. June 2026 data.",
    canonical: "https://auremcto.com/vs/windsurf",
    intro:
      "Windsurf is an agentic IDE — its Cascade agent works with you inside the " +
      "editor. AUREM is an autonomous engineer that takes tasks from any " +
      "browser and ships verified commits to GitHub. Like Cursor, this is " +
      "assistance vs delegation — with a pricing model that changed in March 2026.",
    pickAurem: [
      "Delegate whole tasks and get verified commits back",
      "No IDE install — web, mobile, VS Code extension, MCP",
      "Vanguard 2.0 security scan before every commit",
      "Flat task pricing immune to quota changes",
      "Checkpoints + one-click rollback per autonomous run",
      "A free tier without a credit card",
    ],
    pickThem: [
      "A polished agentic IDE experience (Cascade) you supervise",
      "In-editor flows, previews and terminal awareness",
      "Working primarily as a hands-on editor user",
      "Team plans standardised on a per-seat IDE",
    ],
    pricingProse: [
      "Windsurf reworked pricing in March 2026: the credit pool gave way to " +
      "daily/weekly quotas — Free, Pro $20/month, Teams $40/user, with a $200 " +
      "Max tier reported by several trackers. Quota models can shift under your " +
      "feet mid-quarter. AUREM stays flat: $0 free, $9 Starter (50 tasks), " +
      "$19 Pro (300 tasks), $49 Team — billed on task outcomes, not quotas.",
    ],
    pricingFootnote:
      "Windsurf figures last verified June 2026 across vendor pages and " +
      "independent trackers (post-March-2026 quota model). Always check the " +
      "vendor page — prices change.",
    rows: [
      ["Product type", "Autonomous AI engineer — assign a task, get a commit/PR", "Agentic IDE — Cascade agent works with you in-editor"],
      ["Pricing model", AUREM.pricing, "Free · Pro $20/mo · Teams $40/user (quota-based since Mar 2026)"],
      ["Free tier", AUREM.freeTier, "Yes — limited quota"],
      ["Delivery mode", AUREM.delivery, "Edits in your local editor; you commit and push"],
      ["Works without an IDE", AUREM.surfaces, "No — the IDE is the product"],
      ["Pre-commit security scan", AUREM.security, "Not advertised"],
      ["Autonomy pipeline", AUREM.loop, "Cascade in-editor agent, supervised"],
      ["Per-repo memory", AUREM.memory, "Workspace context + rules"],
      ["Two-model review", AUREM.review, "Single model per request"],
      ["Mobile access", "Yes — full web UI works on mobile", "No"],
    ],
    faq: [
      {
        q: "Is AUREM a Windsurf alternative?",
        a: "They solve different moments. Windsurf's Cascade makes you faster " +
           "inside the editor; AUREM removes the editor from the loop for " +
           "delegated tasks — it plans, codes, verifies, security-scans and " +
           "ships to GitHub on its own. If you're choosing where a whole task " +
           "gets done, AUREM is the alternative.",
      },
      {
        q: "How does AUREM's pricing compare to Windsurf's?",
        a: "Windsurf moved to quota-based plans in March 2026 — Free, $20/month " +
           "Pro, $40/user Teams, with daily/weekly usage quotas. AUREM is " +
           "flat per month on task outcomes: $0 free, $9 Starter (50 tasks), " +
           "$19 Pro (300 tasks), $49 Team. No quotas that reset under you.",
      },
      {
        q: "Does Windsurf verify or security-scan its changes?",
        a: "Windsurf does not advertise a pre-commit security gate. AUREM " +
           "runs Vanguard 2.0 (25+ secret and dangerous-code patterns plus " +
           "deep-mode rules) and syntax gates on every change, and Maxx mode " +
           "can add a second-model review before commit.",
      },
      {
        q: "When is Windsurf the better choice?",
        a: "If you live in an editor and want the smoothest supervised agent " +
           "experience there, Windsurf is a strong pick. If you want to hand " +
           "off tasks from anywhere and receive verified commits, choose AUREM.",
      },
    ],
  },
};

export const ALL_SLUGS = ["devin", "cursor", "github-copilot", "replit-agent", "windsurf"];

export const COMPARE_HUB = {
  title: "ORA vs Devin, Cursor, Copilot, Replit & Windsurf (2026) — honest comparisons",
  description:
    "How AUREM compares to Devin, Cursor, GitHub Copilot, Replit Agent and " +
    "Windsurf in 2026 — pricing, delivery mode, security gates and where each " +
    "tool wins. Verified June 2026, updated quarterly.",
  canonical: "https://auremcto.com/compare",
};
