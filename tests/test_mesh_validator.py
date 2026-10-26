"""Tests for the mesh geometry validator (shared/mesh_validator.py).

Covers:
- Single grounded cube → 0 errors
- Floating cube (Z=10mm) → floating error with coordinates
- Part count mismatch (2 bodies, expected 1)
- Matching part count (2 bodies, expected 2, both grounded) → 0 errors
- Bottleneck detection (dumbbell shape with thin neck)
- Edge cases: empty mesh, missing file
"""

import os
import struct
import sys
import tempfile


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.slicer.mesh_validator import analyze_mesh_geometry


# ── Helper: write a binary STL cube ─────────────────────────────


def _write_binary_stl_box(path, x_min, y_min, z_min, x_max, y_max, z_max):
    """Write a valid binary STL file for an axis-aligned box."""
    # Define the 12 triangles of a box
    verts = [
        (x_min, y_min, z_min),
        (x_max, y_min, z_min),
        (x_max, y_max, z_min),
        (x_min, y_max, z_min),
        (x_min, y_min, z_max),
        (x_max, y_min, z_max),
        (x_max, y_max, z_max),
        (x_min, y_max, z_max),
    ]
    # 12 triangles forming the 6 faces
    triangles = [
        # Bottom (Z_min)
        (0, 2, 1, (0, 0, -1)),
        (0, 3, 2, (0, 0, -1)),
        # Top (Z_max)
        (4, 5, 6, (0, 0, 1)),
        (4, 6, 7, (0, 0, 1)),
        # Front (Y_min)
        (0, 1, 5, (0, -1, 0)),
        (0, 5, 4, (0, -1, 0)),
        # Back (Y_max)
        (2, 3, 7, (0, 1, 0)),
        (2, 7, 6, (0, 1, 0)),
        # Left (X_min)
        (0, 4, 7, (-1, 0, 0)),
        (0, 7, 3, (-1, 0, 0)),
        # Right (X_max)
        (1, 2, 6, (1, 0, 0)),
        (1, 6, 5, (1, 0, 0)),
    ]

    with open(path, "wb") as f:
        f.write(b"\0" * 80)  # header
        f.write(struct.pack("<I", len(triangles)))
        for v0i, v1i, v2i, normal in triangles:
            f.write(struct.pack("<3f", *normal))
            f.write(struct.pack("<3f", *verts[v0i]))
            f.write(struct.pack("<3f", *verts[v1i]))
            f.write(struct.pack("<3f", *verts[v2i]))
            f.write(struct.pack("<H", 0))  # attribute byte count


def _write_multi_box_stl(path, boxes):
    """Write a binary STL with multiple separate boxes (disconnected bodies)."""
    all_triangles_data = []
    for x_min, y_min, z_min, x_max, y_max, z_max in boxes:
        verts = [
            (x_min, y_min, z_min),
            (x_max, y_min, z_min),
            (x_max, y_max, z_min),
            (x_min, y_max, z_min),
            (x_min, y_min, z_max),
            (x_max, y_min, z_max),
            (x_max, y_max, z_max),
            (x_min, y_max, z_max),
        ]
        triangles = [
            (0, 2, 1, (0, 0, -1)),
            (0, 3, 2, (0, 0, -1)),
            (4, 5, 6, (0, 0, 1)),
            (4, 6, 7, (0, 0, 1)),
            (0, 1, 5, (0, -1, 0)),
            (0, 5, 4, (0, -1, 0)),
            (2, 3, 7, (0, 1, 0)),
            (2, 7, 6, (0, 1, 0)),
            (0, 4, 7, (-1, 0, 0)),
            (0, 7, 3, (-1, 0, 0)),
            (1, 2, 6, (1, 0, 0)),
            (1, 6, 5, (1, 0, 0)),
        ]
        for v0i, v1i, v2i, normal in triangles:
            all_triangles_data.append((verts[v0i], verts[v1i], verts[v2i], normal))

    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(all_triangles_data)))
        for v0, v1, v2, normal in all_triangles_data:
            f.write(struct.pack("<3f", *normal))
            f.write(struct.pack("<3f", *v0))
            f.write(struct.pack("<3f", *v1))
            f.write(struct.pack("<3f", *v2))
            f.write(struct.pack("<H", 0))


