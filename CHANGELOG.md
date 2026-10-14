# Changelog

## Unreleased

- The fieldmap-less correction is chosen by a method (`--sdc-fieldmapless-method`: `topup`
  for SyNb0 + TOPUP, `syn`, or `unpile`, which is experimental) and an anatomical reference
  (`--sdc-anat-reference`: `synb0`, `invt1w`, `t2w`), with the issues each combination
  raises, instead of `auto` and the rule that a T2w selects a correction by itself. The
  correction stays off by default (`none`): an output no fieldmap reaches is left uncorrected,
  and the `no-sdc` warning names the flag that would correct it.
- unpile is a correction method of its own (`CorrectionMethod.UNPILE`), planned for every head
  motion method against the synthetic b=0 or the T2w, with a GRE fieldmap that also lists the
  series as its prior.
- The cohort dashboard tracks T1w presence.
- Fix a dataset that sits below a directory named `sub-*` (e.g. a per-subject working
  directory, `work/sub-01/data/sub-01/dwi/`) being read with the wrong datatype, so its
  images were neither DWIs nor fieldmaps. The datatype is now read from the file's own
  directory instead of from the first `sub-*` component of the absolute path.
- Add documentation site.
- With a `MultipartID` anywhere in a subject, no concatenation is guessed: a series
  without one is its own output, even when it shares a distortion or a fieldmap with
  another series. Subjects with no `MultipartID` are grouped as before.
- Reverse-PE pairing is inferred only for subjects with no linked fieldmap. A link in one
  session (`B0Field*`, or an epi fieldmap's `IntendedFor`) now stops the inference in every
  session of the subject; the check used to be per session.
- Stop mentioning qsiprep's deprecated `--denoise-after-combining` in reports. Series
  are always denoised on their own; the concepts page explains how to concatenate
  series yourself before running qsiprep if you want them denoised together.

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
