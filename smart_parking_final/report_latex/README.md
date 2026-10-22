# Smart Parking System: English LaTeX Report

English report following the eight-chapter structure of the supplied final-report
template. The institutional details are preserved: PHENIKAA UNIVERSITY, SCHOOL
OF INFORMATION SYSTEMS, CSE703095 - SOFTWARE REQUIREMENTS.

## Compile

- Main document: `main.tex`. Compile with **XeLaTeX** three times (contents, lists, and references settle on the third pass).
- Windows: run `./build.ps1` from PowerShell.
- Overleaf: upload the complete ZIP, select `main.tex` as the main document,
  and select **XeLaTeX** as the compiler.
- The compiled PDF is `main.pdf`. The delivered English PDF is also available
  at `output/pdf/smart_parking_report_en.pdf` in the project.
- Requires TeX Live or MiKTeX with fontspec, TikZ, longtable, tabularx, titlesec,
  fancyhdr, fancyvrb, enumitem, setspace, etoolbox, hyperref, and basic packages.
- Times New Roman and Arial are preferred; TeX Gyre Termes/Heros are fallbacks.

Team members have been entered in `main.tex`:

- Đỗ Minh Trí - 24100517
- Nguyễn Bá Trường Giang - 24100470

The other cover fields are set in `main.tex`: `\instructor`, `\classinfo`, `\semester`,
and `\submissiondate`. Edit them there if the values change.

## Files

```text
main.tex                Entry point, formatting, and cover information
chapters/               Eight chapters, conclusion, and two appendices
figures/                Editable TikZ diagrams
generated/              Generated tables, results, and source excerpts
data/parking_data.xlsx  Workbook snapshot used for this report
data/dataset.json       All fields and rows of the 13 sheets
data/diagrams.mmd       Mermaid models exported from the source
source/                 Unmodified copies of the five project Python files
evidence/               Test results, scenarios, NFR results, and hashes
refresh_evidence.py      Regenerates evidence and English tables
build.ps1               Runs XeLaTeX three times
```

## Refresh evidence (optional)

Requires Python and `openpyxl` (`python -m pip install openpyxl`).

```powershell
python refresh_evidence.py
./build.ps1
```

Use `python refresh_evidence.py --tables-only` to regenerate English tables
from the existing evidence without rerunning measurements. Both modes use the
bundled snapshots and do not write to the original project workbook. If the
dataset changes, manually review narrative counts and conclusions as well.

Performance measurements vary by machine. Uptime comes from an assumed outage
model, not production monitoring. Cameras, payment, devices, and synchronization
are simulated. Specification defects, unimplemented encryption, and evidence
limitations remain explicit; no field-study figures have been invented.

Appendix A indexes the schema and supplementary tables; the complete dataset
is supplied as JSON and a workbook. Appendix B includes core excerpts and
references the full source rather than printing all Python code in the PDF.
