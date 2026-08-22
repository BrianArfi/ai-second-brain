#!/usr/bin/env python3
"""
Updates all ExampleProject PoC tasks in ClickUp:
1. Adds actual PRD content to description for each section reference
2. Fixes priority: P0 -> high (2), P1 -> normal (3)
"""

import json
import re
import time
import requests

TOKEN  = "pk_88954348_1FB6GNJATX3QOVXKP38TABFTA3U9Q0XS"
LIST_ID = "901614365044"
PRD_URL = "https://docs.google.com/document/d/<YOUR_DRIVE_ID>/edit"

# ---------------------------------------------------------------------------
# PRD content library — keyed by canonical section tag
# ---------------------------------------------------------------------------
PRD_SECTIONS = {

"section_23_2": """\
**Section 23.2 — Pre-Build Checklist (Phase 0)**
- Use case confirmed with Fadlan (CS Ops alignment)
- Mukhib confirms secondary_user_id + user metadata available in Yellow workflow builder
- Tool credentials obtained (get_transaction, initiate_refund)
- KB documents from ready-doc folder accessible
- Mukhib: infra setup (service accounts, DB, repo, CI/CD)
- Fajri's existing code: unit tests added, deployed to production""",

"section_18_3": """\
**Section 18.3 — Integration Strategy: Yellow Workflow Builder**
Critical decision (Engineering, April 2026): Phase 0 and Phase 1 integrate with Yellow via its workflow builder (HTTP calls to our agent endpoint), NOT by replacing Yellow as the front-end layer. This avoids mobile app changes that would add significant delays.

Rollout control: Traffic is controlled via Yellow's workflow builder (internal emails first, then %-based user groups). Requires confirmation from Fadlan that secondary_user_id and user metadata are available in Yellow's workflow context.""",

"section_3_step_1": """\
**Section 3 — Step 1: Greeting & Whitelist Verification**
- Verify that the user's secondary_user_id is part of the Pilot Whitelist.
- All participants entering this flow are already authenticated via their Secondary account.""",

"section_3_step_2": """\
**Section 3 — Step 2: Channel & Context Filtering**
- Channel: Handle only users from the Chat channel.
- Escalation: Automatically hand over users from Social Media or Email to human agents (out of POC scope).""",

"section_3_step_3": """\
**Section 3 — Step 3: Intent Understanding & Transaction Identification (Full Text)**
- Natural Language Understanding: ExampleProject extracts intent, amount, bank name, date, and other clues from the user's free-text input.
- Transaction History Pull: Seamlessly pull the user's transaction history in the background.
- AI Matching: Match extracted details to a specific Transaction ID using AI context matching (no selection UI, no carousel).
- Transaction Detail Retrieval: Call the Get User Transaction Detail API to retrieve Status Code, Date, Transfer Pool, and Amount.""",

"section_3_step_4": """\
**Section 3 — Step 4: Incident RTFM Check**
- Query the Service Incident Database for any known downtime or delays for the identified bank.
- If a known incident is found, inform the user immediately with the relevant details and expected resolution timeline.""",

"section_3_step_5": """\
**Section 3 — Step 5: Scenario Classification (Decision Matrix)**
- Based on the transaction details, ExampleProject classifies the complaint into one of the 7 Scenarios (see Section 2).
- Route to the appropriate handler: Refund Flow (Scenarios 4, 5, 6), UCC Flow (Scenario 3), or Jingga Handback (Scenarios 1, 2, 7).""",

"section_3_step_6": """\
**Section 3 — Step 6: Bank Receipt Collection & OCR Processing (Refund Scenarios Only)**
- For all refund scenarios (4, 5, 6), ExampleProject requests the user to upload their bank transfer receipt (bukti transfer).
- The receipt image is processed through the OCR API (using the Refund Automation OCR, not the legacy Jingga OCR) to extract:
  Sender Bank Name, Recipient Bank Name, Transfer Amount, Transfer Date & Time, Sender Name, Reference Number.
- OCR Failure Handling: If OCR fails or returns invalid data, and the user insists the receipt is correct, ExampleProject falls back to manual text-based probing to gather the same data points.""",

"section_3_step_7": """\
**Section 3 — Step 7: Fund Verification & Refund Execution**
- ExampleProject verifies that the transferred funds are still available and have not been processed for the original transaction purpose. This is critical to ensure we are refunding real, unprocessed money.
- Upon successful verification, ExampleProject calls initiate_refund with the collected data.
- ExampleProject notifies the user of the refund result (success or failure with the specific reason).""",

"section_3_2": """\
**Section 3.2 — Core Component Descriptions**
| Component | Responsibility | Technology |
| Gateway | Receives inbound messages from all channels. Normalizes payload. Routes to Orchestrator. | Go / FastAPI + Redis Queue |
| Orchestrator | Master Agent. Classifies intent, selects Specialist Agent, manages conversation memory, enforces guardrails, decides when to escalate. | PocketFlow + Gemini 3.0 |
| Tool Registry | Central catalog of all callable tools/APIs. Tools are versioned and access-controlled. | Postgres + API Gateway |
| RAG Module | Retrieval-Augmented Generation engine for FAQ and KB lookups. Fed by Ops-maintained documents. | Vector Store (pgvector) + Embedding Model |
| Session Memory | Per-conversation state store. Maintains full context. Backed by Redis for fast access. | Redis + Gemini Context |
| Monitoring Stack | Real-time dashboards for cost, deflection, latency, accuracy, and hallucination tracking. | Grafana + Prometheus |""",

"section_2": """\
**Section 2 — Decision Matrix: The 7 Scenarios**
All "Bank Transfer Pending" complaints (Txn Type 4, Status 10):
| Scenario | Description | Route | ExampleProject Action |
| 1 (A) | User hasn't transferred yet | Yellow (Jingga) | Identify & Handback |
| 2 (B) | User has transferred (Correct) | Yellow (Jingga) | Identify & Handback |
| 3 (C) | Wrong nominal/amount | ExampleProject (UCC) | UCC Flow Trigger (Phase 0 Stretch) |
| 4 (D) | Wrong beneficiary bank | ExampleProject (Refund) | Full Refund Lifecycle |
| 5 (E) | Double transfer | ExampleProject (Refund) | Auto-Refund for Unprocessed Transfer |
| 6 (F) | Transact w/o Txn creation | ExampleProject (Refund) | Full Refund / OCR-Based Case |
| 7 (G) | History Request / Other | Yellow (Jingga) | Identify & Handback |""",

"section_21_1": """\
**Section 21.1 — Conversation State**
Each conversation maintains:
- Full message history (DB-persisted)
- conversation_id linked to external ticket ID
- Classified intent and extracted entities (transaction IDs, account references)
- Resolution status and domain tag
- All tool calls made
Session alignment with Yellow: Yellow defines 1 session = 24-hour window. Our conversation_id must map to the same 24-hour boundary for metrics consistency (per Fadhlan, April 2026).""",

"section_21_2": """\
**Section 21.2 — Escalation Triggers**
The agent escalates when:
(a) Turn limit reached (default: 8)
(b) User requests human explicitly
(c) Response confidence below threshold
(d) Sensitive case (fraud, deceased account)
(e) Write tool fails after retry""",

"section_21_3": """\
**Section 21.3 — Write Tool Safety**
All write tools require explicit user confirmation before execution. The agent must:
1. Summarize the proposed action
2. Ask for explicit confirmation
3. Only execute after confirmation received
Maximum 3 write tool calls per conversation (configurable).""",

"section_21_4": """\
**Section 21.4 — Tool Inventory (Current)**
Tools are indicative and will be finalized with Ops and Ops Portal engineering.
| Tool | Domain | Access | Status |
| get_transaction | transaction | read | Available |
| list_transactions | transaction | read | Available |
| get_balance | transaction | read | Not available yet |
| initiate_refund | refund | write | Available |
| get_kyc_status | account | read | Available |
| update_account | account | write | Available |
| check_bill_status | product | read | Not available yet |
| escalate_to_human | system | write | Required (build needed) |""",

"section_20_1": """\
**Section 20.1 — Conversation API**
| Method | Endpoint | Description |
| POST | /v1/conversations | Create new conversation. Accepts ticket_id, user_id, initial_message. Returns conversation_id. |
| POST | /v1/conversations/{id}/messages | Send user message. Returns agent response + metadata (intent, domain, confidence, tools called). |
| GET | /v1/conversations/{id} | Retrieve full conversation history, tool calls, metadata, resolution status. |
| PATCH | /v1/conversations/{id}/status | Update status (resolved, escalated, closed). Used by ticketing system to sync state. |
| GET | /v1/health | Health check. Returns system status, LLM provider status, tool availability. |
| POST | /v1/conversations/{id}/escalate | Force-escalate to human agents. Returns escalation payload. |
| POST | /internal/kb/documents | Upload KB document with metadata. Triggers auto re-indexing. VPC-internal only. |
| GET | /internal/kb/documents | List all KB documents with metadata and timestamps. |
| DELETE | /internal/kb/documents/{id} | Remove document and trigger re-indexing. VPC-internal only. |

Design principles: All endpoints authenticated via API key. POST endpoints accept idempotency_key header. /messages supports sync (<5s) and async (webhook callback) modes. /internal/* endpoints are VPC-internal only.""",

"section_22": """\
**Section 22 — Configuration Management**
Phase 0 principle: Engineering owns all behavioral config (prompt, tools, guardrails) via PR. This establishes best practices before Ops self-service is enabled in Phase 1+.
| Config Area | Format | Change Process | Phase 0 Owner |
| System prompt | prompt.md | PR review | Engineering |
| Tool registry | tools.yaml | PR review | Engineering |
| LLM settings | llm.yaml | PR review | Engineering |
| Guardrails | guardrails.yaml | PR review | Engineering |
| Feature flags | features.yaml | PR review | Engineering |
| Knowledge base | Upload API + cloud storage | Upload API (no PR required) | Ops team |
| Secrets / API keys | Secrets manager | Secrets manager process | Engineering |""",

"section_13_nfr": """\
**Section 13 — Non-Functional Requirements**
| Category | Requirement | Target |
| Performance | Initial response from Gateway | < 1 second |
| Performance | Total reasoning time (Orchestrator + Specialist + Tool calls) | < 3 seconds (p95) |
| Scalability | Concurrent active support sessions | > 10,000 |
| Privacy | PII masking (NIK, full names, phone numbers) before data leaves VPC | 100% compliance |
| Privacy | Conversation data retention | 90 days (configurable) |
| Availability | Platform uptime | 99.9% (monthly) |
| Availability | Graceful degradation: if Gemini 3.0 unavailable, fall back to Gemini 2.5 | Automatic, < 5s switchover |
| Security | All API calls authenticated via service tokens within VPC | Zero unauthenticated calls |
| Observability | All metrics emitted to Prometheus with Grafana dashboards | Real-time (< 30s delay) |
| Observability | Structured logging for all agent turns (Thought Logs) | 100% of turns logged |""",

"section_5_1": """\
**Section 5.1 — Business Success Metrics**
| Metric | Target | Collection Method |
| CSAT | > 7.0 / 10.0 | Yellow.ai CSAT widget post-resolution |
| Deflection Rate | > 60% | Count sessions tagged FINA_RESOLVED vs. HANDBACK_TO_AGENT from execution logs |
| Average Token Cost per Session | < IDR 200 | Manually dividing total cost from AI dashboard with total sessions handled |""",

"section_5_2": """\
**Section 5.2 — Technical Performance KPIs**
| Metric | Target | Collection Method |
| Response Latency | Avg < 8s per turn | Computed from response_times[] in execution logs |
| Hallucination / Nonsensical Rate | < 2% | Ops Sample Audit: Ops reviews 10% of full_conversation_log entries daily |
| Tool Call Accuracy | > 95% | Computed from tools_invoked[] vs. tool_results[] in logs |
| Scenario Classification Accuracy | > 90% | Ops Sample Audit: Ops verifies scenario_classified against user intent |
| Refund Execution Success | > 90% | Computed from refund_result success rate in execution logs |""",

"section_6": """\
**Section 6 — Definition of Done (Phase 0)**
- [ ] Sequential User Stories (01–07) reflect "Full Text" and "Auto-Refund" logic.
- [ ] Refund stories (US-02, US-03, US-04) implemented with full bank receipt + OCR + fund verification flow.
- [ ] Scenario 3 (US-05 – UCC) implemented as High Priority Stretch.
- [ ] Smart Handback (US-06) passes session context to Jingga.
- [ ] Execution Logging (US-07) produces structured, queryable session logs.
- [ ] Technical KPI infrastructure (Latency, Quality Audit) is operational from Day 1 of pilot.""",

"section_23_1": """\
**Section 23.1 — Phase 0 Launch Criteria (Go/No-Go)**
Measured over the first 1 week of production traffic:
| Metric | Threshold |
| Deflection rate (refund use case) | ≥ 30% |
| Unintended write tool executions | Zero (all refunds had explicit confirmation) |
| Average first-response time | < 5s (sync) / < 10s (async) |
| Tool call success rate | ≥ 90% |""",

"rollout_strategy": """\
**Section 7 — Phase 0 Rollout Strategy**
Stage 1 — Internal Team (Day 1–2): ~10–15 people (Engineering, PM, Ops directly involved in ExampleProject). Whitelisted by secondary_user_id. Goal: smoke test end-to-end flow in production.
Stage 2 — All Secondary Internal Employees (Day 3–4): ~500 users (any secondary_user_id with @secondary.id email). Goal: surface edge cases, stress-test OCR and scenario classification.
Stage 3 — 1–2% Real Secondary Users (Day 5–7): Traffic controlled via Yellow's workflow builder with a percentage-based user group rule. Full measurement tracking active.

Rollback Trigger (any stage): Unintended refund execution (zero tolerance), response latency > 15s avg for 30-min window, misclassification rate > 20% (sampled), any PII leak detected.""",

"infra_setup": """\
**Infrastructure Setup (Section 3.2 — Core Components)**
Phase 0 infra scope:
- Service accounts with minimal necessary permissions
- Postgres DB: conversation state + config (skills / tools / agents)
- Redis: per-conversation session memory
- GitHub/GitLab repo + automated CI/CD pipeline
- Secrets manager: centralized API key management
- Grafana: emit metrics (latency, tool call success, deflection rate)""",

"us_01": """\
**US-01: Orchestrator Identification & Scenario Classification**
As a whitelisted user contacting ExampleProject via chat, I want ExampleProject to understand my problem from my text description and identify my specific transaction, so that my issue is correctly classified and handled without me repeating myself.

Acceptance Criteria:
- AC1: ExampleProject extracts intent and transaction details from free-text without requiring structured input.
- AC2: ExampleProject matches details to a unique Txn ID from history using AI matching (No selection list/carousel).
- AC3: ExampleProject classifies the complaint into one of the 7 scenarios.
- AC4: ExampleProject checks the RTFM incident database and informs the user if their issue is caused by a known bank outage.""",

"us_02": """\
**US-02: Handling Scenario 4 — Wrong Bank (Full Refund)**
As a user who transferred to the wrong Secondary bank pool, I want ExampleProject to verify the error, collect my receipt, confirm funds are available, and process my refund.

Detailed Flow:
1. ExampleProject identifies bank pool mismatch (Expected Bank vs. Actual Transfer Bank).
2. ExampleProject informs the user with eligibility for refund.
3. ExampleProject requests bank transfer receipt (bukti transfer).
4. ExampleProject processes receipt via OCR (Verification: Bank, Amount, Date, Sender, Ref Number).
5. Fallback to manual probing if OCR fails.
6. ExampleProject calls Fund Verification API to confirm money is still available and unprocessed.
7. ExampleProject presents a summary to the user for final confirmation.
8. Upon user confirmation, ExampleProject calls initiate_refund.

Acceptance Criteria: AC1: Correctly identifies mismatch. AC2: Collects receipt + OCR (or manual fallback). AC3: Verifies fund availability. AC4: Obtains explicit confirmation before action. AC5: Reports success or failure with clear reason.""",

"us_03": """\
**US-03: Handling Scenario 5 — Double Transfer (Auto-Refund Unprocessed)**
As a user who accidentally transferred twice for the same transaction, I want ExampleProject to detect the duplicate, automatically identify which transfer is still unprocessed, and refund it.

Detailed Flow:
1. User describes issue ("Transfer dua kali").
2. ExampleProject identifies two separate payment records for a single Txn ID.
3. ExampleProject requests bank transfer receipt(s) for verification.
4. ExampleProject processes receipt(s) via OCR (or manual probing fallback).
5. ExampleProject cross-references: identifies one as processed/forwarded and the other as pending/unprocessed.
6. ExampleProject automatically selects the unprocessed transfer for refund (No user selection).
7. ExampleProject verifies fund availability for the unprocessed transfer.
8. ExampleProject calls initiate_refund for the unprocessed transfer.

Acceptance Criteria: AC1: Detects duplicate payment records. AC2: Collects receipts + verifies data. AC3: Automatically identifies unprocessed transfer. AC4: Verifies fund availability. AC5: Executes initiate_refund and reports result.""",

"us_04": """\
**US-04: Handling Scenario 6 — No Transaction Created (OCR-Based Refund)**
As a user who transferred money to Secondary without first creating a transaction in the app, I want ExampleProject to find my payment using my bank receipt and process a refund.

Detailed Flow:
1. ExampleProject identifies lack of matching Txn ID and recognizes Scenario 6.
2. ExampleProject mandates bank transfer receipt as primary evidence.
3. ExampleProject extracts details via OCR (Bank, Amount, Sender, Date/Time, Reference).
4. Fallback to manual probing for same data points if OCR fails.
5. ExampleProject locates incoming payment in Secondary's money-in records using extracted data.
6. ExampleProject verifies funds are available and unprocessed.
7. ExampleProject presents summary to the user.
8. ExampleProject creates manual refund case and calls initiate_refund.

Acceptance Criteria: AC1: Recognizes Scenario 6. AC2: Mandates bank receipt upload. AC3: Extracts transfer details via OCR or manual probing fallback. AC4: Locates unmatched payment in incoming records. AC5: Verifies fund availability and initiates refund.""",

"us_05": """\
**US-05: Handling Scenario 3 — Wrong Nominal (Phase 0 High Priority Stretch)**
As a user who transferred the wrong amount, I want ExampleProject to detect the nominal mismatch and adjust my transaction details via the UCC flow.

Detailed Flow:
1. ExampleProject identifies nominal mismatch.
2. ExampleProject requests bank transfer receipt for verification via OCR.
3. ExampleProject triggers UCC Integration (Change Unique code) tool to update transaction details.
4. Fallback to Jingga handback if UCC automation is unavailable.

Acceptance Criteria: AC1: Identifies nominal mismatch. AC2: Verifies actual amount via receipt. AC3: Triggers UCC flow to adjust transaction.""",

"us_06": """\
**US-06: Smart Handback (Scenarios 1, 2, 7)**
As a user whose issue falls outside ExampleProject's execution scope, I want ExampleProject to identify my situation and hand me over to Jingga without losing context, so that I don't have to repeat my problem to the next system.

Acceptance Criteria:
- AC1: ExampleProject classifies scenario correctly (1, 2, or 7).
- AC2: ExampleProject sends handback signal with ScenarioID, TxnID, and session summary.""",

"us_07": """\
**US-07: Execution Logging & Observability**
As an engineer or ops team member, I want ExampleProject to log every step of its execution with structured data, so that I can measure success, debug failures, and audit response quality.

Detailed Log Attributes:
- session_id, secondary_user_id, scenario_classified (1–7), turns_count, response_times[]
- tools_invoked[], tool_results[], ocr_used, ocr_success, manual_probing_fallback
- refund_initiated, refund_result, handback_triggered, resolution_status
- full_conversation_log

Acceptance Criteria: AC1: Every ExampleProject session generates a structured execution log. AC2: Logs are queryable by scenario, status, and date. AC3: Ops team can pull a random 10% sample of logs for quality auditing.""",

"section_7_prompt": """\
**Section 7 — Prompt Spec: Language Lock (RESPONSE_LANGUAGE)**
The system prompt must include a language lock procedure:
- At the start of every conversation, detect and store the user's language (id/en) as RESPONSE_LANGUAGE.
- The response language MUST NOT change throughout the session, regardless of language switches by the user.
- All agent responses, system messages, and confirmation prompts must use the locked RESPONSE_LANGUAGE.""",

}

