from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import zipfile
import xml.etree.ElementTree as ET

import altair as alt
import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="Muscat 2040 infrastructure model",
    page_icon=":material/query_stats:",
    layout="wide",
)


BASE_DIR = Path(__file__).resolve().parent
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
FORECAST_START = 2024
FORECAST_END = 2040

AGE_GROUP_ORDER = [
    "0 - 4",
    "5 - 9",
    "10 - 14",
    "15 - 19",
    "20 - 24",
    "25 - 29",
    "30 - 34",
    "35 - 39",
    "40 - 44",
    "45 - 49",
    "50 - 54",
    "55 - 59",
    "60 - 64",
    "65 - 69",
    "70 - 74",
    "75 - 79",
    "+80",
]

AGE_BROAD_BANDS = {
    "Youth (0-14)": ["0 - 4", "5 - 9", "10 - 14"],
    "Working age (15-64)": [
        "15 - 19",
        "20 - 24",
        "25 - 29",
        "30 - 34",
        "35 - 39",
        "40 - 44",
        "45 - 49",
        "50 - 54",
        "55 - 59",
        "60 - 64",
    ],
    "Elderly (65+)": ["65 - 69", "70 - 74", "75 - 79", "+80"],
}

SCENARIO_DEFAULTS = {
    "Low": {"omani_growth": 1.22, "non_omani_growth": 0.39},
    "Base": {"omani_growth": 2.02, "non_omani_growth": 1.59},
    "High": {"omani_growth": 2.82, "non_omani_growth": 2.99},
}

SCENARIO_NOTES = {
    "Low": "Slower migration recovery and softer local household growth.",
    "Base": "Continuation of long-run population trends observed in Muscat.",
    "High": "Faster migration inflows and stronger urban concentration in Muscat.",
}

SCENARIO_STYLES = {
    "Low": {"color": "green", "icon": ":material/trending_flat:"},
    "Base": {"color": "blue", "icon": ":material/track_changes:"},
    "High": {"color": "orange", "icon": ":material/trending_up:"},
}


@dataclass(frozen=True)
class BaselineMetrics:
    omani_2024: int
    non_omani_2024: int
    total_2024: int
    beds_2023: int
    beds_per_1000: float
    gov_students_2024: int
    gov_classes_2024: int
    gov_teachers_2024: int
    gov_schools_2024: int
    gov_student_share: float
    students_per_class: float
    students_per_teacher: float
    youth_share: float
    working_age_share: float
    elderly_share: float
    dependency_ratio: float
    median_age_estimate: float


def resolve_input_file(label: str, patterns: list[str]) -> Path:
    for pattern in patterns:
        matches = sorted(BASE_DIR.glob(pattern))
        if matches:
            return matches[0]

    available_files = ", ".join(
        sorted(path.name for path in BASE_DIR.glob("dataset/*.xlsx"))
    )
    raise FileNotFoundError(
        f"Could not find the {label} dataset in {BASE_DIR}. "
        f"Tried patterns: {patterns}. Available Excel files: {available_files or 'none'}."
    )


POPULATION_FILE = resolve_input_file(
    "population",
    [
        "dataset/Muscat - Total Population Last 10 Years*.xlsx",
        "dataset/Muscat - Last 10 years Population*.xlsx",
        "dataset/Muscat - Last 10 years*.xlsx",
    ],
)
HEALTHCARE_CAPACITY_FILE = resolve_input_file(
    "healthcare bed capacity",
    [
        "dataset/Muscat - Number of Beds - Hospitals*.xlsx",
        "dataset/Muscat - Number of Beds*.xlsx",
    ],
)
HEALTHCARE_CONTEXT_FILE = resolve_input_file(
    "healthcare context",
    [
        "dataset/Muscat - Healthcare 10 Years*.xlsx",
        "dataset/Muscat - Healthcare Total*.xlsx",
    ],
)
EDUCATION_FILE = resolve_input_file(
    "education",
    [
        "dataset/Muscat - Education*.xlsx",
    ],
)


def _col_letter_to_index(col_str: str) -> int:
    """Convert an Excel column letter (A, B, ..., Z, AA, ...) to a 0-based index."""
    result = 0
    for ch in col_str:
        result = result * 26 + (ord(ch.upper()) - ord("A") + 1)
    return result - 1


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """Read the shared strings table from an OOXML workbook."""
    try:
        ss_xml = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    ss_root = ET.fromstring(ss_xml)
    strings: list[str] = []
    for si in ss_root.findall("x:si", NS):
        # A shared string may be a single <t> or multiple <r><t> runs.
        parts: list[str] = []
        t_node = si.find("x:t", NS)
        if t_node is not None and t_node.text:
            parts.append(t_node.text)
        else:
            for run in si.findall("x:r", NS):
                rt = run.find("x:t", NS)
                if rt is not None and rt.text:
                    parts.append(rt.text)
        strings.append("".join(parts))
    return strings


