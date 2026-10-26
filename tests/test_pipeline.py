import os
import sys

# Add the root directory to the python path so we can import shared
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from src.core.slicer.slicer_3mf import compile_and_slice


from src.core.utils import has_openscad, has_slicer


@pytest.mark.skipif(
    not (has_openscad() and has_slicer()), reason="OpenSCAD or Slicer not installed on host machine"
)
def test_pipeline_no_print():
    print("Running pipeline test without print step...")

    # Create a temporary SCAD file in the current directory
    scad_content = "cube([10, 10, 10]);"
    out_dir = os.path.dirname(os.path.abspath(__file__))
    test_scad = os.path.join(out_dir, "test_gemini_output.scad")

    with open(test_scad, "w") as f:
        f.write(scad_content)

    print(f"Testing pipeline for {test_scad}...")
    try:
        out_3mf = compile_and_slice(test_scad, out_dir)

        assert out_3mf is not None, "Pipeline failed to produce 3MF output"
        assert os.path.exists(out_3mf), f"Output 3MF {out_3mf} does not exist"
        assert os.path.getsize(out_3mf) > 0, "Output 3MF is empty"

        print(f"Success! Pipeline produced valid 3MF: {out_3mf}")

    finally:
        # Cleanup
        if os.path.exists(test_scad):
            os.remove(test_scad)

        stl_file = test_scad.replace(".scad", ".stl")
        if os.path.exists(stl_file):
            os.remove(stl_file)

        if "out_3mf" in locals() and out_3mf and os.path.exists(out_3mf):
            os.remove(out_3mf)

    print("All tests passed successfully!")


if __name__ == "__main__":
    test_pipeline_no_print()
