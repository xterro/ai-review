import pytest

from ai_review.config import settings
from ai_review.libs.config.review import ReviewMode
from ai_review.services.review.internal.summary.schema import SummaryCommentSchema
from ai_review.services.review.runner.summary import SummaryReviewRunner
from ai_review.services.vcs.types import ReviewCommentSchema
from ai_review.tests.fixtures.services.cost import FakeCostService
from ai_review.tests.fixtures.services.diff import FakeDiffService
from ai_review.tests.fixtures.services.policy import FakePolicyService
from ai_review.tests.fixtures.services.prompt import FakePromptService
from ai_review.tests.fixtures.services.review.gateway.review_comment_gateway import FakeReviewCommentGateway
from ai_review.tests.fixtures.services.review.gateway.review_direct_llm_gateway import FakeReviewDirectLLMGateway
from ai_review.tests.fixtures.services.review.internal.summary import FakeSummaryCommentService
from ai_review.tests.fixtures.services.vcs import FakeVCSClient


@pytest.mark.asyncio
async def test_run_happy_path(
        summary_review_runner: SummaryReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_diff_service: FakeDiffService,
        fake_cost_service: FakeCostService,
        fake_prompt_service: FakePromptService,
        fake_policy_service: FakePolicyService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should render all changed files, call LLM and post summary comment."""
    fake_review_comment_gateway.responses["get_summary_comments"] = []

    await summary_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert "get_review_info" in vcs_calls

    assert any(call[0] == "render_files" for call in fake_diff_service.calls)
    assert any(call[0] == "apply_for_files" for call in fake_policy_service.calls)
    assert any(call[0] == "build_summary_request" for call in fake_prompt_service.calls)
    assert any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert any(call[0] == "process_summary_comment" for call in fake_review_comment_gateway.calls)

    assert any(call[0] == "aggregate" for call in fake_cost_service.calls)


@pytest.mark.asyncio
async def test_run_skips_when_existing_summary_comments(
        summary_review_runner: SummaryReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should skip summary review if summary comment already exists."""
    fake_review_comment_gateway.responses["get_summary_comments"] = [
        ReviewCommentSchema(id="1", body="#ai-review-summary existing"),
    ]

    await summary_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert vcs_calls == []
    assert not any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)


@pytest.mark.asyncio
async def test_run_skips_when_no_changed_files(
        summary_review_runner: SummaryReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_policy_service: FakePolicyService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
):
    """Should skip when no changed files remain after policy filtering."""
    fake_policy_service.responses["apply_for_files"] = []
    fake_review_comment_gateway.responses["get_summary_comments"] = []

    await summary_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert "get_review_info" in vcs_calls
    assert any(call[0] == "apply_for_files" for call in fake_policy_service.calls)


@pytest.mark.asyncio
async def test_run_skips_when_empty_summary_from_llm(
        summary_review_runner: SummaryReviewRunner,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_summary_comment_service: FakeSummaryCommentService,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should skip posting comment if LLM output is empty."""
    fake_review_comment_gateway.responses["get_summary_comments"] = []
    fake_summary_comment_service.responses["parse_model_output"] = SummaryCommentSchema(text="")

    await summary_review_runner.run()

    assert any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert not any(call[0] == "process_summary_comment" for call in fake_review_comment_gateway.calls)


@pytest.mark.asyncio
async def test_run_custom_text_mode(
        summary_review_runner: SummaryReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_diff_service: FakeDiffService,
        fake_prompt_service: FakePromptService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
        monkeypatch: pytest.MonkeyPatch,
):
    """Should use build_custom_text_request and skip diff rendering in CUSTOM_TEXT mode."""
    monkeypatch.setattr(settings.review, "mode", ReviewMode.CUSTOM_TEXT)
    monkeypatch.setattr(settings.review, "custom_text", "Review security issues")
    fake_review_comment_gateway.responses["get_summary_comments"] = []

    await summary_review_runner.run()

    assert not any(call[0] == "render_files" for call in fake_diff_service.calls)
    assert any(call[0] == "build_custom_text_request" for call in fake_prompt_service.calls)
    assert any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert any(call[0] == "process_summary_comment" for call in fake_review_comment_gateway.calls)


@pytest.mark.asyncio
async def test_run_custom_text_mode_skips_when_empty(
        summary_review_runner: SummaryReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
        monkeypatch: pytest.MonkeyPatch,
):
    """Should skip when CUSTOM_TEXT mode is set but custom_text is empty."""
    monkeypatch.setattr(settings.review, "mode", ReviewMode.CUSTOM_TEXT)
    monkeypatch.setattr(settings.review, "custom_text", "")
    fake_review_comment_gateway_responses = fake_review_comment_gateway.responses
    fake_review_comment_gateway_responses["get_summary_comments"] = []

    await summary_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert vcs_calls == []
    assert not any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