def read_sheet_rows(file_path: Path) -> list[list[str | None]]:
    """Read an Excel sheet into a list of lists, resolving shared strings."""
    with zipfile.ZipFile(file_path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheet_xml = archive.read("xl/worksheets/sheet1.xml")

    root = ET.fromstring(sheet_xml)
    sheet_data = root.find("x:sheetData", NS)
    if sheet_data is None:
        return []

    rows: list[list[str | None]] = []
    for row_el in sheet_data.findall("x:row", NS):
        values: list[str | None] = []
        for cell in row_el.findall("x:c", NS):
            ref = cell.get("r", "")  # e.g. "B7"
            col_letters = "".join(ch for ch in ref if ch.isalpha())
            col_idx = _col_letter_to_index(col_letters) if col_letters else len(values)

            # Pad with None if there are gaps between columns
            while len(values) < col_idx:
                values.append(None)

            value_node = cell.find("x:v", NS)
            raw = value_node.text if value_node is not None else None

            cell_type = cell.get("t")
            if cell_type == "s" and raw is not None:
                # Shared string index
                resolved = shared_strings[int(raw)]
            else:
                resolved = raw

            values.append(resolved)
        rows.append(values)
    return rows


def safe_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


@st.cache_data(show_spinner=False)
def load_age_group_data() -> pd.DataFrame:
    """Load the age-group population file into a long-format DataFrame.

    Parses the OOXML structure directly (zipfile + xml.etree.ElementTree),
    consistent with the rest of the codebase — no openpyxl required.
    """
    all_rows = read_sheet_rows(POPULATION_FILE)

    # Find the header row (contains "Nationality")
    header_idx = None
    for i, row in enumerate(all_rows):
        if row and row[0] == "Nationality":
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Could not find header row in population file")

    years = [int(float(y)) for y in all_rows[header_idx][3:] if y is not None]

    # Find section start indices by scanning for non-null nationality cells
    sections: list[tuple[int, str]] = []
    for i in range(header_idx + 1, len(all_rows)):
        if all_rows[i] and all_rows[i][0] is not None:
            sections.append((i, str(all_rows[i][0])))

    records: list[dict] = []
    for sec_idx, (start, nationality_raw) in enumerate(sections):
        if "omani" in nationality_raw.lower() and "non" not in nationality_raw.lower():
            nat = "Omani"
        elif (
            "non" in nationality_raw.lower() or "expatriate" in nationality_raw.lower()
        ):
            nat = "Non-Omani"
        elif "total" in nationality_raw.lower():
            nat = "Total"
        else:
            nat = nationality_raw

        end = sections[sec_idx + 1][0] if sec_idx + 1 < len(sections) else len(all_rows)
        for i in range(start, end):
            row = all_rows[i]
            age_group = row[1] if len(row) > 1 else None
            if (
                age_group is None
                or str(age_group).strip() == ""
                or str(age_group).strip() == "75"
            ):
                continue
            age_label = (
                "+80"
                if str(age_group).strip() in ("+80", "80+")
                else str(age_group).strip()
            )
            for j, year in enumerate(years):
                cell = row[3 + j] if (3 + j) < len(row) else None
                val = safe_int(cell) if cell not in (None, "") else None
                if val is not None and val > 0:
                    records.append(
                        {
                            "nationality": nat,
                            "age_group": age_label,
                            "year": year,
                            "population": val,
                        }
                    )

    df = pd.DataFrame(records)
    df["age_group"] = pd.Categorical(
        df["age_group"], categories=AGE_GROUP_ORDER, ordered=True
    )
    return df


def _broad_band(age_group: str) -> str:
    """Map a fine age group to its broad band."""
    for band, groups in AGE_BROAD_BANDS.items():
        if age_group in groups:
            return band
    return "Unknown"


def _estimate_median_age(age_df: pd.DataFrame) -> float:
    """Estimate median age from grouped population data using midpoints."""
    midpoints = {
        "0 - 4": 2,
        "5 - 9": 7,
        "10 - 14": 12,
        "15 - 19": 17,
        "20 - 24": 22,
        "25 - 29": 27,
        "30 - 34": 32,
        "35 - 39": 37,
        "40 - 44": 42,
        "45 - 49": 47,
        "50 - 54": 52,
        "55 - 59": 57,
        "60 - 64": 62,
        "65 - 69": 67,
        "70 - 74": 72,
        "75 - 79": 77,
        "+80": 85,
    }
    sorted_groups = sorted(
        age_df.itertuples(index=False), key=lambda r: midpoints.get(r.age_group, 0)
    )
    total = sum(r.population for r in sorted_groups)
    if total == 0:
        return 0.0
    cumulative = 0
    for r in sorted_groups:
        cumulative += r.population
        if cumulative >= total / 2:
            return midpoints.get(r.age_group, 0)
    return 0.0


@st.cache_data(show_spinner=False)
def load_inputs() -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    BaselineMetrics,
]:
    # Load age-group data
    age_group_data = load_age_group_data()

    # Derive flat population table from age-group data (summing across age groups)
    # Use the "Omani" and "Non-Omani" sections (not "Total" to avoid double counting)
    omani_totals = (
        age_group_data[age_group_data["nationality"] == "Omani"]
        .groupby("year")["population"]
        .sum()
        .reset_index()
        .rename(columns={"population": "omani_population"})
    )
    non_omani_totals = (
        age_group_data[age_group_data["nationality"] == "Non-Omani"]
        .groupby("year")["population"]
        .sum()
        .reset_index()
        .rename(columns={"population": "non_omani_population"})
    )
    population = omani_totals.merge(non_omani_totals, on="year", how="outer").fillna(0)
    population["total_population"] = (
        population["omani_population"] + population["non_omani_population"]
    )
    population = population.astype(
        {"omani_population": int, "non_omani_population": int, "total_population": int}
    )

    healthcare_capacity_rows = read_sheet_rows(HEALTHCARE_CAPACITY_FILE)
    healthcare_capacity = pd.DataFrame(
        {
            "year": [int(year) for year in healthcare_capacity_rows[0][2:]],
            "beds": [safe_int(v) for v in healthcare_capacity_rows[1][2:]],
        }
    )

    healthcare_context_rows = read_sheet_rows(HEALTHCARE_CONTEXT_FILE)
    healthcare_context = pd.DataFrame(
        [
            {
                "indicator": row[0],
                **{
                    int(healthcare_context_rows[0][idx]): safe_int(row[idx])
                    for idx in range(2, len(healthcare_context_rows[0]))
                },
            }
            for row in healthcare_context_rows[1:]
        ]
    )

    education_rows = read_sheet_rows(EDUCATION_FILE)
    education_years = [int(year) for year in education_rows[0][4:]]
    education_records = []
    for row in education_rows[1:]:
        record = {
            "type_of_education": row[0],
            "indicator": row[1],
            "school_type": row[2],
            "units": row[3],
        }
        for idx, year in enumerate(education_years, start=4):
            record[year] = safe_int(row[idx]) if idx < len(row) else None
        education_records.append(record)
    education = pd.DataFrame(education_records)

    gov_students_2024 = int(
        education.loc[
            (education["indicator"] == "Number of Students")
            & (education["school_type"] == "Government Schools"),
            2024,
        ].iloc[0]
    )
    gov_classes_2024 = int(
        education.loc[
            (education["indicator"] == "Number of classes")
            & (education["school_type"] == "Government Schools"),
            2024,
        ].iloc[0]
    )
    gov_teachers_2024 = int(
        education.loc[
            (education["indicator"] == "Teachers")
            & (education["school_type"] == "Government Schools"),
            2024,
        ].iloc[0]
    )
    gov_schools_2024 = int(
        education.loc[
            (education["indicator"] == "Number of Schools")
            & (education["school_type"] == "Government Schools"),
            2024,
        ].iloc[0]
    )

    total_population_2023 = int(
        population.loc[population["year"] == 2023, "total_population"].iloc[0]
    )
    beds_2023 = int(
        healthcare_capacity.loc[healthcare_capacity["year"] == 2023, "beds"].iloc[0]
    )
    omani_2024 = int(
        population.loc[population["year"] == 2024, "omani_population"].iloc[0]
    )
    non_omani_2024 = int(
        population.loc[population["year"] == 2024, "non_omani_population"].iloc[0]
    )
    total_2024 = int(
        population.loc[population["year"] == 2024, "total_population"].iloc[0]
    )

    # Compute demographic ratios from 2024 total age data
    total_2024_age = age_group_data[
        (age_group_data["nationality"] == "Total") & (age_group_data["year"] == 2024)
    ].copy()
    # If no "Total" section, compute from Omani + Non-Omani
    if total_2024_age.empty:
        total_2024_age = (
            age_group_data[
                (age_group_data["nationality"].isin(["Omani", "Non-Omani"]))
                & (age_group_data["year"] == 2024)
            ]
            .groupby("age_group", observed=True)["population"]
            .sum()
            .reset_index()
        )

    total_2024_age["broad_band"] = total_2024_age["age_group"].apply(
        lambda x: _broad_band(str(x))
    )
    band_pops = total_2024_age.groupby("broad_band")["population"].sum()
    grand_total = band_pops.sum() or 1
    youth_pop = band_pops.get("Youth (0-14)", 0)
    working_pop = band_pops.get("Working age (15-64)", 0)
    elderly_pop = band_pops.get("Elderly (65+)", 0)
    dependency_ratio = (
        (youth_pop + elderly_pop) / working_pop if working_pop > 0 else 0.0
    )
    median_age_est = _estimate_median_age(total_2024_age)

    baseline = BaselineMetrics(
        omani_2024=omani_2024,
        non_omani_2024=non_omani_2024,
        total_2024=total_2024,
        beds_2023=beds_2023,
        beds_per_1000=beds_2023 / total_population_2023 * 1000,
        gov_students_2024=gov_students_2024,
        gov_classes_2024=gov_classes_2024,
        gov_teachers_2024=gov_teachers_2024,
        gov_schools_2024=gov_schools_2024,
        gov_student_share=gov_students_2024 / omani_2024,
        students_per_class=gov_students_2024 / gov_classes_2024,
        students_per_teacher=gov_students_2024 / gov_teachers_2024,
        youth_share=round(youth_pop / grand_total * 100, 1),
        working_age_share=round(working_pop / grand_total * 100, 1),
        elderly_share=round(elderly_pop / grand_total * 100, 1),
        dependency_ratio=round(dependency_ratio, 3),
        median_age_estimate=median_age_est,
    )

    return (
        population,
        healthcare_capacity,
        healthcare_context,
        education,
        age_group_data,
        baseline,
    )


