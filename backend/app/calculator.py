from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from .models import (
    CalculatorRequest,
    CalculatorResponse,
    HourlyCoverage,
    ShiftRule,
    ShiftSummary,
    VirtualPerson,
)

DAY_START = 7
HOURS_IN_DAY = 24


DEFAULT_SHIFTS = [
    ShiftRule(code="A7", start_hour=7, duration_hours=7),
    ShiftRule(code="A8", start_hour=8, duration_hours=8),
    ShiftRule(code="A9", start_hour=9, duration_hours=8),
    ShiftRule(code="A10", start_hour=10, duration_hours=8),
    ShiftRule(code="A11", start_hour=11, duration_hours=8),
    ShiftRule(code="A12", start_hour=12, duration_hours=8),
    ShiftRule(code="A13", start_hour=13, duration_hours=8),
    ShiftRule(code="A14", start_hour=14, duration_hours=7),
    ShiftRule(code="A15", start_hour=15, duration_hours=8),
    ShiftRule(code="A16", start_hour=16, duration_hours=8),
    ShiftRule(code="A17", start_hour=17, duration_hours=8),
    ShiftRule(code="A21", start_hour=21, duration_hours=10),
]


@dataclass(frozen=True)
class PersonState:
    id: str
    license: str
    shift: str
    role: str | None = None
    sector_hours: int = 0
    used_as_sector_controller: bool = False


@dataclass(frozen=True)
class ScheduledResult:
    people: list[PersonState]
    hourly_workers: list[list[str]]
    total_hours: int


def hour_index(hour: int) -> int:
    return (hour - DAY_START) % HOURS_IN_DAY


def hour_label(index: int) -> str:
    start = (DAY_START + index) % HOURS_IN_DAY
    end = (start + 1) % HOURS_IN_DAY
    return f"{start:02d}:00–{end:02d}:00"


def shift_slots(shift: ShiftRule) -> set[int]:
    return {hour_index(shift.start_hour + offset) for offset in range(shift.duration_hours)}


def label_for_person(number: int) -> str:
    # A..Z, AA..AZ, BA.. for larger generated configurations.
    label = ""
    n = number
    while True:
        label = chr(ord("A") + (n % 26)) + label
        n = n // 26 - 1
        if n < 0:
            return label


def role_penalty(person: PersonState) -> int:
    if person.role == "FMP":
        return 4
    if person.role in {"V1", "V2", "V3"}:
        return 2
    return 0


def can_work_slot(worked: list[int], slot: int, max_consecutive: int, rest_after_max: int) -> bool:
    candidate_worked = set(worked) | {slot}
    consecutive_before = 0
    cursor = slot - 1
    while cursor in candidate_worked:
        consecutive_before += 1
        cursor -= 1
    if consecutive_before >= max_consecutive:
        return False

    window_size = max_consecutive + rest_after_max
    for window_start in range(max(0, slot - window_size + 1), slot + 1):
        window = range(window_start, window_start + window_size)
        if slot in window and sum(1 for item in window if item in candidate_worked) > max_consecutive:
            return False
    return True


def build_schedule(
    people: list[PersonState],
    shift_map: dict[str, ShiftRule],
    max_sectors_per_hour: int,
    max_consecutive: int,
    rest_after_max: int,
) -> ScheduledResult:
    available_slots = {person.id: shift_slots(shift_map[person.shift]) for person in people}
    worked_slots: dict[str, list[int]] = defaultdict(list)
    person_map = {person.id: person for person in people}
    hourly_workers: list[list[str]] = []

    for slot in range(HOURS_IN_DAY):
        candidates: list[PersonState] = []
        for person in people:
            if slot not in available_slots[person.id]:
                continue
            previous = worked_slots[person.id]
            if not can_work_slot(previous, slot, max_consecutive, rest_after_max):
                continue
            candidates.append(person)

        def candidate_key(person: PersonState) -> tuple[int, int, int, int, str]:
            # Prefer people who leave sooner, have fewer assigned sector hours, and are not FMP.
            remaining = len([future for future in available_slots[person.id] if future >= slot])
            return (
                role_penalty(person),
                person.sector_hours,
                remaining,
                0 if person.license == "ACS" else 1,
                person.id,
            )

        selected = sorted(candidates, key=candidate_key)[:max_sectors_per_hour]
        worker_ids = [person.id for person in selected]
        hourly_workers.append(worker_ids)
        for person in selected:
            worked_slots[person.id].append(slot)
            person_map[person.id] = replace(
                person_map[person.id],
                sector_hours=person_map[person.id].sector_hours + 1,
                used_as_sector_controller=True,
            )

        people = [person_map[person.id] for person in people]

    scheduled_people = [person_map[person.id] for person in people]
    return ScheduledResult(
        people=scheduled_people,
        hourly_workers=hourly_workers,
        total_hours=sum(len(workers) for workers in hourly_workers),
    )


