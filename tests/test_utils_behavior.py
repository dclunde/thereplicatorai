import os
import sys
import tempfile

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.utils import (
    calculate_max_dimensions,
    get_blandness_score,
    get_closest_color_name,
    match_color,
)


def test_calculate_max_dimensions():
    print("Testing calculate_max_dimensions...")

    # 1. Test with an empty or non-existent file
    assert calculate_max_dimensions("non_existent_file.stl") == (0.0, 0.0, 0.0), (
        "Should return (0.0, 0.0, 0.0) for missing file"
    )

    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"")
        empty_name = f.name
    try:
        assert calculate_max_dimensions(empty_name) == (0.0, 0.0, 0.0), (
            "Should return (0.0, 0.0, 0.0) for empty file"
        )
    finally:
        os.remove(empty_name)

    # 2. Test ASCII STL parsing
    ascii_stl = b"""solid cube
  facet normal 0 0 0
    outer loop
      vertex 0 0 0
      vertex 10 0 0
      vertex 0 10 0
    endloop
  endfacet
endsolid cube
"""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(ascii_stl)
        ascii_name = f.name
    try:
        w, d, h = calculate_max_dimensions(ascii_name)
        assert abs(w - 10.0) < 0.01 and abs(d - 10.0) < 0.01 and abs(h - 0.0) < 0.01, (
            f"ASCII STL failed, got {w}, {d}, {h}"
        )
    finally:
        os.remove(ascii_name)


def test_get_blandness_score():
    print("Testing get_blandness_score...")

    # Perfect gray/black/white should have 0 blandness (meaning very bland)
    assert get_blandness_score("#000000") == 0
    assert get_blandness_score("#FFFFFF") == 0
    assert get_blandness_score("#808080") == 0

    # Vibrant colors should have high blandness score
    assert get_blandness_score("#FF0000") > 200, "Red should be vibrant"
    assert get_blandness_score("#00FF00") > 200, "Green should be vibrant"

    # Edge cases
    assert get_blandness_score("invalid") == 999, "Invalid hex should return 999"
    assert get_blandness_score("#F") == 999, "Short hex should return 999"
    # Handling missing hash
    assert get_blandness_score("FFFFFF") == 0, "Should handle missing hash"


def test_get_closest_color_name():
    print("Testing get_closest_color_name...")

    assert get_closest_color_name("#FF0000") == "Red"
    assert get_closest_color_name("#00FF00") == "Green"
    assert get_closest_color_name("#0000FF") == "Blue"
    assert get_closest_color_name("#FFFFFF") == "White"
    assert get_closest_color_name("#000000") == "Black"

    # Inexact matches
    assert get_closest_color_name("#FE0101") == "Red"

    # Edge cases
    assert get_closest_color_name("invalid") == "Unknown"
    assert get_closest_color_name("") == "Unknown"
    assert get_closest_color_name(None) == "Unknown"


def test_match_color():
    print("Testing match_color edge cases...")

    # No colors available
    assert match_color([], {}, 100) is None

    # Mixed valid and invalid types
    available = {
        0: {"color": "FF0000", "type": "PETG", "remain": 100, "is_bambu": True},
        1: {"color": "FF0000", "type": "PLA", "remain": 100, "is_bambu": True},
    }
    # It should skip PETG because the code currently only looks for PLA
    slot = match_color([], available, 500)
    assert slot == 1, f"Should pick the PLA slot, got {slot}"

    # Required grams too high
    available2 = {
        0: {"color": "FF0000", "type": "PLA", "remain": 10, "is_bambu": True}  # 100g
    }
    assert match_color([], available2, 500) is None, "Should return None if insufficient material"


def main():
    test_calculate_max_dimensions()
    test_get_blandness_score()
    test_get_closest_color_name()
    test_match_color()
    print("All test_utils_behavior passed!")


if __name__ == "__main__":
    main()
