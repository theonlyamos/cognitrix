#!/usr/bin/env python3
# ruff: noqa: F401
"""
Test script to validate the new modular CLI structure.
"""

def test_modular_imports():
    """Test that all CLI modules can be imported successfully."""
    try:
        # Test core imports
        from cognitrix.cli.args import get_arguments
        from cognitrix.cli.core import start

        # Test handler imports
        from cognitrix.cli.handlers import list_agents, list_tools, manage_agents, manage_tools
        from cognitrix.cli.main import main

        # Test shell imports
        from cognitrix.cli.shell import CognitrixCompleter, initialize_shell

        # Test UI imports
        from cognitrix.cli.ui import start_web_ui
        from cognitrix.cli.tui import CognitrixApp

        # Test utils imports
        from cognitrix.cli.utils import print_table, str_or_file

        print("✅ All modular CLI imports successful!")
        return True

    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False


def test_package_level_imports():
    """Test that package-level imports work correctly."""
    try:
        # Test package-level imports
        from cognitrix.cli import get_arguments, list_agents, list_tools, main, print_table, start, str_or_file

        print("✅ Package-level imports successful!")
        return True

    except ImportError as e:
        print(f"❌ Package-level import error: {e}")
        return False


def test_entry_point():
    """Test that the main entry point is accessible."""
    try:
        from cognitrix.cli.main import main

        # Check that main is callable
        assert callable(main), "main function should be callable"

        print("✅ Entry point validation successful!")
        return True

    except Exception as e:
        print(f"❌ Entry point error: {e}")
        return False


if __name__ == "__main__":
    print("Testing new modular CLI structure...")
    print("=" * 50)

    success = True
    success &= test_modular_imports()
    success &= test_package_level_imports()
    success &= test_entry_point()

    print("=" * 50)
    if success:
        print("🎉 All tests passed! Modular CLI structure is working correctly.")
    else:
        print("⚠️ Some tests failed. Please check the issues above.")