# ---------------------------------------------------------------------------
# Notes → PRD content mapper
# ---------------------------------------------------------------------------
def get_prd_content(notes: str) -> str:
    """Return relevant PRD content string(s) for the given notes reference."""
    if not notes:
        return ""
    n = notes.lower()
    parts = []

    if "23.2" in n:
        parts.append(PRD_SECTIONS["section_23_2"])
    if "18.3" in n:
        parts.append(PRD_SECTIONS["section_18_3"])
    if "step 1" in n:
        parts.append(PRD_SECTIONS["section_3_step_1"])
    if "step 2" in n:
        parts.append(PRD_SECTIONS["section_3_step_2"])
    if "step 3" in n:
        parts.append(PRD_SECTIONS["section_3_step_3"])
    if "step 4" in n:
        parts.append(PRD_SECTIONS["section_3_step_4"])
    if "step 5" in n:
        parts.append(PRD_SECTIONS["section_3_step_5"])
    if "step 6" in n:
        parts.append(PRD_SECTIONS["section_3_step_6"])
    if "step 7" in n:
        parts.append(PRD_SECTIONS["section_3_step_7"])
    if "section 3.2" in n or "section3.2" in n:
        parts.append(PRD_SECTIONS["section_3_2"])
    if "section 2 - decision" in n or "decision matrix" in n:
        parts.append(PRD_SECTIONS["section_2"])
    if "21.1" in n:
        parts.append(PRD_SECTIONS["section_21_1"])
    if "21.2" in n:
        parts.append(PRD_SECTIONS["section_21_2"])
    if "21.3" in n:
        parts.append(PRD_SECTIONS["section_21_3"])
    if "21.4" in n:
        parts.append(PRD_SECTIONS["section_21_4"])
    if "20.1" in n:
        parts.append(PRD_SECTIONS["section_20_1"])
    if "section 22" in n:
        parts.append(PRD_SECTIONS["section_22"])
    if "section 13" in n or "nfr" in n:
        parts.append(PRD_SECTIONS["section_13_nfr"])
    if "5.1" in n:
        parts.append(PRD_SECTIONS["section_5_1"])
    if "5.2" in n:
        parts.append(PRD_SECTIONS["section_5_2"])
    if "section 6" in n:
        parts.append(PRD_SECTIONS["section_6"])
    if "23.1" in n:
        parts.append(PRD_SECTIONS["section_23_1"])
    if "rollout" in n:
        parts.append(PRD_SECTIONS["rollout_strategy"])
    if "infra setup" in n:
        parts.append(PRD_SECTIONS["infra_setup"])
    if "us-01" in n:
        parts.append(PRD_SECTIONS["us_01"])
    if "us-02" in n:
        parts.append(PRD_SECTIONS["us_02"])
    if "us-03" in n:
        parts.append(PRD_SECTIONS["us_03"])
    if "us-04" in n:
        parts.append(PRD_SECTIONS["us_04"])
    if "us-05" in n:
        parts.append(PRD_SECTIONS["us_05"])
    if "us-06" in n:
        parts.append(PRD_SECTIONS["us_06"])
    if "us-07" in n:
        parts.append(PRD_SECTIONS["us_07"])
    if "section 7" in n and "prompt" in n:
        parts.append(PRD_SECTIONS["section_7_prompt"])

    return "\n\n".join(parts)