def build_projection(
    scenario_name: str,
    omani_growth_pct: float,
    non_omani_growth_pct: float,
    beds_per_1000: float,
    government_student_share_pct: float,
    students_per_class: float,
    students_per_teacher: float,
    baseline: BaselineMetrics,
) -> pd.DataFrame:
    records = []
    omani = float(baseline.omani_2024)
    non_omani = float(baseline.non_omani_2024)

    for year in range(FORECAST_START, FORECAST_END + 1):
        if year > FORECAST_START:
            omani *= 1 + omani_growth_pct / 100
            non_omani *= 1 + non_omani_growth_pct / 100

        total = omani + non_omani
        required_beds = total * beds_per_1000 / 1000
        projected_gov_students = omani * government_student_share_pct / 100
        required_classes = projected_gov_students / students_per_class
        required_teachers = projected_gov_students / students_per_teacher

        records.append(
            {
                "scenario": scenario_name,
                "year": year,
                "omani_population": round(omani),
                "non_omani_population": round(non_omani),
                "total_population": round(total),
                "required_beds": required_beds,
                "bed_gap": required_beds - baseline.beds_2023,
                "projected_gov_students": projected_gov_students,
                "required_classes": required_classes,
                "class_gap": required_classes - baseline.gov_classes_2024,
                "required_teachers": required_teachers,
                "teacher_gap": required_teachers - baseline.gov_teachers_2024,
            }
        )

    return pd.DataFrame(records)


def first_exceeded_year(series: pd.Series, years: pd.Series) -> int | None:
    exceeded = years[series > 0.5]
    if exceeded.empty:
        return None
    return int(exceeded.iloc[0])


def format_year(value: int | None) -> str:
    return "No breach to 2040" if value is None else str(value)


def build_overview_metrics(
    projections: pd.DataFrame, baseline: BaselineMetrics
) -> pd.DataFrame:
    summaries = []
    for scenario_name, scenario_df in projections.groupby("scenario"):
        row_2040 = scenario_df.loc[scenario_df["year"] == FORECAST_END].iloc[0]
        summaries.append(
            {
                "Scenario": scenario_name,
                "2040 population": int(row_2040["total_population"]),
                "2040 beds gap": math.ceil(row_2040["bed_gap"]),
                "2040 class gap": math.ceil(row_2040["class_gap"]),
                "2040 teacher gap": math.ceil(row_2040["teacher_gap"]),
                "Beds exceeded": format_year(
                    first_exceeded_year(scenario_df["bed_gap"], scenario_df["year"])
                ),
                "Classes exceeded": format_year(
                    first_exceeded_year(scenario_df["class_gap"], scenario_df["year"])
                ),
                "Teachers exceeded": format_year(
                    first_exceeded_year(scenario_df["teacher_gap"], scenario_df["year"])
                ),
            }
        )
    return pd.DataFrame(summaries)


def line_chart(
    data: pd.DataFrame, y_field: str, y_title: str, color_title: str = "Scenario"
) -> alt.Chart:
    return (
        alt.Chart(data)
        .mark_line(point=True)
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y(f"{y_field}:Q", title=y_title),
            color=alt.Color("scenario:N", title=color_title),
            tooltip=["scenario", "year", alt.Tooltip(f"{y_field}:Q", format=",.0f")],
        )
    )


(
    population_history,
    healthcare_capacity,
    healthcare_context,
    education,
    age_group_data,
    baseline,
) = load_inputs()

historical_gov_share = [
    round(
        education.loc[
            (education["indicator"] == "Number of Students")
            & (education["school_type"] == "Government Schools"),
            year,
        ].iloc[0]
        / population_history.loc[
            population_history["year"] == year, "omani_population"
        ].iloc[0]
        * 100,
        2,
    )
    for year in range(2015, 2025)
]

historical_students_per_class = [
    round(
        education.loc[
            (education["indicator"] == "Number of Students")
            & (education["school_type"] == "Government Schools"),
            year,
        ].iloc[0]
        / education.loc[
            (education["indicator"] == "Number of classes")
            & (education["school_type"] == "Government Schools"),
            year,
        ].iloc[0],
        2,
    )
    for year in range(2015, 2025)
]

healthcare_facility_2023 = (
    healthcare_context[["indicator", 2023]].copy().rename(columns={2023: "2023"})
)
scenario_order = list(SCENARIO_DEFAULTS.keys())
default_scenario = "Base"
service_assumption_defaults = {
    "bed_standard": round(baseline.beds_per_1000, 2),
    "gov_student_share": round(baseline.gov_student_share * 100, 2),
    "student_per_class": round(baseline.students_per_class, 1),
    "student_per_teacher": round(baseline.students_per_teacher, 1),
}


def reset_service_assumptions() -> None:
    for key, value in service_assumption_defaults.items():
        st.session_state[key] = value


