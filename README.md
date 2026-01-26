# AnesthesiaTOC  

[![DOI](https://img.shields.io/badge/DOI-10.5281%2Fzenodo.18180894-blue)](https://doi.org/10.5281/zenodo.18180894)
[![GitHub Pages](https://img.shields.io/badge/live-GitHub%20Pages-brightgreen)](https://helenopaiva.github.io/AnesthesiaTOC/)
[![GitHub release](https://img.shields.io/github/v/release/HelenoPaiva/Retraction-Radar)](https://github.com/HelenoPaiva/Retraction-Radar/releases/tag/v1.0.0)
![Last commit](https://img.shields.io/github/last-commit/HelenoPaiva/AnesthesiaTOC)
![License](https://img.shields.io/github/license/HelenoPaiva/AnesthesiaTOC)


https://helenopaiva.github.io/AnesthesiaTOC/

**An automated, open, web-based dashboard for continuous surveillance of anesthesiology literature**

---

## Overview

**AnesthesiaTOC** is a fully automated, static web dashboard designed to aggregate and display the most recent tables of contents from major anesthesiology journals in a unified, continuously updated interface.

The project addresses a common challenge in anesthesiology practice and academia: maintaining situational awareness of newly published literature across multiple journals and publication platforms without reliance on fragmented alerting systems, proprietary feeds, or subscription-dependent tools.

The dashboard relies exclusively on **open scholarly metadata infrastructures** and **static web technologies**, requiring no backend server, database, or user authentication.

---

## Key features

- Automated retrieval of recent articles from selected anesthesiology journals  
- Journal identification by **ISSN** (single ISSN per journal for robustness)  
- **Journal ordering by SCImago Journal Rank (SJR)** using the latest available metric  
- Unified metadata structure (title, authors, publication date, DOI, journal)  
- Automatic de-duplication using digital object identifiers (DOI)  
- Robust publication date handling (future “issue” dates excluded)  
- Detection and labeling of **Ahead of Print** articles  
- Optional PubMed enrichment with direct PubMed links  
- Client-side full-text search and journal filtering  
- **Progressive loading / infinite scrolling** of large datasets  
- Automatic loading of large article sets (≫30 per journal), with optional “Load more”  
- Persistent local bookmarking (browser-based, no accounts)  
- Static deployment via GitHub Pages (no server-side maintenance)

---

## System architecture

The system is deliberately divided into two independent layers.

### 1. Data acquisition (automation layer)

- Implemented in **Python**
- Executed via **GitHub Actions** on a scheduled basis
- Queries the **Crossref REST API** using journal ISSNs
- Retrieves a configurable number of recent articles per journal
- Normalizes, validates, and de-duplicates records
- Applies logic to select non-future publication dates
- Identifies articles published **Ahead of Print**
- Optionally resolves DOIs to PubMed identifiers via **NCBI E-utilities**
- Generates static datasets:
  - `data.json` (article-level metadata)
  - `journal_metrics.json` (journal-level SJR metrics)

All processing occurs offline during the automated build step.

---

### 2. Presentation (frontend layer)

- Implemented as a static single-page web application
- Uses **HTML, CSS, and vanilla JavaScript**
- Loads and renders datasets entirely client-side
- Progressive rendering ensures fast initial load even with large datasets
- No tracking, cookies, analytics, or user data transmission

This separation ensures reproducibility, transparency, and minimal operational complexity.

---

## Journal ranking and metrics

Journal ordering in the interface is based on **SCImago Journal Rank (SJR)**:

- SJR is treated strictly as a **journal-level metric**
- The system automatically uses the **latest year available** in the upstream dataset
- Metrics are refreshed automatically via a scheduled workflow
- If the upstream source is temporarily unavailable, previously stored metrics are preserved
- Journals without available SJR data are listed at the end of the menu

No tier system or quartile labels are used.

---

## Data sources

- **Crossref REST API**  
  Bibliographic metadata for journal articles by ISSN.

- **SCImago Journal Rank (SJR)**  
  Journal-level bibliometric ranking used for ordering.

- **NCBI E-utilities (PubMed)**  
  Optional DOI-to-PMID resolution and PubMed linking.

All data sources are publicly accessible and do not require API keys for standard use.

---

## Update mechanism

Datasets are regenerated automatically using **GitHub Actions** on a scheduled basis (configurable; typically daily or weekly).

Each execution:

1. Queries Crossref for recent articles  
2. Normalizes and de-duplicates records  
3. Applies publication date validation  
4. Detects Ahead-of-Print articles  
5. Optionally enriches entries with PubMed identifiers  
6. Updates journal-level SJR metrics  
7. Writes new dataset files only when changes are detected  

The web interface reflects updates automatically after deployment.

---

## Software registration and intellectual property

The software has been **registered as a Programa de Computador** at the  
**Instituto Nacional da Propriedade Industrial (INPI), Brazil**, under **personal ownership**, prior to journal submission.

This registration establishes legal authorship and priority while remaining fully compatible with open academic dissemination and future development.

---

## Customization and reuse

This project is explicitly designed to be **forked and adapted**.

Common customization options include:

- Modifying `sources.json` to add or remove journals  
- Adapting the dashboard for other medical specialties  
- Adjusting article volume per journal and global dataset limits  
- Changing update frequency in GitHub Actions workflows  
- Translating interface text or adapting date formats  

No backend infrastructure changes are required.

---

## Privacy and data protection

- No user accounts  
- No cookies or analytics  
- No personal data collection  
- All interactions occur locally in the browser  

The project is suitable for public academic and institutional deployment.

---

## Intended use

- Literature surveillance for anesthesiology clinicians  
- Academic and educational environments (journal clubs, residency programs)  
- Research topic monitoring and horizon scanning  
- Demonstration of reproducible, low-cost academic software tooling  

This tool is **not intended to replace bibliographic databases** or systematic review platforms.

---

## How to cite

If you use, adapt, or build upon **AnesthesiaTOC** in academic work, teaching materials, or derivative projects, please cite it as a research software resource.

### Recommended citation (Vancouver / AMA)

> Oliveira HP. **AnesthesiaTOC: an automated, web-based dashboard for continuous surveillance of anesthesiology literature** [software]. Zenodo.  
> https://doi.org/10.5281/zenodo.18180895

---

## License

This repository is intended for open academic use.  
License terms will be finalized prior to any commercial deployment.

---

## Author

**Heleno de Paiva Oliveira, MD, PhD**  
Professor of Anesthesiology  
Universidade Federal do Rio Grande do Norte (UFRN), Brazil
