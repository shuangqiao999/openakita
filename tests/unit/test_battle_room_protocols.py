"""
作战指挥室协议模块测试
"""

import pytest
from datetime import datetime

from openakita.protocols.reporting import (
    ReportStatus,
    CommandType,
    StatusReport,
    Command,
)


class TestReportStatus:
    """测试汇报状态枚举"""

    def test_terminal_states(self):
        """测试终端状态"""
        terminal = ReportStatus.terminal_states()
        assert ReportStatus.COMPLETED in terminal
        assert ReportStatus.FAILED in terminal
        assert ReportStatus.CANCELLED in terminal
        assert ReportStatus.TIMEOUT in terminal
        assert ReportStatus.STEPS_EXHAUSTED in terminal

    def test_is_terminal(self):
        """测试 is_terminal 属性"""
        assert ReportStatus.COMPLETED.is_terminal is True
        assert ReportStatus.IN_PROGRESS.is_terminal is False
        assert ReportStatus.PENDING.is_terminal is False


class TestStatusReport:
    """测试状态汇报数据类"""

    def test_create_report(self):
        """测试创建汇报"""
        report = StatusReport(
            mission_id="mission_123",
            task_id="task_456",
            soldier_id="soldier_789",
            status=ReportStatus.IN_PROGRESS,
            progress=0.5,
            message="Working on it",
        )

        assert report.mission_id == "mission_123"
        assert report.task_id == "task_456"
        assert report.soldier_id == "soldier_789"
        assert report.status == ReportStatus.IN_PROGRESS
        assert report.progress == 0.5

    def test_progress_clamping(self):
        """测试进度 clamping"""
        report = StatusReport(
            mission_id="m1",
            task_id="t1",
            soldier_id="s1",
            status=ReportStatus.IN_PROGRESS,
            progress=1.5,  # 超出范围
        )
        assert report.progress == 1.0

        report2 = StatusReport(
            mission_id="m1",
            task_id="t1",
            soldier_id="s1",
            status=ReportStatus.IN_PROGRESS,
            progress=-0.5,  # 低于范围
        )
        assert report2.progress == 0.0


class TestCommand:
    """测试指挥官命令数据类"""

    def test_create_command(self):
        """测试创建命令"""
        cmd = Command(
            command_type=CommandType.CONTINUE,
            mission_id="mission_123",
            task_id="task_456",
            parameters={"foo": "bar"},
        )

        assert cmd.command_type == CommandType.CONTINUE
        assert cmd.mission_id == "mission_123"
        assert cmd.task_id == "task_456"
        assert cmd.parameters == {"foo": "bar"}

    def test_command_properties(self):
        """测试命令属性"""
        cancel_cmd = Command(
            command_type=CommandType.CANCEL,
            mission_id="m1",
        )
        assert cancel_cmd.is_cancel is True
        assert cancel_cmd.is_pause is False
        assert cancel_cmd.needs_human is False

        pause_cmd = Command(
            command_type=CommandType.PAUSE,
            mission_id="m1",
        )
        assert pause_cmd.is_pause is True

        human_cmd = Command(
            command_type=CommandType.REQUEST_HUMAN_INTERVENTION,
            mission_id="m1",
        )
        assert human_cmd.needs_human is True
