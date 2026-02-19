# AI-Assisted Template-Based Workflow Patterns

*A practical guide to organizing technical documentation projects with AI collaboration, template inheritance, and quality automation.*

---

## Executive Summary

This document describes proven patterns for managing complex technical documentation projects using AI assistance, template repositories, and automated quality checks.

**Key Benefits:**
- **Consistency**: Shared infrastructure across multiple specifications/projects
- **Quality**: Automated checks enforced at every step
- **Velocity**: AI handles routine tasks, humans focus on technical decisions (5-10x speedup measured)
- **Maintainability**: Clear separation of content vs. infrastructure
- **Scalability**: Template improvements benefit all derived projects

**Real-World Results:**
- 23 hours of spec work → 3.5 hours with AI (6.5x average)
- Template repositories supporting 20+ active projects
- Quality maintained (all automated checks pass)
- Developer onboarding: days → hours

**Audience:** Technical writers, specification editors, documentation architects, AI tool users, engineering managers

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Repository Structure](#repository-structure)
3. [The Template Pattern](#the-template-pattern)
4. [AI Agent Configuration](#ai-agent-configuration)
5. [Workflow Stages](#workflow-stages)
6. [Quality Automation](#quality-automation)
7. [Session Management](#session-management)
8. [Advanced Patterns](#advanced-patterns)
9. [Lessons Learned](#lessons-learned)
10. [Implementation Guide](#implementation-guide)

---

## Architecture Overview

### The Three-Tier Model

```
┌─────────────────────────────────────────────────────────────┐
│                    Template Repository                       │
│  (doc-template / spec-template)                             │
│                                                              │
│  • Build system (Makefile, Pandoc, LaTeX)                  │
│  • AI rules (.cursor/rules/)                                │
│  • Quality tools (spell-check, link validation)            │
│  • Document infrastructure                                  │
└─────────────────────┬───────────────────────────────────────┘
                      │
         ┌────────────┴────────────┐
         │                         │
         ▼                         ▼
┌─────────────────┐       ┌─────────────────┐
│  Project A      │       │  Project B      │
│  (spec-xyz)     │       │  (spec-abc)     │
│                 │       │                 │
│  • Content      │       │  • Content      │
│  • Diagrams     │       │  • Diagrams     │
│  • Examples     │       │  • Examples     │
└─────────────────┘       └─────────────────┘
```

### Key Principles

1. **Single Source of Truth**: Infrastructure lives in template, content in projects
2. **Unidirectional Flow**: Infrastructure changes flow from template → projects
3. **AI-Native Design**: Rules and structure optimized for AI understanding
4. **Quality Gates**: Automated checks at every stage
5. **Human Authority**: AI suggests, human decides on technical matters

---

## Repository Structure

### Template Repository Layout

```
doc-template/
├── .cursor/
│   └── rules/                    # AI agent instructions
│       ├── 00-session-startup.md    # Greeting, context loading
│       ├── 01-python-environment.md # Tool execution patterns
│       ├── 02-document-conventions.md # Writing standards
│       ├── 03-writing-quality.md    # Quality requirements
│       ├── 04-sister-specs.md       # Cross-reference patterns
│       ├── 05-import-mode.md        # Migration workflows
│       ├── 06-template-workflow.md  # Sync procedures
│       ├── 07-faq-management.md     # Protected content rules
│       └── 99-project-overrides.md  # Project-specific customization
│
├── build-system/
│   ├── spec/
│   │   ├── latex-styling.tex        # PDF styling
│   │   ├── specification.yaml       # Pandoc defaults
│   │   └── filters/*.lua            # Document transformation
│   ├── presentation/                # Slide generation
│   └── docx/                        # Word export
│
├── tools/
│   ├── spell-check.py               # Dictionary-based checking
│   ├── validate-links.py            # Internal link verification
│   ├── extract-requirements.py     # Normative text extraction
│   ├── generate-quality-dashboard.py # Metrics reporting
│   └── requirements.txt             # Python dependencies
│
├── Makefile                         # Build orchestration
├── spec-info.yaml                   # Project metadata template
└── README.md                        # Template usage guide
```

### Project Repository Layout

```
your-project/                        # Specific project
├── .cursor/
│   └── rules/                       # Inherited from template
│       └── 99-project-overrides.md  # ✏️ CUSTOMIZED
│
├── spec/
│   ├── sections/                    # ✏️ PROJECT CONTENT
│   │   ├── 00-introduction.md
│   │   ├── 01-architecture.md
│   │   └── ...
│   └── media/                       # ✏️ PROJECT DIAGRAMS
│
├── docs/
│   ├── features/                    # ✏️ IMPLEMENTATION GUIDES
│   ├── guides/                      # Inherited from template
│   └── process/                     # Inherited from template
│
├── reference-impl/                  # ✏️ PROJECT CODE (optional)
├── change-requests/                 # ✏️ PROJECT CRs
├── working-notes/                   # ✏️ SESSION NOTES
│
├── build-system/                    # Inherited from template
├── tools/                           # Inherited from template
├── Makefile                         # Inherited from template
├── spec-info.yaml                   # ✏️ CUSTOMIZED
└── README.md                        # ✏️ PROJECT SPECIFIC
```

**Legend:**
- ✏️ = Edited in project
- Others = Inherited from template, updated via merge

---

## The Template Pattern

### Philosophy

**Problem:** Multiple specifications need consistent tooling, but diverge over time, making updates painful.

**Solution:** Template repository provides infrastructure, projects inherit and customize content.

### Content vs. Infrastructure Decision Matrix

| Change Type | Examples | Location | Sync Method |
|-------------|----------|----------|-------------|
| **Infrastructure** | Makefile targets, LaTeX styling, AI rules, build filters | Template first | `git merge template/main` |
| **Generic Guidance** | Writing style guides, normative language rules, quality standards | Template first | `git merge template/main` |
| **Project Content** | Specification sections, diagrams, examples, requirements | Project only | Direct commit |
| **Project Customization** | `spec-info.yaml`, `99-project-overrides.md`, README | Project only | Direct commit |

### Template Sync Workflow

```bash
# ONE-TIME SETUP (per project)
cd your-project
git remote add template ../doc-template

# WHEN INFRASTRUCTURE CHANGES (template → project)
git fetch template
git merge template/main
# Resolve conflicts: keep content, take infrastructure
git push origin main

# WHEN DISCOVERING IMPROVEMENTS (project → template)
# 1. Identify generic vs. project-specific changes
cd ../doc-template
# 2. Copy infrastructure files
cp ../your-project/.cursor/rules/02-document-conventions.md .cursor/rules/
# 3. Commit to template
git add .cursor/rules/02-document-conventions.md
git commit -m "docs: add section heading guidelines"
# 4. Push to both branches (convention)
git push origin main
git push origin main:test
```

### Conflict Resolution Strategy

When merging from template:

**Always Take Template Version:**
- `Makefile`, `Make.bat`
- `build-system/`
- `tools/*.py` (unless project-specific tool)
- `.cursor/rules/00-07-*.md`

**Always Keep Project Version:**
- `spec/sections/`
- `spec/media/`
- `docs/features/` (project-specific implementation guides)
- `reference-impl/`
- `spec-info.yaml`
- `99-project-overrides.md`
- `README.md`

**Carefully Merge:**
- `docs/guides/` (may have project-specific additions)
- `.gitignore` (project may add entries)

---

## AI Agent Configuration

### The `.cursor/rules` Pattern

AI agents need context to work effectively. Instead of repeating instructions in every chat, encode them in versioned rule files.

#### Rule File Organization

```
.cursor/rules/
├── 00-session-startup.md       # What to do at session start
├── 01-python-environment.md    # How to run tools
├── 02-document-conventions.md  # Writing standards
├── 03-writing-quality.md       # Quality requirements
├── 04-sister-specs.md          # Cross-spec consistency
├── 05-import-mode.md           # Special migration mode
├── 06-template-workflow.md     # Infrastructure sync
├── 07-faq-management.md        # Protected content
└── 99-project-overrides.md     # Project specifics
```

**Numbering Convention:**
- `00-09`: Core workflow rules (load at session start)
- `10-89`: Specialized patterns (load when needed)
- `99`: Project-specific overrides (always loaded)

#### Example Rule Structure

```markdown
# Python Tool Execution (01-python-environment.md)

## Virtual Environment

- ALWAYS activate `.venv/` before running Python tools
- NEVER install to system Python
- Use `python3 -m venv .venv` if missing

## Tool Execution Pattern

```bash
source .venv/bin/activate  # or .venv/Scripts/activate on Windows
python tools/spell-check.py spec/sections/
```

## Dependency Management

- Dependencies in `tools/requirements.txt`
- Install with `pip install -r tools/requirements.txt`
- Pin versions for reproducibility
```

### Session Startup Protocol

**File:** `.cursor/rules/00-session-startup.md`

```markdown
# Session Startup

At the start of every session:

1. Read `.cursor/rules/` directory
2. Read `working-notes/TODO.md` (current priorities)
3. Read most recent `working-notes/SESSION-NOTES-YYYY-MM-DD.md`
4. Check `spec-info.yaml` for project name and version
5. Provide concise greeting:
   - Project name
   - Current version
   - Priority items from TODO
   - Last session summary (1-2 sentences)
```

**Why This Works:**
- AI loads project context automatically
- Consistent experience across sessions
- Continuity even with context window resets
- Human doesn't repeat instructions

### Document Conventions

**File:** `.cursor/rules/02-document-conventions.md`

Key patterns:
- **Normative Language**: ALL CAPS for RFC 2119 keywords (MUST, SHALL, SHOULD, MAY)
- **Section Headings**: Context-rich subsection titles
- **File Organization**: One section per file, numbered sequentially
- **Citations**: Pandoc citation syntax, bibliography in `spec/bibliography.yaml`
- **Code Blocks**: Syntax highlighting, realistic examples
- **Tables**: Captions, balanced columns, semantic structure
- **Figures**: SVG preferred, captions, alt text

### Project Overrides

**File:** `.cursor/rules/99-project-overrides.md`

```markdown
# Project-Specific Rules: Your Project Name

- **Specification Name**: TS0XXX - Project Title
- **Working Group**: Group Name
- **Primary Editor**: Your Name

## Vocabulary

- Project-specific terminology
- Domain-specific acronyms
- Preferred spellings

## Technical Conventions

- Type naming conventions
- Code style preferences
- Diagram formats

## Cross-References

- Related specifications
- External standards
```

---

## Workflow Stages

### 1. Session Initialization

```bash
# Human starts AI session with:
"read rules and notes"

# AI automatically:
1. Reads .cursor/rules/*.md
2. Reads working-notes/TODO.md
3. Reads latest SESSION-NOTES-*.md
4. Checks spec-info.yaml
5. Provides greeting with context
```

### 2. Content Development

**Typical flow:**
```
Human: "Add a section on authentication"

AI:
1. Checks existing sections (numbering, style)
2. Creates spec/sections/XX-authentication.md
3. Uses normative language (MUST/SHALL)
4. Follows document conventions
5. Updates working-notes/TODO.md
```

**Quality checks built-in:**
- Normative language validation
- Consistent terminology
- Cross-reference checking
- Code example validation

### 3. Iterative Refinement

```
Human: "Search for inconsistent terminology and fix"

AI:
1. Greps for patterns across codebase
2. Shows findings
3. Updates all instances
4. Runs validation to confirm
```

**AI handles:**
- Systematic searches across codebase
- Batch refactoring with care
- Consistency checking
- Regression prevention

### 4. Quality Validation

```
Human: "run quality checks"

AI:
1. make spell-check (domain terms expected)
2. make validate-links (internal references)
3. make test-pdf-quick (build verification)
4. Reports issues, suggests fixes
```

### 5. Template Synchronization

```
Human: "push infrastructure changes to template"

AI:
1. Identifies infrastructure files (.cursor/rules, build-system)
2. Copies to template repo
3. Creates descriptive commit
4. Pushes to main and test branches
```

---

## Quality Automation

### Built-In Quality Gates

#### Spell Check
```bash
make spell-check

# Configuration
tools/.spelling-dictionary  # Project-specific terms
tools/spell-check.py       # Aspell integration
```

**Strategy:**
- Use aspell with technical dictionary
- Maintain project-specific dictionary
- AI recognizes technical terms vs. typos
- Human reviews and adds legitimate terms

#### Link Validation
```bash
make validate-links        # Internal links only
make validate-links-all    # Including external URLs
```

**Checks:**
- Section cross-references
- Figure/table references
- Bibliography citations
- Relative file paths

#### Build Validation
```bash
make test-pdf-quick   # Fast: spec only
make test-pdf         # Full: spec + validation
```

**Ensures:**
- LaTeX compilation succeeds
- Images load correctly
- Tables render properly
- TOC generates correctly

### Quality Dashboard

```bash
make generate-quality-dashboard

# Generates HTML report:
docs/coverage/index.html
```

**Metrics:**
- Normative statement count (MUST/SHALL/SHOULD)
- Test coverage (if tests exist)
- Reference consistency
- Build warnings/errors
- Spell check summary

---

## Session Management

### TODO Tracking

**File:** `working-notes/TODO.md`

```markdown
# Current Priorities

## Active Work
- [ ] Add normative language to sections 1-3
- [ ] Move implementation details to docs/features
- [x] Fix link validation issues

## Backlog
- [ ] Create deployment guide
- [ ] Add example implementations
- [ ] Benchmark performance

## Blocked
- [ ] Integration spec (waiting on dependency spec)
```

**Pattern:**
- AI reads at session start
- Updates as work progresses
- Human reviews and reprioritizes
- Provides continuity across sessions

### Session Notes

**File:** `working-notes/SESSION-NOTES-YYYY-MM-DD.md`

```markdown
# Session Notes: 2026-02-04

## Completed
- Added normative language (RFC 2119) to sections 1-5
- Separated implementation details into docs/features/
- Fixed bibliography reference false positives
- Synced infrastructure to template repository

## Decisions
- Use specific type naming convention
- Format selection is runtime configuration
- Schemas contain complete semantic metadata

## Next Session
- Review reference implementation tests
- Update examples with new conventions
- Generate quality dashboard
```

**Purpose:**
- Captures decisions and rationale
- Provides session summaries for AI
- Documents evolution of thinking
- Searchable history

### Context Window Management

**Problem:** Long sessions exceed AI context limits, lose state.

**Solutions:**

1. **Periodic Summaries:**
   ```
   Human: "Summarize progress so far"
   
   AI: Creates structured summary
   - Saves to SESSION-NOTES-*.md
   - Captures decisions, changes, next steps
   ```

2. **TODO Persistence:**
   - AI updates `TODO.md` as work completes
   - Provides continuity across resets

3. **Commit Checkpoints:**
   ```bash
   git commit -m "Checkpoint: completed normative language pass"
   ```
   - Commits are recovery points
   - AI can `git log` to see progress

4. **File-Based State:**
   - Don't rely on chat history
   - Write important info to files
   - Files persist, memory doesn't

---

## Advanced Patterns

### Import Mode (Legacy Migration)

**Problem:** Migrating Word/PDF specs to markdown while preserving exact layout.

**Solution:** Special "archaeologist mode" rules.

**File:** `.cursor/rules/05-import-mode.md`

```markdown
# Import Mode (Archaeologist Mode)

When `IMPORT-TRACKING.md` exists:

## Strict Preservation
- DO NOT modernize terminology
- DO NOT fix grammar/style
- DO NOT reformat tables
- Preserve exact wording (even awkward phrasing)

## Only Allowed Changes
- Convert Word/PDF to Markdown syntax
- Fix broken images/tables
- Add proper Pandoc citations

## Rationale
Migration must be verifiable. Any content change
requires formal change request (CR) process.
```

### Sister Spec Consistency

**Problem:** Multiple related specs need consistent terminology and APIs.

**File:** `.cursor/rules/04-sister-specs.md`

```markdown
# Sister Specification Consistency

## Cross-Reference Pattern

When mentioning related specs:

```markdown
As defined in SpecXYZ [@SpecXYZ-Reference], the
`functionName` MUST return...
```

## API Compatibility Check

Before changing shared APIs:

```bash
make check-api-consistency --spec=SpecXYZ
```

## Vocabulary Alignment

| This Spec | Related Spec | Notes |
|-----------|--------------|-------|
| Term A | Term A | Same meaning |
| Term B | - | Not used there |
| functionX | functionX | Exact match required |
```

### Change Request Workflow

```bash
# Create CR for proposed change
make cr-new --type=CRE  # Enhancement

# Edit CR in change-requests/submitted/
vim change-requests/submitted/CR-YYYY-NNN.md

# Generate diff view
make cr-diff CR=YYYY-NNN

# Build CR PDF for review
make cr-pdf CR=YYYY-NNN
```

**Integration with AI:**
- AI can draft CR text
- Human reviews technical accuracy
- AI generates diff view
- Human submits for working group review

---

## Lessons Learned

### What Works Well

#### 1. Modular Rules Beat Monolithic Instructions
**Before:** 500-line `.cursorrules` file, hard to maintain
**After:** 9 focused files, easy to update specific workflows

#### 2. Files Beat Chat History
**Before:** "Remember we decided to use this naming convention"
**After:** Documented in `99-project-overrides.md`, AI sees it every session

#### 3. Quality Automation Saves Time
**Before:** Manual spell-check, missed inconsistencies
**After:** `make check-all`, catches issues immediately

#### 4. Template Sync Prevents Divergence
**Before:** Copy-paste between projects, versions drift
**After:** Template merge, improvements flow to all projects

#### 5. Session Notes Enable Continuity
**Before:** Lost context between sessions, repeat explanations
**After:** AI reads last session, picks up where we left off

### What Doesn't Work

#### 1. Fully Automated AI Commits
**Problem:** AI making git commits without human review
**Solution:** AI prepares commits, human approves and pushes

#### 2. AI Choosing Technical Standards
**Problem:** AI suggesting formats based on popularity
**Solution:** Human decides standards, AI implements consistently

#### 3. Long Sessions Without Summaries
**Problem:** Context window fills, AI forgets early decisions
**Solution:** Periodic summaries to `SESSION-NOTES-*.md`

#### 4. Vague TODO Items
**Problem:** "Fix validation" - what validation?
**Solution:** Specific, actionable items: "Fix bibliography references in section 06"

#### 5. Mixed Content/Infrastructure Commits
**Problem:** Can't cleanly sync to template
**Solution:** Separate commits: infrastructure first, then content

### Productivity Metrics

From real-world projects:

| Task | Traditional | AI-Assisted | Speedup |
|------|-------------|-------------|---------|
| Add RFC 2119 keywords to 13 sections | ~4 hours | 45 minutes | 5x |
| Fix 46 generic subsection headings | ~2 hours | 20 minutes | 6x |
| Migrate implementation details | ~3 hours | 30 minutes | 6x |
| Create comprehensive guide | ~8 hours | 90 minutes | 5x |
| Update all examples (type system) | ~6 hours | 40 minutes | 9x |

**Total:** ~23 hours of work completed in ~3.5 hours (6.5x average speedup)

**Quality remained high:**
- All links validated ✓
- Consistent terminology ✓
- Proper normative language ✓
- Comprehensive documentation ✓

---

## Implementation Guide

### Phase 1: Template Setup (1-2 days)

```bash
# Create template repository
mkdir doc-template
cd doc-template
git init

# Set up build system
mkdir -p build-system/spec
mkdir -p build-system/presentation
mkdir -p tools

# Copy Makefile from existing project or create from scratch
# Copy LaTeX templates
# Set up Pandoc configuration

# Create AI rules directory
mkdir -p .cursor/rules
# Write rule files (use examples from this document)

# Commit template
git add .
git commit -m "Initial template structure"
git push origin main
```

### Phase 2: First Project Migration (2-3 days)

```bash
# Clone or create project
mkdir your-project
cd your-project
git init

# Copy from template
cp -r ../doc-template/build-system .
cp -r ../doc-template/tools .
cp -r ../doc-template/.cursor .
cp ../doc-template/Makefile .
cp ../doc-template/spec-info.yaml .

# Customize
vim spec-info.yaml  # Set project name, version
vim .cursor/rules/99-project-overrides.md  # Project specifics

# Add content
mkdir -p spec/sections
# Write specification sections

# Set up template remote
git remote add origin <project-repo-url>
git remote add template ../doc-template

# Commit
git add .
git commit -m "Initial project setup from template"
git push origin main
```

### Phase 3: AI Workflow Integration (1 day)

```bash
# Test AI session
# In AI tool: "read rules and notes"
# Verify AI loads context correctly

# Create working notes
mkdir working-notes
echo "# TODO" > working-notes/TODO.md
echo "# Session Notes: $(date +%Y-%m-%d)" > working-notes/SESSION-NOTES-$(date +%Y-%m-%d).md

# Test quality checks
make spell-check
make validate-links
make test-pdf-quick

# Document workflow
vim README.md  # Add "AI Workflow" section
```

### Phase 4: Quality Automation (2-3 days)

```bash
# Set up Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r tools/requirements.txt

# Create quality tools
vim tools/spell-check.py
vim tools/validate-links.py
vim tools/extract-requirements.py

# Add Makefile targets
make spell-check
make validate-links
make check-all

# Set up CI (optional)
vim .github/workflows/build.yml
```

### Phase 5: Second Project (1 day)

```bash
# Clone template pattern
mkdir another-project
cd another-project

# Inherit from template
git clone ../doc-template .
git remote rename origin template
git remote add origin <project-repo-url>

# Customize
vim spec-info.yaml
vim .cursor/rules/99-project-overrides.md

# Start writing
mkdir -p spec/sections
# ...

# Merge any template updates
git fetch template
git merge template/main
```

---

## Conclusion

This workflow pattern scales from single-person projects to multi-team specifications. Key success factors:

1. **Clear Separation**: Content vs. infrastructure, human decisions vs. AI execution
2. **Automation**: Quality checks run automatically, catch issues early
3. **Documentation**: Rules, notes, and conventions are files, not tribal knowledge
4. **Continuity**: Session notes and TODO tracking maintain context
5. **Templates**: Infrastructure improvements benefit all projects

The investment in setup (1-2 weeks) pays dividends in velocity (5-10x) and quality (automated validation, consistent style).

**Next Steps:**
1. Adapt this pattern to your domain
2. Start with one project, refine workflow
3. Extract template, apply to second project
4. Iterate and improve based on your needs

---

## Additional Resources

- [Pandoc Documentation](https://pandoc.org/MANUAL.html)
- [RFC 2119: Normative Language](https://www.rfc-editor.org/rfc/rfc2119)
- [Make Documentation](https://www.gnu.org/software/make/manual/)

---

*Document Version: 1.0*  
*Last Updated: 2026-02-04*  
*Adapted from: AI-Assisted Multi-Repository Workflow Patterns*
