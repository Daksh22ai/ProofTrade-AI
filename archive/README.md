# archive/: Reference Documents

Reference materials that informed the system design. These files are not part of the running codebase.

| File | Description |
|---|---|
| `Complete_Trading_System_v3.pdf` | The 41-page trading playbook. Core intelligence source for all AI agent prompts and the deterministic decision tree. |
| `Complete_Trading_System_v3.docx` | Original Word document of the playbook. |
| `Judging Criteria of AI Awakening - Trading & Strategy.pdf` | Hackathon judging scorecard (100 points: 50 Mantle General, 50 BGA Track). |
| `extract_docx.py` | One-off script used to extract plain text from the .docx for prompt engineering. |
| `extract_docx.js` | Node.js version of the same extraction script. |

## Why These Files Matter

The `Complete_Trading_System_v3.pdf` is the foundation of the entire system's intelligence:

- The 9-scenario decision tree (Q1-Q4) directly implements Part 2 of the playbook.
- All 12 confluence indicators and their scoring rules come from Part 10.
- Session-based leverage caps come from Part 1.
- The CVD matrix states (BOTH_RISING, BOTH_FALLING, FUT_UP/SPOT_FLAT, FUT_DOWN/SPOT_FLAT) are Part 3.
- The pre-trade note structure (why, what proves me wrong, when to add) is Part 9.
- Risk management formulas (1% account risk, ATR-based stops) are Part 8.
- Macro regime classification (BULL/BEAR/TRANSITION) is Part 0.

The Groq LLM prompts in `agents/prompts.py` are condensed distillations of this document into under 1500 tokens each. The AI is explicitly instructed to cite Part numbers when explaining its reasoning, creating a traceable link from every recommendation back to a specific rule in the original playbook.

The Playbook page in the frontend (`frontend/src/pages/Playbook.jsx`) is a public-facing summary of all nine scenarios, the CVD matrix, session rules, and risk management framework written independently so it can be shared with users without requiring access to the original PDF.
