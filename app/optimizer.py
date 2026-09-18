from __future__ import annotations

import numpy as np
from scipy.optimize import linprog

from app.models import DirectiveInterpretation, HourlyPlan, OptimizeRequest, OptimizeResponse


class OptimizationError(ValueError):
    pass


def optimize(request: OptimizeRequest, directives: list[DirectiveInterpretation]) -> OptimizeResponse:
    hours = sorted(request.hours, key=lambda item: item.hour)
    battery = request.battery
    solar_factor = np.ones(24)
    reserve = np.full(24, battery.minimum_energy_kwh)
    no_charge: set[int] = set()
    no_discharge: set[int] = set()
    grid_cap = np.full(24, np.inf)

    for directive in directives:
        if not directive.applies or directive.structured_adjustment is None:
            continue
        adjustment = directive.structured_adjustment
        selected = adjustment["hours"]
        if directive.directive_type == "solar_reduction":
            solar_factor[selected] = np.minimum(solar_factor[selected], adjustment["factor"])
        elif directive.directive_type == "minimum_battery_reserve":
            reserve[selected] = np.maximum(reserve[selected], adjustment["minimum_energy_kwh"])
        elif directive.directive_type == "no_charge_window":
            no_charge.update(selected)
        elif directive.directive_type == "no_discharge_window":
            no_discharge.update(selected)
        elif directive.directive_type == "max_grid_window":
            grid_cap[selected] = np.minimum(grid_cap[selected], adjustment["max_grid_kwh"])

    demand = np.array([item.demand_kwh for item in hours])
    effective_solar = np.array([item.solar_kwh for item in hours]) * solar_factor
    tariffs = np.array([item.tariff_bdt_per_kwh for item in hours])
    objective = np.concatenate([tariffs, np.zeros(48)])

    # Variables: grid[24], solar_used[24], net_battery_charge[24].
    equality = np.zeros((25, 72))
    equality[:24, :24] = np.eye(24)
    equality[:24, 24:48] = np.eye(24)
    equality[:24, 48:72] = -np.eye(24)
    equality[24, 48:72] = 1
    equality_rhs = np.concatenate([demand, [0]])

    inequalities: list[np.ndarray] = []
    inequality_rhs: list[float] = []
    for hour in range(24):
        cumulative = np.zeros(72)
        cumulative[48:48 + hour + 1] = 1
        inequalities.append(cumulative)
        inequality_rhs.append(battery.capacity_kwh - battery.initial_energy_kwh)
        inequalities.append(-cumulative)
        inequality_rhs.append(battery.initial_energy_kwh - reserve[hour])

    bounds: list[tuple[float | None, float | None]] = []
    bounds.extend((0, None if np.isinf(grid_cap[h]) else float(grid_cap[h])) for h in range(24))
    bounds.extend((0, float(effective_solar[h])) for h in range(24))
    for hour in range(24):
        lower = 0 if hour in no_discharge else -battery.max_discharge_kwh_per_hour
        upper = 0 if hour in no_charge else battery.max_charge_kwh_per_hour
        bounds.append((lower, upper))

    result = linprog(
        objective,
        A_ub=np.array(inequalities),
        b_ub=np.array(inequality_rhs),
        A_eq=equality,
        b_eq=equality_rhs,
        bounds=bounds,
        method="highs",
    )
    if not result.success:
        raise OptimizationError("scenario and directives have no feasible schedule")

    grid = result.x[:24]
    solar_used = result.x[24:48]
    battery_net = result.x[48:72]
    energy = battery.initial_energy_kwh
    plan: list[HourlyPlan] = []
    for hour in range(24):
        value = 0.0 if abs(battery_net[hour]) < 1e-7 else float(battery_net[hour])
        energy += value
        action = "charge" if value > 0 else "discharge" if value < 0 else "idle"
        plan.append(HourlyPlan(
            hour=hour,
            grid_kwh=_clean(grid[hour]),
            solar_used_kwh=_clean(solar_used[hour]),
            battery_action=action,
            battery_kwh=_clean(abs(value)),
            battery_energy_after_kwh=_clean(energy),
        ))

    total_grid = float(np.sum(grid))
    total_cost = float(np.dot(grid, tariffs))
    response = OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=plan,
        total_grid_kwh=_clean(total_grid),
        total_cost_bdt=_clean(total_cost),
        peak_grid_kwh=_clean(float(np.max(grid))),
        plan_summary=f"24-hour minimum-cost feasible schedule; {sum(item.applies for item in directives)} directive(s) applied.",
    )
    validate_plan(request, response, solar_factor, reserve, no_charge, no_discharge, grid_cap)
    return response


def _clean(value: float) -> float:
    result = round(float(value), 6)
    return 0.0 if abs(result) < 1e-7 else result


def validate_plan(
    request: OptimizeRequest,
    response: OptimizeResponse,
    solar_factor: np.ndarray,
    reserve: np.ndarray,
    no_charge: set[int],
    no_discharge: set[int],
    grid_cap: np.ndarray,
) -> None:
    tolerance = 0.01
    source = {item.hour: item for item in request.hours}
    before = request.battery.initial_energy_kwh
    for item in response.hourly_plan:
        hour = source[item.hour]
        signed = item.battery_kwh if item.battery_action == "charge" else -item.battery_kwh
        if item.battery_action == "idle" and item.battery_kwh > tolerance:
            raise OptimizationError("replay failed: idle battery has nonzero energy")
        balance = item.grid_kwh + item.solar_used_kwh - signed
        if abs(balance - hour.demand_kwh) > tolerance:
            raise OptimizationError("replay failed: hourly energy imbalance")
        if item.solar_used_kwh > hour.solar_kwh * solar_factor[item.hour] + tolerance:
            raise OptimizationError("replay failed: solar limit")
        if item.grid_kwh > grid_cap[item.hour] + tolerance:
            raise OptimizationError("replay failed: grid limit")
        if item.hour in no_charge and signed > tolerance:
            raise OptimizationError("replay failed: no-charge window")
        if item.hour in no_discharge and signed < -tolerance:
            raise OptimizationError("replay failed: no-discharge window")
        if signed > request.battery.max_charge_kwh_per_hour + tolerance:
            raise OptimizationError("replay failed: charge rate")
        if signed < -request.battery.max_discharge_kwh_per_hour - tolerance:
            raise OptimizationError("replay failed: discharge rate")
        expected = before + signed
        if abs(item.battery_energy_after_kwh - expected) > tolerance:
            raise OptimizationError("replay failed: battery transition")
        if item.battery_energy_after_kwh < reserve[item.hour] - tolerance:
            raise OptimizationError("replay failed: battery reserve")
        if item.battery_energy_after_kwh > request.battery.capacity_kwh + tolerance:
            raise OptimizationError("replay failed: battery capacity")
        before = item.battery_energy_after_kwh
    if abs(before - request.battery.initial_energy_kwh) > tolerance:
        raise OptimizationError("replay failed: end-of-day battery neutrality")
    total_grid = sum(item.grid_kwh for item in response.hourly_plan)
    total_cost = sum(item.grid_kwh * source[item.hour].tariff_bdt_per_kwh for item in response.hourly_plan)
    peak_grid = max(item.grid_kwh for item in response.hourly_plan)
    if abs(response.total_grid_kwh - total_grid) > tolerance:
        raise OptimizationError("replay failed: total grid")
    if abs(response.total_cost_bdt - total_cost) > tolerance:
        raise OptimizationError("replay failed: total cost")
    if abs(response.peak_grid_kwh - peak_grid) > tolerance:
        raise OptimizationError("replay failed: peak grid")