for key, value in service_assumption_defaults.items():
    st.session_state.setdefault(key, value)

with st.sidebar:
    st.title("Muscat 2040 model")
    st.caption(
        "Scenario-driven planning for population growth, healthcare, and education capacity."
    )
    st.markdown(
        ":blue-badge[NCSI data] :orange-badge[2040 horizon] :green-badge[Interactive]"
    )

    selected_scenario = st.segmented_control(
        "Focus scenario",
        options=scenario_order,
        default=default_scenario,
        selection_mode="single",
    )
    selected_scenario = selected_scenario or default_scenario
    st.caption(SCENARIO_NOTES[selected_scenario])

    with st.container(border=True):
        st.markdown("**Population assumptions**")
        omani_growth = st.slider(
            "Omani annual growth (%)",
            min_value=0.0,
            max_value=5.0,
            value=float(SCENARIO_DEFAULTS[selected_scenario]["omani_growth"]),
            step=0.05,
        )
        non_omani_growth = st.slider(
            "Non-Omani annual growth (%)",
            min_value=-1.0,
            max_value=6.0,
            value=float(SCENARIO_DEFAULTS[selected_scenario]["non_omani_growth"]),
            step=0.05,
        )
        st.caption(
            f"Default {selected_scenario.lower()} settings: {SCENARIO_DEFAULTS[selected_scenario]['omani_growth']:.2f}% Omani and {SCENARIO_DEFAULTS[selected_scenario]['non_omani_growth']:.2f}% non-Omani growth."
        )

    with st.container(border=True):
        heading_col, action_col = st.columns([1, 1], vertical_alignment="bottom")
        with heading_col:
            st.markdown("**Service assumptions**")
        with action_col:
            with st.container(horizontal_alignment="right"):
                st.button(
                    ":material/restart_alt: Reset",
                    key="reset_service_assumptions",
                    on_click=reset_service_assumptions,
                )
        bed_standard = st.slider(
            "Beds per 1,000 residents",
            min_value=0.8,
            max_value=2.5,
            step=0.01,
            key="bed_standard",
        )
        gov_student_share = st.slider(
            "Government students as % of Omani population",
            min_value=18.0,
            max_value=32.0,
            step=0.1,
            key="gov_student_share",
        )
        student_per_class = st.slider(
            "Students per class",
            min_value=24.0,
            max_value=40.0,
            step=0.1,
            key="student_per_class",
        )
        student_per_teacher = st.slider(
            "Students per teacher",
            min_value=10.0,
            max_value=20.0,
            step=0.1,
            key="student_per_teacher",
        )
        st.caption(
            f"Observed Muscat baselines: {baseline.beds_per_1000:.2f} beds per 1,000, {baseline.students_per_class:.1f} students per class, and {baseline.students_per_teacher:.1f} students per teacher."
        )

    with st.container(border=True):
        st.markdown("**Baseline at a glance**")
        st.markdown(f"**2024** population: :blue-badge[{baseline.total_2024:,}]")
        st.markdown(f"**2023** beds: :blue-badge[{baseline.beds_2023:,}]")
        st.markdown(
            f"**2024** government students: :blue-badge[{baseline.gov_students_2024:,}]"
        )
        st.markdown(
            f"**2024** government schools: :blue-badge[{baseline.gov_schools_2024:,}]"
        )

    with st.container(border=True):
        st.markdown("**Demographic profile (2024)**")
        st.markdown(f"Youth (0-14): :green-badge[{baseline.youth_share}%]")
        st.markdown(f"Working age (15-64): :blue-badge[{baseline.working_age_share}%]")
        st.markdown(f"Elderly (65+): :orange-badge[{baseline.elderly_share}%]")
        st.markdown(f"Dependency ratio: :red-badge[{baseline.dependency_ratio:.3f}]")
        st.markdown(
            f"Median age (est.): :blue-badge[~{baseline.median_age_estimate:.0f} years]"
        )

scenario_inputs = {
    name: (
        omani_growth if name == selected_scenario else defaults["omani_growth"],
        non_omani_growth if name == selected_scenario else defaults["non_omani_growth"],
    )
    for name, defaults in SCENARIO_DEFAULTS.items()
}

projection_frames = [
    build_projection(
        scenario_name=name,
        omani_growth_pct=inputs[0],
        non_omani_growth_pct=inputs[1],
        beds_per_1000=bed_standard,
        government_student_share_pct=gov_student_share,
        students_per_class=student_per_class,
        students_per_teacher=student_per_teacher,
        baseline=baseline,
    )
    for name, inputs in scenario_inputs.items()
]
projections = pd.concat(projection_frames, ignore_index=True)
overview_metrics = build_overview_metrics(projections, baseline)
focus_projection = projections.loc[projections["scenario"] == selected_scenario].copy()
focus_2040 = focus_projection.loc[focus_projection["year"] == FORECAST_END].iloc[0]

bed_breach_year = first_exceeded_year(
    focus_projection["bed_gap"], focus_projection["year"]
)
class_breach_year = first_exceeded_year(
    focus_projection["class_gap"], focus_projection["year"]
)
teacher_breach_year = first_exceeded_year(
    focus_projection["teacher_gap"], focus_projection["year"]
)

selected_style = SCENARIO_STYLES[selected_scenario]
st.markdown(
    ":blue-badge[Population scenarios] :red-badge[Healthcare stress test] :green-badge[Education stress test]",
    width="content",
)
st.title("Muscat 2040 growth and infrastructure challenge")
st.caption(
    "A decision-support dashboard that turns NCSI Muscat datasets into scenario-based population, hospital bed, and government-school capacity forecasts through 2040."
)

hero_left, hero_right = st.columns([1.45, 1], vertical_alignment="center")

with hero_left.container(border=True):
    st.subheader(":material/public: Why this model matters")
    st.markdown(
        f"{selected_style['icon']} **{selected_scenario} scenario in focus.** Muscat already carries visible pressure in both sectors. "
        "The dashboard keeps current capacity fixed so decision-makers can see how quickly today's assets become insufficient as the population grows."
    )
    st.markdown(
        f"By `2040`, the selected scenario reaches **{int(focus_2040['total_population']):,} residents**, requiring about **{math.ceil(focus_2040['required_beds']):,} beds**, "
        f"**{math.ceil(focus_2040['required_classes']):,} government-school classes**, and **{math.ceil(focus_2040['required_teachers']):,} teachers** if service standards are preserved."
    )
    st.caption(
        f"Baseline used: {baseline.total_2024:,} residents in 2024, {baseline.beds_2023:,} beds in 2023, and {baseline.gov_students_2024:,} government-school students in 2024."
    )