# ── Test Cases ───────────────────────────────────────────────────


def test_single_grounded_cube_no_errors(tmp_path):
    """A single cube sitting at Z=0 should produce zero errors."""
    stl_file = str(tmp_path / "cube.stl")
    _write_binary_stl_box(stl_file, -5, -5, 0, 5, 5, 10)

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=1)
    assert errors == [], f"Expected no errors, got: {errors}"


def test_floating_cube_flags_error(tmp_path):
    """A cube at Z=10mm should be flagged as floating."""
    stl_file = str(tmp_path / "floating.stl")
    _write_binary_stl_box(stl_file, -5, -5, 10, 5, 5, 20)

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=1, bed_threshold_mm=0.4)
    assert len(errors) >= 1
    assert any("mid-air" in e for e in errors), f"Expected floating error, got: {errors}"
    assert any("Z=10.00" in e for e in errors), f"Expected Z coordinate, got: {errors}"


def test_part_count_mismatch(tmp_path):
    """Two separate cubes when expected_parts_count=1 should flag mismatch."""
    stl_file = str(tmp_path / "two_cubes.stl")
    _write_multi_box_stl(
        stl_file,
        [
            (-20, -5, 0, -10, 5, 10),  # left cube at Z=0
            (10, -5, 0, 20, 5, 10),  # right cube at Z=0
        ],
    )

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=1)
    assert any("Expected 1 part" in e and "2 disconnected" in e for e in errors), (
        f"Expected part count mismatch error, got: {errors}"
    )


def test_matching_part_count_both_grounded(tmp_path):
    """Two cubes both at Z=0 with expected_parts_count=2 → 0 errors."""
    stl_file = str(tmp_path / "two_cubes_ok.stl")
    _write_multi_box_stl(
        stl_file,
        [
            (-20, -5, 0, -10, 5, 10),
            (10, -5, 0, 20, 5, 10),
        ],
    )

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=2)
    # No part count error, no floating error
    part_errors = [e for e in errors if "Expected" in e or "mid-air" in e]
    assert part_errors == [], f"Expected no part/floating errors, got: {part_errors}"


def test_floating_with_count_mismatch(tmp_path):
    """One grounded + one floating cube, expected 1 → flags both issues."""
    stl_file = str(tmp_path / "mixed.stl")
    _write_multi_box_stl(
        stl_file,
        [
            (-20, -5, 0, -10, 5, 10),  # grounded
            (10, -5, 15, 20, 5, 25),  # floating at Z=15
        ],
    )

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=1)
    assert any("Expected 1 part" in e for e in errors)
    assert any("mid-air" in e for e in errors)


def test_nonexistent_file():
    """Analyzing a nonexistent file should return an error, not crash."""
    errors = analyze_mesh_geometry("/nonexistent/path/model.stl")
    assert len(errors) >= 1
    assert any("Failed to load" in e for e in errors)


def test_empty_defaults():
    """Default expected_parts_count=1 should be used when not specified."""
    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as f:
        path = f.name
        _write_binary_stl_box(path, -5, -5, 0, 5, 5, 10)

    try:
        errors = analyze_mesh_geometry(path)
        assert errors == []
    finally:
        os.unlink(path)


