"""Geometric health validation for 3D-printable STL meshes.

Detects:
- Unexpected part counts (disconnected components)
- Floating parts not grounded at Z=0
- Weak structural bottlenecks (< min_thickness_mm)

All error messages are purely factual — no fix suggestions or code pointers.
"""

import math

import numpy as np


def analyze_mesh_geometry(
    stl_path: str,
    expected_parts_count: int = 1,
    min_thickness_mm: float = 1.2,
    bed_threshold_mm: float = 0.4,
) -> list[str]:
    """Analyze an STL mesh for geometric health issues.

    Args:
        stl_path: Path to the STL file to analyze.
        expected_parts_count: How many disconnected parts the user expects.
        min_thickness_mm: Minimum acceptable joint/wall thickness in mm.
        bed_threshold_mm: Maximum Z_min for a part to be considered grounded.

    Returns:
        A list of factual error strings. Empty list means all checks passed.
    """
    try:
        import trimesh
    except ImportError:
        print("[MESH] ⚠️ trimesh not installed, skipping geometry health check")
        return []

    errors: list[str] = []

    try:
        mesh = trimesh.load(stl_path, force="mesh")
    except Exception as e:
        errors.append(f"Geometry Error: Failed to load STL for analysis: {e}")
        return errors

    if mesh.is_empty or len(mesh.vertices) == 0:
        errors.append("Geometry Error: STL mesh is empty (no geometry).")
        return errors

    # ── Split into disconnected components ────────────────────────
    try:
        bodies = mesh.split(only_watertight=False)
    except Exception:
        # If splitting fails, treat as single body
        bodies = [mesh]

    num_bodies = len(bodies)

    # ── Part Count Check ─────────────────────────────────────────
    if num_bodies != expected_parts_count:
        errors.append(
            f"Geometry Error: Expected {expected_parts_count} part(s) "
            f"(from 'expected_parts_count': {expected_parts_count}), "
            f"but found {num_bodies} disconnected part(s)."
        )

    # ── Grounding Check ──────────────────────────────────────────
    for i, body in enumerate(bodies, start=1):
        if body is None or not hasattr(body, "vertices") or len(body.vertices) == 0:
            continue
        z_min = float(body.vertices[:, 2].min())
        if z_min > bed_threshold_mm:
            bounds = body.bounds  # [[x_min, y_min, z_min], [x_max, y_max, z_max]]
            centroid = body.centroid
            errors.append(
                f"Geometry Error: Part #{i} starts at Z={z_min:.2f}mm in mid-air "
                f"(Bounding Box: [X: {bounds[0][0]:.1f} to {bounds[1][0]:.1f}, "
                f"Y: {bounds[0][1]:.1f} to {bounds[1][1]:.1f}, "
                f"Z: {bounds[0][2]:.1f} to {bounds[1][2]:.1f}], "
                f"Centroid: [{centroid[0]:.1f}, {centroid[1]:.1f}, {centroid[2]:.1f}])."
            )

    # ── Weak Connection / Bottleneck Check ───────────────────────
    _check_bottlenecks(bodies, min_thickness_mm, errors)

    return errors


def _check_bottlenecks(
    bodies: list,
    min_thickness_mm: float,
    errors: list[str],
) -> None:
    """Check each body for structural bottlenecks thinner than min_thickness_mm.

    Uses voxelization followed by binary erosion to detect pinch points where
    a single body splits into multiple regions after erosion — indicating a
    structural weak point.
    """
    try:
        from scipy.ndimage import binary_erosion, label
    except ImportError:
        print("[MESH] ⚠️ scipy not installed, skipping bottleneck check")
        return

    BASE_PITCH = 0.4  # mm — matches nozzle diameter

    for i, body in enumerate(bodies, start=1):
        if body is None or (hasattr(body, "vertices") and len(body.vertices) == 0):
            continue
        # Adaptive pitch: for very large models, scale pitch to keep memory & runtime reasonable
        max_extent = float(body.extents.max()) if hasattr(body, "extents") else 50.0
        pitch = max(BASE_PITCH, max_extent / 250.0)

        erosion_voxels = max(1, int(math.ceil((min_thickness_mm / 2.0) / pitch)))

        try:
            voxelized = body.voxelized(pitch=pitch)
            # trimesh voxelized() only generates surface shell voxels by default.
            # We must call fill() so the voxel grid represents a solid 3D volume.
            voxel_grid = voxelized.fill().matrix
        except Exception:
            # Voxelization can fail on degenerate geometry; skip silently
            continue

        if voxel_grid is None or voxel_grid.size == 0:
            continue

        # Build a spherical structuring element for erosion
        struct_size = 2 * erosion_voxels + 1
        struct = np.zeros((struct_size, struct_size, struct_size), dtype=bool)
        center = erosion_voxels
        for x in range(struct_size):
            for y in range(struct_size):
                for z in range(struct_size):
                    if (x - center) ** 2 + (y - center) ** 2 + (z - center) ** 2 <= center**2:
                        struct[x, y, z] = True

        # Erode the voxel grid
        eroded = binary_erosion(voxel_grid, structure=struct)

        if not eroded.any():
            # Entire body eroded away — the body itself is thinner than min_thickness_mm
            # This is already caught by the slicer / other checks, so skip
            continue

        # Label connected regions in the eroded grid
        labeled, num_features = label(eroded)

        if num_features > 1:
            # Body split into multiple regions after erosion → bottleneck detected
            # Filter noise specks (< 4 voxels) and sort features by size descending
            counts = np.bincount(labeled.flat)
            valid_labels = [lbl for lbl in range(1, num_features + 1) if counts[lbl] >= 4]

            if len(valid_labels) < 2:
                # Disconnection was just tiny surface discretization specks, not a real bottleneck
                continue

            sorted_labels = sorted(valid_labels, key=lambda l: counts[l], reverse=True)

            # Locate bottleneck: find closest voxels between the two largest split features
            try:
                from scipy.spatial import KDTree

                coords1 = np.argwhere(labeled == sorted_labels[0])
                coords2 = np.argwhere(labeled == sorted_labels[1])

                if len(coords1) > 0 and len(coords2) > 0:
                    kdt1 = KDTree(coords1)
                    d, nearest_indices = kdt1.query(coords2)
                    min_idx2 = int(np.argmin(d))
                    pt2 = coords2[min_idx2]
                    pt1 = coords1[nearest_indices[min_idx2]]
                    midpoint_voxel = (pt1 + pt2) / 2.0
                else:
                    midpoint_voxel = np.argwhere(voxel_grid & ~eroded).mean(axis=0)

                origin = voxelized.transform[:3, 3]
                world_coords = origin + midpoint_voxel * pitch
            except Exception:
                # Fallback to centroid of eroded boundary
                boundary = voxel_grid & ~eroded
                centroid_voxel = (
                    np.argwhere(boundary).mean(axis=0) if boundary.any() else np.zeros(3)
                )
                world_coords = centroid_voxel * pitch

            errors.append(
                f"Geometry Error: Part #{i} contains a weak structural bottleneck "
                f"(< {min_thickness_mm}mm) near "
                f"[X={world_coords[0]:.1f}, Y={world_coords[1]:.1f}, "
                f"Z={world_coords[2]:.1f}mm]."
            )
