import json
from pathlib import Path

import pytest

from app.guardrails import parse_and_normalize
from app.models import OptimizeRequest
from app.optimizer import optimize


CASE_FILE = Path(__file__).parents[1] / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
CASES = json.loads(CASE_FILE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_public_case_output(case):
    request = OptimizeRequest.model_validate(case["input"])
    expected = case["expected_output"]
    directives = parse_and_normalize(
        json.dumps({"interpretations": expected["directive_interpretation"]}),
        len(request.operator_notes),
        request.battery,
    )

    response = optimize(request, directives)

    assert [item.directive_type for item in response.directive_interpretation] == [
        item["directive_type"] for item in expected["directive_interpretation"]
    ]
    assert [item.structured_adjustment for item in response.directive_interpretation] == [
        item["structured_adjustment"] for item in expected["directive_interpretation"]
    ]
    assert len(response.hourly_plan) == 24
    assert response.total_cost_bdt == pytest.approx(expected["total_cost_bdt"], abs=0.01)
