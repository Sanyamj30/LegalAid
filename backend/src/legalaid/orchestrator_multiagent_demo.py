"""
legalaid/orchestrator_multiagent_demo.py
=========================================
End-to-End Multi-Agent Orchestration Demo
-----------------------------------------
Demonstrates the full interoperability pipeline:
  1. Initialize thread-safe LegalBlackboard & A2A Message Broker.
  2. Start ObservabilityBridge monitoring on the A2A bus.
  3. Run Google ADK ReAct Legal Researcher (invoking FastMCP hybrid search tool).
  4. ADK Researcher posts findings to Blackboard and dispatches A2AMessageEnvelope.
  5. CrewAI Synthesis Team receives A2A handoff trigger, pulls GroundingManifest, 
     and executes synthesis with step and task callbacks.
  6. Emits structured Pydantic output (LegalSynthesisResponse) and logs telemetry.
"""

from __future__ import annotations

import asyncio
import logging
from pprint import pprint

from legalaid.agents.adk_researcher import ADKReActLegalResearcher, PromptProfile
from legalaid.agents.crewai_synthesis import CrewAILegalSynthesisTeam
from legalaid.blackboard.blackboard import BlackboardPlane, LegalBlackboard
from legalaid.observability.telemetry_bridge import ObservabilityBridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("legalaid.demo")


async def run_multiagent_workflow(
    question: str,
    prompt_profile: PromptProfile = PromptProfile.REACT_LOOP,
) -> None:
    trace_id = "LA-DEMO1234"
    logger.info("=== Starting LegalAId Multi-Agent Orchestration Workflow ===")
    logger.info("Trace ID: %s | Question: %s", trace_id, question)

    # 1. Initialize Blackboard & Telemetry
    blackboard = LegalBlackboard()
    telemetry = ObservabilityBridge(trace_id=trace_id)
    await telemetry.start_a2a_monitoring()

    # Pre-populate intake facts on Blackboard
    await blackboard.post_entry(
        plane=BlackboardPlane.MATTER_FACTS,
        author_agent="system_intake",
        author_framework="SYSTEM",
        data={
            "jurisdiction": "India",
            "statutory_domain": "Commercial / Contract Law",
            "summary": question,
        },
    )

    # 2. Instantiate Agents
    adk_researcher = ADKReActLegalResearcher(
        blackboard=blackboard,
        trace_id=trace_id,
    )
    crewai_synthesis = CrewAILegalSynthesisTeam(
        blackboard=blackboard,
        trace_id=trace_id,
    )

    try:
        # 3. Concurrently kick off Synthesis listener and ADK Research runner
        logger.info("Launching synthesis listener & ADK research loop...")
        synthesis_task = asyncio.create_task(
            crewai_synthesis.listen_and_run(question=question, trigger_timeout=120.0)
        )

        # ADK executes research and automatically fires A2A handoff when done
        await adk_researcher.execute_research(
            question=question,
            prompt_profile=prompt_profile,
        )

        # 4. Await final structured synthesis output
        final_response = await synthesis_task

        logger.info("=== Multi-Agent Workflow Completed Successfully ===")
        print("\n" + "=" * 80)
        print("FINAL STRUCTURED OUTPUT (LegalSynthesisResponse):")
        print("=" * 80)
        pprint(final_response.model_dump(mode="json"))

        # 5. Persist final Blackboard snapshot
        await telemetry.persist_blackboard_snapshot(blackboard)

    finally:
        await telemetry.stop()


if __name__ == "__main__":
    test_query = (
        "Under the Indian Contract Act, 1872, what are the legal consequences "
        "of an agreement in restraint of trade under Section 27?"
    )
    asyncio.run(run_multiagent_workflow(test_query, prompt_profile=PromptProfile.REACT_LOOP))

