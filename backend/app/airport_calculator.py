from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field, model_validator


SLOT_MINUTES = 15
SLOTS_PER_DAY = 24 * 60 // SLOT_MINUTES
MAX_CONTINUOUS_WORK_SLOTS = 3 * 60 // SLOT_MINUTES
MIN_REST_SLOTS = 60 // SLOT_MINUTES
AIRPORT_CODES = ("BRN", "MBX", "POW", "CEK")


@dataclass(frozen=True)
class AirportShift:
    code: str
    start: str
    end: str
    break_start: str | None = None
    break_end: str | None = None


AIRPORT_SHIFT_CATALOG: dict[str, tuple[AirportShift, ...]] = {
    "BRN": (
        AirportShift("B7", "06:45", "14:00"),
        AirportShift("B8", "07:45", "16:00"),
        AirportShift("B9", "09:00", "17:00"),
        AirportShift("BD", "09:00", "19:00", "13:00", "15:45"),
        AirportShift("B14", "13:45", "21:00"),
        AirportShift("B17", "16:45", "01:00"),
        AirportShift("B21", "20:45", "07:00"),
    ),
    "MBX": (
        AirportShift("M7V", "06:45", "14:30"),
        AirportShift("MDA", "06:45", "21:00", "11:00", "16:45"),
        AirportShift("MBD", "07:00", "15:00"),
        AirportShift("MDB", "07:45", "20:00", "12:00", "15:45"),
        AirportShift("MDR", "07:45", "17:45"),
        AirportShift("M8", "07:45", "16:00"),
        AirportShift("MCW", "08:15", "16:00"),
        AirportShift("MDC", "08:00", "20:00", "12:00", "16:00"),
        AirportShift("MCL", "08:15", "16:45"),
        AirportShift("MCX", "08:15", "14:30"),
        AirportShift("MCR", "08:30", "16:30"),
        AirportShift("MCS", "08:30", "17:00"),
        AirportShift("MDD", "08:30", "20:00", "12:30", "16:00"),
        AirportShift("MDT", "08:15", "18:15", "13:00", "15:00"),
        AirportShift("MDU", "10:15", "20:15", "15:00", "17:00"),
        AirportShift("M10", "09:45", "18:00"),
        AirportShift("MFY", "10:15", "16:45"),
        AirportShift("MFS", "10:30", "19:00"),
        AirportShift("MFZ", "10:15", "18:00"),
        AirportShift("MGV", "10:45", "17:00"),
        AirportShift("MHV", "11:45", "20:15"),
        AirportShift("MHY", "11:45", "20:00"),
        AirportShift("MIW", "13:00", "21:15"),
        AirportShift("MIK", "13:15", "21:15"),
        AirportShift("MIX", "12:30", "20:15"),
    ),
    "POW": (
        AirportShift("P8", "07:45", "16:00"),
        AirportShift("P8V", "07:45", "16:15"),
        AirportShift("PCE", "08:00", "16:30"),
        AirportShift("PCL", "08:15", "16:45"),
        AirportShift("PCS", "08:30", "17:00"),
        AirportShift("PDC", "08:00", "20:00", "12:00", "16:00"),
        AirportShift("PDD", "08:15", "17:15", "11:45", "13:45"),
        AirportShift("PDE", "09:45", "18:45"),
        AirportShift("PDI", "08:15", "20:15", "12:30", "16:00"),
        AirportShift("PDH", "08:30", "20:00", "12:30", "16:00"),
        AirportShift("P9V", "08:45", "17:15"),
        AirportShift("PFV", "09:45", "18:15"),
        AirportShift("PFE", "10:00", "18:30"),
        AirportShift("PFL", "10:15", "18:45"),
        AirportShift("PFW", "10:00", "16:15"),
        AirportShift("PGV", "11:00", "17:00"),
        AirportShift("PHV", "11:45", "20:15"),
        AirportShift("PHW", "12:00", "20:15"),
    ),
    "CEK": (
        AirportShift("CBW", "07:45", "15:00"),
        AirportShift("CCA", "08:00", "15:00"),
        AirportShift("CDC", "08:00", "20:00", "12:15", "16:45"),
        AirportShift("CCW", "08:00", "15:45"),
        AirportShift("CCV", "08:30", "15:00"),
        AirportShift("CCS", "08:30", "17:00"),
        AirportShift("CEX", "08:45", "16:00"),
        AirportShift("CDD", "09:00", "21:00", "13:15", "17:45"),
        AirportShift("CDF", "08:15", "18:15", "13:00", "15:00"),
        AirportShift("CEV", "09:00", "15:00"),
        AirportShift("CFX", "09:45", "18:15"),
        AirportShift("CFL", "10:15", "18:45"),
        AirportShift("CGY", "10:45", "19:15"),
        AirportShift("CGU", "11:15", "18:00"),
        AirportShift("C12", "11:45", "20:00"),
        AirportShift("C13", "12:45", "21:00"),
        AirportShift("CKV", "15:00", "21:00"),
        AirportShift("CKY", "14:45", "20:00"),
        AirportShift("C15", "14:45", "22:00"),
        AirportShift("CKX", "14:45", "23:00"),
    ),
}


