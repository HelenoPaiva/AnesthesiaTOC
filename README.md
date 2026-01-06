# AnesthesiaTOC

**An automated, open, web-based dashboard for continuous surveillance of anesthesiology literature**

---

## Overview

**AnesthesiaTOC** is a fully automated, static web dashboard designed to aggregate and display the most recent tables of contents from major anesthesiology journals in a unified, continuously updated interface.

The project addresses a common challenge in anesthesiology practice and academia: maintaining awareness of newly published literature across multiple journals, subspecialties, and publication platforms without reliance on fragmented alerting systems or proprietary tools.

The dashboard relies exclusively on **open scholarly metadata infrastructures** and **static web technologies**, requiring no backend server, database, or user authentication.

---

## Key features

- Automated retrieval of recent articles from selected anesthesiology journals  
- Journal identification by ISSN, with support for multiple ISSNs per journal  
- Tier-based journal organization (core, subspecialty, regional)  
- Unified metadata structure (title, authors, publication date, DOI, journal, tier)  
- Automatic de-duplication using digital object identifiers  
- Robust publication date handling (future “issue” dates excluded)  
- Detection and labeling of **Ahead of Print** articles  
- Optional PubMed enrichment with direct PubMed links  
- Client-side full-text search and journal filtering  
- Persistent local bookmarking (browser-based, no accounts)  
- Static deployment via GitHub Pages (no server-side maintenance)

---

## System architecture

The system is deliberately divided into two independent layers.

### 1. Data acquisition (automation layer)

- Implemented in **Python**
- Executed via **GitHub Actions** on a scheduled basis
- Queries the **Crossref REST API** using journal ISSNs
- Optionally resolves DOIs to PubMed identifiers using **NCBI E-utilities**
- Produces a single static dataset (`data.json`)

### 2. Presentation (frontend layer)

- Implemented as a static single-page web application
- Uses **HTML, CSS, and vanilla JavaScript**
- Loads and renders `data.json` entirely client-side
- No tracking, cookies, or user data transmission

This separation ensures reproducibility, transparency, and low operational complexity.

---

## Data sources

- **Crossref REST API**  
  Used to retrieve bibliographic metadata for journal articles by ISSN.

- **NCBI E-utilities (PubMed)**  
  Used to resolve DOIs to PubMed identifiers when available.

All data sources are publicly accessible and do not require API keys for standard use.

---

## Update mechanism

The dataset is regenerated automatically using **GitHub Actions** on a scheduled basis (e.g., hourly or daily, configurable in the workflow file).

Each execution:
1. Queries Crossref for recent articles  
2. Normalizes and de-duplicates records  
3. Selects a non-future publication date  
4. Identifies articles published ahead of print  
5. Optionally enriches entries with PubMed links  
6. Writes a new `data.json` file only when changes are detected  

The web interface reflects updates immediately after deployment.

---

## Customization

This project is designed to be **forked and adapted**.

Common customization options include:
- Modifying `sources.json` to add or remove journals  
- Adapting journal tiers to local or institutional preferences  
- Repurposing the dashboard for other medical specialties  
- Adjusting update frequency in the GitHub Actions workflow  
- Translating interface text or adapting date formats  

No backend infrastructure changes are required.

---

## Privacy and data protection

- No user accounts  
- No cookies or analytics  
- No personal data collection  
- All interactions occur locally in the browser  

The project is suitable for public academic deployment.

---

## Intended use

- Literature surveillance for anesthesiology clinicians  
- Academic and educational environments (journal clubs, residency programs)  
- Research monitoring and topic awareness  
- Demonstration of reproducible, low-cost academic tooling  

This tool is **not intended to replace bibliographic databases** or systematic review platforms.

---

## How to cite

If you use, adapt, or build upon **AnesthesiaTOC** in academic work, teaching materials, or derivative projects, please cite it as a research software resource.

### Recommended citation (Vancouver / AMA)

> Oliveira H.P. **AnesthesiaTOC: an automated, web-based dashboard for continuous surveillance of anesthesiology literature** [software]. GitHub. Available at: https://github.com/HelenoPaiva/AnesthesiaTOC. Accessed YYYY-MM-DD.

---

## License

This repository is intended for open academic use.
License details should be specified prior to formal publication or commercialization.

---

## Author

Heleno de Paiva Oliveira, MD, PhD

Anesthesiology Professor

Universidade Federal do Rio Grande do Norte (UFRN), Brazil
