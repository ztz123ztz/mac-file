# mac-bom usage
Locked columns (order immutable): `Item,Qty,Unit,Part,Type,Material,Spec-PartNo,Refs`.
Lock rules: (1) header exact; (2) Spec-PartNo non-empty (drawing no. or standard);
(3) Qty numeric, from geometry only; (4) `# Part-Version:` footer present.
`miss` + substitute note in footer Notes for unmatched purchased lines.