def test_bottleneck_detection_dumbbell(tmp_path):
    """A dumbbell (two lobes joined by a 0.6mm neck) should flag a bottleneck.

    Uses trimesh revolve to create a true single-manifold watertight mesh
    with a structural pinch-point thinner than min_thickness_mm (1.2mm).
    """
    import numpy as np
    import trimesh

    # Revolve a 2D profile around Z to form a solid watertight dumbbell:
    # - Start at axis (Z=0, R=0)
    # - Bottom lobe: radius 5mm (Z=0 to Z=4)
    # - Thin neck: radius 0.3mm (Z=8 to Z=12) -> 0.6mm diameter (< 1.2mm threshold)
    # - Top lobe: radius 5mm (Z=16 to Z=20)
    # - End at axis (Z=20, R=0)
    profile = np.array(
        [
            [0.0, 0.0],
            [5.0, 0.0],
            [5.0, 4.0],
            [0.3, 8.0],
            [0.3, 12.0],
            [5.0, 16.0],
            [5.0, 20.0],
            [0.0, 20.0],
        ]
    )
    combined = trimesh.creation.revolve(profile, sections=16)

    stl_file = str(tmp_path / "dumbbell.stl")
    combined.export(stl_file)

    errors = analyze_mesh_geometry(
        stl_file,
        expected_parts_count=1,
        min_thickness_mm=1.2,
        bed_threshold_mm=0.4,
    )

    assert len(errors) >= 1
    assert any("bottleneck" in e for e in errors), f"Expected bottleneck error, got: {errors}"
    assert any("< 1.2mm" in e for e in errors), f"Expected threshold in error, got: {errors}"
    assert any("Z=10.0" in e for e in errors), f"Expected neck Z coordinate, got: {errors}"


def test_empty_mesh_with_zero_triangles(tmp_path):
    """An STL file with 0 triangles should be flagged as empty without crashing."""
    empty_stl = str(tmp_path / "empty.stl")
    with open(empty_stl, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", 0))  # 0 triangles

    errors = analyze_mesh_geometry(empty_stl)
    assert len(errors) >= 1
    assert any("empty" in e.lower() for e in errors)


def test_multipart_assembly_with_floating_lid(tmp_path):
    """When expected_parts_count=2, a grounded base and a hovering lid flags ONLY the lid as floating."""
    stl_file = str(tmp_path / "box_and_lid.stl")
    _write_multi_box_stl(
        stl_file,
        [
            (-15, -15, 0, 15, 15, 10),  # Grounded main box (Z=0 to Z=10)
            (-12, -12, 18, 12, 12, 22),  # Floating lid hovering at Z=18
        ],
    )

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=2, bed_threshold_mm=0.4)
    # Part count matches (2 parts expected, 2 found), so no part-count error
    assert not any("Expected 2 part" in e for e in errors)
    # But part #2 is hovering in mid-air at Z=18
    assert any("mid-air" in e and "Z=18.00" in e for e in errors)


def test_noise_immunity_on_solid_primitive(tmp_path):
    """A smooth solid sphere should pass without any false bottleneck errors."""
    import trimesh

    sphere = trimesh.creation.icosphere(radius=8, subdivisions=2)
    sphere.apply_translation([0, 0, 8])  # Grounded on Z=0
    stl_file = str(tmp_path / "sphere.stl")
    sphere.export(stl_file)

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=1, min_thickness_mm=1.2)
    assert errors == [], f"Expected no errors on solid sphere, got: {errors}"


def test_adaptive_pitch_on_large_mesh(tmp_path):
    """A larger mesh (> 100mm) runs smoothly with adaptive pitch scaling without timeout or memory error."""
    import trimesh

    # 120mm wide box
    box = trimesh.creation.box(extents=[120, 20, 20])
    box.apply_translation([0, 0, 10])  # Grounded on Z=0
    stl_file = str(tmp_path / "large_box.stl")
    box.export(stl_file)

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=1, min_thickness_mm=1.2)
    assert errors == [], f"Expected no errors on grounded large box, got: {errors}"


