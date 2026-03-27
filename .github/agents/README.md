# Chat Agents Configuration

Specialized agent configurations for different AI models, optimized for their unique strengths.

---

## Agents Overview

| File                                             | Model              | Best For                               | Key Strengths                                 |
| ------------------------------------------------ | ------------------ | -------------------------------------- | --------------------------------------------- |
| [`claude.agent.md`](claude.agent.md)             | Claude 3.7 Sonnet  | General development, complex reasoning | Precision, reliability, balanced approach     |
| [`chatgpt.agent.md`](chatgpt.agent.md)           | GPT-4              | Creative solutions, rapid prototyping  | Innovation, modern patterns, quick iteration  |
| [`gemini.agent.md`](gemini.agent.md)             | Gemini 2.0 Pro Exp | Research, refactoring, deep analysis   | Long-context mastery, comprehensive synthesis |
| [`gemini-flash.agent.md`](gemini-flash.agent.md) | Gemini 2.0 Flash   | Speed, quick fixes, high-volume tasks  | Rapid execution, parallel efficiency          |

---

## Shared Instructions

All agents extend **[`_shared.md`](_shared.md)**, which contains:

- ✅ Universal response principles
- 🛠️ Core tool usage patterns
- 📝 Code quality standards
- 🧠 Memory management guidelines
- ❌ Error handling protocols
- 🎯 Output formatting rules

**Each agent file adds model-specific optimizations** on top of this shared foundation.

---

## Recent Improvements (March 2026)

### Version 2.0 Changes

**Shared Foundation:**

- ✨ Created centralized `_shared.md` for DRY principles
- 🔄 Standardized tool usage patterns across all agents
- 📊 Enhanced memory management strategies
- ⚡ Improved parallel execution guidelines

**Claude Agent:**

- 🎯 Refined precision-focused workflow
- 📋 Added verification checklists
- 🔍 Enhanced debugging protocols

**ChatGPT Agent:**

- 💡 Emphasized creative problem-solving
- 🚀 Added rapid prototyping patterns
- 🎨 Modern framework optimizations

**Gemini Pro Agent (NEW):**

- 📚 Comprehensive research protocols
- 🔗 Long-context analysis patterns
- ✅ Multi-source validation framework
- 🧩 Systematic synthesis approach

**Gemini Flash Agent (NEW):**

- ⚡ Speed-first optimization strategies
- 🔄 Parallel-first execution mindset
- 💬 Concise communication patterns
- 🎯 Efficient workflow templates

---

## Usage

### Selecting the Right Agent

**Choose based on your task:**

```markdown
Research & Analysis → gemini.agent.md

- Understanding large codebases
- Comprehensive refactoring
- Architecture documentation
- Complex debugging

Speed & Iteration → gemini-flash.agent.md

- Quick bug fixes
- Rapid feature additions
- High-volume edits
- Fast iterations

General Development → claude.agent.md

- Balanced approach
- Complex reasoning
- Mission-critical code
- High reliability needs

Creative Solutions → chatgpt.agent.md

- Novel problems
- Modern patterns
- Rapid prototyping
- Experimental features
```

### In VS Code / GitHub Copilot

1. Place agents in `.github/agents/` directory
2. Open file editor and select agent from dropdown
3. Agent automatically loads shared + specific instructions

### In Custom Implementations

```python
# Load shared instructions first
shared = read_file(".github/agents/_shared.md")

# Then load model-specific enhancements
agent = read_file(".github/agents/gemini.agent.md")

# Combine into prompt
instructions = f"{shared}\n\n{agent}"
```

---

## Examples

### Example 1: Complex Refactoring

**Task:** Refactor authentication system across 15 files

**Best Agent:** `gemini.agent.md`

**Why:**

- Needs comprehensive context across many files
- Requires understanding relationships
- Must maintain consistency
- Benefits from thorough analysis

**Workflow:**

1. Loads 15+ files in parallel
2. Maps authentication flow
3. Identifies all dependencies
4. Plans comprehensive refactor
5. Validates against tests
6. Documents changes

---

