# Archive

Reference documents that informed the system design. Not part of the running codebase.

| File | Description |
|---|---|
| `Complete_Trading_System_v3.pdf` | The 41-page trading playbook — core intelligence source for all AI agent prompts and the deterministic decision tree |
| `Complete_Trading_System_v3.docx` | Original Word document of the playbook |
| `Judging Criteria of AI Awakening - Trading & Strategy.pdf` | Hackathon judging scorecard (100pts: 50 Mantle General + 50 BGA Track) |
| `extract_docx.py` | One-off script used to extract plain text from the .docx for prompt engineering |
| `extract_docx.js` | Node.js version of the same extraction script |

## Why these files matter

The `Complete_Trading_System_v3.pdf` is the foundation of the entire system:
- The 9-scenario decision tree (Q1-Q4) directly implements Part 2 of the playbook
- All 12 confluence indicators and their scoring rules come from Part 10
- Session-based leverage caps come from Part 1
- The CVD Matrix (BOTH_RISING, BOTH_FALLING, FUT_UP_SPOT_FLAT, FUT_DOWN_SPOT_FLAT) is Part 3
- The Pre-Trade Note (Why/Wrong/Add) is Part 9
- Risk management formulas (1% risk, ATR stop) are Part 8

The Groq LLM prompts in `agents/prompts.py` are condensed from this document.
