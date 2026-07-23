from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event, Thread

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field, model_validator

from .calculator import sector_allowed_licenses, sector_names_for_count
from .models import ShiftRule


SLOT_MINUTES = 15
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES
DAY_START_MINUTES = 7 * 60
LICENSES = ("FL", "APS", "ACS")
COVERAGE_WEIGHT = 1_000
CP_SAT_WORKERS = 8
CancelCallback = Callable[[], bool]


class FutureCalculatorRequest(BaseModel):
    calculation_mode: str = "staff_to_coverage"
    total_people: int = Field(ge=1, le=80)
    fl_count: int = Field(ge=0, le=80)
    aps_count: int = Field(default=0, ge=0, le=80)
    acs_count: int = Field(ge=0, le=80)
    requested_sector_counts: list[int]
    shifts: list[ShiftRule]
    min_continuous_work_minutes: int = Field(default=60, ge=15, le=240)
    max_continuous_work_minutes: int = Field(default=120, ge=15, le=240)
    rest_ratio_percent: int = Field(default=50, ge=0, le=100)
    allow_quarter_hour_shift_starts: bool = True
    time_limit_seconds: int = Field(default=60, ge=1, le=600)

    @model_validator(mode="after")
    def validate_future_request(self) -> "FutureCalculatorRequest":
        if self.calculation_mode not in {"staff_to_coverage", "demand_to_staff"}:
            raise ValueError("Neznan način futurističnega izračuna.")
        if self.fl_count + self.aps_count + self.acs_count != self.total_people:
            raise ValueError("FL + APS + ACS mora biti enako skupnemu številu ljudi.")
        if len(self.requested_sector_counts) != SLOTS_PER_DAY:
            raise ValueError("Futuristični profil mora vsebovati 96 četrturnih vrednosti.")
        if any(value < 0 or value > 8 for value in self.requested_sector_counts):
            raise ValueError("Število sektorjev mora biti med 0 in 8.")
        if self.max_continuous_work_minutes % SLOT_MINUTES != 0:
            raise ValueError("Najdaljši delovni blok mora biti večkratnik 15 minut.")
        if self.min_continuous_work_minutes % SLOT_MINUTES != 0:
            raise ValueError("Najkrajši delovni blok mora biti večkratnik 15 minut.")
        if self.min_continuous_work_minutes > self.max_continuous_work_minutes:
            raise ValueError("Najkrajši delovni blok ne sme biti daljši od najdaljšega.")
        if not any(shift.enabled for shift in self.shifts):
            raise ValueError("Omogočena mora biti vsaj ena izmena.")
        return self


class FutureWorkBlock(BaseModel):
    start_slot: int
    end_slot: int
    start: str
    end: str
    duration_minutes: int
    required_rest_minutes: int


class FuturePersonResult(BaseModel):
    id: str
    license: str
    shift: str
    worked_minutes: int
    work_slots: list[int]
    blocks: list[FutureWorkBlock]


class FutureSectorAssignment(BaseModel):
    sector_name: str
    lower_worker: str
    upper_worker: str


class FutureSlotCoverage(BaseModel):
    slot: int
    time: str
    requested_sectors: int
    open_sectors: int
    workers: list[str]
    resting_workers: list[str]
    sectors: list[FutureSectorAssignment]


class FutureCalculatorResponse(BaseModel):
    calculation_mode: str
    feasible: bool
    solver_status: str
    solver_stop_reason: str | None = None
    elapsed_seconds: float
    requested_quarter_slots: int
    covered_quarter_slots: int
    missing_quarter_slots: int
    requested_sector_hours: float
    covered_sector_hours: float
    missing_sector_hours: float
    solver_upper_bound_quarter_slots: int | None = None
    solver_gap_quarter_slots: int | None = None
    planned_people: int
    available_people: int
    active_people: int
    controller_hours: float
    people: list[FuturePersonResult]
    coverage: list[FutureSlotCoverage]
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


FutureIncumbentCallback = Callable[[FutureCalculatorResponse], None]


@dataclass(frozen=True)
class ShiftVariant:
    code: str
    slots: frozenset[int]


