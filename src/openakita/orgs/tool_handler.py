"""
OrgToolHandler — 组织工具执行器

处理组织节点 Agent 调用的 org_* 系列工具。
每个 handler 方法接收 tool_name, arguments, context(org_id, node_id) 并返回结果。
"""

from __future__ import annotations

import json
import logging
from typing import Any, TYPE_CHECKING

from .models import (
    MemoryType,
    MsgType,
    NodeSchedule,
    NodeStatus,
    OrgMessage,
    ScheduleType,
    _now_iso,
)

if TYPE_CHECKING:
    from .runtime import OrgRuntime

logger = logging.getLogger(__name__)


class OrgToolHandler:
    """Dispatch and execute org_* tool calls."""

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime

    @staticmethod
    def _coerce_types(args: dict) -> dict:
        """Ensure LLM-provided arguments have correct Python types."""
        if "priority" in args:
            try:
                args["priority"] = int(args["priority"])
            except (ValueError, TypeError):
                args["priority"] = 0
        if "bandwidth_limit" in args:
            try:
                args["bandwidth_limit"] = int(args["bandwidth_limit"])
            except (ValueError, TypeError):
                args["bandwidth_limit"] = 60
        return args

    def _resolve_node_refs(self, args: dict, org_id: str) -> None:
        """Resolve node references: LLM may pass role titles or wrong-cased IDs."""
        org = self._runtime.get_org(org_id)
        if not org:
            return
        for key in ("to_node", "node_id", "target_node_id"):
            val = args.get(key, "")
            if not val:
                continue
            if org.get_node(val):
                continue
            val_lower = val.lower().replace(" ", "_").replace("-", "_")
            for n in org.nodes:
                if n.id == val_lower or n.role_title == val or n.role_title.lower() == val.lower():
                    args[key] = n.id
                    break

    @staticmethod
    def _resolve_aliases(args: dict) -> dict:
        """Resolve common LLM parameter name variations to canonical names."""
        if "to_node" not in args:
            args["to_node"] = (
                args.pop("target_node", None)
                or args.pop("target", None)
                or args.pop("to", None)
                or ""
            )
        if "task" not in args:
            alias_task = (
                args.pop("task_description", None)
                or args.pop("task_content", None)
                or args.pop("description", None)
            )
            if alias_task:
                args["task"] = alias_task
        if "content" not in args:
            args["content"] = (
                args.pop("message", None)
                or args.pop("text", None)
                or args.pop("body", None)
                or ""
            )
        if "need" not in args and "query" in args and "filename" not in args:
            args["need"] = args.get("query", "")
        if "query" not in args and "need" in args and "filename" not in args:
            args["query"] = args.get("need", "")
        if "node_id" not in args:
            v = args.pop("target_id", None)
            if v:
                args["node_id"] = v
        if "reply_to" not in args:
            v = args.pop("reply_to_id", None) or args.pop("message_id", None)
            if v:
                args["reply_to"] = v
        if "filename" not in args:
            v = args.pop("file_name", None) or args.pop("file", None)
            if v:
                args["filename"] = v
        return args

    async def handle(
        self, tool_name: str, arguments: dict, org_id: str, node_id: str
    ) -> str:
        """Execute an org tool and return the result as a string."""
        handler = getattr(self, f"_handle_{tool_name}", None)
        if handler is None:
            return f"Unknown org tool: {tool_name}"

        arguments = self._resolve_aliases(arguments)
        arguments = self._coerce_types(arguments)
        self._resolve_node_refs(arguments, org_id)

        try:
            result = await handler(arguments, org_id, node_id)
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False, indent=2)
            return str(result)
        except Exception as e:
            logger.error(f"[OrgToolHandler] Error in {tool_name}: {e}")
            return f"Tool error: {e}"

    # ------------------------------------------------------------------
    # Communication tools
    # ------------------------------------------------------------------

    async def _handle_org_send_message(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        parent_depth = self._runtime._cascade_depth.get(f"{org_id}:{node_id}", 0)
        metadata = {"_cascade_depth": parent_depth + 1}

        raw_type = args.get("msg_type", "question")
        try:
            msg_type = MsgType(raw_type)
        except ValueError:
            msg_type = MsgType.QUESTION
            logger.warning(f"[OrgToolHandler] Invalid msg_type '{raw_type}', falling back to 'question'")

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=args["to_node"],
            msg_type=msg_type,
            content=args["content"],
            priority=args.get("priority", 0),
            metadata=metadata,
        )
        ok = await messenger.send(msg)
        if ok:
            await self._runtime._broadcast_ws("org:message", {
                "org_id": org_id, "from_node": node_id, "to_node": args["to_node"],
                "msg_type": args.get("msg_type", "question"),
                "content": args["content"][:120],
            })
        return f"消息已发送给 {args['to_node']}" if ok else "发送失败"

    async def _handle_org_reply_message(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"
        original = messenger._pending_messages.get(args["reply_to"])
        to_node = original.from_node if original else ""
        if not to_node:
            return f"原始消息 {args['reply_to']} 未找到，无法确定回复目标"
        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=to_node,
            msg_type=MsgType.ANSWER,
            content=args["content"],
            reply_to=args["reply_to"],
        )
        await messenger.send(msg)
        return "已回复"

    async def _handle_org_delegate_task(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"
        metadata = {}
        if args.get("deadline"):
            metadata["task_deadline"] = args["deadline"]

        parent_depth = self._runtime._cascade_depth.get(f"{org_id}:{node_id}", 0)
        metadata["_cascade_depth"] = parent_depth + 1

        chain_id = args.get("task_chain_id") or _now_iso() + ":" + node_id[:8]
        metadata["task_chain_id"] = chain_id

        to_node = args["to_node"]

        existing_affinity = messenger.get_task_affinity(chain_id)
        if existing_affinity:
            org = self._runtime.get_org(org_id)
            if org:
                affinity_node = org.get_node(existing_affinity)
                if affinity_node and affinity_node.status not in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
                    to_node = existing_affinity

        msg = await messenger.send_task(
            from_node=node_id,
            to_node=to_node,
            task_content=args["task"],
            priority=args.get("priority", 0),
            metadata=metadata,
        )

        messenger.bind_task_affinity(chain_id, to_node)

        self._runtime.get_event_store(org_id).emit(
            "task_assigned", node_id,
            {"to": to_node, "task": args["task"][:100], "chain_id": chain_id},
        )
        await self._runtime._broadcast_ws("org:task_delegated", {
            "org_id": org_id, "from_node": node_id, "to_node": to_node,
            "task": args["task"][:120], "chain_id": chain_id,
        })
        return f"任务已分配给 {to_node}（chain: {chain_id[:12]}）: {args['task'][:50]}"

    async def _handle_org_escalate(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        parent_depth = self._runtime._cascade_depth.get(f"{org_id}:{node_id}", 0)

        result = await messenger.escalate(
            node_id, args["content"], priority=args.get("priority", 1),
            metadata={"_cascade_depth": parent_depth + 1},
        )
        if result:
            await self._runtime._broadcast_ws("org:escalation", {
                "org_id": org_id, "from_node": node_id,
                "to_node": result.to_node if hasattr(result, "to_node") else "",
                "content": args["content"][:120],
            })
            return f"已上报给上级"
        return "无法上报（没有上级节点）"

    async def _handle_org_broadcast(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"
        scope = args.get("scope", "department")
        msg_type = MsgType.DEPT_BROADCAST if scope == "department" else MsgType.BROADCAST
        org = self._runtime.get_org(org_id)
        node = org.get_node(node_id) if org else None
        if msg_type == MsgType.BROADCAST and node and node.level > 0:
            return "只有顶层节点可以全组织广播，你可以使用部门广播"

        parent_depth = self._runtime._cascade_depth.get(f"{org_id}:{node_id}", 0)

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            msg_type=msg_type,
            content=args["content"],
            metadata={"_cascade_depth": parent_depth + 1},
        )
        await messenger.send(msg)
        return f"已{'部门' if scope == 'department' else '全组织'}广播"

    # ------------------------------------------------------------------
    # Organization awareness tools
    # ------------------------------------------------------------------

    async def _handle_org_get_org_chart(
        self, args: dict, org_id: str, node_id: str
    ) -> dict:
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "组织未找到"}
        departments: dict[str, list] = {}
        for n in org.nodes:
            dept = n.department or "未分配"
            departments.setdefault(dept, []).append({
                "id": n.id,
                "title": n.role_title,
                "goal": n.role_goal[:80] if n.role_goal else "",
                "skills": n.skills[:5],
                "status": n.status.value,
                "level": n.level,
            })
        edges = [
            {"from": e.source, "to": e.target, "type": e.edge_type.value}
            for e in org.edges
        ]
        return {"departments": [{"name": k, "members": v} for k, v in departments.items()], "edges": edges}

    async def _handle_org_find_colleague(
        self, args: dict, org_id: str, node_id: str
    ) -> list:
        org = self._runtime.get_org(org_id)
        if not org:
            return []
        need = (args.get("need") or args.get("query") or "").lower()
        if not need:
            return []
        prefer_dept = args.get("prefer_department", "").lower()
        results = []
        for n in org.nodes:
            if n.id == node_id:
                continue
            score = 0.0
            text = f"{n.role_title} {n.role_goal} {' '.join(n.skills)}".lower()
            for word in need.split():
                if word in text:
                    score += 0.3
            if prefer_dept and n.department.lower() == prefer_dept:
                score += 0.2
            if n.status == NodeStatus.IDLE:
                score += 0.1
            if score > 0:
                results.append({
                    "id": n.id,
                    "title": n.role_title,
                    "department": n.department,
                    "relevance": round(min(score, 1.0), 2),
                    "status": n.status.value,
                })
        results.sort(key=lambda x: x["relevance"], reverse=True)
        return results[:5]

    async def _handle_org_get_node_status(
        self, args: dict, org_id: str, node_id: str
    ) -> dict:
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "组织未找到"}
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return {"error": f"节点未找到: {target_id}"}
        messenger = self._runtime.get_messenger(org_id)
        pending = messenger.get_pending_count(target.id) if messenger else 0
        return {
            "id": target.id,
            "title": target.role_title,
            "status": target.status.value,
            "department": target.department,
            "pending_messages": pending,
        }

    async def _handle_org_get_org_status(
        self, args: dict, org_id: str, node_id: str
    ) -> dict:
        org = self._runtime.get_org(org_id)
        if not org:
            return {"error": "组织未找到"}
        node_stats: dict[str, int] = {}
        for n in org.nodes:
            s = n.status.value
            node_stats[s] = node_stats.get(s, 0) + 1
        return {
            "org_name": org.name,
            "status": org.status.value,
            "node_count": len(org.nodes),
            "node_stats": node_stats,
            "total_tasks": org.total_tasks_completed,
            "total_messages": org.total_messages_exchanged,
        }

    # ------------------------------------------------------------------
    # Memory tools
    # ------------------------------------------------------------------

    async def _handle_org_read_blackboard(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        if not bb:
            return "黑板不可用"
        entries = bb.read_org(
            limit=args.get("limit", 10),
            tag=args.get("tag"),
        )
        if not entries:
            return "(黑板暂无内容)"
        lines = []
        for e in entries:
            tags = f" [{', '.join(e.tags)}]" if e.tags else ""
            lines.append(f"[{e.memory_type.value}] {e.content}{tags} (by {e.source_node})")
        return "\n".join(lines)

    async def _handle_org_write_blackboard(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        if not bb:
            return "黑板不可用"
        raw_mt = args.get("memory_type", "fact")
        try:
            mt = MemoryType(raw_mt)
        except ValueError:
            mt = MemoryType.FACT
            logger.warning(f"[OrgToolHandler] Invalid memory_type '{raw_mt}', falling back to 'fact'")
        entry = bb.write_org(
            content=args["content"],
            source_node=node_id,
            memory_type=mt,
            tags=args.get("tags", []),
            importance=args.get("importance", 0.5),
        )
        if entry is None:
            return f"黑板已有相似内容，跳过重复写入: {args['content'][:50]}"
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "org", "node_id": node_id,
            "memory_type": args.get("memory_type", "fact"),
            "content": args["content"][:120],
        })
        return f"已写入组织黑板: {args['content'][:50]}"

    async def _handle_org_read_dept_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        org = self._runtime.get_org(org_id)
        if not bb or not org:
            return "不可用"
        node = org.get_node(node_id)
        dept = node.department if node else ""
        if not dept:
            return "你未分配部门"
        entries = bb.read_department(dept, limit=args.get("limit", 10))
        if not entries:
            return f"({dept} 暂无部门记忆)"
        return "\n".join(f"[{e.memory_type.value}] {e.content}" for e in entries)

    async def _handle_org_write_dept_memory(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        bb = self._runtime.get_blackboard(org_id)
        org = self._runtime.get_org(org_id)
        if not bb or not org:
            return "不可用"
        node = org.get_node(node_id)
        dept = node.department if node else ""
        if not dept:
            return "你未分配部门"
        raw_mt = args.get("memory_type", "fact")
        try:
            mt = MemoryType(raw_mt)
        except ValueError:
            mt = MemoryType.FACT
        entry = bb.write_department(
            dept, args["content"], node_id,
            memory_type=mt,
            tags=args.get("tags", []),
            importance=args.get("importance", 0.5),
        )
        if entry is None:
            return "部门记忆已有相似内容，跳过重复写入"
        await self._runtime._broadcast_ws("org:blackboard_update", {
            "org_id": org_id, "scope": "department", "department": dept,
            "node_id": node_id, "memory_type": args.get("memory_type", "fact"),
            "content": args["content"][:120],
        })
        return f"已写入 {dept} 部门记忆"

    # ------------------------------------------------------------------
    # Policy tools
    # ------------------------------------------------------------------

    async def _handle_org_list_policies(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org_dir = self._runtime._manager._org_dir(org_id)
        policies_dir = org_dir / "policies"
        if not policies_dir.exists():
            return "(暂无制度文件)"
        files = sorted(policies_dir.glob("*.md"))
        if not files:
            return "(暂无制度文件)"
        return "\n".join(f"- {f.name}" for f in files)

    async def _handle_org_read_policy(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org_dir = self._runtime._manager._org_dir(org_id)
        fname = args["filename"]
        if ".." in fname or "/" in fname:
            return "非法文件名"
        p = org_dir / "policies" / fname
        if not p.is_file():
            return f"制度文件不存在: {fname}"
        return p.read_text(encoding="utf-8")

    async def _handle_org_search_policy(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org_dir = self._runtime._manager._org_dir(org_id)
        policies_dir = org_dir / "policies"
        query = args["query"].lower()
        results = []
        if policies_dir.exists():
            for f in policies_dir.glob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                    if query in content.lower() or query in f.name.lower():
                        lines = [l for l in content.split("\n") if query in l.lower()][:3]
                        results.append(f"📄 {f.name}\n" + "\n".join(f"  > {l.strip()}" for l in lines))
                except Exception:
                    continue
        if not results:
            return f"未找到与「{args['query']}」相关的制度"
        return "\n\n".join(results)

    # ------------------------------------------------------------------
    # HR tools
    # ------------------------------------------------------------------

    async def _handle_org_freeze_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"
        parent = org.get_parent(target_id)
        caller = org.get_node(node_id)
        if not caller:
            return "你不在此组织中"
        roots = org.get_root_nodes()
        if caller.level >= target.level and (not roots or node_id != roots[0].id):
            return "只能冻结比你层级低的节点"
        target.status = NodeStatus.FROZEN
        target.frozen_by = node_id
        target.frozen_reason = args.get("reason", "")
        target.frozen_at = _now_iso()
        self._runtime._save_org(org)
        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            messenger.freeze_mailbox(target.id)
        self._runtime.get_event_store(org_id).emit(
            "node_frozen", node_id,
            {"target": target.id, "reason": args.get("reason", "")},
        )
        return f"已冻结 {target.role_title}，原因：{args.get('reason', '')}"

    async def _handle_org_unfreeze_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        target_id = args.get("node_id") or args.get("target_node") or ""
        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"
        if target.status != NodeStatus.FROZEN:
            return f"{target.role_title} 未处于冻结状态"
        target.status = NodeStatus.IDLE
        target.frozen_by = None
        target.frozen_reason = None
        target.frozen_at = None
        self._runtime._save_org(org)
        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            messenger.unfreeze_mailbox(target.id)
        self._runtime.get_event_store(org_id).emit(
            "node_unfrozen", node_id, {"target": target.id},
        )
        return f"已解冻 {target.role_title}"

    async def _handle_org_request_clone(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        scaler = self._runtime.get_scaler()
        try:
            req = scaler.request_clone(
                org_id=org_id,
                requester=node_id,
                source_node_id=args["source_node_id"],
                reason=args["reason"],
                ephemeral=args.get("ephemeral", True),
            )
            if req.status == "approved":
                return f"克隆申请已自动批准。新节点: {req.result_node_id}"
            return f"克隆申请已提交（ID: {req.id}），等待审批。"
        except ValueError as e:
            return str(e)

    async def _handle_org_request_recruit(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        scaler = self._runtime.get_scaler()
        try:
            req = scaler.request_recruit(
                org_id=org_id,
                requester=node_id,
                role_title=args["role_title"],
                role_goal=args.get("role_goal", ""),
                department=args.get("department", ""),
                parent_node_id=args["parent_node_id"],
                reason=args["reason"],
            )
            return f"招募申请已提交（ID: {req.id}，岗位: {args['role_title']}），等待审批。"
        except ValueError as e:
            return str(e)

    async def _handle_org_dismiss_node(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        scaler = self._runtime.get_scaler()
        ok = scaler.dismiss_node(org_id, args["node_id"], by=node_id)
        if ok:
            return f"已裁撤节点 {args['node_id']}"
        return "裁撤失败（节点不存在或非临时节点）"

    # ------------------------------------------------------------------
    # Task delivery & acceptance
    # ------------------------------------------------------------------

    async def _handle_org_submit_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        to_node = args.get("to_node", "")
        deliverable = args.get("deliverable", "")
        summary = args.get("summary", "")
        chain_id = args.get("task_chain_id") or _now_iso()

        metadata = {
            "deliverable": deliverable[:2000],
            "summary": summary[:500],
            "task_chain_id": chain_id,
            "_cascade_depth": self._runtime._cascade_depth.get(f"{org_id}:{node_id}", 0) + 1,
        }

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=to_node,
            msg_type=MsgType.TASK_DELIVERED,
            content=f"任务交付: {deliverable[:200]}",
            metadata=metadata,
        )
        ok = await messenger.send(msg)

        self._runtime.get_event_store(org_id).emit(
            "task_delivered", node_id,
            {"to": to_node, "chain_id": chain_id, "deliverable_preview": deliverable[:100]},
        )

        if ok:
            await self._runtime._broadcast_ws("org:task_delivered", {
                "org_id": org_id, "from_node": node_id, "to_node": to_node,
                "chain_id": chain_id, "summary": summary[:120],
            })
            return f"交付物已提交给 {to_node}，等待验收。"
        return "提交失败"

    async def _handle_org_accept_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        chain_id = args.get("task_chain_id", "")
        from_node = args.get("from_node", "")
        feedback = args.get("feedback", "验收通过")

        metadata = {
            "task_chain_id": chain_id,
            "acceptance_feedback": feedback[:500],
            "_cascade_depth": self._runtime._cascade_depth.get(f"{org_id}:{node_id}", 0) + 1,
        }

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=from_node,
            msg_type=MsgType.TASK_ACCEPTED,
            content=f"验收通过: {feedback[:200]}",
            metadata=metadata,
        )
        await messenger.send(msg)

        if chain_id:
            messenger.release_task_affinity(chain_id)

        self._runtime.get_event_store(org_id).emit(
            "task_accepted", node_id,
            {"from": from_node, "chain_id": chain_id},
        )
        await self._runtime._broadcast_ws("org:task_accepted", {
            "org_id": org_id, "from_node": from_node, "accepted_by": node_id,
            "chain_id": chain_id, "feedback": feedback[:120],
        })

        bb = self._runtime.get_blackboard(org_id)
        if bb:
            bb.write_org(
                content=f"任务验收通过 [{chain_id[:8] if chain_id else ''}]: {feedback[:100]}",
                source_node=node_id,
                memory_type=MemoryType.PROGRESS,
                tags=["acceptance", "completed"],
            )

        return f"已验收 {from_node} 的交付物。"

    async def _handle_org_reject_deliverable(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "组织未运行"

        chain_id = args.get("task_chain_id", "")
        from_node = args.get("from_node", "")
        reason = args.get("reason", "")

        metadata = {
            "task_chain_id": chain_id,
            "rejection_reason": reason[:500],
            "_cascade_depth": self._runtime._cascade_depth.get(f"{org_id}:{node_id}", 0) + 1,
        }

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=from_node,
            msg_type=MsgType.TASK_REJECTED,
            content=f"任务打回: {reason[:200]}",
            metadata=metadata,
        )
        await messenger.send(msg)

        self._runtime.get_event_store(org_id).emit(
            "task_rejected", node_id,
            {"from": from_node, "chain_id": chain_id, "reason": reason[:100]},
        )
        await self._runtime._broadcast_ws("org:task_rejected", {
            "org_id": org_id, "from_node": from_node, "rejected_by": node_id,
            "chain_id": chain_id, "reason": reason[:120],
        })

        return f"已打回 {from_node} 的交付物，原因：{reason[:50]}"

    # ------------------------------------------------------------------
    # Meeting tools
    # ------------------------------------------------------------------

    async def _handle_org_request_meeting(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        import asyncio

        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        participants = args.get("participants", [])
        topic = args.get("topic", "")
        max_rounds = min(args.get("max_rounds", 3), 5)

        if len(participants) > 6:
            return "会议参与人数上限为 6 人，建议拆分为多个小会议"

        all_members = [node_id] + participants
        valid = [mid for mid in all_members if org.get_node(mid) is not None]
        if len(valid) < 2:
            return "有效参与者不足 2 人"

        meeting_record: list[str] = [f"## 会议主题: {topic}\n"]
        meeting_record.append(f"主持人: {node_id}")
        meeting_record.append(f"参与者: {', '.join(participants)}\n")

        await self._runtime._broadcast_ws("org:meeting_started", {
            "org_id": org_id, "topic": topic,
            "host": node_id, "participants": participants, "rounds": max_rounds,
        })

        prev_round_summary = ""
        for round_num in range(1, max_rounds + 1):
            meeting_record.append(f"\n### 第 {round_num} 轮\n")

            await self._runtime._broadcast_ws("org:meeting_round", {
                "org_id": org_id, "round": round_num, "total_rounds": max_rounds,
            })

            async def _get_opinion(
                pid: str,
                _round: int = round_num,
                _prev: str = prev_round_summary,
            ) -> tuple[str, str]:
                node_obj = org.get_node(pid)
                if not node_obj or node_obj.status in (NodeStatus.FROZEN, NodeStatus.OFFLINE):
                    return pid, "(缺席)"
                try:
                    response = await self._lightweight_meeting_speak(
                        org, node_obj, topic, _round, max_rounds, _prev,
                    )
                    return pid, response
                except Exception as e:
                    logger.error(f"[Meeting] {pid} speak error: {e}")
                    return pid, "(发言异常)"

            results = await asyncio.gather(*[_get_opinion(pid) for pid in valid])

            round_opinions = []
            for pid, response in results:
                node_obj = org.get_node(pid)
                title = node_obj.role_title if node_obj else pid
                meeting_record.append(f"- **{title}**: {response}")
                round_opinions.append(f"{title}: {response}")
                await self._runtime._broadcast_ws("org:meeting_speak", {
                    "org_id": org_id, "node_id": pid, "role_title": title,
                    "round": round_num, "content": response[:200],
                })

            prev_round_summary = "\n".join(round_opinions)

        conclusion = await self._meeting_summarize(org_id, topic, meeting_record)
        if conclusion:
            meeting_record.append(f"\n### 会议结论\n\n{conclusion}")

        bb = self._runtime.get_blackboard(org_id)
        if bb:
            summary_text = conclusion or meeting_record[-1][:200]
            bb.write_org(
                content=f"会议结论 — {topic}: {summary_text}",
                source_node=node_id,
                memory_type=MemoryType.DECISION,
                tags=["meeting"],
            )

        self._runtime.get_event_store(org_id).emit(
            "meeting_completed", node_id,
            {"topic": topic, "participants": participants, "rounds": max_rounds},
        )

        await self._runtime._broadcast_ws("org:meeting_completed", {
            "org_id": org_id, "topic": topic,
        })

        return "\n".join(meeting_record)

    async def _lightweight_meeting_speak(
        self,
        org: Any,
        node: Any,
        topic: str,
        round_num: int,
        max_rounds: int,
        prev_round_summary: str,
    ) -> str:
        """轻量会议发言：直接 LLM 单次调用，不走完整 Agent/ReAct 循环。"""
        from openakita.llm.client import chat as llm_chat

        identity = self._runtime._get_identity(org.id)
        role_prompt = ""
        if identity:
            try:
                resolved = identity.resolve(node, org)
                role_prompt = (resolved.role or "")[:400]
            except Exception:
                pass

        context_parts = [
            f"你是「{org.name}」的 {node.role_title}（{node.department or ''}）。",
        ]
        if node.responsibilities:
            context_parts.append(f"你的职责: {', '.join(node.responsibilities[:3])}")
        if role_prompt:
            context_parts.append(role_prompt)

        system_prompt = "\n".join(context_parts)

        user_parts = [
            f"你正在参加一个关于「{topic}」的组织内部会议（第 {round_num}/{max_rounds} 轮）。",
        ]
        if prev_round_summary:
            user_parts.append(f"\n上一轮发言摘要:\n{prev_round_summary[:800]}\n")
        user_parts.append(
            "请基于你的职责和专业领域，发表简洁的观点（100-200字）。"
            "直接表达核心观点，不要客套寒暄。"
        )

        messages = [{"role": "user", "content": "\n".join(user_parts)}]
        try:
            resp = await llm_chat(messages, system=system_prompt, max_tokens=400)
            text = resp.text or "(无内容)"
            return text[:500]
        except Exception as e:
            logger.error(f"[Meeting] LLM call failed for {node.id}: {e}")
            return f"(发言失败: {e})"

    async def _meeting_summarize(
        self, org_id: str, topic: str, meeting_record: list[str],
    ) -> str:
        """用 LLM 生成会议结论。"""
        from openakita.llm.client import chat as llm_chat

        full_record = "\n".join(meeting_record)
        if len(full_record) > 3000:
            full_record = full_record[:3000] + "\n...(已截断)"

        messages = [{"role": "user", "content": (
            f"以下是关于「{topic}」的会议讨论记录:\n\n{full_record}\n\n"
            "请总结会议结论，包括: 1) 达成的共识 2) 待决事项 3) 行动计划。"
            "用 150-300 字简洁总结。"
        )}]
        try:
            resp = await llm_chat(messages, system="你是一位专业的会议记录员。", max_tokens=500)
            return (resp.text or "")[:600]
        except Exception as e:
            logger.error(f"[Meeting] Summary LLM failed: {e}")
            return ""

    # ------------------------------------------------------------------
    # Schedule tools
    # ------------------------------------------------------------------

    async def _handle_org_create_schedule(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        sched = NodeSchedule(
            name=args["name"],
            schedule_type=ScheduleType(args.get("schedule_type", "interval")),
            cron=args.get("cron"),
            interval_s=args.get("interval_s"),
            run_at=args.get("run_at"),
            prompt=args["prompt"],
            report_to=args.get("report_to"),
            report_condition=args.get("report_condition", "on_issue"),
            enabled=True,
        )
        self._runtime._manager.add_node_schedule(org_id, node_id, sched)

        inbox = self._runtime.get_inbox(org_id)
        inbox.push_approval_request(
            org_id, node_id,
            title=f"{node_id} 申请创建定时任务「{sched.name}」",
            body=f"任务指令: {sched.prompt[:100]}\n类型: {sched.schedule_type.value}",
            metadata={"schedule_id": sched.id, "node_id": node_id},
        )

        self._runtime.get_event_store(org_id).emit(
            "schedule_created", node_id,
            {"schedule_id": sched.id, "name": sched.name},
        )
        return f"定时任务「{sched.name}」已创建（ID: {sched.id}），已提交审批。"

    async def _handle_org_list_my_schedules(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        schedules = self._runtime._manager.get_node_schedules(org_id, node_id)
        if not schedules:
            return "你目前没有定时任务"
        lines = []
        for s in schedules:
            status = "✅ 启用" if s.enabled else "⏸️ 暂停"
            freq = s.cron or (f"每 {s.interval_s}s" if s.interval_s else s.run_at or "未设置")
            last = s.last_run_at or "从未执行"
            lines.append(f"- [{status}] {s.name} | 频率: {freq} | 上次: {last}")
        return "\n".join(lines)

    async def _handle_org_assign_schedule(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        target_id = args["target_node_id"]
        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"

        caller = org.get_node(node_id)
        if caller and caller.level >= target.level:
            parent = org.get_parent(target_id)
            if not parent or parent.id != node_id:
                return "只能给直属下级指定定时任务"

        sched = NodeSchedule(
            name=args["name"],
            schedule_type=ScheduleType(args.get("schedule_type", "interval")),
            cron=args.get("cron"),
            interval_s=args.get("interval_s"),
            prompt=args["prompt"],
            report_to=args.get("report_to", node_id),
            report_condition=args.get("report_condition", "on_issue"),
            enabled=True,
        )
        self._runtime._manager.add_node_schedule(org_id, target_id, sched)

        self._runtime.get_event_store(org_id).emit(
            "schedule_assigned", node_id,
            {"target": target_id, "schedule_id": sched.id, "name": sched.name},
        )
        return f"已为 {target.role_title} 指定定时任务「{sched.name}」（ID: {sched.id}）"

    # ------------------------------------------------------------------
    # Policy proposal tool
    # ------------------------------------------------------------------

    async def _handle_org_propose_policy(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        inbox = self._runtime.get_inbox(org_id)
        inbox.push_approval_request(
            org_id, node_id,
            title=f"制度提议: {args['title']}",
            body=f"提议者: {node_id}\n原因: {args['reason']}\n文件: {args['filename']}\n\n{args['content'][:500]}",
            options=["approve", "reject"],
            metadata={
                "policy_filename": args["filename"],
                "policy_content": args["content"],
                "policy_title": args["title"],
            },
        )

        self._runtime.get_event_store(org_id).emit(
            "policy_proposed", node_id,
            {"filename": args["filename"], "title": args["title"]},
        )
        return f"制度提议「{args['title']}」已提交审批。"

    # ------------------------------------------------------------------
    # Tool request / grant / revoke
    # ------------------------------------------------------------------

    async def _handle_org_request_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"
        parent = org.get_parent(node_id)
        if not parent:
            return "你是最高级节点，无法向上级申请。请直接配置 external_tools。"

        tools = args.get("tools", [])
        reason = args.get("reason", "")
        if not tools:
            return "参数不完整：请指定需要申请的工具列表（tools）。"

        messenger = self._runtime.get_messenger(org_id)
        if not messenger:
            return "消息系统未就绪"

        from .tool_categories import TOOL_CATEGORIES
        tool_desc = ", ".join(tools)
        cat_details = []
        for t in tools:
            if t in TOOL_CATEGORIES:
                cat_details.append(f"{t}({', '.join(TOOL_CATEGORIES[t])})")
            else:
                cat_details.append(t)

        content = (
            f"[工具申请] {node_id} 申请增加外部工具：{', '.join(cat_details)}\n"
            f"申请原因：{reason}\n\n"
            f"如果批准，请使用 org_grant_tools(node_id=\"{node_id}\", tools={tools}) 授权。"
        )

        msg = OrgMessage(
            org_id=org_id,
            from_node=node_id,
            to_node=parent.id,
            msg_type=MsgType.QUESTION,
            content=content,
            metadata={"_tool_request": True, "requested_tools": tools},
        )
        await messenger.send(msg)

        self._runtime.get_event_store(org_id).emit(
            "tools_requested", node_id,
            {"tools": tools, "reason": reason, "superior": parent.id},
        )
        return f"工具申请已发送给 {parent.role_title}（{parent.id}），等待审批。"

    async def _handle_org_grant_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"

        target_id = args.get("node_id", "")
        tools = args.get("tools", [])
        if not target_id or not tools:
            return "参数不完整：需要 node_id 和 tools"

        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"

        children = org.get_children(node_id)
        child_ids = {c.id for c in children}
        if target_id not in child_ids:
            return f"{target_id} 不是你的直属下级，无法授权。"

        existing = set(target.external_tools)
        for t in tools:
            if t not in existing:
                target.external_tools.append(t)
                existing.add(t)

        self._runtime._save_org(org)
        self._runtime.evict_node_agent(org_id, target_id)

        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            notify = OrgMessage(
                org_id=org_id,
                from_node=node_id,
                to_node=target_id,
                msg_type=MsgType.FEEDBACK,
                content=f"你的工具权限已更新，新增：{', '.join(tools)}。下次激活时生效。",
                metadata={"_tool_grant": True, "granted_tools": tools},
            )
            await messenger.send(notify)

        self._runtime.get_event_store(org_id).emit(
            "tools_granted", node_id,
            {"target": target_id, "tools": tools},
        )
        return f"已授权 {target.role_title}（{target_id}）使用：{', '.join(tools)}"

    async def _handle_org_revoke_tools(
        self, args: dict, org_id: str, node_id: str
    ) -> str:
        org = self._runtime.get_org(org_id)
        if not org:
            return "组织未找到"

        target_id = args.get("node_id", "")
        tools = args.get("tools", [])
        if not target_id or not tools:
            return "参数不完整：需要 node_id 和 tools"

        target = org.get_node(target_id)
        if not target:
            return f"节点未找到: {target_id}"

        children = org.get_children(node_id)
        child_ids = {c.id for c in children}
        if target_id not in child_ids:
            return f"{target_id} 不是你的直属下级，无法操作。"

        removed = []
        for t in tools:
            if t in target.external_tools:
                target.external_tools.remove(t)
                removed.append(t)

        if not removed:
            return f"{target.role_title} 没有这些工具可收回。"

        self._runtime._save_org(org)
        self._runtime.evict_node_agent(org_id, target_id)

        messenger = self._runtime.get_messenger(org_id)
        if messenger:
            notify = OrgMessage(
                org_id=org_id,
                from_node=node_id,
                to_node=target_id,
                msg_type=MsgType.FEEDBACK,
                content=f"你的部分工具权限已收回：{', '.join(removed)}。下次激活时生效。",
                metadata={"_tool_revoke": True, "revoked_tools": removed},
            )
            await messenger.send(notify)

        self._runtime.get_event_store(org_id).emit(
            "tools_revoked", node_id,
            {"target": target_id, "tools": removed},
        )
        return f"已收回 {target.role_title}（{target_id}）的工具：{', '.join(removed)}"
