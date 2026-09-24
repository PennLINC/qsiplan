# Changelog

## Unreleased

- Add documentation site.

## 0.4.1 (2026-09-23)

- Pair reverse-PE DWIs past a GRE IntendedFor; run single-warp corrections per encoding (#12)

## 0.4.0 (2026-09-09)

- Add a session axis to grouping (#6)
- Fix the "auto" setting in the web apps (#7)
- Refactor so all the domain knowledge goes in one spot (#8)
- Robustly handle phase and magnitude (#10)

## 0.3.1 (2026-09-03)

- Replace the fieldmap-less flags with `--sdc-anat-reference` / `--force sdc-anat-reference`

## 0.3.0 (2026-09-03)

- Add group-level dashboard (#2)
- Rename repository from QSIPlan to qsiplan (#5)
- Switch to bids2table (#3)

## 0.2.0 (2026-08-26)

- Add `--ignore pepolar-dwis` to prevent using DWIs for SDC. This allows users
  to preferentially use `fmap/*_epi` fieldmaps, or to ignore fieldmaps and
  pepolar-dwis to force fieldmap-less corrections.

## 0.1.0 (2026-08-26)

- Initial release: a `qsiplan` CLI that shows how QSIPrep will process a BIDS
  dataset.