AIRPORT_NAMES = {
    "BRN": "BRN",
    "MBX": "MBX",
    "POW": "POW",
    "CEK": "CEK",
}


class AirportShiftDefinition(BaseModel):
    code: str
    start: str
    end: str
    break_start: str | None = None
    break_end: str | None = None


class AirportDefinition(BaseModel):
    code: str
    name: str
    shifts: list[AirportShiftDefinition]


class AirportCalculatorRequest(BaseModel):
    airport: str
    total_people: int = Field(ge=1, le=40)
    opening_start: str
    opening_end: str
    calculation_mode: Literal["opening", "selected_shifts"] = "opening"
    fixed_shift_counts: dict[str, int] = Field(default_factory=dict)
    continuous_24_hours: bool = False
    require_assistant_presence: bool = True
    avoid_split_shifts: bool = True
    explore_opening_extension: bool = True
    time_limit_seconds: int = Field(default=15, ge=1, le=60)

    @model_validator(mode="after")
    def validate_request(self) -> "AirportCalculatorRequest":
        self.airport = self.airport.upper()
        if self.airport not in AIRPORT_SHIFT_CATALOG:
            raise ValueError("Neznana letališka kontrola.")
        if self.calculation_mode == "selected_shifts":
            known_shifts = {
                shift.code for shift in AIRPORT_SHIFT_CATALOG[self.airport]
            }
            normalized_counts: dict[str, int] = {}
            for raw_code, count in self.fixed_shift_counts.items():
                code = raw_code.upper()
                if code not in known_shifts:
                    raise ValueError(f"Neznana izmena {code} za {self.airport}.")
                if count < 0:
                    raise ValueError("Število izbranih izmen ne sme biti negativno.")
                if count > 0:
                    normalized_counts[code] = count
            selected_people = sum(normalized_counts.values())
            if not 1 <= selected_people <= 40:
                raise ValueError("Izberi med 1 in 40 izmenami.")
            self.fixed_shift_counts = normalized_counts
            self.total_people = selected_people
            self.continuous_24_hours = False
            self.explore_opening_extension = False
            return self
        start_slot = time_to_slot(self.opening_start)
        end_slot = time_to_slot(self.opening_end)
        if not self.continuous_24_hours and start_slot == end_slot:
            raise ValueError("Za celodnevno odprtost vključi možnost 24/7.")
        return self


class AirportTimeBlock(BaseModel):
    start_slot: int
    end_slot: int
    start: str
    end: str
    duration_minutes: int


class AirportPersonResult(BaseModel):
    id: str
    shift: str
    shift_start: str
    shift_end: str
    shift_segments: list[AirportTimeBlock]
    presence_minutes: int
    duty_minutes: int
    controller_minutes: int
    presence_slots: list[int]
    preparation_slots: list[int]
    duty_slots: list[int]
    controller_slots: list[int]
    assistant_slots: list[int]
    duty_blocks: list[AirportTimeBlock]
    controller_blocks: list[AirportTimeBlock]
    break_blocks: list[AirportTimeBlock]


class AirportSlotResult(BaseModel):
    slot: int
    start: str
    end: str
    is_open: bool
    is_covered: bool
    controller_id: str | None = None
    assistant_id: str | None = None
    present_workers: list[str] = Field(default_factory=list)
    duty_workers: list[str] = Field(default_factory=list)
    break_workers: list[str] = Field(default_factory=list)


class AirportOpeningExtension(BaseModel):
    suggested_start: str
    suggested_end: str
    before_minutes: int
    after_minutes: int
    total_minutes: int


class AirportScheduleVariant(BaseModel):
    opening_start: str
    opening_end: str
    opening_minutes: int
    active_people: int
    handovers: int
    solver_status: str
    elapsed_seconds: float
    people: list[AirportPersonResult]
    coverage: list[AirportSlotResult]


