# ppt-rubric-review

**Handmade PPT Quality Gate & De-AI-Flavor Skill (v2.3)** — An agent skill and zero-dependency CLI for evaluating, de-AI-ing, and iteratively revising `.pptx` presentations until they reach true handmade quality.

Works across **Claude Code**, **Antigravity**, **Codex**, and **DeepSeek (`dsh`)** using only the Python 3 standard library.

---

## Why This Skill Exists

Most AI-generated slide decks fail in one of five recognizable ways:
1. **Document-slicer slop**: baked full-bleed PNG backgrounds, 100% Blank layouts, CSS quarter-point font sizes (`11.25pt` / `14.25pt`), full prose dumped into speaker notes, mismatched hand-typed page numbers, and giant 12+ row tables.
2. **Polished cookie-cutter templates**: clean typography, yet zero real domain visuals (substituted with toy 2–3 bar charts and generic 4-box flows), mechanical bottom red/accent bars stamped onto >60% of slides, verbatim boilerplate copied across slides, leaked AI meta-text, or chart-vs-text numerical contradictions.
3. **Flashy card-buzzword decks**: endless equal-width 3–4 column rounded cards, generic linear icons, neon gradients, and empty slogan prose.
4. **Non-human citations, tracking links & defensive annotations**: raw URLs with `?utm_source=chatgpt.com` / `grounding-api-redirect` (in text or embedded `.rels` hyperlinks), leaked Markdown/search tags (`[title](url)`, `【1†source】`), internal working-file names cited as formal footers (`draft_v3.md`, `内部底表合并版`), robotic per-slide pseudo-source stamps, or over-defensive disclaimers ("for illustrative purposes only", "留档可查 / 避免口径争议").
5. **Stiff AI coinages, over-compressed jargon & unreadable prose**: awkward pseudo-executive coinages (`竞合窗口`, `领跑势能`, `前置化解`, `硬任务已压实`, `共享基座`, `队伍底盘`), non-standard four-character abbreviations that force listeners to pause and guess, and stacked multi-clause modifiers that fail a simple read-aloud check.

`ppt-rubric-review` encodes the **Handmade PPT Rubric v2.3** into both **pre-creation guardrails (P1–P7)** and a **post-creation forensic scan + visual review + revision loop**.

---

## v2.3 Rubric Architecture (Dual-Axis Scoring)

Every presentation is scored on **two independent axes (0–100)**:
- **Overall Handmade Score (0–100)** — 23 universal criteria (88 wt) + 3 active genre-specific criteria (12 wt) = **26 active items / 100 wt**.
- **Anti-AI-Flavor Index (0–100)** — 11 high-discrimination core items (`★`, 41 wt) that block polished empty shells from gaming the score.

| Layer | Components | Role |
|---|---|---|
| **1. Delivery Vetoes** | `V1`–`V4` | Immediate **D-tier block** on fabricated/conflicting data or fake URLs, unreadable damage, missing deliverables / leaked AI meta-text or raw search syntax, or ≥2 hard process red flags |
| **2. AI-Flavor Signals** | `S1`–`S6` (Process) + `S7`–`S12` (Visual/Content/Citations/Language) | Structured `0 / 1 / 2` red-flag fingerprints covering all five Bad AI archetypes (including `S11` non-human links & defensive notes, and `S12` stiff AI coinages & over-compressed jargon) |
| **3. Universal Core** | 23 items (`A`–`G`, 13 whole-deck + 10 per-page, 88 wt) | Claims & granularity, narrative & navigation highlight, layout & pacing, natural fluent prose (`D2`) & notes split, visual motif, first-hand artifacts & custom diagrams, natural citations (`F4`), native PPT craft |
| **4. Genre Modules** | `H1`–`H9` (auto-activates 3 items = 12 wt) | `01/02` Project & Award Defense (`H1`–`H3`) · `03` Academic Talk (`H4`–`H6`) · `04` Teaching Lecture (`H7`–`H9`) · `other` (rescales cleanly from 88 wt) |

### Tier Thresholds