with hero_right.container(border=True):
    st.subheader(":material/insights: Selected scenario snapshot")
    st.metric(
        "2040 population",
        f"{int(focus_2040['total_population']):,}",
        delta=f"{int(focus_2040['total_population'] - baseline.total_2024):,} vs 2024",
        border=True,
    )
    st.metric(
        "2040 bed shortfall",
        f"{math.ceil(focus_2040['bed_gap']):,}",
        delta=f"Breached in {format_year(bed_breach_year)}",
        border=True,
    )
    st.metric(
        "2040 class shortfall",
        f"{math.ceil(focus_2040['class_gap']):,}",
        delta=f"Teacher gap {math.ceil(focus_2040['teacher_gap']):,}",
        border=True,
    )

with st.container(horizontal=True):
    st.metric(
        "2024 population",
        f"{baseline.total_2024:,}",
        delta=f"Omani {baseline.omani_2024:,}",
        border=True,
        chart_data=population_history["total_population"].tolist(),
        chart_type="line",
    )
    st.metric(
        "Current bed standard",
        f"{bed_standard:.2f}",
        delta="Beds per 1,000 residents",
        border=True,
        chart_data=healthcare_capacity["beds"].tolist(),
        chart_type="line",
    )
    st.metric(
        "Government student share",
        f"{gov_student_share:.1f}%",
        delta=f"{baseline.gov_students_2024:,} students in 2024",
        border=True,
        chart_data=historical_gov_share,
        chart_type="line",
    )
    st.metric(
        "Students per class",
        f"{student_per_class:.1f}",
        delta=f"Students per teacher {student_per_teacher:.1f}",
        border=True,
        chart_data=historical_students_per_class,
        chart_type="line",
    )

scenario_columns = st.columns(3)
for column, scenario_name in zip(scenario_columns, scenario_order):
    scenario_df = projections.loc[projections["scenario"] == scenario_name]
    scenario_2040 = scenario_df.loc[scenario_df["year"] == FORECAST_END].iloc[0]
    scenario_style = SCENARIO_STYLES[scenario_name]
    with column.container(border=True):
        st.markdown(
            f"{scenario_style['icon']} :{scenario_style['color']}-badge[{scenario_name}]",
            width="content",
        )
        st.metric(
            "2040 population",
            f"{int(scenario_2040['total_population']):,}",
            delta=f"+{int(scenario_2040['total_population'] - baseline.total_2024):,}",
            border=True,
        )
        st.caption(SCENARIO_NOTES[scenario_name])
        st.write(
            f"Beds gap `{math.ceil(scenario_2040['bed_gap']):,}` | Classes gap `{math.ceil(scenario_2040['class_gap']):,}` | Teachers gap `{math.ceil(scenario_2040['teacher_gap']):,}`"
        )

tab_summary, tab_demographics, tab_healthcare, tab_education, tab_appendix = st.tabs(
    [
        ":material/overview: Scenario summary",
        ":material/groups: Demographics",
        ":material/local_hospital: Healthcare",
        ":material/school: Education",
        ":material/article: Technical appendix",
    ]
)

with tab_summary:
    top_left, top_right = st.columns([1.35, 1])

    with top_left.container(border=True):
        st.subheader("Population outlook to 2040")
        population_chart = line_chart(projections, "total_population", "Residents")
        st.altair_chart(
            population_chart.properties(height=360), use_container_width=True
        )
        st.caption(
            "All scenarios use the same baseline and diverge through different Omani and non-Omani growth assumptions."
        )

    with top_right.container(border=True):
        st.subheader("2040 scenario comparison")
        st.dataframe(
            overview_metrics,
            hide_index=True,
            use_container_width=True,
            column_config={
                "2040 population": st.column_config.NumberColumn(
                    "2040 population", format="%d"
                ),
                "2040 beds gap": st.column_config.NumberColumn(
                    "2040 beds gap", format="%d"
                ),
                "2040 class gap": st.column_config.NumberColumn(
                    "2040 class gap", format="%d"
                ),
                "2040 teacher gap": st.column_config.NumberColumn(
                    "2040 teacher gap", format="%d"
                ),
            },
        )

    bottom_left, bottom_right = st.columns([1.1, 1])
    with bottom_left.container(border=True):
        st.subheader("Population composition in the selected scenario")
        composition_chart = (
            alt.Chart(
                focus_projection.melt(
                    id_vars=["year", "scenario"],
                    value_vars=["omani_population", "non_omani_population"],
                    var_name="group",
                    value_name="population",
                )
            )
            .mark_area(opacity=0.85)
            .encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("population:Q", title="Residents"),
                color=alt.Color(
                    "group:N",
                    title="Population segment",
                    scale=alt.Scale(
                        domain=["omani_population", "non_omani_population"],
                        range=["#215A6D", "#D98C3A"],
                    ),
                ),
                tooltip=["year", "group", alt.Tooltip("population:Q", format=",.0f")],
            )
        )
        st.altair_chart(
            composition_chart.properties(height=320), use_container_width=True
        )

    with bottom_right.container(border=True):
        st.subheader("Planning interpretation")
        st.markdown(
            f"- **Population:** Under the selected {selected_scenario.lower()} scenario, Muscat adds about `{int(focus_2040['total_population'] - baseline.total_2024):,}` residents by `2040`.\n"
            f"- **Healthcare:** Keeping current capacity fixed leaves a shortfall of about `{math.ceil(focus_2040['bed_gap']):,}` beds, with pressure emerging in `{format_year(bed_breach_year)}`.\n"
            f"- **Education:** Government schools need roughly `{math.ceil(focus_2040['class_gap']):,}` extra classes and `{math.ceil(focus_2040['teacher_gap']):,}` extra teachers by `2040`.\n"
            "- **Decision signal:** Population growth alone is not the issue; the risk comes from service capacity expanding more slowly than demand."
        )