### Example 2: Quick Bug Fix

**Task:** Fix typo in validation regex

**Best Agent:** `gemini-flash.agent.md`

**Why:**

- Simple, focused change
- Known location
- Fast iteration preferred
- No research needed

**Workflow:**

1. Read error + file
2. Fix regex
3. Quick validation
4. Done in <10s

---

### Example 3: Novel Feature Design

**Task:** Design real-time collaborative editing feature

**Best Agent:** `chatgpt.agent.md`

**Why:**

- Requires creative solution
- Modern patterns (WebSockets, CRDTs)
- Rapid prototyping valuable
- Innovation encouraged

**Workflow:**

1. Explore modern approaches
2. Design WebSocket architecture
3. Prototype conflict resolution
4. Iterate on feedback

---

### Example 4: Critical Security Fix

**Task:** Fix authentication bypass vulnerability

**Best Agent:** `claude.agent.md`

**Why:**

- Security critical
- Zero tolerance for errors
- Needs thorough validation
- Precision essential

**Workflow:**

1. Analyze vulnerability carefully
2. Design secure solution
3. Verify edge cases
4. Validate security properties
5. Test exhaustively

---

## Maintenance

### Updating Agents

**When to update:**

- Model capabilities change
- New patterns emerge
- User feedback surfaces gaps
- Performance can be improved

**Update process:**

1. Modify `_shared.md` for cross-cutting changes
2. Update specific agent for model optimizations
3. Test with representative tasks
4. Document changes in this README

### Version History

- **v2.0** (March 2026): Added Gemini agents, created shared foundation
- **v1.5** (January 2026): Enhanced Claude + ChatGPT agents
- **v1.0** (November 2025): Initial Claude + ChatGPT agents

---

## Contributing

### Adding New Agents

1. Start with `_shared.md` as base
2. Identify model's unique strengths
3. Create `[model-name].agent.md`
4. Add model-specific optimizations
5. Update this README table
6. Test with real tasks

### Best Practices

**Agent files should:**

- ✅ Reference `_shared.md` explicitly
- ✅ Focus on model-specific strengths
- ✅ Include concrete examples
- ✅ Provide workflow templates
- ✅ Define success metrics

**Agent files should NOT:**

- ❌ Duplicate shared instructions
- ❌ Override core principles without reason
- ❌ Be model-agnostic (that's `_shared.md`)
- ❌ Ignore shared tool patterns

---

## Architecture

```
.github/agents/
├── _shared.md              # Core instructions (all agents)
├── claude.agent.md         # Claude-specific optimizations
├── chatgpt.agent.md        # ChatGPT-specific optimizations
├── gemini.agent.md         # Gemini Pro optimizations
├── gemini-flash.agent.md   # Gemini Flash optimizations
└── README.md              # This file
```

**Inheritance model:**

```
_shared.md (base)
    ↓
[model].agent.md (augments)
    ↓
Final agent instructions
```

---

## FAQ

**Q: Can I use multiple agents in one session?**
A: VS Code/Copilot typically uses one agent per session. Choose the best fit for your primary task.

**Q: What if my task fits multiple agents?**
A: Use this priority:

1. Security/Critical → Claude
2. Research/Refactor → Gemini Pro
3. Speed/Quick → Gemini Flash
4. Creative/Novel → ChatGPT

**Q: Can I modify agents for my project?**
A: Yes! Fork and customize. Consider contributing improvements back.

**Q: Why separate files instead of one?**
A: Model-specific optimizations can conflict. Separation maintains clarity and allows independent evolution.

**Q: How often should agents be updated?**
A: Review quarterly or when models are updated. `_shared.md` is more stable.

---

## Resources

- [VS Code Copilot Docs](https://code.visualstudio.com/docs/copilot/)
- [GitHub Copilot Custom Instructions](https://docs.github.com/en/copilot/customizing-copilot)
- [Agent Best Practices](https://github.com/microsoft/vscode-copilot-docs)

---

**Version:** 2.0
**Last Updated:** March 13, 2026
**Maintainer:** Development Team
