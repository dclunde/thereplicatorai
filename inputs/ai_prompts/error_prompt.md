The user originally requested: "{user_request}"

Your previous OpenSCAD code failed to compile, slice, or violated physical constraints.

Here is the code you generated:
```openscad
{broken_code}
```

Physical Properties (if geometry was compiled):
- Bounding Size: {size}

Here is the error log from the slicer/compiler:
```
{error_log}
```

Geometric Health Check Results:
{geometry_errors}

Fix the errors in the code so that it compiles and slices successfully. Also, strictly verify that the object meets all design guidelines:
- The object must be centered on X=0, Y=0 with its flat bottom resting on Z=0.
- All geometry must be wrapped in a single top-level `union()` to guarantee a watertight solid.
- No floating, disconnected, or hovering parts.
- All joined (`union()`) or subtracted (`difference()`) parts must overlap by at least 0.1mm.
- Minimum wall thickness of 1.2mm.
- Self-supporting overhangs <= 55 degrees are preferred.
- If you define `module` blocks, you MUST instantiate the main module at the bottom of the script.

IMPORTANT: Return a valid JSON object containing ONLY the "code" key. Do not return filename or color_hierarchy.