def slot_label(slot: int) -> str:
    minutes = (DAY_START_MINUTES + slot * SLOT_MINUTES) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _shift_variants(request: FutureCalculatorRequest) -> list[ShiftVariant]:
    offsets = range(4) if request.allow_quarter_hour_shift_starts else range(1)
    variants: list[ShiftVariant] = []
    for shift in request.shifts:
        if not shift.enabled:
            continue
        duration_slots = shift.duration_hours * 60 // SLOT_MINUTES
        for offset in offsets:
            start_minutes = (shift.start_hour * 60 + offset * SLOT_MINUTES) % (24 * 60)
            start_slot = ((start_minutes - DAY_START_MINUTES) % (24 * 60)) // SLOT_MINUTES
            code = shift.code if offset == 0 else f"{shift.code}+{offset * SLOT_MINUTES}"
            variants.append(
                ShiftVariant(
                    code=code,
                    slots=frozenset((start_slot + index) % SLOTS_PER_DAY for index in range(duration_slots)),
                )
            )
    return variants


def rest_slots_for_work_slots(work_slots: int, rest_ratio_percent: int) -> int:
    if work_slots <= 0 or rest_ratio_percent <= 0:
        return 0
    return math.ceil(work_slots * rest_ratio_percent / 100)


def work_rest_transitions(
    max_work_slots: int,
    rest_ratio_percent: int,
    min_work_slots: int = 1,
) -> tuple[list[tuple[int, int, int]], int]:
    max_rest_slots = rest_slots_for_work_slots(max_work_slots, rest_ratio_percent)

    def rest_state(remaining: int) -> int:
        return max_work_slots + remaining

    transitions: list[tuple[int, int, int]] = [(0, 0, 0), (0, 1, 1)]
    for run_length in range(1, max_work_slots + 1):
        if run_length < max_work_slots:
            transitions.append((run_length, 1, run_length + 1))
        if run_length >= min_work_slots:
            required_rest = rest_slots_for_work_slots(run_length, rest_ratio_percent)
            next_state = 0 if required_rest <= 1 else rest_state(required_rest - 1)
            transitions.append((run_length, 0, next_state))

    for remaining in range(1, max_rest_slots + 1):
        next_state = 0 if remaining == 1 else rest_state(remaining - 1)
        transitions.append((rest_state(remaining), 0, next_state))
    return transitions, max_work_slots + max_rest_slots


def _cyclic_work_blocks(work_slots: list[int], rest_ratio_percent: int) -> list[FutureWorkBlock]:
    worked = set(work_slots)
    if not worked:
        return []
    if len(worked) == SLOTS_PER_DAY:
        starts_and_lengths = [(0, SLOTS_PER_DAY)]
    else:
        boundary = next(slot for slot in range(SLOTS_PER_DAY) if slot not in worked)
        starts_and_lengths: list[tuple[int, int]] = []
        current_start: int | None = None
        current_length = 0
        for offset in range(1, SLOTS_PER_DAY + 1):
            slot = (boundary + offset) % SLOTS_PER_DAY
            if slot in worked:
                if current_start is None:
                    current_start = slot
                current_length += 1
            elif current_start is not None:
                starts_and_lengths.append((current_start, current_length))
                current_start = None
                current_length = 0
        if current_start is not None:
            starts_and_lengths.append((current_start, current_length))

    return sorted(
        [
            FutureWorkBlock(
                start_slot=start,
                end_slot=(start + length) % SLOTS_PER_DAY,
                start=slot_label(start),
                end=slot_label((start + length) % SLOTS_PER_DAY),
                duration_minutes=length * SLOT_MINUTES,
                required_rest_minutes=rest_slots_for_work_slots(length, rest_ratio_percent) * SLOT_MINUTES,
            )
            for start, length in starts_and_lengths
        ],
        key=lambda block: block.start_slot,
    )


