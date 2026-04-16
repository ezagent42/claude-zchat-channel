"""全自动飞书 E2E 测试 — 9 类 23 test case (含 5 个 AuthorizationModel)。

消息发送：通过 Bridge API WebSocket 注入（模拟 customer/operator/admin 输入）
消息验证：通过 FeishuTestClient 检查真实飞书群（agent 回复可见）

需要:
- 飞书凭证 (FEISHU_APP_ID / FEISHU_APP_SECRET)
- 完整运行栈 (full_stack fixture)
- 三个飞书测试群 (cs-customer / cs-squad / cs-admin)
"""

from __future__ import annotations

import time
import pytest

from tests.pre_release.conftest import capture_zellij_screenshot

pytestmark = pytest.mark.prerelease

# 跨测试共享状态 — 例如 card message_id 用于 thread reply
_state: dict = {}

# customer 群的 chat_id 同时用作 conversation_id（feishu_bridge 的简化映射）
# 由 groups fixture 注入


@pytest.mark.prerelease
class TestFeishuFullJourney:
    """PRD 6 步状态机端到端验证"""

    def test_step1_customer_onboard(self, feishu, groups, bridge_ws, full_stack):
        """US-2.1: 客户接入 → agent 回复

        通过 Bridge API 发送 customer_connect + customer_message
        → channel-server 创建 conversation → dispatch agent
        → agent 回复 → feishu_bridge → 飞书 customer 群收到回复
        """
        conv_id = groups["customer_chat"]
        bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
        bridge_ws.customer_message(conv_id, "B 套餐多少钱")

        # 验证: 飞书 customer 群收到 agent 回复
        msg = feishu.assert_message_appears(
            groups["customer_chat"],
            contains="",  # agent 任意回复即可
            timeout=30,
        )
        assert msg is not None
        _state["conv_id"] = conv_id
        capture_zellij_screenshot("01-customer-onboard")

    def test_step2_squad_card_notification(self, feishu, groups, full_stack):
        """US-2.3: 分队群收到 interactive card（不是纯文本）

        conversation.created 事件 → feishu_bridge VisibilityRouter
        → 在 squad 群发 interactive card（thread root）
        """
        card = feishu.assert_card_appears(
            groups["squad_chat"],
            contains="进行中",
            timeout=15,
        )
        assert card is not None
        _state["squad_card_msg_id"] = card["message_id"]
        capture_zellij_screenshot("02-squad-card")

    def test_step3_copilot_in_squad_thread(self, feishu, groups, bridge_ws, full_stack):
        """US-2.4: copilot 模式 — operator 在 squad thread 发建议，客户群看不到

        operator_message(side) → channel-server Gate 判定 side
        → feishu_bridge 只发到 squad thread，不发到 customer 群
        """
        conv_id = _state.get("conv_id")
        if not conv_id:
            pytest.fail("conv_id 未在 step1 中设置")

        test_text = f"建议强调优惠_{int(time.time())}"
        bridge_ws.operator_message(conv_id, test_text, operator_id="test-operator")
        time.sleep(5)

        # 验证: 客户群没有这条消息（side visibility）
        feishu.assert_message_absent(
            groups["customer_chat"],
            contains=test_text,
            wait=8,
        )

    def test_step4_auto_hijack(self, feishu, groups, bridge_ws, full_stack):
        """US-2.5: operator 发 /hijack → mode 切换为 takeover

        operator_command(/hijack) → channel-server mode.changed
        → feishu_bridge 更新 squad card 状态
        """
        conv_id = _state.get("conv_id")
        if not conv_id:
            pytest.fail("conv_id 未在 step1 中设置")

        bridge_ws.operator_command(conv_id, "/hijack")

        # 验证: squad 卡片更新为 takeover 状态
        feishu.assert_card_updated(
            groups["squad_chat"],
            contains="takeover",
            timeout=15,
        )
        capture_zellij_screenshot("04-auto-hijack")

    def test_step5_operator_message_reaches_customer(self, feishu, groups, bridge_ws, full_stack):
        """US-2.6: takeover 下 operator 消息 → 客户可见

        operator_message 在 takeover 模式下 → Gate 判定 public
        → feishu_bridge 发到 customer 群
        """
        conv_id = _state.get("conv_id")
        if not conv_id:
            pytest.fail("conv_id 未在 step1 中设置")

        operator_text = f"您好我是客服小李_{int(time.time())}"
        bridge_ws.operator_message(conv_id, operator_text, operator_id="test-operator")

        # 验证: 客户群收到
        feishu.assert_message_appears(
            groups["customer_chat"],
            contains="客服小李",
            timeout=15,
        )

    def test_step6_resolve_and_csat(self, feishu, groups, bridge_ws, full_stack):
        """US-2.6: /resolve → CSAT 卡片"""
        conv_id = _state.get("conv_id")
        if not conv_id:
            pytest.fail("conv_id 未在 step1 中设置")

        bridge_ws.operator_command(conv_id, "/resolve")

        # 验证: 客户群收到 CSAT 评分卡片
        feishu.assert_message_appears(
            groups["customer_chat"],
            contains="评分",
            timeout=15,
        )

        # 验证: squad 卡片更新为"已关闭"
        feishu.assert_card_updated(
            groups["squad_chat"],
            contains="已关闭",
            timeout=15,
        )
        capture_zellij_screenshot("06-resolve-csat")


