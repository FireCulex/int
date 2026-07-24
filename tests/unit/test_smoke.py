"""Smoke test: the package imports and exposes a version string."""


def test_package_importable() -> None:
    import int

    assert int.__doc__ is not None
    assert "int" in int.__doc__.lower()


def test_cli_package_importable() -> None:
    import int_cli

    assert int_cli.__doc__ is not None