| Tier | Overall | Anti-AI | Hard Constraints |
|---|---:|---:|---|
| **S 级｜优秀手工标杆** | ≥ 88 | ≥ 85 | `V1–V4` all pass · `S1–S12` red flags = 0 · whole-deck 0s = 0 · zero-score slides ≤ 10% |
| **A 级｜良好手工级** | ≥ 75 | ≥ 72 | `V1–V4` all pass · `S1–S6` red flags = 0 · `S7–S12` red flags ≤ 1 |
| **B 级｜混合·短板/AI味** | ≥ 55 | ≥ 40 | Passes vetoes but falls below A-tier |
| **C 级｜明显 AI 味/重做** | < 55 | or < 40 | Severe AI flavor or structural breakdown |
| **D 级｜阻断交付** | Any | Any | Any `V1–V4` veto triggered |

---

## Installation

### Option 1: One-Line Installer (Recommended)

```bash
git clone https://github.com/QingyaoAi/ppt-rubric-review.git
cd ppt-rubric-review

# Install to Claude Code (~/.claude/skills/ppt-rubric-review)
./install.sh --claude

# Or install once in ~/.claude/skills and symlink to Antigravity, Codex, and DeepSeek
./install.sh --all
```

### Option 2: `npx skills` CLI

```bash
npx skills add QingyaoAi/ppt-rubric-review
```

### Option 3: Download Release ZIP

Download `ppt-rubric-review-v2.3.zip` from [Releases](https://github.com/QingyaoAi/ppt-rubric-review/releases) and extract `ppt-rubric-review/` into your agent's skill folder:
- **Claude Code**: `~/.claude/skills/ppt-rubric-review`
- **Antigravity**: `~/.gemini/config/skills/ppt-rubric-review`
- **Codex**: `~/.agents/skills/ppt-rubric-review`
- **DeepSeek (`dsh`)**: `~/.dsh/skills/ppt-rubric-review`

---

## CLI Quick Start (Zero Third-Party Dependencies)

Requires only **Python 3.9+** standard library (`zipfile`, `xml.etree.ElementTree`, `json`, `re`):

```bash
SKILL=~/.claude/skills/ppt-rubric-review

# 1. Inspect v2.3 criteria & anchors for a genre (01 | 02 | 03 | 04 | other)
python3 "$SKILL/scripts/rubric.py" guide --genre 01

# 2. Run instant forensic XML pre-scan on any .pptx (checks S1-S12 + embedded hyperlinks + stiff coinages)
python3 "$SKILL/scripts/rubric.py" scan /path/to/deck.pptx -o review/scan-v1.json

# 3. Initialize an evidence-backed v2.3 review record
python3 "$SKILL/scripts/rubric.py" init \
  --deck "draft-v1.pptx" --genre 01 --pages 28 --mode full --target 88 \
  -o review/round-01.json

# 4. Validate, score (Overall + Anti-AI + A-H + Tier), and emit prioritized fix_queue
python3 "$SKILL/scripts/rubric.py" score review/round-01.json -o review/score-01.json

# 5. Compare two revision rounds
python3 "$SKILL/scripts/rubric.py" diff review/round-01.json review/round-02.json
```

---

## Repository Structure

```text
.
├── README.md
├── LICENSE
├── install.sh                                       # Cross-agent installer
└── skills/
    └── ppt-rubric-review/
        ├── SKILL.md                                 # Agent runbook (pre-creation rules P1-P7 + post-creation loop)
        ├── scripts/
        │   └── rubric.py                            # Zero-dependency CLI (guide / scan / init / score / diff)
        └── references/
            ├── rubric-v2.3.json                     # Machine-readable v2.3 rubric definition
            ├── rubric-v2.3.md                       # Complete scoring anchors & anti-false-positive rules
            ├── review-workflow.md                   # CLI guide & de-AI-ing fix playbook
            └── 手工PPT评价量表_v2.3_模板.xlsx        # Blank reusable 6-sheet Excel rubric (1,041 classic formulas)
```

---

## License

MIT License — see [LICENSE](./LICENSE).
