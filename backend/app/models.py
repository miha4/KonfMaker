from pydantic import BaseModel, Field, field_validator, model_validator


DEFAULT_INCLUDE_NIGHT_FL_REQUIREMENT = True
DEFAULT_EXTRA_NIGHT_A21_FL_COUNT = 3
DEFAULT_REQUIRED_NIGHT_FL_COUNT = DEFAULT_EXTRA_NIGHT_A21_FL_COUNT + 1


class ShiftRule(BaseModel):
    code: str
    start_hour: int = Field(ge=0, le=23)
    duration_hours: int = Field(ge=1, le=24)
    enabled: bool = True


class CalculatorSettings(BaseModel):
    max_sectors_per_hour: int = Field(default=5, ge=1, le=8)
    max_consecutive_work_hours: int = Field(default=2, ge=1, le=6)
    rest_after_max_consecutive_hours: int = Field(default=1, ge=1, le=4)
    cp_sat_time_limit_seconds: int = Field(default=600, ge=1, le=7200)
    cp_sat_no_improvement_seconds: int = Field(default=180, ge=0, le=7200)
    cp_sat_acceptable_sector_gap: int = Field(default=0, ge=0, le=100)
    cp_sat_min_auto_stop_coverage_percent: int = Field(default=95, ge=0, le=100)
    include_required_shift_leaders: bool = True
    include_night_fl_requirement: bool = DEFAULT_INCLUDE_NIGHT_FL_REQUIREMENT
    required_night_fl_count: int = Field(default=DEFAULT_REQUIRED_NIGHT_FL_COUNT, ge=0, le=10)
    v1_sector_limit: int = Field(default=1, ge=0, le=24)
    v2_sector_limit: int = Field(default=1, ge=0, le=24)
    v3_sector_limit: int = Field(default=4, ge=0, le=24)
    fmp_sector_limit: int = Field(default=6, ge=0, le=24)
    shifts: list[ShiftRule]
    officer_shifts: list[ShiftRule] = Field(default_factory=list)


class FixedStaffRule(BaseModel):
    count: int = Field(default=1, ge=1, le=80)
    license: str
    shift: str
    role: str | None = None

    @field_validator("license")
    @classmethod
    def license_must_be_known(cls, value: str) -> str:
        if value not in {"FL", "APS", "ACS"}:
            raise ValueError("Licenca mora biti FL, APS ali ACS.")
        return value

    @field_validator("role")
    @classmethod
    def blank_role_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class LockedStaffRule(BaseModel):
    count: int = Field(default=1, ge=1, le=80)
    license: str
    shift: str
    role: str | None = None
    label: str | None = None

    @field_validator("license")
    @classmethod
    def license_must_be_known(cls, value: str) -> str:
        if value not in {"FL", "APS", "ACS"}:
            raise ValueError("Licenca mora biti FL, APS ali ACS.")
        return value

    @field_validator("role")
    @classmethod
    def blank_role_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("label")
    @classmethod
    def blank_label_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class OfficerStaffRule(BaseModel):
    count: int = Field(default=0, ge=0, le=80)
    license: str
    shift: str

    @field_validator("license")
    @classmethod
    def license_must_be_known(cls, value: str) -> str:
        if value not in {"FL", "APS", "ACS"}:
            raise ValueError("Licenca mora biti FL, APS ali ACS.")
        return value


class OfficePoolRule(BaseModel):
    count: int = Field(default=0, ge=0, le=80)
    license: str

    @field_validator("license")
    @classmethod
    def license_must_be_known(cls, value: str) -> str:
        if value not in {"FL", "APS", "ACS"}:
            raise ValueError("Licenca mora biti FL, APS ali ACS.")
        return value


class LicenseMixPercent(BaseModel):
    fl: int = Field(default=50, ge=0, le=100)
    aps: int = Field(default=0, ge=0, le=100)
    acs: int = Field(default=50, ge=0, le=100)

    @model_validator(mode="after")
    def at_least_one_license_must_be_positive(self) -> "LicenseMixPercent":
        if self.fl + self.aps + self.acs <= 0:
            raise ValueError("Vsaj en delež licenc mora biti večji od 0.")
        return self


