"""Tests for the path sanitizer."""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException


class TestValidateFolderPath:
    """Tests for app.core.path_sanitizer.validate_folder_path."""

    def _import(self):
        from app.core.path_sanitizer import validate_folder_path
        return validate_folder_path

    def test_valid_path_within_allowed_root(self):
        """A real directory under /tmp is allowed when /tmp is in allowed_folder_roots."""
        validate_folder_path = self._import()
        # Use tempfile.mkdtemp so the path is under the real /tmp tree (resolves
        # macOS /tmp -> /private/tmp symlink consistently with allowed_folder_roots)
        with tempfile.TemporaryDirectory(dir=Path("/tmp").resolve()) as td:
            subdir = Path(td) / "docs"
            subdir.mkdir()
            result = validate_folder_path(str(subdir))
            assert result == subdir.resolve()

    def test_nonexistent_path_raises_400(self):
        validate_folder_path = self._import()
        with pytest.raises(HTTPException) as exc_info:
            validate_folder_path("/tmp/this_path_definitely_does_not_exist_xyz123")
        assert exc_info.value.status_code == 400

    def test_relative_path_raises_400(self):
        validate_folder_path = self._import()
        with pytest.raises(HTTPException) as exc_info:
            validate_folder_path("relative/path")
        assert exc_info.value.status_code == 400

    def test_path_outside_allowed_root_raises_403(self, tmp_path):
        validate_folder_path = self._import()
        # /var is typically outside /tmp
        if Path("/var").exists():
            with pytest.raises(HTTPException) as exc_info:
                validate_folder_path("/var")
            assert exc_info.value.status_code == 403

    def test_file_instead_of_directory_raises_400(self):
        validate_folder_path = self._import()
        with tempfile.TemporaryDirectory(dir=Path("/tmp").resolve()) as td:
            f = Path(td) / "file.txt"
            f.write_text("hello")
            with pytest.raises(HTTPException) as exc_info:
                validate_folder_path(str(f))
            assert exc_info.value.status_code == 400

    def test_symlink_pointing_outside_allowed_root_raises_403(self):
        validate_folder_path = self._import()
        # Create a symlink inside /tmp pointing to /var (if it exists)
        target = Path("/var")
        if not target.exists():
            pytest.skip("/var does not exist on this system")
        with tempfile.TemporaryDirectory(dir=Path("/tmp").resolve()) as td:
            link = Path(td) / "escape_link"
            link.symlink_to(target)
            with pytest.raises(HTTPException) as exc_info:
                validate_folder_path(str(link))
            assert exc_info.value.status_code == 403


class TestValidateFileExtension:
    def _import(self):
        from app.core.path_sanitizer import validate_file_extension
        return validate_file_extension

    @pytest.mark.parametrize("ext", ["sh", "exe", "py", "pem", "env", "key", "sqlite"])
    def test_blocked_extensions_raise_400(self, ext):
        validate_file_extension = self._import()
        with pytest.raises(HTTPException) as exc_info:
            validate_file_extension(f"/tmp/malicious.{ext}")
        assert exc_info.value.status_code == 400

    @pytest.mark.parametrize("fname", ["report.pdf", "notes.txt", "data.csv", "readme.md"])
    def test_allowed_extensions_pass(self, fname):
        validate_file_extension = self._import()
        validate_file_extension(f"/tmp/{fname}")  # must not raise
