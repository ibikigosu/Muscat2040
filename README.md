<div align="center">

# Muscat 2040: Growth & Infrastructure Model

[![Streamlit](https://img.shields.io/badge/Streamlit->=1.55-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)](https://streamlit.io)
[![Python](https://img.shields.io/badge/Python-3-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org)

An interactive dashboard for forecasting Muscat Governorate's population through 2040 and checking whether healthcare and education infrastructure can keep up.

[Overview](#overview) • [Features](#features) • [Getting started](#getting-started) • [How it works](#how-it-works) • [Project structure](#project-structure) • [Data sources](#data-sources)

</div>

## Overview

Muscat is growing, and that growth puts pressure on hospitals, schools, and other public services at a rate that's easy to underestimate from static reports. This project builds a reproducible model that projects Muscat's population to 2040 and asks a simple question: does current infrastructure capacity hold?

Built as a submission for the [Rihal Codestacker](https://www.rihal.om/) Data Analyst challenge, the dashboard covers two sectors — healthcare and education — with adjustable assumptions so planners can test their own numbers rather than just accept defaults.

> [!NOTE]
> The model holds current infrastructure capacity constant. It is designed for scenario planning, not formal capital budgeting without further sector validation.

## Features

- Three population scenarios (Low, Base, High) with separate growth rates for Omani and non-Omani residents
- Hospital bed demand projected against current capacity, with automatic detection of the year the gap turns positive
- Government school class and teacher requirements linked to Omani population growth
- Sidebar controls for growth rates, bed standards, class sizes, and student-teacher ratios — no code changes needed
- No database, no API calls. Three Python packages and four local Excel files.

## Getting started

### Prerequisites

- [Python 3.10+](https://www.python.org/downloads/)

### Installation

```bash
# Clone the repository
git clone https://github.com/ibikigosu/Muscat2040.git
cd Muscat2040

# Install dependencies
pip install -r requirements.txt
```

### Run the dashboard

```bash
streamlit run streamlit_app.py
```

The app opens at `http://localhost:8501`. Use the sidebar to switch scenarios and adjust assumptions.

> [!TIP]
> On Windows, use `py -3 -m streamlit run streamlit_app.py` if `streamlit` is not on your PATH.

## How it works

| Layer | Description |
|---|---|
| Population projection | Omani and non-Omani residents are projected separately using compound annual growth from a 2024 baseline of ~1.50 million. |
| Healthcare | Projected total population converts to required hospital beds, compared against the 2023 observed capacity of 1,608 beds. |
| Education | Projected Omani population converts to government-school students, then to required classes and teachers using configurable ratios. |
| Sensitivity | Growth rates, bed standards, class sizes, and teacher ratios are all adjustable from the sidebar. Results update on every change. |

### Scenario defaults

| Scenario | Omani growth | Non-Omani growth | 2040 population estimate |
|---|---|---|---|
| Low | 1.22% | 0.39% | ~1.68 million |
| Base | 2.02% | 1.59% | ~1.98 million |
| High | 2.82% | 2.99% | ~2.38 million |

## Project structure

```
├── streamlit_app.py          # Main Streamlit application
├── .streamlit/
│   └── config.toml           # Theme (colors, fonts, chart palettes)
├── requirements.txt          # Python dependencies
│
├── Muscat - Last 10 years Population *.xlsx   # Population 2015–2024
├── Muscat - Number of Beds - Hospitals *.xlsx # Hospital beds 2014–2023
├── Muscat - Healthcare Total.xlsx             # Healthcare facilities 2014–2023
├── Muscat - Education *.xlsx                  # Education metrics 2015–2024
│
├── executive_summary.md      # Decision-maker summary
├── technical_appendix.md     # Assumptions, formulas, reproduction steps
└── muscat_2040_challenge.md  # Original challenge brief
```

## Data sources

The data comes from the [National Centre for Statistics and Information (NCSI)](https://data.gov.om) of the Sultanate of Oman:

| Dataset | Time span | Key metrics |
|---|---|---|
| Muscat population | 2015–2024 | Omani, non-Omani, and total residents |
| Hospital beds | 2014–2023 | Bed count across Muscat facilities |
| Healthcare facilities | 2014–2023 | Hospitals, health centres, clinics, pharmacies |
| Education | 2015–2024 | Students, classes, teachers, schools by type |

> [!IMPORTANT]
> The `.xlsx` files are parsed directly from their raw OOXML structure using Python's built-in `zipfile` and `xml.etree.ElementTree` — no `openpyxl` required.

## Tech stack

| Technology | Role |
|---|---|
| [Streamlit](https://streamlit.io) | Web application framework |
| [Pandas](https://pandas.pydata.org) | Data manipulation |
| [Altair](https://altair-viz.github.io) | Declarative charting |