class CalculatorRequest(BaseModel):
    calculation_mode: str = "staff_to_coverage"
    total_people: int = Field(ge=0, le=80)
    fl_count: int = Field(ge=0, le=80)
    aps_count: int = Field(default=0, ge=0, le=80)
    acs_count: int = Field(ge=0, le=80)
    include_fmp: bool = True
    fmp_shift_mode: str = "auto"
    fmp_shift: str = "A9"
    settings: CalculatorSettings
    requested_sector_counts: list[int] | None = None
    fixed_staff: list[FixedStaffRule] = Field(default_factory=list)
    locked_staff: list[LockedStaffRule] = Field(default_factory=list)
    officer_staff: list[OfficerStaffRule] = Field(default_factory=list)
    office_pool: list[OfficePoolRule] = Field(default_factory=list)
    license_mix_percent: LicenseMixPercent | None = None
    include_pareto: bool = False
    prefer_minimal_fl: bool = False
    office_fallback_mode: str = "auto"
    leader_exception_mode: str = "forbid"
    max_leader_exception_hours: int = Field(default=0, ge=0, le=48)
    continuation_min_sector_hours: int | None = Field(default=None, ge=0, le=192)
    solver_random_seed: int = Field(default=1, ge=1, le=2_147_483_647)
    preferred_manual_configuration_id: str | None = None
    warm_start: dict[str, object] | None = None
    warm_start_snapshot_id: str | None = None

    @field_validator("preferred_manual_configuration_id")
    @classmethod
    def blank_preferred_manual_configuration_id_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("fmp_shift")
    @classmethod
    def clean_fmp_shift(cls, value: str) -> str:
        cleaned = value.strip()
        return cleaned or "A9"

    @field_validator("warm_start_snapshot_id")
    @classmethod
    def blank_warm_start_snapshot_id_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def counts_must_match(self) -> "CalculatorRequest":
        if self.calculation_mode not in {"staff_to_coverage", "demand_to_staff"}:
            raise ValueError("Neznan način izračuna.")
        if self.office_fallback_mode not in {"auto", "disabled", "force"}:
            raise ValueError("Način office fallback mora biti auto, disabled ali force.")
        if self.leader_exception_mode not in {"forbid", "allow"}:
            raise ValueError("Način kriznih VI/FMP izjem mora biti forbid ali allow.")
        if self.leader_exception_mode == "forbid" and self.max_leader_exception_hours != 0:
            raise ValueError("Pri prepovedanih VI/FMP izjemah mora biti največ kriznih ur 0.")
        if self.fmp_shift_mode not in {"auto", "fixed"}:
            raise ValueError("Način FMP izmene mora biti auto ali fixed.")
        if self.calculation_mode == "staff_to_coverage" and self.total_people < 1:
            raise ValueError("Skupno število ljudi mora biti večje od 0.")
        if self.calculation_mode == "staff_to_coverage" and self.fl_count + self.aps_count + self.acs_count != self.total_people:
            raise ValueError("FL + APS + ACS mora biti enako skupnemu številu ljudi.")
        if self.requested_sector_counts is not None:
            if len(self.requested_sector_counts) != 24:
                raise ValueError("Vnos želene odprtosti mora imeti 24 urnih vrednosti.")
            if any(
                count < 0 or count > self.settings.max_sectors_per_hour
                for count in self.requested_sector_counts
            ):
                raise ValueError("Želena odprtost mora biti med 0 in največ sektorji hkrati.")
        configured_shift_codes = {shift.code for shift in self.settings.shifts}
        if self.include_fmp and self.fmp_shift_mode == "fixed" and self.fmp_shift not in configured_shift_codes:
            raise ValueError("FMP izmena mora uporabljati eno izmed nastavljenih rednih izmen.")
        if any(item.shift not in configured_shift_codes for item in self.fixed_staff):
            raise ValueError("Fiksna dodatna izmena mora uporabljati eno izmed nastavljenih izmen.")
        if any(item.shift not in configured_shift_codes for item in self.locked_staff):
            raise ValueError("Zaklenjena what-if izmena mora uporabljati eno izmed nastavljenih izmen.")
        configured_officer_shift_codes = {shift.code for shift in self.settings.officer_shifts}
        if any(item.shift not in configured_officer_shift_codes for item in self.officer_staff):
            raise ValueError("Officer izmena mora uporabljati eno izmed nastavljenih officer izmen.")
        if sum(item.count for item in self.fixed_staff) > 80:
            raise ValueError("Skupno število fiksno vpisanih ljudi ne sme presegati 80.")
        if sum(item.count for item in self.locked_staff) > 80:
            raise ValueError("Skupno število zaklenjenih what-if ljudi ne sme presegati 80.")
        if sum(item.count for item in self.officer_staff) > 80:
            raise ValueError("Skupno število officerjev ne sme presegati 80.")
        if sum(item.count for item in self.office_pool) > 80:
            raise ValueError("Skupno število operativnih officev ne sme presegati 80.")
        return self