with tab_healthcare:
    health_left, health_right = st.columns([1.3, 1])

    health_chart = (
        alt.Chart(
            pd.concat(
                [
                    focus_projection[["year", "required_beds"]]
                    .rename(columns={"required_beds": "value"})
                    .assign(series="Required beds"),
                    pd.DataFrame(
                        {
                            "year": focus_projection["year"],
                            "value": baseline.beds_2023,
                            "series": "Current bed capacity",
                        }
                    ),
                ],
                ignore_index=True,
            )
        )
        .mark_line(point=True)
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("value:Q", title="Beds"),
            color=alt.Color("series:N", title="Measure"),
            tooltip=["series", "year", alt.Tooltip("value:Q", format=",.0f")],
        )
    )

    with health_left.container(border=True):
        st.subheader("Hospital bed demand vs current capacity")
        st.altair_chart(health_chart.properties(height=360), use_container_width=True)
        st.caption(
            "The bed line stays flat to show whether today's observed Muscat capacity would still be enough under future population demand."
        )

    with health_right.container(border=True):
        st.subheader("Healthcare pressure points")
        st.metric("Current beds", f"{baseline.beds_2023:,}", border=True)
        st.metric(
            "Required beds in 2040",
            f"{math.ceil(focus_2040['required_beds']):,}",
            border=True,
        )
        st.metric("2040 bed gap", f"{math.ceil(focus_2040['bed_gap']):,}", border=True)
        st.metric("Capacity exceeded", format_year(bed_breach_year), border=True)
        st.caption(
            "Default bed standard uses the latest observed Muscat ratio and can be tightened or relaxed from the sidebar."
        )

    history_left, history_right = st.columns([1, 1])
    with history_left.container(border=True):
        st.subheader("Healthcare capacity history")
        bed_history_chart = (
            alt.Chart(healthcare_capacity)
            .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("beds:Q", title="Beds"),
                tooltip=["year", alt.Tooltip("beds:Q", format=",.0f")],
            )
        )
        st.altair_chart(
            bed_history_chart.properties(height=280), use_container_width=True
        )

    with history_right.container(border=True):
        st.subheader("Latest healthcare context")
        st.dataframe(
            healthcare_facility_2023,
            hide_index=True,
            use_container_width=True,
            column_config={"2023": st.column_config.NumberColumn("2023", format="%d")},
        )

with tab_education:
    edu_left, edu_right = st.columns([1.3, 1])

    class_chart = (
        alt.Chart(
            pd.concat(
                [
                    focus_projection[["year", "required_classes"]]
                    .rename(columns={"required_classes": "value"})
                    .assign(series="Required classes"),
                    pd.DataFrame(
                        {
                            "year": focus_projection["year"],
                            "value": baseline.gov_classes_2024,
                            "series": "Current classes",
                        }
                    ),
                ],
                ignore_index=True,
            )
        )
        .mark_line(point=True)
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("value:Q", title="Classes"),
            color=alt.Color("series:N", title="Measure"),
            tooltip=["series", "year", alt.Tooltip("value:Q", format=",.0f")],
        )
    )

    with edu_left.container(border=True):
        st.subheader("Government school class demand vs current capacity")
        st.altair_chart(class_chart.properties(height=360), use_container_width=True)
        st.caption(
            "The selected scenario translates projected Omani population into government-school demand using the adjustable student-share assumption."
        )

    with edu_right.container(border=True):
        st.subheader("Education pressure points")
        st.metric("Government schools", f"{baseline.gov_schools_2024:,}", border=True)
        st.metric(
            "2040 class gap", f"{math.ceil(focus_2040['class_gap']):,}", border=True
        )
        st.metric(
            "2040 teacher gap", f"{math.ceil(focus_2040['teacher_gap']):,}", border=True
        )
        st.metric(
            "Capacity exceeded",
            f"Classes {format_year(class_breach_year)} | Teachers {format_year(teacher_breach_year)}",
            border=True,
        )

    edu_bottom_left, edu_bottom_right = st.columns([1, 1])
    with edu_bottom_left.container(border=True):
        st.subheader("Teacher demand vs current capacity")
        teacher_chart = (
            alt.Chart(
                pd.concat(
                    [
                        focus_projection[["year", "required_teachers"]]
                        .rename(columns={"required_teachers": "value"})
                        .assign(series="Required teachers"),
                        pd.DataFrame(
                            {
                                "year": focus_projection["year"],
                                "value": baseline.gov_teachers_2024,
                                "series": "Current teachers",
                            }
                        ),
                    ],
                    ignore_index=True,
                )
            )
            .mark_line(point=True)
            .encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("value:Q", title="Teachers"),
                color=alt.Color("series:N", title="Measure"),
                tooltip=["series", "year", alt.Tooltip("value:Q", format=",.0f")],
            )
        )
        st.altair_chart(teacher_chart.properties(height=280), use_container_width=True)

    with edu_bottom_right.container(border=True):
        st.subheader("Government school baselines")
        government_education = education.loc[
            education["school_type"] == "Government Schools",
            ["indicator", 2021, 2022, 2023, 2024],
        ].copy()
        st.dataframe(government_education, hide_index=True, use_container_width=True)

