# Aircraft explorer and autofill

The page includes a local SVG schematic with keyboard-accessible controls for
airframe, wings, engines and landing gear. Selection changes the highlighted area
and the text-topic search action. No component/document mapping is fabricated.
The image is explicitly generic; it does not change geometry to claim an exact
model representation. Reduced-motion preferences disable its selection transition.

## Inputs

- EMB-120 preset: Embraer, model EMB-120, serial 120006.
- Boeing preset: model 757-200, line 95, docket FAA-2003-16646.
- Airbus preset: Airbus Industrie, model A320-211, MSN 1516, docket 2002-NM-224-AD.
  Its label explicitly identifies it as historical, and loading it enables
  historical retrieval/comparison. Other presets switch those options off.

These are demo configurations, not saved or verified aircraft records. Presets
replace the visible configuration, clear prior condition attestations and results,
and wait for the user to search. Live model/identifier labels reflect form inputs.

`GET /api/aircraft` supplies indexed model/manufacturer names for input suggestions.
The underlying index contains metadata and text candidates; suggestions do not
establish a model's verified manufacturer or applicability. Selecting a model
does not infer its manufacturer or serial number.

`POST /api/autofill` accepts `{"query":"A320-211 MSN 1516"}`. Local interpretation
and query parsing extract stated fields. The response does not call an external
model. Multiple models/manufacturers or conflicting identifiers require
clarification. No serial, bulletin answer or historical comparison is inferred.
HTTP input/origin validation is retained.

The browser fills empty configuration fields and preserves conflicting existing
values with an explicit review message. A response for an edited question is
discarded. The normal search conflict checks still apply before returning results.

## Verification

The complete suite passed 118 tests. Browser checks confirmed preset autofill,
engine selection/highlighting, extraction of A320-211/MSN 1516 with manufacturer
left blank, and preservation of an existing conflicting model. SVG/CSS layout
was inspected in the browser; JavaScript syntax checks passed.
