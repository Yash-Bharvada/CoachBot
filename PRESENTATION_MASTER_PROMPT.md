# 🎙️ CoachBot — Competition Presentation Deck Master Guide & Manus Prompt
**HackMatrix 2026 (Round 2) Submission**  
**Team:** Aura Spectre | **Team Leader:** Yash Bharvada (`23cs006@charusat.edu.in`)  
**Project:** CoachBot (Live AI Mock Interviews with Adaptive Feedback & Real-Time Tavus Video)  
**Repository:** [https://github.com/Yash-Bharvada/CoachBot](https://github.com/Yash-Bharvada/CoachBot)

---

## 🤖 1. Manus Infographic Master Prompt (Copy & Paste)

Copy and paste the exact prompt below into **Manus**:

```markdown
Task: Build a professional, visually-dominant, and text-light competition pitch presentation deck on "CoachBot: Live AI Mock Interviews with Adaptive Feedback & Real-Time Tavus Video (HackMatrix 2026)". Please act autonomously and orchestrate the connected apps in the following exact sequence:

Phase 1: Data Synthesis for Visuals (via Firecrawl)
Extract and structure the core technical and narrative data for CoachBot:
- Project: CoachBot (Team Aura Spectre - Lead: Yash Bharvada, HackMatrix 2026 Round 2)
- Core Tech: FastAPI (Async Python), Next.js 16 (Turbopack), Tavus API v2 (Digital Human PAL Video Avatar), Groq Cloud (Llama 3.3 70B & Whisper STT), MongoDB Atlas.
- Key USPs: Grounded Role Context Matrix (Resume + JD ingestion), Adaptive Difficulty State Machine (Easy ↔ Medium ↔ Hard based on turn scoring), Real-time LLM-as-Judge rubric (Accuracy, Relevance, Clarity, Confidence), and Executive PDF Feedback Export.
- Structure this data into distinct infographic assets: 3-column problem comparison, 4-step user workflow, 5-layer system architecture diagram, adaptive state machine flowchart, and a KPI readiness dashboard.

Phase 2: Custom Infographic Generation (via Canva)
Leverage the Canva integration to create or source high-end, professionally designed modern SVG/vector infographic assets. Do NOT use plain bullet lists or stock icons. Create full visual summaries:
1. "Problem vs. Reality" side-by-side pain matrix.
2. "System Architecture & Data Flow" sequential node diagram (Next.js -> FastAPI -> Tavus / Groq / MongoDB).
3. "Adaptive Difficulty Engine" circular state machine diagram with trigger thresholds.
4. "Live Candidate Journey" 4-phase horizontal roadmap (Onboard -> Stream -> Judge -> Report).
5. "Executive Feedback Breakdown" circular gauge meter (Readiness Score / 100) + 4-quadrant competency grid.

Phase 3: Visual Deck Assembly (via Gamma)
Construct an 8-slide presentation in Gamma adhering to an ultra-clean, high-contrast dark tech aesthetic (slate/emerald/cyan neon accents):
- Prioritize visual storytelling over text.
- Text blocks must be minimal: bold headline + 2-3 word metric labels and callouts.
- Slide structure:
  - Slide 1: Hero & Vision (CoachBot & HackMatrix 2026)
  - Slide 2: The Problem (Broken Traditional Interview Prep)
  - Slide 3: The Solution (Grounded AI Video Interviewing)
  - Slide 4: System Architecture & Tech Stack (Full-Stack Flow)
  - Slide 5: Key Innovations & USPs (State Machine & Context Matrix)
  - Slide 6: Product Workflow & User Journey (4-Step Demo Flow)
  - Slide 7: Market Impact & Scalability (B2C & B2B Roadmap)
  - Slide 8: Technical Viability & Conclusion (Production-Ready)

Phase 4: Native .PPTX Final Output
Compile and deliver the final deck as a complete, native .pptx file formatted for competition pitching with editable infographic shapes and typography.
```

---

## 📑 2. Detailed Slide-by-Slide Infographic Blueprint

### Slide 1: Title & Hero
* **Header / Title:** `CoachBot`
* **Sub-headline:** Live AI Mock Interviews with Adaptive Feedback & Real-Time Tavus Video
* **Visual Component:** 3D digital human interviewer avatar with audio wave elements and prominent stack badges (`FastAPI`, `Next.js 16`, `Tavus PAL`, `Groq Llama 3.3`, `MongoDB Atlas`).
* **Tags / Subtext:** 
  * *Event:* HackMatrix 2026 – Round 2
  * *Team:* Aura Spectre
  * *Lead:* Yash Bharvada

---

### Slide 2: The Problem
* **Header / Title:** Traditional Interview Prep is Broken
* **Sub-headline:** The Gap Between Reading and Live Performing
* **Visual Component (3-Card Infographic Matrix):**
  1. 📄 **Generic Question Banks** — Zero role context or JD grounding.
  2. ⌨️ **Text-Box Fatigue** — Typing answers fails to simulate live spoken pressure.
  3. ❓ **Unvetted Resume Claims** — Paper claims collapse under real technical probing.
* **Key Stat Callout:** `87%` of candidates struggle with live articulate communication despite strong resume qualifications.

---

### Slide 3: The Solution
* **Header / Title:** Realistic. Grounded. Adaptive.
* **Sub-headline:** Next-Generation AI Technical Interview Simulation
* **Visual Component (3 Pillars Diagram):**
  1. 🎯 **Role Context Matrix** — Dynamic synthesis of Target Job Title, JD, and uploaded Resume (PDF/DOCX).
  2. 👤 **Tavus AI Video Avatar** — Interactive digital human interviewer asking persona-driven questions.
  3. ⚖️ **LLM-As-Judge Feedback** — Instant scoring across 4 evaluation rubrics with an executive PDF report.

---

### Slide 4: System Architecture & Data Pipeline
* **Header / Title:** Production-Grade Architecture
* **Sub-headline:** Sub-300ms Turn Latency & Real-Time Orchestration
* **Visual Component (Multi-Node Flow Diagram):**
  * **Layer 1:** Candidate Web Client (Next.js 16 + Web Speech API)
  * **Layer 2:** Async API Gateway (FastAPI + Pydantic v2)
  * **Layer 3:** Real-time AI Services:
    * 🎥 *Tavus v2 PAL Engine* — Digital avatar video stream
    * ⚡ *Groq Llama-3.3-70b-versatile* — Rubric judging & adaptive generation
    * 🎙️ *Groq Whisper* — Speech-to-text fallback
    * 🍃 *MongoDB Atlas (Motor Driver)* — Session & matrix persistence

---

### Slide 5: Core Innovations & USPs
* **Header / Title:** Technical Differentiators
* **Sub-headline:** Beyond Static Form Bots
* **Visual Component (Dual Feature Infographics):**
  * **Infographic A: Adaptive Difficulty State Machine**
    * Score $\ge 80\%$ $\rightarrow$ Scale to **Hard / Architectural Drilldown**
    * Score $50\% - 79\%$ $\rightarrow$ Maintain **Medium / Deep Dive**
    * Score $< 50\%$ $\rightarrow$ Adjust to **Easy / Foundational Clarification**
  * **Infographic B: Resume Claim Verification**
    * Highlights candidate resume bullet points that lacked supporting evidence during live answers.

---

### Slide 6: Product Workflow & User Journey
* **Header / Title:** End-to-End User Experience
* **Sub-headline:** From Resume Upload to Executive Evaluation
* **Visual Component (4-Step Horizontal Roadmap):**
  1. 📤 **01. Onboard** — Upload Resume & Job Description $\rightarrow$ Generates Context Matrix.
  2. 🎥 **02. Live Room** — Real-time Tavus video interview with zero-latency speech capture.
  3. 📊 **03. Real-Time Judge** — Turn-by-turn rubric evaluation (Accuracy, Relevance, Clarity, Confidence).
  4. 📑 **04. Executive Report** — Instant readiness scoring and one-click print-ready PDF export.

---

### Slide 7: Market Impact & Business Scalability
* **Header / Title:** Market Opportunity & Expansion
* **Sub-headline:** Transforming Technical Hiring & Preparation
* **Visual Component (3 Target Segment Hexagons):**
  * 🎓 **B2C Job Seekers** — Affordable, on-demand high-fidelity mock interview coaching.
  * 🏫 **University Placement Cells** — Automated batch student screening & readiness analytics.
  * 🏢 **Enterprise Talent Teams** — Objective, unbiased pre-screening before engineering interview rounds.

---

### Slide 8: Technical Viability & Conclusion
* **Header / Title:** Built for Production
* **Sub-headline:** HackMatrix 2026 Round 2 Submission
* **Visual Component (4 Key Readiness Metrics):**
  * ⚡ **100% Async Backend** — Python FastAPI + Motor async MongoDB client.
  * 🛡️ **Zero Fluff Rubric** — Deterministic scoring based on actual industry expectations.
  * 🖨️ **Native PDF Export** — High-contrast `@media print` layout engine.
  * 💻 **Open Source & Extensible** — Modular services for seamless LLM & Avatar provider swaps.

---

## 🛠️ 3. Quick Reference Project Metadata

| Item | Value |
|---|---|
| **Project Title** | CoachBot |
| **Subtitle** | Live AI Mock Interviews with Adaptive Feedback & Real-Time Tavus Video |
| **Hackathon** | HackMatrix 2026 – Round 2 |
| **Team Name** | Aura Spectre |
| **Team Lead** | Yash Bharvada (`23cs006@charusat.edu.in`) |
| **GitHub Repo** | `https://github.com/Yash-Bharvada/CoachBot` |
| **Canva Deck Link** | `https://canva.link/coachbothackmatrixround2` |
