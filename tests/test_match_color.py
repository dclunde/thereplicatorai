import sys
import os

# Add the root directory to the python path so we can import shared
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.utils import match_color


def test_match_color():
    print("Running match_color tests...")

    # Test 1: Single Bambu spool with enough material
    available = {
        0: {"color": "FFFFFF", "type": "PLA", "remain": 100, "is_bambu": True}  # 1000g
    }
    slot = match_color([], available, 500)
    assert slot == 0, f"Test 1 failed. Expected 0, got {slot}"

    # Test 2: Single Bambu spool without enough material
    available = {
        0: {"color": "FFFFFF", "type": "PLA", "remain": 10, "is_bambu": True}  # 100g
    }
    slot = match_color([], available, 500)
    assert slot is None, f"Test 2 failed. Expected None, got {slot}"

    # Test 3: Two identical Bambu spools that sum up to enough material (100g + 1000g = 1100g)
    # It should select the one with the lowest remain first!
    available = {
        0: {"color": "FFFFFF", "type": "PLA", "remain": 100, "is_bambu": True},
        2: {"color": "FFFFFF", "type": "PLA", "remain": 10, "is_bambu": True},
    }
    slot = match_color([], available, 500)
    assert slot == 2, f"Test 3 failed. Expected 2, got {slot}"

    # Test 4: Two identical Bambu spools that sum up but don't have enough (100g + 100g = 200g)
    available = {
        0: {"color": "FFFFFF", "type": "PLA", "remain": 10, "is_bambu": True},
        1: {"color": "FFFFFF", "type": "PLA", "remain": 10, "is_bambu": True},
    }
    slot = match_color([], available, 500)
    assert slot is None, f"Test 4 failed. Expected None, got {slot}"

    # Test 5: Ignore "Unknown" type
    available = {0: {"color": "Unknown", "type": "Unknown", "remain": 100, "is_bambu": False}}
    slot = match_color([], available, 500)
    assert slot is None, f"Test 5 failed. Expected None, got {slot}"

    # Test 6: Non-Bambu identical spools that sum up to > 20%
    available = {
        0: {"color": "FF0000", "type": "PLA", "remain": 15, "is_bambu": False},
        3: {"color": "FF0000", "type": "PLA", "remain": 10, "is_bambu": False},
    }
    slot = match_color(
        [], available, 100
    )  # For non-bambu, required_grams isn't heavily used for capacity, it relies on > 20%
    assert slot == 3, f"Test 6 failed. Expected 3, got {slot}"

    print("All tests passed successfully!")


if __name__ == "__main__":
    test_match_color()