class VirtualPerson(BaseModel):
    id: str
    license: str
    shift: str
    role: str | None = None
    sector_hours: int = 0
    max_sector_hours: int = 0
    utilization_percent: int = 0
    used_as_sector_controller: bool = False
    source: str = "regular"


class ParetoPoint(BaseModel):
    people_limit: int
    planned_people: int = 0
    active_people: int = 0
    max_sector_hours: int = 0
    requested_sector_hours: int = 0
    coverage_percent: int = 0
    missing_sector_hours: int = 0
    scheduled_person_hours: int = 0
    total_person_capacity_hours: int = 0
    utilization_percent: int = 0
    used_officers: int = 0
    feasible: bool = False
    solver_status: str | None = None
    solver_solution_count: int = 0
    solver_optimality_gap_percent: float | None = None
    solver_stop_reason: str | None = None


class ParetoResponse(BaseModel):
    requested_sector_hours: int = 0
    points: list[ParetoPoint] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ShiftSummary(BaseModel):
    shift: str
    fl: int
    aps: int = 0
    acs: int
    total: int


class SectorAssignment(BaseModel):
    sector_name: str
    lower_worker: str
    upper_worker: str


class HourlyCoverage(BaseModel):
    hour: str
    open_sectors: int
    workers: list[str]
    sector_workers: list[SectorAssignment | None]


class CalculatorResponse(BaseModel):
    feasible: bool
    max_sector_hours: int
    requested_sector_hours: int = 0
    solver_upper_bound_sector_hours: int | None = None
    solver_gap_to_upper_bound: int | None = None
    solver_status: str | None = None
    solver_solution_count: int = 0
    solver_optimality_gap_percent: float | None = None
    solver_stop_reason: str | None = None
    leader_edge_exception_hours: int = 0
    fmp_vi_overlap_hours: int = 0
    crisis_exception_hours: int = 0
    missing_sector_hours: int = 0
    baseline_min_people: int = 0
    baseline_min_people_formula: str | None = None
    minimum_required_fl: int
    planned_people: int = 0
    active_people: int = 0
    unused_people: int
    scheduled_person_hours: int = 0
    total_person_capacity_hours: int = 0
    utilization_percent: int = 0
    people: list[VirtualPerson]
    shift_summary: list[ShiftSummary]
    hourly_coverage: list[HourlyCoverage]
    pareto_points: list[ParetoPoint] = Field(default_factory=list)
    notes: list[str]
    warnings: list[str]


class SaveUserConfigurationRequest(BaseModel):
    name: str | None = None
    result: CalculatorResponse
    note: str | None = None


class CompleteConfigurationRequest(BaseModel):
    request: CalculatorRequest
    current_result: CalculatorResponse | None = None
    time_limit_seconds: int = Field(default=8, ge=1, le=120)


class ManualConfigurationOneDownRequest(BaseModel):
    time_limit_seconds: int = Field(default=8, ge=1, le=120)
    settings: CalculatorSettings | None = None


class CompareConfigurationRequest(BaseModel):
    result: CalculatorResponse
    limit: int = Field(default=8, ge=1, le=50)
