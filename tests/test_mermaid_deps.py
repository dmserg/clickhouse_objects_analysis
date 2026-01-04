# tests/test_mermaid_deps.py
import pytest

from dependencies_to_mermaid import (
    MermaidDependencyGraphError,
    MermaidOptions,
    json_to_mermaid,
    loads_json_to_mermaid,
)


def test_json_to_mermaid_happy_path_edges_no_quotes():
    data = {
        "view_dependencies": {
            "test.v_car_inventory": ["test.car", "test.household", "test.human"],
            "test.v_city_household_flag_stats": ["test.v_household_flags"],
            "test.v_household_flags": [
                "test.car",
                "test.household",
                "test.household_member",
                "test.pet",
            ],
            "test.v_household_health_score": ["test.v_household_flags"],
            "test.v_household_roster": [
                "test.car",
                "test.household",
                "test.household_member",
                "test.pet",
            ],
        },
        "errors": {},
    }

    out = json_to_mermaid(data)

    assert out.startswith("graph LR\n")

    # Ensure no quotes are present anywhere
    assert '"' not in out

    # spot-check a few edges
    assert "  test.car -.-> test.v_car_inventory\n" in out
    assert "  test.v_household_flags -.-> test.v_city_household_flag_stats\n" in out
    assert "  test.v_household_flags -.-> test.v_household_health_score\n" in out


def test_direction_option_changes_header():
    data = {"view_dependencies": {"a": ["b"]}}
    out = json_to_mermaid(data, options=MermaidOptions(direction="TB"))
    assert out.startswith("graph TB\n")
    assert "  b -.-> a\n" in out
    assert '"' not in out


def test_dedupe_edges_true_removes_duplicates_preserves_first_order():
    data = {"view_dependencies": {"a": ["b", "b", "c", "b"]}}
    out = json_to_mermaid(data, options=MermaidOptions(dedupe_edges=True))

    assert out.count("  b -.-> a\n") == 1
    assert out.count("  c -.-> a\n") == 1
    assert '"' not in out


def test_dedupe_edges_false_keeps_duplicates():
    data = {"view_dependencies": {"a": ["b", "b"]}}
    out = json_to_mermaid(data, options=MermaidOptions(dedupe_edges=False))

    assert out.count("  b -.-> a\n") == 2
    assert '"' not in out


def test_include_isolated_nodes_true_lists_isolated_nodes_when_edges_exist():
    data = {"view_dependencies": {"a": ["b"], "isolated": []}}
    out = json_to_mermaid(data, options=MermaidOptions(include_isolated_nodes=True))

    assert "  b -.-> a\n" in out
    assert "  isolated\n" in out
    assert '"' not in out


def test_include_isolated_nodes_false_does_not_list_isolated_nodes():
    data = {"view_dependencies": {"a": ["b"], "isolated": []}}
    out = json_to_mermaid(data, options=MermaidOptions(include_isolated_nodes=False))

    assert "  b -.-> a\n" in out
    assert "  isolated\n" not in out
    assert '"' not in out


def test_no_edges_with_isolated_nodes_true_still_lists_nodes():
    data = {"view_dependencies": {"a": [], "b": []}}
    out = json_to_mermaid(data, options=MermaidOptions(include_isolated_nodes=True))

    assert out.startswith("graph LR\n")
    assert "  a\n" in out
    assert "  b\n" in out
    assert "-.->" not in out
    assert '"' not in out


def test_missing_view_dependencies_key_raises():
    with pytest.raises(MermaidDependencyGraphError, match="Missing required key"):
        json_to_mermaid({"errors": {}})


def test_view_dependencies_not_dict_raises():
    with pytest.raises(MermaidDependencyGraphError, match="must be a dictionary"):
        json_to_mermaid({"view_dependencies": ["nope"]})


def test_view_key_not_string_raises():
    with pytest.raises(MermaidDependencyGraphError, match="keys.*must be strings"):
        json_to_mermaid({"view_dependencies": {123: ["a"]}})


def test_dependencies_not_list_or_null_raises():
    with pytest.raises(MermaidDependencyGraphError, match="must be a list"):
        json_to_mermaid({"view_dependencies": {"a": "b"}})


def test_dependencies_list_contains_non_string_raises():
    with pytest.raises(MermaidDependencyGraphError, match="list of strings"):
        json_to_mermaid({"view_dependencies": {"a": ["b", 1]}})


def test_null_dependencies_are_treated_as_empty_list():
    out = json_to_mermaid({"view_dependencies": {"a": None}})
    assert out.startswith("graph LR\n")
    assert "  a\n" in out
    assert "-.->" not in out
    assert '"' not in out


def test_invalid_direction_raises():
    data = {"view_dependencies": {"a": ["b"]}}
    with pytest.raises(MermaidDependencyGraphError, match="direction must be one of"):
        json_to_mermaid(data, options=MermaidOptions(direction="DIAGONAL"))


def test_loads_json_to_mermaid_invalid_json_raises():
    with pytest.raises(MermaidDependencyGraphError, match="Invalid JSON"):
        loads_json_to_mermaid("{ this is not json }")


def test_loads_json_to_mermaid_top_level_not_object_raises():
    with pytest.raises(MermaidDependencyGraphError, match="Top-level JSON must be an object"):
        loads_json_to_mermaid('["not an object"]')


def test_invalid_node_name_with_space_raises():
    # Space isn't allowed since we must keep names unquoted
    data = {"view_dependencies": {"bad name": ["b"]}}
    with pytest.raises(MermaidDependencyGraphError, match="Invalid node name"):
        json_to_mermaid(data)


def test_invalid_node_name_with_quote_raises():
    data = {"view_dependencies": {'a"b': ["b"]}}
    with pytest.raises(MermaidDependencyGraphError, match="Invalid node name"):
        json_to_mermaid(data)
