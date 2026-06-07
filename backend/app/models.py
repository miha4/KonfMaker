from pydantic import BaseModel, Field, model_validator


class ShiftRule(BaseModel):
    code: str
    start_hour: int = Field(ge=0, le=23)
    duration_hours: int = Field(ge=1, le=24)


class CalculatorSettings(BaseModel):
    max_sectors_per_hour: int = Field(default=5, ge=1, le=8)
    max_consecutive_work_hours: int = Field(default=2, ge=1, le=6)
    rest_after_max_consecutive_hours: int = Field(default=1, ge=1, le=4)
    include_required_shift_leaders: bool = True
    required_night_fl_count: int = Field(default=4, ge=0, le=10)
    shifts: list[ShiftRule]


class CalculatorRequest(BaseModel):
    total_people: int = Field(ge=1, le=80)
    fl_count: int = Field(ge=0, le=80)
    aps_count: int = Field(default=0, ge=0, le=80)
    acs_count: int = Field(ge=0, le=80)
    include_fmp: bool = True
    settings: CalculatorSettings
    requested_sector_counts: list[int] | None = None

    @model_validator(mode="after")
    def counts_must_match(self) -> "CalculatorRequest":
        if self.fl_count + self.aps_count + self.acs_count != self.total_people:
            raise ValueError("FL + APS + ACS mora biti enako skupnemu številu ljudi.")
        if self.requested_sector_counts is not None:
            if len(self.requested_sector_counts) != 24:
                raise ValueError("Vnos želene odprtosti mora imeti 24 urnih vrednosti.")
            if any(
                count < 0 or count > self.settings.max_sectors_per_hour
                for count in self.requested_sector_counts
            ):
                raise ValueError("Želena odprtost mora biti med 0 in največ sektorji hkrati.")
        return self


class VirtualPerson(BaseModel):
    id: str
    license: str
    shift: str
    role: str | None = None
    sector_hours: int = 0
    used_as_sector_controller: bool = False


class ShiftSummary(BaseModel):
    shift: str
    fl: int
    aps: int = 0
    acs: int
    total: int


class SectorAssignment(BaseModel):
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
    minimum_required_fl: int
    unused_people: int
    people: list[VirtualPerson]
    shift_summary: list[ShiftSummary]
    hourly_coverage: list[HourlyCoverage]
    notes: list[str]
    warnings: list[str]
