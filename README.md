# AnesthesiaTOC  

[![Demo](https://img.shields.io/badge/demo-live-brightgreen)](https://helenopaiva.github.io/AnesthesiaTOC/)
![GitHub Actions](https://github.com/HelenoPaiva/AnesthesiaTOC/actions/workflows/update.yml/badge.svg)
![Last Commit](https://img.shields.io/github/last-commit/HelenoPaiva/AnesthesiaTOC)
![License](https://img.shields.io/badge/license-academic--use-lightgrey)

[![DOI](https://zenodo.org/badge/1128458328.svg)](https://doi.org/10.5281/zenodo.18180894)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Data Source](https://img.shields.io/badge/data-Crossref%20%7C%20PubMed-orange)
![Domain](https://img.shields.io/badge/domain-anesthesiology-blueviolet)


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
- Automatic loading up to ~1000 articles, followed by manual “Load more”  
- Persistent local bookmarking (browser-based, no accounts)  
- Static deployment via GitHub Pages (no server-side maintenance)

---

## System architecture

The system is deliberately divided into two independent layers.

### 1. Data acquisition (automation layer)

- Implemented in **Python**
- Executed via **GitHub Actions** on a scheduled basis
- Queries the **Crossref REST API** using journal ISSNs
- Retrieves substantially more than 30 articles per journal (configurable)
- Optionally resolves DOIs to PubMed identifiers using **NCBI E-utilities**
- Produces static datasets:
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

- SJR is a **journal-level** metric (not article-level)
- The system automatically uses the **latest year available** in the upstream dataset
- Metrics are refreshed automatically via a scheduled workflow
- If the upstream source is temporarily unavailable, existing metrics are preserved

No journal tiers or quartile labels are used.

---

## Data sources

- **Crossref REST API**  
  Used to retrieve bibliographic metadata for journal articles by ISSN.

- **SCImago Journal Rank (SJR)**  
  Used to obtain journal-level ranking metrics for ordering the journal list.

- **NCBI E-utilities (PubMed)**  
  Used to resolve DOIs to PubMed identifiers when available.

All data sources are publicly accessible and do not require API keys for standard use.

---

## Update mechanism

Datasets are regenerated automatically using **GitHub Actions** on a scheduled basis (configurable; typically daily or weekly).

Each execution:

1. Queries Crossref for recent articles from each journal  
2. Normalizes and de-duplicates records  
3. Selects a non-future publication date  
4. Identifies articles published ahead of print  
5. Optionally enriches entries with PubMed links  
6. Updates journal-level SJR metrics when available  
7. Writes new dataset files only when changes are detected  

The web interface reflects updates automatically after deployment.

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

> Oliveira HP. **AnesthesiaTOC: an automated, web-based dashboard for continuous surveillance of anesthesiology literature** [software]. GitHub.  
> Available at: https://github.com/HelenoPaiva/AnesthesiaTOC. Accessed YYYY-MM-DD.

---

## License

This repository is intended for open academic use.  
License details should be finalized prior to formal publication or commercialization.

---

## Author

**Heleno de Paiva Oliveira, MD, PhD**  
Professor of Anesthesiology  
Universidade Federal do Rio Grande do Norte (UFRN), Brazil