def summarize_shifts(people: list[PersonState]) -> list[ShiftSummary]:
    shift_order = [shift.code for shift in DEFAULT_SHIFTS]
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for person in people:
        key = f"{person.role}/{person.shift}" if person.role in {"V1", "V2", "V3", "FMP"} else person.shift
        counts[key][person.license] += 1

    def sort_key(item: tuple[str, Counter[str]]) -> tuple[int, str]:
        shift = item[0].split("/")[-1]
        try:
            return (shift_order.index(shift), item[0])
        except ValueError:
            return (999, item[0])

    summaries: list[ShiftSummary] = []
    for shift, counter in sorted(counts.items(), key=sort_key):
        fl = counter["FL"]
        acs = counter["ACS"]
        summaries.append(ShiftSummary(shift=shift, fl=fl, acs=acs, total=fl + acs))
    return summaries


def create_mandatory_people(request: CalculatorRequest) -> tuple[list[PersonState], int, list[str]]:
    people: list[PersonState] = []
    warnings: list[str] = []
    next_id = 0

    def add(license_name: str, shift: str, role: str | None = None) -> None:
        nonlocal next_id
        people.append(PersonState(id=label_for_person(next_id), license=license_name, shift=shift, role=role))
        next_id += 1

    if request.settings.include_required_shift_leaders:
        add("FL", "A7", "V1")
        add("FL", "A14", "V2")
        add("FL", "A21", "V3")

    night_without_v3 = max(0, request.settings.required_night_fl_count - 1)
    for _ in range(night_without_v3):
        add("FL", "A21", None)

    if request.include_fmp:
        add("FL", "A9", "FMP")

    if request.settings.required_night_fl_count != 4:
        warnings.append("Nočna FL zahteva je spremenjena iz privzete vrednosti 4.")

    return people, next_id, warnings


def calculate(request: CalculatorRequest) -> CalculatorResponse:
    shift_map = {shift.code: shift for shift in request.settings.shifts}
    notes: list[str] = []
    mandatory_people, next_id, warnings = create_mandatory_people(request)
    minimum_required_fl = sum(1 for person in mandatory_people if person.license == "FL")

    if request.fl_count < minimum_required_fl:
        return CalculatorResponse(
            feasible=False,
            max_sector_hours=0,
            minimum_required_fl=minimum_required_fl,
            unused_people=request.total_people,
            people=[],
            shift_summary=[],
            hourly_coverage=[],
            notes=[f"Potrebnih je najmanj {minimum_required_fl} FL za V1/V2/V3, noč in FMP."],
            warnings=warnings,
        )

    people = list(mandatory_people)
    remaining_fl = request.fl_count - minimum_required_fl
    remaining_acs = request.acs_count
    allowed_shifts = [shift.code for shift in request.settings.shifts]

    def current_score(candidate_people: list[PersonState]) -> int:
        return build_schedule(
            candidate_people,
            shift_map,
            request.settings.max_sectors_per_hour,
            request.settings.max_consecutive_work_hours,
            request.settings.rest_after_max_consecutive_hours,
        ).total_hours

    # Greedy generator: each remaining virtual person is placed into the shift that improves
    # the achievable sector-hour schedule the most. This is intentionally lightweight for MVP.
    remaining_licenses = ["ACS"] * remaining_acs + ["FL"] * remaining_fl
    for license_name in remaining_licenses:
        best_person: PersonState | None = None
        best_score = -1
        for shift_code in allowed_shifts:
            candidate = PersonState(id=label_for_person(next_id), license=license_name, shift=shift_code)
            score = current_score(people + [candidate])
            if score > best_score:
                best_score = score
                best_person = candidate
        if best_person is None:
            break
        people.append(best_person)
        next_id += 1

    scheduled = build_schedule(
        people,
        shift_map,
        request.settings.max_sectors_per_hour,
        request.settings.max_consecutive_work_hours,
        request.settings.rest_after_max_consecutive_hours,
    )

    notes.append("Prva verzija uporablja hitro generiranje izmen in izvedljiv urni razpored, ne še polnega matematičnega solverja.")
    notes.append("FMP je dovoljen kot sektorski kontrolor, vendar ima pri izbiri delavcev slabšo prioriteto.")

    response_people = [
        VirtualPerson(
            id=person.id,
            license=person.license,
            shift=person.shift,
            role=person.role,
            sector_hours=person.sector_hours,
            used_as_sector_controller=person.used_as_sector_controller,
        )
        for person in scheduled.people
    ]

    hourly_coverage = [
        HourlyCoverage(hour=hour_label(slot), open_sectors=len(workers), workers=workers)
        for slot, workers in enumerate(scheduled.hourly_workers)
    ]

    unused_people = len([person for person in scheduled.people if person.sector_hours == 0])

    return CalculatorResponse(
        feasible=True,
        max_sector_hours=scheduled.total_hours,
        minimum_required_fl=minimum_required_fl,
        unused_people=unused_people,
        people=response_people,
        shift_summary=summarize_shifts(scheduled.people),
        hourly_coverage=hourly_coverage,
        notes=notes,
        warnings=warnings,
    )
