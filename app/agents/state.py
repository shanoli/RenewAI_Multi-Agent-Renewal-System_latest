"""
LangGraph State definition for RenewAI multi-agent workflow.
"""
from typing import TypedDict, List, Optional, Dict, Any
from typing_extensions import Annotated
import operator


class RenewalState(TypedDict):
    # Policy + Customer context
    policy_id: str
    customer_id: str
    customer_name: str
    customer_age: int
    customer_city: str
    customer_phone: Optional[str]
    customer_email: Optional[str]
    preferred_channel: str
    preferred_language: str
    segment: str
    policy_type: str
    sum_assured: int
    annual_premium: int
    premium_due_date: str
    payment_mode: str
    fund_value: Optional[int]
    policy_status: str

    # Workflow state
    current_node: str
    selected_channel: Optional[str]
    channel_justification: Optional[str]
    critique_a_result: Optional[str]       # APPROVED / OVERRIDE
    execution_plan: Optional[Dict]
    draft_message: Optional[str]
    greeting: Optional[str]
    closing: Optional[str]
    final_message: Optional[str]
    critique_b_result: Optional[str]       # APPROVED / REVISION_NEEDED / ESCALATE

    # Escalation / distress
    distress_flag: bool
    objection_count: int
    mode: str                              # AI or HUMAN_CONTROL
    escalate: bool
    escalation_reason: Optional[str]

    # Interaction history (last N interactions)
    interaction_history: List[Dict]

    # RAG retrieved context
    rag_policy_docs: Optional[str]
    rag_objections: Optional[str]
    rag_regulations: Optional[str]

    # Output / audit
    messages_sent: Annotated[List[str], operator.add]
    audit_trail: Annotated[List[str], operator.add]
    active_versions: Dict[str, int] # agent_name -> version
    error: Optional[str]