def test_trimesh_import_error_skips_gracefully(monkeypatch, tmp_path):
    """When trimesh is unavailable, analyze_mesh_geometry returns [] without raising."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "trimesh":
            raise ImportError("No module named trimesh")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert analyze_mesh_geometry(str(tmp_path / "any.stl")) == []


def test_scipy_import_error_skips_bottleneck_check(monkeypatch, tmp_path):
    """When scipy is unavailable, _check_bottlenecks returns gracefully without error."""
    import builtins
    from src.core.slicer.mesh_validator import _check_bottlenecks

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError("No module named scipy")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    errors = []
    _check_bottlenecks([], 1.2, errors)
    assert errors == []


def test_mesh_split_exception_falls_back_to_single_body(monkeypatch, tmp_path):
    """If mesh.split raises an exception, it treats the mesh as a single body."""
    import trimesh

    stl_file = str(tmp_path / "split_fail.stl")
    _write_binary_stl_box(stl_file, -5, -5, 0, 5, 5, 10)

    orig_load = trimesh.load

    def fake_load(*args, **kwargs):
        m = orig_load(*args, **kwargs)
        m.split = lambda **kw: (_ for _ in ()).throw(RuntimeError("split failed"))
        return m

    monkeypatch.setattr(trimesh, "load", fake_load)
    errors = analyze_mesh_geometry(stl_file, expected_parts_count=1)
    assert errors == []


def test_thin_wafer_erodes_completely_without_false_bottleneck(tmp_path):
    """A thin wafer (< min_thickness_mm) that erodes completely should not crash or report split features."""
    stl_file = str(tmp_path / "thin_wafer.stl")
    # 0.5mm tall wafer when min_thickness_mm is 2.0mm -> erodes completely
    _write_binary_stl_box(stl_file, -5, -5, 0, 5, 5, 0.5)

    errors = analyze_mesh_geometry(stl_file, expected_parts_count=1, min_thickness_mm=2.0)
    assert errors == []


def test_voxelization_exception_and_empty_grid_handled():
    """Degenerate bodies whose voxelization raises or returns empty matrix are skipped safely."""
    import numpy as np
    from src.core.slicer.mesh_validator import _check_bottlenecks

    class FailingBody:
        extents = np.array([10.0, 10.0, 10.0])

        def voxelized(self, pitch):
            raise ValueError("degenerate mesh")

    class EmptyGridBody:
        extents = np.array([10.0, 10.0, 10.0])

        def voxelized(self, pitch):
            class Filled:
                matrix = np.zeros((0, 0, 0), dtype=bool)

            class Vox:
                def fill(self):
                    return Filled()

            return Vox()

    errors = []
    _check_bottlenecks([FailingBody(), EmptyGridBody()], 1.2, errors)
    assert errors == []


def test_bottleneck_noise_speck_and_kdtree_fallback(monkeypatch):
    """Verify <4 voxel specks are ignored and KDTree error falls back to boundary centroid."""
    import numpy as np
    import scipy.ndimage
    import scipy.spatial
    from src.core.slicer.mesh_validator import _check_bottlenecks

    class MockVoxBody:
        extents = np.array([10.0, 10.0, 10.0])

        def voxelized(self, pitch):
            class Filled:
                matrix = np.ones((10, 10, 10), dtype=bool)

            class Vox:
                transform = np.eye(4)

                def fill(self):
                    return Filled()

            return Vox()

    # Case 1: 2 features after erosion, but second feature has only 2 voxels (< 4 speck threshold)
    def fake_erosion(grid, structure):
        er = np.zeros_like(grid, dtype=bool)
        er[1:4, 1:4, 1:4] = True  # 27 voxels
        er[8, 8, 8:10] = True  # 2 voxels (speck)
        return er

    monkeypatch.setattr(scipy.ndimage, "binary_erosion", fake_erosion)
    errors = []
    _check_bottlenecks([MockVoxBody()], 1.2, errors)
    assert errors == []

    # Case 2: 2 valid features (>= 4 voxels each), but KDTree raises an exception -> fallback to boundary centroid
    def fake_erosion_two_valid(grid, structure):
        er = np.zeros_like(grid, dtype=bool)
        er[1:3, 1:3, 1:3] = True  # 8 voxels
        er[7:9, 7:9, 7:9] = True  # 8 voxels
        return er

    monkeypatch.setattr(scipy.ndimage, "binary_erosion", fake_erosion_two_valid)
    monkeypatch.setattr(
        scipy.spatial,
        "KDTree",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("KDTree error")),
    )
    errors2 = []
    _check_bottlenecks([MockVoxBody()], 1.2, errors2)
    assert len(errors2) == 1
    assert "weak structural bottleneck" in errors2[0]
