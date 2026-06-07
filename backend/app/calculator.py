from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace

from .models import (
    CalculatorRequest,
    CalculatorResponse,
    HourlyCoverage,
    SectorAssignment,
    ShiftRule,
    ShiftSummary,
    VirtualPerson,
)

DAY_START = 7
HOURS_IN_DAY = 24
LOWER_SECTOR_LICENSES = {"APS", "FL"}
UPPER_SECTOR_LICENSES = {"ACS", "FL"}


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
class ScheduledSector:
    lower_worker: str
    upper_worker: str


@dataclass(frozen=True)
class ScheduledResult:
    people: list[PersonState]
    hourly_sectors: list[list[ScheduledSector]]
    total_hours: int

    @property
    def hourly_workers(self) -> list[list[str]]:
        return [
            [worker for sector in sectors for worker in (sector.lower_worker, sector.upper_worker)]
            for sectors in self.hourly_sectors
        ]


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


def can_fill_position(person: PersonState, position: str) -> bool:
    if position == "lower":
        return person.license in LOWER_SECTOR_LICENSES
    return person.license in UPPER_SECTOR_LICENSES


def position_preference(person: PersonState, position: str) -> int:
    if position == "lower":
        return 0 if person.license == "APS" else 1
    return 0 if person.license == "ACS" else 1


def build_schedule(
    people: list[PersonState],
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    max_consecutive: int,
    rest_after_max: int,
) -> ScheduledResult:
    available_slots = {person.id: shift_slots(shift_map[person.shift]) for person in people}
    worked_slots: dict[str, list[int]] = defaultdict(list)
    person_map = {person.id: person for person in people}
    hourly_sectors: list[list[ScheduledSector]] = []

    for slot in range(HOURS_IN_DAY):
        scheduled_sectors: list[ScheduledSector] = []

        def available_for_current_slot(person: PersonState) -> bool:
            if slot not in available_slots[person.id]:
                return False
            previous = worked_slots[person.id]
            if slot in previous:
                return False
            return can_work_slot(previous, slot, max_consecutive, rest_after_max)

        def remaining_slots(person: PersonState) -> int:
            return len([future for future in available_slots[person.id] if future >= slot])

        def pair_key(pair: tuple[PersonState, PersonState]) -> tuple[int, int, int, int, int, str, str]:
            lower, upper = pair
            return (
                position_preference(lower, "lower"),
                position_preference(upper, "upper"),
                role_penalty(lower) + role_penalty(upper),
                lower.sector_hours + upper.sector_hours,
                remaining_slots(lower) + remaining_slots(upper),
                lower.id,
                upper.id,
            )

        while len(scheduled_sectors) < target_sector_counts[slot]:
            candidates = [person for person in people if available_for_current_slot(person)]
            lower_candidates = [person for person in candidates if can_fill_position(person, "lower")]
            upper_candidates = [person for person in candidates if can_fill_position(person, "upper")]

            best_pair: tuple[PersonState, PersonState] | None = None
            best_pair_key: tuple[int, int, int, int, int, str, str] | None = None
            for lower in lower_candidates:
                available_upper_candidates = [upper for upper in upper_candidates if upper.id != lower.id]
                if not available_upper_candidates:
                    continue
                upper = min(
                    available_upper_candidates,
                    key=lambda candidate: (
                        position_preference(candidate, "upper"),
                        role_penalty(candidate),
                        candidate.sector_hours,
                        remaining_slots(candidate),
                        candidate.id,
                    ),
                )
                current_pair = (lower, upper)
                current_key = pair_key(current_pair)
                if best_pair_key is None or current_key < best_pair_key:
                    best_pair = current_pair
                    best_pair_key = current_key

            if best_pair is None:
                break

            lower, upper = best_pair
            scheduled_sectors.append(ScheduledSector(lower_worker=lower.id, upper_worker=upper.id))

            for person in (lower, upper):
                worked_slots[person.id].append(slot)
                person_map[person.id] = replace(
                    person_map[person.id],
                    sector_hours=person_map[person.id].sector_hours + 1,
                    used_as_sector_controller=True,
                )

            people = [person_map[person.id] for person in people]

        hourly_sectors.append(scheduled_sectors)

    scheduled_people = [person_map[person.id] for person in people]
    return ScheduledResult(
        people=scheduled_people,
        hourly_sectors=hourly_sectors,
        total_hours=sum(len(sectors) for sectors in hourly_sectors),
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
        aps = counter["APS"]
        acs = counter["ACS"]
        summaries.append(ShiftSummary(shift=shift, fl=fl, aps=aps, acs=acs, total=fl + aps + acs))
    return summaries


def create_mandatory_people(request: CalculatorRequest) -> tuple[list[PersonState], int, list[str]]:
    people: list[PersonState] = []
    warnings: list[str] = []
    next_id = 0

    def add(license_name: str, shift: str, role: str | None = None) -> None:
        nonlocal next_id
        people.append(PersonState(id=label_for_person(next_id), license=license_name, shift=shift, role=role))
        next_id += 1

    includes_v3 = request.settings.include_required_shift_leaders
    if request.settings.include_required_shift_leaders:
        add("FL", "A7", "V1")
        add("FL", "A14", "V2")
        add("FL", "A21", "V3")

    night_without_v3 = max(0, request.settings.required_night_fl_count - (1 if includes_v3 else 0))
    for _ in range(night_without_v3):
        add("FL", "A21", None)

    if request.include_fmp:
        add("FL", "A9", "FMP")

    if request.settings.required_night_fl_count != 4:
        warnings.append("Nočna FL zahteva je spremenjena iz privzete vrednosti 4.")

    return people, next_id, warnings


def target_sector_counts_for_request(request: CalculatorRequest) -> list[int]:
    if request.requested_sector_counts is None:
        return [request.settings.max_sectors_per_hour] * HOURS_IN_DAY
    return request.requested_sector_counts


def shift_demand_score(shift: ShiftRule, target_sector_counts: list[int]) -> int:
    return sum(target_sector_counts[slot] for slot in shift_slots(shift))


def generated_shift_key(
    shift_code: str,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    people: list[PersonState],
) -> tuple[int, int, int, str]:
    shift = shift_map[shift_code]
    assigned_to_shift = sum(1 for person in people if person.shift == shift_code)
    demand_score = shift_demand_score(shift, target_sector_counts)

    # Keep generation cheap: rank shifts by how much requested demand they cover,
    # while subtracting a small load penalty so one popular shift does not absorb
    # every generated person. This replaces repeated full schedule simulations.
    return (demand_score - assigned_to_shift * 2, demand_score, -assigned_to_shift, shift_code)


def response_from_schedule(
    scheduled: ScheduledResult,
    request: CalculatorRequest,
    minimum_required_fl: int,
    notes: list[str],
    warnings: list[str],
    requested_sector_hours: int,
) -> CalculatorResponse:
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
        HourlyCoverage(
            hour=hour_label(slot),
            open_sectors=len(scheduled.hourly_sectors[slot]),
            workers=workers,
            sector_workers=[
                SectorAssignment(lower_worker=sector.lower_worker, upper_worker=sector.upper_worker)
                for sector in scheduled.hourly_sectors[slot]
            ]
            + [None] * (request.settings.max_sectors_per_hour - len(scheduled.hourly_sectors[slot])),
        )
        for slot, workers in enumerate(scheduled.hourly_workers)
    ]

    unused_people = len([person for person in scheduled.people if person.sector_hours == 0])
    missing_sector_hours = max(0, requested_sector_hours - scheduled.total_hours)

    return CalculatorResponse(
        feasible=missing_sector_hours == 0,
        max_sector_hours=scheduled.total_hours,
        requested_sector_hours=requested_sector_hours,
        missing_sector_hours=missing_sector_hours,
        minimum_required_fl=minimum_required_fl,
        unused_people=unused_people,
        people=response_people,
        shift_summary=summarize_shifts(scheduled.people),
        hourly_coverage=hourly_coverage,
        notes=notes,
        warnings=warnings,
    )