# ---------------------------------------------------------------------------
# Task data: (category, action_item, description, owner, priority, status, notes)
# priority here = original P0/P1 from sheet
# ---------------------------------------------------------------------------
TASKS = [
    ("Pre-Build / Alignment","Confirm use case with Fadlan (CS Ops)","Alignment with CS Ops lead on POC scope - refund scenarios as primary focus","Your Name","P0","Done","Section 23.2 checklist"),
    ("Pre-Build / Alignment","Confirm secondary_user_id + user metadata available in Yellow workflow builder","Coordinate with Mukhib/Fadlan that secondary_user_id is accessible from Yellow's workflow context","Muhammad Teammate","P0","To Do","Section 18.3 - critical blocker"),
    ("Pre-Build / Alignment","Obtain tool credentials: get_transaction + initiate_refund","Request service token / API key from Ops Portal engineering for both tools","Muhammad Teammate","P0","To Do","Section 23.2 checklist"),
    ("Pre-Build / Alignment","Confirm KB documents from ready-doc folder are accessible","Ensure knowledge base source documents are available and can be uploaded to RAG pipeline","Muhammad Teammate","P0","To Do","Section 23.2 checklist"),
    ("Pre-Build / Alignment","Deploy Fajri's existing code to production with unit tests","Add unit tests to Fajri's existing codebase + deploy to production","Muhammad Fajry Hamzah","P0","To Do","Section 23.2 checklist"),
    ("Pre-Build / Alignment","Create pilot whitelist (secondary_user_id list)","Coordinate with Ops to define which users are included in the POC whitelist","Fadhlan Husaini","P0","To Do","Section 3 - Step 1"),
    ("Pre-Build / Alignment","Confirm traffic split percentage from Yellow to ExampleProject","Confirm with Fadlan how Yellow workflow builder controls traffic routing to ExampleProject","Your Name","P0","Done","Rollout Strategy"),
    ("Infrastructure","Set up service accounts for ExampleProject","Create service accounts with minimal necessary permissions","Muhammad Fajry Hamzah","P0","To Do","Infra setup"),
    ("Infrastructure","Set up Postgres database","DB for conversation state + config (skills / tools / agents)","Muhammad Teammate","P0","To Do","Section 3.2"),
    ("Infrastructure","Set up Redis for session storage","Per-conversation session memory - maintain context across turns","Muhammad Teammate","P0","To Do","Section 3.2"),
    ("Infrastructure","Create repository + CI/CD pipeline","Repo setup (GitHub/GitLab) + automated deploy pipeline","Muhammad Teammate","P0","To Do",""),
    ("Infrastructure","Set up AI API credentials","Obtain + store API keys in secrets manager","Muhammad Teammate","P0","To Do",""),
    ("Infrastructure","Set up secrets manager for API keys","Centralized secrets management for all keys","Muhammad Teammate","P0","To Do","Section 22"),
    ("Infrastructure","Set up Grafana for metrics collection","Emit metrics: latency + tool call success + deflection rate","Muhammad Teammate","P1","To Do","Section 13 - NFR"),
    ("Backend - Orchestrator Core","Implement Orchestrator (PocketFlow-based)","Master agent managing intent classification + routing + conversation memory","BE","P0","To Do","Section 3.2"),
    ("Backend - Orchestrator Core","Implement whitelist verification (Step 1)","Check secondary_user_id against pilot whitelist at the start of every conversation","BE","P0","To Do","Section 3 - Step 1"),
    ("Backend - Orchestrator Core","Implement channel filtering (Step 2)","Handle Chat channel only - auto-escalate Social Media + Email users to human agents","BE","P0","To Do","Section 3 - Step 2"),
    ("Backend - Orchestrator Core","Implement NLU - intent + entity extraction from free text (Step 3)","Extract: intent / amount / bank name / date from user text input without structured UI","BE","P0","To Do","Section 3 - Step 3"),
    ("Backend - Orchestrator Core","Implement background transaction history pull (Step 3)","Call list_transactions API in the background to retrieve user's transaction history","BE","P0","To Do","Section 3 - Step 3"),
    ("Backend - Orchestrator Core","Implement AI matching - extracted details to Txn ID (Step 3)","Match extracted conversation details to a specific Transaction ID - no carousel/selection UI allowed","BE","P0","To Do","Section 3 - Step 3; No selection UI allowed"),
    ("Backend - Orchestrator Core","Implement Get User Transaction Detail API call (Step 3)","Call get_transaction to retrieve: Status Code / Date / Transfer Pool / Amount","BE","P0","To Do","Section 3 - Step 3"),
    ("Backend - Orchestrator Core","Implement Incident RTFM Check (Step 4)","Query Service Incident Database for known bank downtime or delays","BE","P0","To Do","Section 3 - Step 4"),
    ("Backend - Orchestrator Core","Implement 7-scenario classification logic (Step 5)","Classify complaint into one of 7 scenarios based on transaction details","BE","P0","To Do","Section 2 - Decision Matrix"),
    ("Backend - Orchestrator Core","Implement session memory and conversation state","Maintain full context across conversation turns - stored in Redis","BE","P0","To Do","Section 21.1"),
    ("Backend - Orchestrator Core","Implement language lock (RESPONSE_LANGUAGE)","Lock response language (id/en) at conversation start - does not change throughout session","BE","P0","To Do","Section 7 - Prompt spec"),
    ("Backend - Orchestrator Core","Implement structured execution logging (US-07)","Log per session: session_id / secondary_user_id / scenario_classified / tools_invoked / refund_result / etc.","BE","P0","To Do","US-07 + Section 5"),
    ("Backend - Scenario Flows","Implement Smart Handback to Jingga/Yellow (Scenarios 1 / 2 / 7)","Send handback signal to Yellow with: ScenarioID + TxnID + session summary","BE","P0","To Do","US-06; Section 3 - Step 5"),
    ("Backend - Scenario Flows","Implement bank receipt collection request (Scenarios 4 / 5 / 6)","ExampleProject requests user to upload their bank transfer receipt (bukti transfer)","BE","P0","To Do","Section 3 - Step 6"),
    ("Backend - Scenario Flows","Implement OCR integration to process receipt (Refund OCR API)","Process receipt image - extract: Sender Bank / Recipient Bank / Amount / Date / Sender Name / Ref Number","BE","P0","To Do","Section 3 - Step 6; Use Refund Automation OCR not legacy Jingga OCR"),
    ("Backend - Scenario Flows","Implement manual text probing fallback if OCR fails","If OCR fails/returns invalid data but user insists receipt is correct - probe manually via conversation","BE","P0","To Do","Section 3 - Step 6"),
    ("Backend - Scenario Flows","Implement Fund Verification API call (Step 7)","Verify funds are still available and unprocessed before initiating refund","BE","P0","To Do","Section 3 - Step 7; Critical safety check"),
    ("Backend - Scenario Flows","Implement explicit user confirmation before refund execution (Step 7)","Summarize proposed action + ask confirmation + only execute after confirmed. Max 3 write tool calls per session","BE","P0","To Do","Section 21.3; Section 3 - Step 7"),
    ("Backend - Scenario Flows","Implement initiate_refund API call + result notification (Step 7)","Call initiate_refund + notify user of result (success or failure with specific reason)","BE","P0","To Do","Section 3 - Step 7"),
    ("Backend - Scenario Flows","Implement Scenario 4 logic - Wrong Bank (US-02)","Detect bank pool mismatch: Expected Bank vs Actual Transfer Bank → full refund lifecycle","BE","P0","To Do","US-02"),
    ("Backend - Scenario Flows","Implement Scenario 5 logic - Double Transfer (US-03)","Detect 2 payment records for 1 TxnID → auto-identify the unprocessed one → refund (no user selection)","BE","P0","To Do","US-03"),
    ("Backend - Scenario Flows","Implement Scenario 6 logic - No Transaction Created (US-04)","Locate incoming payment in money-in records without a matching TxnID using OCR data","BE","P0","To Do","US-04"),
    ("Backend - Scenario Flows","Implement UCC flow trigger for Scenario 3 - Wrong Nominal (US-05 - Stretch)","Trigger UCC Integration tool to adjust transaction nominal. Fallback to Jingga handback if unavailable","BE","P1","To Do","US-05; Phase 0 Stretch / High Priority"),
    ("Backend - Scenario Flows","Implement generic escalation to human agent","Fallback to human if ExampleProject cannot handle (turn limit / low confidence / sensitive case)","BE","P0","To Do","Section 21.2"),
    ("Backend - API Layer","Implement POST /v1/conversations","Create new conversation. Accept: ticket_id / user_id / initial_message. Return: conversation_id","BE","P0","To Do","Section 20.1"),
    ("Backend - API Layer","Implement POST /v1/conversations/{id}/messages","Send user message → return agent response + metadata (intent / domain / confidence / tools called)","BE","P0","To Do","Section 20.1"),
    ("Backend - API Layer","Implement GET /v1/conversations/{id}","Retrieve full conversation history + tool calls + metadata + resolution status","BE","P0","To Do","Section 20.1"),
    ("Backend - API Layer","Implement PATCH /v1/conversations/{id}/status","Update status (resolved / escalated / closed) - used by ticketing system to sync state","BE","P0","To Do","Section 20.1"),
    ("Backend - API Layer","Implement GET /v1/health","Health check endpoint - returns system status / LLM status / tool availability","BE","P0","To Do","Section 20.1"),
    ("Backend - API Layer","Implement POST /v1/conversations/{id}/escalate","Force-escalate to human agents + return escalation payload","BE","P0","To Do","Section 20.1"),
    ("Backend - API Layer","Implement POST /internal/kb/documents","Upload KB document + trigger auto re-indexing. VPC-internal only","BE","P1","To Do","Section 20.1"),
    ("Backend - API Layer","Implement GET /internal/kb/documents","List all KB documents with metadata + timestamps","BE","P1","To Do","Section 20.1"),
    ("Backend - API Layer","Implement DELETE /internal/kb/documents/{id}","Remove KB document + trigger re-indexing. VPC-internal only","BE","P1","To Do","Section 20.1"),
    ("Backend - API Layer","Implement idempotency_key header support for POST endpoints","Prevent duplicate executions on retry scenarios","BE","P0","To Do","Section 20.1 - Design Principles"),
    ("Backend - API Layer","Implement sync (<5s) + async (webhook callback) mode for /messages","Support both response modes - sync as default + async with webhook callback","BE","P0","To Do","Section 20.1"),
    ("Tool Registry","Register + test tool: get_transaction (Available)","Tool for fetching transaction details. Register endpoint / schema / auth / rate limit","BE","P0","To Do","Section 21.4"),
    ("Tool Registry","Register + test tool: list_transactions (Available)","Tool for listing user transactions. Register endpoint / schema / auth / rate limit","BE","P0","To Do","Section 21.4"),
    ("Tool Registry","Register + test tool: initiate_refund (Available)","Write tool - requires explicit user confirmation. Register with safety controls","BE","P0","To Do","Section 21.4; Write tool - requires confirmation"),
    ("Tool Registry","Register + test tool: get_kyc_status (Available)","Read tool for checking KYC status","BE","P1","To Do","Section 21.4"),
    ("Tool Registry","Register + test tool: update_account (Available)","Write tool for account updates. Register with proper safety controls","BE","P1","To Do","Section 21.4"),
    ("Tool Registry","Build + register tool: escalate_to_human (Required - not yet available)","Build and register escalation tool - required for human agent handoff","BE","P0","To Do","Section 21.4; Marked as Required in PRD"),
    ("Tool Registry","Build + register tool: OCR API for bank receipt processing","Integrate Refund Automation OCR (not legacy Jingga OCR) to process bank transfer receipts","BE","P0","To Do","Section 3 - Step 6"),
    ("Tool Registry","Build + register tool: Fund Verification API","API to verify funds are still available and unprocessed","BE","P0","To Do","Section 3 - Step 7; Confirm availability with Ops Portal team"),
    ("Tool Registry","Build + register tool: RTFM / Service Incident Database query","Query known bank incidents / downtime database","BE","P0","To Do","Section 3 - Step 4; Confirm DB location with Ops"),
    ("Tool Registry","Build + register tool: UCC Integration (Stretch)","Tool to trigger Unique Code Change flow for Scenario 3","BE","P1","To Do","US-05; Stretch goal"),
    ("Tool Registry","Confirm availability: get_balance + check_bill_status","Both tools marked 'Not available yet' - confirm whether needed for POC scope or skip","BE + PM","P1","To Do","Section 21.4"),
    ("RAG / Knowledge Base","Set up vector store (pgvector)","Set up pgvector extension in Postgres for embedding storage","BE","P0","To Do","Section 3.2"),
    ("RAG / Knowledge Base","Implement embedding pipeline for KB documents","Auto-embed and index KB documents upon upload","BE","P0","To Do","Section 3.2"),
    ("RAG / Knowledge Base","Upload initial KB documents from ready-doc folder","Ops uploads all relevant knowledge base documents","Ops","P0","To Do","Section 23.2"),
    ("RAG / Knowledge Base","Test KB search relevance + tuning","Test query accuracy of the RAG pipeline - ensure retrieval is relevant","BE + Ops","P0","To Do",""),
    ("Configuration","Write Orchestrator system prompt (prompt.md)","Engineering writes the prompt defining master agent behavior + routing logic + language rules","BE + PM","P0","To Do","Section 22; Engineering owns via PR in Phase 0"),
    ("Configuration","Write tool registry config (tools.yaml)","Define all tool configurations with schema + auth + rate limits","BE","P0","To Do","Section 22"),
    ("Configuration","Write LLM settings config (llm.yaml)","Define primary (Gemini 3.0) + fallback (Gemini 2.5) + temperature + token settings","BE","P0","To Do","Section 22"),
    ("Configuration","Write guardrails config (guardrails.yaml)","Define: PII masking / max loops / escalation triggers / amount thresholds","BE","P0","To Do","Section 22"),
    ("Configuration","Write feature flags config (features.yaml)","Feature flags to control rollout behavior","BE","P0","To Do","Section 22"),
    ("Configuration","Set up Yellow workflow builder to call ExampleProject endpoint","Configure HTTP call from Yellow to ExampleProject /v1/conversations endpoint. Coordinate with Mukhib/Fadlan","BE + Ops","P0","To Do","Section 18.3; Critical integration point"),
    ("Configuration","Set up pilot whitelist in Yellow workflow","Add secondary_user_id check in Yellow workflow to route only whitelisted users to ExampleProject","BE + Ops","P0","To Do","Section 3 - Step 1"),
    ("Analytics / Measurement","Set up deflection rate measurement","Tag each session with FINA_RESOLVED or HANDBACK_TO_AGENT in execution logs - query for deflection %","BE","P0","To Do","Section 5.1"),
    ("Analytics / Measurement","Set up response latency tracking","Compute avg latency per turn from response_times[] array in execution logs","BE","P0","To Do","Section 5.2; Target: avg < 8s per turn"),
    ("Analytics / Measurement","Set up tool call accuracy tracking","Compute success rate from tools_invoked[] vs tool_results[] per session","BE","P0","To Do","Section 5.2; Target: > 95%"),
    ("Analytics / Measurement","Set up refund execution success tracking","Track refund_result success rate from execution logs","BE","P0","To Do","Section 5.2; Target: > 90%"),
    ("Analytics / Measurement","Set up CSAT measurement via Yellow.ai widget","Confirm CSAT collection setup via Yellow's existing CSAT widget post-resolution","Ops + BE","P0","To Do","Section 5.1; Target: > 7.0/10"),
    ("Analytics / Measurement","Set up Ops daily audit process - 10% log sampling","Ops reviews 10% random sample of full_conversation_log daily for quality + hallucination check","Ops + PM","P0","To Do","Section 5.2; Target: < 2% hallucination rate"),
    ("Analytics / Measurement","Set up scenario classification accuracy tracking","Ops verifies scenario_classified in logs against actual user intent","Ops","P0","To Do","Section 5.2; Target: > 90% accuracy"),
    ("Testing / QA","Write test cases for US-01: Orchestrator classification (all 7 scenarios)","Test that ExampleProject correctly classifies each scenario from free-text input","QA + BE","P0","To Do","US-01"),
    ("Testing / QA","Write test cases for US-02: Scenario 4 - Wrong Bank Refund (full flow)","Test end-to-end: mismatch detection → receipt → OCR → fund verify → confirm → refund","QA + BE","P0","To Do","US-02"),
    ("Testing / QA","Write test cases for US-03: Scenario 5 - Double Transfer","Test: detect duplicate → OCR → auto-identify unprocessed → fund verify → refund","QA + BE","P0","To Do","US-03"),
    ("Testing / QA","Write test cases for US-04: Scenario 6 - No Transaction Created","Test: no TxnID match → mandatory receipt → OCR → locate in money-in records → refund","QA + BE","P0","To Do","US-04"),
    ("Testing / QA","Write test cases for US-05: Scenario 3 - UCC flow (Stretch)","Test nominal mismatch → receipt verify → UCC trigger","QA + BE","P1","To Do","US-05; Stretch"),
    ("Testing / QA","Write test cases for US-06: Smart Handback (Scenarios 1 / 2 / 7)","Test that handback payload contains ScenarioID + TxnID + session summary correctly","QA + BE","P0","To Do","US-06"),
    ("Testing / QA","Write test cases for US-07: Execution Logging","Verify all required log attributes are populated correctly per session","QA + BE","P0","To Do","US-07"),
    ("Testing / QA","OCR accuracy testing","Test OCR with various receipt quality levels - clear / blurry / handwritten","QA","P0","To Do",""),
    ("Testing / QA","End-to-end integration testing (all 7 scenarios)","Full flow test in staging environment with mock/real user data","QA + BE","P0","To Do",""),
    ("Testing / QA","Performance / load testing","Verify response latency < 8s per turn under load conditions","QA + BE","P0","To Do","Section 5.2"),
    ("Testing / QA","Security testing - PII masking verification","Verify NIK / names / phone numbers are masked in all logs and responses before leaving the system","QA + BE","P0","To Do","Section 13 - NFR"),
    ("Testing / QA","Write tool safety testing","Verify that initiate_refund never executes without explicit user confirmation","QA + BE","P0","To Do","Section 21.3; Zero unintended executions required"),
    ("Testing / QA","Yellow integration end-to-end testing","Test full flow: Yellow workflow → ExampleProject endpoint → response back to Yellow → customer","QA + BE + Ops","P0","To Do",""),
    ("PM / Product","Finalize + publish Definition of Done (DoD) checklist for Phase 0","Lock down Go/No-Go criteria with Engineering + Ops Lead","PM","P0","To Do","Section 6 + 23.1"),
    ("PM / Product","Schedule daily standups for the 2-week POC sprint","Daily progress coordination during the sprint","PM","P0","To Do",""),
    ("PM / Product","Coordinate pilot launch with Ops team + Fadlan","Brief Ops team on what to expect during pilot + how to audit logs","PM + Ops","P0","To Do",""),
    ("PM / Product","Monitor metrics during Week 1 production pilot","Track KPIs daily: deflection rate / latency / tool success / CSAT","PM + Ops","P0","To Do","Section 23.1 - measured over first 1 week"),
    ("PM / Product","Go/No-Go decision after Week 1 pilot","Review metrics vs threshold - decide to proceed to Phase 1 or iterate","PM + Ops","P0","To Do","Section 23.1"),
]