with tab_demographics:
    # --- Population Pyramid (2024) ---
    demo_top_left, demo_top_right = st.columns([1.3, 1])

    # Build 2024 pyramid data using Omani + Non-Omani
    pyramid_omani = age_group_data[
        (age_group_data["nationality"] == "Omani") & (age_group_data["year"] == 2024)
    ][["age_group", "population"]].rename(columns={"population": "Omani"})
    pyramid_non_omani = age_group_data[
        (age_group_data["nationality"] == "Non-Omani")
        & (age_group_data["year"] == 2024)
    ][["age_group", "population"]].rename(columns={"population": "Non-Omani"})
    pyramid_df = pyramid_omani.merge(
        pyramid_non_omani, on="age_group", how="outer"
    ).fillna({"Omani": 0, "Non-Omani": 0})
    pyramid_df["Omani"] = -pyramid_df["Omani"]  # Negative for left side
    pyramid_long = pyramid_df.melt(
        id_vars="age_group",
        value_vars=["Omani", "Non-Omani"],
        var_name="nationality",
        value_name="population",
    )

    pyramid_chart = (
        alt.Chart(pyramid_long)
        .mark_bar()
        .encode(
            y=alt.Y("age_group:N", title="Age group", sort=AGE_GROUP_ORDER),
            x=alt.X(
                "population:Q",
                title="Population",
                axis=alt.Axis(format=",.0f", labelExpr="abs(datum.value)"),
            ),
            color=alt.Color(
                "nationality:N",
                title="Nationality",
                scale=alt.Scale(
                    domain=["Omani", "Non-Omani"], range=["#215A6D", "#D98C3A"]
                ),
            ),
            tooltip=[
                alt.Tooltip("age_group:N", title="Age group"),
                alt.Tooltip("nationality:N", title="Nationality"),
                alt.Tooltip("population:Q", title="Population", format=",.0f"),
            ],
        )
    )

    with demo_top_left.container(border=True):
        st.subheader("Population pyramid (2024)")
        st.altair_chart(pyramid_chart.properties(height=420), use_container_width=True)
        st.caption(
            "Omani population extends to the left, Non-Omani to the right. "
            "The large Non-Omani working-age bulge (25-44) reflects expatriate labor concentration."
        )

    # --- Key Demographic Metrics ---
    with demo_top_right.container(border=True):
        st.subheader("Demographic indicators (2024)")
        st.metric(
            "Estimated median age",
            f"~{baseline.median_age_estimate:.0f} years",
            border=True,
        )
        st.metric(
            "Dependency ratio",
            f"{baseline.dependency_ratio:.3f}",
            delta="(Youth+Elderly) / Working-age",
            border=True,
        )
        st.metric("Youth share (0-14)", f"{baseline.youth_share}%", border=True)
        st.metric(
            "Working-age share (15-64)", f"{baseline.working_age_share}%", border=True
        )
        st.metric("Elderly share (65+)", f"{baseline.elderly_share}%", border=True)
        st.caption(
            "A dependency ratio below 0.5 indicates a favourable workforce balance. "
            "Muscat's young population and large expatriate workforce keep the ratio low."
        )

    # --- Dependency Ratio Over Time ---
    demo_mid_left, demo_mid_right = st.columns([1.3, 1])

    # Compute broad-band shares per year for Total/combined nationality
    combined_by_year = age_group_data[
        age_group_data["nationality"].isin(["Omani", "Non-Omani"])
    ].copy()
    combined_by_year["broad_band"] = combined_by_year["age_group"].apply(
        lambda x: _broad_band(str(x))
    )
    band_year = (
        combined_by_year.groupby(["year", "broad_band"])["population"]
        .sum()
        .reset_index()
    )
    year_totals = (
        band_year.groupby("year")["population"]
        .sum()
        .reset_index()
        .rename(columns={"population": "year_total"})
    )
    band_year = band_year.merge(year_totals, on="year")
    band_year["share"] = round(
        band_year["population"] / band_year["year_total"] * 100, 1
    )

    dependency_chart = (
        alt.Chart(band_year)
        .mark_area(opacity=0.7)
        .encode(
            x=alt.X("year:O", title="Year"),
            y=alt.Y("share:Q", title="Share (%)", stack="normalize"),
            color=alt.Color(
                "broad_band:N",
                title="Age band",
                sort=["Youth (0-14)", "Working age (15-64)", "Elderly (65+)"],
                scale=alt.Scale(
                    domain=["Youth (0-14)", "Working age (15-64)", "Elderly (65+)"],
                    range=["#4CAF50", "#2196F3", "#FF9800"],
                ),
            ),
            tooltip=[
                "year",
                "broad_band",
                alt.Tooltip("share:Q", format=".1f", title="Share %"),
            ],
        )
    )

    with demo_mid_left.container(border=True):
        st.subheader("Age structure evolution (2015-2024)")
        st.altair_chart(
            dependency_chart.properties(height=320), use_container_width=True
        )
        st.caption(
            "Stacked area shows how the three broad age bands have shifted over the decade. "
            "The working-age share dominates due to Muscat's large expatriate labour force."
        )

    # --- Projected 2040 Pyramid ---
    with demo_mid_right.container(border=True):
        st.subheader("Projected 2040 age structure")
        # Simple projection: scale each nationality's 2024 age-group populations
        omani_growth_factor = (1 + omani_growth / 100) ** (
            FORECAST_END - FORECAST_START
        )
        non_omani_growth_factor = (1 + non_omani_growth / 100) ** (
            FORECAST_END - FORECAST_START
        )

        proj_omani = pyramid_omani.copy()
        proj_omani["Omani"] = round(-proj_omani["Omani"] * omani_growth_factor).astype(
            int
        )
        proj_non_omani = pyramid_non_omani.copy()
        proj_non_omani["Non-Omani"] = round(
            proj_non_omani["Non-Omani"] * non_omani_growth_factor
        ).astype(int)

        proj_df = proj_omani.merge(proj_non_omani, on="age_group", how="outer").fillna(
            {"Omani": 0, "Non-Omani": 0}
        )
        proj_df["Total"] = proj_df["Omani"] + proj_df["Non-Omani"]

        # Show as a bar chart comparison: 2024 total vs 2040 projected total
        current_totals = pyramid_omani.copy()
        current_totals["Omani"] = -current_totals["Omani"]  # Flip back to positive
        current_totals = current_totals.merge(
            pyramid_non_omani, on="age_group", how="outer"
        ).fillna({"Omani": 0, "Non-Omani": 0})
        current_totals["population"] = (
            current_totals["Omani"] + current_totals["Non-Omani"]
        )
        current_totals["period"] = "2024 (actual)"

        proj_totals = proj_df[["age_group"]].copy()
        proj_totals["population"] = proj_df["Omani"] + proj_df["Non-Omani"]
        proj_totals["period"] = f"2040 ({selected_scenario})"

        comparison_df = pd.concat(
            [
                current_totals[["age_group", "population", "period"]],
                proj_totals[["age_group", "population", "period"]],
            ],
            ignore_index=True,
        )

        comparison_chart = (
            alt.Chart(comparison_df)
            .mark_bar(opacity=0.8)
            .encode(
                y=alt.Y("age_group:N", title="Age group", sort=AGE_GROUP_ORDER),
                x=alt.X(
                    "population:Q", title="Population", axis=alt.Axis(format=",.0f")
                ),
                color=alt.Color(
                    "period:N",
                    title="Period",
                    scale=alt.Scale(range=["#607D8B", "#E91E63"]),
                ),
                xOffset="period:N",
                tooltip=[
                    alt.Tooltip("age_group:N", title="Age group"),
                    alt.Tooltip("period:N", title="Period"),
                    alt.Tooltip("population:Q", title="Population", format=",.0f"),
                ],
            )
        )
        st.altair_chart(
            comparison_chart.properties(height=420), use_container_width=True
        )
        st.caption(
            f"Projection uses {selected_scenario} scenario rates: {omani_growth:.2f}% Omani, {non_omani_growth:.2f}% Non-Omani."
        )

    # --- Age-Cohort Growth Heatmap ---
    demo_bottom_left, demo_bottom_right = st.columns([1.3, 1])

    with demo_bottom_left.container(border=True):
        st.subheader("Age-cohort growth rates (year-over-year)")
        # Use Omani + Non-Omani combined
        combined_ages = (
            age_group_data[age_group_data["nationality"].isin(["Omani", "Non-Omani"])]
            .groupby(["year", "age_group"], observed=True)["population"]
            .sum()
            .reset_index()
        )
        # Pivot and compute YoY growth
        pivot = combined_ages.pivot(
            index="age_group", columns="year", values="population"
        )
        growth_pct = pivot.pct_change(axis=1) * 100
        growth_pct = growth_pct.drop(
            columns=[growth_pct.columns[0]], errors="ignore"
        )  # drop first year (no prior)

        # Melt for Altair heatmap
        growth_long = growth_pct.reset_index().melt(
            id_vars="age_group", var_name="year", value_name="growth_pct"
        )
        growth_long = growth_long.dropna(subset=["growth_pct"])

        heatmap = (
            alt.Chart(growth_long)
            .mark_rect()
            .encode(
                x=alt.X("year:O", title="Year"),
                y=alt.Y("age_group:N", title="Age group", sort=AGE_GROUP_ORDER),
                color=alt.Color(
                    "growth_pct:Q",
                    title="Growth %",
                    scale=alt.Scale(scheme="redblue", domainMid=0, reverse=True),
                ),
                tooltip=[
                    alt.Tooltip("age_group:N", title="Age group"),
                    alt.Tooltip("year:O", title="Year"),
                    alt.Tooltip("growth_pct:Q", title="Growth %", format=".1f"),
                ],
            )
        )
        st.altair_chart(heatmap.properties(height=420), use_container_width=True)
        st.caption(
            "Blue cells indicate negative growth; red cells indicate positive growth. "
            "2019 Non-Omani data shows anomalies (data quality issue in source)."
        )

    # --- School-age and Elderly Projection ---
    with demo_bottom_right.container(border=True):
        st.subheader("Planning-relevant age segments")
        st.markdown(
            f"Under the **{selected_scenario}** scenario with the selected growth rates:"
        )

        # Current school-age (5-19) and elderly (65+) populations
        school_age_groups = ["5 - 9", "10 - 14", "15 - 19"]
        elderly_groups = ["65 - 69", "70 - 74", "75 - 79", "+80"]

        combined_2024 = age_group_data[
            (age_group_data["nationality"].isin(["Omani", "Non-Omani"]))
            & (age_group_data["year"] == 2024)
        ]

        school_age_2024 = int(
            combined_2024[combined_2024["age_group"].isin(school_age_groups)][
                "population"
            ].sum()
        )
        elderly_2024 = int(
            combined_2024[combined_2024["age_group"].isin(elderly_groups)][
                "population"
            ].sum()
        )

        # Project to 2040 using blended growth
        omani_2024_school = age_group_data[
            (age_group_data["nationality"] == "Omani")
            & (age_group_data["year"] == 2024)
            & (age_group_data["age_group"].isin(school_age_groups))
        ]["population"].sum()
        non_omani_2024_school = age_group_data[
            (age_group_data["nationality"] == "Non-Omani")
            & (age_group_data["year"] == 2024)
            & (age_group_data["age_group"].isin(school_age_groups))
        ]["population"].sum()

        school_age_2040 = int(
            omani_2024_school * omani_growth_factor
            + non_omani_2024_school * non_omani_growth_factor
        )

        omani_2024_elderly = age_group_data[
            (age_group_data["nationality"] == "Omani")
            & (age_group_data["year"] == 2024)
            & (age_group_data["age_group"].isin(elderly_groups))
        ]["population"].sum()
        non_omani_2024_elderly = age_group_data[
            (age_group_data["nationality"] == "Non-Omani")
            & (age_group_data["year"] == 2024)
            & (age_group_data["age_group"].isin(elderly_groups))
        ]["population"].sum()

        elderly_2040 = int(
            omani_2024_elderly * omani_growth_factor
            + non_omani_2024_elderly * non_omani_growth_factor
        )

        st.metric(
            "School-age (5-19) in 2024",
            f"{school_age_2024:,}",
            border=True,
        )
        st.metric(
            "School-age (5-19) projected 2040",
            f"{school_age_2040:,}",
            delta=f"+{school_age_2040 - school_age_2024:,}",
            border=True,
        )
        st.metric(
            "Elderly (65+) in 2024",
            f"{elderly_2024:,}",
            border=True,
        )
        st.metric(
            "Elderly (65+) projected 2040",
            f"{elderly_2040:,}",
            delta=f"+{elderly_2040 - elderly_2024:,}",
            border=True,
        )
        st.caption(
            "School-age projections feed into education demand. Elderly projections "
            "signal future geriatric healthcare and pension system pressure."
        )