def choose_generated_person(
    next_id: int,
    license_name: str,
    people: list[PersonState],
    allowed_shifts: list[str],
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
    max_people_by_shift: dict[str, int],
) -> PersonState | None:
    def shift_has_capacity(shift_code: str) -> bool:
        max_people = max_people_by_shift.get(shift_code)
        if max_people is None:
            return True
        return sum(1 for person in people if person.shift == shift_code) < max_people

    eligible_shifts = [shift_code for shift_code in allowed_shifts if shift_has_capacity(shift_code)]
    if not eligible_shifts:
        return None

    best_shift = max(
        eligible_shifts,
        key=lambda shift_code: generated_shift_key(shift_code, shift_map, target_sector_counts, people),
    )
    return PersonState(id=label_for_person(next_id), license=license_name, shift=best_shift)


def calculate_demand_to_staff(
    request: CalculatorRequest,
    shift_map: dict[str, ShiftRule],
    target_sector_counts: list[int],
) -> CalculatorResponse:
    notes: list[str] = []
    mandatory_people, next_id, warnings = create_mandatory_people(request)
    people = list(mandatory_people)
    allowed_shifts = [shift.code for shift in request.settings.shifts]
    max_people_by_shift = {"A21": request.settings.required_night_fl_count}
    requested_sector_hours = sum(target_sector_counts)

    scheduled = build_schedule(
        people,
        shift_map,
        target_sector_counts,
        request.settings.max_consecutive_work_hours,
        request.settings.rest_after_max_consecutive_hours,
    )

    license_cycle = ["APS", "ACS"]
    cycle_index = 0
    while scheduled.total_hours < requested_sector_hours and len(people) < 80:
        license_name = license_cycle[cycle_index % len(license_cycle)]
        cycle_index += 1
        candidate = choose_generated_person(
            next_id,
            license_name,
            people,
            allowed_shifts,
            shift_map,
            target_sector_counts,
            max_people_by_shift,
        )
        if candidate is None:
            break
        people.append(candidate)
        next_id += 1
        scheduled = build_schedule(
            people,
            shift_map,
            target_sector_counts,
            request.settings.max_consecutive_work_hours,
            request.settings.rest_after_max_consecutive_hours,
        )

    minimum_required_fl = sum(1 for person in mandatory_people if person.license == "FL")
    notes.append("Način 2 izračuna najnižjo najdeno zasedbo za ročno vneseno odprtost po urah.")
    notes.append("Izračun dodaja APS/ACS pare po hitri hevristiki in se ustavi, ko doseže zahtevano odprtost ali omejitve.")
    notes.append("Vsak odprt sektor potrebuje spodnjega kontrolorja (APS ali FL) in zgornjega kontrolorja (ACS ali FL).")
    if scheduled.total_hours < requested_sector_hours:
        warnings.append("Znotraj trenutnih pravil ni bilo mogoče pokriti vseh zahtevanih sektorskih ur.")

    return response_from_schedule(
        scheduled,
        request,
        minimum_required_fl,
        notes,
        warnings,
        requested_sector_hours,
    )


