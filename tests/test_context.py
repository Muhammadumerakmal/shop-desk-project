"""FR-2: the customer lives in context, tools read it through the wrapper, prompts never see it."""

from pathlib import Path

from shop_desk.instructions import resolved_prompt
from shop_desk.tools import DESK_TOOLS, add_to_basket

from tests.conftest import invoke

PROMPT_SOURCES = [Path("shop_desk/instructions.py")]


def test_no_tool_schema_exposes_the_wrapper():
    for tool in DESK_TOOLS:
        props = tool.params_json_schema.get("properties", {})
        assert "ctx" not in props and "context" not in props, tool.name
        assert "RunContextWrapper" not in str(tool.params_json_schema)


async def test_tool_reads_context_through_wrapper(make_context):
    ctx = make_context()
    await invoke(add_to_basket, ctx, sku="KTL-01", qty=2)
    assert ctx.basket == {"KTL-01": 2}


async def test_resolved_prompt_has_no_customer_id_or_tier(make_session):
    session = make_session(tier="regular")
    prompt = await resolved_prompt(session.agents.desk, session.context)
    assert session.context.customer_id not in prompt
    assert "regular" not in prompt.lower()


def test_prompt_sources_never_reference_customer_fields():
    for source in PROMPT_SOURCES:
        text = source.read_text(encoding="utf-8")
        assert "customer_id" not in text and ".tier" not in text, source