with tab_appendix:
    appendix_left, appendix_right = st.columns([1.05, 1])

    with appendix_left.container(border=True):
        st.subheader("Data sources")
        st.markdown(
            f"- `{POPULATION_FILE.name}` - Muscat population by nationality and age group, 2015-2024\n"
            f"- `{HEALTHCARE_CAPACITY_FILE.name}` - Muscat hospital beds, 2014-2023\n"
            f"- `{HEALTHCARE_CONTEXT_FILE.name}` - Muscat healthcare facilities, 2014-2023\n"
            f"- `{EDUCATION_FILE.name}` - Muscat school students, classes, teachers, and schools, 2015-2024\n"
            "- NCSI portals: [data.gov.om](https://data.gov.om) and [ncsi.gov.om](https://www.ncsi.gov.om)"
        )

        st.subheader("Core formulas")
        st.markdown(
            "- `Population_t = Population_(t-1) * (1 + growth rate)` for Omani and non-Omani segments separately\n"
            "- `Required beds = projected total population * beds per 1,000 / 1,000`\n"
            "- `Projected government students = projected Omani population * government student share`\n"
            "- `Required classes = projected government students / students per class`\n"
            "- `Required teachers = projected government students / students per teacher`"
        )

    with appendix_right.container(border=True):
        st.subheader("Assumptions and limits")
        assumptions_df = pd.DataFrame(
            [
                {
                    "Assumption": "Capacity test",
                    "Value": "Current healthcare beds and government-school capacity stay fixed at the latest observed level.",
                },
                {
                    "Assumption": "Population structure",
                    "Value": "Omani and non-Omani residents are forecast separately because migration volatility mainly affects the non-Omani segment.",
                },
                {
                    "Assumption": "Healthcare standard",
                    "Value": f"Default bed ratio is set to the observed Muscat level of {baseline.beds_per_1000:.2f} beds per 1,000 residents.",
                },
                {
                    "Assumption": "Education demand",
                    "Value": f"Default government-student share is {baseline.gov_student_share * 100:.2f}% of the Omani population.",
                },
                {
                    "Assumption": "Education efficiency",
                    "Value": f"Default class and teacher standards are {baseline.students_per_class:.1f} students per class and {baseline.students_per_teacher:.1f} students per teacher.",
                },
            ]
        )
        st.dataframe(assumptions_df, hide_index=True, use_container_width=True)

        with st.expander("How to reproduce the model", icon=":material/play_circle:"):
            st.markdown(
                "1. Keep all `.xlsx` files in the `dataset/` folder (sibling of `streamlit_app.py`).\n"
                "2. Install dependencies with `py -3 -m pip install -r requirements.txt`.\n"
                "3. Run the app with `py -3 -m streamlit run streamlit_app.py`.\n"
                "4. Adjust the sidebar assumptions and export the scenario outputs for the final submission."
            )

        with st.expander("Decision-maker recommendations", icon=":material/lightbulb:"):
            st.markdown(
                "1. Prioritize healthcare expansion planning because the current bed base is already below the implied requirement under every scenario.\n"
                "2. Phase government-school expansion around class and teacher hiring, not only school construction, because both gaps widen materially by 2040.\n"
                "3. Refresh the model annually using the latest NCSI and sector updates so Muscat can react early to migration-led demand shifts."
            )
