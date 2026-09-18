from __future__ import annotations

import json
import math
from typing import Any

from app.models import BatteryInput, DirectiveInterpretation


class InterpretationError(ValueError):
    pass


SUPPORTED = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def _number(value: Any, name: str, minimum: float, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InterpretationError(f"{name} must be a finite number")
    result = float(value)
    if result < minimum or (maximum is not None and result > maximum):
        raise InterpretationError(f"{name} is outside allowed range")
    return result


def _hours(value: Any) -> list[int]:
    if not isinstance(value, list) or not value:
        raise InterpretationError("hours must be a non-empty array")
    if any(isinstance(hour, bool) or not isinstance(hour, int) or hour < 0 or hour > 23 for hour in value):
        raise InterpretationError("hours must contain integers from 0 through 23")
    return sorted(set(value))


def parse_and_normalize(
    content: str, note_count: int, battery: BatteryInput
) -> list[DirectiveInterpretation]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) > 1 and lines[-1].strip().startswith("```"):
            text = "\n".join(lines[1:-1]).strip()
        else:
            text = "\n".join(lines[1:]).strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise InterpretationError("LLM returned malformed JSON") from exc

    entries = payload.get("interpretations") if isinstance(payload, dict) else None
    if not isinstance(entries, list) or len(entries) != note_count:
        raise InterpretationError("LLM must return one interpretation per note")

    normalized: list[DirectiveInterpretation] = []
    for expected_index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.get("note_index") != expected_index:
            raise InterpretationError("interpretations must use consecutive note_index values")
        directive_type = entry.get("directive_type")
        if directive_type not in SUPPORTED:
            raise InterpretationError("LLM returned unsupported directive_type")
        if "structured_adjustment" not in entry:
            raise InterpretationError("LLM response is missing structured_adjustment")
        explanation = entry.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 500:
            raise InterpretationError("explanation must be a non-empty string")

        adjustment = entry.get("structured_adjustment")
        if directive_type == "no_op":
            if adjustment is not None:
                raise InterpretationError("no_op structured_adjustment must be null")
            normalized.append(DirectiveInterpretation(
                note_index=expected_index,
                applies=False,
                directive_type="no_op",
                structured_adjustment=None,
                explanation=explanation.strip(),
            ))
            continue

        if not isinstance(adjustment, dict):
            raise InterpretationError("structured_adjustment must be an object")
        hours = _hours(adjustment.get("hours"))
        allowed_keys = {"hours"}
        clean: dict[str, Any] = {"hours": hours}
        if directive_type == "solar_reduction":
            allowed_keys.add("factor")
            clean["factor"] = _number(adjustment.get("factor"), "factor", 0, 1)
        elif directive_type == "minimum_battery_reserve":
            allowed_keys.add("minimum_energy_kwh")
            clean["minimum_energy_kwh"] = _number(
                adjustment.get("minimum_energy_kwh"),
                "minimum_energy_kwh",
                0,
                battery.capacity_kwh,
            )
        elif directive_type == "max_grid_window":
            allowed_keys.add("max_grid_kwh")
            clean["max_grid_kwh"] = _number(adjustment.get("max_grid_kwh"), "max_grid_kwh", 0)
        if set(adjustment) != allowed_keys:
            raise InterpretationError("structured_adjustment has missing or unsupported fields")

        normalized.append(DirectiveInterpretation(
            note_index=expected_index,
            applies=True,
            directive_type=directive_type,
            structured_adjustment=clean,
            explanation=explanation.strip(),
        ))
    return normalized