def calculate_future_sector_hours(
    request: FutureCalculatorRequest,
    cancel_callback: CancelCallback | None = None,
    incumbent_callback: FutureIncumbentCallback | None = None,
) -> FutureCalculatorResponse:
    model = cp_model.CpModel()
    shift_variants = _shift_variants(request)
    min_work_slots = request.min_continuous_work_minutes // SLOT_MINUTES
    max_work_slots = request.max_continuous_work_minutes // SLOT_MINUTES
    transitions, max_state = work_rest_transitions(
        max_work_slots,
        request.rest_ratio_percent,
        min_work_slots,
    )

    people: list[tuple[str, str]] = []
    for license_name, count in (("FL", request.fl_count), ("APS", request.aps_count), ("ACS", request.acs_count)):
        people.extend((f"{license_name}{index + 1}", license_name) for index in range(count))

    selected: dict[int, cp_model.IntVar] = {}
    shift_choice: dict[tuple[int, int], cp_model.IntVar] = {}
    work: dict[tuple[int, int], cp_model.IntVar] = {}
    for person_index, (_person_id, _license_name) in enumerate(people):
        selected[person_index] = model.NewBoolVar(f"selected_{person_index}")
        choices = []
        for shift_index, _shift in enumerate(shift_variants):
            choice = model.NewBoolVar(f"shift_{person_index}_{shift_index}")
            shift_choice[(person_index, shift_index)] = choice
            choices.append(choice)
        model.Add(sum(choices) == selected[person_index])

        person_work = []
        states = [model.NewIntVar(0, max_state, f"state_{person_index}_{slot}") for slot in range(SLOTS_PER_DAY + 1)]
        model.Add(states[SLOTS_PER_DAY] == states[0])
        for slot in range(SLOTS_PER_DAY):
            current_work = model.NewBoolVar(f"work_{person_index}_{slot}")
            work[(person_index, slot)] = current_work
            person_work.append(current_work)
            available_choices = [
                shift_choice[(person_index, shift_index)]
                for shift_index, shift in enumerate(shift_variants)
                if slot in shift.slots
            ]
            model.Add(current_work <= sum(available_choices))
            model.AddAllowedAssignments([states[slot], current_work, states[slot + 1]], transitions)
        model.Add(sum(person_work) >= selected[person_index])

    people_by_license = {
        license_name: [index for index, (_person_id, person_license) in enumerate(people) if person_license == license_name]
        for license_name in LICENSES
    }
    for indexes in people_by_license.values():
        for first, second in zip(indexes, indexes[1:]):
            model.Add(selected[first] >= selected[second])

    cover: dict[tuple[int, int], cp_model.IntVar] = {}
    sector_assignment_counts: dict[tuple[int, int, str], cp_model.IntVar] = {}
    assignment_counts: dict[tuple[int, str], list[cp_model.IntVar]] = {
        (slot, license_name): []
        for slot in range(SLOTS_PER_DAY)
        for license_name in LICENSES
    }
    for slot, requested_count in enumerate(request.requested_sector_counts):
        sector_names = sector_names_for_count(requested_count)
        previous_cover: cp_model.IntVar | None = None
        for sector_index, sector_name in enumerate(sector_names):
            current_cover = model.NewBoolVar(f"cover_{slot}_{sector_index}")
            cover[(slot, sector_index)] = current_cover
            if previous_cover is not None:
                model.Add(previous_cover >= current_cover)
            previous_cover = current_cover

            allowed_licenses = sector_allowed_licenses(sector_name)
            sector_assignments = []
            for license_name in LICENSES:
                if license_name not in allowed_licenses:
                    continue
                assigned = model.NewIntVar(0, 2, f"assign_{slot}_{sector_index}_{license_name}")
                model.Add(assigned <= 2 * current_cover)
                sector_assignment_counts[(slot, sector_index, license_name)] = assigned
                assignment_counts[(slot, license_name)].append(assigned)
                sector_assignments.append(assigned)
            model.Add(sum(sector_assignments) == 2 * current_cover)

    for slot in range(SLOTS_PER_DAY):
        for license_name in LICENSES:
            working_people = sum(work[(person_index, slot)] for person_index in people_by_license[license_name])
            assigned_people = sum(assignment_counts[(slot, license_name)])
            model.Add(working_people == assigned_people)

    total_coverage = sum(cover.values())
    active_people = sum(selected.values())
    model.Maximize(total_coverage * COVERAGE_WEIGHT - active_people)

    requested_quarter_slots = sum(request.requested_sector_counts)

    def build_response(
        value: Callable[[object], int],
        *,
        status_name: str,
        elapsed_seconds: float,
        raw_bound: float | None,
        has_solution: bool,
        was_cancelled: bool = False,
        is_incumbent: bool = False,
    ) -> FutureCalculatorResponse:
        covered_quarter_slots = int(value(total_coverage)) if has_solution else 0
        missing_quarter_slots = max(0, requested_quarter_slots - covered_quarter_slots)
        if status_name == "OPTIMAL":
            upper_bound = covered_quarter_slots
        elif has_solution and raw_bound is not None:
            upper_bound = min(
                requested_quarter_slots,
                max(covered_quarter_slots, math.floor((raw_bound + 80) / COVERAGE_WEIGHT)),
            )
        else:
            upper_bound = None

        result_people: list[FuturePersonResult] = []
        selected_shift_by_person: dict[int, str] = {}
        selected_shift_slots_by_person: dict[int, frozenset[int]] = {}
        if has_solution:
            for person_index, (person_id, license_name) in enumerate(people):
                if not value(selected[person_index]):
                    continue
                selected_shift_index = next(
                    shift_index
                    for shift_index, _shift in enumerate(shift_variants)
                    if value(shift_choice[(person_index, shift_index)])
                )
                selected_shift = shift_variants[selected_shift_index]
                selected_shift_by_person[person_index] = selected_shift.code
                selected_shift_slots_by_person[person_index] = selected_shift.slots
                person_work_slots = [
                    slot for slot in range(SLOTS_PER_DAY) if value(work[(person_index, slot)])
                ]
                result_people.append(
                    FuturePersonResult(
                        id=person_id,
                        license=license_name,
                        shift=selected_shift.code,
                        worked_minutes=len(person_work_slots) * SLOT_MINUTES,
                        work_slots=person_work_slots,
                        blocks=_cyclic_work_blocks(person_work_slots, request.rest_ratio_percent),
                    )
                )

        coverage_rows: list[FutureSlotCoverage] = []
        for slot, requested_count in enumerate(request.requested_sector_counts):
            sector_names = sector_names_for_count(requested_count)
            open_sectors = (
                sum(1 for sector_index in range(len(sector_names)) if value(cover[(slot, sector_index)]))
                if has_solution
                else 0
            )
            workers = (
                [
                    people[person_index][0]
                    for person_index in selected_shift_by_person
                    if value(work[(person_index, slot)])
                ]
                if has_solution
                else []
            )
            resting_workers = (
                [
                    people[person_index][0]
                    for person_index, shift_slots in selected_shift_slots_by_person.items()
                    if slot in shift_slots and not value(work[(person_index, slot)])
                ]
                if has_solution
                else []
            )
            available_workers_by_license = {
                license_name: [
                    people[person_index][0]
                    for person_index in people_by_license[license_name]
                    if person_index in selected_shift_by_person and value(work[(person_index, slot)])
                ]
                if has_solution
                else []
                for license_name in LICENSES
            }
            sectors: list[FutureSectorAssignment] = []
            for sector_index, sector_name in enumerate(sector_names[:open_sectors]):
                assigned_workers: list[str] = []
                for license_name in LICENSES:
                    assigned = sector_assignment_counts.get((slot, sector_index, license_name))
                    assigned_count = int(value(assigned)) if has_solution and assigned is not None else 0
                    assigned_workers.extend(available_workers_by_license[license_name][:assigned_count])
                    del available_workers_by_license[license_name][:assigned_count]
                sectors.append(
                    FutureSectorAssignment(
                        sector_name=sector_name,
                        lower_worker=assigned_workers[0],
                        upper_worker=assigned_workers[1],
                    )
                )
            coverage_rows.append(
                FutureSlotCoverage(
                    slot=slot,
                    time=slot_label(slot),
                    requested_sectors=requested_count,
                    open_sectors=open_sectors,
                    workers=workers,
                    resting_workers=resting_workers,
                    sectors=sectors,
                )
            )

        notes = [
            (
                f"Časovna mreža je {SLOT_MINUTES} minut; najdaljši delovni blok je "
                f"{request.max_continuous_work_minutes} minut, najkrajši pa "
                f"{request.min_continuous_work_minutes} minut. Počitek je {request.rest_ratio_percent} % "
                "predhodnega delovnega bloka, zaokrožen navzgor na 15 minut."
            ),
            (
                "Prihodi so dovoljeni na :00, :15, :30 in :45."
                if request.allow_quarter_hour_shift_starts
                else "Prihodi ostajajo omejeni na polne ure."
            ),
            "ALL zahteva 2× FL; LOWER sprejme APS/FL; ostali sektorji sprejmejo ACS/FL.",
        ]
        if request.calculation_mode == "demand_to_staff":
            notes.append(
                "Način odprtosti najprej maksimira pokritost, nato pa med enako dobrimi rešitvami minimira aktivne ljudi."
            )
        if is_incumbent:
            notes.append("To je sprotni predogled najboljše doslej najdene rešitve; izračun še poteka.")

        warnings: list[str] = []
        stop_reason = None
        if not is_incumbent:
            if was_cancelled:
                stop_reason = (
                    "Izračun je bil prekinjen; prikazana je najboljša najdena rešitev."
                    if has_solution
                    else "Izračun je bil prekinjen pred prvo najdeno rešitvijo."
                )
                warnings.append(stop_reason)
            elif status_name == "FEASIBLE":
                stop_reason = f"Časovni limit {request.time_limit_seconds} s se je iztekel pred dokazom optimuma."
                warnings.append(stop_reason)
            elif status_name not in {"FEASIBLE", "OPTIMAL"}:
                stop_reason = "Solver ni našel izvedljive rešitve."
                warnings.append(stop_reason)
            elif missing_quarter_slots > 0:
                warnings.append(
                    f"Pri trenutnih ljudeh, izmenah in počitku manjka {missing_quarter_slots / 4:g} sektorskih ur."
                )

        controller_quarter_slots = sum(len(person.work_slots) for person in result_people)
        return FutureCalculatorResponse(
            calculation_mode=request.calculation_mode,
            feasible=missing_quarter_slots == 0,
            solver_status=status_name,
            solver_stop_reason=stop_reason,
            elapsed_seconds=round(elapsed_seconds, 2),
            requested_quarter_slots=requested_quarter_slots,
            covered_quarter_slots=covered_quarter_slots,
            missing_quarter_slots=missing_quarter_slots,
            requested_sector_hours=requested_quarter_slots / 4,
            covered_sector_hours=covered_quarter_slots / 4,
            missing_sector_hours=missing_quarter_slots / 4,
            solver_upper_bound_quarter_slots=upper_bound,
            solver_gap_quarter_slots=None if upper_bound is None else max(0, upper_bound - covered_quarter_slots),
            planned_people=len(result_people) if request.calculation_mode == "demand_to_staff" else request.total_people,
            available_people=request.total_people,
            active_people=len(result_people),
            controller_hours=controller_quarter_slots * SLOT_MINUTES / 60,
            people=result_people,
            coverage=coverage_rows,
            notes=notes,
            warnings=warnings,
        )

    class FutureSolutionPublisher(cp_model.CpSolverSolutionCallback):
        def __init__(self) -> None:
            super().__init__()
            self.last_published_at = -1.0
            self.last_published_rank = (-1, -request.total_people)

        def on_solution_callback(self) -> None:
            if incumbent_callback is None:
                return
            rank = (int(self.Value(total_coverage)), -int(self.Value(active_people)))
            elapsed_seconds = self.WallTime()
            if rank <= self.last_published_rank:
                return
            if self.last_published_at >= 0 and elapsed_seconds - self.last_published_at < 0.5:
                return
            self.last_published_at = elapsed_seconds
            self.last_published_rank = rank
            incumbent_callback(
                build_response(
                    self.Value,
                    status_name="FEASIBLE",
                    elapsed_seconds=elapsed_seconds,
                    raw_bound=self.BestObjectiveBound(),
                    has_solution=True,
                    is_incumbent=True,
                )
            )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = request.time_limit_seconds
    solver.parameters.num_search_workers = CP_SAT_WORKERS
    solver.parameters.random_seed = 1
    cancel_monitor_stop = Event()
    cancel_monitor: Thread | None = None
    if cancel_callback is not None:
        def watch_for_cancel() -> None:
            while not cancel_monitor_stop.wait(0.25):
                if cancel_callback():
                    solver.StopSearch()
                    return

        cancel_monitor = Thread(target=watch_for_cancel, daemon=True)
        cancel_monitor.start()
    solution_publisher = FutureSolutionPublisher() if incumbent_callback is not None else None
    try:
        status = solver.Solve(model, solution_publisher) if solution_publisher is not None else solver.Solve(model)
    finally:
        cancel_monitor_stop.set()
        if cancel_monitor is not None:
            cancel_monitor.join(timeout=1)
    status_name = solver.StatusName(status)
    has_solution = status in {cp_model.FEASIBLE, cp_model.OPTIMAL}
    was_cancelled = cancel_callback is not None and cancel_callback() and status != cp_model.OPTIMAL
    return build_response(
        solver.Value,
        status_name=status_name,
        elapsed_seconds=solver.WallTime(),
        raw_bound=solver.BestObjectiveBound() if has_solution else None,
        has_solution=has_solution,
        was_cancelled=was_cancelled,
    )
