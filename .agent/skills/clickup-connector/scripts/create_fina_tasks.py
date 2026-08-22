#!/usr/bin/env python3
"""
Creates ExampleProject PoC Action Items in ClickUp from the Google Sheet data.
List ID: 901614365044 (List Card, folder: 2026 - HOP AI Agent)
"""

import json
import os
import sys
import time
import requests

TOKEN = "pk_88954348_1FB6GNJATX3QOVXKP38TABFTA3U9Q0XS"
LIST_ID = "901614365044"

# User ID mapping
OWNER   = 88954348
MUKHIB  = 37588486
HAMZAH  = 100960269
FADHLAN = 37519322

def resolve_owner(owner: str) -> list:
    """Map owner string to list of ClickUp user IDs."""
    o = owner.strip()
    # Named individuals
    if "Your Name" in o:
        return [OWNER]
    if "Muhammad Teammate" in o:
        return [MUKHIB]
    if "Muhammad Fajry Hamzah" in o:
        return [HAMZAH]
    if "Fadhlan Husaini" in o:
        return [FADHLAN]

    # Generic roles — parse all tokens
    parts = [p.strip() for p in o.replace("+", ",").split(",")]
    ids = set()
    for part in parts:
        p = part.strip().lower()
        if p == "pm":
            ids.add(OWNER)
        elif p == "ops":
            ids.add(FADHLAN)
        # BE and QA → empty (per user instructions)
    return sorted(ids)

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

PRIORITY_MAP = {"P0": 1, "P1": 2}
STATUS_MAP   = {"Done": "done", "To Do": "backlog"}

# All 96 tasks: (category, action_item, description, owner, priority, status, notes)
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

def build_description(description: str, notes: str) -> str:
    parts = [description]
    if notes:
        parts.append(f"\n**PRD Reference:** {notes}")
    parts.append("\n\n*Source: ExampleProject PoC Action Item Sheet | PRD: Agentic AI Platform for Support Operations*")
    return "\n".join(parts)

def create_task(session, list_id, name, description, assignees, priority, status):
    url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
    payload = {
        "name": name,
        "description": description,
        "priority": priority,
        "status": status,
    }
    if assignees:
        payload["assignees"] = assignees
    resp = session.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def main():
    session = requests.Session()
    session.headers.update({"Authorization": TOKEN, "Content-Type": "application/json"})

    total = len(TASKS)
    success = 0
    errors = []

    for i, (category, action_item, description, owner, priority_str, status_str, notes) in enumerate(TASKS, 1):
        prefix = CATEGORY_PREFIX.get(category, "[Misc]")
        name = f"{prefix} {action_item}"
        desc = build_description(description, notes)
        assignees = resolve_owner(owner)
        priority = PRIORITY_MAP.get(priority_str, 3)
        status = STATUS_MAP.get(status_str, "backlog")

        print(f"[{i:02d}/{total}] Creating: {name[:80]}...", end=" ", flush=True)
        try:
            result = create_task(session, LIST_ID, name, desc, assignees, priority, status)
            task_id = result.get("id", "?")
            print(f"✓ {task_id}")
            success += 1
        except Exception as e:
            print(f"✗ ERROR: {e}")
            errors.append((i, name, str(e)))

        # Small delay to avoid rate limiting
        time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"Done: {success}/{total} tasks created.")
    if errors:
        print(f"\nFailed ({len(errors)}):")
        for idx, name, err in errors:
            print(f"  [{idx}] {name[:60]} → {err}")

if __name__ == "__main__":
    main()