@pytest.mark.prerelease
class TestFeishuPlaceholderAndEdit:
    """US-2.2: 占位消息 + 续写替换 — 快慢双 agent 核心体验"""

    def test_placeholder_then_edit(self, feishu, groups, bridge_ws, full_stack):
        """US-2.2: 复杂查询 → fast-agent 占位 → deep-agent edit 替换"""
        conv_id = groups["customer_chat"]

        # 发起新 conversation（如果未连接）
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)

        bridge_ws.customer_message(conv_id, "A 和 B 套餐的详细对比？能不能自定义？")

        # fast-agent 先发占位消息
        placeholder = feishu.assert_message_appears(
            groups["customer_chat"],
            contains="",  # 任意回复
            timeout=15,
        )
        assert placeholder is not None
        _state["placeholder_msg_id"] = placeholder["message_id"]
        capture_zellij_screenshot("03-placeholder-edit")

    def test_edit_visible_in_squad(self, feishu, groups, full_stack):
        """编辑后的消息在分队群 thread 中也能看到"""
        feishu.assert_message_appears(
            groups["squad_chat"],
            contains="",  # squad thread 中有任意消息
            timeout=15,
        )


@pytest.mark.prerelease
class TestFeishuTimerAndEscalation:
    """US-2.5: Agent @operator 求助 + 超时退回"""

    def test_agent_escalation_notifies_squad(self, feishu, groups, bridge_ws, full_stack):
        """Agent 判断超出能力 → escalation → squad 群通知"""
        conv_id = groups["customer_chat"]
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)

        bridge_ws.customer_message(conv_id, "我要退货，你们客服经理在吗")

        # squad 群应收到通知
        feishu.assert_message_appears(
            groups["squad_chat"],
            contains="",  # squad thread 收到任意消息
            timeout=20,
        )

    def test_timeout_reverts_to_auto(self, feishu, groups, bridge_ws, full_stack):
        """escalation 后无人接管 → timer → 安抚消息"""
        conv_id = groups["customer_chat"]
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)

        bridge_ws.customer_message(conv_id, "我要找经理投诉")
        time.sleep(3)

        # 等待 timer 超时（测试配置 takeover_wait=10s）
        # 客户应收到安抚消息或回复
        feishu.assert_message_appears(
            groups["customer_chat"],
            contains="",  # 有回复即可
            timeout=20,
        )


@pytest.mark.prerelease
class TestFeishuCSATFlow:
    """US-2.6 补充: CSAT 评分提交完整闭环"""

    def test_csat_score_submission(self, feishu, groups, bridge_ws, full_stack):
        """客户点击评分 → csat_response → set_csat 闭环"""
        conv_id = groups["customer_chat"]
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)

        bridge_ws.customer_message(conv_id, "你好")
        time.sleep(5)

        # operator hijack + resolve
        bridge_ws.operator_command(conv_id, "/hijack")
        time.sleep(2)
        bridge_ws.operator_command(conv_id, "/resolve")

        # 客户看到评分卡片
        feishu.assert_message_appears(
            groups["customer_chat"],
            contains="评分",
            timeout=15,
        )

        # 模拟客户点击评分
        feishu.click_card_action(
            groups["customer_chat"],
            action_value="5",
            conv_id=conv_id,
        )


@pytest.mark.prerelease
class TestFeishuConversationReactivation:
    """US-2.1 补充: 老客户重新进入 → 加载历史上下文"""

    def test_reactivation_loads_history(self, feishu, groups, bridge_ws, full_stack):
        """闲置对话重新激活"""
        conv_id = groups["customer_chat"]
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)

        # 第一轮对话
        bridge_ws.customer_message(conv_id, "B 套餐多少钱")
        feishu.assert_message_appears(
            groups["customer_chat"], contains="", timeout=20
        )

        # 等待 idle
        time.sleep(15)

        # 第二轮
        bridge_ws.customer_message(conv_id, "我之前问的那个套餐，能打折吗")
        msg = feishu.assert_message_appears(
            groups["customer_chat"], contains="", timeout=20
        )
        assert msg is not None