class AirportCalculatorResponse(BaseModel):
    airport: str
    airport_name: str
    feasible: bool
    solver_status: str
    elapsed_seconds: float
    opening_start: str
    opening_end: str
    calculation_mode: Literal["opening", "selected_shifts"]
    continuous_24_hours: bool
    require_assistant_presence: bool
    avoid_split_shifts: bool
    explore_opening_extension: bool
    opening_extension: AirportOpeningExtension | None = None
    extended_variant: AirportScheduleVariant | None = None
    requested_minutes: int
    covered_minutes: int
    missing_minutes: int
    available_people: int
    active_people: int
    handovers: int
    people: list[AirportPersonResult]
    coverage: list[AirportSlotResult]
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def airport_definitions() -> list[AirportDefinition]:
    return [
        AirportDefinition(
            code=airport,
            name=AIRPORT_NAMES[airport],
            shifts=[
                AirportShiftDefinition(
                    code=shift.code,
                    start=shift.start,
                    end=shift.end,
                    break_start=shift.break_start,
                    break_end=shift.break_end,
                )
                for shift in AIRPORT_SHIFT_CATALOG[airport]
            ],
        )
        for airport in AIRPORT_CODES
    ]


def time_to_slot(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("Čas mora biti zapisan kot HH:MM.")
    try:
        hours, minutes = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("Čas mora biti zapisan kot HH:MM.") from exc
    if not 0 <= hours <= 23 or minutes not in {0, 15, 30, 45}:
        raise ValueError("Čas mora biti veljavna 15-minutna vrednost.")
    return hours * 4 + minutes // SLOT_MINUTES


def slot_label(slot: int) -> str:
    minutes = (slot % SLOTS_PER_DAY) * SLOT_MINUTES
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def interval_slots(start: str, end: str, continuous_24_hours: bool = False) -> frozenset[int]:
    start_slot = time_to_slot(start)
    if continuous_24_hours:
        return frozenset(range(SLOTS_PER_DAY))
    end_slot = time_to_slot(end)
    length = (end_slot - start_slot) % SLOTS_PER_DAY
    return frozenset((start_slot + offset) % SLOTS_PER_DAY for offset in range(length))


def shift_presence_slots(shift: AirportShift) -> frozenset[int]:
    slots = interval_slots(shift.start, shift.end)
    if shift.break_start is None or shift.break_end is None:
        return slots
    return slots - interval_slots(shift.break_start, shift.break_end)


def _fixed_rest_transitions() -> tuple[list[tuple[int, int, int]], int]:
    def rest_state(remaining: int) -> int:
        return MAX_CONTINUOUS_WORK_SLOTS + remaining

    transitions: list[tuple[int, int, int]] = [(0, 0, 0), (0, 1, 1)]
    for run_length in range(1, MAX_CONTINUOUS_WORK_SLOTS + 1):
        if run_length < MAX_CONTINUOUS_WORK_SLOTS:
            transitions.append((run_length, 1, run_length + 1))
        transitions.append((run_length, 0, rest_state(MIN_REST_SLOTS - 1)))
    for remaining in range(1, MIN_REST_SLOTS):
        next_state = 0 if remaining == 1 else rest_state(remaining - 1)
        transitions.append((rest_state(remaining), 0, next_state))
    return transitions, rest_state(MIN_REST_SLOTS - 1)


def _cyclic_blocks(slots: Iterable[int]) -> list[AirportTimeBlock]:
    included = {slot % SLOTS_PER_DAY for slot in slots}
    if not included:
        return []
    if len(included) == SLOTS_PER_DAY:
        starts_and_lengths = [(0, SLOTS_PER_DAY)]
    else:
        boundary = next(slot for slot in range(SLOTS_PER_DAY) if slot not in included)
        starts_and_lengths: list[tuple[int, int]] = []
        current_start: int | None = None
        current_length = 0
        for offset in range(1, SLOTS_PER_DAY + 1):
            slot = (boundary + offset) % SLOTS_PER_DAY
            if slot in included:
                if current_start is None:
                    current_start = slot
                current_length += 1
            elif current_start is not None:
                starts_and_lengths.append((current_start, current_length))
                current_start = None
                current_length = 0
        if current_start is not None:
            starts_and_lengths.append((current_start, current_length))

    return [
        AirportTimeBlock(
            start_slot=start,
            end_slot=(start + length) % SLOTS_PER_DAY,
            start=slot_label(start),
            end=slot_label(start + length),
            duration_minutes=length * SLOT_MINUTES,
        )
        for start, length in sorted(starts_and_lengths)
    ]


def _calculate_airport_schedule_once(
    request: AirportCalculatorRequest,
) -> AirportCalculatorResponse:
    shifts = AIRPORT_SHIFT_CATALOG[request.airport]
    maximize_opening = request.calculation_mode == "selected_shifts"
    fixed_shift_indexes = [
        shift_index
        for shift_index, shift in enumerate(shifts)
        for _ in range(request.fixed_shift_counts.get(shift.code, 0))
    ]
    shift_slots = [shift_presence_slots(shift) for shift in shifts]
    shift_preparation_slots = [
        frozenset({time_to_slot(shift.start)})
        for shift in shifts
    ]
    shift_work_slots = [
        slots - preparation_slots
        for slots, preparation_slots in zip(shift_slots, shift_preparation_slots)
    ]
    requested_open_slots = (
        frozenset(range(SLOTS_PER_DAY))
        if maximize_opening
        else interval_slots(
            request.opening_start,
            request.opening_end,
            request.continuous_24_hours,
        )
    )
    model = cp_model.CpModel()
    transitions, max_state = _fixed_rest_transitions()
    extension_before: list[cp_model.IntVar] = []
    extension_after: list[cp_model.IntVar] = []
    extension_terms_by_slot: dict[int, list[cp_model.IntVar]] = {
        slot: [] for slot in range(SLOTS_PER_DAY)
    }
    if (
        request.explore_opening_extension
        and not request.continuous_24_hours
        and not maximize_opening
    ):
        opening_start_slot = time_to_slot(request.opening_start)
        opening_end_slot = time_to_slot(request.opening_end)
        free_slot_count = SLOTS_PER_DAY - len(requested_open_slots)
        for distance in range(1, free_slot_count + 1):
            before = model.NewBoolVar(f"extension_before_{distance}")
            after = model.NewBoolVar(f"extension_after_{distance}")
            extension_before.append(before)
            extension_after.append(after)
            extension_terms_by_slot[
                (opening_start_slot - distance) % SLOTS_PER_DAY
            ].append(before)
            extension_terms_by_slot[
                (opening_end_slot + distance - 1) % SLOTS_PER_DAY
            ].append(after)
            if distance > 1:
                model.Add(before <= extension_before[-2])
                model.Add(after <= extension_after[-2])
        model.Add(
            sum(extension_before) + sum(extension_after) <= free_slot_count
        )

    selected: dict[int, cp_model.IntVar] = {}
    shift_choice: dict[tuple[int, int], cp_model.IntVar] = {}
    duty: dict[tuple[int, int], cp_model.IntVar] = {}
    controller: dict[tuple[int, int], cp_model.IntVar] = {}
    block_start: dict[tuple[int, int], cp_model.IntVar] = {}
    controller_block_start: dict[tuple[int, int], cp_model.IntVar] = {}
    single_controller_slot: dict[tuple[int, int], cp_model.IntVar] = {}

    for person in range(request.total_people):
        selected[person] = model.NewBoolVar(f"selected_{person}")
        choices: list[cp_model.IntVar] = []
        for shift_index in range(len(shifts)):
            choice = model.NewBoolVar(f"shift_{person}_{shift_index}")
            shift_choice[(person, shift_index)] = choice
            choices.append(choice)
        model.Add(sum(choices) == selected[person])
        if maximize_opening:
            fixed_shift_index = fixed_shift_indexes[person]
            model.Add(selected[person] == 1)
            for shift_index, choice in enumerate(choices):
                model.Add(choice == int(shift_index == fixed_shift_index))

        states = [
            model.NewIntVar(0, max_state, f"state_{person}_{slot}")
            for slot in range(SLOTS_PER_DAY + 1)
        ]
        model.Add(states[SLOTS_PER_DAY] == states[0])
        for slot in range(SLOTS_PER_DAY):
            current_duty = model.NewBoolVar(f"duty_{person}_{slot}")
            duty[(person, slot)] = current_duty
            presence_choices = [
                shift_choice[(person, shift_index)]
                for shift_index, slots in enumerate(shift_slots)
                if slot in slots
            ]
            model.Add(current_duty <= sum(presence_choices))

            current_controller = model.NewBoolVar(f"controller_{person}_{slot}")
            controller[(person, slot)] = current_controller
            controller_choices = [
                shift_choice[(person, shift_index)]
                for shift_index, slots in enumerate(shift_work_slots)
                if slot in slots
            ]
            model.Add(current_controller <= current_duty)
            model.Add(current_controller <= sum(controller_choices))
            model.AddAllowedAssignments(
                [states[slot], current_duty, states[slot + 1]],
                transitions,
            )

            previous_duty = duty.get((person, (slot - 1) % SLOTS_PER_DAY))
            if previous_duty is not None:
                current_start = model.NewBoolVar(f"block_start_{person}_{slot}")
                block_start[(person, slot)] = current_start
                model.Add(current_start >= current_duty - previous_duty)
                model.Add(current_start <= current_duty)
                model.Add(current_start <= 1 - previous_duty)
    # The slot-0 transition is created after slot 95 exists.
    for person in range(request.total_people):
        current_start = model.NewBoolVar(f"block_start_{person}_0")
        block_start[(person, 0)] = current_start
        model.Add(current_start >= duty[(person, 0)] - duty[(person, SLOTS_PER_DAY - 1)])
        model.Add(current_start <= duty[(person, 0)])
        model.Add(current_start <= 1 - duty[(person, SLOTS_PER_DAY - 1)])

    for person in range(request.total_people):
        for slot in range(SLOTS_PER_DAY):
            previous_controller = controller[
                (person, (slot - 1) % SLOTS_PER_DAY)
            ]
            current_controller = controller[(person, slot)]
            next_controller = controller[
                (person, (slot + 1) % SLOTS_PER_DAY)
            ]

            current_start = model.NewBoolVar(
                f"controller_block_start_{person}_{slot}"
            )
            controller_block_start[(person, slot)] = current_start
            model.Add(current_start >= current_controller - previous_controller)
            model.Add(current_start <= current_controller)
            model.Add(current_start <= 1 - previous_controller)

            current_single = model.NewBoolVar(
                f"single_controller_slot_{person}_{slot}"
            )
            single_controller_slot[(person, slot)] = current_single
            model.Add(
                current_single
                >= current_controller - previous_controller - next_controller
            )
            model.Add(current_single <= current_controller)
            model.Add(current_single <= 1 - previous_controller)
            model.Add(current_single <= 1 - next_controller)

    for person in range(request.total_people - 1):
        model.Add(selected[person] >= selected[person + 1])

    cover: dict[int, cp_model.IntVar] = {}
    required_duty_count = 2 if request.require_assistant_presence else 1
    for slot in range(SLOTS_PER_DAY):
        cover[slot] = model.NewBoolVar(f"cover_{slot}")
        duty_count = sum(duty[(person, slot)] for person in range(request.total_people))
        controller_count = sum(
            controller[(person, slot)]
            for person in range(request.total_people)
        )
        if slot not in requested_open_slots:
            model.Add(cover[slot] == sum(extension_terms_by_slot[slot]))
        model.Add(controller_count == cover[slot])
        model.Add(duty_count == required_duty_count * cover[slot])

    if maximize_opening:
        opening_starts: list[cp_model.IntVar] = []
        for slot in range(SLOTS_PER_DAY):
            opening_start = model.NewBoolVar(f"opening_start_{slot}")
            previous_cover = cover[(slot - 1) % SLOTS_PER_DAY]
            model.Add(opening_start >= cover[slot] - previous_cover)
            model.Add(opening_start <= cover[slot])
            model.Add(opening_start <= 1 - previous_cover)
            opening_starts.append(opening_start)
        model.Add(sum(opening_starts) <= 1)

    total_coverage = sum(cover[slot] for slot in requested_open_slots)
    baseline_complete = model.NewBoolVar("baseline_complete")
    model.Add(
        total_coverage == len(requested_open_slots)
    ).OnlyEnforceIf(baseline_complete)
    model.Add(
        total_coverage <= len(requested_open_slots) - 1
    ).OnlyEnforceIf(baseline_complete.Not())
    for extension_var in [*extension_before, *extension_after]:
        model.Add(extension_var <= baseline_complete)
    total_extension = sum(extension_before) + sum(extension_after)
    active_people = sum(selected.values())
    split_shift_count = sum(
        shift_choice[(person, shift_index)]
        for person in range(request.total_people)
        for shift_index, shift in enumerate(shifts)
        if shift.break_start is not None
    )
    controller_totals = [
        model.NewIntVar(0, SLOTS_PER_DAY, f"controller_total_{person}")
        for person in range(request.total_people)
    ]
    for person, controller_total in enumerate(controller_totals):
        model.Add(
            controller_total
            == sum(controller[(person, slot)] for slot in range(SLOTS_PER_DAY))
        )
    max_controller_total = model.NewIntVar(
        0,
        SLOTS_PER_DAY,
        "max_controller_total",
    )
    model.AddMaxEquality(max_controller_total, controller_totals)
    active_controller_totals = [
        model.NewIntVar(0, SLOTS_PER_DAY, f"active_controller_total_{person}")
        for person in range(request.total_people)
    ]
    for person, active_controller_total in enumerate(active_controller_totals):
        model.Add(
            active_controller_total
            == controller_totals[person] + SLOTS_PER_DAY * (1 - selected[person])
        )
    min_controller_total = model.NewIntVar(
        0,
        SLOTS_PER_DAY,
        "min_controller_total",
    )
    model.AddMinEquality(min_controller_total, active_controller_totals)
    controller_time_spread = model.NewIntVar(
        0,
        SLOTS_PER_DAY,
        "controller_time_spread",
    )
    model.Add(
        controller_time_spread == max_controller_total - min_controller_total
    ).OnlyEnforceIf(selected[0])
    model.Add(controller_time_spread == 0).OnlyEnforceIf(selected[0].Not())
    total_blocks = sum(block_start.values())
    total_controller_blocks = sum(controller_block_start.values())
    total_single_controller_slots = sum(single_controller_slot.values())
    off_window_presence: dict[tuple[int, int], cp_model.IntVar] = {}
    for person in range(request.total_people):
        for slot in range(SLOTS_PER_DAY):
            outside_presence = model.NewBoolVar(
                f"off_window_presence_{person}_{slot}"
            )
            off_window_presence[(person, slot)] = outside_presence
            presence_choices = [
                shift_choice[(person, shift_index)]
                for shift_index, slots in enumerate(shift_slots)
                if slot in slots
            ]
            if not presence_choices:
                model.Add(outside_presence == 0)
                continue
            presence = sum(presence_choices)
            model.Add(outside_presence >= presence - cover[slot])
            model.Add(outside_presence <= presence)
            model.Add(outside_presence <= 1 - cover[slot])
    total_off_window_presence = sum(off_window_presence.values())
    total_presence = sum(
        len(shift_slots[shift_index]) * shift_choice[(person, shift_index)]
        for person in range(request.total_people)
        for shift_index in range(len(shifts))
    )
    model.Maximize(
        total_coverage * 60_000_000_000
        + total_extension * 620_000_000
        - active_people * 15_000_000
        - split_shift_count * (13_500_000 if request.avoid_split_shifts else 0)
        - total_off_window_presence * 100_000
        - total_single_controller_slots * 100_000
        - total_controller_blocks * 35_000
        - controller_time_spread * 100
        - total_blocks * 5
        - total_presence
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = request.time_limit_seconds
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    has_solution = status in {cp_model.FEASIBLE, cp_model.OPTIMAL}
    extension_before_slots = (
        sum(solver.BooleanValue(value) for value in extension_before)
        if has_solution
        else 0
    )
    extension_after_slots = (
        sum(solver.BooleanValue(value) for value in extension_after)
        if has_solution
        else 0
    )
    scheduled_open_slots = (
        {
            slot
            for slot in range(SLOTS_PER_DAY)
            if has_solution and solver.BooleanValue(cover[slot])
        }
        if maximize_opening
        else set(requested_open_slots)
    )
    if not maximize_opening and extension_before_slots > 0:
        opening_start_slot = time_to_slot(request.opening_start)
        scheduled_open_slots.update(
            (opening_start_slot - distance) % SLOTS_PER_DAY
            for distance in range(1, extension_before_slots + 1)
        )
    if not maximize_opening and extension_after_slots > 0:
        opening_end_slot = time_to_slot(request.opening_end)
        scheduled_open_slots.update(
            (opening_end_slot + distance - 1) % SLOTS_PER_DAY
            for distance in range(1, extension_after_slots + 1)
        )

    selected_shift_by_person: dict[int, int] = {}
    people: list[AirportPersonResult] = []
    if has_solution:
        for person in range(request.total_people):
            if not solver.BooleanValue(selected[person]):
                continue
            selected_shift_index = next(
                shift_index
                for shift_index in range(len(shifts))
                if solver.BooleanValue(shift_choice[(person, shift_index)])
            )
            selected_shift_by_person[person] = selected_shift_index
            person_presence_slots = sorted(shift_slots[selected_shift_index])
            person_preparation_slots = sorted(shift_preparation_slots[selected_shift_index])
            person_duty_slots = [
                slot
                for slot in range(SLOTS_PER_DAY)
                if solver.BooleanValue(duty[(person, slot)])
            ]
            person_controller_slots = [
                slot
                for slot in range(SLOTS_PER_DAY)
                if solver.BooleanValue(controller[(person, slot)])
            ]
            person_assistant_slots = sorted(
                set(person_duty_slots) - set(person_controller_slots)
            )
            person_break_slots = sorted(
                set(person_presence_slots)
                - set(person_duty_slots)
                - set(person_preparation_slots)
            )
            shift = shifts[selected_shift_index]
            people.append(
                AirportPersonResult(
                    id=f"K{person + 1}",
                    shift=shift.code,
                    shift_start=shift.start,
                    shift_end=shift.end,
                    shift_segments=_cyclic_blocks(person_presence_slots),
                    presence_minutes=len(person_presence_slots) * SLOT_MINUTES,
                    duty_minutes=len(person_duty_slots) * SLOT_MINUTES,
                    controller_minutes=len(person_controller_slots) * SLOT_MINUTES,
                    presence_slots=person_presence_slots,
                    preparation_slots=person_preparation_slots,
                    duty_slots=person_duty_slots,
                    controller_slots=person_controller_slots,
                    assistant_slots=person_assistant_slots,
                    duty_blocks=_cyclic_blocks(person_duty_slots),
                    controller_blocks=_cyclic_blocks(person_controller_slots),
                    break_blocks=_cyclic_blocks(person_break_slots),
                )
            )

    person_ids = {person: f"K{person + 1}" for person in selected_shift_by_person}
    coverage: list[AirportSlotResult] = []
    covered_slots = 0
    for slot in range(SLOTS_PER_DAY):
        present_workers = [
            person_ids[person]
            for person, shift_index in selected_shift_by_person.items()
            if slot in shift_slots[shift_index]
        ]
        duty_workers = [
            person_ids[person]
            for person in selected_shift_by_person
            if has_solution and solver.BooleanValue(duty[(person, slot)])
        ]
        controller_workers = [
            person_ids[person]
            for person in selected_shift_by_person
            if has_solution and solver.BooleanValue(controller[(person, slot)])
        ]
        assistant_workers = [
            worker
            for worker in duty_workers
            if worker not in controller_workers
        ]
        is_covered = (
            slot in scheduled_open_slots
            and len(controller_workers) == 1
            and len(duty_workers) == required_duty_count
        )
        covered_slots += int(is_covered and slot in requested_open_slots)
        coverage.append(
            AirportSlotResult(
                slot=slot,
                start=slot_label(slot),
                end=slot_label(slot + 1),
                is_open=slot in scheduled_open_slots,
                is_covered=is_covered,
                controller_id=controller_workers[0] if controller_workers else None,
                assistant_id=assistant_workers[0] if assistant_workers else None,
                present_workers=present_workers,
                duty_workers=duty_workers,
                break_workers=[
                    worker for worker in present_workers if worker not in duty_workers
                ],
            )
        )

    covered_minutes = covered_slots * SLOT_MINUTES
    if maximize_opening:
        opening_blocks = _cyclic_blocks(scheduled_open_slots)
        result_opening_start = (
            opening_blocks[0].start if opening_blocks else "00:00"
        )
        result_opening_end = (
            opening_blocks[0].end if opening_blocks else "00:00"
        )
        result_continuous_24_hours = len(scheduled_open_slots) == SLOTS_PER_DAY
        requested_minutes = covered_minutes
        feasible = has_solution and covered_minutes > 0
    else:
        result_opening_start = request.opening_start
        result_opening_end = request.opening_end
        result_continuous_24_hours = request.continuous_24_hours
        requested_minutes = len(requested_open_slots) * SLOT_MINUTES
        feasible = covered_minutes == requested_minutes
    suggested_opening_start = (
        result_opening_start
        if maximize_opening or result_continuous_24_hours
        else slot_label(
            time_to_slot(request.opening_start) - extension_before_slots
        )
    )
    suggested_opening_end = (
        result_opening_end
        if maximize_opening or result_continuous_24_hours
        else slot_label(
            time_to_slot(request.opening_end) + extension_after_slots
        )
    )
    opening_extension = (
        AirportOpeningExtension(
            suggested_start=suggested_opening_start,
            suggested_end=suggested_opening_end,
            before_minutes=extension_before_slots * SLOT_MINUTES,
            after_minutes=extension_after_slots * SLOT_MINUTES,
            total_minutes=(
                extension_before_slots + extension_after_slots
            ) * SLOT_MINUTES,
        )
        if request.explore_opening_extension
        and not result_continuous_24_hours
        and not maximize_opening
        and feasible
        else None
    )
    opening_start_slot = (
        0
        if result_continuous_24_hours
        else time_to_slot(suggested_opening_start)
    )
    ordered_open_coverage = sorted(
        (slot for slot in coverage if slot.is_open),
        key=lambda slot: (slot.slot - opening_start_slot) % SLOTS_PER_DAY,
    )
    handover_pairs = list(zip(ordered_open_coverage, ordered_open_coverage[1:]))
    if result_continuous_24_hours and ordered_open_coverage:
        handover_pairs.append((ordered_open_coverage[-1], ordered_open_coverage[0]))
    handovers = sum(
        1
        for previous, current in handover_pairs
        if previous.controller_id is not None
        and current.controller_id is not None
        and previous.controller_id != current.controller_id
    )
    warnings: list[str] = []
    if request.require_assistant_presence and request.total_people < 2:
        warnings.append("Za delo in hkratno prisotnost asistenta sta potrebni najmanj dve osebi.")
    if maximize_opening and not feasible:
        warnings.append(
            "Iz izbranih izmen ni mogoče sestaviti niti ene pokrite 15-minutne odprtosti."
        )
    elif not feasible:
        warnings.append(
            f"Z izbranimi pogoji manjka {requested_minutes - covered_minutes} minut pokritja."
        )

    notes = [
        "Na sektorju je v posamezni četrtini največ en kontrolor.",
        "Kontrolor in asistent/standby sta oba v operativnem delu.",
        "Prvih 15 minut izmene je priprava; oseba lahko takoj dela kot asistent, ne pa kot glavni kontrolor.",
        "Po največ treh urah operativnega dela sledi najmanj 60 minut pavze.",
        "Čas na poziciji je razdeljen čim bolj enakomerno, brez nepotrebnih 15-minutnih menjav.",
        "Vmesna pavza deljene izmene ni prisotnost v hiši.",
    ]
    if request.avoid_split_shifts and not maximize_opening:
        notes.append("Deljene izmene so uporabljene le, ko izboljšajo pomembnejši cilj.")
    if maximize_opening:
        notes.append(
            "Izbrane izmene so fiksne; optimiziran je najdaljši neprekinjeni odpiralni čas."
        )
    if opening_extension is not None and opening_extension.total_minutes > 0:
        notes.append(
            "Predlagana razširitev je neprekinjena in uporablja isto število razpoložljivih ljudi."
        )
    if request.require_assistant_presence:
        notes.append("Vsak pokrit termin ima enega kontrolorja in enega operativnega asistenta.")

    return AirportCalculatorResponse(
        airport=request.airport,
        airport_name=AIRPORT_NAMES[request.airport],
        feasible=feasible,
        solver_status=status_name,
        elapsed_seconds=round(solver.WallTime(), 3),
        opening_start=result_opening_start,
        opening_end=result_opening_end,
        calculation_mode=request.calculation_mode,
        continuous_24_hours=result_continuous_24_hours,
        require_assistant_presence=request.require_assistant_presence,
        avoid_split_shifts=request.avoid_split_shifts,
        explore_opening_extension=request.explore_opening_extension,
        opening_extension=opening_extension,
        requested_minutes=requested_minutes,
        covered_minutes=covered_minutes,
        missing_minutes=requested_minutes - covered_minutes,
        available_people=request.total_people,
        active_people=len(people),
        handovers=handovers,
        people=people,
        coverage=coverage,
        notes=notes,
        warnings=warnings,
    )


def calculate_airport_schedule(
    request: AirportCalculatorRequest,
) -> AirportCalculatorResponse:
    if (
        not request.explore_opening_extension
        or request.continuous_24_hours
    ):
        return _calculate_airport_schedule_once(request)

    baseline_request = request.model_copy(
        update={"explore_opening_extension": False}
    )
    baseline = _calculate_airport_schedule_once(baseline_request)
    baseline.explore_opening_extension = True
    if not baseline.feasible:
        return baseline

    extended = _calculate_airport_schedule_once(request)
    baseline.elapsed_seconds = round(
        baseline.elapsed_seconds + extended.elapsed_seconds,
        3,
    )
    baseline.opening_extension = extended.opening_extension
    extension = extended.opening_extension
    if extension is None or extension.total_minutes == 0:
        return baseline

    baseline.extended_variant = AirportScheduleVariant(
        opening_start=extension.suggested_start,
        opening_end=extension.suggested_end,
        opening_minutes=(
            baseline.requested_minutes + extension.total_minutes
        ),
        active_people=extended.active_people,
        handovers=extended.handovers,
        solver_status=extended.solver_status,
        elapsed_seconds=extended.elapsed_seconds,
        people=extended.people,
        coverage=extended.coverage,
    )
    baseline.notes.append(
        "Osnovna in razširjena sestava sta izračunani ločeno."
    )
    return baseline
