# mac-qa-kernel usage
Reads STEP via OCP directly (needs cadquery-ocp, pulled by build123d).
`--holes` takes measured through-hole diameters (advisory ISO 273 notes).
JSON report: `ran`, `solids`, `volumes_mm3`, `checks[]`, `errors[]`.
`ran:false` means the engine could not run — treat as unknown, never as pass.
