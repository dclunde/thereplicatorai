import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.web.backend.routes.sessions import sanitize_id


def test_sanitize_id_allows_valid_characters():
    # Regular names
    assert sanitize_id("my_cool_model_2026") == "my_cool_model_2026"
    assert sanitize_id("test-session-123") == "test-session-123"

    # Spaces and parentheses (typical of file duplicates or telegram bot)
    assert (
        sanitize_id("uploaded_space_theme_soap_dish_v3 (1)")
        == "uploaded_space_theme_soap_dish_v3 (1)"
    )

    # Dots (typical of file extensions in names)
    assert (
        sanitize_id("pine_tree_shelf.scad_20260801_202232")
        == "pine_tree_shelf.scad_20260801_202232"
    )

    # Hashes and ampersands
    assert sanitize_id("my_cool_part_#2 & #3") == "my_cool_part_#2 & #3"

    # Brackets and braces
    assert sanitize_id("my_part_[v1]_{final}") == "my_part_[v1]_{final}"


def test_sanitize_id_strips_directory_traversal():
    # Should strip slashes and backslashes
    assert sanitize_id("../my_folder") == "..my_folder"
    assert sanitize_id("folder/subfolder") == "foldersubfolder"
    assert sanitize_id("..\\..\\windows_path") == "....windows_path"

    # Other invalid characters should be stripped
    assert sanitize_id('folder_name_with_quote"') == "folder_name_with_quote"
    assert sanitize_id("folder_name_with_tick'") == "folder_name_with_tick"
    assert sanitize_id("folder_name_with_pipe|") == "folder_name_with_pipe"
    assert sanitize_id("folder_name_with_star*") == "folder_name_with_star"
    assert sanitize_id("folder_name_with_question?") == "folder_name_with_question"