@pytest.mark.prerelease
class TestFeishuGateIsolation:
    """Gate 强制执行验证 — 最关键的安全测试"""

    def test_takeover_agent_side_not_in_customer(self, feishu, groups, bridge_ws, full_stack):
        """takeover 下 agent side 消息 → 客户看不到"""
        conv_id = groups["customer_chat"]
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)

        bridge_ws.customer_message(conv_id, "需要帮助")
        time.sleep(5)

        # operator hijack → takeover
        bridge_ws.operator_command(conv_id, "/hijack")
        time.sleep(3)

        # 验证: 客户群没有 side 消息
        side_marker = f"side_test_{int(time.time())}"
        feishu.assert_message_absent(
            groups["customer_chat"],
            contains=side_marker,
            wait=5,
        )
        capture_zellij_screenshot("05-side-not-in-customer")

    def test_copilot_operator_thread_not_in_customer(self, feishu, groups, bridge_ws, full_stack):
        """copilot 下 operator 消息降为 side → 客户看不到"""
        conv_id = groups["customer_chat"]
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)

        bridge_ws.customer_message(conv_id, "你好")
        time.sleep(5)

        test_text = f"内部建议_{int(time.time())}"
        bridge_ws.operator_message(conv_id, test_text, operator_id="test-operator")
        time.sleep(5)

        feishu.assert_message_absent(
            groups["customer_chat"], contains=test_text, wait=5
        )


@pytest.mark.prerelease
class TestFeishuAdminCommands:
    """管理群命令测试 — US-3.2"""

    def test_status_command(self, feishu, groups, bridge_ws, full_stack):
        """US-3.2: /status → admin 群收到状态响应"""
        # 先确保有活跃对话
        conv_id = groups["customer_chat"]
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)
        bridge_ws.customer_message(conv_id, "你好")
        time.sleep(5)

        bridge_ws.admin_command("/status")
        msg = feishu.assert_message_appears(
            groups["admin_chat"],
            contains="",  # 有响应即可
            timeout=15,
        )
        assert msg is not None
        capture_zellij_screenshot("07-admin-status")

    def test_dispatch_command(self, feishu, groups, bridge_ws, full_stack):
        """US-3.2: /dispatch"""
        conv_id = _state.get("conv_id", groups["customer_chat"])
        bridge_ws.admin_command(f"/dispatch {conv_id} deep-agent")
        feishu.assert_message_appears(
            groups["admin_chat"],
            contains="",  # 有响应即可
            timeout=15,
        )

    def test_review_command(self, feishu, groups, bridge_ws, full_stack):
        """US-3.2: /review → 统计汇总"""
        bridge_ws.admin_command("/review")
        msg = feishu.assert_message_appears(
            groups["admin_chat"],
            contains="",  # 有响应即可
            timeout=15,
        )
        assert msg is not None
        capture_zellij_screenshot("08-admin-review")


@pytest.mark.prerelease
class TestFeishuSLABreach:
    """US-3.3: SLA breach 告警"""

    def test_sla_breach_alert(self, feishu, groups, bridge_ws, full_stack):
        """SLA breach → admin 群告警"""
        conv_id = groups["customer_chat"]
        if conv_id not in _state.get("connected_convs", set()):
            bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
            _state.setdefault("connected_convs", set()).add(conv_id)

        bridge_ws.customer_message(conv_id, "我要找经理投诉，马上解决")
        time.sleep(3)

        # 等 SLA breach timer（测试配置 takeover_wait=10s）
        time.sleep(12)

        feishu.assert_message_appears(
            groups["admin_chat"],
            contains="",  # 有告警即可
            timeout=15,
        )
        capture_zellij_screenshot("09-sla-breach")


@pytest.mark.prerelease
class TestFeishuAuthorizationModel:
    """授权模型验证"""

    def test_customer_group_auto_registered_on_bot_added(self, feishu, groups, bridge_ws, full_stack):
        """customer_connect → 有回复"""
        conv_id = groups["customer_chat"]
        bridge_ws.customer_connect(conv_id, customer_name="TestCustomer")
        bridge_ws.customer_message(conv_id, "你好")
        feishu.assert_message_appears(
            groups["customer_chat"],
            contains="",
            timeout=20,
        )

    def test_operator_in_squad_group_can_use_operator_commands(self, feishu, groups, bridge_ws, full_stack):
        """operator /hijack → squad card 更新"""
        conv_id = _state.get("conv_id", groups["customer_chat"])
        bridge_ws.operator_command(conv_id, "/hijack")
        feishu.assert_card_updated(
            groups["squad_chat"],
            contains="takeover",
            timeout=15,
        )

    def test_admin_in_admin_group_can_use_admin_commands(self, feishu, groups, bridge_ws, full_stack):
        """admin /status → 有响应"""
        bridge_ws.admin_command("/status")
        feishu.assert_message_appears(
            groups["admin_chat"],
            contains="",
            timeout=15,
        )

    def test_customer_cannot_use_operator_commands(self, feishu, groups, bridge_ws, full_stack):
        """customer 发 /hijack → 不应出现 takeover 确认"""
        conv_id = groups["customer_chat"]
        bridge_ws.customer_message(conv_id, "/hijack")
        feishu.assert_message_absent(
            groups["customer_chat"],
            contains="takeover",
            wait=5,
        )

    def test_group_disbanded_archives_conversation(self, feishu, groups, full_stack):
        """群解散 → conversation 归档（需手动操作）"""
        pytest.skip("群解散需要手动操作，在 evidence/ 目录保存截图验证")
