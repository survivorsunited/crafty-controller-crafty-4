import os
import tempfile
from pathlib import Path
from datetime import datetime
import time

import pytest

from app.classes.helpers.helpers import Helpers


def test_resolve_log_file_pattern_single_match(tmp_path) -> None:
    """Test resolving a pattern that matches a single file"""
    # Create a test log file
    server_path = tmp_path / "server"
    server_path.mkdir()
    logs_path = server_path / "logs"
    logs_path.mkdir()
    log_file = logs_path / "latest.log"
    log_file.write_text("test log content")
    
    # Test with relative path pattern
    pattern = "logs/latest.log"
    resolved, error = Helpers.resolve_log_file_pattern(str(server_path), pattern)
    
    assert error is None
    assert resolved is not None
    assert Path(resolved).name == "latest.log"
    assert Path(resolved).exists()


def test_resolve_log_file_pattern_glob_match(tmp_path) -> None:
    """Test resolving a glob pattern that matches multiple files"""
    # Create multiple test log files
    server_path = tmp_path / "server"
    server_path.mkdir()
    logs_path = server_path / "logs"
    logs_path.mkdir()
    
    # Create files with different modification times
    old_log = logs_path / "server-old.log"
    old_log.write_text("old content")
    time.sleep(0.1)
    
    new_log = logs_path / "server-new.log"
    new_log.write_text("new content")
    
    # Test with glob pattern - should return the newest file
    pattern = "logs/server-*.log"
    resolved, error = Helpers.resolve_log_file_pattern(str(server_path), pattern)
    
    assert error is None
    assert resolved is not None
    assert Path(resolved).name == "server-new.log"


def test_resolve_log_file_pattern_no_match(tmp_path) -> None:
    """Test resolving a pattern that matches no files"""
    server_path = tmp_path / "server"
    server_path.mkdir()
    
    pattern = "logs/nonexistent.log"
    resolved, error = Helpers.resolve_log_file_pattern(str(server_path), pattern)
    
    assert resolved is None
    assert error is not None
    assert "No files found" in error


def test_resolve_log_file_pattern_empty_pattern(tmp_path) -> None:
    """Test resolving with an empty pattern"""
    server_path = tmp_path / "server"
    server_path.mkdir()
    
    resolved, error = Helpers.resolve_log_file_pattern(str(server_path), "")
    
    assert resolved is None
    assert error is not None
    assert "No pattern specified" in error


def test_resolve_log_file_pattern_wildcard(tmp_path) -> None:
    """Test resolving with wildcard pattern"""
    server_path = tmp_path / "server"
    server_path.mkdir()
    logs_path = server_path / "logs"
    logs_path.mkdir()
    
    # Create multiple log files
    for i in range(3):
        log_file = logs_path / f"log{i}.log"
        log_file.write_text(f"content {i}")
        time.sleep(0.1)
    
    # Test with wildcard pattern - should return the newest file
    pattern = "logs/*.log"
    resolved, error = Helpers.resolve_log_file_pattern(str(server_path), pattern)
    
    assert error is None
    assert resolved is not None
    assert "log2.log" in resolved


def test_resolve_log_file_pattern_absolute_path(tmp_path) -> None:
    """Test resolving with an absolute path pattern"""
    logs_path = tmp_path / "logs"
    logs_path.mkdir()
    log_file = logs_path / "absolute.log"
    log_file.write_text("absolute content")
    
    # Use absolute path as pattern
    pattern = str(log_file)
    resolved, error = Helpers.resolve_log_file_pattern(str(tmp_path), pattern)
    
    assert error is None
    assert resolved is not None
    assert Path(resolved).name == "absolute.log"