CATEGORY_PREFIX = {
    "Pre-Build / Alignment":      "[Pre-Build]",
    "Infrastructure":             "[Infra]",
    "Backend - Orchestrator Core":"[BE-Core]",
    "Backend - Scenario Flows":   "[BE-Flow]",
    "Backend - API Layer":        "[BE-API]",
    "Tool Registry":              "[Tools]",
    "RAG / Knowledge Base":       "[RAG]",
    "Configuration":              "[Config]",
    "Analytics / Measurement":    "[Analytics]",
    "Testing / QA":               "[QA]",
    "PM / Product":               "[PM]",
}

# P0 → high (2), P1 → normal (3)
PRIORITY_MAP = {"P0": 2, "P1": 3}

def build_description(description: str, notes: str) -> str:
    prd_content = get_prd_content(notes)
    parts = [description]
    if notes:
        parts.append(f"**PRD Reference:** {notes}")
    if prd_content:
        parts.append("---")
        parts.append(prd_content)
    parts.append("---")
    parts.append(f"*Source: ExampleProject PoC Action Item Sheet | [PRD: Agentic AI Platform for Support Operations]({PRD_URL})*")
    return "\n\n".join(parts)

def get_all_tasks(session):
    """Fetch all tasks in the list (paginated)."""
    tasks = []
    page = 0
    while True:
        url = f"https://api.clickup.com/api/v2/list/{LIST_ID}/task"
        r = session.get(url, params={"page": page, "include_closed": "true"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        batch = data.get("tasks", [])
        tasks.extend(batch)
        if data.get("last_page", True):
            break
        page += 1
    return tasks

def update_task(session, task_id, description, priority):
    url = f"https://api.clickup.com/api/v2/task/{task_id}"
    payload = {"description": description, "priority": priority}
    r = session.put(url, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def main():
    session = requests.Session()
    session.headers.update({"Authorization": TOKEN, "Content-Type": "application/json"})

    print("Fetching existing tasks from ClickUp...")
    existing_tasks = get_all_tasks(session)
    print(f"Found {len(existing_tasks)} tasks in list.\n")

    # Build lookup: task name → task_id
    task_lookup = {t["name"]: t["id"] for t in existing_tasks}

    success, errors = 0, []

    for i, (category, action_item, description, owner, priority_str, status_str, notes) in enumerate(TASKS, 1):
        prefix = CATEGORY_PREFIX.get(category, "[Misc]")
        name = f"{prefix} {action_item}"
        desc = build_description(description, notes)
        priority = PRIORITY_MAP.get(priority_str, 3)

        task_id = task_lookup.get(name)
        if not task_id:
            print(f"[{i:02d}/96] ⚠ NOT FOUND: {name[:70]}")
            errors.append((i, name, "task not found in ClickUp"))
            continue

        print(f"[{i:02d}/96] Updating: {name[:70]}...", end=" ", flush=True)
        try:
            update_task(session, task_id, desc, priority)
            print("✓")
            success += 1
        except Exception as e:
            print(f"✗ ERROR: {e}")
            errors.append((i, name, str(e)))

        time.sleep(0.25)

    print(f"\n{'='*60}")
    print(f"Done: {success}/96 tasks updated.")
    if errors:
        print(f"\nFailed ({len(errors)}):")
        for idx, name, err in errors:
            print(f"  [{idx}] {name[:60]} → {err}")

if __name__ == "__main__":
    main()