def calculate(request: CalculatorRequest) -> CalculatorResponse:
    shift_map = {shift.code: shift for shift in request.settings.shifts}
    target_sector_counts = target_sector_counts_for_request(request)
    if request.calculation_mode == "demand_to_staff":
        return calculate_demand_to_staff(request, shift_map, target_sector_counts)

    notes: list[str] = []
    mandatory_people, next_id, warnings = create_mandatory_people(request)
    minimum_required_fl = sum(1 for person in mandatory_people if person.license == "FL")

    if request.fl_count < minimum_required_fl:
        return CalculatorResponse(
            feasible=False,
            max_sector_hours=0,
            requested_sector_hours=sum(target_sector_counts),
            missing_sector_hours=sum(target_sector_counts),
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
    remaining_aps = request.aps_count
    remaining_acs = request.acs_count
    allowed_shifts = [shift.code for shift in request.settings.shifts]
    max_people_by_shift = {"A21": request.settings.required_night_fl_count}

    # Fast generator: rank shifts with a cheap demand/load heuristic, then run the
    # real paired-sector scheduler once at the end. The previous version simulated
    # a full 24-hour schedule for every person/shift candidate, which could keep
    # the API busy long enough for a 504 timeout.
    remaining_licenses = ["APS"] * remaining_aps + ["ACS"] * remaining_acs + ["FL"] * remaining_fl
    for license_name in remaining_licenses:
        candidate = choose_generated_person(
            next_id,
            license_name,
            people,
            allowed_shifts,
            shift_map,
            target_sector_counts,
            max_people_by_shift,
        )
        if candidate is None:
            break
        people.append(candidate)
        next_id += 1

    scheduled = build_schedule(
        people,
        shift_map,
        target_sector_counts,
        request.settings.max_consecutive_work_hours,
        request.settings.rest_after_max_consecutive_hours,
    )

    notes.append("Generator spoštuje delovne ure izmen: npr. A7 je na voljo samo 07–14, A21 samo 21–07.")
    if request.settings.required_night_fl_count == 4 and request.settings.include_required_shift_leaders:
        notes.append("Nočna izmena je omejena na V3 + 3× A21 (skupaj 4 FL), zato generator ne dodaja dodatnih A21.")
    else:
        notes.append(
            "Nočna izmena je omejena na "
            f"{request.settings.required_night_fl_count} ljudi v A21; generator ne dodaja dodatnih A21."
        )
    notes.append("Kalkulator odpira največ toliko sektorjev, kot jih uporabnik označi v urnem vnosu želene odprtosti.")
    notes.append("Vsak odprt sektor potrebuje spodnjega kontrolorja (APS ali FL) in zgornjega kontrolorja (ACS ali FL).")
    notes.append("FMP je dovoljen kot sektorski kontrolor, vendar ima pri izbiri delavcev slabšo prioriteto.")

    if scheduled.total_hours < sum(target_sector_counts):
        warnings.append("Z vnesenimi ljudmi ni mogoče pokriti vseh zahtevanih sektorskih ur.")

    return response_from_schedule(
        scheduled,
        request,
        minimum_required_fl,
        notes,
        warnings,
        sum(target_sector_counts),
    )
